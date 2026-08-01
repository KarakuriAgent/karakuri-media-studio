"""kie.ai バックエンドの共通基盤（SPEC §5.2）。

ネットワークには一切出ない: ``httpx.AsyncClient`` を偽物に差し替え、URL で
どの API を呼んでいるかを見分けて用意した応答を返す。実在の kie ワークフローは
まだ無いので、テスト用のスタブ・マニフェストを登録簿に差し込んで基盤を検証する。
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import backends, config, db, jobs, kie, nsfw, workflows
from app.backends import BackendStatus
from app.main import app
from app.models import GenerationParams, Settings
from app.workflows import KieTask, SelectSpec, WorkflowSpec

API_KEY = "kie-test-key"


# --------------------------------------------------------------------------
# 偽の kie.ai
# --------------------------------------------------------------------------

def envelope(data, code: int = 200, msg: str = "success") -> dict:
    return {"code": code, "msg": msg, "data": data}


class FakeResponse:
    def __init__(self, payload=None, *, status: int = 200, text: str = "",
                 chunks: tuple[bytes, ...] = ()):
        self._payload = payload
        self.status_code = status
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self._chunks = chunks

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "https://kie.invalid/x"),
                response=httpx.Response(self.status_code),
            )

    async def aiter_bytes(self, chunk_size=None):
        for chunk in self._chunks:
            yield chunk


class _Stream:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc_info):
        return False


class FakeKie:
    """kie.ai の HTTP をまるごと肩代わりする。

    どの API かは URL で見分ける（``create`` / ``record`` / ``credit`` /
    ``upload`` / ``download``）。用意した応答は順に返し、尽きたら最後のものを
    使い回す（ポーリングの繰り返しをそのまま書けるように）。
    """

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.answers: dict[str, list] = {
            "credit": [FakeResponse(envelope(120.5))],
            "download": [FakeResponse(chunks=(b"binary",))],
        }

    @staticmethod
    def kind(url: str) -> str:
        # 旧専用系（Veo / Suno）は別のパスだが、テストからは同じ
        # 「作る / 見に行く」。Suno は record-info が生成 URL の下にぶら下がる
        # （`/api/v1/generate/record-info`）ので、record を先に見分ける。
        if "recordInfo" in url or "record-info" in url:
            return "record"
        # 生成済みタスクへの追加操作（issue #26）。延長は新しいタスクを作るので
        # create と同じ扱い、1080P 取得はタスクを作らないので独立した種別。
        if "get-1080p-video" in url:
            return "veo_1080p"
        if (
            "createTask" in url
            or url.endswith("/veo/generate")
            or url.endswith("/veo/extend")
            or url.endswith("/api/v1/generate")
        ):
            return "create"
        if "credit" in url:
            return "credit"
        if "file-base64-upload" in url:
            return "upload"
        return "download"

    def answer(self, kind: str, *responses) -> None:
        self.answers[kind] = list(responses)

    def _next(self, kind: str):
        queue = self.answers.get(kind)
        if not queue:
            raise AssertionError(f"no fake answer prepared for {kind}")
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def sent(self, kind: str) -> list[dict]:
        return [call[2] for call in self.calls if call[0] == kind]

    # --- httpx.AsyncClient の代役 -------------------------------------
    def client(self):
        fake = self

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def request(self, method, url, headers=None, **kwargs):
                kind = fake.kind(url)
                fake.calls.append((kind, url, {"headers": dict(headers or {}), **kwargs}))
                answer = fake._next(kind)
                if isinstance(answer, Exception):
                    raise answer
                return answer

            def stream(self, method, url, **kwargs):
                kind = fake.kind(url)
                fake.calls.append((kind, url, dict(kwargs)))
                return _Stream(fake._next(kind))

        return FakeClient


@pytest.fixture
def fake_kie(monkeypatch, tmp_path):
    """API キーが設定済みで、HTTP が偽物に差し替わった状態。"""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_settings", Settings(kie_api_key=API_KEY))
    fake = FakeKie()
    monkeypatch.setattr(kie.httpx, "AsyncClient", fake.client())
    # ポーリングは即座に回す（間隔のロジックは _sleeps で別に見る）
    monkeypatch.setattr(kie, "POLL_INTERVAL", 0.0)
    monkeypatch.setattr(kie, "MAX_POLL_INTERVAL", 0.0)
    yield fake
    config._settings = None


def record(state: str, **extra) -> FakeResponse:
    return FakeResponse(envelope({"state": state, **extra}))


def success(urls, credits=None) -> FakeResponse:
    data = {"state": "success", "resultJson": json.dumps({"resultUrls": list(urls)})}
    if credits is not None:
        data["creditsConsumed"] = credits
    return FakeResponse(envelope(data))


# --------------------------------------------------------------------------
# テスト用のスタブ・ワークフロー
# --------------------------------------------------------------------------

STUB_IMAGE = WorkflowSpec(
    id="kie_stub_image",
    label="kie スタブ画像",
    kind="image",
    backend="kie",
    family="kie",
    description="テスト用のスタブ（実在しないモデル）。",
    kie=KieTask(
        model="stub/image",
        fields={
            "prompt": "prompt",
            "aspect_ratio": "aspect_ratio",
            "image": "image_url",
            "select:style": "style",
        },
        constants={"output_format": "png"},
        credits=8.0,
    ),
    selects={
        "style": SelectSpec(label="スタイル", choices=("anime", "photo")),
    },
)

STUB_VIDEO = WorkflowSpec(
    id="kie_stub_video",
    label="kie スタブ動画",
    kind="video",
    backend="kie",
    family="kie",
    description="テスト用のスタブ（実在しないモデル）。",
    prompt_hint="Describe the shot in one English sentence.",
    accepts_start_image=True,
    kie=KieTask(
        model="stub/video",
        fields={"prompt": "prompt", "duration": "duration", "image": "image_url"},
    ),
)


@pytest.fixture
def stub_workflows(monkeypatch):
    """スタブを登録簿に差し込む（実運用の SPECS は触らない）。"""
    monkeypatch.setattr(workflows, "SPECS", (*workflows.SPECS, STUB_IMAGE, STUB_VIDEO))
    for spec in (STUB_IMAGE, STUB_VIDEO):
        monkeypatch.setitem(workflows.BY_ID, spec.id, spec)
    return STUB_IMAGE


def mark_available(monkeypatch, state: str = "ok") -> None:
    """kie の確認結果を（ネットワークに出ずに）決め打ちする。"""
    monkeypatch.setitem(
        backends._status, "kie", BackendStatus("kie", state, "テスト")
    )


# --------------------------------------------------------------------------
# resultJson の二重パース
# --------------------------------------------------------------------------

def test_result_json_is_parsed_twice():
    """Market 系の ``resultJson`` は JSON 文字列（さらに二重のこともある）。"""
    inner = {"resultUrls": ["https://cdn.kie.ai/a.mp4"]}
    assert kie.parse_result_json(inner) == inner
    assert kie.parse_result_json(json.dumps(inner)) == inner
    assert kie.parse_result_json(json.dumps(json.dumps(inner))) == inner


def test_unreadable_result_json_is_empty():
    assert kie.parse_result_json("") == {}
    assert kie.parse_result_json("not json") == {}
    assert kie.parse_result_json(None) == {}


# --------------------------------------------------------------------------
# 認証
# --------------------------------------------------------------------------

def test_settings_key_wins_over_the_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_settings", Settings(kie_api_key="from-settings"))
    monkeypatch.setenv(kie.API_KEY_ENV, "from-env")
    assert kie.api_key() == "from-settings"
    config._settings = None


def test_the_environment_is_the_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_settings", Settings())
    monkeypatch.setenv(kie.API_KEY_ENV, "from-env")
    assert kie.api_key() == "from-env"
    assert kie.configured()
    config._settings = None


def test_without_a_key_nothing_is_sent(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_settings", Settings())
    with pytest.raises(kie.KieNotConfigured):
        asyncio.run(kie.get_credits())
    config._settings = None


def test_requests_carry_the_bearer_token(fake_kie):
    asyncio.run(kie.get_credits())
    headers = fake_kie.calls[0][2]["headers"]
    assert headers["Authorization"] == f"Bearer {API_KEY}"


# --------------------------------------------------------------------------
# createTask -> recordInfo
# --------------------------------------------------------------------------

def test_a_task_runs_to_completion(fake_kie):
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "task-1"})))
    fake_kie.answer(
        "record",
        record("queuing"),
        record("generating"),
        success(["https://cdn.kie.ai/out.png"], credits=12),
    )
    labels: list[str] = []

    async def run():
        task_id = await kie.create_task("stub/image", {"prompt": "a cat"})
        return await kie.wait_for_task(
            task_id, on_progress=lambda state: _collect(labels, state)
        )

    state = asyncio.run(run())

    assert state.phase == "success"
    assert state.result_urls == ("https://cdn.kie.ai/out.png",)
    assert state.credits == 12.0
    # 進捗は状態が変わったときだけ流す
    assert labels == ["queuing", "generating"]
    body = fake_kie.sent("create")[0]["json"]
    assert body == {"model": "stub/image", "input": {"prompt": "a cat"}}
    assert fake_kie.sent("record")[0]["params"] == {"taskId": "task-1"}


async def _collect(sink, state):
    sink.append(state.label)


def test_a_failed_task_reports_the_reason(fake_kie):
    fake_kie.answer("record", record("fail", failMsg="prompt was rejected", failCode=501))

    with pytest.raises(kie.KieError) as caught:
        asyncio.run(kie.wait_for_task("task-1"))

    assert "prompt was rejected" in str(caught.value)
    assert "生成に失敗" in str(caught.value)


def test_rate_limits_back_off_instead_of_failing(fake_kie, monkeypatch):
    """429 はキューに入らないので、間隔を伸ばして数え直す（失敗にしない）。"""
    monkeypatch.setattr(kie, "POLL_INTERVAL", 1.0)
    monkeypatch.setattr(kie, "MAX_POLL_INTERVAL", 8.0)
    waits: list[float] = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(kie.asyncio, "sleep", fake_sleep)
    fake_kie.answer(
        "record",
        FakeResponse(status=429, text="Too Many Requests"),
        FakeResponse(status=429, text="Too Many Requests"),
        success(["https://cdn.kie.ai/out.png"]),
    )

    state = asyncio.run(kie.wait_for_task("task-1"))

    assert state.phase == "success"
    # 1s -> 2s -> 4s（429 のたびに倍。成功したら既定間隔に戻る）
    assert waits == [1.0, 2.0, 4.0]


def test_the_poll_interval_is_capped(fake_kie, monkeypatch):
    monkeypatch.setattr(kie, "POLL_INTERVAL", 10.0)
    monkeypatch.setattr(kie, "MAX_POLL_INTERVAL", 30.0)
    waits: list[float] = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(kie.asyncio, "sleep", fake_sleep)
    fake_kie.answer(
        "record",
        *[FakeResponse(status=429, text="429")] * 4,
        success(["https://cdn.kie.ai/out.png"]),
    )

    asyncio.run(kie.wait_for_task("task-1"))

    assert max(waits) == 30.0


def test_a_task_that_never_finishes_times_out(fake_kie):
    fake_kie.answer("record", record("generating"))

    with pytest.raises(kie.KieError) as caught:
        asyncio.run(kie.wait_for_task("task-1", timeout=0.0))

    assert "秒以内に終わりませんでした" in str(caught.value)


def test_an_error_envelope_is_reported_with_its_hint(fake_kie):
    """HTTP 200 でも封筒の code がエラーのことがある。"""
    fake_kie.answer("create", FakeResponse(envelope(None, code=402, msg="no credits")))

    with pytest.raises(kie.KieError) as caught:
        asyncio.run(kie.create_task("stub/image", {}))

    assert "クレジットが不足" in str(caught.value)


def test_an_unknown_api_family_is_refused():
    with pytest.raises(kie.KieError):
        kie.task_api("udio")


# --------------------------------------------------------------------------
# ファイル
# --------------------------------------------------------------------------

def test_input_files_are_uploaded_as_base64(fake_kie, tmp_path):
    source = tmp_path / "start.png"
    source.write_bytes(b"\x89PNG")
    fake_kie.answer(
        "upload", FakeResponse(envelope({"fileUrl": "https://files.kie.ai/start.png"}))
    )

    url = asyncio.run(kie.upload_file(source))

    assert url == "https://files.kie.ai/start.png"
    body = fake_kie.sent("upload")[0]["json"]
    assert body["base64Data"].startswith("data:image/png;base64,")
    assert body["fileName"] == "start.png"


def test_results_are_downloaded_immediately(fake_kie, tmp_path):
    """成果物は 14 日で消えるので、完了を検知したらすぐ手元に落とす。"""
    state = kie.TaskState(
        "success",
        "success",
        ("https://cdn.kie.ai/a.mp4?token=x", "https://cdn.kie.ai/b.mp4"),
    )

    saved = asyncio.run(kie.download_results(state, tmp_path, "video", "video"))

    assert [path.name for path in saved] == ["video.mp4", "video_2.mp4"]
    assert saved[0].read_bytes() == b"binary"


def test_a_url_without_an_extension_falls_back_to_the_kind(fake_kie, tmp_path):
    state = kie.TaskState("success", "success", ("https://cdn.kie.ai/download/abc",))
    saved = asyncio.run(kie.download_results(state, tmp_path, "image", "image"))
    assert saved[0].name == "image.png"


# --------------------------------------------------------------------------
# マニフェスト -> タスク入力
# --------------------------------------------------------------------------

def _params(**overrides) -> GenerationParams:
    base = dict(
        mode="image_only",
        job_id="job-1",
        image_workflow=STUB_IMAGE.id,
        image_prompt="a cat",
        aspect_ratio="16:9 (Widescreen)",
        negative_prompt="blurry",
    )
    base.update(overrides)
    return GenerationParams(**base)


def test_the_manifest_decides_the_task_input():
    payload = kie.task_input(
        STUB_IMAGE, _params(), {"image": "https://files.kie.ai/start.png"}
    )
    assert payload == {
        "output_format": "png",  # constants
        "prompt": "a cat",
        "aspect_ratio": "16:9 (Widescreen)",
        "image_url": "https://files.kie.ai/start.png",
        "style": "anime",  # 未指定の選択項目は既定値
    }
    # 宣言していない値（negative_prompt）は送らない
    assert "negative_prompt" not in payload


def test_selected_values_and_empty_values():
    payload = kie.task_input(STUB_IMAGE, _params(selects={"style": "photo"}), {})
    assert payload["style"] == "photo"
    # 画像を渡していないのでキーごと落ちる（空文字を送らない）
    assert "image_url" not in payload


def test_the_request_records_what_was_sent():
    request = kie.build_request(STUB_IMAGE, _params(), {})
    assert request.as_dict()["model"] == "stub/image"
    assert request.as_dict()["api"] == "market"


# --------------------------------------------------------------------------
# マニフェスト検証
# --------------------------------------------------------------------------

def test_the_shipped_manifests_are_valid():
    assert workflows.validate_specs(use_cache=False) == []


def test_a_stub_manifest_is_valid():
    assert workflows.validate_external_spec(STUB_IMAGE) == []
    assert workflows.validate_external_spec(STUB_VIDEO) == []


def test_an_unknown_input_name_is_reported():
    broken = WorkflowSpec(
        id="broken",
        label="broken",
        kind="image",
        backend="kie",
        description="x",
        kie=KieTask(model="m", fields={"nonsense": "key"}),
    )
    problems = workflows.validate_external_spec(broken)
    assert any("nonsense" in problem for problem in problems)


def test_a_kie_manifest_without_a_task_is_reported():
    broken = WorkflowSpec(id="broken", label="broken", kind="image",
                          backend="kie", description="x")
    assert any("KieTask" in problem for problem in workflows.validate_external_spec(broken))


def test_a_comfy_manifest_may_not_declare_a_kie_task():
    broken = WorkflowSpec(id="broken", label="broken", kind="image",
                          description="x", kie=KieTask(model="m"))
    assert any("KieTask" in problem for problem in workflows.validate_spec(broken))


# --------------------------------------------------------------------------
# 可用性（選択肢に出るかどうか）
# --------------------------------------------------------------------------

def test_workflows_are_hidden_until_the_key_is_verified(stub_workflows):
    """未確認・未設定のあいだは kie 系を一切出さない（SPEC §5.2）。"""
    assert [spec.id for spec in workflows.image_specs() if spec.backend == "kie"] == []


def test_verified_workflows_become_selectable(stub_workflows, monkeypatch):
    mark_available(monkeypatch)
    assert STUB_IMAGE.id in [spec.id for spec in workflows.image_specs()]
    # カタログ（エージェントのプロンプト）にも同じ基準で載る
    assert STUB_IMAGE.id in [entry.id for entry in workflows.image_catalog()]


def test_an_invalid_key_keeps_the_workflows_hidden(stub_workflows, monkeypatch):
    mark_available(monkeypatch, "error")
    assert STUB_IMAGE.id not in [spec.id for spec in workflows.image_specs()]


def test_the_check_uses_the_credit_endpoint(fake_kie):
    status = asyncio.run(backends.refresh("kie"))
    assert status.state == "ok"
    assert "120.5" in status.detail
    assert fake_kie.calls[0][0] == "credit"


def test_a_rejected_key_is_reported_as_an_error(fake_kie):
    fake_kie.answer("credit", FakeResponse(status=401, text="unauthorized"))
    status = asyncio.run(backends.refresh("kie"))
    assert status.state == "error"
    assert "API キーが正しくありません" in status.detail
    assert not backends.available("kie")


def test_an_unconfigured_backend_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "_settings", Settings())
    status = asyncio.run(backends.refresh("kie"))
    assert status.state == "not_configured"
    assert not backends.available("kie")
    config._settings = None


def test_comfyui_is_always_selectable():
    assert backends.available("comfyui")


def test_a_future_backend_is_not_available_yet():
    assert not backends.available("grok_cli")
    assert backends.status("grok_cli").state == "not_configured"


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

@pytest.fixture
def client(fake_kie, stub_workflows, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with TestClient(app) as test_client:
        yield test_client


def test_credits_are_exposed(client):
    body = client.get("/api/kie/credits").json()
    assert body == {"configured": True, "credits": 120.5, "error": None}


def test_credit_failures_do_not_break_the_endpoint(client, fake_kie):
    fake_kie.answer("credit", FakeResponse(status=455, text="maintenance"))
    body = client.get("/api/kie/credits").json()
    assert body["configured"] is True
    assert body["credits"] is None
    assert "メンテナンス" in body["error"]


def test_the_check_endpoint_refreshes_the_availability(client, fake_kie):
    fake_kie.answer("credit", FakeResponse(status=401, text="nope"))
    body = client.post("/api/kie/check").json()
    assert body["error"]
    assert not backends.available("kie")

    fake_kie.answer("credit", FakeResponse(envelope(50)))
    body = client.post("/api/kie/check").json()
    assert body["credits"] == 50
    assert backends.available("kie")


def test_health_reports_the_backend(client):
    status = client.get("/api/health").json()["kie"]
    assert status["status"] == "ok"
    assert "残クレジット" in status["detail"]


def test_options_lists_only_verified_backends(client, monkeypatch):
    ids = [wf["id"] for wf in client.get("/api/options").json()["image_workflows"]]
    assert STUB_IMAGE.id in ids

    backends.invalidate()
    monkeypatch.setattr(config, "_settings", Settings())  # キーを外す
    body = client.get("/api/options").json()
    assert STUB_IMAGE.id not in [wf["id"] for wf in body["image_workflows"]]
    kie_status = [b for b in body["backends"] if b["backend"] == "kie"][0]
    assert kie_status["status"] == "not_configured"
    assert kie_status["available"] is False


def test_saving_the_key_makes_the_workflows_selectable(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_settings", Settings())
    backends.invalidate()
    assert STUB_IMAGE.id not in [
        wf["id"] for wf in client.get("/api/options").json()["image_workflows"]
    ]

    saved = client.put("/api/settings", json={"kie_api_key": API_KEY}).json()

    assert saved["kie_api_key"] == API_KEY
    # 保存のたびに確認し直すので、次の /api/options から選べる
    assert backends.available("kie")
    assert STUB_IMAGE.id in [
        wf["id"] for wf in client.get("/api/options").json()["image_workflows"]
    ]


# --------------------------------------------------------------------------
# ジョブ実行
# --------------------------------------------------------------------------

@pytest.fixture
def job_env(client, tmp_path, monkeypatch):
    """kie のジョブを 1 本走らせられる状態（DB / outputs / 判定を隔離）。"""
    outputs = tmp_path / "outputs"
    assets = tmp_path / "assets"
    (assets / "image").mkdir(parents=True)
    outputs.mkdir()
    monkeypatch.setattr(jobs, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)

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


def test_a_kie_job_runs_end_to_end(client, fake_kie, job_env):
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "task-9"})))
    fake_kie.answer(
        "record",
        record("queuing"),
        success(["https://cdn.kie.ai/out.png"], credits=8),
    )

    created = client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": STUB_IMAGE.id,
            "image_prompt": "a cat on a roof",
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])

    assert job["status"] == "done", job["error"]
    # 成果物は手元の outputs/{job_id}/ に落ちている
    saved = job_env / job["id"] / "image.png"
    assert saved.is_file()
    assert job["image_url"] == f"/outputs/{job['id']}/image.png"
    # 消費クレジットが履歴に残る
    assert job["credits_consumed"] == 8.0
    # 何を送ったかが再現できる形で残る
    stage = job["workflow_json"]["image"]
    assert stage["backend"] == "kie"
    assert stage["task_id"] == "task-9"
    assert stage["request"]["model"] == "stub/image"
    # 選択項目は既定値が入る（選択式は動画ワークフロー向けの仕組み、SPEC §3.1）
    assert stage["request"]["input"]["style"] == "anime"


def test_a_failed_kie_task_fails_the_job(client, fake_kie, job_env):
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "task-9"})))
    fake_kie.answer("record", record("fail", failMsg="content policy"))

    created = client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": STUB_IMAGE.id,
            "image_prompt": "a cat",
        },
    )
    job = wait_for(client, created.json()["id"])

    assert job["status"] == "failed"
    assert "content policy" in job["error"]
    # 失敗したタスクは kie 側で返金されるので、クレジットは記録しない
    assert job["credits_consumed"] is None


def test_an_unimplemented_bridge_is_refused(client, job_env):
    """kie で画像 → ComfyUI で動画、の受け渡しはまだ実装していない（§5.2）。"""
    created = client.post(
        "/api/jobs",
        json={
            "mode": "full",
            "image_workflow": STUB_IMAGE.id,  # kie.ai
            "video_workflow": "tx2_3_i2v",  # ComfyUI
            "image_prompt": "a cat",
            "video_prompt": "it walks",
        },
    )
    assert created.status_code == 422
    assert "受け渡し" in created.json()["detail"]


def test_an_unverified_backend_is_refused_at_creation(client, monkeypatch, tmp_path):
    """キーを外したあと投入しても、失敗ジョブではなく 422 で止める。"""
    backends.invalidate()
    monkeypatch.setattr(config, "_settings", Settings())
    created = client.post(
        "/api/jobs",
        json={
            "mode": "image_only",
            "image_workflow": STUB_IMAGE.id,
            "image_prompt": "a cat",
        },
    )
    assert created.status_code == 422
    assert "使えません" in created.json()["detail"]


# --------------------------------------------------------------------------
# Veo 3.1（旧専用系 API、issue #17）
# --------------------------------------------------------------------------

VEO_FAST = workflows.BY_ID["veo3_1_fast"]
VEO_QUALITY = workflows.BY_ID["veo3_1_quality"]


def veo_record(flag: int, urls=(), **extra) -> FakeResponse:
    """``/veo/record-info`` の応答（成果物は ``response.resultUrls``）。"""
    data = {"taskId": "veo-1", "successFlag": flag, **extra}
    if urls:
        data["response"] = {"resultUrls": list(urls)}
    return FakeResponse(envelope(data))


def _video_params(**overrides) -> GenerationParams:
    base = dict(
        mode="i2v",
        job_id="job-veo",
        video_workflow=VEO_FAST.id,
        video_prompt="A medium shot of a woman on a rooftop.",
    )
    base.update(overrides)
    return _params(**base)


def test_veo_uses_its_own_endpoints():
    api = kie.task_api("veo")
    assert api.create_url.endswith("/api/v1/veo/generate")
    assert api.record_url.endswith("/api/v1/veo/record-info")
    assert VEO_FAST.kie.model == "veo3_fast"
    assert VEO_QUALITY.kie.model == "veo3"
    assert VEO_FAST.kie.api == "veo" and VEO_QUALITY.kie.api == "veo"


def test_a_start_frame_becomes_one_image_url():
    request = kie.build_request(
        VEO_FAST, _video_params(), {"image": "https://files.kie.ai/start.png"}
    )
    body = kie.task_api(request.api).create_body(request.model, request.input)

    assert body["model"] == "veo3_fast"
    assert body["imageUrls"] == ["https://files.kie.ai/start.png"]
    # 1 枚は開始フレーム扱い（旧 API では TEXT_2_VIDEO のまま）
    assert body["generationType"] == "TEXT_2_VIDEO"
    assert body["prompt"].startswith("A medium shot")
    # 既定値がドキュメント内で食い違うので明示して送る
    assert body["enableTranslation"] is True
    # 選択式の既定値。尺は数値で送る（選択肢は文字列で届く）
    assert body["aspect_ratio"] == "16:9"
    assert body["resolution"] == "720p"
    assert body["duration"] == 8


def test_first_and_last_frames_become_two_image_urls():
    """flf2v 相当: 2 枚目が最後のフレーム（並びに意味がある）。"""
    request = kie.build_request(
        VEO_QUALITY,
        _video_params(
            video_workflow=VEO_QUALITY.id,
            selects={"aspect_ratio": "9:16", "duration": "6", "resolution": "1080p"},
        ),
        {
            "image": "https://files.kie.ai/first.png",
            "end_image": "https://files.kie.ai/last.png",
        },
    )
    body = kie.task_api(request.api).create_body(request.model, request.input)

    assert body["imageUrls"] == [
        "https://files.kie.ai/first.png",
        "https://files.kie.ai/last.png",
    ]
    assert body["generationType"] == "FIRST_AND_LAST_FRAMES_2_VIDEO"
    assert body["aspect_ratio"] == "9:16"
    assert body["duration"] == 6
    assert body["resolution"] == "1080p"


def test_veo_can_be_generated_straight_at_4k():
    """``4k`` は generate API が受ける値（8 秒生成のときのみ・高価）。"""
    request = kie.build_request(
        VEO_QUALITY,
        _video_params(
            video_workflow=VEO_QUALITY.id,
            selects={"resolution": "4k", "duration": "8"},
        ),
        {},
    )
    body = kie.task_api(request.api).create_body(request.model, request.input)

    assert body["resolution"] == "4k"
    assert body["duration"] == 8


def test_without_an_image_veo_is_text_to_video():
    request = kie.build_request(VEO_FAST, _video_params(), {})
    body = kie.task_api(request.api).create_body(request.model, request.input)
    assert "imageUrls" not in body
    assert body["generationType"] == "TEXT_2_VIDEO"


def test_veo_polls_until_the_success_flag_flips(fake_kie):
    """``successFlag`` 0 = 生成中 / 1 = 成功（Market 系の state 語彙ではない）。"""
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "veo-1"})))
    fake_kie.answer(
        "record",
        veo_record(0),
        veo_record(0),
        veo_record(1, ("https://cdn.kie.ai/veo.mp4",)),
    )
    labels: list[str] = []

    async def run():
        task_id = await kie.create_task(
            "veo3_fast", {"prompt": "a rooftop"}, api=kie.VEO
        )
        return await kie.wait_for_task(
            task_id, api=kie.VEO, on_progress=lambda state: _collect(labels, state)
        )

    state = asyncio.run(run())

    assert state.phase == "success"
    assert state.result_urls == ("https://cdn.kie.ai/veo.mp4",)
    # 生成中は 1 語しかないので、進捗は 1 度だけ流れる
    assert labels == ["generating"]
    # ボディは平ら（input で包まない）
    assert fake_kie.sent("create")[0]["json"]["prompt"] == "a rooftop"
    assert fake_kie.sent("record")[0]["params"] == {"taskId": "veo-1"}


def test_a_failed_veo_task_reports_the_reason(fake_kie):
    fake_kie.answer(
        "record",
        veo_record(2, errorMessage="rejected by Flow", errorCode=422),
    )

    with pytest.raises(kie.KieError) as caught:
        asyncio.run(kie.wait_for_task("veo-1", api=kie.VEO))

    assert "rejected by Flow" in str(caught.value)
    assert "受け付けられませんでした" in str(caught.value)


def test_a_veo_success_without_urls_is_an_error(fake_kie):
    fake_kie.answer("record", veo_record(1))
    with pytest.raises(kie.KieError) as caught:
        asyncio.run(kie.wait_for_task("veo-1", api=kie.VEO))
    assert "成果物 URL" in str(caught.value)


def test_a_full_job_feeds_the_generated_image_to_veo(client, fake_kie, job_env,
                                                     monkeypatch):
    """full モード: 1 段目の画像が Veo の開始フレーム（imageUrls）になる。"""
    # 落としてくるのは偽のバイト列なので、ffmpeg は通さない
    async def fake_last_frame(video, dest):
        dest.write_bytes(b"png")
        return dest

    monkeypatch.setattr(jobs, "extract_last_frame", fake_last_frame)
    fake_kie.answer(
        "create",
        FakeResponse(envelope({"taskId": "task-img"})),
        FakeResponse(envelope({"taskId": "task-veo"})),
    )
    fake_kie.answer(
        "record",
        success(["https://cdn.kie.ai/out.png"], credits=8),
        veo_record(1, ("https://cdn.kie.ai/out.mp4",)),
    )
    fake_kie.answer(
        "upload", FakeResponse(envelope({"fileUrl": "https://files.kie.ai/start.png"}))
    )

    created = client.post(
        "/api/jobs",
        json={
            "mode": "full",
            "image_workflow": STUB_IMAGE.id,
            "video_workflow": VEO_FAST.id,
            "image_prompt": "a cat on a roof",
            "video_prompt": "The cat stretches and yawns.",
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])

    assert job["status"] == "done", job["error"]
    assert (job_env / job["id"] / "video.mp4").is_file()
    # 2 段目は 1 段目の成果物をアップロードして開始フレームに使う
    stage = job["workflow_json"]["video"]
    assert stage["task_id"] == "task-veo"
    assert stage["request"]["api"] == "veo"
    assert stage["request"]["input"]["imageUrls"] == ["https://files.kie.ai/start.png"]
    body = fake_kie.sent("create")[1]["json"]
    assert body["model"] == "veo3_fast"
    assert body["generationType"] == "TEXT_2_VIDEO"


def test_veo_is_offered_as_a_video_workflow(client, monkeypatch):
    mark_available(monkeypatch)
    body = client.get("/api/options").json()
    veo = [wf for wf in body["video_workflows"] if wf["id"] == VEO_FAST.id][0]
    # 画像は必須ではないが受け取れる（フォームは supports を見て欄を出す）
    assert veo["requires"] == []
    # Fast は素材参照生成（REFERENCE_2_VIDEO）も受け取る
    assert set(veo["supports"]) == {
        "prompt", "image", "end_image", "reference_images",
    }
    assert veo["multi_inputs"] == {"reference_images": 3}
    assert veo["reference_selects"] == {"duration": "8"}
    assert veo["accepts_start_image"] is True
    assert [select["name"] for select in veo["selects"]] == [
        "aspect_ratio",
        "duration",
        "resolution",
    ]
    resolution = [s for s in veo["selects"] if s["name"] == "resolution"][0]
    # 4k は generate API が直接受ける（生成後の追加取得とは別の経路）
    assert resolution["choices"] == ["720p", "1080p", "4k"]
    assert resolution["default"] == "720p"


def test_the_veo_guide_is_injected_only_when_veo_is_selected():
    """モデル固有のガイドは選択中のワークフローの分だけ（SPEC §4.3）。"""
    from app.models import ChatSessionCreate
    from app.prompts import build_system_prompt

    veo = build_system_prompt(
        ChatSessionCreate(mode="i2v", video_workflow=VEO_FAST.id)
    )
    assert "VIDEO PROMPT SPEC — Google Veo 3.1" in veo
    # 否定語の扱い・音声・カメラワークの要点が入っていること
    assert "Negative: cartoon, blurry" in veo
    assert "(no subtitles)" in veo

    ltx = build_system_prompt(ChatSessionCreate(mode="i2v"))
    assert "Google Veo 3.1" not in ltx


def test_the_agent_prompt_lists_the_guide_once_per_available_model(monkeypatch):
    from app.prompts import video_prompt_guides_section

    assert video_prompt_guides_section() == ""  # kie が使えないうちは節ごと出ない

    mark_available(monkeypatch)
    section = video_prompt_guides_section()
    # Fast と Quality は同じガイドなので 1 回だけ載る
    assert section.count("VIDEO PROMPT SPEC — Google Veo 3.1") == 1


# --------------------------------------------------------------------------
# Veo の素材参照生成（REFERENCE_2_VIDEO、issue #26）
# --------------------------------------------------------------------------

def _veo_job(client, fake_kie, monkeypatch, **overrides):
    """Veo の動画ジョブを 1 本走らせて、終わったジョブを返す。"""
    async def fake_last_frame(video, dest):
        dest.write_bytes(b"png")
        return dest

    monkeypatch.setattr(jobs, "extract_last_frame", fake_last_frame)
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "veo-1"})))
    fake_kie.answer(
        "record", veo_record(1, ("https://cdn.kie.ai/veo.mp4",), creditsConsumed=60)
    )
    payload = {
        "mode": "i2v",
        "video_workflow": VEO_FAST.id,
        "video_prompt": "A medium shot of a woman on a rooftop at dusk.",
    }
    payload.update(overrides)
    created = client.post("/api/jobs", json=payload)
    assert created.status_code == 201, created.text
    return wait_for(client, created.json()["id"])


def test_veo_reference_images_switch_the_generation_type(
    client, fake_kie, job_env, monkeypatch
):
    """参照画像は開始フレームと同じ ``imageUrls`` に載るが、種別が変わる。"""
    fake_kie.answer(
        "upload",
        FakeResponse(envelope({"fileUrl": "https://files.kie.ai/ref0.png"})),
        FakeResponse(envelope({"fileUrl": "https://files.kie.ai/ref1.png"})),
    )
    job = _veo_job(
        client, fake_kie, monkeypatch,
        reference_images=_reference_assets(job_env),
    )
    assert job["status"] == "done", job["error"]

    task_input = job["workflow_json"]["video"]["request"]["input"]
    assert task_input["imageUrls"] == [
        "https://files.kie.ai/ref0.png",
        "https://files.kie.ai/ref1.png",
    ]
    # 枚数だけでは flf2v と区別が付かないので、宣言した固定値で切り替える
    assert task_input["generationType"] == "REFERENCE_2_VIDEO"
    assert fake_kie.sent("create")[0]["json"]["generationType"] == "REFERENCE_2_VIDEO"


def test_veo_without_references_keeps_the_usual_generation_type(
    client, fake_kie, job_env, monkeypatch
):
    job = _veo_job(client, fake_kie, monkeypatch)
    assert job["status"] == "done", job["error"]
    assert "generationType" not in job["workflow_json"]["video"]["request"]["input"]
    assert fake_kie.sent("create")[0]["json"]["generationType"] == "TEXT_2_VIDEO"


def test_veo_reference_images_are_exclusive_with_a_start_frame(client, job_env):
    references = _reference_assets(job_env, 1)
    (job_env.parent / "assets" / "image" / "start.png").write_bytes(b"png")

    answer = client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": VEO_FAST.id,
            "video_prompt": "She turns toward the window.",
            "source_image": "/assets/image/start.png",
            "reference_images": references,
        },
    )
    assert answer.status_code == 422
    assert "同時に指定できません" in answer.text


def test_veo_reference_mode_is_fixed_to_eight_seconds(client, job_env):
    """参照素材のときの尺は API 側で 8 秒固定なので、他を選んだら 422。"""
    references = _reference_assets(job_env, 1)
    body = {
        "mode": "i2v",
        "video_workflow": VEO_FAST.id,
        "video_prompt": "She turns toward the window.",
        "reference_images": references,
        "selects": {"duration": "4"},
    }
    answer = client.post("/api/jobs", json=body)
    assert answer.status_code == 422
    assert "'8' 固定" in answer.text

    # 8 秒を明示指定するのはもちろん通る（未指定も既定が 8 なので通る）
    body["selects"] = {"duration": "8"}
    assert client.post("/api/jobs", json=body).status_code == 201


def test_veo_quality_does_not_take_reference_images(client, job_env):
    """素材参照生成は Fast / Lite のみ（Quality は宣言しない）。"""
    answer = client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": VEO_QUALITY.id,
            "video_prompt": "She turns toward the window.",
            "reference_images": _reference_assets(job_env, 1),
        },
    )
    assert answer.status_code == 422
    assert "受け取れません" in answer.text


def test_too_many_veo_reference_images_are_rejected(client, job_env):
    answer = client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": VEO_FAST.id,
            "video_prompt": "She turns toward the window.",
            "reference_images": _reference_assets(job_env, 4),
        },
    )
    assert answer.status_code == 422
    assert "3 件までです" in answer.text


# --------------------------------------------------------------------------
# Veo の追加操作: 延長と 1080P 取得（issue #26）
# --------------------------------------------------------------------------

def test_the_extend_api_has_its_own_endpoint_and_model_names():
    api = kie.task_api("veo_extend")
    assert api.create_url.endswith("/api/v1/veo/extend")
    # 照会は生成と同じ record-info に乗る
    assert api.record_url == kie.VEO.record_url
    # model の書式は生成と違う（veo3_fast -> fast）
    assert kie.extend_model("veo3_fast") == "fast"
    assert kie.extend_model("veo3") == "quality"
    assert kie.extend_model("veo3_lite") == "lite"
    with pytest.raises(kie.KieError):
        kie.extend_model("kling-3.0/video")


def test_the_extend_body_is_flat_and_carries_the_source_task():
    body = kie.VEO_EXTEND.create_body(
        "fast", kie.extend_input("veo-1", "She keeps walking.", seeds=12345)
    )
    assert body == {
        "model": "fast",
        "taskId": "veo-1",
        "prompt": "She keeps walking.",
        "seeds": 12345,
    }
    # 生成と違って generationType は付けない（画像を渡さないので意味がない）
    assert "generationType" not in body
    # 任意のものは指定したときだけ載る
    assert "seeds" not in kie.extend_input("veo-1", "x")
    assert kie.extend_input("veo-1", "x", watermark=" mine ")["watermark"] == "mine"


def test_an_extended_task_returns_the_whole_video():
    """延長の成果物は「元動画 + 7 秒」の通し（``fullResultUrls``）。"""
    state = kie.VEO_EXTEND.read_state({
        "successFlag": 1,
        "response": {
            "resultUrls": ["https://cdn.kie.ai/tail.mp4"],
            "fullResultUrls": ["https://cdn.kie.ai/full.mp4"],
        },
    })
    assert state.result_urls == ("https://cdn.kie.ai/full.mp4",)
    # 通しが無ければ従来どおり resultUrls を使う
    state = kie.VEO_EXTEND.read_state({
        "successFlag": 1,
        "response": {"resultUrls": ["https://cdn.kie.ai/tail.mp4"]},
    })
    assert state.result_urls == ("https://cdn.kie.ai/tail.mp4",)


def test_1080p_is_retried_until_it_is_ready(fake_kie):
    """1080P は生成の 1〜3 分後にできるので、未準備は失敗にしない。"""
    waited: list[tuple[int, int]] = []

    async def on_wait(attempt, attempts):
        waited.append((attempt, attempts))

    fake_kie.answer(
        "veo_1080p",
        FakeResponse(status=422, text="not ready"),
        FakeResponse(envelope({"code": 404}), status=404),
        FakeResponse(envelope({"resultUrls": ["https://cdn.kie.ai/1080.mp4"]})),
    )

    url = asyncio.run(
        kie.get_1080p_video("veo-1", index=0, interval=0.0, on_wait=on_wait)
    )

    assert url == "https://cdn.kie.ai/1080.mp4"
    assert waited == [(1, kie.P1080_ATTEMPTS), (2, kie.P1080_ATTEMPTS)]
    assert fake_kie.sent("veo_1080p")[0]["params"] == {"taskId": "veo-1", "index": "0"}


def test_1080p_gives_up_after_the_last_attempt(fake_kie):
    fake_kie.answer("veo_1080p", FakeResponse(status=422, text="not ready"))
    with pytest.raises(kie.KieError) as caught:
        asyncio.run(kie.get_1080p_video("veo-1", interval=0.0, attempts=3))
    assert "1080P 版が用意されませんでした" in str(caught.value)
    assert len(fake_kie.sent("veo_1080p")) == 3


def test_a_failure_that_will_not_change_is_not_retried(fake_kie):
    """クレジット不足のような「待っても変わらない」失敗はそのまま投げる。"""
    fake_kie.answer("veo_1080p", FakeResponse(status=402, text="no credits"))
    with pytest.raises(kie.KieError) as caught:
        asyncio.run(kie.get_1080p_video("veo-1", interval=0.0))
    assert "クレジットが不足" in str(caught.value)
    assert len(fake_kie.sent("veo_1080p")) == 1


def test_a_finished_veo_job_offers_both_follow_ups(client, fake_kie, job_env,
                                                   monkeypatch):
    job = _veo_job(client, fake_kie, monkeypatch)
    assert job["status"] == "done", job["error"]
    assert job["followups"] == ["veo_extend", "veo_1080p"]
    # 一覧（workflow_json を返さない経路）でも同じ判定が出る
    listed = [row for row in client.get("/api/jobs").json() if row["id"] == job["id"]]
    assert listed[0]["followups"] == ["veo_extend", "veo_1080p"]


def test_a_1080p_generation_is_not_offered_the_1080p_follow_up(
    client, fake_kie, job_env, monkeypatch
):
    job = _veo_job(client, fake_kie, monkeypatch, selects={"resolution": "1080p"})
    assert job["followups"] == ["veo_extend"]


def test_a_comfyui_job_has_no_follow_ups(client, fake_kie, job_env, monkeypatch):
    """kie のタスク ID が無いジョブには何も掛けられない。"""
    from app.jobs import job_followups

    assert job_followups("i2v", "done", {"video_workflow": VEO_FAST.id}, {}, "v.mp4") == []
    assert job_followups(
        "i2v", "failed", {"video_workflow": VEO_FAST.id},
        {"video": {"backend": "kie", "task_id": "veo-1"}}, "v.mp4",
    ) == []


def test_extending_a_veo_job_runs_a_new_job(client, fake_kie, job_env, monkeypatch):
    source = _veo_job(client, fake_kie, monkeypatch)
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "veo-2"})))
    fake_kie.answer(
        "record",
        FakeResponse(envelope({
            "successFlag": 1,
            "response": {"fullResultUrls": ["https://cdn.kie.ai/full.mp4"]},
            "creditsConsumed": 30,
        })),
    )

    created = client.post(
        f"/api/jobs/{source['id']}/veo/extend",
        json={"prompt": "She keeps walking toward the edge."},
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])

    assert job["status"] == "done", job["error"]
    assert job["mode"] == "veo_extend"
    assert job["params"]["continued_from"] == source["id"]
    stage = job["workflow_json"]["video"]
    assert stage["followup"] == "veo_extend"
    assert stage["source_task_id"] == "veo-1"
    assert stage["task_id"] == "veo-2"
    # 延長 API は model の書式が違い、body は平ら
    body = fake_kie.sent("create")[-1]["json"]
    assert body["model"] == "fast"
    assert body["taskId"] == "veo-1"
    assert body["prompt"] == "She keeps walking toward the edge."
    assert (job_env / job["id"] / "video.mp4").is_file()
    assert job["credits_consumed"] == 30
    # 延長した動画にはさらに延長も 1080P も掛けられる
    assert job["followups"] == ["veo_extend", "veo_1080p"]


def test_extending_needs_a_prompt(client, fake_kie, job_env, monkeypatch):
    source = _veo_job(client, fake_kie, monkeypatch)
    answer = client.post(f"/api/jobs/{source['id']}/veo/extend", json={"prompt": " "})
    assert answer.status_code == 422
    assert "video_prompt" in answer.text


def test_fetching_1080p_runs_a_new_job(client, fake_kie, job_env, monkeypatch):
    source = _veo_job(client, fake_kie, monkeypatch)
    fake_kie.answer(
        "veo_1080p",
        FakeResponse(envelope({"resultUrls": ["https://cdn.kie.ai/1080.mp4"]})),
    )

    created = client.post(f"/api/jobs/{source['id']}/veo/1080p", json={})
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])

    assert job["status"] == "done", job["error"]
    assert job["mode"] == "veo_1080p"
    stage = job["workflow_json"]["video"]
    assert stage["followup"] == "veo_1080p"
    assert stage["source_task_id"] == "veo-1"
    # タスクは作らないので task_id は空のまま
    assert stage["task_id"] is None
    assert (job_env / job["id"] / "video.mp4").is_file()
    # アップスケール済みの動画には追加操作を掛けられない（延長は API 側で不可）
    assert job["followups"] == []


def test_a_follow_up_on_a_job_that_cannot_take_one_is_rejected(client, job_env):
    created = client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": VEO_FAST.id,
            "video_prompt": "She turns toward the window.",
        },
    )
    # まだ走り終わっていない（成果物も task_id も無い）ジョブには掛けられない
    answer = client.post(
        f"/api/jobs/{created.json()['id']}/veo/extend", json={"prompt": "more"}
    )
    assert answer.status_code == 422
    assert "追加操作" in answer.text


def test_a_follow_up_on_an_unknown_job_is_404(client):
    answer = client.post("/api/jobs/nope/veo/1080p", json={})
    assert answer.status_code == 404


# --------------------------------------------------------------------------
# バックエンドをまたぐ full ジョブ（ComfyUI 画像 → kie 動画、SPEC §5.2）
# --------------------------------------------------------------------------

@pytest.fixture
def comfy_env(monkeypatch):
    """ComfyUI を偽物に差し替える（画像ステージだけをローカルで走らせる）。"""
    from app import comfy
    from test_jobs import FakeComfy

    fake = FakeComfy(None)
    for name in ("upload_file", "queue_prompt", "get_history", "download_view",
                 "ws_url"):
        monkeypatch.setattr(comfy, name, getattr(fake, name))
    monkeypatch.setattr(jobs, "POLL_INTERVAL", 0.02)
    return fake


def test_a_full_job_bridges_comfyui_images_into_veo(client, fake_kie, comfy_env,
                                                    job_env, monkeypatch):
    """本命の使い方: ローカルで画像を作り、その画像を Veo に渡して動画にする。"""
    async def fake_last_frame(video, dest):
        dest.write_bytes(b"png")
        return dest

    monkeypatch.setattr(jobs, "extract_last_frame", fake_last_frame)
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "task-veo"})))
    fake_kie.answer("record", veo_record(1, ("https://cdn.kie.ai/out.mp4",)))
    fake_kie.answer(
        "upload", FakeResponse(envelope({"fileUrl": "https://files.kie.ai/gen.png"}))
    )
    events: list[dict] = []

    with client.websocket_connect("/api/ws") as socket:
        created = client.post(
            "/api/jobs",
            json={
                "mode": "full",
                "image_workflow": workflows.DEFAULT_IMAGE_WORKFLOW,  # ComfyUI
                "video_workflow": VEO_FAST.id,  # kie.ai
                "image_prompt": "a cat on a roof",
                "video_prompt": "The cat stretches and yawns.",
            },
        )
        assert created.status_code == 201, created.text
        for _ in range(40):
            event = socket.receive_json()
            events.append(event)
            if event["status"] in ("done", "failed"):
                break

    job = wait_for(client, created.json()["id"])
    assert job["status"] == "done", job["error"]

    # 1 段目は ComfyUI のグラフ、2 段目は kie のタスク
    assert len(comfy_env.queued) == 1
    stages = job["workflow_json"]
    assert stages["image"]["prompt_id"] == "prompt-1"
    assert stages["image"]["workflow_id"] == workflows.DEFAULT_IMAGE_WORKFLOW
    assert stages["video"]["backend"] == "kie"
    assert stages["video"]["task_id"] == "task-veo"

    # 1 段目の静止画を kie にアップロードし直して開始フレームにしている
    uploaded = fake_kie.sent("upload")[0]["json"]["fileName"]
    assert uploaded == "image.png"
    assert stages["video"]["request"]["input"]["imageUrls"] == [
        "https://files.kie.ai/gen.png"
    ]
    # ComfyUI 側には生成画像を上げ直していない（2 段目は ComfyUI を使わない）
    assert str(job_env / job["id"] / "image.png") not in comfy_env.uploads

    # 成果物は両方とも outputs/{job_id}/ に揃う
    assert (job_env / job["id"] / "image.png").is_file()
    assert (job_env / job["id"] / "video.mp4").is_file()
    assert job["image_url"] and job["video_url"]
    assert job["credits_consumed"] is None  # Veo はクレジットを返さない応答

    # 進捗は 2 段表示のまま
    messages = [event.get("message") or "" for event in events]
    assert any("画像生成 (1/2)" in message for message in messages)
    assert any("動画生成 (2/2)" in message for message in messages)


# --------------------------------------------------------------------------
# Kling 3.0（Market 系の統一 API、issue #18）
# --------------------------------------------------------------------------

KLING = workflows.BY_ID["kling3_video"]


def _kling_params(**overrides) -> GenerationParams:
    base = dict(
        mode="i2v",
        job_id="job-kling",
        video_workflow=KLING.id,
        video_prompt="Slow dolly push forward, a woman in a grey coat turns.",
    )
    base.update(overrides)
    return _params(**base)


def test_kling_rides_on_the_market_api():
    """Veo と違って専用系ではないので、既定の統一 API のまま。"""
    assert KLING.kie.api == "market"
    assert KLING.kie.model == "kling-3.0/video"
    api = kie.task_api(KLING.kie.api)
    assert api.create_url.endswith("/api/v1/jobs/createTask")
    assert api.record_url.endswith("/api/v1/jobs/recordInfo")


def test_a_start_frame_becomes_one_kling_image_url():
    request = kie.build_request(
        KLING, _kling_params(), {"image": "https://files.kie.ai/start.png"}
    )
    body = kie.task_api(request.api).create_body(request.model, request.input)

    # Market 系はパラメータを input で包む
    assert set(body) == {"model", "input"}
    assert body["model"] == "kling-3.0/video"
    task_input = body["input"]
    assert task_input["image_urls"] == ["https://files.kie.ai/start.png"]
    assert task_input["prompt"].startswith("Slow dolly push forward")
    assert task_input["mode"] == "pro"
    assert task_input["aspect_ratio"] == "16:9"
    # **duration は文字列**（Veo の int と逆）
    assert task_input["duration"] == "5"
    assert isinstance(task_input["duration"], str)
    # sound は真偽値（選択式の文字列を bool に直して送る）
    assert task_input["sound"] is False
    # kie.ai 経由の Kling には無いパラメータは宣言していない
    for absent in ("negative_prompt", "cfg", "camera_control", "seed"):
        assert absent not in task_input


def test_kling_takes_a_first_and_last_frame():
    """2 枚目は最終フレーム（``image_urls`` の並びに意味がある）。"""
    request = kie.build_request(
        KLING,
        _kling_params(
            selects={
                "mode": "4K",
                "duration": "12",
                "aspect_ratio": "9:16",
                "sound": "true",
            }
        ),
        {
            "image": "https://files.kie.ai/first.png",
            "end_image": "https://files.kie.ai/last.png",
        },
    )
    task_input = request.input

    assert task_input["image_urls"] == [
        "https://files.kie.ai/first.png",
        "https://files.kie.ai/last.png",
    ]
    assert task_input["mode"] == "4K"
    assert task_input["duration"] == "12"
    assert task_input["aspect_ratio"] == "9:16"
    assert task_input["sound"] is True


def test_without_an_image_kling_is_text_to_video():
    task_input = kie.build_request(KLING, _kling_params(), {}).input
    assert "image_urls" not in task_input
    assert task_input["prompt"]


def test_kling_rejects_a_prompt_over_the_character_limit(client, monkeypatch):
    """500 文字の上限は投入前に落とす（走らせてから 422 を食わない）。"""
    from app.models import prompt_length_problem

    mark_available(monkeypatch)
    limit = workflows.KLING_MAX_PROMPT_CHARS
    assert limit == 500

    created = client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": KLING.id,
            "video_prompt": "a" * (limit + 1),
        },
    )
    assert created.status_code == 422
    assert "500 文字" in created.text

    # ちょうど上限までは通る（数え方が 1 ずれていないこと）
    assert prompt_length_problem("i2v", KLING.id, "a" * limit) is None
    # 上限を宣言していないワークフロー（ComfyUI 側）は素通し
    assert (
        prompt_length_problem(
            "i2v", workflows.DEFAULT_VIDEO_WORKFLOW, "a" * (limit + 1)
        )
        is None
    )


# ------------------------------------------ マルチショット / Elements（issue #26）
# 平坦な値ではない**構造化パラメータ**の 2 つ: ショット割り（文と秒数の組が最大
# 5 つ）と Elements（名前つきの参照画像の束を `@要素名` で呼ぶ）。

async def _fake_last_frame(video, dest):
    """成果物はダミーの中身なので、ラストフレーム抽出（ffmpeg）だけ差し替える。"""
    dest.write_bytes(b"png")
    return dest


_SHOTS = [
    {"prompt": "Slow dolly push forward, she steps off the tram.", "duration": 4},
    {"prompt": "Low tracking shot, she pushes through the door.", "duration": 6},
]


def test_multi_shots_replace_the_top_level_prompt():
    """``multi_prompt`` を送るときトップレベルの ``prompt`` は送らない。"""
    request = kie.build_request(KLING, _kling_params(multi_shots=_SHOTS), {})
    task_input = request.input

    assert task_input["multi_shots"] is True
    assert task_input["multi_prompt"] == _SHOTS
    # duration は **整数**（Kling のトップレベル duration は文字列なので型が違う）
    assert all(isinstance(shot["duration"], int) for shot in task_input["multi_prompt"])
    assert "prompt" not in task_input
    # 単発のときは逆に multi_* が一切載らない
    single = kie.build_request(KLING, _kling_params(), {}).input
    assert "multi_shots" not in single and "multi_prompt" not in single
    assert single["prompt"]


def test_multi_shots_turn_the_sound_on_by_default():
    """ショット割りは音つき前提の機能なので、既定が false から true に変わる。"""
    assert KLING.selects["sound"].fallback == "false"

    shots = kie.build_request(KLING, _kling_params(multi_shots=_SHOTS), {}).input
    assert shots["sound"] is True
    # 明示指定はそのまま尊重する（既定の入れ替えは「未指定のとき」だけ）
    muted = kie.build_request(
        KLING, _kling_params(multi_shots=_SHOTS, selects={"sound": "false"}), {}
    ).input
    assert muted["sound"] is False


def test_multi_shots_are_checked_before_the_job_is_queued(client, monkeypatch):
    from app.models import multi_shot_problem

    mark_available(monkeypatch)
    ok = [{"prompt": "She turns.", "duration": 5}]
    assert multi_shot_problem("i2v", KLING.id, ok) is None
    # ちょうど上限まで（5 ショット / 1 秒 / 12 秒）は通る
    assert multi_shot_problem("i2v", KLING.id, ok * 5) is None
    assert (
        multi_shot_problem("i2v", KLING.id, [{"prompt": "x", "duration": 1}]) is None
    )
    assert (
        multi_shot_problem("i2v", KLING.id, [{"prompt": "x", "duration": 12}]) is None
    )

    assert "5 ショットまでです" in (multi_shot_problem("i2v", KLING.id, ok * 6) or "")
    assert "1〜12 秒" in (
        multi_shot_problem("i2v", KLING.id, [{"prompt": "x", "duration": 13}]) or ""
    )
    assert "整数の秒数" in (
        multi_shot_problem("i2v", KLING.id, [{"prompt": "x", "duration": "auto"}]) or ""
    )
    # 1 ショットのプロンプトも 500 文字まで
    long_shot = [{"prompt": "a" * 501, "duration": 5}]
    assert "500 文字" in (multi_shot_problem("i2v", KLING.id, long_shot) or "")
    # 宣言していないワークフローには渡せない
    assert "対応していません" in (
        multi_shot_problem("i2v", SEEDANCE.id, ok) or ""
    )

    # API も同じ理由で断る。`video_prompt` はショットがあるので要らない
    answer = client.post(
        "/api/jobs",
        json={"mode": "i2v", "video_workflow": KLING.id, "multi_shots": ok * 6},
    )
    assert answer.status_code == 422
    assert "5 ショットまでです" in answer.text


def test_a_multi_shot_job_needs_no_video_prompt(client, fake_kie, job_env, monkeypatch):
    """本文はショット側にあるので、トップレベルの必須チェックから外れる。"""
    monkeypatch.setattr(jobs, "extract_last_frame", _fake_last_frame)
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "task-shots"})))
    fake_kie.answer("record", success(["https://cdn.kie.ai/out.mp4"], credits=120))

    created = client.post(
        "/api/jobs",
        json={"mode": "i2v", "video_workflow": KLING.id, "multi_shots": _SHOTS},
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])
    assert job["status"] == "done", job["error"]

    task_input = job["workflow_json"]["video"]["request"]["input"]
    assert task_input["multi_shots"] is True
    assert [shot["duration"] for shot in task_input["multi_prompt"]] == [4, 6]
    assert "prompt" not in task_input
    # 再実行できるよう params にも残る
    assert len(job["params"]["multi_shots"]) == 2


def test_an_element_reference_costs_thirty_seven_characters():
    """``@要素名`` は見た目の長さではなく 37 文字として上限を消費する。"""
    from app.models import prompt_chars, prompt_length_problem

    assert prompt_chars("@kaori walks.", 37) == 37 + len(" walks.")
    # Elements を持たないモデルでは補正しない
    assert prompt_chars("@kaori walks.", 0) == len("@kaori walks.")

    # 見た目 470 文字でも `@kaori`（6 -> 37）の分で 501 文字になり弾かれる
    text = "@kaori " + "a" * 463
    assert len(text) == 470
    problem = prompt_length_problem("i2v", KLING.id, text)
    assert problem is not None
    assert "501 文字" in problem and "37 文字として数えます" in problem
    # 1 文字短ければ通る
    assert prompt_length_problem("i2v", KLING.id, text[:-1]) is None


def test_elements_must_match_the_at_references(client, monkeypatch):
    from app.models import elements_problem

    mark_available(monkeypatch)
    element = {
        "name": "kaori",
        "description": "the woman in the grey coat",
        "images": ["/library/image/a.png", "/library/image/b.png"],
    }
    base = dict(mode="i2v", video_workflow=KLING.id)

    assert (
        elements_problem(
            **base, elements=[element], video_prompt="@kaori steps off the tram."
        )
        is None
    )
    # 宣言していない `@名前` は拒否（黙って 37 文字を食われるより気づかせる）
    assert "対応する要素が" in (
        elements_problem(
            **base, elements=[element], video_prompt="@akira steps off the tram."
        )
        or ""
    )
    # 要素が 1 つも無いのに参照しているときも同じ
    assert "対応する要素が" in (
        elements_problem(**base, elements=[], video_prompt="@kaori waits.") or ""
    )
    # 逆に「宣言したが参照していない」は素材を先に用意しただけなので通す
    assert elements_problem(**base, elements=[element], video_prompt="She waits.") is None
    # マルチショットの本文の `@名前` も同じように見る
    assert "対応する要素が" in (
        elements_problem(
            **base, elements=[element], shots=[{"prompt": "@akira waits.", "duration": 4}]
        )
        or ""
    )
    # Elements を持たないモデルでは `@` はただの文字
    assert (
        elements_problem(
            mode="i2v", video_workflow=SEEDANCE.id, elements=[], video_prompt="a@b"
        )
        is None
    )
    assert "対応していません" in (
        elements_problem(
            mode="i2v", video_workflow=SEEDANCE.id, elements=[element]
        )
        or ""
    )

    answer = client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": KLING.id,
            "video_prompt": "@akira steps off the tram.",
            "kling_elements": [element],
        },
    )
    assert answer.status_code == 422
    assert "`@akira`" in answer.text


def test_the_shape_of_an_element_is_checked_too(monkeypatch):
    from app.models import elements_problem

    def problem(**overrides):
        element = {
            "name": "kaori",
            "images": ["/library/image/a.png", "/library/image/b.png"],
        }
        element.update(overrides)
        return elements_problem("i2v", KLING.id, [element]) or ""

    assert problem() == ""
    assert "2〜4 枚です" in problem(images=["/library/image/a.png"])
    assert "2〜4 枚です" in problem(
        images=[f"/library/image/{n}.png" for n in range(5)]
    )
    assert "拡張子" in problem(images=["/library/image/a.png", "/library/video/b.mp4"])
    assert "name がありません" in problem(name="  ")
    assert "`@kaori san` として書けません" in problem(name="kaori san")
    # 4 要素目、と名前の重複
    many = [
        {"name": f"e{index}", "images": ["/a.png", "/b.png"]} for index in range(4)
    ]
    assert "3 要素までです" in (elements_problem("i2v", KLING.id, many) or "")
    twice = [{"name": "kaori", "images": ["/a.png", "/b.png"]}] * 2
    assert "重複しています" in (elements_problem("i2v", KLING.id, twice) or "")


def test_element_images_become_element_input_urls(
    client, fake_kie, job_env, monkeypatch
):
    """要素ごとの参照画像が 1 枚ずつ上がり、API の形に組み直される。"""
    monkeypatch.setattr(jobs, "extract_last_frame", _fake_last_frame)
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "task-elem"})))
    fake_kie.answer("record", success(["https://cdn.kie.ai/out.mp4"], credits=120))
    fake_kie.answer(
        "upload",
        FakeResponse(envelope({"fileUrl": "https://files.kie.ai/ref0.png"})),
        FakeResponse(envelope({"fileUrl": "https://files.kie.ai/ref1.png"})),
    )
    images = _reference_assets(job_env)

    created = client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": KLING.id,
            "video_prompt": "Slow dolly push forward, @kaori steps off the tram.",
            "kling_elements": [
                {"name": "kaori", "description": "grey coat", "images": images},
            ],
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])
    assert job["status"] == "done", job["error"]

    task_input = job["workflow_json"]["video"]["request"]["input"]
    assert task_input["kling_elements"] == [
        {
            "name": "kaori",
            "description": "grey coat",
            "element_input_urls": [
                "https://files.kie.ai/ref0.png",
                "https://files.kie.ai/ref1.png",
            ],
        }
    ]
    # 参照画像は params にも残る（再実行で同じ素材を使う）
    assert len(job["params"]["kling_elements"][0]["images"]) == 2

    # 続き生成では本文の `@kaori` だけが残らないよう、要素も一緒に引き継ぐ
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "task-cont"})))
    fake_kie.answer("record", success(["https://cdn.kie.ai/out2.mp4"], credits=120))
    fake_kie.answer(
        "upload",
        *[FakeResponse(envelope({"fileUrl": "https://files.kie.ai/again.png"}))] * 4,
    )
    carried = client.post(f"/api/jobs/{job['id']}/continue", json={})
    assert carried.status_code == 201, carried.text
    assert carried.json()["params"]["kling_elements"][0]["name"] == "kaori"

    # 要素を受け取れないワークフローに切り替えたときは落とす
    switched = client.post(
        f"/api/jobs/{job['id']}/continue", json={"video_workflow": SEEDANCE.id}
    )
    assert switched.status_code == 201, switched.text
    assert switched.json()["params"]["kling_elements"] == []


def test_the_agent_plan_validation_knows_the_kling_rules(client, job_env):
    """プランでも同じ理由で断る（投入前に気づかせる、SPEC §4.3）。"""
    from app.agent_protocol import ActionError, validate_job

    images = _reference_assets(job_env)
    base = {"mode": "i2v", "video_workflow": KLING.id}

    payload = validate_job(
        {
            **base,
            "multi_shots": _SHOTS,
            "kling_elements": [{"name": "kaori", "images": images}],
        },
        where="tasks[0].job",
    )
    assert len(payload.multi_shots) == 2
    assert payload.kling_elements[0].name == "kaori"

    with pytest.raises(ActionError, match="5 ショットまでです"):
        validate_job({**base, "multi_shots": _SHOTS * 3}, where="tasks[0].job")
    with pytest.raises(ActionError, match="対応していません"):
        validate_job(
            {**base, "video_workflow": SEEDANCE.id, "multi_shots": _SHOTS},
            where="tasks[0].job",
        )
    with pytest.raises(ActionError, match="対応する要素が"):
        validate_job(
            {**base, "video_prompt": "@akira waits."}, where="tasks[0].job"
        )
    with pytest.raises(ActionError, match="not found"):
        validate_job(
            {
                **base,
                "video_prompt": "@kaori waits.",
                "kling_elements": [
                    {"name": "kaori", "images": ["/assets/image/missing.png", images[0]]}
                ],
            },
            where="tasks[0].job",
        )


def test_the_kling_guide_explains_the_second_stage_features():
    from app.models import ChatSessionCreate
    from app.prompts import build_system_prompt
    from app.workflows import catalog_entry

    prompt = build_system_prompt(
        ChatSessionCreate(mode="i2v", video_workflow=KLING.id)
    )
    assert "## Multi-shot (`multi_shots`)" in prompt
    assert "## Elements (`kling_elements`)" in prompt
    assert "37 characters" in prompt

    # カタログ側にも上限が出る（エージェントが件数を推測しなくてよい）
    from app.prompts import _catalog_entry_lines

    catalog = "\n".join(_catalog_entry_lines(catalog_entry(KLING)))
    assert "`multi_shots`（最大 5 ショット" in catalog
    assert "1 参照が 37 文字" in catalog
    # 宣言のないワークフローには行そのものが出ない
    other = "\n".join(_catalog_entry_lines(catalog_entry(SEEDANCE)))
    assert "マルチショット" not in other and "Elements" not in other


def test_kling_is_offered_as_a_video_workflow(client, monkeypatch):
    mark_available(monkeypatch)
    body = client.get("/api/options").json()
    kling = [wf for wf in body["video_workflows"] if wf["id"] == KLING.id][0]

    assert kling["requires"] == []
    assert set(kling["supports"]) == {
        "prompt", "image", "end_image",
        # 構造化パラメータ（マルチショット・Elements、issue #26）
        "multi_shots", "multi_prompt", "kling_elements",
    }
    assert kling["accepts_start_image"] is True
    assert kling["backend"] == "kie"
    # フォームが行数・秒数・残り文字数を出せるだけの宣言が載る（SPEC §3.1）
    assert kling["max_prompt_chars"] == 500
    assert kling["multi_shot"] == {
        "max_shots": 5, "min_duration": 1, "max_duration": 12,
    }
    assert kling["elements"] == {
        "max_elements": 3, "min_images": 2, "max_images": 4,
        "reference_chars": 37,
    }
    # 宣言のないワークフローでは欄そのものが出ない
    seedance = [wf for wf in body["video_workflows"] if wf["id"] == SEEDANCE.id][0]
    assert seedance["multi_shot"] is None
    assert seedance["elements"] is None
    selects = {select["name"]: select for select in kling["selects"]}
    assert list(selects) == ["mode", "duration", "aspect_ratio", "sound"]
    assert selects["mode"]["choices"] == ["std", "pro", "4K"]
    assert selects["mode"]["default"] == "pro"
    assert selects["duration"]["choices"][0] == "3"
    assert selects["duration"]["choices"][-1] == "15"
    assert selects["duration"]["default"] == "5"
    assert selects["sound"]["choices"] == ["false", "true"]
    assert selects["sound"]["default"] == "false"


def test_the_kling_guide_is_injected_only_when_kling_is_selected():
    from app.models import ChatSessionCreate
    from app.prompts import build_system_prompt

    kling = build_system_prompt(
        ChatSessionCreate(mode="i2v", video_workflow=KLING.id)
    )
    assert "VIDEO PROMPT SPEC — Kling 3.0" in kling
    # 500 字・カメラ先頭・ネガティブが無いこと・音声の書き方が入っている
    assert "500 characters" in kling
    assert "Camera first" in kling
    assert "no `negative_prompt`" in kling
    assert "lip-synced" in kling

    veo = build_system_prompt(
        ChatSessionCreate(mode="i2v", video_workflow=VEO_FAST.id)
    )
    assert "Kling 3.0" not in veo


def test_the_agent_prompt_carries_the_kling_guide(monkeypatch):
    from app.prompts import video_prompt_guides_section

    mark_available(monkeypatch)
    section = video_prompt_guides_section()
    assert section.count("VIDEO PROMPT SPEC — Kling 3.0") == 1


def test_a_full_job_bridges_comfyui_images_into_kling(client, fake_kie, comfy_env,
                                                      job_env, monkeypatch):
    """本命の使い方: ローカルで画像を作り、その画像を Kling に渡して動画にする。"""
    async def fake_last_frame(video, dest):
        dest.write_bytes(b"png")
        return dest

    monkeypatch.setattr(jobs, "extract_last_frame", fake_last_frame)
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "task-kling"})))
    fake_kie.answer("record", success(["https://cdn.kie.ai/out.mp4"], credits=90))
    fake_kie.answer(
        "upload", FakeResponse(envelope({"fileUrl": "https://files.kie.ai/gen.png"}))
    )

    created = client.post(
        "/api/jobs",
        json={
            "mode": "full",
            "image_workflow": workflows.DEFAULT_IMAGE_WORKFLOW,  # ComfyUI
            "video_workflow": KLING.id,  # kie.ai
            "image_prompt": "a cat on a roof",
            "video_prompt": "Slow dolly in, the cat stretches and yawns.",
            "selects": {"duration": "10", "sound": "true"},
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])
    assert job["status"] == "done", job["error"]

    stages = job["workflow_json"]
    assert len(comfy_env.queued) == 1
    assert stages["image"]["workflow_id"] == workflows.DEFAULT_IMAGE_WORKFLOW
    assert stages["video"]["backend"] == "kie"
    assert stages["video"]["task_id"] == "task-kling"
    assert stages["video"]["request"]["api"] == "market"

    # 1 段目の静止画を kie に上げ直して開始フレームにしている
    task_input = stages["video"]["request"]["input"]
    assert task_input["image_urls"] == ["https://files.kie.ai/gen.png"]
    assert task_input["duration"] == "10"
    assert task_input["sound"] is True

    body = fake_kie.sent("create")[0]["json"]
    assert body["model"] == "kling-3.0/video"
    assert body["input"]["image_urls"] == ["https://files.kie.ai/gen.png"]

    assert (job_env / job["id"] / "image.png").is_file()
    assert (job_env / job["id"] / "video.mp4").is_file()
    assert job["credits_consumed"] == 90


# --------------------------------------------------------------------------
# Seedance 2 系（Market 系の統一 API、issue #19）
# --------------------------------------------------------------------------

SEEDANCE = workflows.BY_ID["seedance2"]
SEEDANCE_FAST = workflows.BY_ID["seedance2_fast"]
SEEDANCE_MINI = workflows.BY_ID["seedance2_mini"]


def _seedance_params(**overrides) -> GenerationParams:
    base = dict(
        mode="i2v",
        job_id="job-seedance",
        video_workflow=SEEDANCE.id,
        video_prompt=(
            "A woman in a grey wool coat walks briskly along a wet quay."
            " Golden hour backlight rims her hair. Slow tracking shot beside"
            " her. 35mm film grain. Avoid jitter and bent limbs."
        ),
    )
    base.update(overrides)
    return _params(**base)


def test_seedance_rides_on_the_market_api():
    """Kling と同じ Market 系。モデル名はマニフェスト側の宣言だけで決まる。"""
    for spec, model in (
        (SEEDANCE, "bytedance/seedance-2"),
        (SEEDANCE_MINI, "bytedance/seedance-2-mini"),
    ):
        assert spec.kie.api == "market"
        assert spec.kie.model == model
        api = kie.task_api(spec.kie.api)
        assert api.create_url.endswith("/api/v1/jobs/createTask")
        assert api.record_url.endswith("/api/v1/jobs/recordInfo")


def test_a_start_frame_becomes_the_seedance_first_frame_url():
    request = kie.build_request(
        SEEDANCE, _seedance_params(), {"image": "https://files.kie.ai/start.png"}
    )
    body = kie.task_api(request.api).create_body(request.model, request.input)

    assert set(body) == {"model", "input"}
    assert body["model"] == "bytedance/seedance-2"
    task_input = body["input"]
    # Kling の image_urls と違い、開始 / 最終フレームはキーが別（配列ではない）
    assert task_input["first_frame_url"] == "https://files.kie.ai/start.png"
    assert "last_frame_url" not in task_input
    assert "image_urls" not in task_input
    assert task_input["resolution"] == "720p"
    assert task_input["aspect_ratio"] == "16:9"
    # duration は **整数**（Kling の文字列と型が逆）
    assert task_input["duration"] == 5
    assert isinstance(task_input["duration"], int)
    # 音声は既定 ON、真偽値で送る
    assert task_input["generate_audio"] is True
    # NSFW チェックは既定 OFF（真偽値で送る。false でも省略はしない）
    assert task_input["nsfw_checker"] is False
    # 2 系に無いパラメータは宣言していない
    for absent in ("seed", "camera_fixed", "negative_prompt"):
        assert absent not in task_input


def test_seedance_takes_a_first_and_a_last_frame():
    request = kie.build_request(
        SEEDANCE,
        _seedance_params(
            selects={
                "resolution": "4k",
                "duration": "15",
                "aspect_ratio": "adaptive",
                "generate_audio": "false",
                "nsfw_checker": "true",
            }
        ),
        {
            "image": "https://files.kie.ai/first.png",
            "end_image": "https://files.kie.ai/last.png",
        },
    )
    task_input = request.input

    assert task_input["first_frame_url"] == "https://files.kie.ai/first.png"
    assert task_input["last_frame_url"] == "https://files.kie.ai/last.png"
    assert task_input["resolution"] == "4k"
    assert task_input["duration"] == 15
    assert task_input["aspect_ratio"] == "adaptive"
    assert task_input["generate_audio"] is False
    assert task_input["nsfw_checker"] is True


def test_without_an_image_seedance_is_text_to_video():
    task_input = kie.build_request(SEEDANCE, _seedance_params(), {}).input
    assert "first_frame_url" not in task_input
    assert "last_frame_url" not in task_input
    assert task_input["prompt"]


def test_reference_material_goes_in_as_arrays_of_urls():
    """マルチモーダル参照は 1 フィールド = URL の配列（issue #26 B）。"""
    request = kie.build_request(
        SEEDANCE,
        _seedance_params(),
        {
            "reference_images": [
                "https://files.kie.ai/ref1.png",
                "https://files.kie.ai/ref2.png",
            ],
            "reference_videos": ["https://files.kie.ai/move.mp4"],
            "reference_audios": ["https://files.kie.ai/mood.mp3"],
        },
    )
    body = kie.task_api(request.api).create_body(request.model, request.input)
    task_input = body["input"]

    # 並びは params の順そのまま（Veo の list_keys とは別の機構）
    assert task_input["reference_image_urls"] == [
        "https://files.kie.ai/ref1.png",
        "https://files.kie.ai/ref2.png",
    ]
    assert task_input["reference_video_urls"] == ["https://files.kie.ai/move.mp4"]
    assert task_input["reference_audio_urls"] == ["https://files.kie.ai/mood.mp3"]
    # 参照モードでは先頭フレームのキーは出ない（投入前に排他を弾いている）
    assert "first_frame_url" not in task_input
    assert "last_frame_url" not in task_input


def test_empty_reference_lists_are_not_sent():
    """空のリストは「指定なし」なのでキーごと落ちる。"""
    task_input = kie.build_request(
        SEEDANCE,
        _seedance_params(),
        {"reference_images": [], "reference_videos": [], "reference_audios": []},
    ).input
    for absent in (
        "reference_image_urls", "reference_video_urls", "reference_audio_urls"
    ):
        assert absent not in task_input


def test_the_seedance_variants_declare_the_same_reference_limits():
    """上限は API 側の値そのまま（9 / 3 / 3）で、3 バリアント共通。"""
    for spec in (SEEDANCE, SEEDANCE_FAST, SEEDANCE_MINI):
        assert spec.multi_inputs == {
            "reference_images": 9,
            "reference_videos": 3,
            "reference_audios": 3,
        }


def test_the_mini_variant_only_differs_by_model_and_resolution():
    """2.5 追加時にエントリ 1 つで済む構造か（宣言の形は同じ）。"""
    assert SEEDANCE_MINI.kie.fields == SEEDANCE.kie.fields
    assert SEEDANCE_MINI.kie.int_keys == SEEDANCE.kie.int_keys == ("duration",)
    assert SEEDANCE_MINI.kie.bool_keys == SEEDANCE.kie.bool_keys
    assert SEEDANCE_MINI.prompt_hint == SEEDANCE.prompt_hint
    assert SEEDANCE.selects["resolution"].choices == ("480p", "720p", "1080p", "4k")
    assert SEEDANCE_MINI.selects["resolution"].choices == ("480p", "720p")

    task_input = kie.build_request(SEEDANCE_MINI, _seedance_params(), {}).input
    assert task_input["duration"] == 5
    body = kie.task_api("market").create_body(
        SEEDANCE_MINI.kie.model, task_input
    )
    assert body["model"] == "bytedance/seedance-2-mini"


def test_every_seedance_variant_is_offered_as_a_video_workflow(client, monkeypatch):
    mark_available(monkeypatch)
    body = client.get("/api/options").json()
    variants = (SEEDANCE.id, SEEDANCE_FAST.id, SEEDANCE_MINI.id)
    offered = {wf["id"]: wf for wf in body["video_workflows"] if wf["id"] in variants}
    assert set(offered) == set(variants)

    for spec_id, resolutions in (
        (SEEDANCE.id, ["480p", "720p", "1080p", "4k"]),
        (SEEDANCE_FAST.id, ["480p", "720p"]),
        (SEEDANCE_MINI.id, ["480p", "720p"]),
    ):
        entry = offered[spec_id]
        assert entry["requires"] == []
        assert set(entry["supports"]) == {
            "prompt", "image", "end_image",
            "reference_images", "reference_videos", "reference_audios",
        }
        # フォームが参照欄を出すのに要る件数の上限（SPEC §3.1 / §8）
        assert entry["multi_inputs"] == {
            "reference_images": 9,
            "reference_videos": 3,
            "reference_audios": 3,
        }
        assert entry["accepts_start_image"] is True
        assert entry["backend"] == "kie"
        selects = {select["name"]: select for select in entry["selects"]}
        assert list(selects) == [
            "resolution", "duration", "aspect_ratio", "generate_audio",
            "nsfw_checker",
        ]
        assert selects["resolution"]["choices"] == resolutions
        assert selects["resolution"]["default"] == "720p"
        assert selects["duration"]["choices"][0] == "4"
        assert selects["duration"]["choices"][-1] == "15"
        assert selects["duration"]["default"] == "5"
        assert selects["aspect_ratio"]["choices"][-1] == "adaptive"
        assert selects["generate_audio"]["default"] == "true"
        # NSFW チェックは既定 OFF（フィルタ無効）
        assert selects["nsfw_checker"]["choices"] == ["false", "true"]
        assert selects["nsfw_checker"]["default"] == "false"


def test_the_seedance_guide_is_injected_only_when_seedance_is_selected():
    from app.models import ChatSessionCreate
    from app.prompts import build_system_prompt

    for spec_id in (SEEDANCE.id, SEEDANCE_FAST.id, SEEDANCE_MINI.id):
        prompt = build_system_prompt(
            ChatSessionCreate(mode="i2v", video_workflow=spec_id)
        )
        assert "VIDEO PROMPT SPEC — ByteDance Seedance 2" in prompt
        # 参照モードの書き方（一貫性 / 動きのお手本 / ムード）と排他の注意
        assert "identity and consistency" in prompt
        assert "the motion to imitate" in prompt
        assert "mutually exclusive" in prompt
        # 6 要素フォーミュラ・照明・カメラ 1 つ・動きの文の分離・負例
        assert "60-100 words" in prompt
        assert "lighting sentence is the single biggest lever" in prompt
        assert "separate sentences" in prompt
        assert "avoid jitter and bent limbs" in prompt

    kling = build_system_prompt(
        ChatSessionCreate(mode="i2v", video_workflow=KLING.id)
    )
    assert "Seedance 2" not in kling


def test_the_agent_prompt_carries_the_seedance_guide_once(monkeypatch):
    """2 バリアントで同じガイドなので、節には 1 回しか出ない。"""
    from app.prompts import video_prompt_guides_section

    mark_available(monkeypatch)
    section = video_prompt_guides_section()
    assert section.count("VIDEO PROMPT SPEC — ByteDance Seedance 2") == 1


def test_a_full_job_bridges_comfyui_images_into_seedance(client, fake_kie, comfy_env,
                                                         job_env, monkeypatch):
    """ローカルで画像を作り、その画像を Seedance に渡して動画にする。"""
    async def fake_last_frame(video, dest):
        dest.write_bytes(b"png")
        return dest

    monkeypatch.setattr(jobs, "extract_last_frame", fake_last_frame)
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "task-seedance"})))
    fake_kie.answer("record", success(["https://cdn.kie.ai/out.mp4"], credits=120))
    fake_kie.answer(
        "upload", FakeResponse(envelope({"fileUrl": "https://files.kie.ai/gen.png"}))
    )

    created = client.post(
        "/api/jobs",
        json={
            "mode": "full",
            "image_workflow": workflows.DEFAULT_IMAGE_WORKFLOW,  # ComfyUI
            "video_workflow": SEEDANCE.id,  # kie.ai
            "image_prompt": "a cat on a roof",
            "video_prompt": (
                "The tabby cat stretches slowly on the warm tin roof."
                " Slow push-in. Avoid jitter and bent limbs."
            ),
            "selects": {"duration": "10", "resolution": "1080p"},
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])
    assert job["status"] == "done", job["error"]

    stages = job["workflow_json"]
    assert len(comfy_env.queued) == 1
    assert stages["image"]["workflow_id"] == workflows.DEFAULT_IMAGE_WORKFLOW
    assert stages["video"]["backend"] == "kie"
    assert stages["video"]["task_id"] == "task-seedance"
    assert stages["video"]["request"]["api"] == "market"

    # 1 段目の静止画を kie に上げ直して開始フレームにしている
    task_input = stages["video"]["request"]["input"]
    assert task_input["first_frame_url"] == "https://files.kie.ai/gen.png"
    assert task_input["duration"] == 10
    assert task_input["resolution"] == "1080p"
    assert task_input["generate_audio"] is True

    body = fake_kie.sent("create")[0]["json"]
    assert body["model"] == "bytedance/seedance-2"
    assert body["input"]["first_frame_url"] == "https://files.kie.ai/gen.png"

    assert (job_env / job["id"] / "image.png").is_file()
    assert (job_env / job["id"] / "video.mp4").is_file()
    assert job["credits_consumed"] == 120


def _reference_assets(job_env, count: int = 2) -> list[str]:
    """``assets/image/`` に参照画像を置き、ジョブに書ける URL を返す。"""
    directory = job_env.parent / "assets" / "image"
    urls = []
    for index in range(count):
        (directory / f"ref{index}.png").write_bytes(b"png")
        urls.append(f"/assets/image/ref{index}.png")
    return urls


def test_reference_material_travels_from_the_job_to_the_task_input(
    client, fake_kie, job_env, monkeypatch
):
    """ローカルのファイルが 1 本ずつ上がり、URL の配列で input に載る。"""
    async def fake_last_frame(video, dest):
        dest.write_bytes(b"png")
        return dest

    monkeypatch.setattr(jobs, "extract_last_frame", fake_last_frame)
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "task-ref"})))
    fake_kie.answer("record", success(["https://cdn.kie.ai/out.mp4"], credits=70))
    fake_kie.answer(
        "upload",
        FakeResponse(envelope({"fileUrl": "https://files.kie.ai/ref0.png"})),
        FakeResponse(envelope({"fileUrl": "https://files.kie.ai/ref1.png"})),
    )
    references = _reference_assets(job_env)

    created = client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": SEEDANCE.id,
            "video_prompt": (
                "She steps out of the doorway into the rain and looks up."
                " Slow push-in. Avoid jitter and bent limbs."
            ),
            "reference_images": references,
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])
    assert job["status"] == "done", job["error"]

    task_input = job["workflow_json"]["video"]["request"]["input"]
    assert task_input["reference_image_urls"] == [
        "https://files.kie.ai/ref0.png",
        "https://files.kie.ai/ref1.png",
    ]
    assert "first_frame_url" not in task_input
    # 参照素材はジョブの params にも残る（再実行で同じ素材を使う）
    assert len(job["params"]["reference_images"]) == 2


def test_a_start_frame_and_reference_material_are_mutually_exclusive(client, job_env):
    references = _reference_assets(job_env, 1)
    (job_env.parent / "assets" / "image" / "start.png").write_bytes(b"png")

    answer = client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": SEEDANCE.id,
            "video_prompt": "She turns toward the window.",
            "source_image": "/assets/image/start.png",
            "reference_images": references,
        },
    )
    assert answer.status_code == 422
    assert "同時に指定できません" in answer.text


def test_full_mode_cannot_use_reference_material(client, job_env):
    references = _reference_assets(job_env, 1)

    answer = client.post(
        "/api/jobs",
        json={
            "mode": "full",
            "image_workflow": workflows.DEFAULT_IMAGE_WORKFLOW,
            "video_workflow": SEEDANCE.id,
            "image_prompt": "a cat on a roof",
            "video_prompt": "The cat stretches slowly.",
            "reference_images": references,
        },
    )
    assert answer.status_code == 422
    assert "mode 'full'" in answer.text


def test_too_much_reference_material_is_rejected(client, job_env):
    references = _reference_assets(job_env, 10)

    answer = client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": SEEDANCE.id,
            "video_prompt": "She walks along the quay.",
            "reference_images": references,
        },
    )
    assert answer.status_code == 422
    assert "9 件までです" in answer.text


def test_a_workflow_without_a_reference_mode_rejects_the_material(client, job_env):
    references = _reference_assets(job_env, 1)

    answer = client.post(
        "/api/jobs",
        json={
            "mode": "i2v",
            "video_workflow": KLING.id,
            "video_prompt": "She walks along the quay.",
            "reference_images": references,
        },
    )
    assert answer.status_code == 422
    assert "受け取れません" in answer.text


def test_the_agent_plan_validation_knows_the_reference_rules(client, job_env):
    """プランでも同じ理由で断る（投入前に気づかせる、SPEC §4.3）。"""
    from app.agent_protocol import ActionError, validate_job

    references = _reference_assets(job_env, 1)
    base = {
        "mode": "i2v",
        "video_workflow": SEEDANCE.id,
        "video_prompt": "She walks along the quay.",
    }

    # 参照素材だけなら通る
    payload = validate_job(
        {**base, "reference_images": references}, where="tasks[0].job"
    )
    assert payload.reference_images == references

    with pytest.raises(ActionError, match="同時に指定できません"):
        validate_job(
            {
                **base,
                "source_image": references[0],
                "reference_images": references,
            },
            where="tasks[0].job",
        )
    with pytest.raises(ActionError, match="9 件までです"):
        validate_job(
            {**base, "reference_images": references * 10}, where="tasks[0].job"
        )
    with pytest.raises(ActionError, match="受け取れません"):
        validate_job(
            {**base, "video_workflow": KLING.id, "reference_images": references},
            where="tasks[0].job",
        )
    with pytest.raises(ActionError, match="not found"):
        validate_job(
            {**base, "reference_images": ["/assets/image/missing.png"]},
            where="tasks[0].job",
        )


# --------------------------------------------------------------------------
# Suno V5 系（旧専用系 API、issue #20）
# --------------------------------------------------------------------------

SUNO = workflows.BY_ID["suno_v5"]

STYLE = (
    "dreamy Japanese city-pop, 92 BPM, breathy female vocal, warm Rhodes"
    " electric piano, fretless bass, brushed drums, analog tape saturation"
)
LYRICS = "[Verse 1]\nさいごの電車が 雨をぬけて\n\n[Chorus]\nネオンのように\n\n[End]"


def suno_record(status: str, urls=(), **extra) -> FakeResponse:
    """``/generate/record-info`` の応答（成果物は ``response.sunoData[]``）。"""
    data = {"taskId": "suno-1", "status": status, **extra}
    if urls:
        data["response"] = {
            "sunoData": [
                {"id": f"t{index}", "audioUrl": url, "duration": 180.0}
                for index, url in enumerate(urls)
            ]
        }
    return FakeResponse(envelope(data))


def _suno_params(**overrides) -> GenerationParams:
    base = dict(
        mode="audio",
        job_id="job-suno",
        audio_workflow=SUNO.id,
        audio_prompt=STYLE,
        lyrics=LYRICS,
    )
    base.update(overrides)
    return _params(**base)


def test_suno_uses_its_own_endpoints():
    api = kie.task_api("suno")
    assert api.create_url.endswith("/api/v1/generate")
    assert api.record_url.endswith("/api/v1/generate/record-info")
    assert SUNO.kie.api == "suno"
    assert SUNO.kie.model == "V5"
    assert SUNO.backend == "kie" and SUNO.kind == "audio"


def test_the_style_and_the_lyrics_land_in_the_right_keys():
    request = kie.build_request(SUNO, _suno_params(), {})
    body = kie.task_api(request.api).create_body(request.model, request.input)

    # ボディは平ら（input で包まない）
    assert body["model"] == "V5"
    assert body["customMode"] is True
    # audio_prompt -> style（音の記述）、lyrics -> prompt（歌う言葉）
    assert body["style"] == STYLE
    assert body["prompt"] == LYRICS
    assert body["instrumental"] is False
    # title は customMode の必須項目。歌詞の最初の「歌う行」から作る
    assert body["title"] == "さいごの電車が 雨をぬけて"
    # webhook は受け取れないのでダミーを入れてポーリングする
    assert body["callBackUrl"] == kie.CALLBACK_URL
    # 「おまかせ」のボーカル性別はキーごと落とす
    assert "vocalGender" not in body
    # Suno に無いつまみは宣言していない
    for absent in ("bpm", "keyscale", "language", "duration", "negativeTags"):
        assert absent not in body


def test_no_lyrics_means_an_instrumental():
    body = kie.task_api("suno").create_body(
        SUNO.kie.model, kie.build_request(SUNO, _suno_params(lyrics=""), {}).input
    )
    assert body["instrumental"] is True
    assert "prompt" not in body  # 空の値はキーごと落ちる
    # 歌詞が無いのでタイトルはスタイルの頭から
    assert body["title"] == "dreamy Japanese city-pop"


def test_the_model_version_and_the_vocal_gender_are_selects():
    request = kie.build_request(
        SUNO,
        _suno_params(
            selects={"model": "V5_5", "vocal_gender": "f"},
            negative_tags="distorted guitar, screaming",
        ),
        {},
    )
    body = kie.task_api(request.api).create_body(request.model, request.input)

    # 選択したバージョンがマニフェストの既定（V5）を上書きする
    assert body["model"] == "V5_5"
    assert body["vocalGender"] == "f"
    assert body["negativeTags"] == "distorted guitar, screaming"


def test_the_weights_are_sent_as_numbers():
    """``styleWeight`` などは 0〜1 の**小数**（選択肢は文字列で届く）。"""
    request = kie.build_request(
        SUNO,
        _suno_params(
            selects={
                "style_weight": "0.75",
                "weirdness": "0",
                "audio_weight": "1",
            }
        ),
        {},
    )
    body = kie.task_api(request.api).create_body(request.model, request.input)

    assert body["styleWeight"] == 0.75
    assert body["weirdnessConstraint"] == 0.0
    assert body["audioWeight"] == 1.0
    for key in ("styleWeight", "weirdnessConstraint", "audioWeight"):
        assert isinstance(body[key], float)


def test_auto_weights_are_dropped_from_the_body():
    """``auto`` は「指定しない」（0 を送ると「0 を指定した」になってしまう）。"""
    request = kie.build_request(SUNO, _suno_params(), {})
    body = kie.task_api(request.api).create_body(request.model, request.input)

    for key in ("styleWeight", "weirdnessConstraint", "audioWeight"):
        assert key not in body
    # vocal_gender の auto と同じ流儀（キーごと落ちる）
    assert "vocalGender" not in body


def test_one_weight_can_be_set_while_the_others_stay_auto():
    request = kie.build_request(
        SUNO, _suno_params(selects={"weirdness": "0.5"}), {}
    )
    body = kie.task_api(request.api).create_body(request.model, request.input)

    assert body["weirdnessConstraint"] == 0.5
    assert "styleWeight" not in body
    assert "audioWeight" not in body


def test_suno_polls_through_its_own_status_words(fake_kie):
    """``PENDING -> TEXT_SUCCESS -> FIRST_SUCCESS -> SUCCESS``（独自の語彙）。"""
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "suno-1"})))
    fake_kie.answer(
        "record",
        suno_record("PENDING"),
        suno_record("TEXT_SUCCESS"),
        suno_record("FIRST_SUCCESS"),
        suno_record(
            "SUCCESS",
            ("https://cdn.kie.ai/a.mp3", "https://cdn.kie.ai/b.mp3"),
            creditsConsumed=12,
        ),
    )
    labels: list[str] = []

    async def run():
        task_id = await kie.create_task("V5", {"style": STYLE}, api=kie.SUNO)
        return await kie.wait_for_task(
            task_id, api=kie.SUNO, on_progress=lambda state: _collect(labels, state)
        )

    state = asyncio.run(run())

    assert state.phase == "success"
    # 1 リクエストで 2 曲。**両方**回収する
    assert state.result_urls == (
        "https://cdn.kie.ai/a.mp3",
        "https://cdn.kie.ai/b.mp3",
    )
    assert state.credits == 12.0
    assert labels == ["PENDING", "TEXT_SUCCESS", "FIRST_SUCCESS"]
    assert fake_kie.sent("record")[0]["params"] == {"taskId": "suno-1"}


def test_a_failed_suno_task_reports_the_reason(fake_kie):
    fake_kie.answer(
        "record",
        suno_record("SENSITIVE_WORD_ERROR", errorMessage="lyrics were rejected"),
    )
    with pytest.raises(kie.KieError) as caught:
        asyncio.run(kie.wait_for_task("suno-1", api=kie.SUNO))
    assert "lyrics were rejected" in str(caught.value)


def test_an_unknown_failure_status_is_still_a_failure(fake_kie):
    """kie.ai は ``*_FAILED`` / ``*_ERROR`` を増やしてくるので待ち続けない。"""
    fake_kie.answer("record", suno_record("GENERATE_AUDIO_FAILED"))
    with pytest.raises(kie.KieError) as caught:
        asyncio.run(kie.wait_for_task("suno-1", api=kie.SUNO))
    assert "GENERATE_AUDIO_FAILED" in str(caught.value)

    fake_kie.answer("record", suno_record("SOMETHING_NEW_FAILED"))
    with pytest.raises(kie.KieError):
        asyncio.run(kie.wait_for_task("suno-1", api=kie.SUNO))


def test_a_suno_success_without_urls_is_an_error(fake_kie):
    fake_kie.answer("record", suno_record("SUCCESS"))
    with pytest.raises(kie.KieError) as caught:
        asyncio.run(kie.wait_for_task("suno-1", api=kie.SUNO))
    assert "成果物 URL" in str(caught.value)


def test_an_audio_job_saves_both_takes(client, fake_kie, job_env):
    """1 リクエスト 2 曲。1 曲目が列に入り、2 曲目は extra_outputs へ（§6）。"""
    fake_kie.answer("create", FakeResponse(envelope({"taskId": "task-suno"})))
    fake_kie.answer(
        "record",
        suno_record(
            "SUCCESS",
            ("https://cdn.kie.ai/a.mp3", "https://cdn.kie.ai/b.mp3"),
            creditsConsumed=12,
        ),
    )

    created = client.post(
        "/api/jobs",
        json={
            "mode": "audio",
            "audio_workflow": SUNO.id,
            "audio_prompt": STYLE,
            "lyrics": LYRICS,
            "negative_tags": "distorted guitar",
            "selects": {"model": "V5_5", "vocal_gender": "f"},
        },
    )
    assert created.status_code == 201, created.text
    job = wait_for(client, created.json()["id"])
    assert job["status"] == "done", job["error"]

    directory = job_env / job["id"]
    assert (directory / "audio.mp3").is_file()
    assert (directory / "audio_2.mp3").is_file()
    assert job["audio_output_url"].endswith("/audio.mp3")
    assert [url.rsplit("/", 1)[-1] for url in job["extra_output_urls"]] == [
        "audio_2.mp3"
    ]
    assert job["credits_consumed"] == 12

    stage = job["workflow_json"]["audio"]
    assert stage["backend"] == "kie" and stage["request"]["api"] == "suno"
    body = fake_kie.sent("create")[0]["json"]
    assert body["model"] == "V5_5"
    assert body["style"] == STYLE
    assert body["prompt"] == LYRICS
    assert body["negativeTags"] == "distorted guitar"
    assert body["vocalGender"] == "f"
    assert body["customMode"] is True
    assert body["instrumental"] is False


def test_ace_step_only_knobs_are_refused_for_suno():
    """モデルが読まないフィールドは黙って捨てず、プラン検証で断る（§2.4）。"""
    from app import agent_protocol

    for name, value in (("bpm", 92), ("keyscale", "F# minor"), ("language", "ja")):
        with pytest.raises(agent_protocol.ActionError, match=name):
            agent_protocol.validate_job(
                {
                    "mode": "audio",
                    "audio_workflow": SUNO.id,
                    "audio_prompt": STYLE,
                    name: value,
                },
                where="task 1",
            )

    # 宣言してあるものは通る（歌詞・除外タグ・選択式）
    agent_protocol.validate_job(
        {
            "mode": "audio",
            "audio_workflow": SUNO.id,
            "audio_prompt": STYLE,
            "lyrics": LYRICS,
            "negative_tags": "screaming",
            "selects": {"model": "V5_5"},
        },
        where="task 1",
    )
    # 宣言していない選択肢は 422（選択式の検証も音声ワークフローを見る）
    with pytest.raises(agent_protocol.ActionError, match="resolution"):
        agent_protocol.validate_job(
            {
                "mode": "audio",
                "audio_workflow": SUNO.id,
                "audio_prompt": STYLE,
                "selects": {"resolution": "1080p"},
            },
            where="task 1",
        )


def test_suno_is_offered_as_an_audio_workflow(client, monkeypatch):
    mark_available(monkeypatch)
    body = client.get("/api/options").json()
    entry = [wf for wf in body["audio_workflows"] if wf["id"] == SUNO.id][0]

    assert entry["backend"] == "kie"
    assert set(entry["supports"]) == {"prompt", "lyrics", "negative_tags"}
    # 尺のパラメータが無いので長さは宣言しない（フォームは秒数欄を出さない）
    assert entry["max_duration"] == 0
    selects = {select["name"]: select for select in entry["selects"]}
    assert list(selects) == [
        "model", "vocal_gender", "style_weight", "weirdness", "audio_weight",
    ]
    assert selects["model"]["choices"] == ["V5", "V5_5", "V4_5PLUS"]
    assert selects["model"]["default"] == "V5"
    assert selects["vocal_gender"]["choices"] == ["auto", "m", "f"]
    # 0〜1 の重みづけは 0.25 刻み + auto（= 指定しない）が既定
    for name in ("style_weight", "weirdness", "audio_weight"):
        assert selects[name]["choices"] == ["auto", "0", "0.25", "0.5", "0.75", "1"]
        assert selects[name]["default"] == "auto"


def test_the_suno_guide_is_injected_only_when_suno_is_selected():
    from app.models import ChatSessionCreate
    from app.prompts import build_system_prompt

    prompt = build_system_prompt(
        ChatSessionCreate(mode="audio", audio_workflow=SUNO.id)
    )
    assert "AUDIO PROMPT SPEC — Suno V5" in prompt
    # style の作法・メタタグ・日本語歌詞・除外タグの行き先
    assert "120-300 characters" in prompt
    assert "[Pre-Chorus]" in prompt
    assert "Japanese lyrics just work" in prompt
    assert "negative_tags" in prompt

    ace = build_system_prompt(ChatSessionCreate(mode="audio"))
    assert "Suno V5" not in ace


def test_the_agent_prompt_carries_the_suno_guide(monkeypatch):
    from app.prompts import audio_prompt_guides_section, audio_workflow_catalog_section

    mark_available(monkeypatch)
    assert audio_prompt_guides_section().count("AUDIO PROMPT SPEC — Suno V5") == 1
    catalog = audio_workflow_catalog_section()
    assert f"`{SUNO.id}`" in catalog
    # 尺が無いことと選択肢がカタログに出ている
    assert "長さの指定がありません" in catalog
    assert "`vocal_gender`" in catalog
