"""エージェントからのドラマスタジオ操作（AGENT-MODE §4 / app.studio）。

Grok と ComfyUI は test_agent.py と同じ仕掛けで完全にモックする。ここで見るのは
「アクションがどう解釈され、スタジオのサービス層に何が起きたか」まで。
"""

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from itertools import count

import pytest

from app import agent_protocol, agent_runner, canvas_agent, db, studio

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
    """1 セッションの中でアクションを 1 つ走らせる（続きの操作ができる）。

    発言は 202 で即受付され、ターンはバックグラウンドで回る（``say`` が終わりまで
    待つ）ので、結果は落ち着いたセッションを取り直して見る。
    """
    env.cli.answers = [action_answer(payload, "やります。")]
    response = say(env, session_id, said)
    assert response.status_code == 202, response.text
    return env.client.get(f"/api/agent/sessions/{session_id}").json()


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
        ({"action": "studio_translate_shot"}, "shot_id"),
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


def test_parse_translate_shot_takes_shot_id(env):
    action = agent_protocol.parse_action(
        action_answer({"action": "studio_translate_shot", "shot_id": "s1"})
    )
    assert action is not None
    assert action.action == "studio_translate_shot"
    assert action.studio["shot_id"] == "s1"


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


def test_create_project_takes_the_image_render_settings(env):
    """素材画像用の 3 項目もエージェントから作れる（動画側とは別に持つ）。"""
    reply = studio_action(
        env,
        {
            "action": "studio_create_project",
            "name": "素材の設定つき",
            "megapixels": 0.4,
            "aspect_ratio": "16:9 (Widescreen)",
            "steps": 4,
            "image_quality": "turbo",
            "image_megapixels": 1.0,
            "image_aspect_ratio": "1:1 (Square)",
            "image_steps": 8,
        },
    )
    project_id = event_of(reply, "studio_saved")["data"]["project_id"]
    saved = env.client.get(f"/api/studio/projects/{project_id}").json()
    assert saved["megapixels"] == 0.4
    assert saved["aspect_ratio"] == "16:9 (Widescreen)"
    assert saved["steps"] == 4
    assert saved["image_quality"] == "turbo"
    assert saved["image_megapixels"] == 1.0
    assert saved["image_aspect_ratio"] == "1:1 (Square)"
    assert saved["image_steps"] == 8


def test_update_project_changes_the_image_render_settings(env):
    project = make_project(env)
    studio_action(
        env,
        {
            "action": "studio_update_project",
            "project_id": project["id"],
            "image_megapixels": 0.5,
            "image_aspect_ratio": "9:16 (Portrait Widescreen)",
            "image_steps": 12,
        },
    )
    saved = env.client.get(f"/api/studio/projects/{project['id']}").json()
    assert saved["image_megapixels"] == 0.5
    assert saved["image_aspect_ratio"] == "9:16 (Portrait Widescreen)"
    assert saved["image_steps"] == 12


def test_get_project_shows_the_image_render_settings(env):
    """素材の静止画に動画用の値を流用しないよう、両方を明示して出す。"""
    project = make_project(
        env,
        image_megapixels=1.0,
        image_aspect_ratio="1:1 (Square)",
        image_steps=8,
    )
    content = event_of(
        studio_action(
            env,
            {"action": "studio_get_project", "project_id": project["id"]},
        ),
        "studio_project",
    )["content"]
    assert "素材画像の megapixels / aspect_ratio / steps" in content
    assert "`1.0` / `1:1 (Square)` / `8`" in content


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
    assert "action_failed" in kinds(reply)


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
    assert "studio_saved" in kinds(reply)
    assert env.client.get(f"/api/studio/projects/{project['id']}").json()["shots"] == []


def test_delete_of_a_missing_shot_is_reported(env):
    reply = studio_action(env, {"action": "studio_delete_shot", "id": "ghost"})
    assert "action_failed" in kinds(reply)


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
    assert "action_failed" in kinds(reply)
    assert env.client.get(f"/api/studio/projects/{project['id']}").json()["assets"] == []


def test_translate_shot_saves_the_english_prompt(env, monkeypatch):
    from app import grok
    from test_studio import FakeLLM, wait_translated

    llm = FakeLLM()
    llm.error = None
    llm.reply = "integrated_multimodal_description: A cat walks in."
    monkeypatch.setattr(grok, "get_client", lambda *a, **k: llm)

    project = make_project(env)
    shot = make_shot(env, project["id"], prompt="猫が歩いてくる。")
    event = event_of(
        studio_action(
            env, {"action": "studio_translate_shot", "shot_id": shot["id"]}
        ),
        "studio_saved",
    )
    assert event["data"]["shot_id"] == shot["id"]
    assert shot["id"] in event["content"]
    assert "開始しました" in event["content"]
    wait_translated(env, shot["id"])
    stored = env.client.get(f"/api/studio/projects/{project['id']}").json()["shots"][0]
    assert stored["english_prompt"] == llm.reply
    assert stored["prompt"] == "猫が歩いてくる。"
    assert llm.prompts


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
    assert "action_failed" in kinds(reply)


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
    assert "action_failed" in kinds(reply)


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
    assert reply["status"] == "idle"


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
        "studio_translate_shot",
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


# --------------------------------------------------------------------------
# レンダー完了の通知（ポーリングしない / app.agent_runner の Take 通知）
# --------------------------------------------------------------------------
#
# 実物のジョブを回すと「いつ終わるか」がテストの外なので、ここでは jobs 行と
# studio_takes 行を直接作り、完了フックの入口（``_on_job_final``）と安全網
# （``scan_pending_takes``）を呼んで見る。``start_loop`` は差し替えて「起こされた
# かどうか」だけを見る。

def sql(query: str, *params) -> list[tuple]:
    conn = sqlite3.connect(db.DB_PATH)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def run_async(coro):
    """TestClient のループとは別のループで agent_runner の内部を回す。

    ``_append_locks`` はセッションごとの :class:`asyncio.Lock` を使い回すので、
    別ループから触る前に捨てる（Lock はループに束縛される）。
    """
    agent_runner._append_locks.clear()
    return asyncio.run(coro)


def fake_job(session_id: str | None, status: str = "done", error=None) -> str:
    """jobs 行を 1 つ直接作る（ランナーには載せない）。"""
    job_id = f"job-{next(_fake_ids)}"
    sql(
        "INSERT INTO jobs (id, created_at, mode, status, params, workflow_json,"
        " chat_session_id, error) VALUES (?, ?, 'i2v', ?, '{}', '{}', ?, ?)",
        job_id, "2026-01-01T00:00:00+00:00", status, session_id, error,
    )
    return job_id


def fake_take(shot: dict, job_id: str) -> str:
    take_id = f"take-{next(_fake_ids)}"
    sql(
        "INSERT INTO studio_takes (id, shot_id, project_id, job_id, status,"
        " created_at) VALUES (?, ?, ?, ?, 'rendering', ?)",
        take_id, shot["id"], shot["project_id"], job_id,
        "2026-01-01T00:00:00+00:00",
    )
    return take_id


_fake_ids = count(1)


@pytest.fixture
def woken(monkeypatch) -> list[str]:
    """``start_loop`` を差し替えて、起こされたセッションを記録する。"""
    started: list[str] = []

    async def fake_start_loop(session_id, action=None, *, user_turn=False):
        started.append(session_id)

    monkeypatch.setattr(agent_runner, "start_loop", fake_start_loop)
    return started


def take_events(env, session_id: str) -> list[dict]:
    session = env.client.get(f"/api/agent/sessions/{session_id}").json()
    return [m for m in session["messages"] if m["kind"] == "studio_take_finished"]


def test_a_finished_take_wakes_an_idle_session(env, woken):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    job_id = fake_job(session)
    take_id = fake_take(shot, job_id)

    run_async(agent_runner._on_job_final(job_id, "done"))

    events = take_events(env, session)
    assert len(events) == 1
    event = events[0]
    assert event["data"]["take_id"] == take_id
    assert event["data"]["job_id"] == job_id
    assert event["data"]["shot_id"] == shot["id"]
    # ジョブが done なら Take は候補（studio 側の導出そのまま）
    assert event["data"]["take_status"] == "candidate"
    assert take_id in event["content"] and job_id in event["content"]
    assert "studio_select_take" in event["content"]
    assert woken == [session]
    # 通知済みの印が付く（次のスキャンで拾い直さない）
    assert sql(
        "SELECT agent_notified_at FROM studio_takes WHERE id = ?", take_id
    )[0][0]


def test_a_failed_take_reports_the_job_error(env, woken):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    job_id = fake_job(session, status="failed", error="ComfyUI is down")
    fake_take(shot, job_id)

    run_async(agent_runner._on_job_final(job_id, "failed"))

    event = take_events(env, session)[0]
    assert event["data"]["take_status"] == "failed"
    assert "ComfyUI is down" in event["content"]
    assert "studio_render_shot" in event["content"]  # 次の一手が本文に載る
    assert woken == [session]


def test_the_same_take_is_only_announced_once(env, woken):
    """イベントと定期スキャンのどちらが先でも 1 件しか積まれない。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    job_id = fake_job(session)
    fake_take(shot, job_id)

    run_async(agent_runner._on_job_final(job_id, "done"))
    run_async(agent_runner._on_job_final(job_id, "done"))
    assert run_async(agent_runner.scan_pending_takes()) == 0

    assert len(take_events(env, session)) == 1
    assert woken == [session]


def test_a_job_without_a_take_is_not_announced(env, woken):
    """execute_task 由来のジョブは _wait_for_job が待っている（二重に伝えない）。"""
    session = start(env)["id"]
    job_id = fake_job(session)

    run_async(agent_runner._on_job_final(job_id, "done"))

    assert take_events(env, session) == []
    assert woken == []


def test_a_take_without_an_agent_session_is_not_announced(env, woken):
    """スタジオ画面からの手動レンダー（chat_session_id なし）は対象外。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    job_id = fake_job(None)
    take_id = fake_take(shot, job_id)

    run_async(agent_runner._on_job_final(job_id, "done"))

    assert sql(
        "SELECT agent_notified_at FROM studio_takes WHERE id = ?", take_id
    )[0][0] is None


def test_a_stopped_session_is_not_woken_up(env, woken):
    """停止系のセッションはイベントを積むだけ（ユーザーの次の発言で目に入る）。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    sql("UPDATE agent_sessions SET status = 'stopped' WHERE id = ?", session)
    job_id = fake_job(session)
    fake_take(shot, job_id)

    run_async(agent_runner._on_job_final(job_id, "done"))

    assert len(take_events(env, session)) == 1
    assert woken == []


@pytest.mark.parametrize("status", ["planning", "waiting_checkin", "done"])
def test_a_session_waiting_for_the_user_is_not_woken_up(env, woken, status):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    sql("UPDATE agent_sessions SET status = ? WHERE id = ?", status, session)
    job_id = fake_job(session)
    fake_take(shot, job_id)

    run_async(agent_runner._on_job_final(job_id, "done"))

    assert len(take_events(env, session)) == 1
    assert woken == []


def test_the_scan_picks_up_a_take_nobody_announced(env, woken):
    """イベントを取りこぼしても（= プロセスが落ちていても）スキャンが拾う。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    done = fake_take(shot, fake_job(session))
    still_running = fake_take(shot, fake_job(session, status="running"))

    assert run_async(agent_runner.scan_pending_takes()) == 1

    events = take_events(env, session)
    assert [e["data"]["take_id"] for e in events] == [done]
    assert woken == [session]
    # まだ走っているジョブには触らない（ジョブ側のタイムアウトに任せる）
    assert sql(
        "SELECT agent_notified_at FROM studio_takes WHERE id = ?", still_running
    )[0][0] is None


# --------------------------------------------------------------------------
# レンダー完了の通知（キャンバスのチャット / app.canvas_agent）
# --------------------------------------------------------------------------
#
# 仕掛けは上と同じで、積まれる先が canvas_messages、起こされるのが
# project_id 単位のキャンバスループになる。ジョブの ``chat_session_id`` は
# ``canvas_sessions.id``。

def canvas_session(env, project_id: str) -> str:
    created = env.client.post(f"/api/canvas/projects/{project_id}/sessions")
    assert created.status_code == 201, created.text
    return created.json()["id"]


def canvas_take_events(env, project_id: str, session_id: str) -> list[dict]:
    response = env.client.get(
        f"/api/canvas/projects/{project_id}/messages",
        params={"session_id": session_id},
    )
    assert response.status_code == 200, response.text
    return [m for m in response.json() if m["kind"] == "studio_take_finished"]


@pytest.fixture
def canvas_woken(monkeypatch) -> list[str]:
    """``canvas_agent.start`` を差し替えて、起こされたキャンバスを記録する。"""
    started: list[str] = []

    async def fake_start(project_id, episode_id=None, session_id=None):
        started.append(project_id)

    monkeypatch.setattr(canvas_agent, "start", fake_start)
    return started


def test_the_canvas_injects_its_session_into_a_render(env, monkeypatch):
    """キャンバスからのレンダーはその会話に紐付く（でないと通知が届かない）。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session_id = canvas_session(env, project["id"])
    seen: list[dict] = []

    async def fake_run_tool(action):
        seen.append(dict(action.studio))
        return ("studio_render_started", "投入しました", {})

    monkeypatch.setattr(canvas_agent.agent_runner, "run_tool", fake_run_tool)
    monkeypatch.setitem(canvas_agent._active_session, project["id"], session_id)
    action = agent_protocol.parse_action(
        action_answer({"action": "studio_render_shot", "shot_id": shot["id"]}, "")
    )

    run_async(canvas_agent._apply(project["id"], action))

    assert len(seen) == 1
    assert seen[0]["shot_id"] == shot["id"]
    assert seen[0]["chat_session_id"] == session_id


def test_a_finished_take_wakes_the_canvas(env, canvas_woken):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session_id = canvas_session(env, project["id"])
    job_id = fake_job(session_id)
    take_id = fake_take(shot, job_id)

    run_async(canvas_agent._on_job_final(job_id, "done"))

    events = canvas_take_events(env, project["id"], session_id)
    assert len(events) == 1
    event = events[0]
    assert event["role"] == "event"
    assert event["data"]["take_id"] == take_id
    assert event["data"]["job_id"] == job_id
    assert event["data"]["shot_id"] == shot["id"]
    assert event["data"]["take_status"] == "candidate"
    assert take_id in event["content"] and job_id in event["content"]
    assert "studio_select_take" in event["content"]
    assert canvas_woken == [project["id"]]
    assert sql(
        "SELECT agent_notified_at FROM studio_takes WHERE id = ?", take_id
    )[0][0]


def test_a_failed_canvas_take_reports_the_job_error(env, canvas_woken):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session_id = canvas_session(env, project["id"])
    job_id = fake_job(session_id, status="failed", error="ComfyUI is down")
    fake_take(shot, job_id)

    run_async(canvas_agent._on_job_final(job_id, "failed"))

    event = canvas_take_events(env, project["id"], session_id)[0]
    assert event["data"]["take_status"] == "failed"
    assert "ComfyUI is down" in event["content"]
    assert "studio_render_shot" in event["content"]
    assert canvas_woken == [project["id"]]


def test_the_same_canvas_take_is_only_announced_once(env, canvas_woken):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session_id = canvas_session(env, project["id"])
    job_id = fake_job(session_id)
    fake_take(shot, job_id)

    run_async(canvas_agent._on_job_final(job_id, "done"))
    run_async(canvas_agent._on_job_final(job_id, "done"))
    assert run_async(canvas_agent.scan_pending_takes()) == 0

    assert len(canvas_take_events(env, project["id"], session_id)) == 1
    assert canvas_woken == [project["id"]]


def test_a_canceled_canvas_take_does_not_restart_the_loop(env, canvas_woken):
    """⏹ はこのランのジョブを cancel する。その完了で起こしたら止めた意味がない。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session_id = canvas_session(env, project["id"])
    job_id = fake_job(session_id, status="canceled")
    fake_take(shot, job_id)

    run_async(canvas_agent._on_job_final(job_id, "canceled"))

    assert len(canvas_take_events(env, project["id"], session_id)) == 1
    assert canvas_woken == []


def test_a_canvas_asked_to_stop_is_not_woken_up(env, canvas_woken):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session_id = canvas_session(env, project["id"])
    fake_take(shot, fake_job(session_id))
    canvas_agent._stop_requests.add(project["id"])
    try:
        run_async(canvas_agent.scan_pending_takes())
    finally:
        canvas_agent._stop_requests.discard(project["id"])

    assert len(canvas_take_events(env, project["id"], session_id)) == 1
    assert canvas_woken == []


def test_a_manual_render_is_not_announced_to_the_canvas(env, canvas_woken):
    """スタジオ画面からの手動レンダー（chat_session_id なし）は対象外。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    take_id = fake_take(shot, fake_job(None))

    assert run_async(canvas_agent.scan_pending_takes()) == 0

    assert sql(
        "SELECT agent_notified_at FROM studio_takes WHERE id = ?", take_id
    )[0][0] is None
    assert canvas_woken == []


def test_an_agent_tab_take_is_not_delivered_to_the_canvas(env, canvas_woken, woken):
    """エージェントタブ発のジョブはあちらの担当（同じ印を共用している）。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    canvas_session(env, project["id"])  # 会話はあるが、このジョブとは無関係
    session = start(env)["id"]
    fake_take(shot, fake_job(session))

    assert run_async(canvas_agent.scan_pending_takes()) == 0
    assert run_async(agent_runner.scan_pending_takes()) == 1

    assert canvas_woken == []
    assert woken == [session]


def test_the_scan_picks_up_a_canvas_take_nobody_announced(env, canvas_woken):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session_id = canvas_session(env, project["id"])
    done = fake_take(shot, fake_job(session_id))
    still_running = fake_take(shot, fake_job(session_id, status="running"))

    assert run_async(canvas_agent.scan_pending_takes()) == 1

    events = canvas_take_events(env, project["id"], session_id)
    assert [e["data"]["take_id"] for e in events] == [done]
    assert canvas_woken == [project["id"]]
    assert sql(
        "SELECT agent_notified_at FROM studio_takes WHERE id = ?", still_running
    )[0][0] is None


# --------------------------------------------------------------------------
# 停止・キャンセル・削除まわりの通知（コードレビューで見つかった退行）
# --------------------------------------------------------------------------

def test_a_canceled_agent_take_does_not_restart_the_loop(env, woken):
    """⏹ はこのセッションのジョブも cancel する。その完了で起こしたら意味がない。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    job_id = fake_job(session, status="canceled")
    fake_take(shot, job_id)

    run_async(agent_runner._on_job_final(job_id, "canceled"))

    # イベントは積む（次にユーザーが話しかければ目に入る）が、起こさない
    assert len(take_events(env, session)) == 1
    assert woken == []


def test_a_session_asked_to_stop_is_not_woken_up(env, woken):
    """⏹ の直後（`_stop_requests` に居る）は、done の完了でも起こさない。

    `start_loop` は `_stop_requests` を捨てるので、起こしてしまうと「止めた印」
    ごと消えて、止めたはずのエージェントが走り出す。
    """
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    job_id = fake_job(session)
    fake_take(shot, job_id)
    agent_runner._stop_requests.add(session)
    try:
        run_async(agent_runner._on_job_final(job_id, "done"))
    finally:
        agent_runner._stop_requests.discard(session)

    assert len(take_events(env, session)) == 1
    assert woken == []
    # 止めた印は残っている（消されていない）
    assert session not in woken


def test_a_canceled_take_tells_the_agent_not_to_rerender(env, woken):
    """キャンセルは失敗ではない: 「作り直せ」と言ってはいけない。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    job_id = fake_job(session, status="canceled")
    fake_take(shot, job_id)

    run_async(agent_runner._on_job_final(job_id, "canceled"))

    event = take_events(env, session)[0]
    assert event["data"]["job_status"] == "canceled"
    assert "停止" in event["content"]
    assert "作り直さないでください" in event["content"]
    # 失敗の文面（再レンダーの指示）が混ざっていない
    assert "失敗しました" not in event["content"]


def _breaking(module, name: str, monkeypatch):
    """``module.name`` を「1 回だけ失敗して、あとは素の実装」に差し替える。

    ``monkeypatch.undo()`` は env フィクスチャの差し替え（DB_PATH など）まで
    戻してしまうので使わない。
    """
    original = getattr(module, name)
    fail = [True]

    async def wrapped(*args, **kwargs):
        if fail[0]:
            fail[0] = False
            raise RuntimeError("disk is on fire")
        return await original(*args, **kwargs)

    monkeypatch.setattr(module, name, wrapped)
    return fail


def test_a_failed_delivery_leaves_the_take_for_the_next_scan(env, woken, monkeypatch):
    """配達に失敗したら印を戻す（印だけ残ると永久に通知されない）。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    job_id = fake_job(session)
    take_id = fake_take(shot, job_id)
    _breaking(agent_runner, "_event", monkeypatch)

    with pytest.raises(RuntimeError):
        run_async(agent_runner._on_job_final(job_id, "done"))
    assert sql(
        "SELECT agent_notified_at FROM studio_takes WHERE id = ?", take_id
    )[0][0] is None

    # 印が戻っているので、次のスキャンがちゃんと拾い直す
    assert run_async(agent_runner.scan_pending_takes()) == 1
    assert len(take_events(env, session)) == 1


def test_a_failed_canvas_delivery_leaves_the_take_for_the_next_scan(
    env, canvas_woken, monkeypatch
):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    session_id = canvas_session(env, project["id"])
    job_id = fake_job(session_id)
    take_id = fake_take(shot, job_id)
    _breaking(canvas_agent, "append", monkeypatch)

    with pytest.raises(RuntimeError):
        run_async(canvas_agent._on_job_final(job_id, "done"))
    assert sql(
        "SELECT agent_notified_at FROM studio_takes WHERE id = ?", take_id
    )[0][0] is None

    assert run_async(canvas_agent.scan_pending_takes()) == 1
    assert len(canvas_take_events(env, project["id"], session_id)) == 1


def test_deleting_a_job_announces_its_pending_take(env, woken):
    """ジョブを消すと宛先（chat_session_id）が辿れなくなるので、消す前に伝える。"""
    from app import jobs as jobs_module

    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    job_id = fake_job(session, status="running")
    take_id = fake_take(shot, job_id)

    # 走っている最中でも、削除は「結果が分からなくなる」ので伝える
    assert run_async(jobs_module.delete_job(job_id)) is True

    events = take_events(env, session)
    assert len(events) == 1
    assert events[0]["data"]["take_id"] == take_id
    assert "削除" in events[0]["content"]
    assert sql(
        "SELECT agent_notified_at FROM studio_takes WHERE id = ?", take_id
    )[0][0]
    # ジョブが消えたあとのスキャンは、この Take をもう見ない
    assert run_async(agent_runner.scan_pending_takes()) == 0


def test_deleting_a_job_announces_its_pending_canvas_take(env, canvas_woken):
    from app import jobs as jobs_module

    project = make_project(env)
    shot = make_shot(env, project["id"])
    session_id = canvas_session(env, project["id"])
    job_id = fake_job(session_id, status="running")
    take_id = fake_take(shot, job_id)

    assert run_async(jobs_module.delete_job(job_id)) is True

    events = canvas_take_events(env, project["id"], session_id)
    assert len(events) == 1
    assert events[0]["data"]["take_id"] == take_id
    assert "削除" in events[0]["content"]
    assert sql(
        "SELECT agent_notified_at FROM studio_takes WHERE id = ?", take_id
    )[0][0]


def test_deleting_a_job_whose_take_was_already_announced_is_quiet(env, woken):
    """既に伝えてある Take は、削除でもう一度伝えない。"""
    from app import jobs as jobs_module

    project = make_project(env)
    shot = make_shot(env, project["id"])
    session = start(env)["id"]
    job_id = fake_job(session)
    fake_take(shot, job_id)

    run_async(agent_runner._on_job_final(job_id, "done"))
    assert len(take_events(env, session)) == 1

    run_async(jobs_module.delete_job(job_id))
    assert len(take_events(env, session)) == 1
