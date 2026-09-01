"""音源解析ジョブ（``mode: "audio_analysis"``、SPEC §5.2）。

解析の本体（faster-whisper / stable-ts / librosa）はアプリの環境に入れないので、
ここでも**一度も動かさない**: :func:`app.audio_analysis._run` を偽物に差し替えて
「投入の検証」「依存が無いときの 400」「JSON の置き場と ``analysis_url``」だけを見る。

ワーカー（:mod:`app.audio_analysis_worker`）は純関数（タスクの選び方・アライン前の
前処理・1 文字ごとの秒割り）と、**ffmpeg だけで動く ``silence``** を実プロセスで
確かめる（ffmpeg が無ければ skip）。
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import audio_analysis, audio_analysis_worker as worker, db, jobs, media_ref, nsfw
from app.main import app

KEY = "analysis-test-key"


async def _no_llm(text: str) -> None:
    return None


# --------------------------------------------------------------------------
# ワーカーの純関数
# --------------------------------------------------------------------------

def test_tasks_default_to_everything_the_lyrics_allow():
    """既定は全部。歌詞があれば align、無ければ transcribe に倒れる。"""
    assert worker.selected_tasks("", has_lyrics=True) == [
        "align", "onsets", "beats", "silence"
    ]
    assert worker.selected_tasks("", has_lyrics=False) == [
        "transcribe", "onsets", "beats", "silence"
    ]


def test_tasks_can_be_narrowed_and_are_deduplicated():
    assert worker.selected_tasks("silence,silence", has_lyrics=False) == ["silence"]
    # 歌詞が無いのに align だけを頼まれたら、回せる解析が残らない
    assert worker.selected_tasks("align", has_lyrics=False) == []


def test_unknown_task_is_rejected():
    with pytest.raises(ValueError, match="unknown task"):
        worker.selected_tasks("bpm", has_lyrics=True)


def test_alignment_text_is_substituted_and_stripped():
    """英字＋感嘆符は仮名に、記号は落としてからアラインに渡す。"""
    assert worker.prepare_line(
        "BAN! BAN! 撃ち抜け？", {"BAN!": "バン"}
    ) == "バン バン 撃ち抜け"
    # 長いキーから当てるので "BAN!" と "BAN" を両方渡しても取りこぼさない
    assert worker.prepare_line(
        "BAN! BAN", {"BAN!": "バン", "BAN": "ばん"}
    ) == "バン ばん"


def test_words_are_split_into_characters():
    """語の頭は実測のまま、語の中だけを等分する。"""
    chars = worker.chars_of(" 今日", 1.0, 1.4)
    assert [c["c"] for c in chars] == ["今", "日"]
    assert chars[0]["s"] == 1.0
    assert chars[1]["s"] == pytest.approx(1.2)
    assert chars[-1]["e"] == pytest.approx(1.4)
    assert worker.chars_of("  ", 1.0, 2.0) == []


def test_missing_requirements_only_looks_at_the_tasks_that_need_them():
    """librosa / ffmpeg しか要らないタスクは依存不足で落とさない。"""
    assert worker.missing_requirements(["onsets", "beats", "silence"]) == []


# --------------------------------------------------------------------------
# CUDA ライブラリの先読みと CPU フォールバック
# --------------------------------------------------------------------------

def test_preloading_cuda_libraries_is_a_no_op_without_the_wheels(monkeypatch):
    """`nvidia-*` の wheel が無ければ何も開かない（GPU 無しの環境では素通り）。"""
    monkeypatch.setattr(worker, "nvidia_lib_dirs", list)
    assert worker.preload_cuda_libraries() == []


def test_preloading_opens_every_shared_object_in_order(monkeypatch, tmp_path):
    """cuBLAS → cuDNN の順に、ディレクトリの .so を全部開く。"""
    opened: list[str] = []

    def fake_cdll(path, mode=None):
        if path.endswith("broken.so"):
            raise OSError("cannot open shared object file")
        opened.append(Path(path).name)
        return object()

    cublas = tmp_path / "cublas" / "lib"
    cudnn = tmp_path / "cudnn" / "lib"
    for directory in (cublas, cudnn):
        directory.mkdir(parents=True)
    (cublas / "libcublas.so.12").write_bytes(b"")
    (cudnn / "libcudnn.so.9").write_bytes(b"")
    # 開けない .so が混じっていても本命は開く（飛ばすだけ）
    (cudnn / "broken.so").write_bytes(b"")

    monkeypatch.setattr(worker, "nvidia_lib_dirs", lambda: [cublas, cudnn])
    monkeypatch.setattr(worker.ctypes, "CDLL", fake_cdll)
    assert worker.preload_cuda_libraries() == ["libcublas.so.12", "libcudnn.so.9"]
    assert opened == ["libcublas.so.12", "libcudnn.so.9"]


def test_preloading_can_be_turned_off_by_the_environment(monkeypatch, tmp_path):
    """CPU フォールバックの確認用に先読みを止められる。"""
    monkeypatch.setattr(worker, "nvidia_lib_dirs", lambda: [tmp_path])
    monkeypatch.setenv(worker.SKIP_CUDA_PRELOAD_ENV, "1")
    assert worker.preload_cuda_libraries() == []
    # 0 と空文字は「止めない」
    monkeypatch.setenv(worker.SKIP_CUDA_PRELOAD_ENV, "0")
    assert worker.preload_cuda_libraries() == []  # tmp_path に .so が無いだけ


def test_nvidia_lib_dirs_only_returns_directories_that_exist():
    """この環境に wheel が無くても落ちない（返るのは実在するものだけ）。"""
    for directory in worker.nvidia_lib_dirs():
        assert directory.is_dir()
        assert directory.name == "lib"


def test_missing_cuda_libraries_is_empty_without_a_gpu(monkeypatch):
    """GPU が無い環境では「足りない」と言わない（CPU で回るのが正しい）。"""
    monkeypatch.setattr(worker, "_cuda_driver_available", lambda: False)
    assert worker.missing_cuda_libraries() == []


def test_missing_cuda_libraries_names_the_pip_packages(monkeypatch):
    """GPU があるのに cuBLAS 12 / cuDNN 9 が開けなければ pip 名を返す。"""
    monkeypatch.setattr(worker, "_cuda_driver_available", lambda: True)
    monkeypatch.setattr(worker, "preload_cuda_libraries", list)

    def fake_cdll(name, mode=None):
        raise OSError(f"{name}: cannot open shared object file")

    monkeypatch.setattr(worker.ctypes, "CDLL", fake_cdll)
    assert worker.missing_cuda_libraries() == [
        "nvidia-cublas-cu12", "nvidia-cudnn-cu12"
    ]


def test_a_missing_cuda_library_falls_back_to_the_cpu(monkeypatch):
    """ctranslate2 の「libcublas.so.12 が読めない」はジョブを失敗させない。"""
    monkeypatch.setattr(worker, "torch_device", lambda: "cuda")
    devices: list[str] = []

    def load(device: str) -> str:
        devices.append(device)
        if device == "cuda":
            raise RuntimeError(
                "Library libcublas.so.12 is not found or cannot be loaded"
            )
        return "ok"

    warnings: list[str] = []
    assert worker.with_cpu_fallback(load, warnings, "書き起こし") == ("ok", "cpu")
    assert devices == ["cuda", "cpu"]
    assert len(warnings) == 1
    assert "CUDA のライブラリ" in warnings[0]
    assert "書き起こし" in warnings[0]


def test_an_out_of_memory_still_falls_back_with_its_own_message(monkeypatch):
    monkeypatch.setattr(worker, "torch_device", lambda: "cuda")

    def load(device: str) -> str:
        if device == "cuda":
            raise RuntimeError("CUDA failed with error out of memory")
        return "ok"

    warnings: list[str] = []
    assert worker.with_cpu_fallback(load, warnings, "アライン") == ("ok", "cpu")
    assert warnings == ["GPU のメモリが足りないので アライン は CPU で実行しました"]


def test_other_failures_are_not_swallowed(monkeypatch):
    """関係のない失敗まで CPU で回し直さない（黙って遅くなるのを防ぐ）。"""
    monkeypatch.setattr(worker, "torch_device", lambda: "cuda")

    def load(device: str):
        raise RuntimeError("model small is not available")

    with pytest.raises(RuntimeError, match="not available"):
        worker.with_cpu_fallback(load, [], "書き起こし")


# --------------------------------------------------------------------------
# ワーカーの実行（ffmpeg だけで動く silence）
# --------------------------------------------------------------------------

def make_wav(path: Path) -> Path:
    """無音 → 音 → 無音 の 3 秒（silencedetect が拾える形）。"""
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-t", "1", "-i", "anullsrc=r=48000:cl=mono",
            "-f", "lavfi", "-t", "1", "-i", "sine=frequency=440:r=48000",
            "-f", "lavfi", "-t", "1", "-i", "anullsrc=r=48000:cl=mono",
            "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が無い")
def test_the_worker_writes_the_analysis_json_with_ffmpeg_alone(tmp_path):
    """依存を 1 つも入れていなくても ``silence`` だけなら通る。"""
    audio = make_wav(tmp_path / "tone.wav")
    out = tmp_path / "analysis.json"
    result = subprocess.run(
        [
            sys.executable, str(audio_analysis.WORKER),
            "--audio", str(audio), "--out", str(out), "--tasks", "silence",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "PROGRESS " in result.stdout

    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data) >= {
        "duration", "sample_rate", "lines", "onsets", "beats", "silence",
        "sections", "warnings",
    }
    assert data["duration"] == pytest.approx(3.0, abs=0.2)
    assert data["sample_rate"] == 48000
    # 手で書き足す欄は空のまま
    assert data["sections"] == []
    assert data["silence"], "無音区間が 1 つも取れていません"
    assert data["silence"][0]["start"] == pytest.approx(0.0, abs=0.1)


def test_the_worker_exits_3_when_a_dependency_is_missing(monkeypatch, tmp_path):
    """align は stable-ts が要る。無ければ 3 で落ちて理由を言う。"""
    monkeypatch.setattr(worker, "_available", lambda module: False)
    lyrics = tmp_path / "lyrics.txt"
    lyrics.write_text("今日も見張ってる\n", encoding="utf-8")
    code = worker.main([
        "--audio", "x.wav", "--out", str(tmp_path / "a.json"),
        "--tasks", "align", "--lyrics", str(lyrics),
    ])
    assert code == audio_analysis.EXIT_MISSING_DEPENDENCY


# --------------------------------------------------------------------------
# アプリ側（サブプロセスの呼び方と 400）
# --------------------------------------------------------------------------

class FakeRun:
    """``audio_analysis._run`` の代役（引数を控えて決め打ちの結果を返す）。"""

    def __init__(self, *, code: int = 0, stdout: str = "", stderr: str = "",
                 lines: list[str] | None = None, writes: Path | None = None):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.lines = lines or []
        self.writes = writes
        self.calls: list[list[str]] = []

    async def __call__(self, argv, *, timeout=None, on_line=None):
        self.calls.append([str(arg) for arg in argv])
        for line in self.lines:
            if on_line is not None:
                await on_line(line)
        if self.writes is not None:
            self.writes.parent.mkdir(parents=True, exist_ok=True)
            self.writes.write_text('{"lines": []}', encoding="utf-8")
        return self.code, self.stdout or "\n".join(self.lines), self.stderr


def test_progress_lines_are_parsed():
    assert audio_analysis.parse_progress("PROGRESS 0.250 align を実行中") == (
        0.25, "align を実行中"
    )
    # ふつうのログ行はそのまま（割合は読めない）
    assert audio_analysis.parse_progress("analysis.json を書きました") == (
        None, "analysis.json を書きました"
    )


def test_the_command_line_carries_every_option(tmp_path):
    argv = audio_analysis.build_command(
        audio=tmp_path / "song.wav",
        output=tmp_path / "analysis.json",
        tasks=["align", "onsets"],
        lyrics_file=tmp_path / "lyrics.txt",
        stems=[tmp_path / "vocals.wav"],
        language="ja",
        model="medium",
        substitutions='{"BAN!": "バン"}',
    )
    assert argv[1] == str(audio_analysis.WORKER)
    assert argv[argv.index("--tasks") + 1] == "align,onsets"
    assert argv[argv.index("--model") + 1] == "medium"
    assert argv[argv.index("--stem") + 1] == str(tmp_path / "vocals.wav")
    assert argv[argv.index("--substitutions") + 1] == '{"BAN!": "バン"}'


def test_the_configured_interpreter_is_used(monkeypatch):
    from app import config

    config.update_settings({"audio_analysis_python": "/opt/analysis/bin/python"})
    assert audio_analysis.interpreter() == "/opt/analysis/bin/python"
    config.update_settings({"audio_analysis_python": ""})
    assert audio_analysis.interpreter() == sys.executable



async def test_a_missing_dependency_is_a_configuration_problem(monkeypatch):
    fake = FakeRun(code=3, stderr="stable-ts / openai-whisper が入っていません")
    monkeypatch.setattr(audio_analysis, "_run", fake)
    with pytest.raises(audio_analysis.AudioAnalysisNotConfigured) as caught:
        await audio_analysis.check_dependencies(["align"])
    # 「何を入れればよいか」まで本文に入る
    assert "stable-ts" in str(caught.value)
    assert "requirements-optional.txt" in str(caught.value)
    assert "audio_analysis_python" in str(caught.value)



async def test_analyze_streams_progress_and_returns_the_json(monkeypatch, tmp_path):
    output = tmp_path / "analysis.json"
    fake = FakeRun(lines=["PROGRESS 0.500 onsets を実行中"], writes=output)
    monkeypatch.setattr(audio_analysis, "_run", fake)
    seen: list[tuple[float | None, str]] = []

    async def on_progress(fraction, message):
        seen.append((fraction, message))

    saved = await audio_analysis.analyze(
        "job1",
        audio=tmp_path / "song.wav",
        output=output,
        tasks=["onsets"],
        on_progress=on_progress,
    )
    assert saved == output
    assert seen == [(0.5, "onsets を実行中")]



async def test_a_success_without_a_file_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_analysis, "_run", FakeRun())
    with pytest.raises(audio_analysis.AudioAnalysisError, match="出力ファイル"):
        await audio_analysis.analyze(
            "job1", audio=tmp_path / "song.wav",
            output=tmp_path / "analysis.json", tasks=["silence"],
        )



async def test_the_lyrics_file_is_written_and_removed(monkeypatch, tmp_path):
    """歌詞は改行を含むので引数ではなくファイルで渡し、終わったら消す。"""
    monkeypatch.setattr(audio_analysis, "AUDIO_ANALYSIS_TMP_DIR", tmp_path / "tmp")
    output = tmp_path / "analysis.json"
    fake = FakeRun(writes=output)
    monkeypatch.setattr(audio_analysis, "_run", fake)
    await audio_analysis.analyze(
        "job1", audio=tmp_path / "song.wav", output=output,
        tasks=["align"], lyrics="1 行目\n2 行目\n",
    )
    argv = fake.calls[0]
    lyrics_file = Path(argv[argv.index("--lyrics") + 1])
    assert lyrics_file.parent == tmp_path / "tmp"
    assert not lyrics_file.exists()


# --------------------------------------------------------------------------
# ジョブ経路（mode: "audio_analysis"）
# --------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    """DB・outputs をテスト用に閉じ込めた TestClient（ComfyUI は一切使わない）。"""
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    assets = tmp_path / "assets"
    assets.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(jobs, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)
    monkeypatch.setattr(audio_analysis, "AUDIO_ANALYSIS_TMP_DIR", tmp_path / "tmp")
    # 素材の指し方（MediaRef）が見る置き場もテスト用に寄せる
    monkeypatch.setattr(
        media_ref, "URL_ROOTS",
        {"/outputs/": outputs, "/library/": library, "/assets/": assets},
    )
    monkeypatch.setattr(nsfw, "classify", _no_llm)

    song = assets / "song.wav"
    song.write_bytes(b"RIFF....WAVE")
    with TestClient(app) as client:
        yield type(
            "Env",
            (),
            {"client": client, "outputs": outputs, "assets": assets, "song": song},
        )


def body(env, **analysis) -> dict:
    payload = {"audio": {"path": str(env.song)}}
    payload.update(analysis)
    return {"mode": "audio_analysis", "analysis": payload}


def wait_for(client, job_id, timeout=30.0):
    deadline = time.time() + timeout
    job: dict = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "canceled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} stuck in {job.get('status')!r}")


def stub_worker(monkeypatch, *, code: int = 0, stderr: str = "") -> list[list[str]]:
    """ワーカーのサブプロセスを丸ごと差し替える（analysis.json だけ置く）。"""
    calls: list[list[str]] = []

    async def fake_run(argv, *, timeout=None, on_line=None):
        calls.append([str(arg) for arg in argv])
        if on_line is not None:
            await on_line("PROGRESS 0.500 silence を実行中")
        if "--check" in argv:
            # 依存の事前確認は「入っていない（3）」以外は素通し
            return (code if code == 3 else 0), "", stderr
        if code == 0:
            out = Path(argv[argv.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps({"duration": 3.0, "lines": [], "sections": []}),
                encoding="utf-8",
            )
        return code, "", stderr

    monkeypatch.setattr(audio_analysis, "_run", fake_run)
    return calls


def test_the_job_writes_analysis_json_and_exposes_its_url(env, monkeypatch):
    calls = stub_worker(monkeypatch)

    response = env.client.post(
        "/api/jobs", json=body(env, lyrics="今日も見張ってる\n", tasks=["align"])
    )
    assert response.status_code == 201, response.text
    created = response.json()
    # 絵を作らないジョブなので NSFW は判定に掛けず false で確定（SPEC §5.2）
    assert created["nsfw"] is False and created["nsfw_source"] == ""

    job = wait_for(env.client, created["id"])
    assert job["status"] == "done", job["error"]
    assert job["analysis_url"] == f"/outputs/{job['id']}/analysis.json"
    assert Path(job["analysis_path"]).is_file()
    assert job["video_url"] is None and job["image_url"] is None
    # 何を頼んだかは workflow_json に残る（ComfyUI のグラフと同じ役目）
    assert job["workflow_json"]["audio_analysis"]["tasks"] == ["align"]

    # 依存の事前確認（--check）と本番の 2 回だけ走る
    assert [argv for argv in calls if "--check" in argv]
    run = [argv for argv in calls if "--check" not in argv][0]
    assert run[run.index("--audio") + 1] == str(env.song)
    assert run[run.index("--tasks") + 1] == "align"


def test_a_missing_dependency_is_400_at_submission(env, monkeypatch):
    """走らせる前に断る（履歴に無駄な失敗ジョブを残さない）。"""
    stub_worker(monkeypatch, code=3, stderr="stable-ts が入っていません")
    response = env.client.post("/api/jobs", json=body(env, lyrics="歌詞\n"))
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "stable-ts" in detail and "audio_analysis_python" in detail
    assert env.client.get("/api/jobs").json() == []


def test_the_audio_is_required(env):
    response = env.client.post("/api/jobs", json={"mode": "audio_analysis"})
    assert response.status_code == 422, response.text
    assert "analysis.audio" in response.text


def test_analysis_belongs_to_its_own_mode(env):
    response = env.client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_prompt": "a cat",
            "analysis": {"audio": {"path": "song.wav"}},
        },
    )
    assert response.status_code == 422, response.text


def test_a_missing_audio_file_is_rejected(env, monkeypatch):
    stub_worker(monkeypatch)
    response = env.client.post(
        "/api/jobs",
        json={
            "mode": "audio_analysis",
            "analysis": {"audio": {"path": str(env.assets / "nope.wav")}},
        },
    )
    assert response.status_code == 422, response.text


def test_only_silence_needs_no_lyrics(env, monkeypatch):
    """歌詞なし・``tasks: ["silence"]`` は依存が無くても通る道。"""
    calls = stub_worker(monkeypatch)
    response = env.client.post("/api/jobs", json=body(env, tasks=["silence"]))
    assert response.status_code == 201, response.text
    job = wait_for(env.client, response.json()["id"])
    assert job["status"] == "done", job["error"]
    check = [argv for argv in calls if "--check" in argv][0]
    assert check[check.index("--tasks") + 1] == "silence"
    assert "--lyrics" not in check


def test_a_failing_worker_fails_the_job(env, monkeypatch):
    stub_worker(monkeypatch, code=1, stderr="RuntimeError: CUDA out of memory")
    response = env.client.post("/api/jobs", json=body(env, tasks=["beats"]))
    assert response.status_code == 201, response.text
    job = wait_for(env.client, response.json()["id"])
    assert job["status"] == "failed"
    assert "CUDA out of memory" in job["error"]


def test_the_job_can_be_rerun(env, monkeypatch):
    """params に解析の指定が残るので、同じ音源で焼き直せる。"""
    stub_worker(monkeypatch)
    created = env.client.post("/api/jobs", json=body(env, tasks=["silence"])).json()
    assert wait_for(env.client, created["id"])["status"] == "done"

    response = env.client.post(f"/api/jobs/{created['id']}/rerun")
    assert response.status_code == 201, response.text
    again = wait_for(env.client, response.json()["id"])
    assert again["status"] == "done", again["error"]
    assert again["analysis_url"] == f"/outputs/{again['id']}/analysis.json"


def test_the_external_api_reports_the_analysis_url(env, monkeypatch):
    stub_worker(monkeypatch)
    env.client.put("/api/settings", json={"external_api_key": KEY})
    created = env.client.post(
        "/api/v1/jobs", headers={"X-API-Key": KEY}, json=body(env, tasks=["silence"])
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]
    assert wait_for(env.client, job_id)["status"] == "done"

    response = env.client.get(f"/api/v1/jobs/{job_id}", headers={"X-API-Key": KEY})
    assert response.status_code == 200, response.text
    assert response.json()["analysis_url"] == f"/outputs/{job_id}/analysis.json"
