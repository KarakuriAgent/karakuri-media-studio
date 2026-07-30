"""ライブラリ素材の日本語タグ・表示名の自動生成（SPEC §7.2）。Grok はモックする。"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import autotag, comfy, db, grok, jobs, library, ws
from app.main import app
from app.models import Job
from app.routers import assets as assets_router


class FakeCli:
    """`grok -p` の代わりに決め打ちの答えを返す（test_chat.py と同じ方式）。"""

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def __call__(self, argv, cwd, timeout):
        self.prompts.append(argv[-1])
        if not self.answers:
            raise AssertionError("fake grok CLI ran out of scripted answers")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return (0, answer, "")


@pytest.fixture
def env(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    lib = tmp_path / "library"
    (assets / "image").mkdir(parents=True)
    lib.mkdir()

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(library, "LIBRARY_DIR", lib)
    monkeypatch.setattr(jobs, "LIBRARY_DIR", lib)
    monkeypatch.setattr(jobs, "ASSETS_DIR", assets)
    monkeypatch.setattr(assets_router, "ASSETS_DIR", assets)

    async def offline():
        raise comfy.ComfyError("ComfyUI is down")

    monkeypatch.setattr(comfy, "get_object_info", lambda *a, **k: offline())

    cli = FakeCli()
    monkeypatch.setattr(grok, "_exec", cli)

    with TestClient(app) as client:
        yield type("Env", (), {"client": client, "cli": cli, "tmp": tmp_path, "library": lib})


ANSWER = """了解しました。

```json
{"name": "夕暮れ屋上のダンス", "tags": ["女性", "屋上", "夕暮れ", "ダンス"]}
```
"""


def job_with(**fields) -> Job:
    base = {
        "id": "job1",
        "created_at": "2026-07-30T10:00:00+00:00",
        "mode": "full",
        "status": "done",
        "video_prompt": "a young woman dancing on a rooftop at sunset",
    }
    return Job(**{**base, **fields})


# --------------------------------------------------------------------------
# describe（Grok への 1 往復）
# --------------------------------------------------------------------------

async def test_describe_returns_a_japanese_name_and_tags(env):
    env.cli.answers = [ANSWER]
    name, tags = await autotag.describe("a young woman dancing on a rooftop")
    assert name == "夕暮れ屋上のダンス"
    assert tags == ["女性", "屋上", "夕暮れ", "ダンス"]
    # プロンプトには日本語で返させる指示と、対象テキストが入っている
    assert "日本語" in env.cli.prompts[0]
    assert "a young woman dancing on a rooftop" in env.cli.prompts[0]


async def test_describe_normalizes_and_caps_the_tags(env):
    env.cli.answers = [
        '```json\n{"name": " 名前 ", "tags": ["a", " a ", "", "b", "c", "d", "e", "f"]}\n```'
    ]
    name, tags = await autotag.describe("something")
    assert name == "名前"
    # 重複と空を落としたうえで上限まで
    assert tags == ["a", "b", "c", "d", "e"][: autotag.MAX_TAGS]


async def test_describe_gives_up_quietly_when_grok_fails(env):
    env.cli.answers = [grok.LLMError("grok not found")]
    assert await autotag.describe("something") == ("", [])


async def test_describe_gives_up_on_an_unparsable_answer(env):
    env.cli.answers = ["すみません、よく分かりません。"]
    assert await autotag.describe("something") == ("", [])


async def test_describe_skips_an_empty_text(env):
    assert await autotag.describe("   ") == ("", [])
    assert env.cli.prompts == []


def test_source_text_joins_what_the_job_tried_to_make(env):
    assert autotag.source_text(job_with()) == (
        "a young woman dancing on a rooftop at sunset"
    )
    combined = autotag.source_text(
        job_with(image_prompt="a portrait", user_input="踊っている女性")
    )
    assert combined.splitlines() == [
        "a young woman dancing on a rooftop at sunset",
        "a portrait",
        "踊っている女性",
    ]
    assert autotag.source_text(job_with(video_prompt=None)) == ""


# --------------------------------------------------------------------------
# annotate（書き戻しと WS 通知）
# --------------------------------------------------------------------------

async def make_item(name="a young woman dancing（生成画像）", tags=()):
    picture = library.kind_dir("image") / "x.png"
    picture.write_bytes(b"PNG")
    return await library._insert(
        kind="image",
        name=name,
        path=picture,
        nsfw=False,
        nsfw_source="",
        source_job_id="job1",
        source="image",
        tags=list(tags),
    )


async def test_annotate_writes_back_the_generated_name_and_tags(env, monkeypatch):
    env.cli.answers = [ANSWER]
    item = await make_item()
    published: list[dict] = []

    async def capture(payload: dict) -> None:
        published.append(payload)

    monkeypatch.setattr(ws.hub, "broadcast", capture)

    await autotag.annotate(item.id, "a woman dancing", set_name=True, set_tags=True)

    updated = await library.get_item(item.id)
    assert updated is not None
    assert updated.name == "夕暮れ屋上のダンス"
    assert updated.tags == ["女性", "屋上", "夕暮れ", "ダンス"]
    # WS で画面に伝える
    assert published and published[0]["type"] == "library"
    assert published[0]["item_id"] == item.id
    assert published[0]["tags"] == updated.tags


async def test_annotate_keeps_what_the_caller_set(env):
    env.cli.answers = [ANSWER]
    item = await make_item(name="わたしが付けた名前", tags=["自分のタグ"])

    await autotag.annotate(
        item.id, "a woman dancing", set_name=False, set_tags=False
    )
    updated = await library.get_item(item.id)
    assert updated is not None
    assert (updated.name, updated.tags) == ("わたしが付けた名前", ["自分のタグ"])
    # Grok すら呼ばない
    assert env.cli.prompts == []


async def test_annotate_can_fill_only_the_tags(env):
    env.cli.answers = [ANSWER]
    item = await make_item(name="わたしが付けた名前")
    await autotag.annotate(item.id, "a woman dancing", set_name=False, set_tags=True)
    updated = await library.get_item(item.id)
    assert updated is not None
    assert updated.name == "わたしが付けた名前"
    assert updated.tags == ["女性", "屋上", "夕暮れ", "ダンス"]


async def test_annotate_survives_a_grok_failure(env):
    env.cli.answers = [grok.LLMError("grok not found")]
    item = await make_item()
    await autotag.annotate(item.id, "a woman dancing", set_name=True, set_tags=True)
    updated = await library.get_item(item.id)
    assert updated is not None
    # 何も変えない（登録そのものは成功したまま）
    assert updated.name == "a young woman dancing（生成画像）"
    assert updated.tags == []


async def test_annotate_survives_a_deleted_item(env):
    env.cli.answers = [ANSWER]
    await autotag.annotate("ghost", "a woman dancing", set_name=True, set_tags=True)


# --------------------------------------------------------------------------
# 登録経路との結線
# --------------------------------------------------------------------------

def make_job_row(env, job_id="job1", **outputs):
    fields = {}
    for column, filename in outputs.items():
        path = env.tmp / "outputs" / job_id / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"OUT")
        fields[column] = str(path)

    async def insert():
        async with db.get_db() as conn:
            await conn.execute(
                "INSERT INTO jobs (id, created_at, mode, status, params,"
                " workflow_json, video_prompt, nsfw, nsfw_source)"
                " VALUES (?, '2026-07-30T10:00:00+00:00', 'full', 'done', '{}', '{}',"
                " 'a young woman dancing on a rooftop at sunset', 0, '')",
                (job_id,),
            )
            for column, value in fields.items():
                await conn.execute(
                    f"UPDATE jobs SET {column} = ? WHERE id = ?", (value, job_id)
                )
            await conn.commit()

    asyncio.run(insert())


def wait_for_tags(env, item_id: str, timeout: float = 5.0) -> dict:
    """バックグラウンドのタグ生成が反映されるまで待つ。"""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = env.client.get("/api/library").json()["items"]
        item = next((row for row in rows if row["id"] == item_id), None)
        if item and item["tags"]:
            return item
        time.sleep(0.05)
    raise AssertionError("タグが付きませんでした")


def test_from_job_generates_tags_in_the_background(env):
    make_job_row(env, image_path="still.png")
    env.cli.answers = [ANSWER]
    created = env.client.post(
        "/api/library/from-job", json={"job_id": "job1", "source": "image"}
    )
    assert created.status_code == 201, created.text
    # 応答はすぐ返る（この時点ではまだ既定の名前）
    assert created.json()["tags"] == []

    item = wait_for_tags(env, created.json()["id"])
    assert item["tags"] == ["女性", "屋上", "夕暮れ", "ダンス"]
    assert item["name"] == "夕暮れ屋上のダンス"


def test_from_job_does_not_touch_what_the_caller_specified(env):
    make_job_row(env, image_path="still.png")
    created = env.client.post(
        "/api/library/from-job",
        json={
            "job_id": "job1",
            "source": "image",
            "name": "わたしが付けた名前",
            "tags": ["自分のタグ"],
        },
    ).json()
    assert created["name"] == "わたしが付けた名前"
    assert created["tags"] == ["自分のタグ"]
    # Grok は呼ばれない（scripted answer を用意していないので、呼ばれたら失敗する）
    assert env.cli.prompts == []


def test_upload_is_not_auto_tagged(env):
    """アップロードにはプロンプトが無いので対象外（ファイル名からは作らない）。"""
    created = env.client.post(
        "/api/library/image", files={"file": ("ref.png", b"PNG", "image/png")}
    ).json()
    assert created["tags"] == []
    assert env.cli.prompts == []
