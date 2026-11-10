"""Remotion を実際に走らせて確かめる（重いので、明示したときだけ）。

``KARAKURI_REMOTION_E2E=1`` を付けて、同梱の ``remotion/`` に依存が入っている
（``npm --prefix remotion install`` 済み）ときだけ走る。素材はその場で ffmpeg で
作り、テストの中で立てた HTTP サーバーから配る（OffthreadVideo は http(s) しか
読めない）。

- ``FxOverlay`` の ``video`` イベント（``loop``）を、素材を初めて読むコマに置くと
  「Fetching … from server was called but not cleared after 28000ms」で落ちていた
  （尺を読み終えたところで OffthreadVideo を ``<Loop>`` で包み直していたため。
  ``remotion/src/fx/Video.tsx``）。落ちるかどうかは取得の間合いしだいで、実際の
  タイムラインの並びでは毎回、この合成の素材では時々落ちた。包み直さないことは
  ``frontend/src/components/studio/FxVideoEvent.test.tsx`` が決まった形で確かめる。
  ここは同じ並びが実際のレンダで最後まで焼けることを見る
"""

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from app import remotion

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    os.environ.get("KARAKURI_REMOTION_E2E") != "1"
    or not (ROOT / "remotion" / "node_modules").is_dir()
    or shutil.which("ffmpeg") is None
    or shutil.which("npx") is None,
    reason="KARAKURI_REMOTION_E2E=1 と remotion の依存・ffmpeg・npx が要る",
)


@pytest.fixture
def served(tmp_path):
    """``tmp_path`` を配る HTTP サーバー（``http://127.0.0.1:<port>``）。

    アプリと同じ配り方（uvicorn + StaticFiles）にする。
    """
    import socket
    import time

    import uvicorn
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    app = FastAPI()
    app.mount("/", StaticFiles(directory=str(tmp_path)), name="media")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _ffmpeg(*arguments: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *arguments], check=True)


async def test_a_looping_video_event_renders_without_timing_out(
    tmp_path, served, monkeypatch
):
    monkeypatch.setattr(remotion, "REMOTION_TMP_DIR", tmp_path / "runtime")
    settings = remotion.load_settings().model_copy(update={"remotion_enabled": True})
    monkeypatch.setattr(remotion, "load_settings", lambda: settings)
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=20",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "base.mp4"),
    )
    # アルファ付きの WebM（VP9）の丸。動く漫符の代わり
    _ffmpeg(
        "-f", "lavfi", "-i",
        "color=c=black@0.0:s=540x540:r=30:d=3.2,format=yuva420p,"
        "geq=lum='p(X,Y)':a='if(lt(hypot(X-270,Y-270),100+50*sin(T*6)),255,0)'",
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
        str(tmp_path / "alpha.webm"),
    )
    video = {"src": f"{served}/alpha.webm", "w": 0.26, "maxH": 0.15, "loop": True}
    props = {
        "fps": 30, "width": 1080, "height": 1920, "durationInSeconds": 20,
        "base": {"src": f"{served}/base.mp4", "fit": "fill", "muted": True},
        "events": [
            {"type": "invertShake", "t": 2.4, "frames": 0, "shakeTail": 0.15},
            {"type": "video", "t": 7.8667, "until": 10.4333, "cx": 0.72, "cy": 0.2,
             **video},
            {"type": "video", "t": 17.6667, "until": 20, "cx": 0.3, "cy": 0.2,
             **video},
        ],
    }
    output = tmp_path / "out.mp4"
    await remotion.render("e2e", "FxOverlay", props, output)
    frames = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(output)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert int(frames) == 600
