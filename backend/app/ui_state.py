"""画面の状態を人と外部エージェントで共有する置き場（``ui_state`` テーブル）。

いま置いているのは生成フォームの下書き 1 件だけ。外部エージェントが
``PATCH /api/v1/ui/generate-form`` で値を入れると、ブラウザは WS
（``type: "form"``）で受け取ってフォームへ流し込み、人がフォームを触れば
``PUT /api/ui/generate-form`` で書き戻る、という双方向の同期になる。

値のスキーマの正本は**フロントの ``FormState``** で、ここでは JSON の辞書として
素通しする（項目が増えてもバックエンドを直さずに済む）。代わりに 2 つだけ守る:

- 大きさの上限（:data:`MAX_VALUE_BYTES`）。フォーム 1 枚に収まらない量は事故。
- ``revision``: 保存のたびに 1 つ上がる連番。書き手は「これを見て書いた」という
  ``base_revision`` を添えられ、その間に誰かが書いていれば 409 で弾かれる
  （省略すると強制上書き = 現在値を知らない外部エージェントのための逃げ道）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .db import get_db
from .models import UiFormState
from .workflows import (
    INPUT_FIELDS,
    MULTI_INPUT_FIELDS,
    WorkflowSpec,
    WorkflowSpecError,
    get_audio_spec,
    get_image_spec,
    get_video_spec,
)

#: 生成フォームの下書きを置くキー（いまのところ ``ui_state`` の唯一の住人）。
GENERATE_FORM_KEY = "generate_form"

#: ``values`` の JSON の上限。フォーム 1 枚ぶんとしては十分に大きい。
MAX_VALUE_BYTES = 64 * 1024


class UiStateError(Exception):
    """入力が受け取れない（ルーターが 400 に変換する）。"""


class UiStateConflict(Exception):
    """``base_revision`` が古い（ルーターが 409 と現在値に変換する）。"""

    def __init__(self, message: str, current: UiFormState) -> None:
        super().__init__(message)
        self.current = current


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_state(row: Any) -> UiFormState:
    try:
        values = json.loads(row["value"])
    except (TypeError, ValueError):
        values = {}
    return UiFormState(
        values=values if isinstance(values, dict) else {},
        revision=int(row["revision"]),
        updated_by=str(row["updated_by"]),
        updated_at=str(row["updated_at"]),
    )


def _checked(values: Any) -> dict[str, Any]:
    """辞書であることと大きさだけ見る（項目の中身はフロントの領分）。"""
    if not isinstance(values, dict):
        raise UiStateError("values はオブジェクトで送ってください")
    try:
        encoded = json.dumps(values, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise UiStateError("values に JSON にできない値が入っています") from exc
    if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
        raise UiStateError(
            f"values が大きすぎます（上限 {MAX_VALUE_BYTES} バイト）"
        )
    return values


async def get(key: str = GENERATE_FORM_KEY) -> UiFormState:
    """保存されている値（無ければ空 + ``revision`` 0）。"""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT * FROM ui_state WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
    return _row_to_state(row) if row else UiFormState()


def _check_base(base_revision: int | None, current: UiFormState) -> None:
    """``base_revision`` の申告を今の連番と突き合わせる。

    省略（``None``）は「現在値を見ずに上書きする」という意思表示なので通す。
    """
    if base_revision is None:
        return
    if base_revision > current.revision:
        raise UiStateError(
            f"base_revision {base_revision} は未来です"
            f"（いまは {current.revision}）"
        )
    if base_revision < current.revision:
        raise UiStateConflict(
            f"ほかの更新が入っています（base_revision {base_revision} /"
            f" 現在 {current.revision}）",
            current,
        )


async def _write(
    key: str,
    merge: Callable[[dict[str, Any]], dict[str, Any]],
    updated_by: str,
    base_revision: int | None,
) -> UiFormState:
    """読み → 突き合わせ → マージ → 書き を **1 つのトランザクション**で行う。

    ``merge`` は今の値を受け取って書き込む値を返す（丸ごと置き換えなら今の値を
    捨て、部分更新なら重ねる）。読みと書きを別々にすると、そのあいだに入った
    他方の保存を黙って巻き戻してしまう（外部エージェントの部分更新と、人が
    フォームを触ったときの保存はふつうに交錯する）ので、``BEGIN IMMEDIATE`` で
    書き手を直列化して、採番も値も**同じ読み**に基づかせる。
    """
    async with get_db() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute(
                "SELECT * FROM ui_state WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
            current = _row_to_state(row) if row else UiFormState()
            _check_base(base_revision, current)
            state = UiFormState(
                values=_checked(merge(current.values)),
                revision=current.revision + 1,
                updated_by=updated_by,
                updated_at=_now(),
            )
            await conn.execute(
                "INSERT INTO ui_state (key, value, revision, updated_by, updated_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " revision = excluded.revision, updated_by = excluded.updated_by,"
                " updated_at = excluded.updated_at",
                (
                    key,
                    json.dumps(state.values, ensure_ascii=False),
                    state.revision,
                    state.updated_by,
                    state.updated_at,
                ),
            )
        except BaseException:
            await conn.rollback()
            raise
        await conn.commit()
    return state


async def put(
    values: dict[str, Any],
    *,
    updated_by: str,
    base_revision: int | None = None,
    key: str = GENERATE_FORM_KEY,
) -> UiFormState:
    """丸ごと置き換える（送られなかった項目は消える）。"""
    checked = _checked(values)
    return await _write(key, lambda _current: checked, updated_by, base_revision)


async def patch(
    values: dict[str, Any],
    *,
    updated_by: str,
    base_revision: int | None = None,
    key: str = GENERATE_FORM_KEY,
) -> UiFormState:
    """送られたキーだけ重ねる（触れなかった項目は今のまま）。

    重ねる相手は**書き込みと同じトランザクションで読んだ**値なので、待たされて
    いるあいだに入った保存を巻き戻さない。
    """
    changes = _checked(values)
    return await _write(
        key, lambda current: {**current, **changes}, updated_by, base_revision
    )


# --------------------------------------------------------------------------
# 下書き -> ジョブ（``POST /api/v1/jobs`` の ``from_form``）
# --------------------------------------------------------------------------
#
# 画面の「生成」ボタンと同じことを外部エージェントにもさせるための写像。フォーム
# （``FormState``、camelCase）と :class:`app.models.JobCreate`（snake_case）は
# 項目の切り方が違ううえ、フォーム側は**走らないステージの入力を送らない**
# （宣言していないワークフローに渡すと 422）。
#
# **正本はフロントの ``App.tsx`` の ``submit()``**（音声モードは
# ``frontend/src/form.ts`` の ``audioJobPayload()``）で、ここはその鏡写し。
# 対応は次のとおり:
#
# ===========================  ==========================================
# ここ                          フロント（``frontend/src/``）
# ===========================  ==========================================
# :func:`job_fields`           ``App.tsx`` の ``submit()``
# :func:`_audio_fields`        ``form.ts`` の ``audioJobPayload()``
# :func:`_media_fields`        ``App.tsx`` の ``submit()`` の ``payload``
# 開始フレームの ``accepted``   ``form.ts`` の ``imageWorkflowNeedsSource()``
#                              ＋ ``submit()`` の ``needs`` / ``accepts``
# :func:`_picked_selects`      ``form.ts`` の ``jobSelects()``
# ===========================  ==========================================
#
# 片方を触ったら、もう片方も揃えること（食い違うと「画面から押したときだけ
# 通る / 外部から投げたときだけ 422」という差が出る）。

#: そのまま移せる項目（フォームのキー -> JobCreate のキー）。
_COMMON_FIELDS: dict[str, str] = {
    "mode": "mode",
    "negativePrompt": "negative_prompt",
    "aspectRatio": "aspect_ratio",
    "megapixels": "megapixels",
    "fps": "fps",
}

#: 単発の入力欄（論理名 -> フォームのキー）。ジョブ側の名前は
#: :data:`app.workflows.INPUT_FIELDS` が持っている。
_INPUT_FORM_KEYS: dict[str, str] = {
    "image": "sourceImage",
    "end_image": "endImage",
    "audio": "audioPath",
    "video": "referenceVideo",
}


def _lora_refs(raw: Any) -> list[dict[str, Any]]:
    """フォームの LoRA 選択（表示名つき）を JobCreate の 3 項目だけに絞る。"""
    refs: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        refs.append(
            {
                "lora_name": item.get("lora_name", ""),
                "trigger_word": item.get("trigger_word", ""),
                "strength": item.get("strength", 1.0),
            }
        )
    return refs


def _picked_selects(raw: Any, specs: list[WorkflowSpec]) -> dict[str, str]:
    """走るステージが宣言していて、選択肢にある値だけを残す（``jobSelects``）。"""
    picked: dict[str, str] = {}
    for name, value in (raw if isinstance(raw, dict) else {}).items():
        text = str(value or "")
        if not text:
            continue
        for spec in specs:
            select = spec.select(str(name))
            if select is not None and text in select.choices:
                picked[str(name)] = text
                break
    return picked


def _audio_fields(values: dict[str, Any]) -> dict[str, Any]:
    """``mode: "audio"`` の写像（``form.ts`` の ``audioJobPayload()``）。

    音声は画像・動画と繋がらない独立ジョブなので、送るのは音声の項目だけ。
    """
    spec = get_audio_spec(str(values.get("audioWorkflow") or ""))
    fields: dict[str, Any] = {
        "mode": "audio",
        "audio_workflow": spec.id,
        "audio_prompt": values.get("audioPrompt", ""),
        "duration": values.get("audioDuration", 60),
        "lyrics": values.get("lyrics", ""),
        "negative_tags": values.get("negativeTags", ""),
        "reprompt": bool(values.get("reprompt")),
    }
    if values.get("audioCategory"):
        fields["audio_category"] = values["audioCategory"]
    selects = _picked_selects(values.get("selects"), [spec])
    if selects:
        fields["selects"] = selects
    return fields


def job_fields(values: Any) -> dict[str, Any]:
    """フォームの下書きを :class:`app.models.JobCreate` の body へ写す。

    走らないステージの入力は落とす（画像だけのモードに動画の項目を渡さない、
    宣言のない参照欄を渡さない、など）。ワークフロー id が壊れているといった
    「そもそも写せない」入力は :class:`UiStateError` にして 400 で返す。
    """
    if not isinstance(values, dict):
        raise UiStateError("保存されているフォームの下書きが壊れています")
    mode = str(values.get("mode") or "full")
    try:
        if mode == "audio":
            fields = _audio_fields(values)
        else:
            fields = _media_fields(values, mode)
    except WorkflowSpecError as exc:
        raise UiStateError(str(exc)) from exc

    # 以下はモード共通。シードは「固定」にしているときだけ送る（それ以外は
    # 毎回引き直す = null）。
    if values.get("seedLocked"):
        fields["seed"] = values.get("seed", 0)
    steps = values.get("steps") or 0
    if isinstance(steps, (int, float)) and steps > 0:
        fields["steps"] = int(steps)
    overrides = values.get("modelOverrides")
    if isinstance(overrides, dict) and overrides:
        fields["model_overrides"] = {
            str(key): str(value) for key, value in overrides.items()
        }
    # チェックしたときだけ manual 指定として送る（オフ = 自動判定に任せる）
    if values.get("nsfw"):
        fields["nsfw"] = True
    return fields


def _media_fields(values: dict[str, Any], mode: str) -> dict[str, Any]:
    """画像 / 動画のモード（``full`` / ``i2v`` / ``image_only``）の写像。"""
    runs_image = mode in ("full", "image_only")
    runs_video = mode in ("full", "i2v")
    image_spec = (
        get_image_spec(str(values.get("imageWorkflow") or ""))
        if runs_image
        else None
    )
    video_spec = (
        get_video_spec(str(values.get("videoWorkflow") or ""))
        if runs_video
        else None
    )
    fields: dict[str, Any] = {
        target: values[key]
        for key, target in _COMMON_FIELDS.items()
        if key in values
    }
    fields["mode"] = mode
    # ワークフロー id は「走らないステージのぶんも送ってよい」（既定値のまま
    # 通る）。画面と同じ組み合わせで履歴に残るよう、あるものはそのまま渡す。
    for key, target in (
        ("videoWorkflow", "video_workflow"),
        ("imageWorkflow", "image_workflow"),
    ):
        if values.get(key):
            fields[target] = values[key]

    # 画像ステージの入力（i2v では走らない）
    if mode != "i2v":
        fields["image_prompt"] = values.get("imagePrompt", "")
        fields["loras"] = _lora_refs(values.get("loras"))
        fields["trigger_text"] = values.get("triggerText", "")
    # 動画ステージの入力（image_only では走らない）
    if mode != "image_only":
        fields["video_loras"] = _lora_refs(values.get("videoLoras"))
        fields["video_trigger_text"] = values.get("videoTriggerText", "")
        fields["duration"] = values.get("duration", 10)

    # ショット割り / Elements は動画ステージのパラメータ。宣言のあるワークフロー
    # を走らせるときだけ送り、ショット割りを使うならトップレベルのプロンプトは
    # 送らない（送ると 422）。
    shots = values.get("multiShots") if runs_video else None
    uses_shots = bool(shots) and video_spec is not None and video_spec.multi_shot
    if uses_shots:
        fields["multi_shots"] = shots
    if runs_video and video_spec is not None and video_spec.elements:
        elements = values.get("klingElements")
        if elements:
            fields["kling_elements"] = elements
    if runs_video and not uses_shots:
        fields["video_prompt"] = values.get("videoPrompt", "")

    # 単発の入力欄（開始フレーム・最後のフレーム・音声・参照動画）。絞り込みは
    # ``App.tsx`` の ``submit()`` の ``needs`` / ``accepts`` と同じにする:
    #
    # * 音声と参照動画は、動画ワークフローが **requires** している欄だけ
    # * 最後のフレームは requires か supports（任意入力でも選ばれていれば送る）
    # * 開始フレームは「i2v で動画側が受け取る」か「画像ワークフローが写真を
    #   要る（画像編集）」ときだけ。後者は ``imageWorkflowNeedsSource()`` と
    #   揃えて **requires だけ**を見る（``supports`` まで広げると「画面から
    #   押すと送らないのに、外部からだと送る」差が出る）。
    def video_requires(name: str) -> bool:
        return video_spec is not None and name in video_spec.requires

    def video_accepts(name: str) -> bool:
        return video_spec is not None and video_spec.supports(name)

    accepted = {
        "audio": video_requires("audio"),
        "video": video_requires("video"),
        "end_image": video_accepts("end_image"),
        "image": (mode == "i2v" and video_accepts("image"))
        or (image_spec is not None and "image" in image_spec.requires),
    }
    for name, target in INPUT_FIELDS.items():
        value = values.get(_INPUT_FORM_KEYS[name])
        if value and accepted[name]:
            fields[target] = value

    # マルチモーダル参照（走るステージが宣言している欄だけ）
    for name, target in MULTI_INPUT_FIELDS.items():
        value = values.get(_camel(name))
        if not value:
            continue
        if any(
            spec is not None and spec.supports(name)
            for spec in (video_spec, image_spec)
        ):
            fields[target] = value

    stages = [spec for spec in (image_spec, video_spec) if spec is not None]
    selects = _picked_selects(values.get("selects"), stages)
    if selects:
        fields["selects"] = selects
    return fields


def _camel(name: str) -> str:
    """``reference_images`` -> ``referenceImages``（フォーム側のキーの綴り）。"""
    head, *rest = name.split("_")
    return head + "".join(word.capitalize() for word in rest)
