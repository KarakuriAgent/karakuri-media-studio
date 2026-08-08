"""ドラマスタジオ: プロジェクト -> 脚本（Shot）-> Shot ごとの生成 -> Take の採用。

生成フォームが「1 本の動画を作る道具」なのに対して、ここは**作品を通して作る**
ための入れ物になる:

- **プロジェクト**（:data:`studio_projects`）が 1 本の作品。
- **話 / 場**（:data:`studio_episodes` / :data:`studio_scenes`）は Shot をまとめる
  見出し。Shot は場に属さなくてもよい（未分類）。
- **World Bible**（:data:`studio_assets`）はその作品に出るキャラ・場所・小道具の
  素材。実体は ``assets/<kind>/`` に置き、プロンプトからは ``@名前`` で呼ぶ。
  ファイルを持たない**メタデータのみの素材**もあり、そちらは参照に添付する
  代わりに説明文へ展開される。メインのファイルに加えて、声サンプルや動画
  リファレンスを何本でも足せる（:data:`studio_asset_files`）。こちらは今の
  生成ワークフローには流し込まず、目録とエージェントへの手がかりとして持つ。
- **Shot**（:data:`studio_shots`）が脚本の 1 カット。台詞・SE・BGM・カメラは
  別々の欄で書いておき、投入のときに MiniMax H3 のプロンプト規約
  （:mod:`app.workflows` の `prompt_hint`）へ組み立てる。画面比・解像度・シード・
  ワークフローの強制指定も Shot ごとに持てる。
- **Take**（:data:`studio_takes`）が Shot 1 回ぶんの生成。実行状態と成果物は
  ジョブ（:mod:`app.jobs`）が持っているので、ここが覚えるのは「どのジョブか」と
  「採用したか」だけで、読み取りのたびに join して返す。Take を作ったあとに
  脚本や素材が動いたか（stale）も、保存せずその場で導出する。
- **リビジョン**（:data:`studio_revisions`）はプロジェクト状態の履歴。状態を変える
  操作のたびにスナップショットを 1 件残し、指定したところへ書き戻せる。

ルーター（:mod:`app.routers.studio`）とテストの両方から使うので、DB とジョブ
投入はこのモジュールに集約する。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

import aiosqlite
from pydantic import ValidationError

from . import grok
from . import jobs as job_service
from . import studio_demo
from .db import get_db
from .ids import new_id
from .models import (
    ASSET_FILE_ROLE_KINDS,
    JobCreate,
    StudioAsset,
    StudioAssetFile,
    StudioEpisode,
    StudioProject,
    StudioProjectDetail,
    StudioProjectSummary,
    StudioPromptReference,
    StudioRevision,
    StudioRevisionDetail,
    StudioScene,
    StudioShot,
    StudioShotPreview,
    StudioTake,
    validate_asset_profile,
)
from .paths import rebase_stored_path
from .workflows import get_video_spec

#: Shot が使う動画ワークフロー（すべて MiniMax H3。音声も同じパスで出る）
WORKFLOW_T2V = "minimax_h3_t2v"
WORKFLOW_I2V = "minimax_h3_i2v"
WORKFLOW_R2V = "minimax_h3_r2v"

#: 素材の種別 -> プロンプト中で参照素材を呼ぶタグの名前（SPEC §3.1 / r2v）
MENTION_TAGS: dict[str, str] = {
    "image": "Picture",
    "video": "Video",
    "audio": "Audio",
}

#: 素材の種別 -> :class:`app.models.JobCreate` の参照素材フィールド
REFERENCE_FIELDS: dict[str, str] = {
    "image": "reference_images",
    "video": "reference_videos",
    "audio": "reference_audios",
}

#: MiniMax H3 に「焼き込み字幕を出さない」と伝える締めの一文。プロンプトに
#: 否定欄が無いので本文の最後に書く決まりで、台詞のある Shot でこれを落とすと
#: 画面に字幕が乗る（:mod:`app.workflows` の `_MINIMAX_H3_PROMPT_CORE`）。
EXCLUSION_SENTENCE = "No text, subtitles, logos or watermarks."

#: ASCII の語中文字。`@名前` の直後がこれだと名前が途中で切れているとみなす
#: （`@Aki` が `@Akira` の一部を食わないように）。日本語の名前は語中文字を
#: 持たないので、後ろに助詞が続いてもそのまま解決できる。
_WORD_CHAR = re.compile(r"[A-Za-z0-9_]")

#: 日本語（ひらがな・カタカナ・漢字）が 1 文字でもあるか。英訳の要否の判定に使う。
_JAPANESE = re.compile("[\\u3040-\\u30ff\\u3400-\\u4dbf\\u4e00-\\u9fff]")

#: 1 プロジェクトが持てるリビジョンの数（超えたぶんは古いものから捨てる）
REVISION_LIMIT = 200

#: Shot のうち、書き換えると**プロンプトが変わる**項目。ここが動いたときだけ
#: `prompt_updated_at` を進め、それより古い Take が stale になる（採用・並べ替え
#: のような「本文に効かない更新」で作り直しを促さないため）。
PROMPT_FIELDS = frozenset({
    "prompt", "dialogue", "soundscape", "bgm", "camera", "duration_seconds",
    "aspect_ratio", "megapixels", "seed", "workflow_override",
})

#: 素材のうち、書き換えるとプロンプト（または参照そのもの）が変わる項目。
ASSET_PROMPT_FIELDS = frozenset({"name", "caption", "prompt_caption", "kind", "path"})


class StudioError(Exception):
    """スタジオ操作の失敗（ルーターが 400 に変換する）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _asset_url(kind: str, path: str) -> str:
    """静的配信 URL。メタデータのみの素材（パスなし）は空。"""
    return f"/assets/{kind}/{Path(path).name}" if path else ""


def _row_to_project(row: aiosqlite.Row) -> StudioProject:
    data = dict(row)
    data["auto_translate"] = bool(data.get("auto_translate", 1))
    return StudioProject(**data)


def _load_json(raw: Any) -> dict[str, Any]:
    """TEXT 列に入れた JSON を dict に戻す（壊れていたら空）。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:  # pragma: no cover - 自分で書いた JSON なので通らない
        return {}
    return value if isinstance(value, dict) else {}


def _row_to_asset(
    row: aiosqlite.Row, files: list[StudioAssetFile] | None = None
) -> StudioAsset:
    data = dict(row)
    data["locked"] = bool(data["locked"])
    data["path"] = data.get("path") or ""
    data["profile"] = _load_json(data.get("profile"))
    data["url"] = _asset_url(data["kind"], data["path"])
    data["updated_at"] = data.get("updated_at") or data["created_at"]
    data["prompt_updated_at"] = data.get("prompt_updated_at") or data["updated_at"]
    data["files"] = list(files or ())
    return StudioAsset(**data)


def _row_to_asset_file(row: aiosqlite.Row) -> StudioAssetFile:
    data = dict(row)
    data["path"] = data.get("path") or ""
    data["url"] = _asset_url(ASSET_FILE_ROLE_KINDS.get(data["role"], "image"), data["path"])
    return StudioAssetFile(**data)


def _stored_asset_path(path: str, kind: str) -> str:
    """素材のファイルとして持てる場所（``assets/<kind>/``）に置いたパス。

    既に ``assets/`` の下にあるものはそのまま使い、それ以外（チャットの添付や
    ジョブの成果物）は複製してから参照する: 素材のファイルは作品の持ち物なので、
    作業ディレクトリの掃除で消えては困るため。
    """
    raw = str(path or "").strip()
    if not raw:
        return ""
    source = rebase_stored_path(raw)
    if not source.is_file():
        raise StudioError(f"ファイルが見つかりません: {raw}")
    assets_root = job_service.ASSETS_DIR.resolve()
    resolved = source.resolve()
    if assets_root == resolved or assets_root in resolved.parents:
        return str(resolved)
    try:
        return str(job_service.copy_into_assets(resolved, kind))
    except job_service.JobValidationError as exc:
        raise StudioError(str(exc)) from exc


def _row_to_shot(row: aiosqlite.Row) -> StudioShot:
    data = dict(row)
    data["carry_over_end_frame"] = bool(data["carry_over_end_frame"])
    data["prompt_updated_at"] = data.get("prompt_updated_at") or data["updated_at"]
    return StudioShot(**data)


# --------------------------------------------------------------------------
# プロジェクト
# --------------------------------------------------------------------------

async def list_projects() -> list[StudioProjectSummary]:
    """一覧。1 行ごとに「どれだけ進んでいるか」の件数を添える。"""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT p.*,"
            " (SELECT COUNT(*) FROM studio_shots s WHERE s.project_id = p.id)"
            "   AS shot_count,"
            " (SELECT COUNT(*) FROM studio_assets a WHERE a.project_id = p.id)"
            "   AS asset_count,"
            " (SELECT COUNT(*) FROM studio_takes t WHERE t.project_id = p.id)"
            "   AS take_count,"
            " (SELECT COUNT(*) FROM studio_shots s WHERE s.project_id = p.id"
            "   AND s.selected_take_id IS NOT NULL) AS selected_take_count"
            " FROM studio_projects p ORDER BY p.created_at DESC, p.id DESC"
        ) as cur:
            rows = await cur.fetchall()
    summaries = []
    for row in rows:
        data = dict(row)
        data["auto_translate"] = bool(data.get("auto_translate", 1))
        summaries.append(StudioProjectSummary(**data))
    return summaries


async def create_project(
    name: str,
    code: str,
    synopsis: str,
    world_notes: str,
    auto_translate: bool = True,
    *,
    actor: str = "user",
) -> StudioProject:
    title = (name or "").strip()
    if not title:
        raise StudioError("プロジェクト名を入れてください")
    async with get_db() as conn:
        project = await _insert_project(
            conn, title, code, synopsis, world_notes, auto_translate
        )
        await _record_revision(conn, project.id, actor, "プロジェクトを作成")
        await conn.commit()
        return project


async def _insert_project(
    conn: aiosqlite.Connection,
    title: str,
    code: str,
    synopsis: str,
    world_notes: str,
    auto_translate: bool,
) -> StudioProject:
    project_id = new_id()
    now = _now()
    try:
        await conn.execute(
            "INSERT INTO studio_projects"
            " (id, name, code, synopsis, world_notes, auto_translate,"
            "  created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                title,
                (code or "").strip(),
                synopsis or "",
                world_notes or "",
                1 if auto_translate else 0,
                now,
                now,
            ),
        )
    except aiosqlite.IntegrityError as exc:
        raise StudioError(f"作品コード '{code}' は既に使われています") from exc
    return await _fetch_project(conn, project_id)  # type: ignore[return-value]


async def _fetch_project(
    conn: aiosqlite.Connection, project_id: str
) -> StudioProject | None:
    async with conn.execute(
        "SELECT * FROM studio_projects WHERE id = ?", (project_id,)
    ) as cur:
        row = await cur.fetchone()
    return _row_to_project(row) if row else None


async def get_project(project_id: str) -> StudioProject | None:
    async with get_db() as conn:
        return await _fetch_project(conn, project_id)


async def update_project(
    project_id: str, *, actor: str = "user", **fields: Any
) -> StudioProject | None:
    """``None`` の項目は触らない（指定されたものだけ書き換える）。"""
    changes = {key: value for key, value in fields.items() if value is not None}
    if "name" in changes:
        changes["name"] = str(changes["name"]).strip()
        if not changes["name"]:
            raise StudioError("プロジェクト名を空にはできません")
    if "code" in changes:
        changes["code"] = str(changes["code"]).strip()
    if "auto_translate" in changes:
        changes["auto_translate"] = 1 if changes["auto_translate"] else 0
    async with get_db() as conn:
        if await _fetch_project(conn, project_id) is None:
            return None
        if changes:
            changes["updated_at"] = _now()
            assignments = ", ".join(f"{key} = ?" for key in changes)
            try:
                await conn.execute(
                    f"UPDATE studio_projects SET {assignments} WHERE id = ?",
                    (*changes.values(), project_id),
                )
            except aiosqlite.IntegrityError as exc:
                raise StudioError(
                    f"作品コード '{changes.get('code')}' は既に使われています"
                ) from exc
            await _record_revision(conn, project_id, actor, "プロジェクトを更新")
            await conn.commit()
        return await _fetch_project(conn, project_id)


async def delete_project(project_id: str) -> bool:
    """素材・Shot・Take ごと消す（外部キーの ON DELETE CASCADE）。

    素材のファイル実体と生成済みのジョブは残す: どちらも他から参照されうる
    （ライブラリ・履歴）ので、目録だけを畳む。
    """
    async with get_db() as conn:
        cur = await conn.execute(
            "DELETE FROM studio_projects WHERE id = ?", (project_id,)
        )
        await conn.commit()
        return cur.rowcount > 0


async def project_detail(project_id: str) -> StudioProjectDetail | None:
    """画面 1 枚ぶん（話・場・素材・Shot・Take をジョブの状態つきで）。"""
    async with get_db() as conn:
        project = await _fetch_project(conn, project_id)
        if project is None:
            return None
        assets = await _fetch_assets(conn, project_id)
        episodes = await _fetch_episodes(conn, project_id)
        scenes = await _fetch_scenes(conn, "project_id = ?", (project_id,))
        shots = await _fetch_shots(conn, project_id)
        takes = await _fetch_takes(conn, "project_id = ?", (project_id,))
        takes = _mark_stale(takes, shots, assets)
    return StudioProjectDetail(
        **project.model_dump(),
        assets=assets,
        episodes=episodes,
        scenes=scenes,
        shots=shots,
        takes=takes,
    )


# --------------------------------------------------------------------------
# リビジョン履歴（軽量版）
# --------------------------------------------------------------------------
#
# プロジェクトの状態を変える操作のたびに、その**後**の状態を丸ごと 1 行に残す。
# 残すのは DB の行そのもの（projects / episodes / scenes / shots / assets）で、
# Take とジョブから導出する値は入れない: 実行結果は「戻す」対象ではないため。

#: スナップショットに入れるテーブルと、その主キー以外の並び順。
#: キャンバスのカード（:mod:`app.canvas`）は**最後**に書き戻す: エンティティを
#: 先に戻しておかないと、行を消したときのトリガーが戻したばかりのカードを
#: 巻き添えで消してしまう。表示位置（`canvas_*`）は project 行に入っている。
_SNAPSHOT_TABLES = (
    ("episodes", "studio_episodes", "sort_order, created_at, id"),
    ("scenes", "studio_scenes", "sort_order, created_at, id"),
    ("shots", "studio_shots", "sort_order, created_at, id"),
    ("assets", "studio_assets", "sort_order, created_at, id"),
    # 素材のリファレンスは**素材のあと**（親が戻る前に入れると FK で落ちる）。
    ("asset_files", "studio_asset_files", "sort_order, created_at, id"),
    ("cards", "canvas_cards", "z, created_at, id"),
)


async def _snapshot(conn: aiosqlite.Connection, project_id: str) -> dict[str, Any]:
    async with conn.execute(
        "SELECT * FROM studio_projects WHERE id = ?", (project_id,)
    ) as cur:
        row = await cur.fetchone()
    snapshot: dict[str, Any] = {"project": dict(row) if row else {}}
    for key, table, order in _SNAPSHOT_TABLES:
        async with conn.execute(
            f"SELECT * FROM {table} WHERE project_id = ? ORDER BY {order}",
            (project_id,),
        ) as cur:
            snapshot[key] = [dict(item) for item in await cur.fetchall()]
    return snapshot


async def _record_revision(
    conn: aiosqlite.Connection, project_id: str, actor: str, action: str
) -> None:
    """今のプロジェクト状態を 1 リビジョンとして残す（commit は呼び出し側）。"""
    snapshot = await _snapshot(conn, project_id)
    if not snapshot["project"]:  # 消えたプロジェクトには何も残さない
        return
    async with conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM studio_revisions"
        " WHERE project_id = ?",
        (project_id,),
    ) as cur:
        seq = int((await cur.fetchone())["next"])
    await conn.execute(
        "INSERT INTO studio_revisions"
        " (id, project_id, seq, actor, action, snapshot_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            new_id(),
            project_id,
            seq,
            actor if actor in ("user", "agent") else "user",
            action,
            json.dumps(snapshot, ensure_ascii=False),
            _now(),
        ),
    )
    # 直近 REVISION_LIMIT 件だけ残す（履歴は「少し戻れる」ためのもので、
    # 作品まるごとのバックアップではない）。
    await conn.execute(
        "DELETE FROM studio_revisions WHERE project_id = ? AND seq <= ?",
        (project_id, seq - REVISION_LIMIT),
    )


async def list_revisions(project_id: str) -> list[StudioRevision]:
    """新しい順の見出し一覧（snapshot は含めない）。"""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT seq, actor, action, created_at FROM studio_revisions"
            " WHERE project_id = ? ORDER BY seq DESC",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [StudioRevision(**dict(row)) for row in rows]


async def get_revision(project_id: str, seq: int) -> StudioRevisionDetail | None:
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM studio_revisions WHERE project_id = ? AND seq = ?",
            (project_id, seq),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    try:
        snapshot = json.loads(row["snapshot_json"])
    except ValueError:  # pragma: no cover - 自分で書いた JSON なので通らない
        snapshot = {}
    return StudioRevisionDetail(
        seq=row["seq"],
        actor=row["actor"],
        action=row["action"],
        created_at=row["created_at"],
        snapshot=snapshot if isinstance(snapshot, dict) else {},
    )


def _restore_columns(rows: list[dict[str, Any]], columns: set[str]) -> list[str]:
    """スナップショットと今のテーブルの**両方にある**カラムだけを書き戻す。"""
    if not rows:
        return []
    return [name for name in rows[0] if name in columns]


async def _table_columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        return {row["name"] for row in await cur.fetchall()}


async def restore_revision(project_id: str, seq: int) -> StudioProjectDetail | None:
    """``seq`` のスナップショットの内容でプロジェクトを書き戻す。

    Take と実ファイルは対象外で、そのまま残る（実行結果は履歴で戻すものでは
    ない）。復元後の状態も 1 リビジョンとして記録するので、「復元をやめる」も
    履歴から辿れる。
    """
    revision = await get_revision(project_id, seq)
    if revision is None:
        return None
    snapshot = revision.snapshot
    async with get_db() as conn:
        if await _fetch_project(conn, project_id) is None:
            return None
        project_row = snapshot.get("project") or {}
        columns = await _table_columns(conn, "studio_projects")
        fields = [
            name
            for name in project_row
            if name in columns and name not in ("id", "created_at")
        ]
        if fields:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            await conn.execute(
                f"UPDATE studio_projects SET {assignments} WHERE id = ?",
                (*(project_row[name] for name in fields), project_id),
            )
        # 素材名はプロジェクト内で一意なので、入れ替えの途中で衝突しないよう
        # いったん id を名前にしてから書き戻す。
        await conn.execute(
            "UPDATE studio_assets SET name = id WHERE project_id = ?", (project_id,)
        )
        for key, table, _order in _SNAPSHOT_TABLES:
            await _restore_table(conn, project_id, table, snapshot.get(key) or [])
        # 消えた Take を指したままの採用は外す（Take は復元しないため）。
        await conn.execute(
            "UPDATE studio_shots SET selected_take_id = NULL"
            " WHERE project_id = ? AND selected_take_id IS NOT NULL"
            " AND selected_take_id NOT IN (SELECT id FROM studio_takes)",
            (project_id,),
        )
        # 復元で消えた場に残った Shot は未分類へ戻す。
        await conn.execute(
            "UPDATE studio_shots SET scene_id = NULL"
            " WHERE project_id = ? AND scene_id IS NOT NULL"
            " AND scene_id NOT IN (SELECT id FROM studio_scenes)",
            (project_id,),
        )
        # スナップショットを取ったあとに参照先（Take など、復元の対象外の行）が
        # 消えていたカードは戻さない（キャンバスに空のカードを残さない）。
        await conn.execute(
            "DELETE FROM canvas_cards WHERE project_id = ? AND entity_id IS NOT NULL"
            " AND entity_id NOT IN ("
            "   SELECT id FROM studio_assets UNION ALL SELECT id FROM studio_scenes"
            "   UNION ALL SELECT id FROM studio_shots"
            "   UNION ALL SELECT id FROM studio_takes)",
            (project_id,),
        )
        await _record_revision(
            conn, project_id, "user", f"リビジョン {seq} を復元"
        )
        await conn.commit()
    return await project_detail(project_id)


async def _restore_table(
    conn: aiosqlite.Connection,
    project_id: str,
    table: str,
    rows: list[dict[str, Any]],
) -> None:
    """``table`` のプロジェクトぶんの行を ``rows`` の通りにする。"""
    columns = await _table_columns(conn, table)
    names = _restore_columns(rows, columns)
    wanted = {str(row["id"]) for row in rows if row.get("id")}
    async with conn.execute(
        f"SELECT id FROM {table} WHERE project_id = ?", (project_id,)
    ) as cur:
        current = {str(row["id"]) for row in await cur.fetchall()}
    for stale_id in current - wanted:
        await conn.execute(f"DELETE FROM {table} WHERE id = ?", (stale_id,))
    for row in rows:
        row_id = str(row.get("id") or "")
        if not row_id:
            continue
        values = [row[name] for name in names]
        if row_id in current:
            assignments = ", ".join(f"{name} = ?" for name in names)
            await conn.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?", (*values, row_id)
            )
        else:
            placeholders = ", ".join("?" * len(names))
            await conn.execute(
                f"INSERT INTO {table} ({', '.join(names)})"
                f" VALUES ({placeholders})",
                values,
            )


# --------------------------------------------------------------------------
# 話（エピソード）と場（シーン）
# --------------------------------------------------------------------------

async def _fetch_episodes(
    conn: aiosqlite.Connection, project_id: str
) -> list[StudioEpisode]:
    async with conn.execute(
        "SELECT * FROM studio_episodes WHERE project_id = ?"
        " ORDER BY sort_order, created_at, id",
        (project_id,),
    ) as cur:
        return [StudioEpisode(**dict(row)) for row in await cur.fetchall()]


async def _fetch_episode(
    conn: aiosqlite.Connection, episode_id: str
) -> StudioEpisode | None:
    async with conn.execute(
        "SELECT * FROM studio_episodes WHERE id = ?", (episode_id,)
    ) as cur:
        row = await cur.fetchone()
    return StudioEpisode(**dict(row)) if row else None


async def _fetch_scenes(
    conn: aiosqlite.Connection, where: str, params: tuple[Any, ...]
) -> list[StudioScene]:
    async with conn.execute(
        f"SELECT * FROM studio_scenes WHERE {where}"
        " ORDER BY sort_order, created_at, id",
        params,
    ) as cur:
        return [StudioScene(**dict(row)) for row in await cur.fetchall()]


async def _fetch_scene(
    conn: aiosqlite.Connection, scene_id: str
) -> StudioScene | None:
    scenes = await _fetch_scenes(conn, "id = ?", (scene_id,))
    return scenes[0] if scenes else None


async def get_episode(episode_id: str) -> StudioEpisode | None:
    async with get_db() as conn:
        return await _fetch_episode(conn, episode_id)


async def get_scene(scene_id: str) -> StudioScene | None:
    async with get_db() as conn:
        return await _fetch_scene(conn, scene_id)


async def _next_sort_order(
    conn: aiosqlite.Connection, table: str, column: str, parent_id: str
) -> int:
    async with conn.execute(
        f"SELECT COALESCE(MAX(sort_order), -1) + 1 AS next FROM {table}"
        f" WHERE {column} = ?",
        (parent_id,),
    ) as cur:
        return int((await cur.fetchone())["next"])


async def create_episode(
    project_id: str, payload: Any, *, actor: str = "user"
) -> StudioEpisode:
    async with get_db() as conn:
        if await _fetch_project(conn, project_id) is None:
            raise StudioError("project not found")
        sort_order = payload.sort_order
        if sort_order is None:
            sort_order = await _next_sort_order(
                conn, "studio_episodes", "project_id", project_id
            )
        episode_id = new_id()
        await conn.execute(
            "INSERT INTO studio_episodes"
            " (id, project_id, sort_order, title, synopsis, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                episode_id,
                project_id,
                sort_order,
                payload.title,
                payload.synopsis,
                _now(),
            ),
        )
        await _record_revision(conn, project_id, actor, "話を追加")
        await conn.commit()
        return await _fetch_episode(conn, episode_id)  # type: ignore[return-value]


async def update_episode(
    episode_id: str, *, actor: str = "user", **fields: Any
) -> StudioEpisode | None:
    changes = {key: value for key, value in fields.items() if value is not None}
    async with get_db() as conn:
        episode = await _fetch_episode(conn, episode_id)
        if episode is None:
            return None
        if changes:
            assignments = ", ".join(f"{key} = ?" for key in changes)
            await conn.execute(
                f"UPDATE studio_episodes SET {assignments} WHERE id = ?",
                (*changes.values(), episode_id),
            )
            await _record_revision(conn, episode.project_id, actor, "話を更新")
            await conn.commit()
        return await _fetch_episode(conn, episode_id)


async def delete_episode(episode_id: str) -> bool:
    """配下の場ごと消す。場にいた Shot は未分類（``scene_id = NULL``）に戻る。"""
    async with get_db() as conn:
        episode = await _fetch_episode(conn, episode_id)
        if episode is None:
            return False
        scenes = await _fetch_scenes(conn, "episode_id = ?", (episode_id,))
        for scene in scenes:
            await _detach_shots(conn, scene.id)
        await conn.execute("DELETE FROM studio_episodes WHERE id = ?", (episode_id,))
        await _record_revision(conn, episode.project_id, "user", "話を削除")
        await conn.commit()
        return True


async def reorder_episodes(project_id: str, ids: list[str]) -> list[StudioEpisode]:
    async with get_db() as conn:
        if await _fetch_project(conn, project_id) is None:
            raise StudioError("project not found")
        current = {episode.id for episode in await _fetch_episodes(conn, project_id)}
        _check_reorder(ids, current, "ids はこのプロジェクトの話を全件並べたもの")
        for order, episode_id in enumerate(ids):
            await conn.execute(
                "UPDATE studio_episodes SET sort_order = ? WHERE id = ?",
                (order, episode_id),
            )
        await _record_revision(conn, project_id, "user", "話を並べ替え")
        await conn.commit()
        return await _fetch_episodes(conn, project_id)


async def create_scene(
    episode_id: str, payload: Any, *, actor: str = "user"
) -> StudioScene:
    async with get_db() as conn:
        episode = await _fetch_episode(conn, episode_id)
        if episode is None:
            raise StudioError("episode not found")
        sort_order = payload.sort_order
        if sort_order is None:
            sort_order = await _next_sort_order(
                conn, "studio_scenes", "episode_id", episode_id
            )
        scene_id = new_id()
        await conn.execute(
            "INSERT INTO studio_scenes"
            " (id, episode_id, project_id, sort_order, title, synopsis,"
            "  time_of_day, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scene_id,
                episode_id,
                episode.project_id,
                sort_order,
                payload.title,
                payload.synopsis,
                payload.time_of_day,
                _now(),
            ),
        )
        await _record_revision(conn, episode.project_id, actor, "場を追加")
        await conn.commit()
        return await _fetch_scene(conn, scene_id)  # type: ignore[return-value]


async def update_scene(
    scene_id: str, *, actor: str = "user", **fields: Any
) -> StudioScene | None:
    """場の項目を変える。

    ``episode_id`` を渡すと**別の話へ引っ越す**（同じ作品の話だけ。並び順は
    引っ越し先の末尾に付け直す）。キャンバスは場の所属する話でタブが決まるので、
    ここが「カードを別のタブへ動かす」口も兼ねる。
    """
    changes = {key: value for key, value in fields.items() if value is not None}
    async with get_db() as conn:
        scene = await _fetch_scene(conn, scene_id)
        if scene is None:
            return None
        moved_to = changes.get("episode_id")
        if moved_to is not None and moved_to != scene.episode_id:
            episode = await _fetch_episode(conn, moved_to)
            if episode is None or episode.project_id != scene.project_id:
                raise StudioError(f"話が見つかりません: {moved_to}")
            changes.setdefault(
                "sort_order",
                await _next_sort_order(
                    conn, "studio_scenes", "episode_id", moved_to
                ),
            )
        if changes:
            assignments = ", ".join(f"{key} = ?" for key in changes)
            await conn.execute(
                f"UPDATE studio_scenes SET {assignments} WHERE id = ?",
                (*changes.values(), scene_id),
            )
            await _record_revision(conn, scene.project_id, actor, "場を更新")
            await conn.commit()
        return await _fetch_scene(conn, scene_id)


async def _detach_shots(conn: aiosqlite.Connection, scene_id: str) -> None:
    """場から Shot を外す（Shot 自体は消さない。未分類に戻すだけ）。"""
    await conn.execute(
        "UPDATE studio_shots SET scene_id = NULL WHERE scene_id = ?", (scene_id,)
    )


async def delete_scene(scene_id: str) -> bool:
    """場だけ消す。そこにいた Shot は未分類（``scene_id = NULL``）に戻る。"""
    async with get_db() as conn:
        scene = await _fetch_scene(conn, scene_id)
        if scene is None:
            return False
        await _detach_shots(conn, scene_id)
        await conn.execute("DELETE FROM studio_scenes WHERE id = ?", (scene_id,))
        await _record_revision(conn, scene.project_id, "user", "場を削除")
        await conn.commit()
        return True


async def reorder_scenes(episode_id: str, ids: list[str]) -> list[StudioScene]:
    async with get_db() as conn:
        episode = await _fetch_episode(conn, episode_id)
        if episode is None:
            raise StudioError("episode not found")
        current = {
            scene.id for scene in await _fetch_scenes(conn, "episode_id = ?", (episode_id,))
        }
        _check_reorder(ids, current, "ids はこの話の場を全件並べたもの")
        for order, scene_id in enumerate(ids):
            await conn.execute(
                "UPDATE studio_scenes SET sort_order = ? WHERE id = ?",
                (order, scene_id),
            )
        await _record_revision(conn, episode.project_id, "user", "場を並べ替え")
        await conn.commit()
        return await _fetch_scenes(conn, "episode_id = ?", (episode_id,))


def _check_reorder(ids: list[str], current: set[str], what: str) -> None:
    """並べ替えは**全件、過不足なく**受ける（一部だけでは順序が決まらない）。"""
    if len(set(ids)) != len(ids):
        raise StudioError("ids に同じ id が複数あります")
    if set(ids) != current:
        raise StudioError(f"{what}にしてください")


# --------------------------------------------------------------------------
# デモプロジェクト
# --------------------------------------------------------------------------

class StudioConflict(StudioError):
    """既にあるものを作ろうとした（ルーターが 409 に変換する）。"""


async def create_demo_project(code: str) -> StudioProjectDetail:
    """:mod:`app.studio_demo` のマニフェスト 1 本ぶんを丸ごと作る。

    素材はメタデータのみ（ファイルなし）で入る。同じ作品コードのプロジェクトが
    既にあれば :class:`StudioConflict`。
    """
    manifest = studio_demo.DEMO_BY_CODE.get((code or "").strip())
    if manifest is None:
        known = "・".join(studio_demo.DEMO_BY_CODE)
        raise StudioError(f"そんなデモはありません: '{code}'（ある のは {known}）")
    async with get_db() as conn:
        async with conn.execute(
            "SELECT id FROM studio_projects WHERE code = ?", (manifest["code"],)
        ) as cur:
            if await cur.fetchone() is not None:
                raise StudioConflict(
                    f"作品コード '{manifest['code']}' のプロジェクトは既にあります"
                )
        project = await _insert_project(
            conn,
            manifest["name"],
            manifest["code"],
            manifest.get("synopsis", ""),
            manifest.get("world_notes", ""),
            True,
        )
        for order, asset in enumerate(manifest.get("assets", ())):
            await _insert_asset(
                conn,
                project.id,
                name=asset["name"],
                kind=asset.get("kind", "image"),
                category=asset.get("category", "reference"),
                caption=asset.get("caption", ""),
                prompt_caption=asset.get("prompt_caption", ""),
                locked=asset.get("locked", False),
                sort_order=order,
            )
        now = _now()
        shot_order = 0
        for episode_order, episode in enumerate(manifest.get("episodes", ())):
            episode_id = new_id()
            await conn.execute(
                "INSERT INTO studio_episodes"
                " (id, project_id, sort_order, title, synopsis, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    episode_id,
                    project.id,
                    episode_order,
                    episode.get("title", ""),
                    episode.get("synopsis", ""),
                    now,
                ),
            )
            for scene_order, scene in enumerate(episode.get("scenes", ())):
                scene_id = new_id()
                await conn.execute(
                    "INSERT INTO studio_scenes"
                    " (id, episode_id, project_id, sort_order, title, synopsis,"
                    "  time_of_day, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        scene_id,
                        episode_id,
                        project.id,
                        scene_order,
                        scene.get("title", ""),
                        scene.get("synopsis", ""),
                        scene.get("time_of_day", ""),
                        now,
                    ),
                )
                for shot in scene.get("shots", ()):
                    await _insert_demo_shot(
                        conn, project.id, scene_id, shot_order, shot, now
                    )
                    shot_order += 1
        await _record_revision(
            conn, project.id, "user", f"デモ『{manifest['name']}』を作成"
        )
        await conn.commit()
    detail = await project_detail(project.id)
    assert detail is not None
    return detail


async def _insert_demo_shot(
    conn: aiosqlite.Connection,
    project_id: str,
    scene_id: str,
    sort_order: int,
    shot: dict[str, Any],
    now: str,
) -> None:
    columns = ("id", "project_id", "scene_id", "sort_order", *_SHOT_TEXT_FIELDS,
               "duration_seconds", "carry_over_end_frame",
               "created_at", "updated_at", "prompt_updated_at")
    values = (
        new_id(),
        project_id,
        scene_id,
        sort_order,
        *(shot.get(name) or ("draft" if name == "status" else "")
          for name in _SHOT_TEXT_FIELDS),
        float(shot.get("duration_seconds", 5.0)),
        1 if shot.get("carry_over_end_frame") else 0,
        now,
        now,
        now,
    )
    placeholders = ", ".join("?" * len(columns))
    await conn.execute(
        f"INSERT INTO studio_shots ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )




# --------------------------------------------------------------------------
# World Bible の素材
# --------------------------------------------------------------------------

async def _fetch_assets(
    conn: aiosqlite.Connection, project_id: str
) -> list[StudioAsset]:
    async with conn.execute(
        "SELECT * FROM studio_assets WHERE project_id = ?"
        " ORDER BY sort_order, created_at, id",
        (project_id,),
    ) as cur:
        rows = await cur.fetchall()
    async with conn.execute(
        "SELECT * FROM studio_asset_files WHERE project_id = ?"
        " ORDER BY sort_order, created_at, id",
        (project_id,),
    ) as cur:
        files: dict[str, list[StudioAssetFile]] = {}
        for row in await cur.fetchall():
            reference = _row_to_asset_file(row)
            files.setdefault(reference.asset_id, []).append(reference)
    return [_row_to_asset(row, files.get(row["id"])) for row in rows]


async def _fetch_asset(
    conn: aiosqlite.Connection, asset_id: str
) -> StudioAsset | None:
    async with conn.execute(
        "SELECT * FROM studio_assets WHERE id = ?", (asset_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_asset(row, await _fetch_asset_files(conn, asset_id))


async def _fetch_asset_files(
    conn: aiosqlite.Connection, asset_id: str
) -> list[StudioAssetFile]:
    async with conn.execute(
        "SELECT * FROM studio_asset_files WHERE asset_id = ?"
        " ORDER BY sort_order, created_at, id",
        (asset_id,),
    ) as cur:
        return [_row_to_asset_file(row) for row in await cur.fetchall()]


async def get_asset(asset_id: str) -> StudioAsset | None:
    async with get_db() as conn:
        return await _fetch_asset(conn, asset_id)


async def list_asset_files(asset_id: str) -> list[StudioAssetFile] | None:
    """素材のリファレンス一覧（素材が無ければ ``None``）。"""
    async with get_db() as conn:
        if await _fetch_asset(conn, asset_id) is None:
            return None
        return await _fetch_asset_files(conn, asset_id)


async def add_asset_file(
    asset_id: str,
    *,
    role: str = "image",
    path: str,
    caption: str = "",
    actor: str = "user",
) -> StudioAssetFile:
    """素材にリファレンスを 1 本足す（ファイル実体は ``assets/`` へ複製）。

    メインのファイル（``studio_assets.path``）は触らない。役割は
    :data:`app.models.ASSET_FILE_ROLE_KINDS` の 3 つで、それぞれ画像 / 音声 /
    動画として ``assets/<kind>/`` に置かれる。
    """
    kind = ASSET_FILE_ROLE_KINDS.get(role)
    if kind is None:
        raise StudioError(
            f"リファレンスの role は {' / '.join(ASSET_FILE_ROLE_KINDS)} の"
            "いずれかで指定してください"
        )
    stored = _stored_asset_path(path, kind)
    if not stored:
        raise StudioError("リファレンスにはファイルが要ります")
    async with get_db() as conn:
        asset = await _fetch_asset(conn, asset_id)
        if asset is None:
            raise StudioError(f"素材 `{asset_id}` が見つかりません")
        file_id = new_id()
        await conn.execute(
            "INSERT INTO studio_asset_files"
            " (id, asset_id, project_id, role, path, caption, sort_order,"
            "  created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                file_id,
                asset_id,
                asset.project_id,
                role,
                stored,
                caption or "",
                await _next_sort_order(
                    conn, "studio_asset_files", "asset_id", asset_id
                ),
                _now(),
            ),
        )
        await _record_revision(
            conn,
            asset.project_id,
            actor,
            f"素材『{asset.name}』にリファレンスを追加",
        )
        await conn.commit()
        async with conn.execute(
            "SELECT * FROM studio_asset_files WHERE id = ?", (file_id,)
        ) as cur:
            row = await cur.fetchone()
    return _row_to_asset_file(row)  # type: ignore[arg-type]


async def delete_asset_file(file_id: str) -> bool:
    """リファレンスを 1 本外す（ファイル実体は ``assets/`` に残す）。"""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM studio_asset_files WHERE id = ?", (file_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
        await conn.execute(
            "DELETE FROM studio_asset_files WHERE id = ?", (file_id,)
        )
        await _record_revision(
            conn, row["project_id"], "user", "素材のリファレンスを削除"
        )
        await conn.commit()
        return True


async def add_asset(
    project_id: str,
    *,
    name: str,
    kind: str,
    path: str = "",
    category: str = "reference",
    caption: str = "",
    prompt_caption: str = "",
    profile: dict[str, Any] | None = None,
    locked: bool = False,
    sort_order: int | None = None,
    actor: str = "user",
) -> StudioAsset:
    """素材を 1 件登録する。

    ``path`` を省くと**メタデータのみの素材**になる: ファイルを持たないので
    参照には添付されず、``@名前`` は説明文へ展開される（:func:`resolve_mentions`）。
    設定だけ先に決めておきたいキャラや場所のための持ち方。

    ``path`` を渡したときは ``assets/`` の外のファイル（チャットの添付など）で
    あれば複製してから参照する（:func:`_stored_asset_path`）。
    """
    label = (name or "").strip()
    if not label:
        raise StudioError("素材の名前を入れてください")
    if "@" in label:
        # `@名前` で呼ぶので、名前そのものに @ が入っていると解決できない。
        raise StudioError("素材の名前に '@' は使えません")
    async with get_db() as conn:
        asset = await _insert_asset(
            conn,
            project_id,
            name=label,
            kind=kind,
            path=path,
            category=category,
            caption=caption,
            prompt_caption=prompt_caption,
            profile=profile,
            locked=locked,
            sort_order=sort_order,
        )
        await _record_revision(conn, project_id, actor, f"素材『{label}』を追加")
        await conn.commit()
        return asset


async def _insert_asset(
    conn: aiosqlite.Connection,
    project_id: str,
    *,
    name: str,
    kind: str,
    path: str = "",
    category: str = "reference",
    caption: str = "",
    prompt_caption: str = "",
    profile: dict[str, Any] | None = None,
    locked: bool = False,
    sort_order: int | None = None,
) -> StudioAsset:
    if await _fetch_project(conn, project_id) is None:
        raise StudioError("project not found")
    path = _stored_asset_path(str(path or ""), kind)
    profile_json = json.dumps(
        _validated_profile(category, profile or {}), ensure_ascii=False
    )
    if sort_order is None:
        sort_order = await _next_sort_order(
            conn, "studio_assets", "project_id", project_id
        )
    asset_id = new_id()
    now = _now()
    try:
        await conn.execute(
            "INSERT INTO studio_assets"
            " (id, project_id, name, category, caption, prompt_caption, profile,"
            "  kind, path, locked, sort_order, created_at, updated_at,"
            "  prompt_updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                asset_id,
                project_id,
                name,
                category,
                caption or "",
                prompt_caption or "",
                profile_json,
                kind,
                str(path or ""),
                1 if locked else 0,
                sort_order,
                now,
                now,
                now,
            ),
        )
    except aiosqlite.IntegrityError as exc:
        raise StudioError(f"素材名 '{name}' は既にあります") from exc
    return await _fetch_asset(conn, asset_id)  # type: ignore[return-value]


def _validated_profile(
    category: str, profile: dict[str, Any], *, strict: bool = True
) -> dict[str, Any]:
    """分類ごとのスキーマで拡張項目を検証する（違反は :class:`StudioError`）。"""
    try:
        return validate_asset_profile(category, profile, strict=strict)
    except ValidationError as exc:
        raise StudioError(
            f"素材の profile が {category} の形になっていません:"
            f" {_first_message(exc)}"
        ) from exc


async def update_asset(
    asset_id: str, *, actor: str = "user", **fields: Any
) -> StudioAsset | None:
    changes = {key: value for key, value in fields.items() if value is not None}
    if "name" in changes:
        changes["name"] = str(changes["name"]).strip()
        if not changes["name"]:
            raise StudioError("素材の名前を空にはできません")
        if "@" in changes["name"]:
            raise StudioError("素材の名前に '@' は使えません")
    if "locked" in changes:
        changes["locked"] = 1 if changes["locked"] else 0
    async with get_db() as conn:
        asset = await _fetch_asset(conn, asset_id)
        if asset is None:
            return None
        if "path" in changes:
            # ファイルの差し替え（``assets/`` の外にあるものは複製してから持つ）。
            changes["path"] = _stored_asset_path(
                str(changes["path"]), str(changes.get("kind") or asset.kind)
            )
        category = str(changes.get("category") or asset.category)
        if "profile" in changes:
            changes["profile"] = json.dumps(
                _validated_profile(category, changes["profile"]), ensure_ascii=False
            )
        elif category != asset.category and asset.profile:
            # 分類を付け替えたら、前の分類でだけ意味があった項目は落とす
            # （新しい分類のスキーマに無い項目を抱えたままにしない）。
            changes["profile"] = json.dumps(
                _validated_profile(category, asset.profile, strict=False),
                ensure_ascii=False,
            )
        if changes:
            now = _now()
            changes["updated_at"] = now
            if ASSET_PROMPT_FIELDS & set(changes):
                # プロンプトに効く項目が動いたときだけ進める。これより古い Take は
                # stale（作り直したほうがよい）と見なされる。
                changes["prompt_updated_at"] = now
            assignments = ", ".join(f"{key} = ?" for key in changes)
            try:
                await conn.execute(
                    f"UPDATE studio_assets SET {assignments} WHERE id = ?",
                    (*changes.values(), asset_id),
                )
            except aiosqlite.IntegrityError as exc:
                raise StudioError(
                    f"素材名 '{changes.get('name')}' は既にあります"
                ) from exc
            await _record_revision(
                conn, asset.project_id, actor, f"素材『{asset.name}』を更新"
            )
            await conn.commit()
        return await _fetch_asset(conn, asset_id)


async def delete_asset(asset_id: str) -> bool:
    """目録から外す（ファイル実体は ``assets/`` に残す）。"""
    async with get_db() as conn:
        asset = await _fetch_asset(conn, asset_id)
        if asset is None:
            return False
        await conn.execute("DELETE FROM studio_assets WHERE id = ?", (asset_id,))
        await _record_revision(
            conn, asset.project_id, "user", f"素材『{asset.name}』を削除"
        )
        await conn.commit()
        return True


# --------------------------------------------------------------------------
# Shot（脚本）
# --------------------------------------------------------------------------

async def _fetch_shots(
    conn: aiosqlite.Connection, project_id: str
) -> list[StudioShot]:
    async with conn.execute(
        "SELECT * FROM studio_shots WHERE project_id = ?"
        " ORDER BY sort_order, created_at, id",
        (project_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_shot(row) for row in rows]


async def _fetch_shot(conn: aiosqlite.Connection, shot_id: str) -> StudioShot | None:
    async with conn.execute(
        "SELECT * FROM studio_shots WHERE id = ?", (shot_id,)
    ) as cur:
        row = await cur.fetchone()
    return _row_to_shot(row) if row else None


async def get_shot(shot_id: str) -> StudioShot | None:
    async with get_db() as conn:
        return await _fetch_shot(conn, shot_id)


#: Shot のうち、文字列としてそのまま持つ欄（Create / Update で共通）
_SHOT_TEXT_FIELDS = (
    "title",
    "purpose",
    "action",
    "dialogue",
    "soundscape",
    "bgm",
    "camera",
    "prompt",
    "status",
)


#: Shot のうち、生成設定の欄（NULL 可。JobCreate へは値があるものだけ渡す）
_SHOT_SETTING_FIELDS = ("aspect_ratio", "megapixels", "seed", "workflow_override")


async def create_shot(
    project_id: str, payload: Any, *, actor: str = "user"
) -> StudioShot:
    async with get_db() as conn:
        if await _fetch_project(conn, project_id) is None:
            raise StudioError("project not found")
        scene_id = getattr(payload, "scene_id", None)
        if scene_id is not None:
            scene = await _fetch_scene(conn, scene_id)
            if scene is None or scene.project_id != project_id:
                raise StudioError("scene not found")
        sort_order = payload.sort_order
        if sort_order is None:
            sort_order = await _next_sort_order(
                conn, "studio_shots", "project_id", project_id
            )
        shot_id = new_id()
        now = _now()
        columns = ("id", "project_id", "scene_id", "sort_order", *_SHOT_TEXT_FIELDS,
                   "duration_seconds", "carry_over_end_frame",
                   *_SHOT_SETTING_FIELDS,
                   "created_at", "updated_at", "prompt_updated_at")
        values = (
            shot_id,
            project_id,
            scene_id,
            sort_order,
            *(getattr(payload, name) for name in _SHOT_TEXT_FIELDS),
            float(payload.duration_seconds),
            1 if payload.carry_over_end_frame else 0,
            *(getattr(payload, name, None) for name in _SHOT_SETTING_FIELDS),
            now,
            now,
            now,
        )
        placeholders = ", ".join("?" * len(columns))
        await conn.execute(
            f"INSERT INTO studio_shots ({', '.join(columns)})"
            f" VALUES ({placeholders})",
            values,
        )
        await _record_revision(conn, project_id, actor, "Shot を追加")
        await conn.commit()
        return await _fetch_shot(conn, shot_id)  # type: ignore[return-value]


async def update_shot(
    shot_id: str, changes: dict[str, Any], *, actor: str = "user"
) -> StudioShot | None:
    """``changes`` に入っている項目だけ書き換える。

    値が ``None`` の項目も**入っていれば** NULL を書く（採用の取り消しや、生成
    設定を既定へ戻すのに要る）。「送らなかった」と「null を送った」の区別は
    :meth:`app.models.StudioShotUpdate.changes` が済ませている。
    """
    changes = dict(changes)
    if changes.get("carry_over_end_frame") is not None:
        changes["carry_over_end_frame"] = 1 if changes["carry_over_end_frame"] else 0
    if changes.get("duration_seconds") is not None:
        changes["duration_seconds"] = float(changes["duration_seconds"])
    async with get_db() as conn:
        shot = await _fetch_shot(conn, shot_id)
        if shot is None:
            return None
        scene_id = changes.get("scene_id")
        if scene_id is not None:
            scene = await _fetch_scene(conn, str(scene_id))
            if scene is None or scene.project_id != shot.project_id:
                raise StudioError("scene not found")
        if changes:
            now = _now()
            changes["updated_at"] = now
            if PROMPT_FIELDS & set(changes):
                changes["prompt_updated_at"] = now
            assignments = ", ".join(f"{key} = ?" for key in changes)
            await conn.execute(
                f"UPDATE studio_shots SET {assignments} WHERE id = ?",
                (*changes.values(), shot_id),
            )
            await _record_revision(conn, shot.project_id, actor, "Shot を更新")
            await conn.commit()
        return await _fetch_shot(conn, shot_id)


async def delete_shot(shot_id: str, *, actor: str = "user") -> bool:
    """Take ごと消す（ON DELETE CASCADE）。ジョブと成果物は履歴に残る。"""
    async with get_db() as conn:
        shot = await _fetch_shot(conn, shot_id)
        if shot is None:
            return False
        await conn.execute("DELETE FROM studio_shots WHERE id = ?", (shot_id,))
        await _record_revision(conn, shot.project_id, actor, "Shot を削除")
        await conn.commit()
        return True


async def reorder_shots(project_id: str, shot_ids: list[str]) -> list[StudioShot]:
    """``shot_ids`` の並び順をそのまま ``sort_order`` にする。

    プロジェクトの Shot を**全件、過不足なく**並べたものを受け取る（一部だけ
    送られると残りの順序が決まらないので、その場で断る）。
    """
    async with get_db() as conn:
        if await _fetch_project(conn, project_id) is None:
            raise StudioError("project not found")
        current = {shot.id for shot in await _fetch_shots(conn, project_id)}
        wanted = list(shot_ids)
        if len(set(wanted)) != len(wanted):
            raise StudioError("shot_ids に同じ id が複数あります")
        if set(wanted) != current:
            raise StudioError(
                "shot_ids はこのプロジェクトの Shot を全件並べたものにしてください"
            )
        now = _now()
        for order, shot_id in enumerate(wanted):
            await conn.execute(
                "UPDATE studio_shots SET sort_order = ?, updated_at = ? WHERE id = ?",
                (order, now, shot_id),
            )
        await _record_revision(conn, project_id, "user", "Shot を並べ替え")
        await conn.commit()
        return await _fetch_shots(conn, project_id)


# --------------------------------------------------------------------------
# `@素材名` メンションの解決
# --------------------------------------------------------------------------

def _mention_name(prompt: str, at: int, names: list[str]) -> tuple[str, int] | None:
    """``prompt[at]`` の ``@`` に続く素材名と、メンションの終端。

    ``@{名前}`` の波括弧つきも受ける（名前に空白や記号が入る場合の逃げ道）。
    見つからなければ None。
    """
    if prompt.startswith("@{", at):
        close = prompt.find("}", at + 2)
        if close < 0:
            raise StudioError("`@{…}` の閉じ括弧がありません")
        name = prompt[at + 2:close]
        if name not in names:
            raise StudioError(f"解決できない素材メンションです: @{{{name}}}")
        return name, close + 1
    for name in names:  # 長いものから見るので、前方一致でも取り違えない
        end = at + 1 + len(name)
        if not prompt.startswith(name, at + 1):
            continue
        following = prompt[end:end + 1]
        if following and _WORD_CHAR.match(name[-1]) and _WORD_CHAR.match(following):
            continue  # 名前の途中で切れている（`@Aki` が `@Akira` を食わない）
        return name, end
    return None


def _description(asset: StudioAsset) -> str:
    """参照として添付できないときに ``@名前`` の代わりに書く説明。"""
    return asset.prompt_caption or asset.caption or asset.name


def mentioned_names(prompt: str, assets: list[StudioAsset]) -> list[str]:
    """``prompt`` が実際に呼んでいる素材の名前（解決できないものは黙って飛ばす）。

    :func:`resolve_mentions` と違って**投入しない読み取り**（stale 判定）で使う
    ので、書きかけの本文でも例外にしない。
    """
    names = sorted({asset.name for asset in assets}, key=len, reverse=True)
    found: list[str] = []
    cursor = 0
    while True:
        at = prompt.find("@", cursor)
        if at < 0:
            return found
        cursor = at + 1
        try:
            hit = _mention_name(prompt, at, names)
        except StudioError:  # `@{` の閉じ忘れなど。判定としては無視する
            continue
        if hit is not None:
            name, cursor = hit
            if name not in found:
                found.append(name)


def _unresolved_name(prompt: str, at: int) -> str:
    """エラーメッセージに出す「解決できなかったメンション」の見出し。"""
    tail = prompt[at + 1:]
    match = re.match(r"[^\s@、。,.!?！？]+", tail)
    return match.group(0) if match else ""


class Resolved(NamedTuple):
    """:func:`resolve_mentions` の結果。

    ``tags`` は ``references`` と同じ並びで、その素材を指すために**本文へ実際に
    書いたタグ**（``attach=False`` のときは空文字）。プレビューが「どの素材が
    ``<Picture 1>`` なのか」を番号の付け方を写さずに言えるようにするため、
    解決したその場で控えておく。
    """

    text: str
    references: list[StudioAsset]
    tags: list[str]


def resolve_mentions(
    prompt: str, assets: list[StudioAsset], *, attach: bool
) -> Resolved:
    """``@素材名`` を置き換えた本文と、参照として添付する素材を返す。

    ``attach=True``（r2v）では素材を参照入力に渡すので、メンションは MiniMax H3
    のタグ（``<Picture 1>`` / ``<Video 1>`` / ``<Audio 1>``）になる。番号は種類
    ごとに**添付順**で、``<Audio j>`` だけは**参照動画のサウンドトラックが先に
    番号を取る**という H3 の決まりに合わせて、参照動画の本数だけ後ろへずらす
    （:data:`app.workflows.MINIMAX_H3_R2V` の注記）。

    ``attach=False``（t2v / i2v）では参照入力そのものが無いので、代わりに素材の
    説明（``prompt_caption`` → ``caption`` → 名前）を文章として埋め込む。

    **メタデータのみの素材**（``path`` が空）は ``attach`` に関係なく必ず説明文に
    なる: 添付するファイルが無いので、タグを書いても指す先が無い。

    未登録の名前を指していたら :class:`StudioError`（ルーターが 400 にする）。
    """
    by_name = {asset.name: asset for asset in assets}
    names = sorted(by_name, key=len, reverse=True)
    attached: dict[str, list[StudioAsset]] = {"image": [], "video": [], "audio": []}
    tags: dict[str, str] = {}
    parts: list[str] = []
    cursor = 0
    while True:
        at = prompt.find("@", cursor)
        if at < 0:
            parts.append(prompt[cursor:])
            break
        parts.append(prompt[cursor:at])
        found = _mention_name(prompt, at, names)
        if found is None:
            raise StudioError(
                f"解決できない素材メンションです: @{_unresolved_name(prompt, at)}"
            )
        name, cursor = found
        asset = by_name[name]
        if not attach or not asset.path:
            parts.append(_description(asset))
            continue
        bucket = attached[asset.kind]
        if asset not in bucket:
            bucket.append(asset)
        index = bucket.index(asset) + 1
        if asset.kind == "audio":
            index += len(attached["video"])
        tag = f"<{MENTION_TAGS[asset.kind]} {index}>"
        tags.setdefault(asset.name, tag)
        parts.append(tag)
    ordered = [*attached["image"], *attached["video"], *attached["audio"]]
    return Resolved(
        "".join(parts), ordered, [tags.get(asset.name, "") for asset in ordered]
    )


# --------------------------------------------------------------------------
# プロンプトの組み立て
# --------------------------------------------------------------------------

def _audio_line(shot: StudioShot) -> str:
    """台詞・SE・BGM を H3 の ``Audio:`` 行にまとめる（何も無ければ空）。"""
    segments: list[str] = []
    if shot.soundscape.strip():
        segments.append(shot.soundscape.strip())
    if shot.bgm.strip():
        segments.append(f"music: {shot.bgm.strip()}")
    if shot.dialogue.strip():
        line = shot.dialogue.strip()
        # 台詞は二重引用符で囲むのが H3 の決まり。書き手が自分で囲っていれば
        # そのまま（話者や言い方を添えた書き方を壊さない）。
        if '"' not in line:
            line = f'"{line}"'
        segments.append(f"spoken: {line}")
    return f"Audio: {'; '.join(segments)}" if segments else ""


def compose_prompt(shot: StudioShot, body: str) -> str:
    """メンション解決済みの本文に、カメラ・音・除外指定を足した最終プロンプト。

    どの行も**本文が既に自分で書いていれば足さない**: 書き手がタイムラインごと
    仕上げたプロンプトを、欄の内容で二重に上書きしないため。
    """
    text = body.strip()
    if not text:
        raise StudioError("Shot のプロンプトが空です")
    lines = [text]
    lowered = text.lower()
    if shot.camera.strip() and "camera:" not in lowered:
        lines.append(f"Camera: {shot.camera.strip()}")
    audio = _audio_line(shot)
    if audio and "audio:" not in lowered:
        lines.append(audio)
    # 否定プロンプトが無いモデルなので、除外は本文の最後に文として書く。
    if "subtitle" not in lowered and "watermark" not in lowered:
        lines.append(EXCLUSION_SENTENCE)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 日本語 -> 英語の変換（Grok）
# --------------------------------------------------------------------------
#
# MiniMax H3 は英語プロンプト前提のモデルなので、日本語で書いた脚本はそのまま
# 投げると精度が落ちる。プロジェクトの `auto_translate` が有効なら、組み立て
# 終わった本文を Grok に「H3 用の英語プロンプト」へ直させてから投入する。
# 壊してはいけないものが 2 つあるので、指示で名指しする:
#   - `<Picture N>` / `<Video N>` / `<Audio N>`（参照素材を指すタグ）
#   - 引用符の中の台詞（H3 はこれをそのまま喋るので、原語のまま残す）

#: 変換指示のひな形。``{hint}`` に投入先ワークフローの `prompt_hint` が入る。
TRANSLATION_INSTRUCTION = (
    "You rewrite a Japanese video prompt into a single English prompt for the"
    " MiniMax H3 video+audio model. Output **only** the rewritten prompt — no"
    " preamble, no explanation, no markdown fences.\n\n"
    "Hard rules:\n"
    "- Keep every reference tag (`<Picture 1>`, `<Video 2>`, `<Audio 1>`, …)"
    " exactly as written, in the same places. Never renumber, translate or"
    " drop one, and never invent a new one.\n"
    "- Keep spoken lines inside double quotes **in their original language**"
    " (Japanese stays Japanese); translate only the words around them.\n"
    "- Keep the `Camera:` and `Audio:` lines and the closing exclusion"
    " sentence.\n"
    "- Keep the same shots, timing and length: do not add events that are not"
    " in the source.\n\n"
    "How an H3 prompt should read:\n{hint}\n\n"
    "Source prompt:\n{prompt}"
)

#: 応答が ```…``` で包まれていたときに中身だけ取り出す
_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*(.*?)\s*```$", re.DOTALL)


def has_japanese(text: str) -> bool:
    return bool(_JAPANESE.search(text or ""))


def _unfence(text: str) -> str:
    match = _FENCE.match((text or "").strip())
    return (match.group(1) if match else text).strip()


async def translate_prompt(prompt: str, workflow_id: str) -> str:
    """日本語まじりの本文を H3 用の英語プロンプトへ直す。

    :class:`app.grok.LLMError` はそのまま投げる（呼び出し側が「原文のまま投入」に
    落とす）。空の応答も同じ扱いにする。
    """
    hint = get_video_spec(workflow_id).prompt_hint
    answer = await grok.get_client().complete(
        TRANSLATION_INSTRUCTION.format(hint=hint, prompt=prompt)
    )
    translated = _unfence(answer)
    if not translated:
        raise grok.LLMError("grok が空のプロンプトを返しました")
    return translated


# --------------------------------------------------------------------------
# 投入の組み立て（生成とプレビューで唯一の経路）
# --------------------------------------------------------------------------

class _Plan(NamedTuple):
    """1 回の投入で決まるもの一式。"""

    workflow: str
    reason: str
    #: 実際に投入する本文（Camera: / Audio: 行と除外文まで込み。英訳の前）
    prompt: str
    start_image: str | None
    references: list[StudioAsset]
    #: ``references`` と同じ並びの ``<Picture 1>`` などのタグ
    tags: list[str]


async def _plan_render(
    conn: aiosqlite.Connection, shot: StudioShot, assets: list[StudioAsset]
) -> _Plan:
    """モードを決めて ``@素材名`` を解決し、最終プロンプトまで組み立てる。

    生成（:func:`render_shot`）と投入プレビュー（:func:`preview_shot`）が
    **同じものを見る**ための唯一の経路。組み立てられなければ
    :class:`StudioError`（生成は 400、プレビューは理由として見せる）。
    """
    # 添付できる（＝ファイル実体を持つ）素材を呼んでいるか。メタデータだけの
    # 素材はここに出てこないので、r2v の理由にはならない。
    mode = await _pick_workflow(
        conn, shot, bool(resolve_mentions(shot.prompt, assets, attach=True).references)
    )
    resolved = resolve_mentions(shot.prompt, assets, attach=mode.attach)
    return _Plan(
        mode.workflow,
        mode.reason,
        compose_prompt(shot, resolved.text),
        mode.start_image,
        resolved.references,
        resolved.tags,
    )


async def preview_shot(shot_id: str) -> StudioShotPreview | None:
    """この Shot を今生成したら何が投入されるか（Shot が無ければ None）。

    生成と同じ :func:`_plan_render` を通すが、**Grok の英訳は走らせない**
    （遅く、課金枠を食う）。英訳が入るかどうかは ``will_translate`` で返す。
    組み立てられないときも 200 で、理由を ``error`` に入れて返す。
    """
    async with get_db() as conn:
        shot = await _fetch_shot(conn, shot_id)
        if shot is None:
            return None
        project = await _fetch_project(conn, shot.project_id)
        assets = await _fetch_assets(conn, shot.project_id)
        auto_translate = project is not None and project.auto_translate
        try:
            plan = await _plan_render(conn, shot, assets)
        except StudioError as exc:
            return StudioShotPreview(
                shot_id=shot.id,
                workflow=shot.workflow_override,
                auto_translate=auto_translate,
                error=str(exc),
            )
    return StudioShotPreview(
        shot_id=shot.id,
        workflow=plan.workflow,
        workflow_reason=plan.reason,
        prompt=plan.prompt,
        references=[
            StudioPromptReference(
                name=asset.name, kind=asset.kind, tag=tag, path=asset.path
            )
            for asset, tag in zip(plan.references, plan.tags)
        ],
        start_frame=plan.start_image,
        auto_translate=auto_translate,
        will_translate=auto_translate and has_japanese(plan.prompt),
    )


# --------------------------------------------------------------------------
# Take（Shot の生成）
# --------------------------------------------------------------------------

def _take_status(stored: str, job: Any) -> str:
    """DB の値とジョブの状態から見せる状態を決める。

    人が決めた 'selected' / 'rejected' はそのまま。それ以外は実行状態そのもので、
    ジョブが終わっていれば候補（'candidate'）、こけていれば 'failed'。
    """
    if stored in ("selected", "rejected"):
        return stored
    if job is None:
        return "failed"
    if job.status == "done":
        return "candidate"
    if job.status in ("failed", "canceled"):
        return "failed"
    return "rendering"


async def _fetch_takes(
    conn: aiosqlite.Connection, where: str, params: tuple[Any, ...]
) -> list[StudioTake]:
    async with conn.execute(
        f"SELECT * FROM studio_takes WHERE {where} ORDER BY created_at, id", params
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return []
    job_ids = sorted({str(row["job_id"]) for row in rows})
    placeholders = ", ".join("?" * len(job_ids))
    async with conn.execute(
        f"SELECT * FROM jobs WHERE id IN ({placeholders})", tuple(job_ids)
    ) as cur:
        job_rows = await cur.fetchall()
    by_id = {
        str(row["id"]): job_service.row_to_job(row, include_workflow=False)
        for row in job_rows
    }
    takes: list[StudioTake] = []
    for row in rows:
        data = dict(row)
        job = by_id.get(str(data["job_id"]))
        data["status"] = _take_status(str(data["status"]), job)
        if job is not None:
            data["job_status"] = job.status
            data["video_workflow"] = job.params.get("video_workflow")
            data["video_path"] = job.video_path
            data["video_url"] = job.video_url
            data["last_frame_path"] = job.last_frame_path
            data["last_frame_url"] = job.last_frame_url
            data["error"] = job.error
        takes.append(StudioTake(**data))
    return takes


async def _fetch_take(conn: aiosqlite.Connection, take_id: str) -> StudioTake | None:
    takes = await _fetch_takes(conn, "id = ?", (take_id,))
    return takes[0] if takes else None


async def get_take(take_id: str) -> StudioTake | None:
    async with get_db() as conn:
        return await _fetch_take(conn, take_id)


async def list_takes(shot_id: str) -> list[StudioTake]:
    async with get_db() as conn:
        takes = await _fetch_takes(conn, "shot_id = ?", (shot_id,))
        if not takes:
            return []
        shot = await _fetch_shot(conn, shot_id)
        shots = [shot] if shot is not None else []
        assets = await _fetch_assets(conn, takes[0].project_id)
        return _mark_stale(takes, shots, assets)


# --------------------------------------------------------------------------
# stale（作り直したほうがよい Take）の導出
# --------------------------------------------------------------------------
#
# 保存はしない: 「その Take を作ったあとに、その Take の中身を決めた材料が
# 変わったか」を読み取りのたびに時刻の比較で出す。材料は 2 つだけ——
# Shot のプロンプトに効く欄（`PROMPT_FIELDS`）と、本文が呼んでいる素材。

def _mark_stale(
    takes: list[StudioTake], shots: list[StudioShot], assets: list[StudioAsset]
) -> list[StudioTake]:
    by_shot = {shot.id: shot for shot in shots}
    by_name = {asset.name: asset for asset in assets}
    mentions: dict[str, list[str]] = {}
    marked: list[StudioTake] = []
    for take in takes:
        shot = by_shot.get(take.shot_id)
        if shot is None:
            marked.append(take)
            continue
        reasons: list[str] = []
        if shot.prompt_updated_at > take.created_at:
            reasons.append("脚本が更新されました")
        if shot.id not in mentions:
            mentions[shot.id] = mentioned_names(shot.prompt, assets)
        for name in mentions[shot.id]:
            asset = by_name.get(name)
            if asset is None:
                continue
            changed = asset.prompt_updated_at or asset.updated_at
            if changed > take.created_at:
                reasons.append(f"素材『{name}』が更新されました")
        marked.append(
            take.model_copy(update={"stale": bool(reasons), "stale_reasons": reasons})
        )
    return marked


async def _carry_over_start_frame(
    conn: aiosqlite.Connection, shot: StudioShot
) -> str | None:
    """直前の Shot の採用 Take のラストフレーム（無ければ None）。

    「直前」は並び順（``sort_order`` -> ``created_at`` -> ``id``）で 1 つ手前の
    Shot。まだ採用していない・生成が終わっていないときは None を返すだけで
    エラーにはしない（先頭 Shot と同じく t2v / r2v に落ちる）。

    返すのは ``assets/image/`` に複製したほうのパス: ジョブの入力に取れるのは
    ``assets/`` と ``library/`` の中だけなので（:func:`app.jobs.resolve_asset_path`）、
    続き生成（``/api/jobs/{id}/continue``）と同じように移してから渡す。
    """
    shots = await _fetch_shots(conn, shot.project_id)
    index = next((i for i, item in enumerate(shots) if item.id == shot.id), 0)
    if index == 0:
        return None
    previous = shots[index - 1]
    if not previous.selected_take_id:
        return None
    take = await _fetch_take(conn, previous.selected_take_id)
    if take is None or not take.last_frame_path:
        return None
    return str(job_service.copy_into_assets(take.last_frame_path, "image"))


async def render_shot(shot_id: str) -> StudioTake:
    """Shot を 1 回生成してジョブに載せ、Take を返す。

    やることは 4 つ:

    1. モードを決めて ``@素材名`` を解決し、プロンプトを組み立てる
       （:func:`_plan_render`。投入プレビューと同じ経路。未登録の名前は 400）。
    2. 生成設定（画面比・解像度・シード）も Shot の値があればここで渡す。
    3. プロジェクトの ``auto_translate`` が有効で本文に日本語が混ざっていれば、
       Grok に H3 用の英語プロンプトへ直させる。直せなければ**原文のまま投入**し、
       Take の ``warning`` に理由を残す（生成そのものは止めない）。
    4. :func:`app.jobs.create_job` にそのまま渡す（HTTP は経由しない）。
    """
    async with get_db() as conn:
        shot = await _fetch_shot(conn, shot_id)
        if shot is None:
            raise StudioError("shot not found")
        project = await _fetch_project(conn, shot.project_id)
        assets = await _fetch_assets(conn, shot.project_id)
        plan = await _plan_render(conn, shot, assets)
        workflow = plan.workflow
        prompt = plan.prompt

        fields: dict[str, Any] = {
            "mode": "i2v",  # 動画ステージだけを走らせるモード（SPEC §2）
            "video_workflow": workflow,
            "video_prompt": prompt,
            "duration": float(shot.duration_seconds),
            "user_input": shot.title or None,
        }
        # Shot ごとの生成設定。**H3 が実際に受け取るものだけ**を渡す（否定
        # プロンプトは MiniMax H3 のグラフに注入先が無いので Shot には持たせない）。
        if shot.aspect_ratio:
            fields["aspect_ratio"] = shot.aspect_ratio
        if shot.megapixels is not None:
            fields["megapixels"] = float(shot.megapixels)
        if shot.seed is not None:
            fields["seed"] = int(shot.seed)
        if plan.start_image is not None:
            fields["source_image"] = plan.start_image
        for asset in plan.references:
            fields.setdefault(REFERENCE_FIELDS[asset.kind], []).append(asset.path)

    # 英訳は DB の接続を閉じてから（Grok の応答を待つあいだ掴まない）。
    source_prompt = ""
    warning = ""
    if project is not None and project.auto_translate and has_japanese(prompt):
        try:
            translated = await translate_prompt(prompt, workflow)
        except grok.LLMError as exc:
            warning = (
                "英語プロンプトへの変換ができなかったので、原文のまま投入しました"
                f"（{exc}）"
            )
        else:
            source_prompt = prompt
            prompt = translated
            fields["video_prompt"] = prompt

    # ジョブの投入も接続を閉じてから: 走り出したランナーが jobs 行を書きに来る
    # ので、読み取り用の接続を掴んだままにしない。
    try:
        payload = JobCreate(**fields)
    except ValidationError as exc:
        raise StudioError(_first_message(exc)) from exc
    try:
        job = await job_service.create_job(payload)
    except job_service.JobValidationError as exc:
        raise StudioError(str(exc)) from exc

    take_id = new_id()
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO studio_takes"
            " (id, shot_id, project_id, job_id, status, created_at,"
            "  prompt, source_prompt, warning)"
            " VALUES (?, ?, ?, ?, 'rendering', ?, ?, ?, ?)",
            (
                take_id,
                shot.id,
                shot.project_id,
                job.id,
                _now(),
                prompt,
                source_prompt,
                warning,
            ),
        )
        await conn.commit()
        return await _fetch_take(conn, take_id)  # type: ignore[return-value]


class _Mode(NamedTuple):
    """:func:`_pick_workflow` の答え（``reason`` は画面に出す日本語の理由）。"""

    workflow: str
    start_image: str | None
    attach: bool
    reason: str


async def _pick_workflow(
    conn: aiosqlite.Connection, shot: StudioShot, has_material: bool
) -> _Mode:
    """``(ワークフロー, 開始フレーム, 参照として添付するか, 理由)``。

    ``workflow_override`` があればそれに従い、足りない入力はその場で断る
    （黙って別のモードに落ちると、指定した意味が無くなるため）。
    """
    override = shot.workflow_override
    if override == WORKFLOW_T2V:
        return _Mode(WORKFLOW_T2V, None, False, "t2v を強制指定しています")
    if override == WORKFLOW_I2V:
        start_image = await _carry_over_start_frame(conn, shot)
        if start_image is None:
            raise StudioError(
                "i2v を指定していますが、開始フレームにできる直前 Shot の"
                "採用 Take がありません"
            )
        return _Mode(
            WORKFLOW_I2V, start_image, False,
            "i2v を強制指定しています（直前カットのラストフレームが開始フレーム）",
        )
    if override == WORKFLOW_R2V:
        if not has_material:
            raise StudioError(
                "r2v を指定していますが、プロンプトが参照素材（ファイルのある"
                "素材）を呼んでいません"
            )
        return _Mode(
            WORKFLOW_R2V, None, True,
            "r2v を強制指定しています（素材を参照として添付します）",
        )
    # 自動: 引き継ぎが最優先（i2v は参照素材を取れず、`<Picture 1>` が開始
    # フレームを指してしまう）、次に参照素材、どちらでもなければ t2v。
    start_image = (
        await _carry_over_start_frame(conn, shot)
        if shot.carry_over_end_frame
        else None
    )
    if start_image is not None:
        return _Mode(
            WORKFLOW_I2V, start_image, False,
            "直前カットの採用 Take のラストフレームを引き継ぎます",
        )
    if has_material:
        return _Mode(
            WORKFLOW_R2V, None, True,
            "プロンプトがファイルのある素材を呼んでいます（参照として添付）",
        )
    return _Mode(
        WORKFLOW_T2V, None, False,
        "開始フレームの引き継ぎも、ファイルのある素材の参照もありません",
    )


def _first_message(exc: ValidationError) -> str:
    """pydantic の検証エラーを 1 行のメッセージにする。"""
    for error in exc.errors():
        message = str(error.get("msg", ""))
        return message.removeprefix("Value error, ")
    return str(exc)


async def select_take(take_id: str, *, actor: str = "user") -> StudioTake | None:
    """この Take を採用する（同じ Shot の他の採用は候補へ戻す）。"""
    async with get_db() as conn:
        take = await _fetch_take(conn, take_id)
        if take is None:
            return None
        await conn.execute(
            "UPDATE studio_takes SET status = 'candidate'"
            " WHERE shot_id = ? AND status = 'selected'",
            (take.shot_id,),
        )
        await conn.execute(
            "UPDATE studio_takes SET status = 'selected' WHERE id = ?", (take_id,)
        )
        await conn.execute(
            "UPDATE studio_shots SET selected_take_id = ?, status = 'done',"
            " updated_at = ? WHERE id = ?",
            (take_id, _now(), take.shot_id),
        )
        await _record_revision(conn, take.project_id, actor, "Take を採用")
        await conn.commit()
        return await _fetch_take(conn, take_id)


async def reject_take(take_id: str, *, actor: str = "user") -> StudioTake | None:
    """この Take を不採用にする（採用中だったら Shot の採用も外す）。"""
    async with get_db() as conn:
        take = await _fetch_take(conn, take_id)
        if take is None:
            return None
        await conn.execute(
            "UPDATE studio_takes SET status = 'rejected' WHERE id = ?", (take_id,)
        )
        await conn.execute(
            "UPDATE studio_shots SET selected_take_id = NULL, status = 'ready',"
            " updated_at = ? WHERE id = ? AND selected_take_id = ?",
            (_now(), take.shot_id, take_id),
        )
        await _record_revision(conn, take.project_id, actor, "Take を不採用に")
        await conn.commit()
        return await _fetch_take(conn, take_id)


async def delete_take(take_id: str) -> bool:
    """Take を目録から外す（ジョブと成果物は履歴に残す）。"""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT shot_id, project_id FROM studio_takes WHERE id = ?", (take_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
        await conn.execute("DELETE FROM studio_takes WHERE id = ?", (take_id,))
        await conn.execute(
            "UPDATE studio_shots SET selected_take_id = NULL, updated_at = ?"
            " WHERE id = ? AND selected_take_id = ?",
            (_now(), str(row["shot_id"]), take_id),
        )
        await _record_revision(conn, str(row["project_id"]), "user", "Take を削除")
        await conn.commit()
        return True
