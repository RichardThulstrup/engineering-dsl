"""What an install brings with it: the files placed beside the package and
the optional parts a new user asks for by name."""
from pathlib import Path
import re
import runpy
import struct
import tomllib

from utils import worksheet

ROOT = Path(__file__).resolve().parents[1]
EXTRAS = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]


def test_the_labextension_and_the_kernel_are_installed_with_the_package():
    placed = dict(runpy.run_path(str(ROOT / "setup.py"), run_name="setup")["data_files"]())
    assert placed["share/jupyter/kernels/engineering-dsl"] == ["utils/kernelspec/kernel.json"]
    assert ("jupyterlab-edsl-highlight/labextension/package.json"
            in placed["share/jupyter/labextensions/jupyterlab-edsl-highlight"])
    assert all((ROOT / source).is_file() for sources in placed.values() for source in sources)


def test_the_icon_file_holds_a_picture_for_every_size_windows_asks_for():
    # static/icon.ico is made by utils.worksheet.make_icon; a shortcut, the
    # page and the app window all show it.
    from utils.worksheet import make_icon

    data = make_icon.TARGET.read_bytes()
    assert struct.unpack_from("<HHH", data) == (0, 1, len(make_icon.SIZES))
    for index, size in enumerate(make_icon.SIZES):
        width, height, _, _, _, bits, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * index)
        picture = data[offset:offset + length]
        assert (width or 256, height or 256, bits) == (size, size, 32)
        assert picture[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", picture[16:24]) == (size, size)
    # The file is a directory and then the pictures, smallest first.
    packed = make_icon.pack({32: b"larger", 16: b"small"})
    assert struct.unpack_from("<HHH", packed) == (0, 1, 2) and packed.endswith(b"smalllarger")
    assert struct.unpack_from("<BBBBHHII", packed, 6) == (16, 16, 0, 0, 1, 32, 5, 38)


def test_the_all_extra_is_the_lab_worksheet_and_hardcopy_extras_together():
    assert set(EXTRAS["all"]) == set(EXTRAS["lab"]) | set(EXTRAS["worksheet"]) | set(EXTRAS["hardcopy"])


def test_the_worksheet_asks_for_what_its_extra_installs():
    # The packages it names when one is missing are the ones the extra brings.
    assert {re.match(r"[A-Za-z0-9_.-]+", requirement).group() for requirement in EXTRAS["worksheet"]} \
        == set(worksheet.NEEDS)
