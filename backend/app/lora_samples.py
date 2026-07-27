"""LoRA サンプル画像の置き場所とシリアライズ補助。

各 LoRA には「この見た目が正解」というサンプル画像を複数登録できる。
ファイルは ``assets/lora_samples/<lora_id>/`` に保存され、既存の ``/assets``
静的マウントでそのまま配信される。DB の ``loras.sample_images`` カラムには
ファイル名の JSON 配列だけを持つ（API へはフルの URL に展開して返す）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import Lora
from .paths import ASSETS_DIR

SAMPLES_KIND = "lora_samples"
SAMPLE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_stem(name: str, fallback: str = "sample") -> str:
    return _SAFE.sub("_", name)[:64] or fallback


def sample_dir(lora_id: int) -> Path:
    return ASSETS_DIR / SAMPLES_KIND / str(lora_id)


def sample_url(lora_id: int, name: str) -> str:
    return f"/assets/{SAMPLES_KIND}/{lora_id}/{name}"


def sample_names(lora: Lora) -> list[str]:
    """API 形式（URL）からファイル名だけを取り出す。"""
    return [Path(url).name for url in lora.sample_images]


def sample_paths(lora: Lora) -> list[Path]:
    """実在するサンプル画像ファイルの一覧（エージェント workdir へのコピー用）。"""
    directory = sample_dir(lora.id)
    return [
        directory / name
        for name in sample_names(lora)
        if (directory / name).is_file()
    ]


def row_to_lora(row) -> Lora:
    """DB 行 → :class:`Lora`。``sample_images`` は JSON 文字列から URL に展開する。"""
    data = dict(row)
    raw = data.pop("sample_images", None)
    try:
        names = json.loads(raw or "[]")
    except (TypeError, ValueError):
        names = []
    lora = Lora(**data)
    lora.sample_images = [
        sample_url(lora.id, name)
        for name in names
        if isinstance(name, str) and name
    ]
    return lora
