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


def _frame_rgb(path: Path, frame: int, width: int, height: int) -> bytes:
    """``path`` の ``frame`` 番目のコマを RGB の生のバイト列で取り出す。"""
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vf", f"select=eq(n\\,{frame})", "-vframes", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout[: width * height * 3]


def _pixel(raw: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    i = (y * width + x) * 3
    return raw[i], raw[i + 1], raw[i + 2]


async def test_cover_fills_the_corners_and_fit_start_keeps_the_slam_inside(
    tmp_path, served, monkeypatch
):
    """``sprite`` / ``video`` の ``cover`` は中心を上に寄せても四隅まで覆い、
    ``imageSlam`` の ``fitStart`` は叩き込みはじめのコマでも画面の左右に収まる。"""
    monkeypatch.setattr(remotion, "REMOTION_TMP_DIR", tmp_path / "runtime")
    settings = remotion.load_settings().model_copy(update={"remotion_enabled": True})
    monkeypatch.setattr(remotion, "load_settings", lambda: settings)
    # 縦長でない（正方形・横長の）素材。集中線・幅の広い文字の代わり
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=300x300", "-frames:v", "1",
            str(tmp_path / "square.png"))
    _ffmpeg("-f", "lavfi", "-i", "color=c=lime:s=900x150", "-frames:v", "1",
            str(tmp_path / "wide.png"))
    _ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=400x300:r=30:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "blue.mp4"))
    width, height = 270, 480
    props = {
        "fps": 30, "width": width, "height": height, "durationInSeconds": 1,
        "base": {"src": "color:#000000"},
        "events": [
            # 0〜0.3 秒: 集中線の代わりの赤（中心は上寄り。前の固定の大きさなら下が切れる）
            {"type": "sprite", "t": 0, "until": 0.3, "src": f"{served}/square.png",
             "cx": 0.5, "cy": 0.25, "w": 0.3, "maxH": 0.2, "motion": "none",
             "cover": True},
            # 0.3〜0.6 秒: 同じことを動画で（横長の素材・少し回す）
            {"type": "video", "t": 0.3, "until": 0.6, "src": f"{served}/blue.mp4",
             "cx": 0.4, "cy": 0.3, "rot": 10, "transparent": False, "cover": True},
            # 0.6 秒〜: 幅の広い文字を 1.6 倍で叩き込む（fitStart で左右に収める）
            {"type": "imageSlam", "t": 0.6, "until": 1, "src": f"{served}/wide.png",
             "cx": 0.5, "cy": 0.5, "w": 0.75, "maxH": 0.3, "snap": [1.6, 1.0],
             "spring": False, "flash": 0, "fitStart": True},
        ],
    }
    output = tmp_path / "out.mp4"
    await remotion.render("e2e", "FxOverlay", props, output)

    corners = [(1, 1), (width - 2, 1), (1, height - 2), (width - 2, height - 2)]
    red = _frame_rgb(output, 3, width, height)
    for x, y in corners:
        r, g, b = _pixel(red, width, x, y)
        assert r > 120 and g < 80 and b < 80, (x, y, (r, g, b))
    blue = _frame_rgb(output, 12, width, height)
    for x, y in corners:
        r, g, b = _pixel(blue, width, x, y)
        assert b > 120 and r < 80, (x, y, (r, g, b))
    # 叩き込みはじめのコマ（0.6 秒 = 18 コマ目）: 左右の端は下地の黒のまま、内側は緑
    slam = _frame_rgb(output, 18, width, height)
    assert max(_pixel(slam, width, 0, height // 2)) < 60
    assert max(_pixel(slam, width, width - 1, height // 2)) < 60
    r, g, b = _pixel(slam, width, 8, height // 2)
    assert g > 120 and r < 80, (r, g, b)
