from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

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
  credits_consumed REAL,                    -- 外部バックエンドが消費したクレジット（§5.2）
  chat_session_id TEXT                      -- 生成フォームの chat / エージェント session。エージェント発判定に使う
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
  messages   TEXT NOT NULL,
  grok_session_id TEXT NOT NULL DEFAULT '',  -- 続き用の grok セッション（正本は messages）
  grok_cwd   TEXT NOT NULL DEFAULT ''        -- このチャットの作業ディレクトリ
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
  nsfw_source  TEXT NOT NULL DEFAULT '',
  grok_session_id TEXT NOT NULL DEFAULT '',
  grok_cwd     TEXT NOT NULL DEFAULT '',
  snapshot_key TEXT NOT NULL DEFAULT ''
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
  quality     TEXT NOT NULL DEFAULT 'normal', -- 動画生成の品質（normal / opt / turbo）
  megapixels  REAL,                        -- 動画生成のメガピクセル（NULL = ワークフローの既定）
  aspect_ratio TEXT,                       -- 動画生成のアスペクト比（NULL = 既定）
  steps       INTEGER NOT NULL DEFAULT 0,  -- サンプリング回数（0 = テンプレートの既定のまま）
  nsfw        INTEGER NOT NULL DEFAULT 0,   -- 1 = この作品から投入するジョブはすべて NSFW（0 = 非 NSFW 固定）
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
  -- **場の中での**並び順（未分類なら「作品の未分類グループ」の中での並び順）。
  -- 表示順は 話.sort_order -> 場.sort_order -> この値 の階層で決まり、未分類は
  -- 作品の末尾にまとまる（app/studio.py の _fetch_shots）。作品全体で 1 本の
  -- 連番だった頃の DB は起動時に場ごとの 0..n へ振り直す（_renumber_shots）。
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
  english_prompt       TEXT NOT NULL DEFAULT '',   -- 訳した（または人が直した）英語。公式フィールド込みの完成文
  english_source       TEXT NOT NULL DEFAULT '',   -- その英語の元になった組み立て済み日本語
  english_status       TEXT NOT NULL DEFAULT '',   -- '' / translating / failed
  english_error        TEXT NOT NULL DEFAULT '',
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
  warning       TEXT NOT NULL DEFAULT '',  -- 投入はできたが伝えたいこと（過去の英訳失敗フォールバックなど）
  latent_path   TEXT,                      -- ラテント連続性で保存した AV ラテント（ComfyUI 側のパス。NULL = 無し）
  -- エージェントに「この Take の生成が終わった」と伝えた時刻（NULL = まだ）。
  -- ジョブ完了イベントと定期スキャンのどちらが先に来ても 1 回しか通知しない
  -- ための印（app/agent_runner.py の _claim_take_notification）。
  agent_notified_at TEXT
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
-- 同じ作品に複数セッションを持てる。session_id は canvas_sessions を指す。
CREATE TABLE IF NOT EXISTS canvas_sessions (
  id              TEXT PRIMARY KEY,
  project_id      TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  title           TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  grok_session_id TEXT NOT NULL DEFAULT '',
  grok_cwd        TEXT NOT NULL DEFAULT '',
  snapshot_key    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS canvas_messages (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  session_id TEXT,
  ts         TEXT NOT NULL,
  role       TEXT NOT NULL,                -- user / assistant / event
  content    TEXT NOT NULL,
  kind       TEXT,                         -- event の種別（action_result 等）
  data       TEXT NOT NULL DEFAULT '{}'
);

-- 編集タブ（スタジオの動画編集）: タイムライン -> トラック -> クリップ。
-- 焼き上がった Take を並べ直して 1 本の動画に書き出すための EDL で、生成
-- （studio_*）とは別の面として持つ。フェーズ 1 で実際に使うのは V1 の
-- video トラックと source_kind='take' のクリップだけ。
--
-- ソース（Take・ジョブ・素材）への外部キーは**張らない**: 元が消えても
-- タイムラインの並びは残し、読み取り時に「メディア欠落」として見せる。
-- タイムラインを消したときのトラック・クリップ・書き出しの後始末は
-- アプリ側（app/timeline.py の delete_timeline）で行う。
--
-- project_id をトラックとクリップにも持たせてあるのは、リビジョンの
-- スナップショット（app/studio.py の _SNAPSHOT_TABLES）が project_id で
-- 束ねて書き戻すため（studio_asset_files と同じ持ち方）。
CREATE TABLE IF NOT EXISTS studio_timelines (
  id          TEXT PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  episode_id  TEXT,                        -- どの話を組んだものか（NULL = 作品まるごと）
  name        TEXT NOT NULL DEFAULT '',
  fps         REAL NOT NULL DEFAULT 24,    -- 書き出しの規格（クリップはここへ揃える）
  width       INTEGER NOT NULL DEFAULT 1280,
  height      INTEGER NOT NULL DEFAULT 720,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS timeline_tracks (
  id          TEXT PRIMARY KEY,
  timeline_id TEXT NOT NULL,
  project_id  TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL CHECK(kind IN ('video', 'audio', 'subtitle')),
  name        TEXT NOT NULL DEFAULT '',
  sort_order  INTEGER NOT NULL DEFAULT 0,
  muted       INTEGER NOT NULL DEFAULT 0,
  locked      INTEGER NOT NULL DEFAULT 0
);

-- timeline_id は track から辿れるが**非正規で持つ**: 1 本のタイムラインの
-- クリップを全部引く／全部差し替える（PUT /clips）のが主な使い道なので、
-- そのたびにトラックと join しないで済ませる。
CREATE TABLE IF NOT EXISTS timeline_clips (
  id          TEXT PRIMARY KEY,
  track_id    TEXT NOT NULL,
  timeline_id TEXT NOT NULL,
  project_id  TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  start_ms    INTEGER NOT NULL DEFAULT 0,   -- タイムライン上の開始位置
  duration_ms INTEGER NOT NULL DEFAULT 0,   -- 尺（等速なので out_ms - in_ms と一致する）
  source_kind TEXT NOT NULL
    CHECK(source_kind IN ('take', 'asset_file', 'library', 'job', 'image',
                          'text', 'gap')),
  source_id   TEXT,                         -- 上の種別の中での id（gap / text は NULL）
  in_ms       INTEGER NOT NULL DEFAULT 0,   -- ソースの中の切り出し位置
  out_ms      INTEGER NOT NULL DEFAULT 0,
  gain_db     REAL NOT NULL DEFAULT 0,
  fade_in_ms  INTEGER NOT NULL DEFAULT 0,
  fade_out_ms INTEGER NOT NULL DEFAULT 0,
  -- 前のクリップとの繋ぎ（NULL = カット）。オーバーラップ方式なので、繋ぎが
  -- 付くとその分だけこのクリップは前へ食い込み、タイムライン全長は縮む。
  transition_kind TEXT,
  transition_ms INTEGER NOT NULL DEFAULT 0,
  text_payload TEXT,                        -- text クリップの中身（JSON。他は NULL）
  -- 再生速度（1.0 = 等速）。duration_ms = (out_ms - in_ms) / speed になる
  speed       REAL NOT NULL DEFAULT 1,
  sort_order  INTEGER NOT NULL DEFAULT 0
);

-- 1 回の書き出し。ffmpeg の実行状態と成果物（outputs/exports/{id}/final.mp4）。
-- リビジョンのスナップショットには入れない（実行結果は履歴で戻すものではない）。
CREATE TABLE IF NOT EXISTS timeline_exports (
  id          TEXT PRIMARY KEY,
  timeline_id TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'queued',  -- queued / running / done / failed
  progress    REAL NOT NULL DEFAULT 0,         -- 0.0〜1.0
  params      TEXT NOT NULL DEFAULT '{}',      -- 書き出し設定（JSON）
  output_path TEXT,
  error       TEXT,
  created_at  TEXT NOT NULL,
  finished_at TEXT
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
-- 並び順は場の中のものなので、引き当ても (作品, 場) 単位で引く。
CREATE INDEX IF NOT EXISTS idx_studio_shots_project
  ON studio_shots(project_id, scene_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_studio_takes_shot
  ON studio_takes(shot_id, created_at);
CREATE INDEX IF NOT EXISTS idx_studio_episodes_project
  ON studio_episodes(project_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_studio_scenes_episode
  ON studio_scenes(episode_id, sort_order);
-- リビジョンの連番はプロジェクトごとに 1 本（同じ seq を 2 つ作らない）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_studio_revisions_seq
  ON studio_revisions(project_id, seq);

CREATE INDEX IF NOT EXISTS idx_studio_timelines_project
  ON studio_timelines(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_timeline_tracks_timeline
  ON timeline_tracks(timeline_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_timeline_clips_timeline
  ON timeline_clips(timeline_id, track_id, start_ms);
CREATE INDEX IF NOT EXISTS idx_timeline_exports_timeline
  ON timeline_exports(timeline_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_canvas_cards_project
  ON canvas_cards(project_id, z);
-- 同じエンティティを指すカードは 1 枚だけ（キャンバス上の分身を作らない）。
-- text / model は entity_id が NULL なので対象外。
CREATE UNIQUE INDEX IF NOT EXISTS idx_canvas_cards_entity
  ON canvas_cards(project_id, entity_id) WHERE entity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_canvas_messages_project
  ON canvas_messages(project_id, ts);
-- session_id 付きインデックスは SCHEMA に置かない。既存 DB の
-- canvas_messages にはまだ列が無く、executescript が _migrate より先に
-- 落ちる。列を足したあと _backfill_canvas_sessions が作る。
CREATE INDEX IF NOT EXISTS idx_canvas_sessions_project
  ON canvas_sessions(project_id, updated_at DESC);

-- Web Push の購読（単一ユーザー。endpoint が識別子）。
CREATE TABLE IF NOT EXISTS push_subscriptions (
  endpoint   TEXT PRIMARY KEY,
  p256dh     TEXT NOT NULL,
  auth       TEXT NOT NULL,
  created_at TEXT NOT NULL
);

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
        # 投入元の chat / agent セッション。エージェント発のジョブは
        # agent_sessions.id と一致し、完了通知の対象外になる。
        ("chat_session_id", "TEXT"),
    ],
    "chat_sessions": [
        # 続き用の grok セッションと、その cwd（SPEC §4.3）。会話の正本は
        # messages なので、消えていれば履歴を組み直して新しい会話を始める。
        ("grok_session_id", "TEXT NOT NULL DEFAULT ''"),
        ("grok_cwd", "TEXT NOT NULL DEFAULT ''"),
    ],
    "agent_sessions": [
        ("nsfw", "INTEGER NOT NULL DEFAULT 0"),
        ("nsfw_source", "TEXT NOT NULL DEFAULT ''"),
        ("grok_session_id", "TEXT NOT NULL DEFAULT ''"),
        ("grok_cwd", "TEXT NOT NULL DEFAULT ''"),
        ("snapshot_key", "TEXT NOT NULL DEFAULT ''"),
    ],
    "canvas_messages": [
        ("session_id", "TEXT"),
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
        # 動画生成の品質（normal / opt / turbo）。既存のプロジェクトは
        # 'normal' = 今までどおり素の MiniMax H3（20 steps）。
        ("quality", "TEXT NOT NULL DEFAULT 'normal'"),
        # 画質（メガピクセル）と画面比。生成フォームと同じ 2 項目を作品単位の
        # 既定として持つ。既存のプロジェクトは NULL = 今までどおりワークフロー
        # 宣言の default_megapixels / グローバル既定に従う。
        #
        # 短いあいだ standard / high / max の 3 段プリセットを 'resolution' 列で
        # 持っていたので、その頃に一度でも起動した DB には列が残る。読み書き
        # しないので放置する（Shot 側の nsfw 列と同じ扱い。NOT NULL だが既定値
        # つきなので、列を書かない INSERT はそのまま通る）。
        ("megapixels", "REAL"),
        ("aspect_ratio", "TEXT"),
        # サンプリング回数（`steps` を宣言しているワークフローにだけ効く）。
        # 既存のプロジェクトは 0 = 今までどおりテンプレートの既定のまま
        # （品質 turbo なら 4、normal / opt なら 20）。画質の 2 項目と違って
        # NULL を持たせず、「未指定」は 0 で表す（JobCreate.steps と同じ流儀）。
        ("steps", "INTEGER NOT NULL DEFAULT 0"),
        # この作品から投入するジョブを NSFW 扱いにするか。既存のプロジェクトは
        # 0 = 非 NSFW（投入時に明示するので Grok の自動判定は走らない）。
        ("nsfw", "INTEGER NOT NULL DEFAULT 0"),
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
        # NSFW はプロジェクト単位に移した（studio_projects.nsfw）。一度でも
        # 起動した DB には Shot 側の nsfw 列が残るが、読み書きしないので放置する
        # （SQLite では列を落とせず、落とす価値もない）。
        ("prompt_updated_at", "TEXT"),
        # 組み立て済み本文の英語キャッシュ。既存行は空 = 今までどおり投入時に訳す。
        ("english_prompt", "TEXT NOT NULL DEFAULT ''"),
        ("english_source", "TEXT NOT NULL DEFAULT ''"),
        ("english_status", "TEXT NOT NULL DEFAULT ''"),
        ("english_error", "TEXT NOT NULL DEFAULT ''"),
    ],
    "canvas_cards": [
        # カードを置いたタブ（話）。参照カードの所属はスタジオのデータから
        # 導くので NULL のままで、使うのは text / model カードだけ。既存の
        # カードはすべて NULL = 作品共通のタブになる。
        ("episode_id", "TEXT REFERENCES studio_episodes(id) ON DELETE SET NULL"),
    ],
    "timeline_clips": [
        # リタイム（フェーズ 3）。既存のクリップは 1.0 = 等速のまま。
        ("speed", "REAL NOT NULL DEFAULT 1"),
    ],
    "studio_takes": [
        ("prompt", "TEXT NOT NULL DEFAULT ''"),
        ("source_prompt", "TEXT NOT NULL DEFAULT ''"),
        ("warning", "TEXT NOT NULL DEFAULT ''"),
        # ラテント連続性で保存した AV ラテントのパス（ComfyUI 側）。既存行と、
        # ラテント連続性を使わなかった Take は NULL のまま。
        ("latent_path", "TEXT"),
        # エージェントへ完了を伝えた時刻。既存行は NULL だが、起動時のスキャンが
        # 「終端に達しているのに未通知」の Take を拾うので、移行直後に過去の
        # Take がまとめて通知されないよう、ここで一度だけ現在時刻で埋める
        # （下の _migrate を参照）。
        ("agent_notified_at", "TEXT"),
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
    added: dict[str, set[str]] = {}
    for table, columns in MIGRATIONS.items():
        async with conn.execute(f"PRAGMA table_info({table})") as cur:
            existing = {row["name"] for row in await cur.fetchall()}
        for name, definition in columns:
            if name not in existing:
                await conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                )
                added.setdefault(table, set()).add(name)

    # 旧 Shot 単位の NSFW 印を安全側（プロジェクト全体 NSFW）に引き継ぐ。
    # 列を足した回だけ走らせるので、あとからプロジェクト側で外しても復活しない。
    if "nsfw" in added.get("studio_projects", set()):
        async with conn.execute("PRAGMA table_info(studio_shots)") as cur:
            shot_columns = {row["name"] for row in await cur.fetchall()}
        if "nsfw" in shot_columns:
            await conn.execute(
                "UPDATE studio_projects SET nsfw = 1 WHERE id IN "
                "(SELECT DISTINCT project_id FROM studio_shots WHERE nsfw = 1)"
            )

    # 既存の Take は「もう伝えた」ことにする。列を足した直後のスキャンが、
    # この機能より前に終わっていた Take をまとめて制作記録へ積むのを防ぐ。
    if "agent_notified_at" in added.get("studio_takes", set()):
        await _backfill_take_notifications(conn)

    await _widen_timeline_clip_sources(conn)
    await _backfill_canvas_sessions(conn)
    await _renumber_shots(conn)


#: 静止画クリップ（フェーズ 3）を足したあとの ``timeline_clips`` の列。
#: テーブルを作り直すときに「この順で写す」ための正本。
_TIMELINE_CLIP_COLUMNS = (
    "id", "track_id", "timeline_id", "project_id", "start_ms", "duration_ms",
    "source_kind", "source_id", "in_ms", "out_ms", "gain_db", "fade_in_ms",
    "fade_out_ms", "transition_kind", "transition_ms", "text_payload", "speed",
    "sort_order",
)


async def _widen_timeline_clip_sources(conn: aiosqlite.Connection) -> None:
    """``timeline_clips.source_kind`` の CHECK に ``'image'`` を足す。

    CHECK 制約は ``ALTER TABLE`` では変えられないので（:data:`MIGRATIONS` では
    拾えない）、古い制約を持つテーブルだけ作り直して中身を写す。編集タブの
    フェーズ 1〜2 で一度でも起動した DB がここを通る。

    判定は ``sqlite_master`` に残る CREATE 文（= その DB が実際に持っている
    制約）で行うので、作り直したあとは何もしない（冪等）。
    """
    async with conn.execute(
        "SELECT sql FROM sqlite_master"
        " WHERE type = 'table' AND name = 'timeline_clips'"
    ) as cur:
        row = await cur.fetchone()
    if row is None or "'image'" in (row["sql"] or ""):
        return

    columns = ", ".join(_TIMELINE_CLIP_COLUMNS)
    await conn.execute("""
        CREATE TABLE timeline_clips_new (
          id          TEXT PRIMARY KEY,
          track_id    TEXT NOT NULL,
          timeline_id TEXT NOT NULL,
          project_id  TEXT NOT NULL
            REFERENCES studio_projects(id) ON DELETE CASCADE,
          start_ms    INTEGER NOT NULL DEFAULT 0,
          duration_ms INTEGER NOT NULL DEFAULT 0,
          source_kind TEXT NOT NULL
            CHECK(source_kind IN ('take', 'asset_file', 'library', 'job',
                                  'image', 'text', 'gap')),
          source_id   TEXT,
          in_ms       INTEGER NOT NULL DEFAULT 0,
          out_ms      INTEGER NOT NULL DEFAULT 0,
          gain_db     REAL NOT NULL DEFAULT 0,
          fade_in_ms  INTEGER NOT NULL DEFAULT 0,
          fade_out_ms INTEGER NOT NULL DEFAULT 0,
          transition_kind TEXT,
          transition_ms INTEGER NOT NULL DEFAULT 0,
          text_payload TEXT,
          speed       REAL NOT NULL DEFAULT 1,
          sort_order  INTEGER NOT NULL DEFAULT 0
        )
    """)
    await conn.execute(
        f"INSERT INTO timeline_clips_new ({columns})"
        f" SELECT {columns} FROM timeline_clips"
    )
    await conn.execute("DROP TABLE timeline_clips")
    await conn.execute("ALTER TABLE timeline_clips_new RENAME TO timeline_clips")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_timeline_clips_timeline"
        " ON timeline_clips(timeline_id, track_id, start_ms)"
    )


async def _renumber_shots(conn: aiosqlite.Connection) -> None:
    """``studio_shots.sort_order`` を「場の中の 0..n」へ振り直す。

    並び順は作品全体で 1 本の連番だったので、話 -> 場 -> カットの階層順に
    並べるために意味を「場の中での順番」（未分類なら作品の未分類グループの
    中での順番）へ変えた。既存の DB はその連番を持ったままなので、**今見えて
    いる順序をそのまま保って**場ごとに 0 から振り直す。

    列は増えないので :data:`MIGRATIONS` では拾えない。代わりに毎回走らせる:
    振り直したあとの値をもう一度この規則で並べても同じ値になる（冪等）ので、
    2 回目以降は書き込みが 1 行も出ない。
    """
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'studio_shots'"
    ) as cur:
        if await cur.fetchone() is None:
            return
    # 旧 (project_id, sort_order) のインデックスは場を含まないので張り替える
    # （CREATE INDEX IF NOT EXISTS は同名の既存インデックスを作り直さない）。
    async with conn.execute(
        "SELECT sql FROM sqlite_master"
        " WHERE type = 'index' AND name = 'idx_studio_shots_project'"
    ) as cur:
        row = await cur.fetchone()
    if row is not None and "scene_id" not in (row["sql"] or ""):
        await conn.execute("DROP INDEX idx_studio_shots_project")
        await conn.execute(
            "CREATE INDEX idx_studio_shots_project"
            " ON studio_shots(project_id, scene_id, sort_order)"
        )

    async with conn.execute(
        "SELECT id, project_id, scene_id, sort_order FROM studio_shots"
        " ORDER BY project_id, sort_order, created_at, id"
    ) as cur:
        rows = await cur.fetchall()
    counters: dict[tuple[str, str | None], int] = {}
    for row in rows:
        key = (row["project_id"], row["scene_id"])
        order = counters.get(key, 0)
        counters[key] = order + 1
        if row["sort_order"] != order:
            await conn.execute(
                "UPDATE studio_shots SET sort_order = ? WHERE id = ?",
                (order, row["id"]),
            )


#: :func:`_backfill_take_notifications` が「まだ終わっていない」とみなすジョブ。
#: ここに居るジョブの Take は印を付けない（付けると、いま走っているレンダーの
#: 完了通知がそのまま消える）。
_IN_FLIGHT_JOB_STATUSES = ("queued", "prompting", "running")


async def _backfill_take_notifications(conn: aiosqlite.Connection) -> None:
    """``agent_notified_at`` を足した回だけ走る後始末（SPEC §2.2）。

    この機能より前に終わっていた Take が、列を足した直後のスキャンでまとめて
    制作記録へ積まれるのを防ぐために「もう伝えた」ことにする。

    ただし**まだ走っているジョブの Take は対象外**。移行の瞬間に実行中だった
    レンダーまで埋めてしまうと、その完了通知が永久に届かなくなる（印は
    ``IS NULL`` でしか拾われない）。ジョブ行がもう無い Take は結果を辿れないので
    印を付ける側に寄せる。
    """
    placeholders = ", ".join("?" for _ in _IN_FLIGHT_JOB_STATUSES)
    await conn.execute(
        "UPDATE studio_takes SET agent_notified_at = ?"
        " WHERE job_id NOT IN ("
        f"   SELECT id FROM jobs WHERE status IN ({placeholders})"
        " )",
        (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            *_IN_FLIGHT_JOB_STATUSES,
        ),
    )


async def _backfill_canvas_sessions(conn: aiosqlite.Connection) -> None:
    """メッセージがある作品ごとにキャンバスセッションを 1 本作り、session_id を埋める。"""
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'canvas_sessions'"
    ) as cur:
        if await cur.fetchone() is None:
            return
    async with conn.execute("PRAGMA table_info(canvas_messages)") as cur:
        columns = {row["name"] for row in await cur.fetchall()}
    if "session_id" not in columns:
        return
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_canvas_messages_session"
        " ON canvas_messages(session_id, ts)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_canvas_sessions_project"
        " ON canvas_sessions(project_id, updated_at DESC)"
    )

    from .ids import new_id
    from .paths import AGENT_SESSIONS_DIR

    async with conn.execute(
        "SELECT DISTINCT m.project_id FROM canvas_messages m"
        " JOIN studio_projects p ON p.id = m.project_id"
        " WHERE m.session_id IS NULL OR m.session_id = ''"
    ) as cur:
        project_ids = [row["project_id"] for row in await cur.fetchall()]
    for project_id in project_ids:
        async with conn.execute(
            "SELECT content, ts FROM canvas_messages"
            " WHERE project_id = ? AND role = 'user'"
            " ORDER BY ts, id LIMIT 1",
            (project_id,),
        ) as cur:
            first = await cur.fetchone()
        async with conn.execute(
            "SELECT MIN(ts) AS created_at, MAX(ts) AS updated_at"
            " FROM canvas_messages WHERE project_id = ?",
            (project_id,),
        ) as cur:
            times = await cur.fetchone()
        title = "チャット"
        if first and (first["content"] or "").strip():
            title = first["content"].strip().splitlines()[0][:40]
        session_id = new_id()
        created = (times["created_at"] if times else None) or ""
        updated = (times["updated_at"] if times else None) or created
        cwd = str(AGENT_SESSIONS_DIR / f"canvas-{project_id}")
        await conn.execute(
            "INSERT INTO canvas_sessions"
            " (id, project_id, title, created_at, updated_at, grok_cwd)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, project_id, title, created, updated, cwd),
        )
        await conn.execute(
            "UPDATE canvas_messages SET session_id = ?"
            " WHERE project_id = ? AND (session_id IS NULL OR session_id = '')",
            (session_id, project_id),
        )


async def init_db() -> None:
    """Create tables. LoRA registrations are user data (managed in the settings
    screen), so nothing is seeded here."""
    async with get_db() as conn:
        await conn.executescript(SCHEMA)
        await _migrate(conn)
        await conn.commit()
