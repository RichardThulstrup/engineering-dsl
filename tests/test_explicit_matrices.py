"""Explicit matrices keep native SymPy semantics in default DSL cells."""
import pytest
from sympy import ImmutableMatrix, Matrix, MutableSparseMatrix, Rational, eye

import utils.Engineer as E
from utils import circuit_dsl as dsl


def run(source, **values):
    ns = {name: getattr(E, name) for name in E.__all__}
    ns.update(values)
    exec(dsl.transform_source(source, syntax_mode="python"), ns)
    return ns


def test_matrix_algebra_stays_exact():
    ns = run("""
_M = Matrix([[1, 2], [3, 4]])
_det = |_M|
_inverse = _M⁻¹
_square = _M²
_seventh = _M⁷
_transpose = _Mᵀ
_product = _M × _inverse
""")
    assert ns['_det'] == -2
    assert ns['_inverse'] == Matrix([[-2, 1], [Rational(3, 2), Rational(-1, 2)]])
    assert ns['_square'] == Matrix([[7, 10], [15, 22]])
    assert ns['_seventh'] == Matrix([[30853, 44966], [67449, 98302]])
    assert ns['_transpose'] == Matrix([[1, 3], [2, 4]])
    assert ns['_product'] == eye(2)


@pytest.mark.parametrize('constructor', [Matrix, ImmutableMatrix, MutableSparseMatrix])
def test_unicode_indices_match_native_matrix_keys(constructor):
    matrix = constructor([[10, 20, 30], [40, 50, 60]])
    ns = run("""
_first = _M₀͵₁
_last = _M₋₁͵₋₁
_flat = _M₁
_brackets = _M[1, 2]
_slice = _M[:, 1]
_row = _M.row(0)
""", _M=matrix)
    assert (ns['_first'], ns['_last'], ns['_flat'], ns['_brackets']) == (20, 60, 20, 60)
    assert ns['_slice'] == Matrix([20, 50])
    assert ns['_row'] == Matrix([[10, 20, 30]])
    with pytest.raises(IndexError):
        run('_bad = _M₂͵₀', _M=matrix)


@pytest.mark.parametrize('constructor', [Matrix, MutableSparseMatrix])
def test_unicode_assignment_mutates_original_matrix(constructor):
    matrix = constructor([[1, 2], [3, 4]])
    run("""
_M₀͵₁ := 99
_M₋₁͵₀ = 77
_M₀ := 42
_M[1, 1] = 88
""", _M=matrix)
    assert matrix == Matrix([[42, 99], [77, 88]])
    with pytest.raises(IndexError):
        run('_M₂͵₀ := 1', _M=matrix)
    with pytest.raises(TypeError):
        run('_M₀͵₁ := 1', _M=ImmutableMatrix([[1, 2], [3, 4]]))


def test_symbolic_outer_product_and_unit_grids():
    ns = run("""
symbols: alpha, beta, gamma, delta
_Jac = Matrix([[alpha, beta], [gamma, delta]])
_det = _Jac.det()
_row = Matrix([[alpha, beta]])
_col = Matrix([[gamma], [delta]])
_outer = _col × _row
_grid = [[1, 2], [3, 4]] mW
_plain = [[1, 2], [3, 4]]
_plain₀͵₁ := 9
""")
    a, b, c, d = (ns[name] for name in ('alpha', 'beta', 'gamma', 'delta'))
    assert ns['_det'] == a*d - b*c
    assert ns['_outer'] == Matrix([[a*c, b*c], [a*d, b*d]])
    assert E.as_numeric(ns['_grid'], unit=E.mW).tolist() == [[1, 2], [3, 4]]
    assert isinstance(ns['_plain'], list)
    assert ns['_plain'][0][1] == 9
