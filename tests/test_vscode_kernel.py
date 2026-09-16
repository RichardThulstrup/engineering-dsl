"""Exercise the DSL-language kernel over the real Jupyter protocol."""
import json
import os
from pathlib import Path
import sys

import nbformat
from nbclient import NotebookClient
from jupyter_client import AsyncKernelManager

from utils.vscode_kernel import EngineeringDSLKernel, install

ROOT = Path(__file__).resolve().parents[1]


def test_registration_uses_selected_python(tmp_path):
    directory = Path(install(user=False, prefix=str(tmp_path)))
    spec = json.loads((directory / "kernel.json").read_text(encoding="utf-8"))
    assert spec["language"] == "engineering-dsl"
    assert spec["argv"][0] == sys.executable
    assert Path(spec["argv"][1]).is_file()
    assert EngineeringDSLKernel.language_info["name"] == "engineering-dsl"


def test_kernel_executes_equations_matrices_and_plots(tmp_path):
    sources = ["from utils.Engineer import *", """
symbols: x
assert solve(x² ≡ 4, x) == [-2, 2]
assert 2 ≟ 2
assert 2 ＝ 2
_M = Matrix([[1, 2], [3, 4]])
_M₀͵₁ := 9
assert _M[0, 1] == 9
assert _Mᵀ == Matrix([[1, 3], [9, 4]])
""", """
import matplotlib.pyplot as _plt
_plt.plot([1, 2], [3, 4])
_plt.show()
"""]
    notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(source) for source in sources])
    manager = AsyncKernelManager(kernel_name="python3")
    manager.kernel_spec.argv = [sys.executable, "-B", str(ROOT / "utils/vscode_kernel.py"),
                                "-f", "{connection_file}"]
    env = dict(os.environ, SYMBOL_PALETTE_EXE=os.devnull, PYTHONDONTWRITEBYTECODE="1",
               MPLBACKEND="module://matplotlib_inline.backend_inline",
               MPLCONFIGDIR=str(tmp_path / "matplotlib"), IPYTHONDIR=str(tmp_path / "ipython"))
    client = NotebookClient(notebook, km=manager, timeout=60)
    client.execute(cwd=str(ROOT), env=env, cleanup_kc=True)
    assert notebook.metadata.language_info.name == "engineering-dsl"
    assert any("image/png" in output.get("data", {}) for output in notebook.cells[-1].outputs)
