from fastapi import APIRouter

from .. import backends, comfy, library
from ..config import load_settings
from ..models import (
    DEFAULT_NEGATIVE_PROMPT,
    BackendInfo,
    Options,
    WorkflowOption,
    WorkflowSelect,
)
from ..workflow import MODEL_FIELDS, selectable_model_slots
from ..workflows import (
    DEFAULT_AUDIO_WORKFLOW,
    DEFAULT_IMAGE_WORKFLOW,
    DEFAULT_VIDEO_WORKFLOW,
    WorkflowSpec,
    audio_specs,
    image_specs,
    video_specs,
)
from .assets import AUDIO_EXT, IMAGE_EXT, VIDEO_EXT, list_assets
from .loras import loras_for

router = APIRouter(prefix="/api", tags=["options"])

NEGATIVE_PRESETS = {
    # empty == keep whatever the selected template ships with (SPEC §3.1)
    "template": "",
    "current": DEFAULT_NEGATIVE_PROMPT,
    "author": (
        "blurry, oversaturated, pixelated, low resolution, grainy, distorted, "
        "noise, compression artifacts, jpeg artifacts, glitches, watermark, "
        "text, logo, signature, copyright, subtitles, distorted sound, "
        "saturated sound, loud"
    ),
}


def _workflow_option(spec: WorkflowSpec) -> WorkflowOption:
    return WorkflowOption(
        id=spec.id,
        label=spec.label,
        kind=spec.kind,
        family=spec.family,
        notes=spec.notes,
        requires=list(spec.requires),
        supports=sorted(spec.inject),
        accepts_start_image=spec.accepts_start_image,
        image_label=spec.image_label,
        min_duration=spec.min_duration,
        max_duration=spec.max_duration,
        default_duration=spec.default_duration,
        selects=[
            WorkflowSelect(
                name=name,
                label=select.label,
                choices=list(select.choices),
                default=select.fallback,
                auto=bool(select.auto),
                hint=select.hint,
            )
            for name, select in spec.selects.items()
        ],
        prompt_required=spec.prompt_required,
        accepts_video_loras=spec.lora_chain is not None,
        backend=spec.backend,
    )


@router.get("/options", response_model=Options)
async def get_options() -> Options:
    """Form choices. ComfyUI being down is reported inline, never as an HTTP error."""
    settings = load_settings()
    # 外部バックエンド（kie.ai など）は**認証が通ることを確かめられたものだけ**
    # ワークフロー一覧に載る（SPEC §5.2）。未確認ならここで 1 度だけ確かめる。
    statuses = await backends.ensure_all()
    options = Options(
        comfy_target=settings.comfy_target,
        comfy_url=settings.active_comfy_url(),
        audio_assets=list_assets("audio", AUDIO_EXT),
        image_assets=list_assets("image", IMAGE_EXT),
        video_assets=list_assets("video", VIDEO_EXT),
        negative_presets=NEGATIVE_PRESETS,
        image_workflows=[_workflow_option(spec) for spec in image_specs()],
        video_workflows=[_workflow_option(spec) for spec in video_specs()],
        audio_workflows=[_workflow_option(spec) for spec in audio_specs()],
        default_video_workflow=DEFAULT_VIDEO_WORKFLOW,
        default_image_workflow=DEFAULT_IMAGE_WORKFLOW,
        default_audio_workflow=DEFAULT_AUDIO_WORKFLOW,
        # 実行時に選べるモデル（候補が 2 件以上あるスロットだけ、SPEC §3.3）
        model_slots=selectable_model_slots(
            settings.overrides_for(), settings.choices_for()
        ),
        backends=[
            BackendInfo(
                backend=status.backend,
                status=status.state,
                detail=status.detail,
                available=status.available,
            )
            for status in statuses
        ],
    )

    # LoRA 登録は接続先ごと（共通行を含む、SPEC §5）: 生成フォームには現在の
    # 接続先で使えるものだけを出す。
    options.loras = await loras_for()
    # ライブラリ（SPEC §7.2）: 入力欄の「ライブラリから選択」とエージェントの
    # CHOICES が読む。フォーム側の NSFW フィルタはモーダルの中で行う。
    options.library = await library.list_items()

    try:
        info = await comfy.get_object_info()
    except comfy.ComfyError as exc:
        # 接続先に合わせた案内に置き換える（RunPod の Pod 停止中など、SPEC §5.1）
        options.comfy_error = comfy.display_error(exc)
        return options

    options.comfy_connected = True
    errors: list[str] = []
    for target, class_type, field in (
        ("aspect_ratios", "ResolutionSelector", "aspect_ratio"),
        ("lora_files", "LoraLoaderModelOnly", "lora_name"),
    ):
        try:
            setattr(options, target, comfy.combo_options(info, class_type, field))
        except comfy.ComfyError as exc:
            errors.append(str(exc))
    # 設定ページの候補入力の datalist 用。テンプレートが使う class_type が入って
    # いない ComfyUI もあるので、取れなかったものは黙って省く（欠落は
    # /api/health の custom node チェックが報告する）。
    for class_type, field in sorted(MODEL_FIELDS):
        try:
            options.model_files[f"{class_type}.{field}"] = comfy.combo_options(
                info, class_type, field
            )
        except comfy.ComfyError:
            continue
    if errors:
        options.comfy_error = "; ".join(errors)
    return options
