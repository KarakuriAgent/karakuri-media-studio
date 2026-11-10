"""アーカイブ（ZIP）から素材を 1 つずつ安全に取り出す（素材パックの取り込みが使う）。

配布が ZIP だけの入手元のために、目録の ``member``（ZIP の中のパス）で指した
ファイルだけを、**ディスクへ展開せずに**メモリへ読み出す。中身は第三者のもの
なので、次の備えを置く:

- パス: ``..``・絶対パス・バックスラッシュ・ドライブ名（``C:``）を含む名前は使わない
  （目録の ``member`` も、ZIP の中の名前も）
- 数と大きさ: 中のファイルの数（:data:`MAX_ENTRIES`）・1 ファイルの展開後の大きさ
  （:data:`MAX_MEMBER_BYTES`）・1 回の取り込みで取り出す合計（:data:`MAX_TOTAL_BYTES`）
- ZIP 爆弾: 展開後が大きいのに圧縮率が極端なもの（:data:`MAX_RATIO`）は読まない。
  読み出しも宣言された大きさで打ち切る（``zipfile`` は宣言より多くは返さない）
- 暗号化されたファイル・フォルダ・入れ子の展開はしない
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

from . import library as library_service
from .models import AssetPackArchiveEntry

#: 中のファイルの数の上限（フォルダも数える）
MAX_ENTRIES = 20000
#: 1 ファイルの展開後の大きさの上限（バイト）
MAX_MEMBER_BYTES = 500 * 1024 * 1024
#: 1 回の取り込みでアーカイブから取り出す合計の上限（バイト）
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
#: これより大きい展開後のファイルは、圧縮率も確かめる（バイト）
RATIO_CHECK_BYTES = 16 * 1024 * 1024
#: 圧縮率の上限（展開後 / 圧縮後）
MAX_RATIO = 200

_DRIVE = re.compile(r"^[A-Za-z]:")


class ArchiveError(Exception):
    """アーカイブを読めない・取り出せない（理由はメッセージ）。"""


def unsafe_reason(name: str) -> str:
    """中のパスが危ないなら理由（安全なら空文字）。"""
    if not name or not name.strip():
        return "パスが空です"
    if "\\" in name:
        return "パスにバックスラッシュがあります"
    if name.startswith("/") or _DRIVE.match(name):
        return "絶対パスです"
    if "\x00" in name:
        return "パスに NUL があります"
    if any(part in ("..", ".", "") for part in name.rstrip("/").split("/")):
        return "パスに .. か . か空の区切りがあります"
    return ""


def check_member(name: str) -> str:
    """目録の ``member`` を確かめる（危なければ :class:`ArchiveError`）。"""
    reason = unsafe_reason(name)
    if reason:
        raise ArchiveError(f"member が使えません（{reason}）: {name}")
    if name.endswith("/"):
        raise ArchiveError(f"member はファイルのパスで書いてください（フォルダです）: {name}")
    return name


def _open(path: Path) -> zipfile.ZipFile:
    if not zipfile.is_zipfile(path):
        raise ArchiveError("ZIP ではありません（いまは ZIP だけを扱えます）")
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveError(f"ZIP を読めません（{exc}）") from exc
    if len(archive.infolist()) > MAX_ENTRIES:
        archive.close()
        raise ArchiveError(f"中のファイルが多すぎます（{MAX_ENTRIES} 件まで）")
    return archive


def _refusal(info: zipfile.ZipInfo) -> str:
    """そのファイルを取り出さない理由（取り出せるなら空文字）。"""
    reason = unsafe_reason(info.filename)
    if reason:
        return reason
    if info.is_dir():
        return "フォルダです"
    if info.flag_bits & 0x1:
        return "暗号化されています"
    if info.file_size > MAX_MEMBER_BYTES:
        return f"大きすぎます（{MAX_MEMBER_BYTES // (1024 * 1024)}MB まで）"
    if (
        info.file_size > RATIO_CHECK_BYTES
        and info.file_size > MAX_RATIO * max(1, info.compress_size)
    ):
        return f"圧縮率が極端です（{MAX_RATIO} 倍まで。ZIP 爆弾のおそれ）"
    return ""


def _kind_of(name: str) -> str | None:
    try:
        return library_service.detect_source_kind(PurePosixPath(name).name)
    except library_service.LibraryError:
        return None


def list_entries(path: Path) -> list[AssetPackArchiveEntry]:
    """中のファイルの一覧（フォルダは出さない。危ないものも ``usable: false`` で出す）。"""
    with _open(path) as archive:
        entries: list[AssetPackArchiveEntry] = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            reason = _refusal(info)
            kind = _kind_of(info.filename)
            if not reason and kind is None:
                reason = "取り込めない拡張子です"
            entries.append(
                AssetPackArchiveEntry(
                    path=info.filename,
                    size=info.file_size,
                    compressed_size=info.compress_size,
                    kind=kind,
                    usable=not reason,
                    reason=reason,
                )
            )
    return entries


def read_member(path: Path, member: str, *, budget: int | None = None) -> bytes:
    """``member`` を 1 つ読み出す（ディスクには展開しない）。

    ``budget`` はこの取り込みで取り出してよい残りのバイト数（超えるなら
    :class:`ArchiveError`）。
    """
    check_member(member)
    with _open(path) as archive:
        try:
            info = archive.getinfo(member)
        except KeyError as exc:
            raise ArchiveError(f"ZIP の中にありません: {member}") from exc
        reason = _refusal(info)
        if reason:
            raise ArchiveError(f"取り出せません（{reason}）: {member}")
        if budget is not None and info.file_size > budget:
            raise ArchiveError(
                "この取り込みで ZIP から取り出す合計が上限を超えます"
                f"（{MAX_TOTAL_BYTES // (1024 * 1024 * 1024)}GB まで）"
            )
        chunks: list[bytes] = []
        size = 0
        try:
            with archive.open(info) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    size += len(chunk)
                    if size > info.file_size or size > MAX_MEMBER_BYTES:
                        raise ArchiveError(f"宣言より大きなファイルです: {member}")
                    chunks.append(chunk)
        except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError) as exc:
            raise ArchiveError(f"取り出せません（{exc}）: {member}") from exc
    return b"".join(chunks)
