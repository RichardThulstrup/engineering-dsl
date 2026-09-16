"""Native Python semantics and explicit library conversion boundaries."""
import builtins
from decimal import Decimal
from fractions import Fraction
import operator
import warnings

from mpmath import mp
import numpy as np
import pytest

import utils.Engineer as E
from utils import circuit_dsl as dsl
from utils.sigfig import Sig


def run(source, mode="python", **values):
    ns = {key: getattr(E, key) for key in E.__all__}
    ns.update(values)
    exec(dsl.transform_source(source, syntax_mode=mode), ns)
    return ns


def test_assignment_comparison_and_symbolic_equation():
    ns = run('_a = 2\n_b := 3\n_r = _a + _b == 5')
    assert ns['_r'] is True
    assert run('symbols: x\n_r = solve(Eq(x, 2), x)')['_r'] == [2]
    assert run('_r := (2 + 2 = 4)', mode='legacy')['_r'] is True


@pytest.mark.parametrize('mode', ['python', 'legacy'])
def test_tuple_keys_read_and_write(mode):
    data = {(1, 2): 'pair', 1: {2: 'nested'}}
    ns = run('_before := _data[1,2]\n_data[1,2] := "changed"', mode=mode, _data=data)
    assert ns['_before'] == 'pair'
    assert data[(1,2)] == 'changed'
    assert data[1][2] == 'nested'


@pytest.mark.parametrize('value', [1.9, 1.0, -1.9, float('nan')])
def test_fractional_and_float_indices_rejected(value):
    with pytest.raises(TypeError):
        operator.index(Sig(value))
    with pytest.raises(TypeError):
        [10,20,30][Sig(value)]
    with pytest.raises(TypeError):
        range(Sig(value))


def test_native_integer_indices_and_slices():
    assert operator.index(Sig(np.int64(2))) == 2
    assert [1,2,3][Sig(1):Sig(3)] == [2,3]


def test_native_lists_and_explicit_legacy_matrices():
    assert isinstance(run('_r = [[1,2],[3,4]]')['_r'], list)
    matrix = run('_r := [[1,2],[3,4]]',mode='legacy')['_r']
    assert matrix.shape == (2,2)
    assert matrix.det() == -2


def test_mpmath_precision_and_context():
    with mp.workdps(70):
        assert mp.mpf(Sig(10**65+1)) == mp.mpf(10**65+1)
        value = Decimal('1.000000000000000000000000000000000000001')
        assert mp.mpf(Sig(value)) == mp.mpf(str(value))
        assert mp.mpf(Sig(Fraction(1,7))) == mp.mpf(1)/7
        assert mp.sqrt(Sig(2)) == mp.sqrt(2)
        assert mp.mpc(Sig(1+2j)) == mp.mpc(1,2)
        assert abs(mp.quad(lambda x:x**Sig(2),[Sig(0),Sig(1)])-mp.mpf(1)/3) < mp.mpf('1e-65')
        assert abs(mp.findroot(lambda x:x**Sig(2)-Sig(2),Sig(1))-mp.sqrt(2)) < mp.mpf('1e-65')
        other = mp.clone()
        other.dps = 25
        assert other.sqrt(Sig(2)) == other.sqrt(2)
        assert mp.dps == 70


def test_mpmath_rejects_units_and_intervals():
    for value in (2*E.mV, Sig(dsl.Range(1,2))):
        with pytest.raises(TypeError, match='dimensionless'):
            mp.mpf(value)


def test_proton_glyph_survives_mp_import():
    ns = run('from mpmath import mp\n_mass = mₚ\n_r = mp.sqrt(2)')
    assert ns['_mass'] is E.m_p
    assert ns['_r'] == mp.sqrt(2)


def test_explicit_numeric_conversion_for_linalg_and_units():
    result=np.linalg.solve(E.as_numeric([[Sig(2),0],[0,Sig(2)]]), E.as_numeric([Sig(4),Sig(6)]))
    np.testing.assert_allclose(result,[2,3])
    np.testing.assert_allclose(E.as_numeric([2*E.mV,3*E.mV],unit=E.mV),[2,3])
    assert E.as_numeric(2000*E.mV,unit=builtins.V) == 2
    assert E.as_numeric(Sig(1+2j),dtype=complex) == 1+2j
    for value, kwargs in [(2*E.mV,{}),(2*E.mV,{'unit':E.si.A}),([2*E.mV,3],{'unit':E.mV})]:
        with pytest.raises(TypeError):
            E.as_numeric(value,**kwargs)
    with pytest.raises(TypeError):
        E.as_numeric([1,2],dtype=object)


def test_numpy_copy_protocol():
    data=np.arange(3.)
    assert Sig(data).__array__(copy=False) is data
    assert not np.shares_memory(Sig(data).__array__(copy=True),data)
    with pytest.raises(ValueError):
        Sig(data).__array__(dtype=np.int64,copy=False)
    with warnings.catch_warnings():
        warnings.simplefilter('error', DeprecationWarning)
        with pytest.raises(ValueError):
            np.array(Sig(2),copy=False)


def test_source_scope_and_unknown_mode():
    assert dsl.transform_source('answer = 42',filename='C:/tmp/helper.py') == 'answer = 42'
    with pytest.raises(ValueError):
        dsl.transform_source('answer = 42',syntax_mode='unknown')
    assert dsl._wrap_matrix_literals('x = 1\n# comment\n') == 'x = 1\n# comment\n'


def test_protection_is_checked_before_legacy_container_rewrite():
    dsl.protect("reserved")
    with pytest.raises(SyntaxError, match="reserved"):
        dsl.transform_source("reserved := [[1,2],[3,4]]", syntax_mode="legacy")
    dsl.protect("mp")
    dsl.transform_source("from mpmath import mp", syntax_mode="python")
    with pytest.raises(SyntaxError, match="mp"):
        dsl.transform_source("from mpmath import mp", syntax_mode="legacy")


def test_legacy_list_unpacking_and_unchanged_source():
    ns = {}
    exec(dsl._wrap_matrix_literals("[_left, _right] = [1, 2]"), ns)
    assert ns["_right"] == 2
    assert run("[_left, _right] = [1, 2]")["_right"] == 2
    source = "text = '[brackets in string]'  # preserve formatting\n"
    assert dsl._wrap_matrix_literals(source) == source


def test_numeric_conversion_does_not_drop_symbolic_unit_metadata():
    for scalar in (2, 1+2j):
        value = Sig(scalar)
        value._stripped_unit = "V"
        with pytest.raises(TypeError):
            mp.convert(value)
        with pytest.raises(TypeError):
            E.as_numeric(value)
