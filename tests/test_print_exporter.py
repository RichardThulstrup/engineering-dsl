"""The toolkit's PDF hardcopy (edsl_pdf), printed for real: on A4, or on the paper it is set to.

Each export runs nbconvert in a process of its own, as hardcopy() does,
with the exporter named by its class (no installed entry point needed).
The tests skip where nbconvert, Playwright or its Chromium is missing.
"""
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import nbformat
import pytest
from nbformat.v4 import new_markdown_cell, new_notebook

from utils import hardcopy_helpers

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def export(tmp_path):
    """Print a small notebook to PDF with the given nbconvert options (and, as
    ``browsers``, a folder for Playwright to look for its browser in); gives the
    completed process, and each page's size in mm (as the PDF's /MediaBox says it)."""
    for needed in ("nbconvert", "playwright"):
        if importlib.util.find_spec(needed) is None:
            pytest.skip(f"{needed} is not installed")
    nbformat.write(new_notebook(cells=[new_markdown_cell("# A page\n\nOf a notebook.")]), str(tmp_path / "page.ipynb"))
    env = dict(os.environ, PYTHONPATH=str(ROOT), JUPYTER_CONFIG_DIR=str(tmp_path / "config"),
               JUPYTER_DATA_DIR=str(tmp_path / "data"), PYTHONDONTWRITEBYTECODE="1")

    def run(*options, browsers=None):
        more = {"PLAYWRIGHT_BROWSERS_PATH": str(browsers)} if browsers else {}
        done = subprocess.run([sys.executable, "-m", "nbconvert", "--to", "utils.print_exporter.EDSLPDFExporter",
                               *options, "page.ipynb"], cwd=str(tmp_path), env=dict(env, **more),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        if browsers is None and hardcopy_helpers._NO_BROWSER in done.stderr:
            pytest.skip("no Chromium for Playwright (run: python -m playwright install chromium)")
        pdf = (tmp_path / "page.pdf").read_bytes() if done.returncode == 0 else b""
        boxes = re.findall(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", pdf)
        return done, [(round(float(w) * 25.4 / 72), round(float(h) * 25.4 / 72)) for w, h in boxes]

    return run


def test_the_pdf_is_printed_on_a4_as_the_worksheets_pages_are(export):
    done, pages = export()
    assert done.returncode == 0, done.stderr[-2000:]
    assert pages == [(210, 297)]


def test_another_paper_is_a_setting(export):
    # (Its name is taken in capitals or not, and a paper that is none is said at once.)
    done, pages = export("--EDSLPDFExporter.paper=letter")
    assert done.returncode == 0 and pages == [(216, 279)]
    done, pages = export("--EDSLPDFExporter.paper=B9")
    assert done.returncode != 0 and "The 'paper' trait" in done.stderr and "'A4', 'A3', 'A5', 'Letter'" in done.stderr


def test_without_its_browser_it_says_so_in_the_words_hardcopy_knows(export, tmp_path):
    # (hardcopy() then says how to fetch the browser, and makes HTML instead.)
    done, pages = export(browsers=tmp_path / "no browsers here")
    assert done.returncode != 0 and hardcopy_helpers._NO_BROWSER in done.stderr and pages == []


def test_a_pdf_of_one_long_page_is_left_to_nbconvert(tmp_path):
    # (That has no paper; nbconvert's own printing makes it, as it did.)
    if importlib.util.find_spec("nbconvert") is None:
        pytest.skip("nbconvert is not installed")
    stand_in = ("from nbconvert.exporters.webpdf import WebPDFExporter\n"
                "WebPDFExporter.run_playwright = lambda self, html: 'printed by nbconvert: ' + html\n"
                "from utils.print_exporter import EDSLPDFExporter\n"
                "print(EDSLPDFExporter(paginate=False).run_playwright('the page'))\n")
    done = subprocess.run([sys.executable, "-c", stand_in], cwd=str(tmp_path), capture_output=True, text=True,
                          env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1"), timeout=120)
    assert done.stdout.strip() == "printed by nbconvert: the page", done.stderr[-2000:]
