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
    # コマは [n/fps, (n+1)/fps) を占めるので、番号は切り捨てで出す
    assert contact_sheet.frame_label(1.51, 24) == "1.51s  #36"


def test_the_frame_number_survives_the_rounding_of_the_second():
    # plan_seconds は秒を小数第 3 位で丸めるので、n/fps ちょうどがわずかに
    # 手前に落ちる（30fps の #1054 = 35.13333… → 35.133）。それでも #1054。
    second = contact_sheet.plan_seconds(frames=[1054], fps=30)[0]
    assert second == 35.133
    assert contact_sheet.frame_index(second, 30) == 1054


def test_the_seek_aims_half_a_frame_before_the_wanted_frame():
    # ffmpeg の入力シークは「pts が指定秒以上の最初のコマ」を返すので、
    # 狙うコマの pts より半コマ手前を渡す（先頭は 0 で止める）。
    assert contact_sheet.seek_second(1.0, 24) == pytest.approx(23.5 / 24)
    assert contact_sheet.seek_second(0.0, 24) == 0.0
    # fps が読めなければ指定された秒をそのまま渡すしかない
    assert contact_sheet.seek_second(1.0, None) == 1.0


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


@has_ffmpeg
def test_the_wanted_frame_is_the_frame_that_comes_out(tmp_path):
    """``frames`` で頼んだ番号のコマが、そのまま抜けてくる（#52 の 1f ズレ）。

    フレームごとに明るさを変えた動画を焼き、抜いたコマの明るさから
    「何コマ目が出たか」を逆算して確かめる（OCR は要らない）。
    """
    video = tmp_path / "counted.mp4"
    levels = asyncio.run(_make_counted_video(video))
    duration, fps = asyncio.run(contact_sheet.probe_video(video))
    assert fps is not None and round(fps) == COUNTED_FPS

    for number in (0, 1, 17, 33, COUNTED_FRAMES - 1):
        second = contact_sheet.plan_seconds(frames=[number], fps=fps)[0]
        frame = asyncio.run(
            contact_sheet.extract_frame(video, second, tmp_path / f"{number}.png", fps)
        )
        assert _which_frame(frame, levels) == number
        assert contact_sheet.frame_label(second, fps) == f"{second:.2f}s  #{number}"


#: フレーム番号入りの動画（1 コマごとに明るさが GRAY_STEP ずつ上がるだけ）
COUNTED_FPS = 24
COUNTED_FRAMES = 48
GRAY_STEP = 5


def _which_frame(frame: Image.Image, levels: list[int]) -> int:
    """抜けたコマの明るさから「何コマ目か」を逆算する。"""
    gray = frame.convert("RGB").getpixel((frame.width // 2, frame.height // 2))[0]
    return min(range(len(levels)), key=lambda index: abs(levels[index] - gray))


async def _make_counted_video(dest) -> list[int]:
    """``n`` コマ目が灰色 ``n * GRAY_STEP`` の動画を焼き、**復号後の**明るさを返す。

    yuv420p を通ると明るさは 1 前後ずれるので、期待値は焼いた動画から読み直す
    （こうしておけば ffmpeg の版が変わっても判定が壊れない）。
    """
    source = dest.parent / "frames"
    source.mkdir()
    for number in range(COUNTED_FRAMES):
        level = number * GRAY_STEP
        Image.new("RGB", (160, 90), (level, level, level)).save(
            source / f"f{number:03d}.png"
        )
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-y",
        "-framerate", str(COUNTED_FPS), "-i", str(source / "f%03d.png"),
        "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(dest),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()

    decoded = dest.parent / "decoded"
    decoded.mkdir()
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-y", "-i", str(dest), str(decoded / "d%03d.png"),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    files = sorted(decoded.glob("d*.png"))
    assert len(files) == COUNTED_FRAMES
    levels = [
        Image.open(path).convert("RGB").getpixel((80, 45))[0] for path in files
    ]
    # 逆算できる程度に離れていること（隣り合うコマの差が十分にある）
    assert all(b - a >= 3 for a, b in zip(levels, levels[1:]))
    return levels


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
