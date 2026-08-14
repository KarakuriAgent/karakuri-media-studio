"""キャンバスのチャットからのエージェント実行（AGENT-MODE §5.4 / app.canvas_agent）。

Grok と ComfyUI は test_agent.py と同じ仕掛けで完全にモックする。見るのは
「アクションがどう解釈され、カードとスタジオに何が起きて、会話に何が残ったか」。
"""

import asyncio
import time
from pathlib import Path

import pytest

from app import agent_protocol, canvas_agent, grok, jobs
from tests.test_jobs import _hang_until_cancelled, wait_for

# test_agent.py の env フィクスチャ（Grok / ComfyUI のモック一式）をそのまま使う。
from test_agent import (  # noqa: F401 - フィクスチャの再エクスポート
    action_answer,
    env,
    sample_video,
    tweak_settings,
)


# --------------------------------------------------------------------------
# ヘルパー
# --------------------------------------------------------------------------

def make_project(env, **overrides) -> dict:
    body = {"name": "深夜のラーメン屋", "auto_translate": False}
    body.update(overrides)
    created = env.client.post("/api/studio/projects", json=body)
    assert created.status_code == 201, created.text
    return created.json()


def make_asset(env, project_id: str, **overrides) -> dict:
    """メタデータだけの素材（登録はフォーム送信の口を使う）。"""
    data = {"name": "アキ", "category": "character", "kind": "image"}
    data.update(overrides)
    created = env.client.post(f"/api/studio/projects/{project_id}/assets", data=data)
    assert created.status_code == 201, created.text
    return created.json()


def make_card(env, project_id: str, kind: str, **body) -> dict:
    created = env.client.post(
        f"/api/canvas/projects/{project_id}/cards", json={"kind": kind, **body}
    )
    assert created.status_code == 201, created.text
    return created.json()


def make_episode(env, project_id: str, **body) -> dict:
    created = env.client.post(
        f"/api/studio/projects/{project_id}/episodes", json={"title": "第1話", **body}
    )
    assert created.status_code == 201, created.text
    return created.json()


def board(env, project_id: str, episode_id: str | None = None) -> dict:
    params = {"episode_id": episode_id} if episode_id else None
    response = env.client.get(f"/api/canvas/projects/{project_id}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def messages(env, project_id: str) -> list[dict]:
    return board(env, project_id)["messages"]


def kinds(env, project_id: str) -> list[str]:
    return [m["kind"] for m in messages(env, project_id) if m["kind"]]


def event_of(env, project_id: str, kind: str) -> dict:
    found = [m for m in messages(env, project_id) if m["kind"] == kind]
    assert found, f"{kind} イベントがありません: {kinds(env, project_id)}"
    return found[-1]


def revisions(env, project_id: str) -> list[dict]:
    return env.client.get(f"/api/studio/projects/{project_id}/revisions").json()


def ask(
    env,
    project_id: str,
    answers: list[str],
    said: str = "お願い",
    episode_id: str | None = None,
) -> dict:
    """1 発言ぶん走らせて、実行が終わるまで待つ（``episode_id`` = 開いたタブ）。"""
    env.cli.answers = list(answers)
    response = env.client.post(
        f"/api/canvas/projects/{project_id}/agent",
        json={"content": said, "episode_id": episode_id},
    )
    assert response.status_code == 202, response.text
    wait_idle(env, project_id)
    return response.json()


def wait_idle(env, project_id: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = env.client.get(f"/api/canvas/projects/{project_id}/agent").json()
        if not state["running"]:
            return
        time.sleep(0.02)
    raise AssertionError("canvas agent is stuck")


#: アクションを 1 つ実行して、次のターンで普通に返事をする台本
def script(payload: dict) -> list[str]:
    return [action_answer(payload), "できました。"]


# --------------------------------------------------------------------------
# パース（AGENT-MODE §4）
# --------------------------------------------------------------------------

def test_canvas_actions_are_part_of_the_protocol(env):
    for name in agent_protocol.CANVAS_ACTIONS:
        assert name in agent_protocol.ACTION_NAMES


def test_parse_place_card(env):
    action = agent_protocol.parse_action(
        action_answer(
            {
                "action": "canvas_place_card",
                "project_id": "p1",
                "kind": "shot",
                "title": "湯気",
                "x": 640,
                "y": 220,
            }
        )
    )
    assert action is not None
    assert action.canvas["project_id"] == "p1"
    assert action.canvas["body"] == {
        "kind": "shot", "title": "湯気", "x": 640, "y": 220
    }


@pytest.mark.parametrize(
    "payload, needle",
    [
        ({"action": "canvas_list_cards"}, "project_id"),
        ({"action": "canvas_place_card"}, "project_id"),
        ({"action": "canvas_place_card", "project_id": "p1"}, "kind"),
        ({"action": "canvas_move_card", "x": 0, "y": 0}, "card_id"),
        ({"action": "canvas_move_card", "card_id": "c1"}, "canvas_move_card"),
        ({"action": "canvas_update_card"}, "card_id"),
        ({"action": "canvas_read_session"}, "session_id"),
    ],
)
def test_a_canvas_action_without_its_target_is_rejected(env, payload, needle):
    with pytest.raises(agent_protocol.ActionError, match=needle):
        agent_protocol.parse_action(action_answer(payload))


def test_an_unknown_canvas_field_is_rejected(env):
    with pytest.raises(agent_protocol.ActionError, match="未知のフィールド"):
        agent_protocol.parse_action(
            action_answer(
                {
                    "action": "canvas_place_card",
                    "project_id": "p1",
                    "kind": "text",
                    "colour": "red",
                }
            )
        )


# --------------------------------------------------------------------------
# 実行（キャンバス固有のツール）
# --------------------------------------------------------------------------

def test_the_agent_creates_the_entity_it_places(env):
    """既にあるものは鏡が並べるので、置く = 新しく作る。"""
    project = make_project(env)
    ask(
        env,
        project["id"],
        script(
            {
                "action": "canvas_place_card",
                "project_id": project["id"],
                "kind": "character",
                "title": "アキ",
                "x": 320,
                "y": 120,
            }
        ),
    )
    cards = board(env, project["id"])["cards"]
    assert [(c["kind"], c["x"], c["y"]) for c in cards] == [("character", 320, 120)]
    studio = env.client.get(f"/api/studio/projects/{project['id']}").json()
    assert [a["name"] for a in studio["assets"]] == ["アキ"]
    assert "canvas_card_placed" in kinds(env, project["id"])


def test_an_existing_entity_cannot_be_placed_again(env):
    """`entity_id` は無くなった（盤面はスタジオの鏡なので置き直しは無い）。"""
    with pytest.raises(agent_protocol.ActionError, match="未知のフィールド"):
        agent_protocol.parse_action(
            action_answer(
                {
                    "action": "canvas_place_card",
                    "project_id": "p1",
                    "kind": "character",
                    "entity_id": "a1",
                }
            )
        )


def test_a_card_placed_by_the_agent_says_so_in_the_history(env):
    """人が置いたカードと見分けが付くよう、リビジョンの主体は agent。"""
    project = make_project(env)
    ask(
        env,
        project["id"],
        script(
            {
                "action": "canvas_place_card",
                "project_id": project["id"],
                "kind": "shot",
                "title": "湯気",
            }
        ),
    )
    added = [
        rev for rev in revisions(env, project["id"]) if "カードを追加" in rev["action"]
    ]
    assert [rev["actor"] for rev in added] == ["agent"]


def test_the_agent_can_create_a_canvas_only_card(env):
    project = make_project(env)
    ask(
        env,
        project["id"],
        script(
            {
                "action": "canvas_place_card",
                "project_id": project["id"],
                "kind": "text",
                "data": {"body": "湯気のカットを足す"},
                "x": 0,
                "y": 0,
            }
        ),
    )
    card = board(env, project["id"])["cards"][0]
    assert card["kind"] == "text"
    assert card["data"] == {"body": "湯気のカットを足す"}


def test_the_agent_updates_a_text_card(env):
    project = make_project(env)
    card = make_card(env, project["id"], "text", data={"body": "前のメモ"})
    ask(
        env,
        project["id"],
        script(
            {
                "action": "canvas_update_card",
                "card_id": card["id"],
                "data": {"body": "書き直したメモ"},
            }
        ),
    )
    assert board(env, project["id"])["cards"][0]["data"] == {"body": "書き直したメモ"}


def test_the_agent_moves_a_card(env):
    project = make_project(env)
    card = make_card(env, project["id"], "text", data={"body": "メモ"})
    ask(
        env,
        project["id"],
        script(
            {"action": "canvas_move_card", "card_id": card["id"], "x": 900, "y": 40}
        ),
    )
    moved = board(env, project["id"])["cards"][0]
    assert (moved["x"], moved["y"]) == (900, 40)


def test_the_agent_can_read_the_board(env):
    project = make_project(env)
    make_asset(env, project["id"])  # 鏡が並べるので、カードは作らなくてよい
    ask(
        env,
        project["id"],
        script({"action": "canvas_list_cards", "project_id": project["id"]}),
    )
    event = event_of(env, project["id"], "canvas_cards")
    assert event["data"]["count"] == 1
    assert "@アキ" in event["content"]


def test_the_board_is_burnt_into_the_prompt(env):
    """1 往復減らすため、いまの盤面と作品の現況を先に渡してある。"""
    project = make_project(env)
    asset = make_asset(env, project["id"])
    ask(env, project["id"], ["盤面を見ました。"])
    prompt = env.cli.prompts[0]
    assert "# CANVAS BOARD" in prompt
    assert "# THIS PROJECT" in prompt
    assert f"`{asset['id']}`" in prompt


# --------------------------------------------------------------------------
# タブ（作品共通 + 話ごと）
# --------------------------------------------------------------------------

def test_the_open_tab_is_what_the_prompt_shows(env):
    """盤面は開いているタブぶんだけ。他のタブは一覧で存在だけ伝える。"""
    project = make_project(env)
    asset = make_asset(env, project["id"])  # 素材 = 作品共通タブ
    episode = make_episode(env, project["id"], title="第1話")
    ask(env, project["id"], ["見ました。"], episode_id=episode["id"])

    prompt = env.cli.prompts[0]
    assert "「第1話」" in prompt
    assert f"`{episode['id']}`" in prompt
    assert "# CANVAS TABS" in prompt
    # 素材は作品共通タブなので、第1話の盤面には出ない
    assert f"カード `{asset['id']}`" not in prompt
    assert "まだカードがありません" in prompt


def test_a_note_the_agent_places_lands_on_the_open_tab(env):
    project = make_project(env)
    episode = make_episode(env, project["id"], title="第1話")
    ask(
        env,
        project["id"],
        script(
            {
                "action": "canvas_place_card",
                "project_id": project["id"],
                "kind": "text",
                "data": {"body": "この話の山場"},
            }
        ),
        episode_id=episode["id"],
    )
    assert board(env, project["id"])["cards"] == []  # 作品共通には出ない
    placed = board(env, project["id"], episode["id"])["cards"]
    assert [card["episode_id"] for card in placed] == [episode["id"]]


def test_a_scene_the_agent_places_lands_in_the_open_episode(env):
    project = make_project(env)
    make_episode(env, project["id"], title="第1話")
    second = make_episode(env, project["id"], title="第2話")
    ask(
        env,
        project["id"],
        script(
            {
                "action": "canvas_place_card",
                "project_id": project["id"],
                "kind": "scene",
                "title": "路地",
            }
        ),
        episode_id=second["id"],
    )
    scenes = env.client.get(f"/api/studio/projects/{project['id']}").json()["scenes"]
    assert [scene["episode_id"] for scene in scenes] == [second["id"]]


def test_the_agent_moves_a_scene_to_another_episode(env):
    """タブを移す操作 = 場の引っ越し（`studio_upsert_scene` の episode_id）。"""
    project = make_project(env)
    first = make_episode(env, project["id"], title="第1話")
    second = make_episode(env, project["id"], title="第2話")
    scene = env.client.post(
        f"/api/studio/episodes/{first['id']}/scenes", json={"title": "路地"}
    ).json()
    ask(
        env,
        project["id"],
        script(
            {
                "action": "studio_upsert_scene",
                "id": scene["id"],
                "episode_id": second["id"],
            }
        ),
    )
    assert board(env, project["id"], first["id"])["cards"] == []
    moved = board(env, project["id"], second["id"])["cards"]
    assert [card["entity_id"] for card in moved] == [scene["id"]]


def test_the_agent_can_read_one_tab(env):
    project = make_project(env)
    make_asset(env, project["id"])
    episode = make_episode(env, project["id"], title="第1話")
    ask(
        env,
        project["id"],
        script(
            {
                "action": "canvas_list_cards",
                "project_id": project["id"],
                "episode_id": episode["id"],
            }
        ),
    )
    event = event_of(env, project["id"], "canvas_cards")
    assert event["data"]["count"] == 0  # 素材は作品共通タブにいる
    assert "第1話" in event["content"]


# --------------------------------------------------------------------------
# 実行（スタジオのツールをそのまま共通利用する）
# --------------------------------------------------------------------------

def test_the_agent_writes_the_studio_and_the_revision_says_agent(env):
    project = make_project(env)
    ask(
        env,
        project["id"],
        script(
            {
                "action": "studio_upsert_shot",
                "project_id": project["id"],
                "shot": {"title": "湯気", "prompt": "steam rises", "duration_seconds": 6},
            }
        ),
    )
    shots = env.client.get(f"/api/studio/projects/{project['id']}").json()["shots"]
    assert [shot["title"] for shot in shots] == ["湯気"]
    assert "agent" in {rev["actor"] for rev in revisions(env, project["id"])}


def test_a_failing_tool_is_reported_in_the_conversation(env):
    project = make_project(env)
    ask(
        env,
        project["id"],
        script({"action": "canvas_move_card", "card_id": "nope", "x": 0, "y": 0}),
    )
    failed = event_of(env, project["id"], "action_failed")
    assert "nope" in failed["content"]


def test_generation_planning_is_not_available_from_the_canvas(env):
    """プランと生成はエージェントモードの担当（キャンバスでは断る）。"""
    project = make_project(env)
    ask(
        env,
        project["id"],
        script({"action": "note", "title": "メモ", "content": "本文"}),
    )
    event = event_of(env, project["id"], "action_unavailable")
    assert event["data"]["action"] == "note"


# --------------------------------------------------------------------------
# 会話とループ
# --------------------------------------------------------------------------

def test_the_conversation_keeps_the_whole_run(env):
    project = make_project(env)
    ask(
        env,
        project["id"],
        script(
            {
                "action": "canvas_place_card",
                "project_id": project["id"],
                "kind": "text",
                "data": {"body": "メモ"},
            }
        ),
        said="メモを置いて",
    )
    roles = [(m["role"], m["kind"]) for m in messages(env, project["id"])]
    assert roles == [
        ("user", None),
        ("assistant", None),
        ("event", "canvas_card_placed"),
        ("assistant", None),
    ]
    assert messages(env, project["id"])[0]["content"] == "メモを置いて"


def test_the_conversation_is_fed_back_to_the_next_turn(env):
    project = make_project(env)
    ask(
        env,
        project["id"],
        script({"action": "canvas_list_cards", "project_id": project["id"]}),
        said="いま何がある？",
    )
    second = env.cli.prompts[1]
    assert "いま何がある？" not in second
    assert "# ROLE" not in second
    assert "# EVENT" in second
    assert "canvas_cards" in second


def test_stop_cancels_render_jobs(env, monkeypatch):
    """⏹ はキャンバスのランで投入した studio_render_shot ジョブも止める。"""
    monkeypatch.setattr(jobs, "_run_job_stages", _hang_until_cancelled)
    project = make_project(env)
    shot = env.client.post(
        f"/api/studio/projects/{project['id']}/shots",
        json={"title": "S1", "prompt": "A cat walks in.", "duration_seconds": 5},
    ).json()
    calls = {"n": 0}

    async def exec_then_hang(argv, cwd, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return (
                0,
                action_answer({"action": "studio_render_shot", "shot_id": shot["id"]}),
                "",
            )
        # ワンショット実行は host.cancel が届かないので、停止要求を見て抜ける
        deadline = time.time() + 30
        while time.time() < deadline:
            if project["id"] in canvas_agent._stop_requests:
                return (0, "ok", "")
            await asyncio.sleep(0.05)
        return (0, "ok", "")

    monkeypatch.setattr(grok, "_exec", exec_then_hang)
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/agent", json={"content": "焼いて"}
    )
    assert response.status_code == 202, response.text

    deadline = time.time() + 10
    take = None
    while time.time() < deadline:
        takes = env.client.get(f"/api/studio/shots/{shot['id']}/takes").json()
        # 2 ターン目に入ってから止める（1 本目の job_id を覚え終わっている）
        if takes and calls["n"] >= 2:
            take = takes[0]
            break
        time.sleep(0.05)
    assert take is not None, "studio_render_shot が Take を作らなかった"
    wait_for(env.client, take["job_id"], statuses=("queued", "prompting", "running"))

    env.client.post(f"/api/canvas/projects/{project['id']}/agent/stop")
    wait_idle(env, project["id"])
    job = wait_for(env.client, take["job_id"], statuses=("canceled",))
    assert job["status"] == "canceled"
    assert event_of(env, project["id"], "stopped")["content"] == "実行を止めました。"


def test_stop_during_a_turn_does_not_apply_the_action(env, monkeypatch):
    """ターン中に ⏹ したら、返ってきたアクションは実行しない。"""
    project = make_project(env)

    async def stopping_exec(argv, cwd, timeout):
        canvas_agent.request_stop(project["id"])
        return (
            0,
            action_answer(
                {
                    "action": "canvas_place_card",
                    "project_id": project["id"],
                    "kind": "character",
                    "title": "残ってはいけない",
                    "x": 10,
                    "y": 10,
                }
            ),
            "",
        )

    monkeypatch.setattr(grok, "_exec", stopping_exec)
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/agent", json={"content": "お願い"}
    )
    assert response.status_code == 202, response.text
    wait_idle(env, project["id"])
    assert event_of(env, project["id"], "stopped")["content"] == "実行を止めました。"
    assert board(env, project["id"])["cards"] == []
    assert "canvas_card_placed" not in kinds(env, project["id"])


def test_done_ends_the_run(env):
    project = make_project(env)
    env.cli.answers = [action_answer({"action": "done", "summary": "置きました"})]
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/agent", json={"content": "お願い"}
    )
    assert response.status_code == 202
    wait_idle(env, project["id"])
    assert event_of(env, project["id"], "done")["content"] == "置きました"
    assert env.cli.answers == []  # 追加のターンは回っていない


def test_the_run_stops_at_the_turn_limit(env, monkeypatch):
    tweak_settings(monkeypatch, canvas_max_turns=2)
    project = make_project(env)
    listing = action_answer(
        {"action": "canvas_list_cards", "project_id": project["id"]}
    )
    ask(env, project["id"], [listing, listing])
    assert "turn_limit" in kinds(env, project["id"])
    assert "連続 2 ターン" in event_of(env, project["id"], "turn_limit")["content"]


def test_the_turn_limit_can_be_switched_off(env, monkeypatch):
    """canvas_max_turns=0 は無制限: 既定の 8 ターンを超えても区切られない。"""
    tweak_settings(monkeypatch, canvas_max_turns=0)
    assert canvas_agent.turn_limit() == 0
    project = make_project(env)
    listing = action_answer(
        {"action": "canvas_list_cards", "project_id": project["id"]}
    )
    ask(
        env,
        project["id"],
        [*[listing] * 12, action_answer({"action": "done", "summary": "見ました"})],
    )
    assert "turn_limit" not in kinds(env, project["id"])
    assert event_of(env, project["id"], "done")["content"] == "見ました"


def test_the_turn_limit_defaults_to_eight(env):
    assert canvas_agent.turn_limit() == canvas_agent.MAX_TURNS == 8


def test_a_broken_action_is_retried_once_then_reported(env):
    project = make_project(env)
    ask(
        env,
        project["id"],
        [
            action_answer({"action": "canvas_move_card", "card_id": "c1"}),
            action_answer({"action": "canvas_move_card", "card_id": "c1"}),
        ],
    )
    assert "action_invalid" in kinds(env, project["id"])


def test_an_empty_message_is_refused(env):
    project = make_project(env)
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/agent", json={"content": "  "}
    )
    assert response.status_code == 422


def test_an_unknown_project_is_404(env):
    response = env.client.post(
        "/api/canvas/projects/nope/agent", json={"content": "お願い"}
    )
    assert response.status_code == 404
    assert env.client.get("/api/canvas/projects/nope/agent").status_code == 404


def test_the_plain_message_endpoint_does_not_run_the_agent(env):
    """発言を残すだけの口は据え置き（返事を作るのは /agent の仕事）。"""
    project = make_project(env)
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/messages", json={"content": "ひとりごと"}
    )
    assert response.status_code == 201
    assert not canvas_agent.is_running(project["id"])
    assert [m["role"] for m in messages(env, project["id"])] == ["user"]


# --------------------------------------------------------------------------
# 添付ファイル
# --------------------------------------------------------------------------
#
# 置き場はキャンバスの作業ディレクトリの `attachments/`。grok CLI はそこを根に
# 動くので、絶対パスを本文に書いておけばそのまま開ける（エージェントモードの
# 添付と同じ流儀）。

def upload_attachment(env, project_id: str, name: str = "ref.png") -> dict:
    response = env.client.post(
        f"/api/canvas/projects/{project_id}/attachments",
        files={"file": (name, b"DATA", "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_an_attachment_lands_in_the_canvas_workdir(env):
    project = make_project(env)
    uploaded = upload_attachment(env, project["id"], "アキ 立ち絵.png")
    assert uploaded["name"] == "アキ 立ち絵.png"
    assert uploaded["path"].startswith("attachments/")
    assert uploaded["kind"] == "image"
    # 実体はセッションの作業ディレクトリの下（エージェントが開ける場所）
    assert "/agent-sessions/" in uploaded["abs_path"]
    assert Path(uploaded["abs_path"]).is_file()
    # 画面のサムネイル用にそのまま読める
    served = env.client.get(
        f"/api/canvas/projects/{project['id']}/attachments/{uploaded['path']}"
    )
    assert served.status_code == 200
    assert served.content == b"DATA"


def test_an_unsupported_attachment_is_refused(env):
    project = make_project(env)
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/attachments",
        files={"file": ("model.blend", b"DATA", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_the_agent_is_told_where_the_attachment_is(env):
    """エージェントには絶対パス・種別・元のファイル名が渡る。"""
    project = make_project(env)
    uploaded = upload_attachment(env, project["id"], "koe.wav")
    env.cli.answers = ["見ました。"]
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/agent",
        json={"content": "この声で", "attachments": [uploaded["path"]]},
    )
    assert response.status_code == 202, response.text
    wait_idle(env, project["id"])

    prompt = env.cli.prompts[-1]
    assert uploaded["abs_path"] in prompt
    assert Path(uploaded["abs_path"]).name in prompt
    assert "audio" in prompt
    # 画面には元の本文と添付だけを出す（data に控えてある）
    said = messages(env, project["id"])[0]
    assert said["data"]["text"] == "この声で"
    assert said["data"]["attachments"][0]["kind"] == "audio"
    assert said["data"]["attachments"][0]["path"] == uploaded["path"]


def test_an_attachment_alone_can_be_sent(env):
    project = make_project(env)
    uploaded = upload_attachment(env, project["id"])
    env.cli.answers = ["見ました。"]
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/agent",
        json={"content": "", "attachments": [uploaded["path"]]},
    )
    assert response.status_code == 202, response.text
    wait_idle(env, project["id"])
    assert uploaded["abs_path"] in env.cli.prompts[-1]


def test_an_attachment_outside_the_workdir_is_refused(env):
    project = make_project(env)
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/agent",
        json={"content": "見て", "attachments": ["../../etc/passwd"]},
    )
    assert response.status_code == 400


def test_the_agent_can_register_an_attachment_as_an_asset(env):
    """添付をそのまま素材にできる（`@名前` で参照に添付されるようになる）。"""
    project = make_project(env)
    uploaded = upload_attachment(env, project["id"], "aki.png")
    ask(
        env,
        project["id"],
        script(
            {
                "action": "studio_upsert_asset",
                "project_id": project["id"],
                "name": "アキ",
                "category": "character",
                "kind": "image",
                "path": uploaded["abs_path"],
            }
        ),
        said="この画像をアキとして登録して",
    )
    assets = env.client.get(f"/api/studio/projects/{project['id']}").json()["assets"]
    assert [row["name"] for row in assets] == ["アキ"]
    # 実体は作品の持ち物（assets/）へ複製される
    assert assets[0]["url"].startswith("/assets/image/")
    assert assets[0]["path"] != uploaded["abs_path"]
    assert Path(assets[0]["path"]).is_file()


def test_the_asset_references_reach_the_agent(env):
    """素材の声・動画リファレンスは、パスつきでプロンプトに出る。"""
    project = make_project(env)
    asset = make_asset(env, project["id"])
    added = env.client.post(
        f"/api/studio/assets/{asset['id']}/files",
        files={"file": ("koe.wav", b"DATA", "audio/wav")},
        data={"role": "voice", "caption": "落ち着いた声"},
    )
    assert added.status_code == 201, added.text

    env.cli.answers = ["はい。"]
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/agent", json={"content": "声を教えて"}
    )
    assert response.status_code == 202, response.text
    wait_idle(env, project["id"])
    prompt = env.cli.prompts[-1]
    assert added.json()["path"] in prompt
    assert "落ち着いた声" in prompt


# --------------------------------------------------------------------------
# セッション継続・検索・移行
# --------------------------------------------------------------------------

def test_a_new_session_starts_empty(env):
    project = make_project(env)
    ask(env, project["id"], ["はい。"], said="最初の会話")
    created = env.client.post(f"/api/canvas/projects/{project['id']}/sessions")
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    board = env.client.get(
        f"/api/canvas/projects/{project['id']}", params={"session_id": sid}
    ).json()
    assert board["messages"] == []
    assert board["session_id"] == sid


def test_search_finds_another_sessions_message(env):
    project = make_project(env)
    first = env.client.post(
        f"/api/canvas/projects/{project['id']}/messages",
        json={"content": "ラーメンの湯気について"},
    ).json()
    env.client.post(f"/api/canvas/projects/{project['id']}/sessions", json={"title": "別"})
    env.client.post(
        f"/api/canvas/projects/{project['id']}/messages",
        json={"content": "別の話"},
        params={"session_id": env.client.get(
            f"/api/canvas/projects/{project['id']}/sessions"
        ).json()[0]["id"]},
    )
    hits = env.client.get(
        f"/api/canvas/projects/{project['id']}/sessions/search",
        params={"q": "湯気"},
    ).json()
    assert any(first["session_id"] == hit["session_id"] for hit in hits)
    assert any("湯気" in hit["snippet"] or "湯気" in hit["title"] for hit in hits)


def test_existing_messages_move_to_a_default_session(env):
    import sqlite3

    from app import db

    project = make_project(env)
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "INSERT INTO canvas_messages"
        " (id, project_id, ts, role, content, kind, data)"
        " VALUES (?, ?, ?, 'user', ?, NULL, '{}')",
        ("orphan1", project["id"], "2026-01-01T00:00:00+00:00", "昔の発言"),
    )
    conn.commit()
    conn.close()
    import asyncio

    asyncio.get_event_loop().run_until_complete(db.init_db())
    messages = env.client.get(f"/api/canvas/projects/{project['id']}/messages").json()
    moved = [m for m in messages if m["content"] == "昔の発言"]
    assert moved
    assert moved[0]["session_id"]


def test_init_db_upgrades_legacy_canvas_messages_without_session_id():
    """既存 DB の canvas_messages に session_id が無くても起動できる。"""
    import asyncio
    import sqlite3

    from app import db

    conn = sqlite3.connect(db.DB_PATH)
    conn.executescript(
        """
        CREATE TABLE canvas_messages (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          ts TEXT NOT NULL,
          role TEXT NOT NULL,
          content TEXT NOT NULL,
          kind TEXT,
          data TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    conn.commit()
    conn.close()
    asyncio.get_event_loop().run_until_complete(db.init_db())
    conn = sqlite3.connect(db.DB_PATH)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(canvas_messages)")}
    conn.close()
    assert "session_id" in columns


def test_the_agent_can_search_other_canvas_sessions(env):
    project = make_project(env)
    env.client.post(
        f"/api/canvas/projects/{project['id']}/messages",
        json={"content": "深夜のスープの塩加減"},
    )
    other = env.client.post(
        f"/api/canvas/projects/{project['id']}/sessions", json={"title": "別件"}
    ).json()
    ask(
        env,
        project["id"],
        script(
            {
                "action": "canvas_search_sessions",
                "project_id": project["id"],
                "q": "スープ",
            }
        ),
        said="前の会話を探して",
        episode_id=None,
    )
    # 最新セッション（別件）で走らせるには session_id が要る。上の ask は最新
    # （別件）に付くので、検索は他セッションの「スープ」を当てる。
    event = event_of(env, project["id"], "canvas_search_result")
    assert "スープ" in event["content"]
    assert other["id"] not in event["content"] or "スープ" in event["content"]
    assert "canvas_read_session" in event["content"]


def test_the_agent_can_read_another_canvas_session(env):
    project = make_project(env)
    first = env.client.post(
        f"/api/canvas/projects/{project['id']}/messages",
        json={"content": "塩は小さじ2"},
    ).json()
    env.client.post(
        f"/api/canvas/projects/{project['id']}/sessions", json={"title": "別件"}
    )
    ask(
        env,
        project["id"],
        script(
            {
                "action": "canvas_read_session",
                "session_id": first["session_id"],
            }
        ),
        said="前のセッションの塩加減を読んで",
        episode_id=None,
    )
    event = event_of(env, project["id"], "canvas_session_transcript")
    assert "塩は小さじ2" in event["content"]
    assert first["session_id"] in event["content"]
    assert "### USER" in event["content"]


def test_the_agent_cannot_read_its_own_canvas_session(env):
    project = make_project(env)
    mine = env.client.post(
        f"/api/canvas/projects/{project['id']}/sessions", json={"title": "今"}
    ).json()
    ask(
        env,
        project["id"],
        script({"action": "canvas_read_session", "session_id": mine["id"]}),
        said="この会話を読んで",
        episode_id=None,
    )
    event = event_of(env, project["id"], "canvas_session_transcript")
    assert "今の会話自身は読めない" in event["content"]


def test_the_agent_cannot_read_a_canvas_session_of_another_project(env):
    project = make_project(env)
    other = make_project(env, name="別作品")
    foreign = env.client.post(
        f"/api/canvas/projects/{other['id']}/messages",
        json={"content": "秘密のレシピ"},
    ).json()
    ask(
        env,
        project["id"],
        script(
            {
                "action": "canvas_read_session",
                "session_id": foreign["session_id"],
            }
        ),
        said="向こうの会話を読んで",
        episode_id=None,
    )
    event = event_of(env, project["id"], "canvas_session_transcript")
    assert "この作品のものではない" in event["content"]
    assert "秘密のレシピ" not in event["content"]
