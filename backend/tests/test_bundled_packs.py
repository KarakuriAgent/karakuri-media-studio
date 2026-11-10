"""同梱の素材パック（``asset-packs/``）: 目録の中身・一覧・入れる・改名したパックの扱い。

取得は :mod:`tests.test_asset_packs` と同じく差し替えるので、ネットワークには出ない。
"""

import asyncio
import json
import re
from pathlib import Path

import pytest

from app import asset_packs, bundled_packs

from tests.test_asset_packs import ROBOTS, call, env, fake_http, manifest, wait_import  # noqa: F401

REPO = Path(__file__).resolve().parents[2]
BUNDLED = REPO / "asset-packs"

#: 同梱の目録（id -> (件数, 前の id)）
EXPECTED = {
    "shorts-sfx-komori": (80, ["kanon-shorts-sfx"]),
    "shorts-bgm-amacha": (37, ["amacha-shorts-bgm"]),
    "manga-symbols-ja": (137, ["kanon-shorts-manga-symbols"]),
}


# --------------------------------------------------------------------------
# リポジトリに同梱している目録
# --------------------------------------------------------------------------

def test_the_bundled_manifests_parse():
    found = {manifest.pack: manifest for _path, manifest in bundled_packs.manifests()}
    assert set(found) == set(EXPECTED)
    for pack, (count, aliases) in EXPECTED.items():
        parsed = found[pack]
        assert len(parsed.items) == count
        assert parsed.aliases == aliases
        assert parsed.name and parsed.description
        used = {item.source for item in parsed.items}
        for source in parsed.sources:
            # 入れる前に見せる情報がそろっている（素材の無い入手元は載せない）
            assert source.id in used
            assert source.terms_url.startswith("https://")
            assert source.terms_summary
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", source.checked_at)
            assert source.acquire == "auto"
            assert source.credit_required is False
        for item in parsed.items:
            assert item.download_url.startswith("https://")


def test_only_manifests_are_bundled_never_the_material_files():
    """素材のファイルは同梱しない（どの入手元も再配布を禁止している）。"""
    allowed = {".yaml", ".yml", ".json", ".md"}
    files = [path for path in BUNDLED.rglob("*") if path.is_file()]
    assert files
    assert all(path.suffix.lower() in allowed for path in files), files
    # 不採用の記録は同梱しない（採用した物だけ）
    for path in files:
        if path.suffix.lower() in (".yaml", ".yml"):
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"^rejected", text, re.MULTILINE), path


def test_the_bundled_manifests_do_not_lean_on_a_particular_character():
    words = ("花音", "KB-001", "クレア", "トンプソン", "kanon")
    for _path, parsed in bundled_packs.manifests():
        texts = [parsed.pack, parsed.name, parsed.description]
        for source in parsed.sources:
            texts += [source.name, source.terms_summary, source.notes, source.content_id]
        for item in parsed.items:
            texts += [item.file, item.name, item.description, item.category, *item.tags]
        for text in texts:
            assert not any(word.casefold() in text.casefold() for word in words), text


# --------------------------------------------------------------------------
# 一覧と入れる（テスト用の同梱フォルダ）
# --------------------------------------------------------------------------

OLD = "old-pack"
NEW = "new-pack"


def _bundle(tmp_path: Path, monkeypatch) -> Path:
    folder = tmp_path / "asset-packs"
    folder.mkdir()
    body = manifest(pack=NEW, aliases=[OLD], name="新しいパック")
    body["items"] = body["items"][:1] + body["items"][3:4]  # hit.wav と page.wav（auto だけ）
    body["sources"] = body["sources"][:1]
    (folder / "new-pack.json").write_text(json.dumps(body), encoding="utf-8")
    (folder / "README.md").write_text("目録の説明", encoding="utf-8")
    monkeypatch.setattr(bundled_packs, "BUNDLED_PACKS_DIR", folder)
    return folder


def test_bundled_packs_are_listed_with_terms_and_counts(env, monkeypatch):
    _bundle(env.tmp, monkeypatch)
    response = call(env, "GET", "/api/v1/library/packs/bundled")
    assert response.status_code == 200, response.text
    [pack] = response.json()
    assert pack["id"] == NEW and pack["aliases"] == [OLD]
    assert pack["total"] == 2 and pack["installed"] == 0
    assert pack["categories"] == ["impact"]
    assert pack["kinds"] == {"audio": 2}
    assert pack["file"].endswith("new-pack.json")
    assert "規約" in pack["notice"]
    [source] = pack["sources"]
    assert source["id"] == "auto-site"
    assert source["terms_url"] == "https://auto.example.com/terms"
    assert source["checked_at"] == "2026-11-10"
    assert source["total"] == 2 and source["installed"] == 0
    # 内部 API も同じ
    assert env.client.get("/api/library/packs/bundled").json() == response.json()
    # 前の id でも引ける
    assert call(env, "GET", f"/api/v1/library/packs/bundled/{OLD}").json()["id"] == NEW
    assert call(env, "GET", "/api/v1/library/packs/bundled/nope").status_code == 404


def test_installing_without_consent_is_refused(env, monkeypatch):
    _bundle(env.tmp, monkeypatch)
    seen = fake_http(monkeypatch, {})
    url = f"/api/v1/library/packs/bundled/{NEW}/install"
    response = call(env, "POST", url, json={"accept_sources": []})
    assert response.status_code == 400
    assert "同意" in response.json()["detail"]
    response = call(env, "POST", url, json={"accept_sources": ["someone-else"]})
    assert response.status_code == 400
    assert "someone-else" in response.json()["detail"]
    assert call(env, "POST", "/api/v1/library/packs/bundled/nope/install",
                json={"accept_sources": ["auto-site"]}).status_code == 404
    assert seen == []
    assert call(env, "GET", "/api/v1/library/packs/imports").json() == []


def test_a_dry_run_install_touches_nothing(env, monkeypatch):
    _bundle(env.tmp, monkeypatch)
    seen = fake_http(monkeypatch, {})
    response = call(env, "POST", f"/api/v1/library/packs/bundled/{NEW}/install",
                    json={"accept_sources": ["auto-site"], "dry_run": True})
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["status"] == "done" and result["dry_run"] is True
    assert result["pack"] == NEW
    assert {entry["file"] for entry in result["imported"]} == {"hit.wav", "page.wav"}
    assert seen == []
    assert call(env, "GET", "/api/v1/library").json()["total"] == 0


def test_install_fetches_and_reports_through_the_import_records(env, monkeypatch):
    _bundle(env.tmp, monkeypatch)
    fake_http(monkeypatch, {
        "https://auto.example.com/robots.txt": ROBOTS,
        "https://auto.example.com/files/hit.wav": (200, "audio/wav", b"RIFFhit"),
        "https://auto.example.com/files/page.wav": (200, "text/html", b"<html>"),
    })
    # 内部 API（人の画面）から入れる
    response = env.client.post(f"/api/library/packs/bundled/{NEW}/install",
                               json={"accept_sources": ["auto-site"]})
    assert response.status_code == 202, response.text
    result = wait_import(env, response.json()["id"])
    assert result["status"] == "done", result
    assert [entry["file"] for entry in result["imported"]] == ["hit.wav"]
    assert [entry["file"] for entry in result["failed"]] == ["page.wav"]
    [pack] = call(env, "GET", "/api/v1/library/packs/bundled").json()
    assert pack["installed"] == 1
    [item] = call(env, "GET", "/api/v1/library").json()["items"]
    assert item["license"]["pack"] == NEW
    assert item["license"]["terms_url"] == "https://auto.example.com/terms"


def _item(env, item_id: str) -> dict:
    items = call(env, "GET", "/api/v1/library?limit=50").json()["items"]
    return next(item for item in items if item["id"] == item_id)


def _import_under_the_old_id(env, monkeypatch) -> str:
    """前の id（目録の改名前）で hit.wav を取り込んで、その棚の id を返す。"""
    fake_http(monkeypatch, {
        "https://auto.example.com/robots.txt": ROBOTS,
        "https://auto.example.com/files/hit.wav": (200, "audio/wav", b"RIFFhit"),
    })
    body = manifest(pack=OLD)
    body["items"] = body["items"][:1]
    body["sources"] = body["sources"][:1]
    result = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": body, "accept_sources": ["auto-site"],
    }).json()["id"])
    assert result["status"] == "done", result
    [item] = call(env, "GET", "/api/v1/library").json()["items"]
    assert item["license"]["pack"] == OLD and OLD in item["tags"]
    return item["id"]


def test_items_installed_under_an_old_id_count_and_are_not_fetched_again(env, monkeypatch):
    _bundle(env.tmp, monkeypatch)
    item_id = _import_under_the_old_id(env, monkeypatch)

    [pack] = call(env, "GET", "/api/v1/library/packs/bundled").json()
    assert pack["installed"] == 1  # 前の id で入っている物も入れ済み

    # dry_run では記録を書き換えない
    seen = fake_http(monkeypatch, {
        "https://auto.example.com/robots.txt": ROBOTS,
        "https://auto.example.com/files/page.wav": (404, "text/plain", b""),
    })
    dry = call(env, "POST", f"/api/v1/library/packs/bundled/{NEW}/install",
               json={"accept_sources": ["auto-site"], "dry_run": True}).json()
    assert [entry["file"] for entry in dry["skipped"]] == ["hit.wav"]
    item = _item(env, item_id)
    assert item["license"]["pack"] == OLD

    result = wait_import(env, call(
        env, "POST", f"/api/v1/library/packs/bundled/{NEW}/install",
        json={"accept_sources": ["auto-site"]},
    ).json()["id"])
    assert result["status"] == "done", result
    assert [(e["file"], e["library_id"]) for e in result["skipped"]] == [("hit.wav", item_id)]
    assert "https://auto.example.com/files/hit.wav" not in seen
    # 入れるときに、前の id の記録とタグを今の id に揃える
    item = _item(env, item_id)
    assert item["license"]["pack"] == NEW
    assert NEW in item["tags"] and OLD not in item["tags"]
    assert call(env, "GET", "/api/v1/library").json()["total"] == 1


def test_startup_adopts_the_old_ids_of_bundled_packs(env, monkeypatch):
    _bundle(env.tmp, monkeypatch)
    item_id = _import_under_the_old_id(env, monkeypatch)
    assert asyncio.run(bundled_packs.adopt_all_aliases()) == 1
    assert asyncio.run(bundled_packs.adopt_all_aliases()) == 0  # 何度呼んでも同じ
    item = _item(env, item_id)
    assert item["license"]["pack"] == NEW
    assert NEW in item["tags"] and OLD not in item["tags"]


@pytest.mark.parametrize("aliases", [["../evil"], ["ok", "bad alias"]])
def test_broken_aliases_are_refused(aliases):
    with pytest.raises(asset_packs.AssetPackError, match="aliases"):
        asset_packs.parse_manifest(json.dumps(manifest(aliases=aliases)))


def test_an_alias_equal_to_the_pack_is_dropped():
    parsed = asset_packs.parse_manifest(
        json.dumps(manifest(aliases=["test-pack", "older", "older"]))
    )
    assert parsed.aliases == ["older"]
