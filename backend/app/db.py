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
  audio_prompt  TEXT,
  grok_raw      TEXT,
  params        TEXT NOT NULL,
  workflow_json TEXT NOT NULL,
  comfy_prompt_id TEXT,
  image_path    TEXT,
  video_path    TEXT,
  last_frame_path TEXT,
  source_image  TEXT,
  audio_path    TEXT,
  audio_output_path TEXT,
  error         TEXT,
  nsfw          INTEGER NOT NULL DEFAULT 0,
  nsfw_source   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS loras (
  id            INTEGER PRIMARY KEY,
  display_name  TEXT NOT NULL,
  lora_name     TEXT NOT NULL,
  trigger_word  TEXT NOT NULL,
  default_strength REAL DEFAULT 1.0,
  default_audio TEXT,
  sort_order    INTEGER DEFAULT 0,
  sample_images TEXT NOT NULL DEFAULT '[]',
  target        TEXT NOT NULL DEFAULT 'image',
  family        TEXT NOT NULL DEFAULT 'krea2'
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
  artifacts    TEXT NOT NULL DEFAULT '[]',
  nsfw         INTEGER NOT NULL DEFAULT 0,
  nsfw_source  TEXT NOT NULL DEFAULT ''
);

-- ライブラリ（SPEC §7.2）: 履歴とは別に取っておく素材の目録。ファイル実体は
-- library/{kind}/ に置き、/library で静的配信される。
CREATE TABLE IF NOT EXISTS library (
  id            TEXT PRIMARY KEY,
  created_at    TEXT NOT NULL,
  kind          TEXT NOT NULL,
  name          TEXT NOT NULL,
  path          TEXT NOT NULL,
  nsfw          INTEGER NOT NULL DEFAULT 0,
  nsfw_source   TEXT NOT NULL DEFAULT '',
  source_job_id TEXT,
  source        TEXT,                       -- 元ジョブのどの出力か（image/last_frame/video/audio）
  tags          TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_library_created_at ON library(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_created_at
  ON agent_sessions(created_at DESC);
"""

# 既存 DB に後から足したカラム: {テーブル: [(カラム名, 定義), …]}。
# CREATE TABLE IF NOT EXISTS では既存テーブルに反映されないため、起動時に
# PRAGMA table_info と比べて足りないものだけ ALTER TABLE で追加する。
MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "jobs": [
        ("nsfw", "INTEGER NOT NULL DEFAULT 0"),
        ("nsfw_source", "TEXT NOT NULL DEFAULT ''"),
        # 音声ジョブ（mode='audio'）。既存行はどちらも NULL のままでよい:
        # audio_prompt は音声ジョブの指示、audio_output_path は生成された MP3。
        # 入力のリファレンス音声を保持する audio_path とは別物。
        ("audio_prompt", "TEXT"),
        ("audio_output_path", "TEXT"),
    ],
    "agent_sessions": [
        ("nsfw", "INTEGER NOT NULL DEFAULT 0"),
        ("nsfw_source", "TEXT NOT NULL DEFAULT ''"),
    ],
    "library": [
        # 分類タグ（JSON 配列。loras.sample_images と同じ持ち方）。ライブラリを
        # 後から追加したときの既存行は空配列になる。
        ("tags", "TEXT NOT NULL DEFAULT '[]'"),
        # 元ジョブのどの出力か。source_job_id だけでは生成画像とラストフレームを
        # 区別できないため後から追加した。既存行は NULL（＝どの出力か不明）で、
        # 重複判定の対象にしない。
        ("source", "TEXT"),
    ],
    "loras": [
        ("sample_images", "TEXT NOT NULL DEFAULT '[]'"),
        # 'image' = 画像ワークフロー用 / 'video' = LTX 2.3 動画ワークフロー用。
        # 既存レコードは画像用として登録されていたので既定値は 'image'。
        ("target", "TEXT NOT NULL DEFAULT 'image'"),
        # 画像 LoRA のモデルファミリー（krea2 / anima / z-image / qwen-image）。
        # 画像ワークフローが選択式になる前の既存行はすべて krea2 用なので、
        # 既定値 'krea2' がそのままマイグレーション後の値になる。
        # target='video' の行では無視される（動画は LTX 2.3 のみ）。
        ("family", "TEXT NOT NULL DEFAULT 'krea2'"),
    ],
}


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        await conn.close()


async def _migrate(conn: aiosqlite.Connection) -> None:
    """Add the columns :data:`MIGRATIONS` lists but the existing DB lacks."""
    for table, columns in MIGRATIONS.items():
        async with conn.execute(f"PRAGMA table_info({table})") as cur:
            existing = {row["name"] for row in await cur.fetchall()}
        for name, definition in columns:
            if name not in existing:
                await conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                )


async def init_db() -> None:
    """Create tables. LoRA registrations are user data (managed in the settings
    screen), so nothing is seeded here."""
    async with get_db() as conn:
        await conn.executescript(SCHEMA)
        await _migrate(conn)
        await conn.commit()
