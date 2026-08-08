import re
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..ids import new_id
from ..models import Asset
from ..paths import ASSETS_DIR

router = APIRouter(prefix="/api/assets", tags=["assets"])

AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
# reference clips for the motion-transfer workflow (SPEC §3.1)
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov"}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _kind_dir(kind: str) -> Path:
    d = ASSETS_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def _as_asset(path: Path, kind: str) -> Asset:
    return Asset(
        name=path.name,
        kind=kind,
        path=str(path),
        url=f"/assets/{kind}/{path.name}",
        size=path.stat().st_size,
    )


async def _save(upload: UploadFile, kind: str, allowed: set[str]) -> Asset:
    original = Path(upload.filename or "upload")
    ext = original.suffix.lower()
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported extension '{ext}' (allowed: {sorted(allowed)})",
        )
    stem = _SAFE.sub("_", original.stem)[:64] or "asset"
    dest = _kind_dir(kind) / f"{stem}_{new_id()}{ext}"
    dest.write_bytes(await upload.read())
    return _as_asset(dest, kind)


#: 種別 -> 受け付ける拡張子（:func:`save_upload` の入り口）
ALLOWED_EXT: dict[str, set[str]] = {
    "image": IMAGE_EXT,
    "video": VIDEO_EXT,
    "audio": AUDIO_EXT,
}


async def save_upload(upload: UploadFile, kind: str) -> Asset:
    """種別ごとの拡張子で ``assets/<kind>/`` に保存する（ドラマスタジオからも使う）。"""
    allowed = ALLOWED_EXT.get(kind)
    if allowed is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown kind '{kind}' (allowed: {', '.join(ALLOWED_EXT)})",
        )
    return await _save(upload, kind, allowed)


def list_assets(kind: str, allowed: set[str]) -> list[Asset]:
    d = _kind_dir(kind)
    files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in allowed]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [_as_asset(p, kind) for p in files]


@router.post("/audio", response_model=Asset, status_code=201)
async def upload_audio(file: UploadFile = File(...)) -> Asset:
    return await _save(file, "audio", AUDIO_EXT)


@router.get("/audio", response_model=list[Asset])
async def list_audio() -> list[Asset]:
    return list_assets("audio", AUDIO_EXT)


@router.post("/image", response_model=Asset, status_code=201)
async def upload_image(file: UploadFile = File(...)) -> Asset:
    return await _save(file, "image", IMAGE_EXT)


@router.get("/image", response_model=list[Asset])
async def list_image() -> list[Asset]:
    return list_assets("image", IMAGE_EXT)


@router.post("/video", response_model=Asset, status_code=201)
async def upload_video(file: UploadFile = File(...)) -> Asset:
    return await _save(file, "video", VIDEO_EXT)


@router.get("/video", response_model=list[Asset])
async def list_video() -> list[Asset]:
    return list_assets("video", VIDEO_EXT)
