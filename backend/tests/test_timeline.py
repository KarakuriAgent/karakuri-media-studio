"""編集タブ: タイムライン（EDL）の CRUD・クリップの検証・書き出しの組み立て。

ffmpeg は実際には走らせない（:func:`app.timeline_export.run_export` を差し替える）。
ここで見るのは「どんなコマンドを組み立てたか」と「API の受け答え」まで。
"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app import db, timeline as service, timeline_export
from app.main import app
from app.models import TimelineClipInput
from app.timeline_export import (
    ExportClip,
    ExportSpec,
    TimelineExportError,
    build_command,
    parse_progress_line,
)


# --------------------------------------------------------------------------
# 書き出しコマンドの組み立て（純関数）
# --------------------------------------------------------------------------

def _clip(path="/outputs/a/video.mp4", start=0, end=2000, **kwargs):
    return ExportClip(
        path=path, in_ms=start, out_ms=end, duration_ms=end - start, **kwargs
    )


def test_build_command_normalises_and_concats_every_clip():
    spec = ExportSpec(
        width=1280,
        height=720,
        fps=24,
        clips=[_clip("/x/one.mp4", 0, 2000), _clip("/x/two.mp4", 500, 1500)],
    )
    command = build_command(spec, "/out/final.mp4")

    assert command[0] == timeline_export.FFMPEG
    # 入力は 2 本（どちらも音声を持つので lavfi の穴埋めは要らない）
    assert command.count("-i") == 2
    graph = command[command.index("-filter_complex") + 1]
    # 切り出しは 1 フレームぶん余分に取り（2.000 + 1/24 秒）、フレーム数で切る
    assert "[0:v]trim=start=0.000000:end=2.041667" in graph
    assert "[1:v]trim=start=0.500000:end=1.541667" in graph
    assert graph.count("trim=end_frame=48") == 1  # 2 秒 = 48f
    assert graph.count("trim=end_frame=24") == 1  # 1 秒 = 24f
    assert graph.count("tpad=stop_mode=clone") == 2
    assert graph.count("scale=1280:720:force_original_aspect_ratio=decrease") == 2
    assert graph.count("pad=1280:720:(ow-iw)/2:(oh-ih)/2") == 2
    assert graph.count("setsar=1,fps=24") == 2
    # 音声も同じ規格へ揃え、映像とちょうど同じ長さにしてから連結
    assert "atrim=start=0.000000:end=2.000000" in graph
    assert "atrim=start=0.500000:end=1.500000" in graph
    assert graph.count("apad=whole_dur=2.000000,atrim=end=2.000000") == 1
    assert graph.count(f"aresample={timeline_export.AUDIO_RATE}") == 2
    # 出力そのものもフレーム数で締める（2 秒 + 1 秒 = 72f）
    assert command[command.index("-frames:v") + 1] == "72"
    assert graph.endswith("[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]")
    # 出力は H.264 + AAC / yuv420p / faststart
    assert command[-1] == "/out/final.mp4"
    for expected in ("libx264", "yuv420p", "aac", "+faststart"):
        assert expected in command


def test_build_command_fills_silence_for_a_source_without_audio():
    spec = ExportSpec(
        width=640, height=360, fps=30, clips=[_clip("/x/mute.mp4", has_audio=False)]
    )
    command = build_command(spec, "/out/final.mp4")

    # 本体 1 本 + 無音の lavfi 1 本
    assert command.count("-i") == 2
    assert any("anullsrc" in arg for arg in command)
    graph = command[command.index("-filter_complex") + 1]
    assert "atrim=" not in graph
    assert graph.endswith("[v0][a0]concat=n=1:v=1:a=1[outv][outa]")


def test_build_command_generates_black_and_silence_for_a_gap():
    spec = ExportSpec(
        width=1920,
        height=1080,
        fps=24,
        clips=[
            ExportClip(path=None, in_ms=0, out_ms=1000, duration_ms=1000, has_audio=False),
            _clip("/x/one.mp4"),
        ],
    )
    command = build_command(spec, "/out/final.mp4")

    assert any("color=c=black:s=1920x1080:r=24" in arg for arg in command)
    assert any("anullsrc" in arg for arg in command)
    graph = command[command.index("-filter_complex") + 1]
    assert graph.endswith("[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]")


def test_build_command_applies_gain_only_when_set():
    plain = build_command(
        ExportSpec(width=640, height=360, fps=24, clips=[_clip()]), "/o.mp4"
    )
    louder = build_command(
        ExportSpec(width=640, height=360, fps=24, clips=[_clip(gain_db=-6.0)]), "/o.mp4"
    )
    assert "volume=" not in plain[plain.index("-filter_complex") + 1]
    assert "volume=-6dB" in louder[louder.index("-filter_complex") + 1]


def test_build_command_refuses_an_empty_timeline():
    with pytest.raises(TimelineExportError):
        build_command(ExportSpec(width=1280, height=720, fps=24, clips=[]), "/o.mp4")
    # 尺 0 のクリップしかないのも「書き出せるものが無い」
    with pytest.raises(TimelineExportError):
        build_command(
            ExportSpec(
                width=1280,
                height=720,
                fps=24,
                clips=[ExportClip(path="/x.mp4", in_ms=0, out_ms=0, duration_ms=0)],
            ),
            "/o.mp4",
        )


def test_build_command_refuses_a_broken_format():
    spec = ExportSpec(width=0, height=720, fps=24, clips=[_clip()])
    with pytest.raises(TimelineExportError):
        build_command(spec, "/o.mp4")


def test_parse_progress_line_reads_out_time():
    assert parse_progress_line("out_time_us=1500000") == pytest.approx(1.5)
    assert parse_progress_line("out_time_ms=2000000") == pytest.approx(2.0)
    assert parse_progress_line("frame=42") is None
    assert parse_progress_line("out_time=00:00:01.50") is None
    assert parse_progress_line("out_time_us=N/A") is None


# --------------------------------------------------------------------------
# クリップの検証（PUT /clips が通す純関数）
# --------------------------------------------------------------------------

def _input(**kwargs):
    body = {
        "track_id": "T1",
        "start_ms": 0,
        "duration_ms": 1000,
        "source_kind": "take",
        "source_id": "TAKE",
        "in_ms": 0,
        "out_ms": 1000,
    }
    body.update(kwargs)
    return TimelineClipInput(**body)


def test_validate_clips_accepts_a_gapless_track():
    service.validate_clips(
        [_input(start_ms=0), _input(start_ms=1000), _input(start_ms=2000)]
    )


def test_validate_clips_accepts_a_deliberate_gap_between_clips():
    service.validate_clips([_input(start_ms=0), _input(start_ms=5000)])


def test_validate_clips_rejects_overlap_in_one_track():
    with pytest.raises(service.TimelineError, match="重なって"):
        service.validate_clips([_input(start_ms=0), _input(start_ms=999)])


def test_validate_clips_allows_the_same_span_on_another_track():
    service.validate_clips(
        [_input(track_id="T1", start_ms=0), _input(track_id="T2", start_ms=0)]
    )


def test_validate_clips_rejects_an_inverted_trim():
    with pytest.raises(service.TimelineError, match="切り出しの範囲"):
        service.validate_clips([_input(in_ms=1000, out_ms=1000)])


def test_validate_clips_rejects_a_speed_change():
    with pytest.raises(service.TimelineError, match="等速"):
        service.validate_clips([_input(duration_ms=2000, in_ms=0, out_ms=1000)])


def test_validate_clips_rejects_a_zero_length_clip():
    with pytest.raises(service.TimelineError, match="尺"):
        service.validate_clips([_input(duration_ms=0, out_ms=0)])


def test_validate_clips_rejects_a_negative_start():
    with pytest.raises(service.TimelineError, match="開始位置"):
        service.validate_clips([_input(start_ms=-1)])


def test_validate_clips_ignores_the_trim_of_a_gap():
    service.validate_clips(
        [_input(source_kind="gap", source_id=None, in_ms=0, out_ms=0)]
    )


# --------------------------------------------------------------------------
# 音源基準の配置と差し込み（どちらも純関数）
# --------------------------------------------------------------------------

def _planned(seconds, source_ms=5000, **kwargs):
    """計画秒つきのテイククリップ 1 件（:func:`app.timeline.plan_layout` に渡す形）。"""
    return service.PlannedClip(
        _input(duration_ms=source_ms, in_ms=0, out_ms=source_ms, **kwargs),
        seconds,
        source_ms,
    )


def test_plan_layout_places_each_clip_at_its_planned_second():
    """計画秒があれば、並び順ではなく音源上の秒がそのまま置き場所になる。"""
    placed = service.plan_layout(
        [_planned(10.0, source_id="b"), _planned(0.0, source_id="a")], "V1"
    )
    # 5 秒の Take のうしろ（5000〜10000）は隙間になる
    assert [(clip.source_id, clip.start_ms) for clip in placed] == [
        ("a", 0),
        (None, 5000),
        ("b", 10000),
    ]
    # 前のカットは Take の尺どおり（次の計画秒 10000 までは届かない）
    assert placed[0].duration_ms == 5000
    assert (placed[0].in_ms, placed[0].out_ms) == (0, 5000)


def test_plan_layout_cuts_a_clip_at_the_next_planned_second():
    placed = service.plan_layout(
        [_planned(0.0, source_id="a"), _planned(1.5, source_id="b")], "V1"
    )
    assert [(clip.start_ms, clip.duration_ms) for clip in placed] == [
        (0, 1500),
        (1500, 5000),
    ]
    assert placed[0].out_ms == 1500  # 切り出しも詰められる


def test_plan_layout_fills_the_holes_with_a_gap():
    """短い Take のうしろと、先頭までの空きは gap（黒＋無音）で埋まる。"""
    placed = service.plan_layout(
        [_planned(2.0, source_ms=1000, source_id="a"), _planned(5.0, source_id="b")],
        "V1",
    )
    assert [
        (clip.source_kind, clip.start_ms, clip.duration_ms) for clip in placed
    ] == [
        ("gap", 0, 2000),
        ("take", 2000, 1000),
        ("gap", 3000, 2000),
        ("take", 5000, 5000),
    ]


def test_plan_layout_appends_the_clips_without_a_plan_after_the_plan():
    placed = service.plan_layout(
        [
            _planned(0.0, source_ms=2000, source_id="a"),
            service.PlannedClip(_input(duration_ms=1000, out_ms=1000, source_id="b")),
        ],
        "V1",
    )
    assert [(clip.source_id, clip.start_ms) for clip in placed] == [
        ("a", 0),
        ("b", 2000),
    ]


def test_plan_layout_without_any_plan_is_the_old_ripple():
    clips = [_input(duration_ms=1000, out_ms=1000), _input(duration_ms=2000, out_ms=2000)]
    placed = service.plan_layout(
        [service.PlannedClip(clip) for clip in clips], "V1"
    )
    assert [clip.start_ms for clip in placed] == [0, 1000]


def test_plan_layout_refuses_planned_seconds_that_are_too_close():
    with pytest.raises(service.TimelineError):
        service.plan_layout(
            [_planned(0.0, source_id="a"), _planned(0.01, source_id="b")], "V1"
        )


def test_insert_into_splits_the_clip_underneath():
    """差し込むと下のクリップは前後に割れ、後半は切り出しの続きから鳴る。"""
    under = _input(start_ms=0, duration_ms=4000, in_ms=1000, out_ms=5000, source_id="a")
    inserted = _input(
        start_ms=1000, duration_ms=1500, in_ms=0, out_ms=1500, source_id="dr"
    )
    placed = service.insert_into([under], inserted)

    assert [
        (clip.source_id, clip.start_ms, clip.duration_ms, clip.in_ms, clip.out_ms)
        for clip in placed
    ] == [
        ("a", 0, 1000, 1000, 2000),
        ("dr", 1000, 1500, 0, 1500),
        ("a", 2500, 1500, 3500, 5000),
    ]
    # トラックの全長は変わらない（前後へ押し出さない）
    assert placed[-1].start_ms + placed[-1].duration_ms == 4000
    # 分割で増えたほうは新しいクリップとして振り直す
    assert placed[-1].id is None


def test_insert_into_drops_a_clip_it_covers_completely():
    placed = service.insert_into(
        [_input(start_ms=1000, duration_ms=500, out_ms=500, source_id="a")],
        _input(start_ms=0, duration_ms=3000, out_ms=3000, source_id="dr"),
    )
    assert [clip.source_id for clip in placed] == ["dr"]


def test_insert_into_leaves_the_clips_it_does_not_touch():
    before = _input(start_ms=0, duration_ms=1000, out_ms=1000, source_id="a")
    after = _input(start_ms=2000, duration_ms=1000, out_ms=1000, source_id="b")
    placed = service.insert_into(
        [before, after],
        _input(start_ms=1000, duration_ms=1000, out_ms=1000, source_id="dr"),
    )
    assert [clip.source_id for clip in placed] == ["a", "dr", "b"]
    assert placed[2].start_ms == 2000


# --------------------------------------------------------------------------
# フレーム精度と不足尺の保険（純関数）
# --------------------------------------------------------------------------

def test_frame_count_quantises_to_the_nearest_frame():
    assert timeline_export.frame_count(1000, 24) == 24
    assert timeline_export.frame_count(1001, 24) == 24
    assert timeline_export.frame_count(1042, 24) == 25
    assert timeline_export.frame_count(-5, 24) == 0
    with pytest.raises(TimelineExportError):
        timeline_export.frame_count(1000, 0)


def test_total_frames_counts_the_overlap_only_once():
    spec = ExportSpec(
        width=640,
        height=360,
        fps=24,
        clips=[
            _clip("/x/1.mp4", 0, 2000),
            ExportClip(
                path="/x/2.mp4",
                in_ms=0,
                out_ms=3000,
                duration_ms=3000,
                transition_kind="crossfade",
                transition_ms=500,
            ),
        ],
    )
    assert spec.total_frames == 48 + 72 - 12
    assert spec.duration_ms == 4500


def test_pad_warnings_reports_a_source_that_is_too_short():
    spec = ExportSpec(
        width=640,
        height=360,
        fps=24,
        clips=[
            _clip("/x/short.mp4", 0, 2000, source_duration_ms=1580, name="A-1"),
            # 1 フレームぶんの不足は丸めの範囲なので黙って埋める
            _clip("/x/edge.mp4", 0, 2000, source_duration_ms=1970, name="A-2"),
            _clip("/x/ok.mp4", 0, 2000, source_duration_ms=4000, name="A-3"),
        ],
    )
    assert timeline_export.pad_warnings(spec) == ["PAD A-1 0.42s"]


def test_build_command_freezes_the_tail_of_a_short_source():
    spec = ExportSpec(
        width=640,
        height=360,
        fps=24,
        clips=[_clip("/x/short.mp4", 0, 2000, source_duration_ms=1500)],
    )
    graph = build_command(spec, "/o.mp4")[
        build_command(spec, "/o.mp4").index("-filter_complex") + 1
    ]
    # 不足 0.5 秒 + 余裕（丸めのぶん）を末尾フレームの静止で埋める
    assert "tpad=stop_mode=clone:stop_duration=0.750" in graph
    assert "trim=end_frame=48" in graph


#: BAN!BAN!BAN! の PLAN をタイムラインで再現した EDL（43 カット + 差し込み 8 =
#: 51 クリップ）の尺。合計 193,480ms = 24fps で 4,644 枚（#53）。
BAN_DURATIONS_MS = [
    4000, 6000, 6600, 3700, 4100, 3100, 5700, 3600, 2800, 4300,
    1500, 1300, 1500, 1100, 3900, 2400, 1500, 800, 1500, 1100,
    5600, 2900, 4000, 3100, 5900, 5600, 5200, 5800, 1500, 1000,
    1500, 1100, 6300, 1500, 800, 1500, 800, 11100, 5800, 5300,
    6200, 4200, 5800, 4100, 5900, 7300, 4200, 6000, 4800, 5200,
    2980,
]


def test_frame_plan_counts_the_boundaries_not_the_durations():
    """境界を量子化するので、クリップの丸めが積み上がらない（#53）。"""
    clips = [
        ExportClip(path=f"/x/{index}.mp4", in_ms=0, out_ms=duration, duration_ms=duration)
        for index, duration in enumerate(BAN_DURATIONS_MS)
    ]
    spec = ExportSpec(width=1280, height=720, fps=24, clips=clips)

    assert spec.total_frames == round(193.48 * 24) == 4644
    # 尺をひとつずつ丸めると 4 枚足りない（元のバグ）
    assert sum(timeline_export.frame_count(d, 24) for d in BAN_DURATIONS_MS) == 4640

    plan = spec.frames
    # 枚数の足し上げは境界と一致し、隣どうしは境界を共有する
    assert sum(item.count for item in plan) == spec.total_frames
    assert [item.start for item in plan[1:]] == [item.end for item in plan[:-1]]
    for index, item in enumerate(plan):
        start_ms = sum(BAN_DURATIONS_MS[:index])
        assert item.start == timeline_export.frame_count(start_ms, 24)
        assert item.end == timeline_export.frame_count(
            start_ms + BAN_DURATIONS_MS[index], 24
        )


def test_build_command_drops_the_clips_that_are_shorter_than_a_frame():
    """1 フレームに満たない隙間は落とす（``concat`` に尺 0 を渡さない。#53）。"""
    spec = ExportSpec(
        width=1280,
        height=720,
        fps=24,
        clips=[
            _clip("/x/one.mp4", 0, 2000),
            # 自動配置が作ってしまう数ミリ秒の隙間（どちらも 24fps で 0 枚）
            ExportClip(path=None, in_ms=0, out_ms=17, duration_ms=17, has_audio=False),
            _clip("/x/two.mp4", 0, 1000),
            ExportClip(path=None, in_ms=0, out_ms=3, duration_ms=3, has_audio=False),
            _clip("/x/three.mp4", 0, 1000),
        ],
    )
    assert [item.count for item in spec.frames] == [48, 0, 24, 0, 24]
    command = build_command(spec, "/o.mp4")
    graph = command[command.index("-filter_complex") + 1]

    # 黒も無音も作らない（0 枚のセグメントは出てこない）
    assert "color=c=black" not in " ".join(command)
    assert "anullsrc" not in " ".join(command)
    assert "trim=end_frame=0" not in graph
    assert "atrim=end=0.000000" not in graph
    assert graph.endswith("[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[outv][outa]")
    # 落としても全長は変わらない（境界基準なので隙間のぶんは隣が吸う）
    assert spec.total_frames == timeline_export.frame_count(4020, 24) == 96
    assert command[command.index("-frames:v") + 1] == "96"


def test_plan_layout_never_leaves_a_gap_shorter_than_a_frame():
    """1 フレーム未満の隙間は前のカットへ寄せる（書き出しが落ちないように。#53）。"""
    placed = service.plan_layout(
        [_planned(0.0, source_ms=983, source_id="a"), _planned(1.0, source_id="b")],
        "V1",
        24.0,
    )
    # 17ms の隙間は gap にせず、前のカットが吸う
    assert [
        (clip.source_kind, clip.start_ms, clip.duration_ms) for clip in placed
    ] == [("take", 0, 1000), ("take", 1000, 5000)]
    assert placed[0].out_ms == 1000
    # 1 フレームを超える隙間は今までどおり gap で埋まる
    spaced = service.plan_layout(
        [_planned(0.0, source_ms=900, source_id="a"), _planned(1.0, source_id="b")],
        "V1",
        24.0,
    )
    assert [clip.source_kind for clip in spaced] == ["take", "gap", "take"]


def test_frame_warning_only_fires_on_a_mismatch():
    assert timeline_export.frame_warning(4728, 4728) is None
    assert timeline_export.frame_warning(4728, None) is None
    assert "4727" in (timeline_export.frame_warning(4728, 4727) or "")


# --------------------------------------------------------------------------
# API（作成 -> 取得 -> クリップ差し替え -> 書き出し -> ライブラリ）
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_outputs(monkeypatch, tmp_path_factory):
    """書き出し先（``outputs/exports/``）を開発機のリポジトリから切り離す。"""
    outputs = tmp_path_factory.mktemp("outputs")
    monkeypatch.setattr(service, "OUTPUTS_DIR", outputs)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def wait_for_export(client, timeline_id, timeout=10.0) -> dict:
    """書き出しは 202 即受付なので、終端に落ちるまで履歴を見張る。"""
    deadline = time.time() + timeout
    exports: list[dict] = []
    while time.time() < deadline:
        exports = client.get(f"/api/studio/timelines/{timeline_id}/exports").json()
        if exports and exports[0]["status"] in ("done", "failed"):
            return exports[0]
        time.sleep(0.05)
    raise AssertionError(f"export stuck in {exports[0]['status'] if exports else None}")


def _project(client, name="編集テスト"):
    return client.post("/api/studio/projects", json={"name": name}).json()["id"]


async def _seed_take(project_id: str, video_path: str) -> tuple[str, str]:
    """話 -> 場 -> カット -> 採用 Take を 1 組作る（``(episode_id, take_id)``）。

    ジョブは行だけ置く（実行はしない）。``video_path`` が実在するかどうかで、
    自動配置に載るか「メディア欠落」になるかが決まる。
    """
    from app.ids import new_id

    episode_id, scene_id, shot_id = new_id(), new_id(), new_id()
    job_id, take_id = new_id(), new_id()
    now = "2026-01-01T00:00:00+00:00"
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO studio_episodes (id, project_id, sort_order, title,"
            " synopsis, created_at) VALUES (?, ?, 0, '第 1 話', '', ?)",
            (episode_id, project_id, now),
        )
        await conn.execute(
            "INSERT INTO studio_scenes (id, episode_id, project_id, sort_order,"
            " title, synopsis, time_of_day, created_at)"
            " VALUES (?, ?, ?, 0, '場 1', '', '', ?)",
            (scene_id, episode_id, project_id, now),
        )
        await conn.execute(
            "INSERT INTO studio_shots (id, project_id, scene_id, sort_order, title,"
            " selected_take_id, created_at, updated_at)"
            " VALUES (?, ?, ?, 0, 'カット 1', ?, ?, ?)",
            (shot_id, project_id, scene_id, take_id, now, now),
        )
        await conn.execute(
            "INSERT INTO jobs (id, created_at, mode, status, params, workflow_json,"
            " video_path) VALUES (?, ?, 'video', 'done', '{}', '{}', ?)",
            (job_id, now, video_path),
        )
        await conn.execute(
            "INSERT INTO studio_takes (id, shot_id, project_id, job_id, status,"
            " created_at) VALUES (?, ?, ?, ?, 'selected', ?)",
            (take_id, shot_id, project_id, job_id, now),
        )
        await conn.commit()
    return episode_id, take_id


def test_create_timeline_without_an_episode_makes_an_empty_v1(client):
    project_id = _project(client)
    response = client.post(f"/api/studio/projects/{project_id}/timelines", json={})
    assert response.status_code == 201
    detail = response.json()
    assert detail["name"] == "編集テスト の編集"
    assert detail["fps"] == service.DEFAULT_FPS
    assert [track["kind"] for track in detail["tracks"]] == ["video"]
    assert detail["tracks"][0]["name"] == "V1"
    assert detail["tracks"][0]["clips"] == []
    assert detail["duration_ms"] == 0


def test_create_timeline_for_an_episode_lays_out_the_selected_takes(
    client, tmp_path, monkeypatch
):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"not really a video")
    episode_id, take_id = asyncio.run(_seed_take(project_id, str(video)))

    # ffprobe は呼ばせない（尺は固定で返す）。
    async def fake_probe(path):
        return 3200, True

    monkeypatch.setattr(service, "probe_media", fake_probe)

    detail = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()
    assert detail["episode_id"] == episode_id
    assert detail["name"] == "第 1 話 の編集"
    clips = detail["tracks"][0]["clips"]
    assert len(clips) == 1
    assert clips[0]["source_kind"] == "take"
    assert clips[0]["source_id"] == take_id
    assert (clips[0]["start_ms"], clips[0]["duration_ms"]) == (0, 3200)
    assert (clips[0]["in_ms"], clips[0]["out_ms"]) == (0, 3200)
    assert clips[0]["missing"] is False
    assert clips[0]["label"] == "第 1 話 / 場 1 / #1 カット 1"
    assert detail["duration_ms"] == 3200


def test_a_take_whose_file_is_gone_never_makes_it_into_the_layout(client, tmp_path):
    project_id = _project(client)
    episode_id, _ = asyncio.run(_seed_take(project_id, str(tmp_path / "missing.mp4")))
    detail = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()
    assert detail["tracks"][0]["clips"] == []


def test_a_clip_whose_source_disappears_is_reported_as_missing(
    client, tmp_path, monkeypatch
):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 1000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, _ = asyncio.run(_seed_take(project_id, str(video)))
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()

    video.unlink()  # 元の Take の動画が消えた
    fresh = client.get(f"/api/studio/timelines/{timeline['id']}").json()
    clip = fresh["tracks"][0]["clips"][0]
    assert clip["missing"] is True
    assert clip["video_url"] is None


def test_unknown_timeline_is_404(client):
    assert client.get("/api/studio/timelines/NOPE").status_code == 404
    assert client.delete("/api/studio/timelines/NOPE").status_code == 404
    assert (
        client.put("/api/studio/timelines/NOPE/clips", json={"clips": []}).status_code
        == 404
    )


def test_create_timeline_for_an_unknown_project_or_episode_is_404(client):
    assert (
        client.post("/api/studio/projects/NOPE/timelines", json={}).status_code == 404
    )
    project_id = _project(client)
    assert (
        client.post(
            f"/api/studio/projects/{project_id}/timelines",
            json={"episode_id": "NOPE"},
        ).status_code
        == 404
    )


def test_put_clips_replaces_the_whole_track(client):
    project_id = _project(client)
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines", json={}
    ).json()
    track_id = timeline["tracks"][0]["id"]

    body = {
        "clips": [
            {
                "track_id": track_id,
                "start_ms": 0,
                "duration_ms": 1000,
                "source_kind": "take",
                "source_id": "T1",
                "in_ms": 0,
                "out_ms": 1000,
            },
            {
                "id": "KEEPME",
                "track_id": track_id,
                "start_ms": 1000,
                "duration_ms": 500,
                "source_kind": "take",
                "source_id": "T2",
                "in_ms": 250,
                "out_ms": 750,
            },
        ]
    }
    detail = client.put(
        f"/api/studio/timelines/{timeline['id']}/clips", json=body
    ).json()
    clips = detail["tracks"][0]["clips"]
    assert [clip["source_id"] for clip in clips] == ["T1", "T2"]
    # 送った id は引き継ぎ、省いたものには新しく振る
    assert clips[1]["id"] == "KEEPME"
    assert clips[0]["id"] != "KEEPME"
    assert detail["duration_ms"] == 1500

    # もう一度送れば「全置換」（前のクリップは残らない）
    emptied = client.put(
        f"/api/studio/timelines/{timeline['id']}/clips", json={"clips": []}
    ).json()
    assert emptied["tracks"][0]["clips"] == []


def test_put_clips_rejects_an_overlap(client):
    project_id = _project(client)
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines", json={}
    ).json()
    track_id = timeline["tracks"][0]["id"]
    clip = {
        "track_id": track_id,
        "start_ms": 0,
        "duration_ms": 1000,
        "source_kind": "take",
        "source_id": "T1",
        "in_ms": 0,
        "out_ms": 1000,
    }
    response = client.put(
        f"/api/studio/timelines/{timeline['id']}/clips",
        json={"clips": [clip, {**clip, "start_ms": 500}]},
    )
    assert response.status_code == 400
    assert "重なって" in response.json()["detail"]


def test_put_clips_rejects_a_track_from_another_timeline(client):
    project_id = _project(client)
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines", json={}
    ).json()
    response = client.put(
        f"/api/studio/timelines/{timeline['id']}/clips",
        json={
            "clips": [
                {
                    "track_id": "OTHER",
                    "start_ms": 0,
                    "duration_ms": 100,
                    "source_kind": "take",
                    "source_id": "T1",
                    "in_ms": 0,
                    "out_ms": 100,
                }
            ]
        },
    )
    assert response.status_code == 400


def test_patch_and_delete_timeline(client):
    project_id = _project(client)
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines", json={}
    ).json()
    renamed = client.patch(
        f"/api/studio/timelines/{timeline['id']}", json={"name": "本編", "fps": 30}
    ).json()
    assert (renamed["name"], renamed["fps"]) == ("本編", 30)

    assert client.delete(f"/api/studio/timelines/{timeline['id']}").status_code == 204
    assert client.get(f"/api/studio/timelines/{timeline['id']}").status_code == 404
    # トラックとクリップも一緒に片づく（外部キーの CASCADE は張っていない）
    assert asyncio.run(_count("timeline_tracks", timeline["id"])) == 0
    assert asyncio.run(_count("timeline_clips", timeline["id"])) == 0


async def _count(table: str, timeline_id: str) -> int:
    async with db.get_db() as conn:
        async with conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE timeline_id = ?", (timeline_id,)
        ) as cur:
            return int((await cur.fetchone())["n"])


def test_a_revision_carries_the_timeline_and_restores_it(client):
    """EDL はリビジョンに載る（書き出しの記録は載らない）。"""
    project_id = _project(client)
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines", json={}
    ).json()
    track_id = timeline["tracks"][0]["id"]
    client.put(
        f"/api/studio/timelines/{timeline['id']}/clips",
        json={
            "clips": [
                {
                    "track_id": track_id,
                    "start_ms": 0,
                    "duration_ms": 1000,
                    "source_kind": "take",
                    "source_id": "T1",
                    "in_ms": 0,
                    "out_ms": 1000,
                }
            ]
        },
    )
    # クリップを足したあとの状態でリビジョンを 1 つ作る（脚本を触ると残る）。
    client.post(f"/api/studio/projects/{project_id}/shots", json={"title": "印"})
    seq = client.get(f"/api/studio/projects/{project_id}/revisions").json()[0]["seq"]
    snapshot = client.get(
        f"/api/studio/projects/{project_id}/revisions/{seq}"
    ).json()["snapshot"]
    assert len(snapshot["timelines"]) == 1
    assert len(snapshot["timeline_clips"]) == 1

    # クリップを消してから書き戻すと、EDL がそのまま返ってくる。
    client.put(f"/api/studio/timelines/{timeline['id']}/clips", json={"clips": []})
    client.post(f"/api/studio/projects/{project_id}/revisions/{seq}/restore")
    restored = client.get(f"/api/studio/timelines/{timeline['id']}").json()
    assert [clip["source_id"] for clip in restored["tracks"][0]["clips"]] == ["T1"]


def test_restoring_a_revision_taken_before_the_edit_tab_keeps_the_timelines(client):
    """編集タブより前のスナップショットには EDL のキーが無い。

    それを「空だった」と読むとタイムラインが丸ごと消えるので、載っていない面は
    触らない（:func:`app.studio.restore_revision`）。
    """
    project_id = _project(client)
    client.post(f"/api/studio/projects/{project_id}/shots", json={"title": "印"})
    seq = client.get(f"/api/studio/projects/{project_id}/revisions").json()[0]["seq"]
    # そのリビジョンから EDL のキーを抜いて、機能を足す前の形に戻す。
    asyncio.run(_strip_timeline_keys(project_id, seq))

    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines", json={}
    ).json()
    client.post(f"/api/studio/projects/{project_id}/revisions/{seq}/restore")
    assert client.get(f"/api/studio/timelines/{timeline['id']}").status_code == 200


async def _strip_timeline_keys(project_id: str, seq: int) -> None:
    import json as _json

    async with db.get_db() as conn:
        async with conn.execute(
            "SELECT snapshot_json FROM studio_revisions"
            " WHERE project_id = ? AND seq = ?",
            (project_id, seq),
        ) as cur:
            snapshot = _json.loads((await cur.fetchone())["snapshot_json"])
        for key in ("timelines", "timeline_tracks", "timeline_clips"):
            snapshot.pop(key, None)
        await conn.execute(
            "UPDATE studio_revisions SET snapshot_json = ?"
            " WHERE project_id = ? AND seq = ?",
            (_json.dumps(snapshot, ensure_ascii=False), project_id, seq),
        )
        await conn.commit()




# --------------------------------------------------------------------------
# 音源基準の配置と差し込み（API）
# --------------------------------------------------------------------------

async def _seed_planned_takes(
    project_id: str, video_path: str, plans: list[float | None]
) -> tuple[str, list[str]]:
    """計画開始秒つきのカットを ``plans`` の数だけ並べた話を作る。

    返りは ``(episode_id, [shot_id, …])``。Take は採用済みで、動画は全部同じ
    ``video_path`` を指す（尺は ffprobe の差し替えで決める）。
    """
    from app.ids import new_id

    episode_id, scene_id = new_id(), new_id()
    now = "2026-01-01T00:00:00+00:00"
    shot_ids: list[str] = []
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO studio_episodes (id, project_id, sort_order, title,"
            " synopsis, created_at) VALUES (?, ?, 0, '第 1 話', '', ?)",
            (episode_id, project_id, now),
        )
        await conn.execute(
            "INSERT INTO studio_scenes (id, episode_id, project_id, sort_order,"
            " title, synopsis, time_of_day, created_at)"
            " VALUES (?, ?, ?, 0, '場 1', '', '', ?)",
            (scene_id, episode_id, project_id, now),
        )
        for order, planned in enumerate(plans):
            shot_id, job_id, take_id = new_id(), new_id(), new_id()
            await conn.execute(
                "INSERT INTO studio_shots (id, project_id, scene_id, sort_order,"
                " title, planned_start_seconds, selected_take_id, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    shot_id, project_id, scene_id, order,
                    f"カット {order + 1}", planned, take_id, now, now,
                ),
            )
            await conn.execute(
                "INSERT INTO jobs (id, created_at, mode, status, params,"
                " workflow_json, video_path)"
                " VALUES (?, ?, 'video', 'done', '{}', '{}', ?)",
                (job_id, now, video_path),
            )
            await conn.execute(
                "INSERT INTO studio_takes (id, shot_id, project_id, job_id, status,"
                " created_at) VALUES (?, ?, ?, ?, 'selected', ?)",
                (take_id, shot_id, project_id, job_id, now),
            )
            shot_ids.append(shot_id)
        await conn.commit()
    return episode_id, shot_ids


def test_a_planned_shot_lands_on_its_second_with_a_gap_in_front(
    client, tmp_path, monkeypatch
):
    """計画秒つきのカットは音源上の位置に置かれ、手前は gap で埋まる。"""
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 5000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, shot_ids = asyncio.run(
        _seed_planned_takes(project_id, str(video), [1.0, 4.0])
    )

    detail = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()
    clips = detail["tracks"][0]["clips"]
    assert [
        (clip["source_kind"], clip["start_ms"], clip["duration_ms"]) for clip in clips
    ] == [("gap", 0, 1000), ("take", 1000, 3000), ("take", 4000, 5000)]
    # 次の計画秒までで切られたぶんは切り出しにも出る
    assert (clips[1]["in_ms"], clips[1]["out_ms"]) == (0, 3000)


def test_sync_moves_a_clip_when_the_planned_second_changes(
    client, tmp_path, monkeypatch
):
    """計画秒を書き換えて sync すると、その位置へ置き直されて隙間が空く。"""
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 5000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, shot_ids = asyncio.run(
        _seed_planned_takes(project_id, str(video), [0.0, 3.0])
    )
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()
    assert [clip["start_ms"] for clip in timeline["tracks"][0]["clips"]] == [0, 3000]

    patched = client.patch(
        f"/api/studio/shots/{shot_ids[1]}", json={"planned_start_seconds": 4.0}
    )
    assert patched.status_code == 200
    assert patched.json()["planned_start_seconds"] == 4.0

    # 計画が正本なので、前のカットは次の計画秒（4 秒）まで伸びる（Take は 5 秒
    # あるので隙間は空かない）。
    detail = client.post(f"/api/studio/timelines/{timeline['id']}/sync", json={}).json()
    assert [
        (clip["source_kind"], clip["start_ms"], clip["duration_ms"])
        for clip in detail["tracks"][0]["clips"]
    ] == [("take", 0, 4000), ("take", 4000, 5000)]

    # 逆に Take より先の秒へ動かすと、届かないぶんが gap で埋まる
    client.patch(
        f"/api/studio/shots/{shot_ids[1]}", json={"planned_start_seconds": 7.0}
    )
    detail = client.post(f"/api/studio/timelines/{timeline['id']}/sync", json={}).json()
    assert [
        (clip["source_kind"], clip["start_ms"], clip["duration_ms"])
        for clip in detail["tracks"][0]["clips"]
    ] == [("take", 0, 5000), ("gap", 5000, 2000), ("take", 7000, 5000)]


def test_insert_clip_splits_the_clip_under_it(client, tmp_path, monkeypatch):
    """差し込むと下のクリップが 2 つに割れ、トラックの全長は変わらない。"""
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 4000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, take_id = asyncio.run(_seed_take(project_id, str(video)))
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()
    track_id = timeline["tracks"][0]["id"]

    response = client.post(
        f"/api/studio/timelines/{timeline['id']}/clips/insert",
        json={
            "track_id": track_id,
            "start_ms": 1000,
            "duration_ms": 1500,
            "source_kind": "take",
            "source_id": take_id,
            "in_ms": 0,
        },
    )
    assert response.status_code == 200
    clips = response.json()["tracks"][0]["clips"]
    assert [(clip["start_ms"], clip["duration_ms"]) for clip in clips] == [
        (0, 1000),
        (1000, 1500),
        (2500, 1500),
    ]
    # 後半は切り出しの続きから（下のクリップの尺は変えない）
    assert (clips[2]["in_ms"], clips[2]["out_ms"]) == (2500, 4000)
    assert response.json()["duration_ms"] == 4000




def test_insert_clip_refuses_a_stale_base_revision(client, tmp_path, monkeypatch):
    """差し込みの base_revision は、EDL が動いていれば 409（§7.4 の楽観ロック）。"""
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 4000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, take_id = asyncio.run(_seed_take(project_id, str(video)))
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()
    track_id = timeline["tracks"][0]["id"]
    seq = client.get(f"/api/studio/projects/{project_id}").json()["revision_seq"]

    body = {
        "track_id": track_id,
        "start_ms": 1000,
        "duration_ms": 500,
        "source_kind": "gap",
        "source_id": None,
    }
    first = client.post(
        f"/api/studio/timelines/{timeline['id']}/clips/insert",
        json={**body, "base_revision": seq},
    )
    assert first.status_code == 200, first.text

    # 同じ連番でもう一度 = 途中で EDL が動いているので断られる
    stale = client.post(
        f"/api/studio/timelines/{timeline['id']}/clips/insert",
        json={**body, "start_ms": 2000, "base_revision": seq},
    )
    assert stale.status_code == 409, stale.text

    # まだ無い連番は 400
    future = client.post(
        f"/api/studio/timelines/{timeline['id']}/clips/insert",
        json={**body, "start_ms": 2000, "base_revision": seq + 100},
    )
    assert future.status_code == 400



def test_insert_clip_rejects_an_unknown_track(client):
    project_id = _project(client)
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines", json={}
    ).json()
    response = client.post(
        f"/api/studio/timelines/{timeline['id']}/clips/insert",
        json={"track_id": "nope", "start_ms": 0, "duration_ms": 1000,
              "source_kind": "gap", "source_id": None},
    )
    assert response.status_code == 404



def test_export_runs_in_the_background_and_lands_in_the_history(
    client, tmp_path, monkeypatch
):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 2000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, _ = asyncio.run(_seed_take(project_id, str(video)))
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()

    seen: dict = {}

    async def fake_run(spec, output, *, on_progress=None):
        seen["spec"] = spec
        if on_progress is not None:
            await on_progress(0.5)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")
        return output

    monkeypatch.setattr(service, "run_export", fake_run)

    response = client.post(f"/api/studio/timelines/{timeline['id']}/export", json={})
    assert response.status_code == 202
    export_id = response.json()["id"]

    done = wait_for_export(client, timeline["id"])
    assert done["id"] == export_id
    assert done["status"] == "done"
    assert done["progress"] == 1.0
    assert done["output_url"] == f"/outputs/exports/{export_id}/final.mp4"
    assert done["error"] is None
    # 焼き上がりの規格と検算（Remotion の base に渡すとき props と揃えるため）
    assert (done["fps"], done["width"], done["height"]) == (24.0, 1280, 720)
    assert done["frames"] == 48  # 2 秒 * 24fps
    assert done["duration_ms"] == 2000
    assert done["warnings"] == []

    # 焼く直前の EDL がタイムラインどおりに組み立てられている
    spec = seen["spec"]
    assert (spec.width, spec.height, spec.fps) == (1280, 720, 24.0)
    assert [clip.duration_ms for clip in spec.clips] == [2000]
    assert spec.clips[0].path == str(video)


def test_a_failed_export_keeps_the_reason(client, tmp_path, monkeypatch):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 1000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, _ = asyncio.run(_seed_take(project_id, str(video)))
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()

    async def boom(spec, output, *, on_progress=None):
        raise TimelineExportError("ffmpeg が失敗しました: bad filter")

    monkeypatch.setattr(service, "run_export", boom)
    client.post(f"/api/studio/timelines/{timeline['id']}/export", json={})

    export = wait_for_export(client, timeline["id"])
    assert export["status"] == "failed"
    assert "bad filter" in export["error"]


def test_exporting_an_empty_timeline_fails_with_a_reason(client, monkeypatch):
    project_id = _project(client)
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines", json={}
    ).json()
    response = client.post(f"/api/studio/timelines/{timeline['id']}/export", json={})
    assert response.status_code == 202
    export = wait_for_export(client, timeline["id"])
    assert export["status"] == "failed"
    assert "クリップ" in export["error"]


def test_save_to_library_copies_the_finished_mp4(client, tmp_path, monkeypatch):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 1000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    monkeypatch.setattr(
        service.library_service, "LIBRARY_DIR", tmp_path / "library"
    )

    episode_id, _ = asyncio.run(_seed_take(project_id, str(video)))
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()

    async def fake_run(spec, output, *, on_progress=None):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")
        return output

    monkeypatch.setattr(service, "run_export", fake_run)
    export_id = client.post(
        f"/api/studio/timelines/{timeline['id']}/export", json={}
    ).json()["id"]
    assert wait_for_export(client, timeline["id"])["status"] == "done"

    response = client.post(
        f"/api/studio/exports/{export_id}/save-to-library", json={"name": "本編"}
    )
    assert response.status_code == 201
    item = response.json()
    assert item["kind"] == "video"
    assert item["name"] == "本編.mp4"

    # 元の書き出しは動かない（コピー）
    assert service.export_dir(export_id).joinpath("final.mp4").is_file()


def test_save_to_library_refuses_an_unfinished_export(client):
    project_id = _project(client)
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines", json={}
    ).json()
    export_id = client.post(
        f"/api/studio/timelines/{timeline['id']}/export", json={}
    ).json()["id"]
    response = client.post(f"/api/studio/exports/{export_id}/save-to-library", json={})
    assert response.status_code == 400
    assert client.post(
        "/api/studio/exports/NOPE/save-to-library", json={}
    ).status_code == 404


# --------------------------------------------------------------------------
# フェーズ 2/3 の純関数（繋ぎ・リタイム・ASS・台詞の割り付け）
# --------------------------------------------------------------------------

def test_transition_offsets_shrinks_the_timeline_by_each_overlap():
    # 3 つ（2000 / 3000 / 1000）を 500 ずつ重ねる
    offsets, total = timeline_export.transition_offsets(
        [2000, 3000, 1000], [500, 500]
    )
    # 1 本目の末尾 500ms 手前から重ね始め、そのあとは積み上がった長さから引く
    assert offsets == [1500, 4000]
    assert total == 2000 + 3000 + 1000 - 500 - 500


def test_transition_offsets_handles_a_single_run():
    assert timeline_export.transition_offsets([1234], []) == ([], 1234)
    assert timeline_export.transition_offsets([], []) == ([], 0)


def test_transition_offsets_refuses_a_mismatched_count():
    with pytest.raises(TimelineExportError):
        timeline_export.transition_offsets([1000, 1000], [])


def test_atempo_chain_splits_beyond_the_filter_range():
    assert timeline_export.atempo_chain(1.0) == [1.0]
    assert timeline_export.atempo_chain(1.5) == [1.5]
    assert timeline_export.atempo_chain(2.0) == [2.0]
    # 範囲の外は 2 倍・半分に割って、積が元の速度になる
    for speed in (0.25, 0.3, 4.0, 3.5):
        chain = timeline_export.atempo_chain(speed)
        assert all(0.5 - 1e-9 <= factor <= 2.0 + 1e-9 for factor in chain)
        product = 1.0
        for factor in chain:
            product *= factor
        assert product == pytest.approx(speed)


def test_atempo_chain_refuses_a_broken_speed():
    with pytest.raises(TimelineExportError):
        timeline_export.atempo_chain(0)


def test_resolve_format_uses_the_preset_or_the_timeline():
    assert timeline_export.resolve_format("timeline", 1280, 720) == (1280, 720)
    assert timeline_export.resolve_format("1080p", 1280, 720) == (1920, 1080)
    assert timeline_export.resolve_format("vertical", 1280, 720) == (1080, 1920)
    assert timeline_export.resolve_format("なにこれ", 640, 360) == (640, 360)


def test_escape_filter_path_protects_the_graph_separators():
    escaped = timeline_export.escape_filter_path("/a:b/c,d[e].ass")
    assert escaped == "/a\\:b/c\\,d\\[e\\].ass"


# --------------------------------------------------------------------------
# 書き出しコマンド（フェーズ 2/3 の中身）
# --------------------------------------------------------------------------

def test_build_command_uses_xfade_and_acrossfade_across_a_transition():
    spec = ExportSpec(
        width=1280,
        height=720,
        fps=24,
        clips=[
            _clip("/x/one.mp4", 0, 2000),
            ExportClip(
                path="/x/two.mp4",
                in_ms=0,
                out_ms=3000,
                duration_ms=3000,
                transition_kind="crossfade",
                transition_ms=500,
            ),
        ],
    )
    graph = build_command(spec, "/o.mp4")[
        build_command(spec, "/o.mp4").index("-filter_complex") + 1
    ]
    # まとまりは 2 つ（それぞれ concat=n=1 でラベルを揃えてから重ねる）
    assert graph.count("concat=n=1:v=1:a=1") == 2
    assert "xfade=transition=fade:duration=0.500000:offset=1.500000" in graph
    assert "acrossfade=d=0.500000:c1=tri:c2=tri" in graph
    # 全長は重なったぶん縮む
    assert spec.duration_ms == 4500


def test_build_command_keeps_concat_for_the_clips_around_a_transition():
    """繋ぎのないところは今までどおり 1 回の concat で繋ぐ。"""
    spec = ExportSpec(
        width=640,
        height=360,
        fps=24,
        clips=[
            _clip("/x/1.mp4"),
            _clip("/x/2.mp4"),
            ExportClip(
                path="/x/3.mp4",
                in_ms=0,
                out_ms=2000,
                duration_ms=2000,
                transition_kind="wipeleft",
                transition_ms=400,
            ),
            _clip("/x/4.mp4"),
        ],
    )
    command = build_command(spec, "/o.mp4")
    graph = command[command.index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1[r0v][r0a]" in graph
    assert "concat=n=2:v=1:a=1[r1v][r1a]" in graph
    assert "xfade=transition=wipeleft" in graph


def test_build_command_ignores_an_unknown_transition():
    spec = ExportSpec(
        width=640,
        height=360,
        fps=24,
        clips=[
            _clip("/x/1.mp4"),
            ExportClip(
                path="/x/2.mp4",
                in_ms=0,
                out_ms=2000,
                duration_ms=2000,
                transition_kind="ワープ",
                transition_ms=400,
            ),
        ],
    )
    command = build_command(spec, "/o.mp4")
    graph = command[command.index("-filter_complex") + 1]
    assert "xfade" not in graph
    assert graph.endswith("concat=n=2:v=1:a=1[outv][outa]")


def test_build_command_retimes_video_and_audio():
    spec = ExportSpec(
        width=640,
        height=360,
        fps=24,
        clips=[
            ExportClip(
                path="/x/fast.mp4", in_ms=0, out_ms=4000, duration_ms=1000, speed=4.0
            )
        ],
    )
    command = build_command(spec, "/o.mp4")
    graph = command[command.index("-filter_complex") + 1]
    assert "setpts=(PTS-STARTPTS)/4" in graph
    # atempo は 2 段（0.5〜2.0 の外なので割る）
    assert graph.count("atempo=2") == 2


def test_build_command_loops_a_still_image_into_a_clip():
    spec = ExportSpec(
        width=1280,
        height=720,
        fps=24,
        clips=[
            ExportClip(
                path="/x/still.png",
                in_ms=0,
                out_ms=3000,
                duration_ms=3000,
                kind="image",
                has_audio=False,
            )
        ],
    )
    command = build_command(spec, "/o.mp4")
    assert "-loop" in command
    assert command[command.index("-loop") + 1] == "1"
    graph = command[command.index("-filter_complex") + 1]
    # 尺は -t（1 フレーム余分）と trim=end_frame で決まる（3 秒 = 72f）
    assert "trim=start=" not in graph
    assert "trim=end_frame=72" in graph
    assert any("anullsrc" in arg for arg in command)


def test_build_command_mixes_the_audio_track_over_the_video():
    spec = ExportSpec(
        width=640,
        height=360,
        fps=24,
        clips=[_clip("/x/one.mp4", 0, 5000)],
        audio_clips=[
            timeline_export.ExportAudioClip(
                path="/x/bgm.mp3",
                start_ms=1000,
                in_ms=0,
                out_ms=4000,
                gain_db=-6.0,
                fade_in_ms=500,
                fade_out_ms=800,
            )
        ],
    )
    command = build_command(spec, "/o.mp4")
    graph = command[command.index("-filter_complex") + 1]
    assert "atrim=start=0.000:end=4.000" in graph
    assert "volume=-6dB" in graph
    assert "afade=t=in:st=0:d=0.500" in graph
    assert "afade=t=out:st=3.200:d=0.800" in graph
    assert "adelay=1000:all=1" in graph
    # 映像側の音を先頭に置き、全長は映像に合わせて切る
    assert "amix=inputs=2:duration=first:dropout_transition=0:normalize=0[outa]" in graph


def test_build_command_burns_in_the_subtitles_and_normalises_loudness():
    spec = ExportSpec(
        width=640,
        height=360,
        fps=24,
        clips=[_clip("/x/one.mp4")],
        subtitles_path="/tmp/subs.ass",
        loudnorm=True,
    )
    command = build_command(spec, "/o.mp4")
    graph = command[command.index("-filter_complex") + 1]
    assert "subtitles=filename=/tmp/subs.ass[outv]" in graph
    assert "loudnorm=I=-14:TP=-1.5:LRA=11[outa]" in graph


def test_build_command_can_crop_instead_of_letterboxing():
    spec = ExportSpec(
        width=1080,
        height=1920,
        fps=24,
        clips=[_clip("/x/one.mp4")],
        fit=timeline_export.FIT_CROP,
    )
    command = build_command(spec, "/o.mp4")
    graph = command[command.index("-filter_complex") + 1]
    assert "force_original_aspect_ratio=increase" in graph
    assert "crop=1080:1920" in graph
    # 黒帯は付かない（tpad / apad は尺を揃えるためのものなので数に入れない）
    assert "pad=1080:1920" not in graph


# --------------------------------------------------------------------------
# ASS の組み立てと台詞の割り付け（純関数）
# --------------------------------------------------------------------------

def test_build_ass_writes_a_header_and_one_line_per_event():
    from app.timeline_subtitles import SubtitleEvent, build_ass

    ass = build_ass(
        [
            SubtitleEvent(0, 1500, "こんにちは"),
            SubtitleEvent(1500, 3000, "上に黄色で", position="top", color="yellow",
                          size="L"),
        ],
        1920,
        1080,
    )
    assert "PlayResX: 1920" in ass and "PlayResY: 1080" in ass
    assert ass.count("Dialogue: ") == 2
    assert "0:00:00.00,0:00:01.50" in ass
    # 上・黄・大きめが上書きタグで付く
    assert "{\\an8" in ass and "\\c&H0000FFFF" in ass
    assert "{\\an2" in ass


def test_build_ass_drops_empty_and_zero_length_lines():
    from app.timeline_subtitles import SubtitleEvent, build_ass

    ass = build_ass(
        [SubtitleEvent(0, 0, "尺なし"), SubtitleEvent(0, 1000, "   ")], 1280, 720
    )
    assert "Dialogue: " not in ass


def test_escape_text_folds_newlines_and_neutralises_braces():
    from app.timeline_subtitles import escape_text

    assert escape_text("上\n下") == "上\\N下"
    assert escape_text("{\\an8}偽タグ") == "｛\\an8｝偽タグ"


def test_format_time_rounds_to_centiseconds():
    from app.timeline_subtitles import format_time

    assert format_time(0) == "0:00:00.00"
    assert format_time(1234) == "0:00:01.23"
    assert format_time(3_661_000) == "1:01:01.00"


def test_split_dialogue_prefers_newlines_then_sentence_ends():
    from app.timeline_subtitles import split_dialogue

    assert split_dialogue("「行こう」") == ["行こう"]
    assert split_dialogue("一行目\n二行目") == ["一行目", "二行目"]
    assert split_dialogue("待って。もう遅い！") == ["待って。", "もう遅い！"]
    assert split_dialogue("   ") == []


def test_place_dialogue_divides_the_clip_evenly():
    from app.timeline_subtitles import place_dialogue

    placed = place_dialogue(1000, 3000, "一つ。二つ。三つ。")
    assert [(item.start_ms, item.duration_ms) for item in placed] == [
        (1000, 1000),
        (2000, 1000),
        (3000, 1000),
    ]
    # 合計はクリップの尺とぴったり合う
    assert sum(item.duration_ms for item in placed) == 3000


def test_place_dialogue_folds_the_tail_when_the_clip_is_short():
    from app.timeline_subtitles import place_dialogue

    placed = place_dialogue(0, 900, "一つ。二つ。三つ。")
    assert len(placed) == 1
    assert placed[0].text == "一つ。二つ。三つ。"
    assert placed[0].duration_ms == 900


def test_place_dialogue_ignores_an_empty_line():
    from app.timeline_subtitles import place_dialogue

    assert place_dialogue(0, 1000, "") == []
    assert place_dialogue(0, 0, "台詞") == []


# --------------------------------------------------------------------------
# 検証（繋ぎ・速度・テロップ）
# --------------------------------------------------------------------------

VIDEO_TRACK = {"T1": "video"}


def test_validate_clips_accepts_a_transition_that_overlaps_the_previous_clip():
    service.validate_clips(
        [
            _input(start_ms=0, duration_ms=2000, out_ms=2000),
            _input(
                start_ms=1500,
                duration_ms=2000,
                out_ms=2000,
                transition_kind="crossfade",
                transition_ms=500,
            ),
        ],
        VIDEO_TRACK,
    )


def test_validate_clips_rejects_a_transition_longer_than_half_the_shorter_clip():
    with pytest.raises(service.TimelineError, match="長すぎ"):
        service.validate_clips(
            [
                _input(start_ms=0, duration_ms=1000, out_ms=1000),
                _input(
                    start_ms=400,
                    duration_ms=2000,
                    out_ms=2000,
                    transition_kind="crossfade",
                    transition_ms=600,
                ),
            ],
            VIDEO_TRACK,
        )


def test_validate_clips_rejects_a_transition_on_the_first_clip():
    with pytest.raises(service.TimelineError, match="先頭"):
        service.validate_clips(
            [_input(transition_kind="crossfade", transition_ms=300)], VIDEO_TRACK
        )


def test_validate_clips_rejects_a_transition_on_an_audio_track():
    with pytest.raises(service.TimelineError, match="映像トラック"):
        service.validate_clips(
            [
                _input(track_id="A1", start_ms=0, duration_ms=2000, out_ms=2000),
                _input(
                    track_id="A1",
                    start_ms=1500,
                    duration_ms=2000,
                    out_ms=2000,
                    transition_kind="crossfade",
                    transition_ms=500,
                ),
            ],
            {"A1": "audio"},
        )


def test_validate_clips_rejects_an_unknown_transition():
    with pytest.raises(service.TimelineError, match="知らない繋ぎ"):
        service.validate_clips(
            [
                _input(start_ms=0, duration_ms=2000, out_ms=2000),
                _input(
                    start_ms=1500,
                    duration_ms=2000,
                    out_ms=2000,
                    transition_kind="ワープ",
                    transition_ms=500,
                ),
            ],
            VIDEO_TRACK,
        )


def test_validate_clips_accepts_a_retimed_clip():
    # 4 秒ぶんを 2 倍速 = 2 秒
    service.validate_clips([_input(duration_ms=2000, in_ms=0, out_ms=4000, speed=2.0)])


def test_validate_clips_rejects_a_speed_outside_the_range():
    with pytest.raises(service.TimelineError, match="速度は"):
        service.validate_clips(
            [_input(duration_ms=125, in_ms=0, out_ms=1000, speed=8.0)]
        )


def test_validate_clips_rejects_a_retimed_audio_clip():
    with pytest.raises(service.TimelineError, match="映像クリップ"):
        service.validate_clips(
            [_input(track_id="A1", duration_ms=500, in_ms=0, out_ms=1000, speed=2.0)],
            {"A1": "audio"},
        )


def test_validate_clips_rejects_an_empty_subtitle():
    with pytest.raises(service.TimelineError, match="本文が空"):
        service.validate_clips(
            [
                _input(
                    track_id="S1",
                    source_kind="text",
                    source_id=None,
                    in_ms=0,
                    out_ms=0,
                    text_payload={"text": "  "},
                )
            ],
            {"S1": "subtitle"},
        )


def test_validate_clips_ignores_the_trim_of_a_text_or_image_clip():
    service.validate_clips(
        [
            _input(
                track_id="S1",
                source_kind="text",
                source_id=None,
                in_ms=0,
                out_ms=0,
                text_payload={"text": "テロップ"},
            )
        ],
        {"S1": "subtitle"},
    )
    service.validate_clips(
        [_input(source_kind="image", source_id="library:X", in_ms=0, out_ms=0)]
    )


def test_relayout_packs_the_track_and_clamps_the_overlap():
    from app.models import TimelineClipInput

    def clip(duration, transition=None, transition_ms=0):
        return TimelineClipInput(
            track_id="V1",
            start_ms=0,
            duration_ms=duration,
            source_kind="take",
            source_id="T",
            in_ms=0,
            out_ms=duration,
            transition_kind=transition,
            transition_ms=transition_ms,
        )

    placed = service.relayout(
        [clip(2000), clip(3000, "crossfade", 500), clip(1000, "crossfade", 900)]
    )
    assert [item.start_ms for item in placed] == [0, 1500, 4000]
    # 3 本目は 1000ms しかないので、重なりは半分の 500ms へ丸まる
    assert placed[2].transition_ms == 500
    # 先頭の繋ぎは落ちる
    assert placed[0].transition_kind is None


def test_relayout_drops_a_transition_that_would_be_too_short():
    from app.models import TimelineClipInput

    clips = [
        TimelineClipInput(
            track_id="V1", start_ms=0, duration_ms=2000, source_kind="take",
            source_id="A", in_ms=0, out_ms=2000,
        ),
        TimelineClipInput(
            track_id="V1", start_ms=0, duration_ms=300, source_kind="take",
            source_id="B", in_ms=0, out_ms=300,
            transition_kind="crossfade", transition_ms=500,
        ),
    ]
    placed = service.relayout(clips)
    assert placed[1].transition_kind is None
    assert placed[1].start_ms == 2000


def test_split_image_source_reads_the_provider_marker():
    assert service.split_image_source("library:ABC") == ("library", "ABC")
    assert service.split_image_source("job:XYZ") == ("job", "XYZ")
    # 印が無ければライブラリとして読む
    assert service.split_image_source("PLAIN") == ("library", "PLAIN")
    # 知らない印は解決させない
    assert service.split_image_source("なにか:1")[0] == ""


# --------------------------------------------------------------------------
# API（トラック・素材ビン・テロップ生成・差分・欠落）
# --------------------------------------------------------------------------

def _empty_timeline(client, project_id):
    return client.post(f"/api/studio/projects/{project_id}/timelines", json={}).json()


def test_tracks_can_be_added_renamed_muted_and_removed(client):
    project_id = _project(client)
    timeline = _empty_timeline(client, project_id)

    added = client.post(
        f"/api/studio/timelines/{timeline['id']}/tracks", json={"kind": "audio"}
    )
    assert added.status_code == 201
    tracks = added.json()["tracks"]
    assert [track["name"] for track in tracks] == ["V1", "A1"]

    second = client.post(
        f"/api/studio/timelines/{timeline['id']}/tracks", json={"kind": "audio"}
    ).json()
    assert [track["name"] for track in second["tracks"]] == ["V1", "A1", "A2"]

    audio_id = second["tracks"][1]["id"]
    muted = client.patch(
        f"/api/studio/timelines/{timeline['id']}/tracks/{audio_id}",
        json={"muted": True, "name": "BGM"},
    ).json()
    assert muted["tracks"][1]["muted"] is True
    assert muted["tracks"][1]["name"] == "BGM"

    left = client.delete(
        f"/api/studio/timelines/{timeline['id']}/tracks/{audio_id}"
    ).json()
    assert [track["name"] for track in left["tracks"]] == ["V1", "A2"]


def test_the_video_track_can_neither_be_added_nor_removed(client):
    project_id = _project(client)
    timeline = _empty_timeline(client, project_id)
    assert (
        client.post(
            f"/api/studio/timelines/{timeline['id']}/tracks", json={"kind": "video"}
        ).status_code
        == 400
    )
    video_id = timeline["tracks"][0]["id"]
    assert (
        client.delete(
            f"/api/studio/timelines/{timeline['id']}/tracks/{video_id}"
        ).status_code
        == 400
    )


def test_an_audio_clip_may_sit_anywhere_on_its_own_track(client):
    project_id = _project(client)
    timeline = _empty_timeline(client, project_id)
    detail = client.post(
        f"/api/studio/timelines/{timeline['id']}/tracks", json={"kind": "audio"}
    ).json()
    audio_id = detail["tracks"][1]["id"]

    response = client.put(
        f"/api/studio/timelines/{timeline['id']}/clips",
        json={
            "clips": [
                {
                    "track_id": audio_id,
                    "start_ms": 4000,
                    "duration_ms": 2000,
                    "source_kind": "library",
                    "source_id": "BGM",
                    "in_ms": 0,
                    "out_ms": 2000,
                    "gain_db": -3.0,
                    "fade_in_ms": 400,
                    "fade_out_ms": 600,
                }
            ]
        },
    )
    assert response.status_code == 200
    clip = response.json()["tracks"][1]["clips"][0]
    assert (clip["start_ms"], clip["gain_db"], clip["fade_out_ms"]) == (4000, -3.0, 600)
    # ライブラリに無い id なのでメディア欠落として見える
    assert clip["missing"] is True


def test_media_bin_lists_takes_library_and_jobs(client, tmp_path, monkeypatch):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 4321, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    monkeypatch.setattr(service, "OUTPUTS_DIR", tmp_path)
    asyncio.run(_seed_take(project_id, str(video)))
    asyncio.run(_seed_audio_job(str(tmp_path / "bgm.mp3")))
    (tmp_path / "bgm.mp3").write_bytes(b"x")

    videos = client.get(
        f"/api/studio/projects/{project_id}/media", params={"kind": "video"}
    ).json()
    assert videos["total"] == 1
    assert videos["items"][0]["source_kind"] == "take"
    assert videos["items"][0]["duration_ms"] == 4321

    audio = client.get(
        f"/api/studio/projects/{project_id}/media", params={"kind": "audio"}
    ).json()
    assert [item["source_kind"] for item in audio["items"]] == ["job"]
    assert audio["items"][0]["origin"] == "ジョブ"


def test_media_bin_refuses_an_unknown_project(client):
    assert (
        client.get("/api/studio/projects/NOPE/media", params={"kind": "video"}).status_code
        == 404
    )


async def _seed_audio_job(audio_path: str) -> str:
    from app.ids import new_id

    job_id = new_id()
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO jobs (id, created_at, mode, status, params, workflow_json,"
            " audio_output_path, user_input)"
            " VALUES (?, '2026-01-02T00:00:00+00:00', 'audio', 'done', '{}', '{}',"
            " ?, '静かなピアノ')",
            (job_id, audio_path),
        )
        await conn.commit()
    return job_id


def test_generate_subtitles_lays_the_dialogue_over_the_cuts(
    client, tmp_path, monkeypatch
):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 4000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, take_id = asyncio.run(_seed_take(project_id, str(video)))
    asyncio.run(_set_dialogue(take_id, "行こう。もう時間がない。"))

    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()

    detail = client.post(
        f"/api/studio/timelines/{timeline['id']}/generate-subtitles", json={}
    ).json()
    subtitle_tracks = [t for t in detail["tracks"] if t["kind"] == "subtitle"]
    assert len(subtitle_tracks) == 1
    assert subtitle_tracks[0]["name"] == "T1"
    clips = subtitle_tracks[0]["clips"]
    assert [clip["text_payload"]["text"] for clip in clips] == [
        "行こう。",
        "もう時間がない。",
    ]
    assert [(clip["start_ms"], clip["duration_ms"]) for clip in clips] == [
        (0, 2000),
        (2000, 2000),
    ]
    assert all(clip["source_kind"] == "text" for clip in clips)

    # もう一度走らせても積み増さない（置き換え）
    again = client.post(
        f"/api/studio/timelines/{timeline['id']}/generate-subtitles", json={}
    ).json()
    assert len(
        [t for t in again["tracks"] if t["kind"] == "subtitle"][0]["clips"]
    ) == 2


async def _set_dialogue(take_id: str, dialogue: str) -> None:
    async with db.get_db() as conn:
        await conn.execute(
            "UPDATE studio_shots SET dialogue = ?"
            " WHERE id = (SELECT shot_id FROM studio_takes WHERE id = ?)",
            (dialogue, take_id),
        )
        await conn.commit()


def test_generate_subtitles_refuses_a_track_that_is_not_a_subtitle_track(client):
    project_id = _project(client)
    timeline = _empty_timeline(client, project_id)
    response = client.post(
        f"/api/studio/timelines/{timeline['id']}/generate-subtitles",
        json={"track_id": timeline["tracks"][0]["id"]},
    )
    assert response.status_code == 400


def test_sync_preview_reports_a_new_cut_and_applying_it_appends_a_clip(
    client, tmp_path, monkeypatch
):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 2000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, _ = asyncio.run(_seed_take(project_id, str(video)))
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()
    assert len(timeline["tracks"][0]["clips"]) == 1

    # あとから 2 つ目のカットが増えた
    second = tmp_path / "second.mp4"
    second.write_bytes(b"y")
    asyncio.run(_add_shot(project_id, episode_id, str(second), order=1))

    preview = client.get(
        f"/api/studio/timelines/{timeline['id']}/sync-preview"
    ).json()
    assert len(preview["added"]) == 1
    assert preview["retaken"] == [] and preview["removed"] == []

    applied = client.post(
        f"/api/studio/timelines/{timeline['id']}/sync",
        json={"add_shot_ids": [preview["added"][0]["shot_id"]]},
    ).json()
    clips = applied["tracks"][0]["clips"]
    assert len(clips) == 2
    assert [clip["start_ms"] for clip in clips] == [0, 2000]


def test_sync_preview_reports_a_changed_take_and_a_dropped_cut(
    client, tmp_path, monkeypatch
):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 2000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, take_id = asyncio.run(_seed_take(project_id, str(video)))
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()

    # 同じカットで別のテイクを採用し直した
    retake = tmp_path / "retake.mp4"
    retake.write_bytes(b"z")
    new_take = asyncio.run(_add_take(project_id, take_id, str(retake)))

    preview = client.get(
        f"/api/studio/timelines/{timeline['id']}/sync-preview"
    ).json()
    assert len(preview["retaken"]) == 1
    assert preview["retaken"][0]["new_take_id"] == new_take

    applied = client.post(
        f"/api/studio/timelines/{timeline['id']}/sync",
        json={"retake_clip_ids": [preview["retaken"][0]["clip_id"]]},
    ).json()
    assert applied["tracks"][0]["clips"][0]["source_id"] == new_take

    # 採用を外すと「消えたカット」として出る
    asyncio.run(_clear_selection(new_take))
    preview = client.get(
        f"/api/studio/timelines/{timeline['id']}/sync-preview"
    ).json()
    assert len(preview["removed"]) == 1
    emptied = client.post(
        f"/api/studio/timelines/{timeline['id']}/sync",
        json={"remove_clip_ids": [preview["removed"][0]["clip_id"]]},
    ).json()
    assert emptied["tracks"][0]["clips"] == []


async def _add_shot(project_id, episode_id, video_path, order=1) -> str:
    from app.ids import new_id

    shot_id, job_id, take_id = new_id(), new_id(), new_id()
    now = "2026-01-03T00:00:00+00:00"
    async with db.get_db() as conn:
        async with conn.execute(
            "SELECT id FROM studio_scenes WHERE episode_id = ?", (episode_id,)
        ) as cur:
            scene_id = (await cur.fetchone())["id"]
        await conn.execute(
            "INSERT INTO studio_shots (id, project_id, scene_id, sort_order, title,"
            " selected_take_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'カット 2', ?, ?, ?)",
            (shot_id, project_id, scene_id, order, take_id, now, now),
        )
        await conn.execute(
            "INSERT INTO jobs (id, created_at, mode, status, params, workflow_json,"
            " video_path) VALUES (?, ?, 'video', 'done', '{}', '{}', ?)",
            (job_id, now, video_path),
        )
        await conn.execute(
            "INSERT INTO studio_takes (id, shot_id, project_id, job_id, status,"
            " created_at) VALUES (?, ?, ?, ?, 'selected', ?)",
            (take_id, shot_id, project_id, job_id, now),
        )
        await conn.commit()
    return shot_id


async def _add_take(project_id, sibling_take_id, video_path) -> str:
    """``sibling_take_id`` と同じカットに Take を足して、そちらを採用する。"""
    from app.ids import new_id

    job_id, take_id = new_id(), new_id()
    now = "2026-01-04T00:00:00+00:00"
    async with db.get_db() as conn:
        async with conn.execute(
            "SELECT shot_id FROM studio_takes WHERE id = ?", (sibling_take_id,)
        ) as cur:
            shot_id = (await cur.fetchone())["shot_id"]
        await conn.execute(
            "INSERT INTO jobs (id, created_at, mode, status, params, workflow_json,"
            " video_path) VALUES (?, ?, 'video', 'done', '{}', '{}', ?)",
            (job_id, now, video_path),
        )
        await conn.execute(
            "INSERT INTO studio_takes (id, shot_id, project_id, job_id, status,"
            " created_at) VALUES (?, ?, ?, ?, 'selected', ?)",
            (take_id, shot_id, project_id, job_id, now),
        )
        await conn.execute(
            "UPDATE studio_shots SET selected_take_id = ? WHERE id = ?",
            (take_id, shot_id),
        )
        await conn.commit()
    return take_id


async def _clear_selection(take_id: str) -> None:
    async with db.get_db() as conn:
        await conn.execute(
            "UPDATE studio_shots SET selected_take_id = NULL"
            " WHERE id = (SELECT shot_id FROM studio_takes WHERE id = ?)",
            (take_id,),
        )
        await conn.commit()


def test_missing_report_offers_another_take_and_resolve_swaps_it(
    client, tmp_path, monkeypatch
):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 2000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, take_id = asyncio.run(_seed_take(project_id, str(video)))
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()

    spare = tmp_path / "spare.mp4"
    spare.write_bytes(b"y")
    spare_take = asyncio.run(_add_take(project_id, take_id, str(spare)))
    video.unlink()  # 採用テイクの動画が消えた

    report = client.get(f"/api/studio/timelines/{timeline['id']}/missing").json()
    assert len(report["clips"]) == 1
    assert [c["take_id"] for c in report["clips"][0]["candidates"]] == [spare_take]

    clip_id = report["clips"][0]["clip_id"]
    fixed = client.post(
        f"/api/studio/timelines/{timeline['id']}/missing/resolve",
        json={"replace": {clip_id: spare_take}},
    ).json()
    clip = fixed["tracks"][0]["clips"][0]
    assert clip["source_id"] == spare_take
    assert clip["missing"] is False


def test_resolve_missing_can_drop_every_broken_clip(client, tmp_path, monkeypatch):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 2000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, _ = asyncio.run(_seed_take(project_id, str(video)))
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()
    video.unlink()

    fixed = client.post(
        f"/api/studio/timelines/{timeline['id']}/missing/resolve",
        json={"drop_all": True},
    ).json()
    assert fixed["tracks"][0]["clips"] == []


def test_export_refuses_a_timeline_with_a_missing_clip(client, tmp_path, monkeypatch):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 2000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, _ = asyncio.run(_seed_take(project_id, str(video)))
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()
    video.unlink()

    response = client.post(f"/api/studio/timelines/{timeline['id']}/export", json={})
    assert response.status_code == 400
    assert "メディアが見つからない" in response.json()["detail"]


def test_export_carries_the_preset_fit_and_loudnorm_into_the_spec(
    client, tmp_path, monkeypatch
):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 2000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, _ = asyncio.run(_seed_take(project_id, str(video)))
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()

    seen: dict = {}

    async def fake_run(spec, output, *, on_progress=None):
        seen["spec"] = spec
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")
        return output

    monkeypatch.setattr(service, "run_export", fake_run)
    client.post(
        f"/api/studio/timelines/{timeline['id']}/export",
        json={"preset": "vertical", "fit": "crop", "loudnorm": True},
    )
    assert wait_for_export(client, timeline["id"])["status"] == "done"
    spec = seen["spec"]
    assert (spec.width, spec.height) == (1080, 1920)
    assert spec.fit == "crop"
    assert spec.loudnorm is True


def test_export_writes_an_ass_file_for_the_subtitle_track(
    client, tmp_path, monkeypatch
):
    project_id = _project(client)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    async def fake_probe(path):
        return 4000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, take_id = asyncio.run(_seed_take(project_id, str(video)))
    asyncio.run(_set_dialogue(take_id, "行こう。"))
    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()
    client.post(
        f"/api/studio/timelines/{timeline['id']}/generate-subtitles", json={}
    )

    seen: dict = {}

    async def fake_run(spec, output, *, on_progress=None):
        seen["spec"] = spec
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")
        return output

    monkeypatch.setattr(service, "run_export", fake_run)
    client.post(f"/api/studio/timelines/{timeline['id']}/export", json={})
    assert wait_for_export(client, timeline["id"])["status"] == "done"

    path = seen["spec"].subtitles_path
    assert path is not None
    from pathlib import Path as _Path

    assert "行こう。" in _Path(path).read_text(encoding="utf-8")


def test_generated_subtitles_never_overlap_across_a_transition(
    client, tmp_path, monkeypatch
):
    """繋ぎで映像が重なっていても、テロップどうしは重ねない。

    同じトラックでの重なりは 400 なので、生成したものがそのまま保存できないと
    画面の自動保存が落ち続ける。
    """
    project_id = _project(client)
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    first.write_bytes(b"x")
    second.write_bytes(b"y")

    async def fake_probe(path):
        return 4000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)
    episode_id, take_id = asyncio.run(_seed_take(project_id, str(first)))
    asyncio.run(_set_dialogue(take_id, "行こう。"))
    shot_id = asyncio.run(_add_shot(project_id, episode_id, str(second), order=1))
    asyncio.run(_set_shot_dialogue(shot_id, "急ごう。"))

    timeline = client.post(
        f"/api/studio/projects/{project_id}/timelines",
        json={"episode_id": episode_id},
    ).json()
    track_id = timeline["tracks"][0]["id"]
    clips = timeline["tracks"][0]["clips"]

    # 2 本目に 1 秒のクロスフェード（オーバーラップ方式なので前へ食い込む）
    body = [
        {
            "id": clips[0]["id"], "track_id": track_id, "start_ms": 0,
            "duration_ms": 4000, "source_kind": "take",
            "source_id": clips[0]["source_id"], "in_ms": 0, "out_ms": 4000,
        },
        {
            "id": clips[1]["id"], "track_id": track_id, "start_ms": 3000,
            "duration_ms": 4000, "source_kind": "take",
            "source_id": clips[1]["source_id"], "in_ms": 0, "out_ms": 4000,
            "transition_kind": "crossfade", "transition_ms": 1000,
        },
    ]
    assert client.put(
        f"/api/studio/timelines/{timeline['id']}/clips", json={"clips": body}
    ).status_code == 200

    detail = client.post(
        f"/api/studio/timelines/{timeline['id']}/generate-subtitles", json={}
    ).json()
    subs = [t for t in detail["tracks"] if t["kind"] == "subtitle"][0]["clips"]
    spans = [(clip["start_ms"], clip["duration_ms"]) for clip in subs]
    assert spans == [(0, 4000), (4000, 3000)]
    # そのまま保存し直せる（重なりで 400 にならない）
    saved = client.put(
        f"/api/studio/timelines/{timeline['id']}/clips",
        json={
            "clips": [
                {
                    "id": clip["id"], "track_id": clip["track_id"],
                    "start_ms": clip["start_ms"], "duration_ms": clip["duration_ms"],
                    "source_kind": clip["source_kind"], "source_id": clip["source_id"],
                    "in_ms": clip["in_ms"], "out_ms": clip["out_ms"],
                    "transition_kind": clip["transition_kind"],
                    "transition_ms": clip["transition_ms"],
                    "text_payload": clip["text_payload"], "speed": clip["speed"],
                }
                for track in detail["tracks"]
                for clip in track["clips"]
            ]
        },
    )
    assert saved.status_code == 200


async def _set_shot_dialogue(shot_id: str, dialogue: str) -> None:
    async with db.get_db() as conn:
        await conn.execute(
            "UPDATE studio_shots SET dialogue = ? WHERE id = ?", (dialogue, shot_id)
        )
        await conn.commit()
