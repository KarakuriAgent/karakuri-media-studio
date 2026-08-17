"""外部公開 API（``/api/v1``。docs/EXTERNAL-API.md）。

見るのは入り口の 3 つ: API キーでの出し分け、一括投入の「全部か全く無しか」、
未完了 Take が溜まったときの投入拒否。スタジオ操作そのものの中身は
``test_studio.py`` が見ているので、ここでは薄いラッパーが既存のサービスへ
つながっていることだけを確かめる（ComfyUI にも Grok にも繋がない）。
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import (
    comfy,
    config,
    db,
    drafting_guide,
    grok,
    jobs,
    nsfw,
    studio,
    workflows,
)
from app.ids import new_id
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


def test_a_job_can_be_read_but_not_created(env):
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
    # 汎用のジョブ投入は公開しない
    assert call(env, "POST", "/api/v1/jobs", json={}).status_code == 405


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

    monkeypatch.setattr(studio, "count_pending_takes", counted)
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
