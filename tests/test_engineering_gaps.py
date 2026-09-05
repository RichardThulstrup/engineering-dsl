# -*- coding: utf-8 -*-
"""Regression tests for the "engineering gaps" batch (Sept 2026).

  * Angle names (``deg``, ``rev``, ``arcmin`` …) and ``▸ deg`` display;
    ``rpm``/``rps`` as rotational frequencies.
  * Phasors print in polar form; ``polar()`` / ``rect()`` re-tag.
  * New constants (``G``, ``σ_SB``, ``Faraday``, ``m_n``, ``Z_0`` …).
  * New unit names (``L``, ``mS``, ``Ah``, ``mJ``, ``kip``, ``lx``, ``Bq`` …),
    display-tagged ``VA``/``var``, and automatic tight binding for every
    unit ``extra_units`` exports.
  * ``R ▸ plusminus`` / ``R ▸ percent`` interval display.
  * ``interp()`` table lookup, ``approx(…, sf=True)``.
  * ``plot()`` log axes / grid / limits / figsize / save / error bars; ``bode()``.
  * ISO durations: negative and fractional parse, Y/M/W round-trip,
    ``timedelta ▸ hour``.
"""
import math
import os
import sys

import pytest

os.environ.setdefault("SYMBOL_PALETTE_EXE", os.devnull)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import matplotlib                                            # noqa: E402
matplotlib.use("Agg")

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


# --------------------------------------------------------------------------
# Angles and rotation
# --------------------------------------------------------------------------
def test_angle_units():
    assert float(run("_r := 90 deg")) == pytest.approx(math.pi / 2)
    assert float(run("_r := sin(30 deg)")) == pytest.approx(0.5)
    assert str(run("_r := 1.0 rev ▸ deg")) == "360 deg"
    assert str(run("_r := asin(0.5) ▸ deg")) == "30 deg"
    assert float(run("_r := 60 arcmin")) == pytest.approx(math.pi / 180)
    assert float(run("_r := 100 gon")) == pytest.approx(math.pi / 2)


def test_rpm_is_a_frequency():
    assert str(run("_r := 3000 rpm")) == "3000 rpm"
    assert str(run("_r := 3000 rpm ▸ Hz")) == "50 Hz"
    assert str(run("_r := 50 Hz ▸ rpm")) == "3000 rpm"
    assert f(run("_r := 2π · 3000 rpm · 0.1 m")) == pytest.approx(10 * math.pi)


# --------------------------------------------------------------------------
# Phasors
# --------------------------------------------------------------------------
def test_phasor_prints_polar():
    assert str(run("_r := 5.0 ∠ 30°")) == "5.0 ∠ 30°"
    assert str(run("_r := 10.0 ∠ -45°")) == "10.0 ∠ -45.0°"
    z = run("_r := 5.0 ∠ 30°")
    assert abs(complex(z)) == pytest.approx(5.0)
    assert complex(z).real == pytest.approx(5 * math.cos(math.pi / 6))
    # arithmetic drops the tag; polar()/rect() switch the form
    assert str(run("_r := (5.0 ∠ 30°) · 2")) == "(8.7+5.0j)"
    assert str(run("_r := polar((5.0 ∠ 30°) · 2)")) == "10 ∠ 30°"
    assert str(run("_r := rect(5.0 ∠ 30°)")) == "(4.3+2.5j)"


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
def test_new_constants():
    assert f(run("_r := G")) == pytest.approx(6.6743e-11)
    assert f(run("_r := σ_SB")) == pytest.approx(5.670374419e-8)
    assert f(run("_r := Faraday")) == pytest.approx(96485.33212, rel=1e-9)
    assert f(run("_r := m_n")) == pytest.approx(1.67492749804e-27)
    assert float(run("_r := α_fs")) == pytest.approx(7.2973525693e-3)
    assert f(run("_r := Z_0")) == pytest.approx(376.730313668)
    assert run("_r := μ_0 · c ≈ Z_0") is True
    assert f(run("_r := R_∞")) == pytest.approx(10973731.568160)
    assert f(run("_r := p_0")) == 101325.0


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------
@pytest.mark.parametrize("src, si", [
    ("1.0 L", 1e-3), ("2.5 kL", 2.5), ("250 mL ▸ L", 0.25), ("3 ha", 3e4),
    ("5 mmol", 5e-3), ("50 mS", 0.05), ("3.0 Ah", 10800.0), ("2500 mAh", 9000.0),
    ("1.0 mJ", 1e-3), ("2 MWh", 7.2e9), ("1.5 kip", 1.5 * 4448.2216152605),
    ("3 yd", 2.7432), ("10 nmi", 18520.0), ("1 dyn", 1e-5), ("500 lx", 500.0),
    ("3 kBq", 3000.0), ("2.5 mSv", 2.5e-3), ("0.5 gauss", 5e-5), ("1.0 cP", 1e-3),
    ("1 mPa", 1e-3), ("20 μPa", 2e-5), ("2 cSt", 2e-6),
])
def test_new_unit_values(src, si):
    assert f(run(f"_r := {src}")) == pytest.approx(si)


def test_new_units_display_written_form():
    assert str(run("_r := 50 mS")) == "50 mS"
    assert str(run("_r := 2500 mAh")) == "2500 mAh"
    assert str(run("_r := 500 lx")) == "500 lx"
    assert str(run("_r := 3 kBq")) == "3 kBq"
    assert str(run("_r := 230 V · 10 A ▸ kVA")) == "2.3 kVA"
    assert str(run("_r := 400 var")) == "400 var"
    assert str(run("_r := 0.5 gauss ▸ μT")) == "50 μT"


def test_units_bind_tightly_without_table_entry():
    # ``mAh`` is new and never listed by hand; ``5 mAh / 2 hr`` must read
    # as (5 mAh)/(2 hr), i.e. 2.5 mA.
    assert str(run("_r := 5 mAh / 2 hr")) == "2.5 mA"
    assert str(run("_r := 2500 mAh · 3.7 V ▸ Wh")) == "9.2 Wh"


# --------------------------------------------------------------------------
# Interval display
# --------------------------------------------------------------------------
def test_interval_display_forms():
    assert str(run("_r := (100 Ω ± 5 Ω) ▸ plusminus")) == "100 Ω ± 5 Ω"
    assert str(run("_r := (100 Ω ± 5 Ω) ▸ percent")) == "100 Ω ± 5.00%"
    assert str(run("_r := (4.7 kΩ ± 1%) ▸ percent")) == "4.7 kΩ ± 1.0%"
    assert str(run("_r := (100 ± 5) ▸ permille")) == "100.0 ± 50.0‰"
    # transparent under arithmetic
    r = _unwrap(run("_r := ((4.7 kΩ ± 1%) ▸ percent) · 2"))
    assert isinstance(r, Range)


# --------------------------------------------------------------------------
# interp and approx(sf=True)
# --------------------------------------------------------------------------
def test_interp_unit_aware():
    v = run("Ts := [300, 350, 400] K\ncps := [1.005, 1.009, 1.014] kJ/(kg·K)\n_r := interp(325 K, Ts, cps)")
    assert f(v) == pytest.approx(1007.0)
    assert f(run("Ts := [300, 350, 400] K\ncps := [1.005, 1.009, 1.014] kJ/(kg·K)\n_r := interp(500 K, Ts, cps)")) == pytest.approx(1014.0)
    assert f(run("Ts := [300, 350, 400] K\ncps := [1.005, 1.009, 1.014] kJ/(kg·K)\n_r := interp(500 K, Ts, cps, extrapolate=True)")) == pytest.approx(1024.0)
    assert float(run("_r := interp(2.5, [1, 2, 3], [10, 20, 30])")) == 25
    assert [float(v) for v in run("_r := interp([1.5, 2.5], [1, 2, 3], [10, 20, 30])")] == [15, 25]
    with pytest.raises(TypeError):
        run("_r := interp(2.5 m, [1, 2, 3], [10, 20, 30])")


def test_approx_sig_fig_mode():
    assert run("_r := approx(5.00 V, 5.01 V, sf=True)") is False
    assert run("_r := approx(5.0 V, 5.01 V, sf=True)") is True
    assert run("_r := approx(5.0, 5.04, sf=True)") is True
    assert run("_r := approx(5, 5.0000001, sf=True)") is False     # exact → tolerance test
    assert run("_r := approx(5.0 V, 5.0 A, sf=True)") is False


# --------------------------------------------------------------------------
# Plotting
# --------------------------------------------------------------------------
def test_plot_options_and_errorbars(tmp_path):
    out = tmp_path / "e.png"
    ns = dict(NS)
    exec(transform_source(
        "x := [0, 1, 2, 3]\n"
        "y := [1.0 ± 0.1, 2.1 ± 0.2, 2.9 ± 0.1, 4.2 ± 0.3] V\n"
        f"_r := plot(x, y, 'measured', show=False, grid=True, ylim=(0, 5), save=r'{out}', return_ax=True)"), ns)
    ax = ns["_r"]
    assert out.exists() and out.stat().st_size > 0
    assert ax.containers, "errorbar container expected"
    assert ax.get_ylim() == (0.0, 5.0)
    assert ax.get_ylabel() == "[V]"


def test_plot_log_axes(tmp_path):
    out = tmp_path / "l.png"
    ns = dict(NS)
    exec(transform_source(
        "xs := [1, 10, 100, 1000]\nys := [1.0, 0.5, 0.1, 0.01]\n"
        f"_r := plot(xs, ys, loglog=True, figsize=(5, 3), show=False, save=r'{out}', return_ax=True)"), ns)
    ax = ns["_r"]
    assert ax.get_xscale() == "log" and ax.get_yscale() == "log"
    assert out.exists()
    ns = dict(NS)
    exec(transform_source(
        "fr := [10, 100, 1000, 10000] Hz\ngain := [0.0, -1.0, -3.0, -20.0]\n"
        "_r := plot(fr, gain, logx=True, xlim=(1 Hz, 100 kHz), show=False, return_ax=True)"), ns)
    ax = ns["_r"]
    assert ax.get_xscale() == "log"
    assert ax.get_xlim() == (1.0, 1e5)


def test_bode(tmp_path):
    out = tmp_path / "b.png"
    ns = dict(NS)
    exec(transform_source(
        "symbols: p\nH_lp := 1/(1 + p/(2π·1000))\n"
        f"_r := bode(H_lp, p, (10 Hz, 100 kHz), show=False, save=r'{out}', return_axes=True)"), ns)
    ax_m, ax_p = ns["_r"]
    line = ax_m.get_lines()[0]
    xs, ys = line.get_xdata(), line.get_ydata()
    import numpy as np
    k = int(np.argmin(abs(xs - 1000)))
    assert ys[k] == pytest.approx(-3.01, abs=0.05)         # −3 dB at the corner
    assert ax_p.get_lines()[0].get_ydata()[k] == pytest.approx(-45, abs=0.5)
    assert out.exists()


# --------------------------------------------------------------------------
# ISO durations
# --------------------------------------------------------------------------
def test_iso_duration_roundtrip_and_parse():
    assert run('_r := iso(iso("P1Y"))') == "P1Y"
    assert run('_r := iso(iso("P2M"))') == "P2M"
    assert run('_r := iso(iso("P3W"))') == "P3W"
    assert run('_r := iso(iso("PT1.5H"))') == "PT1H30M"
    assert run('_r := iso("-PT5M").total_seconds()') == -300
    assert run('_r := iso(iso("-PT5M"))') == "-PT5M"


def test_timedelta_in_units():
    assert str(run('td := "PT90M"ₜᵢₘₑ\n_r := td ▸ hour')) == "1.5 hour"
    assert str(run('td := "PT90M"ₜᵢₘₑ\n_r := td ▸ s')) == "5400 s"
