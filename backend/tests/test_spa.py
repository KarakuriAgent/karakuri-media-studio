"""frontend/dist の配信: 実ファイル優先と PWA 用ファイル（SPEC §7）。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.paths import FRONTEND_DIST_DIR

pytestmark = pytest.mark.skipif(
    not (FRONTEND_DIST_DIR / "sw.js").is_file(),
    reason="frontend がビルドされていない（frontend/dist が無い）",
)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_the_service_worker_is_served_as_a_real_file(client):
    """sw.js が index.html にフォールバックされると PWA が壊れる。"""
    res = client.get("/sw.js")
    assert res.status_code == 200
    assert "javascript" in res.headers["content-type"]
    assert "<!doctype html" not in res.text[:200].lower()
    # 古い sw.js が居座ると autoUpdate が届かなくなる
    assert res.headers["cache-control"] == "no-cache"


def test_the_manifest_keeps_its_own_media_type(client):
    res = client.get("/manifest.webmanifest")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/manifest+json")
    assert res.json()["name"] == "Karakuri Media Studio"


def test_unknown_routes_still_fall_back_to_the_spa_shell(client):
    res = client.get("/settings")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "<div id=\"root\">" in res.text


def test_unmatched_api_paths_stay_404(client):
    assert client.get("/api/does-not-exist").status_code == 404
