"""Notebook labels support Greek letters in engineering subscripts."""
import pytest
from utils.symbolic import _text_to_latex


@pytest.mark.parametrize("label,expected", [
    ("R_θJAsot223", r"R_{\theta{}JAsot223}"),
    ("R_θJAsot223 =", r"R_{\theta{}JAsot223}\text{ =}"),
    ("Rθ_JA", r"R\theta{}_{JA}"),
    ("λ_θ0", r"\lambda_{\theta{}0}"),
    ("R_eq", "R_{eq}"),
    ("V_out", "V_{out}"),
    ("omega_0", r"\omega_{0}"),
    ("ν_yellow", r"\nu_{yellow}"),
    ("R1_θ", r"R1_{\theta{}}"),
])
def test_engineering_labels(label, expected):
    assert _text_to_latex(label) == expected


def test_text_escaping_and_identifier_boundaries():
    assert _text_to_latex("50% & R_θJA") == r"\text{50\% \& }R_{\theta{}JA}"
    assert _text_to_latex("some_R_θJA") == r"\text{some\_R\_θJA}"
