import sys
from pathlib import Path

import pytest

# backend/ on sys.path so that `import app.…` works without installation.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import config  # noqa: E402
from app.workflows import SPECS  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path_factory):
    """開発機の ``runtime/config.json`` をテストへ持ち込まない。"""
    monkeypatch.setattr(
        config, "CONFIG_PATH", tmp_path_factory.mktemp("config") / "config.json"
    )
    monkeypatch.setattr(config, "_settings", None)
    yield
    config._settings = None


def fake_outputs(
    image: str = "img.png", video: str = "vid.mp4", audio: str = "track.mp3"
) -> dict:
    """A ``/history`` ``outputs`` mapping covering every template's output node.

    Each workflow saves through its own SaveImage / SaveVideo / SaveAudioMP3
    node id, so the ComfyUI fakes answer for all of them and stay valid
    whichever workflow a test selects.
    """
    key_and_file = {
        "image": ("images", image),
        "video": ("videos", video),
        "audio": ("audio", audio),
    }
    outputs: dict[str, dict] = {}
    for spec in SPECS:
        if spec.backend != "comfyui":
            continue  # 外部バックエンドは ComfyUI のノードを持たない（SPEC §5.2）
        key, filename = key_and_file[spec.kind]
        outputs[spec.output_node] = {
            key: [{"filename": filename, "subfolder": "", "type": "output"}]
        }
    return outputs
