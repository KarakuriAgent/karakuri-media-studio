"""Web Push: VAPID, 購読、ジョブ / エージェント / キャンバスからの通知フック。"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import agent_runner, canvas_agent, db, jobs, push
from app.main import app
from app.models import AgentAction


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def notifications(monkeypatch):
    calls: list[dict] = []

    async def fake(title, body, *, url="/", tag=""):
        calls.append({"title": title, "body": body, "url": url, "tag": tag})

    monkeypatch.setattr(push, "notify_all", fake)
    return calls


async def _insert_job(
    job_id: str,
    *,
    status: str = "running",
    chat_session_id: str | None = None,
) -> None:
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO jobs (id, created_at, mode, status, params, workflow_json,"
            " chat_session_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                "2026-01-01T00:00:00Z",
                "image_only",
                status,
                "{}",
                "{}",
                chat_session_id,
            ),
        )
        await conn.commit()


async def _insert_agent(session_id: str, *, status: str = "idle") -> None:
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO agent_sessions (id, created_at, status) VALUES (?, ?, ?)",
            (session_id, "2026-01-01T00:00:00Z", status),
        )
        await conn.commit()


async def _insert_chat(session_id: str) -> None:
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO chat_sessions (id, created_at, messages) VALUES (?, ?, ?)",
            (session_id, "2026-01-01T00:00:00Z", "[]"),
        )
        await conn.commit()


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_vapid_public_key_is_url_base64(client):
    response = client.get("/api/push/vapid-public-key")
    assert response.status_code == 200, response.text
    key = response.json()["public_key"]
    assert len(key) > 20
    assert "+" not in key and "/" not in key
    again = client.get("/api/push/vapid-public-key").json()["public_key"]
    assert again == key


def test_stored_pem_is_accepted_by_webpush(client, monkeypatch):
    """保存している PEM を webpush に渡してもキー形式で落ちない。"""
    from py_vapid import Vapid01

    from app import push

    client.get("/api/push/vapid-public-key")
    seen = []

    def fake_webpush(**kwargs):
        seen.append(kwargs["vapid_private_key"])

    monkeypatch.setattr("pywebpush.webpush", fake_webpush)
    push.send_webpush(
        {
            "endpoint": "https://push.example/x",
            "keys": {"p256dh": "p", "auth": "a"},
        },
        "{}",
        push.load_or_create_vapid()[0],
    )
    assert seen and isinstance(seen[0], Vapid01)


def test_subscribe_upsert_and_delete(client):
    body = {
        "endpoint": "https://push.example/abc",
        "keys": {"p256dh": "p1", "auth": "a1"},
    }
    assert client.post("/api/push/subscriptions", json=body).status_code == 204
    body["keys"] = {"p256dh": "p2", "auth": "a2"}
    assert client.post("/api/push/subscriptions", json=body).status_code == 204
    deleted = client.delete(
        "/api/push/subscriptions", params={"endpoint": body["endpoint"]}
    )
    assert deleted.status_code == 204
    assert client.post("/api/push/subscriptions", json=body).status_code == 204
    via_body = client.request(
        "DELETE", "/api/push/subscriptions", json={"endpoint": body["endpoint"]}
    )
    assert via_body.status_code == 204


async def test_subscription_upsert_overwrites_keys(client):
    await push.upsert_subscription("https://push.example/abc", "p1", "a1")
    await push.upsert_subscription("https://push.example/abc", "p2", "a2")
    rows = await push.list_subscriptions()
    assert len(rows) == 1
    assert rows[0]["p256dh"] == "p2"
    assert rows[0]["auth"] == "a2"
    assert await push.delete_subscription("https://push.example/abc")
    assert await push.list_subscriptions() == []


def test_delete_subscription_needs_an_endpoint(client):
    assert client.delete("/api/push/subscriptions").status_code == 422


# --------------------------------------------------------------------------
# notify_all（送信関数をモック。pywebpush は叩かない）
# --------------------------------------------------------------------------


async def test_notify_all_sends_payload_and_drops_gone_subscriptions(
    client, monkeypatch
):
    await push.upsert_subscription("https://ok.example", "p", "a")
    await push.upsert_subscription("https://gone.example", "p", "a")
    sent: list[tuple[str, str]] = []

    def fake_send(info, data, key, claims=None):
        sent.append((info["endpoint"], data))
        if info["endpoint"] == "https://gone.example":
            raise push.SubscriptionGone("gone")

    monkeypatch.setattr(push, "send_webpush", fake_send)
    await push.notify_all("完了", "本文", url="/jobs", tag="job-done")

    endpoints = {item[0] for item in sent}
    assert endpoints == {"https://ok.example", "https://gone.example"}
    payload = json.loads(sent[0][1])
    assert payload == {
        "title": "完了",
        "body": "本文",
        "url": "/jobs",
        "tag": "job-done",
    }
    remaining = await push.list_subscriptions()
    assert [row["endpoint"] for row in remaining] == ["https://ok.example"]


async def test_notify_all_without_subscriptions_does_not_send(client, monkeypatch):
    called = []
    monkeypatch.setattr(push, "send_webpush", lambda *a, **k: called.append(1))
    await push.notify_all("x", "y")
    assert called == []


# --------------------------------------------------------------------------
# ジョブ
# --------------------------------------------------------------------------


async def test_user_job_done_notifies(client, notifications):
    await _insert_job("job-user")
    await jobs._set_status("job-user", "done")
    assert notifications == [
        {
            "title": "生成が完了しました",
            "body": "生成が完了しました",
            "url": "/",
            "tag": "job-done",
        }
    ]


async def test_user_job_failed_and_canceled_notify(client, notifications):
    await _insert_job("job-fail")
    await jobs._set_status("job-fail", "failed")
    await _insert_job("job-cancel")
    await jobs._set_status("job-cancel", "canceled")
    titles = [item["title"] for item in notifications]
    assert titles == ["生成に失敗しました", "生成がキャンセルされました"]


async def test_same_job_status_is_not_notified_twice(client, notifications):
    await _insert_job("job-dup", status="done")
    await jobs._set_status("job-dup", "done")
    assert notifications == []


async def test_agent_job_does_not_notify(client, notifications):
    await _insert_agent("agent-1")
    await _insert_job("job-agent", chat_session_id="agent-1")
    await jobs._set_status("job-agent", "done")
    assert notifications == []


async def test_chat_session_job_does_notify(client, notifications):
    await _insert_chat("chat-1")
    await _insert_job("job-chat", chat_session_id="chat-1")
    await jobs._set_status("job-chat", "done")
    assert [item["tag"] for item in notifications] == ["job-done"]


async def test_running_status_does_not_notify(client, notifications):
    await _insert_job("job-run", status="queued")
    await jobs._set_status("job-run", "running")
    assert notifications == []


# --------------------------------------------------------------------------
# エージェント
# --------------------------------------------------------------------------


async def test_agent_waiting_checkin_done_and_stopped_notify(client, notifications):
    await _insert_agent("sess-1", status="running")
    await agent_runner._set_status("sess-1", "waiting_checkin")
    await agent_runner._set_status("sess-1", "waiting_checkin")
    await agent_runner._set_status("sess-1", "done")
    await agent_runner._set_status("sess-1", "stopped")
    assert [item["tag"] for item in notifications] == [
        "agent-waiting_checkin",
        "agent-done",
        "agent-stopped",
    ]
    assert notifications[0]["title"] == "エージェントが確認を待っています"
    assert notifications[1]["title"] == "エージェントの処理が終わりました"
    assert notifications[2]["title"] == "処理が止まりました"


async def test_agent_planning_notifies_once(client, notifications):
    await _insert_agent("sess-plan", status="running")
    await agent_runner._apply_plan(
        "sess-plan", AgentAction(action="plan", notes="2本の案です")
    )
    await agent_runner._apply_plan(
        "sess-plan", AgentAction(action="plan", notes="改訂")
    )
    assert [item["tag"] for item in notifications] == ["agent-planning"]
    assert notifications[0]["title"] == "プランの承認待ちです"


async def test_agent_idle_does_not_notify(client, notifications):
    await _insert_agent("sess-idle", status="running")
    await agent_runner._set_status("sess-idle", "idle")
    assert notifications == []


# --------------------------------------------------------------------------
# キャンバス
# --------------------------------------------------------------------------


async def test_canvas_loop_end_notifies(client, notifications, monkeypatch):
    async def fake_loop(project_id, session_id):
        return "done"

    monkeypatch.setattr(canvas_agent, "_loop", fake_loop)
    await canvas_agent._run("proj-1", "sess-1")
    assert notifications == [
        {
            "title": "スタジオの作業が終わりました",
            "body": "スタジオの作業が終わりました",
            "url": "/",
            "tag": "canvas",
        }
    ]


async def test_canvas_stop_and_error_notify(client, notifications, monkeypatch):
    async def stopped(project_id, session_id):
        return "stopped"

    monkeypatch.setattr(canvas_agent, "_loop", stopped)
    await canvas_agent._run("proj-2", "sess-2")
    assert notifications[-1]["title"] == "スタジオの作業が止まりました"


async def test_canvas_lifespan_cancel_does_not_notify(client, notifications, monkeypatch):
    async def cancelled(project_id, session_id):
        raise asyncio.CancelledError

    monkeypatch.setattr(canvas_agent, "_loop", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await canvas_agent._run("proj-3", "sess-3")
    assert notifications == []
