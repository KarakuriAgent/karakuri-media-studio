"""ドラマスタジオ API: プロジェクト・脚本・素材・Take と、生成の投入。

ComfyUI には繋がない（接続確認を潰してあるので投入したジョブは失敗する）。
ここで見るのは「どのワークフローに何を渡してジョブを作ったか」まで。
Grok（日本語 -> 英語の変換）も呼ばせず、既定では「使えない環境」として扱う
（auto_translate の日本語 Shot は受け付けるが、ジョブ側の英訳で失敗する）。
"""

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import comfy, db, grok, jobs, nsfw, studio, workflows
from app.main import app
from app.models import MAX_STEPS, StudioShot
from app.routers import assets as assets_router
from tests.test_jobs import _hang_until_cancelled, wait_for


async def _no_llm(text: str) -> None:
    """NSFW 判定の LLM を呼ばせない差し替え（ヒューリスティックに落ちる）。"""
    return None


class FakeLLM:
    """Grok の差し替え。``reply`` を返すか、``error`` があれば失敗する。

    ``hold`` がセットされた Event なら、立つまで待つ（既定はゲート無しで即返す）。
    """

    def __init__(self) -> None:
        self.reply: str | None = None
        self.error: str | None = "grok CLI が見つかりません"
        self.prompts: list[str] = []
        self.hold: threading.Event | None = None

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        hold = self.hold
        if hold is not None:
            while not hold.is_set():
                await asyncio.sleep(0.05)
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
    extras: list = []
    real_create_job = jobs.create_job

    async def recording_create_job(payload, **kwargs):
        created.append(payload)
        extras.append(kwargs.get("extra_params"))
        return await real_create_job(payload, **kwargs)

    monkeypatch.setattr(studio.job_service, "create_job", recording_create_job)

    # 日本語 -> 英語の変換は既定で「Grok が使えない」= ジョブが失敗する。
    # 変換そのものを見るテストは `llm.error = None` と `llm.reply` を差す。
    llm = FakeLLM()
    monkeypatch.setattr(grok, "get_client", lambda *a, **k: llm)

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
                "extras": extras,
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


def render(env, shot_id: str, body: dict | None = None):
    """カットを 1 回生成する（``body`` はその 1 回だけに効く上書き）。"""
    return env.client.post(
        f"/api/studio/shots/{shot_id}/render", json=body if body is not None else None
    )


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




def test_a_shot_can_carry_a_planned_start_second(env):
    """音源上の計画開始秒は、書けて・null で外せて・負の値は断られる。

    通常のドラマ制作では使わない項目（並び順で足りる）。MV のように音源が正本
    の制作で、タイムラインの sync がこの秒へカットを置く。
    """
    project = make_project(env)
    shot = make_shot(env, project["id"], planned_start_seconds=16.6)
    assert shot["planned_start_seconds"] == 16.6

    plain = make_shot(env, project["id"])
    assert plain["planned_start_seconds"] is None

    moved = env.client.patch(
        f"/api/studio/shots/{shot['id']}", json={"planned_start_seconds": 20.3}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["planned_start_seconds"] == 20.3

    cleared = env.client.patch(
        f"/api/studio/shots/{shot['id']}", json={"planned_start_seconds": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["planned_start_seconds"] is None

    refused = env.client.patch(
        f"/api/studio/shots/{shot['id']}", json={"planned_start_seconds": -1}
    )
    assert refused.status_code == 400



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
    # 台詞が日本語なので、組み立て検証のために英訳は切る。
    project = make_project(env, auto_translate=False)
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
    # 台詞・SE・BGM・カメラが公式 H3 フィールドで本文に足される
    assert payload.video_prompt.startswith("integrated_multimodal_description:")
    assert "The camera handheld, low angle." in payload.video_prompt
    assert "<d>[Japanese] いらっしゃい</d>" in payload.video_prompt
    assert "overall_soundscape: rain on the awning" in payload.video_prompt
    assert "non_diegetic_music: slow jazz" in payload.video_prompt
    assert "Camera:" not in payload.video_prompt
    assert "Audio:" not in payload.video_prompt
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
        "detailed_description: <Picture 1> moves like <Video 1> and sounds like <Audio 2>."
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
    assert payload.video_prompt.startswith(
        "detailed_description: <Picture 1> sits, then <Picture 1> jumps."
    )
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
    assert payload.video_prompt.startswith(
        "detailed_description: <Picture 1> greets <Picture 2>."
    )


def test_a_braced_mention_is_resolved(env):
    project = make_project(env)
    make_asset(env, project["id"], "Ramen Shop", kind="image")
    shot = make_shot(env, project["id"], prompt="Inside @{Ramen Shop}, steam rises.")
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].video_prompt.startswith(
        "detailed_description: Inside <Picture 1>, steam rises."
    )


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
    assert payload.video_prompt.startswith(
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced."
    )
    assert "integrated_multimodal_description: a calico cat sits down." \
        in payload.video_prompt
    assert payload.reference_images == []


# --------------------------------------------------------------------------
# ラテント連続性（Motion Context）の引き継ぎ
# --------------------------------------------------------------------------

def _allow_latent_context(monkeypatch, available: bool = True) -> None:
    """接続先に Motion Context のノードが「ある / ない」ことにする。"""

    async def support(target=None):
        return available

    monkeypatch.setattr(studio.comfy, "latent_context_support", support)


def _finish_context_job(
    env, take: dict, *, latent: str | None, latent_hires: str | None = None
) -> None:
    """ジョブを成功させ、ラテント連続性の成果（AV ラテント）まで揃える。

    ``latent`` が None なら「ラテントは残らなかった」ぶんの再現。
    ``latent_hires`` は 2 段引き継ぎ（``latent_upscale`` = on）で残る
    2 パス目のラテント（None = off で作った過去テイク）。
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
            "UPDATE studio_takes SET latent_path = ?, latent_hires_path = ?"
            " WHERE id = ?",
            (latent, latent_hires, take["id"]),
        )


def _continuity_pair(
    env,
    *,
    latent: str | None = "/comfy/output/h3_context/a_00001_.safetensors",
    latent_hires: str | None = None,
    quality: str = "normal",
):
    """「前カットを採用済み」の状態まで進めた (project, 続きの Shot) を返す。"""
    project = make_project(env, latent_continuity=True, quality=quality)
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    first = make_shot(env, project["id"], prompt="@Neko walks in.")
    second = make_shot(
        env,
        project["id"],
        prompt="@Neko sits down.",
        carry_over_end_frame=True,
    )
    take = render(env, first["id"]).json()
    _finish_context_job(env, take, latent=latent, latent_hires=latent_hires)
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


def test_latent_continuity_carries_the_hires_latent_too(env, monkeypatch):
    """2 段引き継ぎ: 前カットに 2 本目があれば、それも次のカットへ渡す。"""
    _allow_latent_context(monkeypatch)
    latent = "/comfy/output/h3_context/a_00001_.safetensors"
    hires = "/comfy/output/h3_context/a_hires_00001_.safetensors"
    _project, second = _continuity_pair(env, latent=latent, latent_hires=hires)

    assert render(env, second["id"]).status_code == 201
    payload = env.created[-1]
    assert payload.context_latent_path == latent
    assert payload.context_latent_hires_path == hires


def test_latent_continuity_without_a_hires_latent_falls_back(env, monkeypatch):
    """2 本目が無い過去テイク（latent_upscale off）でも断らず 1 段で続ける。"""
    _allow_latent_context(monkeypatch)
    _project, second = _continuity_pair(env, latent_hires=None)

    assert render(env, second["id"]).status_code == 201
    assert env.created[-1].context_latent_hires_path is None


def test_the_take_records_both_latents(env, monkeypatch):
    """ジョブが持ち帰った 2 本のパスは、その Take の 2 列に入る。"""
    _allow_latent_context(monkeypatch)
    latent = "/comfy/output/h3_context/a_00001_.safetensors"
    hires = "/comfy/output/h3_context/a_hires_00001_.safetensors"
    project = make_project(env, latent_continuity=True)
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    shot = make_shot(env, project["id"], prompt="@Neko walks in.")
    take = render(env, shot["id"]).json()
    asyncio.run(studio.record_take_latent(take["job_id"], latent, hires))
    _finish_context_job(env, take, latent=None)
    stored = env.client.get(f"/api/studio/shots/{shot['id']}/takes").json()[0]
    assert stored["latent_path"] == latent
    assert stored["latent_hires_path"] == hires


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


def _continuity_without_a_previous_take(env, monkeypatch):
    """前 Shot がまだ採用されていない連続カットを作る。"""
    _allow_latent_context(monkeypatch)
    project = make_project(env, latent_continuity=True)
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    make_shot(env, project["id"], prompt="@Neko walks in.")
    return make_shot(
        env, project["id"], prompt="@Neko が座る。", carry_over_end_frame=True
    )


def test_the_preview_assembles_before_the_previous_take_exists(env, monkeypatch):
    """引き継ぎ元がまだ無くても、本文は連続カットの形で見せる（投入だけ不可）。"""
    second = _continuity_without_a_previous_take(env, monkeypatch)

    body = preview(env, second["id"])
    assert body["error"] == ""
    assert body["workflow"] == "minimax_h3_r2v_context"
    assert body["prompt"]
    assert body["context_latent"] is None
    assert body["context_video"] is None
    assert "前 Shot の採用 Take がありません" in body["render_blocker"]
    # 生成は今までどおり断る
    response = render(env, second["id"])
    assert response.status_code == 400
    assert "前 Shot の採用 Take がありません" in response.json()["detail"]


def test_translate_works_before_the_previous_take_exists(env, monkeypatch):
    """英訳は前カットの完成を待たずにできる（本文の組み立ては同じ）。"""
    env.llm.error = None
    env.llm.reply = (
        "integrated_multimodal_description: <Picture 1> the cat sits down.\n"
        "No text, subtitles, logos or watermarks."
    )
    second = _continuity_without_a_previous_take(env, monkeypatch)
    assembled = preview(env, second["id"])["prompt"]

    response = _translate(env, second["id"])
    assert response.status_code == 200, response.text
    body = wait_translated(env, second["id"])
    assert body["english_prompt"] == env.llm.reply
    assert body["render_blocker"]
    assert assembled in env.llm.prompts[-1]


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
        # ローカル ComfyUI ならアップスケーラのカスタムノードも入れられる
        "latent_upscale": True,
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
# 動画生成の品質（プロジェクトの quality）
# --------------------------------------------------------------------------
#
# 品質は論理モード（t2v / i2v / r2v）と直交していて、モードが決まったあとに
# 「モード × 品質 -> バリアント id」で解決される（studio._quality_workflow）。
# ここで押さえるのは、効くとき・素へ落ちる 3 つの条件・そのときの理由の文言。

def _use_target(monkeypatch, target: str) -> None:
    """接続先だけを差し替える（turbo / opt の対応判定に効く）。"""
    from app import config
    from app.models import Settings

    monkeypatch.setattr(config, "_settings", Settings(comfy_target=target))


def _quality_pair(env, quality: str):
    """(project, 参照素材を呼ぶ Shot) を作る（r2v になるので品質が効く）。"""
    project = make_project(env, quality=quality)
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    shot = make_shot(env, project["id"], prompt="@Neko walks in.")
    return project, shot


def test_quality_is_normal_by_default(env):
    project = make_project(env)
    assert project["quality"] == "normal"
    assert env.client.get("/api/studio/projects").json()[0]["quality"] == "normal"


def test_quality_is_saved_as_a_project_setting(env):
    project = make_project(env)
    updated = env.client.patch(
        f"/api/studio/projects/{project['id']}", json={"quality": "turbo"}
    )
    assert updated.status_code == 200
    assert updated.json()["quality"] == "turbo"
    assert detail(env, project["id"])["quality"] == "turbo"


def test_an_unknown_quality_is_refused(env):
    project = make_project(env)
    response = env.client.patch(
        f"/api/studio/projects/{project['id']}", json={"quality": "ultra"}
    )
    assert response.status_code == 422


def test_a_project_without_the_column_reads_as_normal(env):
    """列を持たない（品質より前に作られた）既存 DB は 'normal' として読む。"""
    import sqlite3

    project = make_project(env, quality="turbo")
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE studio_projects SET quality = '' WHERE id = ?", (project["id"],)
        )
    assert detail(env, project["id"])["quality"] == "normal"


@pytest.mark.parametrize(
    ("quality", "expected"),
    [
        ("normal", "minimax_h3_r2v"),
        ("opt", "minimax_h3_r2v_opt"),
        ("turbo", "minimax_h3_r2v_turbo"),
    ],
)
def test_quality_picks_the_r2v_variant(env, monkeypatch, quality, expected):
    _use_target(monkeypatch, "local")
    _project, shot = _quality_pair(env, quality)
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].video_workflow == expected


@pytest.mark.parametrize(
    ("quality", "expected"),
    [
        ("normal", "minimax_h3_i2v"),
        ("opt", "minimax_h3_i2v_opt"),
        ("turbo", "minimax_h3_i2v_turbo"),
    ],
)
def test_quality_picks_the_i2v_variant(env, monkeypatch, quality, expected):
    """引き継ぎで i2v になったカットにも品質が掛かる。"""
    _use_target(monkeypatch, "local")
    project = make_project(env, quality=quality)
    first = make_shot(env, project["id"], prompt="A cat walks in.")
    second = make_shot(
        env, project["id"], prompt="The cat sits.", carry_over_end_frame=True
    )
    take = render(env, first["id"]).json()
    last_frame = env.outputs / f"last_{take['id']}.png"
    last_frame.write_bytes(b"PNG")
    _finish_job(env, take["job_id"], last_frame)
    env.client.post(f"/api/studio/takes/{take['id']}/select")

    assert render(env, second["id"]).status_code == 201
    assert env.created[-1].video_workflow == expected


@pytest.mark.parametrize(
    ("quality", "expected"),
    [
        ("normal", "minimax_h3_t2v"),
        ("opt", "minimax_h3_t2v_opt"),
        ("turbo", "minimax_h3_t2v_turbo"),
    ],
)
def test_quality_picks_the_t2v_variant(env, monkeypatch, quality, expected):
    """t2v にも turbo / opt のテンプレートがある。"""
    _use_target(monkeypatch, "local")
    project = make_project(env, quality=quality)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].video_workflow == expected


@pytest.mark.parametrize(
    ("quality", "expected"),
    [
        ("normal", "minimax_h3_r2v_save"),
        ("opt", "minimax_h3_r2v_save_opt"),
        ("turbo", "minimax_h3_r2v_save_turbo"),
    ],
)
def test_quality_applies_to_the_latent_saving_variant(
    env, monkeypatch, quality, expected
):
    """ラテント連続性 ON の**起点**カットは、保存付き × 品質で解決する。"""
    _allow_latent_context(monkeypatch)
    _use_target(monkeypatch, "local")
    project = make_project(env, quality=quality, latent_continuity=True)
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    shot = make_shot(env, project["id"], prompt="@Neko walks in.")
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].video_workflow == expected

    preview = env.client.get(f"/api/studio/shots/{shot['id']}/prompt-preview").json()
    assert preview["quality_applied"] is (quality != "normal")


@pytest.mark.parametrize(
    ("quality", "expected"),
    [
        ("normal", "minimax_h3_r2v_context"),
        ("opt", "minimax_h3_r2v_context_opt"),
        ("turbo", "minimax_h3_r2v_context_turbo"),
    ],
)
def test_quality_applies_to_the_continuous_cut_variant(
    env, monkeypatch, quality, expected
):
    """ラテント連続性 ON の**続き**のカットも、連続カット版 × 品質で解決する。"""
    _allow_latent_context(monkeypatch)
    _use_target(monkeypatch, "local")
    _project, second = _continuity_pair(env, quality=quality)
    assert render(env, second["id"]).status_code == 201
    assert env.created[-1].video_workflow == expected


@pytest.mark.parametrize("quality", ["opt", "turbo"])
def test_latent_continuity_falls_back_on_a_target_without_the_custom_nodes(
    env, monkeypatch, quality
):
    """接続先が対応しないときは、品質だけ落として保存付きの版は保つ。"""
    _allow_latent_context(monkeypatch)
    _use_target(monkeypatch, "comfy_cloud")
    project = make_project(env, quality=quality, latent_continuity=True)
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    shot = make_shot(env, project["id"], prompt="@Neko walks in.")
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].video_workflow == "minimax_h3_r2v_save"

    preview = env.client.get(f"/api/studio/shots/{shot['id']}/prompt-preview").json()
    assert preview["quality_applied"] is False
    assert "接続先" in preview["workflow_reason"]


@pytest.mark.parametrize("quality", ["opt", "turbo"])
def test_quality_falls_back_when_a_workflow_has_no_variant(monkeypatch, quality):
    """表に無いワークフローは黙って落とさず、理由つきでそのまま投げる。"""
    _use_target(monkeypatch, "local")
    workflow, reason = studio._quality_workflow("minimax_h3_nonesuch", quality)
    assert workflow == "minimax_h3_nonesuch"
    assert "用意が無い" in reason


def test_every_quality_variant_is_a_registered_workflow():
    """品質の表が実在しないテンプレートを指していたら、黙って素へ落ちてしまう。"""
    from app.workflows import get_video_spec

    for table in studio.QUALITY_WORKFLOWS.values():
        for base, variant in table.items():
            assert get_video_spec(base).id == base
            assert get_video_spec(variant).id == variant


@pytest.mark.parametrize("quality", ["opt", "turbo"])
def test_quality_falls_back_on_a_target_without_the_custom_nodes(
    env, monkeypatch, quality
):
    """Comfy Cloud には任意のカスタムノードを入れられないので素へ落とす。"""
    _use_target(monkeypatch, "comfy_cloud")
    _project, shot = _quality_pair(env, quality)
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].video_workflow == "minimax_h3_r2v"

    preview = env.client.get(f"/api/studio/shots/{shot['id']}/prompt-preview").json()
    assert preview["quality_applied"] is False
    assert "接続先" in preview["workflow_reason"]


def test_the_preview_keeps_the_mode_reason_next_to_the_quality(env, monkeypatch):
    """品質の一文はモードの理由を置き換えず、後ろに足す。"""
    _use_target(monkeypatch, "local")
    _project, shot = _quality_pair(env, "turbo")
    preview = env.client.get(f"/api/studio/shots/{shot['id']}/prompt-preview").json()
    assert preview["workflow"] == "minimax_h3_r2v_turbo"
    assert preview["quality_applied"] is True
    assert "素材を呼んでいます" in preview["workflow_reason"]
    assert "Turbo" in preview["workflow_reason"]


def test_a_forced_workflow_still_gets_the_quality(env, monkeypatch):
    """workflow_override は素のモード id のままで、品質は掛け合わせで効く。"""
    _use_target(monkeypatch, "local")
    project = make_project(env, quality="opt")
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    shot = make_shot(
        env,
        project["id"],
        prompt="@Neko walks in.",
        workflow_override="minimax_h3_r2v",
    )
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].video_workflow == "minimax_h3_r2v_opt"


# --------------------------------------------------------------------------
# 画像生成の品質（プロジェクトの image_quality）
# --------------------------------------------------------------------------
#
# 動画の quality とは独立したつまみで、素材の静止画（MiniMax H3 Image）にだけ
# 効く。ここで押さえるのは、保存と正規化・動画の品質を巻き込まないこと・
# 「素 / _opt / _turbo」の解決（studio.image_quality_workflow）。

def test_image_quality_is_normal_by_default(env):
    project = make_project(env)
    assert project["image_quality"] == "normal"
    assert env.client.get("/api/studio/projects").json()[0]["image_quality"] == "normal"


def test_image_quality_is_saved_as_a_project_setting(env):
    project = make_project(env, image_quality="opt")
    assert project["image_quality"] == "opt"
    updated = env.client.patch(
        f"/api/studio/projects/{project['id']}", json={"image_quality": "turbo"}
    )
    assert updated.status_code == 200
    assert updated.json()["image_quality"] == "turbo"
    assert detail(env, project["id"])["image_quality"] == "turbo"


def test_an_unknown_image_quality_is_refused(env):
    project = make_project(env)
    response = env.client.patch(
        f"/api/studio/projects/{project['id']}", json={"image_quality": "ultra"}
    )
    assert response.status_code == 422


def test_a_project_without_the_image_quality_column_reads_as_normal(env):
    """列を持たない（画像品質より前に作られた）既存 DB は 'normal' として読む。"""
    import sqlite3

    project = make_project(env, image_quality="turbo")
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE studio_projects SET image_quality = '' WHERE id = ?",
            (project["id"],),
        )
    assert detail(env, project["id"])["image_quality"] == "normal"


def test_image_quality_and_video_quality_are_independent(env, monkeypatch):
    """画像を turbo にしても動画の品質は動かない（その逆も同じ）。"""
    _use_target(monkeypatch, "local")
    project = make_project(env, quality="normal", image_quality="turbo")
    make_asset(env, project["id"], "Neko", kind="image", prompt_caption="a calico cat")
    shot = make_shot(env, project["id"], prompt="@Neko walks in.")
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].video_workflow == "minimax_h3_r2v"

    env.client.patch(
        f"/api/studio/projects/{project['id']}",
        json={"quality": "turbo", "image_quality": "normal"},
    )
    assert detail(env, project["id"])["image_quality"] == "normal"
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].video_workflow == "minimax_h3_r2v_turbo"


@pytest.mark.parametrize(
    ("workflow", "image_quality", "expected"),
    [
        ("minimax_h3_t2i", "normal", "minimax_h3_t2i"),
        ("minimax_h3_t2i", "opt", "minimax_h3_t2i_opt"),
        ("minimax_h3_t2i", "turbo", "minimax_h3_t2i_turbo"),
        ("minimax_h3_i2i", "opt", "minimax_h3_i2i_opt"),
        ("minimax_h3_i2i", "turbo", "minimax_h3_i2i_turbo"),
        ("minimax_h3_r2i", "opt", "minimax_h3_r2i_opt"),
        ("minimax_h3_r2i", "turbo", "minimax_h3_r2i_turbo"),
    ],
)
def test_image_quality_picks_the_image_variant(
    monkeypatch, workflow, image_quality, expected
):
    _use_target(monkeypatch, "local")
    assert studio.image_quality_workflow(workflow, image_quality) == expected


@pytest.mark.parametrize("image_quality", ["opt", "turbo"])
def test_image_quality_falls_back_without_a_variant(monkeypatch, image_quality):
    _use_target(monkeypatch, "local")
    assert (
        studio.image_quality_workflow("qwen_image", image_quality) == "qwen_image"
    )


@pytest.mark.parametrize("image_quality", ["opt", "turbo"])
def test_image_quality_falls_back_on_a_target_without_the_custom_nodes(
    monkeypatch, image_quality
):
    """Comfy Cloud には任意のカスタムノードを入れられないので素へ落とす。"""
    _use_target(monkeypatch, "comfy_cloud")
    assert (
        studio.image_quality_workflow("minimax_h3_t2i", image_quality)
        == "minimax_h3_t2i"
    )


def test_a_broken_image_quality_is_read_as_normal(monkeypatch):
    _use_target(monkeypatch, "local")
    assert (
        studio.image_quality_workflow("minimax_h3_t2i", "ultra") == "minimax_h3_t2i"
    )


def test_every_image_quality_variant_is_a_registered_workflow():
    """画像品質の表が実在しないテンプレートを指していたら黙って素へ落ちてしまう。"""
    from app.workflows import get_image_spec

    for table in studio.IMAGE_QUALITY_WORKFLOWS.values():
        for base, variant in table.items():
            assert get_image_spec(base).id == base
            assert get_image_spec(variant).id == variant


# --------------------------------------------------------------------------
# 素材画像の画質（プロジェクトの image_megapixels / image_aspect_ratio /
# image_steps）
# --------------------------------------------------------------------------
#
# 動画側の megapixels / aspect_ratio / steps と同じ 3 項目を、素材の静止画用に
# 別で持つ（静止画に動画用の値を流用しないため）。null / 0 = 指定しない＝
# テンプレートの既定（MiniMax H3 Image は約 0.98MP）。

def test_the_image_render_settings_are_unset_by_default(env):
    project = make_project(env)
    assert project["image_megapixels"] is None
    assert project["image_aspect_ratio"] is None
    assert project["image_steps"] == 0
    listed = env.client.get("/api/studio/projects").json()[0]
    assert listed["image_megapixels"] is None
    assert listed["image_aspect_ratio"] is None
    assert listed["image_steps"] == 0


def test_the_image_render_settings_can_be_set_at_creation(env):
    project = make_project(
        env,
        image_megapixels=0.5,
        image_aspect_ratio="9:16 (Portrait Widescreen)",
        image_steps=8,
    )
    assert project["image_megapixels"] == 0.5
    assert project["image_aspect_ratio"] == "9:16 (Portrait Widescreen)"
    assert project["image_steps"] == 8


def test_the_image_render_settings_are_saved(env):
    project = make_project(env)
    updated = env.client.patch(
        f"/api/studio/projects/{project['id']}",
        json={
            "image_megapixels": 1.0,
            "image_aspect_ratio": "16:9 (Widescreen)",
            "image_steps": 12,
        },
    )
    assert updated.status_code == 200, updated.text
    saved = detail(env, project["id"])
    assert saved["image_megapixels"] == 1.0
    assert saved["image_aspect_ratio"] == "16:9 (Widescreen)"
    assert saved["image_steps"] == 12


def test_an_explicit_null_puts_the_image_settings_back_to_the_default(env):
    project = make_project(
        env, image_megapixels=1.0, image_aspect_ratio="1:1 (Square)", image_steps=8
    )
    cleared = env.client.patch(
        f"/api/studio/projects/{project['id']}",
        json={
            "image_megapixels": None,
            "image_aspect_ratio": None,
            "image_steps": 0,
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["image_megapixels"] is None
    assert cleared.json()["image_aspect_ratio"] is None
    assert cleared.json()["image_steps"] == 0


def test_not_sending_the_image_settings_leaves_them_alone(env):
    """送らなかった項目は今の値のまま（null 明示との区別）。"""
    project = make_project(
        env, image_megapixels=1.0, image_aspect_ratio="1:1 (Square)", image_steps=8
    )
    env.client.patch(f"/api/studio/projects/{project['id']}", json={"name": "別名"})
    saved = detail(env, project["id"])
    assert saved["image_megapixels"] == 1.0
    assert saved["image_aspect_ratio"] == "1:1 (Square)"
    assert saved["image_steps"] == 8


def test_out_of_range_image_steps_are_refused(env):
    project = make_project(env)
    for value in (-1, MAX_STEPS + 1):
        refused = env.client.patch(
            f"/api/studio/projects/{project['id']}", json={"image_steps": value}
        )
        assert refused.status_code == 400, refused.text
    assert detail(env, project["id"])["image_steps"] == 0


def test_a_project_without_the_image_setting_columns_reads_as_unset(env):
    """列を持たない（この設定より前に作られた）既存 DB は「未指定」として読む。"""
    import sqlite3

    project = make_project(
        env, image_megapixels=1.0, image_aspect_ratio="16:9 (Widescreen)",
        image_steps=8,
    )
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE studio_projects SET image_megapixels = 0,"
            " image_aspect_ratio = '', image_steps = 0 WHERE id = ?",
            (project["id"],),
        )
    saved = detail(env, project["id"])
    assert saved["image_megapixels"] is None
    assert saved["image_aspect_ratio"] is None
    assert saved["image_steps"] == 0


def test_the_image_settings_are_independent_from_the_video_ones(env):
    """画像側を設定しても動画側は動かない（その逆も同じ）。"""
    project = make_project(
        env,
        megapixels=0.4,
        aspect_ratio="16:9 (Widescreen)",
        steps=4,
        image_megapixels=1.0,
        image_aspect_ratio="1:1 (Square)",
        image_steps=20,
    )
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"]).status_code == 201
    payload = env.created[-1]
    # 動画ジョブには動画側の値だけが載る
    assert payload.megapixels == 0.4
    assert payload.aspect_ratio == "16:9 (Widescreen)"
    assert payload.steps == 4


def test_image_render_defaults_only_carries_the_set_values(env):
    """未設定（null / 0）の項目は入れない＝テンプレートの既定に任せる。"""
    from app.models import StudioProject

    project = StudioProject.model_validate(make_project(env))
    assert studio.image_render_defaults(project) == {}

    project = StudioProject.model_validate(
        make_project(
            env,
            code="IMG-2",
            image_megapixels=1.0,
            image_aspect_ratio="1:1 (Square)",
            image_steps=8,
        )
    )
    assert studio.image_render_defaults(project) == {
        "megapixels": 1.0,
        "aspect_ratio": "1:1 (Square)",
        "steps": 8,
    }


def test_image_render_defaults_ignores_the_video_settings(env):
    """動画側だけ設定してあっても、静止画には何も渡さない。"""
    from app.models import StudioProject

    project = StudioProject.model_validate(
        make_project(
            env, megapixels=0.4, aspect_ratio="16:9 (Widescreen)", steps=4
        )
    )
    assert studio.image_render_defaults(project) == {}


# --------------------------------------------------------------------------
# ラテントアップスケール（プロジェクトの latent_upscale）
# --------------------------------------------------------------------------
#
# ワークフロー id は変えず、ジョブの `selects[latent_upscale]` に落ちるつまみ。
# 効き方は **1 回ぶんの上書き > プロジェクト > 既定 ON** で、カスタムノードを
# 入れられない接続先（Comfy Cloud）では ON を頼んでも off に落ちる。

def test_latent_upscale_is_on_by_default(env):
    project = make_project(env)
    assert project["latent_upscale"] is True
    assert env.client.get("/api/studio/projects").json()[0]["latent_upscale"] is True


def test_latent_upscale_is_saved_as_a_project_setting(env):
    project = make_project(env)
    updated = env.client.patch(
        f"/api/studio/projects/{project['id']}", json={"latent_upscale": False}
    )
    assert updated.status_code == 200
    assert updated.json()["latent_upscale"] is False
    assert detail(env, project["id"])["latent_upscale"] is False


def test_latent_upscale_can_be_set_at_creation(env):
    assert make_project(env, latent_upscale=False)["latent_upscale"] is False


def test_a_wrong_typed_latent_upscale_is_refused(env):
    project = make_project(env)
    response = env.client.patch(
        f"/api/studio/projects/{project['id']}", json={"latent_upscale": "うん"}
    )
    assert response.status_code == 422


def test_the_project_latent_upscale_reaches_the_job(env, monkeypatch):
    """作品設定がそのままジョブの selects に載る（既定 ON / 明示 OFF）。"""
    _use_target(monkeypatch, "local")
    on = make_shot(env, make_project(env)["id"], prompt="A cat walks in.")
    assert render(env, on["id"]).status_code == 201
    assert env.created[-1].selects == {"latent_upscale": "on"}

    off = make_shot(
        env, make_project(env, code="OFF", latent_upscale=False)["id"],
        prompt="A cat walks in.",
    )
    assert render(env, off["id"]).status_code == 201
    assert env.created[-1].selects == {"latent_upscale": "off"}


@pytest.mark.parametrize(
    ("project_setting", "override", "expected"),
    [
        (True, False, "off"),
        (False, True, "on"),
        (True, None, "on"),
        (False, None, "off"),
    ],
)
def test_the_render_body_overrides_the_latent_upscale(
    env, monkeypatch, project_setting, override, expected
):
    """1 回ぶんの上書き > 作品設定。未指定（None）なら作品設定のまま。"""
    _use_target(monkeypatch, "local")
    project = make_project(env, latent_upscale=project_setting)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    body = {} if override is None else {"latent_upscale": override}
    assert render(env, shot["id"], body).status_code == 201
    assert env.created[-1].selects == {"latent_upscale": expected}
    # 作品設定は据え置き
    assert detail(env, project["id"])["latent_upscale"] is project_setting


def test_latent_upscale_falls_back_on_a_target_without_the_custom_nodes(
    env, monkeypatch
):
    """Comfy Cloud にはアップスケーラを入れられないので off に落として断らない。"""
    _use_target(monkeypatch, "comfy_cloud")
    project = make_project(env)  # latent_upscale は既定の True
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].selects == {"latent_upscale": "off"}

    preview = env.client.get(f"/api/studio/shots/{shot['id']}/prompt-preview").json()
    assert preview["latent_upscale"] is False
    assert "接続先" in preview["workflow_reason"]


def test_the_preview_shows_the_resolved_latent_upscale(env, monkeypatch):
    _use_target(monkeypatch, "local")
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    preview = env.client.get(f"/api/studio/shots/{shot['id']}/prompt-preview").json()
    assert preview["latent_upscale"] is True
    assert "接続先" not in preview["workflow_reason"]


def test_a_wrong_typed_latent_upscale_in_the_render_body_is_refused(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"], {"latent_upscale": "うん"}).status_code == 422
    assert env.created == []


# --------------------------------------------------------------------------
# 動画生成の画質（プロジェクトの megapixels / aspect_ratio）
# --------------------------------------------------------------------------
#
# 品質と違ってワークフローの選択には効かず、投入時の megapixels / aspect_ratio
# だけを決める。効き方は **Shot 個別 > プロジェクト > グローバル既定** の順で、
# 2 つはそれぞれ独立に解決される。

def test_the_project_quality_settings_are_unset_by_default(env):
    project = make_project(env)
    assert project["megapixels"] is None
    assert project["aspect_ratio"] is None
    listed = env.client.get("/api/studio/projects").json()[0]
    assert listed["megapixels"] is None
    assert listed["aspect_ratio"] is None


def test_the_project_quality_settings_are_saved(env):
    project = make_project(env)
    updated = env.client.patch(
        f"/api/studio/projects/{project['id']}",
        json={"megapixels": 1.0, "aspect_ratio": "16:9 (Widescreen)"},
    )
    assert updated.status_code == 200
    assert updated.json()["megapixels"] == 1.0
    assert updated.json()["aspect_ratio"] == "16:9 (Widescreen)"
    saved = detail(env, project["id"])
    assert saved["megapixels"] == 1.0
    assert saved["aspect_ratio"] == "16:9 (Widescreen)"


def test_a_null_puts_the_project_quality_settings_back_to_the_default(env):
    """null を**明示**したときだけ既定へ戻る（送らなければ今の値のまま）。"""
    project = make_project(
        env, megapixels=1.0, aspect_ratio="16:9 (Widescreen)"
    )
    kept = env.client.patch(
        f"/api/studio/projects/{project['id']}", json={"synopsis": "夜の話"}
    )
    assert kept.json()["megapixels"] == 1.0
    assert kept.json()["aspect_ratio"] == "16:9 (Widescreen)"

    cleared = env.client.patch(
        f"/api/studio/projects/{project['id']}",
        json={"megapixels": None, "aspect_ratio": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["megapixels"] is None
    assert cleared.json()["aspect_ratio"] is None


def test_a_project_without_the_quality_columns_reads_as_unset(env):
    """列を持たない（この設定より前に作られた）既存 DB は「未指定」として読む。"""
    import sqlite3

    project = make_project(env, megapixels=1.0, aspect_ratio="16:9 (Widescreen)")
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE studio_projects SET megapixels = 0, aspect_ratio = ''"
            " WHERE id = ?",
            (project["id"],),
        )
    saved = detail(env, project["id"])
    assert saved["megapixels"] is None
    assert saved["aspect_ratio"] is None


def test_the_project_megapixels_reach_the_job(env):
    project = make_project(env, megapixels=1.0)
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].megapixels == 1.0


def test_an_unset_project_megapixels_leaves_the_global_default(env):
    """未指定なら何も載せない = JobCreate の既定 0.4 のまま。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].megapixels == 0.4


def test_the_project_aspect_ratio_reaches_the_job(env):
    project = make_project(env, aspect_ratio="9:16 (Portrait Widescreen)")
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].aspect_ratio == "9:16 (Portrait Widescreen)"


def test_the_shot_settings_win_over_the_project_ones(env):
    project = make_project(env, megapixels=1.0, aspect_ratio="1:1 (Square)")
    shot = make_shot(
        env,
        project["id"],
        megapixels=0.5,
        aspect_ratio="16:9 (Widescreen)",
    )
    assert render(env, shot["id"]).status_code == 201
    payload = env.created[-1]
    assert payload.megapixels == 0.5
    assert payload.aspect_ratio == "16:9 (Widescreen)"


def test_the_two_project_settings_resolve_independently(env):
    """Shot が片方だけ言ったなら、もう片方はプロジェクトの値が残る。"""
    project = make_project(env, megapixels=1.0, aspect_ratio="1:1 (Square)")
    shot = make_shot(env, project["id"], aspect_ratio="16:9 (Widescreen)")
    assert render(env, shot["id"]).status_code == 201
    payload = env.created[-1]
    assert payload.aspect_ratio == "16:9 (Widescreen)"
    assert payload.megapixels == 1.0


def test_the_project_megapixels_are_not_clamped_to_the_workflow_default(env):
    """1.0 は MiniMax H3 の宣言（0.4MP）に切り下げられずに投入される。

    切り下げ（``app.jobs._fitted_megapixels``）が掛かるのは、別のワークフローから
    引き継いだ params を付け替えるとき（再実行 / 続きから）だけ。スタジオの投入は
    ``JobCreate`` を直に組むので、Shot 個別の指定と同じくそのまま渡る。
    """
    from app.workflows import get_video_spec

    assert get_video_spec("minimax_h3_t2v").default_megapixels == 0.4
    project = make_project(env, megapixels=1.0)
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].megapixels == 1.0


# --------------------------------------------------------------------------
# ステップ数（プロジェクト共通の設定）
# --------------------------------------------------------------------------
#
# 0 = 未指定 = テンプレートの既定のまま（turbo なら 4、normal / opt なら 20）。
# `steps` を宣言しているワークフローにだけ載る。

def test_the_project_steps_are_unset_by_default(env):
    project = make_project(env)
    assert project["steps"] == 0
    assert env.client.get("/api/studio/projects").json()[0]["steps"] == 0


def test_the_project_steps_are_saved(env):
    project = make_project(env)
    updated = env.client.patch(
        f"/api/studio/projects/{project['id']}", json={"steps": 12}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["steps"] == 12
    assert detail(env, project["id"])["steps"] == 12


def test_the_project_steps_can_be_set_at_creation(env):
    assert make_project(env, steps=8)["steps"] == 8


def test_zero_puts_the_project_steps_back_to_the_template_default(env):
    project = make_project(env, steps=8)
    cleared = env.client.patch(
        f"/api/studio/projects/{project['id']}", json={"steps": 0}
    )
    assert cleared.status_code == 200
    assert cleared.json()["steps"] == 0


def test_out_of_range_project_steps_are_refused(env):
    project = make_project(env)
    for value in (-1, MAX_STEPS + 1):
        refused = env.client.patch(
            f"/api/studio/projects/{project['id']}", json={"steps": value}
        )
        assert refused.status_code == 400, refused.text
    assert detail(env, project["id"])["steps"] == 0


def test_an_unreadable_steps_value_reads_as_unset():
    """列を持たない（この設定より前の）DB や壊れた値は 0 = 未指定として読む。"""
    assert studio.normalize_steps(None) == 0
    assert studio.normalize_steps("") == 0
    assert studio.normalize_steps(-5) == 0
    # 読み取りは断らず、上限で止めるだけ
    assert studio.normalize_steps(MAX_STEPS + 10) == MAX_STEPS


def test_the_project_steps_reach_the_job(env):
    project = make_project(env, steps=12)
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].steps == 12


def test_unset_project_steps_leave_the_template_default(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"]).status_code == 201
    # 0 = 何も載せない = テンプレートの既定のまま
    assert env.created[-1].steps == 0


# --------------------------------------------------------------------------
# テイク 1 回ぶんの上書き（POST /shots/{id}/render のボディ）
# --------------------------------------------------------------------------
#
# 送った項目だけがその 1 回に効き、Shot もプロジェクトも書き換えない。

def test_an_empty_render_body_behaves_like_before(env):
    project = make_project(env, megapixels=1.0, steps=12)
    shot = make_shot(env, project["id"], duration_seconds=7, seed=3)
    assert render(env, shot["id"], {}).status_code == 201
    payload = env.created[-1]
    assert payload.megapixels == 1.0
    assert payload.duration == 7
    assert payload.steps == 12
    assert payload.seed == 3


def test_the_render_body_overrides_the_resolution(env):
    project = make_project(env, megapixels=1.0, aspect_ratio="1:1 (Square)")
    shot = make_shot(env, project["id"], megapixels=0.5)
    response = render(
        env,
        shot["id"],
        {"megapixels": 0.8, "aspect_ratio": "16:9 (Widescreen)"},
    )
    assert response.status_code == 201, response.text
    payload = env.created[-1]
    assert payload.megapixels == 0.8
    assert payload.aspect_ratio == "16:9 (Widescreen)"
    # Shot もプロジェクトも据え置き
    saved = detail(env, project["id"])
    assert saved["megapixels"] == 1.0
    assert saved["aspect_ratio"] == "1:1 (Square)"
    assert saved["shots"][0]["megapixels"] == 0.5


def test_the_render_body_overrides_the_duration(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], duration_seconds=5)
    assert render(env, shot["id"], {"duration": 9}).status_code == 201
    assert env.created[-1].duration == 9
    # カットの尺は変わらない
    assert detail(env, project["id"])["shots"][0]["duration_seconds"] == 5


def test_the_render_body_overrides_the_steps(env):
    project = make_project(env, steps=12)
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"], {"steps": 30}).status_code == 201
    assert env.created[-1].steps == 30
    assert detail(env, project["id"])["steps"] == 12


def test_zero_steps_in_the_render_body_drop_the_project_setting(env):
    """0 は「テンプレートの既定のまま」の明示なので、作品の設定にも勝つ。"""
    project = make_project(env, steps=12)
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"], {"steps": 0}).status_code == 201
    assert env.created[-1].steps == 0


def test_the_render_body_pins_the_seed(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    assert shot["seed"] is None
    assert render(env, shot["id"], {"seed": 4242}).status_code == 201
    assert env.created[-1].seed == 4242
    # カットの設定はランダムのまま
    assert detail(env, project["id"])["shots"][0]["seed"] is None


def test_the_render_body_seed_wins_over_the_shot_one(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], seed=7)
    assert render(env, shot["id"], {"seed": 99}).status_code == 201
    assert env.created[-1].seed == 99
    # 送らなければ Shot の設定に落ちる
    assert render(env, shot["id"]).status_code == 201
    assert env.created[-1].seed == 7


def test_the_take_params_keep_the_values_actually_used(env):
    """生成記録（元ジョブの params）に、上書きした値がそのまま残る。"""
    project = make_project(env, steps=12, megapixels=1.0)
    shot = make_shot(env, project["id"], duration_seconds=5)
    take = render(
        env,
        shot["id"],
        {"steps": 30, "megapixels": 0.6, "duration": 8, "seed": 11},
    ).json()
    job = env.client.get(f"/api/jobs/{take['job_id']}").json()
    assert job["params"]["steps"] == 30
    assert job["params"]["megapixels"] == 0.6
    assert job["params"]["duration"] == 8
    assert job["params"]["seed"] == 11


@pytest.mark.parametrize(
    "body",
    [
        {"steps": -1},
        {"steps": MAX_STEPS + 1},
        {"duration": 0},
        {"duration": 16},
    ],
)
def test_an_out_of_range_render_body_is_refused(env, body):
    """範囲外は :class:`StudioError` -> 400（投入そのものが起きない）。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    refused = render(env, shot["id"], body)
    assert refused.status_code == 400, refused.text
    assert env.created == []


@pytest.mark.parametrize("body", [{"duration": "ごびょう"}, {"seed": "たね"}])
def test_a_render_body_with_the_wrong_type_is_refused(env, body):
    """型そのものが違うものは、他の body と同じく FastAPI が 422 で弾く。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    assert render(env, shot["id"], body).status_code == 422
    assert env.created == []


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


def test_canceling_a_take_stops_its_job(env, monkeypatch):
    monkeypatch.setattr(jobs, "_run_job_stages", _hang_until_cancelled)
    project = make_project(env)
    shot = make_shot(env, project["id"])
    take = render(env, shot["id"]).json()
    wait_for(env.client, take["job_id"], statuses=("queued", "prompting", "running"))

    canceled = env.client.post(f"/api/studio/takes/{take['id']}/cancel")
    assert canceled.status_code == 200, canceled.text
    body = canceled.json()
    assert body["status"] == "failed"
    assert body["job_status"] == "canceled"
    job = wait_for(env.client, take["job_id"], statuses=("canceled",))
    assert job["status"] == "canceled"
    row = detail(env, project["id"])["takes"][0]
    assert row["status"] == "failed"
    assert row["job_status"] == "canceled"


def test_deleting_a_rendering_take_cancels_its_job(env, monkeypatch):
    monkeypatch.setattr(jobs, "_run_job_stages", _hang_until_cancelled)
    project = make_project(env)
    shot = make_shot(env, project["id"])
    take = render(env, shot["id"]).json()
    wait_for(env.client, take["job_id"], statuses=("queued", "prompting", "running"))

    assert env.client.delete(f"/api/studio/takes/{take['id']}").status_code == 204
    job = wait_for(env.client, take["job_id"], statuses=("canceled",))
    assert job["status"] == "canceled"
    assert detail(env, project["id"])["takes"] == []


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
    assert env.client.post("/api/studio/takes/nope/cancel").status_code == 404
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
    assert payload.video_prompt.startswith(
        "integrated_multimodal_description: a calico cat sits."
    )


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
# Shot の並び（話 -> 場 -> カット）
# --------------------------------------------------------------------------

def test_shots_are_ordered_by_episode_then_scene_then_shot(env):
    """並びは話 -> 場 -> カット。未分類（場なし）は作品の末尾へ。"""
    project = make_project(env)
    first_episode = make_episode(env, project["id"], title="第1話")
    second_episode = make_episode(env, project["id"], title="第2話")
    opening = make_scene(env, first_episode["id"], title="屋台")
    closing = make_scene(env, second_episode["id"], title="路地")

    # わざと「後ろの話 -> 前の話」の順に作る（作成順では並ばない）。
    late = make_shot(env, project["id"], scene_id=closing["id"], title="ラスト")
    unfiled = make_shot(env, project["id"], title="未分類")
    early = make_shot(env, project["id"], scene_id=opening["id"], title="つかみ")
    second = make_shot(env, project["id"], scene_id=opening["id"], title="転")

    assert [row["id"] for row in detail(env, project["id"])["shots"]] == [
        early["id"], second["id"], late["id"], unfiled["id"]
    ]

    # 話を入れ替えれば Shot の並びも付いてくる。
    env.client.post(
        f"/api/studio/projects/{project['id']}/episodes/reorder",
        json={"ids": [second_episode["id"], first_episode["id"]]},
    )
    assert [row["id"] for row in detail(env, project["id"])["shots"]] == [
        late["id"], early["id"], second["id"], unfiled["id"]
    ]


def test_shot_sort_order_is_scoped_to_its_scene(env):
    """並び順は場の中で 0 から。未分類グループも 1 つのまとまりとして数える。"""
    project = make_project(env)
    episode = make_episode(env, project["id"])
    scene = make_scene(env, episode["id"])
    other = make_scene(env, episode["id"], title="路地")

    assert make_shot(env, project["id"], scene_id=scene["id"])["sort_order"] == 0
    assert make_shot(env, project["id"], scene_id=scene["id"])["sort_order"] == 1
    assert make_shot(env, project["id"], scene_id=other["id"])["sort_order"] == 0
    assert make_shot(env, project["id"])["sort_order"] == 0
    assert make_shot(env, project["id"])["sort_order"] == 1


def test_moving_a_shot_between_scenes_puts_it_at_the_end(env):
    project = make_project(env)
    episode = make_episode(env, project["id"])
    scene = make_scene(env, episode["id"])
    other = make_scene(env, episode["id"], title="路地")
    make_shot(env, project["id"], scene_id=other["id"])
    moved = make_shot(env, project["id"], scene_id=scene["id"])
    assert moved["sort_order"] == 0

    joined = env.client.patch(
        f"/api/studio/shots/{moved['id']}", json={"scene_id": other["id"]}
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()["sort_order"] == 1  # 引っ越し先の末尾

    make_shot(env, project["id"])  # 未分類グループに 1 本
    detached = env.client.patch(
        f"/api/studio/shots/{moved['id']}", json={"scene_id": None}
    )
    assert detached.json()["scene_id"] is None
    assert detached.json()["sort_order"] == 1


def test_unfiled_shots_keep_their_order_when_a_scene_is_deleted(env):
    """場を消したら、そこにいた Shot は未分類グループの末尾へ順序ごと移る。"""
    project = make_project(env)
    episode = make_episode(env, project["id"])
    scene = make_scene(env, episode["id"])
    unfiled = make_shot(env, project["id"], title="未分類")
    first = make_shot(env, project["id"], scene_id=scene["id"], title="A")
    second = make_shot(env, project["id"], scene_id=scene["id"], title="B")

    assert env.client.delete(f"/api/studio/scenes/{scene['id']}").status_code == 204
    shots = detail(env, project["id"])["shots"]
    assert [row["id"] for row in shots] == [
        unfiled["id"], first["id"], second["id"]
    ]
    assert [row["sort_order"] for row in shots] == [0, 1, 2]


def test_shots_can_be_reordered_inside_their_scene(env):
    project = make_project(env)
    episode = make_episode(env, project["id"])
    scene = make_scene(env, episode["id"])
    other = make_scene(env, episode["id"], title="路地")
    first = make_shot(env, project["id"], scene_id=scene["id"], title="A")
    second = make_shot(env, project["id"], scene_id=scene["id"], title="B")
    outsider = make_shot(env, project["id"], scene_id=other["id"], title="C")

    response = env.client.post(
        f"/api/studio/projects/{project['id']}/shots/reorder",
        json={"shot_ids": [second["id"], first["id"]]},
    )
    assert response.status_code == 200, response.text
    # 返るのは作品ぶん全部（並びは話 -> 場 -> カット）。
    assert [row["id"] for row in response.json()] == [
        second["id"], first["id"], outsider["id"]
    ]


def test_reorder_rejects_a_mix_of_scenes(env):
    project = make_project(env)
    episode = make_episode(env, project["id"])
    scene = make_scene(env, episode["id"])
    other = make_scene(env, episode["id"], title="路地")
    first = make_shot(env, project["id"], scene_id=scene["id"])
    make_shot(env, project["id"], scene_id=scene["id"])
    outsider = make_shot(env, project["id"], scene_id=other["id"])

    response = env.client.post(
        f"/api/studio/projects/{project['id']}/shots/reorder",
        json={"shot_ids": [outsider["id"], first["id"]]},
    )
    assert response.status_code == 400


def test_reorder_still_accepts_every_shot_of_the_project(env):
    """場をまたいだ全件送り（従来の呼び方）は場ごとに切り分けて書き戻す。"""
    project = make_project(env)
    episode = make_episode(env, project["id"])
    scene = make_scene(env, episode["id"])
    other = make_scene(env, episode["id"], title="路地")
    first = make_shot(env, project["id"], scene_id=scene["id"], title="A")
    second = make_shot(env, project["id"], scene_id=scene["id"], title="B")
    third = make_shot(env, project["id"], scene_id=other["id"], title="C")
    fourth = make_shot(env, project["id"], scene_id=other["id"], title="D")

    response = env.client.post(
        f"/api/studio/projects/{project['id']}/shots/reorder",
        json={
            "shot_ids": [
                fourth["id"], third["id"], second["id"], first["id"]
            ]
        },
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["id"] for row in rows] == [
        second["id"], first["id"], fourth["id"], third["id"]
    ]
    # 場をまたいだ移動は起きない（所属はそのまま）。
    assert {row["id"]: row["scene_id"] for row in rows} == {
        first["id"]: scene["id"],
        second["id"]: scene["id"],
        third["id"]: other["id"],
        fourth["id"]: other["id"],
    }


def test_reorder_rejects_unknown_shots(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    response = env.client.post(
        f"/api/studio/projects/{project['id']}/shots/reorder",
        json={"shot_ids": [shot["id"], "nope"]},
    )
    assert response.status_code == 400


def test_project_detail_can_be_narrowed_to_one_episode(env):
    """``episode_id`` を付けると場・Shot・Take はその話のぶんだけ。"""
    project = make_project(env)
    first_episode = make_episode(env, project["id"], title="第1話")
    second_episode = make_episode(env, project["id"], title="第2話")
    kept = make_scene(env, first_episode["id"], title="屋台")
    dropped = make_scene(env, second_episode["id"], title="路地")
    mine = make_shot(env, project["id"], scene_id=kept["id"])
    make_shot(env, project["id"], scene_id=dropped["id"])
    make_shot(env, project["id"])  # 未分類はどの話のものでもない
    make_asset(env, project["id"], "Neko")
    take = render(env, mine["id"]).json()

    response = env.client.get(
        f"/api/studio/projects/{project['id']}",
        params={"episode_id": first_episode["id"]},
    )
    assert response.status_code == 200, response.text
    context = response.json()
    assert [row["id"] for row in context["scenes"]] == [kept["id"]]
    assert [row["id"] for row in context["shots"]] == [mine["id"]]
    assert [row["id"] for row in context["takes"]] == [take["id"]]
    # 話タブと素材の表示に要るので、この 2 つは絞っても全件。
    assert [row["id"] for row in context["episodes"]] == [
        first_episode["id"], second_episode["id"]
    ]
    assert len(context["assets"]) == 1

    # 話を指定しなければ今までどおり作品まるごと。
    assert len(detail(env, project["id"])["shots"]) == 3


def test_project_detail_rejects_an_unknown_episode(env):
    project = make_project(env)
    other = make_project(env, name="別の作品")
    stranger = make_episode(env, other["id"])
    assert env.client.get(
        f"/api/studio/projects/{project['id']}", params={"episode_id": "nope"}
    ).status_code == 404
    assert env.client.get(
        f"/api/studio/projects/{project['id']}",
        params={"episode_id": stranger["id"]},
    ).status_code == 404


def test_projects_are_listed_by_last_update(env):
    """一覧は最後に触った順（作った順ではない）。"""
    import sqlite3

    from app import db

    project = make_project(env, name="古い作品")
    newer = make_project(env, name="新しい作品")
    assert [row["id"] for row in env.client.get("/api/studio/projects").json()] == [
        newer["id"], project["id"]
    ]

    # 同じ秒に作られると updated_at で差が付かないので、時刻は明示して確かめる。
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "UPDATE studio_projects SET updated_at = ? WHERE id = ?",
        ("2099-01-01T00:00:00+00:00", project["id"]),
    )
    conn.commit()
    conn.close()
    assert [row["id"] for row in env.client.get("/api/studio/projects").json()] == [
        project["id"], newer["id"]
    ]


def test_init_db_renumbers_project_wide_shot_orders_per_scene(env):
    """作品全体で 1 本だった並び順を、見た目の順序のまま場ごとに振り直す。"""
    import asyncio
    import sqlite3

    from app import db

    project = make_project(env)
    episode = make_episode(env, project["id"])
    scene = make_scene(env, episode["id"])
    other = make_scene(env, episode["id"], title="路地")
    first = make_shot(env, project["id"], scene_id=scene["id"], title="A")
    second = make_shot(env, project["id"], scene_id=other["id"], title="B")
    third = make_shot(env, project["id"], scene_id=scene["id"], title="C")
    unfiled = make_shot(env, project["id"], title="未分類")

    # 旧スキーマの状態（プロジェクト内で 0..n の通し番号）に戻す。
    conn = sqlite3.connect(db.DB_PATH)
    for order, shot in enumerate((first, second, third, unfiled)):
        conn.execute(
            "UPDATE studio_shots SET sort_order = ? WHERE id = ?",
            (order, shot["id"]),
        )
    conn.commit()
    conn.close()

    asyncio.run(db.init_db())

    conn = sqlite3.connect(db.DB_PATH)
    rows = dict(conn.execute("SELECT id, sort_order FROM studio_shots"))
    index_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'idx_studio_shots_project'"
    ).fetchone()[0]
    conn.close()
    assert rows[first["id"]] == 0
    assert rows[third["id"]] == 1     # 同じ場の中で 0..n に詰め直される
    assert rows[second["id"]] == 0    # 別の場は別のまとまり
    assert rows[unfiled["id"]] == 0   # 未分類グループも別のまとまり
    assert "scene_id" in index_sql

    # 見た目の順序（話 -> 場 -> カット）は変わらない。
    assert [row["id"] for row in detail(env, project["id"])["shots"]] == [
        first["id"], third["id"], second["id"], unfiled["id"]
    ]


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
    shot = make_shot(env, project["id"], title="決裂", prompt="A cat walks in.")
    env.client.patch(
        f"/api/studio/shots/{shot['id']}", json={"prompt": "B", "dialogue": "ねえ"}
    )

    rows = revisions(env, project["id"])
    assert [row["seq"] for row in rows] == [3, 2, 1]  # 新しい順
    assert rows[-1]["action"] == "プロジェクト『深夜のラーメン屋』を作成"
    assert rows[1]["action"] == "カット『決裂』を追加"
    # どの項目を変えたかまで説明に入る（差分を開かなくても見当がつく）
    assert rows[0]["action"] == "カット『決裂』を更新(dialogue, prompt)"
    assert all(row["actor"] == "user" for row in rows)
    assert "snapshot" not in rows[0]  # 一覧に中身は載せない


def test_a_revision_carries_the_whole_project(env):
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
    # asset_files は素材のリファレンス、timeline* は編集タブの EDL と
    # FX トラック（演出）。入らないのは実行状態（ジョブ）と書き出しだけ。
    assert set(snapshot) == {
        "project", "episodes", "scenes", "shots", "takes", "assets",
        "asset_files", "timelines", "timeline_tracks", "timeline_clips",
        "timeline_fx", "timeline_fx_events",
    }


def test_restoring_puts_the_script_and_the_deleted_takes_back(env):
    """脚本は書き戻し、スナップショットに載っている Take は元の状態へ戻す。

    Take は**消さない**（次のテスト）ので、ここで見るのは「載っている行が
    戻ること」まで。
    """
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    take = render(env, shot["id"]).json()
    # Take を採用したところまで戻せる（生成そのものは履歴を作らないので、
    # Take が載る最初のリビジョンは採用）。
    env.client.post(f"/api/studio/takes/{take['id']}/select")
    seq = revisions(env, project["id"])[0]["seq"]

    env.client.patch(f"/api/studio/shots/{shot['id']}", json={"prompt": "壊した"})
    doomed = make_shot(env, project["id"], prompt="あとから足した Shot")
    env.client.delete(f"/api/studio/takes/{take['id']}")

    restored = env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/{seq}/restore"
    )
    assert restored.status_code == 200, restored.text
    context = restored.json()
    assert [row["prompt"] for row in context["shots"]] == ["A cat walks in."]
    assert doomed["id"] not in [row["id"] for row in context["shots"]]
    # 消した Take も行ごと戻る（成果物のファイルは消していないので見られる）
    assert [row["id"] for row in context["takes"]] == [take["id"]]
    assert context["takes"][0]["status"] == "selected"
    assert context["shots"][0]["selected_take_id"] == take["id"]
    # 復元そのものも履歴になる
    assert revisions(env, project["id"])[0]["action"] == f"リビジョン {seq} を復元"


def test_restoring_never_deletes_a_take_it_does_not_know(env):
    """復元の意味論は「載っているものは戻す・知らないものは消さない」。

    生成はリビジョンを作らないので、脚本をひとつ戻しただけで直後に焼いた Take の
    目録が消えると事故になる（成果物は残るのに辿れなくなる）。
    """
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    first = render(env, shot["id"]).json()
    env.client.post(f"/api/studio/takes/{first['id']}/select")
    seq = revisions(env, project["id"])[0]["seq"]

    # このリビジョンのあとに焼いた Take（スナップショットには載っていない）
    env.client.patch(f"/api/studio/shots/{shot['id']}", json={"prompt": "書き直した"})
    later = render(env, shot["id"]).json()

    context = env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/{seq}/restore"
    ).json()
    assert [row["id"] for row in context["takes"]] == [first["id"], later["id"]]
    # 採用はスナップショット側の値に戻り、あとの Take は候補としてぶら下がる
    assert context["shots"][0]["selected_take_id"] == first["id"]
    assert {row["id"]: row["status"] for row in context["takes"]}[later["id"]] != (
        "selected"
    )


def test_a_restore_leaves_a_way_back_for_what_it_deletes(env):
    """復元は「消す」ことがあるので、触る前の状態を必ず 1 件残す。

    生成（Take）は履歴を作らないので、復元で消えたカットにぶら下がっていた
    Take は、この自動スナップショットが無いとどのリビジョンにも載らない。
    """
    project = make_project(env)
    make_shot(env, project["id"], title="残るカット")
    old = revisions(env, project["id"])[0]["seq"]

    later = make_shot(env, project["id"], title="あとのカット")
    take = render(env, later["id"]).json()

    env.client.post(f"/api/studio/projects/{project['id']}/revisions/{old}/restore")
    # 古い脚本に戻ったので、あとのカットは Take ごと消えている
    context = detail(env, project["id"])
    assert [row["title"] for row in context["shots"]] == ["残るカット"]
    assert context["takes"] == []

    # 直前の自動スナップショットへ戻せば、カットも Take も帰ってくる
    rows = revisions(env, project["id"])
    backup = next(
        row["seq"] for row in rows if row["action"] == studio.RESTORE_BACKUP_ACTION
    )
    back = env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/{backup}/restore"
    ).json()
    assert [row["title"] for row in back["shots"]] == ["残るカット", "あとのカット"]
    assert [row["id"] for row in back["takes"]] == [take["id"]]


def test_a_partial_restore_also_leaves_a_way_back(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], title="決裂", prompt="元のプロンプト")
    seq = revisions(env, project["id"])[0]["seq"]
    env.client.patch(f"/api/studio/shots/{shot['id']}", json={"prompt": "書き換えた"})

    env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/{seq}/restore",
        json={"entity": "shot", "id": shot["id"], "fields": ["prompt"]},
    )
    rows = revisions(env, project["id"])
    assert rows[1]["action"] == studio.RESTORE_BACKUP_ACTION
    # 自動スナップショットは戻す前（＝書き換えたあと）の値を持っている
    snapshot = env.client.get(
        f"/api/studio/projects/{project['id']}/revisions/{rows[1]['seq']}"
    ).json()["snapshot"]
    assert [row["prompt"] for row in snapshot["shots"]] == ["書き換えた"]


def test_an_old_snapshot_without_takes_leaves_them_alone(env):
    """takes を記録する前のスナップショットでは Take を消さない。"""
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    seq = revisions(env, project["id"])[0]["seq"]
    _drop_snapshot_key(env, project["id"], seq, "takes")
    take = render(env, shot["id"]).json()

    context = env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/{seq}/restore"
    ).json()
    assert [row["id"] for row in context["takes"]] == [take["id"]]


def _drop_snapshot_key(env, project_id: str, seq: int, key: str) -> None:
    """スナップショットからキーを 1 つ落とす（古い履歴の再現）。"""

    async def edit() -> None:
        async with db.get_db() as conn:
            async with conn.execute(
                "SELECT id, snapshot_json FROM studio_revisions"
                " WHERE project_id = ? AND seq = ?",
                (project_id, seq),
            ) as cur:
                row = await cur.fetchone()
            snapshot = json.loads(row["snapshot_json"])
            snapshot.pop(key, None)
            await conn.execute(
                "UPDATE studio_revisions SET snapshot_json = ? WHERE id = ?",
                (json.dumps(snapshot, ensure_ascii=False), row["id"]),
            )
            await conn.commit()

    asyncio.run(edit())


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
    assert env.client.get(
        f"/api/studio/projects/{project['id']}/revisions/999/diff"
    ).status_code == 404
    assert env.client.get("/api/studio/projects/nope/revisions").status_code == 404


# --------------------------------------------------------------------------
# リビジョンの差分と部分復元
# --------------------------------------------------------------------------

def diff(env, project_id: str, seq: int) -> dict:
    response = env.client.get(
        f"/api/studio/projects/{project_id}/revisions/{seq}/diff"
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_diff_shows_which_fields_an_update_moved(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], title="決裂", prompt="A cat walks in.")
    env.client.patch(
        f"/api/studio/shots/{shot['id']}",
        json={"prompt": "A dog walks in.", "dialogue": "行くよ"},
    )
    seq = revisions(env, project["id"])[0]["seq"]

    body = diff(env, project["id"], seq)
    assert body["seq"] == seq
    assert body["actor"] == "user"
    assert body["changes"] == [
        {
            "entity": "shot",
            "id": shot["id"],
            "name": "決裂",
            "op": "update",
            "fields": [
                {
                    "field": "dialogue",
                    "before": "",
                    "after": "行くよ",
                },
                {
                    "field": "prompt",
                    "before": "A cat walks in.",
                    "after": "A dog walks in.",
                },
            ],
        }
    ]


def test_the_diff_shows_creates_and_deletes(env):
    project = make_project(env)
    asset = make_metadata_asset(env, project["id"], "凛")
    created = diff(env, project["id"], revisions(env, project["id"])[0]["seq"])
    assert created["changes"] == [
        {"entity": "asset", "id": asset["id"], "name": "凛", "op": "create",
         "fields": []}
    ]

    episode = make_episode(env, project["id"], title="第1話")
    scene = make_scene(env, episode["id"], title="酒場・夜")
    env.client.delete(f"/api/studio/scenes/{scene['id']}")
    deleted = diff(env, project["id"], revisions(env, project["id"])[0]["seq"])
    assert deleted["changes"] == [
        {"entity": "scene", "id": scene["id"], "name": "酒場・夜", "op": "delete",
         "fields": []}
    ]


def test_the_first_revision_is_all_creates(env):
    project = make_project(env)
    body = diff(env, project["id"], 1)
    assert body["changes"] == [
        {"entity": "project", "id": project["id"], "name": project["name"],
         "op": "create", "fields": []}
    ]


def test_restoring_one_field_leaves_the_rest_alone(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], title="決裂", prompt="元のプロンプト")
    seq = revisions(env, project["id"])[0]["seq"]
    env.client.patch(
        f"/api/studio/shots/{shot['id']}",
        json={"prompt": "書き換えた", "dialogue": "残したい"},
    )

    restored = env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/{seq}/restore",
        json={"entity": "shot", "id": shot["id"], "fields": ["prompt"]},
    )
    assert restored.status_code == 200, restored.text
    back = restored.json()["shots"][0]
    assert back["prompt"] == "元のプロンプト"
    assert back["dialogue"] == "残したい"  # 指定していない項目は触らない
    assert revisions(env, project["id"])[0]["action"] == (
        f"カット『決裂』の prompt をリビジョン {seq} へ戻す"
    )


def test_restoring_one_row_brings_a_deleted_shot_back(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], title="決裂", prompt="元のプロンプト")
    seq = revisions(env, project["id"])[0]["seq"]
    env.client.delete(f"/api/studio/shots/{shot['id']}")

    restored = env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/{seq}/restore",
        json={"entity": "shot", "id": shot["id"]},
    )
    assert restored.status_code == 200, restored.text
    shots = restored.json()["shots"]
    assert [row["id"] for row in shots] == [shot["id"]]
    assert shots[0]["prompt"] == "元のプロンプト"
    assert revisions(env, project["id"])[0]["action"] == (
        f"カット『決裂』をリビジョン {seq} へ戻す"
    )


def test_a_partial_restore_of_something_unknown_is_a_400(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], title="決裂")
    seq = revisions(env, project["id"])[0]["seq"]
    missing = env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/{seq}/restore",
        json={"entity": "shot", "id": "sht_nope"},
    )
    assert missing.status_code == 400
    bad_field = env.client.post(
        f"/api/studio/projects/{project['id']}/revisions/{seq}/restore",
        json={"entity": "shot", "id": shot["id"], "fields": ["nope"]},
    )
    assert bad_field.status_code == 400


# --------------------------------------------------------------------------
# 楽観ロック（base_revision）
# --------------------------------------------------------------------------

def test_base_revision_lets_unrelated_edits_through(env):
    project = make_project(env)
    first = make_shot(env, project["id"], title="出会い")
    second = make_shot(env, project["id"], title="決裂")
    base = detail(env, project["id"])["revision_seq"]

    # 別のカットが動いても、こちらの更新はぶつからない
    env.client.patch(f"/api/studio/shots/{second['id']}", json={"prompt": "別のカット"})
    response = env.client.patch(
        f"/api/studio/shots/{first['id']}",
        json={"prompt": "こちらの変更", "base_revision": base},
    )
    assert response.status_code == 200, response.text
    assert response.json()["prompt"] == "こちらの変更"


def test_base_revision_refuses_when_the_same_shot_moved(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], title="決裂")
    base = detail(env, project["id"])["revision_seq"]

    env.client.patch(f"/api/studio/shots/{shot['id']}", json={"prompt": "誰かの変更"})
    response = env.client.patch(
        f"/api/studio/shots/{shot['id']}",
        json={"prompt": "あとから来た変更", "base_revision": base},
    )
    assert response.status_code == 409, response.text
    message = response.json()["detail"]
    assert "決裂" in message
    assert str(detail(env, project["id"])["revision_seq"]) in message
    assert "prompt" in message
    # 断られたので中身は変わっていない
    assert detail(env, project["id"])["shots"][0]["prompt"] == "誰かの変更"


def test_a_base_revision_from_the_future_is_a_400(env):
    """まだ存在しない連番は「過去に読んだ状態」ではないので素通しさせない。"""
    project = make_project(env)
    shot = make_shot(env, project["id"], title="決裂")
    current = detail(env, project["id"])["revision_seq"]

    response = env.client.patch(
        f"/api/studio/shots/{shot['id']}",
        json={"prompt": "未来から来た変更", "base_revision": current + 5},
    )
    assert response.status_code == 400, response.text
    assert str(current) in response.json()["detail"]
    assert detail(env, project["id"])["shots"][0]["prompt"] != "未来から来た変更"


def test_revisions_can_be_narrowed_to_one_shot(env):
    """「このカットの履歴」は説明文ではなく記録した id で引く。"""
    project = make_project(env)
    first = make_shot(env, project["id"], title="同じ名前")
    second = make_shot(env, project["id"], title="同じ名前")
    env.client.patch(f"/api/studio/shots/{first['id']}", json={"prompt": "こちら"})
    env.client.patch(f"/api/studio/shots/{second['id']}", json={"prompt": "あちら"})
    # 並べ替えは複数のカットに跨るので、どのカットの履歴にも出さない
    env.client.post(
        f"/api/studio/projects/{project['id']}/shots/reorder",
        json={"shot_ids": [second["id"], first["id"]]},
    )

    response = env.client.get(
        f"/api/studio/projects/{project['id']}/revisions",
        params={"entity_kind": "shot", "entity_id": first["id"]},
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert all(row["entity_id"] == first["id"] for row in rows)
    assert [row["action"] for row in rows] == [
        "カット『同じ名前』を更新(prompt)",
        "カット『同じ名前』を追加",
    ]
    # 改名しても履歴は付いてくる（id で引いているため）
    env.client.patch(f"/api/studio/shots/{first['id']}", json={"title": "改名した"})
    renamed = env.client.get(
        f"/api/studio/projects/{project['id']}/revisions",
        params={"entity_kind": "shot", "entity_id": first["id"]},
    ).json()
    assert len(renamed) == 3


def test_take_changes_show_up_in_the_shot_history(env):
    """Take の採用・削除はカットの中の出来事（そのカットの履歴に出す）。"""
    project = make_project(env)
    shot = make_shot(env, project["id"], title="決裂")
    take = render(env, shot["id"]).json()
    env.client.post(f"/api/studio/takes/{take['id']}/select")

    rows = env.client.get(
        f"/api/studio/projects/{project['id']}/revisions",
        params={"entity_kind": "shot", "entity_id": shot["id"]},
    ).json()
    assert rows[0]["action"] == "カット『決裂』の Take を採用"


def test_base_revision_is_not_sent_to_the_database(env):
    """``base_revision`` は書き換える項目ではない（列にしない）。"""
    project = make_project(env)
    response = env.client.patch(
        f"/api/studio/projects/{project['id']}",
        json={"synopsis": "夜食の話", "base_revision": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["synopsis"] == "夜食の話"


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
    assert payload.video_prompt.startswith(
        "integrated_multimodal_description: a calico cat sits down."
    )
    assert payload.reference_images == []


def test_a_file_backed_material_still_wins_r2v_next_to_a_metadata_one(env):
    project = make_project(env)
    make_asset(env, project["id"], "Neko", kind="image")
    make_metadata_asset(env, project["id"], "Yatai", prompt_caption="a ramen stall")
    shot = make_shot(env, project["id"], prompt="@Neko sits in @Yatai.")
    assert render(env, shot["id"]).status_code == 201

    payload = env.created[-1]
    assert payload.video_workflow == "minimax_h3_r2v"
    assert payload.video_prompt.startswith(
        "detailed_description: <Picture 1> sits in a ramen stall."
    )
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
    env.llm.reply = (
        "detailed_description: A cat walks into <Picture 1>. "
        "(S1) says: <d>[Japanese] いらっしゃい</d>"
    )
    project = make_project(env)
    make_asset(env, project["id"], "Yatai", kind="image")
    shot = make_shot(
        env, project["id"], prompt="猫が @Yatai に入ってくる。", dialogue="いらっしゃい"
    )
    take = render(env, shot["id"])
    assert take.status_code == 201, take.text

    payload = env.created[-1]
    assert payload.video_prompt.startswith(
        "detailed_description: 猫が <Picture 1> に入ってくる。"
    )
    assert "<d>[Japanese] いらっしゃい</d>" in payload.video_prompt
    assert env.extras[-1] == {"pending_translate": True}

    body = take.json()
    assert body["source_prompt"].startswith(
        "detailed_description: 猫が <Picture 1> に入ってくる。"
    )
    assert "<d>[Japanese] いらっしゃい</d>" in body["source_prompt"]
    assert body["warning"] == ""

    job = wait_for(env.client, body["job_id"])
    assert job["params"]["video_prompt"] == env.llm.reply
    assert not job["params"].get("pending_translate")

    # 変換の指示にはワークフローの書き方の規約とタグの取り扱いが入る
    instruction = env.llm.prompts[-1]
    assert "<Picture 1>" in instruction
    assert "reference tag" in instruction
    assert "original language" in instruction
    assert "<d>" in instruction
    assert "detailed_description" in instruction
    assert "complete official" in instruction
    assert "Do not invent dialogue" in instruction

    takes = env.client.get(f"/api/studio/shots/{shot['id']}/takes").json()
    assert takes[0]["source_prompt"].startswith(
        "detailed_description: 猫が <Picture 1> に入ってくる。"
    )
    assert takes[0]["prompt"] == env.llm.reply


def test_an_english_prompt_is_submitted_as_is(env):
    env.llm.error = None
    env.llm.reply = "should not be used"
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    take = render(env, shot["id"]).json()
    assert env.llm.prompts == []
    assert take["source_prompt"] == ""
    assert env.created[-1].video_prompt.startswith(
        "integrated_multimodal_description: A cat walks in."
    )
    assert env.extras[-1] is None


def test_translation_can_be_turned_off_per_project(env):
    env.llm.error = None
    env.llm.reply = "should not be used"
    project = make_project(env, auto_translate=False)
    shot = make_shot(env, project["id"], prompt="猫が入ってくる。")
    assert render(env, shot["id"]).status_code == 201
    assert env.llm.prompts == []
    assert env.created[-1].video_prompt.startswith(
        "integrated_multimodal_description: 猫が入ってくる。"
    )
    assert env.extras[-1] is None


def test_a_broken_grok_does_not_submit(env):
    project = make_project(env)  # fixture の既定は「grok が使えない」
    shot = make_shot(env, project["id"], prompt="猫が入ってくる。")
    response = render(env, shot["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert env.created
    job = wait_for(env.client, body["job_id"])
    assert job["status"] == "failed"
    error = job["error"] or ""
    assert "中止しました" in error
    assert "grok CLI が見つかりません" in error
    takes = env.client.get(f"/api/studio/shots/{shot['id']}/takes").json()
    assert takes[0]["status"] == "failed"


def test_an_empty_translation_does_not_submit(env):
    env.llm.error = None
    env.llm.reply = ""
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が入ってくる。")
    response = render(env, shot["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert env.created
    job = wait_for(env.client, body["job_id"])
    assert job["status"] == "failed"
    error = job["error"] or ""
    assert "中止しました" in error
    assert "空のプロンプト" in error
    takes = env.client.get(f"/api/studio/shots/{shot['id']}/takes").json()
    assert takes[0]["status"] == "failed"


def test_translation_uses_the_configured_grok_timeout(env, monkeypatch):
    """英訳の待ち時間は相談と同じ agent_grok_timeout。"""
    from app import config

    seen: list[float | None] = []
    env.llm.error = None
    env.llm.reply = "integrated_multimodal_description: A cat walks in."

    def capture(*args, **kwargs):
        if "timeout" in kwargs:
            seen.append(kwargs["timeout"])
        elif args:
            seen.append(args[0])
        else:
            seen.append(grok.DEFAULT_TIMEOUT)
        return env.llm

    monkeypatch.setattr(
        config,
        "_settings",
        config.load_settings().model_copy(update={"agent_grok_timeout": 900.0}),
    )
    monkeypatch.setattr(grok, "get_client", capture)
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が入ってくる。")
    take = render(env, shot["id"])
    assert take.status_code == 201
    wait_for(env.client, take.json()["job_id"])
    assert seen == [grok.configured_timeout()]
    assert seen == [900.0]


def test_extract_h3_document_drops_a_grok_preamble():
    raw = (
        "I'll load the official MiniMax H3 rewrite rules so the English "
        "document matches the required fields and constraints."
        "subject_definitions:\n<Subject 1> is in <Picture 1>.\n\n"
        "detailed_description:\n[Shot 1] A cat walks in.\n"
        "(S1) says: <d>[日本語] 痛ぁ！</d>"
    )
    text = studio.extract_h3_document(raw)
    assert text.startswith("subject_definitions:")
    assert "I'll load" not in text
    assert "<d>[Japanese] 痛ぁ！</d>" in text


def test_extract_h3_document_rejects_chatter_without_fields():
    assert studio.extract_h3_document(
        "I'll load the official MiniMax H3 rewrite rules and emit only "
        "the completed English document."
    ) == ""


def test_a_fenced_answer_is_unwrapped(env):
    env.llm.error = None
    env.llm.reply = "```\nintegrated_multimodal_description: A cat walks in.\n```"
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が入ってくる。")
    take = render(env, shot["id"])
    assert take.status_code == 201
    assert env.created[-1].video_prompt.startswith(
        "integrated_multimodal_description: 猫が入ってくる。"
    )
    job = wait_for(env.client, take.json()["job_id"])
    assert job["params"]["video_prompt"] == (
        "integrated_multimodal_description: A cat walks in."
    )


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
    env.llm.error = None
    env.llm.reply = "detailed_description: A mechanic walks into the hangar at dawn."
    take = render(env, context["shots"][0]["id"])
    assert take.status_code == 201, take.text
    assert env.created[-1].video_workflow == "minimax_h3_t2v"
    assert "young Japanese mechanic" in take.json()["source_prompt"]
    job = wait_for(env.client, take.json()["job_id"])
    assert job["params"]["video_prompt"] == env.llm.reply


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
    prompt = body["prompt"]
    assert prompt.startswith("integrated_multimodal_description: A cat walks in.")
    assert "The camera slow dolly in." in prompt
    assert "<d>[English] Good evening.</d>" in prompt
    assert "overall_soundscape: rain on the roof" in prompt
    assert "non_diegetic_music: lonely piano" in prompt
    assert "Camera:" not in prompt
    assert "Audio:" not in prompt
    assert prompt.endswith(studio.EXCLUSION_SENTENCE)


def test_the_preview_lists_the_attached_references(env):
    project = make_project(env)
    make_asset(env, project["id"], "Neko", kind="image")
    make_asset(env, project["id"], "Koe", kind="audio")
    shot = make_shot(env, project["id"], prompt="@Neko meows, @Koe answers.")

    body = preview(env, shot["id"])
    assert body["workflow"] == "minimax_h3_r2v"
    assert body["prompt"].startswith(
        "detailed_description: <Picture 1> meows, <Audio 1> answers."
    )
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
    assert body["prompt"].startswith(
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced."
    )
    assert "integrated_multimodal_description: a calico cat sits down." in body["prompt"]
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
    assert body["prompt"].startswith(
        "integrated_multimodal_description: 猫が歩いてくる。"
    )  # 訳す前の姿
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


# --------------------------------------------------------------------------
# 事前英訳キャッシュ（english_prompt / english_source）
# --------------------------------------------------------------------------

def _translate(env, shot_id: str):
    return env.client.post(f"/api/studio/shots/{shot_id}/translate")


def wait_translated(env, shot_id, timeout=5.0) -> dict:
    """preview をポーリングし、英訳が終わったらそのプレビューを返す。"""
    deadline = time.time() + timeout
    body: dict = {}
    while time.time() < deadline:
        body = preview(env, shot_id)
        status = body.get("english_status") or ""
        if status != "translating" and (body.get("english_prompt") or status == "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(
        f"translate {shot_id} stuck in {body.get('english_status')!r}"
    )


def test_translate_saves_the_assembled_english(env):
    env.llm.error = None
    env.llm.reply = (
        "integrated_multimodal_description: A cat walks in.\n"
        "No text, subtitles, logos or watermarks."
    )
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    assembled = preview(env, shot["id"])["prompt"]

    response = _translate(env, shot["id"])
    assert response.status_code == 200, response.text
    wait_translated(env, shot["id"])
    stored = detail(env, project["id"])["shots"][0]
    assert stored["english_prompt"] == env.llm.reply
    assert stored["english_source"] == assembled
    assert stored["prompt"] == "猫が歩いてくる。"
    assert assembled in env.llm.prompts[-1]


def test_translate_returns_before_grok_finishes(env):
    env.llm.error = None
    env.llm.reply = "integrated_multimodal_description: ENGLISH CACHE"
    env.llm.hold = threading.Event()
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    try:
        response = _translate(env, shot["id"])
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["english_status"] == "translating"
        assert body["english_prompt"] == ""
        assert not env.llm.hold.is_set()

        env.llm.hold.set()
        done = wait_translated(env, shot["id"])
        assert done["english_prompt"] == "integrated_multimodal_description: ENGLISH CACHE"
        assert done["english_status"] == ""
    finally:
        env.llm.hold.set()


def test_a_usable_english_cache_is_submitted_without_pending_translate(env):
    env.llm.error = None
    env.llm.reply = "integrated_multimodal_description: ENGLISH CACHE"
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    assembled = preview(env, shot["id"])["prompt"]
    assert _translate(env, shot["id"]).status_code == 200
    wait_translated(env, shot["id"])
    env.llm.prompts.clear()

    take = render(env, shot["id"])
    assert take.status_code == 201, take.text
    assert env.extras[-1] is None
    assert env.created[-1].video_prompt == "integrated_multimodal_description: ENGLISH CACHE"
    assert env.llm.prompts == []
    body = take.json()
    assert body["prompt"] == "integrated_multimodal_description: ENGLISH CACHE"
    assert body["source_prompt"] == assembled


def test_a_stale_english_cache_falls_back_to_pending_translate(env):
    env.llm.error = None
    env.llm.reply = "integrated_multimodal_description: ENGLISH CACHE"
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    assert _translate(env, shot["id"]).status_code == 200
    wait_translated(env, shot["id"])

    patched = env.client.patch(
        f"/api/studio/shots/{shot['id']}", json={"prompt": "犬が走ってくる。"}
    )
    assert patched.status_code == 200
    take = render(env, shot["id"])
    assert take.status_code == 201, take.text
    assert env.extras[-1] == {"pending_translate": True}


def test_changing_seed_alone_keeps_the_english_cache(env):
    env.llm.error = None
    env.llm.reply = "integrated_multimodal_description: ENGLISH CACHE"
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    assert _translate(env, shot["id"]).status_code == 200
    wait_translated(env, shot["id"])
    env.llm.prompts.clear()

    patched = env.client.patch(
        f"/api/studio/shots/{shot['id']}", json={"seed": 42}
    )
    assert patched.status_code == 200
    take = render(env, shot["id"])
    assert take.status_code == 201, take.text
    assert env.extras[-1] is None
    assert env.created[-1].video_prompt == "integrated_multimodal_description: ENGLISH CACHE"
    assert env.created[-1].seed == 42
    assert env.llm.prompts == []


def test_auto_translate_off_still_submits_a_usable_english_cache(env):
    env.llm.error = None
    env.llm.reply = "integrated_multimodal_description: ENGLISH CACHE"
    project = make_project(env, auto_translate=False)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    assert _translate(env, shot["id"]).status_code == 200
    wait_translated(env, shot["id"])
    env.llm.prompts.clear()

    take = render(env, shot["id"])
    assert take.status_code == 201, take.text
    assert env.extras[-1] is None
    assert env.created[-1].video_prompt == "integrated_multimodal_description: ENGLISH CACHE"
    assert env.llm.prompts == []


def test_no_cache_and_japanese_still_sets_pending_translate(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    take = render(env, shot["id"])
    assert take.status_code == 201, take.text
    assert env.extras[-1] == {"pending_translate": True}


def test_a_failed_translate_does_not_write_the_cache(env):
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    response = _translate(env, shot["id"])
    assert response.status_code == 200, response.text
    body = wait_translated(env, shot["id"])
    assert body["english_status"] == "failed"
    assert body["english_prompt"] == ""
    assert "保存しませんでした" in (body.get("english_error") or "")
    stored = detail(env, project["id"])["shots"][0]
    assert stored["english_prompt"] == ""
    assert stored["english_source"] == ""
    assert stored["english_status"] == "failed"
    assert "保存しませんでした" in stored["english_error"]


def test_preview_reports_a_usable_english_cache(env):
    env.llm.error = None
    env.llm.reply = "integrated_multimodal_description: ENGLISH CACHE"
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    assert _translate(env, shot["id"]).status_code == 200
    wait_translated(env, shot["id"])

    body = preview(env, shot["id"])
    assert body["will_translate"] is False
    assert body["english_stale"] is False
    assert body["english_prompt"] == "integrated_multimodal_description: ENGLISH CACHE"


def test_preview_marks_a_stale_english_cache(env):
    env.llm.error = None
    env.llm.reply = "integrated_multimodal_description: ENGLISH CACHE"
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    assert _translate(env, shot["id"]).status_code == 200
    wait_translated(env, shot["id"])
    env.client.patch(
        f"/api/studio/shots/{shot['id']}", json={"prompt": "犬が走ってくる。"}
    )

    body = preview(env, shot["id"])
    assert body["will_translate"] is True
    assert body["english_stale"] is True
    assert body["english_prompt"] == "integrated_multimodal_description: ENGLISH CACHE"


def test_clearing_english_prompt_clears_the_source_too(env):
    env.llm.error = None
    env.llm.reply = "integrated_multimodal_description: ENGLISH CACHE"
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    assert _translate(env, shot["id"]).status_code == 200
    wait_translated(env, shot["id"])

    patched = env.client.patch(
        f"/api/studio/shots/{shot['id']}", json={"english_prompt": ""}
    )
    assert patched.status_code == 200
    assert patched.json()["english_prompt"] == ""
    assert patched.json()["english_source"] == ""
    assert patched.json()["english_status"] == ""
    assert patched.json()["english_error"] == ""

    take = render(env, shot["id"])
    assert take.status_code == 201, take.text
    assert env.extras[-1] == {"pending_translate": True}


def test_an_english_only_prompt_is_cached_without_grok(env):
    env.llm.error = None
    env.llm.reply = "should not be used"
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="A cat walks in.")
    assembled = preview(env, shot["id"])["prompt"]

    response = _translate(env, shot["id"])
    assert response.status_code == 200, response.text
    assert env.llm.prompts == []
    assert response.json()["english_prompt"] == assembled
    assert response.json()["english_source"] == assembled


def test_translating_an_unknown_shot_is_a_404(env):
    assert env.client.post("/api/studio/shots/nope/translate").status_code == 404


def test_preview_keeps_cached_english_when_assembly_fails(env):
    env.llm.error = None
    env.llm.reply = "integrated_multimodal_description: ENGLISH CACHE"
    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    assert _translate(env, shot["id"]).status_code == 200
    wait_translated(env, shot["id"])
    env.client.patch(
        f"/api/studio/shots/{shot['id']}", json={"prompt": "@Inu が吠える。"}
    )

    body = preview(env, shot["id"])
    assert body["error"]
    assert body["english_prompt"] == "integrated_multimodal_description: ENGLISH CACHE"
    assert body["english_stale"] is True


# --------------------------------------------------------------------------
# compose_prompt（公式 MiniMax H3 契約）
# --------------------------------------------------------------------------

def _compose_shot(**overrides) -> StudioShot:
    fields = {
        "id": "s1",
        "project_id": "p1",
        "prompt": "A cat walks in.",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    fields.update(overrides)
    return StudioShot(**fields)


def test_compose_prompt_wraps_a_bare_t2v_body():
    shot = _compose_shot(
        camera="handheld, low angle",
        dialogue="いらっしゃい",
        soundscape="rain on the awning",
        bgm="slow jazz",
    )
    text = studio.compose_prompt(
        shot, "A cat walks into a ramen shop.", workflow="minimax_h3_t2v"
    )
    assert text.startswith("integrated_multimodal_description:")
    assert "The camera handheld, low angle." in text
    assert "(S1) says: <d>[Japanese] いらっしゃい</d>" in text
    assert "overall_soundscape: rain on the awning" in text
    assert "non_diegetic_music: slow jazz" in text
    assert text.endswith(studio.EXCLUSION_SENTENCE)
    assert "Camera:" not in text
    assert "Audio:" not in text


def test_compose_prompt_does_not_double_wrap_official_fields():
    body = (
        "integrated_multimodal_description: [Shot 1] Live-action, cinematic, "
        "a medium shot frames a cat in a doorway."
    )
    shot = _compose_shot(soundscape="rain on the awning")
    text = studio.compose_prompt(shot, body, workflow="minimax_h3_t2v")
    assert text.count("integrated_multimodal_description:") == 1
    assert text.startswith(body)
    assert "overall_soundscape: rain on the awning" in text
    assert "non_diegetic_music: N/A" in text


def test_compose_prompt_i2v_alignment_and_r2v_wrap():
    shot = _compose_shot()
    i2v = studio.compose_prompt(shot, "The cat sits.", workflow="minimax_h3_i2v")
    assert i2v.startswith(
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced."
    )
    assert "integrated_multimodal_description: The cat sits." in i2v

    turbo = studio.compose_prompt(
        shot, "The cat sits.", workflow="minimax_h3_i2v_turbo"
    )
    assert turbo.startswith(
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced."
    )

    r2v = studio.compose_prompt(
        shot, "<Picture 1> walks in.", workflow="minimax_h3_r2v"
    )
    assert r2v.startswith("detailed_description: <Picture 1> walks in.")
    assert "For the target video" not in r2v

    context = studio.compose_prompt(
        shot, "<Picture 1> walks in.", workflow="minimax_h3_r2v_context"
    )
    assert context.startswith("detailed_description: <Picture 1> walks in.")


def test_compose_prompt_strips_wrapping_quotes_and_skips_existing_camera():
    shot = _compose_shot(
        camera="Push In at slow speed",
        dialogue='"いらっしゃい"',
    )
    body = (
        "integrated_multimodal_description: [Shot 1] Live-action, cinematic, "
        "a medium shot frames the doorway. The camera holds a static shot."
    )
    text = studio.compose_prompt(shot, body, workflow="minimax_h3_t2v")
    assert "The camera Push In" not in text
    assert "<d>[Japanese] いらっしゃい</d>" in text
    assert '"いらっしゃい"' not in text


def test_compose_prompt_keeps_camera_when_body_only_cuts():
    shot = _compose_shot(camera="Push In at slow speed")
    body = (
        "integrated_multimodal_description: [Shot 1] Live-action, cinematic, "
        "a medium shot frames the doorway. [Shot 2] At 00:03.500, the camera "
        "cuts to a close-up of the counter."
    )
    text = studio.compose_prompt(shot, body, workflow="minimax_h3_t2v")
    assert "The camera Push In at slow speed." in text
