"""編集タブ: タイムライン（EDL）の組み立てと書き出しの管理。

スタジオ（:mod:`app.studio`）が「1 カットを焼く」までを受け持つのに対して、
ここは**焼き上がったものを並べて 1 本にする**面を持つ:

- **タイムライン**（:data:`studio_timelines`）が 1 本の編集。書き出しの規格
  （幅・高さ・fps）を持ち、話（``episode_id``）を組んだものかどうかを覚える。
- **トラック**（:data:`timeline_tracks`）は並べる段。並べ替えの正本は ``video``
  の ``V1`` 1 本きりで、音声（``A1`` …）と字幕（``T1``）はあとから足す。
- **クリップ**（:data:`timeline_clips`）が 1 つの素材の切り出し。ソース（Take・
  ライブラリ・ジョブ・作品の素材・静止画）を**参照するだけ**で、元が消えても
  並びは残り、読み取りのたびに ``missing``（メディア欠落）として見せる。欠落が
  残っているあいだは書き出しを受け付けない（:func:`start_export` が断る）。
- **書き出し**（:data:`timeline_exports`）は ffmpeg の 1 回の実行。組み立てと
  実行は :mod:`app.timeline_export`、テロップの ASS は
  :mod:`app.timeline_subtitles`、進捗は WS（``type: "timeline_export"``）。

トラックごとに並べ方が違う: **V1 はリップル方式**（常に先頭から詰まり、繋ぎ
（トランジション）を置くとその分だけ前へ食い込んで全長が縮む）、**音声と字幕は
自由配置**（隙間は空けられるが、同じトラックの中では重ねられない）。

ルーター（:mod:`app.routers.timelines`）とテストの両方から使うので、DB と
ffmpeg の呼び出しはこのモジュールに集約する（:mod:`app.studio` と同じ持ち方）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

import aiosqlite

from . import jobs as job_service
from . import library as library_service
from . import studio as studio_service
from . import ws
from .config import load_settings
from .db import get_db
from .ids import new_id
from .models import (
    JobCreate,
    LibraryItem,
    StudioTimeline,
    StudioTimelineCreate,
    StudioTimelineDetail,
    TimelineClip,
    TimelineClipInput,
    TimelineClipInsert,
    TimelineExport,
    TimelineFx,
    TimelineFxEvent,
    TimelineFxEventCreate,
    TimelineFxEventUpdate,
    TimelineFxUpdate,
    TimelineMediaItem,
    TimelineMediaPage,
    TimelineMissingCandidate,
    TimelineMissingClip,
    TimelineMissingFix,
    TimelineMissingReport,
    TimelineSyncAdded,
    TimelineSyncPreview,
    TimelineSyncRemoved,
    TimelineSyncRequest,
    TimelineSyncRetaken,
    TimelineTrack,
    TimelineTrackCreate,
    TimelineTrackUpdate,
)
from .paths import ASSETS_DIR, LIBRARY_DIR, OUTPUTS_DIR, rebase_stored_path
from . import timeline_subtitles as subtitles
from .timeline_export import (
    FIT_CROP,
    FIT_PAD,
    SPEED_MAX,
    SPEED_MIN,
    TRANSITION_MAX_MS,
    TRANSITION_MIN_MS,
    TRANSITIONS,
    ExportAudioClip,
    ExportClip,
    ExportSpec,
    TimelineExportError,
    frame_warning,
    pad_warnings,
    resolve_format,
    run_export,
)

log = logging.getLogger(__name__)

#: 書き出しの置き場（``/outputs`` の下なので、そのまま静的配信できる）
EXPORTS_DIRNAME = "exports"

#: 書き出しの成果物のファイル名
EXPORT_FILENAME = "final.mp4"

#: ffprobe の呼び出し名（:mod:`app.jobs` と同じ流儀でテストが差し替える）
FFPROBE = "ffprobe"

#: タイムラインの既定の規格（作るときに指定が無ければこれ）
DEFAULT_FPS = 24.0
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720

#: 自動配置で尺が読めなかったカットに充てる長さ（ミリ秒）
FALLBACK_CLIP_MS = 5000

#: 計画秒どうしの隙間の既定の埋め方（``clone`` = 前のクリップの末尾静止で伸ばす。
#: MV では黒コマが事故になるので、黒で埋めたいときだけ ``black`` を選ぶ）
DEFAULT_GAP_FILL = "clone"

#: 静止画クリップの既定の尺（ミリ秒）
DEFAULT_IMAGE_MS = 3000

#: クリップに残せる最小の尺（ミリ秒。画面の ``MIN_CLIP_MS`` と同じ意味）
MIN_CLIP_MS = 100

#: 書き出しに添える ASS のファイル名
SUBTITLES_FILENAME = "subtitles.ass"

#: まだ走っている書き出しの状態
RUNNING_STATUSES = ("queued", "running")

#: ffprobe を同時に何本走らせるか（並列にしても I/O で頭打ちになるので、
#: 長いタイムラインでプロセスが溢れない程度に抑える）
PROBE_CONCURRENCY = 8

#: 静止画クリップの ``source_id`` に付ける出どころの印（``library:<id>``）。
#: 静止画だけは 1 つの ``source_kind`` に 3 つの出どころがぶら下がるので、
#: id 側に「どこの id か」を持たせる。
IMAGE_PROVIDERS = ("library", "job", "asset_file")


class TimelineError(Exception):
    """タイムライン操作の失敗（ルーターが 400 に変換する）。"""


class TimelineNotFound(TimelineError):
    """指したものが無い（ルーターが 404 に変換する）。"""


class TimelineConflict(TimelineError):
    """いま走っている書き出しがある（ルーターが 409 に変換する）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _output_url(path: str | None) -> str | None:
    """``outputs/`` の中のファイルを ``/outputs/…`` の配信 URL にする。

    :func:`app.jobs._output_url` と同じ約束（記録は絶対パスなので、別の
    プレフィックスで走らせた行でもいまの ROOT へ載せ替えてから相対化する）。
    """
    if not path:
        return None
    try:
        return "/outputs/" + rebase_stored_path(path).resolve().relative_to(
            OUTPUTS_DIR.resolve()
        ).as_posix()
    except (ValueError, OSError):
        return None


def export_dir(export_id: str) -> Path:
    return OUTPUTS_DIR / EXPORTS_DIRNAME / export_id


# --------------------------------------------------------------------------
# ソースの下調べ（ffprobe）
# --------------------------------------------------------------------------

async def probe_media(path: str | Path) -> tuple[int | None, bool]:
    """``(長さのミリ秒, 音声トラックを持つか)``。読めなければ ``(None, False)``。

    ffprobe が無い・読めないのは致命的ではない（:func:`app.jobs.probe_media_duration`
    と同じ方針）。呼び出し側は長さが None なら既定値へ落ちる。
    """
    try:
        process = await asyncio.create_subprocess_exec(
            FFPROBE,
            "-v", "error",
            "-show_entries", "format=duration:stream=codec_type",
            "-of", "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        log.info("ffprobe を実行できませんでした（%s）: %s", path, exc)
        return None, False
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        log.info(
            "ffprobe が %s を読めませんでした: %s",
            path,
            stderr.decode("utf-8", "replace").strip()[:200],
        )
        return None, False
    try:
        parsed = json.loads(stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        return None, False
    duration_ms: int | None = None
    try:
        seconds = float((parsed.get("format") or {}).get("duration"))
        if seconds > 0:
            duration_ms = int(round(seconds * 1000))
    except (TypeError, ValueError):
        duration_ms = None
    has_audio = any(
        (stream or {}).get("codec_type") == "audio"
        for stream in parsed.get("streams") or []
    )
    return duration_ms, has_audio


#: ffprobe の結果の使い回し（``(パス, 更新時刻, 大きさ) -> (尺, 音声の有無)``）。
#: 同じソースを何十本のクリップが指していても ffprobe は 1 回で済む。ファイルが
#: 書き換われば mtime か大きさが変わるので、古い結果を掴んだままにならない。
_PROBE_CACHE: dict[tuple[str, int, int], tuple[int | None, bool]] = {}

#: 使い回しの上限（1 プロセスの間だけの目安。超えたら丸ごと捨てる）
PROBE_CACHE_LIMIT = 2048


async def probe_frames(path: str | Path) -> int | None:
    """焼き上がった動画の**総フレーム数**（``ffprobe -count_frames``）。

    フレーム数の照合（計画どおりの尺で焼けたか）にだけ使う。全フレームを数える
    ので速くはないが、書き出し 1 回につき 1 度なので許容する。読めなければ None。
    """
    try:
        process = await asyncio.create_subprocess_exec(
            FFPROBE,
            "-v", "error",
            "-select_streams", "v:0",
            "-count_frames",
            "-show_entries", "stream=nb_read_frames",
            "-of", "csv=p=0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        log.info("ffprobe を実行できませんでした（%s）: %s", path, exc)
        return None
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return None
    try:
        return int(stdout.decode("utf-8", "replace").strip().split(",")[0])
    except (ValueError, IndexError):
        return None


def _cache_key(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size)


async def probe_cached(path: str | Path) -> tuple[int | None, bool]:
    """:func:`probe_media` の結果を使い回す版（同じファイルは 1 回だけ読む）。"""
    resolved = Path(path)
    key = _cache_key(resolved)
    if key is None:
        return None, False
    cached = _PROBE_CACHE.get(key)
    if cached is not None:
        return cached
    result = await probe_media(resolved)
    if len(_PROBE_CACHE) >= PROBE_CACHE_LIMIT:
        _PROBE_CACHE.clear()
    _PROBE_CACHE[key] = result
    return result


async def probe_many(paths: list[str]) -> dict[str, tuple[int | None, bool]]:
    """複数のソースをまとめて下調べする（同時実行数は制限つき）。

    クリップごとに順番へ ffprobe を掛けると、長いタイムラインでは読み取りの
    たびに何十プロセスも直列に待つことになる。ここで重複を潰してから
    :data:`PROBE_CONCURRENCY` 本ずつ並べて走らせる。
    """
    unique = sorted({path for path in paths if path})
    if not unique:
        return {}
    limit = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def one(path: str) -> tuple[str, tuple[int | None, bool]]:
        async with limit:
            return path, await probe_cached(path)

    return dict(await asyncio.gather(*(one(path) for path in unique)))


# --------------------------------------------------------------------------
# クリップの検証（純関数。ルーターとテストの両方から使う）
# --------------------------------------------------------------------------

#: 尺と切り出しのつじつまで許す誤差（ミリ秒）。速度で割ると端数が出るため
DURATION_TOLERANCE_MS = 2

#: 切り出し位置を持たないソース（``duration_ms`` がそのまま尺）
_SPANLESS_SOURCES = ("gap", "text", "image")


def expected_duration_ms(in_ms: int, out_ms: int, speed: float) -> int:
    """切り出しと速度から決まるタイムライン上の尺。"""
    if speed <= 0:
        raise TimelineError(f"速度が不正です: {speed}")
    return int(round((out_ms - in_ms) / speed))


def _overlap_ms(clip: TimelineClipInput) -> int:
    """前のクリップと重なる長さ（繋ぎが無ければ 0）。"""
    if not clip.transition_kind or clip.transition_ms <= 0:
        return 0
    return int(clip.transition_ms)


def validate_clips(
    clips: list[TimelineClipInput], track_kinds: dict[str, str] | None = None
) -> None:
    """タイムラインの約束をひととおり確かめる（破れていたら :class:`TimelineError`）。

    クリップ 1 つずつ:

    - ``in_ms < out_ms``（長さ 0 のクリップは置けない）
    - ``duration_ms == (out_ms - in_ms) / speed``（リタイム込みのつじつま）
    - ``speed`` は :data:`app.timeline_export.SPEED_MIN` 〜 ``SPEED_MAX``。
      音声・字幕のクリップは等速のみ
    - ``start_ms >= 0``
    - ``text`` クリップは本文を持つ

    トラックの中の並び:

    - 同じトラックの中でクリップが重ならない（隙間は空けてよい）
    - ただし**繋ぎ（トランジション）を持つクリップだけは前へ食い込む**
      （オーバーラップ方式）。食い込む量はちょうど ``transition_ms`` で、
      隣り合う 2 つの短いほうの 1/2 まで
    - 繋ぎを置けるのは映像トラックだけで、トラックの先頭には置けない

    ``gap`` / ``text`` / ``image`` は切り出し位置を持たないので、``in_ms`` /
    ``out_ms`` は見ない（``duration_ms`` がそのまま尺になる）。
    """
    kinds = track_kinds or {}
    for index, clip in enumerate(clips):
        where = f"クリップ {index + 1}"
        if clip.start_ms < 0:
            raise TimelineError(f"{where}: 開始位置が負です")
        if clip.duration_ms <= 0:
            raise TimelineError(f"{where}: 尺が 0 以下です")
        if not (clip.track_id or "").strip():
            raise TimelineError(f"{where}: トラックが指定されていません")

        kind = kinds.get(clip.track_id, "video")
        speed = float(clip.speed or 1.0)
        if kind != "video" and abs(speed - 1.0) >= 1e-9:
            raise TimelineError(f"{where}: 速度を変えられるのは映像クリップだけです")
        if not (SPEED_MIN - 1e-9 <= speed <= SPEED_MAX + 1e-9):
            raise TimelineError(
                f"{where}: 速度は {SPEED_MIN}〜{SPEED_MAX} の範囲です（{speed:g}）"
            )

        if clip.source_kind == "text":
            text = ((clip.text_payload or {}).get("text") or "").strip()
            if not text:
                raise TimelineError(f"{where}: テロップの本文が空です")
        if clip.source_kind in ("image", "asset_file", "library", "job", "take"):
            if not (clip.source_id or "").strip():
                raise TimelineError(f"{where}: ソースが指定されていません")
        if clip.source_kind in _SPANLESS_SOURCES:
            continue

        if clip.in_ms < 0:
            raise TimelineError(f"{where}: 切り出しの開始位置が負です")
        if clip.in_ms >= clip.out_ms:
            raise TimelineError(f"{where}: 切り出しの範囲が不正です（in < out）")
        expected = expected_duration_ms(clip.in_ms, clip.out_ms, speed)
        if abs(clip.duration_ms - expected) > DURATION_TOLERANCE_MS:
            how = "等速" if abs(speed - 1.0) < 1e-9 else f"速度 {speed:g}"
            raise TimelineError(
                f"{where}: 尺と切り出しの長さが合いません"
                f"（{how}なら {expected}ms）"
            )

    by_track: dict[str, list[TimelineClipInput]] = {}
    for clip in clips:
        by_track.setdefault(clip.track_id, []).append(clip)
    for track_id, group in by_track.items():
        kind = kinds.get(track_id, "video")
        ordered = sorted(group, key=lambda clip: clip.start_ms)
        if _overlap_ms(ordered[0]):
            raise TimelineError(
                f"トラック {track_id} の先頭のクリップには繋ぎを置けません"
            )
        for previous, current in zip(ordered, ordered[1:]):
            overlap = _overlap_ms(current)
            if overlap and kind != "video":
                raise TimelineError(
                    f"トラック {track_id}: 繋ぎを置けるのは映像トラックだけです"
                )
            if overlap:
                if current.transition_kind not in TRANSITIONS:
                    raise TimelineError(
                        f"トラック {track_id}: 知らない繋ぎです"
                        f"（{current.transition_kind}）"
                    )
                if not (TRANSITION_MIN_MS <= overlap <= TRANSITION_MAX_MS):
                    raise TimelineError(
                        f"トラック {track_id}: 繋ぎの長さは"
                        f" {TRANSITION_MIN_MS}〜{TRANSITION_MAX_MS}ms です"
                        f"（{overlap}ms）"
                    )
                shortest = min(previous.duration_ms, current.duration_ms)
                if overlap * 2 > shortest:
                    raise TimelineError(
                        f"トラック {track_id}: 繋ぎが長すぎます"
                        f"（隣り合うクリップの短いほう {shortest}ms の半分まで）"
                    )
                wanted = previous.start_ms + previous.duration_ms - overlap
                if current.start_ms != wanted:
                    raise TimelineError(
                        f"トラック {track_id}: 繋ぎのぶんの重なりが合いません"
                        f"（{wanted}ms から始まるはずが {current.start_ms}ms）"
                    )
                continue
            if current.start_ms < previous.start_ms + previous.duration_ms:
                raise TimelineError(
                    f"トラック {track_id} でクリップが重なっています"
                    f"（{previous.start_ms}ms 〜 と {current.start_ms}ms 〜）"
                )


# --------------------------------------------------------------------------
# 読み取り
# --------------------------------------------------------------------------

def _row_to_timeline(row: aiosqlite.Row) -> StudioTimeline:
    return StudioTimeline(**dict(row))


async def _fetch_timeline(
    conn: aiosqlite.Connection, timeline_id: str
) -> StudioTimeline | None:
    async with conn.execute(
        "SELECT * FROM studio_timelines WHERE id = ?", (timeline_id,)
    ) as cur:
        row = await cur.fetchone()
    return _row_to_timeline(row) if row else None


async def get_timeline(timeline_id: str) -> StudioTimeline | None:
    async with get_db() as conn:
        return await _fetch_timeline(conn, timeline_id)


async def list_timelines(project_id: str) -> list[StudioTimeline]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM studio_timelines WHERE project_id = ?"
            " ORDER BY created_at, id",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_timeline(row) for row in rows]


async def _take_sources(
    conn: aiosqlite.Connection, take_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Take id -> ``{"path": …, "label": …}``（見つからない Take は入らない）。

    ラベルは「話 / 場 / #カット番号」。カット番号は場の中の並び順（0 始まり）を
    1 始まりにしたもので、:func:`app.studio._fetch_shots` の並びと同じ意味になる。
    """
    if not take_ids:
        return {}
    placeholders = ", ".join("?" * len(take_ids))
    async with conn.execute(
        "SELECT t.id AS take_id, j.video_path AS video_path,"
        "       s.title AS shot_title, s.sort_order AS shot_order,"
        "       sc.title AS scene_title, sc.sort_order AS scene_order,"
        "       ep.title AS episode_title, ep.sort_order AS episode_order"
        "  FROM studio_takes t"
        "  LEFT JOIN jobs j ON j.id = t.job_id"
        "  LEFT JOIN studio_shots s ON s.id = t.shot_id"
        "  LEFT JOIN studio_scenes sc ON sc.id = s.scene_id"
        "  LEFT JOIN studio_episodes ep ON ep.id = sc.episode_id"
        f" WHERE t.id IN ({placeholders})",
        tuple(take_ids),
    ) as cur:
        rows = await cur.fetchall()
    sources: dict[str, dict[str, Any]] = {}
    for row in rows:
        parts: list[str] = []
        if row["episode_title"] or row["episode_order"] is not None:
            parts.append(
                row["episode_title"] or f"第 {int(row['episode_order'] or 0) + 1} 話"
            )
        if row["scene_title"] or row["scene_order"] is not None:
            parts.append(row["scene_title"] or f"場 {int(row['scene_order'] or 0) + 1}")
        if row["shot_order"] is not None:
            number = f"#{int(row['shot_order']) + 1}"
            parts.append(f"{number} {row['shot_title']}".strip())
        sources[str(row["take_id"])] = {
            "path": row["video_path"],
            "label": " / ".join(part for part in parts if part),
        }
    return sources


def _served_dirs() -> tuple[tuple[str, Path], ...]:
    """``/outputs`` / ``/library`` / ``/assets`` の配信プレフィックスと置き場。

    毎回モジュール変数を読み直すのは、テストが置き場を差し替えるため
    （:func:`_output_url` が ``OUTPUTS_DIR`` をその場で見ているのと同じ理由）。
    """
    return (
        ("/outputs", OUTPUTS_DIR),
        ("/library", LIBRARY_DIR),
        ("/assets", ASSETS_DIR),
    )


def _media_url(path: str | None) -> str | None:
    """置き場のどれかに入っているファイルを配信 URL にする（外なら None）。

    :func:`_output_url` の一般化。素材ビンはライブラリ（``/library``）と
    アップロード素材（``/assets``）も扱うので、置き場ごとに順に当てる。
    """
    if not path:
        return None
    resolved = rebase_stored_path(path)
    for prefix, directory in _served_dirs():
        try:
            relative = resolved.resolve().relative_to(directory.resolve())
        except (ValueError, OSError):
            continue
        return f"{prefix}/{relative.as_posix()}"
    return None


def split_image_source(source_id: str | None) -> tuple[str, str]:
    """静止画クリップの ``source_id``（``library:<id>``）を出どころと id に割る。

    印が無いものはライブラリの id として読む（画面が古い形を送ってきても
    落とさない）。知らない印は空の出どころで返し、呼び出し側が欠落にする。
    """
    raw = (source_id or "").strip()
    provider, _, rest = raw.partition(":")
    if not rest:
        return ("library", raw)
    return (provider if provider in IMAGE_PROVIDERS else "", rest)


async def _library_sources(
    conn: aiosqlite.Connection, item_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """ライブラリの id -> ``{"path": …, "label": …}``。"""
    if not item_ids:
        return {}
    placeholders = ", ".join("?" * len(item_ids))
    async with conn.execute(
        f"SELECT id, name, path, kind FROM library WHERE id IN ({placeholders})",
        tuple(item_ids),
    ) as cur:
        rows = await cur.fetchall()
    return {
        str(row["id"]): {
            "path": row["path"],
            "label": f"ライブラリ / {row['name'] or row['id']}",
        }
        for row in rows
    }


async def _job_sources(
    conn: aiosqlite.Connection, job_ids: list[str], column: str
) -> dict[str, dict[str, Any]]:
    """ジョブの id -> ``{"path": …, "label": …}``（``column`` の出力を使う）。

    ``column`` は呼び出し側が決める（映像トラックなら ``video_path``、音声なら
    ``audio_output_path``、静止画なら ``image_path``）。列名は固定の 3 つしか
    渡らないので、SQL へ埋め込んでよい。
    """
    if not job_ids or column not in ("video_path", "audio_output_path", "image_path"):
        return {}
    placeholders = ", ".join("?" * len(job_ids))
    async with conn.execute(
        f"SELECT id, {column} AS path, mode, user_input, created_at"
        f"  FROM jobs WHERE id IN ({placeholders})",
        tuple(job_ids),
    ) as cur:
        rows = await cur.fetchall()
    sources: dict[str, dict[str, Any]] = {}
    for row in rows:
        title = (row["user_input"] or "").strip().splitlines()[:1]
        sources[str(row["id"])] = {
            "path": row["path"],
            "label": "ジョブ / " + (title[0][:40] if title else str(row["id"])[:8]),
        }
    return sources


async def _asset_file_sources(
    conn: aiosqlite.Connection, file_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """素材のリファレンス id -> ``{"path": …, "label": …}``。"""
    if not file_ids:
        return {}
    placeholders = ", ".join("?" * len(file_ids))
    async with conn.execute(
        "SELECT f.id AS id, f.path AS path, f.role AS role, f.caption AS caption,"
        "       a.name AS asset_name"
        "  FROM studio_asset_files f"
        "  LEFT JOIN studio_assets a ON a.id = f.asset_id"
        f" WHERE f.id IN ({placeholders})",
        tuple(file_ids),
    ) as cur:
        rows = await cur.fetchall()
    return {
        str(row["id"]): {
            "path": row["path"],
            "label": "素材 / "
            + " / ".join(
                part
                for part in (row["asset_name"], row["caption"] or row["role"])
                if part
            ),
        }
        for row in rows
    }


async def _resolve_sources(
    conn: aiosqlite.Connection, refs: set[tuple[str, str, str]]
) -> dict[tuple[str, str], dict[str, Any]]:
    """クリップのソース参照をまとめて解決する。

    ``refs`` は ``(source_kind, source_id, トラックの種別)`` の集合。同じ
    ``source_kind='job'`` でも映像トラックなら動画、音声トラックなら生成音声を
    見るので、トラックの種別まで込みで引く。返る辞書のキーは
    ``(source_kind, source_id)``。
    """
    by_kind: dict[str, set[str]] = {}
    job_columns: dict[str, set[str]] = {}
    image_ids: dict[str, set[str]] = {}
    for source_kind, source_id, track_kind in refs:
        if source_kind == "image":
            provider, real_id = split_image_source(source_id)
            if provider:
                image_ids.setdefault(provider, set()).add(real_id)
            continue
        if source_kind == "job":
            column = "audio_output_path" if track_kind == "audio" else "video_path"
            job_columns.setdefault(column, set()).add(source_id)
            continue
        by_kind.setdefault(source_kind, set()).add(source_id)

    resolved: dict[tuple[str, str], dict[str, Any]] = {}
    for take_id, info in (
        await _take_sources(conn, sorted(by_kind.get("take", ())))
    ).items():
        resolved[("take", take_id)] = info
    for item_id, info in (
        await _library_sources(conn, sorted(by_kind.get("library", ())))
    ).items():
        resolved[("library", item_id)] = info
    for file_id, info in (
        await _asset_file_sources(conn, sorted(by_kind.get("asset_file", ())))
    ).items():
        resolved[("asset_file", file_id)] = info
    for column, ids in job_columns.items():
        for job_id, info in (await _job_sources(conn, sorted(ids), column)).items():
            resolved[("job", job_id)] = info

    # 静止画は出どころが 3 つあるので、印つきの id のまま引き当てる。
    for provider, ids in image_ids.items():
        ordered = sorted(ids)
        if provider == "library":
            found = await _library_sources(conn, ordered)
        elif provider == "asset_file":
            found = await _asset_file_sources(conn, ordered)
        else:
            found = await _job_sources(conn, ordered, "image_path")
        for real_id, info in found.items():
            resolved[("image", f"{provider}:{real_id}")] = info
    return resolved


async def timeline_detail(timeline_id: str) -> StudioTimelineDetail | None:
    """トラックとクリップ込みのフル EDL（ソースは解決して返す）。

    クリップごとに再生 URL・ソースの長さ・``missing``（実ファイル不在）を足す。
    尺の下調べは ffprobe なので、同じソースを何度も引かないようまとめて行う。
    """
    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            return None
        async with conn.execute(
            "SELECT * FROM timeline_tracks WHERE timeline_id = ?"
            " ORDER BY sort_order, id",
            (timeline_id,),
        ) as cur:
            track_rows = await cur.fetchall()
        async with conn.execute(
            "SELECT * FROM timeline_clips WHERE timeline_id = ?"
            " ORDER BY track_id, start_ms, id",
            (timeline_id,),
        ) as cur:
            clip_rows = await cur.fetchall()
        track_kinds = {str(row["id"]): str(row["kind"]) for row in track_rows}
        refs = {
            (
                str(row["source_kind"]),
                str(row["source_id"]),
                track_kinds.get(str(row["track_id"]), "video"),
            )
            for row in clip_rows
            if row["source_kind"] not in ("gap", "text") and row["source_id"]
        }
        sources = await _resolve_sources(conn, refs)

    # 同じソースを何本のクリップが指していても ffprobe は 1 回だけ、まとめて。
    existing: dict[tuple[str, str], str] = {}
    for key, info in sources.items():
        path = info.get("path")
        resolved = rebase_stored_path(path) if path else None
        if resolved is not None and resolved.is_file():
            existing[key] = str(resolved)
    probed = await probe_many(list(existing.values()))

    clips_by_track: dict[str, list[TimelineClip]] = {}
    for row in clip_rows:
        data = dict(row)
        data.pop("project_id", None)
        data["text_payload"] = json.loads(data["text_payload"] or "null") or None
        source_kind = str(data["source_kind"])
        key = (source_kind, str(data.get("source_id") or ""))
        info = sources.get(key) if source_kind not in ("gap", "text") else None
        path = existing.get(key)
        data["video_url"] = _media_url((info or {}).get("path")) if path else None
        data["source_duration_ms"] = probed.get(path or "", (None, False))[0]
        # 隙間（gap）とテロップ（text）はソースを持たないので欠落にはならない。
        data["missing"] = source_kind not in ("gap", "text") and path is None
        data["label"] = (info or {}).get("label", "")
        if source_kind == "text":
            data["label"] = str((data["text_payload"] or {}).get("text") or "")
        clips_by_track.setdefault(str(data["track_id"]), []).append(TimelineClip(**data))

    tracks = [
        TimelineTrack(
            **{
                key: value
                for key, value in dict(row).items()
                if key not in ("project_id",)
            },
            clips=clips_by_track.get(str(row["id"]), []),
        )
        for row in track_rows
    ]
    duration_ms = max(
        (clip.start_ms + clip.duration_ms for track in tracks for clip in track.clips),
        default=0,
    )
    return StudioTimelineDetail(
        **timeline.model_dump(), tracks=tracks, duration_ms=duration_ms
    )


# --------------------------------------------------------------------------
# 作成・更新・削除
# --------------------------------------------------------------------------

async def _selected_take_videos(
    conn: aiosqlite.Connection, project_id: str, episode_id: str
) -> list[tuple[str, str, float | None]]:
    """その話の「採用 Take の動画があるカット」を場 -> カット順に並べる。

    返すのは ``(take_id, path, 計画開始秒)``。並び順は
    :func:`app.studio._fetch_shots` と同じ規則（話 -> 場 -> カット）で、ここは
    1 つの話に絞ってあるので場の並び順とカットの並び順で決まる。計画開始秒は
    音源基準で組むときだけ入っている（None = 並び順で置く従来どおり）。

    ``timeline_role`` が ``auto`` でないカット（差し込み専用・使わない）は
    自動配置の対象外なので外す（SPEC §7.3）。
    """
    async with conn.execute(
        "SELECT s.selected_take_id AS take_id, j.video_path AS video_path,"
        "       s.planned_start_seconds AS planned"
        "  FROM studio_shots s"
        "  JOIN studio_scenes sc ON sc.id = s.scene_id"
        "  JOIN studio_takes t ON t.id = s.selected_take_id"
        "  LEFT JOIN jobs j ON j.id = t.job_id"
        " WHERE s.project_id = ? AND sc.episode_id = ?"
        "   AND s.timeline_role = 'auto'"
        " ORDER BY sc.sort_order, sc.created_at, sc.id,"
        "          s.sort_order, s.created_at, s.id",
        (project_id, episode_id),
    ) as cur:
        rows = await cur.fetchall()
    found: list[tuple[str, str, float | None]] = []
    for row in rows:
        path = row["video_path"]
        if not path:
            continue
        resolved = rebase_stored_path(path)
        if not resolved.is_file():
            continue
        planned = row["planned"]
        found.append((
            str(row["take_id"]),
            str(resolved),
            None if planned is None else float(planned),
        ))
    return found


async def _publish_timeline(
    project_id: str, timeline_id: str, op: str = "update"
) -> None:
    """編集タブの変更を画面へ流す（WS ``type: "studio"``、``entity: "timeline"``）。

    外部エージェントがつなぎを触ったときに、開いているブラウザがすぐ追いつける
    ようにするためのもの。正本は DB なので、流すのは「どの作品のどのタイムライン
    が動いたか」だけで、受け取り側は取り直す。**commit のあと**に呼ぶ。
    """
    try:
        await ws.publish_studio(project_id, "timeline", timeline_id, op)
    except Exception:  # noqa: BLE001 - 通知の失敗で編集を壊さない
        log.debug("timeline イベントを配信できませんでした: %s", timeline_id)


async def create_timeline(
    project_id: str, payload: StudioTimelineCreate
) -> StudioTimelineDetail:
    """タイムラインを 1 本作る（``episode_id`` があれば自動配置つき）。

    自動配置では、その話のカットを場 -> カット順に走査し、採用 Take の動画が
    **実在する**ものだけを V1 へ隙間なく並べる。クリップの尺は ffprobe で読み、
    読めなければ :data:`FALLBACK_CLIP_MS` に落とす（並びは残したいため）。
    """
    async with get_db() as conn:
        async with conn.execute(
            "SELECT id, name FROM studio_projects WHERE id = ?", (project_id,)
        ) as cur:
            project = await cur.fetchone()
        if project is None:
            raise TimelineNotFound("project not found")

        episode_id = (payload.episode_id or "").strip() or None
        episode_title = ""
        if episode_id is not None:
            async with conn.execute(
                "SELECT title, sort_order FROM studio_episodes"
                " WHERE id = ? AND project_id = ?",
                (episode_id, project_id),
            ) as cur:
                episode = await cur.fetchone()
            if episode is None:
                raise TimelineNotFound("episode not found")
            episode_title = (
                episode["title"] or f"第 {int(episode['sort_order'] or 0) + 1} 話"
            )

        name = (payload.name or "").strip() or (
            f"{episode_title} の編集" if episode_title else f"{project['name']} の編集"
        )
        timeline_id = new_id()
        now = _now()
        planned_end = payload.planned_end_seconds
        if planned_end is not None and float(planned_end) <= 0:
            planned_end = None
        await conn.execute(
            "INSERT INTO studio_timelines"
            " (id, project_id, episode_id, name, fps, width, height,"
            "  gap_fill, planned_end_seconds, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                timeline_id,
                project_id,
                episode_id,
                name,
                float(payload.fps or DEFAULT_FPS),
                int(payload.width or DEFAULT_WIDTH),
                int(payload.height or DEFAULT_HEIGHT),
                payload.gap_fill or DEFAULT_GAP_FILL,
                None if planned_end is None else float(planned_end),
                now,
                now,
            ),
        )
        track_id = new_id()
        await conn.execute(
            "INSERT INTO timeline_tracks"
            " (id, timeline_id, project_id, kind, name, sort_order, muted, locked)"
            " VALUES (?, ?, ?, 'video', 'V1', 0, 0, 0)",
            (track_id, timeline_id, project_id),
        )

        placed: list[PlannedClip] = []
        if episode_id is not None:
            for take_id, path, planned in await _selected_take_videos(
                conn, project_id, episode_id
            ):
                duration_ms, _ = await probe_media(path)
                duration_ms = duration_ms or FALLBACK_CLIP_MS
                placed.append(
                    PlannedClip(
                        TimelineClipInput(
                            track_id=track_id,
                            start_ms=0,  # plan_layout / relayout が決める
                            duration_ms=duration_ms,
                            source_kind="take",
                            source_id=take_id,
                            in_ms=0,
                            out_ms=duration_ms,
                        ),
                        planned,
                        duration_ms,
                    )
                )
        if placed:
            # 計画開始秒を持つカットがあれば音源基準（足りないぶんは gap_fill の
            # 通りに埋まる）、無ければ今までどおり先頭から隙間なく詰める。
            await _write_clips(
                conn,
                timeline_id,
                project_id,
                plan_layout(
                    placed,
                    track_id,
                    float(payload.fps or DEFAULT_FPS),
                    gap_fill=payload.gap_fill or DEFAULT_GAP_FILL,
                    end_ms=(
                        None
                        if planned_end is None
                        else int(round(float(planned_end) * 1000))
                    ),
                ),
            )
        await conn.commit()
    await _publish_timeline(project_id, timeline_id, "create")

    detail = await timeline_detail(timeline_id)
    assert detail is not None
    return detail


async def update_timeline(
    timeline_id: str, changes: dict[str, Any]
) -> StudioTimeline | None:
    """指定された項目だけ書き換える（送られなかった項目は今のまま）。

    ``planned_end_seconds`` だけは **0 以下を送ると外れる**（音源の尺は正の数
    しか意味を持たないので、「未指定へ戻す」をこの値で表す）。
    """
    fields = {
        name: value
        for name, value in changes.items()
        if name in ("name", "fps", "width", "height", "gap_fill")
        and value is not None
    }
    planned_end = changes.get("planned_end_seconds")
    if planned_end is not None:
        fields["planned_end_seconds"] = (
            None if float(planned_end) <= 0 else float(planned_end)
        )
    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            return None
        if fields:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            await conn.execute(
                f"UPDATE studio_timelines SET {assignments}, updated_at = ?"
                " WHERE id = ?",
                (*fields.values(), _now(), timeline_id),
            )
            await conn.commit()
            await _publish_timeline(timeline.project_id, timeline_id)
        return await _fetch_timeline(conn, timeline_id)


async def delete_timeline(timeline_id: str) -> bool:
    """タイムラインとその中身を消す（トラック・クリップ・書き出しの記録）。

    外部キーの CASCADE は張っていないので、後始末はここで行う。書き出した
    ファイル（``outputs/exports/…``）は成果物なので残す（ジョブの出力と同じ扱い）。
    """
    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            return False
        await conn.execute(
            "DELETE FROM timeline_clips WHERE timeline_id = ?", (timeline_id,)
        )
        await conn.execute(
            "DELETE FROM timeline_tracks WHERE timeline_id = ?", (timeline_id,)
        )
        await conn.execute(
            "DELETE FROM timeline_exports WHERE timeline_id = ?", (timeline_id,)
        )
        await conn.execute(
            "DELETE FROM timeline_fx_events WHERE timeline_id = ?", (timeline_id,)
        )
        await conn.execute(
            "DELETE FROM timeline_fx WHERE timeline_id = ?", (timeline_id,)
        )
        await conn.execute("DELETE FROM studio_timelines WHERE id = ?", (timeline_id,))
        await conn.commit()
    await _publish_timeline(timeline.project_id, timeline_id, "delete")
    return True


async def replace_clips(
    timeline_id: str, clips: list[TimelineClipInput]
) -> StudioTimelineDetail | None:
    """このタイムラインのクリップを ``clips`` の通りにする（全置換）。

    画面の自動保存の受け口。1 つのトランザクションで消してから入れ直すので、
    途中で落ちても「前の状態」か「送られた状態」のどちらかになる。
    """
    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            return None
        async with conn.execute(
            "SELECT id, kind FROM timeline_tracks WHERE timeline_id = ?",
            (timeline_id,),
        ) as cur:
            kinds = {str(row["id"]): str(row["kind"]) for row in await cur.fetchall()}
        unknown = sorted({clip.track_id for clip in clips} - set(kinds))
        if unknown:
            raise TimelineError(
                f"このタイムラインに無いトラックです: {', '.join(unknown)}"
            )
        validate_clips(clips, kinds)

        await _write_clips(conn, timeline_id, timeline.project_id, clips)
        await conn.commit()
    await _publish_timeline(timeline.project_id, timeline_id)
    return await timeline_detail(timeline_id)


async def _write_clips(
    conn: aiosqlite.Connection,
    timeline_id: str,
    project_id: str,
    clips: list[TimelineClipInput],
) -> None:
    """このタイムラインのクリップを ``clips`` の通りに書き直す（commit は呼び出し側）。"""
    await conn.execute(
        "DELETE FROM timeline_clips WHERE timeline_id = ?", (timeline_id,)
    )
    ordered = sorted(clips, key=lambda clip: (clip.track_id, clip.start_ms))
    for order, clip in enumerate(ordered):
        await conn.execute(
            "INSERT INTO timeline_clips"
            " (id, track_id, timeline_id, project_id, start_ms, duration_ms,"
            "  source_kind, source_id, in_ms, out_ms, gain_db, fade_in_ms,"
            "  fade_out_ms, transition_kind, transition_ms, text_payload, speed,"
            "  sort_order)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                clip.id or new_id(),
                clip.track_id,
                timeline_id,
                project_id,
                clip.start_ms,
                clip.duration_ms,
                clip.source_kind,
                clip.source_id,
                clip.in_ms,
                clip.out_ms,
                clip.gain_db,
                clip.fade_in_ms,
                clip.fade_out_ms,
                clip.transition_kind,
                clip.transition_ms,
                json.dumps(clip.text_payload, ensure_ascii=False)
                if clip.text_payload
                else None,
                float(clip.speed or 1.0),
                order,
            ),
        )
    await conn.execute(
        "UPDATE studio_timelines SET updated_at = ? WHERE id = ?",
        (_now(), timeline_id),
    )


# --------------------------------------------------------------------------
# FX トラック（タイムラインに載せる演出。SPEC §7.3）
# --------------------------------------------------------------------------
#
# 演出の正本は Remotion 側の zod スキーマ（``remotion/src/schema.ts``）なので、
# ここでの検証は**軽い**: イベントが JSON のオブジェクトで、``type`` が文字列・
# ``t`` が数値であることまでしか見ない。厳密な検証はプレビュー（``@remotion/player``）
# とレンダ（``npx remotion render``）の zod に任せる——ここで真似ると、演出を
# 足すたびに 2 か所を直すことになるため。
#
# 作るのは外部 API（AI）、人が画面でやるのは調整と削除（issue #56）。

#: ``timeline_fx.settings`` に入れる項目（``FxOverlay`` の props と同じ名前）
FX_SETTING_KEYS = ("theme", "seed", "ambient", "backgroundColor")


def _validate_fx_event(raw: Any) -> dict[str, Any]:
    """イベント 1 つぶんの軽い検証（通れば dict をそのまま返す）。"""
    if not isinstance(raw, dict):
        raise TimelineError("イベントは JSON のオブジェクトで送ってください")
    kind = raw.get("type")
    if not isinstance(kind, str) or not kind.strip():
        raise TimelineError("イベントに type（文字列）がありません")
    seconds = raw.get("t")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise TimelineError(f"イベント '{kind}' の t（秒・数値）がありません")
    return raw


def _fx_event_input(item: Any) -> tuple[str, bool, dict[str, Any]]:
    """PUT で送られた 1 件を ``(id, enabled, event)`` にほどく。

    ``FxOverlay`` の props をそのまま投げられるよう、**生のイベント**
    （``{"type": …}``）と、GET が返す ``{"id", "enabled", "event"}`` の
    どちらの形でも受ける。
    """
    if not isinstance(item, dict):
        raise TimelineError("events は JSON のオブジェクトの配列で送ってください")
    if isinstance(item.get("event"), dict):
        return (
            str(item.get("id") or ""),
            bool(item.get("enabled", True)),
            _validate_fx_event(item["event"]),
        )
    raw = dict(item)
    event_id = str(raw.pop("id", "") or "")
    enabled = bool(raw.pop("enabled", True))
    return event_id, enabled, _validate_fx_event(raw)


def _row_to_fx_event(row: aiosqlite.Row) -> TimelineFxEvent:
    try:
        event = json.loads(row["event"] or "{}")
    except ValueError:  # pragma: no cover - 自分で書いた JSON なので通らない
        event = {}
    return TimelineFxEvent(
        id=str(row["id"]),
        enabled=bool(row["enabled"]),
        event=event if isinstance(event, dict) else {},
    )


async def _fetch_fx(
    conn: aiosqlite.Connection, timeline_id: str
) -> TimelineFx:
    """そのタイムラインの演出（1 行も無ければ空の :class:`TimelineFx`）。"""
    async with conn.execute(
        "SELECT settings FROM timeline_fx WHERE timeline_id = ?", (timeline_id,)
    ) as cur:
        row = await cur.fetchone()
    settings: dict[str, Any] = {}
    if row is not None:
        try:
            parsed = json.loads(row["settings"] or "{}")
        except ValueError:  # pragma: no cover - 同上
            parsed = {}
        if isinstance(parsed, dict):
            settings = parsed
    async with conn.execute(
        "SELECT * FROM timeline_fx_events WHERE timeline_id = ?"
        " ORDER BY sort_order, id",
        (timeline_id,),
    ) as cur:
        rows = await cur.fetchall()
    return TimelineFx(
        timeline_id=timeline_id,
        theme=settings.get("theme"),
        seed=settings.get("seed"),
        ambient=settings.get("ambient"),
        backgroundColor=settings.get("backgroundColor"),
        events=[_row_to_fx_event(row) for row in rows],
    )


async def get_fx(timeline_id: str) -> TimelineFx | None:
    """GET /timelines/{id}/fx（タイムラインが無ければ None）。"""
    async with get_db() as conn:
        if await _fetch_timeline(conn, timeline_id) is None:
            return None
        return await _fetch_fx(conn, timeline_id)


async def _write_fx_settings(
    conn: aiosqlite.Connection,
    timeline_id: str,
    project_id: str,
    settings: dict[str, Any],
) -> None:
    """全体設定（theme / seed / ambient / backgroundColor）を 1 行に書く。"""
    await conn.execute(
        "INSERT INTO timeline_fx (timeline_id, project_id, settings, updated_at)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(timeline_id) DO UPDATE SET"
        "   settings = excluded.settings, updated_at = excluded.updated_at",
        (
            timeline_id,
            project_id,
            json.dumps(settings, ensure_ascii=False),
            _now(),
        ),
    )


async def _fx_touched(
    conn: aiosqlite.Connection,
    timeline: StudioTimeline,
    actor: str,
    action: str,
) -> None:
    """演出を書き換えたことをリビジョンへ残す（commit は呼び出し側）。"""
    await studio_service._record_revision(
        conn,
        timeline.project_id,
        actor,
        action,
        entity_kind="timeline",
        entity_id=timeline.id,
    )


async def replace_fx(
    timeline_id: str, payload: TimelineFxUpdate, *, actor: str = "user"
) -> TimelineFx:
    """PUT /timelines/{id}/fx: 演出を丸ごと置き換える。

    ``FxOverlay`` の props をそのまま投げられる（``base`` / ``audio`` / ``fps``
    などタイムラインが持っているものは無視する）。``events[].id`` を送れば
    引き継ぎ、省略すれば採番する。
    """
    parsed = [_fx_event_input(item) for item in payload.events]
    settings = {
        key: value
        for key, value in (
            ("theme", payload.theme),
            ("seed", payload.seed),
            ("ambient", payload.ambient),
            ("backgroundColor", payload.backgroundColor),
        )
        if value is not None
    }
    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            raise TimelineNotFound("timeline not found")
        await _check_edl_revision(
            conn, timeline_id, timeline.project_id, timeline.name,
            payload.base_revision,
        )
        await _write_fx_settings(
            conn, timeline_id, timeline.project_id, settings
        )
        await conn.execute(
            "DELETE FROM timeline_fx_events WHERE timeline_id = ?", (timeline_id,)
        )
        for order, (event_id, enabled, event) in enumerate(parsed):
            await conn.execute(
                "INSERT INTO timeline_fx_events"
                " (id, timeline_id, project_id, sort_order, enabled, event)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event_id or new_id(),
                    timeline_id,
                    timeline.project_id,
                    order,
                    1 if enabled else 0,
                    json.dumps(event, ensure_ascii=False),
                ),
            )
        await _fx_touched(
            conn,
            timeline,
            actor,
            f"タイムライン『{timeline.name}』の演出を差し替え"
            f"（{len(parsed)} 件）",
        )
        await studio_service._commit(conn)
        fresh = await _fetch_fx(conn, timeline_id)
    await _publish_timeline(timeline.project_id, timeline_id)
    return fresh


async def add_fx_event(
    timeline_id: str, payload: TimelineFxEventCreate, *, actor: str = "user"
) -> TimelineFx:
    """POST /timelines/{id}/fx/events: イベントを 1 つ足す。"""
    event = _validate_fx_event(payload.event)
    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            raise TimelineNotFound("timeline not found")
        await _check_edl_revision(
            conn, timeline_id, timeline.project_id, timeline.name,
            payload.base_revision,
        )
        async with conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next"
            "  FROM timeline_fx_events WHERE timeline_id = ?",
            (timeline_id,),
        ) as cur:
            tail = int((await cur.fetchone())["next"])
        order = tail if payload.sort_order is None else int(payload.sort_order)
        await conn.execute(
            "INSERT INTO timeline_fx_events"
            " (id, timeline_id, project_id, sort_order, enabled, event)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                timeline_id,
                timeline.project_id,
                order,
                1 if payload.enabled else 0,
                json.dumps(event, ensure_ascii=False),
            ),
        )
        await _fx_touched(
            conn,
            timeline,
            actor,
            f"タイムライン『{timeline.name}』に演出"
            f"（{event.get('type')}）を追加",
        )
        await studio_service._commit(conn)
        fresh = await _fetch_fx(conn, timeline_id)
    await _publish_timeline(timeline.project_id, timeline_id)
    return fresh


async def update_fx_event(
    timeline_id: str,
    event_id: str,
    payload: TimelineFxEventUpdate,
    *,
    actor: str = "user",
) -> TimelineFx:
    """PATCH /timelines/{id}/fx/events/{event_id}: 1 件だけ書き換える。

    ``event`` は**浅いマージ**（送った項目だけ上書き、``null`` はその項目を
    消す）。画面の帯のドラッグはこの入り口で ``t`` / ``until`` を動かす。
    """
    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            raise TimelineNotFound("timeline not found")
        async with conn.execute(
            "SELECT * FROM timeline_fx_events WHERE id = ? AND timeline_id = ?",
            (event_id, timeline_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise TimelineNotFound("fx event not found")
        await _check_edl_revision(
            conn, timeline_id, timeline.project_id, timeline.name,
            payload.base_revision,
        )
        current = _row_to_fx_event(row)
        event = dict(current.event)
        if payload.event is not None:
            for name, value in payload.event.items():
                if value is None:
                    event.pop(name, None)
                else:
                    event[name] = value
            _validate_fx_event(event)
        enabled = current.enabled if payload.enabled is None else payload.enabled
        await conn.execute(
            "UPDATE timeline_fx_events SET enabled = ?, event = ? WHERE id = ?",
            (1 if enabled else 0, json.dumps(event, ensure_ascii=False), event_id),
        )
        await _fx_touched(
            conn,
            timeline,
            actor,
            f"タイムライン『{timeline.name}』の演出"
            f"（{event.get('type')}）を更新",
        )
        await studio_service._commit(conn)
        fresh = await _fetch_fx(conn, timeline_id)
    await _publish_timeline(timeline.project_id, timeline_id)
    return fresh


async def delete_fx_event(
    timeline_id: str,
    event_id: str,
    *,
    base_revision: int | None = None,
    actor: str = "user",
) -> TimelineFx:
    """DELETE /timelines/{id}/fx/events/{event_id}: 1 件消す。"""
    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            raise TimelineNotFound("timeline not found")
        async with conn.execute(
            "SELECT * FROM timeline_fx_events WHERE id = ? AND timeline_id = ?",
            (event_id, timeline_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise TimelineNotFound("fx event not found")
        await _check_edl_revision(
            conn, timeline_id, timeline.project_id, timeline.name, base_revision,
        )
        removed = _row_to_fx_event(row)
        await conn.execute(
            "DELETE FROM timeline_fx_events WHERE id = ?", (event_id,)
        )
        await _fx_touched(
            conn,
            timeline,
            actor,
            f"タイムライン『{timeline.name}』の演出"
            f"（{removed.event.get('type')}）を削除",
        )
        await studio_service._commit(conn)
        fresh = await _fetch_fx(conn, timeline_id)
    await _publish_timeline(timeline.project_id, timeline_id)
    return fresh


# --------------------------------------------------------------------------
# 書き出し
# --------------------------------------------------------------------------

def _row_to_export(row: aiosqlite.Row) -> TimelineExport:
    data = dict(row)
    try:
        data["params"] = json.loads(data.get("params") or "{}")
    except ValueError:  # pragma: no cover - 自分で書いた JSON なので通らない
        data["params"] = {}
    try:
        data["warnings"] = json.loads(data.get("warnings") or "[]")
    except ValueError:  # pragma: no cover - 同上
        data["warnings"] = []
    data["output_url"] = _output_url(data.get("output_path"))
    data["fx_video_url"] = _output_url(data.get("fx_video_path"))
    return TimelineExport(**data)


async def get_export(export_id: str) -> TimelineExport | None:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM timeline_exports WHERE id = ?", (export_id,)
        ) as cur:
            row = await cur.fetchone()
    return _row_to_export(row) if row else None


async def list_exports(timeline_id: str) -> list[TimelineExport]:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM timeline_exports WHERE timeline_id = ?"
            " ORDER BY created_at DESC, id DESC",
            (timeline_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_export(row) for row in rows]


async def count_running_exports() -> int:
    """まだ終わっていない書き出しの数（外部 API の投入上限に使う）。

    同じタイムラインの二重書き出しは :func:`start_export` が 409 で断るが、
    別々のタイムラインへ次々投入されると ffmpeg が並ぶだけ並んでしまう。
    数え方は :func:`app.jobs.count_pending_jobs` と揃える。
    """
    placeholders = ", ".join("?" * len(RUNNING_STATUSES))
    async with get_db() as conn:
        async with conn.execute(
            "SELECT COUNT(*) AS pending FROM timeline_exports"
            f" WHERE status IN ({placeholders})",
            RUNNING_STATUSES,
        ) as cur:
            return int((await cur.fetchone())["pending"])


async def _update_export(export_id: str, **fields: Any) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    async with get_db() as conn:
        await conn.execute(
            f"UPDATE timeline_exports SET {assignments} WHERE id = ?",
            (*fields.values(), export_id),
        )
        await conn.commit()


async def _resolve_clip_paths(
    detail: StudioTimelineDetail,
) -> dict[tuple[str, str], str]:
    """このタイムラインのクリップが指す**実在するファイル**の絶対パス。

    ``timeline_detail`` は配信 URL しか持たないので、書き出しのためにもう一度
    引き直す（キーは ``(source_kind, source_id)``）。
    """
    refs = {
        (clip.source_kind, clip.source_id or "", track.kind)
        for track in detail.tracks
        for clip in track.clips
        if clip.source_kind not in ("gap", "text") and clip.source_id
    }
    if not refs:
        return {}
    async with get_db() as conn:
        sources = await _resolve_sources(conn, refs)
    paths: dict[tuple[str, str], str] = {}
    for key, info in sources.items():
        path = info.get("path")
        if not path:
            continue
        resolved = rebase_stored_path(path)
        if resolved.is_file():
            paths[key] = str(resolved)
    return paths


def _subtitle_events(detail: StudioTimelineDetail) -> list[subtitles.SubtitleEvent]:
    """字幕トラックのテロップを、焼き込む順（時刻順）に並べる。"""
    events: list[subtitles.SubtitleEvent] = []
    for track in detail.tracks:
        if track.kind != "subtitle" or track.muted:
            continue
        for clip in track.clips:
            if clip.source_kind != "text":
                continue
            event = subtitles.event_from_clip(
                clip.start_ms, clip.duration_ms, clip.text_payload
            )
            if event is not None:
                events.append(event)
    return sorted(events, key=lambda event: (event.start_ms, event.end_ms))


async def build_spec(
    timeline_id: str, params: dict[str, Any], work_dir: Path | None = None
) -> ExportSpec:
    """このタイムラインの今の中身から、書き出し 1 回ぶんの :class:`ExportSpec`。

    焼くのは **V1（一番上の video トラック）** と、ミュートしていない音声トラック
    （A1…）・字幕トラック（T1）。ソースの実ファイルが無いクリップ（メディア欠落）は
    その尺の隙間（黒＋無音）に置き換える——ここまで来て途中で失敗させるより、
    欠けているところが目に見えるほうが直しやすいため（受付の時点では
    :func:`start_export` が 400 で断る）。

    テロップは ASS に書き出して ``work_dir`` へ置く（``work_dir`` を渡さないと
    焼き込みは省く。組み立てだけ見たいテストのため）。
    """
    detail = await timeline_detail(timeline_id)
    if detail is None:
        raise TimelineNotFound("timeline not found")
    video_tracks = [track for track in detail.tracks if track.kind == "video"]
    if not video_tracks:
        raise TimelineError("映像トラックがありません")
    clips = sorted(video_tracks[0].clips, key=lambda clip: clip.start_ms)
    if not clips:
        raise TimelineError("書き出せるクリップがありません")

    paths = await _resolve_clip_paths(detail)
    width, height = resolve_format(
        str(params.get("preset") or "timeline"),
        int(params.get("width") or detail.width),
        int(params.get("height") or detail.height),
    )
    # 幅・高さを直接指定されたらプリセットより優先する（規格の上書き）。
    if params.get("width"):
        width = int(params["width"])
    if params.get("height"):
        height = int(params["height"])

    export_clips: list[ExportClip] = []
    cursor = 0
    for index, clip in enumerate(clips):
        overlap = (
            clip.transition_ms
            if index > 0 and clip.transition_kind in TRANSITIONS and clip.transition_ms > 0
            else 0
        )
        # 繋ぎのないところでクリップの前に空きがあれば、その尺ぶんの隙間を
        # 入れて時間を合わせる（繋ぎがあるところは重なっているので空かない）。
        if not overlap and clip.start_ms > cursor:
            export_clips.append(
                ExportClip(
                    path=None,
                    in_ms=0,
                    out_ms=clip.start_ms - cursor,
                    duration_ms=clip.start_ms - cursor,
                    has_audio=False,
                )
            )
            cursor = clip.start_ms
        path = paths.get((clip.source_kind, clip.source_id or ""))
        is_image = clip.source_kind == "image"
        has_audio = False
        source_duration_ms: int | None = None
        if path is not None and not is_image:
            source_duration_ms, has_audio = await probe_cached(path)
        export_clips.append(
            ExportClip(
                path=path,
                in_ms=clip.in_ms if path and not is_image else 0,
                out_ms=(clip.out_ms if path and not is_image else clip.duration_ms),
                duration_ms=clip.duration_ms,
                has_audio=has_audio,
                gain_db=clip.gain_db,
                speed=clip.speed if path and not is_image else 1.0,
                kind="image" if (is_image and path) else "video",
                transition_kind=clip.transition_kind if overlap else None,
                transition_ms=overlap,
                source_duration_ms=source_duration_ms,
                name=clip.label or clip.source_id or "",
            )
        )
        cursor = clip.start_ms + clip.duration_ms

    audio_clips: list[ExportAudioClip] = []
    for track in detail.tracks:
        if track.kind != "audio" or track.muted:
            continue
        for clip in sorted(track.clips, key=lambda item: item.start_ms):
            path = paths.get((clip.source_kind, clip.source_id or ""))
            if path is None or clip.out_ms <= clip.in_ms:
                continue
            audio_clips.append(
                ExportAudioClip(
                    path=path,
                    start_ms=clip.start_ms,
                    in_ms=clip.in_ms,
                    out_ms=clip.out_ms,
                    gain_db=clip.gain_db,
                    fade_in_ms=clip.fade_in_ms,
                    fade_out_ms=clip.fade_out_ms,
                )
            )

    subtitles_path: str | None = None
    events = _subtitle_events(detail)
    if events and work_dir is not None:
        work_dir.mkdir(parents=True, exist_ok=True)
        target = work_dir / SUBTITLES_FILENAME
        target.write_text(
            subtitles.build_ass(events, width, height), encoding="utf-8"
        )
        subtitles_path = str(target)

    fit = str(params.get("fit") or FIT_PAD)
    return ExportSpec(
        width=width,
        height=height,
        fps=float(params.get("fps") or detail.fps),
        clips=export_clips,
        audio_clips=audio_clips,
        subtitles_path=subtitles_path,
        fit=fit if fit in (FIT_PAD, FIT_CROP) else FIT_PAD,
        loudnorm=bool(params.get("loudnorm", False)),
    )


async def start_export(
    timeline_id: str, params: dict[str, Any], *, base_url: str = ""
) -> TimelineExport:
    """書き出しを 1 本受け付ける（実行はバックグラウンド）。

    同じタイムラインで走っているものがあれば :class:`TimelineConflict`
    （同時に 2 本焼いても得はなく、進捗の見せ方も破綻するため）。

    ``base_url`` は演出付き（``fx: true``）のときだけ使う「レンダラーから見た
    このアプリ」の URL（:func:`local_base_url`）。ルーターが待受のポートから
    組み立てて渡す。
    """
    detail = await timeline_detail(timeline_id)
    if detail is None:
        raise TimelineNotFound("timeline not found")
    # メディア欠落のまま焼くと、その区間が黙って黒＋無音になる。受け付ける前に
    # 断って、何が足りないのかを画面へ返す（直し方は GET .../missing）。
    broken = [
        clip
        for track in detail.tracks
        for clip in track.clips
        if clip.missing
    ]
    if broken:
        names = "、".join(
            (clip.label or clip.id)[:30] for clip in broken[:3]
        )
        more = f" ほか {len(broken) - 3} 件" if len(broken) > 3 else ""
        raise TimelineError(
            f"メディアが見つからないクリップが {len(broken)} 件あります"
            f"（{names}{more}）。差し替えるか削除してから書き出してください"
        )

    # 演出付き（fx: true）は Remotion のレンダリングを続けて投入するので、
    # 連携が OFF のまま受け付けない（Remotion のライセンス、SPEC §5.2）。
    if params.get("fx") and not load_settings().remotion_enabled:
        raise TimelineError(
            "Remotion 連携が無効です（設定で有効にすると演出付きで書き出せます）"
        )

    async with get_db() as conn:
        if await _fetch_timeline(conn, timeline_id) is None:
            raise TimelineNotFound("timeline not found")
        placeholders = ", ".join("?" * len(RUNNING_STATUSES))
        async with conn.execute(
            "SELECT id FROM timeline_exports WHERE timeline_id = ?"
            f" AND status IN ({placeholders})",
            (timeline_id, *RUNNING_STATUSES),
        ) as cur:
            if await cur.fetchone() is not None:
                raise TimelineConflict("このタイムラインはいま書き出し中です")
        export_id = new_id()
        clean = {
            key: value
            for key, value in params.items()
            if key in ("width", "height", "fps", "preset", "fit", "loudnorm", "fx")
            and value is not None
        }
        if clean.get("fx"):
            clean["fx_base_url"] = base_url or local_base_url(None)
        await conn.execute(
            "INSERT INTO timeline_exports"
            " (id, timeline_id, status, progress, params, created_at)"
            " VALUES (?, ?, 'queued', 0, ?, ?)",
            (export_id, timeline_id, json.dumps(clean, ensure_ascii=False), _now()),
        )
        await conn.commit()

    asyncio.create_task(_run(export_id, timeline_id, clean))
    export = await get_export(export_id)
    assert export is not None
    return export


async def _publish(export: TimelineExport) -> None:
    await ws.publish_timeline_export(
        export.id,
        export.timeline_id,
        export.status,
        progress=export.progress,
        output_url=export.output_url,
        error=export.error,
    )


async def _run(export_id: str, timeline_id: str, params: dict[str, Any]) -> None:
    """1 本ぶんの書き出しを最後まで走らせる（例外は status='failed' に落とす）。"""
    await _update_export(export_id, status="running", progress=0.0)
    running = await get_export(export_id)
    if running is not None:
        await _publish(running)

    async def on_progress(value: float) -> None:
        await _update_export(export_id, progress=value)
        await ws.publish_timeline_export(
            export_id, timeline_id, "running", progress=value
        )

    try:
        work_dir = export_dir(export_id)
        spec = await build_spec(timeline_id, params, work_dir)
        output = work_dir / EXPORT_FILENAME
        await run_export(spec, output, on_progress=on_progress)
    except (TimelineError, TimelineExportError) as exc:
        await _update_export(
            export_id, status="failed", error=str(exc), finished_at=_now()
        )
    except Exception as exc:  # noqa: BLE001 - 予期しない失敗も記録して終える
        log.exception("タイムラインの書き出しが落ちました: %s", export_id)
        await _update_export(
            export_id, status="failed", error=str(exc), finished_at=_now()
        )
    else:
        # 焼き上がりの検算: 総フレーム数が計画（round(全長 * fps)）と合うか。
        # 素材が足りずに末尾静止で埋めたところも警告として残す。
        planned = spec.total_frames
        frames = await probe_frames(output)
        warnings = pad_warnings(spec)
        mismatch = frame_warning(planned, frames)
        if mismatch:
            log.warning("書き出し %s: %s", export_id, mismatch)
            warnings.append(mismatch)
        for note in warnings:
            log.info("書き出し %s: %s", export_id, note)
        total = frames if frames is not None else planned
        await _update_export(
            export_id,
            status="done",
            progress=1.0,
            output_path=str(output),
            error=None,
            fps=spec.fps,
            width=spec.width,
            height=spec.height,
            frames=total,
            duration_ms=int(round(total / spec.fps * 1000)) if spec.fps > 0 else 0,
            warnings=json.dumps(warnings, ensure_ascii=False),
            finished_at=_now(),
        )
    finished = await get_export(export_id)
    if finished is not None:
        await _publish(finished)
        # 演出付き（fx: true）は、焼き上がった mp4 を下地に FxOverlay の
        # Remotion ジョブを続けて投入する（失敗しても mp4 は残す）。
        if finished.status == "done" and params.get("fx"):
            try:
                await start_fx_render(export_id)
            except Exception as exc:  # noqa: BLE001 - 演出は落ちても mp4 は残す
                log.exception("演出付き書き出しを投入できませんでした: %s", export_id)
                await _update_export(
                    export_id, fx_status="failed", error=f"演出の投入に失敗: {exc}"
                )


# --------------------------------------------------------------------------
# 演出付き書き出し（ffmpeg の mp4 を下地に FxOverlay を載せる。SPEC §7.3）
# --------------------------------------------------------------------------

#: 演出を載せる composition（``remotion/src/Root.tsx``）
FX_COMPOSITION = "FxOverlay"

#: 演出ジョブの終わりを見張る間隔（秒）
FX_POLL_SECONDS = 3.0


def local_base_url(port: int | None) -> str:
    """自分自身（このアプリ）を指す配信 URL の頭。

    Remotion の ``OffthreadVideo`` / ``Audio`` は **http(s) の URL しか読めない**
    （``file://`` は「Can only download URLs starting with http://」で落ちる）。
    レンダリングは同じ機械で走るので、素材は**自分の静的配信**（``/outputs`` /
    ``/library`` / ``/assets``）を通して渡す。宛先は待受のポートから組み立てる
    （ホスト名は経路によって変わるが、レンダラーから見た自分は常にループバック）。
    """
    return f"http://127.0.0.1:{port or 8000}"


def _serve_url(path: str | None, base_url: str) -> str | None:
    """置き場の中のファイルを、Remotion が読める配信 URL にする（外なら None）。"""
    relative = _media_url(path)
    if relative is None:
        return None
    return f"{base_url.rstrip('/')}{relative}"


async def fx_props(timeline_id: str, export: TimelineExport) -> dict[str, Any]:
    """``FxOverlay`` に渡す props を組み立てる。

    下地（``base``）は焼き上がった mp4、音（``audio``）は A1 の最初の音声
    クリップ、規格（``fps`` / ``width`` / ``height`` / ``durationInSeconds``）は
    その書き出しの実測値（無ければタイムラインの規格）。イベントは FX トラック
    の ``enabled`` なものだけを並び順に並べる。
    """
    detail = await timeline_detail(timeline_id)
    if detail is None:
        raise TimelineNotFound("timeline not found")
    fx = await get_fx(timeline_id)
    if fx is None:
        raise TimelineNotFound("timeline not found")

    props: dict[str, Any] = {
        "fps": float(export.fps or detail.fps),
        "width": int(export.width or detail.width),
        "height": int(export.height or detail.height),
        "events": [item.event for item in fx.events if item.enabled],
    }
    seconds = (export.duration_ms or detail.duration_ms or 0) / 1000
    if seconds > 0:
        props["durationInSeconds"] = round(seconds, 3)
    for key in FX_SETTING_KEYS:
        value = getattr(fx, key)
        if value is not None:
            props[key] = value

    base_url = str(export.params.get("fx_base_url") or local_base_url(None))
    base = _serve_url(export.output_path, base_url)
    if base is None:
        raise TimelineError("書き出した mp4 が見つかりません")
    # 下地の音は鳴らさない（音は audio で別に足す。FxOverlay の既定と同じ）。
    props["base"] = {"src": base, "fit": "fill", "muted": True}

    # A1（最初の音声トラック）の最初のクリップを音として渡す（無ければ省略）。
    paths = await _resolve_clip_paths(detail)
    for track in detail.tracks:
        if track.kind != "audio" or track.muted:
            continue
        clips = sorted(track.clips, key=lambda clip: clip.start_ms)
        if not clips:
            continue
        src = _serve_url(
            paths.get((clips[0].source_kind, clips[0].source_id or "")), base_url
        )
        if src is None:
            continue
        audio: dict[str, Any] = {"src": src}
        if clips[0].in_ms:
            audio["startFrom"] = round(clips[0].in_ms / 1000, 3)
        props["audio"] = audio
        break
    return props


async def start_fx_render(export_id: str) -> TimelineExport:
    """焼き上がった書き出しに、FX トラックの演出を載せるジョブを投入する。

    ジョブは ``mode: "remotion"`` の 1 本（履歴・WS・ライブラリからは他の生成
    ジョブと区別が付かない）。ここは投入して見張り役を立てるだけで、成果物の
    mp4 は ``outputs/{job_id}/video.mp4`` に落ちる。
    """
    export = await get_export(export_id)
    if export is None:
        raise TimelineNotFound("export not found")
    if export.status != "done":
        raise TimelineError("まだ書き出しが終わっていません")
    if not load_settings().remotion_enabled:
        raise TimelineError("Remotion 連携が無効です")
    props = await fx_props(export.timeline_id, export)
    job = await job_service.create_job(
        JobCreate(
            mode="remotion",
            remotion_composition=FX_COMPOSITION,
            remotion_props=props,
            user_input=f"タイムラインの演出付き書き出し（{export.timeline_id}）",
        )
    )
    await _update_export(export_id, fx_job_id=job.id, fx_status="queued")
    asyncio.create_task(_watch_fx_job(export_id, job.id))
    fresh = await get_export(export_id)
    assert fresh is not None
    await _publish(fresh)
    return fresh


async def _watch_fx_job(export_id: str, job_id: str) -> None:
    """演出ジョブの終わりを見張って、書き出しの行へ結果を書き戻す。

    ジョブの進捗そのものは既存の WS（``type: "job"``）に流れているので、ここは
    終端だけを拾う（取りこぼしても ``GET .../exports`` を引き直せば追いつく）。
    """
    while True:
        await asyncio.sleep(FX_POLL_SECONDS)
        job = await job_service.get_job(job_id, include_workflow=False)
        if job is None:
            await _update_export(export_id, fx_status="failed")
            break
        if job.status in ("queued", "running"):
            if job.status == "running":
                await _update_export(export_id, fx_status="running")
            continue
        if job.status == "done" and job.video_path:
            await _update_export(
                export_id, fx_status="done", fx_video_path=job.video_path
            )
        else:
            await _update_export(export_id, fx_status="failed")
        break
    export = await get_export(export_id)
    if export is not None:
        await _publish(export)


async def save_export_to_library(export_id: str, name: str = "") -> LibraryItem:
    """完成した mp4 をライブラリ（``library/video/``）に登録する。

    ファイルは書き出し先（``outputs/exports/…``）に残したまま**コピー**する
    （ジョブの成果物をライブラリへ入れるときと同じ流儀。SPEC §7.2）。
    """
    export = await get_export(export_id)
    if export is None:
        raise TimelineNotFound("export not found")
    if export.status != "done" or not export.output_path:
        raise TimelineError("まだ書き出しが終わっていません")
    source = rebase_stored_path(export.output_path)
    if not source.is_file():
        raise TimelineError(f"書き出したファイルが見つかりません: {source}")

    timeline = await get_timeline(export.timeline_id)
    display = (name or "").strip() or (
        f"{timeline.name} の書き出し" if timeline else "タイムラインの書き出し"
    )
    try:
        return await library_service.add_from_file(source, "video", f"{display}.mp4")
    except library_service.LibraryError as exc:
        raise TimelineError(str(exc)) from exc


# --------------------------------------------------------------------------
# トラックの出し入れ（音声 A1… と字幕 T1）
# --------------------------------------------------------------------------
#
# 映像トラックは V1 の 1 本きり（並べ替えの正本）。音声は何本でも足せて、
# 字幕は 1 本あれば足りる。名前は種別ごとの連番を既定にする。

#: 種別ごとの名前の頭文字（``A2`` / ``T1``）
TRACK_PREFIX = {"video": "V", "audio": "A", "subtitle": "T"}


def _next_track_name(kind: str, existing: list[str]) -> str:
    """その種別でまだ使っていない連番の名前（``A1`` / ``A2`` …）。"""
    prefix = TRACK_PREFIX.get(kind, "X")
    taken = set(existing)
    for number in range(1, 100):
        name = f"{prefix}{number}"
        if name not in taken:
            return name
    return f"{prefix}{len(taken) + 1}"


async def _ensure_track(
    conn: aiosqlite.Connection,
    timeline: StudioTimeline,
    kind: str,
    *,
    name: str = "",
) -> str:
    """その種別のトラックを 1 本用意して id を返す（あれば先頭のものを使う）。"""
    async with conn.execute(
        "SELECT id, name, kind, sort_order FROM timeline_tracks"
        " WHERE timeline_id = ? ORDER BY sort_order, id",
        (timeline.id,),
    ) as cur:
        rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        if row["kind"] == kind:
            return str(row["id"])
    track_id = new_id()
    await conn.execute(
        "INSERT INTO timeline_tracks"
        " (id, timeline_id, project_id, kind, name, sort_order, muted, locked)"
        " VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        (
            track_id,
            timeline.id,
            timeline.project_id,
            kind,
            (name or "").strip()
            or _next_track_name(kind, [str(row["name"]) for row in rows]),
            max((int(row["sort_order"]) for row in rows), default=-1) + 1,
        ),
    )
    return track_id


async def add_track(
    timeline_id: str, payload: TimelineTrackCreate
) -> StudioTimelineDetail:
    """トラックを 1 本足す（音声 A1… / 字幕 T1）。

    映像トラックは足せない: V1 が並べ替えの正本で、2 本目があると「どちらが
    タイムラインの本体か」が決まらなくなるため（合成はスコープ外）。
    """
    if payload.kind == "video":
        raise TimelineError("映像トラックは V1 の 1 本だけです")
    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            raise TimelineNotFound("timeline not found")
        async with conn.execute(
            "SELECT name, sort_order FROM timeline_tracks WHERE timeline_id = ?",
            (timeline_id,),
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
        await conn.execute(
            "INSERT INTO timeline_tracks"
            " (id, timeline_id, project_id, kind, name, sort_order, muted, locked)"
            " VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
            (
                new_id(),
                timeline_id,
                timeline.project_id,
                payload.kind,
                (payload.name or "").strip()
                or _next_track_name(
                    payload.kind, [str(row["name"]) for row in rows]
                ),
                max((int(row["sort_order"]) for row in rows), default=-1) + 1,
            ),
        )
        await conn.execute(
            "UPDATE studio_timelines SET updated_at = ? WHERE id = ?",
            (_now(), timeline_id),
        )
        await conn.commit()
    await _publish_timeline(timeline.project_id, timeline_id)
    detail = await timeline_detail(timeline_id)
    assert detail is not None
    return detail


async def update_track(
    timeline_id: str, track_id: str, payload: TimelineTrackUpdate
) -> StudioTimelineDetail:
    """名前・ミュート・ロックを変える（送らなかった項目はそのまま）。"""
    fields: dict[str, Any] = {}
    if payload.name is not None:
        fields["name"] = payload.name.strip()
    if payload.muted is not None:
        fields["muted"] = int(payload.muted)
    if payload.locked is not None:
        fields["locked"] = int(payload.locked)
    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            raise TimelineNotFound("timeline not found")
        async with conn.execute(
            "SELECT id FROM timeline_tracks WHERE id = ? AND timeline_id = ?",
            (track_id, timeline_id),
        ) as cur:
            if await cur.fetchone() is None:
                raise TimelineNotFound("track not found")
        if fields:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            await conn.execute(
                f"UPDATE timeline_tracks SET {assignments} WHERE id = ?",
                (*fields.values(), track_id),
            )
            await conn.execute(
                "UPDATE studio_timelines SET updated_at = ? WHERE id = ?",
                (_now(), timeline_id),
            )
            await conn.commit()
            await _publish_timeline(timeline.project_id, timeline_id)
    detail = await timeline_detail(timeline_id)
    assert detail is not None
    return detail


async def delete_track(timeline_id: str, track_id: str) -> StudioTimelineDetail:
    """トラックを 1 本消す（載っていたクリップも一緒に消える）。"""
    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            raise TimelineNotFound("timeline not found")
        async with conn.execute(
            "SELECT kind FROM timeline_tracks WHERE id = ? AND timeline_id = ?",
            (track_id, timeline_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise TimelineNotFound("track not found")
        if row["kind"] == "video":
            raise TimelineError("映像トラック（V1）は消せません")
        await conn.execute("DELETE FROM timeline_clips WHERE track_id = ?", (track_id,))
        await conn.execute("DELETE FROM timeline_tracks WHERE id = ?", (track_id,))
        await conn.execute(
            "UPDATE studio_timelines SET updated_at = ? WHERE id = ?",
            (_now(), timeline_id),
        )
        await conn.commit()
    await _publish_timeline(timeline.project_id, timeline_id)
    detail = await timeline_detail(timeline_id)
    assert detail is not None
    return detail


# --------------------------------------------------------------------------
# 素材ビン（タイムラインへ足せるもの）
# --------------------------------------------------------------------------
#
# 制作タブのテイクだけでなく、ライブラリ・単発ジョブ・作品の素材ファイルからも
# 引いてこられるようにする面。1 ページぶんだけ ffprobe を掛けるので、棚が
# 大きくても一覧が重くならない。

#: 出どころごとに拾う上限（これを超えるぶんは新しい方から切る）
MEDIA_SCAN_LIMIT = 500


async def _media_from_takes(
    conn: aiosqlite.Connection, project_id: str
) -> list[TimelineMediaItem]:
    """この作品のテイク（動画が実在するものだけ）。"""
    async with conn.execute(
        "SELECT t.id AS take_id, t.created_at AS created_at, j.video_path AS path,"
        "       s.title AS shot_title, s.sort_order AS shot_order,"
        "       sc.title AS scene_title, ep.title AS episode_title"
        "  FROM studio_takes t"
        "  JOIN jobs j ON j.id = t.job_id"
        "  LEFT JOIN studio_shots s ON s.id = t.shot_id"
        "  LEFT JOIN studio_scenes sc ON sc.id = s.scene_id"
        "  LEFT JOIN studio_episodes ep ON ep.id = sc.episode_id"
        " WHERE t.project_id = ? AND j.video_path IS NOT NULL"
        " ORDER BY t.created_at DESC, t.id DESC LIMIT ?",
        (project_id, MEDIA_SCAN_LIMIT),
    ) as cur:
        rows = await cur.fetchall()
    items: list[TimelineMediaItem] = []
    for row in rows:
        parts = [
            part
            for part in (row["episode_title"], row["scene_title"])
            if part
        ]
        if row["shot_order"] is not None:
            parts.append(
                f"#{int(row['shot_order']) + 1} {row['shot_title'] or ''}".strip()
            )
        items.append(
            TimelineMediaItem(
                source_kind="take",
                source_id=str(row["take_id"]),
                media_kind="video",
                name=" / ".join(parts) or str(row["take_id"])[:8],
                origin="テイク",
                url=_media_url(row["path"]),
                created_at=str(row["created_at"] or ""),
            )
        )
    return items


async def _media_from_library(
    conn: aiosqlite.Connection, kind: str
) -> list[TimelineMediaItem]:
    async with conn.execute(
        "SELECT id, name, path, created_at FROM library WHERE kind = ?"
        " ORDER BY created_at DESC, id DESC LIMIT ?",
        (kind, MEDIA_SCAN_LIMIT),
    ) as cur:
        rows = await cur.fetchall()
    return [
        TimelineMediaItem(
            source_kind="image" if kind == "image" else "library",
            source_id=(
                f"library:{row['id']}" if kind == "image" else str(row["id"])
            ),
            media_kind=kind,  # type: ignore[arg-type]
            name=str(row["name"] or row["id"]),
            origin="ライブラリ",
            url=_media_url(row["path"]),
            created_at=str(row["created_at"] or ""),
        )
        for row in rows
    ]


#: 素材ビンに出すジョブの出力（種別 -> 見に行く列）
_JOB_MEDIA_COLUMNS = {
    "video": "video_path",
    "audio": "audio_output_path",
    "image": "image_path",
}


async def _media_from_jobs(
    conn: aiosqlite.Connection, kind: str
) -> list[TimelineMediaItem]:
    """終わった**単発**ジョブの出力。

    テイクの裏にあるジョブは外す（同じ動画がテイクとして既に並んでいるので、
    2 つ出ると「どちらを置いたか」が分からなくなる）。
    """
    column = _JOB_MEDIA_COLUMNS[kind]
    async with conn.execute(
        f"SELECT id, {column} AS path, user_input, created_at FROM jobs"
        f" WHERE status = 'done' AND {column} IS NOT NULL AND {column} <> ''"
        "   AND id NOT IN (SELECT job_id FROM studio_takes)"
        " ORDER BY created_at DESC, id DESC LIMIT ?",
        (MEDIA_SCAN_LIMIT,),
    ) as cur:
        rows = await cur.fetchall()
    items: list[TimelineMediaItem] = []
    for row in rows:
        title = (row["user_input"] or "").strip().splitlines()[:1]
        items.append(
            TimelineMediaItem(
                source_kind="image" if kind == "image" else "job",
                source_id=f"job:{row['id']}" if kind == "image" else str(row["id"]),
                media_kind=kind,  # type: ignore[arg-type]
                name=(title[0][:60] if title else str(row["id"])[:8]),
                origin="ジョブ",
                url=_media_url(row["path"]),
                created_at=str(row["created_at"] or ""),
            )
        )
    return items


#: 素材ビンに出す素材ファイル（種別 -> ``studio_asset_files.role``）
_ASSET_ROLES = {"audio": "voice", "image": "image", "video": "video"}


async def _media_from_asset_files(
    conn: aiosqlite.Connection, project_id: str, kind: str
) -> list[TimelineMediaItem]:
    async with conn.execute(
        "SELECT f.id AS id, f.path AS path, f.caption AS caption,"
        "       f.created_at AS created_at, a.name AS asset_name"
        "  FROM studio_asset_files f"
        "  LEFT JOIN studio_assets a ON a.id = f.asset_id"
        " WHERE f.project_id = ? AND f.role = ?"
        " ORDER BY f.created_at DESC, f.id DESC LIMIT ?",
        (project_id, _ASSET_ROLES[kind], MEDIA_SCAN_LIMIT),
    ) as cur:
        rows = await cur.fetchall()
    return [
        TimelineMediaItem(
            source_kind="image" if kind == "image" else "asset_file",
            source_id=(
                f"asset_file:{row['id']}" if kind == "image" else str(row["id"])
            ),
            media_kind=kind,  # type: ignore[arg-type]
            name=" / ".join(
                part for part in (row["asset_name"], row["caption"]) if part
            )
            or str(row["id"])[:8],
            origin="素材",
            url=_media_url(row["path"]),
            created_at=str(row["created_at"] or ""),
        )
        for row in rows
    ]


async def list_media(
    project_id: str, kind: str, limit: int = 50, offset: int = 0
) -> TimelineMediaPage:
    """素材ビンの 1 ページ（``kind`` は video / audio / image）。

    出どころ（テイク・ライブラリ・ジョブ・素材ファイル）をそれぞれ新しい順に
    :data:`MEDIA_SCAN_LIMIT` 件まで拾ってから、作成時刻で 1 本に混ぜる。実ファイルが
    無いものは落とす（置いた瞬間に欠落になる素材を並べない）。長さの下調べは
    **返すページのぶんだけ**まとめて行う。
    """
    if kind not in ("video", "audio", "image"):
        raise TimelineError(f"知らない素材の種別です: {kind}")
    async with get_db() as conn:
        async with conn.execute(
            "SELECT id FROM studio_projects WHERE id = ?", (project_id,)
        ) as cur:
            if await cur.fetchone() is None:
                raise TimelineNotFound("project not found")
        items: list[TimelineMediaItem] = []
        if kind == "video":
            items += await _media_from_takes(conn, project_id)
        items += await _media_from_library(conn, kind)
        items += await _media_from_jobs(conn, kind)
        items += await _media_from_asset_files(conn, project_id, kind)

    # 配信 URL を作れなかった（= 置き場の外・ファイルが無い）ものは並べない。
    items = [item for item in items if item.url]
    items.sort(key=lambda item: (item.created_at, item.source_id), reverse=True)
    total = len(items)
    page = items[offset : offset + limit]

    if kind != "image":
        async with get_db() as conn:
            refs = {
                (item.source_kind, item.source_id, "audio" if kind == "audio" else "video")
                for item in page
            }
            sources = await _resolve_sources(conn, refs)
        wanted: dict[str, str] = {}
        for key, info in sources.items():
            path = info.get("path")
            resolved = rebase_stored_path(path) if path else None
            if resolved is not None and resolved.is_file():
                wanted[f"{key[0]}:{key[1]}"] = str(resolved)
        probed = await probe_many(list(wanted.values()))
        for item in page:
            path = wanted.get(f"{item.source_kind}:{item.source_id}")
            if path:
                item.duration_ms = probed.get(path, (None, False))[0]

    return TimelineMediaPage(items=page, total=total, limit=limit, offset=offset)


# --------------------------------------------------------------------------
# クリップの並べ直し（サーバー側で編集するときの下敷き）
# --------------------------------------------------------------------------

def to_clip_input(clip: TimelineClip) -> TimelineClipInput:
    """読み取ったクリップを、書き戻せる形（``PUT /clips`` の 1 件）にする。"""
    return TimelineClipInput(
        id=clip.id,
        track_id=clip.track_id,
        start_ms=clip.start_ms,
        duration_ms=clip.duration_ms,
        source_kind=clip.source_kind,
        source_id=clip.source_id,
        in_ms=clip.in_ms,
        out_ms=clip.out_ms,
        gain_db=clip.gain_db,
        fade_in_ms=clip.fade_in_ms,
        fade_out_ms=clip.fade_out_ms,
        transition_kind=clip.transition_kind,
        transition_ms=clip.transition_ms,
        text_payload=clip.text_payload,
        speed=clip.speed,
    )


def all_clip_inputs(detail: StudioTimelineDetail) -> list[TimelineClipInput]:
    """タイムラインの全クリップを書き戻せる形で（トラックの順のまま）。"""
    return [
        to_clip_input(clip) for track in detail.tracks for clip in track.clips
    ]


def relayout(clips: list[TimelineClipInput]) -> list[TimelineClipInput]:
    """映像トラックの並びを、繋ぎの重なりを含めて先頭から詰め直す（純関数）。

    渡された順番がそのままタイムライン上の順番（リップル方式）。繋ぎを持つ
    クリップは前へ ``transition_ms`` だけ食い込み、長すぎる繋ぎは隣り合う 2 つの
    短いほうの 1/2 へ丸める。先頭のクリップの繋ぎは落とす（重なる相手が居ない）。
    """
    placed: list[TimelineClipInput] = []
    cursor = 0
    for index, clip in enumerate(clips):
        current = clip.model_copy()
        overlap = 0 if index == 0 else _overlap_ms(current)
        if overlap:
            # 隣り合う 2 つの短いほうの 1/2 を超えないところまで丸める。
            # 最小を割ってしまうなら、繋ぎ自体をあきらめてカットにする。
            shortest = min(placed[-1].duration_ms, current.duration_ms)
            overlap = min(overlap, shortest // 2, TRANSITION_MAX_MS)
            if overlap < TRANSITION_MIN_MS:
                overlap = 0
        if overlap:
            current.transition_ms = overlap
        else:
            current.transition_kind = None
            current.transition_ms = 0
        current.start_ms = max(0, cursor - overlap)
        placed.append(current)
        cursor = current.start_ms + current.duration_ms
    return placed


# --------------------------------------------------------------------------
# 音源基準の配置（計画開始秒）
# --------------------------------------------------------------------------
#
# 通常のドラマ制作では使わない（並び順で足りる）。MV のように**音源の秒が正本**
# の制作でだけ、カット（``studio_shots.planned_start_seconds``）に音源上の開始秒
# を書いておくと、sync がその位置へカットを置く。


class PlannedClip(NamedTuple):
    """:func:`plan_layout` に渡す 1 件（クリップと、その元カットの計画）。"""

    clip: TimelineClipInput
    #: 音源上の計画開始秒（None = 計画を持たない＝並び順で置く）
    planned_start_seconds: float | None = None
    #: ソース（Take）の実尺（ミリ秒。0 = いまの切り出しの長さをそのまま使う）
    source_ms: int = 0


def plan_layout(
    items: list[PlannedClip],
    track_id: str,
    fps: float = DEFAULT_FPS,
    *,
    gap_fill: str = "clone",
    end_ms: int | None = None,
) -> list[TimelineClipInput]:
    """計画秒つきのカットを音源上の位置へ置く（純関数）。

    - 計画秒を持つクリップは ``start_ms = round(計画秒 * 1000)`` に置く。尺は
      **次の計画秒までの間隔**（最後のカットは ``end_ms``、無ければ Take の尺）
      が上限
    - 素材（Take）が計画尺に届かないぶんの埋め方は ``gap_fill``:

      - ``"clone"``（既定）… クリップの尺を**計画尺のまま**にして、足りないぶんは
        書き出しの ``tpad``（末尾静止）に埋めさせる。書き出しの ``warnings`` に
        ``PAD …`` が出る（:func:`app.timeline_export.pad_warnings`）。MV では
        黒コマが事故になるのでこちらが既定
      - ``"black"`` … 足りないぶんを ``gap`` クリップ（黒＋無音）で埋める（従来）

    - 先頭の計画秒までの空きは、埋める材料が無いのでどちらでも ``gap``。
      ただし **1 フレームに満たない隙間は作らない**（前のクリップへ寄せる）
      ——書き出しで 0 フレームのセグメントになって ffmpeg が落ちるため
      （``fps`` はそのタイムラインの規格）
    - ``end_ms`` を渡すと**最後の計画クリップをそこで締める**（音源の尺。
      渡さなければ今までどおり Take の尺いっぱい）
    - 計画秒を持たないクリップは、計画の終わったところから従来どおり順に詰める
      （繋ぎの重なりの扱いも :func:`relayout` と同じ）
    - 計画を 1 つも持たないときは :func:`relayout` そのもの

    音源基準では繋ぎ（トランジション）を持てない（重なるとその先の位置が全部
    ずれる）ので、計画秒つきのクリップからは落とす。
    """
    planned = sorted(
        (item for item in items if item.planned_start_seconds is not None),
        key=lambda item: float(item.planned_start_seconds or 0.0),
    )
    if not planned:
        return relayout([item.clip for item in items])

    starts = [
        max(0, int(round(float(item.planned_start_seconds or 0.0) * 1000)))
        for item in planned
    ]
    # 1 フレームの長さ（これに満たない隙間は作らない）。
    frame_ms = 1000 / float(fps) if fps and fps > 0 else 1000 / DEFAULT_FPS
    placed: list[TimelineClipInput] = []
    cursor = 0
    for index, item in enumerate(planned):
        start = starts[index]
        clip = item.clip.model_copy()
        available = item.source_ms or max(0, clip.out_ms - clip.in_ms)
        if available <= 0:
            available = FALLBACK_CLIP_MS
        # 次の計画秒までが上限（最後のカットは end_ms、無ければ Take の尺）。
        if index + 1 < len(planned):
            limit = starts[index + 1] - start
        elif end_ms is not None and end_ms - start >= MIN_CLIP_MS:
            limit = end_ms - start
        else:
            limit = available
        # クローン埋めでは計画尺をそのままクリップの尺にする（足りないぶんは
        # 書き出しが末尾静止で埋め、PAD 警告になる）。黒埋めでは Take の尺で
        # 切って、余ったところを下の gap が埋める。
        span = limit if gap_fill == "clone" else min(limit, available)
        if span < MIN_CLIP_MS:
            raise TimelineError(
                f"計画開始秒が詰まりすぎています（{start / 1000:g} 秒のカットに"
                f" {span}ms しか置けません）"
            )
        hole = start - cursor
        if hole > 0 and placed and (hole < frame_ms or gap_fill == "clone"):
            # 1 フレームに満たない隙間は置かない（gap を挟むと書き出しで
            # 0 フレームのセグメントになる）。クローン埋めのときは隙間そのものを
            # 前のクリップの末尾静止で埋める。
            placed[-1].duration_ms += hole
            placed[-1].out_ms += hole
        elif hole > 0 and hole < frame_ms:
            start = cursor  # 先頭の端数はそのまま頭へ寄せる
        elif hole > 0:
            # 先頭の空きは伸ばす材料が無いので、どちらの埋め方でも黒になる。
            placed.append(
                TimelineClipInput(
                    track_id=track_id,
                    start_ms=cursor,
                    duration_ms=hole,
                    source_kind="gap",
                    source_id=None,
                    in_ms=0,
                    out_ms=hole,
                )
            )
        clip.start_ms = start
        clip.duration_ms = span
        clip.speed = 1.0
        clip.transition_kind = None
        clip.transition_ms = 0
        clip.out_ms = clip.in_ms + span
        placed.append(clip)
        cursor = start + span

    # 計画を持たないカットは、計画の終わったところから今までどおり詰める。
    rest = relayout(
        [item.clip for item in items if item.planned_start_seconds is None]
    )
    for clip in rest:
        clip.start_ms += cursor
    return [*placed, *rest]


def quantize_ms(value: int, fps: float) -> int:
    """ミリ秒を**フレーム境界**へ丸める（純関数）。

    書き出しは切り出し位置もフレーム番号（``round(t * fps)``）で扱うので、
    境界から外れた ``in_ms`` を持つクリップは 1 フレーム短く（長く）焼ける。
    分割で作った ``in_ms`` はここを通してから保存する。
    """
    if fps <= 0:
        return max(0, int(value))
    frame = round(max(0, int(value)) * fps / 1000)
    return int(round(frame * 1000 / fps))


def insert_into(
    clips: list[TimelineClipInput],
    inserted: TimelineClipInput,
    fps: float = DEFAULT_FPS,
) -> list[TimelineClipInput]:
    """``inserted`` の区間に重なる既存クリップを前後に分割して差し込む（純関数）。

    下のクリップの**切り出しは動かさない**（後半は ``in_ms`` をずらして続きから
    再生される）ので、トラック全体の長さは変わらない。:data:`MIN_CLIP_MS` に
    満たない切れ端は残せないので落とす（そのぶんは隙間になる）。

    分割した後半の ``in_ms`` は :func:`quantize_ms` でフレーム境界へ量子化する
    （ミリ秒のまま持つと書き出しで 1 フレーム落ちる）。尺は位置で決まっている
    ので動かさず、``out_ms`` を新しい ``in_ms`` から測り直す。
    """
    start = inserted.start_ms
    end = start + inserted.duration_ms
    kept: list[TimelineClipInput] = []
    for clip in sorted(clips, key=lambda item: item.start_ms):
        finish = clip.start_ms + clip.duration_ms
        if finish <= start or clip.start_ms >= end:
            kept.append(clip.model_copy())
            continue
        speed = float(clip.speed or 1.0)
        spanless = clip.source_kind in _SPANLESS_SOURCES
        head_ms = start - clip.start_ms
        if head_ms >= MIN_CLIP_MS:
            head = clip.model_copy()
            head.duration_ms = head_ms
            if not spanless:
                head.out_ms = clip.in_ms + int(round(head_ms * speed))
            kept.append(head)
        tail_ms = finish - end
        if tail_ms >= MIN_CLIP_MS:
            tail = clip.model_copy()
            # 分割で増えたほうは新しいクリップ（id は書き込みのときに振る）。
            tail.id = None
            tail.start_ms = end
            tail.duration_ms = tail_ms
            if not spanless:
                tail.in_ms = quantize_ms(
                    clip.out_ms - int(round(tail_ms * speed)), fps
                )
                tail.out_ms = tail.in_ms + int(round(tail_ms * speed))
            # 前の境界が差し込んだクリップに変わったので、繋ぎは持ち越さない。
            tail.transition_kind = None
            tail.transition_ms = 0
            kept.append(tail)
    kept.append(inserted.model_copy())
    return sorted(kept, key=lambda item: item.start_ms)


async def _check_edl_revision(
    conn: aiosqlite.Connection,
    timeline_id: str,
    project_id: str,
    name: str,
    base_revision: int | None,
) -> None:
    """``base_revision`` 以降に**このタイムラインの中身**が触られていたら 409。

    :func:`app.studio._check_base_revision` はエンティティ 1 件（``timeline`` の
    行そのもの）しか見ないので、クリップだけが動いた並行編集をすり抜ける。
    ここではリビジョンのスナップショットからこのタイムラインのクリップと
    演出（FX トラック）だけを取り出して突き合わせる（意味は §7.4 の楽観ロックと
    同じ）。

    比べる相手のスナップショットが残っていない・編集タブより前で EDL を持って
    いないときは「変わっていない」と言い切れないので、ぶつかったものとして扱う。
    """
    if base_revision is None:
        return
    current = await studio_service._revision_seq(conn, project_id)
    if base_revision > current:
        raise TimelineError(
            f"base_revision {base_revision} はまだ存在しません"
            f"（現在のリビジョン {current}）"
        )
    if base_revision == current:
        return

    def rows_of(snapshot: dict[str, Any] | None, table: str) -> list[str] | None:
        """スナップショットから、このタイムラインの行だけを比べられる形に。

        値に None が混ざる列（``source_id`` など）があるので、行そのものを
        比べずに JSON 文字列へ落としてから並べる。
        """
        rows = (snapshot or {}).get(table)
        if rows is None:
            return None
        return sorted(
            json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
            for row in rows
            if str(row.get("timeline_id")) == timeline_id
        )

    before = await studio_service._snapshot_of(conn, project_id, base_revision)
    if rows_of(before, "timeline_clips") is None:
        raise TimelineConflict(
            f"リビジョン {base_revision} は履歴に残っていないので"
            f"タイムライン『{name}』の変更を確かめられません"
            f"（現在のリビジョン {current}）"
        )
    after = await studio_service._snapshot(conn, project_id)
    # FX トラックを足す前に取ったスナップショットには演出のキーが無い。
    # 「空だった」と読むと機能を足しただけで全件が衝突に見えるので、
    # 載っていない面は突き合わせない（:func:`app.studio._snapshot_changes` と
    # 同じ考え方）。
    for table in ("timeline_clips", "timeline_fx", "timeline_fx_events"):
        old_rows = rows_of(before, table)
        if old_rows is None:
            continue
        if old_rows != rows_of(after, table):
            raise TimelineConflict(
                f"タイムライン『{name}』は他の変更で更新されています"
                f"（現在のリビジョン {current}）"
            )


async def insert_clip(
    timeline_id: str, payload: TimelineClipInsert, *, actor: str = "user"
) -> StudioTimelineDetail:
    """クリップを 1 つ差し込む（重なる既存クリップは前後に分割される）。

    BAN!BAN!BAN! の「決めポーズを 1.5 秒だけ割り込ませる」ような編集のための
    入り口。トラックの全長は変わらないので、音源基準で組んだ並びが崩れない。
    """
    detail = await timeline_detail(timeline_id)
    if detail is None:
        raise TimelineNotFound("timeline not found")
    tracks = {track.id: track for track in detail.tracks}
    track = tracks.get(payload.track_id)
    if track is None:
        raise TimelineNotFound("track not found")
    if track.locked:
        raise TimelineError("ロックされたトラックには差し込めません")
    if payload.duration_ms <= 0:
        raise TimelineError("差し込むクリップの尺が 0 以下です")
    if payload.start_ms < 0:
        raise TimelineError("差し込む位置が負です")

    inserted = TimelineClipInput(
        track_id=track.id,
        start_ms=payload.start_ms,
        duration_ms=payload.duration_ms,
        source_kind=payload.source_kind,
        source_id=payload.source_id,
        in_ms=payload.in_ms,
        out_ms=payload.in_ms + payload.duration_ms,
        text_payload=payload.text_payload,
    )
    updated = insert_into(
        [to_clip_input(clip) for clip in track.clips],
        inserted,
        float(detail.fps or DEFAULT_FPS),
    )
    others = [
        to_clip_input(clip)
        for other in detail.tracks
        for clip in other.clips
        if other.id != track.id
    ]

    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            raise TimelineNotFound("timeline not found")
        kinds = {item.id: item.kind for item in detail.tracks}
        validate_clips([*updated, *others], kinds)
        # 楽観ロックと履歴はスタジオ側と同じ仕掛けを使う（EDL もリビジョンの
        # スナップショットに載っているので、ここだけ別扱いにしない）。
        await _check_edl_revision(
            conn, timeline_id, timeline.project_id, timeline.name,
            payload.base_revision,
        )
        await _write_clips(conn, timeline_id, timeline.project_id, [*updated, *others])
        await studio_service._record_revision(
            conn,
            timeline.project_id,
            actor,
            f"タイムライン『{timeline.name}』にクリップを差し込み",
            entity_kind="timeline",
            entity_id=timeline_id,
        )
        await studio_service._commit(conn)

    fresh = await timeline_detail(timeline_id)
    assert fresh is not None
    return fresh

def _video_track(detail: StudioTimelineDetail) -> TimelineTrack:
    tracks = [track for track in detail.tracks if track.kind == "video"]
    if not tracks:
        raise TimelineError("映像トラックがありません")
    return tracks[0]


# --------------------------------------------------------------------------
# 台詞からのテロップ生成
# --------------------------------------------------------------------------

async def generate_subtitles(
    timeline_id: str, track_id: str | None = None
) -> StudioTimelineDetail:
    """V1 のクリップの元カットの台詞から、テロップを一括で置き直す。

    クリップ -> Take -> Shot と辿って ``studio_shots.dialogue`` を読み、その
    クリップの区間へ等分に割り付ける（:func:`app.timeline_subtitles.place_dialogue`）。
    **字幕トラックの中身は置き換える**（積み増すと二重に出るため。画面側で
    確認ダイアログを出している）。台詞が 1 つも無ければ何も置かずに返す。
    """
    detail = await timeline_detail(timeline_id)
    if detail is None:
        raise TimelineNotFound("timeline not found")
    video = _video_track(detail)
    take_ids = sorted(
        {
            str(clip.source_id)
            for clip in video.clips
            if clip.source_kind == "take" and clip.source_id
        }
    )

    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        assert timeline is not None
        dialogues: dict[str, str] = {}
        if take_ids:
            placeholders = ", ".join("?" * len(take_ids))
            async with conn.execute(
                "SELECT t.id AS take_id, s.dialogue AS dialogue"
                "  FROM studio_takes t"
                "  LEFT JOIN studio_shots s ON s.id = t.shot_id"
                f" WHERE t.id IN ({placeholders})",
                tuple(take_ids),
            ) as cur:
                dialogues = {
                    str(row["take_id"]): str(row["dialogue"] or "")
                    for row in await cur.fetchall()
                }

        if track_id:
            async with conn.execute(
                "SELECT kind FROM timeline_tracks WHERE id = ? AND timeline_id = ?",
                (track_id, timeline_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise TimelineNotFound("track not found")
            if row["kind"] != "subtitle":
                raise TimelineError("テロップを置けるのは字幕トラックだけです")
            target = track_id
        else:
            target = await _ensure_track(conn, timeline, "subtitle")

        made: list[TimelineClipInput] = []
        cursor = 0
        for clip in sorted(video.clips, key=lambda item: item.start_ms):
            # 繋ぎ（オーバーラップ）があると映像クリップどうしは重なるが、
            # テロップは重ねられない（同じトラックの重なりは 400）。前のカットの
            # テロップが終わったところから、このカットの区間を数える。
            begin = max(clip.start_ms, cursor)
            span = clip.start_ms + clip.duration_ms - begin
            cursor = clip.start_ms + clip.duration_ms
            if span <= 0:
                continue
            dialogue = dialogues.get(str(clip.source_id or ""), "")
            for piece in subtitles.place_dialogue(begin, span, dialogue):
                made.append(
                    TimelineClipInput(
                        track_id=target,
                        start_ms=piece.start_ms,
                        duration_ms=piece.duration_ms,
                        source_kind="text",
                        source_id=None,
                        in_ms=0,
                        out_ms=0,
                        text_payload={"text": piece.text, "style": piece.style},
                    )
                )

        kept = [
            to_clip_input(clip)
            for track in detail.tracks
            for clip in track.clips
            if track.id != target
        ]
        await _write_clips(conn, timeline_id, timeline.project_id, [*kept, *made])
        await conn.commit()
    await _publish_timeline(timeline.project_id, timeline_id)

    fresh = await timeline_detail(timeline_id)
    assert fresh is not None
    return fresh


# --------------------------------------------------------------------------
# 脚本との差分（作ったあとに脚本が動いた分）
# --------------------------------------------------------------------------

async def _shot_state(
    conn: aiosqlite.Connection, take_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Take id -> その Take が属するカットの今の状態。"""
    if not take_ids:
        return {}
    placeholders = ", ".join("?" * len(take_ids))
    async with conn.execute(
        "SELECT t.id AS take_id, t.shot_id AS shot_id,"
        "       s.id AS shot_exists, s.selected_take_id AS selected_take_id,"
        "       s.planned_start_seconds AS planned,"
        "       s.timeline_role AS timeline_role,"
        "       s.title AS shot_title, s.sort_order AS shot_order,"
        "       sc.title AS scene_title, ep.title AS episode_title"
        "  FROM studio_takes t"
        "  LEFT JOIN studio_shots s ON s.id = t.shot_id"
        "  LEFT JOIN studio_scenes sc ON sc.id = s.scene_id"
        "  LEFT JOIN studio_episodes ep ON ep.id = sc.episode_id"
        f" WHERE t.id IN ({placeholders})",
        tuple(take_ids),
    ) as cur:
        return {str(row["take_id"]): dict(row) for row in await cur.fetchall()}


async def sync_preview(timeline_id: str) -> TimelineSyncPreview:
    """タイムラインを作ったあとに脚本で起きた差分を出す。

    3 つだけ見る:

    - **増えたカット** … その話に採用テイクつきのカットが増えた（動画が実在するもの）
    - **採用が変わったカット** … クリップが古いテイクを指している
    - **消えたカット** … 元のカットが消えた / 採用が外れた

    どれも「反映するか」は人が選ぶので、ここでは並べるだけ（:func:`apply_sync`）。
    ``timeline_role`` が ``auto`` でないカット（差し込み専用・使わない）は
    どの欄にも出さない（SPEC §7.3）。
    """
    detail = await timeline_detail(timeline_id)
    if detail is None:
        raise TimelineNotFound("timeline not found")
    video = _video_track(detail)
    take_clips = [
        clip for clip in video.clips if clip.source_kind == "take" and clip.source_id
    ]

    async with get_db() as conn:
        states = await _shot_state(
            conn, sorted({str(clip.source_id) for clip in take_clips})
        )
        covered = {
            str(state["shot_id"])
            for state in states.values()
            if state.get("shot_exists")
        }
        added: list[TimelineSyncAdded] = []
        if detail.episode_id:
            async with conn.execute(
                "SELECT s.id AS shot_id, s.title AS shot_title,"
                "       s.sort_order AS shot_order, s.selected_take_id AS take_id,"
                "       s.planned_start_seconds AS planned,"
                "       sc.title AS scene_title, ep.title AS episode_title,"
                "       j.video_path AS path"
                "  FROM studio_shots s"
                "  JOIN studio_scenes sc ON sc.id = s.scene_id"
                "  LEFT JOIN studio_episodes ep ON ep.id = sc.episode_id"
                "  JOIN studio_takes t ON t.id = s.selected_take_id"
                "  LEFT JOIN jobs j ON j.id = t.job_id"
                " WHERE s.project_id = ? AND sc.episode_id = ?"
                "   AND s.timeline_role = 'auto'"
                " ORDER BY sc.sort_order, sc.created_at, sc.id,"
                "          s.sort_order, s.created_at, s.id",
                (detail.project_id, detail.episode_id),
            ) as cur:
                rows = await cur.fetchall()
            for row in rows:
                if str(row["shot_id"]) in covered or not row["path"]:
                    continue
                resolved = rebase_stored_path(row["path"])
                if not resolved.is_file():
                    continue
                duration_ms, _ = await probe_cached(resolved)
                added.append(
                    TimelineSyncAdded(
                        shot_id=str(row["shot_id"]),
                        take_id=str(row["take_id"]),
                        label=_shot_label(row),
                        duration_ms=duration_ms or FALLBACK_CLIP_MS,
                        planned_start_seconds=(
                            None if row["planned"] is None else float(row["planned"])
                        ),
                    )
                )

        retaken: list[TimelineSyncRetaken] = []
        removed: list[TimelineSyncRemoved] = []
        for clip in take_clips:
            take_id = str(clip.source_id)
            state = states.get(take_id)
            if state is not None and state.get("timeline_role") not in (
                None, "auto",
            ):
                # 差し込み専用（insert_only）・使わない（skip）カットのクリップは
                # 人が置いたものなので、差し替えも削除も勧めない（SPEC §7.3）。
                continue
            if state is None or not state.get("shot_exists"):
                removed.append(
                    TimelineSyncRemoved(
                        clip_id=clip.id,
                        label=clip.label,
                        reason="元のカットが見つかりません",
                    )
                )
                continue
            selected = state.get("selected_take_id")
            if not selected:
                removed.append(
                    TimelineSyncRemoved(
                        clip_id=clip.id,
                        label=clip.label,
                        reason="カットの採用テイクが外れています",
                    )
                )
                continue
            if str(selected) == take_id:
                continue
            duration_ms = await _take_duration(conn, str(selected))
            retaken.append(
                TimelineSyncRetaken(
                    clip_id=clip.id,
                    shot_id=str(state["shot_id"]),
                    old_take_id=take_id,
                    new_take_id=str(selected),
                    label=_shot_label(state),
                    duration_ms=duration_ms,
                    planned_start_seconds=(
                        None
                        if state.get("planned") is None
                        else float(state["planned"])
                    ),
                )
            )

    return TimelineSyncPreview(added=added, retaken=retaken, removed=removed)


def _shot_label(row: Any) -> str:
    """「第 1 話 / 場 1 / #2 カット名」（差分の見出し）。"""
    parts = [
        part
        for part in (row["episode_title"], row["scene_title"])
        if part
    ]
    if row["shot_order"] is not None:
        parts.append(f"#{int(row['shot_order']) + 1} {row['shot_title'] or ''}".strip())
    return " / ".join(parts)


async def _shot_plans(
    conn: aiosqlite.Connection, take_ids: list[str]
) -> dict[str, float | None]:
    """Take id -> その Take の元カットの計画開始秒（持たなければ None）。

    音源基準で組んでいるタイムラインかどうかは、ここが 1 つでも値を返すかで
    決まる（:func:`apply_sync`）。
    """
    if not take_ids:
        return {}
    placeholders = ", ".join("?" * len(take_ids))
    async with conn.execute(
        "SELECT t.id AS take_id, s.planned_start_seconds AS planned"
        "  FROM studio_takes t"
        "  LEFT JOIN studio_shots s ON s.id = t.shot_id"
        f" WHERE t.id IN ({placeholders})",
        tuple(take_ids),
    ) as cur:
        return {
            str(row["take_id"]): (
                None if row["planned"] is None else float(row["planned"])
            )
            for row in await cur.fetchall()
        }


async def _shot_roles(
    conn: aiosqlite.Connection, take_ids: list[str]
) -> dict[str, str]:
    """Take id -> その Take の元カットの ``timeline_role``（既定 ``auto``）。"""
    if not take_ids:
        return {}
    placeholders = ", ".join("?" * len(take_ids))
    async with conn.execute(
        "SELECT t.id AS take_id, s.timeline_role AS role"
        "  FROM studio_takes t"
        "  LEFT JOIN studio_shots s ON s.id = t.shot_id"
        f" WHERE t.id IN ({placeholders})",
        tuple(take_ids),
    ) as cur:
        return {
            str(row["take_id"]): str(row["role"] or "auto")
            for row in await cur.fetchall()
        }


def audio_end_ms(detail: StudioTimelineDetail) -> int | None:
    """A1（最初の音声トラック）の最初のクリップの終わり（無ければ None）。

    音源基準では A1 に曲を 1 本置くので、``planned_end_seconds`` が書かれて
    いないタイムラインではここが「音源の尺」の代わりになる。
    """
    for track in detail.tracks:
        if track.kind != "audio":
            continue
        clips = sorted(track.clips, key=lambda clip: clip.start_ms)
        if not clips:
            return None
        return clips[0].start_ms + clips[0].duration_ms
    return None


def planned_end_ms(
    timeline: StudioTimeline, detail: StudioTimelineDetail
) -> int | None:
    """自動配置の最後のクリップを締める位置（ミリ秒。決まらなければ None）。

    ``planned_end_seconds`` -> A1 の最初の音声クリップ -> None（＝ Take の尺
    いっぱい）の順に決まる（SPEC §7.3）。
    """
    if timeline.planned_end_seconds:
        return int(round(float(timeline.planned_end_seconds) * 1000))
    return audio_end_ms(detail)


async def _take_duration(
    conn: aiosqlite.Connection, take_id: str
) -> int | None:
    """Take の動画の長さ（動画が無ければ None）。"""
    async with conn.execute(
        "SELECT j.video_path AS path FROM studio_takes t"
        "  LEFT JOIN jobs j ON j.id = t.job_id WHERE t.id = ?",
        (take_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None or not row["path"]:
        return None
    resolved = rebase_stored_path(row["path"])
    if not resolved.is_file():
        return None
    duration_ms, _ = await probe_cached(resolved)
    return duration_ms


async def apply_sync(
    timeline_id: str, request: TimelineSyncRequest
) -> StudioTimelineDetail:
    """:func:`sync_preview` の項目のうち、選ばれたものだけ反映する。

    映像トラックは反映のあとで詰め直す（:func:`relayout`）。元カットが
    **計画開始秒**を持っているときは、そちらが正本になって音源上の位置へ置き直す
    （:func:`plan_layout`。差し替えた Take も同じ位置に置き直される。隙間の
    埋め方は ``gap_fill``、最後のクリップは :func:`planned_end_ms` で締める）。
    ``timeline_role`` が ``auto`` でないカットのクリップは動かさず、並べ直した
    あとで元の位置へ差し込み直す。他の
    トラック（BGM・テロップ）は動かさない: 音は尺に合わせて置いてあるので、
    勝手にずらすと合っていたものが外れるため。
    """
    preview = await sync_preview(timeline_id)
    detail = await timeline_detail(timeline_id)
    assert detail is not None
    video = _video_track(detail)

    retakes = {
        item.clip_id: item
        for item in preview.retaken
        if item.clip_id in set(request.retake_clip_ids)
    }
    drops = {
        item.clip_id
        for item in preview.removed
        if item.clip_id in set(request.remove_clip_ids)
    }
    additions = [
        item for item in preview.added if item.shot_id in set(request.add_shot_ids)
    ]

    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            raise TimelineNotFound("timeline not found")

        kept: list[TimelineClipInput] = []
        for clip in sorted(video.clips, key=lambda item: item.start_ms):
            if clip.id in drops:
                continue
            current = to_clip_input(clip)
            retake = retakes.get(clip.id)
            if retake is not None:
                # 新しいテイクは尺が違うかもしれないので、切り出しを丸める。
                limit = retake.duration_ms or current.out_ms
                current.source_id = retake.new_take_id
                current.in_ms = max(0, min(current.in_ms, max(0, limit - MIN_CLIP_MS)))
                current.out_ms = max(current.in_ms + MIN_CLIP_MS, min(current.out_ms, limit))
                current.speed = 1.0
                current.duration_ms = current.out_ms - current.in_ms
            kept.append(current)

        for item in additions:
            kept.append(
                TimelineClipInput(
                    track_id=video.id,
                    start_ms=0,  # relayout が決める
                    duration_ms=item.duration_ms or FALLBACK_CLIP_MS,
                    source_kind="take",
                    source_id=item.take_id,
                    in_ms=0,
                    out_ms=item.duration_ms or FALLBACK_CLIP_MS,
                )
            )

        # 差し込み専用（insert_only）・使わない（skip）カットのクリップは、人が
        # 置いた位置のまま動かさない（並べ直しの対象から外して、あとで同じ位置へ
        # 差し込み直す）。自動配置に混ぜると末尾へ押し出されてしまうため。
        take_ids = sorted({
            str(clip.source_id)
            for clip in kept
            if clip.source_kind == "take" and clip.source_id
        })
        roles = await _shot_roles(conn, take_ids)
        held = [
            clip
            for clip in kept
            if clip.source_kind == "take"
            and roles.get(str(clip.source_id or ""), "auto") != "auto"
        ]
        if held:
            holding = {id(clip) for clip in held}
            kept = [clip for clip in kept if id(clip) not in holding]
            take_ids = sorted({
                str(clip.source_id)
                for clip in kept
                if clip.source_kind == "take" and clip.source_id
            })

        # 音源基準で組んであるか（元カットが計画開始秒を持つか）を見る。
        plans = await _shot_plans(conn, take_ids)
        if any(value is not None for value in plans.values()):
            items: list[PlannedClip] = []
            seen: set[str] = set()
            for clip in sorted(kept, key=lambda item: item.start_ms):
                # 前に埋めた隙間は計画から作り直すので、ここで落とす。
                if clip.source_kind == "gap":
                    continue
                planned = (
                    plans.get(str(clip.source_id or ""))
                    if clip.source_kind == "take"
                    else None
                )
                if planned is not None:
                    # 差し込みで前後に割れた同じ Take は 1 本に戻す（計画が正本
                    # なので、割れたぶんは差し込み直しでもう一度作られる）。
                    if str(clip.source_id) in seen:
                        continue
                    seen.add(str(clip.source_id))
                source_ms = 0
                if planned is not None:
                    source_ms = (
                        await _take_duration(conn, str(clip.source_id)) or 0
                    )
                items.append(PlannedClip(clip, planned, source_ms))
            laid = plan_layout(
                items,
                video.id,
                float(timeline.fps or DEFAULT_FPS),
                gap_fill=timeline.gap_fill or DEFAULT_GAP_FILL,
                end_ms=planned_end_ms(timeline, detail),
            )
        else:
            laid = relayout(kept)

        # 外しておいた差し込みクリップを、元の位置へ差し込み直す（下のクリップが
        # 前後に割れるだけなので、トラックの全長は変わらない）。
        for clip in sorted(held, key=lambda item: item.start_ms):
            laid = insert_into(laid, clip, float(timeline.fps or DEFAULT_FPS))

        others = [
            to_clip_input(clip)
            for track in detail.tracks
            for clip in track.clips
            if track.id != video.id
        ]
        await _write_clips(
            conn, timeline_id, timeline.project_id, [*laid, *others]
        )
        await conn.commit()
    await _publish_timeline(timeline.project_id, timeline_id)

    fresh = await timeline_detail(timeline_id)
    assert fresh is not None
    return fresh


# --------------------------------------------------------------------------
# メディア欠落のリカバリ
# --------------------------------------------------------------------------

async def missing_report(timeline_id: str) -> TimelineMissingReport:
    """実ファイルが見つからないクリップと、その差し替え候補。

    テイクのクリップは**同じカットの別テイク**（動画が実在するもの）を新しい順に
    並べる。それ以外（ライブラリ・ジョブ・素材）は差し替え先を機械的に決められ
    ないので、候補は空で返す（画面では削除だけができる）。
    """
    detail = await timeline_detail(timeline_id)
    if detail is None:
        raise TimelineNotFound("timeline not found")
    broken = [
        clip for track in detail.tracks for clip in track.clips if clip.missing
    ]
    if not broken:
        return TimelineMissingReport()

    take_ids = sorted(
        {
            str(clip.source_id)
            for clip in broken
            if clip.source_kind == "take" and clip.source_id
        }
    )
    alternatives: dict[str, list[TimelineMissingCandidate]] = {}
    async with get_db() as conn:
        states = await _shot_state(conn, take_ids)
        shot_ids = sorted(
            {
                str(state["shot_id"])
                for state in states.values()
                if state.get("shot_exists")
            }
        )
        rows: list[Any] = []
        if shot_ids:
            placeholders = ", ".join("?" * len(shot_ids))
            async with conn.execute(
                "SELECT t.id AS take_id, t.shot_id AS shot_id, t.status AS status,"
                "       t.created_at AS created_at, j.video_path AS path"
                "  FROM studio_takes t"
                "  LEFT JOIN jobs j ON j.id = t.job_id"
                f" WHERE t.shot_id IN ({placeholders})"
                " ORDER BY t.created_at DESC, t.id DESC",
                tuple(shot_ids),
            ) as cur:
                rows = list(await cur.fetchall())

    by_shot: dict[str, list[TimelineMissingCandidate]] = {}
    for row in rows:
        if not row["path"]:
            continue
        resolved = rebase_stored_path(row["path"])
        if not resolved.is_file():
            continue
        duration_ms, _ = await probe_cached(resolved)
        by_shot.setdefault(str(row["shot_id"]), []).append(
            TimelineMissingCandidate(
                take_id=str(row["take_id"]),
                status=str(row["status"] or ""),
                created_at=str(row["created_at"] or ""),
                duration_ms=duration_ms,
            )
        )
    for take_id, state in states.items():
        alternatives[take_id] = [
            candidate
            for candidate in by_shot.get(str(state["shot_id"]), [])
            if candidate.take_id != take_id
        ]

    return TimelineMissingReport(
        clips=[
            TimelineMissingClip(
                clip_id=clip.id,
                label=clip.label,
                source_kind=clip.source_kind,
                source_id=clip.source_id,
                candidates=alternatives.get(str(clip.source_id or ""), []),
            )
            for clip in broken
        ]
    )


async def resolve_missing(
    timeline_id: str, fix: TimelineMissingFix
) -> StudioTimelineDetail:
    """欠落クリップを別テイクへ差し替える / まとめて消す。

    映像トラックは消したあとに詰め直す（:func:`relayout`）。音声・字幕は置き場所を
    保つ（消えた穴はそのまま空きになる）。
    """
    detail = await timeline_detail(timeline_id)
    if detail is None:
        raise TimelineNotFound("timeline not found")
    video = _video_track(detail)
    broken_ids = {
        clip.id for track in detail.tracks for clip in track.clips if clip.missing
    }
    drops = set(fix.drop_clip_ids) | (broken_ids if fix.drop_all else set())

    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        assert timeline is not None
        durations: dict[str, int | None] = {}
        for take_id in set(fix.replace.values()):
            durations[take_id] = await _take_duration(conn, take_id)

        kept_video: list[TimelineClipInput] = []
        for clip in sorted(video.clips, key=lambda item: item.start_ms):
            if clip.id in drops:
                continue
            current = to_clip_input(clip)
            take_id = fix.replace.get(clip.id)
            if take_id:
                limit = durations.get(take_id)
                if limit is None:
                    raise TimelineError(
                        f"差し替え先のテイクの動画が見つかりません: {take_id}"
                    )
                current.source_kind = "take"
                current.source_id = take_id
                current.in_ms = 0
                current.out_ms = limit
                current.speed = 1.0
                current.duration_ms = limit
            kept_video.append(current)

        others = [
            to_clip_input(clip)
            for track in detail.tracks
            for clip in track.clips
            if track.id != video.id and clip.id not in drops
        ]
        await _write_clips(
            conn, timeline_id, timeline.project_id, [*relayout(kept_video), *others]
        )
        await conn.commit()
    await _publish_timeline(timeline.project_id, timeline_id)

    fresh = await timeline_detail(timeline_id)
    assert fresh is not None
    return fresh
