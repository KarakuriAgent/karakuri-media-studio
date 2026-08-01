import sys
from pathlib import Path

import pytest

# backend/ on sys.path so that `import app.…` works without installation.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import backends, config, grok_media, kie  # noqa: E402
from app.workflows import SPECS  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_backends(monkeypatch, tmp_path_factory):
    """外部バックエンドの認証情報と確認結果をテスト間で持ち越さない（SPEC §5.2）。

    開発機の設定が漏れると、それだけでテストがネットワークに出たり、環境ごとに
    ワークフロー一覧が変わったりする。ここで断つのは 3 つ:

    - ``KIE_API_KEY``（環境変数）と ``runtime/config.json``（開発機の kie キー）
    - ``~/.grok/auth.json``（grok CLI にサインイン済みの開発機では grok_cli 系
      ワークフローが選択肢に出てしまう）
    - 確認結果のキャッシュ
    """
    monkeypatch.delenv(kie.API_KEY_ENV, raising=False)
    monkeypatch.setattr(
        config, "CONFIG_PATH", tmp_path_factory.mktemp("config") / "config.json"
    )
    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.setattr(
        grok_media, "auth_path", lambda: Path("/nonexistent/.grok/auth.json")
    )
    backends.invalidate()
    yield
    config._settings = None
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
