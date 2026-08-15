"""inspect の音声解析（AGENT-MODE §3.3, :mod:`app.audio_inspect`）。

エージェントは音を聴けないので、レポートの中身がそのまま判断材料になる。
ffmpeg の出力パース・レポート生成・失敗時のフォールバック（フィルタが転んでも
inspect を止めない / 文字起こしサーバーが落ちていても止めない）をここで固める。
STT は外部の OpenAI 互換エンドポイントなので、ネットワークには出ずに
``httpx.AsyncClient`` を偽物に差し替えて確かめる（test_comfy_errors.py と同じ方式）。
"""

import asyncio
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from app import audio_inspect, config, jobs
from app.audio_inspect import (
    AudioStream,
    Loudness,
    Silence,
    Stats,
    SttSegment,
    format_report,
    parse_audio_stream,
    parse_loudness,
    parse_silences,
    parse_stats,
    parse_transcription,
    transcriptions_url,
)
from app.models import Settings

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")

# 実際の `ffmpeg -af silencedetect,ebur128,astats -f null -` の抜粋
FFMPEG_LOG = """\
[silencedetect @ 0x1] silence_start: 0
[silencedetect @ 0x1] silence_end: 1.5093 | silence_duration: 1.5093
[silencedetect @ 0x1] silence_start: 4.01703
[silencedetect @ 0x1] silence_end: 5.50313 | silence_duration: 1.4861
[Parsed_astats_2 @ 0x2] Overall
[Parsed_astats_2 @ 0x2] DC offset: 0.000001
[Parsed_astats_2 @ 0x2] Peak level dB: -18.039665
[Parsed_astats_2 @ 0x2] RMS level dB: -23.120645
[Parsed_astats_2 @ 0x2] RMS peak dB: -21.054173
[Parsed_astats_2 @ 0x2] Flat factor: 0.000000
[Parsed_ebur128_1 @ 0x3] Summary:

  Integrated loudness:
    I:         -22.1 LUFS
    Threshold: -32.2 LUFS

  Loudness range:
    LRA:         2.2 LU
    Threshold: -43.7 LUFS
    LRA low:   -24.7 LUFS
    LRA high:  -22.5 LUFS

  True peak:
    Peak:      -18.0 dBFS
"""


# --------------------------------------------------------------------------
# ffmpeg / ffprobe 出力のパース
# --------------------------------------------------------------------------

def test_silences_are_read_as_intervals():
    silences = parse_silences(FFMPEG_LOG)
    assert [(s.start, s.end) for s in silences] == [(0.0, 1.5093), (4.01703, 5.50313)]
    assert silences[0].duration == pytest.approx(1.5093)


def test_a_silence_that_never_ends_is_still_reported():
    """末尾まで無音のまま入力が終わると ``silence_end`` が出ない。"""
    silences = parse_silences("[silencedetect @ 0x1] silence_start: 3.5\n")
    assert len(silences) == 1
    assert silences[0].start == 3.5
    assert silences[0].end is None


def test_silence_start_is_derived_when_only_the_end_line_is_there():
    silences = parse_silences(
        "[silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 1.0\n"
    )
    assert [(s.start, s.end) for s in silences] == [(1.5, 2.5)]


def test_loudness_summary_is_read():
    loudness = parse_loudness(FFMPEG_LOG)
    assert loudness.integrated == pytest.approx(-22.1)
    assert loudness.lra == pytest.approx(2.2)
    assert loudness.true_peak == pytest.approx(-18.0)


def test_missing_loudness_summary_leaves_everything_unknown():
    loudness = parse_loudness("nothing to see here")
    assert (loudness.integrated, loudness.lra, loudness.true_peak) == (None, None, None)


def test_astats_peak_and_rms_are_read():
    stats = parse_stats(FFMPEG_LOG)
    assert stats.peak_db == pytest.approx(-18.039665)
    assert stats.rms_db == pytest.approx(-23.120645)
    assert stats.flat_factor == pytest.approx(0.0)


def test_ffprobe_json_without_streams_means_no_audio():
    assert parse_audio_stream('{"streams": []}') is None
    assert parse_audio_stream("not json") is None


def test_ffprobe_json_is_read_into_a_stream():
    stream = parse_audio_stream(
        '{"streams": [{"codec_name": "aac", "channels": 2,'
        ' "sample_rate": "44100", "duration": "8.000000"}]}'
    )
    assert stream == AudioStream(
        codec="aac", channels=2, sample_rate=44100, duration=8.0
    )


def test_the_analysis_filter_keeps_ebur128_quiet():
    """ebur128 の 100ms ごとの経過ログを吐かせない（stderr が数百行になる）。"""
    chain = audio_inspect.analysis_filter()
    assert "silencedetect=noise=-40dB:d=0.4" in chain
    assert "framelog=quiet" in chain
    assert "astats" in chain


# --------------------------------------------------------------------------
# レポート
# --------------------------------------------------------------------------

def report_of(**kwargs) -> str:
    base = dict(
        stream=AudioStream(codec="aac", channels=2, sample_rate=44100, duration=8.0),
        duration=8.0,
        silences=parse_silences(FFMPEG_LOG),
        loudness=parse_loudness(FFMPEG_LOG),
        stats=parse_stats(FFMPEG_LOG),
    )
    base.update(kwargs)
    return format_report(**base)


def test_report_lists_the_measurements_and_locates_each_silence():
    report = report_of()
    assert "aac / 44100 Hz / 2ch" in report
    assert "実尺: 8.00 秒" in report
    assert "-22.1 LUFS" in report
    assert "クリッピングの兆候なし" in report
    assert "先頭 t=0.00s-1.51s" in report
    assert "途中 t=4.02s-5.50s" in report


def test_a_silence_touching_the_end_is_called_末尾():
    report = report_of(silences=[Silence(start=7.0, end=8.0, duration=1.0)])
    assert "末尾 t=7.00s-8.00s" in report


def test_a_peak_at_zero_dbfs_is_flagged_as_clipping():
    assert "クリッピングの疑いあり" in report_of(stats=Stats(peak_db=-0.05))
    assert "歪みに注意" in report_of(stats=Stats(peak_db=-0.6))


def test_no_audio_track_is_stated_explicitly():
    report = format_report(stream=None, duration=None)
    assert "音声トラックがありません" in report


def test_images_are_tied_to_the_clip_length():
    report = report_of(images={"波形画像": "audio_waveform.png"})
    assert "audio_waveform.png" in report
    assert "8.00 秒までの全尺に対応" in report


def test_a_skipped_filter_is_written_down_not_swallowed():
    report = report_of(
        loudness=None, stats=None, warnings=["波形画像の生成をスキップしました（…）"]
    )
    assert "注意: 波形画像の生成をスキップしました" in report


def test_transcript_lines_carry_timestamps():
    report = report_of(
        stt_segments=[SttSegment(start=1.2, end=2.4, text="こんにちは")]
    )
    assert "t=1.2-2.4s: 「こんにちは」" in report


def test_a_transcript_without_timestamps_is_shown_as_one_block():
    """verbose_json を返さないサーバー: 時刻が無くても全文は載せる。"""
    report = report_of(stt_segments=[], stt_text="こんにちは 元気ですか")
    assert "タイムスタンプを返さない" in report
    assert "「こんにちは 元気ですか」" in report


def test_an_empty_transcript_says_so():
    assert "発話は検出されませんでした" in report_of(stt_segments=[])


def test_unknown_values_are_printed_as_不明():
    report = report_of(loudness=Loudness(), stats=Stats(), duration=None)
    assert "実尺: 不明" in report
    assert "判定不能" in report


# --------------------------------------------------------------------------
# STT（OpenAI 互換の外部エンドポイント）
# --------------------------------------------------------------------------

VERBOSE_JSON = (
    '{"task": "transcribe", "language": "ja", "duration": 4.0,'
    ' "text": "こんにちは 元気ですか",'
    ' "segments": [{"id": 0, "start": 1.2, "end": 2.4, "text": " こんにちは"},'
    ' {"id": 1, "start": 2.8, "end": 4.0, "text": "元気ですか"}]}'
)


def stt_client(monkeypatch, response: httpx.Response | Exception):
    """``httpx.AsyncClient`` を、必ずこの応答（か例外）を返す偽物に差し替える。

    送った multipart / ヘッダを覗けるよう、呼び出しの記録を返す。
    """
    calls: list[dict] = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append({"timeout": kwargs.get("timeout")})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, url, **kwargs):
            calls[-1].update(url=url, **kwargs)
            if isinstance(response, Exception):
                raise response
            response.request = httpx.Request("POST", url)
            return response

    monkeypatch.setattr(audio_inspect.httpx, "AsyncClient", FakeClient)
    return calls


def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "clip.wav"
    path.write_bytes(b"RIFFfake")
    return path


@pytest.mark.parametrize(
    "base_url,expected",
    [
        ("http://localhost:8000/v1", "http://localhost:8000/v1/audio/transcriptions"),
        ("http://localhost:8000", "http://localhost:8000/v1/audio/transcriptions"),
        ("https://api.openai.com/v1/", "https://api.openai.com/v1/audio/transcriptions"),
        ("", ""),
    ],
)
def test_the_endpoint_is_derived_from_the_base_url(base_url, expected):
    assert transcriptions_url(base_url) == expected


def test_verbose_json_is_read_into_timestamped_segments():
    segments, text = parse_transcription(VERBOSE_JSON)
    assert [(s.start, s.end, s.text) for s in segments] == [
        (1.2, 2.4, "こんにちは"),
        (2.8, 4.0, "元気ですか"),
    ]
    assert text == "こんにちは 元気ですか"


def test_a_server_that_only_returns_text_falls_back_to_the_whole_transcript():
    assert parse_transcription('{"text": "こんにちは"}') == ([], "こんにちは")
    # `response_format=text` しか実装していないサーバー（JSON ですらない）
    assert parse_transcription("こんにちは") == ([], "こんにちは")


def test_the_read_timeout_grows_with_the_clip():
    assert audio_inspect.stt_timeout(None).read == audio_inspect.STT_MIN_READ_TIMEOUT
    assert audio_inspect.stt_timeout(600.0).read == pytest.approx(3600.0)


def test_transcribe_posts_the_audio_as_multipart(tmp_path, monkeypatch):
    calls = stt_client(monkeypatch, httpx.Response(200, text=VERBOSE_JSON))

    segments, text, note = asyncio.run(
        audio_inspect.transcribe(
            audio_file(tmp_path),
            base_url="http://localhost:8000/v1",
            model="whisper-1",
            api_key="sk-test",
            duration=4.0,
        )
    )

    assert note is None
    assert [s.text for s in segments] == ["こんにちは", "元気ですか"]
    assert text == "こんにちは 元気ですか"
    sent = calls[0]
    assert sent["url"] == "http://localhost:8000/v1/audio/transcriptions"
    assert sent["data"] == {"response_format": "verbose_json", "model": "whisper-1"}
    assert sent["headers"]["Authorization"] == "Bearer sk-test"
    assert sent["files"]["file"][0] == "clip.wav"
    assert sent["files"]["file"][1] == b"RIFFfake"


def test_no_api_key_means_no_authorization_header(tmp_path, monkeypatch):
    """ローカルのサーバーはキーを取らない（空のヘッダを送らない）。"""
    calls = stt_client(monkeypatch, httpx.Response(200, text=VERBOSE_JSON))

    asyncio.run(
        audio_inspect.transcribe(
            audio_file(tmp_path), base_url="http://localhost:8000/v1"
        )
    )

    assert calls[0]["headers"] == {}
    assert "model" not in calls[0]["data"]  # 空のモデル名はサーバー任せにする


def test_a_text_only_server_still_produces_a_transcript(tmp_path, monkeypatch):
    stt_client(monkeypatch, httpx.Response(200, text="こんにちは"))

    segments, text, note = asyncio.run(
        audio_inspect.transcribe(
            audio_file(tmp_path), base_url="http://localhost:8000/v1"
        )
    )

    assert (segments, text, note) == ([], "こんにちは", None)


def test_a_connection_failure_is_reported_not_raised(tmp_path, monkeypatch):
    stt_client(monkeypatch, httpx.ConnectError("connection refused"))

    segments, text, note = asyncio.run(
        audio_inspect.transcribe(
            audio_file(tmp_path), base_url="http://localhost:8000/v1"
        )
    )

    assert (segments, text) == ([], "")
    assert "接続できませんでした" in note
    assert "http://localhost:8000/v1/audio/transcriptions" in note


def test_a_timeout_is_reported_not_raised(tmp_path, monkeypatch):
    stt_client(monkeypatch, httpx.ReadTimeout("timed out"))

    _segments, _text, note = asyncio.run(
        audio_inspect.transcribe(
            audio_file(tmp_path), base_url="http://localhost:8000/v1"
        )
    )

    assert "接続できませんでした" in note


def test_a_non_2xx_answer_is_reported_with_its_body(tmp_path, monkeypatch):
    stt_client(monkeypatch, httpx.Response(401, text="invalid api key"))

    segments, _text, note = asyncio.run(
        audio_inspect.transcribe(
            audio_file(tmp_path), base_url="http://localhost:8000/v1"
        )
    )

    assert segments == []
    assert "HTTP 401" in note
    assert "invalid api key" in note


def test_an_unset_url_skips_stt_with_an_explanation(tmp_path, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("URL が無いのに接続してはいけない")

    monkeypatch.setattr(audio_inspect.httpx, "AsyncClient", never)
    segments, text, note = asyncio.run(
        audio_inspect.transcribe(audio_file(tmp_path), base_url="  ")
    )

    assert (segments, text) == ([], "")
    assert note == audio_inspect.STT_MISSING_URL_MESSAGE
    assert "接続先 URL が未設定" in note


# --------------------------------------------------------------------------
# 通し（ffmpeg を実際に回す）
# --------------------------------------------------------------------------

def make_video(path: Path, *, with_audio: bool) -> Path:
    """8 秒のテスト動画（音声つきなら先頭と途中に無音区間を入れる）。"""
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=12:duration=8",
    ]
    if with_audio:
        cmd += [
            "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
            "-af", "volume=enable='between(t,0,1.5)':volume=0",
            "-c:a", "aac", "-shortest",
        ]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture(autouse=True)
def stt_off(monkeypatch):
    """設定は既定（STT 無効）で読む。"""
    monkeypatch.setattr(config, "_settings", Settings())


@needs_ffmpeg
def test_analyze_measures_a_real_clip_and_writes_both_images(tmp_path):
    video = make_video(tmp_path / "clip.mp4", with_audio=True)
    dest = tmp_path / "inspect"

    result = asyncio.run(audio_inspect.analyze(video, dest, stt_enabled=False))

    assert result.has_audio
    assert [image.name for image in result.images] == [
        audio_inspect.WAVEFORM_NAME,
        audio_inspect.SPECTROGRAM_NAME,
    ]
    assert all(image.is_file() and image.stat().st_size for image in result.images)
    assert "実尺: 8.0" in result.report
    assert "LUFS" in result.report
    assert "先頭 t=0.00s" in result.report  # 頭の無音を拾えている
    assert not result.warnings


@needs_ffmpeg
def test_analyze_says_so_when_the_clip_has_no_audio_track(tmp_path):
    video = make_video(tmp_path / "silent.mp4", with_audio=False)

    result = asyncio.run(audio_inspect.analyze(video, tmp_path / "inspect", stt_enabled=False))

    assert not result.has_audio
    assert "音声トラックがありません" in result.report
    assert not result.images


@needs_ffmpeg
def test_a_failing_filter_does_not_sink_the_whole_analysis(tmp_path, monkeypatch):
    video = make_video(tmp_path / "clip.mp4", with_audio=True)

    async def boom(path, dest):
        raise jobs.JobError("showspectrumpic が無い")

    monkeypatch.setattr(audio_inspect, "render_spectrogram", boom)
    result = asyncio.run(audio_inspect.analyze(video, tmp_path / "inspect", stt_enabled=False))

    assert result.has_audio  # 他の項目は生きている
    assert [image.name for image in result.images] == [audio_inspect.WAVEFORM_NAME]
    assert any("スペクトログラム" in warning for warning in result.warnings)
    assert "注意: スペクトログラムの生成をスキップしました" in result.report


@needs_ffmpeg
def test_analyze_takes_the_stt_connection_from_the_settings(tmp_path, monkeypatch):
    video = make_video(tmp_path / "clip.mp4", with_audio=True)
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(
            agent_stt_enabled=True,
            agent_stt_base_url="http://localhost:8000/v1",
            agent_stt_model="whisper-1",
            agent_stt_api_key="sk-test",
        ),
    )
    calls: list[dict] = []

    async def fake_transcribe(path, *, base_url, model, api_key, duration):
        calls.append(
            {"base_url": base_url, "model": model, "key": api_key, "duration": duration}
        )
        return [SttSegment(start=1.2, end=2.4, text="こんにちは")], "こんにちは", None

    monkeypatch.setattr(audio_inspect, "transcribe", fake_transcribe)
    result = asyncio.run(audio_inspect.analyze(video, tmp_path / "inspect"))

    assert calls[0]["base_url"] == "http://localhost:8000/v1"
    assert calls[0]["model"] == "whisper-1"
    assert calls[0]["key"] == "sk-test"
    assert calls[0]["duration"] == pytest.approx(8.0, abs=0.5)  # 尺を渡す（制限時間用）
    assert "t=1.2-2.4s: 「こんにちは」" in result.report


@needs_ffmpeg
def test_an_enabled_but_unreachable_server_only_costs_the_transcript(
    tmp_path, monkeypatch
):
    """STT が転んでも他の解析は残り、理由はレポートと warnings の両方に出る。"""
    video = make_video(tmp_path / "clip.mp4", with_audio=True)
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(agent_stt_enabled=True, agent_stt_base_url="http://localhost:9/v1"),
    )
    stt_client(monkeypatch, httpx.ConnectError("connection refused"))

    result = asyncio.run(audio_inspect.analyze(video, tmp_path / "inspect"))

    assert result.has_audio
    assert "LUFS" in result.report
    assert "文字起こし: STT サーバー" in result.report
    assert any("接続できませんでした" in warning for warning in result.warnings)


@needs_ffmpeg
def test_an_enabled_stt_without_a_url_says_so_in_the_report(tmp_path, monkeypatch):
    video = make_video(tmp_path / "clip.mp4", with_audio=True)
    monkeypatch.setattr(config, "_settings", Settings(agent_stt_enabled=True))

    result = asyncio.run(audio_inspect.analyze(video, tmp_path / "inspect"))

    assert "接続先 URL が未設定" in result.report


@needs_ffmpeg
def test_stt_stays_off_unless_the_setting_is_on(tmp_path, monkeypatch):
    video = make_video(tmp_path / "clip.mp4", with_audio=True)

    async def fail(path, **kwargs):
        raise AssertionError("STT は既定で走らない")

    monkeypatch.setattr(audio_inspect, "transcribe", fail)
    result = asyncio.run(audio_inspect.analyze(video, tmp_path / "inspect"))

    assert "文字起こし: 設定で無効" in result.report
