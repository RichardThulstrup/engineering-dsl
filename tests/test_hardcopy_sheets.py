"""Hardcopies of a sheet made in the worksheet.

The worksheet shows the value of every definition and saves that as the
cell's output.  Run as a notebook, the same cells show nothing for a
definition — so the tools that run a notebook fresh before exporting it
must leave a sheet as it was saved.  The exports themselves are stood
in for here: these tests are about what gets run and what gets exported.
"""
import argparse
import importlib.util
import subprocess
from pathlib import Path

import nbformat
import pytest
from nbformat.v4 import new_code_cell, new_notebook

from utils import hardcopy_helpers
from utils.worksheet import document

REPO = Path(__file__).resolve().parent.parent


def sheet(folder, shown=("x = 5",)):
    """A sheet as the worksheet saves it: a definition, and the value it showed."""
    path = folder / "sheet.ipynb"
    document.save(path, {"regions": [{
        "id": "r1", "kind": "math", "source": "x := 2 + 3", "x": 40, "y": 40,
        "outputs": [{"output_type": "display_data", "data": {"text/plain": text}} for text in shown]}]})
    return path


def notebook(folder):
    """The same calculation as an ordinary notebook, never run."""
    path = folder / "notebook.ipynb"
    nbformat.write(new_notebook(cells=[new_code_cell("from utils.Engineer import *"),
                                       new_code_cell("x := 2 + 3")]), str(path))
    return path


def test_a_file_saved_by_the_worksheet_is_recognised_and_an_ordinary_notebook_is_not(tmp_path):
    assert hardcopy_helpers.made_in_worksheet(sheet(tmp_path))
    assert not hardcopy_helpers.made_in_worksheet(notebook(tmp_path))
    assert not hardcopy_helpers.made_in_worksheet(tmp_path / "no such file.ipynb")


@pytest.fixture
def commands(monkeypatch):
    """The commands hardcopy() runs, none of them really run."""
    ran = []

    def export(command, **options):
        ran.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(hardcopy_helpers.subprocess, "run", export)
    return ran


def test_hardcopy_exports_a_sheet_as_saved_even_when_a_fresh_run_is_asked_for(tmp_path, commands, capsys):
    path = sheet(tmp_path)
    hardcopy_helpers.hardcopy(str(path), to="html", execute=True)
    assert len(commands) == 1 and "--execute" not in commands[0]
    assert commands[0][-1] == str(path)                             # the sheet itself is what is exported
    assert "exported as saved" in capsys.readouterr().out


def test_hardcopy_still_runs_an_ordinary_notebook_fresh_when_asked(tmp_path, commands, capsys):
    path = notebook(tmp_path)
    hardcopy_helpers.hardcopy(str(path), to="html", execute=True)
    assert "--execute" in commands[0] and commands[0][-1] == str(path)
    assert commands[1][-1].endswith(".exec.ipynb")                  # the fresh run is what is exported
    assert "worksheet" not in capsys.readouterr().out


def test_a_sheet_without_results_is_sent_to_the_worksheet_and_a_notebook_to_a_fresh_run(tmp_path, commands, capsys):
    hardcopy_helpers.hardcopy(str(sheet(tmp_path, shown=())), to="html")
    said = capsys.readouterr().out
    assert "in the worksheet" in said and "execute=True" not in said

    hardcopy_helpers.hardcopy(str(notebook(tmp_path)), to="html")
    assert "hardcopy(execute=True)" in capsys.readouterr().out


@pytest.fixture
def batch(tmp_path, monkeypatch):
    """The batch tool, hardcopy.py: its exports recorded in ``calls`` and not
    run, and the copy it runs kept out of the repository."""
    spec = importlib.util.spec_from_file_location("hardcopy_batch", REPO / "hardcopy.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    tool.calls = []
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tool, "nbconvert", lambda arguments, **more: tool.calls.append(arguments))
    tool.options = argparse.Namespace(execute=True, svg=False, formats=["html"], exporter_map={},
                                      out=tmp_path / "out", no_input=False, md_math="keep", qpdf=False, timeout=300)
    return tool


def test_the_batch_tool_exports_a_sheet_as_saved(tmp_path, batch, capsys):
    path = sheet(tmp_path)
    assert batch.build_notebook(path, batch.options, None) == []
    assert [call[-1] for call in batch.calls] == [str(path)]        # one export, of the sheet itself
    assert "--execute" not in batch.calls[0]
    assert "exported as saved" in capsys.readouterr().out


def test_the_batch_tool_still_runs_an_ordinary_notebook(tmp_path, batch, capsys):
    path = notebook(tmp_path)
    assert batch.build_notebook(path, batch.options, None) == []
    run, export = batch.calls
    assert "--execute" in run and run[-1] == export[-1] == str(tmp_path / "_hardcopy_notebook.ipynb")
    assert "worksheet" not in capsys.readouterr().out
