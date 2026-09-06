"""ブロッキング動画（SPEC §7.2）: 原始形状だけの 3D シーンを mp4 にする。

MiniMax H3 の r2v に「構図とカメラワークだけ」を渡したいことがある——誰が画面の
どこに立ち、カメラがどう動くか。それを言葉で書き切るのは難しいので、四角・丸・
円柱・簡易人型だけで組んだ**プレビズ（ブロッキング）動画**を作り、
``<Video k> (camera path and blocking only): weak_reference`` として添える。
見た目・色・形は再現させない（:func:`reference_note`）。

描き方は **numpy も OpenGL も使わない自前のソフトウェアラスタライザ**:

1. オブジェクトを凸なパーツ（箱・球・円柱・カプセル）の集合として持つ
2. 透視投影の手前でビュー座標に落とし、面の法線を**パーツの重心から外向き**に
   揃えてから背面カリング（巻き順の間違いで裏返らない）
3. 面ごとの奥行きで奥から手前に並べ替え（painter's algorithm）、固定方向光の
   平面シェーディングを掛けて :meth:`PIL.ImageDraw.ImageDraw.polygon` で塗る

原始形状は交差しない前提なので、面単位のソートで十分（交差すると前後が乱れる）。
ネイティブ依存を増やさないのが狙いで、Docker でも headless で確実に動く。

座標は**メートル・Y 上・床が y=0**、オブジェクトの ``position`` は**底面中心**、
角度は度。DB もライブラリ登録も知らない純粋モジュールで、登録は
:func:`app.library.add_blocking`。
"""

from __future__ import annotations

import math
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw

from .models import (
    BlockingCamera,
    BlockingCameraKeyframe,
    BlockingObject,
    BlockingObjectKeyframe,
    BlockingObjectScreen,
    BlockingPositions,
    BlockingScene,
    BlockingScreenPoint,
)

#: 書き出す fps。H3 の参照動画は 24fps 前提なので**固定**にする
#: （尺 × 24 がそのままフレーム数になり、カット時刻の計算がずれない）
FPS = 24

#: 長辺の px 数。H3 のネイティブキャンバスが短辺 768 なので、参照動画も
#: それに合わせておけば縮小で情報が落ちない（これ以上大きくしても意味がない）
LONG_EDGE = 768

#: 解像度を丸める単位（px）。動画コーデックとモデル側の都合で 32 の倍数に揃える
SIZE_STEP = 32

#: 描画してから縮小する倍率（擬似アンチエイリアス）。3 以上は時間だけ増えて
#: 見た目が変わらない
SUPERSAMPLE = 2

#: 尺の下限・上限（秒）。上限は「構図の参照は 1 カットぶんで足りる」から
#: （H3 のカット尺の上限 15 秒よりさらに短く、レンダ時間を実用範囲に抑える）
MIN_DURATION = 0.5
MAX_DURATION = 10.0

#: 1 シーンに置けるオブジェクトの数。これ以上は画面の中で識別できず、
#: location_map も読めない長さになる
MAX_OBJECTS = 20

#: カメラ・オブジェクト 1 つあたりのキーフレーム数。24fps・10 秒なら 240 コマ
#: なので、32 点あれば「1 秒に 3 回向きが変わる」程度まで書ける
MAX_KEYFRAMES = 32

#: 座標の範囲（m）。これ以上遠いものは画面に意味を持たない
MAX_COORD = 200.0

#: オブジェクト 1 辺の下限・上限（m）
MIN_SIZE = 0.01
MAX_SIZE = 50.0

#: 画角（対角。H3 のプロンプトガイドが使う "47 degree diagonal field of view" と
#: 同じ意味）の範囲と既定
MIN_FOV = 20.0
MAX_FOV = 110.0
DEFAULT_FOV = 47.0

#: カメラプリセットの移動量の上限（m / 度）
MAX_MOVE_METERS = 50.0
MAX_MOVE_DEGREES = 180.0

#: ニアクリップ面（m）。これより手前の頂点は多角形ごと切る
NEAR = 0.05

#: 床グリッドの広がり（m。原点から ±この距離まで 1m 間隔）
GRID_EXTENT = 12

#: 使えるアスペクト比 -> (横, 縦) の比
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:3": (4, 3),
    "3:4": (3, 4),
    "21:9": (21, 9),
}

#: 形状
SHAPES: tuple[str, ...] = ("box", "sphere", "cylinder", "capsule", "figure")

#: 形状ごとの既定の大きさ（m。[幅, 高さ, 奥行き]）。球・円柱・カプセルは
#: 幅を直径として使う
DEFAULT_SIZES: dict[str, tuple[float, float, float]] = {
    "box": (0.6, 0.6, 0.6),
    "sphere": (0.5, 0.5, 0.5),
    "cylinder": (0.5, 1.0, 0.5),
    "capsule": (0.5, 1.7, 0.5),
    "figure": (0.45, 1.7, 0.3),
}

#: 向きの自動モード
FACINGS: tuple[str, ...] = ("keyframe", "camera", "path")

#: キーフレーム間の補間
EASINGS: tuple[str, ...] = ("linear", "ease_in_out")

#: カメラプリセット
MOVES: tuple[str, ...] = (
    "static", "push_in", "pull_out", "pan_left", "pan_right",
    "tilt_up", "tilt_down", "truck_left", "truck_right",
    "arc_left", "arc_right", "follow",
)

#: 距離（m）で指定するプリセット。残りは角度（度）
MOVES_IN_METERS: tuple[str, ...] = (
    "push_in", "pull_out", "truck_left", "truck_right",
)

#: ``target``（注視対象のオブジェクト id）が要るプリセット
MOVES_NEEDING_TARGET: tuple[str, ...] = ("follow",)

#: 背景・床・地平線の色。**低彩度に固定**する: H3 に余計な意味
#: （色・素材・光）を拾わせず、位置とカメラの動きだけを読ませるため
DEFAULT_BACKGROUND = "#e6e6e6"
FLOOR_COLOR = "#c9c9c9"
FLOOR_ACCENT_COLOR = "#b4b4b4"
HORIZON_COLOR = "#9a9a9a"

#: オブジェクトの既定色（無彩色のマネキン）
DEFAULT_OBJECT_COLOR = "#9a9a9a"

#: 平面シェーディングの環境光（0..1）と、光が進む向き（左上手前から）
AMBIENT = 0.45
LIGHT_DIRECTION = (-0.35, -1.0, -0.45)

#: 球・円柱の分割数（UV 分割）。増やすほど滑らかだが面数がそのまま重さになる
SPHERE_SEGMENTS = 16
SPHERE_RINGS = 12
CYLINDER_SEGMENTS = 20

#: ffmpeg の呼び出し名（:mod:`app.jobs` と同じ流儀でテストが差し替える）
FFMPEG = "ffmpeg"

#: mp4 の書き出しに許す秒数
ENCODE_TIMEOUT = 300.0

#: id に使える文字（英数字と _）
_ID = re.compile(r"^[A-Za-z0-9_]{1,40}$")

Vec = tuple[float, float, float]


class BlockingError(Exception):
    """シーン定義が不正 / レンダに失敗した（呼び出し側が 400 にする）。"""


# --------------------------------------------------------------------------
# ベクトルの小道具（3 要素のタプルだけで完結させる）
# --------------------------------------------------------------------------

def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a: Vec, k: float) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(a: Vec) -> float:
    return math.sqrt(_dot(a, a))


def _normalize(a: Vec, fallback: Vec = (0.0, 0.0, -1.0)) -> Vec:
    norm = _length(a)
    return fallback if norm < 1e-9 else _scale(a, 1.0 / norm)


def _rotate_about(v: Vec, axis: Vec, degrees: float) -> Vec:
    """``axis`` まわりに ``degrees`` 回した ``v``（ロドリゲスの回転公式）。"""
    angle = math.radians(degrees)
    unit = _normalize(axis, (0.0, 1.0, 0.0))
    cos, sin = math.cos(angle), math.sin(angle)
    return _add(
        _add(_scale(v, cos), _scale(_cross(unit, v), sin)),
        _scale(unit, _dot(unit, v) * (1.0 - cos)),
    )


def _rotate_yaw(v: Vec, degrees: float) -> Vec:
    """Y 軸まわりの回転（``+`` で左へ向く）。"""
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    return (v[0] * cos + v[2] * sin, v[1], -v[0] * sin + v[2] * cos)


def _lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


def _lerp_vec(a: Vec, b: Vec, u: float) -> Vec:
    return (_lerp(a[0], b[0], u), _lerp(a[1], b[1], u), _lerp(a[2], b[2], u))


def _ease(u: float, easing: str) -> float:
    """0..1 の進み具合に補間曲線を掛ける（``ease_in_out`` は smoothstep）。"""
    clamped = min(1.0, max(0.0, u))
    if easing == "linear":
        return clamped
    return clamped * clamped * (3.0 - 2.0 * clamped)


# --------------------------------------------------------------------------
# 検証と正規化（ここを通ったシーンだけを描く）
# --------------------------------------------------------------------------

def check_color(value: object, *, what: str = "color") -> tuple[int, int, int]:
    """色（CSS 表記）を RGB に直す（:mod:`app.sprites` と同じ流儀）。"""
    text = str(value or "").strip()
    if not text:
        raise BlockingError(f"{what} が空です")
    try:
        return ImageColor.getrgb(text)[:3]
    except ValueError as exc:
        raise BlockingError(f"{what} を解釈できません: {text}") from exc


def _number(value: object, what: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise BlockingError(f"{what} が数値ではありません: {value!r}") from exc
    if not math.isfinite(number):
        raise BlockingError(f"{what} が数値ではありません: {value!r}")
    return number


def _check_point(value: object, what: str) -> Vec:
    """``[x, y, z]``（m）を検証する。"""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise BlockingError(f"{what} は [x, y, z] の 3 要素で指定してください")
    point = tuple(_number(part, what) for part in value)
    if any(abs(part) > MAX_COORD for part in point):
        raise BlockingError(
            f"{what} は ±{MAX_COORD:.0f}m の範囲で指定してください（{list(value)}）"
        )
    return point  # type: ignore[return-value]


def _check_choice(value: object, allowed: tuple[str, ...], what: str) -> str:
    text = str(value or "").strip()
    if text not in allowed:
        raise BlockingError(
            f"unknown {what} '{text}' (allowed: {', '.join(allowed)})"
        )
    return text


def _check_times(times: list[float], count: int, what: str, duration: float) -> None:
    """キーフレームの件数と ``t`` の並びを検証する。"""
    if not 1 <= count <= MAX_KEYFRAMES:
        raise BlockingError(
            f"{what} のキーフレームは 1〜{MAX_KEYFRAMES} 件です（{count} 件）"
        )
    for index, value in enumerate(times):
        if not 0.0 <= value <= duration + 1e-6:
            raise BlockingError(
                f"{what} の t は 0〜{duration:g} 秒の範囲で指定してください"
                f"（{value:g}）"
            )
        if index and value <= times[index - 1] + 1e-6:
            raise BlockingError(f"{what} の t は昇順で、同じ時刻は置けません")


def resolution(aspect_ratio: str) -> tuple[int, int]:
    """アスペクト比から出力の ``(幅, 高さ)``（長辺 :data:`LONG_EDGE`）。

    短辺は :data:`SIZE_STEP` の倍数へ丸めるので、比はきっかりにはならない
    （16:9 なら 768x448）。参照動画として見るぶんには支障がなく、
    エンコーダとモデル側の都合を優先する。
    """
    ratio = ASPECT_RATIOS.get(aspect_ratio)
    if ratio is None:
        raise BlockingError(
            f"unknown aspect_ratio '{aspect_ratio}'"
            f" (allowed: {', '.join(ASPECT_RATIOS)})"
        )
    wide, tall = ratio
    if wide >= tall:
        width = LONG_EDGE
        height = max(SIZE_STEP, round(LONG_EDGE * tall / wide / SIZE_STEP) * SIZE_STEP)
    else:
        height = LONG_EDGE
        width = max(SIZE_STEP, round(LONG_EDGE * wide / tall / SIZE_STEP) * SIZE_STEP)
    return width, height


def frame_count(duration: float) -> int:
    """尺（秒）に対して書き出すコマ数（24fps 固定）。"""
    return max(1, round(duration * FPS))


def prepare_scene(
    scene: BlockingScene, *, expand_move: bool = True
) -> BlockingScene:
    """検証して**描ける形**に正規化したシーンを返す（元は触らない）。

    * 大きさの省略を形状ごとの既定（:data:`DEFAULT_SIZES`）で埋める
    * ``camera.move``（プリセット）を ``camera.keyframes`` へ展開する
    * 上限・並び・色・id を検証し、外れていれば :class:`BlockingError`

    同じシーンに 2 回掛けても結果は変わらない（プリセットの展開は先頭の
    キーフレームだけを起点にするため）。

    ``expand_move=False`` にすると ``camera.move`` を展開せず、書かれた
    ``camera.keyframes`` のまま返す（検証と既定値の穴埋めだけ）。展開結果は
    **描くためだけの一時的な形**なので、DB に残すシーン定義はこちらで作る
    （:func:`app.library.add_blocking`）: 保存してしまうと、次に開いたときに
    プリセットの起点が展開後の 2 点に化けてしまう。
    """
    prepared = scene.model_copy(deep=True)

    duration = _number(prepared.duration, "duration")
    if not MIN_DURATION <= duration <= MAX_DURATION:
        raise BlockingError(
            f"duration は {MIN_DURATION:g}〜{MAX_DURATION:g} 秒で指定してください"
            f"（{duration:g}）"
        )
    prepared.duration = duration
    resolution(prepared.aspect_ratio)  # 未知の比はここで 400
    check_color(prepared.background.color, what="background.color")

    if not 1 <= len(prepared.objects) <= MAX_OBJECTS:
        raise BlockingError(
            f"objects は 1〜{MAX_OBJECTS} 件で指定してください"
            f"（{len(prepared.objects)} 件）"
        )

    seen: set[str] = set()
    for obj in prepared.objects:
        if not _ID.match(obj.id or ""):
            raise BlockingError(
                f"オブジェクトの id '{obj.id}' は英数字と _ で 1〜40 文字です"
            )
        if obj.id in seen:
            raise BlockingError(f"オブジェクトの id が重複しています: {obj.id}")
        seen.add(obj.id)
        _check_choice(obj.shape, SHAPES, f"shape（{obj.id}）")
        _check_choice(obj.facing, FACINGS, f"facing（{obj.id}）")
        check_color(obj.color, what=f"color（{obj.id}）")
        obj.size = list(_check_size(obj))
        _check_times(
            [keyframe.t for keyframe in obj.keyframes],
            len(obj.keyframes),
            f"objects[{obj.id}]",
            duration,
        )
        for keyframe in obj.keyframes:
            keyframe.position = list(
                _check_point(keyframe.position, f"objects[{obj.id}].position")
            )
            keyframe.yaw_deg = _number(keyframe.yaw_deg, "yaw_deg")

    camera = prepared.camera
    _check_choice(camera.easing, EASINGS, "easing")
    _check_times(
        [keyframe.t for keyframe in camera.keyframes],
        len(camera.keyframes),
        "camera",
        duration,
    )
    for keyframe in camera.keyframes:
        keyframe.position = list(_check_point(keyframe.position, "camera.position"))
        keyframe.look_at = list(_check_point(keyframe.look_at, "camera.look_at"))
        keyframe.fov_deg = _number(keyframe.fov_deg, "fov_deg")
        if not MIN_FOV <= keyframe.fov_deg <= MAX_FOV:
            raise BlockingError(
                f"fov_deg は {MIN_FOV:g}〜{MAX_FOV:g} 度で指定してください"
                f"（{keyframe.fov_deg:g}）"
            )
        keyframe.roll_deg = _number(keyframe.roll_deg, "roll_deg")
    if camera.keyframes[0].t > 1e-6:
        raise BlockingError("camera.keyframes には t=0 を含めてください")
    if _length(
        _sub(_as_vec(camera.keyframes[0].look_at), _as_vec(camera.keyframes[0].position))
    ) < 1e-6:
        raise BlockingError("camera の position と look_at が同じ点です")

    if expand_move and camera.move is not None:
        camera.keyframes = _expand_move(prepared, camera.move)
    return prepared


def _check_size(obj: BlockingObject) -> tuple[float, float, float]:
    """大きさ（省略時は形状ごとの既定）を検証する。"""
    if obj.size is None:
        return DEFAULT_SIZES[obj.shape]
    if not isinstance(obj.size, (list, tuple)) or len(obj.size) != 3:
        raise BlockingError(
            f"size（{obj.id}）は [w, h, d] の 3 要素で指定してください"
        )
    size = tuple(_number(part, f"size（{obj.id}）") for part in obj.size)
    if any(not MIN_SIZE <= part <= MAX_SIZE for part in size):
        raise BlockingError(
            f"size（{obj.id}）は {MIN_SIZE:g}〜{MAX_SIZE:g}m で指定してください"
            f"（{list(obj.size)}）"
        )
    return size  # type: ignore[return-value]


def _as_vec(value: list[float] | tuple[float, ...]) -> Vec:
    return (float(value[0]), float(value[1]), float(value[2]))


def find_object(scene: BlockingScene, object_id: str) -> BlockingObject:
    for obj in scene.objects:
        if obj.id == object_id:
            return obj
    raise BlockingError(f"オブジェクトが見つかりません: {object_id}")


# --------------------------------------------------------------------------
# カメラプリセットの展開
# --------------------------------------------------------------------------

def _expand_move(scene: BlockingScene, move) -> list[BlockingCameraKeyframe]:
    """``camera.move`` を 2 点（follow は複数点）のキーフレームに展開する。

    起点は ``keyframes[0]``（そこに書いた位置・注視点・画角・ロールを使う）。
    ``amount`` は push / pull / truck が距離（m）、pan / tilt / arc が角度（度）。
    """
    kind = _check_choice(move.type, MOVES, "camera.move.type")
    amount = _number(move.amount, "camera.move.amount")
    limit = MAX_MOVE_METERS if kind in MOVES_IN_METERS else MAX_MOVE_DEGREES
    if abs(amount) > limit:
        unit = "m" if kind in MOVES_IN_METERS else "度"
        raise BlockingError(
            f"camera.move.amount は ±{limit:g}{unit} までです（{amount:g}）"
        )
    if kind in MOVES_NEEDING_TARGET and not (move.target or "").strip():
        raise BlockingError(f"camera.move.type '{kind}' には target が要ります")

    base = scene.camera.keyframes[0]
    position = _as_vec(base.position)
    look_at = _as_vec(base.look_at)
    duration = float(scene.duration)
    forward = _normalize(_sub(look_at, position))
    right = _normalize(_cross(forward, (0.0, 1.0, 0.0)), (1.0, 0.0, 0.0))

    if kind == "follow":
        return _expand_follow(scene, base, move.target or "")

    start_look_at = look_at
    if kind == "static":
        end_position, end_look_at = position, look_at
    elif kind in ("push_in", "pull_out"):
        travel = amount if kind == "push_in" else -amount
        end_position = _add(position, _scale(forward, travel))
        end_look_at = look_at
    elif kind in ("truck_left", "truck_right"):
        travel = -amount if kind == "truck_left" else amount
        end_position = _add(position, _scale(right, travel))
        end_look_at = _add(look_at, _scale(right, travel))
    elif kind in ("pan_left", "pan_right"):
        turn = amount if kind == "pan_left" else -amount
        end_position = position
        end_look_at = _add(
            position, _rotate_about(_sub(look_at, position), (0.0, 1.0, 0.0), turn)
        )
    elif kind in ("tilt_up", "tilt_down"):
        turn = amount if kind == "tilt_up" else -amount
        end_position = position
        end_look_at = _add(position, _rotate_about(_sub(look_at, position), right, turn))
    else:  # arc_left / arc_right
        # 注視点は弧の中心（target があればその中心、無ければ書かれた look_at）に
        # 固定する: 被写体を画面に留めたまま camera だけが回り込む動き
        pivot = look_at
        if (move.target or "").strip():
            pivot = object_center(scene, find_object(scene, move.target), 0.0)
        turn = -amount if kind == "arc_left" else amount
        end_position = _add(
            pivot, _rotate_about(_sub(position, pivot), (0.0, 1.0, 0.0), turn)
        )
        start_look_at = end_look_at = pivot

    return [
        BlockingCameraKeyframe(
            t=0.0,
            position=list(position),
            look_at=list(start_look_at),
            fov_deg=base.fov_deg,
            roll_deg=base.roll_deg,
        ),
        BlockingCameraKeyframe(
            t=duration,
            position=list(end_position),
            look_at=list(end_look_at),
            fov_deg=base.fov_deg,
            roll_deg=base.roll_deg,
        ),
    ]


def _expand_follow(
    scene: BlockingScene, base: BlockingCameraKeyframe, target_id: str
) -> list[BlockingCameraKeyframe]:
    """``follow``: 対象との相対位置を保ったまま、常に対象を見る。"""
    target = find_object(scene, target_id)
    duration = float(scene.duration)
    times = sorted(
        {0.0, duration}
        | {
            min(duration, max(0.0, keyframe.t))
            for keyframe in target.keyframes
        }
    )
    if len(times) > MAX_KEYFRAMES:
        # 対象のキーフレームが上限いっぱいでも収まるよう、等間隔に間引く
        step = (len(times) - 1) / (MAX_KEYFRAMES - 1)
        times = [times[round(index * step)] for index in range(MAX_KEYFRAMES)]
    start = object_center(scene, target, 0.0)
    offset = _sub(_as_vec(base.position), start)
    keyframes: list[BlockingCameraKeyframe] = []
    for moment in times:
        center = object_center(scene, target, moment)
        keyframes.append(
            BlockingCameraKeyframe(
                t=moment,
                position=list(_add(center, offset)),
                look_at=list(center),
                fov_deg=base.fov_deg,
                roll_deg=base.roll_deg,
            )
        )
    return keyframes


# --------------------------------------------------------------------------
# 時刻 t の姿勢
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CameraPose:
    """ある瞬間のカメラ。"""

    position: Vec
    look_at: Vec
    fov_deg: float
    roll_deg: float


def _bracket(times: list[float], t: float) -> tuple[int, int, float]:
    """``t`` を挟む 2 点の添字と、その間の進み具合（0..1）。"""
    if t <= times[0]:
        return 0, 0, 0.0
    if t >= times[-1]:
        last = len(times) - 1
        return last, last, 0.0
    for index in range(1, len(times)):
        if t <= times[index]:
            span = times[index] - times[index - 1]
            return index - 1, index, 0.0 if span <= 0 else (t - times[index - 1]) / span
    last = len(times) - 1
    return last, last, 0.0


def camera_at(scene: BlockingScene, t: float) -> CameraPose:
    """時刻 ``t``（秒）のカメラ（キーフレームの外側は端の値で止める）。"""
    camera: BlockingCamera = scene.camera
    times = [keyframe.t for keyframe in camera.keyframes]
    first, second, raw = _bracket(times, t)
    u = _ease(raw, camera.easing)
    a, b = camera.keyframes[first], camera.keyframes[second]
    return CameraPose(
        position=_lerp_vec(_as_vec(a.position), _as_vec(b.position), u),
        look_at=_lerp_vec(_as_vec(a.look_at), _as_vec(b.look_at), u),
        fov_deg=_lerp(a.fov_deg, b.fov_deg, u),
        roll_deg=_lerp(a.roll_deg, b.roll_deg, u),
    )


def object_position(obj: BlockingObject, t: float) -> Vec:
    """時刻 ``t`` の底面中心（キーフレーム間は線形補間）。"""
    keyframes: list[BlockingObjectKeyframe] = obj.keyframes
    times = [keyframe.t for keyframe in keyframes]
    first, second, u = _bracket(times, t)
    return _lerp_vec(
        _as_vec(keyframes[first].position), _as_vec(keyframes[second].position), u
    )


def object_height(obj: BlockingObject) -> float:
    """立ち上がりの高さ（球は直径 = 幅、それ以外は size[1]）。"""
    size = _as_vec(obj.size or DEFAULT_SIZES[obj.shape])
    return size[0] if obj.shape == "sphere" else size[1]


def object_center(scene: BlockingScene, obj: BlockingObject, t: float) -> Vec:
    """時刻 ``t`` の中心（底面中心 + 高さの半分）。"""
    base = object_position(obj, t)
    return (base[0], base[1] + object_height(obj) / 2.0, base[2])


def object_yaw(scene: BlockingScene, obj: BlockingObject, t: float) -> float:
    """時刻 ``t`` の向き（度）。``facing`` によって自動で決まることがある。

    正面はローカル座標の +Z で、yaw は ``atan2(dx, dz)``（0 度で +Z を向く）。
    """
    keyframes: list[BlockingObjectKeyframe] = obj.keyframes
    times = [keyframe.t for keyframe in keyframes]
    first, second, u = _bracket(times, t)
    manual = _lerp(keyframes[first].yaw_deg, keyframes[second].yaw_deg, u)
    if obj.facing == "camera":
        to_camera = _sub(camera_at(scene, t).position, object_center(scene, obj, t))
        if abs(to_camera[0]) + abs(to_camera[2]) < 1e-6:
            return manual
        return math.degrees(math.atan2(to_camera[0], to_camera[2]))
    if obj.facing == "path":
        step = 1.0 / FPS
        ahead = object_position(obj, min(float(scene.duration), t + step))
        behind = object_position(obj, max(0.0, t - step))
        velocity = _sub(ahead, behind)
        if abs(velocity[0]) + abs(velocity[2]) < 1e-6:
            return manual
        return math.degrees(math.atan2(velocity[0], velocity[2]))
    return manual


# --------------------------------------------------------------------------
# 視点（透視投影）
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class View:
    """1 コマぶんの投影（ビュー基底 + 焦点距離 px）。"""

    origin: Vec
    right: Vec
    up: Vec
    forward: Vec
    focal: float
    width: int
    height: int

    def to_view(self, point: Vec) -> Vec:
        """世界座標をビュー座標（x 右 / y 上 / z 前）に落とす。"""
        delta = _sub(point, self.origin)
        return (
            _dot(delta, self.right),
            _dot(delta, self.up),
            _dot(delta, self.forward),
        )

    def to_screen(self, view_point: Vec) -> tuple[float, float]:
        """ビュー座標を画面座標（px）へ。``z`` は :data:`NEAR` 以上のこと。"""
        depth = max(NEAR, view_point[2])
        return (
            self.width / 2.0 + view_point[0] * self.focal / depth,
            self.height / 2.0 - view_point[1] * self.focal / depth,
        )

    def project(self, point: Vec) -> tuple[float, float, float]:
        """世界座標 -> ``(x_px, y_px, 奥行き)``。奥行きが負なら背後。"""
        view_point = self.to_view(point)
        x, y = self.to_screen(view_point)
        return x, y, view_point[2]

    def project_direction(self, direction: Vec) -> tuple[float, float] | None:
        """**無限遠の方向**の消失点（カメラの後ろ向きなら None）。"""
        unit = _normalize(direction)
        depth = _dot(unit, self.forward)
        if depth <= 1e-6:
            return None
        return (
            self.width / 2.0 + _dot(unit, self.right) * self.focal / depth,
            self.height / 2.0 - _dot(unit, self.up) * self.focal / depth,
        )


def build_view(pose: CameraPose, width: int, height: int) -> View:
    """カメラと画面サイズから投影を組む（画角は**対角**）。

    ``roll_deg`` は正で画面の絵が時計回りに回る（カメラは反時計回りに傾く）。
    """
    forward = _normalize(_sub(pose.look_at, pose.position))
    world_up = (0.0, 1.0, 0.0)
    if abs(_dot(forward, world_up)) > 0.9999:
        # 真上・真下を向いているときは Z を仮の上にする（外積が潰れないように）
        world_up = (0.0, 0.0, 1.0)
    right = _normalize(_cross(forward, world_up), (1.0, 0.0, 0.0))
    up = _cross(right, forward)
    if abs(pose.roll_deg) > 1e-9:
        angle = math.radians(pose.roll_deg)
        cos, sin = math.cos(angle), math.sin(angle)
        right, up = (
            _add(_scale(right, cos), _scale(up, sin)),
            _sub(_scale(up, cos), _scale(right, sin)),
        )
    diagonal = math.hypot(width, height)
    focal = (diagonal / 2.0) / math.tan(math.radians(pose.fov_deg) / 2.0)
    return View(pose.position, right, up, forward, focal, width, height)


def _clip_segment(start: Vec, end: Vec) -> tuple[Vec, Vec] | None:
    """ビュー座標の線分を ``z >= NEAR`` で切る（丸ごと後ろなら None）。

    多角形用の :func:`_clip_near` は端を巡回して閉じてしまうので、線分には
    使えない（同じ点が 2 度出て潰れる）。
    """
    if start[2] >= NEAR and end[2] >= NEAR:
        return start, end
    if start[2] < NEAR and end[2] < NEAR:
        return None
    span = end[2] - start[2]
    u = 0.0 if abs(span) < 1e-12 else (NEAR - start[2]) / span
    crossing = _lerp_vec(start, end, u)
    return (start, crossing) if start[2] >= NEAR else (crossing, end)


def _clip_near(polygon: list[Vec]) -> list[Vec]:
    """ビュー座標の多角形を ``z >= NEAR`` で切る（Sutherland-Hodgman）。"""
    if not polygon:
        return []
    clipped: list[Vec] = []
    previous = polygon[-1]
    for current in polygon:
        inside = current[2] >= NEAR
        was_inside = previous[2] >= NEAR
        if inside != was_inside:
            span = current[2] - previous[2]
            u = 0.0 if abs(span) < 1e-12 else (NEAR - previous[2]) / span
            clipped.append(_lerp_vec(previous, current, u))
        if inside:
            clipped.append(current)
        previous = current
    return clipped


# --------------------------------------------------------------------------
# 形状（凸なパーツの集まり。法線はパーツの重心から外向きに揃える）
# --------------------------------------------------------------------------

@dataclass
class Part:
    """1 つの凸な塊（頂点・面・重心）。ローカル座標で持つ。"""

    vertices: list[Vec]
    faces: list[tuple[int, ...]]
    centroid: Vec
    #: 明るさの微調整（顔・鼻を少しだけ変えて向きを読みやすくする）
    tint: float = 1.0


def _box_part(
    width: float, height: float, depth: float, center: Vec, tint: float = 1.0
) -> Part:
    half_w, half_d = width / 2.0, depth / 2.0
    half_h = height / 2.0
    corners = [
        (-half_w, -half_h, -half_d), (half_w, -half_h, -half_d),
        (half_w, -half_h, half_d), (-half_w, -half_h, half_d),
        (-half_w, half_h, -half_d), (half_w, half_h, -half_d),
        (half_w, half_h, half_d), (-half_w, half_h, half_d),
    ]
    vertices = [_add(corner, center) for corner in corners]
    faces = [
        (0, 1, 2, 3), (4, 5, 6, 7),  # 下・上
        (0, 1, 5, 4), (2, 3, 7, 6),  # 後ろ・前
        (1, 2, 6, 5), (3, 0, 4, 7),  # 右・左
    ]
    return Part(vertices, faces, center, tint)


def _sphere_part(radius: float, center: Vec, tint: float = 1.0) -> Part:
    vertices: list[Vec] = [(center[0], center[1] + radius, center[2])]
    for ring in range(1, SPHERE_RINGS):
        phi = math.pi * ring / SPHERE_RINGS
        y = math.cos(phi) * radius
        band = math.sin(phi) * radius
        for segment in range(SPHERE_SEGMENTS):
            theta = 2.0 * math.pi * segment / SPHERE_SEGMENTS
            vertices.append(
                (
                    center[0] + math.sin(theta) * band,
                    center[1] + y,
                    center[2] + math.cos(theta) * band,
                )
            )
    vertices.append((center[0], center[1] - radius, center[2]))
    bottom = len(vertices) - 1

    def ring_index(ring: int, segment: int) -> int:
        return 1 + (ring - 1) * SPHERE_SEGMENTS + segment % SPHERE_SEGMENTS

    faces: list[tuple[int, ...]] = []
    for segment in range(SPHERE_SEGMENTS):
        faces.append((0, ring_index(1, segment), ring_index(1, segment + 1)))
        faces.append(
            (bottom, ring_index(SPHERE_RINGS - 1, segment + 1),
             ring_index(SPHERE_RINGS - 1, segment))
        )
    for ring in range(1, SPHERE_RINGS - 1):
        for segment in range(SPHERE_SEGMENTS):
            faces.append(
                (
                    ring_index(ring, segment),
                    ring_index(ring + 1, segment),
                    ring_index(ring + 1, segment + 1),
                    ring_index(ring, segment + 1),
                )
            )
    return Part(vertices, faces, center, tint)


def _cylinder_part(
    radius: float, height: float, center: Vec, tint: float = 1.0
) -> Part:
    half = height / 2.0
    vertices: list[Vec] = []
    for segment in range(CYLINDER_SEGMENTS):
        theta = 2.0 * math.pi * segment / CYLINDER_SEGMENTS
        x = center[0] + math.sin(theta) * radius
        z = center[2] + math.cos(theta) * radius
        vertices.append((x, center[1] - half, z))
        vertices.append((x, center[1] + half, z))
    faces: list[tuple[int, ...]] = []
    for segment in range(CYLINDER_SEGMENTS):
        low = 2 * segment
        next_low = 2 * ((segment + 1) % CYLINDER_SEGMENTS)
        faces.append((low, next_low, next_low + 1, low + 1))
    faces.append(tuple(2 * segment for segment in range(CYLINDER_SEGMENTS)))
    faces.append(tuple(2 * segment + 1 for segment in range(CYLINDER_SEGMENTS)))
    return Part(vertices, faces, center, tint)


def _shape_parts(shape: str, size: Vec) -> list[Part]:
    """形状 -> ローカル座標のパーツ（底面中心が原点、正面が +Z）。"""
    width, height, depth = size
    if shape == "box":
        return [_box_part(width, height, depth, (0.0, height / 2.0, 0.0))]
    if shape == "sphere":
        radius = width / 2.0
        return [_sphere_part(radius, (0.0, radius, 0.0))]
    if shape == "cylinder":
        return [_cylinder_part(width / 2.0, height, (0.0, height / 2.0, 0.0))]
    if shape == "capsule":
        radius = width / 2.0
        barrel = max(0.0, height - width)
        parts = [_sphere_part(radius, (0.0, radius, 0.0))]
        if barrel > 1e-6:
            parts.append(
                _cylinder_part(radius, barrel, (0.0, radius + barrel / 2.0, 0.0))
            )
            parts.append(_sphere_part(radius, (0.0, radius + barrel, 0.0)))
        return parts
    # figure: 箱の胴 + 球の頭 + 正面の小さな鼻（向きが読めるように）
    head = min(height * 0.16, width * 0.9)
    torso = max(0.05, height - head)
    nose = head * 0.35
    return [
        _box_part(width, torso, depth, (0.0, torso / 2.0, 0.0)),
        _sphere_part(head / 2.0, (0.0, torso + head / 2.0, 0.0), tint=1.06),
        _box_part(
            nose, nose, nose,
            (0.0, torso + head * 0.55, head / 2.0 + nose / 2.0 - nose * 0.2),
            tint=0.78,
        ),
    ]


# --------------------------------------------------------------------------
# 描画
# --------------------------------------------------------------------------

def _shade(color: tuple[int, int, int], normal: Vec, tint: float) -> tuple[int, int, int]:
    """固定方向光の平面シェーディング（環境光 + 拡散）。"""
    light = _normalize(LIGHT_DIRECTION)
    level = AMBIENT + (1.0 - AMBIENT) * max(0.0, _dot(normal, _scale(light, -1.0)))
    level *= tint
    return tuple(  # type: ignore[return-value]
        max(0, min(255, round(channel * level))) for channel in color
    )


def _draw_floor(draw: ImageDraw.ImageDraw, view: View, scale: int) -> None:
    """1m 間隔の床グリッド（線分をニアクリップしてから投影する）。"""
    extent = float(GRID_EXTENT)
    line_color = ImageColor.getrgb(FLOOR_COLOR)
    accent_color = ImageColor.getrgb(FLOOR_ACCENT_COLOR)
    for step in range(-GRID_EXTENT, GRID_EXTENT + 1):
        color = accent_color if step % 5 == 0 else line_color
        for start, end in (
            ((float(step), 0.0, -extent), (float(step), 0.0, extent)),
            ((-extent, 0.0, float(step)), (extent, 0.0, float(step))),
        ):
            segment = _clip_segment(view.to_view(start), view.to_view(end))
            if segment is None:
                continue
            draw.line(
                [view.to_screen(segment[0]), view.to_screen(segment[1])],
                fill=color,
                width=max(1, scale),
            )


def _draw_horizon(draw: ImageDraw.ImageDraw, view: View, scale: int) -> None:
    """地平線（床面の消失線）を 1 本引く。"""
    points = horizon_points(view)
    if points is None:
        return
    (x1, y1), (x2, y2) = points
    # 画面の外まで伸ばしてから引く（PIL 側で切ってくれる）
    dx, dy = x2 - x1, y2 - y1
    draw.line(
        [(x1 - dx * 50, y1 - dy * 50), (x2 + dx * 50, y2 + dy * 50)],
        fill=ImageColor.getrgb(HORIZON_COLOR),
        width=max(1, scale),
    )


def horizon_points(view: View) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """地平線上の 2 点（カメラが真下・真上を向いていれば None）。"""
    flat = (view.forward[0], 0.0, view.forward[2])
    if _length(flat) < 1e-6:
        return None
    flat = _normalize(flat)
    side = _normalize(_cross((0.0, 1.0, 0.0), flat), (1.0, 0.0, 0.0))
    left = view.project_direction(_add(flat, _scale(side, 1.0)))
    right = view.project_direction(_sub(flat, _scale(side, 1.0)))
    if left is None or right is None:
        return None
    return left, right


def horizon_y(view: View) -> float | None:
    """画面中央での地平線の y（px。引けなければ None）。"""
    points = horizon_points(view)
    if points is None:
        return None
    (x1, y1), (x2, y2) = points
    if abs(x2 - x1) < 1e-9:
        return None
    return y1 + (view.width / 2.0 - x1) * (y2 - y1) / (x2 - x1)


def _object_faces(
    scene: BlockingScene, obj: BlockingObject, t: float, view: View
) -> list[tuple[float, list[tuple[float, float]], tuple[int, int, int]]]:
    """1 オブジェクトぶんの ``(奥行き, 画面座標, 色)``（背面は落とす）。"""
    color = check_color(obj.color, what=f"color（{obj.id}）")
    base = object_position(obj, t)
    yaw = object_yaw(scene, obj, t)
    size = _as_vec(obj.size or DEFAULT_SIZES[obj.shape])
    faces: list[tuple[float, list[tuple[float, float]], tuple[int, int, int]]] = []
    for part in _shape_parts(obj.shape, size):
        world = [_add(_rotate_yaw(vertex, yaw), base) for vertex in part.vertices]
        centroid = _add(_rotate_yaw(part.centroid, yaw), base)
        for indices in part.faces:
            polygon = [world[index] for index in indices]
            face_center = _scale(
                (
                    sum(point[0] for point in polygon),
                    sum(point[1] for point in polygon),
                    sum(point[2] for point in polygon),
                ),
                1.0 / len(polygon),
            )
            normal = _normalize(
                _cross(_sub(polygon[1], polygon[0]), _sub(polygon[2], polygon[0])),
                (0.0, 1.0, 0.0),
            )
            # 巻き順に頼らず、パーツの重心から外向きへ揃える（凸な塊なので確実）
            if _dot(normal, _sub(face_center, centroid)) < 0:
                normal = _scale(normal, -1.0)
            if _dot(normal, _sub(view.origin, face_center)) <= 0:
                continue  # 背面
            clipped = _clip_near([view.to_view(point) for point in polygon])
            if len(clipped) < 3:
                continue
            depth = sum(point[2] for point in clipped) / len(clipped)
            faces.append(
                (
                    depth,
                    [view.to_screen(point) for point in clipped],
                    _shade(color, normal, part.tint),
                )
            )
    return faces


def render_frame(scene: BlockingScene, t: float, *, prepared: bool = False) -> Image.Image:
    """時刻 ``t``（秒）の 1 コマを描く。

    ``prepared`` を立てると :func:`prepare_scene` 済みとして検証を省く
    （動画のように何百コマも描くときのため）。
    """
    ready = scene if prepared else prepare_scene(scene)
    width, height = resolution(ready.aspect_ratio)
    scale = SUPERSAMPLE
    canvas = Image.new(
        "RGB",
        (width * scale, height * scale),
        check_color(ready.background.color, what="background.color"),
    )
    draw = ImageDraw.Draw(canvas)
    view = build_view(camera_at(ready, t), width * scale, height * scale)

    _draw_horizon(draw, view, scale)
    if ready.background.floor_grid:
        _draw_floor(draw, view, scale)

    faces: list[tuple[float, list[tuple[float, float]], tuple[int, int, int]]] = []
    for obj in ready.objects:
        faces.extend(_object_faces(ready, obj, t, view))
    # 奥から手前へ（原始形状は交差しない前提なので面単位のソートで足りる）
    faces.sort(key=lambda face: face[0], reverse=True)
    for _, polygon, color in faces:
        draw.polygon(polygon, fill=color, outline=color)

    return canvas.resize((width, height), Image.LANCZOS)


# --------------------------------------------------------------------------
# 画面上の位置（location_map と UI が読む）
# --------------------------------------------------------------------------

def _screen_point(view: View, point: Vec) -> BlockingScreenPoint:
    x, y, depth = view.project(point)
    return BlockingScreenPoint(
        x_percent=round(100.0 * x / view.width, 1),
        y_percent=round(100.0 * y / view.height, 1),
        x_px=round(x, 1),
        y_px=round(y, 1),
        visible=depth > NEAR,
    )


def screen_positions(
    scene: BlockingScene, t: float = 0.0, *, prepared: bool = False
) -> BlockingPositions:
    """時刻 ``t`` の各オブジェクトの画面上の位置（% と px）と地平線。

    カメラ投影からそのまま出すので、``location_map`` の数字と描かれる絵は必ず
    一致する。
    """
    ready = scene if prepared else prepare_scene(scene)
    width, height = resolution(ready.aspect_ratio)
    view = build_view(camera_at(ready, t), width, height)
    horizon = horizon_y(view)
    objects: list[BlockingObjectScreen] = []
    for obj in ready.objects:
        base = object_position(obj, t)
        top = (base[0], base[1] + object_height(obj), base[2])
        center = object_center(ready, obj, t)
        objects.append(
            BlockingObjectScreen(
                id=obj.id,
                label=obj.label or obj.id,
                shape=obj.shape,
                distance_m=round(_length(_sub(center, view.origin)), 2),
                base=_screen_point(view, base),
                center=_screen_point(view, center),
                top=_screen_point(view, top),
            )
        )
    return BlockingPositions(
        t=round(t, 3),
        width=width,
        height=height,
        horizon_y_percent=(
            None if horizon is None else round(100.0 * horizon / height, 1)
        ),
        objects=objects,
    )


# --------------------------------------------------------------------------
# 英文（H3 のプロンプトへ写すためのもの）
# --------------------------------------------------------------------------

def _move_sentence(scene: BlockingScene) -> str:
    """カメラの動きを 1 文の英語にする（プリセットが無ければ実際の差分から）。"""
    duration = float(scene.duration)
    move = scene.camera.move
    if move is not None:
        amount = float(move.amount)
        unit = "m" if move.type in MOVES_IN_METERS else " degrees"
        wording = {
            "static": "holds a static frame",
            "push_in": f"pushes in {amount:.2f}{unit}",
            "pull_out": f"pulls out {amount:.2f}{unit}",
            "pan_left": f"pans left {amount:.1f}{unit}",
            "pan_right": f"pans right {amount:.1f}{unit}",
            "tilt_up": f"tilts up {amount:.1f}{unit}",
            "tilt_down": f"tilts down {amount:.1f}{unit}",
            "truck_left": f"trucks left {amount:.2f}{unit}",
            "truck_right": f"trucks right {amount:.2f}{unit}",
            "arc_left": f"arcs left {amount:.1f}{unit} around the subject",
            "arc_right": f"arcs right {amount:.1f}{unit} around the subject",
            "follow": "tracks the subject, holding the same relative angle",
        }[move.type]
        return f"The camera {wording} over the {duration:.2f}-second take."
    first = scene.camera.keyframes[0]
    last = scene.camera.keyframes[-1]
    travel = _length(_sub(_as_vec(last.position), _as_vec(first.position)))
    swing = _length(_sub(_as_vec(last.look_at), _as_vec(first.look_at)))
    if travel < 0.02 and swing < 0.02:
        return f"The camera holds a static frame for the {duration:.2f}-second take."
    if travel < 0.02:
        return (
            f"The camera stays on its mark and reframes over the"
            f" {duration:.2f}-second take."
        )
    return (
        f"The camera travels {travel:.2f}m over the {duration:.2f}-second take."
    )


def _positions_sentence(positions: BlockingPositions) -> str:
    """``At 0.00s: hero at x 50%, y 56%; …`` の 1 文。"""
    parts: list[str] = []
    for item in positions.objects:
        if not item.center.visible:
            parts.append(f"{item.label} is behind the camera")
            continue
        parts.append(
            f"{item.label} at x {item.center.x_percent:.0f}%,"
            f" y {item.center.y_percent:.0f}%"
        )
    return f"At {positions.t:.2f}s: " + "; ".join(parts) + "."


def location_map(scene: BlockingScene, *, prepared: bool = False) -> str:
    """H3 のプロンプトへ写すための英文（LOCATION MAP + CAMERA）。

    公式ガイドの LOCATION MAP / CAMERA の書き方（``docs/h3-prompt-guide-draft.md``
    の C04 など）に合わせ、**t=0・中間・終端**の各オブジェクトの画面位置、
    地平線、カメラの距離・高さ・画角・動きを短い 2 段落にする。ここに書いた
    数字は実際に描かれる絵と同じ（:func:`screen_positions` から作る）。
    """
    ready = scene if prepared else prepare_scene(scene)
    duration = float(ready.duration)
    moments = sorted({0.0, round(duration / 2.0, 3), duration})
    samples = [screen_positions(ready, moment, prepared=True) for moment in moments]

    pose = camera_at(ready, 0.0)
    first = samples[0]
    subject = ready.objects[0]
    distance = _length(_sub(object_center(ready, subject, 0.0), pose.position))
    horizon = (
        "the horizon is out of frame"
        if first.horizon_y_percent is None
        else f"flat horizon at y {first.horizon_y_percent:.0f}%"
    )
    head = (
        f"LOCATION MAP Camera {distance:.2f}m from {subject.label or subject.id}"
        f" at {pose.position[1]:.2f}m height,"
        f" {pose.fov_deg:.0f} degree diagonal field of view, {horizon}."
        " Screen positions are given as percentages of frame width and height,"
        " x from the left edge and y from the top edge."
    )
    body = " ".join(_positions_sentence(sample) for sample in samples)
    tail = (
        f"CAMERA {_move_sentence(ready)}"
        f" The frame is {ready.aspect_ratio} and the take is one continuous"
        " shot with no cuts."
    )
    return f"{head} {body}\n\n{tail}"


def reference_note(index: int = 1) -> str:
    """参照として渡すときにプロンプトへ添える定型（``<Video k>``）。

    ``retention_analysis`` の 1 行としてそのまま書ける形にしてある
    （:data:`app.prompts.MINIMAX_H3_REFERENCE_VIDEO_GUIDE` の
    ``<Video 1> (cut and pacing structure): weak_reference - …`` と同じ形）。
    """
    return (
        f"<Video {index}> (camera path and blocking only): weak_reference -"
        " take the framing, the subject placement and the camera movement from"
        f" <Video {index}> only. It is a grey mannequin previz render, so do not"
        " reproduce its look, colours, shapes, surfaces or lighting in the"
        " target video."
    )


# --------------------------------------------------------------------------
# mp4 の書き出し
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class BlockingRenderResult:
    """:func:`render_video` の結果。"""

    path: Path
    width: int
    height: int
    fps: int
    frames: int
    duration: float


def render_video(scene: BlockingScene, out_path: str | Path) -> BlockingRenderResult:
    """シーンを 24fps の mp4（libx264 / yuv420p）に焼く。

    同期関数なので、イベントループから呼ぶときは ``asyncio.to_thread``
    に載せること（:func:`app.library.add_blocking`）。ffmpeg が無ければ
    :class:`BlockingError`。
    """
    ready = prepare_scene(scene)
    width, height = resolution(ready.aspect_ratio)
    frames = frame_count(ready.duration)
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="blocking-") as workdir:
        folder = Path(workdir)
        for index in range(frames):
            frame = render_frame(ready, index / FPS, prepared=True)
            frame.save(folder / f"frame_{index:05d}.png")
        command = [
            FFMPEG,
            "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-framerate", str(FPS),
            "-i", str(folder / "frame_%05d.png"),
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-r", str(FPS),
            "-movflags", "+faststart",
            str(destination),
        ]
        try:
            done = subprocess.run(  # noqa: S603 - 引数は自前で組み立てている
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=ENCODE_TIMEOUT,
            )
        except FileNotFoundError as exc:
            raise BlockingError(
                "ブロッキング動画の書き出しには ffmpeg が要りますが"
                f"見つかりませんでした（{exc}）"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise BlockingError(
                f"ffmpeg が {ENCODE_TIMEOUT:.0f} 秒以内に終わりませんでした"
            ) from exc
        except OSError as exc:
            raise BlockingError(f"ffmpeg を起動できませんでした: {exc}") from exc
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip()[-400:]
        raise BlockingError(f"ffmpeg が失敗しました: {detail}")
    if not destination.is_file() or destination.stat().st_size == 0:
        raise BlockingError("ffmpeg は終了しましたが出力が空でした")
    return BlockingRenderResult(
        path=destination,
        width=width,
        height=height,
        fps=FPS,
        frames=frames,
        duration=float(ready.duration),
    )
