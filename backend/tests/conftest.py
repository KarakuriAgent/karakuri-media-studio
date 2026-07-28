import sys
from pathlib import Path

# backend/ on sys.path so that `import app.…` works without installation.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.workflows import SPECS  # noqa: E402


def fake_outputs(image: str = "img.png", video: str = "vid.mp4") -> dict:
    """A ``/history`` ``outputs`` mapping covering every template's output node.

    Each workflow saves through its own SaveImage / SaveVideo node id, so the
    ComfyUI fakes answer for all of them and stay valid whichever workflow a
    test selects.
    """
    outputs: dict[str, dict] = {}
    for spec in SPECS:
        if spec.kind == "image":
            outputs[spec.output_node] = {
                "images": [{"filename": image, "subfolder": "", "type": "output"}]
            }
        else:
            outputs[spec.output_node] = {
                "videos": [{"filename": video, "subfolder": "", "type": "output"}]
            }
    return outputs
