"""編集タブ: 映像クリップの加工・音声トラックのダッキング・FX 付き書き出しの音。

組み立て（:func:`app.timeline_export.build_command`）は純関数なので文字列で固定し、
ffmpeg が手元にあるときだけ実際に焼いてフレーム数と音量を確かめる。
"""

import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import timeline as service
from app import timeline_export
from app.main import app
from app.models import TimelineClipEffects, TimelineClipInput
from app.timeline_export import (
    ClipEffects,
    ExportAudioClip,
    ExportClip,
    ExportDucking,
    ExportSpec,
    build_command,
    duck_expression,
    duck_intervals,
    pad_warnings,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _graph(spec: ExportSpec) -> str:
    command = build_command(spec, "/o.mp4")
    return command[command.index("-filter_complex") + 1]


def _clip(**kwargs) -> ExportClip:
    defaults = dict(path="/x/a.mp4", in_ms=0, out_ms=2000, duration_ms=2000)
    defaults.update(kwargs)
    return ExportClip(**defaults)


# --------------------------------------------------------------------------
# 加工（純関数）
# --------------------------------------------------------------------------

def test_default_effects_leave_the_command_untouched():
    plain = ExportSpec(width=640, height=360, fps=24, clips=[_clip()])
    with_defaults = ExportSpec(
        width=640, height=360, fps=24, clips=[_clip(effects=ClipEffects())]
    )
    assert build_command(plain, "/o.mp4") == build_command(with_defaults, "/o.mp4")


def test_freeze_takes_one_frame_and_holds_it_for_the_clip():
    spec = ExportSpec(
        width=640,
        height=360,
        fps=24,
        clips=[
            _clip(
                in_ms=1000,
                out_ms=1000,
                duration_ms=1500,
                has_audio=True,
                effects=ClipEffects(freeze=True),
            )
        ],
    )
    graph = _graph(spec)
    assert "[0:v]trim=start=1.000000:end=1.500000" in graph
    assert "trim=end_frame=1,tpad=stop_mode=clone:stop_duration=1.750" in graph
    assert "trim=end_frame=36" in graph  # 1.5 秒 = 36f
    # 止めているあいだの音は無音（素材の音は使わない）
    assert "[0:a]" not in graph
    assert spec.total_frames == 36


def test_freeze_outside_the_source_holds_the_last_frame_and_warns():
    spec = ExportSpec(
        width=640,
        height=360,
        fps=24,
        clips=[
            _clip(
                in_ms=5000,
                out_ms=5000,
                duration_ms=1000,
                source_duration_ms=2000,
                name="外",
                effects=ClipEffects(freeze=True),
            )
        ],
    )
    graph = _graph(spec)
    # 2 秒 = 48f の素材の最後のコマ（47f）
    assert "trim=start=1.958333" in graph
    warnings = pad_warnings(spec)
    assert len(warnings) == 1 and warnings[0].startswith("FREEZE 外")


def test_reverse_flips_video_and_audio_and_keeps_the_speed():
    spec = ExportSpec(
        width=640,
        height=360,
        fps=24,
        clips=[
            _clip(
                in_ms=0,
                out_ms=2000,
                duration_ms=1000,
                speed=2.0,
                effects=ClipEffects(reverse=True),
            )
        ],
    )
    graph = _graph(spec)
    # 逆再生は余分の 1 フレームを取らない（反転すると先頭に来るため）
    assert "[0:v]trim=start=0.000000:end=2.000000" in graph
    assert "setpts=PTS-STARTPTS,reverse,setpts=(PTS-STARTPTS)/2" in graph
    assert "areverse" in graph
    assert "atempo=2" in graph


def test_zoom_step_colour_and_noise_sit_around_the_tail_hold():
    effects = ClipEffects(
        zoom=1.2, zoom_x=0.5, zoom_y=0.3, step=2,
        saturation=0.6, contrast=1.15, brightness=0.05, gamma=1.1, noise=0.5,
    )
    spec = ExportSpec(
        width=1080, height=1920, fps=30, clips=[_clip(effects=effects)]
    )
    graph = _graph(spec)
    # 寄り: 1.2 倍に拡大して、上寄り（0.3）で切り出す
    assert "scale=1296:2304,crop=1080:1920:108:115" in graph
    # コマ打ち: 15fps に間引いてから 30fps へ戻す
    assert "fps=15,fps=30" in graph
    assert "eq=saturation=0.6:contrast=1.15:brightness=0.05:gamma=1.1" in graph
    assert "noise=alls=32:allf=t" in graph
    # 寄りとコマ打ちは末尾静止の前、色とノイズは後
    assert graph.index("crop=1080") < graph.index("tpad=") < graph.index("eq=")
    assert spec.total_frames == 60


def test_image_clips_take_the_zoom_and_colour_too():
    spec = ExportSpec(
        width=640,
        height=360,
        fps=24,
        clips=[
            ExportClip(
                path="/x/still.png", in_ms=0, out_ms=1000, duration_ms=1000,
                kind="image", has_audio=False,
                effects=ClipEffects(zoom=2.0, saturation=0.0),
            )
        ],
    )
    graph = _graph(spec)
    assert "scale=1280:720,crop=640:360:320:180" in graph
    assert "eq=saturation=0" in graph


# --------------------------------------------------------------------------
# ダッキング（純関数）
# --------------------------------------------------------------------------

def test_duck_intervals_merges_spans_whose_ramps_touch():
    spans = [(1000, 2000), (2200, 3000), (6000, 7000)]
    assert duck_intervals(spans, 100, 300) == [(1000, 3000), (6000, 7000)]
    assert duck_intervals([], 100, 300) == []


def test_duck_expression_builds_one_trapezoid_per_group():
    expression = duck_expression([(1000, 2000)], 8, 100, 300)
    assert expression == (
        "pow(10\\,-8*(clip(min((t-0.900)/0.100\\,(2.300-t)/0.300)\\,0\\,1))/20)"
    )
    assert duck_expression([(1000, 2000)], 0, 100, 300) is None
    assert duck_expression([], 8, 100, 300) is None


def _ducked_spec(mode: str) -> ExportSpec:
    return ExportSpec(
        width=640,
        height=360,
        fps=24,
        clips=[_clip(out_ms=5000, duration_ms=5000)],
        audio_clips=[
            ExportAudioClip(path="/x/bgm.wav", start_ms=0, in_ms=0, out_ms=5000, track="A1"),
            ExportAudioClip(path="/x/v1.wav", start_ms=1000, in_ms=0, out_ms=1000, track="A2"),
            ExportAudioClip(path="/x/v2.wav", start_ms=3000, in_ms=0, out_ms=800, track="A2"),
        ],
        ducking=[
            ExportDucking(target="A1", trigger="A2", amount_db=10, mode=mode)
        ],
    )


def test_clip_ducking_lowers_only_the_target_track():
    graph = _graph(_ducked_spec("clips"))
    lines = graph.split(";")
    bgm = next(line for line in lines if line.endswith("[m0]"))
    assert "volume=pow(10\\,-10*(" in bgm and bgm.endswith(":eval=frame[m0]")
    assert "(t-0.900)" in bgm and "(t-2.900)" in bgm
    voices = [line for line in lines if line.endswith(("[m1]", "[m2]"))]
    assert all("eval=frame" not in line for line in voices)
    assert "amix=inputs=4:duration=first" in graph


def test_signal_ducking_runs_a_sidechain_compressor():
    graph = _graph(_ducked_spec("signal"))
    assert "[m1][m2]amix=inputs=2:duration=longest" in graph
    assert "asplit=2[dk0m][dk0c0]" in graph
    assert "[m0][dk0c0]sidechaincompress=threshold=0.031623:ratio=20" in graph
    # 下げ幅は mix で 10 dB に抑える（1 - 10^(-10/20)）
    assert ":mix=0.6838[dk0d0]" in graph
    assert "[dk0d0][dk0m]amix=inputs=3:duration=first" in graph


def test_ducking_without_a_trigger_clip_changes_nothing():
    spec = _ducked_spec("clips")
    spec.audio_clips = spec.audio_clips[:1]
    assert "eval=frame" not in _graph(spec)


# --------------------------------------------------------------------------
# 検証と並べ直し
# --------------------------------------------------------------------------

def _input(**kwargs) -> TimelineClipInput:
    defaults = dict(
        track_id="v1", start_ms=0, duration_ms=2000, source_kind="library",
        source_id="x", in_ms=0, out_ms=2000,
    )
    defaults.update(kwargs)
    return TimelineClipInput(**defaults)


def test_validate_clips_accepts_a_freeze_whatever_the_trim():
    clip = _input(in_ms=1500, out_ms=1500, duration_ms=3000,
                  effects=TimelineClipEffects(freeze=True))
    service.validate_clips([clip], {"v1": "video"})


@pytest.mark.parametrize(
    ("effects", "message"),
    [
        ({"freeze": True, "reverse": True}, "同時に"),
        ({"zoom": {"scale": 5}}, "拡大率"),
        ({"zoom": {"scale": 1.2, "y": 1.5}}, "寄りの位置"),
        ({"step": 6}, "コマ打ち"),
        ({"color": {"saturation": 4}}, "彩度"),
        ({"noise": 2}, "ノイズ"),
    ],
)
def test_validate_clips_rejects_effects_out_of_range(effects, message):
    clip = _input(effects=TimelineClipEffects.model_validate(effects))
    with pytest.raises(service.TimelineError, match=message):
        service.validate_clips([clip], {"v1": "video"})


def test_validate_clips_keeps_effects_off_other_tracks_and_sources():
    with pytest.raises(service.TimelineError, match="映像クリップだけ"):
        service.validate_clips(
            [_input(track_id="a1", effects=TimelineClipEffects(noise=0.3))],
            {"a1": "audio"},
        )
    with pytest.raises(service.TimelineError, match="静止画"):
        service.validate_clips(
            [_input(source_kind="image", effects=TimelineClipEffects(reverse=True))],
            {"v1": "video"},
        )
    with pytest.raises(service.TimelineError, match="速度"):
        service.validate_clips(
            [_input(speed=2.0, duration_ms=1000,
                    effects=TimelineClipEffects(freeze=True))],
            {"v1": "video"},
        )


def test_default_effects_are_stored_as_none():
    assert service.normalize_effects(TimelineClipEffects()) is None
    assert service.normalize_effects(
        TimelineClipEffects.model_validate({"zoom": {"scale": 1}, "color": {}})
    ) is None
    kept = service.normalize_effects(TimelineClipEffects(step=2))
    assert kept is not None and kept.step == 2


def test_insert_into_splits_a_reversed_clip_from_the_far_end():
    under = _input(in_ms=0, out_ms=4000, duration_ms=4000,
                   effects=TimelineClipEffects(reverse=True))
    inserted = _input(source_id="y", start_ms=1000, duration_ms=1000,
                      in_ms=0, out_ms=1000)
    head, middle, tail = service.insert_into([under], inserted, 24)
    # 前半は素材の後ろ側（3〜4 秒）、後半は頭の側（0〜2 秒）を逆に流す
    assert (head.in_ms, head.out_ms) == (3000, 4000)
    assert middle.source_id == "y"
    assert (tail.in_ms, tail.out_ms) == (0, 2000)


def test_insert_into_splits_a_freeze_without_moving_the_frame():
    under = _input(in_ms=1234, out_ms=1234, duration_ms=4000,
                   effects=TimelineClipEffects(freeze=True))
    inserted = _input(source_id="y", start_ms=1000, duration_ms=1000,
                      in_ms=0, out_ms=1000)
    head, _middle, tail = service.insert_into([under], inserted, 24)
    assert head.in_ms == tail.in_ms == 1234
    assert (head.duration_ms, tail.duration_ms) == (1000, 2000)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_outputs(monkeypatch, tmp_path_factory):
    outputs = tmp_path_factory.mktemp("outputs")
    monkeypatch.setattr(service, "OUTPUTS_DIR", outputs)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _timeline_with_audio(client, tracks: int = 2) -> dict:
    project_id = client.post("/api/studio/projects", json={"name": "加工"}).json()["id"]
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines", json={}
    ).json()
    detail = timeline
    for _ in range(tracks):
        detail = client.post(
            f"/api/studio/timelines/{timeline['id']}/tracks", json={"kind": "audio"}
        ).json()
    return detail


def test_clip_effects_round_trip_through_put_clips(client):
    detail = _timeline_with_audio(client, 0)
    video = detail["tracks"][0]["id"]
    effects = {
        "reverse": True,
        "zoom": {"scale": 1.2, "x": 0.5, "y": 0.3},
        "step": 2,
        "color": {"saturation": 0.6, "contrast": 1.15},
        "noise": 0.4,
    }
    response = client.put(
        f"/api/studio/timelines/{detail['id']}/clips",
        json={
            "clips": [
                {"track_id": video, "start_ms": 0, "duration_ms": 2000,
                 "source_kind": "library", "source_id": "a", "in_ms": 0,
                 "out_ms": 2000, "effects": effects},
                {"track_id": video, "start_ms": 2000, "duration_ms": 1500,
                 "source_kind": "library", "source_id": "a", "in_ms": 700,
                 "out_ms": 700, "effects": {"freeze": True}},
                {"track_id": video, "start_ms": 3500, "duration_ms": 1000,
                 "source_kind": "library", "source_id": "a", "in_ms": 0,
                 "out_ms": 1000, "effects": {"step": 1}},
            ]
        },
    )
    assert response.status_code == 200, response.text
    clips = response.json()["tracks"][0]["clips"]
    assert clips[0]["effects"]["reverse"] is True
    assert clips[0]["effects"]["zoom"] == {"scale": 1.2, "x": 0.5, "y": 0.3}
    assert clips[0]["effects"]["color"]["saturation"] == 0.6
    assert clips[1]["effects"]["freeze"] is True
    # 既定値だけの加工は「なし」に畳む
    assert clips[2]["effects"] is None


def test_put_clips_refuses_effects_out_of_range(client):
    detail = _timeline_with_audio(client, 0)
    video = detail["tracks"][0]["id"]
    response = client.put(
        f"/api/studio/timelines/{detail['id']}/clips",
        json={"clips": [
            {"track_id": video, "start_ms": 0, "duration_ms": 2000,
             "source_kind": "library", "source_id": "a", "in_ms": 0,
             "out_ms": 2000, "effects": {"noise": 3}},
        ]},
    )
    assert response.status_code == 400
    assert "ノイズ" in response.json()["detail"]


def test_ducking_is_set_validated_and_cleared(client):
    detail = _timeline_with_audio(client, 2)
    timeline_id = detail["id"]
    video, bgm, voice = (track["id"] for track in detail["tracks"])
    url = f"/api/studio/timelines/{timeline_id}/tracks/{bgm}"

    set_ = client.patch(url, json={"ducking": {"trigger_track_id": voice, "amount_db": 9}})
    assert set_.status_code == 200, set_.text
    track = next(t for t in set_.json()["tracks"] if t["id"] == bgm)
    assert track["ducking"]["trigger_track_id"] == voice
    assert track["ducking"]["amount_db"] == 9
    assert track["ducking"]["mode"] == "clips"

    # 名前だけ変えてもダッキングは残る
    renamed = client.patch(url, json={"name": "BGM"}).json()
    assert next(t for t in renamed["tracks"] if t["id"] == bgm)["ducking"] is not None

    for body, message in (
        ({"trigger_track_id": bgm}, "自分自身"),
        ({"trigger_track_id": video}, "音声トラック"),
        ({"trigger_track_id": "nope"}, "ありません"),
        ({"trigger_track_id": voice, "amount_db": 99}, "下げる量"),
    ):
        bad = client.patch(url, json={"ducking": body})
        assert bad.status_code == 400 and message in bad.json()["detail"]
    on_video = client.patch(
        f"/api/studio/timelines/{timeline_id}/tracks/{video}",
        json={"ducking": {"trigger_track_id": voice}},
    )
    assert on_video.status_code == 400

    cleared = client.patch(url, json={"ducking": None}).json()
    assert next(t for t in cleared["tracks"] if t["id"] == bgm)["ducking"] is None


def test_signal_ducking_cannot_be_chained(client):
    detail = _timeline_with_audio(client, 3)
    timeline_id = detail["id"]
    _video, a1, a2, a3 = (track["id"] for track in detail["tracks"])
    ok = client.patch(
        f"/api/studio/timelines/{timeline_id}/tracks/{a1}",
        json={"ducking": {"trigger_track_id": a2, "mode": "signal"}},
    )
    assert ok.status_code == 200
    chained = client.patch(
        f"/api/studio/timelines/{timeline_id}/tracks/{a2}",
        json={"ducking": {"trigger_track_id": a3, "mode": "signal"}},
    )
    assert chained.status_code == 400


def test_deleting_the_trigger_track_drops_the_ducking(client):
    detail = _timeline_with_audio(client, 2)
    timeline_id = detail["id"]
    _video, bgm, voice = (track["id"] for track in detail["tracks"])
    client.patch(
        f"/api/studio/timelines/{timeline_id}/tracks/{bgm}",
        json={"ducking": {"trigger_track_id": voice}},
    )
    left = client.delete(f"/api/studio/timelines/{timeline_id}/tracks/{voice}").json()
    assert next(t for t in left["tracks"] if t["id"] == bgm)["ducking"] is None


def _seed_library(rows: list[tuple[str, str, str]]) -> None:
    from app.db import get_db

    async def seed():
        async with get_db() as conn:
            for item_id, path, kind in rows:
                await conn.execute(
                    "INSERT INTO library (id, kind, name, path, created_at, tags)"
                    " VALUES (?, ?, '', ?, '2026-01-01T00:00:00+00:00', '[]')",
                    (item_id, kind, path),
                )
            await conn.commit()

    asyncio.run(seed())


def test_export_carries_effects_and_ducking_into_the_spec(client, tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    bgm = tmp_path / "bgm.wav"
    bgm.write_bytes(b"x")
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"x")

    async def fake_probe(path):
        return 4000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    _seed_library([("lv", str(video), "video"), ("lb", str(bgm), "audio"),
                   ("lw", str(voice), "audio")])
    detail = _timeline_with_audio(client, 2)
    timeline_id = detail["id"]
    v1, a1, a2 = (track["id"] for track in detail["tracks"])
    response = client.put(
        f"/api/studio/timelines/{timeline_id}/clips",
        json={"clips": [
            {"track_id": v1, "start_ms": 0, "duration_ms": 2000,
             "source_kind": "library", "source_id": "lv", "in_ms": 0,
             "out_ms": 2000, "effects": {"zoom": {"scale": 1.5}}},
            {"track_id": a1, "start_ms": 0, "duration_ms": 2000,
             "source_kind": "library", "source_id": "lb", "in_ms": 0, "out_ms": 2000},
            {"track_id": a2, "start_ms": 500, "duration_ms": 1000,
             "source_kind": "library", "source_id": "lw", "in_ms": 0, "out_ms": 1000},
        ]},
    )
    assert response.status_code == 200, response.text
    client.patch(
        f"/api/studio/timelines/{timeline_id}/tracks/{a1}",
        json={"ducking": {"trigger_track_id": a2, "amount_db": 6}},
    )
    seen: dict = {}

    async def fake_run(spec, output, *, on_progress=None):
        seen["spec"] = spec
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")
        return output

    monkeypatch.setattr(service, "run_export", fake_run)
    client.post(f"/api/studio/timelines/{timeline_id}/export", json={})
    deadline = time.time() + 10
    while time.time() < deadline and "spec" not in seen:
        time.sleep(0.05)
    spec = seen["spec"]
    assert spec.clips[0].effects is not None and spec.clips[0].effects.zoom == 1.5
    assert [audio.track for audio in spec.audio_clips] == [a1, a2]
    assert len(spec.ducking) == 1
    assert (spec.ducking[0].target, spec.ducking[0].trigger) == (a1, a2)
    assert spec.ducking[0].amount_db == 6


# --------------------------------------------------------------------------
# 実際に焼く（ffmpeg があるときだけ）
# --------------------------------------------------------------------------

def _make_media(folder: Path) -> tuple[Path, Path, Path]:
    source = folder / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=s=320x180:r=24:d=3", "-f", "lavfi", "-i", "sine=f=440:d=3",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         str(source)],
        check=True,
    )
    bgm = folder / "bgm.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=220:d=5", str(bgm)], check=True)
    voice = folder / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=880:d=1", str(voice)], check=True)
    return source, bgm, voice


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg が無い")
@pytest.mark.parametrize("mode", ["clips", "signal"])
def test_effects_and_ducking_render_the_planned_frames(tmp_path, mode):
    source, bgm, voice = _make_media(tmp_path)

    def clip(**kwargs):
        return ExportClip(path=str(source), has_audio=True,
                          source_duration_ms=3000, **kwargs)

    spec = ExportSpec(
        width=360, height=640, fps=24, loudnorm=True,
        clips=[
            clip(in_ms=0, out_ms=1000, duration_ms=1000),
            clip(in_ms=500, out_ms=500, duration_ms=700,
                 effects=ClipEffects(freeze=True, noise=0.5)),
            clip(in_ms=1000, out_ms=2000, duration_ms=500, speed=2.0,
                 effects=ClipEffects(reverse=True, saturation=0.6)),
            clip(in_ms=1500, out_ms=2900, duration_ms=1400,
                 effects=ClipEffects(zoom=1.2, zoom_y=0.3, step=3, gamma=1.2)),
        ],
        audio_clips=[
            ExportAudioClip(path=str(bgm), start_ms=0, in_ms=0, out_ms=4000, track="A1"),
            ExportAudioClip(path=str(voice), start_ms=1000, in_ms=0, out_ms=1000,
                            track="A2"),
        ],
        ducking=[ExportDucking(target="A1", trigger="A2", amount_db=10, mode=mode)],
    )
    output = tmp_path / "out.mp4"
    asyncio.run(timeline_export.run_export(spec, output))
    frames = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(output)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert int(frames) == spec.total_frames == 86


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg が無い")
def test_clip_ducking_lowers_by_exactly_the_amount(tmp_path):
    _source, bgm, _voice = _make_media(tmp_path)
    expression = duck_expression([(1000, 2000)], 10, 100, 300)

    def rms(start: float) -> float:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(bgm), "-filter_complex",
             f"[0:a]volume={expression}:eval=frame,atrim=start={start}:duration=0.2,"
             "astats=reset=0[o]", "-map", "[o]", "-f", "null", "-"],
            capture_output=True, text=True, check=True,
        )
        lines = [line for line in result.stderr.splitlines() if "RMS level dB" in line]
        return float(lines[-1].rsplit(":", 1)[1])

    assert abs((rms(0.3) - rms(1.4)) - 10.0) < 0.2
    assert abs(rms(0.3) - rms(2.8)) < 0.2
