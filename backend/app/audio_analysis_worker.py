#!/usr/bin/env python3
"""音源解析の本体（歌詞アライン・書き起こし・onset・ビート・無音区間）。

**このファイルだけは :mod:`app` パッケージに依存しない**。理由は
:mod:`app.audio_analysis` に書いたとおりで、解析に要る依存（torch /
faster-whisper / stable-ts / librosa）は数 GB になるためアプリの環境には入れず、
**外で構築した別の venv の python でこのスクリプトだけを走らせる**（ComfyUI・
Remotion と同じ「外のバックエンドを参照する」やり方）。したがってここで import
してよいのは標準ライブラリと、その venv に入っている解析ライブラリだけ。

使い方（アプリからはサブプロセスとして呼ばれる）::

    python audio_analysis_worker.py --audio <音源> --out <analysis.json> \
        [--tasks align,onsets,beats,silence] [--lyrics <行区切りのテキスト>] \
        [--stem <ステム>]... [--language ja] [--model small] \
        [--substitutions '{"BAN!": "バン"}']

    python audio_analysis_worker.py --check --tasks align,transcribe

進捗は標準出力に ``PROGRESS <0..1> <メッセージ>`` の 1 行ずつで出す（呼び出し元が
WS へ流す）。それ以外の行はそのままログとして扱われる。

終了コード:

- ``0`` … 解析できた（``--out`` に JSON を書いた）
- ``1`` … 実行時のエラー（理由は標準エラーへ）
- ``2`` … 引数が不正
- ``3`` … **依存が入っていない**（呼び出し元はジョブの失敗ではなく「設定不足」
  として 400 にする）

依存の要否はタスクごとに違う:

- ``align`` … stable-ts（+ openai-whisper）。無ければ 3
- ``transcribe`` … faster-whisper。無ければ 3
- ``onsets`` / ``beats`` … librosa。**無ければそのタスクだけ飛ばして
  ``warnings`` に書く**（歌詞まわりが動けば大半の用は足りるため）
- ``silence`` … ffmpeg だけ。同じく無ければ飛ばして ``warnings`` に書く
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

#: 依存が足りない（呼び出し元が 400 にする）
EXIT_MISSING_DEPENDENCY = 3
#: 引数が不正
EXIT_USAGE = 2

#: 実行できるタスク（既定は歌詞があれば全部）
TASKS: tuple[str, ...] = ("align", "transcribe", "onsets", "beats", "silence")

#: タスク -> 「無いと実行できない」依存 [(import 名, pip 名), …]
REQUIRED: dict[str, list[tuple[str, str]]] = {
    # stable-ts は openai-whisper のモデルを読む（`stable_whisper.load_model`）
    "align": [("stable_whisper", "stable-ts"), ("whisper", "openai-whisper")],
    "transcribe": [("faster_whisper", "faster-whisper")],
}

#: タスク -> 「無ければそのタスクだけ飛ばす」依存
OPTIONAL: dict[str, list[tuple[str, str]]] = {
    "onsets": [("librosa", "librosa"), ("numpy", "numpy")],
    "beats": [("librosa", "librosa"), ("numpy", "numpy")],
}

#: アラインの前に必ず落とす記号（読みに現れないので秒がずれる元になる）
STRIP_CHARS = "？?！!…「」『』“”\"'"

#: 無音とみなす音量と、その長さの下限（ffmpeg の silencedetect）
SILENCE_NOISE = "-40dB"
SILENCE_MIN_DURATION = 0.5

#: onset / beat 検出に使うサンプリングレート（ffmpeg で落としてから渡す）
ANALYSIS_SR = 22050


# --------------------------------------------------------------------------
# 進捗と依存の確認
# --------------------------------------------------------------------------

def progress(fraction: float, message: str) -> None:
    """呼び出し元（:mod:`app.audio_analysis`）が読む進捗の 1 行。"""
    print(f"PROGRESS {min(max(fraction, 0.0), 1.0):.3f} {message}", flush=True)


def log(message: str) -> None:
    print(message, flush=True)


def _available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def missing_requirements(tasks: list[str]) -> list[str]:
    """``tasks`` に要る依存のうち入っていない pip 名（重複なし・順番どおり）。

    **import はしない**（faster-whisper / torch の import は数秒かかるので、
    ジョブ投入時の事前確認に使えなくなる）。
    """
    missing: list[str] = []
    for task in tasks:
        for module, package in REQUIRED.get(task, []):
            if not _available(module) and package not in missing:
                missing.append(package)
    return missing


def _skip_reason(task: str) -> str:
    """``task`` を飛ばす理由（実行できるなら空文字）。"""
    for module, package in OPTIONAL.get(task, []):
        if not _available(module):
            return f"{package} が入っていないので {task} は飛ばしました"
    if task == "silence" and shutil.which("ffmpeg") is None:
        return "ffmpeg が無いので silence は飛ばしました"
    return ""


# --------------------------------------------------------------------------
# ffmpeg / ffprobe
# --------------------------------------------------------------------------

def probe(path: Path) -> tuple[float | None, int | None]:
    """``(尺の秒, サンプリングレート)``。読めなければ None。"""
    if shutil.which("ffprobe") is None:
        return None, None
    try:
        raw = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration:stream=sample_rate",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, check=True,
        ).stdout
        data = json.loads(raw)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None
    try:
        duration = round(float(data["format"]["duration"]), 3)
    except (KeyError, TypeError, ValueError):
        duration = None
    rate: int | None = None
    for stream in data.get("streams") or []:
        try:
            rate = int(stream["sample_rate"])
        except (KeyError, TypeError, ValueError):
            continue
        break
    return duration, rate


def decode(path: Path, sample_rate: int):
    """音源をモノラルの float32 配列に落とす（numpy が要る）。

    librosa の読み込み（soundfile / audioread）に頼らず ffmpeg で落とすのは、
    mp3・m4a・ステム WAV のどれでも同じ経路で読めるようにするため
    （BAN!BAN!BAN! の ``detect_ban_onsets.py`` と同じやり方）。
    """
    import numpy as np

    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(path),
            "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-",
        ],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silence(path: Path, duration: float | None) -> list[dict]:
    """ffmpeg の ``silencedetect`` で無音区間を拾う。

    末尾が無音のまま終わると ``silence_end`` が出ないので、そのときは音源の尺で
    閉じる（読めなければその区間は捨てる）。
    """
    result = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-i", str(path),
            "-af",
            f"silencedetect=noise={SILENCE_NOISE}:d={SILENCE_MIN_DURATION}",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    spans: list[dict] = []
    start: float | None = None
    for line in (result.stderr or "").splitlines():
        opened = _SILENCE_START.search(line)
        if opened:
            start = float(opened.group(1))
        closed = _SILENCE_END.search(line)
        if closed and start is not None:
            spans.append({"start": round(start, 3), "end": round(float(closed.group(1)), 3)})
            start = None
    if start is not None and duration:
        spans.append({"start": round(start, 3), "end": round(duration, 3)})
    return spans


# --------------------------------------------------------------------------
# librosa（onset / beat）
# --------------------------------------------------------------------------

def detect_onsets(path: Path) -> list[dict]:
    """立ち上がり（``[{"t": 秒, "strength": 0..1}]``）。

    強さはピークの onset strength を最大値で割ったもの。秒は ``backtrack=True``
    で「立ち上がりの根元」まで戻した値を使う（演出を当てるのはこちら側）。
    """
    import librosa
    import numpy as np

    y = decode(path, ANALYSIS_SR)
    envelope = librosa.onset.onset_strength(y=y, sr=ANALYSIS_SR)
    peaks = librosa.onset.onset_detect(
        onset_envelope=envelope, sr=ANALYSIS_SR, backtrack=False
    )
    if not len(peaks):
        return []
    starts = librosa.onset.onset_detect(
        onset_envelope=envelope, sr=ANALYSIS_SR, backtrack=True
    )
    times = librosa.frames_to_time(starts, sr=ANALYSIS_SR)
    top = float(np.max(envelope[peaks])) or 1.0
    return [
        {
            "t": round(float(time), 3),
            "strength": round(float(envelope[peak]) / top, 3),
        }
        for time, peak in zip(times, peaks)
    ]


def detect_beats(path: Path) -> dict:
    """``{"bpm": …, "times": [秒, …]}``（拍が取れなければ空）。"""
    import librosa
    import numpy as np

    y = decode(path, ANALYSIS_SR)
    tempo, beats = librosa.beat.beat_track(y=y, sr=ANALYSIS_SR, units="time")
    bpm = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0
    return {
        "bpm": round(bpm, 2),
        "times": [round(float(t), 3) for t in np.atleast_1d(beats)],
    }


# --------------------------------------------------------------------------
# whisper（アライン / 書き起こし）
# --------------------------------------------------------------------------

def torch_device() -> str:
    """``cuda`` が使えるなら cuda、無ければ cpu。"""
    try:
        import torch
    except ImportError:
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _is_out_of_memory(exc: BaseException) -> bool:
    """GPU のメモリ不足か（型名を見るのは torch を import せずに済ませるため）。"""
    return "OutOfMemory" in type(exc).__name__ or "out of memory" in str(exc).lower()


def with_cpu_fallback(load, warnings: list[str], label: str):
    """``load(device)`` を GPU で試し、メモリ不足なら CPU でやり直す。

    GPU は ComfyUI と取り合いになる（生成が走っているあいだは空きが数百 MB しか
    無い）ので、**落とさずに遅くする**ほうが解析としては使える。
    """
    device = torch_device()
    if device == "cpu":
        return load("cpu"), "cpu"
    try:
        return load(device), device
    except Exception as exc:  # noqa: BLE001 - OOM だけを CPU に倒す
        if not _is_out_of_memory(exc):
            raise
        message = f"GPU のメモリが足りないので {label} は CPU で実行しました"
        warnings.append(message)
        log(message)
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001 - 掃除できなくても続ける
            pass
        return load("cpu"), "cpu"


def prepare_line(line: str, substitutions: dict[str, str]) -> str:
    """アラインに渡す 1 行（置換 → 記号落とし）。

    「BAN!」のような英字＋感嘆符は whisper が読みを当てにくいので、呼び出し側が
    ``{"BAN!": "バン"}`` のような置換を渡す。置換は**長いキーから**当てる
    （``BAN!`` と ``BAN`` の両方を渡されても取りこぼさない）。
    """
    text = line
    for key in sorted(substitutions, key=len, reverse=True):
        if key:
            text = text.replace(key, substitutions[key])
    for char in STRIP_CHARS:
        text = text.replace(char, "")
    return " ".join(text.split())


def chars_of(word: str, start: float, end: float) -> list[dict]:
    """1 語の秒を 1 文字ずつに割る（語の中は等分）。

    whisper が返すのは語単位なので、カラオケ表示（``FxOverlay`` の ``lyric``）が
    要る 1 文字ごとの秒はここで作る。**語の頭は実測**なので、ずれるのは語の中だけ。
    """
    text = word.strip()
    if not text:
        return []
    step = max(0.0, end - start) / len(text)
    return [
        {
            "c": char,
            "s": round(start + index * step, 3),
            "e": round(start + (index + 1) * step, 3),
        }
        for index, char in enumerate(text)
    ]


def align_lines(
    path: Path, lines: list[str], *, language: str, model_name: str,
    substitutions: dict[str, str], warnings: list[str],
) -> list[dict]:
    """歌詞テキストを音源に当てて、行と 1 文字ごとの秒を出す。"""
    import stable_whisper

    prepared = [prepare_line(line, substitutions) for line in lines]
    progress(0.1, f"アライン: モデル {model_name} を読み込み中")

    def align_on(device: str):
        progress(0.2, f"アライン: {len(lines)} 行を音源に当てています（{device}）")
        model = stable_whisper.load_model(model_name, device=device)
        return model.align(
            str(path), "\n".join(prepared), language=language,
            original_split=True, vad=False,
        )

    result, _device = with_cpu_fallback(align_on, warnings, "アライン")
    segments = list(result.segments)
    if len(segments) != len(lines):
        warnings.append(
            f"アラインの結果が {len(segments)} 行で、歌詞の {len(lines)} 行と"
            "合いません（前から順に対応させました）"
        )
    out: list[dict] = []
    for index, (segment, original) in enumerate(zip(segments, lines)):
        chars: list[dict] = []
        for word in segment.words or []:
            chars.extend(chars_of(word.word, word.start, word.end))
        line = {
            "i": index + 1,
            "start": round(segment.start, 3),
            "end": round(segment.end, 3),
            "text": original,
            "chars": chars,
        }
        # 置換・記号落としで実際に当てた文字列が変わった行は、``chars`` が
        # そちらの読みになっているので断っておく（カラオケ表示で使う側の判断材料）。
        if prepared[index] != original:
            line["aligned_text"] = prepared[index]
        out.append(line)
    return out


def transcribe(
    path: Path, *, language: str, model_name: str, warnings: list[str]
) -> list[dict]:
    """歌詞が無いときの自由書き起こし（``chars`` は語単位）。"""
    from faster_whisper import WhisperModel

    progress(0.1, f"書き起こし: モデル {model_name} を読み込み中")

    def run_on(device: str) -> list:
        progress(0.2, f"書き起こし: 実行中（{device}）")
        # CPU では float16 が使えないので int8（遅いが動く）
        model = WhisperModel(
            model_name, device=device,
            compute_type="float16" if device == "cuda" else "int8",
        )
        segments, _info = model.transcribe(
            str(path), language=language, word_timestamps=True,
            beam_size=5, condition_on_previous_text=False,
        )
        # faster-whisper は遅延評価なので、**ここで**読み切る（そうしないと
        # GPU のメモリ不足がこの関数の外で起きて CPU に倒せない）。
        return list(segments)

    segments, _device = with_cpu_fallback(run_on, warnings, "書き起こし")
    out: list[dict] = []
    for index, segment in enumerate(segments):
        text = (segment.text or "").strip()
        out.append({
            "i": index + 1,
            "start": round(segment.start, 3),
            "end": round(segment.end, 3),
            "text": text,
            "chars": [
                {
                    "c": word.word.strip(),
                    "s": round(word.start, 3),
                    "e": round(word.end, 3),
                }
                for word in (segment.words or [])
                if word.word.strip()
            ],
        })
    if not out:
        warnings.append("書き起こしで 1 行も取れませんでした")
    return out


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", help="解析する音源（フルミックス）")
    parser.add_argument("--out", help="書き出す analysis.json")
    parser.add_argument(
        "--tasks", default="", help=f"カンマ区切り（{', '.join(TASKS)}）。空 = 全部"
    )
    parser.add_argument("--lyrics", help="行区切りの歌詞ファイル（あれば align）")
    parser.add_argument(
        "--stem", action="append", default=[],
        help="ボーカルステム等（onset はこちらの先頭から採る）",
    )
    parser.add_argument("--language", default="ja")
    parser.add_argument("--model", default="small")
    parser.add_argument(
        "--substitutions", default="", help='アライン前の置換（JSON。例 {"BAN!": "バン"}）'
    )
    parser.add_argument(
        "--check", action="store_true",
        help="依存が入っているかだけを確かめる（解析はしない）",
    )
    return parser.parse_args(argv)


def selected_tasks(raw: str, *, has_lyrics: bool) -> list[str]:
    """指定されたタスク（空なら全部）。``align`` は歌詞があるときだけ。"""
    names = [name.strip() for name in raw.split(",") if name.strip()] or list(TASKS)
    unknown = [name for name in names if name not in TASKS]
    if unknown:
        raise ValueError(
            f"unknown task: {', '.join(unknown)}（{', '.join(TASKS)} から選んでください）"
        )
    if not has_lyrics:
        names = [name for name in names if name != "align"]
    # 歌詞があるなら align が本命なので、同時に指定された transcribe は落とす
    elif "align" in names:
        names = [name for name in names if name != "transcribe"]
    return list(dict.fromkeys(names))


def run(args: argparse.Namespace) -> int:
    lines: list[str] = []
    if args.lyrics and not args.check:
        # ``--check`` は依存を見るだけなので歌詞ファイルは読まない（呼び出し元は
        # 「歌詞つきで投げるつもりがある」ことだけを ``--lyrics`` で伝えてくる）。
        lines = [
            line.strip()
            for line in Path(args.lyrics).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    try:
        tasks = selected_tasks(
            args.tasks, has_lyrics=bool(lines) or (args.check and bool(args.lyrics))
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE

    missing = missing_requirements(tasks)
    if missing:
        print(
            f"{' / '.join(missing)} が入っていません"
            f"（{', '.join(t for t in tasks if t in REQUIRED)} に必要です）",
            file=sys.stderr,
        )
        return EXIT_MISSING_DEPENDENCY
    if args.check:
        return 0

    if not args.audio or not args.out:
        print("--audio と --out は必須です", file=sys.stderr)
        return EXIT_USAGE
    audio = Path(args.audio)
    if not audio.is_file():
        print(f"音源がありません: {audio}", file=sys.stderr)
        return EXIT_USAGE
    stems = [Path(stem) for stem in args.stem if str(stem).strip()]
    for stem in stems:
        if not stem.is_file():
            print(f"ステムがありません: {stem}", file=sys.stderr)
            return EXIT_USAGE
    try:
        substitutions = json.loads(args.substitutions) if args.substitutions else {}
    except ValueError as exc:
        print(f"--substitutions が JSON ではありません: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if not isinstance(substitutions, dict):
        print("--substitutions はオブジェクトで渡してください", file=sys.stderr)
        return EXIT_USAGE

    warnings: list[str] = []
    duration, sample_rate = probe(audio)
    result: dict = {
        "duration": duration,
        "sample_rate": sample_rate,
        "lines": [],
        "onsets": [],
        "beats": {},
        "silence": [],
        # 手で書き足す欄（セクション境界。解析では埋めない）
        "sections": [],
        "warnings": warnings,
    }
    # onset は「あればステム、無ければフルミックス」から採る（アラインの秒より
    # 実測の立ち上がりのほうが演出には効く。EDITING.md §3.2）。
    onset_source = stems[0] if stems else audio

    done = 0
    for task in tasks:
        skip = _skip_reason(task)
        if skip:
            warnings.append(skip)
            log(skip)
            done += 1
            continue
        progress(done / len(tasks), f"{task} を実行中")
        if task == "align":
            result["lines"] = align_lines(
                stems[0] if stems else audio, lines,
                language=args.language, model_name=args.model,
                substitutions=substitutions, warnings=warnings,
            )
        elif task == "transcribe":
            result["lines"] = transcribe(
                audio, language=args.language, model_name=args.model,
                warnings=warnings,
            )
        elif task == "onsets":
            result["onsets"] = detect_onsets(onset_source)
        elif task == "beats":
            result["beats"] = detect_beats(audio)
        elif task == "silence":
            result["silence"] = detect_silence(audio, duration)
        done += 1
        progress(done / len(tasks), f"{task} が終わりました")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    log(f"analysis.json を書きました: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        print(f"外部コマンドが失敗しました: {detail.strip()[:400]}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - 失敗の理由は 1 行で呼び出し元へ
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
