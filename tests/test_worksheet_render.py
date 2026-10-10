"""The worksheet typesets the code that runs, so its notation follows the transformer."""
import ast

import pytest

from utils import circuit_dsl as dsl
from utils.worksheet import mathrender


class Names(mathrender.NameInfo):
    """Units as the transformer knows them; nothing defined by a sheet."""
    units = frozenset(dsl._UNIT_NAMES_FOR_BINDING) - frozenset(dsl._CONSTANT_NAMES)

    def __init__(self, user=()):
        self.user = set(user)

    def is_unit(self, name):
        return name in self.units and name not in self.user

    def is_user(self, name):
        return name in self.user


def render(source, user=()):
    transformed = dsl.transform_source(source)
    node, = ast.parse(transformed).body
    return mathrender.render_statement(node, transformed, Names(user))


def tex(source, user=()):
    return render(source, user)["latex"]


@pytest.mark.parametrize("source,expected", [
    # A quantity is one thing: number, thin gap, upright unit.
    ("V_in := 12.0 V", r"V_{\mathrm{in}} \coloneqq 12.0\ \mathrm{V}"),
    ("R_bot := 10. kΩ", r"R_{\mathrm{bot}} \coloneqq 10.\ \mathrm{k\Omega}"),
    ("v := 5 m/s", r"v \coloneqq 5\ \mathrm{m}/\mathrm{s}"),
    ("9.81 m·s⁻²", r"9.81\ \mathrm{m} \cdot \mathrm{s}^{-2}"),
    ("fuel := 5 L/100 km", r"\mathit{fuel} \coloneqq 5\ \mathrm{L}/100\ \mathrm{km}"),
    ("T_A := 25 °C", r"T_{A} \coloneqq 25\ \mathrm{{}^{\circ}C}"),
    ("R_θJA := 205.9 °C/W", r"R_{\theta \mathrm{JA}} \coloneqq 205.9\ \mathrm{{}^{\circ}C}/\mathrm{W}"),
    ("-5 V", r"-5\ \mathrm{V}"),
    # The grouping drawn is the grouping computed.
    ("12 V / 3 A", r"\frac{12\ \mathrm{V}}{3\ \mathrm{A}}"),
    ("1/2 V", r"\frac{1}{2\ \mathrm{V}}"),
    ("V_out := V_in · R_bot/(R_top + R_bot)",
     r"V_{\mathrm{out}} \coloneqq \frac{V_{\mathrm{in}} \cdot R_{\mathrm{bot}}}"
     r"{R_{\mathrm{top}} + R_{\mathrm{bot}}}"),
    ("a - (b - c)", r"a - \left(b - c\right)"),
    ("a·(-b)", r"a \cdot \left(-b\right)"),
    ("(-x)²", r"{\left(-x\right)}^{2}"),
    ("-x²", r"-{x}^{2}"),
    ("(a + b)(c + d)", r"\left(a + b\right) \cdot \left(c + d\right)"),
    # Coefficients sit against what they multiply; names get a dot.
    ("b_2 := (B_1 - 3b)/2", r"b_{2} \coloneqq \frac{B_{1} - 3\,b}{2}"),
    ("ω := 2π·50 Hz", r"\omega \coloneqq 2\,\pi \cdot 50\ \mathrm{Hz}"),
    ("q := ½ ρ v²", r"q \coloneqq \frac{1}{2}\,\rho \cdot {v}^{2}"),
    # Notation the DSL adds.
    ("R_eq := R_1 ‖ R_2 ‖ R_3", r"R_{\mathrm{eq}} \coloneqq R_{1} \parallel R_{2} \parallel R_{3}"),
    ("x := 100 Ω ± 5%", r"x \coloneqq 100\ \mathrm{\Omega} \pm 5\%"),
    ("hyp := √(a² + b²)", r"\mathit{hyp} \coloneqq \sqrt{{a}^{2} + {b}^{2}}"),
    ("⁵√x", r"\sqrt[5]{x}"),
    ("z := 3∠45°", r"z \coloneqq 3 \angle 45^{\circ}"),
    ("|x|", r"\left|x\right|"),
    ("n!", "n!"),
    ("a ≈ b", r"a \approx b"),
    ("a ≟ b", r"a \stackrel{?}{=} b"),
    ("x² ≡ 4", r"{x}^{2} \equiv 4"),
    ("log₁₀(100)", r"\log_{10}\left(100\right)"),
    ("exp(-t/τ)", r"e^{-t/\tau}"),
    ("Mᵀ", r"M^{\mathsf{T}}"),
    ("M₀͵₁", "M_{0,1}"),
    ("xs := [1..10]", r"\mathit{xs} \coloneqq \left[1 \mathbin{..} 10\right]"),
    ("a := 3 ‥ 7", r"a \coloneqq 3 \mathbin{\text{‥}} 7"),
    ("M := Matrix([[1, 2], [3, 4]])", r"M \coloneqq \begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}"),
    ("x if x > 0 else -x", r"\begin{cases}x & \text{if } x > 0 \\ -x & \text{otherwise}\end{cases}"),
    ("[k² for 1 ≤ k ≤ 5]", r"\left[{k}^{2} \;\middle|\; 1 \le k \le 5\right]"),
    ("fed₁₆", r"\mathrm{fed}_{16}"),
    ("1.5e3 V", r"1.5 \cdot 10^{3}\ \mathrm{V}"),
    # Logic, sets, powers and marks from the symbol keys.  Read as
    # mathematics, ¬ takes only what stands right after it, so a negated
    # comparison keeps its brackets (Python's ``not`` would not need them).
    ("ok := ¬(x_1 > y_1) ∧ done", r"\mathit{ok} \coloneqq \lnot \left(x_{1} > y_{1}\right) \land \mathit{done}"),
    ("ok := ¬(p_1 ∧ q_1)", r"\mathit{ok} \coloneqq \lnot \left(p_{1} \land q_{1}\right)"),
    ("ok := ¬¬done", r"\mathit{ok} \coloneqq \lnot \lnot \mathit{done}"),
    ("ok := ¬ready(x_1)", r"\mathit{ok} \coloneqq \lnot \operatorname{ready}\left(x_{1}\right)"),
    ("ok := ¬done ∨ x ＝ y", r"\mathit{ok} \coloneqq \lnot \mathit{done} \lor x \stackrel{?}{=} y"),
    ("x ∈ P_1 ∪ Q_1", r"x \in P_{1} \cup Q_{1}"),
    ("P_1 ∋ x", r"x \in P_{1}"),
    ("2 ↑ 10", "{2}^{10}"),
    ("∜x", r"\sqrt[4]{x}"),
    ("xⁿ", "{x}^{n}"),
    ("xsₙ₊₁", r"\mathit{xs}_{n + 1}"),
    ("⅔ x", r"\frac{2}{3}\,x"),
    ("y ← 3", r"y \coloneqq 3"),
    ('year := "MMXXVI"ᵣₒₘₑ', r"\mathit{year} \coloneqq \mathrm{MMXXVI}"),
    # Statements with a form of their own.
    ("f := x → x² + 1", r"f\left(x\right) \coloneqq {x}^{2} + 1"),
    ("symbols: x, y", r"\text{symbols: }x,\allowbreak\ y"),
    ("R₁ := 5 Ω", r"R_{1} \coloneqq 5\ \mathrm{\Omega}"),
    ("x_1, x_2 := 1, 2", r"x_{1},\allowbreak\ x_{2} \coloneqq 1,\allowbreak\ 2"),
])
def test_notation(source, expected):
    assert tex(source) == expected


@pytest.mark.parametrize("name,expected", [
    ("V_out", r"V_{\mathrm{out}}"),
    ("x_1", "x_{1}"),
    ("P_D", "P_{D}"),
    ("R_θJAsot23", r"R_{\theta \mathrm{JAsot23}}"),
    ("ΔT", r"\Delta T"),
    ("omega_0", r"\omega_{0}"),
    ("Diagonal", r"\mathit{Diagonal}"),
    ("Mass_water", r"\mathit{Mass}_{\mathrm{water}}"),
    ("x_1_max", r"x_{1,\mathrm{max}}"),
    ("my_long_name", r"\mathit{my\_long\_name}"),
    # A trailing underscore dodges a reserved name; it stays visible so
    # that ``h_`` is never mistaken for Planck's ``h``.
    ("h_", r"h\_"),
])
def test_names(name, expected):
    assert mathrender.name_to_latex(name) == expected


def test_function_names_are_words():
    assert mathrender.name_to_latex("to_kelvin", function=True) == r"\operatorname{to\_kelvin}"
    assert mathrender.name_to_latex("f_c", function=True) == "f_{c}"


def test_a_unit_is_upright_and_a_variable_is_not():
    # ``m`` is the metre here, and the sheet should say so.
    assert tex("E := m c²") == r"E \coloneqq \mathrm{m} \cdot {c}^{2}"


def test_a_name_being_defined_is_a_variable_even_if_a_unit_is_called_that():
    # ``fs`` is the femtosecond until the sheet assigns to it.
    assert tex("fs := [1, 2] Hz") == r"\mathit{fs} \coloneqq \left[1,\ 2\right]\ \mathrm{Hz}"
    assert tex("2 fs") == r"2\ \mathrm{fs}"
    assert tex("2 fs", user={"fs"}) == r"2\,\mathit{fs}"


def test_a_sheet_that_redefines_a_helper_gets_no_special_notation():
    assert tex("sqrt(x)") == r"\sqrt{x}"
    assert tex("sqrt(x)", user={"sqrt"}) == r"\operatorname{sqrt}\left(x\right)"


@pytest.mark.parametrize("source,literal", [
    ("U_in := 5.5 V", True),            # a definition written out in full
    ("x := 100 Ω ± 5%", True),
    ("T_A := 25 °C", True),
    ("xs := [1, 2, 3]", True),
    ("P := U_in · I_out", False),       # calculated: its value is worth showing
    ("xs := [1..10]", False),
    ("τ := 10. kΩ · 100. nF", False),
    ('due := "2026-05-10"ₜᵢₘₑ', True),   # a date says what it is …
    ('wait := "PT90M"ₜᵢₘₑ', True),
    ('seen := "today"ₜᵢₘₑ', False),      # … the clock's words do not,
    ('year := "MMXXVI"ᵣₒₘₑ', False),     # and a Roman numeral reads back as a number
])
def test_only_calculated_values_are_repeated(source, literal):
    assert render(source)["literal"] is literal


def test_a_display_target_belongs_to_the_answer():
    # ``x ▸ cm`` asks for x; the cm is how the answer is written.
    shown = render("Diagonal ▸ cm")
    assert shown["latex"] == r"\mathit{Diagonal}"
    assert shown["target"] == r"\mathrm{cm}"
    # Inside a larger statement the conversion is part of the expression.
    assert r"\blacktriangleright" in tex("y := x ▸ cm")


@pytest.mark.parametrize("source", [
    "pp('R_eq =', R_eq)",               # shown as source: called for their effect
    "pv(a, b)",
    "plot(xs, ys)",
    "import numpy as np",
    "for 1 ≤ k ≤ 5:\n    pp(k)",
    "def f(x):\n    return x",
])
def test_statements_without_a_built_up_form(source):
    assert render(source) is None


def test_an_unexpected_tree_falls_back_to_source():
    node = ast.parse("x = y").body[0]
    node.value = ast.Name(id=None, ctx=ast.Load())      # malformed on purpose
    assert mathrender.render_statement(node, "x = y") is None
