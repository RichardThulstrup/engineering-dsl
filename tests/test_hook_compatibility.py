"""Fresh-process notebook, import-loader and installation regressions."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_process(script, cwd=ROOT):
    env = dict(os.environ, SYMBOL_PALETTE_EXE=os.devnull, PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, "-c", script], cwd=cwd, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "COMPAT_OK" in result.stdout


def test_notebook_modes_mpmath_and_native_numpy():
    run_process(r"""
from IPython.core.interactiveshell import InteractiveShell
shell = InteractiveShell.instance()
def cell(source):
    result = shell.run_cell(source)
    assert result.success, (source, result.error_before_exec, result.error_in_exec)
cell('from utils.Engineer import *')
count = len(shell.input_transformers_cleanup)
cell('eng.add_hook()')
assert len(shell.input_transformers_cleanup) == count
cell('from mpmath import mp\nmp.dps = 60\n_root = mp.sqrt(2)')
assert shell.user_ns['mp'].dps == 60
assert shell.user_ns['_root'] == shell.user_ns['mp'].sqrt(2)
cell('_voltage = 12.0 V\n_resistance := 4.0 Ω\n_current = _voltage / _resistance')
from utils.sigfig import _unwrap
current = _unwrap(shell.user_ns['_current'])
assert abs(current.value - 3.0) < 1e-12
assert current.dimensions == shell.user_ns['si'].A.dimensions
cell('%%python\nimport numpy as np\n_native = np.linalg.solve([[2,0],[0,2]], [4,6])\n_native_precision = 1.25')
assert shell.user_ns['_native'].tolist() == [2.,3.]
assert type(shell.user_ns['_native_precision']) is float
cell('%%dsl legacy\n_matrix := [[1,2],[3,4]]\n_equal := (2 + 2 = 4)')
assert shell.user_ns['_matrix'].shape == (2,2)
assert shell.user_ns['_equal'] is True
cell('_ordinary = [[1,2],[3,4]]')
assert isinstance(shell.user_ns['_ordinary'], list)
cell('set_syntax_mode("legacy")')
cell('_legacy_check := (2 + 2 = 4)')
assert shell.user_ns['_legacy_check'] is True
cell('set_syntax_mode("python")')
cell('_normal_assignment = 9')
cell('%precision 6')
print('COMPAT_OK')
""")


@pytest.mark.parametrize("module", ["zmq", "scipy.linalg", "pandas", "matplotlib.pyplot"])
def test_third_party_imports_keep_native_loaders(module):
    if importlib.util.find_spec(module.split('.')[0]) is None:
        pytest.skip(f"{module} is not installed")
    run_process("import utils.Engineer\nimport " + module + "\nprint('COMPAT_OK')")


def test_ordinary_and_opt_in_module_imports(tmp_path):
    (tmp_path / 'plain_helper.py').write_text('answer = 42\n', encoding='utf-8')
    (tmp_path / 'dsl_helper.py').write_text('from utils.circuit_dsl import *\nanswer := 42\n', encoding='utf-8')
    run_process(f"""
import sys
sys.path.insert(0, {str(tmp_path)!r})
import utils.Engineer as E
import plain_helper
assert plain_helper.answer == 42
assert type(plain_helper.answer) is int
before = list(sys.meta_path)
hook = E.eng.add_hook(modules=('dsl_helper',))
import dsl_helper
assert dsl_helper.answer == 42
hook.remove()
assert sys.meta_path == before
print('COMPAT_OK')
""")


def test_package_import_from_site_packages(tmp_path):
    installed = tmp_path / 'site-packages' / 'utils'
    installed.mkdir(parents=True)
    for path in (ROOT / 'utils').glob('*.py'):
        shutil.copy2(path, installed / path.name)
    run_process(f"""
import sys
sys.path.insert(0, {str(installed.parent)!r})
import utils.Engineer as E
assert E.eng.__file__.startswith({str(installed)!r})
assert E.m_p.sf == 12
assert float(E.c.value.value) == 299792458
import zmq
print('COMPAT_OK')
""", cwd=tmp_path)


def test_explicit_matrix_notebook_display_and_native_python():
    run_process(r"""
from IPython.core.interactiveshell import InteractiveShell
shell = InteractiveShell.instance()
def cell(source):
    result = shell.run_cell(source)
    assert result.success, (source, result.error_before_exec, result.error_in_exec)
cell('from utils.Engineer import *')
cell('_M = Matrix([[10,15],[255,256]])\n_tagged = _M ▸ hex')
matrix = shell.user_ns['_M']
tagged = shell.user_ns['_tagged']
assert matrix == tagged
assert not hasattr(matrix, '_dsl_radix')
latex = shell.display_formatter.formatters['text/latex'](tagged)
assert 'FF' in latex and '100' in latex and '₁₆' in latex, latex
assert 'displaystyle' not in latex
assert 'displaystyle' not in shell.display_formatter.formatters['text/latex'](matrix)
cell('%%python\n_native_flat = _M[1]\n_native_product = _M * _M')
assert shell.user_ns['_native_flat'] == 15
assert shell.user_ns['_native_product'] == matrix * matrix
cell('_M₀͵₁ := 42\n_item = _M₀͵₁')
assert matrix[0,1] == shell.user_ns['_item'] == 42
print('COMPAT_OK')
""")


def test_unicode_equality_in_notebook_cells():
    run_process(r"""
from IPython.core.interactiveshell import InteractiveShell
shell = InteractiveShell.instance()
def cell(source):
    result = shell.run_cell(source)
    assert result.success, (source, result.error_before_exec, result.error_in_exec)
cell('from utils.Engineer import *')
for glyph in ('﹦', '＝', '≟'):
    cell(f'_left = 220 Ω\n_result = _left {glyph} 220 Ω')
    assert shell.user_ns['_result'] is True
    cell(f'%%dsl legacy\n_legacy := (2 + 2 {glyph} 4)')
    assert shell.user_ns['_legacy'] is True
    cell(f'%%python\n_literal = "{glyph}"\n_normal = (2 + 2 == 4)')
    assert shell.user_ns['_literal'] == glyph
    assert shell.user_ns['_normal'] is True
print('COMPAT_OK')
""")


def test_symbolic_equations_in_notebook_cells():
    run_process(r"""
from IPython.core.interactiveshell import InteractiveShell
shell = InteractiveShell.instance()
def cell(source):
    result = shell.run_cell(source)
    assert result.success, (source, result.error_before_exec, result.error_in_exec)
cell('from utils.Engineer import *')
cell('symbols: x, y\n_equation = x² ≡ 4\n_roots = solve(_equation, x)')
assert shell.user_ns['_roots'] == [-2,2]
assert shell.user_ns['_equation'] == shell.user_ns['Eq'](shell.user_ns['x']**2,4)
cell('%%dsl legacy\n_legacy := solve([x + y ≡ 3, x − y ≡ 1], [x, y])')
assert shell.user_ns['_legacy'] == {shell.user_ns['x']:2, shell.user_ns['y']:1}
cell('%%python\n_literal = "x ≡ 2"\n_native = Eq(x,2)')
assert shell.user_ns['_literal'] == 'x ≡ 2'
cell('_native_roots = solve(_native, x)')
assert shell.user_ns['_native_roots'] == [2]
print('COMPAT_OK')
""")
