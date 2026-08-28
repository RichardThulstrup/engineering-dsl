"""Verify nested-paren handling by exec'ing the rewritten Python.

Runs against the real ``utils.circuit_dsl`` in this repo — invoke from
anywhere: ``python tests/test_nested_parens.py``.
"""
import os
import sys
import math as _math

# Make the repo root importable regardless of the working directory
# (this file lives in ``utils/``, so the root is one level up).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils.circuit_dsl as dsl

# Identity stubs for the literal-wrapping helpers, so exec'd code computes
# values directly without needing the full Sig type.
exec_ns_base = {
    '_S': lambda v, sf=None: v,
    '_INF': float('inf'),
    'sqrt': _math.sqrt,
    'percent': lambda x: x / 100,
    'permille': lambda x: x / 1000,
    'fact': _math.factorial,
}

fails = 0
def check(label, inp, expected_value):
    """Rewrite the input via the DSL, exec it as `result = <inp>`,
    and check the resulting value matches expected_value."""
    global fails
    rewritten = dsl.transform_source(f"_result := {inp}")
    ns = dict(exec_ns_base)
    try:
        exec(rewritten, ns)
        got = ns.get('_result')
        ok = abs(got - expected_value) < 1e-9 if isinstance(got, (int, float)) else got == expected_value
    except Exception as e:
        got = f"<{type(e).__name__}: {e}>"
        ok = False
    flag = "OK" if ok else "FAIL"
    if not ok:
        fails += 1
    print(f"  [{flag}] {label:35s} {inp!r:30s} = {got!r:25s} (expected {expected_value!r})")
    if not ok:
        print(f"         rewritten: {rewritten!r}")

# ==== √ ====
print("=" * 70)
print("√ — should produce sqrt(<full-balanced-expression>)")
print("=" * 70)
check("user's reported case",        "√(1+(3+2))",      _math.sqrt(6))
check("3 levels deep",               "√(1+(2+(3+4)))",  _math.sqrt(10))
check("balanced inner group",        "√((1+2)*3)",      _math.sqrt(9))
check("nested sqrt",                 "√(√(16))",        2.0)
check("plain regression",            "√4",              2.0)
check("1-level paren regression",    "√(1+2)",          _math.sqrt(3))
check("cube root of 27",             "³√27",            3.0)
check("cube root of nested",         "³√(1+(2+(3+4+17))) ", 3.0)  # 27

# ==== ² ====
print()
print("=" * 70)
print("² — postfix superscript should produce (operand)**N")
print("=" * 70)
check("user's case-shape",           "(1+(3+2))²",       36)         # 6² = 36
check("3 levels deep",               "((1+(2+3)))²",     36)         # 6² = 36
check("plain regression",            "3²",                9)
check("1-level regression",          "(2+3)²",            25)
check("function-call operand",       "(abs(-5))²",        25)
check("negative exponent + nest",    "(1+(2+3))⁻¹",       1/6)

# ==== % ====
print()
print("=" * 70)
print("% — postfix percent should produce percent(operand)")
print("=" * 70)
check("user's case-shape",           "(1+(3+2))%",        0.06)      # 6/100
check("3 levels deep",               "((1+(2+3)))%",      0.06)
check("plain regression",            "25%",               0.25)
check("nested percent",              "((50)%)%",          0.005)     # 50% = 0.5; (0.5)% = 0.005
check("modulo unaffected",           "10 % 3",            1)
check("mixed with sqrt",             "√((1+(2+3))%)",     _math.sqrt(0.06))

# ==== ‰ ====
print()
print("=" * 70)
print("‰ — postfix permille should produce permille(operand)")
print("=" * 70)
check("user's case-shape",           "(1+(3+2))‰",        0.006)
check("3 levels deep",               "((1+(2+3)))‰",      0.006)
check("plain regression",            "5‰",                0.005)

# ==== Combined ====
print()
print("=" * 70)
print("Combined deep nesting + multiple operators")
print("=" * 70)
check("sqrt(percent(deeply nested))",  "√((1+(2+3))%)",        _math.sqrt(0.06))
check("squared with deep operand",     "(1+(2+(3+4)))²",       100)
check("multiple sqrts",                "√(1+(2+3)) + √(2+(3+4))", _math.sqrt(6) + _math.sqrt(9))

# ==== ! (factorial) ====
print()
print("=" * 70)
print("! — postfix factorial should produce fact(operand)")
print("=" * 70)
check("user's case-shape (2 levels)",  "(1+(1+(1)))!",          _math.factorial(3))
check("3 levels deep",                 "(1+(2+(3+4)))!",        _math.factorial(10))
check("5 levels of redundant",         "(((((1+2)))))!",        _math.factorial(3))
check("plain regression",              "5!",                    120)
check("function-call operand",         "abs(-3)!",              6)
check("function-call w/ nested arg",   "abs(-(1+2))!",          6)
check("chained postfix",               "3!!",                   _math.factorial(6))
check("nested with parens",            "((3)!)!",               _math.factorial(6))
check("inequality unaffected",         "1 if 5 != 3 else 0",    1)

# ==== ‰ (permille) extra ====
print()
print("=" * 70)
print("‰ — function-call operand (latent fix)")
print("=" * 70)
check("function-call operand",         "abs(-50)‰",             0.05)

# ==== % function-call (latent fix) ====
print()
print("=" * 70)
print("% — function-call operand (latent fix)")
print("=" * 70)
check("function-call operand",         "abs(-50)%",             0.5)
check("function-call w/ nested arg",   "abs(-(1+2))%",          0.03)

# ==== ² function-call (latent fix) ====
print()
print("=" * 70)
print("² — function-call operand (latent fix)")
print("=" * 70)
check("function-call operand",         "abs(-3)²",              9)
check("function-call w/ nested arg",   "abs(-(1+2))²",          9)

print()
print("=" * 70)
print(f"FINAL: {'ALL PASSED' if fails == 0 else f'{fails} FAILED'}")
print("=" * 70)
sys.exit(0 if fails == 0 else 1)
