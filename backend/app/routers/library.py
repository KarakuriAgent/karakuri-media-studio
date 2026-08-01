"""ライブラリ API（SPEC §7.2）。

履歴とは別に取っておく素材の登録・一覧・更新・削除。実体の保存と DB 操作は
:mod:`app.library` に集約してあり（エージェントからも同じ関数を使う）、ここは
HTTP の入り口だけを担当する。
"""

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from .. import autotag
from .. import jobs as job_service
from .. import library as service
from .. import sheets
from ..models import (
    LibraryFromJob,
    LibraryItem,
    LibraryPage,
    LibrarySheet,
    LibraryUpdate,
)

router = APIRouter(prefix="/api/library", tags=["library"])

#: 1 リクエストで返す最大件数（それ以上は offset で辿る）
MAX_LIMIT = 200


def _bad_request(exc: service.LibraryError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("", response_model=LibraryPage)
async def list_library(
    kind: str | None = Query(None, pattern="^(image|video|audio)$"),
    #: 分類（未指定 = 全件 / 'none' = 未分類のみ）
    category: str | None = Query(None),
    #: 表示名とタグへの部分一致（大文字小文字は無視）
    q: str = Query(""),
    #: タグの完全一致
    tag: str | None = Query(None),
    limit: int = Query(service.DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> LibraryPage:
    """絞り込んだ 1 ページ分。``total`` で「まだ何件あるか」が分かる。"""
    try:
        items, total = await service.search_items(
            kind=kind, category=category, query=q, tag=tag, limit=limit, offset=offset
        )
    except service.LibraryError as exc:
        raise _bad_request(exc) from exc
    return LibraryPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        tags=await service.all_tags(),
    )


@router.post("/from-job", response_model=LibraryItem, status_code=201)
async def add_from_job(payload: LibraryFromJob) -> LibraryItem:
    """ジョブの出力をライブラリへ登録する（NSFW フラグは元ジョブを引き継ぐ）。"""
    job = await job_service.get_job(payload.job_id, include_workflow=False)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        item = await service.add_from_job(
            job, payload.source, payload.name, payload.tags, payload.category
        )
    except service.LibraryDuplicate as exc:
        # 同じ出力を二重に持たせない。エラーというより「もう棚にある」ので、
        # 既存のアイテムを添えて 409 で知らせる。
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "item": exc.item.model_dump(mode="json")},
        ) from exc
    except service.LibraryError as exc:
        raise _bad_request(exc) from exc
    # 表示名とタグを Grok に考えさせる（指定済みのものは触らない、SPEC §7.2）
    autotag.spawn_for(item, job, named=bool(payload.name.strip()))
    return item


@router.post("/sheet", response_model=LibraryItem, status_code=201)
async def create_sheet(payload: LibrarySheet) -> LibraryItem:
    """素材を 1 枚のリファレンスシートに合成して登録する（SPEC §7.2）。

    IC-LoRA の動画ワークフロー（`ltx2_3_ic_lora_image`）が参照入力に取る「複数
    パネルを並べた 1 枚」を、ライブラリの画像から組み立てる。`item_ids` の並び順に
    左上から置き、`character` の素材だけ大きいパネルになる。

    ``/{kind}`` より先に定義しておく（後ろだと `kind='sheet'` として食われる）。
    """
    try:
        return await service.add_sheet(
            payload.item_ids,
            payload.name,
            payload.width or sheets.DEFAULT_WIDTH,
            payload.height or sheets.DEFAULT_HEIGHT,
        )
    except service.LibraryError as exc:
        raise _bad_request(exc) from exc


@router.post("/{kind}", response_model=LibraryItem, status_code=201)
async def upload(
    kind: str,
    file: UploadFile = File(...),
    #: multipart はリストを送りにくいのでカンマ区切りで受ける
    tags: str = Form(""),
    #: 分類（空なら未分類）
    category: str = Form(""),
) -> LibraryItem:
    """手元のファイルをライブラリに追加する（種別ごとの拡張子のみ）。"""
    try:
        return await service.add_upload(
            kind, file.filename or "upload", await file.read(), tags, category
        )
    except service.LibraryError as exc:
        raise _bad_request(exc) from exc


@router.patch("/{item_id}", response_model=LibraryItem)
async def update(item_id: str, payload: LibraryUpdate) -> LibraryItem:
    """表示名 / NSFW フラグ / タグ / 分類の変更（指定した項目だけ）。

    ``category`` に ``"none"`` を送ると未分類に戻す（送らなければそのまま）。
    """
    try:
        item = await service.update_item(
            item_id,
            name=payload.name,
            nsfw=payload.nsfw,
            tags=payload.tags,
            category=payload.category,
        )
    except service.LibraryError as exc:
        raise _bad_request(exc) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="library item not found")
    return item


@router.delete("/{item_id}", status_code=204)
async def delete(item_id: str) -> None:
    if not await service.delete_item(item_id):
        raise HTTPException(status_code=404, detail="library item not found")
