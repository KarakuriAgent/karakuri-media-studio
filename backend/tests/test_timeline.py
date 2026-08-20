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
    # 切り出し -> 解像度・SAR・fps の正規化
    assert "trim=start=0.000:end=2.000" in graph
    assert "trim=start=0.500:end=1.500" in graph
    assert graph.count("scale=1280:720:force_original_aspect_ratio=decrease") == 2
    assert graph.count("pad=1280:720:(ow-iw)/2:(oh-ih)/2") == 2
    assert graph.count("setsar=1,fps=24") == 2
    # 音声も同じ規格へ揃えてから連結
    assert graph.count("atrim=") == 2
    assert graph.count(f"aresample={timeline_export.AUDIO_RATE}") == 2
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
