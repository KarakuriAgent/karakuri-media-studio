"""保存済み絶対パスの載せ替え（:func:`app.paths.rebase_stored_path`）。

DB には成果物の**絶対パス**が入るが、リポジトリの見え方（``/home/…`` と
``/mnt/…`` のように同じ実体を指す別のプレフィックス、``${PWD}`` をそのまま
マウントする Docker 起動）によって ROOT は変わりうる。別のプレフィックスで
記録された行でも履歴の URL とファイル読み出しが壊れないことを確かめる。
"""

from pathlib import Path

import pytest

from app import config, grok, grok_media, jobs, paths
from app.models import Settings


@pytest.fixture
def root(tmp_path, monkeypatch):
    """ROOT と outputs/ をテスト用ディレクトリに差し替える。"""
    outputs = tmp_path / "outputs"
    (outputs / "job1").mkdir(parents=True)
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setattr(jobs, "OUTPUTS_DIR", outputs)
    return tmp_path


def test_rebase_keeps_a_path_that_already_resolves(root):
    """いまの ROOT の下にあるパスはそのまま（載せ替えない）。"""
    stored = root / "outputs" / "job1" / "video.mp4"
    stored.write_bytes(b"x")
    assert paths.rebase_stored_path(str(stored)) == stored


def test_rebase_moves_an_old_prefix_onto_the_current_root(root):
    """別のプレフィックスで記録されたパスは、いまの ROOT に接ぎ直す。"""
    (root / "outputs" / "job1" / "video.mp4").write_bytes(b"x")
    stored = "/home/someone/workspace/video-studio/outputs/job1/video.mp4"
    assert paths.rebase_stored_path(stored) == root / "outputs" / "job1" / "video.mp4"


def test_rebase_uses_the_last_anchor(root):
    """リポジトリ自体が同名ディレクトリの下にあっても、末尾側の置き場を採る。"""
    (root / "library" / "image").mkdir(parents=True)
    (root / "library" / "image" / "ref.png").write_bytes(b"x")
    stored = "/srv/library/video-studio/library/image/ref.png"
    assert paths.rebase_stored_path(stored) == root / "library" / "image" / "ref.png"


def test_rebase_passes_unknown_paths_through(root):
    """置き場の名前が無い / 載せ替え先も無いパスは素通し（存在確認は呼び出し側）。"""
    assert paths.rebase_stored_path("/var/tmp/elsewhere.png") == Path(
        "/var/tmp/elsewhere.png"
    )
    missing = "/home/someone/workspace/video-studio/outputs/job1/gone.mp4"
    assert paths.rebase_stored_path(missing) == Path(missing)


def test_output_url_for_a_current_path(root):
    stored = root / "outputs" / "job1" / "video.mp4"
    stored.write_bytes(b"x")
    assert jobs._output_url(str(stored)) == "/outputs/job1/video.mp4"


def test_output_url_for_an_old_prefix(root):
    """旧プレフィックスの記録でも ``/outputs/…`` に解決できる（履歴の表示）。"""
    (root / "outputs" / "job1" / "video.mp4").write_bytes(b"x")
    stored = "/home/someone/workspace/video-studio/outputs/job1/video.mp4"
    assert jobs._output_url(stored) == "/outputs/job1/video.mp4"


def test_output_url_outside_outputs_is_none(root):
    """outputs/ の外（素材）と空の記録は URL を持たない。"""
    asset = root / "assets" / "image"
    asset.mkdir(parents=True)
    (asset / "ref.png").write_bytes(b"x")
    assert jobs._output_url(str(asset / "ref.png")) is None
    assert jobs._output_url(None) is None


# ------------------------------------- 設定の作業ディレクトリ（grok CLI, SPEC §5.2）
# ``runtime/config.json`` に入るのは保存した時点の ROOT を前提にした絶対パス。
# Docker 内ではリポジトリが別のプレフィックスに載るので、そのままでは作業
# ディレクトリを作れず（``/home/…`` は Permission denied）CLI の実行が丸ごと
# 落ちる。設定由来のパスも成果物と同じように載せ替わることを確かめる
# （相談チャットの cwd はセッションごとの ``runtime/agent-sessions/chat-<id>/``
# なので、この設定は使わない）。

#: 別のプレフィックス（ホスト側）で保存された作業ディレクトリ
STORED_PREFIX = "/home/someone/workspace/video-studio/runtime"


@pytest.fixture
def workdirs(root):
    """ROOT の下に grok の作業ディレクトリを作る（``ensure_dirs`` と同じ形）。"""
    made = tuple(
        root / "runtime" / name for name in ("grok-workdir", "grok-media-workdir")
    )
    for directory in made:
        directory.mkdir(parents=True)
    return made


def test_settings_workdir_lands_under_the_current_root(workdirs, monkeypatch):
    """存在しない別プレフィックスの設定でも、いまの ROOT の作業ディレクトリを使う。"""
    grok_dir, media_dir = workdirs
    monkeypatch.setattr(
        config,
        "_settings",
        Settings(
            grok_workdir=f"{STORED_PREFIX}/grok-workdir",
            grok_media_workdir=f"{STORED_PREFIX}/grok-media-workdir",
        ),
    )
    assert grok.GrokCliClient().workdir == grok_dir
    assert grok_media.workdir() == media_dir


def test_settings_workdir_falls_back_to_the_default(monkeypatch):
    """設定が空なら ROOT 直下の既定の置き場。"""
    monkeypatch.setattr(config, "_settings", Settings())
    assert grok.GrokCliClient().workdir == paths.GROK_WORKDIR
    assert grok_media.workdir() == paths.GROK_MEDIA_WORKDIR


def test_settings_workdir_outside_the_repo_is_kept(root, monkeypatch):
    """リポジトリの外を指した設定はそのまま（載せ替え先が無ければ触らない）。"""
    outside = root.parent / "elsewhere" / "grok"
    monkeypatch.setattr(config, "_settings", Settings(grok_workdir=str(outside)))
    assert grok.GrokCliClient().workdir == outside
