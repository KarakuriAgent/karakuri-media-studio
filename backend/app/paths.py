from pathlib import Path

# backend/app/paths.py -> project root
ROOT = Path(__file__).resolve().parents[2]

OUTPUTS_DIR = ROOT / "outputs"
ASSETS_DIR = ROOT / "assets"
RUNTIME_DIR = ROOT / "runtime"
GROK_WORKDIR = RUNTIME_DIR / "grok-workdir"

FRONTEND_DIST_DIR = ROOT / "frontend" / "dist"

DB_PATH = ROOT / "app.db"
CONFIG_PATH = RUNTIME_DIR / "config.json"
WORKFLOW_TEMPLATE_PATH = ROOT / "video-gen.json"


def ensure_dirs() -> None:
    for d in (OUTPUTS_DIR, ASSETS_DIR, RUNTIME_DIR, GROK_WORKDIR):
        d.mkdir(parents=True, exist_ok=True)
