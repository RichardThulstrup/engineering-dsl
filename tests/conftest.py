"""Keep per-cell configuration from leaking between regression tests."""
import os
os.environ.setdefault("SYMBOL_PALETTE_EXE", os.devnull)

import pytest
from utils import circuit_dsl as dsl


@pytest.fixture(autouse=True)
def isolated_dsl_state():
    protected = dsl.PROTECTED_NAMES.copy()
    mode = dsl.get_syntax_mode()
    dsl.clear_protections()
    dsl.set_syntax_mode("python")
    try:
        yield
    finally:
        dsl.PROTECTED_NAMES.clear()
        dsl.PROTECTED_NAMES.update(protected)
        dsl.set_syntax_mode(mode)
