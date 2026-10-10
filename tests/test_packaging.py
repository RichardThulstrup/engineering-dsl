"""What an install brings with it: the files placed beside the package and
the optional parts a new user asks for by name."""
from pathlib import Path
import re
import runpy
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


def test_the_all_extra_is_the_lab_worksheet_and_hardcopy_extras_together():
    assert set(EXTRAS["all"]) == set(EXTRAS["lab"]) | set(EXTRAS["worksheet"]) | set(EXTRAS["hardcopy"])


def test_the_worksheet_asks_for_what_its_extra_installs():
    # The packages it names when one is missing are the ones the extra brings.
    assert {re.match(r"[A-Za-z0-9_.-]+", requirement).group() for requirement in EXTRAS["worksheet"]} \
        == set(worksheet.NEEDS)
