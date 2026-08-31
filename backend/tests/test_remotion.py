"""Remotion 連携（SPEC §5.2）。

``npx remotion`` は一度も起動しない: :func:`app.remotion._run` を偽物に差し替え、
「出力のパース」「進捗の中継」「失敗のときの stderr」だけを見る。実プロセスを
使うのは**中断で子プロセスを殺すこと**の確認だけ（ジョブのキャンセルが裏で
走り続けるレンダリングを残さないことが要）。

ジョブ経路（``mode: "remotion"``）は :func:`app.remotion.render` を差し替えて、
ComfyUI を 1 度も通らずに ``video_path`` が入って done になることを見る。
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, jobs, nsfw, remotion
from app.main import app

KEY = "remotion-test-key"


@pytest.fixture(autouse=True)
def clear_cache():
    """composition 一覧のキャッシュをテストごとに捨てる。"""
    remotion.clear_cache()
    yield
    remotion.clear_cache()


async def _no_llm(text: str) -> None:
    return None


def make_project(tmp_path: Path, *, entry: str = "src/index.ts", package=None) -> Path:
    """構築済み Remotion プロジェクトのふり（entry と package.json だけ）。"""
    root = tmp_path / "remotion-project"
    target = root / entry
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("// entry", encoding="utf-8")
    if package is not None:
        (root / "package.json").write_text(
            json.dumps(package), encoding="utf-8"
        )
    return root


def use_project(directory: Path | str) -> None:
    config.update_settings({"remotion_project_dir": str(directory)})
    remotion.clear_cache()


class FakeRun:
    """``remotion._run`` の代役。呼ばれた引数を控えて決め打ちの結果を返す。"""

    def __init__(self, *, code: int = 0, stdout: str = "", stderr: str = "",
                 lines: list[str] | None = None, writes: Path | None = None):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.lines = lines or []
        self.writes = writes
        self.calls: list[tuple[list[str], Path]] = []
        self.props: list[dict] = []

    async def __call__(self, argv, cwd, *, timeout=None, on_line=None):
        self.calls.append((list(argv), Path(cwd)))
        for arg in argv:
            if str(arg).startswith("--props="):
                path = Path(str(arg).split("=", 1)[1])
                self.props.append(json.loads(path.read_text(encoding="utf-8")))
        for line in self.lines:
            if on_line is not None:
                await on_line(line)
        if self.writes is not None:
            self.writes.parent.mkdir(parents=True, exist_ok=True)
            self.writes.write_bytes(b"\x00\x00\x00 ftypmp42")
        return self.code, self.stdout or "\n".join(self.lines), self.stderr

    @property
    def runs(self) -> int:
        return len(self.calls)


# --------------------------------------------------------------------------
# 設定とエントリポイント
# --------------------------------------------------------------------------

def test_unset_project_dir_is_not_configured():
    use_project("")
    with pytest.raises(remotion.RemotionNotConfigured):
        remotion.project_dir()


def test_missing_project_dir_says_which_path(tmp_path):
    use_project(tmp_path / "nope")
    with pytest.raises(remotion.RemotionError) as caught:
        remotion.project_dir()
    assert "nope" in str(caught.value)


def test_entry_point_defaults_to_src_index(tmp_path):
    root = make_project(tmp_path)
    assert remotion.entry_point(root) == "src/index.ts"
    assert remotion.resolve_entry(root) == "src/index.ts"


def test_entry_point_honours_package_json(tmp_path):
    root = make_project(
        tmp_path,
        entry="remotion/root.tsx",
        package={"config": {"remotionEntry": "remotion/root.tsx"}},
    )
    assert remotion.resolve_entry(root) == "remotion/root.tsx"


def test_broken_package_json_falls_back_to_the_default(tmp_path):
    root = make_project(tmp_path)
    (root / "package.json").write_text("{ not json", encoding="utf-8")
    assert remotion.entry_point(root) == "src/index.ts"


def test_missing_entry_point_is_reported(tmp_path):
    root = make_project(tmp_path, entry="src/other.ts")
    with pytest.raises(remotion.RemotionError) as caught:
        remotion.resolve_entry(root)
    assert "src/index.ts" in str(caught.value)


# --------------------------------------------------------------------------
# composition の一覧
# --------------------------------------------------------------------------

def test_parse_compositions_reads_one_id_per_line():
    assert remotion.parse_compositions("Intro\nMain\nOutro\n") == [
        "Intro",
        "Main",
        "Outro",
    ]


def test_parse_compositions_follows_the_remotion_id_grammar():
    """``^[a-zA-Z0-9-]+$``: 数字始まりは通し、Remotion が拒む文字は通さない。"""
    stdout = "4kIntro\nmy-comp\nnot_valid\nno.dots\n"
    assert remotion.parse_compositions(stdout) == ["4kIntro", "my-comp"]


def test_parse_compositions_survives_a_table_and_log_noise():
    stdout = (
        "Bundling video 100%\n"
        "Composition  Frames  FPS  Dimensions\n"
        "-----------  ------  ---  ----------\n"
        "\x1b[32mIntro\x1b[39m        150     30   1920x1080\n"
        "Main         300     30   1920x1080\n"
        "Main         300     30   1920x1080\n"
    )
    assert remotion.parse_compositions(stdout) == ["Intro", "Main"]


async def test_list_compositions_runs_npx_in_the_project(tmp_path, monkeypatch):
    root = make_project(tmp_path)
    use_project(root)
    fake = FakeRun(stdout="Intro\nMain\n")
    monkeypatch.setattr(remotion, "_run", fake)

    assert await remotion.list_compositions() == ["Intro", "Main"]
    argv, cwd = fake.calls[0]
    assert argv[:4] == ["npx", "remotion", "compositions", "src/index.ts"]
    assert "--quiet" in argv
    assert cwd == root


async def test_list_compositions_is_cached_for_a_while(tmp_path, monkeypatch):
    use_project(make_project(tmp_path))
    fake = FakeRun(stdout="Intro\n")
    monkeypatch.setattr(remotion, "_run", fake)

    assert await remotion.list_compositions() == ["Intro"]
    assert await remotion.list_compositions() == ["Intro"]
    assert fake.runs == 1
    # キャッシュを捨てれば読み直す
    remotion.clear_cache()
    assert await remotion.list_compositions() == ["Intro"]
    assert fake.runs == 2


async def test_list_compositions_reports_stderr_on_failure(tmp_path, monkeypatch):
    use_project(make_project(tmp_path))
    monkeypatch.setattr(
        remotion, "_run", FakeRun(code=1, stderr="Cannot find module 'remotion'")
    )
    with pytest.raises(remotion.RemotionError) as caught:
        await remotion.list_compositions()
    assert "Cannot find module 'remotion'" in str(caught.value)
    assert "exit 1" in str(caught.value)


async def test_list_compositions_needs_the_setting():
    use_project("")
    with pytest.raises(remotion.RemotionNotConfigured):
        await remotion.list_compositions()


# --------------------------------------------------------------------------
# 進捗のパース
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "line, expected",
    [
        ("Rendered 120/300", 0.4),
        ("Rendering frames | ████░░ | 150/300", 0.5),
        ("+ Encoded 300/300 frames", 1.0),
        ("Bundling 1/2", 0.5),
    ],
)
def test_parse_progress_reads_the_fraction(line, expected):
    assert remotion.parse_progress(line) == pytest.approx(expected)


@pytest.mark.parametrize(
    "line",
    [
        "Rendered frames",  # 分数が無い
        "Serving on http://localhost:3000",  # フレームの話ではない
        "Rendered 400/300",  # 進捗として使えない
        "Rendered 1/0",
    ],
)
def test_parse_progress_ignores_what_it_cannot_use(line):
    assert remotion.parse_progress(line) is None


# --------------------------------------------------------------------------
# レンダリング
# --------------------------------------------------------------------------

async def test_render_passes_props_as_a_temp_file_and_removes_it(
    tmp_path, monkeypatch
):
    root = make_project(tmp_path)
    use_project(root)
    monkeypatch.setattr(remotion, "REMOTION_TMP_DIR", tmp_path / "runtime")
    output = tmp_path / "outputs" / "job-1" / "video.mp4"
    fake = FakeRun(writes=output, lines=["Rendered 3/6"])
    monkeypatch.setattr(remotion, "_run", fake)

    saved = await remotion.render(
        "job-1", "Intro", {"title": "第3話", "n": 2}, output
    )
    assert saved == output and output.is_file()

    argv, cwd = fake.calls[0]
    assert argv[:5] == ["npx", "remotion", "render", "src/index.ts", "Intro"]
    assert f"--output={output}" in argv
    assert "--overwrite" in argv
    assert cwd == root
    # props はファイル渡し（引数には埋めない）で、終わったら消えている
    assert fake.props == [{"title": "第3話", "n": 2}]
    props_arg = next(arg for arg in argv if arg.startswith("--props="))
    assert not Path(props_arg.split("=", 1)[1]).exists()


async def test_render_streams_progress_to_the_caller(tmp_path, monkeypatch):
    use_project(make_project(tmp_path))
    monkeypatch.setattr(remotion, "REMOTION_TMP_DIR", tmp_path / "runtime")
    output = tmp_path / "out.mp4"
    monkeypatch.setattr(
        remotion,
        "_run",
        FakeRun(
            writes=output,
            lines=["Bundling video", "Rendered 150/300", "Rendered 300/300"],
        ),
    )
    seen: list[tuple[float | None, str]] = []

    async def on_progress(fraction, line):
        seen.append((fraction, line))

    await remotion.render("job-1", "Intro", {}, output, on_progress=on_progress)
    assert [fraction for fraction, _ in seen] == [None, 0.5, 1.0]
    assert seen[0][1] == "Bundling video"


async def test_render_failure_carries_the_stderr_tail(tmp_path, monkeypatch):
    use_project(make_project(tmp_path))
    monkeypatch.setattr(remotion, "REMOTION_TMP_DIR", tmp_path / "runtime")
    monkeypatch.setattr(
        remotion,
        "_run",
        FakeRun(code=1, stderr="Error: No composition with the ID 'Nope'"),
    )
    with pytest.raises(remotion.RemotionError) as caught:
        await remotion.render("job-1", "Nope", {}, tmp_path / "out.mp4")
    assert "No composition with the ID 'Nope'" in str(caught.value)


async def test_render_rejects_a_success_without_a_file(tmp_path, monkeypatch):
    use_project(make_project(tmp_path))
    monkeypatch.setattr(remotion, "REMOTION_TMP_DIR", tmp_path / "runtime")
    monkeypatch.setattr(remotion, "_run", FakeRun(stdout="all good"))
    with pytest.raises(remotion.RemotionError) as caught:
        await remotion.render("job-1", "Intro", {}, tmp_path / "out.mp4")
    assert "出力ファイル" in str(caught.value)


# --------------------------------------------------------------------------
# 中断（ここだけ実プロセスを使う）
# --------------------------------------------------------------------------

async def test_run_kills_the_child_process_when_cancelled(tmp_path):
    """キャンセルでレンダリングが裏に残らないこと（ジョブ停止の要）。"""
    pidfile = tmp_path / "pid"
    script = (
        "import os, sys, time\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "sys.stdout.flush()\n"
        "time.sleep(120)\n"
    )
    task = asyncio.create_task(
        remotion._run([sys.executable, "-c", script], tmp_path)
    )
    deadline = time.time() + 10
    while not pidfile.exists() and time.time() < deadline:
        await asyncio.sleep(0.02)
    assert pidfile.exists(), "子プロセスが起動しませんでした"
    pid = int(pidfile.read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"pid {pid} がまだ生きています")


async def test_run_reads_carriage_return_progress_bars(tmp_path):
    script = (
        "import sys\n"
        "sys.stdout.write('Rendered 1/2\\rRendered 2/2\\r')\n"
        "sys.stdout.flush()\n"
    )
    lines: list[str] = []

    async def on_line(line: str) -> None:
        lines.append(line)

    code, stdout, _ = await remotion._run(
        [sys.executable, "-c", script], tmp_path, on_line=on_line
    )
    assert code == 0
    assert lines == ["Rendered 1/2", "Rendered 2/2"]
    assert "Rendered 2/2" in stdout


# --------------------------------------------------------------------------
# ジョブ経路（mode: "remotion"）
# --------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    """DB・outputs をテスト用に閉じ込めた TestClient（ComfyUI は一切使わない）。"""
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(jobs, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)
    monkeypatch.setattr(nsfw, "classify", _no_llm)

    project = make_project(tmp_path)
    with TestClient(app) as client:
        yield type(
            "Env",
            (),
            {
                "client": client,
                "outputs": outputs,
                "assets": assets,
                "project": project,
                "tmp": tmp_path,
            },
        )


def enable(env, **settings) -> None:
    body = {"remotion_project_dir": str(env.project)}
    body.update(settings)
    response = env.client.put("/api/settings", json=body)
    assert response.status_code == 200, response.text


def stub_render(monkeypatch) -> list[tuple[str, str, dict, Path]]:
    """``remotion.render`` の代役（mp4 らしいバイト列を置くだけ）。"""
    calls: list[tuple[str, str, dict, Path]] = []

    async def fake_render(job_id, composition, props, output_path, *, on_progress=None):
        calls.append((job_id, composition, props, Path(output_path)))
        if on_progress is not None:
            await on_progress(0.5, "Rendered 150/300")
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00\x00\x00 ftypmp42")
        return output

    monkeypatch.setattr(remotion, "render", fake_render)
    return calls


def stub_last_frame(monkeypatch) -> list[tuple[Path, Path]]:
    """ffmpeg は動かさない（偽の mp4 からは抜けないので）。"""
    calls: list[tuple[Path, Path]] = []

    async def fake_extract(video_path, dest):
        calls.append((Path(video_path), Path(dest)))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x89PNG")
        return dest

    monkeypatch.setattr(jobs, "extract_last_frame", fake_extract)
    return calls


def body(**overrides) -> dict:
    payload = {
        "mode": "remotion",
        "remotion_composition": "Intro",
        "remotion_props": {"title": "第3話"},
    }
    payload.update(overrides)
    return payload


def wait_for(client, job_id, statuses=("done", "failed", "canceled"), timeout=30.0):
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} stuck in {job.get('status')!r}")


def test_remotion_job_renders_and_records_the_video(env, monkeypatch):
    enable(env)
    calls = stub_render(monkeypatch)
    frames = stub_last_frame(monkeypatch)

    response = env.client.post("/api/jobs", json=body())
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["mode"] == "remotion"
    assert created["nsfw"] is False

    job = wait_for(env.client, created["id"])
    assert job["status"] == "done", job["error"]
    assert Path(job["video_path"]).is_file()
    assert job["video_url"] == f"/outputs/{job['id']}/video.mp4"
    assert job["started_at"] and job["finished_at"]
    # 何を頼んだかは workflow_json に残る（ComfyUI のグラフと同じ役目）
    assert job["workflow_json"]["remotion"] == {
        "composition": "Intro",
        "props": {"title": "第3話"},
    }

    job_id, composition, props, output = calls[0]
    assert job_id == created["id"]
    assert composition == "Intro"
    assert props == {"title": "第3話"}
    assert output == env.outputs / created["id"] / "video.mp4"

    # ComfyUI の動画ジョブと同じくラストフレームも残る（サムネイル / 続き生成）
    assert frames == [(output, env.outputs / created["id"] / "last_frame.png")]
    assert Path(job["last_frame_path"]).is_file()
    assert job["last_frame_url"] == f"/outputs/{job['id']}/last_frame.png"


def test_remotion_job_failure_is_recorded(env, monkeypatch):
    enable(env)

    async def failing(*args, **kwargs):
        raise remotion.RemotionError("Error: No composition with the ID 'Nope'")

    monkeypatch.setattr(remotion, "render", failing)

    created = env.client.post("/api/jobs", json=body()).json()
    job = wait_for(env.client, created["id"])
    assert job["status"] == "failed"
    assert "No composition with the ID 'Nope'" in job["error"]


def test_remotion_job_cancel_stops_the_render(env, monkeypatch):
    enable(env)
    started = asyncio.Event()
    state = {"cancelled": False}

    async def slow(job_id, composition, props, output_path, *, on_progress=None):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            # 実物は remotion._run が子プロセスを殺す（上のプロセステスト）
            state["cancelled"] = True
            raise
        return Path(output_path)

    monkeypatch.setattr(remotion, "render", slow)

    created = env.client.post("/api/jobs", json=body()).json()
    deadline = time.time() + 10
    while not started.is_set() and time.time() < deadline:
        time.sleep(0.02)
    assert started.is_set(), "レンダリングが始まりませんでした"

    response = env.client.post(f"/api/jobs/{created['id']}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "canceled"
    assert state["cancelled"] is True


def test_remotion_job_needs_the_project_dir(env):
    """設定が空のまま投げたら 400（値ではなく設定が足りていない）。"""
    response = env.client.post("/api/jobs", json=body())
    assert response.status_code == 400, response.text
    assert "Remotion" in response.json()["detail"]


def test_remotion_job_requires_composition_and_props(env):
    enable(env)
    response = env.client.post("/api/jobs", json={"mode": "remotion"})
    assert response.status_code == 422, response.text
    detail = response.text
    assert "remotion_composition" in detail and "remotion_props" in detail


def test_remotion_job_accepts_empty_props(env, monkeypatch):
    """props を取らない composition もあるので、空オブジェクトは足りている。"""
    enable(env)
    stub_render(monkeypatch)
    stub_last_frame(monkeypatch)

    response = env.client.post("/api/jobs", json=body(remotion_props={}))
    assert response.status_code == 201, response.text
    assert wait_for(env.client, response.json()["id"])["status"] == "done"


def test_remotion_job_can_be_continued(env, monkeypatch):
    """ラストフレームが残るので「続き生成」の入り口を通れる（422 にならない）。"""
    enable(env)
    stub_render(monkeypatch)
    stub_last_frame(monkeypatch)

    created = env.client.post("/api/jobs", json=body()).json()
    assert wait_for(env.client, created["id"])["status"] == "done"

    response = env.client.post(
        f"/api/jobs/{created['id']}/continue", json={"video_prompt": "he walks away"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["mode"] == "i2v"
    env.client.post(f"/api/jobs/{response.json()['id']}/cancel")


def test_remotion_rerun_keeps_the_nsfw_flag_off(env, monkeypatch):
    """焼き直しでも自動判定は走らない（投入時と同じ扱い、SPEC §5.2）。"""
    enable(env)
    stub_render(monkeypatch)
    stub_last_frame(monkeypatch)

    def refuse(text):  # pragma: no cover - 呼ばれたら失敗
        raise AssertionError("Remotion のジョブで NSFW 判定が走りました")

    monkeypatch.setattr(nsfw, "job_text", refuse)

    created = env.client.post("/api/jobs", json=body()).json()
    assert wait_for(env.client, created["id"])["status"] == "done"

    response = env.client.post(f"/api/jobs/{created['id']}/rerun")
    assert response.status_code == 201, response.text
    again = response.json()
    assert again["nsfw"] is False
    assert again["nsfw_source"] == ""
    assert wait_for(env.client, again["id"])["status"] == "done"


def test_remotion_rerun_carries_a_manual_nsfw_flag(env, monkeypatch):
    """手動で立てたフラグは焼き直しにも引き継ぐ（既存のジョブと同じ）。"""
    enable(env)
    stub_render(monkeypatch)
    stub_last_frame(monkeypatch)

    created = env.client.post("/api/jobs", json=body(nsfw=True)).json()
    assert created["nsfw"] is True and created["nsfw_source"] == "manual"
    assert wait_for(env.client, created["id"])["status"] == "done"

    again = env.client.post(f"/api/jobs/{created['id']}/rerun").json()
    assert again["nsfw"] is True
    assert again["nsfw_source"] == "auto"


# --------------------------------------------------------------------------
# 外部 API（/api/v1）
# --------------------------------------------------------------------------

def call(env, method: str, path: str, **kwargs):
    return env.client.request(method, path, headers={"X-API-Key": KEY}, **kwargs)


def test_external_compositions_needs_the_setting(env):
    env.client.put("/api/settings", json={"external_api_key": KEY})
    response = call(env, "GET", "/api/v1/remotion/compositions")
    assert response.status_code == 400, response.text
    assert "設定されていません" in response.json()["detail"]


def test_external_compositions_lists_the_ids(env, monkeypatch):
    enable(env, external_api_key=KEY)
    monkeypatch.setattr(remotion, "_run", FakeRun(stdout="Intro\nMain\n"))

    response = call(env, "GET", "/api/v1/remotion/compositions")
    assert response.status_code == 200, response.text
    assert response.json() == {"compositions": ["Intro", "Main"]}


def test_external_compositions_reports_a_failing_cli(env, monkeypatch):
    enable(env, external_api_key=KEY)
    monkeypatch.setattr(remotion, "_run", FakeRun(code=1, stderr="boom"))
    response = call(env, "GET", "/api/v1/remotion/compositions")
    assert response.status_code == 400
    assert "boom" in response.json()["detail"]


def test_external_job_submission(env, monkeypatch):
    enable(env, external_api_key=KEY)
    stub_render(monkeypatch)
    stub_last_frame(monkeypatch)

    response = call(env, "POST", "/api/v1/jobs", json=body())
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["mode"] == "remotion"
    assert wait_for(env.client, created["id"])["status"] == "done"


def test_external_job_submission_needs_the_setting(env):
    env.client.put("/api/settings", json={"external_api_key": KEY})
    response = call(env, "POST", "/api/v1/jobs", json=body())
    assert response.status_code == 400, response.text


def test_external_jobs_share_the_pending_pool(env, monkeypatch):
    """暴走ガードは Remotion のジョブも数える（生成プールは 1 つ）。"""
    enable(env, external_api_key=KEY, external_max_pending_takes=1)
    started = asyncio.Event()

    async def slow(job_id, composition, props, output_path, *, on_progress=None):
        started.set()
        await asyncio.sleep(30)
        return Path(output_path)

    monkeypatch.setattr(remotion, "render", slow)

    first = call(env, "POST", "/api/v1/jobs", json=body())
    assert first.status_code == 201, first.text
    second = call(env, "POST", "/api/v1/jobs", json=body())
    assert second.status_code == 429, second.text

    env.client.post(f"/api/jobs/{first.json()['id']}/cancel")
