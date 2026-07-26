import json

from .models import Settings
from .paths import CONFIG_PATH, GROK_WORKDIR

_settings: Settings | None = None


def _defaults() -> Settings:
    return Settings(grok_workdir=str(GROK_WORKDIR))


def load_settings() -> Settings:
    """Read settings from runtime/config.json (cached)."""
    global _settings
    if _settings is None:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            _settings = _defaults().model_copy(update=data)
        else:
            _settings = _defaults()
    return _settings


def save_settings(settings: Settings) -> Settings:
    global _settings
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _settings = settings
    return _settings


def update_settings(patch: dict) -> Settings:
    """Partial update. Unknown keys are ignored by the model."""
    merged = load_settings().model_copy(update={
        k: v for k, v in patch.items() if k in Settings.model_fields
    })
    return save_settings(merged)
