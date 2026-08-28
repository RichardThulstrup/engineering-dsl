# -*- coding: utf-8 -*-
"""DSL matrix indexing is 0-based, bounds-checked, and mutation-safe.

Runs the real ``transform_source`` pipeline and execs the result — invoke
from anywhere: ``python tests/test_matrix_indexing.py`` or via pytest.
"""
import os
import sys

# Make the repo root importable regardless of the working directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.circuit_dsl import transform_source  # noqa: E402


def _run(src):
    env = {}
    exec("from utils.circuit_dsl import *", env)
    exec(compile(transform_source(src), "<dsl>", "exec"), env)
    return env


def test_matrix_indexing_is_zero_based():
    env = _run(
        "M := [[10, 20, 30], [40, 50, 60]]\n"
        "a := M₀͵₁          # row 0, col 1 -> 20\n"
        "b := M₁͵₂          # row 1, col 2 -> 60\n"
        "M₀͵₂ := 99         # subscript assignment\n"
        "c := M[0, 2]\n"
        "M[1, 0] := 77       # bracket assignment\n"
        "d := M₁͵₀\n"
        "row := M₀           # single index -> row 0\n"
        "e := row₁\n"
        "L := [7, 8, 9]\n"
        "f := L₀             # plain list, same convention\n"
        "g := M₋₁͵₋₁        # negative wraps like Python -> 60\n"
    )
    assert env["a"] == 20
    assert env["b"] == 60
    assert env["c"] == 99
    assert env["d"] == 77
    assert env["e"] == 20
    assert env["f"] == 7
    assert env["g"] == 60


def test_out_of_range_raises_clear_error():
    try:
        _run("M := [[1, 2], [3, 4]]\nx := M₂͵₀")
    except IndexError as exc:
        assert "valid 0..1" in str(exc)
    else:
        raise AssertionError("expected IndexError for row 2 of a 2-row matrix")


def test_sympy_internals_unaffected():
    env = _run("M := [[1, 2], [3, 4]]")
    M = env["M"]
    assert M.det() == -2
    assert M.T.shape == (2, 2)


if __name__ == "__main__":
    test_matrix_indexing_is_zero_based()
    test_out_of_range_raises_clear_error()
    test_sympy_internals_unaffected()
    print("all matrix indexing tests passed")
