"""ライブラリ API（SPEC §7.2）: 登録・一覧・更新・削除とジョブ入力への利用。"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import autotag, comfy, db, jobs, library
from app.main import app
from app.routers import assets as assets_router


async def _no_llm(text: str) -> tuple[str, list[str]]:
    """タグ自動生成の Grok 呼び出しを潰す差し替え。"""
    return "", []


@pytest.fixture
def env(tmp_path, monkeypatch):
    """DB・assets・library をテスト用ディレクトリに閉じ込めたクライアント。"""
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
    # タグの自動生成は test_autotag.py で検証する。ここでは Grok を呼ばせない。
    monkeypatch.setattr(autotag, "describe", _no_llm)

    with TestClient(app) as client:
        yield type(
            "Env",
            (),
            {"client": client, "library": lib, "assets": assets, "tmp": tmp_path},
        )


def upload(env, kind: str, name: str, data: bytes = b"data", tags: str | None = None):
    return env.client.post(
        f"/api/library/{kind}",
        files={"file": (name, data, "application/octet-stream")},
        data={"tags": tags} if tags is not None else None,
    )


def page(env, **params) -> dict:
    """GET /api/library の 1 ページ（items / total / tags を持つ）。"""
    response = env.client.get("/api/library", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def items(env, **params) -> list[dict]:
    return page(env, **params)["items"]


def ids(rows: list[dict]) -> list[str]:
    return [row["id"] for row in rows]


# --------------------------------------------------------------------------
# アップロードでの登録
# --------------------------------------------------------------------------

def test_upload_stores_the_file_and_lists_it(env):
    created = upload(env, "image", "ref.png", b"PNG")
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["kind"] == "image"
    assert item["name"] == "ref.png"
    assert item["url"].startswith("/library/image/")
    assert item["nsfw"] is False
    assert item["source_job_id"] is None
    # 実体は library/image/ に置かれ、URL とパスが一致する
    stored = env.library / "image" / item["url"].rsplit("/", 1)[-1]
    assert stored.read_bytes() == b"PNG"
    assert item["path"] == str(stored)

    assert ids(items(env)) == [item["id"]]


def test_upload_rejects_a_wrong_extension(env):
    response = upload(env, "image", "clip.mp4")
    assert response.status_code == 400
    assert ".mp4" in response.text
    assert items(env) == []


def test_upload_rejects_an_unknown_kind(env):
    assert upload(env, "model", "x.png").status_code == 400


def test_list_filters_by_kind_and_is_newest_first(env):
    first = upload(env, "image", "a.png").json()
    second = upload(env, "image", "b.png").json()
    clip = upload(env, "video", "c.mp4").json()

    everything = items(env)
    assert {row["id"] for row in everything} == {first["id"], second["id"], clip["id"]}
    # 同じ秒に作られても id の降順で新しいほうが先に来る
    assert ids(items(env, kind="image")) == sorted(
        [first["id"], second["id"]], reverse=True
    )
    assert ids(items(env, kind="video")) == [clip["id"]]


def test_list_rejects_an_unknown_kind_filter(env):
    assert env.client.get("/api/library?kind=nope").status_code == 422


# --------------------------------------------------------------------------
# 生成物からの登録
# --------------------------------------------------------------------------

async def _insert_job(job_id: str, **fields) -> None:
    """最低限のカラムだけ埋めた done ジョブを 1 件作る。"""
    row = {
        "id": job_id,
        "created_at": "2026-07-30T10:00:00+00:00",
        "mode": "full",
        "status": "done",
        "user_input": None,
        "image_prompt": "a portrait",
        "video_prompt": "a dance clip",
        "audio_prompt": None,
        "grok_raw": None,
        "params": "{}",
        "workflow_json": "{}",
        "comfy_prompt_id": None,
        "image_path": None,
        "video_path": None,
        "last_frame_path": None,
        "source_image": None,
        "audio_path": None,
        "audio_output_path": None,
        "error": None,
        "nsfw": 0,
        "nsfw_source": "",
        **fields,
    }
    async with db.get_db() as conn:
        await conn.execute(
            "INSERT INTO jobs (id, created_at, mode, status, user_input, image_prompt,"
            " video_prompt, audio_prompt, grok_raw, params, workflow_json,"
            " comfy_prompt_id, image_path, video_path, last_frame_path, source_image,"
            " audio_path, audio_output_path, error, nsfw, nsfw_source)"
            " VALUES (:id, :created_at, :mode, :status, :user_input, :image_prompt,"
            " :video_prompt, :audio_prompt, :grok_raw, :params, :workflow_json,"
            " :comfy_prompt_id, :image_path, :video_path, :last_frame_path,"
            " :source_image, :audio_path, :audio_output_path, :error, :nsfw,"
            " :nsfw_source)",
            row,
        )
        await conn.commit()


def make_job(env, job_id="job1", *, nsfw=False, **outputs):
    """出力ファイルを作ってから、それを指す done ジョブを DB に入れる。"""
    fields = {}
    for column, filename in outputs.items():
        path = env.tmp / "outputs" / job_id / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"OUT")
        fields[column] = str(path)
    asyncio.run(_insert_job(job_id, nsfw=1 if nsfw else 0, **fields))


def test_from_job_copies_the_output_into_the_library(env):
    make_job(env, image_path="still.png", last_frame_path="last.png")

    created = env.client.post(
        "/api/library/from-job", json={"job_id": "job1", "source": "image"}
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["kind"] == "image"
    assert item["source_job_id"] == "job1"
    # 既定の表示名はプロンプト + 何の出力か
    assert item["name"] == "a dance clip（生成画像）"
    stored = env.library / "image" / item["url"].rsplit("/", 1)[-1]
    assert stored.read_bytes() == b"OUT"
    # 元の生成物はそのまま（コピーであって移動ではない）
    assert (env.tmp / "outputs" / "job1" / "still.png").is_file()

    # 同じジョブのラストフレームは別の 1 件として登録できる
    frame = env.client.post(
        "/api/library/from-job",
        json={"job_id": "job1", "source": "last_frame", "name": "決めポーズ"},
    ).json()
    assert frame["name"] == "決めポーズ"
    assert frame["id"] != item["id"]
    assert len(items(env, kind="image")) == 2


def test_from_job_inherits_the_nsfw_flag(env):
    make_job(env, "spicy", nsfw=True, image_path="still.png")
    item = env.client.post(
        "/api/library/from-job", json={"job_id": "spicy", "source": "image"}
    ).json()
    assert item["nsfw"] is True
    assert item["nsfw_source"] == "auto"


def test_from_job_maps_each_source_to_its_kind(env):
    make_job(
        env,
        "all",
        image_path="still.png",
        last_frame_path="last.png",
        video_path="clip.mp4",
        audio_output_path="track.mp3",
    )
    kinds = {}
    for source in ("image", "last_frame", "video", "audio"):
        item = env.client.post(
            "/api/library/from-job", json={"job_id": "all", "source": source}
        ).json()
        kinds[source] = item["kind"]
    assert kinds == {
        "image": "image",
        "last_frame": "image",
        "video": "video",
        "audio": "audio",
    }


def test_from_job_without_that_output_is_400(env):
    make_job(env, "img", image_path="still.png")
    response = env.client.post(
        "/api/library/from-job", json={"job_id": "img", "source": "video"}
    )
    assert response.status_code == 400
    assert "動画" in response.text


def test_from_job_with_an_unknown_job_is_404(env):
    response = env.client.post(
        "/api/library/from-job", json={"job_id": "ghost", "source": "image"}
    )
    assert response.status_code == 404


def test_from_job_rejects_an_unknown_source(env):
    make_job(env, "img", image_path="still.png")
    response = env.client.post(
        "/api/library/from-job", json={"job_id": "img", "source": "thumbnail"}
    )
    assert response.status_code == 422  # pydantic の Literal で弾かれる


# --------------------------------------------------------------------------
# 更新・削除
# --------------------------------------------------------------------------

def test_patch_changes_the_name_and_the_nsfw_flag(env):
    item = upload(env, "image", "ref.png").json()
    renamed = env.client.patch(
        f"/api/library/{item['id']}", json={"name": "お気に入り"}
    ).json()
    assert renamed["name"] == "お気に入り"
    assert renamed["nsfw"] is False

    flagged = env.client.patch(
        f"/api/library/{item['id']}", json={"nsfw": True}
    ).json()
    assert flagged["nsfw"] is True
    assert flagged["nsfw_source"] == "manual"
    # 触っていない項目はそのまま
    assert flagged["name"] == "お気に入り"


def test_patch_of_an_unknown_item_is_404(env):
    assert env.client.patch("/api/library/ghost", json={"name": "x"}).status_code == 404


def test_delete_removes_the_row_and_the_file(env):
    item = upload(env, "image", "ref.png").json()
    stored = env.library / "image" / item["url"].rsplit("/", 1)[-1]
    assert env.client.delete(f"/api/library/{item['id']}").status_code == 204
    assert not stored.exists()
    assert items(env) == []
    assert env.client.delete(f"/api/library/{item['id']}").status_code == 404


# --------------------------------------------------------------------------
# ジョブの入力としての利用 / /api/options
# --------------------------------------------------------------------------

def test_library_paths_are_accepted_as_job_inputs(env):
    item = upload(env, "image", "ref.png", b"PNG").json()
    for value in (item["url"], item["path"]):
        assert jobs.resolve_asset_path(value, field="source_image").is_file()


def test_assets_paths_still_work(env):
    """LoRA の default_audio など、既存の /assets URL は引き続き有効。"""
    picture = env.assets / "image" / "old.png"
    picture.write_bytes(b"PNG")
    assert jobs.resolve_asset_path(
        "/assets/image/old.png", field="source_image"
    ) == picture.resolve()
    assert jobs.resolve_asset_path("image/old.png", field="source_image")


def test_a_path_outside_both_roots_is_rejected(env):
    stray = env.tmp / "stray.png"
    stray.write_bytes(b"PNG")
    with pytest.raises(jobs.JobValidationError) as excinfo:
        jobs.resolve_asset_path(str(stray), field="source_image")
    assert "must point inside" in str(excinfo.value)


def test_options_expose_the_library(env):
    item = upload(env, "audio", "bgm.mp3").json()
    options = env.client.get("/api/options").json()
    assert [row["id"] for row in options["library"]] == [item["id"]]
    assert options["library"][0]["url"] == item["url"]


def test_library_survives_a_deleted_job(env):
    """履歴を消してもライブラリは残る（それが取っておく意味）。"""
    make_job(env, "gone", image_path="still.png")
    item = env.client.post(
        "/api/library/from-job", json={"job_id": "gone", "source": "image"}
    ).json()

    async def drop_job():
        async with db.get_db() as conn:
            await conn.execute("DELETE FROM jobs WHERE id = 'gone'")
            await conn.commit()

    asyncio.run(drop_job())
    assert ids(items(env)) == [item["id"]]
    assert jobs.resolve_asset_path(item["url"], field="source_image").is_file()


def test_json_payload_shape_is_stable(env):
    """フロントの LibraryItem 型と同じキーを返す。"""
    item = upload(env, "image", "ref.png").json()
    assert set(item) == {
        "id",
        "created_at",
        "kind",
        "name",
        "path",
        "url",
        "nsfw",
        "nsfw_source",
        "source_job_id",
        "source",
        "tags",
    }
    assert json.loads(json.dumps(item)) == item


# --------------------------------------------------------------------------
# タグ
# --------------------------------------------------------------------------

def test_upload_accepts_tags_as_a_comma_separated_field(env):
    item = upload(env, "image", "ref.png", tags=" 背景 , 夜景 ,, 背景 ").json()
    # 前後の空白・空・重複は落とし、並びは書いた順のまま
    assert item["tags"] == ["背景", "夜景"]


def test_upload_without_tags_has_none(env):
    assert upload(env, "image", "ref.png").json()["tags"] == []


def test_from_job_accepts_tags(env):
    make_job(env, image_path="still.png")
    item = env.client.post(
        "/api/library/from-job",
        json={"job_id": "job1", "source": "image", "tags": ["キャラ", "サクラ"]},
    ).json()
    assert item["tags"] == ["キャラ", "サクラ"]


def test_patch_replaces_the_tags(env):
    item = upload(env, "image", "ref.png", tags="背景").json()
    updated = env.client.patch(
        f"/api/library/{item['id']}", json={"tags": ["夜景", "都市"]}
    ).json()
    assert updated["tags"] == ["夜景", "都市"]
    # 空リストは「全部外す」（name と違って無視しない）
    assert env.client.patch(
        f"/api/library/{item['id']}", json={"tags": []}
    ).json()["tags"] == []
    # tags を送らなければ触らない
    upload_again = env.client.patch(
        f"/api/library/{item['id']}", json={"name": "別名"}
    ).json()
    assert upload_again["tags"] == []


def test_known_tags_are_listed_for_completion(env):
    upload(env, "image", "a.png", tags="背景,夜景")
    upload(env, "audio", "b.mp3", tags="BGM")
    # 表記ゆれは最初に見たものへ寄せる
    upload(env, "image", "c.png", tags="はいけい,背景")
    assert page(env)["tags"] == sorted(
        ["背景", "夜景", "BGM", "はいけい"], key=str.casefold
    )


# --------------------------------------------------------------------------
# 検索・ページング
# --------------------------------------------------------------------------

def test_q_matches_the_name_and_the_tags(env):
    named = upload(env, "image", "sakura_pose.png").json()
    tagged = upload(env, "image", "other.png", tags="サクラ,立ち絵").json()
    upload(env, "image", "unrelated.png")

    assert set(ids(items(env, q="sakura"))) == {named["id"]}
    # 大文字小文字は無視する
    assert set(ids(items(env, q="SAKURA"))) == {named["id"]}
    assert set(ids(items(env, q="サクラ"))) == {tagged["id"]}
    assert set(ids(items(env, q="ポーズ"))) == set()


def test_tag_filter_is_an_exact_match(env):
    exact = upload(env, "image", "a.png", tags="背景").json()
    upload(env, "image", "b.png", tags="背景画").json()
    assert ids(items(env, tag="背景")) == [exact["id"]]
    # 部分一致で欲しいときは q を使う
    assert len(items(env, q="背景")) == 2


def test_filters_combine_with_kind(env):
    picture = upload(env, "image", "a.png", tags="夜").json()
    upload(env, "audio", "b.mp3", tags="夜").json()
    assert ids(items(env, tag="夜", kind="image")) == [picture["id"]]


def test_limit_and_offset_page_through_the_result(env):
    created = [upload(env, "image", f"a{i}.png").json()["id"] for i in range(5)]
    newest_first = list(reversed(created))

    first = page(env, limit=2)
    assert ids(first["items"]) == newest_first[:2]
    assert (first["total"], first["limit"], first["offset"]) == (5, 2, 0)

    second = page(env, limit=2, offset=2)
    assert ids(second["items"]) == newest_first[2:4]
    assert second["total"] == 5

    last = page(env, limit=2, offset=4)
    assert ids(last["items"]) == newest_first[4:]
    # 行き過ぎても total はそのまま（「もう無い」と分かる）
    assert page(env, limit=2, offset=99)["items"] == []
    assert page(env, limit=2, offset=99)["total"] == 5


def test_total_counts_the_filtered_result_not_the_whole_library(env):
    upload(env, "image", "keep_me.png")
    for i in range(3):
        upload(env, "image", f"other{i}.png")
    result = page(env, q="keep", limit=1)
    assert result["total"] == 1
    assert len(result["items"]) == 1


def test_bad_paging_parameters_are_rejected(env):
    assert env.client.get("/api/library", params={"limit": 0}).status_code == 422
    assert env.client.get("/api/library", params={"limit": 999}).status_code == 422
    assert env.client.get("/api/library", params={"offset": -1}).status_code == 422


def test_default_limit_caps_one_page(env):
    for i in range(library.DEFAULT_LIMIT + 3):
        upload(env, "image", f"a{i}.png")
    result = page(env)
    assert len(result["items"]) == library.DEFAULT_LIMIT
    assert result["total"] == library.DEFAULT_LIMIT + 3


def test_normalize_tags_accepts_strings_and_lists(env):
    assert library.normalize_tags(" a , b ,, a ") == ["a", "b"]
    assert library.normalize_tags(["a", "A", " ", "b"]) == ["a", "b"]
    assert library.normalize_tags(None) == []
    assert library.normalize_tags(123) == []
    assert len(library.normalize_tags(["x" * 200])[0]) == library.MAX_TAG_LENGTH


# --------------------------------------------------------------------------
# 重複登録の検知
# --------------------------------------------------------------------------

def test_registering_the_same_output_twice_is_409(env):
    make_job(env, image_path="still.png")
    first = env.client.post(
        "/api/library/from-job", json={"job_id": "job1", "source": "image"}
    ).json()

    again = env.client.post(
        "/api/library/from-job", json={"job_id": "job1", "source": "image"}
    )
    assert again.status_code == 409
    detail = again.json()["detail"]
    assert "登録済み" in detail["message"]
    # 既存アイテムを添えて返すので、画面はそのまま案内できる
    assert detail["item"]["id"] == first["id"]
    assert detail["item"]["name"] == first["name"]
    # コピーは増えない
    assert len(items(env)) == 1
    assert len(list((env.library / "image").iterdir())) == 1


def test_the_other_outputs_of_the_same_job_are_not_duplicates(env):
    """source_job_id だけでは生成画像とラストフレームを区別できない（§7.2）。"""
    make_job(env, image_path="still.png", last_frame_path="last.png")
    first = env.client.post(
        "/api/library/from-job", json={"job_id": "job1", "source": "image"}
    ).json()
    second = env.client.post(
        "/api/library/from-job", json={"job_id": "job1", "source": "last_frame"}
    )
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first["id"]
    assert {row["source"] for row in items(env)} == {"image", "last_frame"}


def test_the_same_output_of_another_job_is_not_a_duplicate(env):
    make_job(env, "job1", image_path="still.png")
    make_job(env, "job2", image_path="still.png")
    for job_id in ("job1", "job2"):
        created = env.client.post(
            "/api/library/from-job", json={"job_id": job_id, "source": "image"}
        )
        assert created.status_code == 201, created.text
    assert len(items(env)) == 2


def test_uploads_are_never_duplicates(env):
    """アップロードは source が無いので、同じ名前でも別物として増える。"""
    upload(env, "image", "ref.png")
    upload(env, "image", "ref.png")
    rows = items(env)
    assert len(rows) == 2
    assert all(row["source"] is None for row in rows)


def test_old_rows_without_a_source_do_not_block_registration(env):
    """source カラムを足す前の行（NULL）は重複判定の対象外（§7.2）。"""
    make_job(env, image_path="still.png")
    item = env.client.post(
        "/api/library/from-job", json={"job_id": "job1", "source": "image"}
    ).json()

    async def clear_source():
        async with db.get_db() as conn:
            await conn.execute("UPDATE library SET source = NULL WHERE id = ?", (item["id"],))
            await conn.commit()

    asyncio.run(clear_source())
    assert items(env)[0]["source"] is None
    # 旧行は「どの出力か不明」なので新規登録を妨げない
    again = env.client.post(
        "/api/library/from-job", json={"job_id": "job1", "source": "image"}
    )
    assert again.status_code == 201, again.text
    assert len(items(env)) == 2


def test_a_duplicate_can_be_registered_again_after_deleting_it(env):
    make_job(env, image_path="still.png")
    item = env.client.post(
        "/api/library/from-job", json={"job_id": "job1", "source": "image"}
    ).json()
    env.client.delete(f"/api/library/{item['id']}")
    assert (
        env.client.post(
            "/api/library/from-job", json={"job_id": "job1", "source": "image"}
        ).status_code
        == 201
    )
