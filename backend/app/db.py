from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from .paths import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id            TEXT PRIMARY KEY,
  created_at    TEXT NOT NULL,
  mode          TEXT NOT NULL,
  status        TEXT NOT NULL,
  user_input    TEXT,
  image_prompt  TEXT,
  video_prompt  TEXT,
  grok_raw      TEXT,
  params        TEXT NOT NULL,
  workflow_json TEXT NOT NULL,
  comfy_prompt_id TEXT,
  image_path    TEXT,
  video_path    TEXT,
  last_frame_path TEXT,
  source_image  TEXT,
  audio_path    TEXT,
  error         TEXT
);

CREATE TABLE IF NOT EXISTS loras (
  id            INTEGER PRIMARY KEY,
  display_name  TEXT NOT NULL,
  lora_name     TEXT NOT NULL,
  trigger_word  TEXT NOT NULL,
  default_strength REAL DEFAULT 1.0,
  default_audio TEXT,
  sort_order    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chat_sessions (
  id         TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  job_id     TEXT,
  messages   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
"""

SEED_LORAS = [
    {
        "display_name": "かおり",
        "lora_name": "kohei06__yui__kaori.safetensors",
        "trigger_word": "muted minimalist sketch style",
        "default_strength": 1.0,
        "default_audio": None,
        "sort_order": 0,
    }
]


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        await conn.close()


async def init_db() -> None:
    async with get_db() as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()
        await _seed_loras(conn)


async def _seed_loras(conn: aiosqlite.Connection) -> None:
    async with conn.execute("SELECT COUNT(*) AS n FROM loras") as cur:
        row = await cur.fetchone()
    if row["n"]:
        return
    for lora in SEED_LORAS:
        await conn.execute(
            "INSERT INTO loras (display_name, lora_name, trigger_word,"
            " default_strength, default_audio, sort_order)"
            " VALUES (:display_name, :lora_name, :trigger_word,"
            " :default_strength, :default_audio, :sort_order)",
            lora,
        )
    await conn.commit()
