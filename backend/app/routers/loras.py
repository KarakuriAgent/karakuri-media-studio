import json
import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import lora_samples
from ..db import get_db
from ..ids import new_id
from ..models import Lora, LoraCreate, LoraUpdate

router = APIRouter(prefix="/api/loras", tags=["loras"])


@router.get("", response_model=list[Lora])
async def list_loras() -> list[Lora]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM loras ORDER BY sort_order, id"
        ) as cur:
            rows = await cur.fetchall()
    return [lora_samples.row_to_lora(r) for r in rows]


@router.get("/{lora_id}", response_model=Lora)
async def get_lora(lora_id: int) -> Lora:
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM loras WHERE id = ?", (lora_id,)) as cur:
            row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="lora not found")
    return lora_samples.row_to_lora(row)


@router.post("", response_model=Lora, status_code=201)
async def create_lora(payload: LoraCreate) -> Lora:
    async with get_db() as conn:
        cur = await conn.execute(
            "INSERT INTO loras (display_name, lora_name, trigger_word,"
            " default_strength, default_audio, sort_order, target)"
            " VALUES (:display_name, :lora_name, :trigger_word,"
            " :default_strength, :default_audio, :sort_order, :target)",
            payload.model_dump(),
        )
        await conn.commit()
        new_id_ = cur.lastrowid
    return await get_lora(new_id_)


@router.put("/{lora_id}", response_model=Lora)
async def update_lora(lora_id: int, payload: LoraUpdate) -> Lora:
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return await get_lora(lora_id)
    assignments = ", ".join(f"{k} = :{k}" for k in fields)
    async with get_db() as conn:
        cur = await conn.execute(
            f"UPDATE loras SET {assignments} WHERE id = :id",
            {**fields, "id": lora_id},
        )
        await conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="lora not found")
    return await get_lora(lora_id)


@router.delete("/{lora_id}", status_code=204)
async def delete_lora(lora_id: int) -> None:
    async with get_db() as conn:
        cur = await conn.execute("DELETE FROM loras WHERE id = ?", (lora_id,))
        await conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="lora not found")
    shutil.rmtree(lora_samples.sample_dir(lora_id), ignore_errors=True)


# --------------------------------------------------------------------------
# sample images
# --------------------------------------------------------------------------

async def _save_sample_names(lora_id: int, names: list[str]) -> None:
    async with get_db() as conn:
        await conn.execute(
            "UPDATE loras SET sample_images = ? WHERE id = ?",
            (json.dumps(names), lora_id),
        )
        await conn.commit()


@router.post("/{lora_id}/samples", response_model=Lora, status_code=201)
async def upload_sample(lora_id: int, file: UploadFile = File(...)) -> Lora:
    """サンプル画像を 1 枚追加する（assets/lora_samples/<id>/ に保存）。"""
    lora = await get_lora(lora_id)
    original = Path(file.filename or "sample")
    ext = original.suffix.lower()
    if ext not in lora_samples.SAMPLE_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported extension '{ext}'"
            f" (allowed: {sorted(lora_samples.SAMPLE_EXT)})",
        )
    directory = lora_samples.sample_dir(lora_id)
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{lora_samples.safe_stem(original.stem)}_{new_id()}{ext}"
    (directory / name).write_bytes(await file.read())
    await _save_sample_names(lora_id, [*lora_samples.sample_names(lora), name])
    return await get_lora(lora_id)


@router.delete("/{lora_id}/samples/{name}", response_model=Lora)
async def delete_sample(lora_id: int, name: str) -> Lora:
    lora = await get_lora(lora_id)
    base = Path(name).name
    names = lora_samples.sample_names(lora)
    if base not in names:
        raise HTTPException(status_code=404, detail="sample image not found")
    (lora_samples.sample_dir(lora_id) / base).unlink(missing_ok=True)
    await _save_sample_names(lora_id, [n for n in names if n != base])
    return await get_lora(lora_id)
