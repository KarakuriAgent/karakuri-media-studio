"""コンタクトシート（SPEC §7.2）: 抜く秒の決め方とグリッドの寸法。

ffmpeg を呼ぶのは :func:`app.contact_sheet.extract_frame` だけで、そこは実物の
ffmpeg があるときにまとめて 1 本だけ確かめる（無ければ skip）。
"""

import asyncio
import shutil
from io import BytesIO

import pytest
from PIL import Image

from app import contact_sheet

has_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg / ffprobe が無い環境ではコマを抜けない",
)


def cells(count: int, size=(640, 360)):
    return [
        (Image.new("RGB", size, (index * 10 % 255, 0, 0)), f"{index}.00s")
        for index in range(count)
    ]


# --------------------------------------------------------------------------
# どの秒を抜くか
# --------------------------------------------------------------------------

def test_explicit_seconds_win():
    assert contact_sheet.plan_seconds(
        seconds=[1, 2.5], span={"start": 0, "end": 9, "step": 3}, duration=10
    ) == [1.0, 2.5]


def test_a_range_includes_its_end():
    assert contact_sheet.plan_seconds(
        span={"start": 1, "end": 3, "step": 0.5}, duration=10
    ) == [1.0, 1.5, 2.0, 2.5, 3.0]


def test_frames_need_the_fps():
    assert contact_sheet.plan_seconds(frames=[0, 24, 48], fps=24) == [0.0, 1.0, 2.0]
    with pytest.raises(contact_sheet.ContactSheetError):
        contact_sheet.plan_seconds(frames=[0, 24])


def test_nothing_given_spreads_over_the_duration():
    values = contact_sheet.plan_seconds(duration=12)
    assert len(values) == contact_sheet.DEFAULT_COUNT
    assert values[0] == 0.5 and values[-1] == 11.5
    # 尺も指定も無ければ、どこを抜けばよいか分からないので断る
    with pytest.raises(contact_sheet.ContactSheetError):
        contact_sheet.plan_seconds()


def test_seconds_are_clamped_to_the_duration():
    assert contact_sheet.plan_seconds(seconds=[100], duration=10) == [9.95]


def test_too_many_frames_are_rejected():
    with pytest.raises(contact_sheet.ContactSheetError):
        contact_sheet.plan_seconds(seconds=list(range(contact_sheet.MAX_FRAMES + 1)))
    with pytest.raises(contact_sheet.ContactSheetError):
        contact_sheet.plan_seconds(span={"start": 0, "end": 1000, "step": 1})
    with pytest.raises(contact_sheet.ContactSheetError):
        contact_sheet.plan_seconds(span={"start": 0, "end": 5, "step": 0})
    with pytest.raises(contact_sheet.ContactSheetError):
        contact_sheet.plan_seconds(seconds=[-1])


def test_labels_carry_the_second_and_the_frame_number():
    assert contact_sheet.frame_label(1.5, 24) == "1.50s  #36"
    assert contact_sheet.frame_label(1.5, None) == "1.50s"


# --------------------------------------------------------------------------
# グリッドの寸法
# --------------------------------------------------------------------------

def test_the_grid_fills_rows_from_the_left():
    assert contact_sheet.grid_size(7, 4) == (4, 2)
    assert contact_sheet.grid_size(8, 4) == (4, 2)
    assert contact_sheet.grid_size(9, 4) == (4, 3)
    # コマが列数より少なければ列も詰める
    assert contact_sheet.grid_size(2, 4) == (2, 1)


def test_the_sheet_size_follows_the_columns_and_the_cell_width():
    sheet = contact_sheet.build_grid(cells(5), columns=3, width=320, labels=True)
    cell_width, cell_height, band = contact_sheet.cell_size((640, 360), 320, True)
    assert (cell_width, cell_height) == (320, 180)
    assert band > 0
    assert sheet.size == (3 * 320, 2 * (180 + band))


def test_labels_can_be_turned_off():
    sheet = contact_sheet.build_grid(cells(4), columns=2, width=200, labels=False)
    assert sheet.size == (2 * 200, 2 * 112)  # 200 * 360/640 = 112.5 -> 112


def test_a_portrait_source_keeps_its_aspect():
    sheet = contact_sheet.build_grid(
        cells(2, size=(720, 1280)), columns=2, width=180, labels=False
    )
    assert sheet.size == (2 * 180, 320)


def test_an_empty_sheet_is_rejected():
    with pytest.raises(contact_sheet.ContactSheetError):
        contact_sheet.build_grid([], columns=2, width=200)
    with pytest.raises(contact_sheet.ContactSheetError):
        contact_sheet.check_columns(0)
    with pytest.raises(contact_sheet.ContactSheetError):
        contact_sheet.check_width(8)


# --------------------------------------------------------------------------
# ffmpeg を通した 1 本
# --------------------------------------------------------------------------

@has_ffmpeg
def test_a_real_video_becomes_a_sheet(tmp_path):
    video = tmp_path / "clip.mp4"
    asyncio.run(_make_video(video))
    data, seconds = asyncio.run(
        contact_sheet.render_contact_sheet(
            video, seconds=[0.5, 1.5, 2.5], columns=3, width=160, labels=True
        )
    )
    assert seconds == [0.5, 1.5, 2.5]
    sheet = Image.open(BytesIO(data))
    assert sheet.format == "JPEG"
    assert sheet.width == 3 * 160


async def _make_video(dest) -> None:
    """3 秒のテスト用 mp4（色が変わっていくだけ）。"""
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:duration=3",
        "-pix_fmt", "yuv420p", str(dest),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
