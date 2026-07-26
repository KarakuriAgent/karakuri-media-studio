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

CREATE TABLE IF NOT EXISTS agent_sessions (
  id           TEXT PRIMARY KEY,
  created_at   TEXT NOT NULL,
  title        TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL DEFAULT 'idle',
  checkin_mode TEXT NOT NULL DEFAULT 'milestone',
  auto_limit   INTEGER NOT NULL DEFAULT 5,
  messages     TEXT NOT NULL DEFAULT '[]',
  plan         TEXT NOT NULL DEFAULT '{}',
  artifacts    TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_created_at
  ON agent_sessions(created_at DESC);
"""

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
    """Create tables. LoRA registrations are user data (managed in the settings
    screen), so nothing is seeded here."""
    async with get_db() as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()
