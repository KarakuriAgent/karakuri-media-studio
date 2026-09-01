"""編集タブ: FX トラック（タイムラインに載せる演出）と演出付き書き出し。

演出そのものの見た目は Remotion 側（``remotion/src/FxOverlay.tsx``）の話なので、
ここで見るのは「保存できるか」「リビジョンで守れるか」「``fx: true`` の書き出しが
``FxOverlay`` のジョブを投入するか」まで。ffmpeg も ``npx remotion`` も走らせない。
"""

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, jobs as job_service, remotion
from app import timeline as service
from app.main import app


@pytest.fixture(autouse=True)
def isolated_outputs(monkeypatch, tmp_path_factory):
    """置き場（``outputs/`` と ``library/``）を開発機のリポジトリから切り離す。

    演出の props は素材を**配信 URL**で渡す（Remotion は http しか読めない）ので、
    ライブラリの置き場も差し替えないと URL に直せない。
    """
    root = tmp_path_factory.mktemp("root")
    outputs = root / "outputs"
    library = root / "library"
    outputs.mkdir()
    library.mkdir()
    monkeypatch.setattr(service, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(service, "LIBRARY_DIR", library)
    monkeypatch.setattr(job_service, "OUTPUTS_DIR", outputs)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _project(client, name="演出テスト"):
    return client.post("/api/studio/projects", json={"name": name}).json()["id"]


def _timeline(client, project_id):
    return client.post(
        f"/api/studio/projects/{project_id}/timelines", json={}
    ).json()


def _revision(client, project_id) -> int:
    return client.get(f"/api/studio/projects/{project_id}").json()["revision_seq"]


LYRIC = {"type": "lyric", "t": 45.96, "until": 47.5, "text": "撃ち抜け"}
SPRITE = {"type": "sprite", "t": 10.0, "until": 16.2, "src": "logo.png", "w": 0.3}


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------

def test_a_fresh_timeline_has_no_fx(client):
    timeline = _timeline(client, _project(client))
    fx = client.get(f"/api/studio/timelines/{timeline['id']}/fx").json()
    assert fx == {
        "timeline_id": timeline["id"],
        "theme": None,
        "seed": None,
        "ambient": None,
        "backgroundColor": None,
        "events": [],
    }


def test_put_fx_takes_the_fxoverlay_props_as_they_are(client):
    """``FxOverlay`` の props をそのまま投げられる（余分な項目は捨てる）。"""
    timeline = _timeline(client, _project(client))
    response = client.put(
        f"/api/studio/timelines/{timeline['id']}/fx",
        json={
            "fps": 24,
            "width": 1280,
            "height": 720,
            "durationInSeconds": 197.0,
            "base": {"src": "http://example/final.mp4"},
            "audio": {"src": "http://example/song.wav"},
            "backgroundColor": "#000000",
            "seed": 7,
            "theme": {"palette": ["rgb(220,20,40)"]},
            "ambient": {"scanline": False},
            "events": [LYRIC, SPRITE],
        },
    )
    assert response.status_code == 200
    fx = response.json()
    assert (fx["seed"], fx["backgroundColor"]) == (7, "#000000")
    assert fx["theme"] == {"palette": ["rgb(220,20,40)"]}
    assert fx["ambient"] == {"scanline": False}
    # 並び順は送った順。id は採番され、既定では有効
    assert [item["event"] for item in fx["events"]] == [LYRIC, SPRITE]
    assert all(item["enabled"] for item in fx["events"])
    assert len({item["id"] for item in fx["events"]}) == 2
    # タイムラインが持っている規格は取り込まない
    assert "fps" not in fx and "base" not in fx

    # GET でも同じものが返る
    assert client.get(f"/api/studio/timelines/{timeline['id']}/fx").json() == fx


def test_put_fx_also_takes_the_shape_get_returns(client):
    """GET が返す ``{id, enabled, event}`` の形でも投げられる（往復できる）。"""
    timeline = _timeline(client, _project(client))
    first = client.put(
        f"/api/studio/timelines/{timeline['id']}/fx", json={"events": [LYRIC]}
    ).json()
    event_id = first["events"][0]["id"]

    again = client.put(
        f"/api/studio/timelines/{timeline['id']}/fx",
        json={"events": [{"id": event_id, "enabled": False, "event": LYRIC}]},
    ).json()
    assert again["events"] == [{"id": event_id, "enabled": False, "event": LYRIC}]


def test_fx_events_can_be_added_patched_and_deleted(client):
    timeline = _timeline(client, _project(client))
    added = client.post(
        f"/api/studio/timelines/{timeline['id']}/fx/events", json={"event": LYRIC}
    )
    assert added.status_code == 201
    event_id = added.json()["events"][0]["id"]

    # event は浅いマージ（送った項目だけ動く）
    patched = client.patch(
        f"/api/studio/timelines/{timeline['id']}/fx/events/{event_id}",
        json={"event": {"t": 46.5}, "enabled": False},
    ).json()
    assert patched["events"][0]["event"] == {**LYRIC, "t": 46.5}
    assert patched["events"][0]["enabled"] is False

    # null を送るとその項目が消える
    trimmed = client.patch(
        f"/api/studio/timelines/{timeline['id']}/fx/events/{event_id}",
        json={"event": {"until": None}},
    ).json()
    assert "until" not in trimmed["events"][0]["event"]

    emptied = client.delete(
        f"/api/studio/timelines/{timeline['id']}/fx/events/{event_id}"
    )
    assert emptied.status_code == 200
    assert emptied.json()["events"] == []


def test_fx_validation_stops_at_type_and_t(client):
    """検証は「オブジェクト・``type`` が文字列・``t`` が数値」まで。"""
    timeline = _timeline(client, _project(client))
    url = f"/api/studio/timelines/{timeline['id']}/fx"
    assert client.put(url, json={"events": [{"t": 1.0}]}).status_code == 400
    assert client.put(url, json={"events": [{"type": "lyric"}]}).status_code == 400
    assert (
        client.put(url, json={"events": [{"type": "lyric", "t": "x"}]}).status_code
        == 400
    )
    # 型の中身は見ない（正本は Remotion の zod スキーマ）
    ok = client.put(url, json={"events": [{"type": "nope", "t": 1, "??": [1]}]})
    assert ok.status_code == 200


def test_fx_on_an_unknown_timeline_is_404(client):
    assert client.get("/api/studio/timelines/NOPE/fx").status_code == 404
    assert client.put("/api/studio/timelines/NOPE/fx", json={}).status_code == 404
    timeline = _timeline(client, _project(client))
    assert (
        client.delete(
            f"/api/studio/timelines/{timeline['id']}/fx/events/NOPE"
        ).status_code
        == 404
    )


def test_deleting_a_timeline_takes_its_fx_with_it(client):
    project_id = _project(client)
    timeline = _timeline(client, project_id)
    client.put(
        f"/api/studio/timelines/{timeline['id']}/fx", json={"events": [LYRIC]}
    )
    client.delete(f"/api/studio/timelines/{timeline['id']}")

    async def rows():
        from app.db import get_db

        async with get_db() as conn:
            async with conn.execute(
                "SELECT COUNT(*) AS n FROM timeline_fx_events"
            ) as cur:
                events = (await cur.fetchone())["n"]
            async with conn.execute("SELECT COUNT(*) AS n FROM timeline_fx") as cur:
                settings = (await cur.fetchone())["n"]
        return events, settings

    assert asyncio.run(rows()) == (0, 0)


# --------------------------------------------------------------------------
# リビジョン（楽観ロック）
# --------------------------------------------------------------------------

def test_fx_lands_in_the_revision_snapshot(client):
    project_id = _project(client)
    timeline = _timeline(client, project_id)
    before = _revision(client, project_id)
    client.put(
        f"/api/studio/timelines/{timeline['id']}/fx", json={"events": [LYRIC]}
    )
    after = _revision(client, project_id)
    assert after > before

    snapshot = client.get(
        f"/api/studio/projects/{project_id}/revisions/{after}"
    ).json()["snapshot"]
    assert [
        json.loads(row["event"]) for row in snapshot["timeline_fx_events"]
    ] == [LYRIC]


def test_a_stale_base_revision_is_a_conflict(client):
    project_id = _project(client)
    timeline = _timeline(client, project_id)
    stale = _revision(client, project_id)
    # 誰かが先に演出を触る
    client.put(
        f"/api/studio/timelines/{timeline['id']}/fx", json={"events": [LYRIC]}
    )

    late = client.post(
        f"/api/studio/timelines/{timeline['id']}/fx/events",
        json={"event": SPRITE, "base_revision": stale},
    )
    assert late.status_code == 409
    assert "他の変更で更新されています" in late.json()["detail"]

    # 読み直した base_revision なら通る
    fresh = _revision(client, project_id)
    assert (
        client.post(
            f"/api/studio/timelines/{timeline['id']}/fx/events",
            json={"event": SPRITE, "base_revision": fresh},
        ).status_code
        == 201
    )


def test_a_base_revision_from_the_future_is_a_400(client):
    project_id = _project(client)
    timeline = _timeline(client, project_id)
    response = client.put(
        f"/api/studio/timelines/{timeline['id']}/fx",
        json={"events": [], "base_revision": _revision(client, project_id) + 5},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# 演出付き書き出し（fx: true）
# --------------------------------------------------------------------------

def _fake_remotion_project(tmp_path: Path) -> Path:
    """``npm install`` 済みの Remotion プロジェクトのふり。"""
    root = tmp_path / "remotion-project"
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "index.ts").write_text("// entry", encoding="utf-8")
    (root / "node_modules").mkdir(exist_ok=True)
    return root


def _one_clip_timeline(client, project_id, tmp_path, monkeypatch):
    """V1 に 1 クリップ・A1 に音声 1 本だけ載ったタイムライン。"""
    from app.db import get_db
    from app.ids import new_id

    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    # 音は配信 URL で渡すので、ライブラリの置き場の中に置く
    song = service.LIBRARY_DIR / "audio" / "song.wav"
    song.parent.mkdir(parents=True, exist_ok=True)
    song.write_bytes(b"x")

    async def fake_probe(path):
        return 2000, True

    monkeypatch.setattr(service, "probe_media", fake_probe)

    timeline = _timeline(client, project_id)
    track_id = timeline["tracks"][0]["id"]
    audio = client.post(
        f"/api/studio/timelines/{timeline['id']}/tracks", json={"kind": "audio"}
    ).json()
    audio_track = [t for t in audio["tracks"] if t["kind"] == "audio"][0]["id"]

    # ライブラリの行を 2 つ置いて、そこからクリップを組む。
    video_id, song_id = new_id(), new_id()

    async def seed():
        async with get_db() as conn:
            for item_id, path, kind in (
                (video_id, str(video), "video"),
                (song_id, str(song), "audio"),
            ):
                await conn.execute(
                    "INSERT INTO library (id, kind, name, path, created_at, tags)"
                    " VALUES (?, ?, '', ?, '2026-01-01T00:00:00+00:00', '[]')",
                    (item_id, kind, path),
                )
            await conn.commit()

    asyncio.run(seed())
    client.put(
        f"/api/studio/timelines/{timeline['id']}/clips",
        json={
            "clips": [
                {
                    "track_id": track_id,
                    "start_ms": 0,
                    "duration_ms": 2000,
                    "source_kind": "library",
                    "source_id": video_id,
                    "in_ms": 0,
                    "out_ms": 2000,
                },
                {
                    "track_id": audio_track,
                    "start_ms": 0,
                    "duration_ms": 2000,
                    "source_kind": "library",
                    "source_id": song_id,
                    "in_ms": 0,
                    "out_ms": 2000,
                },
            ]
        },
    )
    return timeline, str(video), str(song)


def test_fx_export_queues_a_remotion_job_with_the_timelines_events(
    client, tmp_path, monkeypatch
):
    config.update_settings({"remotion_enabled": True})
    monkeypatch.setattr(remotion, "REMOTION_BUNDLED_DIR", _fake_remotion_project(tmp_path))
    remotion.clear_cache()
    monkeypatch.setattr(service, "FX_POLL_SECONDS", 0.05)

    project_id = _project(client)
    timeline, video, song = _one_clip_timeline(
        client, project_id, tmp_path, monkeypatch
    )
    client.put(
        f"/api/studio/timelines/{timeline['id']}/fx",
        json={
            "seed": 7,
            "theme": {"palette": ["#dc1428"]},
            "events": [
                LYRIC,
                {"id": None, "enabled": False, "event": SPRITE},
            ],
        },
    )
    # 外したイベント（enabled: false）は props に載せない
    fx = client.get(f"/api/studio/timelines/{timeline['id']}/fx").json()
    off = fx["events"][1]["id"]
    client.patch(
        f"/api/studio/timelines/{timeline['id']}/fx/events/{off}",
        json={"enabled": False},
    )

    async def fake_export(spec, output, *, on_progress=None):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")
        return output

    monkeypatch.setattr(service, "run_export", fake_export)

    rendered: dict = {}

    async def fake_render(job_id, composition, props, output, *, on_progress=None):
        rendered["composition"] = composition
        rendered["props"] = props
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00\x00\x00 ftypmp42")
        return output

    async def fake_last_frame(video_path, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"png")
        return dest

    monkeypatch.setattr(job_service.remotion, "render", fake_render)
    monkeypatch.setattr(job_service, "extract_last_frame", fake_last_frame)

    response = client.post(
        f"/api/studio/timelines/{timeline['id']}/export", json={"fx": True}
    )
    assert response.status_code == 202
    export_id = response.json()["id"]

    deadline = time.time() + 20
    export: dict = {}
    while time.time() < deadline:
        export = client.get(f"/api/studio/exports/{export_id}").json()
        if export["fx_status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert export["status"] == "done"
    assert export["fx_status"] == "done", export.get("error")
    assert export["fx_job_id"]
    assert export["fx_video_url"].endswith("/video.mp4")

    # 投入した props: 下地は焼いた mp4、音は A1 の 1 本目、events は有効なものだけ
    assert rendered["composition"] == "FxOverlay"
    props = rendered["props"]
    assert props["events"] == [LYRIC]
    # 素材は http の配信 URL で渡す（Remotion は file:// を読めない）。
    # 宛先は待受のポートから組み立てる（TestClient の scope では 80）。
    assert props["base"]["src"] == (
        f"http://127.0.0.1:80/outputs/exports/{export_id}/final.mp4"
    )
    assert props["base"]["muted"] is True
    assert props["audio"]["src"] == "http://127.0.0.1:80/library/audio/song.wav"
    assert (props["fps"], props["width"], props["height"]) == (24.0, 1280, 720)
    assert props["durationInSeconds"] == 2.0
    assert props["seed"] == 7 and props["theme"] == {"palette": ["#dc1428"]}


def test_fx_export_is_refused_while_remotion_is_off(client, tmp_path, monkeypatch):
    config.update_settings({"remotion_enabled": False})
    project_id = _project(client)
    timeline, _video, _song = _one_clip_timeline(
        client, project_id, tmp_path, monkeypatch
    )
    response = client.post(
        f"/api/studio/timelines/{timeline['id']}/export", json={"fx": True}
    )
    assert response.status_code == 400
    assert "Remotion" in response.json()["detail"]
    # 演出なしなら今までどおり受け付ける
    monkeypatch.setattr(
        service, "run_export", lambda *a, **k: (_ for _ in ()).throw(RuntimeError)
    )
    assert (
        client.post(
            f"/api/studio/timelines/{timeline['id']}/export", json={}
        ).status_code
        == 202
    )
