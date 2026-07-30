"""不足モデルのダウンロード（SPEC §3.3）。

ComfyUI 本体にも Comfy Cloud にもモデルを取ってくる API は無いので、**このアプリが
自分でダウンロードして** ComfyUI の models ディレクトリ（環境変数 ``COMFY_MODELS_DIR``）へ
直接置く。ComfyUI はフォルダの mtime を見て一覧を作り直すため、ファイルを置くだけで
再起動なしに認識される。

数十 GB のファイルが前提なので、レスポンスはメモリに貯めずチャンクで
``<filename>.part`` に書き、完走したときだけ本来の名前へ ``rename`` する（途中で
落ちた ``.part`` は消すので、半端なファイルが ComfyUI の一覧に出ることはない）。
進捗は :mod:`app.ws` の hub 経由で ``type: "model_download"`` として配信する。

リダイレクトは httpx に任せず**自分で追う**。``follow_redirects=True`` はクライアントの
既定ヘッダをリダイレクト先にもそのまま送るため、Hugging Face → CDN のような転送や
悪意ある ``Location`` でトークンが無関係なホストに漏れてしまう。ホップごとに
:func:`auth_headers` をその URL で計算し直し、**そのリクエストにだけ**渡す。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from . import ws
from .config import load_settings
from .models import ModelDownload, ModelsDirStatus

log = logging.getLogger(__name__)

#: 接続は短く諦め、本文（チャンク 1 つぶん）の待ちは長めに取る。全体の時間には
#: 上限を置かない（大容量ファイルを丸ごと落とすため）。
TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
CHUNK_SIZE = 4 * 1024 * 1024
#: 進捗配信の間隔。時間・バイト数のどちらか先に達したら 1 フレーム流す。
PROGRESS_INTERVAL = 1.0
PROGRESS_BYTES = 32 * 1024 * 1024
PART_SUFFIX = ".part"

#: 自分で追うリダイレクトの上限ホップ数
MAX_REDIRECTS = 10
#: GET のまま追ってよいリダイレクト（303 と 302 の慣行を含む。元から GET しかしない）
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

#: Authorization を付ける相手（ホスト名の完全一致か、サブドメイン一致）
HF_HOSTS = ("huggingface.co", "hf.co")
CIVITAI_HOSTS = ("civitai.com",)


class DownloadError(Exception):
    """保存先の指定不備やダウンロードの失敗（ルーターが 400 に変換する）。"""


class DownloadBusy(DownloadError):
    """同じファイル名のダウンロードが既に走っている（409）。"""


#: ファイル名 -> 状態。完了・失敗も残すので、UI は WS を取りこぼしても
#: ``GET /api/models/downloads`` で追いつける（プロセスを再起動すると消える）。
_downloads: dict[str, ModelDownload] = {}
_tasks: dict[str, asyncio.Task[None]] = {}


# --------------------------------------------------------------------------
# models ディレクトリ
# --------------------------------------------------------------------------

#: models ディレクトリを指す環境変数（``.env`` から run.sh / docker compose 経由で入る）
MODELS_DIR_ENV = "COMFY_MODELS_DIR"


def models_dir() -> Path | None:
    """ComfyUI の models ディレクトリ（環境変数が未設定なら ``None``）。

    設定 (``runtime/config.json``) ではなく環境変数だけを見る: Docker ではホストの
    models ディレクトリを同じ絶対パスでマウントして初めて書けるので、パスの出所を
    ``.env``（compose のマウント元と同じ変数）に一本化している。
    """
    raw = os.environ.get(MODELS_DIR_ENV, "").strip()
    return Path(raw).expanduser() if raw else None


def dir_status() -> ModelsDirStatus:
    """models ディレクトリの状態（``GET /api/models/dir-status``）。"""
    root = models_dir()
    if root is None:
        return ModelsDirStatus()
    exists = root.is_dir()
    return ModelsDirStatus(
        configured=True,
        exists=exists,
        writable=exists and os.access(root, os.W_OK),
        path=str(root),
    )


def _check_name(name: str, label: str) -> str:
    """ファイル名 1 要素の検証（区切り文字と ``..`` を許さない）。"""
    cleaned = (name or "").strip()
    if not cleaned:
        raise DownloadError(f"{label}が空です")
    if "/" in cleaned or "\\" in cleaned or cleaned in {".", ".."}:
        raise DownloadError(f"{label}にパス区切りは使えません: {name}")
    return cleaned


def resolve_target(subfolder: str, filename: str, root: Path | None = None) -> Path:
    """保存先の絶対パスを返す。models ディレクトリの外に出る指定は拒否する。

    ``..`` や絶対パスを弾いたうえで、``resolve()`` 後に models ディレクトリ配下で
    あることも確かめる（シンボリックリンク経由の抜け道を塞ぐ）。
    """
    base_dir = root if root is not None else models_dir()
    if base_dir is None:
        raise DownloadError(f"{MODELS_DIR_ENV} が設定されていません")
    base = base_dir.expanduser().resolve()

    name = _check_name(filename, "ファイル名")
    raw = (subfolder or "").strip().replace("\\", "/")
    if raw.startswith("/") or Path(raw).is_absolute():
        raise DownloadError(f"置き場所に絶対パスは使えません: {subfolder}")
    relative = raw.strip("/")
    if relative:
        for part in relative.split("/"):
            _check_name(part, "置き場所")

    target = (base / relative / name).resolve()
    parent = target.parent
    if parent != base and base not in parent.parents:
        raise DownloadError(f"models ディレクトリの外は指定できません: {subfolder}")
    return target


def check_url(url: str) -> str:
    """http(s) の URL であることを確かめて返す（リダイレクト先にも毎回かける）。"""
    address = (url or "").strip()
    if urlparse(address).scheme not in {"http", "https"}:
        raise DownloadError(f"http(s) の URL を指定してください: {url}")
    return address


def auth_headers(url: str) -> dict[str, str]:
    """URL のホストに応じた認証ヘッダ（トークン未設定なら付けない）。

    Hugging Face は gated リポジトリ、Civitai は要ログインのファイルで必要になる。
    Civitai は ``?token=`` クエリでも通るが、ヘッダに寄せて URL をログに残せる
    ようにしておく。

    リダイレクトを追うたびに**その時点の URL で**呼び直すこと（トークンを
    無関係なホストへ送らないため）。
    """
    host = (urlparse(url).hostname or "").lower()
    settings = load_settings()
    token = ""
    if any(host == known or host.endswith(f".{known}") for known in HF_HOSTS):
        token = (settings.hf_token or "").strip()
    elif any(host == known or host.endswith(f".{known}") for known in CIVITAI_HOSTS):
        token = (settings.civitai_api_key or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


# --------------------------------------------------------------------------
# ダウンロード本体
# --------------------------------------------------------------------------

def _describe(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} {exc.response.reason_phrase}".strip()
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


async def download(state: ModelDownload) -> None:
    """``state`` の 1 件を落として所定の場所に置く。進捗は WS に流す。

    例外は投げず、失敗は ``state.status = "error"`` として配信する
    （バックグラウンドタスクとして走るため、投げても誰も受け取れない）。
    """
    target = Path(state.path)
    part = target.with_name(target.name + PART_SUFFIX)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # follow_redirects=False: 認証ヘッダはホップごとに計算して単発で渡す
        # （クライアントの既定ヘッダに載せると転送先へ漏れる）。
        async with httpx.AsyncClient(
            timeout=TIMEOUT, follow_redirects=False
        ) as client:
            address = check_url(state.url)
            for _ in range(MAX_REDIRECTS + 1):
                async with client.stream(
                    "GET", address, headers=auth_headers(address)
                ) as response:
                    if response.status_code in REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise DownloadError(
                                f"リダイレクト先が示されていません（HTTP "
                                f"{response.status_code}）"
                            )
                        # 相対 Location もあるので、いまの URL を基準に解決する
                        address = check_url(urljoin(address, location))
                        continue
                    response.raise_for_status()
                    length = response.headers.get("content-length")
                    state.total = int(length) if length and length.isdigit() else None
                    await ws.publish_model_download(state)
                    last_at = time.monotonic()
                    last_received = 0
                    with part.open("wb") as sink:
                        async for chunk in response.aiter_bytes(CHUNK_SIZE):
                            sink.write(chunk)
                            state.received += len(chunk)
                            now = time.monotonic()
                            if (
                                now - last_at >= PROGRESS_INTERVAL
                                or state.received - last_received >= PROGRESS_BYTES
                            ):
                                last_at, last_received = now, state.received
                                await ws.publish_model_download(state)
                    break
            else:
                raise DownloadError(
                    f"リダイレクトが {MAX_REDIRECTS} 回を超えました: {state.url}"
                )
        part.replace(target)
        state.status = "done"
        state.error = None
        log.info("model downloaded: %s (%d bytes)", state.path, state.received)
    except asyncio.CancelledError:
        part.unlink(missing_ok=True)
        state.status = "error"
        state.error = "中断されました"
        await ws.publish_model_download(state)
        raise
    except Exception as exc:  # noqa: BLE001 - 失敗は UI に見せるだけで良い
        part.unlink(missing_ok=True)
        state.status = "error"
        state.error = _describe(exc)
        log.warning("model download failed (%s): %s", state.filename, state.error)
    await ws.publish_model_download(state)


def start(filename: str, url: str, subfolder: str = "") -> ModelDownload:
    """バックグラウンドでダウンロードを開始し、その状態を返す。

    保存先の検証はここで済ませるので、呼び出し側（ルーター）は不正な指定を
    その場で 400 として返せる。同じファイル名が進行中なら :class:`DownloadBusy`。
    """
    name = _check_name(filename, "ファイル名")
    target = resolve_target(subfolder, name)

    address = check_url(url)

    status = dir_status()
    if not status.exists:
        raise DownloadError(
            "ComfyUI の models ディレクトリが見つかりません"
            f"（{MODELS_DIR_ENV}: {status.path or '未設定'}）"
        )
    if not status.writable:
        raise DownloadError(f"models ディレクトリに書き込めません: {status.path}")

    current = _downloads.get(name)
    if current is not None and current.status == "downloading":
        raise DownloadBusy(f"すでにダウンロード中です: {name}")

    state = ModelDownload(
        filename=name,
        status="downloading",
        received=0,
        total=None,
        error=None,
        subfolder=(subfolder or "").strip().strip("/"),
        url=address,
        path=str(target),
    )
    _downloads[name] = state
    task = asyncio.create_task(download(state))
    _tasks[name] = task
    task.add_done_callback(lambda finished, key=name: _tasks.pop(key, None))
    return state


def downloads() -> list[ModelDownload]:
    """進行中のものと、直近の完了 / 失敗（``GET /api/models/downloads``）。"""
    return list(_downloads.values())
