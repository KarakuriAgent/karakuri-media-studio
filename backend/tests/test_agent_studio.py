"""エージェントからのドラマスタジオ操作（AGENT-MODE §4 / app.studio）。

Grok と ComfyUI は test_agent.py と同じ仕掛けで完全にモックする。ここで見るのは
「アクションがどう解釈され、スタジオのサービス層に何が起きたか」まで。
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from itertools import count

import pytest

from app import agent_protocol, db, studio

# test_agent.py の env フィクスチャ（Grok / ComfyUI のモック一式）をそのまま使う。
from test_agent import (  # noqa: F401 - フィクスチャの再エクスポート
    action_answer,
    env,
    event_of,
    kinds,
    plan_answer,
    sample_video,
    say,
    start,
    wait_status,
)


# --------------------------------------------------------------------------
# ヘルパー
# --------------------------------------------------------------------------

def act(env, session_id: str, payload: dict, said: str = "お願い") -> dict:
    """1 セッションの中でアクションを 1 つ走らせる（続きの操作ができる）。"""
    env.cli.answers = [action_answer(payload, "やります。")]
    response = say(env, session_id, said)
    assert response.status_code == 200, response.text
    return response.json()


def studio_action(env, payload: dict) -> dict:
    """使い捨てセッションでアクションを 1 つ走らせる。"""
    return act(env, start(env)["id"], payload)


def make_project(env, **overrides) -> dict:
    body = {"name": "深夜のラーメン屋", "auto_translate": False}
    body.update(overrides)
    created = env.client.post("/api/studio/projects", json=body)
    assert created.status_code == 201, created.text
    return created.json()


def make_shot(env, project_id: str, **overrides) -> dict:
    body = {"title": "Shot 1", "prompt": "A cat walks in.", "duration_seconds": 5}
    body.update(overrides)
    created = env.client.post(
        f"/api/studio/projects/{project_id}/shots", json=body
    )
    assert created.status_code == 201, created.text
    return created.json()


def revisions(env, project_id: str) -> list[dict]:
    return env.client.get(f"/api/studio/projects/{project_id}/revisions").json()


def finished_job_id(env) -> tuple[dict, str]:
    """1 本だけ生成を回して ``(session, job_id)`` を返す（成果物の登録用）。"""
    session = start(env)
    env.cli.answers = [
        plan_answer(env, 1),
        action_answer({"action": "done", "summary": "1本できました"}, "完了です。"),
    ]
    say(env, session["id"])
    env.client.post(f"/api/agent/sessions/{session['id']}/approve", json={})
    done = wait_status(env, session["id"], ("done", "stopped", "idle"))
    return session, done["plan"]["tasks"][0]["job_id"]


# --------------------------------------------------------------------------
# パース（AGENT-MODE §4）
# --------------------------------------------------------------------------

def test_studio_actions_are_part_of_the_protocol(env):
    for name in agent_protocol.STUDIO_ACTIONS:
        assert name in agent_protocol.ACTION_NAMES


def test_parse_create_project(env):
    action = agent_protocol.parse_action(
        action_answer(
            {
                "action": "studio_create_project",
                "name": "深夜のラーメン屋",
                "synopsis": "閉店間際の 3 分間",
                "auto_translate": False,
            }
        )
    )
    assert action is not None
    assert action.action == "studio_create_project"
    assert action.studio["body"] == {
        "name": "深夜のラーメン屋",
        "synopsis": "閉店間際の 3 分間",
        "auto_translate": False,
    }


def test_parse_upsert_shot_takes_the_fields_in_a_nested_object(env):
    """Shot の `action`（何が起きるか）はアクション名とぶつかるので入れ子で渡す。"""
    action = agent_protocol.parse_action(
        action_answer(
            {
                "action": "studio_upsert_shot",
                "project_id": "p1",
                "shot": {
                    "title": "湯気",
                    "action": "The chef lifts the noodles.",
                    "dialogue": "「あがったよ」",
                    "duration_seconds": 6,
                    "carry_over_end_frame": True,
                },
            }
        )
    )
    assert action is not None
    assert action.studio["project_id"] == "p1"
    assert action.studio["id"] is None
    assert action.studio["body"]["action"] == "The chef lifts the noodles."
    assert action.studio["body"]["carry_over_end_frame"] is True


def test_parse_upsert_shot_keeps_an_explicit_null(env):
    """更新では「送らなかった」と「null を送った」を取り違えない。"""
    action = agent_protocol.parse_action(
        action_answer(
            {
                "action": "studio_upsert_shot",
                "id": "s1",
                "shot": {"scene_id": None, "title": "湯気"},
            }
        )
    )
    assert action is not None
    assert action.studio["id"] == "s1"
    assert action.studio["body"] == {"scene_id": None, "title": "湯気"}


@pytest.mark.parametrize(
    "payload, needle",
    [
        ({"action": "studio_get_project"}, "project_id"),
        ({"action": "studio_update_project"}, "project_id"),
        ({"action": "studio_upsert_episode"}, "project_id"),
        ({"action": "studio_upsert_scene"}, "episode_id"),
        ({"action": "studio_upsert_shot"}, "project_id"),
        ({"action": "studio_delete_shot"}, "id"),
        ({"action": "studio_render_shot"}, "shot_id"),
        ({"action": "studio_get_takes"}, "shot_id"),
        ({"action": "studio_select_take"}, "take_id"),
        ({"action": "studio_reject_take"}, "take_id"),
        ({"action": "studio_register_asset_from_job", "job_id": "j"}, "project_id"),
    ],
)
def test_a_studio_action_without_its_target_is_rejected(env, payload, needle):
    with pytest.raises(agent_protocol.ActionError, match=needle):
        agent_protocol.parse_action(action_answer(payload))


def test_an_unknown_studio_field_is_rejected(env):
    with pytest.raises(agent_protocol.ActionError, match="未知のフィールド"):
        agent_protocol.parse_action(
            action_answer(
                {"action": "studio_create_project", "name": "作品", "budget": 3}
            )
        )


def test_an_unknown_asset_category_is_rejected(env):
    with pytest.raises(agent_protocol.ActionError, match="category"):
        agent_protocol.parse_action(
            action_answer(
                {
                    "action": "studio_upsert_asset",
                    "project_id": "p1",
                    "name": "アキ",
                    "category": "villain",
                }
            )
        )


def test_an_unknown_workflow_override_is_rejected(env):
    with pytest.raises(agent_protocol.ActionError, match="workflow_override"):
        agent_protocol.parse_action(
            action_answer(
                {
                    "action": "studio_render_shot",
                    "shot_id": "s1",
                    "workflow_override": "ghost_workflow",
                }
            )
        )


def test_an_unknown_job_source_is_rejected(env):
    with pytest.raises(agent_protocol.ActionError, match="source"):
        agent_protocol.parse_action(
            action_answer(
                {
                    "action": "studio_register_asset_from_job",
                    "project_id": "p1",
                    "job_id": "j1",
                    "name": "アキ",
                    "source": "thumbnail",
                }
            )
        )


def test_studio_list_projects_needs_nothing(env):
    action = agent_protocol.parse_action(
        action_answer({"action": "studio_list_projects"})
    )
    assert action is not None and action.studio == {}


# --------------------------------------------------------------------------
# 実行（app.studio のサービス層との結合）
# --------------------------------------------------------------------------

def test_create_project_writes_a_project_with_the_agent_as_actor(env):
    reply = studio_action(
        env,
        {
            "action": "studio_create_project",
            "name": "深夜のラーメン屋",
            "synopsis": "閉店間際の 3 分間",
            "auto_translate": False,
        },
    )
    event = event_of(reply, "studio_saved")
    project_id = event["data"]["project_id"]
    assert project_id in event["content"]

    projects = env.client.get("/api/studio/projects").json()
    assert [p["name"] for p in projects] == ["深夜のラーメン屋"]
    assert projects[0]["auto_translate"] is False
    # リビジョン履歴には「エージェントがやった」と残る
    assert [r["actor"] for r in revisions(env, project_id)] == ["agent"]


def test_list_projects_reports_the_counts(env):
    project = make_project(env)
    make_shot(env, project["id"])
    reply = studio_action(env, {"action": "studio_list_projects"})
    event = event_of(reply, "studio_projects")
    assert project["id"] in event["content"]
    assert "Shot 1 件" in event["content"]
    assert event["data"]["project_ids"] == [project["id"]]


def test_list_projects_on_an_empty_studio_points_at_create(env):
    event = event_of(
        studio_action(env, {"action": "studio_list_projects"}), "studio_projects"
    )
    assert "studio_create_project" in event["content"]
    assert event["data"]["count"] == 0


def test_get_project_shows_the_whole_context(env):
    project = make_project(env)
    episode = env.client.post(
        f"/api/studio/projects/{project['id']}/episodes", json={"title": "第1話"}
    ).json()
    scene = env.client.post(
        f"/api/studio/episodes/{episode['id']}/scenes",
        json={"title": "厨房", "time_of_day": "閉店後"},
    ).json()
    env.client.post(
        f"/api/studio/projects/{project['id']}/assets",
        data={
            "name": "アキ",
            "kind": "image",
            "category": "character",
            "prompt_caption": "a tired cook in her forties",
        },
    )
    shot = make_shot(env, project["id"], scene_id=scene["id"], title="湯気")

    event = event_of(
        studio_action(
            env, {"action": "studio_get_project", "project_id": project["id"]}
        ),
        "studio_project",
    )
    text = event["content"]
    for shown in (project["id"], episode["id"], scene["id"], shot["id"], "@アキ"):
        assert shown in text
    # ファイルの無い素材は「説明文に展開される」と分かるように出す
    assert "メタデータのみ" in text
    assert "a tired cook in her forties" in text


def test_get_project_reports_a_missing_project(env):
    reply = studio_action(
        env, {"action": "studio_get_project", "project_id": "ghost"}
    )
    assert "action_failed" in kinds(reply["session"])


def test_upsert_episode_and_scene_build_the_outline(env):
    project = make_project(env)
    session = start(env)["id"]

    episode_event = event_of(
        act(
            env,
            session,
            {
                "action": "studio_upsert_episode",
                "project_id": project["id"],
                "title": "第1話 閉店後",
            },
        ),
        "studio_saved",
    )
    episode_id = episode_event["data"]["episode_id"]

    scene_event = event_of(
        act(
            env,
            session,
            {
                "action": "studio_upsert_scene",
                "episode_id": episode_id,
                "title": "厨房",
                "time_of_day": "深夜1時",
            },
        ),
        "studio_saved",
    )
    detail = env.client.get(f"/api/studio/projects/{project['id']}").json()
    assert [e["title"] for e in detail["episodes"]] == ["第1話 閉店後"]
    assert [s["title"] for s in detail["scenes"]] == ["厨房"]
    assert scene_event["data"]["scene_id"] == detail["scenes"][0]["id"]

    # id を付けて送れば同じ話を書き換える（作り直さない）
    act(
        env,
        session,
        {"action": "studio_upsert_episode", "id": episode_id, "title": "第1話 改"},
    )
    detail = env.client.get(f"/api/studio/projects/{project['id']}").json()
    assert [e["title"] for e in detail["episodes"]] == ["第1話 改"]


def test_upsert_shot_creates_then_updates(env):
    project = make_project(env)
    session = start(env)["id"]
    created = event_of(
        act(
            env,
            session,
            {
                "action": "studio_upsert_shot",
                "project_id": project["id"],
                "shot": {
                    "title": "湯気",
                    "action": "The chef lifts the noodles.",
                    "dialogue": "「あがったよ」",
                    "prompt": "A cook lifts noodles from the pot.",
                    "duration_seconds": 6,
                    "carry_over_end_frame": True,
                },
            },
        ),
        "studio_saved",
    )
    shot_id = created["data"]["shot_id"]
    shot = env.client.get(f"/api/studio/projects/{project['id']}").json()["shots"][0]
    assert shot["id"] == shot_id
    assert shot["action"] == "The chef lifts the noodles."
    assert shot["duration_seconds"] == 6
    assert shot["carry_over_end_frame"] is True

    act(
        env,
        session,
        {
            "action": "studio_upsert_shot",
            "id": shot_id,
            "shot": {"status": "ready", "camera": "slow push in"},
        },
    )
    shot = env.client.get(f"/api/studio/projects/{project['id']}").json()["shots"][0]
    assert (shot["status"], shot["camera"]) == ("ready", "slow push in")
    assert shot["title"] == "湯気"  # 送らなかった項目はそのまま


def test_delete_shot_removes_it(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    reply = studio_action(env, {"action": "studio_delete_shot", "id": shot["id"]})
    assert "studio_saved" in kinds(reply["session"])
    assert env.client.get(f"/api/studio/projects/{project['id']}").json()["shots"] == []


def test_delete_of_a_missing_shot_is_reported(env):
    reply = studio_action(env, {"action": "studio_delete_shot", "id": "ghost"})
    assert "action_failed" in kinds(reply["session"])


def test_upsert_asset_registers_a_metadata_only_material(env):
    project = make_project(env)
    session = start(env)["id"]
    event = event_of(
        act(
            env,
            session,
            {
                "action": "studio_upsert_asset",
                "project_id": project["id"],
                "name": "アキ",
                "category": "character",
                "caption": "店主",
                "prompt_caption": "a tired cook in her forties",
            },
        ),
        "studio_saved",
    )
    asset_id = event["data"]["asset_id"]
    assert "@アキ" in event["content"]
    asset = env.client.get(f"/api/studio/projects/{project['id']}").json()["assets"][0]
    assert (asset["id"], asset["name"], asset["category"]) == (
        asset_id, "アキ", "character"
    )
    assert asset["path"] == ""  # ファイルは付けられない

    act(
        env,
        session,
        {
            "action": "studio_upsert_asset",
            "id": asset_id,
            "prompt_caption": "a tired cook, short hair",
        },
    )
    asset = env.client.get(f"/api/studio/projects/{project['id']}").json()["assets"][0]
    assert asset["prompt_caption"] == "a tired cook, short hair"


def test_register_asset_from_job_gives_the_material_a_real_file(env):
    """自分で生成した画像をそのまま World Bible の素材にできる。"""
    project = make_project(env)
    _, job_id = finished_job_id(env)

    event = event_of(
        studio_action(
            env,
            {
                "action": "studio_register_asset_from_job",
                "project_id": project["id"],
                "job_id": job_id,
                "name": "アキ",
                "category": "character",
                "source": "image",
            },
        ),
        "studio_saved",
    )
    asset = env.client.get(f"/api/studio/projects/{project['id']}").json()["assets"][0]
    assert asset["id"] == event["data"]["asset_id"]
    assert asset["kind"] == "image"
    assert asset["path"].startswith(str(env.assets))
    assert env.assets.joinpath("image").exists()
    # ファイルを持つ素材なので `@名前` は参照として添付される
    assert asset["url"].startswith("/assets/image/")


def test_register_asset_from_a_missing_job_is_reported(env):
    project = make_project(env)
    reply = studio_action(
        env,
        {
            "action": "studio_register_asset_from_job",
            "project_id": project["id"],
            "job_id": "ghost",
            "name": "アキ",
        },
    )
    assert "action_failed" in kinds(reply["session"])
    assert env.client.get(f"/api/studio/projects/{project['id']}").json()["assets"] == []


def test_render_shot_queues_a_take_and_reports_its_ids(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]

    event = event_of(
        act(env, session, {"action": "studio_render_shot", "shot_id": shot["id"]}),
        "studio_render_started",
    )
    take_id = event["data"]["take_id"]
    job_id = event["data"]["job_id"]
    assert take_id and job_id
    assert take_id in event["content"] and job_id in event["content"]

    takes = env.client.get(f"/api/studio/shots/{shot['id']}/takes").json()
    assert [take["id"] for take in takes] == [take_id]
    assert takes[0]["job_id"] == job_id
    row = sqlite3.connect(db.DB_PATH).execute(
        "SELECT chat_session_id FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row is not None and row[0] == session

    # 同じ内容が studio_get_takes でも読める（完了はこれで追う）
    listed = event_of(
        act(env, session, {"action": "studio_get_takes", "shot_id": shot["id"]}),
        "studio_takes",
    )
    assert listed["data"]["take_ids"] == [take_id]
    assert job_id in listed["content"]


def test_render_shot_can_force_a_workflow(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    act(
        env,
        start(env)["id"],
        {
            "action": "studio_render_shot",
            "shot_id": shot["id"],
            "workflow_override": studio.WORKFLOW_T2V,
        },
    )
    detail = env.client.get(f"/api/studio/projects/{project['id']}").json()
    assert detail["shots"][0]["workflow_override"] == studio.WORKFLOW_T2V
    assert detail["takes"][0]["shot_id"] == shot["id"]


def test_render_of_a_missing_shot_is_reported(env):
    reply = studio_action(env, {"action": "studio_render_shot", "shot_id": "ghost"})
    assert "action_failed" in kinds(reply["session"])


def test_select_and_reject_a_take(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    take_id = event_of(
        act(env, session, {"action": "studio_render_shot", "shot_id": shot["id"]}),
        "studio_render_started",
    )["data"]["take_id"]

    selected = event_of(
        act(env, session, {"action": "studio_select_take", "take_id": take_id}),
        "studio_take_selected",
    )
    assert selected["data"]["shot_id"] == shot["id"]
    detail = env.client.get(f"/api/studio/projects/{project['id']}").json()
    assert detail["shots"][0]["selected_take_id"] == take_id
    assert detail["shots"][0]["status"] == "done"

    act(env, session, {"action": "studio_reject_take", "take_id": take_id})
    detail = env.client.get(f"/api/studio/projects/{project['id']}").json()
    assert detail["shots"][0]["selected_take_id"] is None
    assert [t["status"] for t in detail["takes"]] == ["rejected"]
    # 採用も不採用もエージェントの操作として履歴に残る
    assert set(r["actor"] for r in revisions(env, project["id"])) == {"user", "agent"}


def test_select_of_a_missing_take_is_reported(env):
    reply = studio_action(env, {"action": "studio_select_take", "take_id": "ghost"})
    assert "action_failed" in kinds(reply["session"])


def test_takes_of_a_shot_without_any_point_at_render(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    event = event_of(
        studio_action(env, {"action": "studio_get_takes", "shot_id": shot["id"]}),
        "studio_takes",
    )
    assert "studio_render_shot" in event["content"]
    assert event["data"]["count"] == 0


def test_a_stale_take_says_why_in_the_project_context(env, monkeypatch):
    """脚本を直したあとの Take は「作り直したほうがよい」と伝わる。"""
    # スタジオの時計を 1 呼び出し = 1 秒進める（実時間だと Take と脚本の更新が
    # 同じ秒に収まり、前後関係が出ない。test_studio.py と同じ仕掛け）。
    base = datetime.now(timezone.utc)
    ticks = count()
    monkeypatch.setattr(
        studio,
        "_now",
        lambda: (base + timedelta(seconds=next(ticks))).isoformat(timespec="seconds"),
    )
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    act(env, session, {"action": "studio_render_shot", "shot_id": shot["id"]})
    act(
        env,
        session,
        {
            "action": "studio_upsert_shot",
            "id": shot["id"],
            "shot": {"prompt": "A dog walks in instead."},
        },
    )
    event = event_of(
        act(env, session, {"action": "studio_get_project",
                           "project_id": project["id"]}),
        "studio_project",
    )
    assert "stale" in event["content"]
    assert "脚本が更新されました" in event["content"]


def test_the_studio_actions_are_handled_inside_the_request(env):
    """スタジオ操作は即時アクション（実行ループを起こさない）。"""
    project = make_project(env)
    reply = studio_action(
        env, {"action": "studio_get_project", "project_id": project["id"]}
    )
    assert reply["session"]["status"] == "idle"


# --------------------------------------------------------------------------
# システムプロンプト
# --------------------------------------------------------------------------

def test_system_prompt_explains_the_studio(env):
    system = start(env)["messages"][0]["content"]
    assert "# DRAMA STUDIO" in system
    for shown in (
        "studio_create_project",
        "studio_upsert_shot",
        "studio_render_shot",
        "studio_select_take",
        "studio_register_asset_from_job",
        "`@名前`",
        "carry_over_end_frame",
        "auto_translate",
        "stale",
        "minimax_h3_r2v",
    ):
        assert shown in system, shown


def test_system_prompt_says_the_shot_fields_are_nested(env):
    system = start(env)["messages"][0]["content"]
    assert "nested `shot` object" in system


def test_the_action_json_of_a_studio_call_round_trips(env):
    """プロトコル通りの JSON がそのまま解釈できる（フェンス込み）。"""
    payload = {
        "action": "studio_render_shot",
        "notes": "1 カット目を回します",
        "shot_id": "s1",
    }
    text = "回します。\n\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n"
    action = agent_protocol.parse_action(text)
    assert action is not None
    assert (action.action, action.notes) == ("studio_render_shot", "1 カット目を回します")
    assert action.studio["shot_id"] == "s1"
