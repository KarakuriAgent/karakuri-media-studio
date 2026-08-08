"""Grok Build CLI 経由の画像生成・画像編集（SPEC §5.2 / §4.1）。

CLI は一切起動しない: :func:`app.grok_media._exec` を偽物に差し替え、「終了コード /
標準出力 / ファイルを置いたかどうか」を組み合わせて 4 段構えの判定を確かめる。
実プロセスを使うのは ``XAI_API_KEY`` を env から外していることの確認だけ。
"""

import asyncio
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, grok_media
from app.main import app
from app.models import GenerationParams, Settings
from app.workflows import GROK_IMAGINE_EDIT, GROK_IMAGINE_T2I

PROMPT = "A fisherman mending a net on a pier at dawn"
EDIT_PROMPT = "Change the net to a red one, keep everything else unchanged"


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
    """``--output-format plain`` の応答（標準出力そのものが本文）。"""
    return code, text, err


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
    """作業ディレクトリ・CLI の保存先・設定を隔離した状態（実行だけを差し替える）。"""
    workdir = tmp_path / "grok-media-workdir"
    sessions = tmp_path / "home" / ".grok" / "sessions"
    monkeypatch.setattr(grok_media, "workdir", lambda: workdir)
    monkeypatch.setattr(grok_media, "sessions_dir", lambda: sessions)
    monkeypatch.setattr(config, "_settings", Settings())
    return workdir


def run(request, cli, monkeypatch) -> Path:
    monkeypatch.setattr(grok_media, "_exec", cli)
    return asyncio.run(grok_media.generate(request))


def image_request(dest: Path) -> grok_media.ImageRequest:
    return grok_media.ImageRequest(
        tool="image_gen", prompt=PROMPT, dest=dest, aspect_ratio="16:9"
    )


# --------------------------------------------------------------------------
# 指示文
# --------------------------------------------------------------------------

def test_the_instruction_carries_the_path_the_ratio_and_the_contract(tmp_path):
    request = image_request(tmp_path / "image.png")
    text = request.instruction

    assert str(tmp_path / "image.png") in text
    assert "`image_gen`" in text
    assert "aspect_ratio: 16:9" in text
    # 合図の約束事（4 段構えの ②）
    assert f"`OK {tmp_path / 'image.png'}`" in text
    assert "`FAILED <one-line reason>`" in text
    # 承認待ちでハングしないためのフラグ
    argv = grok_media._argv(text)
    assert "--always-approve" in argv
    assert argv[argv.index("--output-format") + 1] == "plain"
    assert "--no-auto-update" in argv


def test_the_edit_instruction_points_at_the_staged_file(tmp_path):
    source = tmp_path / "inputs" / "job1-photo.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\x89PNG")
    request = grok_media.ImageRequest(
        tool="image_edit",
        prompt=EDIT_PROMPT,
        dest=tmp_path / "image.png",
        source_image=source,
    )

    text = request.instruction
    assert "`image_edit`" in text
    assert "job1-photo.png" in text
    assert str(source) in text
    # 編集では縦横比を指示しない（出力は入力画像に従う）
    assert "aspect_ratio" not in text


def test_the_request_is_built_from_the_manifest(media_env, tmp_path):
    params = GenerationParams(
        mode="image_only",
        job_id="job-1",
        image_prompt=PROMPT,
        aspect_ratio="4:3 (Standard)",
        megapixels=1.0,
    )
    request = grok_media.build_request(
        GROK_IMAGINE_T2I, params, tmp_path / "image.png"
    )

    assert request.tool == "image_gen"
    assert request.prompt == PROMPT
    # フォームのプリセットは Grok が受ける語彙へ寄せる（4:3 -> 3:2）
    assert request.aspect_ratio == "3:2"
    assert request.source_image is None
    assert request.as_dict()["dest"] == str(tmp_path / "image.png")
    assert request.as_dict()["backend"] == "grok_cli"


def test_the_edit_request_copies_the_source_into_the_workdir(media_env, tmp_path):
    source = tmp_path / "assets" / "photo.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\x89PNG original")
    params = GenerationParams(
        mode="image_only", job_id="job-1", image_prompt=EDIT_PROMPT
    )

    request = grok_media.build_request(
        GROK_IMAGINE_EDIT,
        params,
        tmp_path / "out" / "image.png",
        {"image": str(source)},
    )

    # CLI のサンドボックスから読めるよう、作業ディレクトリの中へ写す
    assert request.source_image is not None
    assert request.source_image.parent == media_env / grok_media.INPUTS_RELPATH
    assert request.source_image.read_bytes() == b"\x89PNG original"
    # 取り違え防止にジョブのフォルダ名を前置する
    assert request.source_image.name == "out-photo.png"


def test_the_edit_request_needs_a_source_image(media_env, tmp_path):
    params = GenerationParams(
        mode="image_only", job_id="job-1", image_prompt=EDIT_PROMPT
    )
    with pytest.raises(grok_media.GrokMediaError) as caught:
        grok_media.build_request(GROK_IMAGINE_EDIT, params, tmp_path / "image.png")
    assert "編集元画像" in str(caught.value)


def test_a_missing_source_file_is_reported(media_env, tmp_path):
    params = GenerationParams(
        mode="image_only", job_id="job-1", image_prompt=EDIT_PROMPT
    )
    with pytest.raises(grok_media.GrokMediaError) as caught:
        grok_media.build_request(
            GROK_IMAGINE_EDIT,
            params,
            tmp_path / "image.png",
            {"image": str(tmp_path / "gone.png")},
        )
    assert "見つかりません" in str(caught.value)


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


def test_a_file_left_in_the_cli_session_is_recovered(media_env, tmp_path, monkeypatch):
    """④ CLI 自身の保存先に置かれてしまった生成物を拾う保険。

    実機の置き場は ``~/.grok/sessions/<cwd>/<session-id>/images/1.jpg`` で、
    セッション id は実行のたびに変わるのでセッションの根から丸ごと探す。
    """
    def saves_in_its_own_session(argv, cwd):
        folder = grok_media.sessions_dir() / "%2Fworkdir" / "sess-42" / "images"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "1.jpg").write_bytes(b"\xff\xd8 recovered")
        return answer("Saved the image.")

    dest = tmp_path / "image.png"

    saved = run(image_request(dest), FakeCli(saves_in_its_own_session), monkeypatch)

    assert saved == dest
    assert dest.read_bytes() == b"\xff\xd8 recovered"


def test_a_stale_file_in_the_cli_session_is_ignored(media_env, tmp_path, monkeypatch):
    """前回の残りを今回の成果物と取り違えない（mtime で切る）。"""
    folder = grok_media.sessions_dir() / "%2Fworkdir" / "sess-1" / "images"
    folder.mkdir(parents=True)
    stale = folder / "1.jpg"
    stale.write_bytes(b"\xff\xd8 old")
    os.utime(stale, (1_000_000, 1_000_000))

    with pytest.raises(grok_media.GrokMediaError):
        run(
            image_request(tmp_path / "image.png"),
            FakeCli(reports_without_saving),
            monkeypatch,
        )


def test_a_stale_destination_is_not_mistaken_for_a_result(
    media_env, tmp_path, monkeypatch
):
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


def test_a_timeout_is_reported_as_a_generation_failure(
    media_env, tmp_path, monkeypatch
):
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
# 可用性（SPEC §5.2）
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
    assert status.status == "ok"


def test_a_missing_command_is_not_configured(monkeypatch):
    monkeypatch.setattr(config, "_settings", Settings())
    monkeypatch.setattr(grok_media.shutil, "which", lambda cmd: None)
    status = asyncio.run(grok_media.check_backend())
    assert status.status == "not_configured"
    assert "インストール" in status.detail


def test_an_unauthenticated_cli_is_not_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_settings", Settings())
    monkeypatch.setattr(grok_media.shutil, "which", lambda cmd: "/usr/local/bin/grok")
    monkeypatch.setattr(grok_media, "auth_path", lambda: tmp_path / "auth.json")
    status = asyncio.run(grok_media.check_backend())
    assert status.status == "not_configured"
    assert "device-auth" in status.detail


def test_the_live_check_runs_one_turn(cli_installed, media_env, monkeypatch):
    cli = FakeCli(lambda argv, cwd: answer("ok"))
    monkeypatch.setattr(grok_media, "_exec", cli)

    status = asyncio.run(grok_media.check_live())

    assert status.status == "ok"
    assert cli.runs == 1
    assert "--always-approve" in cli.calls[0][0]


def test_the_live_check_reports_a_failure(cli_installed, media_env, monkeypatch):
    monkeypatch.setattr(
        grok_media, "_exec", FakeCli(lambda argv, cwd: (1, "", "session expired"))
    )
    status = asyncio.run(grok_media.check_live())
    assert status.status == "error"
    assert "session expired" in status.detail


# --------------------------------------------------------------------------
# API と選択肢
# --------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.routers import assets as assets_router

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(assets_router, "ASSETS_DIR", tmp_path / "assets")
    with TestClient(app) as test_client:
        yield test_client


def test_the_status_endpoint_reports_the_cli(client, monkeypatch):
    monkeypatch.setattr(grok_media.shutil, "which", lambda cmd: None)
    body = client.get("/api/grok/status").json()
    assert body["status"] == "not_configured"


def test_the_check_endpoint_runs_the_cli(client, cli_installed, media_env, monkeypatch):
    cli = FakeCli(lambda argv, cwd: answer("ok"))
    monkeypatch.setattr(grok_media, "_exec", cli)

    body = client.post("/api/grok/check").json()

    assert body["status"] == "ok"
    assert cli.runs == 1


def test_the_workflows_are_offered_as_image_workflows(client):
    images = {w["id"]: w for w in client.get("/api/options").json()["image_workflows"]}

    t2i = images["grok_imagine_t2i"]
    assert t2i["backend"] == "grok_cli"
    assert t2i["family_label"] == "Grok Imagine（サブスク CLI）"
    assert t2i["requires"] == []
    # グラフが無いので LoRA は挿せない
    assert t2i["accepts_video_loras"] is False

    edit = images["grok_imagine_edit"]
    assert edit["requires"] == ["image"]
    assert edit["image_label"] == "編集元画像"


def test_the_edit_workflow_needs_a_source_image(client):
    body = {
        "mode": "image_only",
        "image_workflow": "grok_imagine_edit",
        "image_prompt": EDIT_PROMPT,
    }
    response = client.post("/api/jobs", json=body)
    assert response.status_code == 422
    assert "source_image" in response.text


@pytest.fixture
def job_env(tmp_path, monkeypatch):
    """``tests.test_jobs`` の ``env``（隔離した DB / outputs + 偽の ComfyUI）。

    ジョブランナーまで通す確認はあちらの下ごしらえがそのまま使えるので、
    フィクスチャの本体を借りる（``sample_video`` は動画を作らないので不要）。
    """
    from tests import test_jobs

    request = type("R", (), {"getfixturevalue": lambda self, name: None})()
    yield from test_jobs.env.__wrapped__(tmp_path / "jobs", monkeypatch, request)


def test_an_image_job_runs_through_the_cli_and_lands_in_the_history(
    monkeypatch, media_env, job_env
):
    """成果物は ComfyUI のジョブと同じ ``outputs/{job_id}/`` に入る（SPEC §5.2）。"""
    from tests.test_jobs import wait_for

    cli = FakeCli(saves_and_reports)
    monkeypatch.setattr(grok_media, "_exec", cli)

    created = job_env.client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": "grok_imagine_t2i",
            "image_prompt": PROMPT,
            "aspect_ratio": "16:9 (Widescreen)",
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(job_env.client, created.json()["id"])

    assert job["status"] == "done", job["error"]
    saved = Path(job["image_path"])
    assert saved.is_file() and saved.parent == job_env.outputs / job["id"]
    # ComfyUI には一切投げていない
    assert job_env.comfy.queued == []
    # 何を頼んだかは workflow_json に残る（グラフを残すのと同じ意図）
    stage = job["workflow_json"]["image"]
    assert stage["workflow_id"] == "grok_imagine_t2i"
    assert stage["backend"] == "grok_cli"
    assert "aspect_ratio: 16:9" in stage["instruction"]
    assert cli.runs == 1


def test_an_edit_job_stages_the_source_image(monkeypatch, media_env, job_env):
    """編集ジョブは編集元画像を作業ディレクトリへ写してから CLI に渡す。"""
    from tests.test_jobs import wait_for

    seen: list[str] = []

    def saves_and_records(argv, cwd):
        seen.append(instruction_of(argv))
        return saves_and_reports(argv, cwd)

    monkeypatch.setattr(grok_media, "_exec", FakeCli(saves_and_records))

    created = job_env.client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": "grok_imagine_edit",
            "image_prompt": EDIT_PROMPT,
            "source_image": str(job_env.start_image),
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(job_env.client, created.json()["id"])

    assert job["status"] == "done", job["error"]
    assert "`image_edit`" in seen[0]
    staged = media_env / grok_media.INPUTS_RELPATH / f"{job['id']}-start.png"
    assert staged.is_file()
    assert str(staged) in seen[0]


def test_loras_are_rejected_for_the_cli_workflows(client):
    body = {
        "mode": "image_only",
        "image_workflow": "grok_imagine_t2i",
        "image_prompt": PROMPT,
        "loras": [{"lora_name": "a.safetensors", "strength": 1.0}],
    }
    response = client.post("/api/jobs", json=body)
    assert response.status_code == 422
    assert "does not support LoRAs" in response.json()["detail"]
