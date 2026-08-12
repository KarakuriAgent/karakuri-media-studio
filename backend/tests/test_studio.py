"""ドラマスタジオ API: プロジェクト・脚本・素材・Take と、生成の投入。

ComfyUI には繋がない（接続確認を潰してあるので投入したジョブは失敗する）。
ここで見るのは「どのワークフローに何を渡してジョブを作ったか」まで。
Grok（日本語 -> 英語の変換）も呼ばせず、既定では「使えない環境」として扱う。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import comfy, db, grok, jobs, nsfw, studio, workflows
from app.main import app
from app.routers import assets as assets_router


async def _no_llm(text: str) -> None:
    """NSFW 判定の LLM を呼ばせない差し替え（ヒューリスティックに落ちる）。"""
    return None


class FakeLLM:
    """Grok の差し替え。``reply`` を返すか、``error`` があれば失敗する。"""

    def __init__(self) -> None:
        self.reply: str | None = None
        self.error: str | None = "grok CLI が見つかりません"
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise grok.LLMError(self.error)
        return self.reply or ""

    async def health(self):  # pragma: no cover - 呼ばれない
        raise NotImplementedError


@pytest.fixture
def env(tmp_path, monkeypatch):
    """DB・assets をテスト用ディレクトリに閉じ込め、投入されたジョブを記録する。"""
    assets = tmp_path / "assets"
    outputs = tmp_path / "outputs"
    for kind in ("image", "video", "audio"):
        (assets / kind).mkdir(parents=True)
    outputs.mkdir()

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)
    monkeypatch.setattr(jobs, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(assets_router, "ASSETS_DIR", assets)
    monkeypatch.setattr(nsfw, "classify", _no_llm)

    async def offline(*args, **kwargs):
        raise comfy.ComfyError("ComfyUI is down")

    for name in ("get_object_info", "upload_file", "queue_prompt"):
        monkeypatch.setattr(comfy, name, offline)
    # 接続先ごとのケーパビリティ（ラテント連続性）はプロセス内に覚えるので、
    # テストのあいだで持ち越さない。
    comfy.clear_latent_context_cache()

    # 実際に投入された JobCreate を覚えておく（生成の中身の検証はここを見る）
    created: list = []
    real_create_job = jobs.create_job

    async def recording_create_job(payload, **kwargs):
        created.append(payload)
        return await real_create_job(payload, **kwargs)

    monkeypatch.setattr(studio.job_service, "create_job", recording_create_job)

    # 日本語 -> 英語の変換は既定で「Grok が使えない」= 原文のまま投入。
    # 変換そのものを見るテストは `llm.error = None` と `llm.reply` を差す。
    llm = FakeLLM()
    monkeypatch.setattr(grok, "get_client", lambda: llm)

    # スタジオの時計を 1 呼び出し = 1 秒で進める。実時間では 1 テストが同じ秒に
    # 収まってしまい、「Take を作ったあとに脚本を直した」順序が出せないため。
    from datetime import datetime, timedelta, timezone
    from itertools import count

    base = datetime.now(timezone.utc)
    ticks = count()
    monkeypatch.setattr(
        studio,
        "_now",
        lambda: (base + timedelta(seconds=next(ticks))).isoformat(timespec="seconds"),
    )

    with TestClient(app) as client:
        yield type(
            "Env",
            (),
            {
                "client": client,
                "assets": assets,
                "outputs": outputs,
                "created": created,
                "llm": llm,
                "tmp": tmp_path,
            },
        )


# --------------------------------------------------------------------------
# ヘルパー
# --------------------------------------------------------------------------

def make_project(env, **overrides) -> dict:
    body = {"name": "深夜のラーメン屋"}
    body.update(overrides)
    response = env.client.post("/api/studio/projects", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def make_shot(env, project_id: str, **overrides) -> dict:
    body = {"prompt": "A cat walks in.", "duration_seconds": 5}
    body.update(overrides)
    response = env.client.post(
        f"/api/studio/projects/{project_id}/shots", json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_asset(env, project_id: str, name: str, kind: str = "image", **form) -> dict:
    ext = {"image": ".png", "video": ".mp4", "audio": ".wav"}[kind]
    data = {"name": name, "kind": kind}
    data.update(form)
    response = env.client.post(
        f"/api/studio/projects/{project_id}/assets",
        files={"file": (f"{name}{ext}", b"DATA", "application/octet-stream")},
        data=data,
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_metadata_asset(env, project_id: str, name: str, **form) -> dict:
    """ファイルを付けない（メタデータのみの）素材。"""
    data = {"name": name, "kind": "image"}
    data.update(form)
    response = env.client.post(
        f"/api/studio/projects/{project_id}/assets", data=data
    )
    assert response.status_code == 201, response.text
    return response.json()


def render(env, shot_id: str):
    return env.client.post(f"/api/studio/shots/{shot_id}/render")


def detail(env, project_id: str) -> dict:
    response = env.client.get(f"/api/studio/projects/{project_id}")
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# プロジェクト
# --------------------------------------------------------------------------

def test_project_crud(env):
    project = make_project(env, code="RAMEN", synopsis="夜食の話")
    assert project["code"] == "RAMEN"

    listed = env.client.get("/api/studio/projects").json()
    assert [row["id"] for row in listed] == [project["id"]]

    patched = env.client.patch(
        f"/api/studio/projects/{project['id']}", json={"world_notes": "冬・雪"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["world_notes"] == "冬・雪"
    assert patched.json()["name"] == project["name"]  # 触っていない項目は残る

    assert env.client.delete(f"/api/studio/projects/{project['id']}").status_code == 204
    assert env.client.get(f"/api/studio/projects/{project['id']}").status_code == 404


def test_a_project_needs_a_name(env):
    response = env.client.post("/api/studio/projects", json={"name": "  "})
    assert response.status_code == 400


def test_the_code_is_unique_but_may_be_empty(env):
    make_project(env, code="RAMEN")
    duplicate = env.client.post(
        "/api/studio/projects", json={"name": "別の話", "code": "RAMEN"}
    )
    assert duplicate.status_code == 400
    # 空のコードはいくつあってもよい（未設定なので）
    make_project(env, name="A")
    make_project(env, name="B")


def test_deleting_a_project_takes_its_shots_and_assets(env):
    project = make_project(env)
    make_shot(env, project["id"])
    make_asset(env, project["id"], "Neko")
    env.client.delete(f"/api/studio/projects/{project['id']}")
    assert env.client.get("/api/studio/projects").json() == []


# --------------------------------------------------------------------------
# Shot
# --------------------------------------------------------------------------

def test_shot_crud_and_ordering(env):
    project = make_project(env)
    first = make_shot(env, project["id"], title="つかみ")
    second = make_shot(env, project["id"], title="転")
    assert [first["sort_order"], second["sort_order"]] == [0, 1]

    patched = env.client.patch(
        f"/api/studio/shots/{first['id']}",
        json={"dialogue": "いらっしゃい", "duration_seconds": 8},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["dialogue"] == "いらっしゃい"
    assert patched.json()["duration_seconds"] == 8
    assert patched.json()["title"] == "つかみ"

    reordered = env.client.post(
        f"/api/studio/projects/{project['id']}/shots/reorder",
        json={"shot_ids": [second["id"], first["id"]]},
    )
    assert reordered.status_code == 200, reordered.text
    assert [row["id"] for row in reordered.json()] == [second["id"], first["id"]]

    assert env.client.delete(f"/api/studio/shots/{first['id']}").status_code == 204
    assert [row["id"] for row in detail(env, project["id"])["shots"]] == [second["id"]]


def test_reorder_needs_every_shot_of_the_project(env):
    project = make_project(env)
    first = make_shot(env, project["id"])
    make_shot(env, project["id"])
    response = env.client.post(
        f"/api/studio/projects/{project['id']}/shots/reorder",
        json={"shot_ids": [first["id"]]},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# 素材（World Bible）
# --------------------------------------------------------------------------

def test_asset_upload_update_and_delete(env):
    project = make_project(env)
    asset = make_asset(
        env, project["id"], "Neko", caption="三毛猫", prompt_caption="a calico cat"
    )
    assert asset["url"].startswith("/assets/image/")
    assert (env.assets / "image").is_dir()

    duplicate = env.client.post(
        f"/api/studio/projects/{project['id']}/assets",
        files={"file": ("x.png", b"DATA", "image/png")},
        data={"name": "Neko", "kind": "image"},
    )
    assert duplicate.status_code == 400

    patched = env.client.patch(
        f"/api/studio/assets/{asset['id']}",
        json={"category": "character", "locked": True},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["category"] == "character"
    assert patched.json()["locked"] is True

    assert env.client.delete(f"/api/studio/assets/{asset['id']}").status_code == 204
    assert detail(env, project["id"])["assets"] == []


def test_a_metadata_only_asset_can_get_its_file_later(env):
    """ファイルなしで作った素材に、あとからメインのファイルを付けられる。"""
    project = make_project(env)
    asset = make_metadata_asset(env, project["id"], "Neko")
    assert asset["path"] == ""

    response = env.client.post(
        f"/api/studio/assets/{asset['id']}/file",
        files={"file": ("neko.png", b"DATA", "image/png")},
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["kind"] == "image"
    assert updated["url"].startswith("/assets/image/")
    # 種別は拡張子から決まる（音声を差せば音声の素材になる）
    swapped = env.client.post(
        f"/api/studio/assets/{asset['id']}/file",
        files={"file": ("voice.wav", b"DATA", "audio/wav")},
    )
    assert swapped.status_code == 200, swapped.text
    assert swapped.json()["kind"] == "audio"
    assert swapped.json()["url"].startswith("/assets/audio/")


def test_asset_references_are_listed_with_the_asset(env):
    """声・動画・追加画像は素材にぶら下がり、素材と一緒に返る。"""
    project = make_project(env)
    asset = make_asset(env, project["id"], "Neko")

    added = env.client.post(
        f"/api/studio/assets/{asset['id']}/files",
        files={"file": ("voice.wav", b"DATA", "audio/wav")},
        data={"role": "voice", "caption": "落ち着いた声"},
    )
    assert added.status_code == 201, added.text
    reference = added.json()
    assert reference["role"] == "voice"
    assert reference["url"].startswith("/assets/audio/")
    assert reference["caption"] == "落ち着いた声"

    env.client.post(
        f"/api/studio/assets/{asset['id']}/files",
        files={"file": ("walk.mp4", b"DATA", "video/mp4")},
        data={"role": "video"},
    ).raise_for_status()

    listed = env.client.get(f"/api/studio/assets/{asset['id']}/files").json()
    assert [row["role"] for row in listed] == ["voice", "video"]
    # プロジェクト詳細（画面 1 枚ぶん）にもそのまま入る
    assets = detail(env, project["id"])["assets"]
    assert [row["role"] for row in assets[0]["files"]] == ["voice", "video"]
    # メインのファイルは触られない
    assert assets[0]["path"] == asset["path"]

    assert (
        env.client.delete(f"/api/studio/asset-files/{reference['id']}").status_code
        == 204
    )
    assert [row["role"] for row in detail(env, project["id"])["assets"][0]["files"]] == [
        "video"
    ]


def test_an_unknown_reference_role_is_refused(env):
    project = make_project(env)
    asset = make_asset(env, project["id"], "Neko")
    response = env.client.post(
        f"/api/studio/assets/{asset['id']}/files",
        files={"file": ("x.png", b"DATA", "image/png")},
        data={"role": "sound"},
    )
    assert response.status_code == 400


def test_deleting_an_asset_takes_its_references(env):
    project = make_project(env)
    asset = make_asset(env, project["id"], "Neko")
    env.client.post(
        f"/api/studio/assets/{asset['id']}/files",
        files={"file": ("voice.wav", b"DATA", "audio/wav")},
        data={"role": "voice"},
    ).raise_for_status()

    assert env.client.delete(f"/api/studio/assets/{asset['id']}").status_code == 204
    assert env.client.get(f"/api/studio/assets/{asset['id']}/files").status_code == 404


def test_restoring_a_revision_puts_the_references_back(env):
    """リビジョンはリファレンスも一緒に戻す（素材だけ戻っても片手落ちなので）。"""
    project = make_project(env)
    asset = make_asset(env, project["id"], "Neko")
    env.client.post(
        f"/api/studio/assets/{asset['id']}/files",
        files={"file": ("voice.wav", b"DATA", "audio/wav")},
        data={"role": "voice"},
    ).raise_for_status()
    seq = revisions(env, project["id"])[0]["seq"]

    reference_id = detail(env, project["id"])["assets"][0]["files"][0]["id"]
    env.client.delete(f"/api/studio/asset-files/{reference_id}").raise_for_status()
    assert detail(env, project["id"])["assets"][0]["files"] == []

    restored = env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/{seq}/restore"
    )
    assert restored.status_code == 200, restored.text
    assert [row["role"] for row in restored.json()["assets"][0]["files"]] == ["voice"]


def test_an_unsupported_extension_is_refused(env):
    project = make_project(env)
    response = env.client.post(
        f"/api/studio/projects/{project['id']}/assets",
        files={"file": ("notes.txt", b"DATA", "text/plain")},
        data={"name": "Notes", "kind": "image"},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# モードの自動選択とメンション解決
# --------------------------------------------------------------------------

def test_a_shot_without_material_renders_as_t2v(env):
    project = make_project(env)
    shot = make_shot(
        env,
        project["id"],
        prompt="A cat walks into a ramen shop.",
        camera="handheld, low angle",
        dialogue="いらっしゃい",
        soundscape="rain on the awning",
        bgm="slow jazz",
    )
    response = render(env, shot["id"])
    assert response.status_code == 201, response.text
    take = response.json()
    # ここでは ComfyUI に繋がらないので、ランナーが先に走り切っていれば
    # 'failed' が返る（状態はジョブから導出しているため）。
    assert take["status"] in ("rendering", "failed")

    payload = env.created[-1]
    assert payload.video_workflow == "minimax_h3_t2v"
    assert payload.mode == "i2v"  # 動画ステージだけを走らせる
    assert payload.duration == 5
    assert payload.source_image is None
    assert payload.reference_images == []
    # 台詞・SE・BGM・カメラが H3 の書式で本文に足される
    assert "Camera: handheld, low angle" in payload.video_prompt
    assert 'Audio: rain on the awning; music: slow jazz; spoken: "いらっしゃい"' \
        in payload.video_prompt
    assert payload.video_prompt.endswith(studio.EXCLUSION_SENTENCE)


def test_mentions_become_reference_tags_and_pick_r2v(env):
    project = make_project(env)
    make_asset(env, project["id"], "Neko", kind="image")
    make_asset(env, project["id"], "Odori", kind="video")
    make_asset(env, project["id"], "Koe", kind="audio")
    shot = make_shot(
        env,
        project["id"],
        prompt="@Neko moves like @Odori and sounds like @Koe.",
    )
    assert render(env, shot["id"]).status_code == 201

    payload = env.created[-1]
    assert payload.video_workflow == "minimax_h3_r2v"
    # `<Audio j>` は参照動画のサウンドトラックが先に番号を取る（H3 の決まり）
    assert payload.video_prompt.startswith(
        "<Picture 1> moves like <Video 1> and sounds like <Audio 2>."
    )
    assert [p.rsplit("/", 1)[-1].split("_")[0] for p in payload.reference_images] \
        == ["Neko"]
    assert len(payload.reference_videos) == 1
    assert len(payload.reference_audios) == 1


def test_the_same_asset_mentioned_twice_keeps_one_slot(env):
    project = make_project(env)
    make_asset(env, project["id"], "Neko", kind="image")
    shot = make_shot(env, project["id"], prompt="@Neko sits, then @Neko jumps.")
    assert render(env, shot["id"]).status_code == 201

    payload = env.created[-1]
    assert payload.video_prompt.startswith("<Picture 1> sits, then <Picture 1> jumps.")
    assert len(payload.reference_images) == 1


def test_an_unknown_mention_is_a_400(env):
    project = make_project(env)
    make_asset(env, project["id"], "Neko", kind="image")
    shot = make_shot(env, project["id"], prompt="@Inu barks at @Neko.")
    response = render(env, shot["id"])
    assert response.status_code == 400
    assert "@Inu" in response.json()["detail"]
    assert env.created == []  # ジョブは作られない


def test_a_longer_name_wins_over_its_prefix(env):
    project = make_project(env)
    make_asset(env, project["id"], "Aki", kind="image")
    make_asset(env, project["id"], "Akira", kind="image")
    shot = make_shot(env, project["id"], prompt="@Akira greets @Aki.")
    assert render(env, shot["id"]).status_code == 201
    payload = env.created[-1]
    assert payload.video_prompt.startswith("<Picture 1> greets <Picture 2>.")


def test_a_braced_mention_is_resolved(env):
    project = make_project(env)
    make_asset(env, project["id"], "Ramen Shop", kind="image")
    shot = make_shot(env, project["id"], prompt="Inside @{Ramen Shop}, steam rises.")
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].video_prompt.startswith("Inside <Picture 1>, steam rises.")


def test_an_empty_prompt_is_a_400(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="   ")
    assert render(env, shot["id"]).status_code == 400


# --------------------------------------------------------------------------
# ラストフレームの引き継ぎ（i2v）
# --------------------------------------------------------------------------

def _finish_job(env, job_id: str, last_frame) -> None:
    """ジョブを「成功して成果物が揃った」状態に書き換える（ComfyUI の代わり）。

    ランナーが先に走って失敗させるので、決着が付くのを待ってから上書きする。
    """
    import sqlite3
    import time

    deadline = time.time() + 10.0
    while time.time() < deadline:
        status = env.client.get(f"/api/jobs/{job_id}").json()["status"]
        if status in ("done", "failed", "canceled"):
            break
        time.sleep(0.02)
    else:  # pragma: no cover - ランナーが動いていれば通らない
        raise AssertionError(f"job {job_id} did not settle")

    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'done', video_path = ?, last_frame_path = ?"
            " WHERE id = ?",
            (str(last_frame.with_suffix(".mp4")), str(last_frame), job_id),
        )


def test_carry_over_uses_the_previous_selected_take_as_the_start_frame(env):
    project = make_project(env)
    first = make_shot(env, project["id"], prompt="A cat walks in.")
    second = make_shot(
        env,
        project["id"],
        prompt="The cat sits down.",
        carry_over_end_frame=True,
    )

    take = render(env, first["id"]).json()
    last_frame = env.outputs / "last.png"
    last_frame.write_bytes(b"PNG")
    _finish_job(env, take["job_id"], last_frame)

    selected = env.client.post(f"/api/studio/takes/{take['id']}/select")
    assert selected.status_code == 200, selected.text
    assert selected.json()["status"] == "selected"

    assert render(env, second["id"]).status_code == 201
    payload = env.created[-1]
    assert payload.video_workflow == "minimax_h3_i2v"
    assert payload.source_image.endswith(".png")
    assert (env.assets / "image") in Path(payload.source_image).parents


def test_carry_over_falls_back_when_nothing_was_selected_yet(env):
    project = make_project(env)
    make_shot(env, project["id"], prompt="A cat walks in.")
    second = make_shot(
        env, project["id"], prompt="The cat sits.", carry_over_end_frame=True
    )
    assert render(env, second["id"]).status_code == 201
    assert env.created[-1].video_workflow == "minimax_h3_t2v"


def test_carry_over_renders_mentions_as_descriptions(env):
    """i2v は参照素材を取れないので、`@名前` はタグではなく説明文になる。"""
    project = make_project(env)
    make_asset(
        env, project["id"], "Neko", kind="image", prompt_caption="a calico cat"
    )
    first = make_shot(env, project["id"], prompt="Establishing shot.")
    second = make_shot(
        env, project["id"], prompt="@Neko sits down.", carry_over_end_frame=True
    )

    take = render(env, first["id"]).json()
    last_frame = env.outputs / "last.png"
    last_frame.write_bytes(b"PNG")
    _finish_job(env, take["job_id"], last_frame)
    env.client.post(f"/api/studio/takes/{take['id']}/select")

    assert render(env, second["id"]).status_code == 201
    payload = env.created[-1]
    assert payload.video_workflow == "minimax_h3_i2v"
    assert payload.video_prompt.startswith("a calico cat sits down.")
    assert payload.reference_images == []


# --------------------------------------------------------------------------
# ラテント連続性（Motion Context）の引き継ぎ
# --------------------------------------------------------------------------

def _allow_latent_context(monkeypatch, available: bool = True) -> None:
    """接続先に Motion Context のノードが「ある / ない」ことにする。"""

    async def support(target=None):
        return available

    monkeypatch.setattr(studio.comfy, "latent_context_support", support)


def _finish_context_job(env, take: dict, *, latent: str | None) -> None:
    """ジョブを成功させ、ラテント連続性の成果（AV ラテント）まで揃える。

    ``latent`` が None なら「ラテントは残らなかった」ぶんの再現。
    """
    import sqlite3

    last_frame = env.outputs / f"last_{take['id']}.png"
    last_frame.write_bytes(b"PNG")
    # 引き継ぎ元の動画は実体が要る（assets/video/ に複製してから渡すため）
    last_frame.with_suffix(".mp4").write_bytes(b"MP4")
    _finish_job(env, take["job_id"], last_frame)
    if latent is None:
        return
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE studio_takes SET latent_path = ? WHERE id = ?",
            (latent, take["id"]),
        )


def _continuity_pair(env, *, latent: str | None = "/comfy/output/h3_context/a_00001_.safetensors"):
    """「前カットを採用済み」の状態まで進めた (project, 続きの Shot) を返す。"""
    project = make_project(env, latent_continuity=True)
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    first = make_shot(env, project["id"], prompt="@Neko walks in.")
    second = make_shot(
        env,
        project["id"],
        prompt="@Neko sits down.",
        carry_over_end_frame=True,
    )
    take = render(env, first["id"]).json()
    _finish_context_job(env, take, latent=latent)
    assert env.client.post(f"/api/studio/takes/{take['id']}/select").status_code == 200
    return project, second


def test_latent_continuity_is_off_by_default(env):
    project = make_project(env)
    assert project["latent_continuity"] is False


def test_latent_continuity_carries_the_previous_video_and_latent(env, monkeypatch):
    _allow_latent_context(monkeypatch)
    latent = "/comfy/output/h3_context/a_00001_.safetensors"
    _project, second = _continuity_pair(env, latent=latent)

    assert render(env, second["id"]).status_code == 201
    payload = env.created[-1]
    assert payload.video_workflow == "minimax_h3_r2v_context"
    # 直前カットの動画はジョブの入力なので assets/video/ に移してから渡す
    assert (env.assets / "video") in Path(payload.reference_video).parents
    # AV ラテントは ComfyUI 側のパスなので、そのまま渡す
    assert payload.context_latent_path == latent
    # 素材は参照として添付される（連続カットは r2v の上に乗っている）
    assert len(payload.reference_images) == 1


def test_latent_continuity_needs_reference_material(env, monkeypatch):
    """参照素材を呼んでいないカットは、黙って i2v に落とさずその場で断る。"""
    _allow_latent_context(monkeypatch)
    project = make_project(env, latent_continuity=True)
    make_shot(env, project["id"], prompt="A cat walks in.")
    second = make_shot(
        env, project["id"], prompt="The cat sits.", carry_over_end_frame=True
    )
    response = render(env, second["id"])
    assert response.status_code == 400
    assert "連続カットには参照素材が必要です" in response.json()["detail"]


def test_latent_continuity_needs_a_selected_previous_take(env, monkeypatch):
    _allow_latent_context(monkeypatch)
    project = make_project(env, latent_continuity=True)
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    make_shot(env, project["id"], prompt="@Neko walks in.")
    second = make_shot(
        env, project["id"], prompt="@Neko sits down.", carry_over_end_frame=True
    )
    response = render(env, second["id"])
    assert response.status_code == 400
    assert "前 Shot の採用 Take がありません" in response.json()["detail"]


def test_latent_continuity_needs_the_previous_take_to_have_a_latent(env, monkeypatch):
    """採用済みでも AV ラテントが残っていなければ続きにはできない。"""
    _allow_latent_context(monkeypatch)
    _project, second = _continuity_pair(env, latent=None)
    response = render(env, second["id"])
    assert response.status_code == 400
    assert "前 Shot の採用 Take がありません" in response.json()["detail"]


def test_latent_continuity_is_refused_when_the_backend_lacks_the_nodes(env, monkeypatch):
    """ノードの無い接続先には投げない（黙って別のモードに落とさない）。"""
    _allow_latent_context(monkeypatch)
    _project, second = _continuity_pair(env)
    _allow_latent_context(monkeypatch, available=False)
    response = render(env, second["id"])
    assert response.status_code == 400
    assert "ラテント連続性" in response.json()["detail"]
    # 投入そのものが起きていない
    assert all(
        payload.video_workflow != "minimax_h3_r2v_context" for payload in env.created
    )


def test_latent_continuity_saves_the_latent_on_the_plain_cuts_too(env, monkeypatch):
    """連鎖の起点になる通常カットも保存付きバリアントで投げる。

    ここでラテントを残さないと、次のカットが引き継ぐものが無く連鎖を始められない。
    """
    _allow_latent_context(monkeypatch)
    project = make_project(env, latent_continuity=True)
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    # t2v（素材も引き継ぎも無い）
    plain = make_shot(env, project["id"], prompt="A cat walks in.")
    assert render(env, plain["id"]).status_code == 201
    assert env.created[-1].video_workflow == "minimax_h3_t2v_save"
    # r2v（プロンプトが素材を呼ぶ）
    reference = make_shot(env, project["id"], prompt="@Neko walks in.")
    assert render(env, reference["id"]).status_code == 201
    assert env.created[-1].video_workflow == "minimax_h3_r2v_save"
    # AV ラテントは持たないので、引き継ぎ元は渡らない
    assert env.created[-1].context_latent_path is None


def test_latent_continuity_saves_the_latent_on_a_forced_workflow(env, monkeypatch):
    """強制指定（``workflow_override``）でも、ON なら保存付きに読み替える。"""
    _allow_latent_context(monkeypatch)
    project = make_project(env, latent_continuity=True)
    shot = make_shot(
        env, project["id"], prompt="A cat walks in.", workflow_override="minimax_h3_t2v"
    )
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].video_workflow == "minimax_h3_t2v_save"


def test_latent_continuity_keeps_the_context_workflow_as_is(env, monkeypatch):
    """連続カット版は元から保存するので、読み替えない。"""
    _allow_latent_context(monkeypatch)
    _project, second = _continuity_pair(env)
    assert render(env, second["id"]).status_code == 201
    assert env.created[-1].video_workflow == "minimax_h3_r2v_context"


def test_the_plain_cuts_are_refused_when_the_backend_lacks_the_nodes(env, monkeypatch):
    """保存付きバリアントもカスタムノード頼みなので、投入前に断る。"""
    _allow_latent_context(monkeypatch, available=False)
    project = make_project(env, latent_continuity=True)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    response = render(env, shot["id"])
    assert response.status_code == 400
    assert "ラテント連続性" in response.json()["detail"]
    assert env.created == []


def test_latent_continuity_off_keeps_the_plain_workflows(env, monkeypatch):
    """OFF のプロジェクトは今までどおり素のワークフロー id のまま。"""
    _allow_latent_context(monkeypatch)
    project = make_project(env)  # latent_continuity は既定の False
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    plain = make_shot(env, project["id"], prompt="A cat walks in.")
    assert render(env, plain["id"]).status_code == 201
    assert env.created[-1].video_workflow == "minimax_h3_t2v"
    reference = make_shot(env, project["id"], prompt="@Neko walks in.")
    assert render(env, reference["id"]).status_code == 201
    assert env.created[-1].video_workflow == "minimax_h3_r2v"


def test_latent_continuity_off_keeps_the_last_frame_carry_over(env, monkeypatch):
    """既定（OFF）のプロジェクトの挙動は今までどおり i2v のまま。"""
    _allow_latent_context(monkeypatch)
    project = make_project(env)  # latent_continuity は既定の False
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    first = make_shot(env, project["id"], prompt="@Neko walks in.")
    second = make_shot(
        env, project["id"], prompt="@Neko sits down.", carry_over_end_frame=True
    )
    take = render(env, first["id"]).json()
    _finish_context_job(env, take, latent="/comfy/output/h3_context/a_00001_.safetensors")
    env.client.post(f"/api/studio/takes/{take['id']}/select")

    assert render(env, second["id"]).status_code == 201
    payload = env.created[-1]
    assert payload.video_workflow == "minimax_h3_i2v"
    assert payload.reference_video is None
    assert payload.context_latent_path is None


def test_the_preview_shows_where_the_latent_comes_from(env, monkeypatch):
    _allow_latent_context(monkeypatch)
    latent = "/comfy/output/h3_context/a_00001_.safetensors"
    _project, second = _continuity_pair(env, latent=latent)

    preview = env.client.get(f"/api/studio/shots/{second['id']}/prompt-preview").json()
    assert preview["workflow"] == "minimax_h3_r2v_context"
    assert preview["latent_continuity"] is True
    assert preview["context_latent"] == latent
    assert preview["context_video"]
    assert "ラテント" in preview["workflow_reason"]


def test_the_capabilities_endpoint_reports_the_target(env, monkeypatch):
    # ComfyUI に届かないときは「使えない」＋理由（500 にはしない）
    unreachable = env.client.get("/api/studio/capabilities").json()
    assert unreachable["latent_continuity"] is False
    assert unreachable["error"]

    async def object_info(class_type=None, *, target=None):
        return {name: {} for name in workflows.LATENT_CONTEXT_CLASS_TYPES}

    monkeypatch.setattr(comfy, "get_object_info", object_info)
    comfy.clear_latent_context_cache()
    assert env.client.get("/api/studio/capabilities").json() == {
        "latent_continuity": True,
        "error": "",
    }


def test_a_job_records_its_latent_on_the_take(env, monkeypatch):
    """ジョブランナーが持ち帰ったパスは、その Take に控えられる。"""
    import asyncio

    _allow_latent_context(monkeypatch)
    project = make_project(env, latent_continuity=True)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    take = render(env, shot["id"]).json()

    asyncio.run(studio.record_take_latent(take["job_id"], "/comfy/output/x_00003_.safetensors"))
    takes = env.client.get(f"/api/studio/shots/{shot['id']}/takes").json()
    assert takes[0]["latent_path"] == "/comfy/output/x_00003_.safetensors"


# --------------------------------------------------------------------------
# Take の採用・不採用
# --------------------------------------------------------------------------

def test_selecting_a_take_demotes_the_previous_one(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    first = render(env, shot["id"]).json()
    second = render(env, shot["id"]).json()

    env.client.post(f"/api/studio/takes/{first['id']}/select")
    env.client.post(f"/api/studio/takes/{second['id']}/select")

    context = detail(env, project["id"])
    by_id = {take["id"]: take for take in context["takes"]}
    assert by_id[second["id"]]["status"] == "selected"
    # 前の採用は候補へ戻る（ジョブがまだ走っているので 'rendering' の導出になる）
    assert by_id[first["id"]]["status"] != "selected"
    assert context["shots"][0]["selected_take_id"] == second["id"]
    assert context["shots"][0]["status"] == "done"


def test_rejecting_the_selected_take_clears_the_shot(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    take = render(env, shot["id"]).json()
    env.client.post(f"/api/studio/takes/{take['id']}/select")

    rejected = env.client.post(f"/api/studio/takes/{take['id']}/reject")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    context = detail(env, project["id"])
    assert context["shots"][0]["selected_take_id"] is None
    assert context["shots"][0]["status"] == "ready"


def test_a_finished_job_makes_the_take_a_candidate(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    take = render(env, shot["id"]).json()
    last_frame = env.outputs / "last.png"
    last_frame.write_bytes(b"PNG")
    _finish_job(env, take["job_id"], last_frame)

    row = detail(env, project["id"])["takes"][0]
    assert row["status"] == "candidate"
    assert row["job_status"] == "done"
    assert row["last_frame_path"] == str(last_frame)
    assert row["last_frame_url"] == "/outputs/last.png"
    assert row["video_workflow"] == "minimax_h3_t2v"


def test_deleting_a_take_clears_the_selection(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    take = render(env, shot["id"]).json()
    env.client.post(f"/api/studio/takes/{take['id']}/select")

    assert env.client.delete(f"/api/studio/takes/{take['id']}").status_code == 204
    assert detail(env, project["id"])["shots"][0]["selected_take_id"] is None
    assert detail(env, project["id"])["takes"] == []


def test_deleting_a_shot_takes_its_takes(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    render(env, shot["id"])
    env.client.delete(f"/api/studio/shots/{shot['id']}")
    assert detail(env, project["id"])["takes"] == []


def test_unknown_ids_are_404(env):
    assert env.client.get("/api/studio/projects/nope").status_code == 404
    assert env.client.post("/api/studio/shots/nope/render").status_code == 404
    assert env.client.post("/api/studio/takes/nope/select").status_code == 404
    assert env.client.patch("/api/studio/assets/nope", json={}).status_code == 404
    assert env.client.delete("/api/studio/shots/nope").status_code == 404


# --------------------------------------------------------------------------
# 一覧の件数
# --------------------------------------------------------------------------

def test_the_project_list_carries_its_counts(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    make_shot(env, project["id"])
    make_asset(env, project["id"], "Neko")
    take = render(env, shot["id"]).json()

    row = env.client.get("/api/studio/projects").json()[0]
    assert row["shot_count"] == 2
    assert row["asset_count"] == 1
    assert row["take_count"] == 1
    assert row["selected_take_count"] == 0

    env.client.post(f"/api/studio/takes/{take['id']}/select")
    assert env.client.get("/api/studio/projects").json()[0]["selected_take_count"] == 1


# --------------------------------------------------------------------------
# Shot ごとの生成設定
# --------------------------------------------------------------------------

def test_the_shot_settings_reach_the_job(env):
    project = make_project(env)
    shot = make_shot(
        env,
        project["id"],
        aspect_ratio="16:9 (Widescreen)",
        megapixels=0.5,
        seed=1234,
    )
    assert render(env, shot["id"]).status_code == 201
    payload = env.created[-1]
    assert payload.aspect_ratio == "16:9 (Widescreen)"
    assert payload.megapixels == 0.5
    assert payload.seed == 1234


def test_settings_left_out_keep_the_job_defaults(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    assert shot["aspect_ratio"] is None
    assert render(env, shot["id"]).status_code == 201
    payload = env.created[-1]
    assert payload.aspect_ratio == "4:3 (Standard)"
    assert payload.megapixels == 0.4
    assert payload.seed is None


def test_a_setting_can_be_cleared_with_an_explicit_null(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], seed=7, aspect_ratio="16:9 (Widescreen)")
    patched = env.client.patch(
        f"/api/studio/shots/{shot['id']}", json={"seed": None}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["seed"] is None
    # 送らなかった項目は残る
    assert patched.json()["aspect_ratio"] == "16:9 (Widescreen)"


def test_an_nsfw_project_submits_its_jobs_as_nsfw(env):
    project = make_project(env, nsfw=True)
    assert project["nsfw"] is True
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"]).status_code == 201
    # 明示指定なので manual 扱い（あとから自動判定に上書きされない）
    assert env.created[-1].nsfw is True
    take = detail(env, project["id"])["takes"][0]
    assert take["nsfw"] is True
    assert take["nsfw_source"] == "manual"


def test_a_plain_project_pins_its_jobs_to_non_nsfw(env):
    project = make_project(env)
    assert project["nsfw"] is False
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"]).status_code == 201
    # False も明示する = 自動判定は走らない（非 NSFW で固定）
    assert env.created[-1].nsfw is False
    take = detail(env, project["id"])["takes"][0]
    assert take["nsfw"] is False
    assert take["nsfw_source"] == "manual"


def test_a_plain_project_never_runs_the_auto_classifier(env, monkeypatch):
    calls: list[str] = []

    async def spy(text: str) -> bool | None:
        calls.append(text)
        return True

    monkeypatch.setattr(nsfw, "classify", spy)
    project = make_project(env)
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"]).status_code == 201
    assert calls == []


def test_the_nsfw_flag_of_a_project_can_be_turned_off_again(env):
    project = make_project(env, nsfw=True)
    patched = env.client.patch(
        f"/api/studio/projects/{project['id']}", json={"nsfw": False}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["nsfw"] is False
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].nsfw is False


def test_the_project_nsfw_flag_shows_up_in_the_listing(env):
    make_project(env, nsfw=True)
    rows = env.client.get("/api/studio/projects").json()
    assert [row["nsfw"] for row in rows] == [True]


def test_shots_do_not_carry_an_nsfw_flag_of_their_own(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    assert "nsfw" not in shot


def test_a_take_carries_the_nsfw_flag_of_its_job(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    take = render(env, shot["id"]).json()
    # 非 NSFW のプロジェクトなので、投入の時点で False に固定されている
    assert take["nsfw"] is False

    toggled = env.client.post(f"/api/jobs/{take['job_id']}/nsfw", json={"nsfw": True})
    assert toggled.status_code == 200, toggled.text
    row = detail(env, project["id"])["takes"][0]
    assert row["nsfw"] is True
    assert row["nsfw_source"] == "manual"


def test_the_workflow_can_be_forced_to_t2v_even_with_material(env):
    project = make_project(env)
    make_asset(env, project["id"], "Neko", prompt_caption="a calico cat")
    shot = make_shot(
        env, project["id"], prompt="@Neko sits.", workflow_override="minimax_h3_t2v"
    )
    assert render(env, shot["id"]).status_code == 201
    payload = env.created[-1]
    assert payload.video_workflow == "minimax_h3_t2v"
    assert payload.reference_images == []
    assert payload.video_prompt.startswith("a calico cat sits.")


def test_forcing_r2v_without_material_is_a_400(env):
    project = make_project(env)
    shot = make_shot(
        env, project["id"], prompt="A cat.", workflow_override="minimax_h3_r2v"
    )
    response = render(env, shot["id"])
    assert response.status_code == 400
    assert "r2v" in response.json()["detail"]
    assert env.created == []


def test_forcing_i2v_without_a_start_frame_is_a_400(env):
    project = make_project(env)
    make_shot(env, project["id"])
    second = make_shot(
        env, project["id"], workflow_override="minimax_h3_i2v"
    )
    response = render(env, second["id"])
    assert response.status_code == 400
    assert "i2v" in response.json()["detail"]


# --------------------------------------------------------------------------
# 話（エピソード）と場（シーン）
# --------------------------------------------------------------------------

def make_episode(env, project_id: str, **body) -> dict:
    response = env.client.post(
        f"/api/studio/projects/{project_id}/episodes", json=body or {"title": "第1話"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_scene(env, episode_id: str, **body) -> dict:
    response = env.client.post(
        f"/api/studio/episodes/{episode_id}/scenes", json=body or {"title": "屋台"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_episode_and_scene_crud(env):
    project = make_project(env)
    first = make_episode(env, project["id"], title="第1話")
    second = make_episode(env, project["id"], title="第2話")
    assert [first["sort_order"], second["sort_order"]] == [0, 1]

    scene = make_scene(env, first["id"], title="屋台", time_of_day="深夜")
    assert scene["project_id"] == project["id"]
    assert scene["episode_id"] == first["id"]

    patched = env.client.patch(
        f"/api/studio/episodes/{first['id']}", json={"synopsis": "出会い"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["synopsis"] == "出会い"
    assert patched.json()["title"] == "第1話"

    reordered = env.client.post(
        f"/api/studio/projects/{project['id']}/episodes/reorder",
        json={"ids": [second["id"], first["id"]]},
    )
    assert reordered.status_code == 200, reordered.text
    assert [row["id"] for row in reordered.json()] == [second["id"], first["id"]]

    context = detail(env, project["id"])
    assert [row["id"] for row in context["episodes"]] == [second["id"], first["id"]]
    assert [row["id"] for row in context["scenes"]] == [scene["id"]]


def test_reordering_needs_every_episode(env):
    project = make_project(env)
    first = make_episode(env, project["id"])
    make_episode(env, project["id"])
    response = env.client.post(
        f"/api/studio/projects/{project['id']}/episodes/reorder",
        json={"ids": [first["id"]]},
    )
    assert response.status_code == 400


def test_scenes_can_be_reordered_inside_their_episode(env):
    project = make_project(env)
    episode = make_episode(env, project["id"])
    first = make_scene(env, episode["id"], title="A")
    second = make_scene(env, episode["id"], title="B")
    response = env.client.post(
        f"/api/studio/episodes/{episode['id']}/scenes/reorder",
        json={"ids": [second["id"], first["id"]]},
    )
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [second["id"], first["id"]]


def test_a_shot_can_join_and_leave_a_scene(env):
    project = make_project(env)
    episode = make_episode(env, project["id"])
    scene = make_scene(env, episode["id"])
    shot = make_shot(env, project["id"], scene_id=scene["id"])
    assert shot["scene_id"] == scene["id"]

    detached = env.client.patch(
        f"/api/studio/shots/{shot['id']}", json={"scene_id": None}
    )
    assert detached.status_code == 200, detached.text
    assert detached.json()["scene_id"] is None

    rejoined = env.client.patch(
        f"/api/studio/shots/{shot['id']}", json={"scene_id": scene["id"]}
    )
    assert rejoined.json()["scene_id"] == scene["id"]


def test_a_shot_cannot_join_a_scene_of_another_project(env):
    project = make_project(env)
    other = make_project(env, name="別の作品")
    episode = make_episode(env, other["id"])
    scene = make_scene(env, episode["id"])
    response = env.client.post(
        f"/api/studio/projects/{project['id']}/shots",
        json={"prompt": "x", "scene_id": scene["id"]},
    )
    assert response.status_code == 404


def test_deleting_a_scene_leaves_its_shots_unfiled(env):
    project = make_project(env)
    episode = make_episode(env, project["id"])
    scene = make_scene(env, episode["id"])
    shot = make_shot(env, project["id"], scene_id=scene["id"])

    assert env.client.delete(f"/api/studio/scenes/{scene['id']}").status_code == 204
    context = detail(env, project["id"])
    assert context["scenes"] == []
    assert [row["id"] for row in context["shots"]] == [shot["id"]]
    assert context["shots"][0]["scene_id"] is None


def test_deleting_an_episode_takes_its_scenes_and_unfiles_the_shots(env):
    project = make_project(env)
    episode = make_episode(env, project["id"])
    scene = make_scene(env, episode["id"])
    make_shot(env, project["id"], scene_id=scene["id"])

    assert env.client.delete(f"/api/studio/episodes/{episode['id']}").status_code == 204
    context = detail(env, project["id"])
    assert context["episodes"] == []
    assert context["scenes"] == []
    assert context["shots"][0]["scene_id"] is None


# --------------------------------------------------------------------------
# 採用の取り消し（null の明示）
# --------------------------------------------------------------------------

def test_the_selection_can_be_cleared_with_an_explicit_null(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    take = render(env, shot["id"]).json()
    env.client.post(f"/api/studio/takes/{take['id']}/select")

    kept = env.client.patch(f"/api/studio/shots/{shot['id']}", json={"title": "つかみ"})
    assert kept.json()["selected_take_id"] == take["id"]  # 送らなければ残る

    cleared = env.client.patch(
        f"/api/studio/shots/{shot['id']}", json={"selected_take_id": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["selected_take_id"] is None


# --------------------------------------------------------------------------
# リビジョン履歴
# --------------------------------------------------------------------------

def revisions(env, project_id: str) -> list[dict]:
    response = env.client.get(f"/api/studio/projects/{project_id}/revisions")
    assert response.status_code == 200, response.text
    return response.json()


def test_every_change_leaves_a_revision(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    env.client.patch(f"/api/studio/shots/{shot['id']}", json={"prompt": "B"})

    rows = revisions(env, project["id"])
    assert [row["seq"] for row in rows] == [3, 2, 1]  # 新しい順
    assert rows[-1]["action"] == "プロジェクトを作成"
    assert all(row["actor"] == "user" for row in rows)
    assert "snapshot" not in rows[0]  # 一覧に中身は載せない


def test_a_revision_carries_the_whole_project_but_no_takes(env):
    project = make_project(env)
    make_shot(env, project["id"], prompt="A cat walks in.")
    make_asset(env, project["id"], "Neko")
    seq = revisions(env, project["id"])[0]["seq"]

    response = env.client.get(f"/api/studio/projects/{project['id']}/revisions/{seq}")
    assert response.status_code == 200, response.text
    snapshot = response.json()["snapshot"]
    assert snapshot["project"]["name"] == project["name"]
    assert [row["prompt"] for row in snapshot["shots"]] == ["A cat walks in."]
    assert [row["name"] for row in snapshot["assets"]] == ["Neko"]
    # cards はキャンバス（別ビュー）の置き場所、asset_files は素材の
    # リファレンス。Take だけが入らない。
    assert set(snapshot) == {
        "project", "episodes", "scenes", "shots", "assets", "asset_files",
        "cards",
    }


def test_restoring_puts_the_script_back_and_keeps_the_takes(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    take = render(env, shot["id"]).json()
    seq = revisions(env, project["id"])[0]["seq"]

    env.client.patch(f"/api/studio/shots/{shot['id']}", json={"prompt": "壊した"})
    doomed = make_shot(env, project["id"], prompt="あとから足した Shot")

    restored = env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/{seq}/restore"
    )
    assert restored.status_code == 200, restored.text
    context = restored.json()
    assert [row["prompt"] for row in context["shots"]] == ["A cat walks in."]
    assert doomed["id"] not in [row["id"] for row in context["shots"]]
    # Take は復元の対象外＝そのまま残る
    assert [row["id"] for row in context["takes"]] == [take["id"]]
    # 復元そのものも履歴になる
    assert revisions(env, project["id"])[0]["action"] == f"リビジョン {seq} を復元"


def test_restoring_brings_deleted_records_back(env):
    project = make_project(env)
    episode = make_episode(env, project["id"], title="第1話")
    scene = make_scene(env, episode["id"], title="屋台")
    asset = make_metadata_asset(env, project["id"], "Neko", caption="三毛猫")
    seq = revisions(env, project["id"])[0]["seq"]

    env.client.delete(f"/api/studio/episodes/{episode['id']}")
    env.client.delete(f"/api/studio/assets/{asset['id']}")

    context = env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/{seq}/restore"
    ).json()
    assert [row["id"] for row in context["episodes"]] == [episode["id"]]
    assert [row["id"] for row in context["scenes"]] == [scene["id"]]
    assert [row["caption"] for row in context["assets"]] == ["三毛猫"]


def test_swapping_two_asset_names_survives_a_restore(env):
    project = make_project(env)
    first = make_metadata_asset(env, project["id"], "Aki")
    second = make_metadata_asset(env, project["id"], "Nao")
    seq = revisions(env, project["id"])[0]["seq"]

    env.client.patch(f"/api/studio/assets/{first['id']}", json={"name": "Zzz"})
    env.client.patch(f"/api/studio/assets/{second['id']}", json={"name": "Aki"})

    context = env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/{seq}/restore"
    ).json()
    assert {row["id"]: row["name"] for row in context["assets"]} == {
        first["id"]: "Aki",
        second["id"]: "Nao",
    }


def test_only_the_latest_revisions_are_kept(env, monkeypatch):
    monkeypatch.setattr(studio, "REVISION_LIMIT", 3)
    project = make_project(env)
    for index in range(5):
        make_shot(env, project["id"], prompt=f"shot {index}")
    rows = revisions(env, project["id"])
    assert [row["seq"] for row in rows] == [6, 5, 4]


def test_an_unknown_revision_is_a_404(env):
    project = make_project(env)
    assert env.client.get(
        f"/api/studio/projects/{project['id']}/revisions/999"
    ).status_code == 404
    assert env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/999/restore"
    ).status_code == 404
    assert env.client.get("/api/studio/projects/nope/revisions").status_code == 404


# --------------------------------------------------------------------------
# stale（作り直したほうがよい Take）
# --------------------------------------------------------------------------

def test_a_fresh_take_is_not_stale(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    render(env, shot["id"])
    take = detail(env, project["id"])["takes"][0]
    assert take["stale"] is False
    assert take["stale_reasons"] == []


def test_editing_the_script_makes_the_take_stale(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    render(env, shot["id"])

    env.client.patch(f"/api/studio/shots/{shot['id']}", json={"prompt": "A dog."})
    row = detail(env, project["id"])["takes"][0]
    assert row["stale"] is True
    assert row["stale_reasons"] == ["脚本が更新されました"]
    # /shots/{id}/takes からも同じ導出が見える
    listed = env.client.get(f"/api/studio/shots/{shot['id']}/takes").json()
    assert listed[0]["stale_reasons"] == ["脚本が更新されました"]


def test_a_change_that_does_not_touch_the_prompt_leaves_the_take_alone(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    render(env, shot["id"])
    env.client.patch(f"/api/studio/shots/{shot['id']}", json={"title": "つかみ"})
    assert detail(env, project["id"])["takes"][0]["stale"] is False


def test_editing_a_mentioned_material_makes_the_take_stale(env):
    project = make_project(env)
    asset = make_asset(env, project["id"], "Neko", prompt_caption="a cat")
    other = make_asset(env, project["id"], "Inu", prompt_caption="a dog")
    shot = make_shot(env, project["id"], prompt="@Neko walks in.")
    render(env, shot["id"])

    # 本文が呼んでいない素材を直しても影響しない
    patched = env.client.patch(
        f"/api/studio/assets/{other['id']}", json={"prompt_caption": "a big dog"}
    )
    assert patched.status_code == 200, patched.text
    assert detail(env, project["id"])["takes"][0]["stale"] is False

    env.client.patch(
        f"/api/studio/assets/{asset['id']}", json={"prompt_caption": "a calico cat"}
    )
    row = detail(env, project["id"])["takes"][0]
    assert row["stale"] is True
    assert row["stale_reasons"] == ["素材『Neko』が更新されました"]


def test_locking_a_material_does_not_make_takes_stale(env):
    project = make_project(env)
    asset = make_asset(env, project["id"], "Neko")
    shot = make_shot(env, project["id"], prompt="@Neko walks in.")
    render(env, shot["id"])

    env.client.patch(f"/api/studio/assets/{asset['id']}", json={"locked": True})
    assert detail(env, project["id"])["takes"][0]["stale"] is False


# --------------------------------------------------------------------------
# メタデータのみの素材
# --------------------------------------------------------------------------

def test_a_material_without_a_file_is_written_out_as_text(env):
    project = make_project(env)
    make_metadata_asset(
        env, project["id"], "Neko", caption="三毛猫", prompt_caption="a calico cat"
    )
    asset = detail(env, project["id"])["assets"][0]
    assert asset["path"] == ""
    assert asset["url"] == ""

    shot = make_shot(env, project["id"], prompt="@Neko sits down.")
    assert render(env, shot["id"]).status_code == 201
    payload = env.created[-1]
    # 添付する実体が無いので r2v にはならず、説明文が本文に入る
    assert payload.video_workflow == "minimax_h3_t2v"
    assert payload.video_prompt.startswith("a calico cat sits down.")
    assert payload.reference_images == []


def test_a_file_backed_material_still_wins_r2v_next_to_a_metadata_one(env):
    project = make_project(env)
    make_asset(env, project["id"], "Neko", kind="image")
    make_metadata_asset(env, project["id"], "Yatai", prompt_caption="a ramen stall")
    shot = make_shot(env, project["id"], prompt="@Neko sits in @Yatai.")
    assert render(env, shot["id"]).status_code == 201

    payload = env.created[-1]
    assert payload.video_workflow == "minimax_h3_r2v"
    assert payload.video_prompt.startswith("<Picture 1> sits in a ramen stall.")
    assert len(payload.reference_images) == 1


def test_a_material_without_a_file_still_needs_a_name(env):
    project = make_project(env)
    response = env.client.post(
        f"/api/studio/projects/{project['id']}/assets", data={"kind": "image"}
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# 日本語 -> 英語の変換（Grok）
# --------------------------------------------------------------------------

def test_a_japanese_prompt_is_translated_before_it_is_submitted(env):
    env.llm.error = None
    env.llm.reply = 'A cat walks into <Picture 1>.\nAudio: spoken: "いらっしゃい"'
    project = make_project(env)
    make_asset(env, project["id"], "Yatai", kind="image")
    shot = make_shot(
        env, project["id"], prompt="猫が @Yatai に入ってくる。", dialogue="いらっしゃい"
    )
    take = render(env, shot["id"])
    assert take.status_code == 201, take.text

    payload = env.created[-1]
    assert payload.video_prompt == env.llm.reply
    # 変換の指示にはワークフローの書き方の規約とタグの取り扱いが入る
    instruction = env.llm.prompts[-1]
    assert "<Picture 1>" in instruction
    assert "reference tag" in instruction
    assert "original language" in instruction
    assert "shot-by-shot timeline" in instruction

    body = take.json()
    assert body["prompt"] == env.llm.reply
    assert body["source_prompt"].startswith("猫が <Picture 1> に入ってくる。")
    assert body["warning"] == ""


def test_an_english_prompt_is_submitted_as_is(env):
    env.llm.error = None
    env.llm.reply = "should not be used"
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    take = render(env, shot["id"]).json()
    assert env.llm.prompts == []
    assert take["source_prompt"] == ""
    assert env.created[-1].video_prompt.startswith("A cat walks in.")


def test_translation_can_be_turned_off_per_project(env):
    env.llm.error = None
    env.llm.reply = "should not be used"
    project = make_project(env, auto_translate=False)
    shot = make_shot(env, project["id"], prompt="猫が入ってくる。")
    assert render(env, shot["id"]).status_code == 201
    assert env.llm.prompts == []
    assert env.created[-1].video_prompt.startswith("猫が入ってくる。")


def test_a_broken_grok_warns_but_still_submits_the_original(env):
    project = make_project(env)  # fixture の既定は「grok が使えない」
    shot = make_shot(env, project["id"], prompt="猫が入ってくる。")
    response = render(env, shot["id"])
    assert response.status_code == 201, response.text
    take = response.json()
    assert "原文のまま投入" in take["warning"]
    assert take["source_prompt"] == ""
    assert env.created[-1].video_prompt.startswith("猫が入ってくる。")


def test_a_fenced_answer_is_unwrapped(env):
    env.llm.error = None
    env.llm.reply = "```\nA cat walks in.\n```"
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が入ってくる。")
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].video_prompt == "A cat walks in."


# --------------------------------------------------------------------------
# デモプロジェクト
# --------------------------------------------------------------------------

def test_the_demo_builds_a_whole_project(env):
    response = env.client.post("/api/studio/demo", json={"code": "YOAKE-01"})
    assert response.status_code == 201, response.text
    context = response.json()
    assert context["name"] == "夜明けの鋼"
    assert context["auto_translate"] is True
    assert len(context["episodes"]) == 1
    assert len(context["scenes"]) == 1
    assert len(context["shots"]) == 6
    assert len(context["assets"]) == 4
    assert context["takes"] == []

    # 素材はすべてメタデータのみ（日本語の caption と英語の prompt_caption の対）
    assert all(asset["path"] == "" for asset in context["assets"])
    rin = next(asset for asset in context["assets"] if asset["name"] == "凛")
    assert rin["category"] == "character"
    assert rin["locked"] is True
    assert "整備士" in rin["caption"]
    assert rin["prompt_caption"].startswith("young Japanese mechanic")

    # Shot は場に属し、本文は素材を `@名前` で呼ぶ
    scene_id = context["scenes"][0]["id"]
    assert all(shot["scene_id"] == scene_id for shot in context["shots"])
    assert "@凛" in context["shots"][0]["prompt"]

    # そのまま投入できる（メタデータのみの素材は説明文に展開される）
    assert render(env, context["shots"][0]["id"]).status_code == 201
    assert env.created[-1].video_workflow == "minimax_h3_t2v"
    assert "young Japanese mechanic" in env.created[-1].video_prompt


def test_every_demo_can_be_created(env):
    from app.studio_demo import DEMO_BY_CODE

    for code in DEMO_BY_CODE:
        assert env.client.post("/api/studio/demo", json={"code": code}).status_code == 201
    assert len(env.client.get("/api/studio/projects").json()) == len(DEMO_BY_CODE)


def test_the_same_demo_twice_is_a_409(env):
    env.client.post("/api/studio/demo", json={"code": "SAZANAMI-02"})
    again = env.client.post("/api/studio/demo", json={"code": "SAZANAMI-02"})
    assert again.status_code == 409
    assert "SAZANAMI-02" in again.json()["detail"]


def test_an_unknown_demo_is_a_400(env):
    response = env.client.post("/api/studio/demo", json={"code": "NOPE"})
    assert response.status_code == 400


# --------------------------------------------------------------------------
# 投入プレビュー（GET /api/studio/shots/{id}/prompt-preview）
# --------------------------------------------------------------------------

def preview(env, shot_id: str) -> dict:
    response = env.client.get(f"/api/studio/shots/{shot_id}/prompt-preview")
    assert response.status_code == 200, response.text
    return response.json()


def test_the_preview_shows_the_whole_assembled_prompt(env):
    project = make_project(env)
    shot = make_shot(
        env,
        project["id"],
        prompt="A cat walks in.",
        camera="slow dolly in",
        dialogue="Good evening.",
        soundscape="rain on the roof",
        bgm="lonely piano",
    )
    body = preview(env, shot["id"])
    assert body["shot_id"] == shot["id"]
    assert body["workflow"] == "minimax_h3_t2v"
    assert body["workflow_reason"]
    assert body["error"] == ""
    assert body["references"] == []
    assert body["start_frame"] is None
    lines = body["prompt"].splitlines()
    assert lines[0] == "A cat walks in."
    assert lines[1] == "Camera: slow dolly in"
    assert "rain on the roof" in lines[2]
    assert "music: lonely piano" in lines[2]
    assert 'spoken: "Good evening."' in lines[2]
    assert lines[3] == studio.EXCLUSION_SENTENCE


def test_the_preview_lists_the_attached_references(env):
    project = make_project(env)
    make_asset(env, project["id"], "Neko", kind="image")
    make_asset(env, project["id"], "Koe", kind="audio")
    shot = make_shot(env, project["id"], prompt="@Neko meows, @Koe answers.")

    body = preview(env, shot["id"])
    assert body["workflow"] == "minimax_h3_r2v"
    assert body["prompt"].startswith("<Picture 1> meows, <Audio 1> answers.")
    assert [(row["name"], row["kind"], row["tag"]) for row in body["references"]] == [
        ("Neko", "image", "<Picture 1>"),
        ("Koe", "audio", "<Audio 1>"),
    ]
    assert body["references"][0]["path"].endswith(".png")


def test_the_preview_shows_the_carried_over_start_frame(env):
    project = make_project(env)
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    first = make_shot(env, project["id"], prompt="Establishing shot.")
    second = make_shot(
        env, project["id"], prompt="@Neko sits down.", carry_over_end_frame=True
    )

    take = render(env, first["id"]).json()
    last_frame = env.outputs / "last.png"
    last_frame.write_bytes(b"PNG")
    _finish_job(env, take["job_id"], last_frame)
    env.client.post(f"/api/studio/takes/{take['id']}/select")

    body = preview(env, second["id"])
    assert body["workflow"] == "minimax_h3_i2v"
    assert body["start_frame"].endswith(".png")
    # i2v は参照を取れないので、`@名前` は説明文になる（生成と同じ）
    assert body["prompt"].startswith("a calico cat sits down.")
    assert body["references"] == []


def test_the_preview_reports_an_unresolved_mention_without_failing(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="@Inu barks.")
    body = preview(env, shot["id"])
    assert "@Inu" in body["error"]
    assert body["prompt"] == ""
    assert env.created == []  # プレビューは何も投入しない


def test_the_preview_reports_a_forced_mode_that_cannot_run(env):
    project = make_project(env)
    make_shot(env, project["id"])
    second = make_shot(env, project["id"], workflow_override="minimax_h3_i2v")
    body = preview(env, second["id"])
    assert "i2v" in body["error"]
    assert body["workflow"] == "minimax_h3_i2v"  # 何を指定していたかは見せる
    assert body["prompt"] == ""


def test_the_preview_reports_an_empty_prompt(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="   ")
    assert preview(env, shot["id"])["error"]


def test_the_preview_says_the_prompt_will_be_translated_but_does_not_call_grok(env):
    project = make_project(env)
    env.llm.error = None
    env.llm.reply = "A cat walks in."
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")

    body = preview(env, shot["id"])
    assert body["auto_translate"] is True
    assert body["will_translate"] is True
    assert body["prompt"].startswith("猫が歩いてくる。")  # 訳す前の姿
    assert env.llm.prompts == []


def test_an_english_prompt_is_not_marked_for_translation(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    body = preview(env, shot["id"])
    assert body["auto_translate"] is True
    assert body["will_translate"] is False


def test_translation_turned_off_shows_in_the_preview(env):
    project = make_project(env, auto_translate=False)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    body = preview(env, shot["id"])
    assert body["auto_translate"] is False
    assert body["will_translate"] is False


def test_the_preview_matches_what_is_actually_submitted(env):
    project = make_project(env)
    make_asset(env, project["id"], "Neko", kind="image")
    make_metadata_asset(env, project["id"], "Yatai", prompt_caption="a ramen stall")
    shot = make_shot(
        env,
        project["id"],
        prompt="@Neko waits at @Yatai.",
        camera="handheld, low angle",
        dialogue="One more bowl.",
    )

    body = preview(env, shot["id"])
    assert render(env, shot["id"]).status_code == 201
    payload = env.created[-1]
    assert payload.video_workflow == body["workflow"]
    assert payload.video_prompt == body["prompt"]
    assert payload.reference_images == [row["path"] for row in body["references"]]


def test_the_preview_of_an_unknown_shot_is_a_404(env):
    assert env.client.get("/api/studio/shots/nope/prompt-preview").status_code == 404
