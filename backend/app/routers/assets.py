import re
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..ids import new_id
from ..models import Asset
from ..paths import ASSETS_DIR

router = APIRouter(prefix="/api/assets", tags=["assets"])

AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

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


def _list(kind: str, allowed: set[str]) -> list[Asset]:
    d = _kind_dir(kind)
    files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in allowed]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [_as_asset(p, kind) for p in files]


@router.post("/audio", response_model=Asset, status_code=201)
async def upload_audio(file: UploadFile = File(...)) -> Asset:
    return await _save(file, "audio", AUDIO_EXT)


@router.get("/audio", response_model=list[Asset])
async def list_audio() -> list[Asset]:
    return _list("audio", AUDIO_EXT)


@router.post("/image", response_model=Asset, status_code=201)
async def upload_image(file: UploadFile = File(...)) -> Asset:
    return await _save(file, "image", IMAGE_EXT)


@router.get("/image", response_model=list[Asset])
async def list_image() -> list[Asset]:
    return _list("image", IMAGE_EXT)
