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
  extra_outputs TEXT,                       -- 主成果物に収まらない出力（JSON 配列）
  error         TEXT,
  nsfw          INTEGER NOT NULL DEFAULT 0,
  nsfw_source   TEXT NOT NULL DEFAULT '',
  credits_consumed REAL                     -- 外部バックエンドが消費したクレジット（§5.2）
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
  family        TEXT NOT NULL DEFAULT 'krea2',
  comfy_target  TEXT                        -- 置いてある接続先（NULL = 全環境共通）
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
  tags          TEXT NOT NULL DEFAULT '[]',
  category      TEXT                        -- 分類（character/background/prop。NULL = 未分類）
);

-- ドラマスタジオ: プロジェクト（1 本の作品）。脚本（studio_shots）と素材
-- （studio_assets）の入れ物で、Shot ごとの生成結果は studio_takes が持つ。
CREATE TABLE IF NOT EXISTS studio_projects (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  code        TEXT NOT NULL DEFAULT '',   -- 作品コード（任意。空でよい）
  synopsis    TEXT NOT NULL DEFAULT '',
  world_notes TEXT NOT NULL DEFAULT '',   -- World Bible の覚え書き
  auto_translate INTEGER NOT NULL DEFAULT 1, -- 日本語プロンプトを Grok で英訳してから投入
  latent_continuity INTEGER NOT NULL DEFAULT 0, -- 引き継ぎを Motion Context（ラテント連続性）で行う
  canvas_x    REAL NOT NULL DEFAULT 0,      -- キャンバス（別ビュー）の表示位置
  canvas_y    REAL NOT NULL DEFAULT 0,
  canvas_zoom REAL NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

-- 話（エピソード）と場（シーン）。Shot の上に 2 段だけ持つ入れ物で、
-- どちらも「並び順つきの見出し」以上のことはしない（生成には効かない）。
CREATE TABLE IF NOT EXISTS studio_episodes (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  sort_order INTEGER NOT NULL DEFAULT 0,
  title      TEXT NOT NULL DEFAULT '',
  synopsis   TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS studio_scenes (
  id          TEXT PRIMARY KEY,
  episode_id  TEXT NOT NULL REFERENCES studio_episodes(id) ON DELETE CASCADE,
  project_id  TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  title       TEXT NOT NULL DEFAULT '',
  synopsis    TEXT NOT NULL DEFAULT '',
  time_of_day TEXT NOT NULL DEFAULT '',   -- 「夜明け前」「閉店後」などの時間帯メモ
  created_at  TEXT NOT NULL
);

-- リビジョン履歴（軽量版）: プロジェクトの状態を変えるたびに、その時点の
-- projects / episodes / scenes / shots / assets を丸ごと JSON で残す。Take と
-- ファイル実体は対象外（実行結果は履歴で戻すものではない。app/studio.py）。
CREATE TABLE IF NOT EXISTS studio_revisions (
  id            TEXT PRIMARY KEY,
  project_id    TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  seq           INTEGER NOT NULL,           -- プロジェクトごとの 1 始まりの連番
  actor         TEXT NOT NULL DEFAULT 'user',  -- user / agent
  action        TEXT NOT NULL DEFAULT '',   -- 変更内容の短い説明（日本語）
  snapshot_json TEXT NOT NULL,
  created_at    TEXT NOT NULL
);

-- World Bible の素材（キャラ・場所・小道具・その他の参照）。実体は assets/ の
-- 下に置き、プロンプトからは `@名前` で呼ぶ（app/studio.py のメンション解決）。
CREATE TABLE IF NOT EXISTS studio_assets (
  id             TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  name           TEXT NOT NULL,               -- `@名前` で呼ぶ識別名（プロジェクト内で一意）
  category       TEXT NOT NULL DEFAULT 'reference',
  caption        TEXT NOT NULL DEFAULT '',    -- 人間向けの説明（日本語可）
  prompt_caption TEXT NOT NULL DEFAULT '',    -- 生成プロンプトに入る説明（英語推奨）
  kind           TEXT NOT NULL,               -- image / video / audio
  path           TEXT NOT NULL DEFAULT '',    -- ファイルの絶対パス（空 = メタデータのみの素材）
  profile        TEXT NOT NULL DEFAULT '{}',  -- 分類ごとの拡張項目（JSON。app/models.py）
  locked         INTEGER NOT NULL DEFAULT 0,  -- 差し替え禁止の印
  sort_order     INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  updated_at     TEXT,                        -- 最後に書き換えた時刻（NULL = 作成のまま）
  prompt_updated_at TEXT                      -- プロンプトに効く項目を変えた時刻（stale 判定用）
);

-- 素材にぶら下がる追加リファレンス（キャラの声サンプル・動画リファレンス・
-- 追加画像）。メインのファイルは studio_assets.path のままで、こちらは
-- 「何本でも足せる参照」を持つ。project_id を持たせてあるのは、リビジョンの
-- スナップショット（app/studio.py の _SNAPSHOT_TABLES）が project_id で
-- 束ねて書き戻すため（studio_takes と同じ持ち方）。
CREATE TABLE IF NOT EXISTS studio_asset_files (
  id         TEXT PRIMARY KEY,
  asset_id   TEXT NOT NULL REFERENCES studio_assets(id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  role       TEXT NOT NULL DEFAULT 'image',  -- image / voice / video
  path       TEXT NOT NULL DEFAULT '',       -- ファイルの絶対パス
  caption    TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

-- 脚本の 1 カット。prompt が生成に渡る本文で、台詞・SE・BGM・カメラは
-- MiniMax H3 のプロンプト規約に沿って投入時に本文へ組み立てる。
CREATE TABLE IF NOT EXISTS studio_shots (
  id                   TEXT PRIMARY KEY,
  project_id           TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  scene_id             TEXT,                       -- 所属する場（NULL = 未分類。FK は張らず、場を消したら NULL に戻す）
  sort_order           INTEGER NOT NULL DEFAULT 0,
  title                TEXT NOT NULL DEFAULT '',
  purpose              TEXT NOT NULL DEFAULT '',   -- 物語上の目的
  action               TEXT NOT NULL DEFAULT '',
  dialogue             TEXT NOT NULL DEFAULT '',   -- 台詞
  soundscape           TEXT NOT NULL DEFAULT '',   -- 効果音・環境音
  bgm                  TEXT NOT NULL DEFAULT '',
  camera               TEXT NOT NULL DEFAULT '',
  duration_seconds     REAL NOT NULL DEFAULT 5.0,  -- MiniMax H3 は 1〜15 秒
  prompt               TEXT NOT NULL DEFAULT '',   -- `@素材名` メンション可
  status               TEXT NOT NULL DEFAULT 'draft',
  selected_take_id     TEXT,                       -- 採用した Take（FK は張らない: 相互参照になるため）
  carry_over_end_frame INTEGER NOT NULL DEFAULT 0, -- 直前 Shot のラストフレームを開始フレームに使う
  -- Shot ごとの生成設定（NULL = JobCreate の既定値のまま）。MiniMax H3 が実際に
  -- 受け取れるものだけ持つ（否定プロンプトは H3 に注入先が無いので置かない）。
  aspect_ratio         TEXT,                       -- 例 '16:9 (Widescreen)'
  megapixels           REAL,                       -- 解像度の目安（比と合わせて幅×高さになる）
  seed                 INTEGER,                    -- NULL = 毎回ランダム
  workflow_override    TEXT,                       -- NULL = t2v/i2v/r2v を自動選択
  nsfw                 INTEGER NOT NULL DEFAULT 0, -- 1 = 投入するジョブに NSFW の印を付ける（manual）
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  prompt_updated_at    TEXT                        -- プロンプトに効く項目を変えた時刻（stale 判定用）
);

-- Shot 1 回ぶんの生成。実行状態は jobs 側が持つので、ここに置くのは
-- 採用／不採用だけ（読み取り時に jobs と join して導出する。app/studio.py）。
CREATE TABLE IF NOT EXISTS studio_takes (
  id         TEXT PRIMARY KEY,
  shot_id    TEXT NOT NULL REFERENCES studio_shots(id) ON DELETE CASCADE,
  project_id TEXT NOT NULL,
  job_id     TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'rendering',
  created_at TEXT NOT NULL,
  prompt        TEXT NOT NULL DEFAULT '',  -- 実際に投入した本文
  source_prompt TEXT NOT NULL DEFAULT '',  -- 英訳する前の原文（訳していなければ空）
  warning       TEXT NOT NULL DEFAULT '',  -- 投入はできたが伝えたいこと（英訳の失敗など）
  latent_path   TEXT                       -- ラテント連続性で保存した AV ラテント（ComfyUI 側のパス。NULL = 無し）
);

-- キャンバス: スタジオの中身を「座標を持つカード」として並べる別ビュー。
-- カードは中身のデータを持たず、スタジオ側の行（素材・場・Shot・Take）への
-- 参照と置き場所だけを覚える（唯一の正は studio_* のまま）。対応する
-- エンティティが無い text / model のカードだけ、中身を data に持つ。
CREATE TABLE IF NOT EXISTS canvas_cards (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL,                -- character / location / object / style /
                                           -- reference / scene / shot / media / text / model
  entity_id  TEXT,                         -- スタジオ側の行 ID（text / model は NULL）
  -- どのタブ（話）に置いてあるか。参照カードの所属はスタジオのデータから
  -- 導けるので NULL のままで、この列を使うのは text / model カードだけ
  -- （NULL = 作品共通のタブ）。話ごと消えたら作品共通に戻す。
  episode_id TEXT REFERENCES studio_episodes(id) ON DELETE SET NULL,
  data       TEXT NOT NULL DEFAULT '{}',   -- キャンバス専用 kind の中身（JSON）
  x          REAL NOT NULL DEFAULT 0,
  y          REAL NOT NULL DEFAULT 0,
  w          REAL NOT NULL DEFAULT 320,
  h          REAL NOT NULL DEFAULT 220,
  z          INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- キャンバスの表示位置のうち、**話タブ**のぶん。作品共通のタブは
-- studio_projects の canvas_x / canvas_y / canvas_zoom に持つ（キャンバスを
-- 話ごとに分ける前からある列で、既存の作品の表示位置をそのまま引き継ぐ）。
CREATE TABLE IF NOT EXISTS canvas_viewports (
  project_id TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  episode_id TEXT NOT NULL REFERENCES studio_episodes(id) ON DELETE CASCADE,
  x          REAL NOT NULL DEFAULT 0,
  y          REAL NOT NULL DEFAULT 0,
  zoom       REAL NOT NULL DEFAULT 1,
  PRIMARY KEY (project_id, episode_id)
);

-- キャンバスのチャット履歴（エージェント実行は別途。ここは保存だけ）。
CREATE TABLE IF NOT EXISTS canvas_messages (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  ts         TEXT NOT NULL,
  role       TEXT NOT NULL,                -- user / assistant / event
  content    TEXT NOT NULL,
  kind       TEXT,                         -- event の種別（action_result 等）
  data       TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_library_created_at ON library(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_created_at
  ON agent_sessions(created_at DESC);

-- 作品コードは「付けたなら重複させない」。空文字は未設定なので対象から外す。
CREATE UNIQUE INDEX IF NOT EXISTS idx_studio_projects_code
  ON studio_projects(code) WHERE code <> '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_studio_assets_name
  ON studio_assets(project_id, name);
CREATE INDEX IF NOT EXISTS idx_studio_asset_files_asset
  ON studio_asset_files(asset_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_studio_shots_project
  ON studio_shots(project_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_studio_takes_shot
  ON studio_takes(shot_id, created_at);
CREATE INDEX IF NOT EXISTS idx_studio_episodes_project
  ON studio_episodes(project_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_studio_scenes_episode
  ON studio_scenes(episode_id, sort_order);
-- リビジョンの連番はプロジェクトごとに 1 本（同じ seq を 2 つ作らない）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_studio_revisions_seq
  ON studio_revisions(project_id, seq);

CREATE INDEX IF NOT EXISTS idx_canvas_cards_project
  ON canvas_cards(project_id, z);
-- 同じエンティティを指すカードは 1 枚だけ（キャンバス上の分身を作らない）。
-- text / model は entity_id が NULL なので対象外。
CREATE UNIQUE INDEX IF NOT EXISTS idx_canvas_cards_entity
  ON canvas_cards(project_id, entity_id) WHERE entity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_canvas_messages_project
  ON canvas_messages(project_id, ts);

-- 参照先が消えたカードは残さない（孤児化の防止）。スタジオ API 経由の削除
-- だけでなく、親を消した ON DELETE CASCADE やリビジョン復元での行の入れ替えも
-- ここを通るので、消し忘れが出ない。
CREATE TRIGGER IF NOT EXISTS trg_canvas_cards_asset_deleted
AFTER DELETE ON studio_assets BEGIN
  DELETE FROM canvas_cards WHERE entity_id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_canvas_cards_scene_deleted
AFTER DELETE ON studio_scenes BEGIN
  DELETE FROM canvas_cards WHERE entity_id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_canvas_cards_shot_deleted
AFTER DELETE ON studio_shots BEGIN
  DELETE FROM canvas_cards WHERE entity_id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_canvas_cards_take_deleted
AFTER DELETE ON studio_takes BEGIN
  DELETE FROM canvas_cards WHERE entity_id = OLD.id;
END;
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
        # 主成果物（image_path / video_path / audio_output_path）に収まらない
        # 追加の出力のパス（JSON 配列）。1 回の生成で複数返すモデル（たとえば
        # 1 リクエストで 2 曲）のため。既存行は NULL = 追加成果物なし。
        ("extra_outputs", "TEXT"),
        # 外部バックエンドのジョブが消費したクレジット（SPEC §5.2）。
        # ComfyUI のジョブは自前 GPU なので NULL のままでよい。
        ("credits_consumed", "REAL"),
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
        # 素材の分類（character / background / prop）。既存行は NULL＝未分類の
        # ままでよい（タグと違って 1 件に 1 つだけ持つ、棚の仕切りにあたる値）。
        ("category", "TEXT"),
    ],
    "loras": [
        ("sample_images", "TEXT NOT NULL DEFAULT '[]'"),
        # 'image' = 画像ワークフロー用 / 'video' = 動画ワークフロー用。
        # 既存レコードは画像用として登録されていたので既定値は 'image'。
        ("target", "TEXT NOT NULL DEFAULT 'image'"),
        # 画像 LoRA のモデルファミリー（krea2 / anima / z-image / qwen-image）。
        # 画像ワークフローが選択式になる前の既存行はすべて krea2 用なので、
        # 既定値 'krea2' がそのままマイグレーション後の値になる。
        # target='video' の行では無視される（動画 LoRA はファミリーを持たない）。
        ("family", "TEXT NOT NULL DEFAULT 'krea2'"),
        # どの接続先環境（ComfyCloud / RunPod / ローカル）に置いてある LoRA か。
        # 既定は NULL = 「全環境で出す」: 接続先を分ける前に登録した行がどの環境の
        # ものか分からないので、勝手に 1 環境へ寄せず今までどおり全部で見せる
        # （環境を絞りたければ設定ページで選び直せる）。
        ("comfy_target", "TEXT"),
    ],
    # ドラマスタジオ（studio_* は先に作られている DB があるので ALTER が要る）
    "studio_projects": [
        # 日本語のプロンプトを Grok で英語に直してから投入するか。既存の
        # プロジェクトも既定 ON（H3 は英語プロンプト前提のモデル）。
        ("auto_translate", "INTEGER NOT NULL DEFAULT 1"),
        # キャンバスの表示位置（別ビューなのでプロジェクト側に持つ）。既存の
        # プロジェクトは原点・等倍から始まる。
        ("canvas_x", "REAL NOT NULL DEFAULT 0"),
        ("canvas_y", "REAL NOT NULL DEFAULT 0"),
        ("canvas_zoom", "REAL NOT NULL DEFAULT 1"),
        # 引き継ぎを Motion Context（ラテント連続性）で行うか。既存の
        # プロジェクトは既定 OFF = 今までどおりラストフレームの引き継ぎ。
        ("latent_continuity", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "studio_assets": [
        # ファイル実体を持たない「名前とキャプションだけ」の素材を許すので、
        # 既存 DB の NOT NULL 列はそのまま空文字で使う（ALTER では外せない）。
        ("updated_at", "TEXT"),
        ("prompt_updated_at", "TEXT"),
        # 分類ごとの拡張項目（キャラの外見・声、画風のパレットなど）。既存行は
        # 空の JSON で、検証スキーマは :data:`app.models.ASSET_PROFILE_MODELS`。
        ("profile", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "studio_shots": [
        ("scene_id", "TEXT"),
        # Shot ごとの生成設定。既存行は NULL = 今までどおり JobCreate の既定値。
        ("aspect_ratio", "TEXT"),
        ("megapixels", "REAL"),
        ("seed", "INTEGER"),
        ("workflow_override", "TEXT"),
        # 既存行は 0 = 今までどおり投入後の自動判定に任せる。
        ("nsfw", "INTEGER NOT NULL DEFAULT 0"),
        ("prompt_updated_at", "TEXT"),
    ],
    "canvas_cards": [
        # カードを置いたタブ（話）。参照カードの所属はスタジオのデータから
        # 導くので NULL のままで、使うのは text / model カードだけ。既存の
        # カードはすべて NULL = 作品共通のタブになる。
        ("episode_id", "TEXT REFERENCES studio_episodes(id) ON DELETE SET NULL"),
    ],
    "studio_takes": [
        ("prompt", "TEXT NOT NULL DEFAULT ''"),
        ("source_prompt", "TEXT NOT NULL DEFAULT ''"),
        ("warning", "TEXT NOT NULL DEFAULT ''"),
        # ラテント連続性で保存した AV ラテントのパス（ComfyUI 側）。既存行と、
        # ラテント連続性を使わなかった Take は NULL のまま。
        ("latent_path", "TEXT"),
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
