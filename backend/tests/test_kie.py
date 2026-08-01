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
        # 旧専用系（Veo）は別のパスだが、テストからは同じ「作る / 見に行く」
        if "createTask" in url or "/veo/generate" in url:
            return "create"
        if "recordInfo" in url or "record-info" in url:
            return "record"
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
        kie.task_api("suno")


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
    assert set(veo["supports"]) == {"prompt", "image", "end_image"}
    assert veo["accepts_start_image"] is True
    assert [select["name"] for select in veo["selects"]] == [
        "aspect_ratio",
        "duration",
        "resolution",
    ]


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


def test_kling_is_offered_as_a_video_workflow(client, monkeypatch):
    mark_available(monkeypatch)
    body = client.get("/api/options").json()
    kling = [wf for wf in body["video_workflows"] if wf["id"] == KLING.id][0]

    assert kling["requires"] == []
    assert set(kling["supports"]) == {"prompt", "image", "end_image"}
    assert kling["accepts_start_image"] is True
    assert kling["backend"] == "kie"
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
