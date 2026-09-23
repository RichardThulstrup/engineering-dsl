"""A written ratio whose dimensions partly cancel keeps its written form."""
import pytest
import utils.Engineer as E
from utils.circuit_dsl import transform_source


def run(expression):
    ns = {name: getattr(E, name) for name in E.__all__}
    exec(transform_source("_result := " + expression), ns)
    return ns["_result"]


@pytest.mark.parametrize("expression,shown", [
    ("17 L/km", "17 L/km"),                 # not 17 mm²
    ("2 mi/gal_us", "2 mi/gal_us"),         # not 0.85 mm⁻²
    ("17.0 L/km", "17.0 L/km"),
    ("5 J/kg", "5 J/kg"),                   # not 5 Gy
    ("5 N/mm", "5 N/mm"),
    ("5 kW h/km", "5 kW·h/km"),
    ("2 * 17 L/km", "34 L/km"),
    ("(17 L/km) * 3", "51 L/km"),
    # A number in the denominator: ``L/100 km`` fuel figures.
    ("5 L/100 km", "5 L/100 km"),           # not 50000 μm²
    ("5 L/(100 km)", "5 L/100 km"),
    ("5.50 L/100km", "5.50 L/100 km"),
    ("2 * 5 L/100 km", "10 L/100 km"),
    ("5 L/(100 km) + 1 L/(100 km)", "6 L/100 km"),
    ("15 kW h/100 km", "15 kW·h/100 km"),
    ("5 m/100 s", "0.05 m·s⁻¹"),
    # A spaced ``/`` or a non-power-of-ten count is a calculation.
    ("5 mAh / 2 hr", "2.5 mA"),
    ("5 mAh/2 hr", "2.5 mA"),
    ("17 L / km", "17 mm²"),
    ("5 L / 100 km", "50000 μm²"),
    # Nothing cancels, or everything does: unchanged display.
    ("30 m/s", "30 m·s⁻¹"),
    ("10 mA/V", "10 mS"),
    ("3 mm/m", "0.003"),
])
def test_written_ratio_display(expression, shown):
    assert repr(run(expression)) == shown


@pytest.mark.parametrize("expression,tex", [
    # Multi-letter subscripts are braced: ``gal_{us}``, not ``gal_us``
    # (which LaTeX typesets as galᵤs).
    ("2 mi/gal_us", r"$2\ \mathrm{mi}/\mathrm{gal_{us}}$"),
    ("3 gal_us", r"$3\ \mathrm{gal_{us}}$"),
    ("2 mi/gal_uk", r"$2\ \mathrm{mi}/\mathrm{gal_{uk}}$"),
    # Only the last part subscripts; ``fl_{oz}_{us}`` would not typeset.
    ("4 fl_oz_us", r"$4\ \mathrm{fl\ oz_{us}}$"),
    ("5 MeV_per_c2", r"$5\ \mathrm{MeV/c^{2}}$"),
    # The space in ``100 km`` survives math mode.
    ("5 L/100 km", r"$5\ \mathrm{L}/\mathrm{100\ km}$"),
])
def test_written_ratio_latex(expression, tex):
    assert run(expression)._repr_latex_() == tex


def test_ratio_value_is_unchanged():
    assert run("17 L/km").value.value == pytest.approx(17e-6)


def test_per_hundred_value_is_unchanged():
    assert run("5 L/100 km").value.value == pytest.approx(5e-3 / 100e3)


def test_explicit_multiply_is_not_a_ratio():
    # ``/ 100 * km`` is written division-then-multiply; leave it be.
    assert run("5 L / 100 * km").value.value == pytest.approx(5e-3 / 100 * 1e3)


@pytest.mark.parametrize("expression", ["10 DKK/km", "10 DKK/100 km"])
def test_currency_over_unit_still_refused(expression):
    with pytest.raises(TypeError, match="physical"):
        run(expression)
