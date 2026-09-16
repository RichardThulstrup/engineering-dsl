"""Temperature coefficients use differences; readings retain offsets."""
import pytest
import forallpeople as si
import utils.Engineer as E
from utils.circuit_dsl import transform_source
from utils.sigfig import _unwrap


def run(expression):
    ns = {name: getattr(E, name) for name in E.__all__}
    exec(transform_source("_result := " + expression), ns)
    return _unwrap(ns["_result"])


@pytest.mark.parametrize("unit,scale", [
    ("°C", 1), ("℃", 1), ("°F", 5/9), ("℉", 5/9), ("°R", 5/9),
])
@pytest.mark.parametrize("suffix", ["/W", " / W", "/(W)", "·s"])
def test_compound_temperature_units(unit, scale, suffix):
    result = run(f"205.9 {unit}{suffix}")
    expected = 205.9 * scale * si.K
    expected = expected * si.s if suffix == "·s" else expected / (si.kg * si.m**2 / si.s**3)
    assert result.dimensions == expected.dimensions
    assert result.value == pytest.approx(expected.value)


@pytest.mark.parametrize("expression,kelvin", [
    ("25 °C", 298.15), ("77 °F", 298.15),
    ("25 °C / 2", 149.075), ("(25 °C) / W", 298.15),
    ("(25 + 5) °C", 303.15), ("205.9 ΔC/W", 205.9),
])
def test_absolute_readings_and_explicit_differences(expression, kelvin):
    assert run(expression).value == pytest.approx(kelvin)


@pytest.mark.parametrize("resistance,expected", [
    (205.9, 154.7633275), (53.1, 58.4649475),
])
def test_ldo_junction_temperature(resistance, expected):
    result = run(f"25 °C + {resistance} °C/W * ((5.5 V - 3.0 V) * 250 mA + 5.5 V * 0.95 mA)")
    assert result.dimensions == si.K.dimensions
    assert result.value - 273.15 == pytest.approx(expected)


def test_strings_and_comments_are_preserved():
    source = '_result := "205.9 °C/W" # 25 °C/W'
    ns = {}
    exec(transform_source(source), ns)
    assert ns['_result'] == '205.9 °C/W'


@pytest.mark.parametrize("expression,ending", [
    ("205.9 °C/W", "°C/W"), ("53.1 °C / (W)", "°C/W"),
    ("205. °C/W", "°C/W"), ("2.0 °F/mW", "°F/mW"),
    ("25 °C + 205.9 °C/W * 0.630225 W", "°C"),
    ("0.630225 W * 205.9 °C/W + 25 °C", "°C"),
    ("77 °F + 10 ΔF", "°F"), ("10 ΔC + 25 °C", "°C"),
    ("25 °C - 10 ΔC", "°C"), ("25 °C * 2", "°C"),
])
def test_written_temperature_display_survives_arithmetic(expression, ending):
    ns = {name: getattr(E, name) for name in E.__all__}
    exec(transform_source("_result := " + expression), ns)
    result = ns['_result']
    assert str(result).endswith(ending)
    latex = result._repr_latex_()
    assert latex and ('circ' in latex or '°' in latex)
    if ending == '°C/W':
        assert 'W' in latex and 'kg' not in latex


@pytest.mark.parametrize('expression', ['2.0 °C/W**2', '2.0 °C/(W)**2'])
def test_denominator_power_keeps_original_dimensions(expression):
    result = run(expression)
    expected = 2 * si.K / (si.kg * si.m**2 / si.s**3)**2
    assert result.dimensions == expected.dimensions
    assert result.value == pytest.approx(expected.value)


def test_temperature_difference_does_not_become_absolute():
    ns = {name: getattr(E, name) for name in E.__all__}
    exec(transform_source('_result := 100 °C - 25 °C'), ns)
    assert str(ns['_result']).endswith('ΔC')
    assert float(ns['_result']) == pytest.approx(75)
