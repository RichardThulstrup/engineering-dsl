"""hardcopy() when the browser that prints its PDFs is not there.

Playwright can be installed without its browser — never fetched, or
Playwright upgraded since.  The exports themselves are stood in for
here: these tests are about what hardcopy() does with their outcome.
"""
import importlib.util
import subprocess

import pytest

from utils import hardcopy_helpers

# What nbconvert's webpdf exporter ends with in that case.
NO_BROWSER = ("RuntimeError: No suitable chromium executable found on the system. Please use "
              "'--allow-chromium-download' to allow downloading one,or install it using `playwright install chromium`.")


@pytest.fixture
def exports(tmp_path, monkeypatch):
    """Call hardcopy() on a notebook with the given outcomes of its exports,
    in turn, as (exit code, stderr); returns (what it returned, the exporters asked for)."""
    notebook = tmp_path / "sheet.ipynb"
    notebook.write_text('{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}', encoding="utf-8")
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",                    # Playwright itself is installed
                        lambda name, *more: object() if name == "playwright" else real(name, *more))

    def run(outcomes):
        asked = []

        def export(command, **options):
            code, said = outcomes[len(asked)]
            asked.append(command[command.index("--to") + 1])
            return subprocess.CompletedProcess(command, code, stdout="", stderr=said.format(folder=tmp_path))

        monkeypatch.setattr(hardcopy_helpers.subprocess, "run", export)
        return hardcopy_helpers.hardcopy(str(notebook), to="webpdf"), asked

    run.folder = tmp_path
    return run


def test_without_the_browser_the_hardcopy_is_html_and_says_how_to_get_a_pdf(exports, capsys):
    written, asked = exports([(1, "Traceback (most recent call last):\n" + NO_BROWSER),
                              (0, "[NbConvertApp] Writing 9 bytes to {folder}/sheet.html")])
    assert asked == ["webpdf", "html"]
    assert written == f"{exports.folder}/sheet.html"
    said = capsys.readouterr().out
    assert "python -m playwright install chromium" in said and "Falling back to HTML" in said


def test_with_the_browser_the_hardcopy_is_the_pdf(exports, capsys):
    written, asked = exports([(0, "[NbConvertApp] Writing 9 bytes to {folder}/sheet.pdf")])
    assert asked == ["webpdf"] and written == f"{exports.folder}/sheet.pdf"
    assert "playwright" not in capsys.readouterr().out


def test_any_other_failure_of_the_export_is_reported_and_not_tried_again(exports):
    with pytest.raises(RuntimeError, match="the template is missing"):
        exports([(1, "ValueError: the template is missing"), (0, "never asked for")])
