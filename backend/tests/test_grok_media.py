"""Grok Build CLI 経由の画像生成（SPEC §5.2 / issue #21）。

CLI は一切起動しない: :func:`app.grok_media._exec` を偽物に差し替え、「終了コード /
標準出力 / ファイルを置いたかどうか」を組み合わせて 4 段構えの判定を確かめる。
実プロセスを使うのは ``XAI_API_KEY`` を env から外していることの確認だけ。
"""

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import backends, config, db, grok_media, jobs, nsfw, workflows
from app.backends import BackendStatus
from app.main import app
from app.models import GenerationParams, Settings
from app.workflows import GROK_IMAGINE

PROMPT = "A fisherman mending a net on a pier at dawn"


# --------------------------------------------------------------------------
# 偽の grok CLI
# --------------------------------------------------------------------------

def instruction_of(argv: list[str]) -> str:
    return argv[argv.index("-p") + 1]


def dest_of(argv: list[str]) -> Path:
    """指示文に書き込んだ保存先（``  /abs/path`` の行）。"""
    match = re.search(r"^\s+(/\S+)$", instruction_of(argv), re.M)
    assert match, instruction_of(argv)
    return Path(match.group(1))


def answer(text: str, *, code: int = 0, err: str = "") -> tuple[int, str, str]:
    """``--output-format json`` の応答（フィールド構造は二次情報）。"""
    return code, json.dumps({"text": text, "stopReason": "end_turn"}), err


class FakeCli:
    """``grok_media._exec`` の代役。用意した手順を順に演じる。

    手順は ``(argv, cwd) -> (returncode, stdout, stderr)`` の関数で、尽きたら
    最後のものを使い回す（リトライをそのまま書けるように）。
    """

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls: list[tuple[list[str], Path, float]] = []

    async def __call__(self, argv, cwd, seconds):
        self.calls.append((list(argv), Path(cwd), seconds))
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        return step(list(argv), Path(cwd))

    @property
    def runs(self) -> int:
        return len(self.calls)


def saves_and_reports(argv, cwd):
    """素直な成功: 指示どおりに置いて ``OK <パス>`` を返す。"""
    dest = dest_of(argv)
    dest.write_bytes(b"\x89PNG generated")
    return answer(f"OK {dest}")


def saves_without_reporting(argv, cwd):
    """ファイルは置いたが合図を忘れた（言葉ではなくファイルを信じる）。"""
    dest_of(argv).write_bytes(b"\x89PNG generated")
    return answer("Done! I created the picture for you.")


def reports_without_saving(argv, cwd):
    """「作った」と言うだけでファイルが無い（③ でしか検出できない）。"""
    return answer(f"OK {dest_of(argv)}")


def refuses(argv, cwd):
    return answer("FAILED the prompt was blocked by moderation")


@pytest.fixture
def media_env(tmp_path, monkeypatch):
    """作業ディレクトリと設定を隔離した状態（実行だけを差し替える）。"""
    workdir = tmp_path / "grok-media-workdir"
    monkeypatch.setattr(grok_media, "workdir", lambda: workdir)
    monkeypatch.setattr(config, "_settings", Settings())
    return workdir


def run(request, cli, monkeypatch) -> Path:
    monkeypatch.setattr(grok_media, "_exec", cli)
    return asyncio.run(grok_media.generate(request))


def image_request(dest: Path) -> grok_media.MediaRequest:
    return grok_media.MediaRequest(
        media="image",
        prompt=PROMPT,
        dest=dest,
        aspect_ratio="16:9 (Widescreen)",
        width=1344,
        height=768,
    )


# --------------------------------------------------------------------------
# 指示文
# --------------------------------------------------------------------------

def test_the_instruction_carries_the_path_the_ratio_and_the_contract(tmp_path):
    request = image_request(tmp_path / "image.png")
    text = request.instruction

    assert str(tmp_path / "image.png") in text
    assert "16:9 (Widescreen)" in text
    assert "1344x768" in text
    # 合図の約束事（4 段構えの ②）
    assert f"`OK {tmp_path / 'image.png'}`" in text
    assert "`FAILED <one-line reason>`" in text
    # 承認待ちでハングしないためのフラグ（issue #21）
    argv = grok_media._argv(request)
    assert "--always-approve" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--no-auto-update" in argv


def test_the_request_is_built_from_the_manifest(media_env, tmp_path):
    params = GenerationParams(
        mode="image_only",
        job_id="job-1",
        image_prompt=PROMPT,
        aspect_ratio="1:1 (Square)",
        megapixels=1.0,
    )
    request = grok_media.build_request(GROK_IMAGINE, params, tmp_path / "image.png")

    assert request.media == "image"
    assert request.prompt == PROMPT
    assert request.aspect_ratio == "1:1 (Square)"
    # 解像度は ComfyUI 側と同じ計算（希望として指示文に書くだけ）
    assert (request.width, request.height) == (1024, 1024)
    assert request.as_dict()["dest"] == str(tmp_path / "image.png")


# --------------------------------------------------------------------------
# 成否の判定（4 段構え）
# --------------------------------------------------------------------------

def test_a_saved_file_and_an_ok_signal_succeed(media_env, tmp_path, monkeypatch):
    dest = tmp_path / "out" / "image.png"
    cli = FakeCli(saves_and_reports)

    saved = run(image_request(dest), cli, monkeypatch)

    assert saved == dest and dest.is_file()
    assert cli.runs == 1
    # 実行は専用の作業ディレクトリで（プロンプト用とは別、SPEC §5.2）
    assert cli.calls[0][1] == media_env


def test_a_missing_signal_does_not_discard_the_file(media_env, tmp_path, monkeypatch):
    """③ ファイルが在ることが最終的な根拠（合図は補助）。"""
    dest = tmp_path / "image.png"

    saved = run(image_request(dest), FakeCli(saves_without_reporting), monkeypatch)

    assert saved == dest and dest.is_file()


def test_an_ok_signal_without_a_file_fails(media_env, tmp_path, monkeypatch):
    """「生成した」と言われてもファイルが無ければ失敗（1 回やり直してから）。"""
    cli = FakeCli(reports_without_saving)

    with pytest.raises(grok_media.GrokMediaError) as caught:
        run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert "見つかりません" in str(caught.value)
    assert cli.runs == 2


def test_a_file_left_in_generated_media_is_recovered(media_env, tmp_path, monkeypatch):
    """④ 既定の置き場に置かれてしまった生成物を拾う保険。"""
    def saves_in_the_default_place(argv, cwd):
        folder = cwd / grok_media.GENERATED_MEDIA_RELPATH
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "grok-image-1.png").write_bytes(b"\x89PNG recovered")
        return answer("Saved the image.")

    dest = tmp_path / "image.png"

    saved = run(image_request(dest), FakeCli(saves_in_the_default_place), monkeypatch)

    assert saved == dest
    assert dest.read_bytes() == b"\x89PNG recovered"


def test_a_stale_file_in_generated_media_is_ignored(media_env, tmp_path, monkeypatch):
    """前回の残りを今回の成果物と取り違えない（mtime で切る）。"""
    folder = media_env / grok_media.GENERATED_MEDIA_RELPATH
    folder.mkdir(parents=True)
    stale = folder / "old.png"
    stale.write_bytes(b"\x89PNG old")
    import os

    os.utime(stale, (1_000_000, 1_000_000))

    with pytest.raises(grok_media.GrokMediaError):
        run(image_request(tmp_path / "image.png"), FakeCli(reports_without_saving),
            monkeypatch)


def test_a_stale_destination_is_not_mistaken_for_a_result(media_env, tmp_path,
                                                          monkeypatch):
    dest = tmp_path / "image.png"
    dest.write_bytes(b"\x89PNG from a previous run")

    with pytest.raises(grok_media.GrokMediaError):
        run(image_request(dest), FakeCli(reports_without_saving), monkeypatch)

    assert not dest.exists()


def test_a_non_zero_exit_reports_the_output(media_env, tmp_path, monkeypatch):
    cli = FakeCli(lambda argv, cwd: (1, "", "boom: tool crashed"))

    with pytest.raises(grok_media.GrokMediaError) as caught:
        run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert "exit 1" in str(caught.value)
    assert "boom: tool crashed" in str(caught.value)


def test_an_unauthenticated_cli_says_how_to_sign_in(media_env, tmp_path, monkeypatch):
    cli = FakeCli(lambda argv, cwd: (1, "", "Error: not authenticated"))

    with pytest.raises(grok_media.GrokMediaError) as caught:
        run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert "--device-auth" in str(caught.value)


def test_a_timeout_is_reported_as_a_generation_failure(media_env, tmp_path,
                                                       monkeypatch):
    """タイムアウトは :class:`app.grok.LLMError`。ジョブ向けの型に揃える。"""
    from app import grok

    async def times_out(argv, cwd, seconds, env=None):
        raise grok.LLMError("grok CLI が 300 秒以内に応答しませんでした（タイムアウト）")

    monkeypatch.setattr(grok, "_exec", times_out)

    with pytest.raises(grok_media.GrokMediaError) as caught:
        asyncio.run(grok_media.generate(image_request(tmp_path / "image.png")))

    assert "タイムアウト" in str(caught.value)


def test_the_timeout_comes_from_the_settings(media_env, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_settings", Settings(grok_media_timeout=42.0))
    cli = FakeCli(saves_and_reports)

    run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert cli.calls[0][2] == 42.0


# --------------------------------------------------------------------------
# リトライとクォータ
# --------------------------------------------------------------------------

def test_a_refusal_is_retried_once(media_env, tmp_path, monkeypatch):
    dest = tmp_path / "image.png"
    cli = FakeCli(refuses, saves_and_reports)

    saved = run(image_request(dest), cli, monkeypatch)

    assert saved == dest
    assert cli.runs == 2


def test_two_refusals_report_the_reason(media_env, tmp_path, monkeypatch):
    cli = FakeCli(refuses)

    with pytest.raises(grok_media.GrokMediaError) as caught:
        run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert "moderation" in str(caught.value)
    assert cli.runs == 2


def test_a_used_up_quota_is_not_retried(media_env, tmp_path, monkeypatch):
    cli = FakeCli(lambda argv, cwd: (1, "", "429 rate limit exceeded, try again later"))

    with pytest.raises(grok_media.GrokQuotaError) as caught:
        run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert "サブスク枠" in str(caught.value)
    assert cli.runs == 1  # やり直しても無駄


def test_a_quota_message_in_the_signal_is_recognised(media_env, tmp_path, monkeypatch):
    cli = FakeCli(lambda argv, cwd: answer("FAILED daily quota exhausted"))

    with pytest.raises(grok_media.GrokQuotaError):
        run(image_request(tmp_path / "image.png"), cli, monkeypatch)


# --------------------------------------------------------------------------
# 従量課金へのフォールバック防止（SPEC §4.1）
# --------------------------------------------------------------------------

def test_the_api_key_is_dropped_from_the_environment(monkeypatch):
    monkeypatch.setenv(grok_media.API_KEY_ENV, "xai-secret")
    monkeypatch.setenv("PATH_MARKER_FOR_TEST", "kept")

    assert grok_media.API_KEY_ENV not in grok_media.clean_env()
    assert grok_media.clean_env()["PATH_MARKER_FOR_TEST"] == "kept"


def test_the_child_process_never_sees_the_api_key(tmp_path, monkeypatch):
    """実プロセスで確認する（env の受け渡しは :func:`app.grok._exec` の仕事）。"""
    monkeypatch.setenv(grok_media.API_KEY_ENV, "xai-secret")
    code, out, _ = asyncio.run(
        grok_media._exec(
            [
                "python3",
                "-c",
                f"import os; print(os.environ.get('{grok_media.API_KEY_ENV}', 'ABSENT'))",
            ],
            tmp_path,
            10.0,
        )
    )
    assert code == 0
    assert out.strip() == "ABSENT"


# --------------------------------------------------------------------------
# 可用性（SPEC §5.2、issue #21 のコメント）
# --------------------------------------------------------------------------

@pytest.fixture
def cli_installed(monkeypatch, tmp_path):
    """``grok`` が入っていて認証済みに見える状態。"""
    monkeypatch.setattr(config, "_settings", Settings())
    monkeypatch.setattr(grok_media.shutil, "which", lambda cmd: "/usr/local/bin/grok")
    auth = tmp_path / ".grok" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(grok_media, "auth_path", lambda: auth)
    return auth


def test_the_backend_is_available_once_the_cli_is_authenticated(cli_installed):
    status = asyncio.run(grok_media.check_backend())
    assert status.state == "ok"
    assert status.available


def test_a_missing_command_is_not_configured(monkeypatch):
    monkeypatch.setattr(config, "_settings", Settings())
    monkeypatch.setattr(grok_media.shutil, "which", lambda cmd: None)
    status = asyncio.run(grok_media.check_backend())
    assert status.state == "not_configured"
    assert "インストール" in status.detail


def test_an_unauthenticated_cli_is_not_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_settings", Settings())
    monkeypatch.setattr(grok_media.shutil, "which", lambda cmd: "/usr/local/bin/grok")
    monkeypatch.setattr(grok_media, "auth_path", lambda: tmp_path / "auth.json")
    status = asyncio.run(grok_media.check_backend())
    assert status.state == "not_configured"
    assert "device-auth" in status.detail


def test_the_live_check_runs_one_turn(cli_installed, media_env, monkeypatch):
    cli = FakeCli(lambda argv, cwd: answer("ok"))
    monkeypatch.setattr(grok_media, "_exec", cli)

    status = asyncio.run(grok_media.check_live())

    assert status.available
    assert cli.runs == 1
    assert "--always-approve" in cli.calls[0][0]


def test_the_live_check_reports_a_failure(cli_installed, media_env, monkeypatch):
    monkeypatch.setattr(
        grok_media, "_exec", FakeCli(lambda argv, cwd: (1, "", "session expired"))
    )
    status = asyncio.run(grok_media.check_live())
    assert status.state == "error"
    assert "session expired" in status.detail


# --------------------------------------------------------------------------
# 選択肢の出し分けと API
# --------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.routers import assets as assets_router

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(assets_router, "ASSETS_DIR", tmp_path / "assets")
    with TestClient(app) as test_client:
        yield test_client


def mark_available(monkeypatch, state: str = "ok") -> None:
    monkeypatch.setitem(
        backends._status, "grok_cli", BackendStatus("grok_cli", state, "テスト")
    )


def test_options_hide_the_workflow_until_the_cli_is_verified(client, monkeypatch):
    body = client.get("/api/options").json()
    assert GROK_IMAGINE.id not in [wf["id"] for wf in body["image_workflows"]]
    status = [b for b in body["backends"] if b["backend"] == "grok_cli"][0]
    assert status["available"] is False

    mark_available(monkeypatch)
    body = client.get("/api/options").json()
    listed = {wf["id"]: wf for wf in body["image_workflows"]}
    assert GROK_IMAGINE.id in listed
    assert listed[GROK_IMAGINE.id]["backend"] == "grok_cli"
    assert listed[GROK_IMAGINE.id]["family"] == "grok-imagine"
    assert set(listed[GROK_IMAGINE.id]["supports"]) == {"prompt", "aspect_ratio"}
    # 外部バックエンドなので LoRA チェーンは無い
    assert listed[GROK_IMAGINE.id]["accepts_video_loras"] is False


def test_the_image_guide_is_listed_only_when_the_backend_is_available(monkeypatch):
    from app.prompts import image_prompt_guides_section

    assert "Grok Imagine" not in image_prompt_guides_section()

    mark_available(monkeypatch)
    section = image_prompt_guides_section()
    assert "IMAGE PROMPT SPEC — Grok Imagine" in section
    # 要点（語順・先頭の重み・否定形を使わない）
    assert "first 20-30 words" in section
    assert "`sharp focus` rather than `no blur`" in section


def test_the_check_endpoint_updates_the_availability(client, monkeypatch, media_env):
    monkeypatch.setattr(grok_media, "check_live", _live_ok)

    body = client.post("/api/grok/check").json()

    assert body == {
        "backend": "grok_cli",
        "status": "ok",
        "detail": "通りました",
        "available": True,
    }
    assert backends.available("grok_cli")


async def _live_ok() -> BackendStatus:
    return BackendStatus("grok_cli", "ok", "通りました")


# --------------------------------------------------------------------------
# ジョブ実行
# --------------------------------------------------------------------------

@pytest.fixture
def job_env(client, tmp_path, monkeypatch):
    outputs = tmp_path / "outputs"
    assets = tmp_path / "assets"
    (assets / "image").mkdir(parents=True)
    outputs.mkdir()
    monkeypatch.setattr(jobs, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)
    monkeypatch.setattr(jobs, "POLL_INTERVAL", 0.02)

    async def no_llm(text: str) -> None:
        return None

    monkeypatch.setattr(nsfw, "classify", no_llm)
    mark_available(monkeypatch)
    return outputs


def wait_for(client, job_id, timeout=10.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish: {body}")


def test_an_image_job_runs_end_to_end(client, job_env, media_env, monkeypatch):
    cli = FakeCli(saves_and_reports)
    monkeypatch.setattr(grok_media, "_exec", cli)

    created = client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": GROK_IMAGINE.id,
            "image_prompt": PROMPT,
            "aspect_ratio": "16:9 (Widescreen)",
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])

    assert job["status"] == "done", job["error"]
    # 成果物は他のバックエンドと同じ置き場・同じ名前
    saved = job_env / job["id"] / "image.png"
    assert saved.is_file()
    assert job["image_url"] == f"/outputs/{job['id']}/image.png"
    # 何を頼んだかが再現できる形で残る
    stage = job["workflow_json"]["image"]
    assert stage["backend"] == "grok_cli"
    assert stage["workflow_id"] == GROK_IMAGINE.id
    assert stage["request"]["prompt"] == PROMPT
    assert stage["request"]["aspect_ratio"] == "16:9 (Widescreen)"
    assert str(saved) in stage["request"]["instruction"]
    # CLI には outputs/{job_id}/ を直接書かせる（ダウンロードの段が要らない）
    assert dest_of(cli.calls[0][0]) == saved


def test_a_refused_prompt_fails_the_job(client, job_env, media_env, monkeypatch):
    monkeypatch.setattr(grok_media, "_exec", FakeCli(refuses))

    created = client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": GROK_IMAGINE.id,
            "image_prompt": PROMPT,
        },
    )
    job = wait_for(client, created.json()["id"])

    assert job["status"] == "failed"
    assert "moderation" in job["error"]


def test_loras_are_refused(client, job_env):
    created = client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": GROK_IMAGINE.id,
            "image_prompt": PROMPT,
            "loras": [{"lora_name": "x.safetensors", "strength": 1.0}],
        },
    )
    assert created.status_code == 422
    assert "does not support LoRAs" in created.json()["detail"]


def test_an_unverified_backend_is_refused_at_creation(client, job_env, monkeypatch):
    monkeypatch.setitem(
        backends._status, "grok_cli", BackendStatus("grok_cli", "not_configured", "未認証")
    )
    created = client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": GROK_IMAGINE.id,
            "image_prompt": PROMPT,
        },
    )
    assert created.status_code == 422
    assert "使えません" in created.json()["detail"]


# --------------------------------------------------------------------------
# full モード（Grok CLI の画像 → 他バックエンドの動画、SPEC §5.2）
# --------------------------------------------------------------------------

@pytest.fixture
def comfy_env(monkeypatch):
    from app import comfy
    from test_jobs import FakeComfy

    fake = FakeComfy(None)
    for name in ("upload_file", "queue_prompt", "get_history", "download_view",
                 "ws_url"):
        monkeypatch.setattr(comfy, name, getattr(fake, name))
    return fake


def test_a_full_job_bridges_the_image_into_comfyui(client, job_env, media_env,
                                                   comfy_env, monkeypatch):
    """本命の使い方: サブスク枠で画像を作り、ローカルの ComfyUI で動画にする。"""
    async def fake_last_frame(video, dest):
        dest.write_bytes(b"png")
        return dest

    monkeypatch.setattr(jobs, "extract_last_frame", fake_last_frame)
    monkeypatch.setattr(grok_media, "_exec", FakeCli(saves_and_reports))

    created = client.post(
        "/api/jobs",
        json={
            "mode": "full",
            "image_workflow": GROK_IMAGINE.id,  # Grok Build CLI
            "video_workflow": "tx2_3_i2v",  # ComfyUI
            "image_prompt": PROMPT,
            "video_prompt": "He pulls the net taut.",
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])
    assert job["status"] == "done", job["error"]

    image = job_env / job["id"] / "image.png"
    # 1 段目は CLI、2 段目は ComfyUI のグラフ
    assert job["workflow_json"]["image"]["backend"] == "grok_cli"
    assert job["workflow_json"]["video"]["prompt_id"] == "prompt-1"
    assert len(comfy_env.queued) == 1
    # 生成画像を ComfyUI の input へ上げ直して開始フレームにしている
    assert str(image) in comfy_env.uploads
    assert image.is_file()
    assert job["video_url"]


def test_a_full_job_bridges_the_image_into_kie(client, job_env, media_env,
                                               monkeypatch):
    """外部 API の動画（kie.ai）へ渡す向きも通る。"""
    from app import kie

    from test_kie import FakeKie, FakeResponse, envelope, veo_record

    async def fake_last_frame(video, dest):
        dest.write_bytes(b"png")
        return dest

    monkeypatch.setattr(jobs, "extract_last_frame", fake_last_frame)
    monkeypatch.setattr(grok_media, "_exec", FakeCli(saves_and_reports))
    monkeypatch.setattr(config, "_settings", Settings(kie_api_key="kie-test-key"))
    monkeypatch.setitem(backends._status, "kie", BackendStatus("kie", "ok", "テスト"))
    fake = FakeKie()
    monkeypatch.setattr(kie.httpx, "AsyncClient", fake.client())
    monkeypatch.setattr(kie, "POLL_INTERVAL", 0.0)
    fake.answer("create", FakeResponse(envelope({"taskId": "task-veo"})))
    fake.answer("record", veo_record(1, ("https://cdn.kie.ai/out.mp4",)))
    fake.answer(
        "upload", FakeResponse(envelope({"fileUrl": "https://files.kie.ai/gen.png"}))
    )

    created = client.post(
        "/api/jobs",
        json={
            "mode": "full",
            "image_workflow": GROK_IMAGINE.id,  # Grok Build CLI
            "video_workflow": "veo3_1_fast",  # kie.ai
            "image_prompt": PROMPT,
            "video_prompt": "He pulls the net taut.",
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])
    assert job["status"] == "done", job["error"]

    # 1 段目の静止画を kie にアップロードし直して開始フレームにしている
    assert fake.sent("upload")[0]["json"]["fileName"] == "image.png"
    assert job["workflow_json"]["video"]["request"]["input"]["imageUrls"] == [
        "https://files.kie.ai/gen.png"
    ]


def test_the_reverse_bridge_is_still_refused(client, job_env, monkeypatch):
    """ComfyUI の画像 → Grok CLI の動画、のような未実装の向きは 422（§5.2）。"""
    assert ("comfyui", "grok_cli") not in jobs._STAGE_BRIDGES
    assert ("grok_cli", "comfyui") in jobs._STAGE_BRIDGES
    assert ("grok_cli", "kie") in jobs._STAGE_BRIDGES


# --------------------------------------------------------------------------
# マニフェスト
# --------------------------------------------------------------------------

def test_the_manifest_is_valid():
    assert workflows.validate_external_spec(GROK_IMAGINE) == []
    assert GROK_IMAGINE.backend == "grok_cli"
    assert GROK_IMAGINE.lora_chain is None
    assert GROK_IMAGINE.kie is None


def test_a_manifest_without_a_task_is_reported():
    broken = workflows.WorkflowSpec(
        id="broken_grok",
        label="壊れた宣言",
        kind="image",
        backend="grok_cli",
        description="テスト用。",
    )
    assert any("GrokCliTask" in problem for problem in
               workflows.validate_external_spec(broken))
