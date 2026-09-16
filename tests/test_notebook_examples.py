"""Run every example from a fresh Jupyter kernel, with offline currency rates.

    python -m pytest tests/test_notebook_examples.py -q

Notebooks are read without changing their saved outputs. Each kernel uses the
same Python executable as pytest. Optional palette launch and external rate
refresh are disabled; plots and rich notebook display still execute normally.
"""
import os
from pathlib import Path
import sys

import nbformat
from nbclient import NotebookClient
from jupyter_client import AsyncKernelManager
import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = sorted(ROOT.glob("*.ipynb"))


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda path: path.name)
def test_example_notebook(path, tmp_path):
    notebook = nbformat.read(path, as_version=4)
    notebook.cells.insert(0, nbformat.v4.new_code_cell("""
import time as _test_time
import utils.currencies as _test_currencies
_test_currencies._rates_timestamp = _test_time.time()
"""))
    env = dict(os.environ, SYMBOL_PALETTE_EXE=os.devnull,
               PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               MPLBACKEND="module://matplotlib_inline.backend_inline",
               MPLCONFIGDIR=str(tmp_path / "matplotlib"),
               IPYTHONDIR=str(tmp_path / "ipython"))
    manager = AsyncKernelManager(kernel_name="python3")
    manager.kernel_spec.argv = [sys.executable, "-B", "-m", "ipykernel_launcher",
                                "-f", "{connection_file}"]
    client = NotebookClient(notebook, km=manager, timeout=120,
                            resources={"metadata": {"path": str(ROOT)}})
    # Shut down the supplied manager even if a notebook fails midway.
    client.execute(env=env, cwd=str(ROOT), cleanup_kc=True)
    errors = [(index, output) for index, cell in enumerate(notebook.cells)
              for output in cell.get("outputs", [])
              if output.output_type == "error"]
    assert not errors, (path.name, errors)
