# -*- coding: utf-8 -*-
"""Regression tests for the "mathematical gaps" batch (Sept 2026).

  * ``round``/``math.floor``/``ceil``/``trunc``/``divmod`` on ``Sig``.
  * ``∛`` ``∜`` ``∥`` ``∆`` ``∧`` ``∨`` ``¬`` ``∫`` ``∂`` glyphs.
  * ``sin⁻¹(x)`` is the inverse function.
  * Math-style ``=`` is symbolic-aware: ``solve(x² = 4, x)`` works.
  * ``‖v‖`` is the norm; ``norm``/``dot``/``cross``/``hypot`` are unit-aware.
  * ``nan``, ``gcd``, ``lcm``, ``comb``, ``perm``, ``sign``, ``clamp``, ``cbrt``.
  * ``Range``: ``abs``, ``in``, ``width``, ``b ** R``, transcendental functions.
  * ``nsolve``, ``lambdify``, ``latex``, ``det``, ``eye`` … at top level.
  * ``symbols: R₁`` binds a symbol that later ``R₁`` references resolve to.
"""
import math
import os
import sys

import pytest

os.environ.setdefault("SYMBOL_PALETTE_EXE", os.devnull)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.Engineer as E                                   # noqa: E402
from utils.circuit_dsl import transform_source, Range        # noqa: E402
from utils.sigfig import Sig, _unwrap                        # noqa: E402

NS = {k: getattr(E, k) for k in E.__all__}
E.clear_protections()          # see test_precedence_and_numerics.py


def run(src):
    ns = dict(NS)
    exec(transform_source(src), ns)
    return ns["_r"]


def f(x):
    v = _unwrap(x)
    return float(v.value) if hasattr(v, "value") and hasattr(v, "dimensions") else float(v)


def rng(x):
    r = _unwrap(x)
    assert isinstance(r, Range), repr(x)
    return f(r.low), f(r.high)


# --------------------------------------------------------------------------
# Rounding protocol
# --------------------------------------------------------------------------
def test_round_on_sig_keeps_unit_and_caps_sf():
    v = run("_r := round(1.234567 V, 2)")
    assert str(v) == "1.23 V"
    assert v.sf == 3
    assert str(run("_r := round(1.234567, 3)")) == "1.235"
    assert str(run("_r := round(1234.5678, -2)")) == "1200"
    assert run("_r := round(2.5)") == 2


def test_floor_ceil_trunc_divmod():
    assert run("_r := math.floor(2.7)") == 2
    assert run("_r := math.ceil(2.2)") == 3
    assert run("_r := math.trunc(-2.7)") == -2
    q, r = run("_r := divmod(7.0, 2)")
    assert (float(q), float(r)) == (3.0, 1.0)


# --------------------------------------------------------------------------
# Glyphs
# --------------------------------------------------------------------------
def test_root_glyphs():
    assert float(run("_r := ∛27")) == pytest.approx(3.0)
    assert float(run("_r := ∜16")) == pytest.approx(2.0)
    assert str(run("_r := ∛(8.0 m³)")) == "2.0 m"


def test_inverse_function_superscript():
    assert float(run("_r := sin⁻¹(0.5)")) == pytest.approx(math.asin(0.5))
    assert float(run("_r := tan⁻¹(1.0)")) == pytest.approx(math.atan(1.0))
    assert float(run("_r := sinh⁻¹(1.0)")) == pytest.approx(math.asinh(1.0))


def test_logic_glyphs():
    assert run("_r := True ∧ False") is False
    assert run("_r := True ∨ False") is True
    assert run("_r := ¬False") is True
    assert run("_r := 3 > 2 ∧ 2 > 1") is True


def test_calculus_glyphs():
    import sympy
    x = sympy.Symbol("x")
    assert run("symbols: x\n_r := ∫(x², x)") == x**3 / 3
    assert run("symbols: x\n_r := ∫(x², (x, 0, 1))") == sympy.Rational(1, 3)
    assert run("symbols: x\n_r := ∂(x³, x)") == 3 * x**2


def test_parallel_to_glyph_alias():
    assert float(run("_r := 100 Ω ∥ 100 Ω")) == pytest.approx(50.0)


# --------------------------------------------------------------------------
# Math-style ``=``
# --------------------------------------------------------------------------
def test_equals_is_symbolic_aware():
    import sympy
    assert run("symbols: x\n_r := solve(x² = 4, x)") == [-2, 2]
    assert isinstance(run("symbols: x\n_r := (x² = 4)"), sympy.Equality)
    assert run("symbols: x, y\n_r := solve([x + y = 3, x - y = 1], [x, y])") == \
        {sympy.Symbol("x"): 2, sympy.Symbol("y"): 1}


def test_equals_plain_values_still_bool():
    assert run("_r := (2 + 2 = 4)") is True
    assert run("_r := (5 V = 5000 mV)") is True
    assert run("_r := (1 = 2)") is False
    ns = dict(NS)
    exec(transform_source("if 2 + 2 = 4:\n    _r := 'yes'\nelse:\n    _r := 'no'"), ns)
    assert ns["_r"] == "yes"


def test_equals_kwargs_untouched():
    assert run("def g(a, b=2): return a + b\n_r := g(1, b=5)") == 6


# --------------------------------------------------------------------------
# Vectors
# --------------------------------------------------------------------------
def test_norm_bars_and_helpers():
    assert str(run("u := [3.0, 4.0] N\n_r := ‖u‖")) == "5.0 N"
    assert float(run("_r := ‖[[3], [4]]‖")) == 5
    assert float(run("_r := ‖-5‖")) == 5
    assert str(run("_r := dot([1.0, 2.0, 3.0] N, [4.0, 5.0, 6.0] m)")) == "32. J"      # 2 sf, trailing dot marks the significant zero
    c = run("_r := cross([1.0, 0.0, 0.0] m, [0.0, 2.0, 0.0] N)")
    assert [f(v) for v in c] == pytest.approx([0.0, 0.0, 2.0])
    assert list(run("_r := cross([1, 2, 3], [4, 5, 6])")) == [-3, 6, -3]


def test_norm_bars_do_not_break_parallel():
    assert float(run("_r := 1 ‖ 2 ‖ 3")) == pytest.approx(6 / 11)
    assert float(run("_r := 2.0 ‖ (‖[3.0, 4.0]‖)")) == pytest.approx(2 * 5 / 7)


def test_hypot_unit_aware():
    assert str(run("_r := hypot(3.0 m, 4.0 m)")) == "5.0 m"
    assert str(run("_r := hypot(1.0 m, 2.0 m, 2.0 m)")) == "3.0 m"
    assert float(run("_r := hypot(3.0, 4.0)")) == 5.0


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def test_small_helpers():
    assert str(run("_r := cbrt(8.0 m³)")) == "2.0 m"
    assert float(run("_r := cbrt(-27.0)")) == pytest.approx(-3.0)
    assert run("_r := sign(-3.0 V)") == -1
    assert run("_r := sign(0)") == 0
    assert run("_r := clamp(5, 0, 3)") == 3
    assert str(run("_r := clamp(2.0 V, 0 V, 3 V)")) == "2.0 V"
    assert run("_r := (gcd(12, 18), lcm(4, 6), comb(5, 2), perm(5, 2))") == (6, 12, 10, 20)
    assert math.isnan(run("_r := nan"))


def test_numpy_ufunc_dispatch_extended():
    import numpy as np
    a = np.array([Sig(1.0, 2)])
    assert float(np.arcsinh(a)[0]) == pytest.approx(math.asinh(1.0))
    assert float(np.degrees(np.array([Sig(math.pi, 6)]))[0]) == pytest.approx(180.0)
    assert float(np.floor(np.array([Sig(2.7, 2)]))[0]) == 2


# --------------------------------------------------------------------------
# Range
# --------------------------------------------------------------------------
def test_range_membership_abs_width_rpow():
    assert run("R := 100 Ω ± 5 Ω\n_r := 102 Ω in R") is True
    assert run("R := 100 Ω ± 5 Ω\n_r := 110 Ω in R") is False
    assert run("R := 100 Ω ± 5 Ω\n_r := (98 Ω ± 1 Ω) in R") is True
    assert f(run("R := 100 Ω ± 5 Ω\n_r := R.width")) == pytest.approx(10.0)
    assert rng(run("_r := |-3 ± 5|")) == (0, 8)
    assert rng(run("_r := |(-7 ‥ -2)|")) == (2, 7)
    assert rng(run("_r := abs(-3 Ω ± 5 Ω)")) == (0.0, 8.0)
    assert rng(run("_r := 2 ** (2 ± 0.5)")) == pytest.approx((2 ** 1.5, 2 ** 2.5))
    assert rng(run("_r := 0.5 ** (2 ± 0.5)")) == pytest.approx((0.5 ** 2.5, 0.5 ** 1.5))


def test_range_transcendental_functions():
    lo, hi = rng(run("θ := 0.5 ± 0.1\n_r := sin(θ)"))
    assert (lo, hi) == pytest.approx((math.sin(0.4), math.sin(0.6)))
    # π/2 lies inside (1.3, 1.7): the max is exactly 1
    lo, hi = rng(run("θ := 1.5 ± 0.2\n_r := sin(θ)"))
    assert hi == 1.0 and lo == pytest.approx(min(math.sin(1.3), math.sin(1.7)))
    # π lies inside (2.5, 3.5): the min is exactly -1
    lo, hi = rng(run("θ := 3.0 ± 0.5\n_r := cos(θ)"))
    assert lo == -1.0
    lo, hi = rng(run("θ := 0.0 ± 1.0\n_r := cosh(θ)"))
    assert lo == 1.0 and hi == pytest.approx(math.cosh(1.0))
    assert rng(run("θ := 0.5 ± 0.1\n_r := exp(θ)")) == pytest.approx((math.exp(0.4), math.exp(0.6)))
    assert rng(run("θ := 0.5 ± 0.1\n_r := ln(θ)")) == pytest.approx((math.log(0.4), math.log(0.6)))
    assert rng(run("θ := 0.5 ± 0.1\n_r := √θ")) == pytest.approx((math.sqrt(0.4), math.sqrt(0.6)))
    with pytest.raises(ValueError, match="pole"):
        run("θ := 1.5 ± 0.2\n_r := tan(θ)")


# --------------------------------------------------------------------------
# Symbolic re-exports and subscripted symbols
# --------------------------------------------------------------------------
def test_symbolic_reexports():
    import sympy
    assert float(run("symbols: x\n_r := nsolve(cos(x) - x, x, 1)")) == pytest.approx(0.7390851332)
    assert run("symbols: x\n_r := lambdify(x, x²)(3)") == 9
    assert run("symbols: x\n_r := latex(x² + 1)") == "x^{2} + 1"
    assert run("_r := det(Matrix([[1, 2], [3, 4]]))") == -2
    assert run("_r := eye(2)") == sympy.eye(2)


def test_declared_subscript_symbols():
    import sympy
    R1, R2 = sympy.symbols("R_1 R_2")
    assert run("symbols: R₁, R₂\n_r := R₁ + R₂") == R1 + R2
    assert run("symbols: R₁, R₂\n_r := solve(R₁ + R₂ = 10, R₁)") == [10 - R2]
    assert run("symbols: R₁, R₂\n_r := R_1 + R_2") == R1 + R2     # ASCII spelling still works
    # Ordinary subscripts keep indexing.
    assert run("xs := [10, 20, 30]\n_r := xs₁") == 20
    assert run("symbols: R₁\nM := [[1, 2], [3, 4]]\n_r := M₀͵₁") == 2
