#!/usr/bin/env python3
"""``models.txt`` を workflow/ のテンプレートと設定から生成する。

各テンプレートのローダーノードが要求するモデルファイル名と、その置き場所
（``backend/app/workflow.py`` の ``MODEL_SUBFOLDERS``）はアプリ側が既に単一の
情報源として持っているので、それをそのまま使う。ここでやるのは
「``<subfolder>/<filename>`` に取得元 URL を添える」ことだけ。

テンプレートの既定値に加えて、**設定（``runtime/config.json``）でモデルを差し替え
たり候補として登録したファイル名**も対象にする（SPEC §3.3 の
``model_overrides`` / ``model_choices``）。置き場所は同じスロットの
``MODEL_SUBFOLDERS`` から決まる。さらに、設定画面の「LoRA 管理」に登録した LoRA
（DB の ``loras`` テーブル）も ``loras/<lora_name>`` として対象にする。設定や DB が
読めない環境（他所でこのスクリプトだけを動かす場合など）ではテンプレート既定値
だけで生成する。

URL は :data:`KNOWN_URLS` → 設定の ``model_download_urls`` の順で探す。
:data:`KNOWN_URLS` に載せてよいのは**確実に分かっているものだけ**。当て推量の URL を
書くと、間違ったファイルが正しい名前で置かれて「ComfyUI からは見えるのに結果が
壊れる」という一番たちの悪い壊れ方をするので、分からないものは ``# TODO url``
のコメント行として残し、利用者に埋めてもらう。

使い方（リポジトリのルートから）::

    python3 deploy/runpod/gen_models_manifest.py > deploy/runpod/models.txt

手元の設定ぶんを Pod に持っていくときは、生成したものを Network Volume の
``/workspace/models.local.txt`` に置く（deploy/runpod/README.md）。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.workflow import model_fields  # noqa: E402

# ---------------------------------------------------------------------------
# 取得元 URL
# ---------------------------------------------------------------------------
# キーはワークフローが要求する**ファイル名**（置き場所は自動で決まる）。値は
# 直リンク。ダウンロード側は保存名をキーのファイル名に揃えるので、配布元の
# ファイル名が違っていても構わない。
#
# ここに載せてよいのは「そのファイルが Hugging Face で公式に配布されていると
# 分かっているもの」だけ。増やすときは実際に 1 度落として中身を確かめること。
KNOWN_URLS: dict[str, str] = {
    # Wan 2.1 / 2.2 系の共通部品（Comfy-Org の再パッケージ配布）
    "clip_vision_h.safetensors": (
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/"
        "split_files/clip_vision/clip_vision_h.safetensors"
    ),
    "umt5_xxl_fp16.safetensors": (
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/"
        "split_files/text_encoders/umt5_xxl_fp16.safetensors"
    ),
    # 配布名は wan_2.1_vae.safetensors。保存名はワークフローに合わせて変わる。
    "Wan2_1_VAE_bf16.safetensors": (
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/"
        "split_files/vae/wan_2.1_vae.safetensors"
    ),
    # Qwen-Image / Qwen-Image-Edit 系（Comfy-Org の再パッケージ配布）
    "qwen_image_vae.safetensors": (
        "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/"
        "split_files/vae/qwen_image_vae.safetensors"
    ),
    "qwen_2.5_vl_7b_fp8_scaled.safetensors": (
        "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/"
        "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"
    ),
    # FLUX の VAE（ae.safetensors）。Apache-2.0 の schnell 側から取る。
    "ae.safetensors": (
        "https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/"
        "ae.safetensors"
    ),
    # LTX 2.3 系（Lightricks 直営 + Comfy-Org 再パッケージ。ingredients IC-LoRA は
    # Lightricks 本家が gated のため Comfy-Org ミラーを使う）
    "ltx-2.3-22b-dev-fp8.safetensors": (
        "https://huggingface.co/Lightricks/LTX-2.3-fp8/resolve/main/"
        "ltx-2.3-22b-dev-fp8.safetensors"
    ),
    "ltx-2.3-spatial-upscaler-x2-1.1.safetensors": (
        "https://huggingface.co/Lightricks/LTX-2.3/resolve/main/"
        "ltx-2.3-spatial-upscaler-x2-1.1.safetensors"
    ),
    "ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors": (
        "https://huggingface.co/Comfy-Org/ltx-2.3/resolve/main/"
        "split_files/loras/ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors"
    ),
    "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors": (
        "https://huggingface.co/Lightricks/LTX-2.3-22b-IC-LoRA-Union-Control/resolve/main/"
        "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors"
    ),
    "ltx-2.3-id-lora-talkvid-3k.safetensors": (
        "https://huggingface.co/Comfy-Org/ltx-2.3/resolve/main/"
        "split_files/loras/ltx-2.3-id-lora-talkvid-3k.safetensors"
    ),
    "ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors": (
        "https://huggingface.co/Comfy-Org/ltx-2.3/resolve/main/"
        "split_files/loras/ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors"
    ),
    # TE はひとつ前の Comfy-Org/ltx-2 リポジトリ側にある
    "gemma_3_12B_it_fp4_mixed.safetensors": (
        "https://huggingface.co/Comfy-Org/ltx-2/resolve/main/"
        "split_files/text_encoders/gemma_3_12B_it_fp4_mixed.safetensors"
    ),
    "gemma-3-12b-it-abliterated_lora_rank64_bf16.safetensors": (
        "https://huggingface.co/Comfy-Org/ltx-2/resolve/main/"
        "split_files/loras/gemma-3-12b-it-abliterated_lora_rank64_bf16.safetensors"
    ),
    # Stable Audio 3（stabilityai 本家は Transformers 形式なので Comfy-Org 版を使う）
    "stable_audio_3_medium_base.safetensors": (
        "https://huggingface.co/Comfy-Org/stable-audio-3/resolve/main/"
        "checkpoints/stable_audio_3_medium_base.safetensors"
    ),
    "qwen3.5_2b_bf16.safetensors": (
        "https://huggingface.co/Comfy-Org/Qwen3.5/resolve/main/"
        "text_encoders/qwen3.5_2b_bf16.safetensors"
    ),
    "t5gemma_b_b_ul2.safetensors": (
        "https://huggingface.co/Comfy-Org/stable-audio-3/resolve/main/"
        "text_encoders/t5gemma_b_b_ul2.safetensors"
    ),
    # ACE-Step 1.5
    "acestep_v1.5_xl_sft_bf16.safetensors": (
        "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/"
        "split_files/diffusion_models/acestep_v1.5_xl_sft_bf16.safetensors"
    ),
    "qwen_0.6b_ace15.safetensors": (
        "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/"
        "split_files/text_encoders/qwen_0.6b_ace15.safetensors"
    ),
    "qwen_4b_ace15.safetensors": (
        "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/"
        "split_files/text_encoders/qwen_4b_ace15.safetensors"
    ),
    "ace_1.5_vae.safetensors": (
        "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/"
        "split_files/vae/ace_1.5_vae.safetensors"
    ),
    # Qwen-Image Edit 2511
    "qwen_image_edit_2511_int8_convrot.safetensors": (
        "https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/"
        "split_files/diffusion_models/qwen_image_edit_2511_int8_convrot.safetensors"
    ),
    "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors": (
        "https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/"
        "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"
    ),
    # Krea 2 turbo（このリポジトリは split_files/ プレフィックス無し）
    "krea2_turbo_fp8_scaled.safetensors": (
        "https://huggingface.co/Comfy-Org/Krea-2/resolve/main/"
        "diffusion_models/krea2_turbo_fp8_scaled.safetensors"
    ),
    "qwen3vl_4b_fp8_scaled.safetensors": (
        "https://huggingface.co/Comfy-Org/Krea-2/resolve/main/"
        "text_encoders/qwen3vl_4b_fp8_scaled.safetensors"
    ),
    # Z-Image turbo
    "z_image_turbo_bf16.safetensors": (
        "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/"
        "split_files/diffusion_models/z_image_turbo_bf16.safetensors"
    ),
    "qwen_3_4b.safetensors": (
        "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/"
        "split_files/text_encoders/qwen_3_4b.safetensors"
    ),
    # Anima（公式 org は circlestone-labs）
    "anima-base-v1.0.safetensors": (
        "https://huggingface.co/circlestone-labs/Anima/resolve/main/"
        "split_files/diffusion_models/anima-base-v1.0.safetensors"
    ),
    "qwen_3_06b_base.safetensors": (
        "https://huggingface.co/circlestone-labs/Anima/resolve/main/"
        "split_files/text_encoders/qwen_3_06b_base.safetensors"
    ),
    # Wan Dancer（専用リポジトリ。split_files/ 無し）
    "wan2.2_dancer_14b_global_fp8_scaled.safetensors": (
        "https://huggingface.co/Comfy-Org/Wan-Dancer/resolve/main/"
        "diffusion_models/wan2.2_dancer_14b_global_fp8_scaled.safetensors"
    ),
    "wan2.2_dancer_14b_local_fp8_scaled.safetensors": (
        "https://huggingface.co/Comfy-Org/Wan-Dancer/resolve/main/"
        "diffusion_models/wan2.2_dancer_14b_local_fp8_scaled.safetensors"
    ),
    "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors": (
        "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/"
        "Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"
    ),
    # MoGe 2（ComfyUI 用 fp16 単一ファイルは Comfy-Org 版のみ）
    "moge_2_vitl_normal_fp16.safetensors": (
        "https://huggingface.co/Comfy-Org/MoGe/resolve/main/"
        "geometry_estimation/moge_2_vitl_normal_fp16.safetensors"
    ),
}

HEADER = """\
# ComfyUI が使うモデルの一覧（deploy/runpod/entrypoint.sh が読む）。
#
# 行の形式:  <subfolder>/<filename> <url>
#   subfolder … /workspace/ComfyUI/models からの相対パス
#   filename  … ワークフローが要求するファイル名（この名前で保存される）
#   url       … 直リンク。huggingface.co / hf.co には HF_TOKEN、civitai.com には
#               CIVITAI_API_KEY が Bearer で付く（無関係なホストには付かない）
#
# 空行と '#' から始まる行は無視される。すでに置いてあるファイルは飛ばすので、
# 2 回目以降の起動ではこのファイルを読むだけで終わる。
#
# `# TODO url` の行は取得元が確定していないもの。使うワークフローのぶんだけ
# URL を書き足せばよい（全部を揃える必要はない）。
#
# コメントの `（設定で指定）` は、テンプレートの既定ではなく設定（モデルの
# 差し替え / 候補、および「LoRA 管理」に登録した LoRA）で足されたモデル。
# それ以外はテンプレートの既定値。
#
# このファイルは自動生成できる:
#   python3 deploy/runpod/gen_models_manifest.py > deploy/runpod/models.txt
"""


def settings_models() -> tuple[dict[str, str], dict[str, list[str]], dict[str, str]]:
    """設定のモデル指定 ``(overrides, choices, download_urls)``。

    設定が読めない環境（backend の依存が入っていない、``runtime/config.json`` が
    壊れている等）では空を返し、テンプレート既定値だけで生成できるようにする。
    """
    try:
        from app.config import load_settings

        settings = load_settings()
    except Exception as exc:  # noqa: BLE001 - 設定は「あれば使う」だけ
        print(f"設定を読めませんでした（テンプレート既定のみ）: {exc}", file=sys.stderr)
        return {}, {}, {}
    return (
        dict(settings.model_overrides),
        {key: list(names) for key, names in settings.model_choices.items()},
        dict(settings.model_download_urls),
    )


def registered_loras() -> list[tuple[str, str]]:
    """設定画面の「LoRA 管理」に登録済みの LoRA ``[(ファイル名, 表示名), …]``。

    人物 LoRA はワークフローのテンプレートには出てこない（実行時に差し込まれる）
    ので、テンプレート走査では拾えない。DB を直接読む（読めなければ空）。
    """
    try:
        from app.paths import DB_PATH

        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT lora_name, display_name FROM loras ORDER BY sort_order, id"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - DB も「あれば使う」だけ
        print(f"LoRA 登録を読めませんでした: {exc}", file=sys.stderr)
        return []
    return [(str(name or ""), str(display or "")) for name, display in rows]


def _usable(name: str) -> bool:
    """マニフェストに書けるファイル名か（絶対パスと ``..`` は download 側で弾かれる）。"""
    return bool(name) and not name.startswith("/") and ".." not in Path(name).parts


def main() -> None:
    overrides, choices, download_urls = settings_models()

    # path -> (使うワークフローの表示名, テンプレート既定に無く設定由来だけか)
    seen: dict[str, set[str]] = {}
    from_settings_only: dict[str, bool] = {}

    def add(path: str, label: str, from_settings: bool) -> None:
        seen.setdefault(path, set()).add(label)
        from_settings_only[path] = from_settings_only.get(path, True) and from_settings

    for field in model_fields():
        subfolder = field.subfolder or "checkpoints"
        label = field.workflow_label or field.workflow_id
        if _usable(field.default):
            add(f"{subfolder}/{field.default}", label, False)
        # 設定で差し替えたもの + そのスロットに登録した候補（SPEC §3.3）
        for name in [overrides.get(field.key, ""), *(choices.get(field.key) or ())]:
            name = (name or "").strip()
            if name != field.default and _usable(name):
                add(f"{subfolder}/{name}", label, True)

    # 「LoRA 管理」に登録した人物 LoRA（置き場所は必ず loras/）。テンプレート由来の
    # 行と同じファイル名なら add() が一本化する。
    for lora_name, display_name in registered_loras():
        if _usable(lora_name):
            add(f"loras/{lora_name}", f"LoRA「{display_name or lora_name}」", True)

    print(HEADER)
    for path in sorted(seen):
        filename = path.split("/", 1)[1]
        users = ", ".join(sorted(seen[path]))
        origin = "（設定で指定）" if from_settings_only[path] else ""
        print(f"# {users}{origin}")
        url = KNOWN_URLS.get(filename) or download_urls.get(filename)
        print(f"{path} {url}" if url else f"# TODO url\n# {path} <url>")
        print()


if __name__ == "__main__":
    main()
