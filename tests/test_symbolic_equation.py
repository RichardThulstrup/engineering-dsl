"""The infix equation operator is exactly the SymPy Eq constructor."""
import pytest
from pygments import lex
from pygments.token import Operator, String
import sympy as sym

import utils.Engineer as E
from utils import circuit_dsl as dsl
from utils.Engineer_Style import EngineeringDSLLexer


def run(source, mode="python"):
    ns = {name: getattr(E, name) for name in E.__all__}
    exec(dsl.transform_source(source, syntax_mode=mode), ns)
    return ns


@pytest.mark.parametrize("mode", ["python", "legacy"])
def test_solve_equations_and_preserve_comparisons(mode):
    ns = run("""
symbols: x, y
_equation := x² ≡ 4
_roots := solve(_equation, x)
_inline := solve(x²≡4, x)
_system := solve([x + y ≡ 3, x − y ≡ 1], [x, y], dict=True)
_comparison := x ≟ 2
_plain_comparison := x == 2
""", mode)
    x, y = ns['x'], ns['y']
    assert ns['_equation'] == sym.Eq(x**2, 4)
    assert ns['_roots'] == ns['_inline'] == [-2, 2]
    assert ns['_system'] == [{x: 2, y: 1}]
    assert ns['_comparison'] is ns['_plain_comparison'] is False


@pytest.mark.parametrize("mode", ["python", "legacy"])
def test_equation_precedence_and_expression_contexts(mode):
    ns = run("""
symbols: x, y
_equation := x² + 2·x + 1 ≡ 4*y − 3
_nested := Eq(x, y) ≡ Eq(y, x)
_keyword := dict(equation=x + 1 ≡ 2*y)
_expressions := [x ≡ v for v in [2, 3]]
_selected := x ≡ 2 if True else x ≡ 3
def make_equation(value):
    return x + value ≡ 4
_returned := make_equation(2)
_semicolon := x ≡ 2; _second := y ≡ 3
""", mode)
    x, y = ns['x'], ns['y']
    assert ns['_equation'] == sym.Eq(x**2+2*x+1, 4*y-3)
    assert ns['_nested'] == sym.Eq(sym.Eq(x,y), sym.Eq(y,x))
    assert ns['_keyword']['equation'] == sym.Eq(x+1, 2*y)
    assert ns['_expressions'] == [sym.Eq(x,2), sym.Eq(x,3)]
    assert ns['_selected'] == ns['_semicolon'] == sym.Eq(x,2)
    assert ns['_returned'] == sym.Eq(x+2,4)
    assert ns['_second'] == sym.Eq(y,3)


@pytest.mark.parametrize("mode", ["python", "legacy"])
def test_multiline_equations_and_matrix_operands(mode):
    ns = run("""
symbols: x, y
_equation := (
    x + 1
    ≡
    2*y
)
_solution := solve(
    x²
    ≡ 4,
    x
)
_matrix := Matrix([[x, 2], [3, y]])
_matrix_equation := _matrix ≡ Matrix([[1, 2], [3, 4]])
""", mode)
    x, y = ns['x'], ns['y']
    assert ns['_equation'] == sym.Eq(x+1,2*y)
    assert ns['_solution'] == [-2,2]
    assert ns['_matrix_equation'] == sym.Eq(sym.Matrix([[x,2],[3,y]]),sym.Matrix([[1,2],[3,4]]))


@pytest.mark.parametrize("mode", ["python", "legacy"])
def test_equations_keep_sympy_evaluation_and_literal_text(mode):
    source = '''symbols: x
_true := 2 ≡ 2
_false := 2 ≡ 3
_identity := x ≡ x
_text := "x ≡ 2"
_multiline := """x ≡ 2
y ≡ 3"""
# Keep this comment: x ≡ 2
'''
    code = dsl.transform_source(source, syntax_mode=mode)
    assert '# Keep this comment: x ≡ 2' in code
    ns = run(source, mode)
    assert ns['_true'] is ns['_identity'] is sym.true
    assert ns['_false'] is sym.false
    assert ns['_text'] == 'x ≡ 2'
    assert ns['_multiline'] == 'x ≡ 2\ny ≡ 3'
    assert dsl.transform_source(source, filename='helper.py') == source


@pytest.mark.parametrize('source', ['_r := x ≡', '_r := ≡ 2', '_r := x ≡ y ≡ z'])
def test_malformed_or_chained_equations_have_clear_errors(source):
    with pytest.raises(SyntaxError, match='≡'):
        dsl.transform_source(source)


def test_equation_highlighting():
    tokens = list(lex('x ≡ 2\n"≡"', EngineeringDSLLexer()))
    assert (Operator, '≡') in tokens
    assert any(kind in String and '≡' in value for kind, value in tokens)
