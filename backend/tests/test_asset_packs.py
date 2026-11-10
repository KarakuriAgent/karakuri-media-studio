"""素材パック（目録・取り込み）・ライブラリのライセンス情報・書き出しの素材一覧。

取得（:func:`app.asset_packs.http_get`）と待ち（``sleep``）は差し替えるので、
ネットワークには出ない。測定（:mod:`app.media_measure`）も既定は差し替え、実際の
ffmpeg / Pillow で測るテストは ffmpeg があるときだけ走らせる。
"""

import asyncio
import hashlib
import json
import shutil
import subprocess
import time
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import archives, asset_packs, db, jobs, library, media_measure
from app import timeline as timeline_service
from app.main import app
from app.models import LibraryMediaInfo

KEY = "test-key-asset-packs"
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "asset-pack.example.yaml"


@pytest.fixture
def env(tmp_path, monkeypatch):
    lib = tmp_path / "library"
    lib.mkdir()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(library, "LIBRARY_DIR", lib)
    monkeypatch.setattr(jobs, "LIBRARY_DIR", lib)
    monkeypatch.setattr(asset_packs, "PACKS_DIR", tmp_path / "packs")
    monkeypatch.setattr(timeline_service, "OUTPUTS_DIR", tmp_path / "outputs")

    async def fake_measure(path, kind):
        return LibraryMediaInfo(duration_ms=1000 if kind == "audio" else None)

    monkeypatch.setattr(media_measure, "measure", fake_measure)
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asset_packs, "sleep", fake_sleep)
    with TestClient(app) as client:
        response = client.put("/api/settings", json={"external_api_key": KEY})
        assert response.status_code == 200
        yield type("Env", (), {"client": client, "tmp": tmp_path, "slept": slept,
                               "library": lib})


def call(env, method, path, **kwargs):
    return env.client.request(method, path, headers={"X-API-Key": KEY}, **kwargs)


def wait_import(env, import_id, timeout=10.0) -> dict:
    deadline = time.time() + timeout
    body: dict = {}
    while time.time() < deadline:
        body = call(env, "GET", f"/api/v1/library/packs/imports/{import_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"import stuck in {body.get('status')}")


def manifest(**overrides) -> dict:
    body = {
        "pack": "test-pack",
        "sources": [
            {"id": "auto-site", "name": "Auto Site", "url": "https://auto.example.com/",
             "terms_url": "https://auto.example.com/terms", "checked_at": "2026-11-10",
             "credit_required": False, "acquire": "auto"},
            {"id": "hand-site", "name": "Hand Site", "url": "https://hand.example.org/",
             "acquire": "manual"},
        ],
        "items": [
            {"file": "hit.wav", "source": "auto-site", "category": "impact",
             "tags": ["打撃"], "download_url": "https://auto.example.com/files/hit.wav",
             "page_url": "https://auto.example.com/sounds/hit"},
            {"file": "secret.wav", "source": "auto-site",
             "download_url": "https://auto.example.com/private/secret.wav"},
            {"file": "gone.wav", "source": "auto-site",
             "download_url": "https://auto.example.com/files/gone.wav"},
            {"file": "page.wav", "source": "auto-site",
             "download_url": "https://auto.example.com/files/page.wav"},
            {"file": "loop.mp3", "source": "hand-site", "category": "bgm",
             "name": "ループ"},
            {"file": "icon.png", "source": "hand-site"},
        ],
    }
    body.update(overrides)
    return body


def fake_http(monkeypatch, served: dict[str, tuple[int, str, bytes]]) -> list[str]:
    seen: list[str] = []

    async def http_get(url):
        seen.append(url)
        return served.get(url, (404, "text/plain", b""))

    monkeypatch.setattr(asset_packs, "http_get", http_get)
    return seen


ROBOTS = (200, "text/plain", b"User-agent: *\nDisallow: /private/\n")


# --------------------------------------------------------------------------
# 目録
# --------------------------------------------------------------------------

def test_the_example_manifest_parses():
    parsed = asset_packs.parse_manifest(EXAMPLE.read_text(encoding="utf-8"))
    assert parsed.pack == "example-sfx-pack"
    assert [source.acquire for source in parsed.sources] == ["auto", "manual"]
    assert len(parsed.items) == 7
    assert [item.member for item in parsed.items][3] == "effects/lines/speed_lines_01.png"
    assert parsed.items[5].svg_size == 1024
    assert parsed.items[3].file == "speed_lines_01.png"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"pack": "../evil"}, "pack は"),
        ({"items": [{"file": "a/b.wav", "source": "auto-site"}]}, "ファイル名だけ"),
        ({"items": [{"file": "a.wav", "source": "nope"}]}, "知らない入手元"),
        ({"items": [{"file": "a.exe", "source": "auto-site"}]}, ""),
        ({"items": [{"file": "a.wav", "source": "auto-site", "sha256": "xyz"}]}, "sha256"),
    ],
)
def test_a_broken_manifest_is_refused(change, message):
    with pytest.raises(asset_packs.AssetPackError, match=message):
        asset_packs.parse_manifest(json.dumps(manifest(**change)))


# --------------------------------------------------------------------------
# 取り込み
# --------------------------------------------------------------------------

def test_import_fetches_politely_and_places_by_hand(env, monkeypatch):
    seen = fake_http(monkeypatch, {
        "https://auto.example.com/robots.txt": ROBOTS,
        "https://auto.example.com/files/hit.wav": (200, "audio/wav", b"RIFFhit"),
        "https://auto.example.com/files/page.wav": (200, "text/html; charset=utf-8", b"<html>"),
    })
    inbox = env.tmp / "packs" / "test-pack" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "loop.mp3").write_bytes(b"ID3loop")
    # icon.png は無い -> missing

    response = call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": manifest(), "accept_sources": ["auto-site", "hand-site"],
    })
    assert response.status_code == 202, response.text
    result = wait_import(env, response.json()["id"])
    assert result["status"] == "done", result
    assert sorted(entry["file"] for entry in result["imported"]) == ["hit.wav", "loop.mp3"]
    assert [entry["file"] for entry in result["blocked"]] == ["secret.wav"]
    assert {entry["file"] for entry in result["missing"]} == {"gone.wav", "icon.png"}
    assert [entry["file"] for entry in result["failed"]] == ["page.wav"]
    assert "HTML" in result["failed"][0]["reason"]
    assert result["processed"] == result["total"] == 6

    # robots.txt は 1 回だけ、禁止された URL は取りに行かない
    assert seen.count("https://auto.example.com/robots.txt") == 1
    assert "https://auto.example.com/private/secret.wav" not in seen
    # 同じ入手元への 2 回目以降は間隔をあける（既定 2.5 秒の範囲で待つ）
    assert env.slept and all(0 < value <= 2.5 for value in env.slept)

    items = call(env, "GET", "/api/v1/library?limit=50").json()["items"]
    hit = next(item for item in items if item["name"] == "hit.wav")
    assert hit["kind"] == "audio"
    assert set(hit["tags"]) >= {"打撃", "impact", "test-pack"}
    assert hit["license"]["provider"] == "Auto Site"
    assert hit["license"]["page_url"] == "https://auto.example.com/sounds/hit"
    assert hit["license"]["terms_url"] == "https://auto.example.com/terms"
    assert hit["license"]["checked_at"] == "2026-11-10"
    assert hit["license"]["credit_required"] is False
    assert hit["license"]["sha256"] == hashlib.sha256(b"RIFFhit").hexdigest()
    assert hit["media_info"]["duration_ms"] == 1000
    loop = next(item for item in items if item["name"] == "ループ")
    assert loop["license"]["source_id"] == "hand-site"
    assert Path(loop["path"]).read_bytes() == b"ID3loop"


def test_running_twice_skips_what_is_registered_and_reports_changes(env, monkeypatch):
    seen = fake_http(monkeypatch, {
        "https://auto.example.com/robots.txt": ROBOTS,
        "https://auto.example.com/files/hit.wav": (200, "audio/wav", b"RIFFhit"),
    })
    inbox = env.tmp / "packs" / "test-pack" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "loop.mp3").write_bytes(b"ID3loop")
    body = {
        "manifest": manifest(items=manifest()["items"][:1] + manifest()["items"][4:5]),
        "accept_sources": ["auto-site", "hand-site"],
    }
    first = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json=body).json()["id"])
    assert len(first["imported"]) == 2
    before = len(seen)

    second = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json=body).json()["id"])
    assert second["imported"] == []
    assert sorted(entry["reason"] for entry in second["skipped"]) == ["登録済み", "登録済み"]
    # 登録済みの自動の素材は取りに行かない
    assert len(seen) == before

    (inbox / "loop.mp3").write_bytes(b"ID3loop-v2")
    third = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json=body).json()["id"])
    assert [entry["file"] for entry in third["changed"]] == ["loop.mp3"]
    assert len(call(env, "GET", "/api/v1/library?limit=50").json()["items"]) == 2


def test_a_deleted_pack_item_is_fetched_again_unless_dropped_from_the_manifest(
    env, monkeypatch
):
    """消した素材は「未登録」に戻る: 目録に残せば取り直し、外せば取りに行かない。"""
    seen = fake_http(monkeypatch, {
        "https://auto.example.com/robots.txt": ROBOTS,
        "https://auto.example.com/files/hit.wav": (200, "audio/wav", b"RIFFhit"),
    })
    body = {"manifest": manifest(items=manifest()["items"][:1]),
            "accept_sources": ["auto-site"]}
    first = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json=body).json()["id"])
    item_id = first["imported"][0]["library_id"]

    assert call(env, "DELETE", f"/api/v1/library/{item_id}").status_code == 204
    fetched = len(seen)
    again = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json=body).json()["id"])
    assert [entry["file"] for entry in again["imported"]] == ["hit.wav"]
    assert len(seen) > fetched
    # 取り込みの記録は書き換えない（消した項目の id のまま）
    old = call(env, "GET", f"/api/v1/library/packs/imports/{first['id']}").json()
    assert old["imported"][0]["library_id"] == item_id

    # 不採用にするなら目録から外す: もう取りに行かない
    new_id_ = again["imported"][0]["library_id"]
    assert call(env, "DELETE", f"/api/v1/library/{new_id_}").status_code == 204
    fetched = len(seen)
    dropped = {"manifest": manifest(items=manifest()["items"][1:2]),
               "accept_sources": ["auto-site"]}
    wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json=dropped).json()["id"])
    assert "https://auto.example.com/files/hit.wav" not in seen[fetched:]


def test_sources_without_consent_are_not_touched(env, monkeypatch):
    seen = fake_http(monkeypatch, {})
    result = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": manifest(), "accept_sources": ["hand-site"],
    }).json()["id"])
    assert result["unaccepted_sources"] == ["auto-site"]
    assert {entry["file"] for entry in result["skipped"]} == {
        "hit.wav", "secret.wav", "gone.wav", "page.wav"
    }
    assert seen == []


def test_a_hand_placed_file_is_matched_by_sha256(env, monkeypatch):
    fake_http(monkeypatch, {})
    data = b"PNG-bytes"
    inbox = env.tmp / "my-inbox"
    inbox.mkdir()
    (inbox / "downloaded (1).png").write_bytes(data)
    items = [{"file": "icon.png", "source": "hand-site",
              "sha256": hashlib.sha256(data).hexdigest()}]
    result = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": manifest(items=items), "accept_sources": ["hand-site"],
        "inbox_dir": str(inbox),
    }).json()["id"])
    assert [entry["file"] for entry in result["imported"]] == ["icon.png"]
    assert result["inbox_dir"] == str(inbox)


def test_a_sha256_mismatch_is_not_registered(env, monkeypatch):
    fake_http(monkeypatch, {
        "https://auto.example.com/robots.txt": (404, "text/plain", b""),
        "https://auto.example.com/files/hit.wav": (200, "audio/wav", b"RIFFhit"),
    })
    items = [dict(manifest()["items"][0], sha256="0" * 64)]
    result = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": manifest(items=items), "accept_sources": ["auto-site"],
    }).json()["id"])
    assert [entry["reason"] for entry in result["failed"]] == ["sha256 が目録と違います"]
    assert call(env, "GET", "/api/v1/library").json()["total"] == 0


def test_a_robots_error_blocks_the_whole_site(env, monkeypatch):
    fake_http(monkeypatch, {"https://auto.example.com/robots.txt": (503, "text/plain", b"")})
    result = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": manifest(items=manifest()["items"][:1]), "accept_sources": ["auto-site"],
    }).json()["id"])
    assert [entry["file"] for entry in result["blocked"]] == ["hit.wav"]


def test_dry_run_touches_nothing(env, monkeypatch):
    seen = fake_http(monkeypatch, {})
    response = call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest_text": json.dumps(manifest()),
        "accept_sources": ["auto-site", "hand-site"], "dry_run": True,
    })
    assert response.status_code == 202
    result = response.json()
    assert result["status"] == "done" and result["dry_run"] is True
    assert {entry["file"] for entry in result["imported"]} == {
        "hit.wav", "secret.wav", "gone.wav", "page.wav"
    }
    assert seen == []
    assert call(env, "GET", "/api/v1/library").json()["total"] == 0


def test_the_manifest_must_be_given_once(env):
    both = call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": manifest(), "manifest_text": "{}",
    })
    assert both.status_code == 400
    nowhere = call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest_path": "/no/such/manifest.yaml",
    })
    assert nowhere.status_code == 400
    assert call(env, "GET", "/api/v1/library/packs/imports/nope").status_code == 404


def test_manifest_path_reads_yaml(env, monkeypatch):
    fake_http(monkeypatch, {})
    response = call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest_path": str(EXAMPLE), "dry_run": True, "accept_sources": ["example-sfx"],
    })
    assert response.status_code == 202, response.text
    assert response.json()["pack"] == "example-sfx-pack"
    listed = call(env, "GET", "/api/v1/library/packs/imports").json()
    assert listed[0]["id"] == response.json()["id"]


# --------------------------------------------------------------------------
# アーカイブ（ZIP）の中の素材
# --------------------------------------------------------------------------

def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


ZIP_URL = "https://auto.example.com/packs/marks.zip"
ZIP_FILES = {
    "marks/lines/speed.png": b"PNG-speed",
    "marks/lines/focus.png": b"PNG-focus",
    "marks/sfx/pop.wav": b"RIFFpop",
    "readme.txt": b"hello",
}


def zip_manifest(**overrides) -> dict:
    items = [
        {"member": "marks/lines/speed.png", "source": "auto-site",
         "download_url": ZIP_URL, "category": "manga",
         "sha256": hashlib.sha256(ZIP_FILES["marks/lines/speed.png"]).hexdigest()},
        {"member": "marks/lines/focus.png", "file": "focus-lines.png",
         "source": "auto-site", "download_url": ZIP_URL},
        {"member": "marks/sfx/pop.wav", "source": "auto-site", "download_url": ZIP_URL},
        {"member": "marks/nothing.png", "source": "auto-site", "download_url": ZIP_URL},
    ]
    return manifest(items=overrides.pop("items", items), **overrides)


def test_items_inside_one_zip_are_fetched_once(env, monkeypatch):
    seen = fake_http(monkeypatch, {
        "https://auto.example.com/robots.txt": ROBOTS,
        ZIP_URL: (200, "application/zip", make_zip(ZIP_FILES)),
    })
    result = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": zip_manifest(), "accept_sources": ["auto-site"],
    }).json()["id"])
    assert result["status"] == "done", result
    assert sorted(entry["file"] for entry in result["imported"]) == [
        "focus-lines.png", "pop.wav", "speed.png",
    ]
    assert [entry["member"] for entry in result["failed"]] == ["marks/nothing.png"]
    assert "ZIP の中にありません" in result["failed"][0]["reason"]
    # 同じ ZIP は 1 回の取り込みで 1 度だけ
    assert seen.count(ZIP_URL) == 1
    assert seen.count("https://auto.example.com/robots.txt") == 1

    items = call(env, "GET", "/api/v1/library?limit=50").json()["items"]
    speed = next(item for item in items if item["name"] == "speed.png")
    assert speed["kind"] == "image"
    assert Path(speed["path"]).read_bytes() == b"PNG-speed"
    assert speed["license"]["download_url"] == ZIP_URL
    assert speed["license"]["member"] == "marks/lines/speed.png"
    assert speed["license"]["sha256"] == hashlib.sha256(b"PNG-speed").hexdigest()
    pop = next(item for item in items if item["name"] == "pop.wav")
    assert pop["kind"] == "audio"

    # もう一度: 登録済みは飛ばし、ZIP も取りに行かない
    again = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": zip_manifest(items=zip_manifest()["items"][:3]),
        "accept_sources": ["auto-site"],
    }).json()["id"])
    assert again["imported"] == [] and len(again["skipped"]) == 3
    assert seen.count(ZIP_URL) == 1


def test_a_member_sha256_mismatch_is_not_registered(env, monkeypatch):
    fake_http(monkeypatch, {
        "https://auto.example.com/robots.txt": ROBOTS,
        ZIP_URL: (200, "application/zip", make_zip(ZIP_FILES)),
    })
    items = [dict(zip_manifest()["items"][0], sha256="0" * 64)]
    result = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": zip_manifest(items=items), "accept_sources": ["auto-site"],
    }).json()["id"])
    assert [entry["reason"] for entry in result["failed"]] == ["sha256 が目録と違います"]


def test_a_non_zip_download_fails_every_member(env, monkeypatch):
    seen = fake_http(monkeypatch, {
        "https://auto.example.com/robots.txt": ROBOTS,
        ZIP_URL: (200, "application/octet-stream", b"not a zip at all"),
    })
    result = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": zip_manifest(), "accept_sources": ["auto-site"],
    }).json()["id"])
    assert len(result["failed"]) == 4
    assert all("ZIP ではありません" in entry["reason"] for entry in result["failed"])
    assert seen.count(ZIP_URL) == 1


def test_a_zip_placed_in_the_inbox_is_opened(env, monkeypatch):
    fake_http(monkeypatch, {})
    inbox = env.tmp / "packs" / "test-pack" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "marks.zip").write_bytes(make_zip(ZIP_FILES))
    items = [
        {"member": "marks/lines/speed.png", "archive": "marks.zip", "source": "hand-site"},
        {"member": "marks/sfx/pop.wav", "archive": "other.zip", "source": "hand-site"},
    ]
    result = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": manifest(items=items), "accept_sources": ["hand-site"],
    }).json()["id"])
    assert [entry["file"] for entry in result["imported"]] == ["speed.png"]
    assert [entry["file"] for entry in result["missing"]] == ["pop.wav"]
    assert "other.zip" in result["missing"][0]["reason"]

    # 中身が変わっていれば changed（取り込み直さない）
    (inbox / "marks.zip").write_bytes(make_zip({**ZIP_FILES, "marks/lines/speed.png": b"v2"}))
    again = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": manifest(items=items[:1]), "accept_sources": ["hand-site"],
    }).json()["id"])
    assert [entry["file"] for entry in again["changed"]] == ["speed.png"]


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({"member": "../evil.png", "source": "auto-site"}, "member が使えません"),
        ({"member": "/etc/evil.png", "source": "auto-site"}, "絶対パス"),
        ({"member": "a\\b.png", "source": "auto-site"}, "バックスラッシュ"),
        ({"member": "C:/x.png", "source": "auto-site"}, "絶対パス"),
        ({"member": "dir/", "source": "auto-site"}, ""),
        ({"member": "a.png", "source": "hand-site"}, "archive"),
        ({"file": "a.png", "archive": "x.zip", "source": "hand-site"}, "member"),
        ({"member": "a.png", "archive": "../x.zip", "source": "hand-site"}, "archive"),
    ],
)
def test_a_broken_archive_item_is_refused(item, message):
    with pytest.raises(asset_packs.AssetPackError, match=message):
        asset_packs.parse_manifest(json.dumps(manifest(items=[item])))


def test_list_archive_obeys_robots_and_feeds_the_import(env, monkeypatch):
    unsafe = make_zip({**ZIP_FILES, "../escape.png": b"x", "big.bin": b"1"})
    seen = fake_http(monkeypatch, {
        "https://auto.example.com/robots.txt": ROBOTS,
        ZIP_URL: (200, "application/zip", unsafe),
    })
    response = call(env, "POST", "/api/v1/library/packs/archive", json={"url": ZIP_URL})
    assert response.status_code == 200, response.text
    listing = response.json()
    assert listing["cached"] is False and listing["total"] == 6
    entries = {entry["path"]: entry for entry in listing["entries"]}
    assert entries["marks/lines/speed.png"]["kind"] == "image"
    assert entries["marks/lines/speed.png"]["usable"] is True
    assert entries["marks/lines/speed.png"]["size"] == len(b"PNG-speed")
    assert entries["../escape.png"]["usable"] is False
    assert entries["readme.txt"]["usable"] is False and entries["readme.txt"]["kind"] is None
    assert listing["sha256"] == hashlib.sha256(unsafe).hexdigest()

    # 2 回目と、そのあとの取り込みは取っておいた ZIP を使う
    again = call(env, "POST", "/api/v1/library/packs/archive", json={"url": ZIP_URL}).json()
    assert again["cached"] is True
    result = wait_import(env, call(env, "POST", "/api/v1/library/packs/import", json={
        "manifest": zip_manifest(items=zip_manifest()["items"][:1]),
        "accept_sources": ["auto-site"],
    }).json()["id"])
    assert [entry["file"] for entry in result["imported"]] == ["speed.png"]
    assert seen.count(ZIP_URL) == 1

    # robots.txt で禁止された ZIP は取りに行かない
    blocked = call(env, "POST", "/api/v1/library/packs/archive",
                   json={"url": "https://auto.example.com/private/x.zip"})
    assert blocked.status_code == 400 and "blocked" in blocked.json()["detail"]
    assert "https://auto.example.com/private/x.zip" not in seen

    # 受け取り口の ZIP（アプリから見えるパス）
    local = env.tmp / "local.zip"
    local.write_bytes(make_zip({"a/b.wav": b"RIFF"}))
    by_path = call(env, "POST", "/api/v1/library/packs/archive", json={"path": str(local)})
    assert [entry["path"] for entry in by_path.json()["entries"]] == ["a/b.wav"]
    both = call(env, "POST", "/api/v1/library/packs/archive",
                json={"url": ZIP_URL, "path": str(local)})
    assert both.status_code == 400


def test_archive_guards_against_bombs_and_too_many_entries(tmp_path, monkeypatch):
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zeros.wav", b"\0" * (20 * 1024 * 1024))
    with pytest.raises(archives.ArchiveError, match="圧縮率"):
        archives.read_member(bomb, "zeros.wav")
    listed = archives.list_entries(bomb)
    assert listed[0].usable is False

    small = tmp_path / "small.zip"
    small.write_bytes(make_zip({"a.wav": b"RIFF", "b.wav": b"RIFF"}))
    with pytest.raises(archives.ArchiveError, match="合計"):
        archives.read_member(small, "a.wav", budget=1)
    monkeypatch.setattr(archives, "MAX_ENTRIES", 1)
    with pytest.raises(archives.ArchiveError, match="多すぎ"):
        archives.list_entries(small)
    with pytest.raises(archives.ArchiveError, match="member が使えません"):
        archives.read_member(small, "../a.wav")


# --------------------------------------------------------------------------
# ライブラリのライセンス情報
# --------------------------------------------------------------------------

def test_license_can_be_set_on_upload_patched_and_cleared(env):
    license = {"provider": "Someone", "page_url": "https://example.net/a",
               "credit_required": False, "checked_at": "2026-11-10"}
    uploaded = call(
        env, "POST", "/api/v1/library/audio",
        files={"file": ("a.wav", b"RIFF", "audio/wav")},
        data={"license": json.dumps(license)},
    )
    assert uploaded.status_code == 201, uploaded.text
    item = uploaded.json()
    assert item["license"]["provider"] == "Someone"

    renamed = call(env, "PATCH", f"/api/v1/library/{item['id']}", json={"name": "A"}).json()
    assert renamed["license"]["provider"] == "Someone"  # 送らなければそのまま

    replaced = call(env, "PATCH", f"/api/v1/library/{item['id']}",
                    json={"license": {"provider": "Other"}}).json()
    assert replaced["license"]["provider"] == "Other"
    assert replaced["license"]["page_url"] == ""

    cleared = call(env, "PATCH", f"/api/v1/library/{item['id']}", json={"license": None}).json()
    assert cleared["license"] is None

    plain = call(env, "POST", "/api/v1/library/audio",
                 files={"file": ("b.wav", b"RIFF", "audio/wav")}).json()
    assert plain["license"] is None

    broken = call(env, "POST", "/api/v1/library/audio",
                  files={"file": ("c.wav", b"RIFF", "audio/wav")},
                  data={"license": "{not json"})
    assert broken.status_code == 400


# --------------------------------------------------------------------------
# 書き出しの素材一覧（監査用）
# --------------------------------------------------------------------------

def test_export_lists_the_library_materials_it_used(env, monkeypatch):
    client = env.client
    licensed = call(env, "POST", "/api/v1/library/audio",
                    files={"file": ("bgm.wav", b"RIFF", "audio/wav")},
                    data={"license": json.dumps({"provider": "BGM Site",
                                                 "terms_url": "https://example.net/t"})}).json()
    video = call(env, "POST", "/api/v1/library/upload",
                 files={"file": ("clip.mp4", b"x", "video/mp4")}).json()
    sprite = call(env, "POST", "/api/v1/library/image",
                  files={"file": ("star.png", b"png", "image/png")},
                  data={"license": json.dumps({"provider": "Icon Site"})}).json()
    unused = call(env, "POST", "/api/v1/library/audio",
                  files={"file": ("unused.wav", b"RIFF", "audio/wav")},
                  data={"license": json.dumps({"provider": "Nobody"})}).json()

    async def fake_probe(path):
        return 2000, True

    monkeypatch.setattr(timeline_service, "probe_media", fake_probe)
    project = client.post("/api/studio/projects", json={"name": "素材"}).json()["id"]
    timeline = client.post(f"/api/studio/projects/{project}/timelines", json={}).json()
    detail = client.post(
        f"/api/studio/timelines/{timeline['id']}/tracks", json={"kind": "audio"}
    ).json()
    v1, a1 = (track["id"] for track in detail["tracks"])
    response = client.put(f"/api/studio/timelines/{timeline['id']}/clips", json={"clips": [
        {"track_id": v1, "start_ms": 0, "duration_ms": 2000, "source_kind": "library",
         "source_id": video["id"], "in_ms": 0, "out_ms": 2000},
        {"track_id": a1, "start_ms": 0, "duration_ms": 2000, "source_kind": "library",
         "source_id": licensed["id"], "in_ms": 0, "out_ms": 2000},
    ]})
    assert response.status_code == 200, response.text
    client.put(f"/api/studio/timelines/{timeline['id']}/fx", json={
        "events": [{"type": "imageSlam", "t": 0.5,
                    "src": f"http://127.0.0.1:8000{sprite['url']}"}],
    })

    async def fake_run(spec, output, *, on_progress=None):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")
        return output

    async def fake_frames(path):
        return 48

    monkeypatch.setattr(timeline_service, "run_export", fake_run)
    monkeypatch.setattr(timeline_service, "probe_frames", fake_frames)
    # 演出付きでなければ FX のイベントは焼かれないので一覧にも出ない
    plain = client.post(f"/api/studio/timelines/{timeline['id']}/export", json={}).json()
    deadline = time.time() + 10
    export: dict = {}
    while time.time() < deadline:
        export = call(env, "GET", f"/api/v1/exports/{plain['id']}").json()
        if export["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert export["status"] == "done", export
    items = export["materials"]["items"]
    assert [item["library_id"] for item in items] == [video["id"], licensed["id"]]
    assert items[0]["licensed"] is False and items[0]["used_in"] == ["V1"]
    assert items[1]["license"]["provider"] == "BGM Site"
    assert items[1]["used_in"] == ["A1"]
    assert unused["id"] not in [item["library_id"] for item in items]
    # クレジットの文や credits.txt は作らない
    assert "credits" not in export
    assert not (Path(export["output_path"]).parent / "credits.txt").exists()

    # FX のイベントの中のライブラリの URL もたどる
    fx = asyncio.run(timeline_service.get_fx(timeline["id"]))
    current = asyncio.run(timeline_service.timeline_detail(timeline["id"]))
    collected = asyncio.run(timeline_service.collect_materials(current, fx=fx))
    found = {item.library_id: item.used_in for item in collected.items}
    assert found[sprite["id"]] == ["FX"]


# --------------------------------------------------------------------------
# 測定（実際の ffmpeg / Pillow）
# --------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg が無い")
def test_measure_reads_audio_length_and_loudness(tmp_path):
    tone = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=440:d=2", str(tone)], check=True)
    info = asyncio.run(media_measure.measure(tone, "audio"))
    assert info.duration_ms == 2000
    assert info.loudness_lufs is not None and -40 < info.loudness_lufs < 0
    assert info.true_peak_db is not None


def test_measure_tells_whether_an_image_is_transparent(tmp_path):
    from PIL import Image

    solid = tmp_path / "solid.png"
    Image.new("RGBA", (8, 4), (255, 0, 0, 255)).save(solid)
    clear = tmp_path / "clear.png"
    Image.new("RGBA", (8, 4), (255, 0, 0, 0)).save(clear)
    assert media_measure.image_info(solid).has_alpha is False
    info = media_measure.image_info(clear)
    assert info.has_alpha is True and (info.width, info.height) == (8, 4)


def test_parse_loudnorm_reads_the_last_json():
    stderr = 'noise\n{\n "input_i" : "-16.20",\n "input_tp" : "-1.00"\n}\n'
    assert media_measure.parse_loudnorm(stderr) == (-16.2, -1.0)
    assert media_measure.parse_loudnorm('{"input_i": "-inf", "input_tp": "-inf"}') == (None, None)
