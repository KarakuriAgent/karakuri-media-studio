"""inspect の音声解析（AGENT-MODE §3.3）。

エージェントは動画のフレームは見られるが**音は聴けない**ので、`inspect` のとき
ffmpeg だけで測れることを測って**人間可読なレポート**に落とし、フレーム画像と
突き合わせて判断できるようにする。新しい Python 依存は増やさない:

- ``silencedetect`` … 無音区間（先頭 / 途中 / 末尾を時刻つきで）
- ``ebur128``       … 統合ラウドネス (LUFS) / ラウドネスレンジ / トゥルーピーク
- ``astats``        … ピークレベル (dBFS) と RMS（クリッピング疑いの判定）
- ``showwavespic`` / ``showspectrumpic`` … 波形とスペクトログラムの PNG
  （横軸は動画の全尺に対応するので、レポートに尺を書いて対応を取れるようにする）

文字起こし (STT) だけは任意機能で、設定 ``agent_stt_enabled`` が真のときに
**OpenAI 互換の外部エンドポイント**（``POST {base_url}/audio/transcriptions``）へ
音声を投げて書き起こす。重い推論はこのプロセスに抱えない — ComfyUI や LLM CLI と
同じく外に出す（speaches / whisper.cpp server / OpenAI API などを指す）。
どの項目も失敗しても inspect 全体は止めず、「スキップした」と必ず残す。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import jobs
from .config import load_settings

log = logging.getLogger(__name__)

#: silencedetect のしきい値と最短長（生成物の「頭が無音」「途中で切れた」を拾う）
SILENCE_NOISE_DB = -40.0
SILENCE_MIN_DURATION = 0.4
#: 端（先頭 / 末尾）とみなす余裕
EDGE_TOLERANCE = 0.05
#: ピークがここ以上ならクリッピング疑い / 0dBFS 近接として注意を書く
CLIPPING_PEAK_DB = -0.1
NEAR_CLIPPING_PEAK_DB = -1.0
#: 生成する画像の大きさ
WAVEFORM_SIZE = "1200x240"
SPECTROGRAM_SIZE = "1200x400"
WAVEFORM_NAME = "audio_waveform.png"
SPECTROGRAM_NAME = "audio_spectrogram.png"

#: レポートに載せる文字起こしの最大行数（長い動画で会話履歴を潰さない）
MAX_STT_SEGMENTS = 120
#: 文字起こしの接続 / 書き込みタイムアウト（読み取りだけ音声長から伸ばす）
STT_CONNECT_TIMEOUT = 10.0
STT_MIN_READ_TIMEOUT = 120.0
#: 読み取りタイムアウト = 音声長 × これ（CPU の whisper.cpp でも実時間の数倍で済む）
STT_READ_TIMEOUT_FACTOR = 6.0

# --------------------------------------------------------------------------
# データ構造
# --------------------------------------------------------------------------

@dataclass
class AudioStream:
    """ffprobe が返した音声トラックの素性。"""

    codec: str = ""
    channels: int | None = None
    sample_rate: int | None = None
    duration: float | None = None


@dataclass
class Silence:
    """1 区間の無音（``end`` が None ならファイル末尾まで無音）。"""

    start: float
    end: float | None = None
    duration: float | None = None


@dataclass
class Loudness:
    """ebur128 のサマリ。読めなかった項目は None。"""

    integrated: float | None = None
    lra: float | None = None
    true_peak: float | None = None


@dataclass
class Stats:
    """astats の全体値。読めなかった項目は None。"""

    peak_db: float | None = None
    rms_db: float | None = None
    flat_factor: float | None = None


@dataclass
class SttSegment:
    """文字起こしの 1 区間。"""

    start: float
    end: float
    text: str


@dataclass
class AudioInspection:
    """`inspect` に足す音声解析の結果。"""

    has_audio: bool
    report: str
    images: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# ffmpeg / ffprobe
# --------------------------------------------------------------------------

async def _run(cmd: list[str]) -> tuple[int, str]:
    """コマンドを回して ``(終了コード, stderr)`` を返す（起動失敗は JobError）。"""
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        raise jobs.JobError(f"{cmd[0]} を実行できません: {exc}") from exc
    stdout, stderr = await process.communicate()
    text = (stdout or b"").decode("utf-8", "replace") + (stderr or b"").decode(
        "utf-8", "replace"
    )
    return process.returncode or 0, text


async def probe_audio_stream(path: str | Path) -> AudioStream | None:
    """先頭の音声トラックの素性（音声が無ければ None、判定不能なら JobError）。"""
    code, text = await _run([
        jobs.FFPROBE, "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,channels,sample_rate,duration",
        "-of", "json", str(path),
    ])
    if code != 0:
        raise jobs.JobError(f"ffprobe が音声トラックを読めませんでした: {text.strip()[-200:]}")
    return parse_audio_stream(text)


def parse_audio_stream(payload: str) -> AudioStream | None:
    """``ffprobe -of json`` の出力から音声トラックを取り出す（無ければ None）。"""
    try:
        data = json.loads(payload or "{}")
    except ValueError:
        return None
    streams = data.get("streams") or []
    if not streams:
        return None
    stream = streams[0]
    return AudioStream(
        codec=str(stream.get("codec_name") or ""),
        channels=_as_int(stream.get("channels")),
        sample_rate=_as_int(stream.get("sample_rate")),
        duration=_as_float(stream.get("duration")),
    )


def _as_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number


def analysis_filter(
    noise_db: float = SILENCE_NOISE_DB, min_duration: float = SILENCE_MIN_DURATION
) -> str:
    """無音・ラウドネス・ピークを 1 パスで測るフィルタチェーン。

    ``ebur128`` は既定だと 100ms ごとの経過ログを吐いて stderr が数百行になるので
    ``framelog=quiet`` でサマリだけにする。
    """
    return (
        f"silencedetect=noise={noise_db:g}dB:d={min_duration:g},"
        "ebur128=peak=true:framelog=quiet,"
        "astats=measure_perchannel=none"
    )


async def run_analysis(path: str | Path) -> str:
    """解析フィルタを回して ffmpeg のログ（stderr）を返す。"""
    code, text = await _run([
        jobs.FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
        "-map", "0:a:0", "-af", analysis_filter(), "-f", "null", "-",
    ])
    if code != 0:
        raise jobs.JobError(f"ffmpeg の音声解析が失敗しました: {text.strip()[-200:]}")
    return text


async def render_waveform(path: str | Path, dest: Path) -> Path:
    """波形 PNG を作る（横軸 = 全尺）。"""
    code, text = await _run([
        jobs.FFMPEG, "-hide_banner", "-nostats", "-y", "-i", str(path),
        "-filter_complex", f"showwavespic=s={WAVEFORM_SIZE}:colors=#7ec8ff",
        "-frames:v", "1", str(dest),
    ])
    if code != 0 or not dest.is_file():
        raise jobs.JobError(f"波形画像を作れませんでした: {text.strip()[-200:]}")
    return dest


async def render_spectrogram(path: str | Path, dest: Path) -> Path:
    """スペクトログラム PNG を作る（横軸 = 全尺、縦軸 = 周波数）。"""
    code, text = await _run([
        jobs.FFMPEG, "-hide_banner", "-nostats", "-y", "-i", str(path),
        "-lavfi", f"showspectrumpic=s={SPECTROGRAM_SIZE}:legend=1",
        "-frames:v", "1", str(dest),
    ])
    if code != 0 or not dest.is_file():
        raise jobs.JobError(f"スペクトログラムを作れませんでした: {text.strip()[-200:]}")
    return dest


# --------------------------------------------------------------------------
# ffmpeg 出力のパース
# --------------------------------------------------------------------------

_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(
    r"silence_end:\s*(-?[\d.]+)\s*\|\s*silence_duration:\s*(-?[\d.]+)"
)
_INTEGRATED = re.compile(r"^\s*I:\s*(-?[\d.]+|-?inf)\s*LUFS", re.M)
_LRA = re.compile(r"^\s*LRA:\s*(-?[\d.]+|-?inf)\s*LU\s*$", re.M)
_TRUE_PEAK = re.compile(r"^\s*Peak:\s*(-?[\d.]+|-?inf)\s*dBFS", re.M)


def _number(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        return None


def parse_silences(text: str) -> list[Silence]:
    """``silencedetect`` のログを区間の一覧に直す。

    ``silence_start`` だけで終わっているログ（末尾まで無音のまま入力が終わった）
    も 1 区間として拾う。
    """
    found: list[Silence] = []
    pending: float | None = None
    for line in text.splitlines():
        match = _SILENCE_START.search(line)
        if match:
            pending = _number(match.group(1))
            continue
        match = _SILENCE_END.search(line)
        if match:
            end = _number(match.group(1))
            duration = _number(match.group(2))
            start = pending
            if start is None and end is not None and duration is not None:
                start = max(end - duration, 0.0)
            if start is not None:
                found.append(Silence(start=max(start, 0.0), end=end, duration=duration))
            pending = None
    if pending is not None:
        found.append(Silence(start=max(pending, 0.0)))
    return found


def parse_loudness(text: str) -> Loudness:
    """``ebur128`` のサマリ（Integrated / LRA / True peak）を読む。"""
    integrated = _INTEGRATED.search(text)
    lra = _LRA.search(text)
    peak = _TRUE_PEAK.search(text)
    return Loudness(
        integrated=_number(integrated.group(1)) if integrated else None,
        lra=_number(lra.group(1)) if lra else None,
        true_peak=_number(peak.group(1)) if peak else None,
    )


def _astats_value(text: str, label: str) -> float | None:
    match = re.search(
        rf"{re.escape(label)}:\s*(-?[\d.]+|-?inf|nan)", text
    )
    return _number(match.group(1)) if match else None


def parse_stats(text: str) -> Stats:
    """``astats`` の Overall からピーク / RMS / フラット率を読む。"""
    return Stats(
        peak_db=_astats_value(text, "Peak level dB"),
        rms_db=_astats_value(text, "RMS level dB"),
        flat_factor=_astats_value(text, "Flat factor"),
    )


# --------------------------------------------------------------------------
# STT（オプション: OpenAI 互換の外部エンドポイント）
# --------------------------------------------------------------------------

STT_MISSING_URL_MESSAGE = (
    "STT の接続先 URL が未設定のためスキップしました。"
    "設定 > 接続 / Grok の「文字起こしサーバー」に OpenAI 互換の URL"
    "（例 http://localhost:8000/v1）を入れてください。"
)


def transcriptions_url(base_url: str) -> str:
    """``{base_url}/audio/transcriptions``（``/v1`` 抜きの URL も受ける）。"""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if not base.endswith("/v1") and "/v1/" not in f"{base}/":
        base = f"{base}/v1"
    return f"{base}/audio/transcriptions"


def stt_timeout(duration: float | None) -> httpx.Timeout:
    """音声長に見合った制限時間（短いクリップでも最低 2 分は待つ）。"""
    read = max(STT_MIN_READ_TIMEOUT, (duration or 0.0) * STT_READ_TIMEOUT_FACTOR)
    return httpx.Timeout(
        connect=STT_CONNECT_TIMEOUT, read=read, write=read, pool=STT_CONNECT_TIMEOUT
    )


def parse_transcription(payload: str) -> tuple[list[SttSegment], str]:
    """応答本文を ``(区間一覧, 全文)`` に直す。

    ``verbose_json`` を返すサーバーは ``segments[]`` に時刻を持つ。素の
    ``text``（あるいは JSON ですらない生テキスト）しか返さないサーバーもあるので、
    その場合は時刻なしの全文だけを取り出す。
    """
    body = (payload or "").strip()
    if not body:
        return [], ""
    try:
        data = json.loads(body)
    except ValueError:
        return [], body  # verbose_json 非対応（`response_format=text` 相当）
    if not isinstance(data, dict):
        return [], body
    text = str(data.get("text") or "").strip()
    segments: list[SttSegment] = []
    for entry in data.get("segments") or []:
        if not isinstance(entry, dict):
            continue
        chunk = str(entry.get("text") or "").strip()
        if not chunk:
            continue
        segments.append(
            SttSegment(
                start=_as_float(entry.get("start")) or 0.0,
                end=_as_float(entry.get("end")) or 0.0,
                text=chunk,
            )
        )
        if len(segments) >= MAX_STT_SEGMENTS:
            break
    return segments, text


async def transcribe(
    path: str | Path,
    *,
    base_url: str,
    model: str = "",
    api_key: str = "",
    duration: float | None = None,
) -> tuple[list[SttSegment], str, str | None]:
    """外部の文字起こしサーバーに投げる。``(区間一覧, 全文, スキップ理由)``。

    ネットワーク I/O だけなので :class:`httpx.AsyncClient` でそのまま await する
    （このプロセスは推論を持たないので、スレッドへ逃がす必要はない）。接続不能・
    タイムアウト・非 2xx はどれも例外にせず、理由を返して inspect を続けさせる。
    """
    url = transcriptions_url(base_url)
    if not url:
        return [], "", STT_MISSING_URL_MESSAGE

    audio = Path(path)
    headers = {"Authorization": f"Bearer {api_key.strip()}"} if api_key.strip() else {}
    # `model` が空ならサーバーの既定に任せる（whisper.cpp server などは不要）。
    data = {"response_format": "verbose_json"}
    if (model or "").strip():
        data["model"] = model.strip()
    try:
        content = await asyncio.to_thread(audio.read_bytes)
        async with httpx.AsyncClient(timeout=stt_timeout(duration)) as client:
            response = await client.post(
                url,
                headers=headers,
                data=data,
                files={"file": (audio.name, content, "application/octet-stream")},
            )
    except httpx.HTTPError as exc:
        log.info("STT サーバーに接続できませんでした (%s): %s", url, exc)
        return [], "", f"STT サーバー（{url}）に接続できませんでした（{exc}）。"
    except OSError as exc:
        return [], "", f"STT に送る音声を読めませんでした（{exc}）。"
    if response.status_code >= 400:
        detail = response.text[:200].strip()
        return [], "", (
            f"STT サーバー（{url}）が HTTP {response.status_code} を返しました: {detail}"
        )
    segments, text = parse_transcription(response.text)
    return segments, text, None


# --------------------------------------------------------------------------
# レポート
# --------------------------------------------------------------------------

def _fmt(value: float | None, suffix: str = "", digits: int = 2) -> str:
    if value is None:
        return "不明"
    if value == float("-inf"):
        return f"-inf{suffix}"
    return f"{value:.{digits}f}{suffix}"


def _silence_place(silence: Silence, duration: float | None) -> str:
    if silence.start <= EDGE_TOLERANCE:
        return "先頭"
    if silence.end is None:
        return "末尾"
    if duration is not None and silence.end >= duration - EDGE_TOLERANCE:
        return "末尾"
    return "途中"


def format_report(
    *,
    stream: AudioStream | None,
    duration: float | None,
    silences: list[Silence] | None = None,
    loudness: Loudness | None = None,
    stats: Stats | None = None,
    images: dict[str, str] | None = None,
    stt_segments: list[SttSegment] | None = None,
    stt_text: str = "",
    stt_note: str | None = None,
    warnings: list[str] | None = None,
) -> str:
    """解析結果を人間（とエージェント）が読めるテキストにする。"""
    if stream is None:
        lines = ["【音声解析】この動画には音声トラックがありません（無音の動画です）。"]
        for warning in warnings or []:
            lines.append(f"- 注意: {warning}")
        return "\n".join(lines)

    spec = [part for part in (
        stream.codec,
        f"{stream.sample_rate} Hz" if stream.sample_rate else "",
        f"{stream.channels}ch" if stream.channels else "",
    ) if part]
    lines = ["【音声解析】"]
    lines.append(f"- 音声トラック: {' / '.join(spec) if spec else 'あり'}")
    lines.append(f"- 実尺: {_fmt(duration, ' 秒')}")

    if loudness is not None:
        lines.append(
            f"- 統合ラウドネス: {_fmt(loudness.integrated, ' LUFS', 1)}"
            f"（ラウドネスレンジ {_fmt(loudness.lra, ' LU', 1)} /"
            f" トゥルーピーク {_fmt(loudness.true_peak, ' dBFS', 1)}）"
        )
    if stats is not None:
        peak = stats.peak_db
        if peak is None:
            note = "判定不能"
        elif peak >= CLIPPING_PEAK_DB:
            note = "0 dBFS に達しておりクリッピングの疑いあり"
        elif peak >= NEAR_CLIPPING_PEAK_DB:
            note = "0 dBFS に近いので歪みに注意"
        else:
            note = "クリッピングの兆候なし"
        lines.append(
            f"- ピークレベル: {_fmt(peak, ' dBFS')}（{note}）"
            f" / RMS {_fmt(stats.rms_db, ' dBFS')}"
        )

    if silences is not None:
        if not silences:
            lines.append(
                f"- 無音区間（しきい値 {SILENCE_NOISE_DB:g}dB /"
                f" {SILENCE_MIN_DURATION:g} 秒以上）: なし"
            )
        else:
            lines.append(
                f"- 無音区間（しきい値 {SILENCE_NOISE_DB:g}dB /"
                f" {SILENCE_MIN_DURATION:g} 秒以上）: {len(silences)} か所"
            )
            for silence in silences:
                place = _silence_place(silence, duration)
                end = "末尾まで" if silence.end is None else f"{silence.end:.2f}s"
                length = (
                    "" if silence.duration is None else f"（{silence.duration:.2f} 秒）"
                )
                lines.append(f"  - {place} t={silence.start:.2f}s-{end}{length}")

    for label, name in (images or {}).items():
        lines.append(
            f"- {label}: {name}"
            f"（横軸は 0.00 秒から {_fmt(duration, ' 秒')}までの全尺に対応）"
        )

    if stt_note:
        lines.append(f"- 文字起こし: {stt_note}")
    elif stt_segments:
        lines.append("- 文字起こし（タイムスタンプつき）:")
        for segment in stt_segments:
            lines.append(
                f"  - t={segment.start:.1f}-{segment.end:.1f}s: 「{segment.text}」"
            )
    elif stt_text:
        # verbose_json 非対応のサーバー: 時刻が無いので全文だけ載せる。
        lines.append(
            "- 文字起こし（サーバーがタイムスタンプを返さないため全文のみ）:"
            f"\n  「{stt_text}」"
        )
    elif stt_segments is not None:
        lines.append("- 文字起こし: 発話は検出されませんでした。")

    for warning in warnings or []:
        lines.append(f"- 注意: {warning}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

async def analyze(
    media_path: str | Path,
    dest_dir: Path,
    *,
    stt_enabled: bool | None = None,
    stt_base_url: str | None = None,
    stt_model: str | None = None,
    stt_api_key: str | None = None,
) -> AudioInspection:
    """動画の音声を解析し、レポートと画像（波形 / スペクトログラム）を返す。

    どの工程が転んでも inspect 自体は続けられるよう、失敗はレポートの「注意」行
    として残す（黙って落とさない）。
    """
    settings = load_settings()
    if stt_enabled is None:
        stt_enabled = bool(settings.agent_stt_enabled)
    if stt_base_url is None:
        stt_base_url = settings.agent_stt_base_url
    if stt_model is None:
        stt_model = settings.agent_stt_model
    if stt_api_key is None:
        stt_api_key = settings.agent_stt_api_key

    warnings: list[str] = []
    try:
        stream = await probe_audio_stream(media_path)
    except jobs.JobError as exc:
        return AudioInspection(
            has_audio=False,
            report=format_report(
                stream=None, duration=None, warnings=[f"{exc}（音声解析をスキップしました）"]
            ),
            warnings=[str(exc)],
        )
    if stream is None:
        return AudioInspection(
            has_audio=False, report=format_report(stream=None, duration=None)
        )

    duration = stream.duration or await jobs.probe_media_duration(media_path)

    silences: list[Silence] | None = None
    loudness: Loudness | None = None
    stats: Stats | None = None
    try:
        log_text = await run_analysis(media_path)
    except jobs.JobError as exc:
        warnings.append(f"無音・ラウドネス・ピークの解析をスキップしました（{exc}）")
    else:
        silences = parse_silences(log_text)
        loudness = parse_loudness(log_text)
        stats = parse_stats(log_text)

    dest_dir.mkdir(parents=True, exist_ok=True)
    images: list[Path] = []
    labels: dict[str, str] = {}
    for label, name, render in (
        ("波形画像", WAVEFORM_NAME, render_waveform),
        ("スペクトログラム", SPECTROGRAM_NAME, render_spectrogram),
    ):
        try:
            path = await render(media_path, dest_dir / name)
        except jobs.JobError as exc:
            warnings.append(f"{label}の生成をスキップしました（{exc}）")
            continue
        images.append(path)
        labels[label] = name

    stt_segments: list[SttSegment] | None = None
    stt_text = ""
    stt_note: str | None = None
    if stt_enabled:
        stt_segments, stt_text, stt_note = await transcribe(
            media_path,
            base_url=stt_base_url or "",
            model=stt_model or "",
            api_key=stt_api_key or "",
            duration=duration,
        )
    else:
        stt_note = "設定で無効なため実行していません（設定 > 接続 / Grok で有効化）。"

    report = format_report(
        stream=stream,
        duration=duration,
        silences=silences,
        loudness=loudness,
        stats=stats,
        images=labels,
        stt_segments=stt_segments,
        stt_text=stt_text,
        stt_note=stt_note,
        warnings=warnings,
    )
    # STT の取りこぼしは（レポートの「文字起こし:」行とは別に）呼び出し側からも
    # 見えるよう warnings に残す。有効にしていないだけのときは警告ではない。
    all_warnings = list(warnings)
    if stt_enabled and stt_note:
        all_warnings.append(stt_note)
    return AudioInspection(
        has_audio=True, report=report, images=images, warnings=all_warnings
    )
