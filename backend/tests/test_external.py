"""外部公開 API（``/api/v1``。docs/EXTERNAL-API.md）。

見るのは入り口の 3 つ: API キーでの出し分け、一括投入の「全部か全く無しか」、
未完了 Take が溜まったときの投入拒否。スタジオ操作そのものの中身は
``test_studio.py`` が見ているので、ここでは薄いラッパーが既存のサービスへ
つながっていることだけを確かめる（ComfyUI にも Grok にも繋がない）。
"""

import asyncio
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app import (
    comfy,
    config,
    db,
    drafting_guide,
    grok,
    h3_examples,
    jobs,
    library,
    nsfw,
    studio,
    timeline,
    workflows,
)
from app.ids import new_id
from app.models import MAX_STEPS
from app.main import app
from app.routers import assets as assets_router

KEY = "external-test-key"


async def _no_llm(text: str) -> None:
    return None


class FakeLLM:
    """Grok の差し替え（既定では使えない。日本語の auto_translate は投入しない）。"""

    def __init__(self) -> None:
        self.reply: str | None = None
        self.error: str | None = "grok CLI が見つかりません"

    async def complete(self, prompt: str) -> str:
        if self.error is not None:
            raise grok.LLMError(self.error)
        return self.reply or ""

    async def health(self):  # pragma: no cover - 呼ばれない
        raise NotImplementedError


@pytest.fixture
def env(tmp_path, monkeypatch):
    """DB・assets をテスト用ディレクトリに閉じ込めた TestClient。"""
    assets = tmp_path / "assets"
    outputs = tmp_path / "outputs"
    for kind in ("image", "video", "audio"):
        (assets / kind).mkdir(parents=True)
    outputs.mkdir()

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)
    monkeypatch.setattr(jobs, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(assets_router, "ASSETS_DIR", assets)
    monkeypatch.setattr(nsfw, "classify", _no_llm)

    async def offline(*args, **kwargs):
        raise comfy.ComfyError("ComfyUI is down")

    for name in ("get_object_info", "upload_file", "queue_prompt"):
        monkeypatch.setattr(comfy, name, offline)

    llm = FakeLLM()
    monkeypatch.setattr(grok, "get_client", lambda *a, **k: llm)

    with TestClient(app) as client:
        yield type(
            "Env",
            (),
            {
                "client": client,
                "assets": assets,
                "outputs": outputs,
                "db_path": db_path,
                "tmp": tmp_path,
            },
        )


# --------------------------------------------------------------------------
# ヘルパー
# --------------------------------------------------------------------------

def enable(env, **settings) -> None:
    """外部 API を有効にする（= 設定にキーを入れる）。"""
    body = {"external_api_key": KEY}
    body.update(settings)
    response = env.client.put("/api/settings", json=body)
    assert response.status_code == 200, response.text


def call(env, method: str, path: str, **kwargs):
    headers = {"X-API-Key": KEY}
    headers.update(kwargs.pop("headers", {}))
    return env.client.request(method, path, headers=headers, **kwargs)


def make_project(env, **overrides) -> dict:
    body = {"name": "深夜のラーメン屋", "code": "KW"}
    body.update(overrides)
    response = call(env, "POST", "/api/v1/projects", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def story_body(**overrides) -> dict:
    body = {
        "episode": {"title": "第3話 送金拒否事件", "synopsis": "金の話でもめる"},
        "scenes": [
            {
                "title": "酒場・夜",
                "time_of_day": "深夜",
                "shots": [
                    {"title": "口論の始まり", "prompt": "Two men argue."},
                    {"title": "決裂", "prompt": "One man leaves."},
                ],
            },
            {
                "title": "路地",
                "shots": [{"title": "追跡", "prompt": "He runs."}],
            },
        ],
    }
    body.update(overrides)
    return body


def add_pending_take(env, project_id: str, shot_id: str, status: str = "queued") -> str:
    """まだ走っているジョブと、その Take を直接 DB に置く（暴走ガード用）。

    ComfyUI に繋がないテストでは投入したジョブがすぐ失敗してしまい、「未完了の
    Take が溜まっている」状態を実際の生成では作れないため。
    """
    job_id = new_id()
    take_id = new_id()
    conn = sqlite3.connect(env.db_path)
    with conn:
        conn.execute(
            "INSERT INTO jobs (id, created_at, mode, status, params, workflow_json)"
            " VALUES (?, '2026-01-01T00:00:00+00:00', 'i2v', ?, '{}', '{}')",
            (job_id, status),
        )
        conn.execute(
            "INSERT INTO studio_takes"
            " (id, shot_id, project_id, job_id, status, created_at)"
            " VALUES (?, ?, ?, ?, 'rendering', '2026-01-01T00:00:00+00:00')",
            (take_id, shot_id, project_id, job_id),
        )
    conn.close()
    return take_id


# --------------------------------------------------------------------------
# 認証（docs/EXTERNAL-API.md §3）
# --------------------------------------------------------------------------

def test_the_external_api_does_not_exist_until_a_key_is_set(env):
    assert env.client.get("/api/v1/projects").status_code == 404
    # キーを送っても、設定されていないうちは「機能ごと無い」ふるまい
    assert call(env, "GET", "/api/v1/projects").status_code == 404
    assert env.client.post("/api/v1/stories", json=story_body()).status_code == 404


def test_a_missing_or_wrong_key_is_rejected(env):
    enable(env)
    assert env.client.get("/api/v1/projects").status_code == 401
    response = env.client.get("/api/v1/projects", headers={"X-API-Key": "nope"})
    assert response.status_code == 401
    # 前方一致で通ってしまわない（比較は定数時間の全一致）
    short = env.client.get("/api/v1/projects", headers={"X-API-Key": KEY[:-1]})
    assert short.status_code == 401


def test_a_non_ascii_key_is_rejected_not_crashed(env):
    """設定に全角が混じっていても 401（``compare_digest`` は str だと TypeError）。"""
    enable(env, external_api_key="ひみつのかぎ")
    response = env.client.get("/api/v1/projects", headers={"X-API-Key": "nope"})
    assert response.status_code == 401


def test_the_right_key_gets_through(env):
    enable(env)
    response = call(env, "GET", "/api/v1/projects")
    assert response.status_code == 200
    assert response.json() == []


def test_clearing_the_key_disables_the_api_again(env):
    enable(env)
    assert call(env, "GET", "/api/v1/projects").status_code == 200
    enable(env, external_api_key="")
    assert call(env, "GET", "/api/v1/projects").status_code == 404


# --------------------------------------------------------------------------
# 個別のエンドポイント（既存サービスへの薄いラッパー）
# --------------------------------------------------------------------------

def test_a_project_can_be_created_and_read_back(env):
    enable(env)
    project = make_project(env)
    assert project["name"] == "深夜のラーメン屋"

    detail = call(env, "GET", f"/api/v1/projects/{project['id']}")
    assert detail.status_code == 200
    assert detail.json()["code"] == "KW"

    # 内部 API から見ても同じ 1 件（別の入口から同じデータを触っている）
    assert len(env.client.get("/api/studio/projects").json()) == 1

    patched = call(
        env, "PATCH", f"/api/v1/projects/{project['id']}", json={"synopsis": "夜の話"}
    )
    assert patched.status_code == 200
    assert patched.json()["synopsis"] == "夜の話"


def test_the_image_render_settings_go_through_the_external_api(env):
    """素材画像の 3 項目も外部 API から作成・更新できる（動画側とは別）。"""
    enable(env)
    created = call(
        env,
        "POST",
        "/api/v1/projects",
        json={
            "name": "素材の設定つき",
            "code": "IMG",
            "megapixels": 0.4,
            "aspect_ratio": "16:9 (Widescreen)",
            "steps": 4,
            "image_quality": "opt",
            "image_megapixels": 1.0,
            "image_aspect_ratio": "1:1 (Square)",
            "image_steps": 8,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["megapixels"] == 0.4
    assert body["image_megapixels"] == 1.0
    assert body["image_aspect_ratio"] == "1:1 (Square)"
    assert body["image_steps"] == 8

    # null を明示すると既定へ戻る（動画側の設定は触っていないので残る）
    cleared = call(
        env,
        "PATCH",
        f"/api/v1/projects/{body['id']}",
        json={"image_megapixels": None, "image_aspect_ratio": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["image_megapixels"] is None
    assert cleared.json()["image_aspect_ratio"] is None
    assert cleared.json()["image_steps"] == 8
    assert cleared.json()["megapixels"] == 0.4
    assert cleared.json()["aspect_ratio"] == "16:9 (Widescreen)"

    # 上限を超えた image_steps は 400（動画側の steps と同じ MAX_STEPS）
    refused = call(
        env,
        "PATCH",
        f"/api/v1/projects/{body['id']}",
        json={"image_steps": MAX_STEPS + 1},
    )
    assert refused.status_code == 400, refused.text


def test_a_duplicate_project_code_is_refused(env):
    enable(env)
    make_project(env)
    response = call(
        env, "POST", "/api/v1/projects", json={"name": "別作品", "code": "KW"}
    )
    # 内部 API と同じ移し方（作品コードの重複は StudioError -> 400）
    assert response.status_code == 400
    assert "KW" in response.json()["detail"]


def test_shots_can_be_created_updated_and_deleted(env):
    enable(env)
    project = make_project(env)
    response = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/shots",
        json={"title": "登場", "prompt": "A cat walks in.", "duration_seconds": 5},
    )
    assert response.status_code == 201, response.text
    shot = response.json()

    patched = call(env, "PATCH", f"/api/v1/shots/{shot['id']}", json={"title": "退場"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "退場"

    assert call(env, "DELETE", f"/api/v1/shots/{shot['id']}").status_code == 204
    assert call(env, "DELETE", f"/api/v1/shots/{shot['id']}").status_code == 404


def test_shot_creation_needs_an_existing_project(env):
    enable(env)
    response = call(env, "POST", "/api/v1/projects/nope/shots", json={"prompt": "x"})
    assert response.status_code == 404


def test_an_asset_can_be_registered_as_json(env):
    enable(env)
    project = make_project(env)
    response = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/assets",
        json={"name": "タカシ", "category": "character", "caption": "常連客"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "タカシ"

    # 名前は作品内で一意（既存の検証がそのまま効く）
    again = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/assets",
        json={"name": "タカシ"},
    )
    assert again.status_code == 400


def test_an_asset_can_be_uploaded_as_multipart(env):
    enable(env)
    project = make_project(env)
    response = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/assets",
        files={"file": ("ramen.png", b"not-really-a-png", "image/png")},
        data={"category": "environment"},
    )
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset["name"] == "ramen"
    assert asset["path"]


def test_registering_from_an_unknown_job_is_a_404(env):
    enable(env)
    project = make_project(env)
    response = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/assets/from-job",
        json={"name": "看板", "job_id": "nope"},
    )
    assert response.status_code == 404


def test_a_shot_can_be_translated(env):
    enable(env)
    project = make_project(env)
    shot = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()
    response = call(env, "POST", f"/api/v1/shots/{shot['id']}/translate")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["english_prompt"]
    assert body["english_source"] == body["english_prompt"]
    assert body["prompt"] == "A cat walks in."


def test_a_take_job_can_be_read(env):
    enable(env)
    project = make_project(env)
    shot = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()
    take = call(env, "POST", f"/api/v1/shots/{shot['id']}/render")
    assert take.status_code == 201, take.text

    job = call(env, "GET", f"/api/v1/jobs/{take.json()['job_id']}")
    assert job.status_code == 200
    assert job.json()["id"] == take.json()["job_id"]
    assert call(env, "GET", "/api/v1/jobs/nope").status_code == 404


def test_the_render_body_overrides_that_take_only(env):
    """内部 API と同じ任意の上書き（送らなければ今までどおり）。"""
    enable(env)
    project = make_project(env, steps=12)
    shot = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in.", "duration_seconds": 5},
    ).json()

    take = call(
        env,
        "POST",
        f"/api/v1/shots/{shot['id']}/render",
        json={"steps": 30, "duration": 8, "seed": 4242},
    )
    assert take.status_code == 201, take.text
    params = call(env, "GET", f"/api/v1/jobs/{take.json()['job_id']}").json()["params"]
    assert params["steps"] == 30
    assert params["duration"] == 8
    assert params["seed"] == 4242
    # カットもプロジェクトも据え置き
    assert call(env, "GET", f"/api/v1/projects/{project['id']}").json()["steps"] == 12

    refused = call(
        env, "POST", f"/api/v1/shots/{shot['id']}/render", json={"duration": 99}
    )
    assert refused.status_code == 400, refused.text


def test_takes_can_be_listed_and_selected(env):
    enable(env)
    project = make_project(env)
    shot = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()
    take = call(env, "POST", f"/api/v1/shots/{shot['id']}/render").json()

    listed = call(env, "GET", f"/api/v1/shots/{shot['id']}/takes")
    assert [item["id"] for item in listed.json()] == [take["id"]]

    assert call(env, "POST", f"/api/v1/takes/{take['id']}/select").status_code == 200
    assert call(env, "POST", f"/api/v1/takes/{take['id']}/reject").status_code == 200
    assert call(env, "POST", "/api/v1/takes/nope/select").status_code == 404


# --------------------------------------------------------------------------
# 一括投入（docs/EXTERNAL-API.md §2）
# --------------------------------------------------------------------------

def test_a_story_creates_the_episode_scenes_and_shots(env):
    enable(env)
    project = make_project(env)
    response = call(
        env, "POST", "/api/v1/stories", json=story_body(project_id=project["id"])
    )
    assert response.status_code == 201, response.text
    result = response.json()

    assert result["project_id"] == project["id"]
    assert result["episode_id"]
    assert [len(scene["shots"]) for scene in result["scenes"]] == [2, 1]
    assert len(result["shot_ids"]) == 3
    assert result["take_ids"] == []  # render: false なので投入はしない

    detail = env.client.get(f"/api/studio/projects/{project['id']}").json()
    assert [episode["title"] for episode in detail["episodes"]] == ["第3話 送金拒否事件"]
    assert [scene["title"] for scene in detail["scenes"]] == ["酒場・夜", "路地"]
    assert [shot["title"] for shot in detail["shots"]] == ["口論の始まり", "決裂", "追跡"]
    # Shot は入れ子の位置どおりの場に入る
    scene_of = {scene["title"]: scene["id"] for scene in detail["scenes"]}
    by_title = {shot["title"]: shot for shot in detail["shots"]}
    assert by_title["決裂"]["scene_id"] == scene_of["酒場・夜"]
    assert by_title["追跡"]["scene_id"] == scene_of["路地"]


def test_a_story_can_target_the_project_by_code(env):
    enable(env)
    project = make_project(env, code="KW")
    response = call(
        env, "POST", "/api/v1/stories", json=story_body(project_code="KW")
    )
    assert response.status_code == 201, response.text
    assert response.json()["project_id"] == project["id"]


def test_a_story_needs_a_project_that_exists(env):
    enable(env)
    make_project(env)
    for body in (story_body(), story_body(project_id="nope"),
                 story_body(project_code="ZZ")):
        response = call(env, "POST", "/api/v1/stories", json=body)
        assert response.status_code == 400, response.text


def test_a_story_that_fails_halfway_leaves_nothing_behind(env, monkeypatch):
    """途中で落ちた一括投入は話も場も Shot も残さない（全ロールバック）。"""
    enable(env)
    project = make_project(env)
    real_insert = studio._insert_shot
    calls = {"n": 0}

    async def failing_insert(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:  # 3 カット目で落とす
            raise studio.StudioError("shot が壊れています")
        return await real_insert(*args, **kwargs)

    monkeypatch.setattr(studio, "_insert_shot", failing_insert)

    response = call(
        env, "POST", "/api/v1/stories", json=story_body(project_id=project["id"])
    )
    assert response.status_code == 400
    assert "shot が壊れています" in response.json()["detail"]

    detail = env.client.get(f"/api/studio/projects/{project['id']}").json()
    assert detail["episodes"] == []
    assert detail["scenes"] == []
    assert detail["shots"] == []
    # 中途半端なリビジョンも残さない（残るのはプロジェクト作成の 1 件だけ）
    revisions = env.client.get(
        f"/api/studio/projects/{project['id']}/revisions"
    ).json()
    assert len(revisions) == 1


def test_a_story_with_render_submits_every_shot(env):
    enable(env)
    project = make_project(env)
    response = call(
        env,
        "POST",
        "/api/v1/stories",
        json=story_body(project_id=project["id"], render=True),
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert len(result["take_ids"]) == 3
    for scene in result["scenes"]:
        for shot in scene["shots"]:
            assert shot["take_id"] and shot["job_id"]
            assert shot["error"] == ""


def test_a_story_keeps_the_script_when_a_render_fails(env, monkeypatch):
    """投入に失敗しても作成済みの脚本は残す（生成だけ per-shot の失敗にする）。"""
    enable(env)
    project = make_project(env)

    async def refuse(shot_id: str):
        raise studio.StudioError("GPU がいません")

    monkeypatch.setattr(studio, "render_shot", refuse)
    response = call(
        env,
        "POST",
        "/api/v1/stories",
        json=story_body(project_id=project["id"], render=True),
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["take_ids"] == []
    assert all(
        shot["error"] == "GPU がいません"
        for scene in result["scenes"]
        for shot in scene["shots"]
    )
    detail = env.client.get(f"/api/studio/projects/{project['id']}").json()
    assert len(detail["shots"]) == 3


@pytest.mark.parametrize(
    "error",
    [jobs.JobValidationError("開始フレームが読めません"), OSError("disk full")],
)
def test_a_story_reports_a_render_that_blows_up(env, monkeypatch, error):
    """``StudioError`` 以外（引き継ぎフレームの複製など）でも 500 にしない。"""
    enable(env)
    project = make_project(env)

    async def explode(shot_id: str):
        raise error

    monkeypatch.setattr(studio, "render_shot", explode)
    response = call(
        env,
        "POST",
        "/api/v1/stories",
        json=story_body(project_id=project["id"], render=True),
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["take_ids"] == []
    assert all(
        str(error) in shot["error"]
        for scene in result["scenes"]
        for shot in scene["shots"]
    )


# --------------------------------------------------------------------------
# 暴走ガード（docs/EXTERNAL-API.md §3）
# --------------------------------------------------------------------------

def test_rendering_is_refused_while_too_many_takes_are_pending(env):
    enable(env, external_max_pending_takes=2)
    project = make_project(env)
    shot = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()

    add_pending_take(env, project["id"], shot["id"], status="queued")
    assert call(env, "POST", f"/api/v1/shots/{shot['id']}/render").status_code == 201

    add_pending_take(env, project["id"], shot["id"], status="running")
    response = call(env, "POST", f"/api/v1/shots/{shot['id']}/render")
    assert response.status_code == 429
    assert "上限" in response.json()["detail"]

    # 内部 API（UI からの操作）には掛からない
    assert env.client.post(f"/api/studio/shots/{shot['id']}/render").status_code == 201


def test_a_zero_limit_means_no_limit(env):
    enable(env, external_max_pending_takes=0)
    project = make_project(env)
    shot = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()
    for _ in range(3):
        add_pending_take(env, project["id"], shot["id"])
    assert call(env, "POST", f"/api/v1/shots/{shot['id']}/render").status_code == 201


def test_a_finished_take_does_not_count_against_the_limit(env):
    enable(env, external_max_pending_takes=1)
    project = make_project(env)
    shot = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()
    add_pending_take(env, project["id"], shot["id"], status="done")
    add_pending_take(env, project["id"], shot["id"], status="failed")
    assert call(env, "POST", f"/api/v1/shots/{shot['id']}/render").status_code == 201


def test_a_story_with_render_is_refused_when_the_queue_is_full(env):
    enable(env, external_max_pending_takes=1)
    project = make_project(env)
    shot = call(
        env,
        "POST",
        f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()
    add_pending_take(env, project["id"], shot["id"])

    response = call(
        env,
        "POST",
        "/api/v1/stories",
        json=story_body(project_id=project["id"], render=True),
    )
    assert response.status_code == 429
    # 拒まれたときは脚本も作らない（投入前に見るため）
    detail = env.client.get(f"/api/studio/projects/{project['id']}").json()
    assert detail["episodes"] == []


def test_a_story_stops_submitting_once_the_limit_is_reached(env, monkeypatch):
    """上限に達したところで投入をやめ、作成済みの脚本はそのまま残す。"""
    enable(env, external_max_pending_takes=2)
    project = make_project(env)

    pending = {"n": 0}

    async def counted() -> int:
        return pending["n"]

    real_render = studio.render_shot

    async def render_and_count(shot_id: str):
        pending["n"] += 1
        return await real_render(shot_id)

    # 数えるのは未完了ジョブ（Shot の Take と汎用ジョブで同じプール）
    monkeypatch.setattr(studio.job_service, "count_pending_jobs", counted)
    monkeypatch.setattr(studio, "render_shot", render_and_count)

    response = call(
        env,
        "POST",
        "/api/v1/stories",
        json=story_body(project_id=project["id"], render=True),
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert len(result["take_ids"]) == 2
    errors = [
        shot["error"] for scene in result["scenes"] for shot in scene["shots"]
    ]
    assert errors[:2] == ["", ""]
    assert "上限" in errors[2]
    detail = env.client.get(f"/api/studio/projects/{project['id']}").json()
    assert len(detail["shots"]) == 3


def test_a_story_shares_the_pending_pool_with_plain_jobs(env, monkeypatch):
    """カットごとのガードも「未完了ジョブ」を数える（汎用ジョブと同じプール）。

    入口のガードを通れる 1 件だけ空きがある状態から始め、Shot 由来でない
    ジョブが 1 本走っているせいで 2 カット目以降が止まることを見る。
    """
    enable(env, external_max_pending_takes=2)
    project = make_project(env)

    # Shot 由来でない未完了ジョブが 1 本ある + 投入したぶんが積み上がる
    pending = {"n": 1}

    async def counted() -> int:
        return pending["n"]

    real_render = studio.render_shot

    async def render_and_count(shot_id: str):
        pending["n"] += 1
        return await real_render(shot_id)

    monkeypatch.setattr(studio.job_service, "count_pending_jobs", counted)
    monkeypatch.setattr(studio, "render_shot", render_and_count)

    response = call(
        env,
        "POST",
        "/api/v1/stories",
        json=story_body(project_id=project["id"], render=True),
    )
    assert response.status_code == 201, response.text
    result = response.json()
    # 汎用ジョブの 1 本ぶん枠が埋まっているので、投入できるのは 1 カットだけ
    assert len(result["take_ids"]) == 1
    errors = [
        shot["error"] for scene in result["scenes"] for shot in scene["shots"]
    ]
    assert errors[0] == ""
    assert all("上限" in error for error in errors[1:]), errors
    detail = env.client.get(f"/api/studio/projects/{project['id']}").json()
    assert len(detail["shots"]) == 3


def test_the_limit_is_saved_with_the_settings(env):
    enable(env, external_max_pending_takes=5)
    settings = env.client.get("/api/settings").json()
    assert settings["external_api_key"] == KEY
    assert settings["external_max_pending_takes"] == 5
    assert config.load_settings().external_max_pending_takes == 5


# --------------------------------------------------------------------------
# 脚本ドラフト作成ガイド
# --------------------------------------------------------------------------

def test_the_guide_needs_the_key_too(env):
    assert env.client.get("/api/v1/prompt-guide").status_code == 404  # キー未設定
    enable(env)
    assert env.client.get("/api/v1/prompt-guide").status_code == 401
    wrong = env.client.get("/api/v1/prompt-guide", headers={"X-API-Key": "nope"})
    assert wrong.status_code == 401


def test_the_guide_reports_the_limits_from_the_app_constants(env):
    enable(env)
    response = call(env, "GET", "/api/v1/prompt-guide")
    assert response.status_code == 200, response.text
    guide = response.json()

    assert guide["guide_version"] == drafting_guide.GUIDE_VERSION
    assert guide["markdown"].strip()
    # 数値は本体の定数がそのまま出る（ガイド側で二重管理しない）
    assert guide["limits"] == {
        "shot_duration_min_seconds": studio.SHOT_DURATION_MIN,
        "shot_duration_max_seconds": studio.SHOT_DURATION_MAX,
        "shot_duration_recommended": "4-15",
        "reference_images_max": workflows.MINIMAX_H3_REFERENCE_IMAGES,
        "reference_videos_max": workflows.MINIMAX_H3_REFERENCE_VIDEOS,
        "reference_audios_max": workflows.MINIMAX_H3_REFERENCE_AUDIOS,
    }


def test_the_guide_covers_the_field_contract_and_the_h3_rules(env):
    enable(env)
    markdown = call(env, "GET", "/api/v1/prompt-guide").json()["markdown"]
    for keyword in (
        "`prompt`",              # 唯一モデルに届く記述フィールド
        "`action`",              # 届かないメモ欄
        "@名前",                  # 素材メンション
        "(S1)",                  # 話者 ID
        "overall_soundscape",    # 音のフィールド
        "non_diegetic_music",
        "[Shot 1]",              # H3 のタイムライン
        "<Picture 1>",           # 参照タグ
        "POST /api/v1/stories",  # 投入先
    ):
        assert keyword in markdown, keyword
    # 本文の値も定数から埋める
    assert str(workflows.MINIMAX_H3_REFERENCE_IMAGES) in markdown
    assert studio.EXCLUSION_SENTENCE in markdown


def test_the_guide_lists_the_real_example_modes_and_categories(env):
    enable(env)
    markdown = call(env, "GET", "/api/v1/prompt-guide").json()["markdown"]
    # 「実例の追加取得」節は実例データから組み立てるので、実在する値が全部載る
    assert "GET /api/v1/prompt-examples" in markdown
    for mode in h3_examples.available_modes():
        assert f"`{mode}`" in markdown, mode
    for category in h3_examples.available_categories():
        assert f"`{category}`" in markdown, category
    for example in h3_examples.select_examples(tier="canonical"):
        assert example.id in markdown, example.id


# --------------------------------------------------------------------------
# プロンプト実例
# --------------------------------------------------------------------------

def test_the_examples_need_the_key_too(env):
    assert env.client.get("/api/v1/prompt-examples").status_code == 404
    enable(env)
    assert env.client.get("/api/v1/prompt-examples").status_code == 401


def test_without_a_filter_the_examples_come_back_as_an_index(env):
    enable(env)
    payload = call(env, "GET", "/api/v1/prompt-examples").json()

    assert payload["guide_version"] == drafting_guide.GUIDE_VERSION
    assert payload["modes"] == h3_examples.available_modes()
    assert payload["categories"] == h3_examples.available_categories()
    assert payload["total"] == len(h3_examples.EXAMPLES)
    # 索引なので本文は付かない（載るのは選ぶのに要る分だけ）
    assert all(item["body"] is None for item in payload["examples"])
    first = payload["examples"][0]
    assert first["id"] == "H3-E1"
    assert first["mode"] == "i2v"
    assert first["summary"]
    assert first["tier"] == "canonical"


def test_the_examples_can_be_filtered_by_mode_and_category(env):
    enable(env)
    payload = call(
        env,
        "GET",
        "/api/v1/prompt-examples?mode=r2v&category=multi-reference",
    ).json()

    assert payload["total"] >= 1
    for item in payload["examples"]:
        assert item["mode"] == "r2v"
        assert "multi-reference" in item["categories"]
        assert item["body"]  # 絞り込んだときは本文まで返る

    limited = call(
        env, "GET", "/api/v1/prompt-examples?mode=t2v&limit=1"
    ).json()
    assert limited["total"] == 1


def test_one_example_can_be_fetched_by_id(env):
    enable(env)
    payload = call(env, "GET", "/api/v1/prompt-examples?id=H3-E6").json()

    assert payload["total"] == 1
    example = payload["examples"][0]
    assert example["id"] == "H3-E6"
    assert example["mode"] == "edit"
    assert example["body"].startswith("subject_definitions:")
    assert example["source"]


def test_unknown_example_filters_are_rejected(env):
    enable(env)
    assert call(env, "GET", "/api/v1/prompt-examples?mode=x2v").status_code == 400
    assert call(env, "GET", "/api/v1/prompt-examples?category=nope").status_code == 400
    assert call(env, "GET", "/api/v1/prompt-examples?id=H3-E999").status_code == 404


def test_external_changes_are_recorded_as_external(env):
    """外部 API の変更は履歴で 'external'（UI の 'user' と区別できる）。"""
    enable(env)
    project = make_project(env)
    shot = call(
        env, "POST", f"/api/v1/projects/{project['id']}/shots",
        json={"title": "決裂", "prompt": "One man leaves."},
    ).json()
    call(
        env, "PATCH", f"/api/v1/shots/{shot['id']}", json={"prompt": "He walks out."}
    )

    rows = env.client.get(
        f"/api/studio/projects/{project['id']}/revisions"
    ).json()
    assert [row["actor"] for row in rows] == ["external", "external", "external"]
    assert rows[0]["action"] == "カット『決裂』を更新(prompt)"


def test_external_patches_take_a_base_revision(env):
    """楽観ロックは外部 API でも効く（人の変更を黙って上書きしない）。"""
    enable(env)
    project = make_project(env)
    shot = call(
        env, "POST", f"/api/v1/projects/{project['id']}/shots",
        json={"title": "決裂", "prompt": "One man leaves."},
    ).json()
    base = call(env, "GET", f"/api/v1/projects/{project['id']}").json()["revision_seq"]
    # 人が UI から先に直した
    env.client.patch(f"/api/studio/shots/{shot['id']}", json={"prompt": "人の推敲"})

    conflict = call(
        env, "PATCH", f"/api/v1/shots/{shot['id']}",
        json={"prompt": "エージェントの上書き", "base_revision": base},
    )
    assert conflict.status_code == 409, conflict.text
    assert "決裂" in conflict.json()["detail"]


# --------------------------------------------------------------------------
# リビジョン履歴（409 の読み解きと、消したものの戻し道）
# --------------------------------------------------------------------------

def test_revisions_can_be_listed_and_filtered_to_one_entity(env):
    """409 のあと、そのカットの履歴だけを引ける。"""
    enable(env)
    project = make_project(env)
    shot = call(
        env, "POST", f"/api/v1/projects/{project['id']}/shots",
        json={"title": "決裂", "prompt": "One man leaves."},
    ).json()
    call(env, "PATCH", f"/api/v1/shots/{shot['id']}", json={"prompt": "He walks out."})

    rows = call(env, "GET", f"/api/v1/projects/{project['id']}/revisions").json()
    assert [row["actor"] for row in rows] == ["external", "external", "external"]

    mine = call(
        env, "GET",
        f"/api/v1/projects/{project['id']}/revisions"
        f"?entity_kind=shot&entity_id={shot['id']}",
    ).json()
    assert [row["entity_id"] for row in mine] == [shot["id"], shot["id"]]
    assert mine[0]["action"] == "カット『決裂』を更新(prompt)"


def test_a_revision_diff_shows_what_the_other_side_changed(env):
    """人が UI から直した内容を、項目ごとの差分で読める。"""
    enable(env)
    project = make_project(env)
    shot = call(
        env, "POST", f"/api/v1/projects/{project['id']}/shots",
        json={"title": "決裂", "prompt": "One man leaves."},
    ).json()
    env.client.patch(f"/api/studio/shots/{shot['id']}", json={"prompt": "人の推敲"})

    seq = call(env, "GET", f"/api/v1/projects/{project['id']}/revisions").json()[0]
    assert seq["actor"] == "user"
    diff = call(
        env, "GET", f"/api/v1/projects/{project['id']}/revisions/{seq['seq']}/diff"
    )
    assert diff.status_code == 200, diff.text
    changed = diff.json()["changes"]
    assert [(c["entity"], c["op"]) for c in changed] == [("shot", "update")]
    fields = {f["field"]: (f["before"], f["after"]) for f in changed[0]["fields"]}
    assert fields["prompt"] == ("One man leaves.", "人の推敲")


def test_a_deleted_shot_can_be_restored_field_by_field(env):
    """消したカットを部分復元で戻せる（復元の前後も履歴に残る）。"""
    enable(env)
    project = make_project(env)
    shot = call(
        env, "POST", f"/api/v1/projects/{project['id']}/shots",
        json={"title": "決裂", "prompt": "One man leaves."},
    ).json()
    before = call(env, "GET", f"/api/v1/projects/{project['id']}/revisions").json()[0]
    assert call(env, "DELETE", f"/api/v1/shots/{shot['id']}").status_code == 204
    assert call(env, "GET", f"/api/v1/projects/{project['id']}").json()["shots"] == []

    restored = call(
        env, "POST",
        f"/api/v1/projects/{project['id']}/revisions/{before['seq']}/restore",
        json={"entity": "shot", "id": shot["id"], "fields": ["title", "prompt"]},
    )
    assert restored.status_code == 200, restored.text
    shots = restored.json()["shots"]
    assert [(s["id"], s["title"], s["prompt"]) for s in shots] == [
        (shot["id"], "決裂", "One man leaves.")
    ]
    # 復元も外部の変更として残り、書き換える前の状態も 1 件取ってある
    rows = call(env, "GET", f"/api/v1/projects/{project['id']}/revisions").json()
    assert rows[0]["actor"] == "external"
    assert studio.RESTORE_BACKUP_ACTION in [row["action"] for row in rows]


def test_the_revision_endpoints_need_the_key_and_something_that_exists(env):
    assert env.client.get("/api/v1/projects/x/revisions").status_code == 404
    enable(env)
    assert env.client.get("/api/v1/projects/x/revisions").status_code == 401
    assert env.client.post("/api/v1/projects/x/revisions/1/restore").status_code == 401
    assert call(env, "GET", "/api/v1/projects/nope/revisions").status_code == 404

    project = make_project(env)
    assert call(
        env, "GET", f"/api/v1/projects/{project['id']}/revisions/999/diff"
    ).status_code == 404
    assert call(
        env, "POST", f"/api/v1/projects/{project['id']}/revisions/999/restore"
    ).status_code == 404


# --------------------------------------------------------------------------
# レンダリング前確認（prompt-preview）
# --------------------------------------------------------------------------

def test_prompt_preview_shows_what_would_be_submitted(env):
    """投入される本文を、生成の前に読み取りだけで確かめられる。"""
    enable(env)
    project = make_project(env)
    shot = call(
        env, "POST", f"/api/v1/projects/{project['id']}/shots",
        json={"title": "口論", "prompt": "Two men argue."},
    ).json()

    response = call(env, "GET", f"/api/v1/shots/{shot['id']}/prompt-preview")
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["shot_id"] == shot["id"]
    assert "Two men argue." in preview["prompt"]
    assert preview["error"] == ""
    # 内部 API と同じ組み立てを通している
    internal = env.client.get(
        f"/api/studio/shots/{shot['id']}/prompt-preview"
    ).json()
    assert preview == internal


def test_prompt_preview_needs_the_key_and_an_existing_shot(env):
    assert env.client.get("/api/v1/shots/x/prompt-preview").status_code == 404
    enable(env)
    assert env.client.get("/api/v1/shots/x/prompt-preview").status_code == 401
    assert call(env, "GET", "/api/v1/shots/nope/prompt-preview").status_code == 404


# --------------------------------------------------------------------------
# 汎用ジョブ
# --------------------------------------------------------------------------

def test_a_job_can_be_created_and_listed(env):
    enable(env)
    response = call(
        env, "POST", "/api/v1/jobs",
        json={"mode": "image_only", "image_prompt": "a bowl of ramen"},
    )
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["mode"] == "image_only"

    listed = call(env, "GET", "/api/v1/jobs?limit=10")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [job["id"]]
    assert call(env, "GET", f"/api/v1/jobs/{job['id']}").status_code == 200


def test_a_job_with_a_broken_mode_is_422(env):
    enable(env)
    response = call(
        env, "POST", "/api/v1/jobs",
        json={"mode": "image_only", "image_prompt": "x", "image_workflow": "nope"},
    )
    assert response.status_code == 422
    assert "nope" in response.text


def test_the_pending_guard_also_covers_plain_jobs(env):
    """暴走ガードは Shot のレンダリングだけでなく汎用ジョブにも掛かる。"""
    enable(env, external_max_pending_takes=1)
    project = make_project(env)
    shot = call(
        env, "POST", f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()
    add_pending_take(env, project["id"], shot["id"])

    response = call(
        env, "POST", "/api/v1/jobs",
        json={"mode": "image_only", "image_prompt": "a bowl of ramen"},
    )
    assert response.status_code == 429
    assert "上限" in response.json()["detail"]
    # 内部 API（UI）には掛からない
    assert env.client.post(
        "/api/jobs", json={"mode": "image_only", "image_prompt": "x"}
    ).status_code == 201


def test_jobs_can_be_cancelled_rerun_and_continued(env):
    enable(env)
    job = call(
        env, "POST", "/api/v1/jobs",
        json={"mode": "image_only", "image_prompt": "a bowl of ramen"},
    ).json()

    cancelled = call(env, "POST", f"/api/v1/jobs/{job['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    # 止め方そのものは test_jobs.py が見ている。ここは同じ経路に繋がっていること
    assert cancelled.json()["id"] == job["id"]

    rerun = call(env, "POST", f"/api/v1/jobs/{job['id']}/rerun", json={"seed": 7})
    assert rerun.status_code == 201, rerun.text
    assert rerun.json()["id"] != job["id"]
    assert rerun.json()["params"]["seed"] == 7

    for path in ("cancel", "rerun", "continue"):
        assert call(env, "POST", f"/api/v1/jobs/nope/{path}").status_code == 404


def test_a_take_can_be_cancelled(env):
    enable(env)
    project = make_project(env)
    shot = call(
        env, "POST", f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()
    take_id = add_pending_take(env, project["id"], shot["id"])

    response = call(env, "POST", f"/api/v1/takes/{take_id}/cancel")
    assert response.status_code == 200, response.text
    assert call(env, "POST", "/api/v1/takes/nope/cancel").status_code == 404


# --------------------------------------------------------------------------
# 削除系（プロジェクト以外）
# --------------------------------------------------------------------------

def test_everything_but_the_project_can_be_deleted(env):
    enable(env)
    project = make_project(env)
    episode = call(
        env, "POST", f"/api/v1/projects/{project['id']}/episodes",
        json={"title": "第1話"},
    ).json()
    scene = call(
        env, "POST", f"/api/v1/episodes/{episode['id']}/scenes",
        json={"title": "酒場"},
    ).json()
    asset = call(
        env, "POST", f"/api/v1/projects/{project['id']}/assets",
        json={"name": "タカシ"},
    ).json()
    file_row = call(
        env, "POST", f"/api/v1/assets/{asset['id']}/files",
        files={"file": ("side.png", b"not-really-a-png", "image/png")},
        data={"role": "image"},
    ).json()
    shot = call(
        env, "POST", f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()
    take_id = add_pending_take(env, project["id"], shot["id"])

    assert call(env, "DELETE", f"/api/v1/takes/{take_id}").status_code == 204
    assert call(
        env, "DELETE", f"/api/v1/asset-files/{file_row['id']}"
    ).status_code == 204
    assert call(env, "DELETE", f"/api/v1/assets/{asset['id']}").status_code == 204
    assert call(env, "DELETE", f"/api/v1/scenes/{scene['id']}").status_code == 204
    assert call(env, "DELETE", f"/api/v1/episodes/{episode['id']}").status_code == 204
    # 二度目は 404（消えたことが分かる）
    for path in (
        f"/api/v1/takes/{take_id}",
        f"/api/v1/asset-files/{file_row['id']}",
        f"/api/v1/assets/{asset['id']}",
        f"/api/v1/scenes/{scene['id']}",
        f"/api/v1/episodes/{episode['id']}",
    ):
        assert call(env, "DELETE", path).status_code == 404, path


def test_the_project_delete_is_not_exposed(env):
    """作品の削除は復元できないので外部には出さない（人に頼む運用）。"""
    enable(env)
    project = make_project(env)
    assert call(env, "DELETE", f"/api/v1/projects/{project['id']}").status_code == 405
    # 内部 API（UI）からは今までどおり消せる
    assert env.client.delete(
        f"/api/studio/projects/{project['id']}"
    ).status_code == 204


# --------------------------------------------------------------------------
# 素材のファイル操作
# --------------------------------------------------------------------------

def test_an_asset_file_can_be_replaced_and_references_added(env):
    enable(env)
    project = make_project(env)
    asset = call(
        env, "POST", f"/api/v1/projects/{project['id']}/assets",
        json={"name": "タカシ"},
    ).json()
    assert asset["path"] == ""

    replaced = call(
        env, "POST", f"/api/v1/assets/{asset['id']}/file",
        files={"file": ("face.png", b"not-really-a-png", "image/png")},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["path"]
    assert replaced.json()["kind"] == "image"

    added = call(
        env, "POST", f"/api/v1/assets/{asset['id']}/files",
        files={"file": ("side.png", b"not-really-a-png", "image/png")},
        data={"role": "image", "caption": "横顔"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["caption"] == "横顔"

    files = call(env, "GET", f"/api/v1/assets/{asset['id']}/files")
    assert [row["id"] for row in files.json()] == [added.json()["id"]]


def test_asset_file_operations_report_a_missing_asset(env):
    enable(env)
    assert call(env, "GET", "/api/v1/assets/nope/files").status_code == 404
    assert call(
        env, "POST", "/api/v1/assets/nope/file",
        files={"file": ("face.png", b"x", "image/png")},
    ).status_code == 404


def test_an_unknown_reference_role_is_a_400(env):
    enable(env)
    project = make_project(env)
    asset = call(
        env, "POST", f"/api/v1/projects/{project['id']}/assets",
        json={"name": "タカシ"},
    ).json()
    response = call(
        env, "POST", f"/api/v1/assets/{asset['id']}/files",
        files={"file": ("side.png", b"x", "image/png")},
        data={"role": "nope"},
    )
    assert response.status_code == 400
    assert "nope" in response.json()["detail"]


# --------------------------------------------------------------------------
# 並べ替え
# --------------------------------------------------------------------------

def test_episodes_scenes_and_shots_can_be_reordered(env):
    enable(env)
    project = make_project(env)
    episodes = [
        call(
            env, "POST", f"/api/v1/projects/{project['id']}/episodes",
            json={"title": title},
        ).json()
        for title in ("第1話", "第2話")
    ]
    scenes = [
        call(
            env, "POST", f"/api/v1/episodes/{episodes[0]['id']}/scenes",
            json={"title": title},
        ).json()
        for title in ("酒場", "路地")
    ]
    shots = [
        call(
            env, "POST", f"/api/v1/projects/{project['id']}/shots",
            json={"prompt": prompt, "scene_id": scenes[0]["id"]},
        ).json()
        for prompt in ("A.", "B.")
    ]

    flipped = call(
        env, "POST", f"/api/v1/projects/{project['id']}/episodes/reorder",
        json={"ids": [episodes[1]["id"], episodes[0]["id"]]},
    )
    assert flipped.status_code == 200, flipped.text
    assert [row["id"] for row in flipped.json()] == [
        episodes[1]["id"], episodes[0]["id"]
    ]

    flipped = call(
        env, "POST", f"/api/v1/episodes/{episodes[0]['id']}/scenes/reorder",
        json={"ids": [scenes[1]["id"], scenes[0]["id"]]},
    )
    assert flipped.status_code == 200, flipped.text
    assert [row["id"] for row in flipped.json()] == [scenes[1]["id"], scenes[0]["id"]]

    flipped = call(
        env, "POST", f"/api/v1/projects/{project['id']}/shots/reorder",
        json={"shot_ids": [shots[1]["id"], shots[0]["id"]]},
    )
    assert flipped.status_code == 200, flipped.text
    assert [row["id"] for row in flipped.json()] == [shots[1]["id"], shots[0]["id"]]


def test_reorder_reports_a_missing_parent_and_a_partial_list(env):
    enable(env)
    project = make_project(env)
    episode = call(
        env, "POST", f"/api/v1/projects/{project['id']}/episodes",
        json={"title": "第1話"},
    ).json()
    call(
        env, "POST", f"/api/v1/projects/{project['id']}/episodes",
        json={"title": "第2話"},
    )

    assert call(
        env, "POST", "/api/v1/projects/nope/episodes/reorder", json={"ids": []}
    ).status_code == 404
    # 全件を送らないと並び順が決まらないので断る
    partial = call(
        env, "POST", f"/api/v1/projects/{project['id']}/episodes/reorder",
        json={"ids": [episode["id"]]},
    )
    assert partial.status_code == 400


# --------------------------------------------------------------------------
# ライブラリ
# --------------------------------------------------------------------------

def test_the_library_can_be_searched_but_not_deleted(env):
    enable(env)
    page = call(env, "GET", "/api/v1/library?kind=image&limit=5")
    assert page.status_code == 200, page.text
    assert page.json() == {
        "items": [], "total": 0, "limit": 5, "offset": 0, "tags": []
    }
    # 削除は公開しない（棚から消す操作は人の手で）
    assert call(env, "DELETE", "/api/v1/library/nope").status_code == 405


def test_library_writes_report_missing_material(env):
    enable(env)
    assert call(
        env, "POST", "/api/v1/library/from-job",
        json={"job_id": "nope", "source": "image"},
    ).status_code == 404
    assert call(
        env, "PATCH", "/api/v1/library/nope", json={"name": "x"}
    ).status_code == 404
    sheet = call(
        env, "POST", "/api/v1/library/sheet",
        json={"item_ids": ["nope"], "name": "シート"},
    )
    assert sheet.status_code == 400


# --------------------------------------------------------------------------
# 参照系
# --------------------------------------------------------------------------

def test_capabilities_report_the_backend_even_when_it_is_down(env):
    enable(env)
    response = call(env, "GET", "/api/v1/capabilities")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["latent_continuity"] is False
    assert payload["error"]  # ComfyUI に繋がらない理由が入る


def test_options_are_readable_without_leaking_the_backend_url(env):
    enable(env)
    response = call(env, "GET", "/api/v1/options")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["video_workflows"]
    assert payload["default_video_workflow"]
    # 接続先の所在は外に出さない
    assert payload["comfy_url"] == ""
    assert env.client.get("/api/options").json()["comfy_url"]


def test_the_openapi_subset_only_has_the_external_api(env):
    enable(env)
    response = call(env, "GET", "/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    schema = response.json()

    assert schema["paths"], "パスが 1 本も無い"
    assert all(path.startswith("/api/v1/") for path in schema["paths"]), schema["paths"]
    assert "/api/studio/projects" not in schema["paths"]
    assert "/api/v1/shots/{shot_id}/prompt-preview" in schema["paths"]

    # 参照されているスキーマは全部入っている（辿って解決できる）
    schemas = schema["components"]["schemas"]
    seen: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                assert ref.startswith("#/components/schemas/"), ref
                name = ref.rsplit("/", 1)[-1]
                assert name in schemas, name
                if name not in seen:
                    seen.append(name)
                    walk(schemas[name])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema["paths"])
    assert "StudioShotPreview" in schemas
    # 参照されていないスキーマ（内部 API 専用のもの）は載らない
    assert set(schemas) == set(seen)


def test_audio_and_generic_uploads_land_in_the_library(env, tmp_path, monkeypatch):
    """音源は ``/library/audio``、種別を書かないときは ``/library/upload``。"""
    # 実体の置き場だけはテスト用に逃がす（env は DB と assets しか閉じ込めない）
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path / "library")
    enable(env)
    audio = call(
        env, "POST", "/api/v1/library/audio",
        files={"file": ("ban.wav", b"RIFF", "audio/wav")},
        data={"name": "BAN 本チャン"},
    )
    assert audio.status_code == 201, audio.text
    assert audio.json()["kind"] == "audio"
    assert audio.json()["name"] == "BAN 本チャン"

    guessed = call(
        env, "POST", "/api/v1/library/upload",
        files={"file": ("logo.png", b"PNG", "image/png")},
    )
    assert guessed.status_code == 201, guessed.text
    assert guessed.json()["kind"] == "image"

    unknown = call(
        env, "POST", "/api/v1/library/upload",
        files={"file": ("notes.txt", b"x", "text/plain")},
    )
    assert unknown.status_code == 400
    assert "種別が分かりません" in unknown.text


def test_the_new_endpoints_need_the_key_too(env):
    for path in (
        "/api/v1/jobs",
        "/api/v1/capabilities",
        "/api/v1/options",
        "/api/v1/openapi.json",
        "/api/v1/library",
    ):
        assert env.client.get(path).status_code == 404, path
    enable(env)
    for path in (
        "/api/v1/jobs",
        "/api/v1/capabilities",
        "/api/v1/options",
        "/api/v1/openapi.json",
        "/api/v1/library",
    ):
        assert env.client.get(path).status_code == 401, path


def test_asset_patches_take_a_base_revision_too(env):
    """楽観ロックは素材の PATCH でも効く（PATCH 系で共通のモデル）。"""
    enable(env)
    project = make_project(env)
    asset = call(
        env, "POST", f"/api/v1/projects/{project['id']}/assets",
        json={"name": "タカシ", "caption": "常連客"},
    ).json()
    base = call(env, "GET", f"/api/v1/projects/{project['id']}").json()["revision_seq"]
    env.client.patch(f"/api/studio/assets/{asset['id']}", json={"caption": "人の推敲"})

    conflict = call(
        env, "PATCH", f"/api/v1/assets/{asset['id']}",
        json={"caption": "エージェントの上書き", "base_revision": base},
    )
    assert conflict.status_code == 409, conflict.text


# --------------------------------------------------------------------------
# 編集タブ（タイムライン / トラック / クリップ / 書き出し）
# --------------------------------------------------------------------------

@pytest.fixture
def timeline_env(env, tmp_path, monkeypatch):
    """書き出し先とライブラリを開発機のリポジトリから切り離した ``env``。"""
    outputs = tmp_path / "timeline-outputs"
    outputs.mkdir()
    monkeypatch.setattr(timeline, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(
        timeline.library_service, "LIBRARY_DIR", tmp_path / "library"
    )
    return env


def gap_clip(track_id: str, **overrides) -> dict:
    """ソースを持たない隙間のクリップ（黒＋無音として焼かれる）。"""
    body = {
        "track_id": track_id,
        "source_kind": "gap",
        "source_id": None,
        "start_ms": 0,
        "duration_ms": 2000,
        "in_ms": 0,
        "out_ms": 0,
    }
    body.update(overrides)
    return body


def video_track_id(detail: dict) -> str:
    return next(track["id"] for track in detail["tracks"] if track["kind"] == "video")


def held_export(monkeypatch):
    """ffmpeg を「呼ばれたまま止まる」ものに差し替える（戻り値を呼ぶと進む）。

    書き出しが走っている最中の受け答え（409 / 429）を見るため。走らせっぱなしで
    テストを終えるとイベントループの片付けとぶつかるので、必ず最後に進ませる。
    """
    released = {"go": False}

    async def waits(spec, output, *, on_progress=None):
        while not released["go"]:
            await asyncio.sleep(0.01)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")
        return output

    monkeypatch.setattr(timeline, "run_export", waits)
    return lambda: released.update(go=True)


def wait_for_export(env, export_id: str, timeout: float = 10.0) -> dict:
    """書き出しは 202 即受付なので、終端に落ちるまでポーリングする。"""
    deadline = time.time() + timeout
    export: dict = {}
    while time.time() < deadline:
        response = call(env, "GET", f"/api/v1/exports/{export_id}")
        assert response.status_code == 200, response.text
        export = response.json()
        if export["status"] in ("done", "failed"):
            return export
        time.sleep(0.05)
    raise AssertionError(f"export stuck in {export.get('status')}")


def test_a_timeline_can_be_built_edited_and_deleted(timeline_env):
    env = timeline_env
    enable(env)
    project = make_project(env)

    created = call(
        env, "POST", f"/api/v1/projects/{project['id']}/timelines",
        json={"name": "本編", "fps": 24, "width": 1280, "height": 720},
    )
    assert created.status_code == 201, created.text
    detail = created.json()
    assert detail["name"] == "本編"
    # 作りたては V1 だけの空のタイムライン
    assert [track["kind"] for track in detail["tracks"]] == ["video"]
    timeline_id = detail["id"]

    listed = call(env, "GET", f"/api/v1/projects/{project['id']}/timelines")
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [timeline_id]

    assert call(
        env, "GET", f"/api/v1/timelines/{timeline_id}"
    ).json()["id"] == timeline_id

    renamed = call(
        env, "PATCH", f"/api/v1/timelines/{timeline_id}", json={"name": "本編 v2"}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "本編 v2"

    # 音声トラックを 1 本足して、名前を変えて、消す
    with_audio = call(env, "POST", f"/api/v1/timelines/{timeline_id}/tracks", json={})
    assert with_audio.status_code == 201, with_audio.text
    audio = [t for t in with_audio.json()["tracks"] if t["kind"] == "audio"][0]
    muted = call(
        env, "PATCH", f"/api/v1/timelines/{timeline_id}/tracks/{audio['id']}",
        json={"muted": True},
    )
    assert muted.status_code == 200, muted.text
    assert [t for t in muted.json()["tracks"] if t["id"] == audio["id"]][0]["muted"]
    dropped = call(
        env, "DELETE", f"/api/v1/timelines/{timeline_id}/tracks/{audio['id']}"
    )
    assert dropped.status_code == 200, dropped.text
    assert audio["id"] not in [t["id"] for t in dropped.json()["tracks"]]
    # 映像トラックは V1 の 1 本きり
    assert call(
        env, "POST", f"/api/v1/timelines/{timeline_id}/tracks", json={"kind": "video"}
    ).status_code == 400

    # クリップの全置換（重なりは 400）
    v1 = video_track_id(dropped.json())
    replaced = call(
        env, "PUT", f"/api/v1/timelines/{timeline_id}/clips",
        json={"clips": [gap_clip(v1), gap_clip(v1, start_ms=2000, duration_ms=1000)]},
    )
    assert replaced.status_code == 200, replaced.text
    clips = [t for t in replaced.json()["tracks"] if t["id"] == v1][0]["clips"]
    assert [clip["duration_ms"] for clip in clips] == [2000, 1000]
    assert replaced.json()["duration_ms"] == 3000

    overlapped = call(
        env, "PUT", f"/api/v1/timelines/{timeline_id}/clips",
        json={"clips": [gap_clip(v1), gap_clip(v1, start_ms=1000)]},
    )
    assert overlapped.status_code == 400

    # 差し込み: 下のクリップが前後に割れ、トラックの全長は変わらない
    inserted = call(
        env, "POST", f"/api/v1/timelines/{timeline_id}/clips/insert",
        json={
            "track_id": v1,
            "start_ms": 500,
            "duration_ms": 500,
            "source_kind": "gap",
            "source_id": None,
        },
    )
    assert inserted.status_code == 200, inserted.text
    clips = [t for t in inserted.json()["tracks"] if t["id"] == v1][0]["clips"]
    assert [(c["start_ms"], c["duration_ms"]) for c in clips] == [
        (0, 500), (500, 500), (1000, 1000), (2000, 1000)
    ]
    assert inserted.json()["duration_ms"] == 3000

    # 消せる（タイムラインは同じ話からいつでも組み直せるので外部にも開ける）
    assert call(env, "DELETE", f"/api/v1/timelines/{timeline_id}").status_code == 204
    assert call(env, "GET", f"/api/v1/timelines/{timeline_id}").status_code == 404
    assert call(env, "GET", f"/api/v1/projects/{project['id']}/timelines").json() == []


def test_an_export_runs_in_the_background_and_can_be_polled(
    timeline_env, monkeypatch
):
    env = timeline_env
    enable(env)
    project = make_project(env)
    detail = call(
        env, "POST", f"/api/v1/projects/{project['id']}/timelines", json={}
    ).json()
    call(
        env, "PUT", f"/api/v1/timelines/{detail['id']}/clips",
        json={"clips": [gap_clip(video_track_id(detail))]},
    )

    seen: dict = {}

    async def fake_run(spec, output, *, on_progress=None):
        seen["spec"] = spec
        if on_progress is not None:
            await on_progress(0.5)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")
        return output

    monkeypatch.setattr(timeline, "run_export", fake_run)

    started = call(
        env, "POST", f"/api/v1/timelines/{detail['id']}/export",
        json={"preset": "720p", "loudnorm": False},
    )
    assert started.status_code == 202, started.text
    export_id = started.json()["id"]

    done = wait_for_export(env, export_id)
    assert done["status"] == "done"
    assert done["progress"] == 1.0
    assert done["error"] is None
    assert done["output_url"] == f"/outputs/exports/{export_id}/final.mp4"
    assert (seen["spec"].width, seen["spec"].height) == (1280, 720)

    # 書き出しの履歴も外部 API から読める（id を控え損ねたときの拾い先）
    listed = call(env, "GET", f"/api/v1/timelines/{detail['id']}/exports")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [export_id]
    assert call(env, "GET", "/api/v1/timelines/nope/exports").status_code == 404

    saved = call(env, "POST", f"/api/v1/exports/{export_id}/save-to-library", json={})
    assert saved.status_code == 201, saved.text
    assert saved.json()["kind"] == "video"


def test_a_second_export_of_the_same_timeline_is_a_conflict(timeline_env, monkeypatch):
    """同じタイムラインの二重書き出しは内部 API と同じく 409。"""
    env = timeline_env
    enable(env)
    project = make_project(env)
    detail = call(
        env, "POST", f"/api/v1/projects/{project['id']}/timelines", json={}
    ).json()
    call(
        env, "PUT", f"/api/v1/timelines/{detail['id']}/clips",
        json={"clips": [gap_clip(video_track_id(detail))]},
    )

    release = held_export(monkeypatch)

    first = call(env, "POST", f"/api/v1/timelines/{detail['id']}/export", json={})
    assert first.status_code == 202, first.text
    second = call(env, "POST", f"/api/v1/timelines/{detail['id']}/export", json={})
    assert second.status_code == 409, second.text

    release()
    assert wait_for_export(env, first.json()["id"])["status"] == "done"


def test_the_export_guard_counts_running_exports(timeline_env, monkeypatch):
    """走っている書き出しが上限に達していれば、別のタイムラインでも 429。"""
    env = timeline_env
    enable(env, external_max_pending_takes=1)
    project = make_project(env)
    timelines = []
    for _ in range(2):
        detail = call(
            env, "POST", f"/api/v1/projects/{project['id']}/timelines", json={}
        ).json()
        call(
            env, "PUT", f"/api/v1/timelines/{detail['id']}/clips",
            json={"clips": [gap_clip(video_track_id(detail))]},
        )
        timelines.append(detail["id"])

    release = held_export(monkeypatch)

    first = call(env, "POST", f"/api/v1/timelines/{timelines[0]}/export", json={})
    assert first.status_code == 202, first.text
    blocked = call(env, "POST", f"/api/v1/timelines/{timelines[1]}/export", json={})
    assert blocked.status_code == 429, blocked.text
    assert "上限" in blocked.json()["detail"]
    # 内部 API（UI からの操作）には掛からない
    inside = env.client.post(
        f"/api/studio/timelines/{timelines[1]}/export", json={}
    )
    assert inside.status_code == 202, inside.text

    release()
    for export_id in (first.json()["id"], inside.json()["id"]):
        assert wait_for_export(env, export_id)["status"] == "done"


def test_the_editing_helpers_are_reachable(timeline_env):
    """素材ビン・脚本差分・欠落リカバリ・テロップ生成の入り口が繋がっている。"""
    env = timeline_env
    enable(env)
    project = make_project(env)
    detail = call(
        env, "POST", f"/api/v1/projects/{project['id']}/timelines", json={}
    ).json()
    timeline_id = detail["id"]

    media = call(env, "GET", f"/api/v1/projects/{project['id']}/media?kind=audio")
    assert media.status_code == 200, media.text
    assert media.json()["items"] == []

    preview = call(env, "GET", f"/api/v1/timelines/{timeline_id}/sync-preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["added"] == []

    applied = call(env, "POST", f"/api/v1/timelines/{timeline_id}/sync", json={})
    assert applied.status_code == 200, applied.text

    report = call(env, "GET", f"/api/v1/timelines/{timeline_id}/missing")
    assert report.status_code == 200, report.text
    assert report.json()["clips"] == []

    fixed = call(
        env, "POST", f"/api/v1/timelines/{timeline_id}/resolve-missing", json={}
    )
    assert fixed.status_code == 200, fixed.text

    subtitled = call(
        env, "POST", f"/api/v1/timelines/{timeline_id}/generate-subtitles", json={}
    )
    assert subtitled.status_code == 200, subtitled.text
    assert any(track["kind"] == "subtitle" for track in subtitled.json()["tracks"])


def test_timeline_routes_report_what_is_missing(env):
    enable(env)
    assert call(
        env, "POST", "/api/v1/projects/nope/timelines", json={}
    ).status_code == 404
    assert call(env, "GET", "/api/v1/projects/nope/timelines").status_code == 404
    assert call(env, "GET", "/api/v1/projects/nope/media").status_code == 404
    assert call(env, "GET", "/api/v1/timelines/nope").status_code == 404
    assert call(
        env, "PATCH", "/api/v1/timelines/nope", json={"name": "x"}
    ).status_code == 404
    assert call(env, "DELETE", "/api/v1/timelines/nope").status_code == 404
    assert call(
        env, "PUT", "/api/v1/timelines/nope/clips", json={"clips": []}
    ).status_code == 404
    assert call(
        env, "POST", "/api/v1/timelines/nope/tracks", json={}
    ).status_code == 404
    assert call(env, "DELETE", "/api/v1/timelines/nope/tracks/T1").status_code == 404
    assert call(env, "GET", "/api/v1/timelines/nope/sync-preview").status_code == 404
    assert call(env, "GET", "/api/v1/timelines/nope/missing").status_code == 404
    assert call(
        env, "POST", "/api/v1/timelines/nope/export", json={}
    ).status_code == 404
    assert call(env, "GET", "/api/v1/exports/nope").status_code == 404
    assert call(
        env, "POST", "/api/v1/exports/nope/save-to-library", json={}
    ).status_code == 404


def test_the_timeline_routes_need_the_key_too(env):
    paths = (
        "/api/v1/projects/nope/timelines",
        "/api/v1/projects/nope/media",
        "/api/v1/timelines/nope",
        "/api/v1/timelines/nope/sync-preview",
        "/api/v1/timelines/nope/missing",
        "/api/v1/exports/nope",
    )
    for path in paths:
        assert env.client.get(path).status_code == 404, path
    enable(env)
    for path in paths:
        assert env.client.get(path).status_code == 401, path
    # 書き込み側も同じ（キー無しは 401、キーが違っても 401）
    assert env.client.delete("/api/v1/timelines/nope").status_code == 401
    assert env.client.post(
        "/api/v1/timelines/nope/export", json={}, headers={"X-API-Key": "nope"}
    ).status_code == 401


def test_a_plain_pending_job_also_blocks_rendering(env):
    """生成の枠は 1 つのプール（Take と汎用ジョブで分け合う）。"""
    enable(env, external_max_pending_takes=1)
    project = make_project(env)
    shot = call(
        env, "POST", f"/api/v1/projects/{project['id']}/shots",
        json={"prompt": "A cat walks in."},
    ).json()
    # Take に紐づかない、ただの未完了ジョブを 1 本置く
    conn = sqlite3.connect(env.db_path)
    with conn:
        conn.execute(
            "INSERT INTO jobs (id, created_at, mode, status, params, workflow_json)"
            " VALUES (?, '2026-01-01T00:00:00+00:00', 'image_only', 'running',"
            " '{}', '{}')",
            (new_id(),),
        )
    conn.close()

    refused = call(env, "POST", f"/api/v1/shots/{shot['id']}/render")
    assert refused.status_code == 429, refused.text
    assert "上限" in refused.json()["detail"]


def test_deletes_and_reorders_are_recorded_as_external(env):
    """削除・並べ替えも履歴では 'external'（UI の 'user' と区別できる）。"""
    enable(env)
    project = make_project(env)
    episodes = [
        call(
            env, "POST", f"/api/v1/projects/{project['id']}/episodes",
            json={"title": title},
        ).json()
        for title in ("第1話", "第2話")
    ]
    scene = call(
        env, "POST", f"/api/v1/episodes/{episodes[0]['id']}/scenes",
        json={"title": "酒場"},
    ).json()
    asset = call(
        env, "POST", f"/api/v1/projects/{project['id']}/assets",
        json={"name": "タカシ"},
    ).json()

    call(
        env, "POST", f"/api/v1/projects/{project['id']}/episodes/reorder",
        json={"ids": [episodes[1]["id"], episodes[0]["id"]]},
    )
    call(env, "DELETE", f"/api/v1/scenes/{scene['id']}")
    call(env, "DELETE", f"/api/v1/assets/{asset['id']}")
    call(env, "DELETE", f"/api/v1/episodes/{episodes[0]['id']}")

    rows = env.client.get(
        f"/api/studio/projects/{project['id']}/revisions"
    ).json()
    assert {row["actor"] for row in rows} == {"external"}
    assert [row["action"] for row in rows[:4]] == [
        "話『第1話』を削除（場 0 件ごと）",
        "素材『タカシ』を削除",
        "場『酒場』を削除",
        "話を並べ替え（2 件）",
    ]
    # 内部 API（UI）からの同じ操作は 'user' のまま
    remaining = call(
        env, "POST", f"/api/v1/projects/{project['id']}/episodes",
        json={"title": "第3話"},
    ).json()
    env.client.delete(f"/api/studio/episodes/{remaining['id']}")
    rows = env.client.get(
        f"/api/studio/projects/{project['id']}/revisions"
    ).json()
    assert rows[0]["actor"] == "user"
