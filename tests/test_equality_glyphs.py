"""Unicode equality aliases retain ordinary == semantics."""
import pytest
from pygments import lex
from pygments.token import Operator, String

import utils.Engineer as E
from utils import circuit_dsl as dsl
from utils.Engineer_Style import EngineeringDSLLexer

GLYPHS = ("﹦", "＝", "≟")


def run(source, mode):
    ns = {name: getattr(E, name) for name in E.__all__}
    exec(dsl.transform_source(source, syntax_mode=mode), ns)
    return ns


@pytest.mark.parametrize("glyph", GLYPHS)
@pytest.mark.parametrize("mode", ["python", "legacy"])
def test_equality_glyph_precedence_conditions_and_units(glyph, mode):
    ns = run(f"""
_value := 4
_equal := 1+3{glyph}_value
_unequal := 1 + 2 {glyph} _value
_chain := 0 < _value {glyph} 4 < 5
_condition := False
if _value {glyph} 4:
    _condition := True
_units := 5 V {glyph} 5000 mV
_call := dict(equal=(_value {glyph} 4))
_filtered := [v for v in [3, 4, 5] if v {glyph} 4]
""", mode)
    assert ns['_equal'] is True
    assert ns['_unequal'] is False
    assert ns['_chain'] is True
    assert ns['_condition'] is True
    assert ns['_units'] is True
    assert ns['_call'] == {'equal': True}
    assert ns['_filtered'] == [4]
    assert ns['_value'] == 4  # comparison never assigns


@pytest.mark.parametrize("glyph", GLYPHS)
@pytest.mark.parametrize("mode", ["python", "legacy"])
def test_equality_glyph_preserves_strings_comments_and_symbolic_semantics(glyph, mode):
    source = f'''
_text := "1 {glyph} 2"
_multiline := """a {glyph} b
c {glyph} d"""
# comparison glyph stays in this comment: {glyph}
symbols: x
_symbol_equal := x {glyph} x
_symbol_unequal := x {glyph} 2
_solution := solve(Eq(x, 2), x)
'''
    transformed = dsl.transform_source(source, syntax_mode=mode)
    assert f'# comparison glyph stays in this comment: {glyph}' in transformed
    ns = run(source, mode)
    assert ns['_text'] == f'1 {glyph} 2'
    assert ns['_multiline'] == f'a {glyph} b\nc {glyph} d'
    assert ns['_symbol_equal'] is True
    assert ns['_symbol_unequal'] is False
    assert ns['_solution'] == [2]
    # Imported Python source is still outside the notebook transformer.
    assert dsl.transform_source(source, filename='helper.py') == source


@pytest.mark.parametrize("glyph", GLYPHS)
def test_equality_glyph_highlighting(glyph):
    tokens = list(lex(f'x {glyph} y\n"{glyph}"', EngineeringDSLLexer()))
    assert (Operator, glyph) in tokens
    assert any(kind in String and glyph in value for kind, value in tokens)
