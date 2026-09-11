"""保存済み絶対パスの載せ替え（:func:`app.paths.rebase_stored_path`）。

DB には成果物の**絶対パス**が入るが、リポジトリの見え方（``/home/…`` と
``/mnt/…`` のように同じ実体を指す別のプレフィックス、``${PWD}`` をそのまま
マウントする Docker 起動）によって ROOT は変わりうる。別のプレフィックスで
記録された行でも履歴の URL とファイル読み出しが壊れないことを確かめる。
"""

import importlib
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
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
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


# ------------------------------------------------ データの置き場（KARAKURI_DATA_DIR）
# ``outputs/`` ``assets/`` ``library/`` は環境変数でリポジトリの外（NAS など）へ
# 移せる（:data:`app.paths.DATA_DIR`）。``app.db`` と ``runtime/`` は対象外。
# モジュール定数はインポート時に決まるので、環境変数を変えたら読み直して見る。


@pytest.fixture
def reload_paths(monkeypatch):
    """``KARAKURI_DATA_DIR`` を差し替えて :mod:`app.paths` を読み直す。

    後片付けで環境変数を消してもう一度読み直すので、他のテストには漏れない
    （``from .paths import OUTPUTS_DIR`` で値をコピーしている他モジュールは
    読み直しの影響を受けないが、このテストは :mod:`app.paths` しか見ない）。
    """

    def reload_with(value: str | None):
        if value is None:
            monkeypatch.delenv("KARAKURI_DATA_DIR", raising=False)
        else:
            monkeypatch.setenv("KARAKURI_DATA_DIR", value)
        return importlib.reload(paths)

    yield reload_with
    monkeypatch.delenv("KARAKURI_DATA_DIR", raising=False)
    importlib.reload(paths)


def test_data_dir_defaults_to_the_repository(reload_paths):
    """未設定ならこれまでどおり ROOT 直下。"""
    reloaded = reload_paths(None)
    assert reloaded.DATA_DIR == reloaded.ROOT
    assert reloaded.OUTPUTS_DIR == reloaded.ROOT / "outputs"


def test_data_dir_env_moves_the_file_stores(reload_paths, tmp_path):
    """環境変数を書くと 3 つの置き場だけが外に出る（DB と runtime/ は残る）。"""
    nas = tmp_path / "nas"
    reloaded = reload_paths(str(nas))
    assert reloaded.DATA_DIR == nas
    assert reloaded.OUTPUTS_DIR == nas / "outputs"
    assert reloaded.ASSETS_DIR == nas / "assets"
    assert reloaded.LIBRARY_DIR == nas / "library"
    assert reloaded.RUNTIME_DIR == reloaded.ROOT / "runtime"
    assert reloaded.DB_PATH == reloaded.ROOT / "app.db"


def test_data_dir_env_blank_is_unset(reload_paths):
    """空文字は未設定扱い（compose が ``${KARAKURI_DATA_DIR:-}`` を渡すため）。"""
    reloaded = reload_paths("  ")
    assert reloaded.DATA_DIR == reloaded.ROOT


def test_ensure_dirs_creates_both_sides(reload_paths, tmp_path, monkeypatch):
    """``ensure_dirs`` は外に出した置き場と ROOT 側の runtime/ を両方作る。"""
    reloaded = reload_paths(str(tmp_path / "nas"))
    monkeypatch.setattr(reloaded, "RUNTIME_DIR", tmp_path / "repo" / "runtime")
    monkeypatch.setattr(reloaded, "GROK_WORKDIR", tmp_path / "repo" / "runtime" / "g")
    monkeypatch.setattr(
        reloaded, "GROK_MEDIA_WORKDIR", tmp_path / "repo" / "runtime" / "gm"
    )
    monkeypatch.setattr(
        reloaded, "CHAT_SESSIONS_DIR", tmp_path / "repo" / "runtime" / "chat"
    )
    monkeypatch.setattr(
        reloaded, "REMOTION_TMP_DIR", tmp_path / "repo" / "runtime" / "remotion"
    )
    monkeypatch.setattr(
        reloaded, "AUDIO_ANALYSIS_TMP_DIR", tmp_path / "repo" / "runtime" / "audio"
    )
    reloaded.ensure_dirs()
    assert (tmp_path / "nas" / "outputs").is_dir()
    assert (tmp_path / "nas" / "assets").is_dir()
    assert (tmp_path / "nas" / "library").is_dir()
    assert (tmp_path / "repo" / "runtime" / "chat").is_dir()


@pytest.fixture
def split_dirs(tmp_path, monkeypatch):
    """置き場を外に出した構成（ROOT と DATA_DIR が別）。"""
    repo, data = tmp_path / "repo", tmp_path / "nas"
    (data / "outputs" / "job1").mkdir(parents=True)
    (repo / "runtime" / "grok-workdir").mkdir(parents=True)
    monkeypatch.setattr(paths, "ROOT", repo)
    monkeypatch.setattr(paths, "DATA_DIR", data)
    return repo, data


def test_rebase_moves_outputs_onto_the_data_dir(split_dirs):
    """旧 ROOT 配下で記録された成果物は、いまの DATA_DIR 側に載せ替わる。"""
    repo, data = split_dirs
    (data / "outputs" / "job1" / "video.mp4").write_bytes(b"x")
    stored = str(repo / "outputs" / "job1" / "video.mp4")
    assert paths.rebase_stored_path(stored) == data / "outputs" / "job1" / "video.mp4"


def test_rebase_keeps_runtime_on_the_root(split_dirs):
    """``runtime/`` は置き場の引っ越しの対象外なので ROOT 側のまま。"""
    repo, data = split_dirs
    stored = "/home/someone/workspace/video-studio/runtime/grok-workdir"
    assert paths.rebase_stored_path(stored) == repo / "runtime" / "grok-workdir"
