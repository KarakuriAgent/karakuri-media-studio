"""フォント画像とコンタクトシートの API（SPEC §7.2）。

どちらも「素材の下ごしらえ」で、成果物はふつうのライブラリ項目になる:

* ``POST /api/images/text`` … フォントで組んだ文字を RGBA PNG にする
  （スプライトとして貼る／画像生成の**字形参照**として渡す）
* ``POST /api/videos/contact-sheet`` … 動画の指定した秒のコマを 1 枚の jpg に
  束ねる（演出の位置・タイミングを目視で確かめる）

実体は :mod:`app.textimage` / :mod:`app.contact_sheet` と
:mod:`app.library`（登録）にあり、ここは HTTP の入り口だけ。外部 API
（:mod:`app.routers.external`）は下の 3 つのヘルパーをそのまま共用する。
"""

from fastapi import APIRouter, HTTPException

from .. import library as service
from .. import media_ref, textimage
from ..models import (
    ContactSheet,
    ContactSheetResult,
    FontFace,
    FontList,
    LibraryItem,
    TextImage,
)
from .library import _bad_request

images_router = APIRouter(prefix="/api/images", tags=["images"])
videos_router = APIRouter(prefix="/api/videos", tags=["videos"])


# --------------------------------------------------------------------------
# 共用のヘルパー（内部 API と外部 API が同じものを呼ぶ）
# --------------------------------------------------------------------------

def font_list() -> FontList:
    """インストール済みの書体の一覧と、既定で使われる書体。"""
    faces = textimage.list_fonts()
    default = textimage.default_font()
    return FontList(
        fonts=[
            FontFace(
                name=face.name,
                family=face.family,
                style=face.style,
                path=face.path,
                index=face.index,
            )
            for face in faces
        ],
        default=default.name if default else "",
    )


async def create_text_image(payload: TextImage) -> LibraryItem:
    """文字を描いてライブラリ項目にする。"""
    outline = payload.outline
    try:
        return await service.add_text_image(
            payload.text,
            name=payload.name,
            tags=payload.tags,
            font=payload.font,
            size=payload.size,
            color=payload.color,
            outline_color=(outline.color if outline else ""),
            outline_width=(outline.width if outline else 0),
            background=payload.bg,
            rotate=payload.rotate,
            padding=payload.padding,
            align=payload.align,
        )
    except service.LibraryError as exc:
        raise _bad_request(exc) from exc


async def create_contact_sheet(payload: ContactSheet) -> ContactSheetResult:
    """動画からコマを抜いてグリッド画像にし、ライブラリ項目にする。"""
    try:
        media = await media_ref.resolve(payload.source)
    except media_ref.MediaRefNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except media_ref.MediaRefError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        item, seconds = await service.add_contact_sheet(
            media.path,
            name=payload.name or f"{media.name}（コンタクトシート）",
            tags=payload.tags,
            nsfw=media.nsfw,
            seconds=payload.seconds,
            span=(payload.range.model_dump() if payload.range else None),
            frames=payload.frames,
            columns=payload.columns,
            width=payload.width,
            labels=payload.labels,
        )
    except service.LibraryError as exc:
        raise _bad_request(exc) from exc
    return ContactSheetResult(item=item, seconds=seconds, columns=payload.columns)


# --------------------------------------------------------------------------
# 画像（フォント画像）
# --------------------------------------------------------------------------

@images_router.get("/text/fonts", response_model=FontList)
async def list_text_fonts() -> FontList:
    """使える書体の一覧（``POST /api/images/text`` の ``font`` に書く名前）。

    ``/text`` より先に定義しておく（順序が逆でも衝突はしないが、読み順を揃える）。
    """
    return font_list()


@images_router.post("/text", response_model=LibraryItem, status_code=201)
async def create_text_image_route(payload: TextImage) -> LibraryItem:
    """フォントで組んだ文字を PNG にしてライブラリへ登録する（SPEC §7.2）。

    背景は既定で透明なので、そのまま Remotion の ``sprite`` / ``imageSlam`` の
    ``src`` に書ける。画像生成に日本語の字形を教える参照としても使う。
    """
    return await create_text_image(payload)


# --------------------------------------------------------------------------
# 動画（コンタクトシート）
# --------------------------------------------------------------------------

@videos_router.post("/contact-sheet", response_model=ContactSheetResult,
                    status_code=201)
async def create_contact_sheet_route(payload: ContactSheet) -> ContactSheetResult:
    """動画のコマを 1 枚のグリッド画像に束ねてライブラリへ登録する（SPEC §7.2）。

    抜く秒は ``seconds`` → ``range`` → ``frames`` の順に見て、どれも無ければ
    尺を等分した位置になる。応答の ``seconds`` に実際に抜いた秒が並ぶ。
    """
    return await create_contact_sheet(payload)
