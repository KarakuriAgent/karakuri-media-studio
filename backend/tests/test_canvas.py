"""Canvas Studio のテスト（P1: CRUD とスキーマ検証 / P2: エージェント接続 /
P3: 生成接続）。

キャンバスは独立サブシステムなので、ComfyUI は一切使わない。LLM は
:class:`FakeCli`（``grok._exec`` 差し替え、test_agent.py からのコピー）で
台本どおりに答えさせ、DB と作業ディレクトリは tmp_path に閉じ込める。ジョブ投入
（``jobs.create_job`` / ``jobs.get_job``）も monkeypatch で偽物に差し替える。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import config, db, grok, jobs, workflows
from app.canvas import generate as canvas_generate
from app.canvas import protocol as canvas_protocol
from app.canvas import runner as canvas_runner
from app.canvas import store as canvas_store
from app.canvas.models import CanvasNode, validate_node_data
from app.canvas.protocol import CanvasAction
from app.main import app
from app.routers import canvas as canvas_router
from app.models import DEFAULT_NEGATIVE_PROMPT, Job, Settings


class FakeCli:
    """Replays scripted `grok -p` answers (test_agent.py と同じ方式)."""

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.calls: list[list[str]] = []
        self.cwds: list[str] = []

    async def __call__(self, argv, cwd, timeout):
        self.calls.append(list(argv))
        self.cwds.append(str(cwd))
        if not self.answers:
            raise AssertionError("fake grok CLI ran out of scripted answers")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return (0, answer, "")

    @property
    def prompts(self) -> list[str]:
        return [argv[argv.index("-p") + 1] for argv in self.calls if "-p" in argv]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """DB と canvas の作業ディレクトリをテスト用に差し替えたクライアント。"""
    projects = tmp_path / "canvas-projects"
    projects.mkdir()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(canvas_store, "CANVAS_PROJECTS_DIR", projects)
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(
            grok_command="grok",
            grok_model="grok-4.5",
            grok_workdir=str(tmp_path),
            # ACP はプロセスを本当に起動するので、FakeCli の通るワンショット
            # 実行に固定する（ACP 自体は test_acp.py で検証）。
            agent_use_acp=False,
        ),
    )
    cli = FakeCli()
    monkeypatch.setattr(grok, "_exec", cli)
    canvas_runner.reset()

    with TestClient(app) as client:
        yield type(
            "Env",
            (),
            {"client": client, "cli": cli, "projects": projects, "tmp": tmp_path},
        )
    canvas_runner.reset()


def fenced(payload: dict) -> str:
    """アクション 1 つを ```json フェンスに載せた応答。"""
    return "はい。\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def send(env, project_id: str, content: str):
    return env.client.post(
        f"/api/canvas/projects/{project_id}/messages", json={"content": content}
    )


def new_project(env, title: str = "PV 企画") -> dict:
    response = env.client.post("/api/canvas/projects", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()


def add_node(env, project_id: str, **payload) -> dict:
    body = {"kind": "text", **payload}
    return env.client.post(f"/api/canvas/projects/{project_id}/nodes", json=body)


# --------------------------------------------------------------------------
# 1. プロジェクトのライフサイクル
# --------------------------------------------------------------------------

def test_project_lifecycle(env):
    project = new_project(env)
    assert project["title"] == "PV 企画"
    assert project["viewport"] == {"x": 0.0, "y": 0.0, "zoom": 1.0}
    # 作業ディレクトリは作成時に用意される
    assert (env.projects / project["id"]).is_dir()

    listed = env.client.get("/api/canvas/projects").json()
    assert [item["id"] for item in listed] == [project["id"]]

    detail = env.client.get(f"/api/canvas/projects/{project['id']}").json()
    assert detail["nodes"] == [] and detail["messages"] == []
    assert detail["thinking"] is False

    renamed = env.client.patch(
        f"/api/canvas/projects/{project['id']}", json={"title": "PV 企画 v2"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "PV 企画 v2"

    moved = env.client.patch(
        f"/api/canvas/projects/{project['id']}",
        json={"viewport": {"x": -120, "y": 40, "zoom": 0.8}},
    ).json()
    assert moved["viewport"] == {"x": -120.0, "y": 40.0, "zoom": 0.8}
    # viewport だけの更新でタイトルは消えない
    assert moved["title"] == "PV 企画 v2"

    node = add_node(env, project["id"], data={"body": "メモ"}).json()
    assert env.client.delete(f"/api/canvas/projects/{project['id']}").status_code == 204
    assert env.client.get(f"/api/canvas/projects/{project['id']}").status_code == 404
    # カードも一緒に消えている（親が無いので操作は 404）
    assert (
        env.client.patch(
            f"/api/canvas/projects/{project['id']}/nodes/{node['id']}", json={"x": 1}
        ).status_code
        == 404
    )
    assert env.client.delete(f"/api/canvas/projects/{project['id']}").status_code == 404
    assert not (env.projects / project["id"]).exists()


@pytest.mark.asyncio
async def test_delete_project_removes_rows(env):
    """削除したプロジェクトの nodes / messages が DB に残らない。"""
    project = new_project(env)
    add_node(env, project["id"], data={"body": "メモ"})
    await canvas_store.append_message(project["id"], "user", "こんにちは")

    assert len(await canvas_store.list_nodes(project["id"])) == 1
    assert len(await canvas_store.list_messages(project["id"])) == 1

    assert env.client.delete(f"/api/canvas/projects/{project['id']}").status_code == 204
    assert await canvas_store.list_nodes(project["id"]) == []
    assert await canvas_store.list_messages(project["id"]) == []


def test_messages_endpoint(env):
    project = new_project(env)
    response = env.client.get(f"/api/canvas/projects/{project['id']}/messages")
    assert response.status_code == 200
    assert response.json() == []
    assert env.client.get("/api/canvas/projects/nope/messages").status_code == 404


# --------------------------------------------------------------------------
# 2. 全 9 kind のカード作成と data の正規化
# --------------------------------------------------------------------------

ALL_KINDS = [
    ("style", {"description": "水彩っぽい"}),
    ("character", {"appearance": "silver hair, blue eyes"}),
    ("location", {"mood": "夕暮れ"}),
    ("object", {"description": "赤い傘"}),
    ("script", {"scenes": [{"no": 1, "heading": "屋上・夕", "body": "ト書き"}]}),
    ("storyboard", {"cuts": [{"no": 1, "scene": "屋上", "prompt": "a rooftop"}]}),
    ("media", {"media_type": "video", "url": "/outputs/a.mp4"}),
    ("text", {"body": "メモ"}),
    ("model", {"target": "image", "workflow": "krea2_turbo"}),
]


@pytest.mark.parametrize("kind,data", ALL_KINDS)
def test_create_node_all_kinds(env, kind, data):
    project = new_project(env)
    response = add_node(env, project["id"], kind=kind, title=kind, data=data)
    assert response.status_code == 201, response.text
    node = response.json()
    assert node["kind"] == kind and node["title"] == kind
    assert node["project_id"] == project["id"]
    # 省略したフィールドはスキーマの既定値で埋まって返る（送った項目より多い）
    assert set(node["data"]) >= set(data)
    assert node["w"] == 320.0 and node["h"] == 220.0


def test_node_data_defaults_are_filled(env):
    project = new_project(env)
    node = add_node(
        env, project["id"], kind="character", data={"appearance": "silver hair"}
    ).json()
    assert node["data"] == {
        "description": "",
        "appearance": "silver hair",
        "personality": "",
        "voice": "",
        "images": [],
        "notes": "",
    }
    # ネストしたスキーマ（script）も既定値で埋まる
    script = add_node(
        env, project["id"], kind="script", data={"scenes": [{"heading": "屋上"}]}
    ).json()
    assert script["data"]["scenes"] == [{"no": 0, "heading": "屋上", "body": ""}]
    # model カードの params も丸ごと正規化される
    model = add_node(
        env, project["id"], kind="model", data={"workflow": "krea2_turbo"}
    ).json()
    assert model["data"]["target"] == "image"
    assert model["data"]["params"]["fps"] == 25
    assert model["data"]["params"]["loras"] == []


# --------------------------------------------------------------------------
# 3. 不正な kind / data
# --------------------------------------------------------------------------

def test_unknown_kind_is_422(env):
    project = new_project(env)
    assert add_node(env, project["id"], kind="unknown").status_code == 422


def test_extra_field_is_422(env):
    project = new_project(env)
    assert (
        add_node(env, project["id"], kind="text", data={"foo": 1}).status_code == 422
    )
    assert (
        add_node(
            env, project["id"], kind="character", data={"appearance": 1, "foo": "x"}
        ).status_code
        == 422
    )


def test_node_on_missing_project_is_404(env):
    assert add_node(env, "nope", kind="text").status_code == 404


# --------------------------------------------------------------------------
# 4. model カードのワークフロー検証
# --------------------------------------------------------------------------

def test_model_card_unknown_workflow_is_422(env):
    project = new_project(env)
    response = add_node(
        env,
        project["id"],
        kind="model",
        data={"target": "image", "workflow": "no_such_workflow"},
    )
    assert response.status_code == 422
    assert "no_such_workflow" in response.text


def test_model_card_target_mismatch_is_422(env):
    project = new_project(env)
    response = add_node(
        env,
        project["id"],
        kind="model",
        # krea2_turbo は画像ワークフロー: target=video では受け付けない
        data={"target": "video", "workflow": "krea2_turbo"},
    )
    assert response.status_code == 422


def test_model_card_without_workflow_is_allowed(env):
    """作りかけ（ワークフロー未選択）のカードは保存できる。"""
    project = new_project(env)
    response = add_node(env, project["id"], kind="model", data={"target": "video"})
    assert response.status_code == 201
    assert response.json()["data"]["workflow"] == ""


def test_model_card_patch_validates_workflow(env):
    project = new_project(env)
    node = add_node(
        env, project["id"], kind="model", data={"workflow": "krea2_turbo"}
    ).json()
    bad = env.client.patch(
        f"/api/canvas/projects/{project['id']}/nodes/{node['id']}",
        json={"data": {"target": "image", "workflow": "nope"}},
    )
    assert bad.status_code == 422
    ok = env.client.patch(
        f"/api/canvas/projects/{project['id']}/nodes/{node['id']}",
        json={"data": {"target": "video", "workflow": "ltx2_3_t2v"}},
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["workflow"] == "ltx2_3_t2v"


# --------------------------------------------------------------------------
# 5. ノードの PATCH
# --------------------------------------------------------------------------

def test_patch_node_position_only(env):
    project = new_project(env)
    node = add_node(
        env, project["id"], kind="character", data={"appearance": "silver hair"}
    ).json()
    response = env.client.patch(
        f"/api/canvas/projects/{project['id']}/nodes/{node['id']}",
        json={"x": 340.5, "y": -20},
    )
    assert response.status_code == 200
    moved = response.json()
    assert moved["x"] == 340.5 and moved["y"] == -20.0
    # 位置だけ送っても本文は残る
    assert moved["data"]["appearance"] == "silver hair"
    assert moved["updated_at"] >= node["updated_at"]


def test_patch_node_replaces_data_wholesale(env):
    project = new_project(env)
    node = add_node(
        env,
        project["id"],
        kind="character",
        title="ヒロイン",
        data={"appearance": "silver hair", "notes": "初期案"},
    ).json()
    updated = env.client.patch(
        f"/api/canvas/projects/{project['id']}/nodes/{node['id']}",
        json={"title": "ヒロイン（改）", "data": {"appearance": "black hair"}},
    ).json()
    assert updated["title"] == "ヒロイン（改）"
    assert updated["data"]["appearance"] == "black hair"
    # 部分マージではないので、送らなかった項目は既定値に戻る
    assert updated["data"]["notes"] == ""


def test_patch_node_of_other_project_is_404(env):
    first = new_project(env, "A")
    second = new_project(env, "B")
    node = add_node(env, first["id"], kind="text", data={"body": "x"}).json()
    assert (
        env.client.patch(
            f"/api/canvas/projects/{second['id']}/nodes/{node['id']}", json={"x": 10}
        ).status_code
        == 404
    )
    assert (
        env.client.delete(
            f"/api/canvas/projects/{second['id']}/nodes/{node['id']}"
        ).status_code
        == 404
    )


def test_delete_node(env):
    project = new_project(env)
    node = add_node(env, project["id"], kind="text", data={"body": "x"}).json()
    assert (
        env.client.delete(
            f"/api/canvas/projects/{project['id']}/nodes/{node['id']}"
        ).status_code
        == 204
    )
    detail = env.client.get(f"/api/canvas/projects/{project['id']}").json()
    assert detail["nodes"] == []


# --------------------------------------------------------------------------
# 6. z は作成順に単調増加
# --------------------------------------------------------------------------

def test_z_increases_with_creation_order(env):
    project = new_project(env)
    zs = [
        add_node(env, project["id"], kind="text", data={"body": str(i)}).json()["z"]
        for i in range(4)
    ]
    assert zs == sorted(zs) and len(set(zs)) == 4
    detail = env.client.get(f"/api/canvas/projects/{project['id']}").json()
    assert [n["z"] for n in detail["nodes"]] == zs


# --------------------------------------------------------------------------
# LLM provider（将来の多モデル化への布石）
# --------------------------------------------------------------------------

def test_llm_defaults_to_grok(env):
    project = new_project(env)
    assert project["llm"] == "grok"
    assert env.client.get(f"/api/canvas/projects/{project['id']}").json()["llm"] == "grok"
    assert env.client.get("/api/canvas/projects").json()[0]["llm"] == "grok"


def test_patch_unknown_llm_provider_is_422(env):
    project = new_project(env)
    response = env.client.patch(
        f"/api/canvas/projects/{project['id']}", json={"llm": "gpt-42"}
    )
    assert response.status_code == 422
    assert "gpt-42" in response.text
    # 既知の provider はそのまま通る
    ok = env.client.patch(
        f"/api/canvas/projects/{project['id']}", json={"llm": "grok"}
    )
    assert ok.status_code == 200 and ok.json()["llm"] == "grok"


# --------------------------------------------------------------------------
# 7. アクションのパース（P2）
# --------------------------------------------------------------------------

def test_parse_action_happy_paths():
    parse = canvas_protocol.parse_action

    assert parse("ただの日本語の返事です") is None

    action = parse(fenced({"action": "message", "content": "読みました"}))
    assert action.action == "message" and action.content == "読みました"

    assert parse(fenced({"action": "list_nodes"})).action == "list_nodes"

    action = parse(fenced({"action": "read_node", "node_id": "01ABC"}))
    assert action.node_id == "01ABC"

    action = parse(
        fenced(
            {
                "action": "create_node",
                "kind": "character",
                "title": "ヒロイン",
                "data": {"appearance": "silver hair"},
                "x": 400,
                "y": 120,
            }
        )
    )
    assert action.kind == "character" and action.x == 400.0 and action.y == 120.0
    # 省略したフィールドは kind のスキーマの既定値で埋まる
    assert action.data["appearance"] == "silver hair" and action.data["notes"] == ""

    action = parse(fenced({"action": "move_node", "node_id": "01ABC", "x": 8, "y": 3}))
    assert (action.x, action.y) == (8.0, 3.0)

    action = parse(fenced({"action": "best_practices", "workflow": "krea2_turbo"}))
    assert action.workflow == "krea2_turbo"

    action = parse(
        fenced(
            {
                "action": "generate",
                "model_node_id": "01M",
                "prompt": "A silver-haired girl",
                "source_image": "/library/image/x.png",
            }
        )
    )
    assert action.model_node_id == "01M" and action.source_image == "/library/image/x.png"

    action = parse(fenced({"action": "done", "content": "作りました"}))
    assert action.action == "done" and action.content == "作りました"


@pytest.mark.parametrize(
    "payload, needle",
    [
        ({"action": "teleport"}, "未知の action"),
        ({"action": "read_node"}, "node_id"),
        ({"action": "move_node", "node_id": "01A", "x": 1}, "y"),
        ({"action": "best_practices"}, "workflow"),
        ({"action": "generate", "prompt": "x"}, "model_node_id"),
        ({"action": "generate", "model_node_id": "01M"}, "prompt"),
        ({"action": "create_node", "kind": "unicorn"}, "kind"),
        (
            {"action": "create_node", "kind": "text", "data": {"foo": 1}},
            "スキーマに合いません",
        ),
    ],
)
def test_parse_action_rejects(payload, needle):
    with pytest.raises(canvas_protocol.CanvasActionError) as caught:
        canvas_protocol.parse_action(fenced(payload))
    assert needle in str(caught.value)


# --------------------------------------------------------------------------
# 8. 会話のターンループ（P2）
# --------------------------------------------------------------------------

def test_single_message_action_ends_the_turn(env):
    project = new_project(env)
    env.cli.answers = [fenced({"action": "message", "content": "了解しました"})]

    response = send(env, project["id"], "はじめまして")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content"] == "了解しました"
    assert body["project"]["thinking"] is False
    roles = [m["role"] for m in body["project"]["messages"]]
    assert roles == ["user", "assistant"]
    # LLM 呼び出しは 1 回だけ
    assert len(env.cli.prompts) == 1
    # システムプロンプトは目次と ACTIONS を含む
    assert "# CANVAS CARDS" in env.cli.prompts[0]
    assert "# ACTIONS" in env.cli.prompts[0]


def test_read_node_then_message_runs_two_turns(env):
    project = new_project(env)
    node = add_node(
        env,
        project["id"],
        kind="character",
        title="ヒロイン",
        data={"appearance": "silver hair, blue eyes"},
    ).json()
    env.cli.answers = [
        fenced({"action": "read_node", "node_id": node["id"]}),
        fenced({"action": "message", "content": "銀髪のヒロインですね"}),
    ]

    body = send(env, project["id"], "ヒロインを教えて").json()
    assert body["content"] == "銀髪のヒロインですね"
    assert len(env.cli.prompts) == 2
    # 2 回目のプロンプトには read_node の結果（カード本文）が EVENT で載る
    second = env.cli.prompts[1]
    assert "### EVENT (read_node)" in second
    assert "silver hair, blue eyes" in second

    kinds = [m["kind"] for m in body["project"]["messages"] if m["role"] == "event"]
    assert "read_node" in kinds


def test_create_and_move_node_from_actions(env):
    project = new_project(env)
    env.cli.answers = [
        fenced(
            {
                "action": "create_node",
                "kind": "location",
                "title": "夜の屋上",
                "data": {"description": "都会の屋上", "mood": "夜・小雨"},
                "x": 120,
                "y": 40,
            }
        ),
        fenced({"action": "list_nodes"}),
        fenced({"action": "done", "content": "置きました"}),
    ]

    body = send(env, project["id"], "場所のカードを作って").json()
    assert body["content"] == "置きました"
    nodes = body["project"]["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["kind"] == "location" and nodes[0]["title"] == "夜の屋上"
    assert nodes[0]["data"]["mood"] == "夜・小雨"
    assert (nodes[0]["x"], nodes[0]["y"]) == (120.0, 40.0)
    # list_nodes の結果に作ったカードが載る
    assert "夜の屋上" in env.cli.prompts[2]

    # 続けて移動させる
    env.cli.answers = [
        fenced({"action": "move_node", "node_id": nodes[0]["id"], "x": 800, "y": 300}),
        fenced({"action": "done", "content": "移動しました"}),
    ]
    moved = send(env, project["id"], "右に寄せて").json()["project"]["nodes"][0]
    assert (moved["x"], moved["y"]) == (800.0, 300.0)


def test_update_node_validates_against_the_card_kind(env):
    project = new_project(env)
    node = add_node(
        env, project["id"], kind="text", title="メモ", data={"body": "旧"}
    ).json()
    env.cli.answers = [
        fenced({"action": "update_node", "node_id": node["id"], "data": {"foo": 1}}),
        fenced({"action": "update_node", "node_id": node["id"], "data": {"body": "新"}}),
        fenced({"action": "done", "content": "直しました"}),
    ]

    body = send(env, project["id"], "メモを直して").json()
    assert body["project"]["nodes"][0]["data"]["body"] == "新"
    events = [m for m in body["project"]["messages"] if m["role"] == "event"]
    assert any("スキーマに合いません" in m["content"] for m in events)




# --------------------------------------------------------------------------
# 9. 壊れた JSON のリマインダー再試行（1 回だけ）
# --------------------------------------------------------------------------

def test_broken_action_retries_once(env):
    project = new_project(env)
    env.cli.answers = [
        # action 名が未知 → リマインダー付きで 1 回だけ再試行
        fenced({"action": "teleport", "content": "?"}),
        fenced({"action": "message", "content": "やり直しました"}),
    ]

    body = send(env, project["id"], "何かして").json()
    assert body["content"] == "やり直しました"
    assert len(env.cli.prompts) == 2
    assert "your previous action could not be used" in env.cli.prompts[1]
    assert "未知の action" in env.cli.prompts[1]


def test_broken_action_twice_falls_back_to_plain_text(env):
    project = new_project(env)
    env.cli.answers = [
        fenced({"action": "teleport"}),
        fenced({"action": "teleport"}),
    ]

    body = send(env, project["id"], "何かして").json()
    # 2 回目も駄目なら素のテキスト扱いで会話を終える
    assert len(env.cli.prompts) == 2
    assert body["project"]["thinking"] is False
    events = [m for m in body["project"]["messages"] if m["role"] == "event"]
    assert any(m["kind"] == "action_invalid" for m in events)


# --------------------------------------------------------------------------
# 10. 上限打ち切りと thinking の後始末
# --------------------------------------------------------------------------

def test_max_turns_truncates_and_clears_thinking(env, monkeypatch):
    monkeypatch.setattr(canvas_runner, "MAX_TURNS", 2)
    project = new_project(env)
    env.cli.answers = [fenced({"action": "list_nodes"}) for _ in range(2)]

    body = send(env, project["id"], "延々と調べて").json()
    assert "連続ターンが上限に達したため打ち切りました" in body["content"]
    assert len(env.cli.prompts) == 2
    assert body["project"]["thinking"] is False
    assert canvas_runner.is_thinking(project["id"]) is False


def test_llm_error_is_502_and_clears_thinking(env):
    project = new_project(env)
    env.cli.answers = [grok.LLMError("grok CLI が失敗しました")]

    response = send(env, project["id"], "こんにちは")
    assert response.status_code == 502
    assert "grok CLI" in response.text
    assert canvas_runner.is_thinking(project["id"]) is False
    # ユーザー発言は残る（会話の記録として）
    detail = env.client.get(f"/api/canvas/projects/{project['id']}").json()
    assert [m["role"] for m in detail["messages"]] == ["user"]
    assert detail["thinking"] is False


# --------------------------------------------------------------------------
# 11. @ 参照の展開
# --------------------------------------------------------------------------

def test_mention_expands_card_contents(env):
    project = new_project(env)
    node = add_node(
        env,
        project["id"],
        kind="character",
        title="ヒロイン",
        data={"appearance": "silver hair, blue eyes"},
    ).json()
    env.cli.answers = [fenced({"action": "message", "content": "了解"})]

    body = send(env, project["id"], "@ヒロイン の登場カットの案を出して").json()
    prompt_text = env.cli.prompts[0]
    assert "[Referenced cards — full contents]" in prompt_text
    assert "silver hair, blue eyes" in prompt_text
    assert f"id: {node['id']}" in prompt_text

    user = body["project"]["messages"][0]
    assert user["role"] == "user"
    # 元発言は data に控えてある（UI は展開後でなくこちらを出す）
    assert user["data"]["text"] == "@ヒロイン の登場カットの案を出して"
    assert user["data"]["mentions"] == [node["id"]]


def test_unknown_mention_passes_through(env):
    project = new_project(env)
    env.cli.answers = [fenced({"action": "message", "content": "了解"})]

    body = send(env, project["id"], "@いないひと について").json()
    assert "[Referenced cards — full contents]" not in env.cli.prompts[0]
    user = body["project"]["messages"][0]
    assert user["content"] == "@いないひと について"
    assert user["data"]["mentions"] == []


# --------------------------------------------------------------------------
# 12. 実行中 / 空文 / 停止
# --------------------------------------------------------------------------

def test_send_while_running_is_409(env):
    project = new_project(env)
    canvas_runner._thinking.add(project["id"])
    response = send(env, project["id"], "もう一度")
    assert response.status_code == 409
    assert env.client.get(f"/api/canvas/projects/{project['id']}").json()["thinking"]


def test_try_start_is_a_check_and_set(env):
    project = new_project(env)
    assert canvas_runner.try_start(project["id"]) is True
    assert canvas_runner.try_start(project["id"]) is False
    assert canvas_runner.is_thinking(project["id"]) is True


@pytest.mark.asyncio
async def test_run_conversation_refuses_a_second_loop(env):
    """予約済みのプロジェクトでは 2 本目のループを始めず、席も解放しない。"""
    project = new_project(env)
    assert canvas_runner.try_start(project["id"]) is True

    with pytest.raises(canvas_runner.AlreadyRunningError):
        await canvas_runner.run_conversation(project["id"])

    assert canvas_runner.is_thinking(project["id"]) is True
    assert env.cli.prompts == []


def test_send_reserves_thinking_before_any_await(env, monkeypatch):
    """席取りは content 検証の直後（他の await より前）に済んでいること。

    ここが await をまたぐと、同時 POST が両方とも 409 ガードを抜けてターン
    ループが 2 本走る（TOCTOU）。
    """
    project = new_project(env)
    original = canvas_router.expand_mentions
    observed: list[bool] = []

    async def spy(project_id: str, content: str):
        # この時点で席は取れている = 同時に来た POST は 409 に落ちる
        observed.append(canvas_runner.is_thinking(project_id))
        observed.append(canvas_runner.try_start(project_id))
        return await original(project_id, content)

    monkeypatch.setattr(canvas_router, "expand_mentions", spy)
    env.cli.answers = [fenced({"action": "message", "content": "はい"})]

    assert send(env, project["id"], "こんにちは").status_code == 200
    assert observed == [True, False]
    assert canvas_runner.is_thinking(project["id"]) is False


def test_send_releases_the_reservation_when_setup_fails(env, monkeypatch):
    """ターンに入る前で落ちても席は返す（以降の発言が 409 で詰まらない）。"""
    project = new_project(env)
    original = canvas_router.expand_mentions

    async def boom(project_id: str, content: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(canvas_router, "expand_mentions", boom)
    with pytest.raises(RuntimeError):
        send(env, project["id"], "こんにちは")
    assert canvas_runner.is_thinking(project["id"]) is False

    # 席が返っていれば次の発言は普通に通る
    monkeypatch.setattr(canvas_router, "expand_mentions", original)
    env.cli.answers = [fenced({"action": "message", "content": "はい"})]
    assert send(env, project["id"], "もう一度").status_code == 200


def test_empty_content_is_422(env):
    project = new_project(env)
    assert send(env, project["id"], "   ").status_code == 422
    assert canvas_runner.is_thinking(project["id"]) is False


def test_send_to_unknown_project_is_404(env):
    assert send(env, "01NOPE", "こんにちは").status_code == 404
    # 404 でも席は返す（予約は _require より前に取るため）
    assert canvas_runner.is_thinking("01NOPE") is False
    assert env.client.post("/api/canvas/projects/01NOPE/stop").status_code == 404


def test_stop_returns_detail_and_breaks_the_loop(env):
    project = new_project(env)
    # 実行中でなければ停止要求は残さない（次の発言を邪魔しない）
    detail = env.client.post(f"/api/canvas/projects/{project['id']}/stop")
    assert detail.status_code == 200 and detail.json()["thinking"] is False

    canvas_runner._thinking.add(project["id"])
    env.client.post(f"/api/canvas/projects/{project['id']}/stop")
    assert project["id"] in canvas_runner._stop_requests
    canvas_runner.reset()

    # 停止要求が立っていればターンを 1 つも回さずに畳む
    canvas_runner._stop_requests.add(project["id"])
    body = send(env, project["id"], "やっぱりやめる").json()
    assert body["content"] == "停止しました。"
    assert env.cli.prompts == []


# --------------------------------------------------------------------------
# LLM provider がクライアントファクトリに渡ること（多モデル化への布石）
# --------------------------------------------------------------------------

def test_run_turn_passes_project_llm_to_the_factory(env, monkeypatch):
    from app.canvas import llm as canvas_llm

    project = new_project(env)
    seen: list[tuple[str, str]] = []
    real = canvas_llm.get_client

    def spy(provider, workdir):
        seen.append((provider, str(workdir)))
        return real(provider, workdir)

    monkeypatch.setattr(canvas_runner.llm, "get_client", spy)
    env.cli.answers = [fenced({"action": "message", "content": "了解"})]

    assert send(env, project["id"], "こんにちは").status_code == 200
    assert [provider for provider, _ in seen] == ["grok"]
    # 作業ディレクトリはプロジェクトごとに分かれる
    assert seen[0][1].endswith(project["id"])


# --------------------------------------------------------------------------
# P3-13. model カード + generate アクション -> JobCreate のマッピング
# --------------------------------------------------------------------------

def model_node(project_id: str, **data) -> CanvasNode:
    """DB を通さずに組み立てた model カード（build_job_create の単体テスト用）。"""
    payload = {"target": "image", "workflow": "krea2_turbo", **data}
    return CanvasNode(
        id="n-model",
        project_id=project_id,
        created_at="2026-08-04T00:00:00+00:00",
        updated_at="2026-08-04T00:00:00+00:00",
        kind="model",
        title="モデル",
        data=validate_node_data("model", payload),
        x=100.0,
        y=50.0,
        w=320.0,
        h=220.0,
    )


def generate_action(**fields) -> CanvasAction:
    return CanvasAction(action="generate", model_node_id="n-model", **fields)


def test_build_job_create_for_image():
    node = model_node(
        "p1",
        target="image",
        workflow="krea2_turbo",
        params={
            "aspect_ratio": "16:9 (Widescreen)",
            "megapixels": 2.0,
            "loras": [{"lora_name": "anime.safetensors", "strength": 0.6}],
            "selects": {"quality": "high"},
            "negative_prompt": "blurry",
        },
    )
    payload = canvas_generate.build_job_create(node, generate_action(prompt="a girl"))

    assert payload.mode == "image_only"
    assert payload.image_workflow == "krea2_turbo"
    assert payload.image_prompt == "a girl"
    assert payload.aspect_ratio == "16:9 (Widescreen)"
    assert payload.megapixels == 2.0
    assert [lora.lora_name for lora in payload.loras] == ["anime.safetensors"]
    assert payload.loras[0].strength == 0.6
    assert payload.selects == {"quality": "high"}
    assert payload.negative_prompt == "blurry"
    # キャンバス由来と分かる印（chat_session_id は使わない）
    assert payload.user_input == "canvas:p1"
    assert payload.chat_session_id is None


def test_build_job_create_for_video():
    node = model_node(
        "p1",
        target="video",
        workflow="tx2_3_i2v",
        params={
            "duration": 6.0,
            "fps": 30,
            "sage_attention": True,
            "easy_cache": False,
            "video_loras": [{"lora_name": "motion.safetensors"}],
        },
    )
    payload = canvas_generate.build_job_create(
        node, generate_action(prompt="a car drives", source_image="/library/a.png")
    )

    # 動画単体は「動画ステージだけ走らせる」i2v
    assert payload.mode == "i2v"
    assert payload.video_workflow == "tx2_3_i2v"
    assert payload.video_prompt == "a car drives"
    assert payload.source_image == "/library/a.png"
    assert payload.duration == 6.0 and payload.fps == 30
    assert payload.sage_attention is True and payload.easy_cache is False
    assert [lora.lora_name for lora in payload.video_loras] == ["motion.safetensors"]
    # 画像 LoRA は動画ジョブに載せない
    assert payload.loras == []
    # 空の negative_prompt は JobCreate の既定値に任せる
    assert payload.negative_prompt == DEFAULT_NEGATIVE_PROMPT


def test_build_job_create_for_audio():
    node = model_node(
        "p1",
        target="audio",
        workflow="ace_step1_5_xl_sft",
        params={"duration": 30.0},
    )
    payload = canvas_generate.build_job_create(
        node, generate_action(prompt="dreamy city pop", lyrics="[Verse]\nhello")
    )

    assert payload.mode == "audio"
    assert payload.audio_workflow == "ace_step1_5_xl_sft"
    assert payload.audio_prompt == "dreamy city pop"
    assert payload.lyrics == "[Verse]\nhello"
    assert payload.duration == 30.0


# --------------------------------------------------------------------------
# P3-14/15. 投入できないとき
# --------------------------------------------------------------------------

def fake_job(job_id: str = "job-1", **fields) -> Job:
    base = dict(id=job_id, created_at="2026-08-04T00:00:00+00:00", mode="i2v",
                status="queued")
    return Job(**{**base, **fields})


def add_model_card(env, project_id: str, **data) -> dict:
    payload = {"target": "video", "workflow": "ltx2_3_t2v", **data}
    response = add_node(
        env, project_id, kind="model", title="動画モデル", data=payload, x=100, y=50
    )
    assert response.status_code == 201, response.text
    return response.json()


def event_contents(body: dict) -> list[str]:
    return [m["content"] for m in body["project"]["messages"] if m["role"] == "event"]


def test_generate_with_unknown_model_node_fails(env):
    project = new_project(env)
    env.cli.answers = [
        fenced(
            {"action": "generate", "model_node_id": "nope", "prompt": "a cat"}
        ),
        fenced({"action": "done", "content": "直します"}),
    ]
    body = send(env, project["id"], "生成して").json()

    assert body["content"] == "直します"
    assert any("見つかりませんでした" in text for text in event_contents(body))
    kinds = [m["kind"] for m in body["project"]["messages"] if m["role"] == "event"]
    assert "generate_failed" in kinds


def test_generate_on_a_non_model_card_fails(env):
    project = new_project(env)
    note = add_node(env, project["id"], title="メモ", data={"body": "…"}).json()
    env.cli.answers = [
        fenced(
            {"action": "generate", "model_node_id": note["id"], "prompt": "a cat"}
        ),
        fenced({"action": "done", "content": "model カードを作ります"}),
    ]
    body = send(env, project["id"], "生成して").json()

    assert any(
        "model カードの id を指定してください" in text for text in event_contents(body)
    )


def test_job_validation_error_is_reported(env, monkeypatch):
    project = new_project(env)
    model = add_model_card(env, project["id"])

    async def boom(payload, *, inherit_nsfw: bool = False):
        raise jobs.JobValidationError("source_image が必要です")

    monkeypatch.setattr(jobs, "create_job", boom)
    env.cli.answers = [
        fenced(
            {"action": "generate", "model_node_id": model["id"], "prompt": "a cat"}
        ),
        fenced({"action": "done", "content": "画像を先に作ります"}),
    ]
    body = send(env, project["id"], "動画にして").json()

    assert any(
        "生成を投入できませんでした: source_image が必要です" in text
        for text in event_contents(body)
    )
    # 投入に失敗したらバックグラウンドの完了待ちは走らない
    assert canvas_runner._generating.get(project["id"]) is None


def test_generate_starts_and_reports_the_job_id(env, monkeypatch):
    """投入は同期。完了待ちはバックグラウンドに逃がしてループは次のターンへ。"""
    project = new_project(env)
    model = add_model_card(env, project["id"])
    submitted: list = []

    async def fake_create(payload, *, inherit_nsfw: bool = False):
        submitted.append(payload)
        return fake_job("job-77")

    async def never_finishes(job_id: str, *, include_workflow: bool = True):
        return fake_job(job_id, status="running")

    monkeypatch.setattr(jobs, "create_job", fake_create)
    monkeypatch.setattr(jobs, "get_job", never_finishes)
    monkeypatch.setattr(canvas_generate, "POLL_INTERVAL", 0.01)
    env.cli.answers = [
        fenced(
            {"action": "generate", "model_node_id": model["id"], "prompt": "a cat"}
        ),
        fenced({"action": "done", "content": "投入しました"}),
    ]
    body = send(env, project["id"], "動画にして").json()

    assert body["content"] == "投入しました"
    assert any("生成を開始しました (job job-77)" in t for t in event_contents(body))
    assert submitted and submitted[0].video_prompt == "a cat"
    # 停止すればバックグラウンドの完了待ちも畳む
    env.client.post(f"/api/canvas/projects/{project['id']}/stop")
    assert not canvas_runner.is_generating(project["id"])


# --------------------------------------------------------------------------
# P3-16. 完了 -> media カードの自動配置
# --------------------------------------------------------------------------

async def run_generate_turn(env, project_id: str, model_id: str) -> None:
    """generate → done の 2 ターンを回し、バックグラウンドの完了待ちも待つ。"""
    await canvas_store.append_message(project_id, "user", "動画にして")
    env.cli.answers = [
        fenced(
            {"action": "generate", "model_node_id": model_id, "prompt": "a cat"}
        ),
        fenced({"action": "done", "content": "投入しました"}),
    ]
    await canvas_runner.run_conversation(project_id)
    task = canvas_runner._generating.get(project_id)
    assert task is not None, "generate のバックグラウンドタスクが登録されていない"
    await task


@pytest.mark.asyncio
async def test_finished_job_creates_media_cards(env, monkeypatch):
    project = new_project(env)
    model = add_model_card(env, project["id"])
    polls: list[str] = []

    async def fake_create(payload, *, inherit_nsfw: bool = False):
        return fake_job("job-9")

    async def fake_get(job_id: str, *, include_workflow: bool = True):
        polls.append(job_id)
        if len(polls) < 2:
            return fake_job(job_id, status="running")
        return fake_job(
            job_id,
            status="done",
            image_url="/outputs/a.png",
            video_url="/outputs/a.mp4",
        )

    monkeypatch.setattr(jobs, "create_job", fake_create)
    monkeypatch.setattr(jobs, "get_job", fake_get)
    monkeypatch.setattr(canvas_generate, "POLL_INTERVAL", 0.0)

    await run_generate_turn(env, project["id"], model["id"])

    nodes = await canvas_store.list_nodes(project["id"])
    media = [node for node in nodes if node.kind == "media"]
    # image + video の 2 枚が model カードの右に縦に並ぶ
    assert [node.data["media_type"] for node in media] == ["image", "video"]
    assert [node.data["url"] for node in media] == ["/outputs/a.png", "/outputs/a.mp4"]
    assert all(node.data["job_id"] == "job-9" for node in media)
    assert all(node.data["prompt"] == "a cat" for node in media)
    assert [node.x for node in media] == [100.0 + 320.0 + 40.0] * 2
    assert [node.y for node in media] == [50.0, 50.0 + 240.0]

    events = await canvas_store.list_messages(project["id"])
    done = [m for m in events if m.kind == "job_done"]
    assert done and "media カードを 2 枚配置しました" in done[0].content


@pytest.mark.asyncio
async def test_failed_job_reports_and_creates_nothing(env, monkeypatch):
    project = new_project(env)
    model = add_model_card(env, project["id"])

    async def fake_create(payload, *, inherit_nsfw: bool = False):
        return fake_job("job-x")

    async def fake_get(job_id: str, *, include_workflow: bool = True):
        return fake_job(job_id, status="failed", error="ComfyUI が落ちました")

    monkeypatch.setattr(jobs, "create_job", fake_create)
    monkeypatch.setattr(jobs, "get_job", fake_get)
    monkeypatch.setattr(canvas_generate, "POLL_INTERVAL", 0.0)

    await run_generate_turn(env, project["id"], model["id"])

    nodes = await canvas_store.list_nodes(project["id"])
    assert [node.kind for node in nodes] == ["model"]
    messages = await canvas_store.list_messages(project["id"])
    failed = [m for m in messages if m.kind == "job_failed"]
    assert failed and "ComfyUI が落ちました" in failed[0].content


# --------------------------------------------------------------------------
# P3-17. best_practices
# --------------------------------------------------------------------------

def test_best_practices_returns_the_catalog_entry(env):
    project = new_project(env)
    env.cli.answers = [
        fenced({"action": "best_practices", "workflow": "ltx2_3_t2v"}),
        fenced({"action": "done", "content": "把握しました"}),
    ]
    body = send(env, project["id"], "このワークフローの使い方は？").json()

    assert body["content"] == "把握しました"
    entry = workflows.catalog_entry(workflows.get_spec("ltx2_3_t2v"))
    hits = [
        m["content"]
        for m in body["project"]["messages"]
        if m["kind"] == "best_practices"
    ]
    assert len(hits) == 1
    assert entry.label in hits[0]
    assert entry.description.splitlines()[0] in hits[0]
    assert "PROMPT HINT" in hits[0] and entry.prompt_hint.splitlines()[0] in hits[0]
    # 読んだ本文は次のターンのプロンプトにも載る
    assert entry.label in env.cli.prompts[1]


def test_best_practices_with_unknown_workflow_guides_back(env):
    project = new_project(env)
    env.cli.answers = [
        fenced({"action": "best_practices", "workflow": "no_such_workflow"}),
        fenced({"action": "done", "content": "選び直します"}),
    ]
    body = send(env, project["id"], "使い方は？").json()

    assert any(
        "no_such_workflow" in text and "見つかりませんでした" in text
        for text in event_contents(body)
    )


def test_best_practices_text_lists_selects():
    """選択式のあるワークフローは選択肢と既定値まで出す。"""
    spec = next(
        (s for s in workflows.video_specs() if workflows.catalog_entry(s).selects),
        None,
    )
    if spec is None:  # pragma: no cover - カタログ次第
        pytest.skip("選択式を持つ動画ワークフローがない")
    text = canvas_generate.best_practices_text(spec.id)
    for name, _label, choices, default, _auto, _hint in workflows.catalog_entry(
        spec
    ).selects:
        assert f"select `{name}`" in text
        assert f"（既定 {default}）" in text
        assert choices[0] in text
