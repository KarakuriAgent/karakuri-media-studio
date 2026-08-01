import sys
from pathlib import Path

import pytest

# backend/ on sys.path so that `import app.…` works without installation.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import backends, kie  # noqa: E402
from app.workflows import SPECS  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_backends(monkeypatch):
    """外部バックエンドの認証情報と確認結果をテスト間で持ち越さない（SPEC §5.2）。

    開発機の環境変数に ``KIE_API_KEY`` が入っていると、それだけでテストが
    ネットワークに出てしまうので毎回消す。確認結果のキャッシュも同様。
    """
    monkeypatch.delenv(kie.API_KEY_ENV, raising=False)
    backends.invalidate()
    yield
    backends.invalidate()


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
        key, filename = key_and_file[spec.kind]
        outputs[spec.output_node] = {
            key: [{"filename": filename, "subfolder": "", "type": "output"}]
        }
    return outputs
