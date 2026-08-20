"""編集タブ: タイムライン（EDL）の組み立てと書き出しの管理。

スタジオ（:mod:`app.studio`）が「1 カットを焼く」までを受け持つのに対して、
ここは**焼き上がったものを並べて 1 本にする**面を持つ:

- **タイムライン**（:data:`studio_timelines`）が 1 本の編集。書き出しの規格
  （幅・高さ・fps）を持ち、話（``episode_id``）を組んだものかどうかを覚える。
- **トラック**（:data:`timeline_tracks`）は並べる段。フェーズ 1 で作るのは
  ``video`` の ``V1`` だけで、音声・字幕は入れ物として値だけ通してある。
- **クリップ**（:data:`timeline_clips`）が 1 つの素材の切り出し。ソース
  （フェーズ 1 は Take だけ）を**参照するだけ**で、元が消えても並びは残り、
  読み取りのたびに ``missing``（メディア欠落）として見せる。
- **書き出し**（:data:`timeline_exports`）は ffmpeg の 1 回の実行。組み立てと
  実行は :mod:`app.timeline_export`、進捗は WS（``type: "timeline_export"``）。

ルーター（:mod:`app.routers.timelines`）とテストの両方から使うので、DB と
ffmpeg の呼び出しはこのモジュールに集約する（:mod:`app.studio` と同じ持ち方）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from . import library as library_service
from . import ws
from .db import get_db
from .ids import new_id
from .models import (
    LibraryItem,
    StudioTimeline,
    StudioTimelineCreate,
    StudioTimelineDetail,
    TimelineClip,
    TimelineClipInput,
    TimelineExport,
    TimelineTrack,
)
from .paths import OUTPUTS_DIR, rebase_stored_path
from .timeline_export import (
    ExportClip,
    ExportSpec,
    TimelineExportError,
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

#: まだ走っている書き出しの状態
RUNNING_STATUSES = ("queued", "running")


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


# --------------------------------------------------------------------------
# クリップの検証（純関数。ルーターとテストの両方から使う）
# --------------------------------------------------------------------------

def validate_clips(clips: list[TimelineClipInput]) -> None:
    """フェーズ 1 の約束をひととおり確かめる（破れていたら :class:`TimelineError`）。

    - ``in_ms < out_ms``（長さ 0 のクリップは置けない）
    - ``duration_ms == out_ms - in_ms``（等速のみ。速度変更はフェーズ 2）
    - ``start_ms >= 0``
    - 同じトラックの中でクリップが重ならない（隙間は ``gap`` で表す）

    ``gap`` だけは切り出し位置を持たないので、``in_ms`` / ``out_ms`` は見ない
    （``duration_ms`` がそのまま尺になる）。
    """
    for index, clip in enumerate(clips):
        where = f"クリップ {index + 1}"
        if clip.start_ms < 0:
            raise TimelineError(f"{where}: 開始位置が負です")
        if clip.duration_ms <= 0:
            raise TimelineError(f"{where}: 尺が 0 以下です")
        if not (clip.track_id or "").strip():
            raise TimelineError(f"{where}: トラックが指定されていません")
        if clip.source_kind == "gap":
            continue
        if clip.in_ms < 0:
            raise TimelineError(f"{where}: 切り出しの開始位置が負です")
        if clip.in_ms >= clip.out_ms:
            raise TimelineError(f"{where}: 切り出しの範囲が不正です（in < out）")
        if clip.duration_ms != clip.out_ms - clip.in_ms:
            raise TimelineError(
                f"{where}: 尺と切り出しの長さが合いません"
                "（フェーズ 1 は等速のみ）"
            )

    by_track: dict[str, list[TimelineClipInput]] = {}
    for clip in clips:
        by_track.setdefault(clip.track_id, []).append(clip)
    for track_id, group in by_track.items():
        ordered = sorted(group, key=lambda clip: clip.start_ms)
        for previous, current in zip(ordered, ordered[1:]):
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
        take_ids = sorted(
            {
                str(row["source_id"])
                for row in clip_rows
                if row["source_kind"] == "take" and row["source_id"]
            }
        )
        sources = await _take_sources(conn, take_ids)

    # 同じ Take を何本のクリップが指していても ffprobe は 1 回だけ。
    probed: dict[str, tuple[int | None, bool]] = {}
    for take_id, info in sources.items():
        path = info.get("path")
        resolved = rebase_stored_path(path) if path else None
        if resolved is None or not resolved.is_file():
            continue
        probed[take_id] = await probe_media(resolved)

    clips_by_track: dict[str, list[TimelineClip]] = {}
    for row in clip_rows:
        data = dict(row)
        data.pop("project_id", None)
        data["text_payload"] = json.loads(data["text_payload"] or "null") or None
        source_id = str(data.get("source_id") or "")
        info = sources.get(source_id) if data["source_kind"] == "take" else None
        path = (info or {}).get("path")
        resolved = rebase_stored_path(path) if path else None
        exists = bool(resolved and resolved.is_file())
        data["video_url"] = _output_url(path) if exists else None
        data["source_duration_ms"] = probed.get(source_id, (None, False))[0]
        # 隙間（gap）はソースを持たないので欠落にはならない。
        data["missing"] = data["source_kind"] != "gap" and not exists
        data["label"] = (info or {}).get("label", "")
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
) -> list[tuple[str, str]]:
    """その話の「採用 Take の動画があるカット」を場 -> カット順で ``(take_id, path)``。

    並び順は :func:`app.studio._fetch_shots` と同じ規則（話 -> 場 -> カット）。
    ここは 1 つの話に絞ってあるので、場の並び順とカットの並び順で決まる。
    """
    async with conn.execute(
        "SELECT s.selected_take_id AS take_id, j.video_path AS video_path"
        "  FROM studio_shots s"
        "  JOIN studio_scenes sc ON sc.id = s.scene_id"
        "  JOIN studio_takes t ON t.id = s.selected_take_id"
        "  LEFT JOIN jobs j ON j.id = t.job_id"
        " WHERE s.project_id = ? AND sc.episode_id = ?"
        " ORDER BY sc.sort_order, sc.created_at, sc.id,"
        "          s.sort_order, s.created_at, s.id",
        (project_id, episode_id),
    ) as cur:
        rows = await cur.fetchall()
    found: list[tuple[str, str]] = []
    for row in rows:
        path = row["video_path"]
        if not path:
            continue
        resolved = rebase_stored_path(path)
        if not resolved.is_file():
            continue
        found.append((str(row["take_id"]), str(resolved)))
    return found


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
        await conn.execute(
            "INSERT INTO studio_timelines"
            " (id, project_id, episode_id, name, fps, width, height,"
            "  created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                timeline_id,
                project_id,
                episode_id,
                name,
                float(payload.fps or DEFAULT_FPS),
                int(payload.width or DEFAULT_WIDTH),
                int(payload.height or DEFAULT_HEIGHT),
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

        placed: list[tuple[str, int]] = []
        if episode_id is not None:
            for take_id, path in await _selected_take_videos(
                conn, project_id, episode_id
            ):
                duration_ms, _ = await probe_media(path)
                placed.append((take_id, duration_ms or FALLBACK_CLIP_MS))

        start = 0
        for order, (take_id, duration_ms) in enumerate(placed):
            await conn.execute(
                "INSERT INTO timeline_clips"
                " (id, track_id, timeline_id, project_id, start_ms, duration_ms,"
                "  source_kind, source_id, in_ms, out_ms, gain_db, fade_in_ms,"
                "  fade_out_ms, transition_kind, transition_ms, text_payload,"
                "  sort_order)"
                " VALUES (?, ?, ?, ?, ?, ?, 'take', ?, 0, ?, 0, 0, 0, NULL, 0,"
                "         NULL, ?)",
                (
                    new_id(),
                    track_id,
                    timeline_id,
                    project_id,
                    start,
                    duration_ms,
                    take_id,
                    duration_ms,
                    order,
                ),
            )
            start += duration_ms
        await conn.commit()

    detail = await timeline_detail(timeline_id)
    assert detail is not None
    return detail


async def update_timeline(
    timeline_id: str, changes: dict[str, Any]
) -> StudioTimeline | None:
    """指定された項目だけ書き換える（送られなかった項目は今のまま）。"""
    fields = {
        name: value
        for name, value in changes.items()
        if name in ("name", "fps", "width", "height") and value is not None
    }
    async with get_db() as conn:
        if await _fetch_timeline(conn, timeline_id) is None:
            return None
        if fields:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            await conn.execute(
                f"UPDATE studio_timelines SET {assignments}, updated_at = ?"
                " WHERE id = ?",
                (*fields.values(), _now(), timeline_id),
            )
            await conn.commit()
        return await _fetch_timeline(conn, timeline_id)


async def delete_timeline(timeline_id: str) -> bool:
    """タイムラインとその中身を消す（トラック・クリップ・書き出しの記録）。

    外部キーの CASCADE は張っていないので、後始末はここで行う。書き出した
    ファイル（``outputs/exports/…``）は成果物なので残す（ジョブの出力と同じ扱い）。
    """
    async with get_db() as conn:
        if await _fetch_timeline(conn, timeline_id) is None:
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
        await conn.execute("DELETE FROM studio_timelines WHERE id = ?", (timeline_id,))
        await conn.commit()
    return True


async def replace_clips(
    timeline_id: str, clips: list[TimelineClipInput]
) -> StudioTimelineDetail | None:
    """このタイムラインのクリップを ``clips`` の通りにする（全置換）。

    画面の自動保存の受け口。1 つのトランザクションで消してから入れ直すので、
    途中で落ちても「前の状態」か「送られた状態」のどちらかになる。
    """
    validate_clips(clips)
    async with get_db() as conn:
        timeline = await _fetch_timeline(conn, timeline_id)
        if timeline is None:
            return None
        async with conn.execute(
            "SELECT id FROM timeline_tracks WHERE timeline_id = ?", (timeline_id,)
        ) as cur:
            known = {str(row["id"]) for row in await cur.fetchall()}
        unknown = sorted({clip.track_id for clip in clips} - known)
        if unknown:
            raise TimelineError(
                f"このタイムラインに無いトラックです: {', '.join(unknown)}"
            )

        await conn.execute(
            "DELETE FROM timeline_clips WHERE timeline_id = ?", (timeline_id,)
        )
        ordered = sorted(clips, key=lambda clip: (clip.track_id, clip.start_ms))
        for order, clip in enumerate(ordered):
            await conn.execute(
                "INSERT INTO timeline_clips"
                " (id, track_id, timeline_id, project_id, start_ms, duration_ms,"
                "  source_kind, source_id, in_ms, out_ms, gain_db, fade_in_ms,"
                "  fade_out_ms, transition_kind, transition_ms, text_payload,"
                "  sort_order)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    clip.id or new_id(),
                    clip.track_id,
                    timeline_id,
                    timeline.project_id,
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
                    order,
                ),
            )
        await conn.execute(
            "UPDATE studio_timelines SET updated_at = ? WHERE id = ?",
            (_now(), timeline_id),
        )
        await conn.commit()
    return await timeline_detail(timeline_id)


# --------------------------------------------------------------------------
# 書き出し
# --------------------------------------------------------------------------

def _row_to_export(row: aiosqlite.Row) -> TimelineExport:
    data = dict(row)
    try:
        data["params"] = json.loads(data.get("params") or "{}")
    except ValueError:  # pragma: no cover - 自分で書いた JSON なので通らない
        data["params"] = {}
    data["output_url"] = _output_url(data.get("output_path"))
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


async def build_spec(timeline_id: str, params: dict[str, Any]) -> ExportSpec:
    """このタイムラインの今の中身から、書き出し 1 回ぶんの :class:`ExportSpec`。

    フェーズ 1 は **V1（一番上の video トラック）だけ**を焼く。ソースの実ファイルが
    無いクリップ（メディア欠落）はその尺の隙間（黒＋無音）に置き換える——
    途中で失敗させるより、欠けているところが目に見えるほうが直しやすいため。
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

    # クリップの実ファイルは detail に載っていないので、Take から引き直す。
    paths: dict[str, str] = {}
    async with get_db() as conn:
        take_ids = sorted(
            {clip.source_id for clip in clips if clip.source_kind == "take" and clip.source_id}
        )
        for take_id, info in (await _take_sources(conn, take_ids)).items():
            path = info.get("path")
            if not path:
                continue
            resolved = rebase_stored_path(path)
            if resolved.is_file():
                paths[take_id] = str(resolved)

    export_clips: list[ExportClip] = []
    cursor = 0
    for clip in clips:
        # クリップの前に空きがあれば、その尺ぶんの隙間を入れて時間を合わせる。
        if clip.start_ms > cursor:
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
        path = paths.get(clip.source_id or "") if clip.source_kind == "take" else None
        has_audio = False
        if path is not None:
            _, has_audio = await probe_media(path)
        export_clips.append(
            ExportClip(
                path=path,
                in_ms=clip.in_ms if path else 0,
                out_ms=clip.out_ms if path else clip.duration_ms,
                duration_ms=clip.duration_ms,
                has_audio=has_audio,
                gain_db=clip.gain_db,
            )
        )
        cursor = clip.start_ms + clip.duration_ms

    return ExportSpec(
        width=int(params.get("width") or detail.width),
        height=int(params.get("height") or detail.height),
        fps=float(params.get("fps") or detail.fps),
        clips=export_clips,
    )


async def start_export(timeline_id: str, params: dict[str, Any]) -> TimelineExport:
    """書き出しを 1 本受け付ける（実行はバックグラウンド）。

    同じタイムラインで走っているものがあれば :class:`TimelineConflict`
    （同時に 2 本焼いても得はなく、進捗の見せ方も破綻するため）。
    """
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
            if key in ("width", "height", "fps") and value is not None
        }
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
        spec = await build_spec(timeline_id, params)
        output = export_dir(export_id) / EXPORT_FILENAME
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
        await _update_export(
            export_id,
            status="done",
            progress=1.0,
            output_path=str(output),
            error=None,
            finished_at=_now(),
        )
    finished = await get_export(export_id)
    if finished is not None:
        await _publish(finished)


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
