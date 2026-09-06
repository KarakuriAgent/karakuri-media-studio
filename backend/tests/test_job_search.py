"""ジョブ一覧のサーバー側の絞り込み（``GET /api/jobs`` の q / kind / project_id / nsfw）。

ライブラリタブの「生成履歴」が 1000 件を超える履歴から作品名（日本語）で
探せるようにするためのもの（SPEC §9）。行は API を通さず直接置く: ここで
確かめたいのは実行ではなく SQL の絞り込みとページングだけ。
"""

import pytest
from fastapi.testclient import TestClient

from app import comfy, db, jobs, nsfw
from app.main import app


async def _no_llm(*args, **kwargs):
    return False, ""


@pytest.fixture
def env(tmp_path, monkeypatch):
    """空の DB を持つクライアント（ComfyUI へは繋がない）。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(jobs, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(nsfw, "classify", _no_llm)

    async def offline(*args, **kwargs):
        raise comfy.ComfyError("ComfyUI is down")

    for name in ("get_object_info", "upload_file", "queue_prompt"):
        monkeypatch.setattr(comfy, name, offline)

    with TestClient(app) as client:
        yield client


def seed(client, sql: str, params: tuple) -> None:
    async def run():
        async with db.get_db() as conn:
            await conn.execute(sql, params)
            await conn.commit()

    client.portal.call(run)  # type: ignore[attr-defined]


def add_job(
    client,
    job_id: str,
    *,
    created_at: str,
    video_prompt: str | None = None,
    image_path: str | None = None,
    video_path: str | None = None,
    audio_output_path: str | None = None,
    is_nsfw: bool = False,
) -> None:
    seed(
        client,
        "INSERT INTO jobs (id, created_at, mode, status, video_prompt, params,"
        " workflow_json, image_path, video_path, audio_output_path, nsfw)"
        " VALUES (?, ?, 'full', 'done', ?, '{}', '{}', ?, ?, ?, ?)",
        (
            job_id,
            created_at,
            video_prompt,
            image_path,
            video_path,
            audio_output_path,
            1 if is_nsfw else 0,
        ),
    )


def add_take(
    client, job_id: str, *, project: str, shot_title: str, seq: int = 1
) -> None:
    """作品 1 本・カット 1 つ・その Take を置く（作品名は作品ごとに 1 つ）。"""
    project_id = f"p-{project}"
    shot_id = f"s-{job_id}-{seq}"
    seed(
        client,
        "INSERT OR IGNORE INTO studio_projects (id, name, created_at, updated_at)"
        " VALUES (?, ?, '2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00')",
        (project_id, project),
    )
    seed(
        client,
        "INSERT INTO studio_shots (id, project_id, title, created_at, updated_at)"
        " VALUES (?, ?, ?, '2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00')",
        (shot_id, project_id, shot_title),
    )
    seed(
        client,
        "INSERT INTO studio_takes (id, shot_id, project_id, job_id, created_at)"
        " VALUES (?, ?, ?, ?, '2026-07-01T00:00:00+00:00')",
        (f"t-{job_id}-{seq}", shot_id, project_id, job_id),
    )


@pytest.fixture
def history(env):
    """作品の Take 2 件（かおりプロジェクト）と、素の生成 1 件。"""
    add_job(
        env,
        "j1",
        created_at="2026-07-10T10:00:00+00:00",
        video_prompt="a cat dancing on the rooftop",
        video_path="/outputs/j1/clip.mp4",
    )
    add_take(env, "j1", project="かおりプロジェクト", shot_title="屋上の逢瀬")
    add_job(
        env,
        "j2",
        created_at="2026-08-01T10:00:00+00:00",
        video_prompt="a neon street",
        image_path="/outputs/j2/still.png",
    )
    add_take(env, "j2", project="かおりプロジェクト", shot_title="夜の路地")
    add_job(
        env,
        "j3",
        created_at="2026-08-05T10:00:00+00:00",
        video_prompt="an unrelated clip",
        audio_output_path="/outputs/j3/track.mp3",
        is_nsfw=True,
    )
    return env


def listing(client, query: str = "") -> tuple[list[str], int]:
    response = client.get(f"/api/jobs{query}")
    assert response.status_code == 200, response.text
    return (
        [job["id"] for job in response.json()],
        int(response.headers["X-Total-Count"]),
    )


def test_a_take_job_carries_its_project_and_shot(history):
    rows = history.get("/api/jobs").json()
    by_id = {row["id"]: row for row in rows}
    assert by_id["j1"]["project_name"] == "かおりプロジェクト"
    assert by_id["j1"]["shot_title"] == "屋上の逢瀬"
    assert by_id["j1"]["project_id"] == "p-かおりプロジェクト"
    # Take になっていないジョブは 3 つとも null
    assert by_id["j3"]["project_name"] is None
    assert by_id["j3"]["project_id"] is None
    assert by_id["j3"]["shot_title"] is None
    # 詳細（SELECT * のまま）は今までどおり出どころを持たない
    assert history.get("/api/jobs/j1").json()["project_name"] is None


def test_q_matches_the_project_name_even_with_an_english_prompt(history):
    assert listing(history, "?q=かおり") == (["j2", "j1"], 2)
    # カット題名でも引ける
    assert listing(history, "?q=夜の路地") == (["j2"], 1)
    # プロンプトへの部分一致は今までどおり（大文字小文字は無視）
    assert listing(history, "?q=NEON") == (["j2"], 1)
    assert listing(history, "?q=みつからない") == ([], 0)


def test_wildcards_in_q_are_plain_characters(history):
    """``_`` ``%`` は LIKE のワイルドカードではなく、その文字として探す。"""
    add_job(
        history,
        "j4",
        created_at="2026-08-06T10:00:00+00:00",
        video_prompt="a shot_list for the finale",
        video_path="/outputs/j4/clip.mp4",
    )
    add_job(
        history,
        "j5",
        created_at="2026-08-07T10:00:00+00:00",
        video_prompt="lit at 50% power",
        video_path="/outputs/j5/clip.mp4",
    )

    # `_` で全件が出てはいけない（アンダースコアを含む 1 件だけ）
    assert listing(history, "?q=_") == (["j4"], 1)
    assert listing(history, "?q=shot_list") == (["j4"], 1)
    assert listing(history, "?q=shotXlist") == ([], 0)
    # `%` も同じ（%25 は URL エンコード）
    assert listing(history, "?q=%25") == (["j5"], 1)
    assert listing(history, "?q=50%25 power") == (["j5"], 1)
    # 打ち消し用のバックスラッシュそのものを探しても落ちない
    assert listing(history, "?q=%5C") == ([], 0)


def test_kind_keeps_only_the_jobs_that_have_that_output(history):
    assert listing(history, "?kind=video") == (["j1"], 1)
    assert listing(history, "?kind=image") == (["j2"], 1)
    assert listing(history, "?kind=audio") == (["j3"], 1)
    assert history.get("/api/jobs?kind=model").status_code == 422


def test_project_id_keeps_only_that_project_takes(history):
    assert listing(history, "?project_id=p-かおりプロジェクト") == (["j2", "j1"], 2)
    assert listing(history, "?project_id=p-ない") == ([], 0)


def test_nsfw_filters_in_both_directions(history):
    assert listing(history, "?nsfw=false") == (["j2", "j1"], 2)
    assert listing(history, "?nsfw=true") == (["j3"], 1)
    assert listing(history) == (["j3", "j2", "j1"], 3)


def test_total_count_is_the_whole_match_not_the_page(history):
    ids, total = listing(history, "?limit=1")
    assert ids == ["j3"]
    assert total == 3
    assert listing(history, "?limit=1&offset=2") == (["j1"], 3)


def test_several_takes_on_one_job_do_not_duplicate_the_row(history):
    """同じジョブに Take が 2 つぶら下がっても一覧には 1 行だけ出す。"""
    add_take(
        history, "j1", project="かおりプロジェクト", shot_title="屋上の逢瀬（別）", seq=2
    )
    assert listing(history, "?q=かおり") == (["j2", "j1"], 2)
