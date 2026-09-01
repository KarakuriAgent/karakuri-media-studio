import logging
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from .paths import CHAT_SESSIONS_DIR, DB_PATH, RUNTIME_DIR

log = logging.getLogger(__name__)

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
  analysis_path TEXT,                       -- 音源解析の analysis.json（§5.2）
  extra_outputs TEXT,                       -- 主成果物に収まらない出力（JSON 配列）
  error         TEXT,
  nsfw          INTEGER NOT NULL DEFAULT 0,
  nsfw_source   TEXT NOT NULL DEFAULT '',
  credits_consumed REAL,                    -- 外部バックエンドが消費したクレジット（§5.2）
  chat_session_id TEXT,                     -- 生成フォームの chat セッション
  started_at    TEXT,                       -- 実行を開始した時刻（所要時間の起点）
  finished_at   TEXT                        -- 終端（done/failed/canceled）に入った時刻
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
  latent_upscale INTEGER NOT NULL DEFAULT 1, -- 1 パス目を低解像度で回してラテントのまま拡大する
  quality     TEXT NOT NULL DEFAULT 'normal', -- 動画生成の品質（normal / opt / turbo）
  image_quality TEXT NOT NULL DEFAULT 'normal', -- 素材画像（MiniMax H3 Image）の品質（normal / opt / turbo）
  megapixels  REAL,                        -- 動画生成のメガピクセル（NULL = ワークフローの既定）
  aspect_ratio TEXT,                       -- 動画生成のアスペクト比（NULL = 既定）
  steps       INTEGER NOT NULL DEFAULT 0,  -- サンプリング回数（0 = テンプレートの既定のまま）
  image_megapixels REAL,                   -- 素材画像のメガピクセル（NULL = ワークフローの既定）
  image_aspect_ratio TEXT,                 -- 素材画像のアスペクト比（NULL = 既定）
  image_steps INTEGER NOT NULL DEFAULT 0,  -- 素材画像のサンプリング回数（0 = テンプレートの既定のまま）
  nsfw        INTEGER NOT NULL DEFAULT 0,   -- 1 = この作品から投入するジョブはすべて NSFW（0 = 非 NSFW 固定）
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
-- projects / episodes / scenes / shots / assets / takes / 編集タブの EDL を
-- 丸ごと JSON で残す。ファイル実体（assets/ と outputs/）とジョブの実行状態は
-- 対象外（消さないので戻す必要がない。app/studio.py の _SNAPSHOT_TABLES）。
-- Take だけは復元でも**消さない**（載っている行を戻すだけ。生成はリビジョンを
-- 作らないので、消すと直後に焼いた Take を辿れなくなる。_KEEP_UNKNOWN_ROWS）。
CREATE TABLE IF NOT EXISTS studio_revisions (
  id            TEXT PRIMARY KEY,
  project_id    TEXT NOT NULL REFERENCES studio_projects(id) ON DELETE CASCADE,
  seq           INTEGER NOT NULL,           -- プロジェクトごとの 1 始まりの連番
  -- 変更した主体。user = UI からの操作 / external = 外部 API（/api/v1）/
  -- chat = 内蔵チャット。'agent' は external に分ける前の過去行に残る。
  actor         TEXT NOT NULL DEFAULT 'user',
  action        TEXT NOT NULL DEFAULT '',   -- 変更内容の短い説明（日本語）
  -- 触ったエンティティ（1 件だけを触る操作のときに入る。並べ替えや一括作成の
  -- ように複数へ跨る操作は空）。「このカットの履歴」の絞り込みはこれで引く:
  -- 説明文のタイトル一致だと同名や改名で取りこぼす（app/studio.py）。
  entity_kind   TEXT NOT NULL DEFAULT '',   -- project / episode / scene / shot / asset …
  entity_id     TEXT NOT NULL DEFAULT '',
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
  -- 音源上の計画開始秒（NULL = 並び順で置く従来どおり）。MV のように音に映像を
  -- 合わせる制作でだけ使い、タイムラインの sync がこの秒へカットを置く。
  planned_start_seconds REAL,
  -- タイムラインの自動配置での扱い（SPEC §7.3）。
  --   auto        … 今までどおり自動配置・sync の対象
  --   insert_only … 差し込み（clips/insert）専用。自動配置にも sync にも出さない
  --   skip        … このタイムラインでは使わない（同上）
  timeline_role        TEXT NOT NULL DEFAULT 'auto',
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
  latent_hires_path TEXT                   -- 同じく 2 パス目（最終解像度）の AV ラテント（latent_upscale on の 2 段引き継ぎ。NULL = 無し）
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
  -- 音源基準の配置で計画秒どうしの隙間をどう埋めるか（SPEC §7.3）。
  --   clone … 前のクリップを末尾静止で伸ばす（既定。MV では黒コマが事故になる）
  --   black … gap クリップ（黒＋無音）で埋める（従来の挙動）
  gap_fill    TEXT NOT NULL DEFAULT 'clone',
  -- 音源の尺（秒）。自動配置の最後のクリップをここで締める（NULL = A1 の
  -- 最初の音声クリップ、それも無ければ Take の尺いっぱい）
  planned_end_seconds REAL,
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
  -- 焼き上がりの規格と検算（Remotion の base に渡すとき props と揃えるため）。
  -- 走り終わるまでは NULL。frames は ffprobe -count_frames の実測。
  fps         REAL,
  width       INTEGER,
  height      INTEGER,
  frames      INTEGER,
  duration_ms INTEGER,
  warnings    TEXT NOT NULL DEFAULT '[]',      -- PAD / フレーム数のずれ（JSON 配列）
  created_at  TEXT NOT NULL,
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_library_created_at ON library(created_at DESC);

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
-- 「このカットの履歴」で引く entity_kind / entity_id の索引は、その 2 列が
-- ALTER で足るものなので _migrate の中で作る（ここで作ると古い DB で落ちる）。

CREATE INDEX IF NOT EXISTS idx_studio_timelines_project
  ON studio_timelines(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_timeline_tracks_timeline
  ON timeline_tracks(timeline_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_timeline_clips_timeline
  ON timeline_clips(timeline_id, track_id, start_ms);
CREATE INDEX IF NOT EXISTS idx_timeline_exports_timeline
  ON timeline_exports(timeline_id, created_at DESC);

-- Web Push の購読（単一ユーザー。endpoint が識別子）。
CREATE TABLE IF NOT EXISTS push_subscriptions (
  endpoint   TEXT PRIMARY KEY,
  p256dh     TEXT NOT NULL,
  auth       TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- 画面の状態（いまは生成フォームの下書きだけ）を置く kv。外部エージェントが
-- 触った値をブラウザへ流し込むための共有場所で、``revision`` が上書き合戦の
-- 判定に使う連番、``updated_by`` が最後に書いた側（'ui' / 'external'）。
CREATE TABLE IF NOT EXISTS ui_state (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  revision   INTEGER NOT NULL,
  updated_by TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
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
        # 音源解析ジョブ（mode='audio_analysis'、SPEC §5.2）が書いた
        # analysis.json のパス。画像も動画も作らないジョブなので成果物はこれだけ。
        ("analysis_path", "TEXT"),
        # 主成果物（image_path / video_path / audio_output_path）に収まらない
        # 追加の出力のパス（JSON 配列）。1 回の生成で複数返すモデル（たとえば
        # 1 リクエストで 2 曲）のため。既存行は NULL = 追加成果物なし。
        ("extra_outputs", "TEXT"),
        # 外部バックエンドのジョブが消費したクレジット（SPEC §5.2）。
        # ComfyUI のジョブは自前 GPU なので NULL のままでよい。
        ("credits_consumed", "REAL"),
        # 投入元の chat セッション（生成フォームの相談チャット）。
        ("chat_session_id", "TEXT"),
        # 実行の開始・終了時刻（SPA が「生成にかかった時間」を出すための材料）。
        # 所要時間そのものは持たず、読み出し側で差を取る。この列を足す前に
        # 走った既存行は両方 NULL のままでよい = 所要時間を出さない。
        ("started_at", "TEXT"),
        ("finished_at", "TEXT"),
    ],
    "chat_sessions": [
        # 続き用の grok セッションと、その cwd（SPEC §4.3）。会話の正本は
        # messages なので、消えていれば履歴を組み直して新しい会話を始める。
        ("grok_session_id", "TEXT NOT NULL DEFAULT ''"),
        ("grok_cwd", "TEXT NOT NULL DEFAULT ''"),
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
        # 引き継ぎを Motion Context（ラテント連続性）で行うか。既存の
        # プロジェクトは既定 OFF = 今までどおりラストフレームの引き継ぎ。
        ("latent_continuity", "INTEGER NOT NULL DEFAULT 0"),
        # ラテントアップスケール（1 パス目を 0.2MP で回してから
        # `MinimaxH3LatentUpscaler3D` で指定解像度に拡大する）。既存の
        # プロジェクトも既定 ON（ジョブ側の `latent_upscale` の既定と揃える）。
        ("latent_upscale", "INTEGER NOT NULL DEFAULT 1"),
        # 動画生成の品質（normal / opt / turbo）。既存のプロジェクトは
        # 'normal' = 今までどおり素の MiniMax H3（20 steps）。
        ("quality", "TEXT NOT NULL DEFAULT 'normal'"),
        # 画像生成の品質（normal / opt / turbo）。動画の `quality` とは独立した
        # つまみで、素材の静止画を MiniMax H3 Image で焼くときにだけ効く。
        # 既存のプロジェクトは 'normal' = 素の minimax_h3_{t2i,i2i,r2i}。
        ("image_quality", "TEXT NOT NULL DEFAULT 'normal'"),
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
        # 素材画像の画質（メガピクセル）・画面比・サンプリング回数。動画側の
        # megapixels / aspect_ratio / steps と同じ 3 項目を、素材の静止画用に
        # 別で持つ（動画の値を静止画に流用しないため）。既存のプロジェクトは
        # NULL / 0 = 指定しない＝テンプレートの既定（MiniMax H3 Image は
        # 約 0.98MP）のまま。
        ("image_megapixels", "REAL"),
        ("image_aspect_ratio", "TEXT"),
        ("image_steps", "INTEGER NOT NULL DEFAULT 0"),
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
        # 音源上の計画開始秒。既存行は NULL = 今までどおり並び順で置く。
        ("planned_start_seconds", "REAL"),
        # タイムラインの自動配置での扱い（auto / insert_only / skip）。既存行は
        # 'auto' = 今までどおり自動配置・sync の対象。
        ("timeline_role", "TEXT NOT NULL DEFAULT 'auto'"),
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
    "studio_timelines": [
        # 計画秒どうしの隙間の埋め方（clone / black）。既存のタイムラインも
        # 既定 'clone' = 前のクリップを末尾静止で伸ばす（黒コマを作らない）。
        ("gap_fill", "TEXT NOT NULL DEFAULT 'clone'"),
        # 音源の尺（秒）。既存のタイムラインは NULL = 今までどおり Take の尺で
        # 最後のクリップが終わる。
        ("planned_end_seconds", "REAL"),
    ],
    "timeline_clips": [
        # リタイム（フェーズ 3）。既存のクリップは 1.0 = 等速のまま。
        ("speed", "REAL NOT NULL DEFAULT 1"),
    ],
    "timeline_exports": [
        # 焼き上がりの規格と検算。この列を足す前に走った書き出しは NULL のまま
        # （後から測り直すことはしない。成果物そのものは残っている）。
        ("fps", "REAL"),
        ("width", "INTEGER"),
        ("height", "INTEGER"),
        ("frames", "INTEGER"),
        ("duration_ms", "INTEGER"),
        ("warnings", "TEXT NOT NULL DEFAULT '[]'"),
    ],
    "studio_revisions": [
        # 触ったエンティティ（「このカットの履歴」の絞り込み用）。この列を足す
        # 前の行は空のまま = どのエンティティのものか分からないので絞り込みには
        # 出てこない（一覧には今までどおり出る）。
        ("entity_kind", "TEXT NOT NULL DEFAULT ''"),
        ("entity_id", "TEXT NOT NULL DEFAULT ''"),
    ],
    "studio_takes": [
        ("prompt", "TEXT NOT NULL DEFAULT ''"),
        ("source_prompt", "TEXT NOT NULL DEFAULT ''"),
        ("warning", "TEXT NOT NULL DEFAULT ''"),
        # ラテント連続性で保存した AV ラテントのパス（ComfyUI 側）。既存行と、
        # ラテント連続性を使わなかった Take は NULL のまま。
        ("latent_path", "TEXT"),
        # 2 段引き継ぎ（latent_upscale on）で保存した 2 パス目のラテント。
        # 既存行と、off で作った Take は NULL のまま = 1 段引き継ぎに戻る。
        ("latent_hires_path", "TEXT"),
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

    # entity_kind / entity_id は ALTER で足す列なので、索引はここで作る
    # （SCHEMA に書くと、その列がまだ無い古い DB の起動で落ちる）。
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_studio_revisions_entity"
        " ON studio_revisions(project_id, entity_kind, entity_id)"
    )

    await _widen_timeline_clip_sources(conn)
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


# --------------------------------------------------------------------------
# 撤去した機能（内蔵エージェント / キャンバス）の後始末
# --------------------------------------------------------------------------

#: 撤去した機能が持っていたテーブル。``canvas_projects`` / ``canvas_nodes`` は
#: スキーマから既に消えていた頃の遺物で、古い DB にだけ残っている。
_REMOVED_TABLES = (
    "agent_sessions",
    "canvas_cards",
    "canvas_viewports",
    "canvas_sessions",
    "canvas_messages",
    "canvas_projects",
    "canvas_nodes",
)

#: カードの孤児化を防いでいたトリガー。**張られているのは studio_* 側**なので、
#: canvas_cards を落としても一緒には消えない（残すと DELETE が
#: 「no such table: canvas_cards」で落ちる）。
_REMOVED_TRIGGERS = (
    "trg_canvas_cards_asset_deleted",
    "trg_canvas_cards_scene_deleted",
    "trg_canvas_cards_shot_deleted",
    "trg_canvas_cards_take_deleted",
)

#: 撤去で使わなくなった列。SQLite 3.35+ の ``DROP COLUMN`` で落とす
#: （どれも index / trigger から参照されていない）。
_REMOVED_COLUMNS = (
    ("studio_projects", "canvas_x"),
    ("studio_projects", "canvas_y"),
    ("studio_projects", "canvas_zoom"),
    ("studio_takes", "agent_notified_at"),
)


async def _cleanup_removed_features(conn: aiosqlite.Connection) -> None:
    """内蔵エージェントとキャンバスの残骸を落とす（起動ごとに走る冪等な後始末）。

    片付けるのは **DB の中身だけ**（テーブル・トリガー・列と、保存済みの cwd）。
    作業ディレクトリの実体は :func:`_move_chat_workdirs` が別に受け持つ（テストが
    DB を一時ファイルへ差し替えて走るので、ファイルシステムには触らせない）。何も
    残っていなければ 1 行も書かないので、2 回目以降は実質何もしない。
    ``VACUUM`` はしない（本番 DB が大きく、起動を止めるほどの利得が無い）。
    """
    for trigger in _REMOVED_TRIGGERS:
        await conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in _REMOVED_TABLES:
        await conn.execute(f"DROP TABLE IF EXISTS {table}")

    for table, column in _REMOVED_COLUMNS:
        async with conn.execute(f"PRAGMA table_info({table})") as cur:
            columns = {row["name"] for row in await cur.fetchall()}
        if column in columns:
            await conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")

    # プロンプト生成チャットの作業ディレクトリは
    # ``runtime/agent-sessions/chat-<id>/`` から ``runtime/chat-sessions/<id>/``
    # へ移した。保存済みの cwd を新しい置き場へ読み替える。
    await conn.execute(
        "UPDATE chat_sessions"
        " SET grok_cwd = REPLACE(grok_cwd, '/agent-sessions/chat-', '/chat-sessions/')"
        " WHERE grok_cwd LIKE '%/agent-sessions/chat-%'"
    )


def _move_chat_workdirs() -> None:
    """``runtime/agent-sessions/chat-*`` を ``runtime/chat-sessions/`` へ移す。

    実ファイルを動かすのでサーバー起動時（lifespan）にだけ呼ぶ。撤去した機能の
    置き場（``agent-sessions`` の chat 以外と ``canvas-projects``）は消すが、
    **移せなかったチャットが 1 つでも残るうちは ``agent-sessions/`` 自体を消さない**
    （ユーザーの作業ディレクトリを道連れにしないため）。存在しなければ何もしない。
    """
    old_root = RUNTIME_DIR / "agent-sessions"
    if old_root.is_dir():
        CHAT_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        for entry in old_root.iterdir():
            if entry.is_dir() and entry.name.startswith("chat-"):
                target = CHAT_SESSIONS_DIR / entry.name[len("chat-"):]
                if target.exists():
                    continue  # 移動済み（冪等）。中身は残したままにする
                try:
                    shutil.move(str(entry), str(target))
                except OSError:
                    log.warning("チャットの作業ディレクトリを移せませんでした: %s", entry)
                continue
            # 旧エージェントセッションなど、撤去済み機能の残骸は個別に消す。
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        try:
            old_root.rmdir()  # 空になったときだけ消える
        except OSError:
            log.warning(
                "移せなかった作業ディレクトリが残るので %s は残します", old_root
            )
    shutil.rmtree(RUNTIME_DIR / "canvas-projects", ignore_errors=True)


async def init_db() -> None:
    """Create tables. LoRA registrations are user data (managed in the settings
    screen), so nothing is seeded here."""
    async with get_db() as conn:
        await conn.executescript(SCHEMA)
        await _migrate(conn)
        await _cleanup_removed_features(conn)
        await conn.commit()
