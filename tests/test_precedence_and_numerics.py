# -*- coding: utf-8 -*-
"""Regression tests for a batch of notation/semantics fixes (Sept 2026).

  * ``100 Ω ± 5%`` is a RELATIVE tolerance (±5 Ω), not ±0.05 Ω.
  * ``≈`` has comparison precedence: ``1 + 1 ≈ 2`` is ``approx(1+1, 2)``.
  * Trig / hyperbolic / ``exp`` are numeric on numbers (sf-preserving),
    symbolic on symbols, and reject dimensioned arguments.
  * ``M**2`` / ``M²`` work on a ``[[…]]`` matrix literal.
  * ``|M|`` is the determinant (norm for a vector), never the element count.
  * ``∠`` refuses a unit-carrying magnitude instead of dropping the unit.
  * ``h`` / ``min`` / ``in`` are hour / minute / inch in unit position only.
  * ``rad``, ``sr`` and ``hr`` exist.

Runs the real ``transform_source`` pipeline against the full toolkit —
invoke via ``pytest tests/`` or ``python -m pytest tests/test_precedence_and_numerics.py``.
"""
import os
import sys
import warnings

import pytest

os.environ.setdefault("SYMBOL_PALETTE_EXE", os.devnull)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.Engineer as E                                   # noqa: E402
from utils.circuit_dsl import transform_source, Range        # noqa: E402
from utils.sigfig import Sig, _unwrap                        # noqa: E402

NS = {k: getattr(E, k) for k in E.__all__}

# Importing ``Engineer`` switches on identifier protection process-wide.
# Nothing here needs it, and leaving it on makes sibling test modules
# (which use plain ``from utils.circuit_dsl import *`` and assign to
# ``c``, ``m`` …) fail when collected in the same pytest run.
E.clear_protections()


def run(src):
    """Transform + exec ``src``; return the value bound to ``_r``."""
    ns = dict(NS)
    exec(transform_source(src), ns)
    return ns["_r"]


def rng(x):
    """Unwrap a ``Sig(Range)`` to its bare ``Range``."""
    r = _unwrap(x)
    assert isinstance(r, Range), repr(x)
    return r


def f(x):
    """SI-magnitude float of a Sig / Physical / number."""
    v = _unwrap(x)
    return float(v.value) if hasattr(v, "value") and hasattr(v, "dimensions") else float(v)


# --------------------------------------------------------------------------
# ± with a percentage
# --------------------------------------------------------------------------
def test_plusminus_percent_is_relative():
    r = rng(run("_r := 100 Ω ± 5%"))
    assert f(r.low) == pytest.approx(95.0)
    assert f(r.high) == pytest.approx(105.0)


def test_plusminus_percent_prefixed_unit():
    r = rng(run("_r := 4.7 kΩ ± 1%"))
    assert f(r.low) == pytest.approx(4653.0)
    assert f(r.high) == pytest.approx(4747.0)


def test_plusminus_permille_is_relative():
    r = rng(run("_r := 100 ± 5‰"))
    assert (f(r.low), f(r.high)) == pytest.approx((99.5, 100.5))


def test_plusminus_absolute_unchanged():
    r = rng(run("_r := 100 Ω ± 5 Ω"))
    assert (f(r.low), f(r.high)) == pytest.approx((95.0, 105.0))
    r = rng(run("_r := 100 ± 5"))
    assert (f(r.low), f(r.high)) == (95, 105)


def test_percent_tag_is_consumed_by_arithmetic():
    assert f(run("_r := 1 + 25%")) == pytest.approx(1.25)
    assert f(run("_r := 120 V · (1 + 10%)")) == pytest.approx(132.0)
    assert f(run("_r := 25% · 200")) == pytest.approx(50.0)
    # The tag must not survive a sum: ``(1 + 25%)`` is a plain ratio…
    r = rng(run("_r := 100 ± (1 + 25%)"))          # …so this is ABSOLUTE ±1.25
    assert (f(r.low), f(r.high)) == pytest.approx((98.75, 101.25))


# --------------------------------------------------------------------------
# ≈ precedence
# --------------------------------------------------------------------------
@pytest.mark.parametrize("src, expected", [
    ("1 + 1 ≈ 2", True),
    ("2·3 ≈ 6", True),
    ("2·3 ≈ 7", False),
    ("0.1 + 0.2 ≈ 0.3", True),
    ("5.0 V ≈ 5000 mV", True),
    ("√2 · √2 ≈ 2", True),
    ("1 ≈ 1 ≈ 1", True),                  # chained, left-assoc
    ("(1 + 1) ≈ 2", True),
    ("not 1 + 1 ≈ 3", True),              # keyword is a boundary
    ("1 + 1 ≈ 2 and 2 + 2 ≈ 4", True),
    ("[v ≈ 3 for v in [1, 3]]", [False, True]),
])
def test_approx_precedence(src, expected):
    assert run(f"_r := {src}") == expected


def test_approx_in_if_and_assignment():
    ns = dict(NS)
    exec(transform_source("ok := 2 + 2 ≈ 4\nif 2 + 2 ≈ 4:\n    _r := ok\nelse:\n    _r := None"), ns)
    assert ns["_r"] is True


def test_approx_rewrite_text():
    assert transform_source("print(1 + 1 ≈ 2)").strip().endswith(
        "print(approx(_S(1, _INF) + _S(1, _INF), _S(2, _INF)))")


# --------------------------------------------------------------------------
# Trig / exp numerics
# --------------------------------------------------------------------------
def test_trig_numeric_on_sig():
    import math
    for name, fn in [("sin", math.sin), ("cos", math.cos), ("tan", math.tan),
                     ("sinh", math.sinh), ("cosh", math.cosh), ("tanh", math.tanh),
                     ("exp", math.exp), ("atan", math.atan),
                     ("asinh", math.asinh)]:
        v = run(f"_r := {name}(1.0)")
        assert isinstance(v, Sig), name
        assert float(v) == pytest.approx(fn(1.0)), name
        assert v.sf == 2, name          # ``1.0`` has two sig figs


def test_exp_of_one_is_a_number_not_E():
    assert "E" not in str(run("_r := exp(1.0)"))
    assert str(run("_r := exp(1.0)")) == "2.7"


def test_trig_preserves_sf_through_product():
    assert str(run("_r := sin(0.500) · 2.000000")) == "0.959"
    assert run("_r := sigfigs_of(sin(0.500))") == 3


def test_trig_exact_angles_stay_exact():
    import sympy
    assert run("_r := sin(30°)") == sympy.Rational(1, 2)
    assert run("_r := tan(45°)") == 1
    assert run("_r := sin(π)") == 0


def test_trig_measured_angle_is_numeric():
    v = run("_r := cos(60.0°)")
    assert isinstance(v, Sig) and float(v) == pytest.approx(0.5)
    assert str(v) == "0.500"                     # 3 sf from ``60.0``
    assert str(run("_r := sin(30.0°) · 10.0 V")) == "5.00 V"


def test_trig_symbolic_stays_symbolic():
    import sympy
    ns = dict(NS)
    exec(transform_source("symbols: x\n_r := diff(sin(x), x)"), ns)
    assert ns["_r"] == sympy.cos(sympy.Symbol("x"))


def test_trig_rejects_dimensioned_argument():
    with pytest.raises(TypeError, match="dimensionless"):
        run("_r := sin(2 V)")


def test_atan2_unit_aware():
    import math
    assert float(run("_r := atan2(1.0 m, 2.0 m)")) == pytest.approx(math.atan2(1, 2))
    assert float(run("_r := atan2(1.0 mm, 2.0 m)")) == pytest.approx(math.atan2(0.001, 2))
    with pytest.raises(TypeError):
        run("_r := atan2(1.0 m, 2.0 s)")


# --------------------------------------------------------------------------
# Matrices
# --------------------------------------------------------------------------
def test_matrix_power():
    import sympy
    assert run("M := [[1, 2], [3, 4]]\n_r := M**2") == sympy.Matrix([[7, 10], [15, 22]])
    assert run("M := [[1, 2], [3, 4]]\n_r := M²") == sympy.Matrix([[7, 10], [15, 22]])
    assert run("M := [[1, 2], [3, 4]]\n_r := M⁻¹") == sympy.Matrix([[-2, 1], [sympy.Rational(3, 2), sympy.Rational(-1, 2)]])


def test_matrix_indexing_still_works_after_getitem_removal():
    assert run("M := [[1, 2], [3, 4]]\n_r := M₀͵₁") == 2
    assert run("M := [[1, 2], [3, 4]]\n_r := M₁͵₀") == 3
    assert run("M := [[1, 2], [3, 4]]\nM₁͵₀ := 9\n_r := M₁͵₀") == 9
    assert list(run("M := [[1, 2], [3, 4]]\n_r := M₁")) == [3, 4]


def test_abs_bars_matrix_is_determinant():
    assert run("M := [[1, 2], [3, 4]]\n_r := |M|") == -2
    assert run("v := [[3], [4]]\n_r := |v|") == 5
    # Non-matrix behaviour is unchanged.
    assert run("_r := |-5|") == 5
    assert run("_r := |{1, 2, 3}|") == 3
    assert run("_r := |[1, 2]|") == 2


# --------------------------------------------------------------------------
# Phasor unit guard
# --------------------------------------------------------------------------
def test_phasor_rejects_unit_magnitude():
    with pytest.raises(TypeError, match="unit"):
        run("_r := 5.0 Ω ∠ 30°")


def test_phasor_unitless_still_works():
    z = complex(run("_r := 5.0 ∠ 30°"))
    assert abs(z) == pytest.approx(5.0)


# --------------------------------------------------------------------------
# Time / angle unit names
# --------------------------------------------------------------------------
def test_unit_position_rule_h_min_in():
    """``h``, ``min`` and ``in`` are hour, minute and inch in unit
    position, and Planck's constant / the builtin / the keyword elsewhere."""
    # hour
    assert f(run("_r := 90 km/h")) == pytest.approx(25.0)
    assert f(run("_r := 3 h")) == pytest.approx(10800.0)
    assert str(run("_r := 3 h")) == "3 h"                    # displays as typed
    assert f(run("_r := 5 kW·h")) == pytest.approx(1.8e7)
    assert str(run("_r := 60 minute ▸ h")) == "1 h"
    assert f(run("ν := 5e14 Hz\n_r := h · ν")) == pytest.approx(6.62607015e-34 * 5e14)
    assert f(run("_r := (3 eV)/h")) == pytest.approx(3 * 1.602176634e-19 / 6.62607015e-34)
    # minute
    assert f(run("_r := 20 L/min")) == pytest.approx(20e-3 / 60)
    assert f(run("_r := 5 min")) == pytest.approx(300.0)
    assert run("_r := min(3, 1)") == 1
    assert run("_r := min([4, 2])") == 2
    # inch
    assert f(run("_r := 5 in")) == pytest.approx(0.127)
    assert str(run("_r := 5 in ▸ mm")) == "127 mm"
    assert f(run("_r := lbf/in²")) == pytest.approx(6894.757293168)
    assert f(run("_r := 2 in + 1 ft")) == pytest.approx(0.3556)
    assert [f(v) for v in run("_r := [2 in, 3 in]")] == pytest.approx([0.0508, 0.0762])
    assert f(run("def g(a, b=5 in): return a + b\n_r := g(1 in)")) == pytest.approx(0.1524)
    # membership and the for-keyword are untouched
    assert run("R := 100 Ω ± 5 Ω\n_r := 102 Ω in R") is True
    assert run("_r := 3 in [1, 2, 3]") is True
    assert run("x := 3\n_r := x in [3]") is True
    assert run("_r := [k in [1, 2] for k in 1..3]") == [True, True, False]


def test_hr_alias():
    assert f(run("_r := 36.0 km/hr")) == pytest.approx(10.0)
    assert f(run("_r := hr")) == 3600.0


def test_rad_sr_exist_and_are_one():
    assert float(run("_r := 1.5 rad")) == pytest.approx(1.5)
    assert float(run("_r := 2.0 sr")) == pytest.approx(2.0)
    assert float(run("_r := sin(1.0 rad)")) == pytest.approx(0.8414709848)
