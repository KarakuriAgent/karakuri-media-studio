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

数十 GB のファイルは途中で切られることがある（Civitai は 46GB を 28GB 付近で
落とすことがある）ので、**``.part`` を捨てずに ``Range`` で続きから**取り直す:

- 一時的な失敗（接続断・読み取りエラー・5xx・短い応答）は関数内で最大 5 回、
  5s→10s→…→60s の指数バックオフで再試行する
- 再試行のたびに**元の URL からリダイレクトを辿り直す**（Civitai の署名付き URL
  は期限切れになるので、失敗した転送先を使い回さない）
- ``Range`` に 206 で応えないサーバ相手なら ``.part`` を捨てて頭から書き直す
- 恒久的な失敗（401/403/404 など）だけは ``.part`` を残さず消す

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
MAX_ATTEMPTS = 5
RETRY_WAIT = 5.0
RETRY_WAIT_MAX = 60.0
# 4xx でも「待てば直る」側。これ以外の 4xx は恒久的な失敗として扱う
TRANSIENT_4XX = frozenset({408, 425, 429})

HF_HOSTS = ("huggingface.co", "hf.co")
CIVITAI_HOSTS = ("civitai.com",)


class DownloadError(Exception):
    """1 件のダウンロードの失敗。"""


class PermanentDownloadError(DownloadError):
    """やり直しても直らない失敗（401/403/404 など）。``.part`` を残さない。"""


class RangeError(DownloadError):
    """``Range`` が受け付けられなかった（HTTP 416）。``.part`` を捨ててやり直す。"""


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


def _total_size(res: httpx.Response) -> int:
    """この応答から分かる**ファイル全体**のバイト数（分からなければ 0）。"""
    if res.status_code == 206:
        # Content-Range: bytes <start>-<end>/<total>（total が "*" のこともある）
        value = res.headers.get("content-range", "")
        _, _, size = value.partition("/")
        return int(size) if size.isdigit() else 0
    length = res.headers.get("content-length", "")
    return int(length) if length.isdigit() else 0


def _open(client: httpx.Client, url: str, offset: int) -> httpx.Response:
    """元の URL からリダイレクトを辿り、本文を読める応答を開いて返す。

    ``offset`` が 0 より大きければ ``Range`` を付ける。**呼び側が閉じること**。
    """
    address = check_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        headers = auth_headers(address)
        if offset:
            headers["Range"] = f"bytes={offset}-"
        res = client.send(client.build_request("GET", address, headers=headers), stream=True)
        if res.status_code in REDIRECT_STATUSES:
            location = res.headers.get("location")
            res.close()
            if not location:
                raise DownloadError(
                    f"リダイレクト先が示されていません（HTTP {res.status_code}）"
                )
            # 相対 Location もあるので、いまの URL を基準に解決する
            address = check_url(urljoin(address, location))
            continue
        if res.status_code >= 400:
            code = res.status_code
            res.close()
            message = f"HTTP {code} が返りました: {address}"
            if code == 416:
                raise RangeError(message)
            if 400 <= code < 500 and code not in TRANSIENT_4XX:
                raise PermanentDownloadError(message)
            raise DownloadError(message)
        return res
    raise DownloadError(f"リダイレクトが {MAX_REDIRECTS} 回を超えました: {url}")


def _fetch(client: httpx.Client, url: str, part: Path, expected: int) -> int:
    """``.part`` を（あれば続きから）埋める 1 回分の試行。全体のバイト数を返す。

    やり直せば直りそうな失敗は ``DownloadError``、直らない失敗は
    ``PermanentDownloadError`` を投げる。
    """
    offset = part.stat().st_size if part.exists() else 0
    if expected and offset > expected:
        # 前回までの ``.part`` が壊れている（大きすぎる）。捨ててやり直す
        offset = 0
    try:
        res = _open(client, url, offset)
    except RangeError as exc:
        # 途中まで書いた ``.part`` の位置をサーバが受け付けない。捨ててやり直す
        part.unlink(missing_ok=True)
        raise DownloadError(f"{exc}（.part を捨てて取り直します）") from exc
    try:
        # Range を頼んだのに 206 が返らない = 続きから貰えていない。頭から書き直す
        resume = offset > 0 and res.status_code == 206
        total = _total_size(res)
        if resume and expected and total and total != expected:
            # 配信元のファイルが差し替わった。混ぜると壊れるので頭から取り直す
            part.unlink(missing_ok=True)
            raise DownloadError(
                f"配信元のサイズが変わりました（{expected} → {total}）: {url}"
            )
        received = offset if resume else 0
        last = time.monotonic()
        with part.open("ab" if resume else "wb") as sink:
            for chunk in res.iter_bytes(CHUNK_SIZE):
                sink.write(chunk)
                received += len(chunk)
                now = time.monotonic()
                if now - last >= PROGRESS_INTERVAL:
                    last = now
                    share = f"/{_human(total)}" if total else ""
                    print(f"    {_human(received)}{share}", flush=True)
    except httpx.HTTPError as exc:
        raise DownloadError(f"転送が途切れました: {exc}") from exc
    finally:
        res.close()
    return total


def download(url: str, target: Path) -> None:
    """1 件落として ``target`` に置く（既にあれば呼ばれない）。"""
    part = target.with_name(target.name + PART_SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = 0
    wait = RETRY_WAIT
    # follow_redirects=False: 認証はホップごとに単発で渡す（既定ヘッダに載せない）
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                total = _fetch(client, url, part, expected)
                expected = total or expected
                size = part.stat().st_size
                if expected and size != expected:
                    # 途中で切られた（短い応答）。``.part`` は残して続きを貰う
                    raise DownloadError(
                        f"サイズが足りません（{size}/{expected} バイト）: {url}"
                    )
            except PermanentDownloadError:
                raise
            except (DownloadError, httpx.HTTPError) as exc:
                if attempt == MAX_ATTEMPTS:
                    raise
                print(
                    f"    retry {attempt}/{MAX_ATTEMPTS - 1} in {wait:.0f}s: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(wait)
                wait = min(wait * 2, RETRY_WAIT_MAX)
            else:
                break
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
            part = target.with_name(target.name + PART_SUFFIX)
            if isinstance(exc, PermanentDownloadError):
                # 待っても直らないので、途中まで書いたものを残す意味がない
                part.unlink(missing_ok=True)
            elif part.exists():
                # 次回の起動で ``Range`` の続きから貰えるよう残しておく
                print(
                    f"[models] keeping {part.name} ({_human(part.stat().st_size)})"
                    " for resume",
                    flush=True,
                )
            print(f"[models] FAILED {relative}: {exc}", file=sys.stderr, flush=True)
        else:
            print(f"[models] done {relative}", flush=True)

    if failed:
        print(f"[models] {failed} file(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
