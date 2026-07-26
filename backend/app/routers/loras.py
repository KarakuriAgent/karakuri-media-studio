from fastapi import APIRouter, HTTPException

from ..db import get_db
from ..models import Lora, LoraCreate, LoraUpdate

router = APIRouter(prefix="/api/loras", tags=["loras"])


@router.get("", response_model=list[Lora])
async def list_loras() -> list[Lora]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM loras ORDER BY sort_order, id"
        ) as cur:
            rows = await cur.fetchall()
    return [Lora(**dict(r)) for r in rows]


@router.get("/{lora_id}", response_model=Lora)
async def get_lora(lora_id: int) -> Lora:
    async with get_db() as conn:
        async with conn.execute("SELECT * FROM loras WHERE id = ?", (lora_id,)) as cur:
            row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="lora not found")
    return Lora(**dict(row))


@router.post("", response_model=Lora, status_code=201)
async def create_lora(payload: LoraCreate) -> Lora:
    async with get_db() as conn:
        cur = await conn.execute(
            "INSERT INTO loras (display_name, lora_name, trigger_word,"
            " default_strength, default_audio, sort_order)"
            " VALUES (:display_name, :lora_name, :trigger_word,"
            " :default_strength, :default_audio, :sort_order)",
            payload.model_dump(),
        )
        await conn.commit()
        new_id = cur.lastrowid
    return await get_lora(new_id)


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
