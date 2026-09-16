"""The editor checks the real transformer without running notebook code."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "edsl_editor_checker", ROOT / "vscode-engineering-dsl/python/checker.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_equalities_equations_and_matrices():
    cells = ["from utils.Engineer import *", "symbols: x, y",
             "_m = Matrix([[x, 2], [3, y]])\n_m₀͵₁ = 5\n_mᵀ",
             "solve([x + y ≡ 3, x − y ≡ 1], [x, y])",
             "assert 2 ≟ 2\nassert 2 ﹦ 2\nassert 2 ＝ 2"]
    results = checker.check_notebook(cells)
    assert all(not item["diagnostics"] for item in results)
    assert "Eq(" in results[3]["python"]


def test_real_error_maps_to_original_line():
    source = "_ok = 5 kΩ\n_bad = (2 + )\n"
    issue, = checker.check_notebook([source])[0]["diagnostics"]
    assert issue["range"]["start"]["line"] == 1
    assert 0 <= issue["range"]["start"]["character"] <= len(source.splitlines()[1])


def test_transform_error_and_recovery():
    result = checker.check_notebook(["symbols: x, y", "x ≡ y ≡ 2", "x ≡ 2"])
    assert result[1]["diagnostics"]
    assert not result[2]["diagnostics"]


def test_cells_are_never_executed(tmp_path):
    marker = tmp_path / "must-not-exist"
    cells = [f"open({str(marker)!r}, 'w').write('executed')", "raise RuntimeError('do not run')"]
    assert all(not item["diagnostics"] for item in checker.check_notebook(cells))
    assert not marker.exists()


def test_modes_magics_and_top_level_await():
    cells = ["set_syntax_mode('legacy')", "_m := [[1,2],[3,4]]",
             "%%dsl python\n_list = [[1,2],[3,4]]", "%%python\n_native = 1 == 1",
             "%%html\n<b>≡ ≟</b>", "%matplotlib inline", "await something()"]
    results = checker.check_notebook(cells)
    assert all(not item["diagnostics"] for item in results)
    assert "_as_matrix" in results[1]["python"]
    assert "_as_matrix" not in results[2]["python"]


def test_utf16_columns():
    source = "# 😀\n_bad = )"
    error = SyntaxError("bad", ("<cell>", 2, 8, "_bad = )"))
    assert checker.error_range(source, source, error)["start"] == {"line": 1, "character": 7}
    assert checker._position("'😀' + )", 6)["character"] == 7


def test_notebook_state_does_not_leak():
    from utils import circuit_dsl as dsl
    checker.check_notebook(["symbols: x₀"])
    assert dsl._DECLARED_SUBSCRIPT_SYMBOLS
    checker.check_notebook(["_m = Matrix([[1,2],[3,4]])\n_m₀"])
    assert not dsl._DECLARED_SUBSCRIPT_SYMBOLS


@pytest.mark.parametrize("notebook", sorted(ROOT.glob("*.ipynb")), ids=lambda p: p.name)
def test_all_example_cells_are_accepted(notebook):
    content = json.loads(notebook.read_text(encoding="utf-8"))
    cells = ["".join(cell["source"]) for cell in content["cells"] if cell["cell_type"] == "code"]
    result = checker.check_notebook(cells)
    issues = [(i + 1, item["diagnostics"]) for i, item in enumerate(result) if item["diagnostics"]]
    assert not issues
