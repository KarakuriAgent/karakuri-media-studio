"""キャンバス: ドラマスタジオの中身を「座標を持つカード」として並べる別ビュー。

スタジオ（:mod:`app.studio`）が唯一の正で、ここが足すのは**参照レイヤー**だけ:

- **カード**（:data:`canvas_cards`）1 枚 = スタジオの 1 エンティティ。持つのは
  「どの行か」（``kind`` と ``entity_id``）と「どこに置いてあるか」
  （``x`` / ``y`` / ``w`` / ``h`` / ``z``）で、中身は持たない。カードの中身を
  直すのはスタジオの API（``/api/studio/…``）の仕事で、こちらでは重複させない。
- キャンバスは**スタジオの鏡**で、「置く / 置かない」という状態は持たない。
  読み出し（:func:`board` / :func:`list_cards`）のたびに :func:`_mirror` を
  通し、カードが無いエンティティにはその場でカードを作る。だから既にある作品を
  キャンバスで開いても白紙にならず、スタジオ側で足したものも次に開けば出る。
- 例外は **text / model** の 2 種類。対応するエンティティが無いキャンバス専用の
  カードなので、中身を ``data``（JSON）に持つ（:func:`app.models.validate_card_data`）。
- 盤面は**話（エピソード）ごとのタブ**に分かれる。どのタブに出るかは
  スタジオの所属から**導く**（場 -> その話、Shot -> 場の話、Take -> Shot の話）
  ので、カードには持たせない（:func:`card_episode`）。素材と未分類の Shot は
  話に属さないので「作品共通」タブ（``episode_id`` が ``None``）に出る。
  ``canvas_cards.episode_id`` を使うのは、導きようのない text / model カードの
  置き場所を覚えるときだけ。
- **表示位置**（viewport）はタブごとに 1 つ。作品共通はプロジェクトの列
  （``canvas_x`` / ``canvas_y`` / ``canvas_zoom``）、話タブは
  ``canvas_viewports`` の行に持つ。キャンバス専用のプロジェクトという概念は
  作らない。
- **会話**（:data:`canvas_messages`）はキャンバスのチャット履歴。ここでやるのは
  保存だけで、エージェントの実行は別に載せる。

参照先が消えたカードは DB のトリガー（:mod:`app.db` の ``trg_canvas_cards_*``）が
その場で片付けるので、スタジオ側の削除にこのモジュールが割り込む必要はない。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from pydantic import ValidationError

from . import studio
from .db import get_db
from .ids import new_id
from .models import (
    CanvasBoard,
    CanvasCard,
    CanvasCardCreate,
    CanvasChatSession,
    CanvasMessage,
    CanvasSessionSearchHit,
    CanvasViewport,
    StudioAsset,
    StudioEpisode,
    StudioEpisodeCreate,
    StudioScene,
    StudioSceneCreate,
    StudioShot,
    StudioShotCreate,
    StudioTake,
    validate_card_data,
)

#: 素材を指すカードの種別 -> ``studio_assets.category``。キャンバスでの呼び方
#: （location / object）とスタジオでの分類名（environment / prop）が違うので、
#: ここで対応づける。
CARD_CATEGORIES: dict[str, str] = {
    "character": "character",
    "location": "environment",
    "object": "prop",
    "style": "style",
    "reference": "reference",
}

#: ``studio_assets.category`` -> 素材カードの種別（:data:`CARD_CATEGORIES` の逆引き）
CATEGORY_KINDS: dict[str, str] = {
    category: kind for kind, category in CARD_CATEGORIES.items()
}

#: エンティティを持たない（キャンバス専用の）カード
STANDALONE_KINDS = ("text", "model")

#: 「作品共通」タブを指す API 上の値（``episode_id`` を省いたときと同じ）
COMMON_TAB = "common"

#: カードの既定の大きさ（:class:`app.models.CanvasCard` の既定値）
CARD_W = 320.0
CARD_H = 220.0

#: 名前を省いて素材カードを作ったときに付ける名前の頭（``@名前`` で呼ぶので
#: ASCII にしておく）
_DEFAULT_NAMES: dict[str, str] = {
    "character": "character",
    "location": "location",
    "object": "object",
    "style": "style",
    "reference": "reference",
}


class CanvasError(Exception):
    """キャンバス操作の失敗（ルーターが 400 に変換する）。"""


class CanvasConflict(CanvasError):
    """既にあるものを作ろうとした（ルーターが 409 に変換する）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_card(row: aiosqlite.Row) -> CanvasCard:
    data = dict(row)
    data["data"] = studio._load_json(data.get("data"))
    return CanvasCard(**data)


def _row_to_message(row: aiosqlite.Row) -> CanvasMessage:
    data = dict(row)
    data["data"] = studio._load_json(data.get("data"))
    data["session_id"] = data.get("session_id") or ""
    return CanvasMessage(**data)


def _validated_data(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    try:
        return validate_card_data(kind, data)
    except ValidationError as exc:
        raise CanvasError(
            f"{kind} カードの data が正しくありません: {studio._first_message(exc)}"
        ) from exc


# --------------------------------------------------------------------------
# タブ（作品共通 + 話ごと）
# --------------------------------------------------------------------------
#
# 盤面は話ごとに分かれるが、**カードは 1 つの作品に 1 組**しかない（開いて
# いないタブのカードも存在する）。どのタブに出るかはスタジオの所属から導く
# ので、話を付け替えれば次の読み込みでカードは移動先のタブに現れる。

def tab_of(episode_id: str | None) -> str | None:
    """API から来た ``episode_id`` を内部表現に直す（``'common'`` / 空 = 作品共通）。"""
    return None if episode_id in (None, "", COMMON_TAB) else episode_id


def entity_episodes(
    scenes: list[StudioScene],
    shots: list[StudioShot],
    takes: list[StudioTake],
) -> dict[str, str | None]:
    """スタジオの行 ID -> その行が属する話（``None`` = 作品共通）。

    ここに出てこない ID（素材など）も ``None`` 扱いになる。純関数なので、
    プロジェクト詳細を持っている側（エージェント）からも同じ表が作れる。
    """
    scene_episodes = {scene.id: scene.episode_id for scene in scenes}
    index: dict[str, str | None] = dict(scene_episodes)
    for shot in shots:
        index[shot.id] = scene_episodes.get(shot.scene_id) if shot.scene_id else None
    for take in takes:
        index[take.id] = index.get(take.shot_id)
    return index


def card_episode(card: CanvasCard, index: dict[str, str | None]) -> str | None:
    """そのカードが出るタブ（``None`` = 作品共通）。

    参照カードは**スタジオの所属から導く**（カードの ``episode_id`` は見ない）。
    素材は話に属さず、未分類の Shot（とその生成結果）も置き場所が無いので、
    どちらも作品共通に出る。text / model カードだけが自分の ``episode_id`` に従う。
    """
    if card.entity_id is None:
        return card.episode_id
    return index.get(card.entity_id)


def cards_in_tab(
    cards: list[CanvasCard],
    index: dict[str, str | None],
    episode_id: str | None,
) -> list[CanvasCard]:
    """そのタブに出るカードだけ（並びは元のまま）。"""
    return [card for card in cards if card_episode(card, index) == episode_id]


# --------------------------------------------------------------------------
# 読み取り
# --------------------------------------------------------------------------

async def _fetch_cards(
    conn: aiosqlite.Connection, project_id: str
) -> list[CanvasCard]:
    async with conn.execute(
        "SELECT * FROM canvas_cards WHERE project_id = ?"
        " ORDER BY z, created_at, id",
        (project_id,),
    ) as cur:
        return [_row_to_card(row) for row in await cur.fetchall()]


async def _fetch_card(conn: aiosqlite.Connection, card_id: str) -> CanvasCard | None:
    async with conn.execute(
        "SELECT * FROM canvas_cards WHERE id = ?", (card_id,)
    ) as cur:
        row = await cur.fetchone()
    return _row_to_card(row) if row else None


async def list_cards(project_id: str) -> list[CanvasCard]:
    """作品ぜんぶのカード（読むついでにスタジオの中身を映す）。

    タブで絞らないので、開いていない話のカードも入る。
    """
    async with get_db() as conn:
        cards, _index = await _mirror(conn, project_id)
        return cards


async def list_tab_cards(
    project_id: str, episode_id: str | None = None
) -> list[CanvasCard]:
    """1 タブぶんのカード（``episode_id`` が ``None`` なら作品共通）。"""
    async with get_db() as conn:
        cards, index = await _mirror(conn, project_id)
        return cards_in_tab(cards, index, episode_id)


async def get_card(card_id: str) -> CanvasCard | None:
    async with get_db() as conn:
        return await _fetch_card(conn, card_id)


async def _fetch_viewport(
    conn: aiosqlite.Connection, project_id: str, episode_id: str | None = None
) -> CanvasViewport | None:
    """タブの表示位置（プロジェクトが無ければ ``None``）。

    作品共通はプロジェクトの列に、話タブは ``canvas_viewports`` の行に持つ
    （まだ動かしていない話は原点・等倍）。
    """
    async with conn.execute(
        "SELECT canvas_x, canvas_y, canvas_zoom FROM studio_projects WHERE id = ?",
        (project_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    if episode_id is None:
        return CanvasViewport(
            x=row["canvas_x"], y=row["canvas_y"], zoom=row["canvas_zoom"] or 1.0
        )
    async with conn.execute(
        "SELECT x, y, zoom FROM canvas_viewports"
        " WHERE project_id = ? AND episode_id = ?",
        (project_id, episode_id),
    ) as cur:
        saved = await cur.fetchone()
    if saved is None:
        return CanvasViewport()
    return CanvasViewport(x=saved["x"], y=saved["y"], zoom=saved["zoom"] or 1.0)


async def board(
    project_id: str,
    episode_id: str | None = None,
    session_id: str | None = None,
) -> CanvasBoard | None:
    """キャンバス 1 タブぶん（表示位置・そのタブのカード・会話）。

    カードはここで :func:`_mirror` を通すので、スタジオにあるものは必ず
    出そろう（開いた瞬間に白紙、ということが起きない）。鏡は作品ぜんぶに
    かけたうえで、返すのは開いているタブのカードだけ。会話はセッションごと
    （省略時は更新が新しいセッション。無ければ空）。
    """
    async with get_db() as conn:
        viewport = await _fetch_viewport(conn, project_id, episode_id)
        if viewport is None:
            return None
        cards, index = await _mirror(conn, project_id)
        session = await _resolve_session(conn, project_id, session_id)
        messages = (
            await _fetch_messages(conn, project_id, session.id) if session else []
        )
        return CanvasBoard(
            project_id=project_id,
            episode_id=episode_id,
            session_id=session.id if session else None,
            viewport=viewport,
            cards=cards_in_tab(cards, index, episode_id),
            messages=messages,
        )


async def set_viewport(
    project_id: str, viewport: CanvasViewport, episode_id: str | None = None
) -> CanvasViewport | None:
    """タブの表示位置を覚える（見え方だけなのでリビジョンには残さない）。"""
    async with get_db() as conn:
        if await _fetch_viewport(conn, project_id, episode_id) is None:
            return None
        if episode_id is None:
            await conn.execute(
                "UPDATE studio_projects SET canvas_x = ?, canvas_y = ?,"
                " canvas_zoom = ? WHERE id = ?",
                (viewport.x, viewport.y, viewport.zoom, project_id),
            )
        else:
            await conn.execute(
                "INSERT INTO canvas_viewports (project_id, episode_id, x, y, zoom)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(project_id, episode_id) DO UPDATE SET"
                " x = excluded.x, y = excluded.y, zoom = excluded.zoom",
                (project_id, episode_id, viewport.x, viewport.y, viewport.zoom),
            )
        await conn.commit()
        return await _fetch_viewport(conn, project_id, episode_id)


# --------------------------------------------------------------------------
# 鏡（スタジオの中身をカードに映す）
# --------------------------------------------------------------------------
#
# 自動レイアウトは**タブごと**の格子。話タブは「場が左、その場のカットが右へ
# 1 行、生成結果はそのカットの真下」、作品共通タブは「素材が左の縦列、未分類の
# カットがその右」。エンティティ 1 件が格子の 1 マスに決まるので、何度通しても
# 同じところに落ち着く（既にあるカードは動かさない）。タブが違えば別の平面
# なので、座標がぶつかっても構わない（重なりを見るのは同じタブの中だけ）。

#: 格子 1 マス（カードの既定の大きさ + 余白）
MIRROR_GAP = 40.0
MIRROR_STEP_X = CARD_W + MIRROR_GAP
MIRROR_STEP_Y = CARD_H + MIRROR_GAP

#: 素材の縦列の並び（``studio_assets.category`` を上からこの順に）
_ASSET_ORDER: list[str] = list(CARD_CATEGORIES.values())

#: 作品共通タブ: 素材の縦列と、その右の未分類カット
_ASSET_COLUMN = 0
_LOOSE_SHOT_COLUMN = 1

#: 話タブ: 場の縦列と、その右のカット
_SCENE_COLUMN = 0
_SHOT_COLUMN = 1

#: 空きを探して下にずらす回数の上限（手で動かしたカードとの衝突よけ）
_NUDGE_LIMIT = 20


def _overlaps(spot: tuple[float, float], rects: list[tuple[float, float]]) -> bool:
    """置きたい左上（``spot``）が、既にあるカードの矩形と重なるか。"""
    return any(
        abs(x - spot[0]) < CARD_W and abs(y - spot[1]) < CARD_H for x, y in rects
    )


#: 置き場所を 1 つ決める関数（:func:`_mirror_plan` の中でタブごとに作る）:
#: ``(kind, entity_id, 列, 行)`` を受けて、そのタブの空きに 1 枚ぶん置く。
_Put = Callable[[str, str, int, int], None]


def _mirror_plan(
    assets: list[StudioAsset],
    episodes: list[StudioEpisode],
    scenes: list[StudioScene],
    shots: list[StudioShot],
    takes: list[StudioTake],
    cards: list[CanvasCard],
) -> list[tuple[str, str, float, float]]:
    """カードが無いエンティティの ``(kind, entity_id, x, y)``。

    ここは純関数（DB を触らない）にしてあり、並べ方の検証はこの結果だけを見る。
    置き場所はタブごとに独立に決める（重なりを避けるのも同じタブの中だけ）。
    """
    index = entity_episodes(scenes, shots, takes)
    placed = {card.entity_id for card in cards if card.entity_id}
    plan: list[tuple[str, str, float, float]] = []

    def putter(episode_id: str | None) -> _Put:
        """そのタブの空きに置く関数（手で動かしたカードの上には重ねない）。"""
        taken = [
            (card.x, card.y)
            for card in cards
            if card_episode(card, index) == episode_id
        ]

        def put(kind: str, entity_id: str, column: int, row: int) -> None:
            if entity_id in placed:
                return
            spot = (column * MIRROR_STEP_X, row * MIRROR_STEP_Y)
            for _ in range(_NUDGE_LIMIT):
                if not _overlaps(spot, taken):
                    break
                spot = (spot[0], spot[1] + MIRROR_STEP_Y)
            taken.append(spot)
            plan.append((kind, entity_id, spot[0], spot[1]))

        return put

    # 作品共通: 素材は左の縦列（分類ごとにまとまるよう並べ替えてから縦に積む）、
    # どの場にも入れていないカットはその右へ。
    put = putter(None)
    ordered = sorted(
        assets,
        key=lambda asset: _ASSET_ORDER.index(asset.category)
        if asset.category in _ASSET_ORDER
        else len(_ASSET_ORDER),
    )
    for row, asset in enumerate(ordered):
        kind = CATEGORY_KINDS.get(asset.category)
        if kind:
            put(kind, asset.id, _ASSET_COLUMN, row)
    loose = [shot for shot in shots if index.get(shot.id) is None]
    _plan_shot_rows(
        put, [(None, loose)], takes, placed, shot_column=_LOOSE_SHOT_COLUMN
    )

    # 話タブ: 場は左の列、その所属カットは sort_order 順に右へ、生成結果は
    # そのカットの真下。
    for episode in episodes:
        groups: list[tuple[StudioScene | None, list[StudioShot]]] = [
            (scene, [shot for shot in shots if shot.scene_id == scene.id])
            for scene in scenes
            if scene.episode_id == episode.id
        ]
        _plan_shot_rows(putter(episode.id), groups, takes, placed)
    return plan


def _plan_shot_rows(
    put: _Put,
    groups: list[tuple[StudioScene | None, list[StudioShot]]],
    takes: list[StudioTake],
    placed: set[str | None],
    *,
    scene_column: int = _SCENE_COLUMN,
    shot_column: int = _SHOT_COLUMN,
) -> None:
    """「場 -> そのカット -> カットの下に生成結果」の格子を 1 タブぶん置く。"""
    row = 0
    for scene, members in groups:
        if scene is not None:
            put("scene", scene.id, scene_column, row)
        depth = 1
        for index, shot in enumerate(members):
            column = shot_column + index
            put("shot", shot.id, column, row)
            shown = _mirrored_takes(shot, takes, placed)
            for offset, take in enumerate(shown):
                put("media", take.id, column, row + 1 + offset)
            depth = max(depth, 1 + len(shown))
        row += depth + 1


def _mirrored_takes(
    shot: StudioShot, takes: list[StudioTake], placed: set[str | None]
) -> list[StudioTake]:
    """そのカットの下に出す生成結果（古い順）。

    Take は「試した回数」なので全部出すと盤面が埋まる。出すのは**採用した
    Take**（無ければ**いちばん新しい Take**）1 件だけにして、カットごとに
    「いまの結果」が 1 枚ぶら下がる形にする。採用を差し替えたあとも前の
    カードは残る（鏡は足すだけで、勝手に降ろさない）ので、既にカードがある
    Take は古い順にそのまま並べる。
    """
    mine = [take for take in takes if take.shot_id == shot.id]
    if not mine:
        return []
    chosen = next(
        (take for take in mine if take.id == shot.selected_take_id), mine[-1]
    )
    return [take for take in mine if take.id in placed or take.id == chosen.id]


async def _mirror(
    conn: aiosqlite.Connection, project_id: str
) -> tuple[list[CanvasCard], dict[str, str | None]]:
    """カードが無いエンティティにカードを作り、``(カード, 所属の表)`` を返す。

    ユーザーの編集ではなく**派生状態の生成**なので、置き場所の更新と同じく
    リビジョン履歴には残さない。既にあるカードは動かさず、足りないぶんだけを
    自動レイアウトの位置に足す。鏡は**タブによらず作品ぜんぶ**にかける
    （開いていない話のカードも存在する）。一緒に返す表は
    :func:`entity_episodes` のもので、これを使ってタブごとに絞る。
    """
    cards = await _fetch_cards(conn, project_id)
    scenes = await studio._fetch_scenes(conn, "project_id = ?", (project_id,))
    shots = await studio._fetch_shots(conn, project_id)
    takes = await studio._fetch_takes(conn, "project_id = ?", (project_id,))
    index = entity_episodes(scenes, shots, takes)
    plan = _mirror_plan(
        await studio._fetch_assets(conn, project_id),
        await studio._fetch_episodes(conn, project_id),
        scenes,
        shots,
        takes,
        cards,
    )
    if not plan:
        return cards, index
    z = await _next_z(conn, project_id)
    now = _now()
    for offset, (kind, entity_id, x, y) in enumerate(plan):
        try:
            await conn.execute(
                "INSERT INTO canvas_cards"
                " (id, project_id, kind, entity_id, data, x, y, w, h, z,"
                "  created_at, updated_at)"
                " VALUES (?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(),
                    project_id,
                    kind,
                    entity_id,
                    x,
                    y,
                    CARD_W,
                    CARD_H,
                    z + offset,
                    now,
                    now,
                ),
            )
        except aiosqlite.IntegrityError:
            # 同時に開いた別のタブが先に映していた（鏡は何度通しても同じ）
            continue
    await conn.commit()
    return await _fetch_cards(conn, project_id), index


# --------------------------------------------------------------------------
# カードを置く
# --------------------------------------------------------------------------

async def _default_asset_name(project_id: str, kind: str) -> str:
    """``character_1`` のように、そのプロジェクトで空いている名前を作る。"""
    base = _DEFAULT_NAMES[kind]
    async with get_db() as conn:
        async with conn.execute(
            "SELECT name FROM studio_assets WHERE project_id = ?", (project_id,)
        ) as cur:
            used = {str(row["name"]) for row in await cur.fetchall()}
    index = 1
    while f"{base}_{index}" in used:
        index += 1
    return f"{base}_{index}"


async def _episode_for_scene(
    project_id: str, episode_id: str | None, actor: str
) -> str:
    """場を作る先の話。指定が無ければ先頭の話（1 つも無ければ 1 つ作る）。"""
    if episode_id is not None:
        episode = await studio.get_episode(episode_id)
        if episode is None or episode.project_id != project_id:
            raise CanvasError(f"話が見つかりません: {episode_id}")
        return episode_id
    async with get_db() as conn:
        episodes = await studio._fetch_episodes(conn, project_id)
    if episodes:
        return episodes[0].id
    # 作品共通タブから場を置いたときに「まず話を作る」を強いない
    # （話が 1 つも無い作品では、最初の話をここで作ってそこに入れる）。
    episode = await studio.create_episode(
        project_id, StudioEpisodeCreate(title="第 1 話"), actor=actor
    )
    return episode.id


async def _tab_for_card(project_id: str, episode_id: str | None) -> str | None:
    """text / model カードを置くタブ（``None`` / ``'common'`` = 作品共通）。"""
    tab = tab_of(episode_id)
    if tab is None:
        return None
    episode = await studio.get_episode(tab)
    if episode is None or episode.project_id != project_id:
        raise CanvasError(f"話が見つかりません: {tab}")
    return tab


async def _create_entity(
    project_id: str, payload: CanvasCardCreate, actor: str
) -> str:
    """カードに対応するスタジオ側の行を作り、その ID を返す。"""
    kind = payload.kind
    title = payload.title.strip()
    if kind in CARD_CATEGORIES:
        name = title or await _default_asset_name(project_id, kind)
        asset = await studio.add_asset(
            project_id,
            name=name,
            kind=payload.asset_kind,
            category=CARD_CATEGORIES[kind],
            profile=_validated_profile(kind, payload.data),
            actor=actor,
        )
        return asset.id
    if kind == "scene":
        episode_id = await _episode_for_scene(
            project_id, tab_of(payload.episode_id), actor
        )
        scene = await studio.create_scene(
            episode_id, StudioSceneCreate(title=title), actor=actor
        )
        return scene.id
    if kind == "shot":
        shot = await studio.create_shot(
            project_id,
            StudioShotCreate(title=title, scene_id=payload.scene_id),
            actor=actor,
        )
        return shot.id
    # media カードが指すのは生成結果（Take）で、Take は Shot の生成でしか
    # 生まれない。生成すれば鏡（:func:`_mirror`）が勝手に並べる。
    raise CanvasError(
        "media カードは作れません（Shot を生成すると自動でカードになります）"
    )


def _validated_profile(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """素材カードの ``data`` を、その分類の `profile` として受ける。

    素材カードは中身を持たない（スタジオの行が持つ）が、作るときだけは拡張項目を
    一緒に渡せると往復が減るので、``data`` を `profile` として通す。
    """
    if not data:
        return {}
    try:
        return studio._validated_profile(CARD_CATEGORIES[kind], data)
    except studio.StudioError as exc:
        raise CanvasError(str(exc)) from exc


async def create_card(
    project_id: str, payload: CanvasCardCreate, *, actor: str = "user"
) -> CanvasCard:
    """カードを 1 枚**新しく作る**。

    スタジオに既にあるものは鏡（:func:`_mirror`）が並べるので、ここが作るのは
    素材 / 場 / Shot を**足す**ときと、キャンバス専用の text / model カードだけ。
    参照カードは対応するスタジオ側の行も一緒に作る（:func:`_create_entity`）。

    ``actor`` はリビジョン履歴に残す主体（エージェントからの操作は ``agent``。
    一緒に作るエンティティのリビジョンにも同じ主体が付く）。
    """
    if await studio.get_project(project_id) is None:
        raise CanvasError("project not found")
    kind = payload.kind
    episode_id = None
    if kind in STANDALONE_KINDS:
        entity_id = None
        data = _validated_data(kind, payload.data)
        # キャンバス専用のカードは所属を導けないので、開いていたタブを覚える
        episode_id = await _tab_for_card(project_id, payload.episode_id)
    else:
        data = {}
        entity_id = await _create_entity(project_id, payload, actor)
    async with get_db() as conn:
        card_id = new_id()
        now = _now()
        z = await _next_z(conn, project_id)
        try:
            await conn.execute(
                "INSERT INTO canvas_cards"
                " (id, project_id, kind, entity_id, episode_id, data,"
                "  x, y, w, h, z, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    card_id,
                    project_id,
                    kind,
                    entity_id,
                    episode_id,
                    json.dumps(data, ensure_ascii=False),
                    payload.x,
                    payload.y,
                    payload.w,
                    payload.h,
                    z,
                    now,
                    now,
                ),
            )
        except aiosqlite.IntegrityError as exc:
            raise CanvasConflict(
                "このエンティティのカードは既にキャンバスにあります"
            ) from exc
        await studio._record_revision(
            conn, project_id, actor, f"キャンバスに {kind} カードを追加"
        )
        await conn.commit()
        return await _fetch_card(conn, card_id)  # type: ignore[return-value]


async def _next_z(conn: aiosqlite.Connection, project_id: str) -> int:
    """一番手前（既にあるカードの上）に置く。"""
    async with conn.execute(
        "SELECT COALESCE(MAX(z), -1) + 1 AS next FROM canvas_cards"
        " WHERE project_id = ?",
        (project_id,),
    ) as cur:
        return int((await cur.fetchone())["next"])


async def update_card(
    card_id: str, changes: dict[str, Any], *, actor: str = "user"
) -> CanvasCard | None:
    """カードの中身（キャンバス専用 kind の ``data``）と大きさを変える。

    参照カードの中身はスタジオ側にあるので、``data`` を送っても無視される
    （空のまま保つ）。``actor`` はリビジョン履歴に残す主体。
    """
    changes = {key: value for key, value in changes.items() if value is not None}
    async with get_db() as conn:
        card = await _fetch_card(conn, card_id)
        if card is None:
            return None
        if "data" in changes:
            if card.kind not in STANDALONE_KINDS:
                raise CanvasError(
                    f"{card.kind} カードの中身はスタジオの API で編集してください"
                )
            changes["data"] = json.dumps(
                _validated_data(card.kind, changes["data"]), ensure_ascii=False
            )
        if changes:
            changes["updated_at"] = _now()
            assignments = ", ".join(f"{key} = ?" for key in changes)
            await conn.execute(
                f"UPDATE canvas_cards SET {assignments} WHERE id = ?",
                (*changes.values(), card_id),
            )
            await studio._record_revision(
                conn, card.project_id, actor, "キャンバスのカードを更新"
            )
            await conn.commit()
        return await _fetch_card(conn, card_id)


async def move_card(
    card_id: str,
    x: float,
    y: float,
    w: float | None = None,
    h: float | None = None,
    z: int | None = None,
) -> CanvasCard | None:
    """置き場所だけ動かす（エンティティには触れず、リビジョンにも残さない）。

    ドラッグのたびに履歴が伸びても嬉しくないので、ここは軽い更新にとどめる。
    """
    changes: dict[str, Any] = {"x": x, "y": y}
    for key, value in (("w", w), ("h", h), ("z", z)):
        if value is not None:
            changes[key] = value
    async with get_db() as conn:
        if await _fetch_card(conn, card_id) is None:
            return None
        changes["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in changes)
        await conn.execute(
            f"UPDATE canvas_cards SET {assignments} WHERE id = ?",
            (*changes.values(), card_id),
        )
        await conn.commit()
        return await _fetch_card(conn, card_id)


async def delete_card(card_id: str, *, delete_entity: bool = False) -> bool:
    """カードを取り除く。

    参照カードは**スタジオ側の行ごと**しか消せない（``delete_entity=True``）:
    カードはスタジオの写しなので、カードだけ消しても次に開いた鏡がすぐ戻す。
    キャンバス専用の text / model カードは、そのカードだけを消す。
    """
    card = await get_card(card_id)
    if card is None:
        return False
    if card.entity_id and not delete_entity:
        raise CanvasError(
            f"{card.kind} カードはスタジオの中身の写しです。"
            "外すときは元のエンティティごと削除してください"
        )
    if delete_entity and card.entity_id:
        await _delete_entity(card.kind, card.entity_id)
    async with get_db() as conn:
        cur = await conn.execute("DELETE FROM canvas_cards WHERE id = ?", (card_id,))
        if cur.rowcount > 0:
            await studio._record_revision(
                conn, card.project_id, "user", "キャンバスからカードを削除"
            )
        await conn.commit()
    return True


async def _delete_entity(kind: str, entity_id: str) -> None:
    if kind in CARD_CATEGORIES:
        await studio.delete_asset(entity_id)
    elif kind == "scene":
        await studio.delete_scene(entity_id)
    elif kind == "shot":
        await studio.delete_shot(entity_id)
    elif kind == "media":
        await studio.delete_take(entity_id)


# --------------------------------------------------------------------------
# 会話セッション
# --------------------------------------------------------------------------

SESSION_SEARCH_LIMIT = 20
SESSION_READ_LIMIT = 40


def _row_to_session(row: aiosqlite.Row, *, preview: str = "") -> CanvasChatSession:
    return CanvasChatSession(
        id=row["id"],
        project_id=row["project_id"],
        title=row["title"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        preview=preview,
        grok_session_id=row["grok_session_id"] or "",
        grok_cwd=row["grok_cwd"] or "",
        snapshot_key=row["snapshot_key"] or "",
    )


async def _fetch_session(
    conn: aiosqlite.Connection, session_id: str
) -> CanvasChatSession | None:
    async with conn.execute(
        "SELECT * FROM canvas_sessions WHERE id = ?", (session_id,)
    ) as cur:
        row = await cur.fetchone()
    return _row_to_session(row) if row else None


async def _latest_session(
    conn: aiosqlite.Connection, project_id: str
) -> CanvasChatSession | None:
    async with conn.execute(
        "SELECT * FROM canvas_sessions WHERE project_id = ?"
        " ORDER BY updated_at DESC, created_at DESC, id DESC LIMIT 1",
        (project_id,),
    ) as cur:
        row = await cur.fetchone()
    return _row_to_session(row) if row else None


async def _resolve_session(
    conn: aiosqlite.Connection, project_id: str, session_id: str | None
) -> CanvasChatSession | None:
    if session_id:
        session = await _fetch_session(conn, session_id)
        if session is None or session.project_id != project_id:
            return None
        return session
    return await _latest_session(conn, project_id)


async def get_session(
    project_id: str, session_id: str
) -> CanvasChatSession | None:
    async with get_db() as conn:
        return await _resolve_session(conn, project_id, session_id)


async def latest_session(project_id: str) -> CanvasChatSession | None:
    async with get_db() as conn:
        return await _latest_session(conn, project_id)


async def ensure_session(
    project_id: str, session_id: str | None = None, *, title: str = ""
) -> CanvasChatSession:
    """指定セッション、または最新。無ければ空のセッションを 1 本作る。"""
    async with get_db() as conn:
        if await _fetch_viewport(conn, project_id) is None:
            raise CanvasError("project not found")
        if session_id:
            session = await _resolve_session(conn, project_id, session_id)
            if session is None:
                raise CanvasError("session not found")
            return session
        existing = await _latest_session(conn, project_id)
        if existing is not None:
            return existing
        return await _insert_session(conn, project_id, title=title or "チャット")


async def _insert_session(
    conn: aiosqlite.Connection,
    project_id: str,
    *,
    title: str = "",
    grok_cwd: str = "",
) -> CanvasChatSession:
    now = _now()
    session_id = new_id()
    from .agent_store import session_dir

    cwd = grok_cwd or str(session_dir(f"canvas-sess-{session_id}"))
    await conn.execute(
        "INSERT INTO canvas_sessions"
        " (id, project_id, title, created_at, updated_at, grok_cwd)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, project_id, title, now, now, cwd),
    )
    await conn.commit()
    session = await _fetch_session(conn, session_id)
    assert session is not None
    return session


async def create_session(project_id: str, title: str = "") -> CanvasChatSession:
    async with get_db() as conn:
        if await _fetch_viewport(conn, project_id) is None:
            raise CanvasError("project not found")
        return await _insert_session(conn, project_id, title=title)


async def list_sessions(project_id: str) -> list[CanvasChatSession]:
    async with get_db() as conn:
        if await _fetch_viewport(conn, project_id) is None:
            raise CanvasError("project not found")
        async with conn.execute(
            "SELECT * FROM canvas_sessions WHERE project_id = ?"
            " ORDER BY updated_at DESC, created_at DESC, id DESC",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
        sessions: list[CanvasChatSession] = []
        for row in rows:
            preview = await _session_preview(conn, row["id"])
            sessions.append(_row_to_session(row, preview=preview))
        return sessions


async def _session_preview(conn: aiosqlite.Connection, session_id: str) -> str:
    async with conn.execute(
        "SELECT content FROM canvas_messages WHERE session_id = ?"
        " AND role != 'system' ORDER BY ts DESC, id DESC LIMIT 1",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None or not (row["content"] or "").strip():
        return ""
    return row["content"].strip().splitlines()[0][:80]


async def update_session(
    project_id: str, session_id: str, *, title: str
) -> CanvasChatSession | None:
    async with get_db() as conn:
        session = await _resolve_session(conn, project_id, session_id)
        if session is None:
            return None
        await conn.execute(
            "UPDATE canvas_sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), session_id),
        )
        await conn.commit()
        return await _fetch_session(conn, session_id)


async def update_session_grok(
    session_id: str,
    *,
    grok_session_id: str | None = None,
    grok_cwd: str | None = None,
    snapshot_key: str | None = None,
) -> None:
    fields: dict[str, str] = {}
    if grok_session_id is not None:
        fields["grok_session_id"] = grok_session_id
    if grok_cwd is not None:
        fields["grok_cwd"] = grok_cwd
    if snapshot_key is not None:
        fields["snapshot_key"] = snapshot_key
    if not fields:
        return
    assignments = ", ".join(f"{key} = ?" for key in fields)
    async with get_db() as conn:
        await conn.execute(
            f"UPDATE canvas_sessions SET {assignments} WHERE id = ?",
            (*fields.values(), session_id),
        )
        await conn.commit()


async def delete_session(project_id: str, session_id: str) -> CanvasChatSession | None:
    async with get_db() as conn:
        session = await _resolve_session(conn, project_id, session_id)
        if session is None:
            return None
        await conn.execute(
            "DELETE FROM canvas_messages WHERE session_id = ?", (session_id,)
        )
        await conn.execute("DELETE FROM canvas_sessions WHERE id = ?", (session_id,))
        await conn.commit()
        return session


async def search_sessions(
    project_id: str,
    query: str,
    *,
    exclude_id: str | None = None,
    limit: int = SESSION_SEARCH_LIMIT,
    offset: int = 0,
) -> tuple[list[CanvasSessionSearchHit], int]:
    """同じ作品のセッションをタイトル・本文 LIKE で探す。"""
    q = (query or "").strip()
    like = f"%{q}%"
    async with get_db() as conn:
        if await _fetch_viewport(conn, project_id) is None:
            raise CanvasError("project not found")
        params: list[Any] = [project_id]
        where = "s.project_id = ?"
        if q:
            where += " AND (s.title LIKE ? OR m.content LIKE ?)"
            params += [like, like]
        if exclude_id:
            where += " AND s.id != ?"
            params.append(exclude_id)
        count_sql = (
            "SELECT COUNT(DISTINCT s.id) AS n FROM canvas_sessions s"
            " LEFT JOIN canvas_messages m ON m.session_id = s.id"
            f" WHERE {where}"
        )
        async with conn.execute(count_sql, params) as cur:
            total = int((await cur.fetchone())["n"])
        sql = (
            "SELECT s.id AS session_id, s.title AS title,"
            " COALESCE(m.content, '') AS snippet, COALESCE(m.ts, s.updated_at) AS ts"
            " FROM canvas_sessions s"
            " LEFT JOIN canvas_messages m ON m.session_id = s.id"
            f" WHERE {where}"
            " ORDER BY COALESCE(m.ts, s.updated_at) DESC, s.id DESC"
            " LIMIT ? OFFSET ?"
        )
        async with conn.execute(sql, [*params, limit, offset]) as cur:
            rows = await cur.fetchall()
    hits: list[CanvasSessionSearchHit] = []
    seen: set[str] = set()
    for row in rows:
        sid = row["session_id"]
        if sid in seen:
            continue
        seen.add(sid)
        snippet = (row["snippet"] or "").replace("\n", " ").strip()
        if q:
            idx = snippet.lower().find(q.lower())
            if idx >= 0:
                start = max(0, idx - 20)
                snippet = snippet[start : start + 120]
            else:
                snippet = snippet[:120]
        else:
            snippet = snippet[:120]
        hits.append(
            CanvasSessionSearchHit(
                session_id=sid,
                title=row["title"] or "",
                snippet=snippet,
                ts=row["ts"] or "",
            )
        )
    return hits, total


async def read_session_transcript(
    session_id: str,
    *,
    project_id: str | None = None,
    exclude_id: str | None = None,
    offset: int = 0,
    limit: int = SESSION_READ_LIMIT,
) -> tuple[CanvasChatSession | None, list[CanvasMessage], int, str | None]:
    """同じ作品の他セッション本文。失敗は例外にせず error 文字列で返す。"""
    if exclude_id and session_id == exclude_id:
        return None, [], 0, "今の会話自身は読めない"
    async with get_db() as conn:
        session = await _fetch_session(conn, session_id)
        if session is None:
            return None, [], 0, "セッションが見つからない"
        if project_id and session.project_id != project_id:
            return None, [], 0, "この作品のものではない"
        messages = await _fetch_messages(conn, session.project_id, session_id)
    visible = [message for message in messages if message.role != "system"]
    start = max(0, offset)
    return session, visible[start : start + limit], len(visible), None


# --------------------------------------------------------------------------
# 会話
# --------------------------------------------------------------------------

async def _fetch_messages(
    conn: aiosqlite.Connection, project_id: str, session_id: str | None = None
) -> list[CanvasMessage]:
    if session_id:
        async with conn.execute(
            "SELECT * FROM canvas_messages WHERE session_id = ? ORDER BY ts, id",
            (session_id,),
        ) as cur:
            return [_row_to_message(row) for row in await cur.fetchall()]
    async with conn.execute(
        "SELECT * FROM canvas_messages WHERE project_id = ? ORDER BY ts, id",
        (project_id,),
    ) as cur:
        return [_row_to_message(row) for row in await cur.fetchall()]


async def list_messages(
    project_id: str, session_id: str | None = None
) -> list[CanvasMessage]:
    async with get_db() as conn:
        if session_id is None:
            session = await _latest_session(conn, project_id)
            session_id = session.id if session else None
            if session_id is None:
                return []
        return await _fetch_messages(conn, project_id, session_id)


async def append_message(
    project_id: str,
    role: str,
    content: str,
    *,
    session_id: str | None = None,
    kind: str | None = None,
    data: dict[str, Any] | None = None,
) -> CanvasMessage:
    message_id = new_id()
    now = _now()
    async with get_db() as conn:
        if await _fetch_viewport(conn, project_id) is None:
            raise CanvasError("project not found")
        session = await _resolve_session(conn, project_id, session_id)
        if session is None:
            if session_id:
                raise CanvasError("session not found")
            session = await _insert_session(
                conn, project_id, title=_title_from_content(content) if role == "user" else "チャット"
            )
        await conn.execute(
            "INSERT INTO canvas_messages"
            " (id, project_id, session_id, ts, role, content, kind, data)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                project_id,
                session.id,
                now,
                role,
                content,
                kind,
                json.dumps(data or {}, ensure_ascii=False),
            ),
        )
        title = session.title
        if role == "user" and (not title or title == "チャット"):
            derived = _title_from_content(content)
            if derived:
                title = derived
        await conn.execute(
            "UPDATE canvas_sessions SET updated_at = ?, title = ? WHERE id = ?",
            (now, title, session.id),
        )
        await conn.commit()
        async with conn.execute(
            "SELECT * FROM canvas_messages WHERE id = ?", (message_id,)
        ) as cur:
            row = await cur.fetchone()
    return _row_to_message(row)  # type: ignore[arg-type]


def _title_from_content(content: str) -> str:
    line = (content or "").strip().splitlines()[0] if content else ""
    return line[:40]
