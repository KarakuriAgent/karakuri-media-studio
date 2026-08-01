"""Codex CLI 経由の画像生成（gpt-image-2、SPEC §5.4 / issue #23）。

CLI は一切起動しない: :func:`app.codex_media._exec` を偽物に差し替え、「終了コード /
``--output-last-message`` のファイル / 置かれたファイルの中身」を組み合わせて
3 段構えの判定を確かめる。実プロセスを使うのは ``OPENAI_API_KEY`` を env から
外していることの確認だけ。
"""

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import backends, codex_media, config, db, jobs, nsfw, workflows
from app.backends import BackendStatus
from app.main import app
from app.models import GenerationParams, Settings
from app.workflows import GPT_IMAGE2

PROMPT = 'A kraft coffee bag on a green backdrop, the text "MORNING LIGHT" on it'

#: 本物の PNG の先頭（③ のマジックバイト判定を通る中身）
PNG = codex_media.PNG_MAGIC + b"generated"


# --------------------------------------------------------------------------
# 偽の codex CLI
# --------------------------------------------------------------------------

def instruction_of(argv: list[str]) -> str:
    """``codex exec … '<指示>'`` の指示文（最後の位置引数）。"""
    return argv[-1]


def dest_of(argv: list[str]) -> Path:
    """指示文に書き込んだ保存先（``  /abs/path`` の行）。"""
    match = re.search(r"^\s+(/\S+)$", instruction_of(argv), re.M)
    assert match, instruction_of(argv)
    return Path(match.group(1))


def message_file_of(argv: list[str]) -> Path:
    return Path(argv[argv.index("--output-last-message") + 1])


def answer(argv, text: str, *, code: int = 0, out: str = "", err: str = ""):
    """最終応答は**標準出力ではなくファイル**で返る（``--output-last-message``）。"""
    message_file_of(argv).write_text(text, encoding="utf-8")
    return code, out, err


class FakeCli:
    """``codex_media._exec`` の代役。用意した手順を順に演じる。

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
    dest.write_bytes(PNG)
    return answer(argv, f"OK {dest}")


def saves_without_reporting(argv, cwd):
    """ファイルは置いたが合図を忘れた（言葉ではなくファイルを信じる）。"""
    dest_of(argv).write_bytes(PNG)
    return answer(argv, "Done! I created the picture for you.")


def reports_without_saving(argv, cwd):
    """「作った」と言うだけでファイルが無い（③ でしか検出できない）。"""
    return answer(argv, f"OK {dest_of(argv)}")


def saves_something_that_is_not_a_png(argv, cwd):
    """拡張子は png だが中身が違う（マジックバイトで弾く）。"""
    dest_of(argv).write_bytes(b"\xff\xd8\xff\xe0 this is a JPEG")
    return answer(argv, f"OK {dest_of(argv)}")


def refuses(argv, cwd):
    return answer(argv, "FAILED the prompt was blocked by the safety system")


@pytest.fixture
def media_env(tmp_path, monkeypatch):
    """作業ディレクトリ・CLI ホーム・設定を隔離した状態（実行だけを差し替える）。"""
    workdir = tmp_path / "codex-media-workdir"
    workdir.mkdir()
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setattr(codex_media, "workdir", lambda: workdir)
    monkeypatch.setattr(codex_media, "codex_home", lambda: home)
    monkeypatch.setattr(config, "_settings", Settings())
    return workdir


def run(request, cli, monkeypatch) -> Path:
    monkeypatch.setattr(codex_media, "_exec", cli)
    return asyncio.run(codex_media.generate(request))


def image_request(dest: Path, **overrides) -> codex_media.ImageRequest:
    values = dict(prompt=PROMPT, dest=dest, size="1536x1024", quality="high")
    values.update(overrides)
    return codex_media.ImageRequest(**values)


# --------------------------------------------------------------------------
# 指示文と起動引数
# --------------------------------------------------------------------------

def test_the_instruction_carries_the_path_the_size_and_the_contract(tmp_path):
    request = image_request(tmp_path / "image.png")
    text = request.instruction

    # 組み込みスキルの呼び出し（インストール不要の `.system` スキル）
    assert text.startswith("$imagegen ")
    assert str(tmp_path / "image.png") in text
    assert "Size: 1536x1024 pixels" in text
    assert "Quality: high" in text
    # 合図の約束事（3 段構えの ②）
    assert f"`OK {tmp_path / 'image.png'}`" in text
    assert "`FAILED <one-line reason>`" in text


def test_the_argv_follows_the_recommended_headless_form(media_env, tmp_path):
    request = image_request(tmp_path / "image.png")
    message_file = media_env / "last.txt"

    argv = codex_media._argv(request, media_env, message_file)

    assert argv[:2] == ["codex", "exec"]
    assert "--skip-git-repo-check" in argv
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    # 専用の作業ディレクトリで走らせる（リポジトリの中では走らせない）
    assert argv[argv.index("-C") + 1] == str(media_env)
    assert argv[argv.index("--output-last-message") + 1] == str(message_file)
    assert argv[-1] == request.instruction


def test_the_request_is_built_from_the_manifest(media_env, tmp_path):
    params = GenerationParams(
        mode="image_only",
        job_id="job-1",
        image_workflow=GPT_IMAGE2.id,
        image_prompt=PROMPT,
    )

    request = codex_media.build_request(GPT_IMAGE2, params, tmp_path / "image.png")

    assert request.prompt == PROMPT
    # 選択式フィールドの既定（マニフェスト）がそのまま希望になる
    assert (request.size, request.quality) == ("1024x1024", "medium")
    assert request.as_dict()["dest"] == str(tmp_path / "image.png")
    assert request.as_dict()["media"] == "image"


def test_the_chosen_selects_win(media_env, tmp_path):
    params = GenerationParams(
        mode="image_only",
        job_id="job-1",
        image_workflow=GPT_IMAGE2.id,
        image_prompt=PROMPT,
        selects={"size": "1024x1536", "quality": "low"},
    )

    request = codex_media.build_request(GPT_IMAGE2, params, tmp_path / "image.png")

    assert (request.size, request.quality) == ("1024x1536", "low")
    assert "Size: 1024x1536 pixels" in request.instruction
    assert "Quality: low" in request.instruction


# --------------------------------------------------------------------------
# 成否の判定（3 段構え）
# --------------------------------------------------------------------------

def test_a_saved_file_and_an_ok_signal_succeed(media_env, tmp_path, monkeypatch):
    dest = tmp_path / "out" / "image.png"
    cli = FakeCli(saves_and_reports)

    saved = run(image_request(dest), cli, monkeypatch)

    assert saved == dest and dest.is_file()
    assert cli.runs == 1
    # 実行は専用の作業ディレクトリで（SPEC §5.4）
    assert cli.calls[0][1] == media_env


def test_a_missing_signal_does_not_discard_the_file(media_env, tmp_path, monkeypatch):
    """③ ファイルが在ることが最終的な根拠（合図は補助）。"""
    dest = tmp_path / "image.png"

    saved = run(image_request(dest), FakeCli(saves_without_reporting), monkeypatch)

    assert saved == dest and dest.is_file()


def test_an_ok_signal_without_a_file_fails(media_env, tmp_path, monkeypatch):
    """「生成した」と言われてもファイルが無ければ失敗（1 回やり直してから）。"""
    cli = FakeCli(reports_without_saving)

    with pytest.raises(codex_media.CodexMediaError) as caught:
        run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert "見つかりません" in str(caught.value)
    assert cli.runs == 2


def test_a_file_that_is_not_a_png_is_rejected(media_env, tmp_path, monkeypatch):
    """③ の最終判定はマジックバイト（拡張子だけでは信じない）。"""
    dest = tmp_path / "image.png"
    cli = FakeCli(saves_something_that_is_not_a_png)

    with pytest.raises(codex_media.CodexMediaError) as caught:
        run(image_request(dest), cli, monkeypatch)

    assert "PNG ではありません" in str(caught.value)
    assert cli.runs == 2


def test_a_file_left_in_the_default_place_is_recovered(media_env, tmp_path,
                                                       monkeypatch):
    """保険: ``~/.codex/generated_images/`` に置かれたままの生成物を拾う。"""
    def saves_in_the_default_place(argv, cwd):
        folder = (
            codex_media.codex_home()
            / codex_media.GENERATED_IMAGES_RELNAME
            / "session-1"
        )
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "img_1.png").write_bytes(codex_media.PNG_MAGIC + b"recovered")
        return answer(argv, "Saved the image.")

    dest = tmp_path / "image.png"

    saved = run(image_request(dest), FakeCli(saves_in_the_default_place), monkeypatch)

    assert saved == dest
    assert dest.read_bytes() == codex_media.PNG_MAGIC + b"recovered"


def test_a_stale_file_in_the_default_place_is_ignored(media_env, tmp_path,
                                                      monkeypatch):
    """前回の残りを今回の成果物と取り違えない（mtime で切る）。"""
    import os

    folder = codex_media.codex_home() / codex_media.GENERATED_IMAGES_RELNAME
    folder.mkdir(parents=True)
    stale = folder / "old.png"
    stale.write_bytes(codex_media.PNG_MAGIC + b"old")
    os.utime(stale, (1_000_000, 1_000_000))

    with pytest.raises(codex_media.CodexMediaError):
        run(image_request(tmp_path / "image.png"), FakeCli(reports_without_saving),
            monkeypatch)


def test_a_stale_destination_is_not_mistaken_for_a_result(media_env, tmp_path,
                                                          monkeypatch):
    dest = tmp_path / "image.png"
    dest.write_bytes(codex_media.PNG_MAGIC + b"from a previous run")

    with pytest.raises(codex_media.CodexMediaError):
        run(image_request(dest), FakeCli(reports_without_saving), monkeypatch)

    assert not dest.exists()


def test_a_non_zero_exit_reports_the_output(media_env, tmp_path, monkeypatch):
    cli = FakeCli(lambda argv, cwd: (1, "", "boom: the tool crashed"))

    with pytest.raises(codex_media.CodexMediaError) as caught:
        run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert "exit 1" in str(caught.value)
    assert "boom: the tool crashed" in str(caught.value)


def test_an_unauthenticated_cli_says_how_to_sign_in(media_env, tmp_path, monkeypatch):
    cli = FakeCli(lambda argv, cwd: (1, "", "Error: not logged in"))

    with pytest.raises(codex_media.CodexMediaError) as caught:
        run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert "codex login" in str(caught.value)


def test_a_timeout_is_reported_as_a_generation_failure(media_env, tmp_path,
                                                       monkeypatch):
    monkeypatch.setattr(config, "_settings", Settings(codex_timeout=0.05))

    class Sleeper:
        returncode = 0

        async def communicate(self):
            await asyncio.sleep(5)
            return b"", b""

        def kill(self):
            return None

    async def slow_process(*argv, **kwargs):
        return Sleeper()

    monkeypatch.setattr(codex_media.asyncio, "create_subprocess_exec", slow_process)

    with pytest.raises(codex_media.CodexMediaError) as caught:
        asyncio.run(codex_media.generate(image_request(tmp_path / "image.png")))

    assert "タイムアウト" in str(caught.value)


def test_the_timeout_comes_from_the_settings(media_env, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_settings", Settings(codex_timeout=42.0))
    cli = FakeCli(saves_and_reports)

    run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert cli.calls[0][2] == 42.0


def test_the_command_comes_from_the_settings(media_env, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_settings", Settings(codex_command="/opt/bin/codex"))
    cli = FakeCli(saves_and_reports)

    run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert cli.calls[0][0][0] == "/opt/bin/codex"


def test_the_message_file_is_cleaned_up(media_env, tmp_path, monkeypatch):
    cli = FakeCli(saves_and_reports)

    run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert not message_file_of(cli.calls[0][0]).exists()
    assert list(media_env.glob("last-message-*")) == []


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

    with pytest.raises(codex_media.CodexMediaError) as caught:
        run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert "safety system" in str(caught.value)
    assert cli.runs == 2


def test_a_used_up_quota_is_not_retried(media_env, tmp_path, monkeypatch):
    cli = FakeCli(
        lambda argv, cwd: (1, "", "You've hit your usage limit. Try again later.")
    )

    with pytest.raises(codex_media.CodexQuotaError) as caught:
        run(image_request(tmp_path / "image.png"), cli, monkeypatch)

    assert "サブスク枠" in str(caught.value)
    assert cli.runs == 1  # やり直しても無駄


def test_a_quota_message_in_the_signal_is_recognised(media_env, tmp_path, monkeypatch):
    cli = FakeCli(lambda argv, cwd: answer(argv, "FAILED weekly rate limit reached"))

    with pytest.raises(codex_media.CodexQuotaError):
        run(image_request(tmp_path / "image.png"), cli, monkeypatch)


def test_a_successful_run_that_mentions_a_limit_is_not_a_quota_error(
    media_env, tmp_path, monkeypatch
):
    """ログに紛れた語で成功を取り消さない（判断の根拠はファイル）。"""
    def chatty(argv, cwd):
        dest_of(argv).write_bytes(PNG)
        return answer(
            argv,
            f"OK {dest_of(argv)}",
            out="note: retried once after a transient rate limit",
        )

    dest = tmp_path / "image.png"

    assert run(image_request(dest), FakeCli(chatty), monkeypatch) == dest


# --------------------------------------------------------------------------
# 従量課金へのフォールバック防止（SPEC §5.4）
# --------------------------------------------------------------------------

def test_the_api_key_is_dropped_from_the_environment(monkeypatch):
    monkeypatch.setenv(codex_media.API_KEY_ENV, "sk-secret")
    monkeypatch.setenv("PATH_MARKER_FOR_TEST", "kept")

    assert codex_media.API_KEY_ENV not in codex_media.clean_env()
    assert codex_media.clean_env()["PATH_MARKER_FOR_TEST"] == "kept"


def test_the_child_process_never_sees_the_api_key(tmp_path, monkeypatch):
    """実プロセスで確認する（env の受け渡しは :func:`app.codex_media._exec`）。"""
    monkeypatch.setenv(codex_media.API_KEY_ENV, "sk-secret")
    code, out, _ = asyncio.run(
        codex_media._exec(
            [
                "python3",
                "-c",
                f"import os; print(os.environ.get('{codex_media.API_KEY_ENV}',"
                " 'ABSENT'))",
            ],
            tmp_path,
            10.0,
        )
    )
    assert code == 0
    assert out.strip() == "ABSENT"


# --------------------------------------------------------------------------
# 可用性（`codex login status` 相当、issue #23 のコメント）
# --------------------------------------------------------------------------

@pytest.fixture
def cli_installed(monkeypatch, tmp_path):
    """``codex`` が入っていて ChatGPT でサインイン済みに見える状態。"""
    monkeypatch.setattr(config, "_settings", Settings())
    monkeypatch.setattr(codex_media.shutil, "which", lambda cmd: "/usr/local/bin/codex")
    auth = tmp_path / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(
        '{"OPENAI_API_KEY": null, "tokens": {"access_token": "x"}}', encoding="utf-8"
    )
    monkeypatch.setattr(codex_media, "auth_path", lambda: auth)
    return auth


def test_the_backend_is_available_once_the_cli_is_signed_in(cli_installed):
    status = asyncio.run(codex_media.check_backend())
    assert status.state == "ok"
    assert status.available


def test_a_missing_command_is_not_configured(monkeypatch):
    monkeypatch.setattr(config, "_settings", Settings())
    monkeypatch.setattr(codex_media.shutil, "which", lambda cmd: None)
    status = asyncio.run(codex_media.check_backend())
    assert status.state == "not_configured"
    assert "インストール" in status.detail


def test_an_unauthenticated_cli_is_not_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_settings", Settings())
    monkeypatch.setattr(codex_media.shutil, "which", lambda cmd: "/usr/local/bin/codex")
    monkeypatch.setattr(codex_media, "auth_path", lambda: tmp_path / "auth.json")
    status = asyncio.run(codex_media.check_backend())
    assert status.state == "not_configured"
    assert "codex login" in status.detail


def test_an_api_key_login_is_not_the_subscription_quota(monkeypatch, tmp_path,
                                                        cli_installed):
    """API キーでのログインは従量課金なので、この経路では使えない扱い。"""
    cli_installed.write_text('{"OPENAI_API_KEY": "sk-x"}', encoding="utf-8")

    status = asyncio.run(codex_media.check_backend())

    assert status.state == "not_configured"
    assert "ChatGPT" in status.detail


def test_the_live_check_asks_the_cli_for_the_login_status(cli_installed, media_env,
                                                          monkeypatch):
    cli = FakeCli(lambda argv, cwd: (0, "Logged in using ChatGPT", ""))
    monkeypatch.setattr(codex_media, "_exec", cli)

    status = asyncio.run(codex_media.check_live())

    assert status.available
    assert cli.runs == 1
    # 画像は作らせない（生成ターンは枠を 3〜5 倍速く食う）
    assert cli.calls[0][0][1:] == ["login", "status"]
    assert "Logged in using ChatGPT" in status.detail


def test_the_live_check_reports_a_failure(cli_installed, media_env, monkeypatch):
    monkeypatch.setattr(
        codex_media, "_exec", FakeCli(lambda argv, cwd: (1, "", "session expired"))
    )
    status = asyncio.run(codex_media.check_live())
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
        backends._status, "codex_cli", BackendStatus("codex_cli", state, "テスト")
    )


def test_options_hide_the_workflow_until_the_cli_is_verified(client, monkeypatch):
    body = client.get("/api/options").json()
    assert GPT_IMAGE2.id not in [wf["id"] for wf in body["image_workflows"]]
    status = [b for b in body["backends"] if b["backend"] == "codex_cli"][0]
    assert status["available"] is False

    mark_available(monkeypatch)
    body = client.get("/api/options").json()
    listed = {wf["id"]: wf for wf in body["image_workflows"]}
    assert GPT_IMAGE2.id in listed
    entry = listed[GPT_IMAGE2.id]
    assert entry["backend"] == "codex_cli"
    assert entry["family"] == "gpt-image"
    # 選択式は select: を外した論理名では出さない（フォームは selects を読む）
    assert set(entry["supports"]) == {"prompt"}
    assert [select["name"] for select in entry["selects"]] == ["size", "quality"]
    assert [select["default"] for select in entry["selects"]] == [
        "1024x1024",
        "medium",
    ]
    assert [select["choices"] for select in entry["selects"]] == [
        list(workflows.GPT_IMAGE_SIZES),
        list(workflows.GPT_IMAGE_QUALITIES),
    ]
    # 外部バックエンドなので LoRA チェーンは無い
    assert entry["accepts_video_loras"] is False


def test_the_image_guide_is_listed_only_when_the_backend_is_available(monkeypatch):
    from app.prompts import image_prompt_guides_section

    assert "gpt-image-2" not in image_prompt_guides_section()

    mark_available(monkeypatch)
    section = image_prompt_guides_section()
    assert "IMAGE PROMPT SPEC — gpt-image-2" in section
    # 要点（構成順・引用符 verbatim・photorealistic 直書き・編集は 1 回 1 変更）
    assert "background / scene → subject →" in section
    assert "important details → constraints" in section
    assert "verbatim" in section
    assert "`photorealistic`" in section
    assert "change only X; keep everything else the same" in section


def test_the_check_endpoint_updates_the_availability(client, monkeypatch, media_env):
    monkeypatch.setattr(codex_media, "check_live", _live_ok)

    body = client.post("/api/codex/check").json()

    assert body == {
        "backend": "codex_cli",
        "status": "ok",
        "detail": "通りました",
        "available": True,
    }
    assert backends.available("codex_cli")


async def _live_ok() -> BackendStatus:
    return BackendStatus("codex_cli", "ok", "通りました")


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
    monkeypatch.setattr(codex_media, "_exec", cli)

    created = client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": GPT_IMAGE2.id,
            "image_prompt": PROMPT,
            "selects": {"size": "1536x1024", "quality": "high"},
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
    assert stage["backend"] == "codex_cli"
    assert stage["workflow_id"] == GPT_IMAGE2.id
    assert stage["request"]["prompt"] == PROMPT
    assert stage["request"]["size"] == "1536x1024"
    assert stage["request"]["quality"] == "high"
    assert str(saved) in stage["request"]["instruction"]
    # CLI には outputs/{job_id}/ を直接書かせる（ダウンロードの段が要らない）
    assert dest_of(cli.calls[0][0]) == saved


def test_a_refused_prompt_fails_the_job(client, job_env, media_env, monkeypatch):
    monkeypatch.setattr(codex_media, "_exec", FakeCli(refuses))

    created = client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": GPT_IMAGE2.id,
            "image_prompt": PROMPT,
        },
    )
    job = wait_for(client, created.json()["id"])

    assert job["status"] == "failed"
    assert "safety system" in job["error"]


def test_loras_are_refused(client, job_env):
    created = client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": GPT_IMAGE2.id,
            "image_prompt": PROMPT,
            "loras": [{"lora_name": "x.safetensors", "strength": 1.0}],
        },
    )
    assert created.status_code == 422
    assert "does not support LoRAs" in created.json()["detail"]


def test_an_unknown_select_is_refused(client, job_env):
    created = client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": GPT_IMAGE2.id,
            "image_prompt": PROMPT,
            "selects": {"size": "4096x4096"},
        },
    )
    assert created.status_code == 422
    assert "1024x1024" in created.json()["detail"]


def test_an_unverified_backend_is_refused_at_creation(client, job_env, monkeypatch):
    monkeypatch.setitem(
        backends._status,
        "codex_cli",
        BackendStatus("codex_cli", "not_configured", "未認証"),
    )
    created = client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": GPT_IMAGE2.id,
            "image_prompt": PROMPT,
        },
    )
    assert created.status_code == 422
    assert "使えません" in created.json()["detail"]


# --------------------------------------------------------------------------
# full モード（Codex の画像 → 他バックエンドの動画、SPEC §5.2 / §5.4）
# --------------------------------------------------------------------------

async def _fake_last_frame(video, dest):
    dest.write_bytes(codex_media.PNG_MAGIC + b"last frame")
    return dest


def test_a_full_job_bridges_the_image_into_comfyui(client, job_env, media_env,
                                                   monkeypatch):
    """本命の使い方: サブスク枠で画像を作り、ローカルの ComfyUI で動画にする。"""
    from app import comfy
    from test_jobs import FakeComfy

    fake = FakeComfy(None)
    for name in ("upload_file", "queue_prompt", "get_history", "download_view",
                 "ws_url"):
        monkeypatch.setattr(comfy, name, getattr(fake, name))
    monkeypatch.setattr(jobs, "extract_last_frame", _fake_last_frame)
    monkeypatch.setattr(codex_media, "_exec", FakeCli(saves_and_reports))

    created = client.post(
        "/api/jobs",
        json={
            "mode": "full",
            "image_workflow": GPT_IMAGE2.id,  # Codex CLI
            "video_workflow": "tx2_3_i2v",  # ComfyUI
            "image_prompt": PROMPT,
            "video_prompt": "The steam rises slowly.",
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])
    assert job["status"] == "done", job["error"]

    image = job_env / job["id"] / "image.png"
    assert job["workflow_json"]["image"]["backend"] == "codex_cli"
    assert job["workflow_json"]["video"]["prompt_id"] == "prompt-1"
    # 生成画像を ComfyUI の input へ上げ直して開始フレームにしている
    assert str(image) in fake.uploads
    assert image.is_file()
    assert job["video_url"]


def test_a_full_job_bridges_the_image_into_the_grok_video(client, job_env, media_env,
                                                          tmp_path, monkeypatch):
    """サブスク枠どうしの連結（Codex の画像 → Grok CLI の動画）。"""
    from app import grok_media

    from test_grok_media import FakeCli as GrokCli, saves_video

    grok_workdir = tmp_path / "grok-media-workdir"
    monkeypatch.setattr(grok_media, "workdir", lambda: grok_workdir)
    monkeypatch.setattr(jobs, "extract_last_frame", _fake_last_frame)
    monkeypatch.setattr(codex_media, "_exec", FakeCli(saves_and_reports))
    monkeypatch.setattr(grok_media, "_exec", GrokCli(saves_video))
    monkeypatch.setitem(
        backends._status, "grok_cli", BackendStatus("grok_cli", "ok", "テスト")
    )

    created = client.post(
        "/api/jobs",
        json={
            "mode": "full",
            "image_workflow": GPT_IMAGE2.id,  # Codex CLI
            "video_workflow": "grok_imagine_video",  # Grok Build CLI
            "image_prompt": PROMPT,
            "video_prompt": "The steam rises slowly.",
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])
    assert job["status"] == "done", job["error"]

    image = job_env / job["id"] / "image.png"
    stage = job["workflow_json"]["video"]
    assert stage["backend"] == "grok_cli"
    # 1 段目の生成画像を grok の作業ディレクトリへコピーして開始フレームにしている
    copied = grok_workdir / grok_media.INPUTS_RELPATH / f"{job['id']}-image.png"
    assert stage["request"]["start_image"] == str(copied)
    assert copied.read_bytes() == image.read_bytes()


def test_the_implemented_bridges_are_declared():
    """codex は画像しか作れないので、1 段目からの向きだけを宣言する（§5.4）。"""
    assert ("codex_cli", "comfyui") in jobs._STAGE_BRIDGES
    assert ("codex_cli", "kie") in jobs._STAGE_BRIDGES
    assert ("codex_cli", "grok_cli") in jobs._STAGE_BRIDGES
    assert ("comfyui", "codex_cli") not in jobs._STAGE_BRIDGES
    assert ("grok_cli", "codex_cli") not in jobs._STAGE_BRIDGES


# --------------------------------------------------------------------------
# マニフェスト
# --------------------------------------------------------------------------

def test_the_manifest_is_valid():
    assert workflows.validate_external_spec(GPT_IMAGE2) == []
    assert GPT_IMAGE2.backend == "codex_cli"
    assert GPT_IMAGE2.kind == "image"
    assert GPT_IMAGE2.lora_chain is None
    assert GPT_IMAGE2.kie is None
    assert GPT_IMAGE2.grok is None


def test_a_manifest_without_a_task_is_reported():
    broken = workflows.WorkflowSpec(
        id="broken_codex",
        label="壊れた宣言",
        kind="image",
        backend="codex_cli",
        description="テスト用。",
    )
    assert any(
        "CodexCliTask" in problem
        for problem in workflows.validate_external_spec(broken)
    )


def test_a_select_that_does_not_exist_is_reported():
    broken = workflows.WorkflowSpec(
        id="broken_codex_select",
        label="壊れた宣言",
        kind="image",
        backend="codex_cli",
        description="テスト用。",
        codex=workflows.CodexCliTask(values=("prompt", "select:size")),
    )
    assert any(
        "no such select" in problem
        for problem in workflows.validate_external_spec(broken)
    )


def test_a_video_manifest_on_this_backend_is_reported():
    """Codex CLI は画像しか作れない（動画の宣言は誤り）。"""
    broken = workflows.WorkflowSpec(
        id="broken_codex_video",
        label="壊れた宣言",
        kind="video",
        backend="codex_cli",
        description="テスト用。",
        prompt_hint="テスト用。",
        codex=workflows.CodexCliTask(),
    )
    assert any(
        "only generates images" in problem
        for problem in workflows.validate_external_spec(broken)
    )
