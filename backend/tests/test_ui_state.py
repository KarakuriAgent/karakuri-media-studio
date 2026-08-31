"""画面のリアルタイム化（issue #45 Phase 4）。

見るのは 3 つ:

- **生成フォームの下書き**（``/api/ui/generate-form`` と
  ``/api/v1/ui/generate-form``）: 保存・部分更新・``revision`` による衝突判定と、
  下書きからのジョブ投入（``from_form``）。
- **画面移動**（``POST /api/v1/ui/navigate``）: 実在と噛み合わせの検証。
- **スタジオの更新通知**: 書き込み系の API が WS へ ``type: "studio"`` を流すか。

ComfyUI にも Grok にも繋がない（env は ``test_external`` のものを使い回す）。
"""

import asyncio

import pytest

from app import db, ui_state, ws
from app.models import UiFormState

from tests.test_external import KEY, call, enable, env, make_project  # noqa: F401


@pytest.fixture
def published(monkeypatch):
    """WS へ流れたフレームを覚えておく（ブラウザの代わり）。"""
    frames: list[dict] = []

    async def capture(payload: dict) -> None:
        frames.append(payload)

    monkeypatch.setattr(ws.hub, "broadcast", capture)
    return frames


def form_frames(frames: list[dict]) -> list[dict]:
    return [frame for frame in frames if frame.get("type") == "form"]


def studio_frames(frames: list[dict]) -> list[dict]:
    return [frame for frame in frames if frame.get("type") == "studio"]


# --------------------------------------------------------------------------
# 下書きの読み書き（内部 API）
# --------------------------------------------------------------------------

def test_the_draft_starts_empty(env):
    response = env.client.get("/api/ui/generate-form")
    assert response.status_code == 200
    assert response.json() == UiFormState().model_dump()


def test_saving_the_draft_bumps_the_revision_and_tells_the_browsers(env, published):
    first = env.client.put(
        "/api/ui/generate-form",
        json={"values": {"mode": "image_only", "imagePrompt": "a bowl of ramen"}},
    )
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 1
    assert first.json()["updated_by"] == "ui"

    second = env.client.put(
        "/api/ui/generate-form",
        json={"values": {"mode": "full"}, "base_revision": 1},
    )
    assert second.json()["revision"] == 2
    # 丸ごと置き換えなので、前の項目は残らない
    assert second.json()["values"] == {"mode": "full"}
    assert env.client.get("/api/ui/generate-form").json()["revision"] == 2

    frames = form_frames(published)
    assert [frame["revision"] for frame in frames] == [1, 2]
    assert frames[-1]["values"] == {"mode": "full"}
    assert frames[-1]["updated_by"] == "ui"


def test_a_stale_base_revision_is_refused_with_the_current_values(env):
    env.client.put("/api/ui/generate-form", json={"values": {"mode": "full"}})
    stale = env.client.put(
        "/api/ui/generate-form",
        json={"values": {"mode": "i2v"}, "base_revision": 0},
    )
    assert stale.status_code == 409, stale.text
    current = stale.json()["detail"]["current"]
    assert current["revision"] == 1
    assert current["values"] == {"mode": "full"}
    # 弾かれた側は 1 行も書いていない
    assert env.client.get("/api/ui/generate-form").json()["values"] == {"mode": "full"}


def test_a_base_revision_from_the_future_is_a_bad_request(env):
    response = env.client.put(
        "/api/ui/generate-form", json={"values": {}, "base_revision": 7}
    )
    assert response.status_code == 400
    assert "base_revision" in response.text


def test_a_draft_that_does_not_fit_in_a_form_is_refused(env):
    response = env.client.put(
        "/api/ui/generate-form", json={"values": {"imagePrompt": "あ" * 40000}}
    )
    assert response.status_code == 400
    assert env.client.get("/api/ui/generate-form").json()["revision"] == 0


# --------------------------------------------------------------------------
# 下書きの部分更新（外部 API）
# --------------------------------------------------------------------------

def test_the_external_patch_merges_only_the_keys_it_sends(env, published):
    enable(env)
    env.client.put(
        "/api/ui/generate-form",
        json={"values": {"mode": "full", "imagePrompt": "ramen", "duration": 10}},
    )
    response = call(
        env,
        "PATCH",
        "/api/v1/ui/generate-form",
        json={"values": {"duration": 5}, "base_revision": 1},
    )
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["revision"] == 2
    assert state["updated_by"] == "external"
    assert state["values"] == {"mode": "full", "imagePrompt": "ramen", "duration": 5}
    assert form_frames(published)[-1]["updated_by"] == "external"


def test_the_external_patch_without_a_base_revision_overwrites(env):
    enable(env)
    env.client.put("/api/ui/generate-form", json={"values": {"mode": "full"}})
    response = call(
        env, "PATCH", "/api/v1/ui/generate-form", json={"values": {"mode": "i2v"}}
    )
    assert response.status_code == 200, response.text
    assert response.json()["values"] == {"mode": "i2v"}


def test_the_external_patch_takes_a_base_revision(env):
    enable(env)
    env.client.put("/api/ui/generate-form", json={"values": {"mode": "full"}})
    stale = call(
        env,
        "PATCH",
        "/api/v1/ui/generate-form",
        json={"values": {"mode": "i2v"}, "base_revision": 0},
    )
    assert stale.status_code == 409
    future = call(
        env,
        "PATCH",
        "/api/v1/ui/generate-form",
        json={"values": {"mode": "i2v"}, "base_revision": 99},
    )
    assert future.status_code == 400


def test_reading_the_draft_needs_the_api_key(env):
    """外部 API はキーが無ければ機能ごと 404（内部 API はそのまま読める）。"""
    assert env.client.get("/api/v1/ui/generate-form").status_code == 404
    enable(env)
    assert (
        env.client.get("/api/v1/ui/generate-form", headers={"X-API-Key": KEY}).status_code
        == 200
    )


# --------------------------------------------------------------------------
# 下書きからのジョブ投入（from_form）
# --------------------------------------------------------------------------

def test_a_job_can_be_started_from_the_saved_draft(env):
    enable(env)
    env.client.put(
        "/api/ui/generate-form",
        json={
            "values": {
                "mode": "image_only",
                "imagePrompt": "a bowl of ramen",
                "megapixels": 1.0,
                "seedLocked": True,
                "seed": 4242,
                # 走らないステージの項目は写像で落ちる
                "videoPrompt": "the camera pans",
                "duration": 8,
            }
        },
    )
    response = call(env, "POST", "/api/v1/jobs", json={"from_form": True})
    assert response.status_code == 201, response.text
    params = call(env, "GET", f"/api/v1/jobs/{response.json()['id']}").json()["params"]
    assert params["mode"] == "image_only"
    assert params["image_prompt"] == "a bowl of ramen"
    assert params["megapixels"] == 1.0
    assert params["seed"] == 4242
    assert params["video_prompt"] == ""


def test_the_keys_sent_with_from_form_win(env):
    enable(env)
    env.client.put(
        "/api/ui/generate-form",
        json={"values": {"mode": "image_only", "imagePrompt": "a bowl of ramen"}},
    )
    response = call(
        env,
        "POST",
        "/api/v1/jobs",
        json={"from_form": True, "image_prompt": "a plate of gyoza"},
    )
    assert response.status_code == 201, response.text
    params = call(env, "GET", f"/api/v1/jobs/{response.json()['id']}").json()["params"]
    assert params["image_prompt"] == "a plate of gyoza"


def test_a_draft_that_cannot_be_mapped_is_a_bad_request(env):
    enable(env)
    env.client.put(
        "/api/ui/generate-form",
        json={"values": {"mode": "image_only", "imageWorkflow": "nope"}},
    )
    response = call(env, "POST", "/api/v1/jobs", json={"from_form": True})
    assert response.status_code == 400
    assert "nope" in response.text


def test_without_from_form_the_body_is_the_job(env):
    """``from_form`` を立てなければ今までどおり（下書きは読まれない）。"""
    enable(env)
    env.client.put(
        "/api/ui/generate-form",
        json={"values": {"mode": "image_only", "imagePrompt": "a bowl of ramen"}},
    )
    response = call(
        env, "POST", "/api/v1/jobs",
        json={"mode": "image_only", "image_prompt": "a plate of gyoza"},
    )
    assert response.status_code == 201, response.text
    params = call(env, "GET", f"/api/v1/jobs/{response.json()['id']}").json()["params"]
    assert params["image_prompt"] == "a plate of gyoza"


# --------------------------------------------------------------------------
# 画面移動
# --------------------------------------------------------------------------

def test_navigate_broadcasts_the_view(env, published):
    enable(env)
    response = call(env, "POST", "/api/v1/ui/navigate", json={"view": "settings"})
    assert response.status_code == 204, response.text
    assert published[-1] == {
        "type": "ui",
        "op": "navigate",
        "view": "settings",
        "project_id": None,
        "shot_id": None,
    }


def test_navigate_can_point_at_one_shot(env, published):
    enable(env)
    project = make_project(env)
    shot = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()
    response = call(
        env,
        "POST",
        "/api/v1/ui/navigate",
        json={"view": "studio", "project_id": project["id"], "shot_id": shot["id"]},
    )
    assert response.status_code == 204, response.text
    frame = published[-1]
    assert frame["view"] == "studio"
    assert frame["project_id"] == project["id"]
    assert frame["shot_id"] == shot["id"]


def test_navigate_checks_what_it_points_at(env):
    enable(env)
    project = make_project(env)
    missing_project = call(
        env, "POST", "/api/v1/ui/navigate",
        json={"view": "studio", "project_id": "nope"},
    )
    assert missing_project.status_code == 404
    missing_shot = call(
        env,
        "POST",
        "/api/v1/ui/navigate",
        json={"view": "studio", "project_id": project["id"], "shot_id": "nope"},
    )
    assert missing_shot.status_code == 404
    # 生成タブに作品は無い / カットだけ渡しても行き先が決まらない
    assert (
        call(
            env, "POST", "/api/v1/ui/navigate",
            json={"view": "main", "project_id": project["id"]},
        ).status_code
        == 400
    )
    assert (
        call(
            env, "POST", "/api/v1/ui/navigate",
            json={"view": "studio", "shot_id": "whatever"},
        ).status_code
        == 400
    )


def test_a_shot_from_another_project_is_refused(env):
    enable(env)
    first = make_project(env)
    second = make_project(env, name="別の作品", code="ZZ")
    shot = call(
        env,
        "POST",
        f"/api/v1/projects/{second['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()
    response = call(
        env,
        "POST",
        "/api/v1/ui/navigate",
        json={"view": "studio", "project_id": first["id"], "shot_id": shot["id"]},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# スタジオの更新通知
# --------------------------------------------------------------------------

def test_studio_writes_reach_the_browsers(env, published):
    """作る・直す・消すのそれぞれで、その作品の更新が流れる。"""
    enable(env)
    project = make_project(env)
    created = studio_frames(published)[-1]
    assert created["project_id"] == project["id"]
    assert created["entity"] == "project"
    assert created["op"] == "create"

    shot = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()
    added = studio_frames(published)[-1]
    assert (added["entity"], added["id"], added["op"]) == ("shot", shot["id"], "create")

    call(env, "PATCH", f"/api/v1/shots/{shot['id']}", json={"title": "決裂"})
    updated = studio_frames(published)[-1]
    assert (updated["entity"], updated["id"], updated["op"]) == (
        "shot",
        shot["id"],
        "update",
    )

    call(env, "DELETE", f"/api/v1/shots/{shot['id']}")
    removed = studio_frames(published)[-1]
    assert (removed["entity"], removed["id"], removed["op"]) == (
        "shot",
        shot["id"],
        "delete",
    )


def test_a_timeline_change_reaches_the_browsers(env, published):
    enable(env)
    project = make_project(env)
    timeline = call(
        env, "POST", f"/api/v1/projects/{project['id']}/timelines", json={"name": "第1話"}
    )
    assert timeline.status_code == 201, timeline.text
    frame = studio_frames(published)[-1]
    assert frame["entity"] == "timeline"
    assert frame["project_id"] == project["id"]
    assert frame["op"] == "create"


def test_the_deleted_project_is_announced_too(env, published):
    enable(env)
    project = make_project(env)
    assert (
        env.client.delete(f"/api/studio/projects/{project['id']}").status_code == 204
    )
    frame = studio_frames(published)[-1]
    assert (frame["entity"], frame["op"]) == ("project", "delete")
    assert frame["project_id"] == project["id"]


# --------------------------------------------------------------------------
# 下書きの書き込みの直列化
# --------------------------------------------------------------------------

async def test_two_writers_do_not_lose_each_other(tmp_path, monkeypatch):
    """交錯した部分更新でも、間に入った書き込みが消えない。

    読み → マージ → 書きを 1 つのトランザクションで括っているので、待たされた
    側は**待っているあいだに入った値**の上に自分のキーを重ねる（読みを外へ出す
    と、待っている間に入った保存を黙って巻き戻してしまう）。
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ui.db")
    await db.init_db()

    await ui_state.put({"mode": "full"}, updated_by="ui")
    # 5 人が同時に別々のキーを足す（base_revision なし = 現在値へ重ねる）
    await asyncio.gather(
        *(
            ui_state.patch({f"key{index}": index}, updated_by="external")
            for index in range(5)
        )
    )

    state = await ui_state.get()
    assert state.revision == 6
    assert state.values == {
        "mode": "full",
        **{f"key{index}": index for index in range(5)},
    }


# --------------------------------------------------------------------------
# 下書き -> ジョブの写像（フロントの写像と揃っているか）
# --------------------------------------------------------------------------
#
# 正本は ``frontend/src/App.tsx`` の ``submit()``（音声は
# ``frontend/src/form.ts`` の ``audioJobPayload()``）。全項目の突き合わせでは
# なく、食い違うと 422 / 黙って無視のどちらかになる代表ケースだけを見る。

def test_the_draft_maps_the_way_the_form_submits():
    # 画像編集ワークフロー（`requires: image`）は写真そのものを受け取る
    # = フロントの `imageWorkflowNeedsSource()` が true になるケース
    edit = ui_state.job_fields(
        {
            "mode": "image_only",
            "imageWorkflow": "qwen_image_edit_2511",
            "imagePrompt": "make it night",
            "sourceImage": "/assets/image/a.png",
        }
    )
    assert edit["source_image"] == "/assets/image/a.png"

    # ふつうの t2i は写真を要らない: 欄に値が残っていても送らない
    plain = ui_state.job_fields(
        {
            "mode": "image_only",
            "imageWorkflow": "krea2_turbo",
            "imagePrompt": "a bowl of ramen",
            "sourceImage": "/assets/image/a.png",
        }
    )
    assert "source_image" not in plain
    # 走らない動画ステージの項目も送らない
    assert "video_prompt" not in plain
    assert "duration" not in plain

    # full は画像ステージが開始フレームを作る（動画側が image を受け取れても、
    # フォームは source_image を送らない）
    full = ui_state.job_fields(
        {
            "mode": "full",
            "imageWorkflow": "krea2_turbo",
            "videoWorkflow": "minimax_h3_i2v",
            "imagePrompt": "a bowl of ramen",
            "videoPrompt": "the steam rises",
            "sourceImage": "/assets/image/a.png",
        }
    )
    assert "source_image" not in full

    # i2v は動画側が受け取るので送る。画像ステージは走らないので image_prompt
    # と LoRA は送らない
    i2v = ui_state.job_fields(
        {
            "mode": "i2v",
            "videoWorkflow": "minimax_h3_i2v",
            "videoPrompt": "the steam rises",
            "imagePrompt": "a bowl of ramen",
            "sourceImage": "/assets/image/a.png",
        }
    )
    assert i2v["source_image"] == "/assets/image/a.png"
    assert "image_prompt" not in i2v
    assert "loras" not in i2v
