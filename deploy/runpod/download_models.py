#!/usr/bin/env python3
"""``models.txt`` に並んだモデルのうち、まだ無いものだけを落とす。

流儀はアプリ側の ``backend/app/model_download.py`` に揃えてある:

- ``<filename>.part`` に書き、**完走したときだけ**本来の名前へ rename する
  （途中で落ちた半端なファイルが ComfyUI のモデル一覧に出ない）
- リダイレクトは httpx に任せず**自分で追う**（最大 10 ホップ）。クライアントの
  既定ヘッダに認証を載せると転送先の別ホストにトークンが漏れるので、ホップごとに
  URL を見て認証ヘッダを計算し直し、**そのリクエストにだけ**渡す
- huggingface.co / hf.co（サブドメイン含む）には ``HF_TOKEN``、civitai.com には
  ``CIVITAI_API_KEY`` を ``Authorization: Bearer …`` で付ける。無関係なホストには
  何も付けない

使い方::

    python3 download_models.py models.txt [models.local.txt ...] /workspace/ComfyUI/models

最後の引数が置き場所で、その手前が読むマニフェスト（複数可）。同じ行が複数の
マニフェストに出ていても、すでにあるファイルは飛ばすので二重に落ちることはない。

1 件でも失敗したら終了コード 1（ただし残りの行は最後まで試す）。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
CHUNK_SIZE = 4 * 1024 * 1024
PART_SUFFIX = ".part"
MAX_REDIRECTS = 10
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
PROGRESS_INTERVAL = 15.0

HF_HOSTS = ("huggingface.co", "hf.co")
CIVITAI_HOSTS = ("civitai.com",)


class DownloadError(Exception):
    """1 件のダウンロードの失敗。"""


def check_url(url: str) -> str:
    """http(s) の URL であることを確かめて返す（リダイレクト先にも毎回かける）。"""
    if urlparse(url).scheme not in {"http", "https"}:
        raise DownloadError(f"http(s) の URL を指定してください: {url}")
    return url


def _matches(host: str, known: tuple[str, ...]) -> bool:
    return any(host == name or host.endswith(f".{name}") for name in known)


def auth_headers(url: str) -> dict[str, str]:
    """URL のホストに応じた認証ヘッダ（トークン未設定なら付けない）。"""
    host = (urlparse(url).hostname or "").lower()
    token = ""
    if _matches(host, HF_HOSTS):
        token = os.environ.get("HF_TOKEN", "").strip()
    elif _matches(host, CIVITAI_HOSTS):
        token = os.environ.get("CIVITAI_API_KEY", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _human(size: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GiB"


def download(url: str, target: Path) -> None:
    """1 件落として ``target`` に置く（既にあれば呼ばれない）。"""
    part = target.with_name(target.name + PART_SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)
    # follow_redirects=False: 認証はホップごとに単発で渡す（既定ヘッダに載せない）
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
        address = check_url(url)
        for _ in range(MAX_REDIRECTS + 1):
            with client.stream("GET", address, headers=auth_headers(address)) as res:
                if res.status_code in REDIRECT_STATUSES:
                    location = res.headers.get("location")
                    if not location:
                        raise DownloadError(
                            f"リダイレクト先が示されていません（HTTP {res.status_code}）"
                        )
                    # 相対 Location もあるので、いまの URL を基準に解決する
                    address = check_url(urljoin(address, location))
                    continue
                res.raise_for_status()
                length = res.headers.get("content-length")
                total = int(length) if length and length.isdigit() else 0
                received = 0
                last = time.monotonic()
                with part.open("wb") as sink:
                    for chunk in res.iter_bytes(CHUNK_SIZE):
                        sink.write(chunk)
                        received += len(chunk)
                        now = time.monotonic()
                        if now - last >= PROGRESS_INTERVAL:
                            last = now
                            share = f"/{_human(total)}" if total else ""
                            print(
                                f"    {_human(received)}{share}", flush=True
                            )
                break
        else:
            raise DownloadError(f"リダイレクトが {MAX_REDIRECTS} 回を超えました: {url}")
    part.replace(target)


def parse(manifest: Path) -> list[tuple[str, str]]:
    """``<subfolder>/<filename> <url>`` の行を読む（空行・``#`` は無視）。"""
    entries: list[tuple[str, str]] = []
    for number, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise DownloadError(f"{manifest}:{number}: 行の形式が違います: {raw}")
        relative, url = parts
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise DownloadError(f"{manifest}:{number}: 置き場所が不正です: {relative}")
        entries.append((relative, url))
    return entries


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(
            f"usage: {argv[0]} <models.txt> [<models.txt> ...] <models-dir>",
            file=sys.stderr,
        )
        return 2
    manifests, root = [Path(a) for a in argv[1:-1]], Path(argv[-1])
    for manifest in manifests:
        if not manifest.is_file():
            print(f"manifest not found: {manifest}", file=sys.stderr)
            return 2

    entries: list[tuple[str, str]] = []
    for manifest in manifests:
        entries.extend(parse(manifest))

    failed = 0
    for relative, url in entries:
        target = root / relative
        if target.exists():
            continue
        print(f"[models] downloading {relative}", flush=True)
        try:
            download(url, target)
        except Exception as exc:  # noqa: BLE001 - 1 件の失敗で他を諦めない
            failed += 1
            target.with_name(target.name + PART_SUFFIX).unlink(missing_ok=True)
            print(f"[models] FAILED {relative}: {exc}", file=sys.stderr, flush=True)
        else:
            print(f"[models] done {relative}", flush=True)

    if failed:
        print(f"[models] {failed} file(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
