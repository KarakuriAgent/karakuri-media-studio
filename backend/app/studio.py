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

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

import aiosqlite
from pydantic import ValidationError

from . import comfy
from . import grok
from . import jobs as job_service
from . import nsfw
from . import studio_demo
from . import ws
from .db import get_db
from .ids import new_id
from .models import (
    ASSET_FILE_ROLE_KINDS,
    MAX_STEPS,
    JobCreate,
    StoryCreate,
    StoryResult,
    StorySceneResult,
    StoryShotResult,
    StudioAsset,
    StudioAssetFile,
    StudioEpisode,
    StudioProject,
    StudioProjectCreate,
    StudioProjectDetail,
    StudioProjectSummary,
    STUDIO_REVISION_ACTORS,
    StudioProjectUpdate,
    StudioPromptReference,
    StudioRenderRequest,
    StudioRevision,
    StudioRevisionDetail,
    StudioRevisionDiff,
    StudioRevisionEntityDiff,
    StudioRevisionFieldDiff,
    StudioScene,
    StudioShot,
    StudioShotPreview,
    StudioTake,
    validate_asset_profile,
)
from .config import load_settings
from .paths import rebase_stored_path
from .workflow import WorkflowError, supported_on_target
from .workflows import (
    LATENT_UPSCALE_NAME,
    WorkflowSpecError,
    get_image_spec,
    get_video_spec,
)

log = logging.getLogger(__name__)

#: Shot が使う動画ワークフロー（すべて MiniMax H3。音声も同じパスで出る）
WORKFLOW_T2V = "minimax_h3_t2v"
WORKFLOW_I2V = "minimax_h3_i2v"
WORKFLOW_R2V = "minimax_h3_r2v"
#: ラテント連続性（プロジェクトの ``latent_continuity``）を入れた引き継ぎ。
#: 直前カットのラストフレーム 1 枚ではなく、直前カットの動画そのものと保存して
#: おいた AV ラテントを Motion Context に渡して続きを作る（:mod:`app.workflows`
#: の ``minimax_h3_r2v_context``）。Shot の強制指定（``workflow_override``）には
#: 出さない: プロジェクト設定と直前カットの採用 Take が揃って初めて成立する
#: モードなので、「引き継ぎのやり方」としてプロジェクト側で選ばせる。
WORKFLOW_R2V_CONTEXT = "minimax_h3_r2v_context"

#: ラテント連続性が ON のときに使う「AV ラテントを保存する版」への読み替え。
#: 連鎖の起点になる通常カット（t2v / i2v / r2v）でもラテントを残さないと、次の
#: カットが引き継ぐものが無く連鎖を始められない（``minimax_h3_r2v_context`` は
#: 元から保存する）。仕上がりは素の版と同じで、保存の 2 ノードが増えるだけ。
LATENT_SAVE_WORKFLOWS: dict[str, str] = {
    WORKFLOW_T2V: "minimax_h3_t2v_save",
    WORKFLOW_I2V: "minimax_h3_i2v_save",
    WORKFLOW_R2V: "minimax_h3_r2v_save",
}

#: ラテント連続性が ON のときにスタジオが投げうる**論理**ワークフロー
#: （保存付きと連続カット）。品質のバリアント（``_turbo`` / ``_opt``）は
#: :data:`LATENT_CONTEXT_WORKFLOWS` の方に混ぜて数える。
LATENT_CONTEXT_BASE_WORKFLOWS: frozenset[str] = frozenset(
    {WORKFLOW_R2V_CONTEXT, *LATENT_SAVE_WORKFLOWS.values()}
)


def _latent_save_workflow(workflow: str, latent_continuity: bool) -> str:
    """ラテント連続性が ON なら、保存付きバリアントに読み替えた id。

    OFF のとき（と読み替え先を持たないワークフロー）はそのまま返す。
    """
    if not latent_continuity:
        return workflow
    return LATENT_SAVE_WORKFLOWS.get(workflow, workflow)


# --------------------------------------------------------------------------
# 動画生成の品質（プロジェクトの ``quality``）
# --------------------------------------------------------------------------
#
# 品質は論理モード（t2v / i2v / r2v / r2v_context）ともラテント連続性とも
# **直交**したパラメータとして持つ。モードを決めるのは :func:`_pick_workflow` の
# 仕事で、そのあとにラテント連続性の読み替え（:func:`_latent_save_workflow`）を
# かけ、最後に「そこまでで決まった論理ワークフロー × 品質 -> バリアント id」を
# ここで解決する（:func:`_quality_workflow`）。ラテント保存版・連続カット版にも
# turbo / opt のテンプレートがあるので、品質はどの組み合わせでも効く。
# Shot の ``workflow_override`` は素のモード id しか取らないままでよく、品質を
# 掛け合わせても強制指定の意味は変わらない。

#: 既定の品質（列が無い / 値が壊れている既存プロジェクトもこれとして扱う）
DEFAULT_QUALITY = "normal"

#: 品質 -> 画面に出す短い名前（``workflow_reason`` の文言に使う）
QUALITY_LABELS: dict[str, str] = {
    "normal": "通常",
    "opt": "Opt",
    "turbo": "Turbo",
}

#: 品質 -> （論理ワークフロー -> そのバリアント id）。論理ワークフローは
#: :func:`_pick_workflow` と :func:`_latent_save_workflow` を通ったあとの id
#: （素の t2v / i2v / r2v、ラテント保存版、連続カット版）で、全 7 通りに
#: turbo / opt のテンプレートが揃っている。ここに無い組み合わせは
#: 「バリアントが存在しない」＝素へフォールバックする。
QUALITY_WORKFLOWS: dict[str, dict[str, str]] = {
    "turbo": {
        WORKFLOW_T2V: "minimax_h3_t2v_turbo",
        WORKFLOW_I2V: "minimax_h3_i2v_turbo",
        WORKFLOW_R2V: "minimax_h3_r2v_turbo",
        "minimax_h3_t2v_save": "minimax_h3_t2v_save_turbo",
        "minimax_h3_i2v_save": "minimax_h3_i2v_save_turbo",
        "minimax_h3_r2v_save": "minimax_h3_r2v_save_turbo",
        WORKFLOW_R2V_CONTEXT: "minimax_h3_r2v_context_turbo",
    },
    "opt": {
        WORKFLOW_T2V: "minimax_h3_t2v_opt",
        WORKFLOW_I2V: "minimax_h3_i2v_opt",
        WORKFLOW_R2V: "minimax_h3_r2v_opt",
        "minimax_h3_t2v_save": "minimax_h3_t2v_save_opt",
        "minimax_h3_i2v_save": "minimax_h3_i2v_save_opt",
        "minimax_h3_r2v_save": "minimax_h3_r2v_save_opt",
        WORKFLOW_R2V_CONTEXT: "minimax_h3_r2v_context_opt",
    },
}

#: スタジオが投げうる turbo / opt のワークフロー id（接続先の対応判定用）
QUALITY_VARIANT_WORKFLOWS: frozenset[str] = frozenset(
    workflow for table in QUALITY_WORKFLOWS.values() for workflow in table.values()
)

#: ラテント連続性が ON のときにスタジオが投げうる、カスタムノード頼みの
#: ワークフロー（投入前に接続先の対応を確かめる: :func:`_require_latent_context`）。
#: 論理ワークフローだけでなく、その turbo / opt のバリアントも同じ
#: `MiniMaxH3MotionContext*` を使うので同じ扱いにする。
LATENT_CONTEXT_WORKFLOWS: frozenset[str] = frozenset(
    LATENT_CONTEXT_BASE_WORKFLOWS
    | {
        variant
        for table in QUALITY_WORKFLOWS.values()
        for base, variant in table.items()
        if base in LATENT_CONTEXT_BASE_WORKFLOWS
    }
)


def normalize_quality(value: Any) -> str:
    """DB や API から来た値を ``normal`` / ``opt`` / ``turbo`` に均す。

    列を持たない既存 DB（``None``）も、知らない値も既定の ``normal`` に倒す
    （品質は「速さの好み」でしかないので、読めない値で作品を開けなくしない）。
    """
    text = str(value or "").strip().lower()
    return text if text in QUALITY_LABELS else DEFAULT_QUALITY


def _quality_supported_on_target(workflow: str) -> bool:
    """いまの接続先で turbo / opt のバリアントを投げてよいか。

    turbo / opt はカスタムノード頼み（:data:`app.workflows.OPTIONAL_CLASS_TYPES`）
    なので、任意のノードを入れられない Comfy Cloud では動かない。判定は生成
    フォームの選択肢を絞るのと同じ :func:`app.workflow.supported_on_target` に
    任せる（対応の定義を 2 か所に散らさない）。
    """
    try:
        spec = get_video_spec(workflow)
        return supported_on_target(spec, load_settings().comfy_target)
    except (WorkflowSpecError, WorkflowError, OSError):
        # テンプレートが読めない環境では素へ落として投入だけは通す
        return False


def _quality_workflow(workflow: str, quality: str) -> tuple[str, str]:
    """``(解決したワークフロー id, 理由に足す一文)``。

    ``workflow`` は :func:`_pick_workflow` と :func:`_latent_save_workflow` を
    通ったあとの論理ワークフロー（素 / ラテント保存版 / 連続カット版）。

    ``quality`` が ``normal`` なら何もしない（付ける一文も無い）。それ以外は
    次のどちらかに当てはまれば**その論理ワークフローのまま**投げ、理由を返す:

    1. そのワークフローに turbo / opt のバリアントが無い（:data:`QUALITY_WORKFLOWS`）
    2. いまの接続先が turbo / opt のカスタムノードに対応しない（Comfy Cloud）

    黙って落とすのではなく、必ず ``workflow_reason`` に理由を出す。
    """
    quality = normalize_quality(quality)
    if quality == DEFAULT_QUALITY:
        return workflow, ""
    label = QUALITY_LABELS[quality]
    variant = QUALITY_WORKFLOWS[quality].get(workflow)
    if variant is None:
        return workflow, (
            f"品質「{label}」はこのモードに用意が無いので、通常品質で投入します"
        )
    if not _quality_supported_on_target(variant):
        return workflow, (
            f"いまの接続先は品質「{label}」のカスタムノードに対応しないので、"
            "通常品質で投入します"
        )
    return variant, f"品質「{label}」で投入します"


# --------------------------------------------------------------------------
# 画像生成の品質（プロジェクトの ``image_quality``）
# --------------------------------------------------------------------------
#
# 動画の ``quality`` とは**独立したつまみ**。素材の静止画（World Bible の
# キャラ・場所・小道具）を MiniMax H3 Image で焼くときにだけ効き、動画の品質は
# 一切見ない（動画を turbo で回していても素材の絵は素で焼きたい、という
# 使い分けのため）。値の並びと正規化は動画側と同じ（:func:`normalize_quality`）。
#
# いまのところプロジェクトから静止画を焼く経路はサーバ側に無く、素材画像を
# 作るのは**エージェント**（エージェントモード / 外部 API 経由の Claude Code・
# Cursor CLI）なので、この設定はまず「エージェントに伝える値」として持つ
# （:mod:`app.prompts` のスタジオ節）。解決そのものは動画側と同じ形で
# :func:`image_quality_workflow` に置いてあるので、UI から静止画を焼く経路が
# できたらそのまま使える。

#: 素材の静止画に使う MiniMax H3 Image のワークフロー
WORKFLOW_T2I = "minimax_h3_t2i"
WORKFLOW_I2I = "minimax_h3_i2i"
WORKFLOW_R2I = "minimax_h3_r2i"

#: 既定の画像品質（列が無い / 値が壊れている既存プロジェクトもこれとして扱う）
DEFAULT_IMAGE_QUALITY = "normal"

#: 画像品質 -> （論理ワークフロー -> そのバリアント id）。動画側の
#: :data:`QUALITY_WORKFLOWS` と同じ形で、t2i / i2i / r2i の 3 つに
#: ``_opt`` / ``_turbo`` が揃っている。
IMAGE_QUALITY_WORKFLOWS: dict[str, dict[str, str]] = {
    "turbo": {
        WORKFLOW_T2I: "minimax_h3_t2i_turbo",
        WORKFLOW_I2I: "minimax_h3_i2i_turbo",
        WORKFLOW_R2I: "minimax_h3_r2i_turbo",
    },
    "opt": {
        WORKFLOW_T2I: "minimax_h3_t2i_opt",
        WORKFLOW_I2I: "minimax_h3_i2i_opt",
        WORKFLOW_R2I: "minimax_h3_r2i_opt",
    },
}


def _image_quality_supported_on_target(workflow: str) -> bool:
    """いまの接続先で画像の turbo / opt を投げてよいか。

    動画側の :func:`_quality_supported_on_target` と同じ判定を画像スペックに
    かけるだけ（対応の定義は :func:`app.workflow.supported_on_target` 1 か所）。
    """
    try:
        spec = get_image_spec(workflow)
        return supported_on_target(spec, load_settings().comfy_target)
    except (WorkflowSpecError, WorkflowError, OSError):
        # テンプレートが読めない環境では素へ落として投入だけは通す
        return False


def image_quality_workflow(workflow_id: str, image_quality: str) -> str:
    """素材の静止画を焼くワークフロー id を ``image_quality`` で読み替える。

    ``workflow_id`` は素の ``minimax_h3_t2i`` / ``_i2i`` / ``_r2i``。
    ``image_quality`` が ``normal`` のとき、バリアントが無いとき、いまの接続先が
    turbo / opt のカスタムノードに対応しない（Comfy Cloud）ときは、渡された
    id をそのまま返す。**動画の ``quality`` は見ない。**
    """
    quality = normalize_quality(image_quality)
    if quality == DEFAULT_IMAGE_QUALITY:
        return workflow_id
    variant = IMAGE_QUALITY_WORKFLOWS[quality].get(workflow_id)
    if variant is None or not _image_quality_supported_on_target(variant):
        return workflow_id
    return variant


def image_render_defaults(project: StudioProject) -> dict[str, Any]:
    """素材の静止画ジョブに渡す画質まわりの値をまとめる。

    プロジェクトの ``image_megapixels`` / ``image_aspect_ratio`` /
    ``image_steps`` を「**設定されていれば渡す**」形で拾うだけ（未設定＝
    ``None`` / ``0`` の項目は入れない＝テンプレートの既定に任せる）。
    動画側の ``megapixels`` / ``aspect_ratio`` / ``steps`` は**見ない**
    （静止画に動画用の値を流用しないため）。
    """
    values: dict[str, Any] = {}
    megapixels = normalize_megapixels(project.image_megapixels)
    if megapixels is not None:
        values["megapixels"] = megapixels
    aspect_ratio = normalize_aspect_ratio(project.image_aspect_ratio)
    if aspect_ratio is not None:
        values["aspect_ratio"] = aspect_ratio
    steps = normalize_steps(project.image_steps)
    if steps:
        values["steps"] = steps
    return values


# --------------------------------------------------------------------------
# ラテントアップスケール（プロジェクトの ``latent_upscale``）
# --------------------------------------------------------------------------
#
# 品質（どのテンプレートで焼くか）や画質（何画素で焼くか）と直交する 3 つめの
# つまみで、決まったワークフローの **選択式**（``selects[latent_upscale]``）に
# 落ちる（ワークフロー id は変わらない）。効き方は
# **この 1 回ぶんの上書き > プロジェクト > 既定（ON）** の順。
#
# ON にできるかは接続先次第で、カスタムノード（``MinimaxH3LatentUpscaler3D``）を
# 入れられない Comfy Cloud では選択肢が ``off`` だけになる。そこへ ``on`` を
# 送るとジョブ側（:func:`app.jobs._pin_target_selects`）が 422 にするので、
# スタジオは投入前に off へ落とし、その理由を ``workflow_reason`` に足す。

#: 作品設定を持たない（＝プロジェクトが引けない）ときの既定
DEFAULT_LATENT_UPSCALE = True


def _latent_upscale_choices(workflow: str) -> tuple[str, ...]:
    """``workflow`` の ``latent_upscale`` で、いまの接続先が選べる値。

    宣言を持たないワークフロー（と、テンプレートが読めない環境）では空タプル
    ＝「このつまみは効かない」。判定は生成フォームの選択肢を絞るのと同じ
    :meth:`app.workflows.SelectSpec.choices_for_target` に任せる。
    """
    try:
        select = get_video_spec(workflow).select(LATENT_UPSCALE_NAME)
        if select is None:
            return ()
        return select.choices_for_target(load_settings().comfy_target)
    except (WorkflowSpecError, WorkflowError, OSError):
        return ()


def latent_upscale_available() -> bool:
    """いまの接続先でラテントアップスケールを ON にできるか（UI の出し分け用）。"""
    return "on" in _latent_upscale_choices(WORKFLOW_T2V)


def _resolve_selects(
    workflow: str, latent_upscale: bool
) -> tuple[dict[str, str], str]:
    """``(ジョブに載せる selects, 理由に足す一文)``。

    いまのところ中身は ``latent_upscale`` だけだが、選択式が増えても
    「ワークフローが宣言していて、接続先が許す値だけを載せる」というこの形の
    まま足せるようにしてある。宣言の無いワークフローには何も載せない
    （送っても効かないうえ、ジョブ側の検証を無駄に通すことになる）。
    """
    selects: dict[str, str] = {}
    reasons: list[str] = []
    choices = _latent_upscale_choices(workflow)
    if choices:
        wanted = "on" if latent_upscale else "off"
        if wanted in choices:
            selects[LATENT_UPSCALE_NAME] = wanted
        else:
            # 接続先が許すのは 1 つだけ（Comfy Cloud の "off"）。黙って落とす
            # のではなく、プレビューに理由を出す。
            selects[LATENT_UPSCALE_NAME] = choices[0]
            reasons.append(
                "いまの接続先はラテントアップスケールのカスタムノードに"
                "対応しないので、アップスケールなしで投入します"
            )
    return selects, " / ".join(reasons)


# --------------------------------------------------------------------------
# 動画生成の画質（プロジェクトの ``megapixels`` / ``aspect_ratio``）
# --------------------------------------------------------------------------
#
# 品質（``quality``）が「どのワークフローで焼くか」なのに対して、こちらは
# 「どれだけの画素数・どの画面比で焼くか」で、生成フォームと同じ 2 項目を作品
# 単位の既定として持つだけ。ワークフローの選択には一切かかわらない。
# 効き方は **Shot 個別 > プロジェクト > グローバル既定** の順で、2 つは
# それぞれ独立に解決する（:func:`render_shot`）。


def normalize_megapixels(value: Any) -> float | None:
    """DB や API から来た値を「正の実数」か ``None``（＝既定のまま）に均す。

    列を持たない既存 DB も、0 や負や数でない値も ``None`` に倒す（読めない値で
    作品を開けなくしない）。
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def normalize_aspect_ratio(value: Any) -> str | None:
    """空文字は ``None``（＝既定のまま）に均す。

    ラベルの妥当性は見ない。``app.workflow.parse_aspect_ratio`` が ``W:H`` で
    始まる文字列を広く受けるので、ComfyUI 側が比を増やしてもアプリの更新は
    要らない（Shot の ``aspect_ratio`` と同じ扱い）。
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


#: カットの尺（秒）として受け付ける範囲。MiniMax H3 は 1〜15 秒で、フォーム側の
#: 検証（`studio.ts` の `SHOT_DURATION_MIN` / `SHOT_DURATION_MAX`）と揃えてある。
SHOT_DURATION_MIN = 1.0
SHOT_DURATION_MAX = 15.0


def normalize_steps(value: Any) -> int:
    """DB や API から来た値を ``0``〜:data:`MAX_STEPS` の整数に均す。

    ``0`` は「未指定」= テンプレートの既定のまま。列を持たない既存 DB も、
    数でない値も ``0`` に倒す（読めない値で作品を開けなくしない）。上限を
    超えた値は上限で止める（読み取りは断らない。API から来た値の範囲検査は
    :func:`_checked_steps` が別に行う）。
    """
    if value is None:
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(number, MAX_STEPS))


def _checked_steps(value: Any) -> int:
    """API から来た ``steps`` を検査する（範囲外は :class:`StudioError`）。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise StudioError("ステップ数は整数で入れてください") from None
    if not 0 <= number <= MAX_STEPS:
        raise StudioError(
            f"ステップ数は 0〜{MAX_STEPS} です（0 = テンプレートの既定）"
        )
    return number


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

#: 1 プロジェクトが持てるリビジョンの数（超えたぶんは古いものから捨てる）。
#: 外部エージェントが脚本を書き換える運用では 1 セッションで何十件も積むので、
#: 「人の手が入った日まで戻れる」程度の深さを持たせてある。
REVISION_LIMIT = 1000

#: Shot のうち、書き換えると**プロンプトが変わる**項目。ここが動いたときだけ
#: `prompt_updated_at` を進め、それより古い Take が stale になる（採用・並べ替え
#: のような「本文に効かない更新」で作り直しを促さないため）。
PROMPT_FIELDS = frozenset({
    "prompt", "dialogue", "soundscape", "bgm", "camera", "duration_seconds",
    "aspect_ratio", "megapixels", "seed", "workflow_override",
    "english_prompt",
})

#: 素材のうち、書き換えるとプロンプト（または参照そのもの）が変わる項目。
ASSET_PROMPT_FIELDS = frozenset({"name", "caption", "prompt_caption", "kind", "path"})


class StudioError(Exception):
    """スタジオ操作の失敗（ルーターが 400 に変換する）。"""


class StudioNotFound(StudioError):
    """指したものが無い（ルーターが 404 に変換する）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# 画面への通知（WS ``type: "studio"``）
# --------------------------------------------------------------------------
#
# 外部エージェントが API から書き換えても、開いているブラウザがすぐ追いつける
# ようにするための配信。**書き込みが確定してから**流したいので、いったん接続に
# 積んでおいて :func:`_commit` が commit の後にまとめて流す（先に流すと、受け
# 取ったブラウザが古い状態を読み直してしまう）。

#: :func:`_queue_event` が積んだイベントの置き場（接続オブジェクトの属性名）。
_EVENTS_ATTR = "_studio_events"


def _queue_event(
    conn: aiosqlite.Connection,
    project_id: str,
    entity: str,
    entity_id: str = "",
    op: str = "update",
) -> None:
    """commit 後に流すスタジオイベントを 1 件積む。"""
    if not project_id:
        return
    events: list[tuple[str, str, str, str]] = getattr(conn, _EVENTS_ATTR, [])
    events.append((project_id, entity, entity_id, op))
    setattr(conn, _EVENTS_ATTR, events)


async def _commit(conn: aiosqlite.Connection) -> None:
    """commit して、この接続に積まれたスタジオイベントを流す。

    スタジオの書き込みはすべてここを通す（``conn.commit()`` を直に呼ばない）。
    配信に失敗しても書き込みは済んでいるので、例外は握りつぶして進む。
    """
    await conn.commit()
    events: list[tuple[str, str, str, str]] = getattr(conn, _EVENTS_ATTR, [])
    if not events:
        return
    setattr(conn, _EVENTS_ATTR, [])
    for project_id, entity, entity_id, op in events:
        try:
            await ws.publish_studio(project_id, entity, entity_id, op)
        except Exception:  # noqa: BLE001 - 通知の失敗で編集を壊さない
            log.debug("studio イベントを配信できませんでした: %s", project_id)


def _asset_url(kind: str, path: str) -> str:
    """静的配信 URL。メタデータのみの素材（パスなし）は空。"""
    return f"/assets/{kind}/{Path(path).name}" if path else ""


def _row_to_project(row: aiosqlite.Row) -> StudioProject:
    data = dict(row)
    data["auto_translate"] = bool(data.get("auto_translate", 1))
    data["latent_continuity"] = bool(data.get("latent_continuity", 0))
    data["latent_upscale"] = bool(data.get("latent_upscale", 1))
    data["quality"] = normalize_quality(data.get("quality"))
    data["image_quality"] = normalize_quality(data.get("image_quality"))
    data["megapixels"] = normalize_megapixels(data.get("megapixels"))
    data["aspect_ratio"] = normalize_aspect_ratio(data.get("aspect_ratio"))
    data["steps"] = normalize_steps(data.get("steps"))
    data["image_megapixels"] = normalize_megapixels(data.get("image_megapixels"))
    data["image_aspect_ratio"] = normalize_aspect_ratio(
        data.get("image_aspect_ratio")
    )
    data["image_steps"] = normalize_steps(data.get("image_steps"))
    data["nsfw"] = bool(data.get("nsfw", 0))
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
    data["english_prompt"] = data.get("english_prompt") or ""
    data["english_source"] = data.get("english_source") or ""
    data["english_status"] = data.get("english_status") or ""
    data["english_error"] = data.get("english_error") or ""
    return StudioShot(**data)


# --------------------------------------------------------------------------
# プロジェクト
# --------------------------------------------------------------------------

async def list_projects() -> list[StudioProjectSummary]:
    """一覧。1 行ごとに「どれだけ進んでいるか」の件数を添える。

    並びは**最後に触った順**（``updated_at``。空なら ``created_at``）。
    """
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
            " FROM studio_projects p"
            " ORDER BY COALESCE(NULLIF(p.updated_at, ''), p.created_at) DESC,"
            " p.id DESC"
        ) as cur:
            rows = await cur.fetchall()
    summaries = []
    for row in rows:
        data = dict(row)
        data["auto_translate"] = bool(data.get("auto_translate", 1))
        data["latent_continuity"] = bool(data.get("latent_continuity", 0))
        data["latent_upscale"] = bool(data.get("latent_upscale", 1))
        data["quality"] = normalize_quality(data.get("quality"))
        data["image_quality"] = normalize_quality(data.get("image_quality"))
        data["megapixels"] = normalize_megapixels(data.get("megapixels"))
        data["aspect_ratio"] = normalize_aspect_ratio(data.get("aspect_ratio"))
        data["steps"] = normalize_steps(data.get("steps"))
        data["image_megapixels"] = normalize_megapixels(
            data.get("image_megapixels")
        )
        data["image_aspect_ratio"] = normalize_aspect_ratio(
            data.get("image_aspect_ratio")
        )
        data["image_steps"] = normalize_steps(data.get("image_steps"))
        data["nsfw"] = bool(data.get("nsfw", 0))
        summaries.append(StudioProjectSummary(**data))
    return summaries


async def create_project(
    payload: StudioProjectCreate, *, actor: str = "user"
) -> StudioProject:
    """プロジェクトを 1 本作る（body をそのまま受ける）。

    項目が多い（画質だけで動画・静止画の 2 系統ある）ので、ルーターからは
    :class:`app.models.StudioProjectCreate` を丸ごと渡す約束にしてある。
    """
    if not (payload.name or "").strip():
        raise StudioError("プロジェクト名を入れてください")
    async with get_db() as conn:
        project = await _insert_project(conn, payload)
        await _record_revision(
            conn,
            project.id,
            actor,
            f"{_titled('プロジェクト', project.name)}を作成",
            entity_kind="project",
            entity_id=project.id,
            op="create",
        )
        await _commit(conn)
        return project


async def _insert_project(
    conn: aiosqlite.Connection, payload: StudioProjectCreate
) -> StudioProject:
    """プロジェクトを 1 行書く（``commit`` とリビジョンは呼び出し側）。"""
    project_id = new_id()
    now = _now()
    code = (payload.code or "").strip()
    try:
        await conn.execute(
            "INSERT INTO studio_projects"
            " (id, name, code, synopsis, world_notes, auto_translate,"
            "  latent_continuity, quality, megapixels, aspect_ratio, steps,"
            "  nsfw, latent_upscale, image_quality, image_megapixels,"
            "  image_aspect_ratio, image_steps, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project_id,
                (payload.name or "").strip(),
                code,
                payload.synopsis or "",
                payload.world_notes or "",
                1 if payload.auto_translate else 0,
                1 if payload.latent_continuity else 0,
                normalize_quality(payload.quality),
                normalize_megapixels(payload.megapixels),
                normalize_aspect_ratio(payload.aspect_ratio),
                normalize_steps(_checked_steps(payload.steps)),
                1 if payload.nsfw else 0,
                1 if payload.latent_upscale else 0,
                normalize_quality(payload.image_quality),
                normalize_megapixels(payload.image_megapixels),
                normalize_aspect_ratio(payload.image_aspect_ratio),
                normalize_steps(_checked_steps(payload.image_steps)),
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
    project_id: str,
    *,
    actor: str = "user",
    base_revision: int | None = None,
    **fields: Any,
) -> StudioProject | None:
    """``None`` の項目は触らない（指定されたものだけ書き換える）。

    ただし ``megapixels`` / ``aspect_ratio``（と素材画像側の
    ``image_megapixels`` / ``image_aspect_ratio``）は「既定へ戻す」を表す必要がある
    ので、**入っていれば** ``None``（NULL）も書く。「送らなかった」と「null を
    送った」の区別は :meth:`app.models.StudioProjectUpdate.changes` が済ませて
    いる（Shot 側と同じ約束）。
    """
    nullable = StudioProjectUpdate.NULLABLE
    changes = {
        key: value
        for key, value in fields.items()
        if value is not None or key in nullable
    }
    if "name" in changes:
        changes["name"] = str(changes["name"]).strip()
        if not changes["name"]:
            raise StudioError("プロジェクト名を空にはできません")
    if "code" in changes:
        changes["code"] = str(changes["code"]).strip()
    if "auto_translate" in changes:
        changes["auto_translate"] = 1 if changes["auto_translate"] else 0
    if "latent_continuity" in changes:
        changes["latent_continuity"] = 1 if changes["latent_continuity"] else 0
    if "latent_upscale" in changes:
        changes["latent_upscale"] = 1 if changes["latent_upscale"] else 0
    if "quality" in changes:
        changes["quality"] = normalize_quality(changes["quality"])
    if "image_quality" in changes:
        changes["image_quality"] = normalize_quality(changes["image_quality"])
    if "megapixels" in changes:
        changes["megapixels"] = normalize_megapixels(changes["megapixels"])
    if "aspect_ratio" in changes:
        changes["aspect_ratio"] = normalize_aspect_ratio(changes["aspect_ratio"])
    if "steps" in changes:
        changes["steps"] = _checked_steps(changes["steps"])
    if "image_megapixels" in changes:
        changes["image_megapixels"] = normalize_megapixels(
            changes["image_megapixels"]
        )
    if "image_aspect_ratio" in changes:
        changes["image_aspect_ratio"] = normalize_aspect_ratio(
            changes["image_aspect_ratio"]
        )
    if "image_steps" in changes:
        changes["image_steps"] = _checked_steps(changes["image_steps"])
    if "nsfw" in changes:
        changes["nsfw"] = 1 if changes["nsfw"] else 0
    async with get_db() as conn:
        project = await _fetch_project(conn, project_id)
        if project is None:
            return None
        if changes:
            await _check_base_revision(
                conn, project_id, base_revision, "project", project_id,
                project.name,
            )
            written = list(changes)
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
            await _record_revision(
                conn,
                project_id,
                actor,
                f"{_titled('プロジェクト', project.name)}を更新"
                f"{_changed_note(written)}",
                entity_kind="project",
                entity_id=project_id,
            )
            await _commit(conn)
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
        # リビジョンごと消える操作なので、通知はここで直に積む。
        if cur.rowcount > 0:
            _queue_event(conn, project_id, "project", project_id, "delete")
        await _commit(conn)
        return cur.rowcount > 0


async def project_detail(
    project_id: str, episode_id: str | None = None
) -> StudioProjectDetail | None:
    """画面 1 枚ぶん（話・場・素材・Shot・Take をジョブの状態つきで）。

    ``episode_id`` を渡すと**その話のぶんだけ**（場・Shot・Take）に絞る。話が
    増えても 1 回の取得が重くならないための入り口で、話の一覧と素材は画面
    （話タブと素材の表示）に要るので絞っても全件返す。話が無ければ ``None``
    ではなく :class:`StudioNotFound`（ルーターが 404 に変換する）。
    """
    async with get_db() as conn:
        project = await _fetch_project(conn, project_id)
        if project is None:
            return None
        if episode_id is not None:
            episode = await _fetch_episode(conn, episode_id)
            if episode is None or episode.project_id != project_id:
                raise StudioNotFound(f"episode not found: {episode_id}")
        assets = await _fetch_assets(conn, project_id)
        episodes = await _fetch_episodes(conn, project_id)
        if episode_id is None:
            scenes = await _fetch_scenes(conn, "project_id = ?", (project_id,))
        else:
            scenes = await _fetch_scenes(conn, "episode_id = ?", (episode_id,))
        shots = await _fetch_shots(conn, project_id, episode_id=episode_id)
        if episode_id is None:
            takes = await _fetch_takes(conn, "project_id = ?", (project_id,))
        elif shots:
            placeholders = ", ".join("?" * len(shots))
            takes = await _fetch_takes(
                conn,
                f"shot_id IN ({placeholders})",
                tuple(shot.id for shot in shots),
            )
        else:
            takes = []
        takes = _mark_stale(takes, shots, assets)
        revision_seq = await _revision_seq(conn, project_id)
    return StudioProjectDetail(
        **project.model_dump(),
        assets=assets,
        episodes=episodes,
        scenes=scenes,
        shots=shots,
        takes=takes,
        revision_seq=revision_seq,
    )


# --------------------------------------------------------------------------
# リビジョン履歴（軽量版）
# --------------------------------------------------------------------------
#
# プロジェクトの状態を変える操作のたびに、その**後**の状態を丸ごと 1 行に残す。
# 残すのは DB の行そのもの（projects / episodes / scenes / shots / assets /
# takes / 編集タブの EDL）で、ジョブから導出する実行状態とファイル実体は
# 入れない: どちらも履歴では消えないので、戻す必要がない。

#: スナップショットに入れるテーブルと、その主キー以外の並び順。**親から順**に
#: 並べる（書き戻しはこの順に DELETE -> INSERT するので、子が先に入ると FK で
#: 落ちる）。
_SNAPSHOT_TABLES = (
    ("episodes", "studio_episodes", "sort_order, created_at, id"),
    ("scenes", "studio_scenes", "sort_order, created_at, id"),
    ("shots", "studio_shots", "sort_order, created_at, id"),
    # Take は Shot にぶら下がる（studio_takes.shot_id -> studio_shots.id）ので
    # **Shot のあと**。成果物のファイルとジョブは消さないので、行さえ戻せば
    # 「消した Take を取り消す」まで辿れる（戻し方は _KEEP_UNKNOWN_ROWS）。
    ("takes", "studio_takes", "shot_id, created_at, id"),
    ("assets", "studio_assets", "sort_order, created_at, id"),
    # 素材のリファレンスは**素材のあと**（親が戻る前に入れると FK で落ちる）。
    ("asset_files", "studio_asset_files", "sort_order, created_at, id"),
    # 編集タブの EDL（タイムライン -> トラック -> クリップ）。親から順に戻す。
    # 書き出し（timeline_exports）は実行結果なので対象外。
    ("timelines", "studio_timelines", "created_at, id"),
    ("timeline_tracks", "timeline_tracks", "sort_order, id"),
    ("timeline_clips", "timeline_clips", "track_id, start_ms, id"),
    # タイムラインに載せた演出（FX トラック、SPEC §7.3）。全体設定はタイム
    # ラインに 1 行、イベントは並び順で戻す。
    ("timeline_fx", "timeline_fx", "timeline_id"),
    ("timeline_fx_events", "timeline_fx_events", "timeline_id, sort_order, id"),
)

#: 行の突き合わせに ``id`` 以外の列を使うテーブル（:func:`_restore_table`）。
#: ``timeline_fx`` は 1 タイムラインに 1 行なので、主キーが ``timeline_id``。
_SNAPSHOT_KEYS = {"timeline_fx": "timeline_id"}

#: 復元の直前に自動で残すリビジョンの説明。復元は行を消しうるうえ、生成
#: （Take）は履歴を作らないので、これが無いと「復元で消えた Take」を戻せない。
RESTORE_BACKUP_ACTION = "復元前の自動スナップショット"

#: 丸ごと復元で**スナップショットに無い行を消さない**テーブル。
#:
#: Take は実行結果で、しかも**生成そのものはリビジョンを作らない**（焼いても
#: 履歴は増えない）。素直に DELETE -> INSERT すると、脚本をひとつ戻しただけで
#: その直後に焼いた Take の目録が消えてしまう。復元の意味論は「載っているものは
#: その時点の状態へ戻す・知らないものは触らない」にする。採用（``selected_take_id``）
#: はスナップショット側の値に戻るので、残った新しい Take は候補としてぶら下がる
#: だけになる。
_KEEP_UNKNOWN_ROWS = frozenset({"studio_takes"})

#: エンティティの種別 -> （スナップショットのキー, テーブル, 見出しの列,
#: 日本語の呼び名）。差分（:func:`diff_revision`）と部分復元
#: （:func:`restore_revision`）の両方がこの 1 枚を見る。並び順は差分に出す順
#: （大きい入れ物から順）で、``project`` だけは行の配列ではなく 1 件の dict。
_ENTITIES: dict[str, tuple[str, str, str, str]] = {
    "project": ("project", "studio_projects", "name", "プロジェクト"),
    "episode": ("episodes", "studio_episodes", "title", "話"),
    "scene": ("scenes", "studio_scenes", "title", "場"),
    "shot": ("shots", "studio_shots", "title", "カット"),
    "take": ("takes", "studio_takes", "", "Take"),
    "asset": ("assets", "studio_assets", "name", "素材"),
    "asset_file": ("asset_files", "studio_asset_files", "caption",
                   "素材のリファレンス"),
    "timeline": ("timelines", "studio_timelines", "name", "タイムライン"),
    "timeline_track": ("timeline_tracks", "timeline_tracks", "name", "トラック"),
    "timeline_clip": ("timeline_clips", "timeline_clips", "", "クリップ"),
}

#: 差分に出さない列。id は突き合わせのキーで、残りは「いつ書いたか」の記録
#: （中身が変わっていなくても毎回動くので、出すと差分がノイズだらけになる）。
_DIFF_NOISE = frozenset({
    "id", "project_id", "created_at", "updated_at", "prompt_updated_at",
    "snapshot_json",
})


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


def _titled(label: str, name: str, fallback: str = "") -> str:
    """``カット『決裂』`` の形。名前が空なら id（それも無ければ呼び名だけ）。"""
    text = (name or "").strip() or (fallback or "").strip()
    return f"{label}『{text}』" if text else label


def _changed_note(changes: Any) -> str:
    """``(prompt, dialogue)``。書き換えた項目を履歴の説明に足すための添え字。

    ``updated_at`` のような記録用の列は落とす（人が読んで意味のある項目だけ）。
    """
    names = [str(name) for name in changes if str(name) not in _DIFF_NOISE]
    return f"({', '.join(names)})" if names else ""


async def _record_revision(
    conn: aiosqlite.Connection,
    project_id: str,
    actor: str,
    action: str,
    *,
    entity_kind: str = "",
    entity_id: str = "",
    op: str = "update",
) -> None:
    """今のプロジェクト状態を 1 リビジョンとして残す（commit は呼び出し側）。

    ``action`` は「どのエンティティのどの項目を変えたか」まで書く
    （``カット『決裂』を更新(prompt, dialogue)``）。差分エンドポイントを開かなく
    ても一覧だけで何が起きたか見当がつくようにするため。

    ``entity_kind`` / ``entity_id`` は**1 件だけを触る操作**のときに入れる
    （「このカットの履歴」の絞り込みに使う）。並べ替えや一括作成のように複数へ
    跨る操作は空のままにする: 代表を 1 つ選ぶと、選ばれなかったほうの履歴から
    その変更が消えてしまうため。Take の操作はぶら下がっている**カット**として
    記録する（Take はカットの中の出来事で、そのカットの履歴に出したいもの）。

    リビジョンが 1 つ残るような変更は、そのまま「画面を読み直す理由」でもある
    ので、ここで WS のスタジオイベント（``type: "studio"``）も積む。``op`` は
    その 1 件が create / update / delete のどれだったか（受け取り側は読み直す
    だけなので、厳密でなくても困らない）。
    """
    _queue_event(conn, project_id, entity_kind or "project", entity_id, op)
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
        " (id, project_id, seq, actor, action, entity_kind, entity_id,"
        "  snapshot_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            new_id(),
            project_id,
            seq,
            actor if actor in STUDIO_REVISION_ACTORS else "user",
            action,
            entity_kind if entity_kind in _ENTITIES else "",
            entity_id or "",
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


async def _revision_seq(conn: aiosqlite.Connection, project_id: str) -> int:
    async with conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS seq FROM studio_revisions"
        " WHERE project_id = ?",
        (project_id,),
    ) as cur:
        row = await cur.fetchone()
    return int(row["seq"] if row else 0)


async def current_revision_seq(project_id: str) -> int:
    """いまのリビジョン連番（無ければ 0）。スナップショットの鮮度判定用。"""
    async with get_db() as conn:
        return await _revision_seq(conn, project_id)


async def list_revisions(
    project_id: str,
    *,
    entity_kind: str | None = None,
    entity_id: str | None = None,
) -> list[StudioRevision]:
    """新しい順の見出し一覧。

    ``entity_kind`` / ``entity_id`` を渡すと**その 1 件を触った履歴だけ**に絞る
    （「このカットの履歴」）。絞り込みは記録した id で行うので、同じ名前のカットが
    あっても混ざらず、改名しても履歴が消えない。
    """
    where = ["project_id = ?"]
    params: list[Any] = [project_id]
    if entity_kind:
        where.append("entity_kind = ?")
        params.append(entity_kind)
    if entity_id:
        where.append("entity_id = ?")
        params.append(entity_id)
    async with get_db() as conn:
        async with conn.execute(
            "SELECT seq, actor, action, entity_kind, entity_id, created_at"
            f" FROM studio_revisions WHERE {' AND '.join(where)}"
            " ORDER BY seq DESC",
            tuple(params),
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
        entity_kind=row["entity_kind"],
        entity_id=row["entity_id"],
        created_at=row["created_at"],
        snapshot=snapshot if isinstance(snapshot, dict) else {},
    )


# --------------------------------------------------------------------------
# リビジョンの差分
# --------------------------------------------------------------------------
#
# スナップショット 2 枚を id で突き合わせて、「どのエンティティの何が動いたか」
# にほどく。履歴の一覧から 1 行ぶんの中身を見るため（GET .../{seq}/diff）と、
# 楽観ロックが「この更新とぶつかる変更が入っていたか」を判定するため
# （:func:`_check_base_revision`）の両方で使う。

def _entity_name(row: dict[str, Any], column: str) -> str:
    """そのエンティティの見出し（title / name 相当。無ければ id）。"""
    if column:
        text = str(row.get(column) or "").strip()
        if text:
            return text
    return str(row.get("id") or "")


def _field_diffs(
    before: dict[str, Any], after: dict[str, Any]
) -> list[StudioRevisionFieldDiff]:
    """値の違う列だけを並べる（記録用の列は見ない）。"""
    return [
        StudioRevisionFieldDiff(
            field=name, before=before.get(name), after=after.get(name)
        )
        for name in dict.fromkeys((*before, *after))
        if name not in _DIFF_NOISE and before.get(name) != after.get(name)
    ]


def _rows_by_id(rows: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        return {}
    return {
        str(row["id"]): row
        for row in rows
        if isinstance(row, dict) and row.get("id")
    }


def _snapshot_changes(
    before: dict[str, Any], after: dict[str, Any]
) -> list[StudioRevisionEntityDiff]:
    """スナップショット 2 枚の差分（``before`` が空なら全件 ``create``）。

    ``before`` に**そもそも無いキー**（その面を記録する前に取ったスナップ
    ショット）は触らない: 「空だった」と読むと、機能を足しただけで全件が
    作成・削除に見えてしまう。
    """
    changes: list[StudioRevisionEntityDiff] = []
    fresh = not before  # 直前が無い = 最初のリビジョン
    for entity, (key, _table, name_column, _label) in _ENTITIES.items():
        if not fresh and key not in before and key in after:
            continue
        if entity == "project":
            old = before.get(key) or {}
            new = after.get(key) or {}
            old_rows = {str(old["id"]): old} if old.get("id") else {}
            new_rows = {str(new["id"]): new} if new.get("id") else {}
        else:
            old_rows = _rows_by_id(before.get(key))
            new_rows = _rows_by_id(after.get(key))
        for row_id, new_row in new_rows.items():
            old_row = old_rows.get(row_id)
            if old_row is None:
                changes.append(StudioRevisionEntityDiff(
                    entity=entity,
                    id=row_id,
                    name=_entity_name(new_row, name_column),
                    op="create",
                ))
                continue
            fields = _field_diffs(old_row, new_row)
            if fields:
                changes.append(StudioRevisionEntityDiff(
                    entity=entity,
                    id=row_id,
                    name=_entity_name(new_row, name_column),
                    op="update",
                    fields=fields,
                ))
        for row_id, old_row in old_rows.items():
            if row_id not in new_rows:
                changes.append(StudioRevisionEntityDiff(
                    entity=entity,
                    id=row_id,
                    name=_entity_name(old_row, name_column),
                    op="delete",
                ))
    return changes


async def _snapshot_of(
    conn: aiosqlite.Connection, project_id: str, seq: int
) -> dict[str, Any] | None:
    """``seq`` のスナップショット（無ければ ``None``）。"""
    if seq <= 0:
        return {}
    async with conn.execute(
        "SELECT snapshot_json FROM studio_revisions"
        " WHERE project_id = ? AND seq = ?",
        (project_id, seq),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    try:
        snapshot = json.loads(row["snapshot_json"])
    except ValueError:  # pragma: no cover - 自分で書いた JSON なので通らない
        return {}
    return snapshot if isinstance(snapshot, dict) else {}


async def diff_revision(project_id: str, seq: int) -> StudioRevisionDiff | None:
    """``seq`` と**その直前**（``seq - 1``、無ければ空）の差分。"""
    revision = await get_revision(project_id, seq)
    if revision is None:
        return None
    async with get_db() as conn:
        before = await _snapshot_of(conn, project_id, seq - 1)
    return StudioRevisionDiff(
        seq=revision.seq,
        actor=revision.actor,
        action=revision.action,
        entity_kind=revision.entity_kind,
        entity_id=revision.entity_id,
        created_at=revision.created_at,
        changes=_snapshot_changes(before or {}, revision.snapshot),
    )


async def _check_base_revision(
    conn: aiosqlite.Connection,
    project_id: str,
    base_revision: int | None,
    entity: str,
    entity_id: str,
    name: str,
) -> None:
    """``base_revision`` 以降に**同じエンティティ**が触られていたら 409。

    プロジェクト全体の連番だけで比べると、別のカットを直しただけの並行編集まで
    全部ぶつかってしまう。差分を取り直して、対象の ``entity`` + ``id`` が動いて
    いるときだけ :class:`StudioConflict` を投げる。

    比べる相手のリビジョンが（古すぎて）残っていないときは「変わっていない」と
    言い切れないので、こちらもぶつかったものとして扱う。

    まだ存在しない連番（現在より**大きい**値）は「過去に読んだ状態」ではありえ
    ないので、素通しせず :class:`StudioError`（400）にする。
    """
    if base_revision is None:
        return
    current = await _revision_seq(conn, project_id)
    if base_revision > current:
        raise StudioError(
            f"base_revision {base_revision} はまだ存在しません"
            f"（現在のリビジョン {current}）"
        )
    if base_revision == current:
        return
    base = await _snapshot_of(conn, project_id, base_revision)
    label = _ENTITIES[entity][3]
    if base is None:
        raise StudioConflict(
            f"リビジョン {base_revision} は履歴に残っていないので"
            f"{_titled(label, name, entity_id)}の変更を確かめられません"
            f"（現在のリビジョン {current}）"
        )
    changed = [
        change
        for change in _snapshot_changes(base, await _snapshot(conn, project_id))
        if change.entity == entity and change.id == entity_id
    ]
    if not changed:
        return
    fields = [field.field for change in changed for field in change.fields]
    note = f"（衝突: {', '.join(fields)}）" if fields else ""
    raise StudioConflict(
        f"{_titled(label, name, entity_id)}は他の変更で更新されています"
        f"（現在のリビジョン {current}）{note}"
    )


def _restore_columns(rows: list[dict[str, Any]], columns: set[str]) -> list[str]:
    """スナップショットと今のテーブルの**両方にある**カラムだけを書き戻す。"""
    if not rows:
        return []
    return [name for name in rows[0] if name in columns]


async def _table_columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        return {row["name"] for row in await cur.fetchall()}


async def restore_revision(
    project_id: str,
    seq: int,
    *,
    entity: str | None = None,
    entity_id: str | None = None,
    fields: list[str] | None = None,
    actor: str = "user",
) -> StudioProjectDetail | None:
    """``seq`` のスナップショットの内容でプロジェクトを書き戻す。

    ``entity`` を渡すと**その 1 件だけ**（``fields`` まで渡すとその項目だけ）を
    戻す部分復元になる（:func:`_restore_one`）。渡さなければ今までどおり丸ごと。

    Take だけはスナップショットに無い行を消さない（:data:`_KEEP_UNKNOWN_ROWS`）。
    ファイル実体（``assets/`` と ``outputs/``）とジョブはどちらの経路でも触らない。

    書き換える**前**に今の状態を 1 リビジョンとして残し（:data:`RESTORE_BACKUP_ACTION`）、
    復元後の状態ももう 1 件残す。前者が要るのは、生成（Take）が履歴を作らない
    ため: 復元でカットが消えるとぶら下がっていた Take も一緒に落ち、直前の
    リビジョンにも載っていないと二度と辿れなくなる。
    """
    revision = await get_revision(project_id, seq)
    if revision is None:
        return None
    snapshot = revision.snapshot
    if entity is not None:
        return await _restore_one(
            project_id, seq, snapshot, entity, entity_id, fields, actor
        )
    async with get_db() as conn:
        if await _fetch_project(conn, project_id) is None:
            return None
        # 触る前の状態を 1 件残す（この復元で消えるものへの戻り道）。
        await _record_revision(conn, project_id, actor, RESTORE_BACKUP_ACTION)
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
            # 機能を足す前に取ったスナップショットには、そのテーブルのキーが
            # そもそも無い。「空だった」と読むとその面（たとえば編集タブの
            # タイムライン）を丸ごと消してしまうので、載っていない面は触らない。
            if key not in snapshot:
                continue
            await _restore_table(
                conn,
                project_id,
                table,
                snapshot.get(key) or [],
                prune=table not in _KEEP_UNKNOWN_ROWS,
                key_column=_SNAPSHOT_KEYS.get(table, "id"),
            )
        # 採用したまま消えてしまった Take への参照を外す。Take もスナップ
        # ショットに入るようになったので普段は空振りするが、takes を記録する
        # 前の古いスナップショット（= Take を触らない復元）ではまだ起こる。
        await conn.execute(
            "UPDATE studio_shots SET selected_take_id = NULL"
            " WHERE project_id = ? AND selected_take_id IS NOT NULL"
            " AND selected_take_id NOT IN (SELECT id FROM studio_takes)",
            (project_id,),
        )
        # 復元で消えた場に残った Shot は未分類へ戻す。並び順は場の中のものなので、
        # 戻したあとの未分類グループを 0..n に詰め直す（元の相対順は保つ）。
        await conn.execute(
            "UPDATE studio_shots SET scene_id = NULL"
            " WHERE project_id = ? AND scene_id IS NOT NULL"
            " AND scene_id NOT IN (SELECT id FROM studio_scenes)",
            (project_id,),
        )
        for order, shot_id in enumerate(
            await _scope_shot_ids(conn, project_id, None)
        ):
            await conn.execute(
                "UPDATE studio_shots SET sort_order = ? WHERE id = ?",
                (order, shot_id),
            )
        await _record_revision(
            conn, project_id, actor, f"リビジョン {seq} を復元"
        )
        await _commit(conn)
    return await project_detail(project_id)


async def _restore_one(
    project_id: str,
    seq: int,
    snapshot: dict[str, Any],
    entity: str,
    entity_id: str | None,
    fields: list[str] | None,
    actor: str,
) -> StudioProjectDetail | None:
    """スナップショットの 1 行（``fields`` を渡せばその項目だけ）を書き戻す。

    今その行が無ければスナップショットの行をそのまま INSERT で作り直す
    （「消したカットのこの 1 項目だけ戻す」も同じ入り口で通る）。
    """
    if entity not in _ENTITIES:
        raise StudioError(f"戻せない種別です: '{entity}'")
    key, table, name_column, label = _ENTITIES[entity]
    if key not in snapshot:
        raise StudioError(
            f"リビジョン {seq} は{label}を記録していません（古い履歴）"
        )
    if entity == "project":
        row = snapshot.get(key) or {}
        entity_id = str(row.get("id") or project_id)
    else:
        rows = _rows_by_id(snapshot.get(key))
        if not entity_id:
            raise StudioError("戻す対象の id を指定してください")
        row = rows.get(entity_id) or {}
    if not row:
        raise StudioError(
            f"リビジョン {seq} にその{label}（{entity_id}）はありません"
        )
    async with get_db() as conn:
        if await _fetch_project(conn, project_id) is None:
            return None
        # 丸ごと復元と同じで、書き換える前の状態を 1 件残す（戻り道）。
        await _record_revision(conn, project_id, actor, RESTORE_BACKUP_ACTION)
        columns = await _table_columns(conn, table)
        async with conn.execute(
            f"SELECT id FROM {table} WHERE id = ?", (entity_id,)
        ) as cur:
            exists = await cur.fetchone() is not None
        if fields is None:
            names = [name for name in row if name in columns and name != "id"]
        else:
            unknown = [
                name for name in fields if name not in columns or name not in row
            ]
            if unknown:
                raise StudioError(
                    f"{label}に無い項目です: {', '.join(unknown)}"
                )
            names = [name for name in fields if name != "id"]
        if not names:
            raise StudioError("戻す項目がありません")
        try:
            if exists:
                assignments = ", ".join(f"{name} = ?" for name in names)
                await conn.execute(
                    f"UPDATE {table} SET {assignments} WHERE id = ?",
                    (*(row[name] for name in names), entity_id),
                )
            else:
                # 行ごと消えているので作り直す。指定された項目だけでは NOT NULL
                # を満たせないので、スナップショットにある列は全部入れる。
                insert = [name for name in row if name in columns]
                placeholders = ", ".join("?" * len(insert))
                await conn.execute(
                    f"INSERT INTO {table} ({', '.join(insert)})"
                    f" VALUES ({placeholders})",
                    [row[name] for name in insert],
                )
        except aiosqlite.IntegrityError as exc:
            raise StudioError(
                f"{label}を戻せませんでした（{exc}）"
            ) from exc
        note = f"の {', '.join(fields)} " if fields else ""
        await _record_revision(
            conn,
            project_id,
            actor,
            f"{_titled(label, _entity_name(row, name_column), entity_id)}"
            f"{note}をリビジョン {seq} へ戻す",
            entity_kind=entity,
            entity_id=entity_id or "",
        )
        await _commit(conn)
    return await project_detail(project_id)


async def _restore_table(
    conn: aiosqlite.Connection,
    project_id: str,
    table: str,
    rows: list[dict[str, Any]],
    *,
    prune: bool = True,
    key_column: str = "id",
) -> None:
    """``table`` のプロジェクトぶんの行を ``rows`` の通りにする。

    ``prune`` を降ろすと**スナップショットに無い行を消さない**（載っている行を
    戻すだけ）。:data:`_KEEP_UNKNOWN_ROWS` の実行結果向け。

    ``key_column`` は行の突き合わせに使う列（既定は ``id``）。1 タイムラインに
    1 行しか無い ``timeline_fx`` だけ ``timeline_id`` で突き合わせる。
    """
    columns = await _table_columns(conn, table)
    names = _restore_columns(rows, columns)
    wanted = {str(row[key_column]) for row in rows if row.get(key_column)}
    async with conn.execute(
        f"SELECT {key_column} AS key FROM {table} WHERE project_id = ?", (project_id,)
    ) as cur:
        current = {str(row["key"]) for row in await cur.fetchall()}
    if prune:
        for stale_id in current - wanted:
            await conn.execute(
                f"DELETE FROM {table} WHERE {key_column} = ?", (stale_id,)
            )
    for row in rows:
        row_id = str(row.get(key_column) or "")
        if not row_id:
            continue
        values = [row[name] for name in names]
        if row_id in current:
            assignments = ", ".join(f"{name} = ?" for name in names)
            await conn.execute(
                f"UPDATE {table} SET {assignments} WHERE {key_column} = ?",
                (*values, row_id),
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


async def _insert_episode(
    conn: aiosqlite.Connection, project_id: str, payload: Any
) -> StudioEpisode:
    """話を 1 件書く（``commit`` とリビジョンは呼び出し側）。"""
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
    return await _fetch_episode(conn, episode_id)  # type: ignore[return-value]


async def create_episode(
    project_id: str, payload: Any, *, actor: str = "user"
) -> StudioEpisode:
    async with get_db() as conn:
        episode = await _insert_episode(conn, project_id, payload)
        await _record_revision(
            conn,
            project_id,
            actor,
            f"{_titled('話', episode.title)}を追加",
            entity_kind="episode",
            entity_id=episode.id,
            op="create",
        )
        await _commit(conn)
        return episode


async def update_episode(
    episode_id: str,
    *,
    actor: str = "user",
    base_revision: int | None = None,
    **fields: Any,
) -> StudioEpisode | None:
    changes = {key: value for key, value in fields.items() if value is not None}
    async with get_db() as conn:
        episode = await _fetch_episode(conn, episode_id)
        if episode is None:
            return None
        if changes:
            await _check_base_revision(
                conn, episode.project_id, base_revision, "episode", episode_id,
                episode.title,
            )
            assignments = ", ".join(f"{key} = ?" for key in changes)
            await conn.execute(
                f"UPDATE studio_episodes SET {assignments} WHERE id = ?",
                (*changes.values(), episode_id),
            )
            await _record_revision(
                conn,
                episode.project_id,
                actor,
                f"{_titled('話', episode.title, episode_id)}を更新"
                f"{_changed_note(changes)}",
                entity_kind="episode",
                entity_id=episode_id,
            )
            await _commit(conn)
        return await _fetch_episode(conn, episode_id)


async def delete_episode(episode_id: str, *, actor: str = "user") -> bool:
    """配下の場ごと消す。場にいた Shot は未分類（``scene_id = NULL``）に戻る。"""
    async with get_db() as conn:
        episode = await _fetch_episode(conn, episode_id)
        if episode is None:
            return False
        scenes = await _fetch_scenes(conn, "episode_id = ?", (episode_id,))
        for scene in scenes:
            await _detach_shots(conn, episode.project_id, scene.id)
        await conn.execute("DELETE FROM studio_episodes WHERE id = ?", (episode_id,))
        await _record_revision(
            conn,
            episode.project_id,
            actor,
            f"{_titled('話', episode.title, episode_id)}を削除"
            f"（場 {len(scenes)} 件ごと）",
            entity_kind="episode",
            entity_id=episode_id,
            op="delete",
        )
        await _commit(conn)
        return True


async def reorder_episodes(
    project_id: str, ids: list[str], *, actor: str = "user"
) -> list[StudioEpisode]:
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
        await _record_revision(
            conn, project_id, actor, f"話を並べ替え（{len(ids)} 件）"
        )
        await _commit(conn)
        return await _fetch_episodes(conn, project_id)


async def _insert_scene(
    conn: aiosqlite.Connection, episode_id: str, payload: Any
) -> StudioScene:
    """場を 1 件書く（``commit`` とリビジョンは呼び出し側）。"""
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
    return await _fetch_scene(conn, scene_id)  # type: ignore[return-value]


async def create_scene(
    episode_id: str, payload: Any, *, actor: str = "user"
) -> StudioScene:
    async with get_db() as conn:
        scene = await _insert_scene(conn, episode_id, payload)
        await _record_revision(
            conn,
            scene.project_id,
            actor,
            f"{_titled('場', scene.title)}を追加",
            entity_kind="scene",
            entity_id=scene.id,
            op="create",
        )
        await _commit(conn)
        return scene


async def update_scene(
    scene_id: str,
    *,
    actor: str = "user",
    base_revision: int | None = None,
    **fields: Any,
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
            await _check_base_revision(
                conn, scene.project_id, base_revision, "scene", scene_id,
                scene.title,
            )
            assignments = ", ".join(f"{key} = ?" for key in changes)
            await conn.execute(
                f"UPDATE studio_scenes SET {assignments} WHERE id = ?",
                (*changes.values(), scene_id),
            )
            await _record_revision(
                conn,
                scene.project_id,
                actor,
                f"{_titled('場', scene.title, scene_id)}を更新"
                f"{_changed_note(changes)}",
                entity_kind="scene",
                entity_id=scene_id,
            )
            await _commit(conn)
        return await _fetch_scene(conn, scene_id)


async def _detach_shots(
    conn: aiosqlite.Connection, project_id: str, scene_id: str
) -> None:
    """場から Shot を外す（Shot 自体は消さない。未分類に戻すだけ）。

    並び順は場の中のものなので、そのまま外すと未分類グループの番号と混ざる。
    元の並びを保ったまま未分類グループの末尾へ付け直す。
    """
    detached = await _scope_shot_ids(conn, project_id, scene_id)
    if not detached:
        return
    base = await _next_shot_sort_order(conn, project_id, None)
    for offset, shot_id in enumerate(detached):
        await conn.execute(
            "UPDATE studio_shots SET scene_id = NULL, sort_order = ? WHERE id = ?",
            (base + offset, shot_id),
        )


async def delete_scene(scene_id: str, *, actor: str = "user") -> bool:
    """場だけ消す。そこにいた Shot は未分類（``scene_id = NULL``）に戻る。"""
    async with get_db() as conn:
        scene = await _fetch_scene(conn, scene_id)
        if scene is None:
            return False
        await _detach_shots(conn, scene.project_id, scene_id)
        await conn.execute("DELETE FROM studio_scenes WHERE id = ?", (scene_id,))
        await _record_revision(
            conn,
            scene.project_id,
            actor,
            f"{_titled('場', scene.title, scene_id)}を削除",
            entity_kind="scene",
            entity_id=scene_id,
            op="delete",
        )
        await _commit(conn)
        return True


async def reorder_scenes(
    episode_id: str, ids: list[str], *, actor: str = "user"
) -> list[StudioScene]:
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
        await _record_revision(
            conn,
            episode.project_id,
            actor,
            f"{_titled('話', episode.title, episode_id)}の場を並べ替え"
            f"（{len(ids)} 件）",
        )
        await _commit(conn)
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
            StudioProjectCreate(
                name=manifest["name"],
                code=manifest["code"],
                synopsis=manifest.get("synopsis", ""),
                world_notes=manifest.get("world_notes", ""),
            ),
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
                # 並び順は場の中のものなので、場ごとに 0 から振る。
                for shot_order, shot in enumerate(scene.get("shots", ())):
                    await _insert_demo_shot(
                        conn, project.id, scene_id, shot_order, shot, now
                    )
        await _record_revision(
            conn,
            project.id,
            "user",
            f"デモ『{manifest['name']}』を作成",
            op="create",
        )
        await _commit(conn)
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
            entity_kind="asset",
            entity_id=asset_id,
            op="create",
        )
        await _commit(conn)
        async with conn.execute(
            "SELECT * FROM studio_asset_files WHERE id = ?", (file_id,)
        ) as cur:
            row = await cur.fetchone()
    return _row_to_asset_file(row)  # type: ignore[arg-type]


async def delete_asset_file(file_id: str, *, actor: str = "user") -> bool:
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
            conn,
            row["project_id"],
            actor,
            f"素材のリファレンス（{row['role']}）を削除",
            entity_kind="asset",
            entity_id=row["asset_id"],
            op="delete",
        )
        await _commit(conn)
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
        await _record_revision(
            conn,
            project_id,
            actor,
            f"素材『{label}』を追加",
            entity_kind="asset",
            entity_id=asset.id,
            op="create",
        )
        await _commit(conn)
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
    asset_id: str,
    *,
    actor: str = "user",
    base_revision: int | None = None,
    **fields: Any,
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
            await _check_base_revision(
                conn, asset.project_id, base_revision, "asset", asset_id,
                asset.name,
            )
            written = list(changes)
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
                conn,
                asset.project_id,
                actor,
                f"{_titled('素材', asset.name, asset_id)}を更新"
                f"{_changed_note(written)}",
                entity_kind="asset",
                entity_id=asset_id,
            )
            await _commit(conn)
        return await _fetch_asset(conn, asset_id)


async def delete_asset(asset_id: str, *, actor: str = "user") -> bool:
    """目録から外す（ファイル実体は ``assets/`` に残す）。"""
    async with get_db() as conn:
        asset = await _fetch_asset(conn, asset_id)
        if asset is None:
            return False
        await conn.execute("DELETE FROM studio_assets WHERE id = ?", (asset_id,))
        await _record_revision(
            conn,
            asset.project_id,
            actor,
            f"素材『{asset.name}』を削除",
            entity_kind="asset",
            entity_id=asset_id,
            op="delete",
        )
        await _commit(conn)
        return True


# --------------------------------------------------------------------------
# Shot（脚本）
# --------------------------------------------------------------------------

#: Shot の表示順（話 -> 場 -> カット）。``sort_order`` は場の中での順番なので、
#: 上の 2 段を JOIN してから並べる。どこの場にも入っていない Shot
#: （``scene_id IS NULL`` = 未分類）は作品の末尾へまとめる。
_SHOT_ORDER = (
    " ORDER BY s.scene_id IS NULL,"
    "  ep.sort_order, ep.created_at, ep.id,"
    "  sc.sort_order, sc.created_at, sc.id,"
    "  s.sort_order, s.created_at, s.id"
)

_SHOT_JOIN = (
    "SELECT s.* FROM studio_shots s"
    " LEFT JOIN studio_scenes sc ON sc.id = s.scene_id"
    " LEFT JOIN studio_episodes ep ON ep.id = sc.episode_id"
)


async def _fetch_shots(
    conn: aiosqlite.Connection,
    project_id: str,
    *,
    episode_id: str | None = None,
) -> list[StudioShot]:
    """作品の Shot を話 -> 場 -> カットの順で返す。

    ``episode_id`` を渡すと**その話に属する場の** Shot だけを返す（未分類は
    どの話のものでもないので入らない）。
    """
    where = "WHERE s.project_id = ?"
    params: tuple[Any, ...] = (project_id,)
    if episode_id is not None:
        where += " AND sc.episode_id = ?"
        params = (project_id, episode_id)
    async with conn.execute(
        f"{_SHOT_JOIN} {where}{_SHOT_ORDER}", params
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_shot(row) for row in rows]


async def _scope_shot_ids(
    conn: aiosqlite.Connection, project_id: str, scene_id: str | None
) -> list[str]:
    """1 つの場（``scene_id=None`` なら未分類グループ）の Shot を並び順で。"""
    if scene_id is None:
        where, params = "scene_id IS NULL", (project_id,)
    else:
        where, params = "scene_id = ?", (project_id, scene_id)
    async with conn.execute(
        f"SELECT id FROM studio_shots WHERE project_id = ? AND {where}"
        " ORDER BY sort_order, created_at, id",
        params,
    ) as cur:
        return [str(row["id"]) for row in await cur.fetchall()]


async def _next_shot_sort_order(
    conn: aiosqlite.Connection, project_id: str, scene_id: str | None
) -> int:
    """その場（未分類なら未分類グループ）の末尾の並び順。"""
    if scene_id is None:
        where, params = "scene_id IS NULL", (project_id,)
    else:
        where, params = "scene_id = ?", (project_id, scene_id)
    async with conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next FROM studio_shots"
        f" WHERE project_id = ? AND {where}",
        params,
    ) as cur:
        return int((await cur.fetchone())["next"])


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


async def _insert_shot(
    conn: aiosqlite.Connection,
    project_id: str,
    payload: Any,
    *,
    scene_id: str | None = None,
) -> StudioShot:
    """Shot を 1 件書く（``commit`` とリビジョンは呼び出し側）。

    ``scene_id`` を渡すと本文の値ではなくそちらの場に入れる（一括投入は
    入れ子の位置で所属が決まるため）。
    """
    if await _fetch_project(conn, project_id) is None:
        raise StudioError("project not found")
    if scene_id is None:
        scene_id = getattr(payload, "scene_id", None)
    if scene_id is not None:
        scene = await _fetch_scene(conn, scene_id)
        if scene is None or scene.project_id != project_id:
            raise StudioError("scene not found")
    sort_order = payload.sort_order
    if sort_order is None:
        # 並び順は場の中のものなので、入る場（未分類なら未分類グループ）の末尾。
        sort_order = await _next_shot_sort_order(conn, project_id, scene_id)
    shot_id = new_id()
    now = _now()
    columns = ("id", "project_id", "scene_id", "sort_order", *_SHOT_TEXT_FIELDS,
               "duration_seconds", "planned_start_seconds", "timeline_role",
               "carry_over_end_frame",
               *_SHOT_SETTING_FIELDS,
               "created_at", "updated_at", "prompt_updated_at")
    planned = getattr(payload, "planned_start_seconds", None)
    if planned is not None and float(planned) < 0:
        raise StudioError("計画開始秒は 0 以上で指定してください")
    values = (
        shot_id,
        project_id,
        scene_id,
        sort_order,
        *(getattr(payload, name) for name in _SHOT_TEXT_FIELDS),
        float(payload.duration_seconds),
        None if planned is None else float(planned),
        getattr(payload, "timeline_role", None) or "auto",
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
    return await _fetch_shot(conn, shot_id)  # type: ignore[return-value]


async def create_shot(
    project_id: str, payload: Any, *, actor: str = "user"
) -> StudioShot:
    async with get_db() as conn:
        shot = await _insert_shot(conn, project_id, payload)
        await _record_revision(
            conn,
            project_id,
            actor,
            f"{_titled('カット', shot.title)}を追加",
            entity_kind="shot",
            entity_id=shot.id,
            op="create",
        )
        await _commit(conn)
        return shot


async def update_shot(
    shot_id: str,
    changes: dict[str, Any],
    *,
    actor: str = "user",
    base_revision: int | None = None,
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
    if changes.get("planned_start_seconds") is not None:
        # 音源上の秒なので負の位置は無い（0 秒 = 曲の頭）。
        planned = float(changes["planned_start_seconds"])
        if planned < 0:
            raise StudioError("計画開始秒は 0 以上で指定してください")
        changes["planned_start_seconds"] = planned
    if "english_prompt" in changes and not changes.get("english_prompt"):
        changes["english_prompt"] = ""
        changes["english_source"] = ""
        changes["english_status"] = ""
        changes["english_error"] = ""
    async with get_db() as conn:
        shot = await _fetch_shot(conn, shot_id)
        if shot is None:
            return None
        if "scene_id" in changes:
            scene_id = changes["scene_id"]
            if scene_id is not None:
                scene = await _fetch_scene(conn, str(scene_id))
                if scene is None or scene.project_id != shot.project_id:
                    raise StudioError("scene not found")
            if scene_id != shot.scene_id:
                # 引っ越し先（未分類なら未分類グループ）の末尾に付け直す。
                # 元いた場の並びに空きが出るが、順序は変わらないので詰めない。
                changes.setdefault(
                    "sort_order",
                    await _next_shot_sort_order(
                        conn,
                        shot.project_id,
                        str(scene_id) if scene_id is not None else None,
                    ),
                )
        if changes:
            await _check_base_revision(
                conn, shot.project_id, base_revision, "shot", shot_id, shot.title,
            )
            written = list(changes)
            now = _now()
            changes["updated_at"] = now
            if PROMPT_FIELDS & set(changes):
                changes["prompt_updated_at"] = now
            assignments = ", ".join(f"{key} = ?" for key in changes)
            await conn.execute(
                f"UPDATE studio_shots SET {assignments} WHERE id = ?",
                (*changes.values(), shot_id),
            )
            await _record_revision(
                conn,
                shot.project_id,
                actor,
                f"{_titled('カット', shot.title, shot_id)}を更新"
                f"{_changed_note(written)}",
                entity_kind="shot",
                entity_id=shot_id,
            )
            await _commit(conn)
        return await _fetch_shot(conn, shot_id)


async def delete_shot(shot_id: str, *, actor: str = "user") -> bool:
    """Take ごと消す（ON DELETE CASCADE）。ジョブと成果物は履歴に残る。"""
    async with get_db() as conn:
        shot = await _fetch_shot(conn, shot_id)
        if shot is None:
            return False
        await conn.execute("DELETE FROM studio_shots WHERE id = ?", (shot_id,))
        await _record_revision(
            conn,
            shot.project_id,
            actor,
            f"{_titled('カット', shot.title, shot_id)}を削除",
            entity_kind="shot",
            entity_id=shot_id,
            op="delete",
        )
        await _commit(conn)
        return True


async def reorder_shots(
    project_id: str, shot_ids: list[str], *, actor: str = "user"
) -> list[StudioShot]:
    """``shot_ids`` の並び順をそのまま ``sort_order`` にする。

    並び順は**場の中**のものなので、受け取るのは「1 つの場（または未分類
    グループ）の Shot を全件、過不足なく並べたもの」。一部だけ送られると
    残りの順序が決まらないので、その場で断る。

    作品の Shot を全件送るのも受ける（場をまたいだ並びを 1 本で送っていた
    頃の呼び出し）。その場合は場ごとに切り分けて、それぞれの中の相対順を
    書き戻す（場をまたぐ移動は起きない）。中途半端に複数の場が混ざったものは
    「どの場を並べたいのか」が決まらないので断る。
    """
    async with get_db() as conn:
        if await _fetch_project(conn, project_id) is None:
            raise StudioError("project not found")
        shots = await _fetch_shots(conn, project_id)
        scene_of = {shot.id: shot.scene_id for shot in shots}
        wanted = list(shot_ids)
        if len(set(wanted)) != len(wanted):
            raise StudioError("shot_ids に同じ id が複数あります")
        unknown = [shot_id for shot_id in wanted if shot_id not in scene_of]
        if unknown:
            raise StudioError(
                "shot_ids にこのプロジェクトの Shot でないものが入っています"
            )
        grouped: dict[str | None, list[str]] = {}
        for shot_id in wanted:
            grouped.setdefault(scene_of[shot_id], []).append(shot_id)
        if len(grouped) > 1 and set(wanted) != set(scene_of):
            raise StudioError(
                "shot_ids は 1 つの場の Shot だけ（または作品の Shot 全件）を"
                "並べたものにしてください"
            )
        for scene_id, ids in grouped.items():
            scope = await _scope_shot_ids(conn, project_id, scene_id)
            if set(ids) != set(scope):
                raise StudioError(
                    "shot_ids はその場の Shot を全件並べたものにしてください"
                )
        now = _now()
        for ids in grouped.values():
            for order, shot_id in enumerate(ids):
                await conn.execute(
                    "UPDATE studio_shots SET sort_order = ?, updated_at = ?"
                    " WHERE id = ?",
                    (order, now, shot_id),
                )
        await _record_revision(
            conn, project_id, actor, f"カットを並べ替え（{len(wanted)} 件）"
        )
        await _commit(conn)
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

#: 公式 H3 フィールド（行頭）。本文が既に持っていれば二重に包まない。
_H3_FIELD = re.compile(
    r"(?im)^(integrated_multimodal_description|detailed_description|"
    r"overall_soundscape|non_diegetic_music|subject_definitions|summary|"
    r"retention_analysis)\s*:"
)

_I2VA_ALIGNMENT = (
    "For the target video, at 0.00 seconds into the target video, "
    "<Picture 1> (from [Shot 1]) is fully referenced."
)

_WRAPPED_QUOTES = (
    ('"', '"'),
    ("'", "'"),
    ("「", "」"),
    ("『", "』"),
    ("“", "”"),
)

#: 公式の切替句。これがあるだけではカメラ欄を捨てない（運鏡の指定ではない）。
_CAMERA_CUT = re.compile(
    r"the camera (?:cuts|cut|switches|transitions|changes) to",
    re.I,
)


def _strip_wrapping_quotes(text: str) -> str:
    text = text.strip()
    for left, right in _WRAPPED_QUOTES:
        if len(text) >= len(left) + len(right) and text.startswith(left) and text.endswith(right):
            return text[len(left) : -len(right)].strip()
    return text


def _has_h3_description(text: str) -> bool:
    lowered = text.lower()
    return (
        "integrated_multimodal_description:" in lowered
        or "detailed_description:" in lowered
    )


def _has_alignment(text: str) -> bool:
    lowered = text.lower()
    return (
        "at 0.00 seconds into the target video" in lowered
        or "how the reference pictures align with the target video" in lowered
    )


def _append_to_description(text: str, extra: str) -> str:
    """カメラ・台詞を description フィールドの末尾（次フィールドの直前）へ足す。"""
    matches = list(_H3_FIELD.finditer(text))
    desc_i = next(
        (
            i
            for i, match in reversed(list(enumerate(matches)))
            if match.group(1).lower()
            in {"integrated_multimodal_description", "detailed_description"}
        ),
        None,
    )
    if desc_i is None:
        return f"{text.rstrip()} {extra}"
    end = matches[desc_i + 1].start() if desc_i + 1 < len(matches) else len(text)
    return f"{text[:end].rstrip()} {extra}{text[end:]}"


def compose_prompt(shot: StudioShot, body: str, *, workflow: str = "") -> str:
    """メンション解決済みの本文を公式 MiniMax H3 契約へ組み立てる。

    本文が既に ``integrated_multimodal_description:`` /
    ``detailed_description:`` を持っていれば包み直さない。無いときだけ
    r2v* は ``detailed_description:``、それ以外は
    ``integrated_multimodal_description:`` で包む。
    ``subject_definitions`` / ``summary`` / ``retention_analysis`` は
    一行の本文から作らない。カメラ・台詞・音は**本文が既に書いていれば足さない**。

    ここが「どのフィールドがモデルに届くか」の正本。挙動を変えたら、外部 API が
    配る脚本ガイド（:mod:`app.drafting_guide` の「1. フィールド契約」）も直すこと。
    """
    text = body.strip()
    if not text:
        raise StudioError("Shot のプロンプトが空です")
    if not _has_h3_description(text):
        field = (
            "detailed_description"
            if workflow.startswith("minimax_h3_r2v")
            else "integrated_multimodal_description"
        )
        text = f"{field}: {text}"

    extras: list[str] = []
    lowered = text.lower()
    camera_prose = _CAMERA_CUT.sub("", text)
    if (
        shot.camera.strip()
        and "camera:" not in lowered
        and "the camera " not in camera_prose.lower()
    ):
        extras.append(f"The camera {shot.camera.strip()}.")
    dialogue = shot.dialogue.strip()
    if dialogue and "<d>" not in lowered:
        spoken = _strip_wrapping_quotes(dialogue)
        lang = "Japanese" if has_japanese(spoken) else "English"
        extras.append(f"(S1) says: <d>[{lang}] {spoken}</d>")
    if extras:
        text = _append_to_description(text, " ".join(extras))
        lowered = text.lower()

    parts = [text]
    soundscape_added = False
    if "overall_soundscape:" not in lowered and shot.soundscape.strip():
        parts.append(f"overall_soundscape: {shot.soundscape.strip()}")
        soundscape_added = True
    if "non_diegetic_music:" not in lowered:
        if shot.bgm.strip():
            parts.append(f"non_diegetic_music: {shot.bgm.strip()}")
        elif soundscape_added:
            parts.append("non_diegetic_music: N/A")
    assembled = "\n\n".join(parts)

    if workflow.startswith("minimax_h3_i2v") and not _has_alignment(assembled):
        assembled = f"{_I2VA_ALIGNMENT}\n\n{assembled}"

    assembled_lowered = assembled.lower()
    if "subtitle" not in assembled_lowered and "watermark" not in assembled_lowered:
        assembled = f"{assembled}\n{EXCLUSION_SENTENCE}"
    return assembled


# --------------------------------------------------------------------------
# 日本語 -> 英語の変換（Grok）
# --------------------------------------------------------------------------
#
# MiniMax H3 は英語プロンプト前提のモデルなので、日本語で書いた脚本はそのまま
# 投げると精度が落ちる。Shot に使える英語キャッシュ（``english_prompt`` があり
# ``english_source`` が今の組み立て文と一致）があればそれを投入し、Grok は走らない。
# 無ければプロジェクトの `auto_translate` が有効なら、ジョブランナーが組み立て済み
# 本文を Grok に「公式 H3 文書へ書き直す」仕事をさせてから投入する。
# 直訳ではなく、事実はそのまま、欠ける公式フィールドと観測できる演出を足す。
# 壊してはいけないもの:
#   - `<Picture N>` / `<Video N>` / `<Audio N>` / `<Subject N>`（参照タグ）
#   - 引用符の中の台詞と `<d>…</d>` の中身（H3 はこれをそのまま喋るので、原語）
#   - ユーザーが書いていない人物・場所・衣装・台詞・筋（発明しない）
#   - 除外文があればそのまま

#: 変換指示のひな形。``{hint}`` に投入先ワークフローの `prompt_hint` が入る。
TRANSLATION_INSTRUCTION = (
    "You rewrite a Japanese video prompt into a complete official English"
    " MiniMax H3 document. Output **only** the rewritten prompt — no"
    " preamble, no explanation, no markdown fences. You are an official"
    " rewriter, not a literal translator: keep every stated fact, and fill"
    " the official fields and observable staging the source omitted.\n\n"
    "Hard rules:\n"
    "- Keep every reference tag (`<Picture 1>`, `<Video 2>`, `<Audio 1>`,"
    " `<Subject 1>`, …) exactly as written. Never renumber, translate or"
    " drop one, and never invent a new one.\n"
    "- Keep spoken lines inside double quotes **and** the contents of every"
    " `<d>…</d>` block **in their original language** (Japanese stays"
    " Japanese); translate only the words around them. Do not invent"
    " dialogue.\n"
    "- Do not invent characters, locations, wardrobe, spoken lines, or plot"
    " events the source did not state.\n"
    "- Keep the closing exclusion sentence if present.\n\n"
    "You must now do:\n"
    "- Emit a complete official document (base 3 fields or Ref2VA 6 fields,"
    " from the workflow hint below).\n"
    "- Add missing field headers, `[Shot 1]`, official camera clauses, and"
    " `overall_soundscape` / `non_diegetic_music` derived from the stated"
    " action and any `soundscape` / `bgm` already in the source.\n"
    "- Develop observable staging of **stated** actions (body, contact,"
    " eyeline, resulting state, lighting already implied).\n"
    "- I2VA: keep or add the official alignment first line when the source"
    " is a first-frame job.\n"
    "- Ref2VA: if tags are present but analysis sections are missing, write"
    " **minimal** `subject_definitions` / `summary` /"
    " `retention_analysis` / `detailed_description` **only from those tags"
    " and stated facts** (do not invent extra subjects).\n"
    "- Keep `[Shot N]` and `At MM:SS.mmm`. Do **not** convert into"
    " `Camera:` / `Audio:` lines.\n\n"
    "How an H3 prompt should read:\n{hint}\n\n"
    "Source prompt:\n{prompt}"
)

#: 応答が ```…``` で包まれていたときに中身だけ取り出す
_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*(.*?)\s*```$", re.DOTALL)

#: Grok CLI が公式文書の前に独り言を付けることがある。最初の公式フィールド
#: （または i2v の alignment 行）からを本文とする。
_H3_DOCUMENT_START = re.compile(
    r"(?is)("
    r"For the target video, at 0\.00 seconds into the target video,"
    r"|How the reference pictures align with the target video"
    r"|integrated_multimodal_description\s*:"
    r"|detailed_description\s*:"
    r"|subject_definitions\s*:"
    r"|summary\s*:"
    r"|retention_analysis\s*:"
    r"|overall_soundscape\s*:"
    r"|non_diegetic_music\s*:"
    r")"
)

_DIALOGUE_LANG = (
    (re.compile(r"(<d>\[)日本語(\])"), r"\1Japanese\2"),
    (re.compile(r"(<d>\[)英語(\])"), r"\1English\2"),
)


def has_japanese(text: str) -> bool:
    return bool(_JAPANESE.search(text or ""))


def _unfence(text: str) -> str:
    match = _FENCE.match((text or "").strip())
    return (match.group(1) if match else text).strip()


def extract_h3_document(text: str) -> str:
    """フェンスと先頭の独り言を落とし、公式 H3 文書だけ残す。

    公式フィールドが一つも無い応答は空文字（呼び出し側が失敗にする）。
    """
    body = _unfence(text)
    match = _H3_DOCUMENT_START.search(body)
    if match is None:
        return ""
    body = body[match.start() :].strip()
    for pattern, repl in _DIALOGUE_LANG:
        body = pattern.sub(repl, body)
    return body


async def translate_prompt(prompt: str, workflow_id: str) -> str:
    """日本語まじりの本文を H3 用の英語プロンプトへ直す。

    :class:`app.grok.LLMError` はそのまま投げる（呼び出し側がジョブを失敗させる）。
    空の応答も同じ扱いにする。待ち時間は相談と同じ ``agent_grok_timeout``。
    """
    hint = get_video_spec(workflow_id).prompt_hint
    started = time.monotonic()
    answer = await grok.get_client(timeout=grok.configured_timeout()).complete(
        TRANSLATION_INSTRUCTION.format(hint=hint, prompt=prompt)
    )
    translated = extract_h3_document(answer)
    if not translated:
        raise grok.LLMError("grok が空のプロンプトを返しました")
    log.info("translate_prompt took %.1fs", time.monotonic() - started)
    return translated


# --------------------------------------------------------------------------
# 投入の組み立て（生成とプレビューで唯一の経路）
# --------------------------------------------------------------------------

class _Plan(NamedTuple):
    """1 回の投入で決まるもの一式。"""

    workflow: str
    reason: str
    #: 実際に投入する本文（公式フィールドと除外文まで込み。英訳の前）
    prompt: str
    start_image: str | None
    references: list[StudioAsset]
    #: ``references`` と同じ並びの ``<Picture 1>`` などのタグ
    tags: list[str]
    #: ラテント連続性で引き継ぐ直前カットの動画（``assets/video/`` に複製済み）
    context_video: str | None = None
    #: 同じく、直前カットの AV ラテント（ComfyUI 側のパス）
    context_latent: str | None = None
    #: 同じく、直前カットの 2 パス目（最終解像度）の AV ラテント。
    #: ``latent_upscale`` = on の 2 段引き継ぎでだけ付く（無ければ 1 段）。
    context_latent_hires: str | None = None
    #: プロジェクトの ``quality`` が実際にバリアントとして効いたか
    #: （False = ``normal`` だった、または素へフォールバックした）
    quality_applied: bool = False
    #: ジョブに載せる選択式（いまは ``latent_upscale`` だけ。宣言を持たない
    #: ワークフローや読めない環境では空 = 何も載せない）
    selects: dict[str, str] = {}
    #: 組み立てはできたが投入はできない理由（日本語。``require_context`` を
    #: 落とした英訳・プレビューでだけ入りうる。空なら投入できる）
    render_blocker: str = ""


async def _plan_render(
    conn: aiosqlite.Connection,
    shot: StudioShot,
    assets: list[StudioAsset],
    project: StudioProject | None = None,
    latent_upscale: bool | None = None,
    *,
    require_context: bool = True,
) -> _Plan:
    """モードを決めて ``@素材名`` を解決し、最終プロンプトまで組み立てる。

    生成（:func:`render_shot`）と投入プレビュー（:func:`preview_shot`）が
    **同じものを見る**ための唯一の経路。組み立てられなければ
    :class:`StudioError`（生成は 400、プレビューは理由として見せる）。

    ``project`` は引き継ぎのやり方（``latent_continuity``）と品質（``quality``）を
    見るために渡す。ワークフロー id は 3 段で決まる:

    1. 論理モード（:func:`_pick_workflow`。t2v / i2v / r2v / r2v_context）
    2. ラテント連続性が ON なら保存付きバリアントへの読み替え
       （:func:`_latent_save_workflow`。連鎖の起点になる通常カットも AV ラテントを
       残せるようにするため）
    3. そこまでで決まった論理ワークフロー × 品質 -> バリアント
       （:func:`_quality_workflow`。接続先が対応しなければ 2 の結果のまま投げ、
       その理由を ``reason`` に足す）

    ワークフローが決まったら、その宣言と接続先に合わせて選択式
    （``latent_upscale``）を解決する（:func:`_resolve_selects`）。
    ``latent_upscale`` はその 1 回ぶんの上書きで、``None`` ならプロジェクトの
    設定（それも無ければ :data:`DEFAULT_LATENT_UPSCALE`）に従う。

    ``require_context`` を False にすると、連続カット（ラテント連続性）の
    引き継ぎ元が無くても断らず、本文だけ同じ形で組み立てて、投入できない理由を
    ``render_blocker`` に入れて返す（英訳とプレビューだけが使う。生成は
    今までどおり断る）。
    """
    # 添付できる（＝ファイル実体を持つ）素材を呼んでいるか。メタデータだけの
    # 素材はここに出てこないので、r2v の理由にはならない。
    mode = await _pick_workflow(
        conn,
        shot,
        bool(resolve_mentions(shot.prompt, assets, attach=True).references),
        project,
        require_context=require_context,
    )
    latent_continuity = project is not None and project.latent_continuity
    quality = normalize_quality(project.quality if project is not None else None)
    # 品質のバリアントは同じ論理ワークフローの別テンプレートなので、先に
    # ラテント連続性の読み替えを済ませてから、その id で品質を引く。
    logical = _latent_save_workflow(mode.workflow, latent_continuity)
    workflow, quality_reason = _quality_workflow(logical, quality)
    quality_applied = workflow != logical
    reason = f"{mode.reason} / {quality_reason}" if quality_reason else mode.reason
    if latent_upscale is None:
        latent_upscale = (
            project.latent_upscale if project is not None else DEFAULT_LATENT_UPSCALE
        )
    selects, selects_reason = _resolve_selects(workflow, latent_upscale)
    if selects_reason:
        reason = f"{reason} / {selects_reason}"
    resolved = resolve_mentions(shot.prompt, assets, attach=mode.attach)
    return _Plan(
        workflow,
        reason,
        compose_prompt(shot, resolved.text, workflow=workflow),
        mode.start_image,
        resolved.references,
        resolved.tags,
        mode.context_video,
        mode.context_latent,
        mode.context_latent_hires,
        quality_applied,
        selects,
        mode.blocker,
    )


def _english_cache_usable(shot: StudioShot, assembled: str) -> bool:
    """組み立て済み本文に対して、保存してある英語がそのまま使えるか。"""
    return bool(shot.english_prompt) and shot.english_source == assembled


async def preview_shot(shot_id: str) -> StudioShotPreview | None:
    """この Shot を今生成したら何が投入されるか（Shot が無ければ None）。

    生成と同じ :func:`_plan_render` を通すが、**Grok の英訳は走らせない**
    （遅く、課金枠を食う）。英訳が入るかどうかは ``will_translate`` で返す
    （使える ``english_prompt`` があれば False）。
    組み立てられないときも 200 で、理由を ``error`` に入れて返す。

    連続カット（ラテント連続性）の引き継ぎ元がまだ無いときは、本文までは
    生成と同じ形で組み立てて見せ、投入できない理由を ``render_blocker`` に
    入れる（``error`` は「組み立てそのものができない」ときだけ）。
    """
    async with get_db() as conn:
        shot = await _fetch_shot(conn, shot_id)
        if shot is None:
            return None
        project = await _fetch_project(conn, shot.project_id)
        assets = await _fetch_assets(conn, shot.project_id)
        auto_translate = project is not None and project.auto_translate
        latent_continuity = project is not None and project.latent_continuity
        quality = normalize_quality(project.quality if project is not None else None)
        try:
            plan = await _plan_render(
                conn, shot, assets, project, require_context=False
            )
        except StudioError as exc:
            return StudioShotPreview(
                shot_id=shot.id,
                workflow=shot.workflow_override,
                auto_translate=auto_translate,
                latent_continuity=latent_continuity,
                quality=quality,
                latent_upscale=(
                    project.latent_upscale
                    if project is not None
                    else DEFAULT_LATENT_UPSCALE
                ),
                english_prompt=shot.english_prompt,
                english_stale=bool(shot.english_prompt),
                english_status=shot.english_status,
                english_error=shot.english_error,
                error=str(exc),
            )
    usable = _english_cache_usable(shot, plan.prompt)
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
        will_translate=(
            not usable and auto_translate and has_japanese(plan.prompt)
        ),
        english_prompt=shot.english_prompt,
        english_stale=bool(shot.english_prompt) and not usable,
        english_status=shot.english_status,
        english_error=shot.english_error,
        latent_continuity=latent_continuity,
        quality=quality,
        quality_applied=plan.quality_applied,
        latent_upscale=plan.selects.get(LATENT_UPSCALE_NAME) == "on",
        context_video=plan.context_video,
        context_latent=plan.context_latent,
        context_latent_hires=plan.context_latent_hires,
        render_blocker=plan.render_blocker,
    )


async def recover_interrupted_translates() -> None:
    """再起動で残った ``translating`` を失敗にする（起動時に 1 回だけ呼ぶ）。"""
    async with get_db() as conn:
        await conn.execute(
            "UPDATE studio_shots SET english_status='failed',"
            " english_error='英訳が中断されました。もう一度英訳してください'"
            " WHERE english_status='translating'"
        )
        await _commit(conn)


async def _run_translate(
    shot_id: str, assembled: str, workflow_id: str, actor: str
) -> None:
    """Grok 英訳の本体。例外は投げない（autotag と同じ）。"""
    try:
        error = ""
        english = ""
        try:
            english = await translate_prompt(assembled, workflow_id)
        except asyncio.CancelledError:
            raise
        except grok.LLMError as exc:
            error = (
                "英語プロンプトへの変換ができないので保存しませんでした"
                f"（{exc}）"
            )
        async with get_db() as conn:
            shot = await _fetch_shot(conn, shot_id)
            if shot is None or shot.english_status != "translating":
                return
            now = _now()
            if error:
                await conn.execute(
                    "UPDATE studio_shots SET english_status = 'failed',"
                    " english_error = ?, updated_at = ? WHERE id = ?",
                    (error, now, shot_id),
                )
            else:
                await conn.execute(
                    "UPDATE studio_shots SET english_prompt = ?, english_source = ?,"
                    " english_status = '', english_error = '',"
                    " updated_at = ?, prompt_updated_at = ? WHERE id = ?",
                    (english, assembled, now, now, shot_id),
                )
                await _record_revision(
                    conn,
                    shot.project_id,
                    actor,
                    f"{_titled('カット', shot.title, shot_id)}を更新"
                    "(english_prompt)",
                    entity_kind="shot",
                    entity_id=shot_id,
                )
            await _commit(conn)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 英訳の失敗で HTTP を壊さない
        log.exception("shot %s の英訳に失敗しました", shot_id)
        try:
            async with get_db() as conn:
                await conn.execute(
                    "UPDATE studio_shots SET english_status = 'failed',"
                    " english_error = ?, updated_at = ?"
                    " WHERE id = ? AND english_status = 'translating'",
                    ("英訳に失敗しました", _now(), shot_id),
                )
                await _commit(conn)
        except Exception:  # noqa: BLE001
            log.exception("shot %s の英訳失敗を記録できませんでした", shot_id)


async def translate_shot(shot_id: str, *, actor: str = "user") -> StudioShot | None:
    """組み立て済み本文の英訳を開始して Shot を返す。

    組み立て不能なら :class:`StudioError`。英語だけなら Grok は呼ばず、その
    組み立て文をキャッシュとして残して返す。日本語があるときは
    ``english_status='translating'`` にして Grok を裏で走らせ、すぐ返す。
    """
    async with get_db() as conn:
        shot = await _fetch_shot(conn, shot_id)
        if shot is None:
            return None
        project = await _fetch_project(conn, shot.project_id)
        assets = await _fetch_assets(conn, shot.project_id)
        # 英訳に要るのは本文だけなので、連続カットの引き継ぎ元が無くても
        # 組み立てて訳す（投入は :func:`render_shot` が今までどおり断る）。
        plan = await _plan_render(conn, shot, assets, project, require_context=False)

    if not has_japanese(plan.prompt):
        now = _now()
        async with get_db() as conn:
            await conn.execute(
                "UPDATE studio_shots SET english_prompt = ?, english_source = ?,"
                " english_status = '', english_error = '',"
                " updated_at = ?, prompt_updated_at = ? WHERE id = ?",
                (plan.prompt, plan.prompt, now, now, shot_id),
            )
            await _record_revision(
                conn,
                shot.project_id,
                actor,
                f"{_titled('カット', shot.title, shot_id)}を更新(english_prompt)",
                entity_kind="shot",
                entity_id=shot_id,
            )
            await _commit(conn)
            return await _fetch_shot(conn, shot_id)

    async with get_db() as conn:
        shot = await _fetch_shot(conn, shot_id)
        if shot is None:
            return None
        if shot.english_status == "translating":
            return shot
        await conn.execute(
            "UPDATE studio_shots SET english_status = 'translating',"
            " english_error = '' WHERE id = ?",
            (shot_id,),
        )
        await _commit(conn)
        shot = await _fetch_shot(conn, shot_id)
    if shot is None:
        return None

    nsfw.spawn(
        _run_translate(shot_id, plan.prompt, plan.workflow, actor),
        key=f"translate:{shot_id}",
    )
    return shot


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
            # NSFW はジョブ側が正（手動トグルは POST /api/jobs/{id}/nsfw）。
            data["nsfw"] = job.nsfw
            data["nsfw_source"] = job.nsfw_source
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


async def _previous_selected_take(
    conn: aiosqlite.Connection, shot: StudioShot
) -> StudioTake | None:
    """直前の Shot の採用 Take（無ければ None）。

    「直前」は並び順（``sort_order`` -> ``created_at`` -> ``id``）で 1 つ手前の
    Shot。先頭 Shot・まだ採用していない Shot では None。
    """
    shots = await _fetch_shots(conn, shot.project_id)
    index = next((i for i, item in enumerate(shots) if item.id == shot.id), 0)
    if index == 0:
        return None
    previous = shots[index - 1]
    if not previous.selected_take_id:
        return None
    return await _fetch_take(conn, previous.selected_take_id)


async def _carry_over_context(
    conn: aiosqlite.Connection, shot: StudioShot
) -> tuple[str, str, str] | None:
    """ラテント連続性で引き継ぐ ``(直前カットの動画, AV ラテント, 高解像度)``。

    動画と AV ラテントのどちらか片方でも欠けていたら None（続きとして生成できない
    ため）。AV ラテントは ComfyUI 側に置きっぱなしのファイルなので複製しないが、
    動画のほうはジョブの入力になるので ``assets/video/`` に移してから渡す
    （:func:`app.jobs.resolve_asset_path` が読めるのは ``assets/`` と
    ``library/`` の中だけ）。

    3 本目は 2 段引き継ぎ（``latent_upscale`` = on）で保存した 2 パス目
    （最終解像度）のラテント。``off`` で作った過去テイクには無いので、その
    ときは空文字 = 1 段引き継ぎにフォールバックする。
    """
    take = await _previous_selected_take(conn, shot)
    if take is None or not take.video_path or not take.latent_path:
        return None
    return (
        str(job_service.copy_into_assets(take.video_path, "video")),
        take.latent_path,
        take.latent_hires_path or "",
    )


async def record_take_latent(
    job_id: str, latent_path: str, hires_path: str = ""
) -> None:
    """ジョブが保存した AV ラテントのパスを、その Take に控える。

    呼ぶのはジョブランナー（:func:`app.jobs._record_take_latent`）で、スタジオ
    由来でないジョブでは対象の行が無いだけ（何も起きない）。``hires_path`` は
    2 段引き継ぎ（``latent_upscale`` = on）で保存した 2 パス目のラテントで、
    ``off`` のジョブでは空 = 列は NULL のままにする。
    """
    async with get_db() as conn:
        if hires_path:
            await conn.execute(
                "UPDATE studio_takes SET latent_path = ?, latent_hires_path = ?"
                " WHERE job_id = ?",
                (latent_path, hires_path, job_id),
            )
        else:
            await conn.execute(
                "UPDATE studio_takes SET latent_path = ? WHERE job_id = ?",
                (latent_path, job_id),
            )
        await _commit(conn)


async def record_translated_prompt(job_id: str, translated: str) -> None:
    """英訳が終わった本文を、そのジョブの Take に書き戻す。

    呼ぶのはジョブランナー。スタジオ由来でないジョブでは対象の行が無いだけ。
    """
    async with get_db() as conn:
        async with conn.execute(
            "SELECT id, project_id FROM studio_takes WHERE job_id = ?", (job_id,)
        ) as cur:
            rows = await cur.fetchall()
        await conn.execute(
            "UPDATE studio_takes SET prompt = ? WHERE job_id = ?",
            (translated, job_id),
        )
        for row in rows:
            _queue_event(conn, row["project_id"], "take", row["id"], "update")
        await _commit(conn)


async def notify_job_settled(job_id: str) -> None:
    """終わったジョブがスタジオの Take なら、その作品へ更新を流す。

    Take の状態（``candidate`` / ``failed``）はジョブの状態から導出するので、
    完了しても ``studio_takes`` には 1 行も書かれない = 上の :func:`_commit`
    経由の通知が出ない。ジョブランナー（:func:`app.jobs._set_status`）が終端に
    入ったときだけここを呼んで、開いているブラウザに読み直させる。
    """
    async with get_db() as conn:
        async with conn.execute(
            "SELECT id, project_id FROM studio_takes WHERE job_id = ?", (job_id,)
        ) as cur:
            rows = await cur.fetchall()
    for row in rows:
        await ws.publish_studio(row["project_id"], "take", row["id"], "update")


async def _carry_over_start_frame(
    conn: aiosqlite.Connection, shot: StudioShot
) -> str | None:
    """直前の Shot の採用 Take のラストフレーム（無ければ None）。

    まだ採用していない・生成が終わっていないときは None を返すだけでエラーには
    しない（先頭 Shot と同じく t2v / r2v に落ちる）。

    返すのは ``assets/image/`` に複製したほうのパス: ジョブの入力に取れるのは
    ``assets/`` と ``library/`` の中だけなので（:func:`app.jobs.resolve_asset_path`）、
    続き生成（``/api/jobs/{id}/continue``）と同じように移してから渡す。
    """
    take = await _previous_selected_take(conn, shot)
    if take is None or not take.last_frame_path:
        return None
    return str(job_service.copy_into_assets(take.last_frame_path, "image"))


async def _require_latent_context() -> None:
    """今の接続先で Motion Context が使えなければ断る（黙って落とさない）。

    ラテント連続性はカスタムノード頼みなので、入っていない接続先（Comfy Cloud
    など）に投げると ComfyUI 側で落ちる。投入前に ``/object_info`` を見て、
    設定を直すか引き継ぎをオフにするよう伝える。
    """
    try:
        available = await comfy.latent_context_support()
    except comfy.ComfyError as exc:
        raise StudioError(
            "ComfyUI に接続できないので、ラテント連続性が使えるか確かめられません"
            f"（{comfy.display_error(exc)}）"
        ) from exc
    if not available:
        raise StudioError(
            "いまの接続先では「ラテント連続性」が使えません"
            "（MiniMaxH3MotionContext 系のカスタムノードが入っていません）。"
            "接続先を変えるか、プロジェクト設定のラテント連続性をオフにしてください"
        )


def _workflow_supports_steps(workflow: str) -> bool:
    """そのワークフローが ``steps`` を宣言しているか（知らない id なら False）。"""
    try:
        return get_video_spec(workflow).supports("steps")
    except WorkflowSpecError:  # pragma: no cover - 投入直前の id は必ず既知
        return False


def _checked_duration(value: Any) -> float:
    """テイク 1 回ぶんの尺の上書きを検査する（範囲外は :class:`StudioError`）。"""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise StudioError("尺は数値で入れてください") from None
    if not SHOT_DURATION_MIN <= seconds <= SHOT_DURATION_MAX:
        raise StudioError(
            f"尺は {SHOT_DURATION_MIN:g}〜{SHOT_DURATION_MAX:g} 秒です"
        )
    return seconds


async def render_shot(
    shot_id: str,
    overrides: StudioRenderRequest | None = None,
    *,
    chat_session_id: str | None = None,
) -> StudioTake:
    """Shot を 1 回生成してジョブに載せ、Take を返す。

    やることは 4 つ:

    1. モードを決めて ``@素材名`` を解決し、プロンプトを組み立てる
       （:func:`_plan_render`。投入プレビューと同じ経路。未登録の名前は 400）。
    2. 生成設定（画面比・解像度・尺・ステップ数・シード・ラテント
       アップスケール）を **この 1 回ぶんの上書き > Shot > プロジェクト >
       既定** の順に解決する。
    3. 使える英語キャッシュがあればそれを ``video_prompt`` にする。無ければ
       プロジェクトの ``auto_translate`` が有効で本文に日本語が混ざっていれば、
       ジョブ側で英訳する（ここでは待たない。``pending_translate`` を載せる）。
    4. :func:`app.jobs.create_job` にそのまま渡す（HTTP は経由しない）。

    ``overrides``（:class:`app.models.StudioRenderRequest`）はそのテイクにだけ
    効き、Shot もプロジェクトも書き換えない。実際に使った値は Take の元ジョブの
    ``params`` に残る。
    """
    over = overrides or StudioRenderRequest()
    async with get_db() as conn:
        shot = await _fetch_shot(conn, shot_id)
        if shot is None:
            raise StudioError("shot not found")
        project = await _fetch_project(conn, shot.project_id)
        assets = await _fetch_assets(conn, shot.project_id)
        plan = await _plan_render(conn, shot, assets, project, over.latent_upscale)
        workflow = plan.workflow
        prompt = plan.prompt

        # 尺は **この 1 回ぶんの上書き > Shot** の順（プロジェクト既定は無い）。
        duration = (
            _checked_duration(over.duration)
            if over.duration is not None
            else float(shot.duration_seconds)
        )
        fields: dict[str, Any] = {
            "mode": "i2v",  # 動画ステージだけを走らせるモード（SPEC §2）
            "video_workflow": workflow,
            "video_prompt": prompt,
            "duration": duration,
            "user_input": shot.title or None,
        }
        # 選択式（いまは `latent_upscale` だけ）。**この 1 回ぶんの上書き >
        # プロジェクト > 既定 ON** で決まった値を :func:`_plan_render` が
        # 接続先に合わせて解決済み。宣言の無いワークフローには載らない。
        if plan.selects:
            fields["selects"] = dict(plan.selects)
        # Shot ごとの生成設定。**H3 が実際に受け取るものだけ**を渡す（否定
        # プロンプトは MiniMax H3 のグラフに注入先が無いので Shot には持たせない）。
        #
        # 画面比とメガピクセルは **この 1 回ぶんの上書き > Shot 個別 >
        # プロジェクト > グローバル既定** の順で、2 つはそれぞれ独立に解決する。
        # どこにも指定が無ければ何も載せず、今までどおり `JobCreate` /
        # ワークフロー宣言の既定に落とす。
        aspect_ratio = (
            normalize_aspect_ratio(over.aspect_ratio)
            or shot.aspect_ratio
            or (project.aspect_ratio if project is not None else None)
        )
        if aspect_ratio:
            fields["aspect_ratio"] = aspect_ratio
        megapixels = normalize_megapixels(over.megapixels)
        if megapixels is None:
            megapixels = shot.megapixels
        if megapixels is None and project is not None:
            megapixels = project.megapixels
        if megapixels is not None:
            fields["megapixels"] = float(megapixels)
        # シードは **上書き > Shot > 毎回ランダム**（載せなければランダム）。
        seed = over.seed if over.seed is not None else shot.seed
        if seed is not None:
            fields["seed"] = int(seed)
        # ステップ数は **上書き > プロジェクト > テンプレートの既定**。0 は
        # 「テンプレートの既定のまま」なので、そのときは何も載せない（上書きで
        # 0 を明示すればプロジェクトの設定も外れる）。宣言の無いワークフローに
        # 送っても効かないので、そもそも載せない。
        steps = (
            _checked_steps(over.steps)
            if over.steps is not None
            else (project.steps if project is not None else 0)
        )
        if steps > 0 and _workflow_supports_steps(workflow):
            fields["steps"] = steps
        # NSFW はプロジェクトの設定をそのまま**常に明示**する（True でも False
        # でも manual 扱い）。オフの作品は非 NSFW で固定され、投入後の Grok の
        # 自動判定は走らない（:func:`app.jobs._resolve_nsfw`）。
        fields["nsfw"] = bool(project is not None and project.nsfw)
        if plan.start_image is not None:
            fields["source_image"] = plan.start_image
        # ラテント連続性: 直前カットの動画は入力ファイル、AV ラテントは
        # ComfyUI 側のパス（上げ直さない）。
        if plan.context_video is not None:
            fields["reference_video"] = plan.context_video
        if plan.context_latent is not None:
            fields["context_latent_path"] = plan.context_latent
        # 2 段引き継ぎ（`latent_upscale` = on）で保存されていた 2 パス目の
        # ラテント。無ければ載せない = 1 段引き継ぎのまま。
        if plan.context_latent_hires:
            fields["context_latent_hires_path"] = plan.context_latent_hires
        for asset in plan.references:
            fields.setdefault(REFERENCE_FIELDS[asset.kind], []).append(asset.path)

    # 接続先にカスタムノードが入っているかは、投入の直前に 1 度だけ確かめる
    # （DB の接続を閉じてから。黙って別のモードに落とさず、その場で断る）。
    if workflow in LATENT_CONTEXT_WORKFLOWS:
        await _require_latent_context()

    # 使える英語キャッシュがあればそれを投入する（投入時の Grok は走らない）。
    # 無ければ今までどおり、auto_translate かつ日本語ならジョブランナー側で
    # 英訳する（HTTP を待たせない。``pending_translate`` だけ載せる）。
    source_prompt = ""
    warning = ""
    extra_params: dict[str, Any] | None = None
    submitted = prompt
    if _english_cache_usable(shot, prompt):
        submitted = shot.english_prompt
        fields["video_prompt"] = submitted
        source_prompt = prompt
    elif project is not None and project.auto_translate and has_japanese(prompt):
        source_prompt = prompt
        extra_params = {"pending_translate": True}

    if chat_session_id:
        fields["chat_session_id"] = chat_session_id

    # ジョブの投入も接続を閉じてから: 走り出したランナーが jobs 行を書きに来る
    # ので、読み取り用の接続を掴んだままにしない。
    try:
        payload = JobCreate(**fields)
    except ValidationError as exc:
        raise StudioError(_first_message(exc)) from exc
    try:
        job = await job_service.create_job(payload, extra_params=extra_params)
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
                submitted,
                source_prompt,
                warning,
            ),
        )
        # Take はリビジョンを残さない（生成物であって脚本ではない）ので、
        # 画面への通知だけここで積む。
        _queue_event(conn, shot.project_id, "take", take_id, "create")
        await _commit(conn)

    # ランナーが Take 作成より先に英訳を済ませていることがある。
    if extra_params and extra_params.get("pending_translate"):
        current = await job_service.get_job(job.id, include_workflow=False)
        translated = ""
        if current is not None:
            translated = str(
                current.params.get("video_prompt") or current.video_prompt or ""
            )
        if translated and translated != prompt:
            await record_translated_prompt(job.id, translated)

    async with get_db() as conn:
        return await _fetch_take(conn, take_id)  # type: ignore[return-value]


#: 連続カット（ラテント連続性）の材料が揃っていないときの断り文句。
#: 生成では :class:`StudioError` として投げ、英訳・プレビューでは組み立てを
#: 通したうえで「投入できない理由」として返す（:func:`_pick_workflow`）。
NO_CARRY_OVER_CONTEXT = (
    "前 Shot の採用 Take がありません。前 Shot の Take を採用するか、"
    "carry_over_end_frame をオフにしてください"
)


class _Mode(NamedTuple):
    """:func:`_pick_workflow` の答え（``reason`` は画面に出す日本語の理由）。"""

    workflow: str
    start_image: str | None
    attach: bool
    reason: str
    #: ラテント連続性で引き継ぐ直前カットの動画（``assets/video/`` に複製済み）
    context_video: str | None = None
    #: 同じく、直前カットの AV ラテント（ComfyUI 側のパス）
    context_latent: str | None = None
    #: 同じく、直前カットの 2 パス目（最終解像度）の AV ラテント（2 段引き継ぎ）
    context_latent_hires: str | None = None
    #: 組み立てはできるが投入はできない理由（日本語。``require_context`` を
    #: 落としたときだけ入りうる。空なら投入できる）
    blocker: str = ""


async def _pick_workflow(
    conn: aiosqlite.Connection,
    shot: StudioShot,
    has_material: bool,
    project: StudioProject | None = None,
    *,
    require_context: bool = True,
) -> _Mode:
    """``(ワークフロー, 開始フレーム, 参照として添付するか, 理由)``。

    ``workflow_override`` があればそれに従い、足りない入力はその場で断る
    （黙って別のモードに落ちると、指定した意味が無くなるため）。

    引き継ぎ（``carry_over_end_frame``）のやり方はプロジェクトの
    ``latent_continuity`` が決める。OFF なら今までどおりラストフレーム 1 枚を
    開始フレームにする i2v で、ON なら直前カットの動画と AV ラテントを渡す
    ``minimax_h3_r2v_context``。ON のときに材料が揃わなければ、こちらも
    黙って i2v / r2v に落とさずその場で断る（強制指定と同じ考え方）。

    ``require_context`` を False にすると、引き継ぎ元（前 Shot の採用 Take の
    動画と AV ラテント）が無くても断らず、``minimax_h3_r2v_context`` のまま
    引き継ぎ材料を ``None`` にして返し、断り文句を ``blocker`` に入れる。
    本文の組み立てだけが要る英訳・プレビュー用（生成は既定の True のまま）。

    返すのは**論理モードのまま**の id で、ラテント連続性が ON のときの保存付き
    バリアントへの読み替えは呼び出し側（:func:`_plan_render`）がやる。
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
    #
    # 引き継ぎのやり方が「ラテント連続性」のときは、ここだけが分かれる。
    # 直前カットの動画と AV ラテントを参照モードに足すので、参照素材（`@素材`）
    # が要る。材料が欠けていたら黙って落とさず、何をすればよいかを言って断る。
    if shot.carry_over_end_frame and project is not None and project.latent_continuity:
        if not has_material:
            raise StudioError(
                "連続カットには参照素材が必要です。プロンプトで @素材 を参照するか、"
                "carry_over_end_frame をオフにしてください"
            )
        context = await _carry_over_context(conn, shot)
        if context is None:
            if require_context:
                raise StudioError(NO_CARRY_OVER_CONTEXT)
            # 英訳・プレビュー: 本文の形は連続カットのままで、引き継ぎ材料が
            # 無いことは投入できない理由として持ち帰る。
            return _Mode(
                WORKFLOW_R2V_CONTEXT, None, True,
                "直前カットの動きと音をラテントごと引き継ぎます"
                "（ラテント連続性・素材は参照として添付）",
                blocker=NO_CARRY_OVER_CONTEXT,
            )
        context_video, context_latent, context_latent_hires = context
        return _Mode(
            WORKFLOW_R2V_CONTEXT, None, True,
            "直前カットの動きと音をラテントごと引き継ぎます"
            "（ラテント連続性・素材は参照として添付）",
            context_video,
            context_latent,
            context_latent_hires,
        )
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
        shot = await _fetch_shot(conn, take.shot_id)
        await _record_revision(
            conn,
            take.project_id,
            actor,
            f"{_titled('カット', shot.title if shot else '', take.shot_id)}"
            "の Take を採用",
            entity_kind="shot",
            entity_id=take.shot_id,
        )
        await _commit(conn)
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
        shot = await _fetch_shot(conn, take.shot_id)
        await _record_revision(
            conn,
            take.project_id,
            actor,
            f"{_titled('カット', shot.title if shot else '', take.shot_id)}"
            "の Take を不採用に",
            entity_kind="shot",
            entity_id=take.shot_id,
        )
        await _commit(conn)
        return await _fetch_take(conn, take_id)


#: 「まだ終わっていない」とみなすジョブの状態（暴走ガードの数え方）。
#: docs/EXTERNAL-API.md §3 は queued / running と書いているが、その 2 つの
#: 間に一瞬だけ通る prompting も走っている最中なので同じ扱いにする
#: （正本は :data:`app.jobs.PENDING_STATUSES`）。
PENDING_JOB_STATUSES = job_service.PENDING_STATUSES

#: 暴走ガードの「数えてから投入する」を直列化する錠（外部 API 用）。
#: 数えたあとに投入するまでのあいだ（英訳の待ちを含む）に別のリクエストが
#: 割り込むと、上限に達していても全部すり抜けてしまう。バックエンドは 1 本の
#: プロセスで動かすので、プロセス内の錠で足りる。
#: **投入する側だけが取る**（数えるほう自体は錠を取らない）。
#:
#: 括るのは「生成の投入」だけ（Shot のレンダリングと汎用ジョブ。どちらも同じ
#: 未完了ジョブのプールを数える）。書き出しのように別のプールを見るガードは、
#: この錠に相乗りさせず自前の錠を持つこと（遅い投入が無関係な投入を塞ぐ）。
PENDING_JOBS_LOCK = asyncio.Lock()


async def _story_project(
    conn: aiosqlite.Connection, payload: StoryCreate
) -> StudioProject:
    """一括投入の宛先（``project_id`` 優先、無ければ作品コードで引く）。"""
    project_id = (payload.project_id or "").strip()
    if project_id:
        project = await _fetch_project(conn, project_id)
        if project is None:
            raise StudioError(f"project '{project_id}' が見つかりません")
        return project
    code = (payload.project_code or "").strip()
    if not code:
        raise StudioError("project_id か project_code のどちらかを指定してください")
    async with conn.execute(
        "SELECT * FROM studio_projects WHERE code = ?", (code,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise StudioError(f"作品コード '{code}' のプロジェクトがありません")
    return _row_to_project(row)


async def create_story(
    payload: StoryCreate, *, actor: str = "external", pending_limit: int = 0
) -> StoryResult:
    """話 1 本ぶん（話 -> 場 -> Shot）をまとめて作る（外部 API の一括投入）。

    作成は 1 トランザクション: 途中の検証に落ちたら全ロールバックして
    :class:`StudioError` を投げる（中途半端な脚本を残さない）。

    ``render`` が立っているときは**コミットしたあと**に 1 カットずつ
    :func:`render_shot` を呼ぶ。生成は GPU と課金がからむので二択にはせず、
    投入に失敗したカットがあっても作成済みの脚本は残し、成否をカットごとに
    返す。``pending_limit`` を渡すと、未完了 Take がその数に達しているあいだは
    投入を見送る（外部 API の暴走ガード）。
    """
    async with get_db() as conn:
        project = await _story_project(conn, payload)
        try:
            episode = await _insert_episode(conn, project.id, payload.episode)
            created: list[tuple[StudioScene, list[StudioShot]]] = []
            for scene_body in payload.scenes:
                scene = await _insert_scene(conn, episode.id, scene_body)
                shots = [
                    await _insert_shot(
                        conn, project.id, shot_body, scene_id=scene.id
                    )
                    for shot_body in scene_body.shots
                ]
                created.append((scene, shots))
            await _record_revision(
                conn,
                project.id,
                actor,
                f"{_titled('話', episode.title)}を一括作成"
                f"（場 {len(created)} 件・カット"
                f" {sum(len(shots) for _scene, shots in created)} 件）",
                op="create",
            )
        except Exception:
            await conn.rollback()
            raise
        await _commit(conn)

    result = StoryResult(
        project_id=project.id,
        episode_id=episode.id,
        scenes=[
            StorySceneResult(
                id=scene.id,
                title=scene.title,
                shots=[
                    StoryShotResult(id=shot.id, title=shot.title) for shot in shots
                ],
            )
            for scene, shots in created
        ],
        shot_ids=[shot.id for _, shots in created for shot in shots],
    )
    if not payload.render:
        return result

    for scene_result in result.scenes:
        for shot_result in scene_result.shots:
            # 数えてから投入するまでを錠で括る（同時に走っている別の一括投入と
            # 数え合いになって、両方すり抜けるのを防ぐ）
            async with PENDING_JOBS_LOCK:
                # 数えるのは**未完了ジョブ**（:func:`app.jobs.count_pending_jobs`）。
                # Shot のレンダリングと汎用ジョブは 1 つのプールを分け合うので、
                # 外部 API 側のガード（``external._check_pending_jobs``）と同じ
                # 数え方にしておかないと、汎用ジョブで埋まっているときに一括
                # 投入だけが上限を素通りしてしまう。
                pending = await job_service.count_pending_jobs()
                if pending_limit > 0 and pending >= pending_limit:
                    shot_result.error = (
                        f"未完了のジョブが {pending} 件あります"
                        f"（上限 {pending_limit} 件）。"
                        "投入を見送りました"
                    )
                    continue
                try:
                    take = await render_shot(shot_result.id)
                except StudioError as exc:
                    shot_result.error = str(exc)
                except (job_service.JobValidationError, OSError) as exc:
                    # 引き継ぎフレームの複製（`_carry_over_start_frame`）や入力の
                    # 検証がここで落ちても、脚本はもうコミット済みで先行カットは
                    # 投入済みなので 500 にはしない（そのカットだけ理由を返す）
                    shot_result.error = f"投入に失敗しました: {exc}"
                else:
                    shot_result.take_id = take.id
                    shot_result.job_id = take.job_id
                    result.take_ids.append(take.id)
    return result


async def cancel_take(take_id: str) -> StudioTake | None:
    """Take に紐づくジョブを止める。行は残す（状態はジョブから導出）。"""
    take = await get_take(take_id)
    if take is None:
        return None
    if take.job_id:
        await job_service.cancel_job(take.job_id)
    return await get_take(take_id)


async def delete_take(take_id: str, *, actor: str = "user") -> bool:
    """Take を目録から外す（実行中ならジョブも止める。成果物は履歴に残す）。"""
    async with get_db() as conn:
        async with conn.execute(
            "SELECT shot_id, project_id, job_id FROM studio_takes WHERE id = ?",
            (take_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
        job_id = str(row["job_id"] or "")
        shot_id = str(row["shot_id"])
        project_id = str(row["project_id"])

    if job_id:
        job = await job_service.get_job(job_id, include_workflow=False)
        if job is not None and job.status in PENDING_JOB_STATUSES:
            await job_service.cancel_job(job_id)

    async with get_db() as conn:
        async with conn.execute(
            "SELECT 1 FROM studio_takes WHERE id = ?", (take_id,)
        ) as cur:
            if await cur.fetchone() is None:
                return False
        await conn.execute("DELETE FROM studio_takes WHERE id = ?", (take_id,))
        await conn.execute(
            "UPDATE studio_shots SET selected_take_id = NULL, updated_at = ?"
            " WHERE id = ? AND selected_take_id = ?",
            (_now(), shot_id, take_id),
        )
        shot = await _fetch_shot(conn, shot_id)
        await _record_revision(
            conn,
            project_id,
            actor,
            f"{_titled('カット', shot.title if shot else '', shot_id)}"
            "の Take を削除",
            entity_kind="shot",
            entity_id=shot_id,
            op="delete",
        )
        await _commit(conn)
        return True
