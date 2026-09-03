"""Round-trip and consistency checks for the two dot operators.

``a ‥ b`` (U+2025, the glyph ``Range`` prints with) is a CLOSED INTERVAL;
``a..b`` (two ASCII dots) ENUMERATES the steps between the ends.  This
file pins down:

  * the printout of ``±`` is valid input that rebuilds the same interval;
  * ``..`` between unit-carrying endpoints enumerates instead of crashing,
    and gives the same array as the ``(a..b) unit`` form.

Runs against the real toolkit (forallpeople + sigfig), so it needs the
full install — invoke from anywhere: ``python tests/test_interval_dots.py``.
"""
import os
import sys

os.environ.setdefault("SYMBOL_PALETTE_EXE", os.devnull)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.Engineer as E                       # noqa: E402
from utils.circuit_dsl import transform_source, Range  # noqa: E402
from utils.sigfig import Sig                     # noqa: E402

ns = {k: getattr(E, k) for k in E.__all__}
fails = 0


def run(src):
    """Transform and exec ``_r := <src>``; return the value."""
    exec(transform_source(f"_r := {src}"), ns)
    return ns["_r"]


def check(label, cond, detail=""):
    global fails
    print(f"{'OK  ' if cond else 'FAIL'} {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails += 1


def unwrap(x):
    while isinstance(x, Sig):
        x = x.value
    return x


def _mag(x):
    """SI magnitude of a Physical (``float()`` would give the auto-prefixed
    display value, which makes a 1 nΩ difference look like 1.0)."""
    return x.value if hasattr(x, "value") and hasattr(x, "dimensions") else float(x)


def same_range(a, b, tol=1e-9):
    ra, rb = unwrap(a), unwrap(b)
    if not (isinstance(ra, Range) and isinstance(rb, Range)):
        return False
    return (abs(_mag(ra.low) - _mag(rb.low)) < tol
            and abs(_mag(ra.high) - _mag(rb.high)) < tol)


print("=" * 70)
print("‥ — the printout of ± is valid input and rebuilds the same interval")
print("=" * 70)
for src in ["35°C±90ΔC", "5 ± 2", "100 ± 5 Ω", "4.7 kΩ ± 0.2 kΩ", "±10 °C", "2.5 ± 0.1 ms"]:
    first = run(src)
    text = repr(first)
    second = run(text)
    check(f"{src:<14} → {text}", repr(second) == text and same_range(first, second),
          "" if repr(second) == text else f"re-entered as {second!r}")

print()
print("=" * 70)
print("‥ — direct interval input")
print("=" * 70)
r = run("(3 ‥ 7)")
check("(3 ‥ 7) is a Range 3..7", isinstance(unwrap(r), Range) and unwrap(r).low == 3 and unwrap(r).high == 7, repr(r))
r = run("12 Ω ‥ 15 Ω")
# Interval display resolves the width to two digits (same rule as ``±``:
# ``100 ± 5 Ω`` shows ``(95.0 Ω ‥ 105. Ω)``), hence the ``.0``.
check("12 Ω ‥ 15 Ω keeps units", repr(r) == "(12.0 Ω ‥ 15.0 Ω)", repr(r))
ns["lo"], ns["hi"] = 2, 9
r = run("lo ‥ hi")
check("identifiers as ends", unwrap(r).low == 2 and unwrap(r).high == 9, repr(r))
r = run("(1 ‥ 3) * 2")
check("interval arithmetic", unwrap(r).low == 2 and unwrap(r).high == 6, repr(r))
r = run("(7 ‥ 3)")
check("reversed ends normalise", unwrap(r).low == 3 and unwrap(r).high == 7, repr(r))
r = run("(-55.0 °C ‥ 125. °C)")
check("negative first end", repr(r) == "(-55.0 °C ‥ 125. °C)", repr(r))
r = run("max(1 ‥ 3, 5)")
check("inside a call argument list", unwrap(r) == 5, repr(r))
r = run('"a ‥ b"')
check("string literal is untouched", r == "a ‥ b" or "_interval" not in r, repr(r))

print()
print("=" * 70)
print(".. — enumeration with unit-carrying endpoints")
print("=" * 70)
a = run("(-55.0 °C..125.0 °C)")
b = run("(−55.0..125.0) °C")
check("(-55.0 °C..125.0 °C) matches (-55.0..125.0) °C",
      len(a) == len(b) == 181 and [repr(x) for x in a] == [repr(x) for x in b],
      f"{len(a)} vs {len(b)}; first {a[0]!r} / {b[0]!r}; last {a[-1]!r} / {b[-1]!r}")
a = run("[0 °C..100 °C..25 ΔC]")
check("[0 °C..100 °C..25 ΔC] steps by ΔC", [repr(x) for x in a] == ["0 °C", "25 °C", "50 °C", "75 °C", "100 °C"], repr(a))
a = run("[32 °F..212 °F..90 ΔF]")
check("[32 °F..212 °F..90 ΔF] steps in °F", [repr(x) for x in a] == ["32 °F", "122 °F", "212 °F"], repr(a))
a = run("[1 kΩ..5 kΩ]")
check("[1 kΩ..5 kΩ] steps in the written prefix", [repr(x) for x in a] == ["1 kΩ", "2 kΩ", "3 kΩ", "4 kΩ", "5 kΩ"], repr(a))
a = run("[1.0 mm..2.0 mm..0.25 mm]")
b = run("[1.0..2.0..0.25] mm")
check("[1.0 mm..2.0 mm..0.25 mm] matches [1.0..2.0..0.25] mm",
      [repr(x) for x in a] == [repr(x) for x in b] and len(a) == 5, f"{a!r} vs {b!r}")
a = run("[0.5 V..2.5 V..0.5 V]")
b = run("[0.5..2.5..0.5] V")
check("[0.5 V..2.5 V..0.5 V] matches [0.5..2.5..0.5] V (coarser prefix wins)",
      [repr(x) for x in a] == [repr(x) for x in b] and len(a) == 5, f"{a!r} vs {b!r}")
a = run("[10 V..8 V]")
check("descending [10 V..8 V]", [repr(x) for x in a] == ["10 V", "9 V", "8 V"], repr(a))
a = run("[1 kΩ..5 kΩ] ▶ Ω")
check("enumerated array still converts", len(a) == 5, repr(a))
try:
    run("[1 V..5 A]")
    check("mismatched units raise", False, "no error")
except TypeError as e:
    check("mismatched units raise", "different units" in str(e), str(e))
r = run("[i * 2 for i in 1..3]")
check("bare a..b still enumerates plain numbers", list(r) == [2, 4, 6], repr(r))
r = run("[1..5] mV")
check("[1..5] mV unchanged", len(r) == 5 and repr(r[0]) == "1 mV", repr(r))
r = run("(1, 2, ...)")
check("ellipsis untouched", r == (1, 2, Ellipsis), repr(r))

print()
print("=" * 70)
print("[a..b] unit — bare base units print like prefixed (Sig-wrapped) ones")
print("=" * 70)
a = run("[1..5] V")
check("[1..5] V prints exact", [repr(x) for x in a] == ["1 V", "2 V", "3 V", "4 V", "5 V"], repr(a))
a = run("[10..8] V")
check("[10..8] V prints exact", [repr(x) for x in a] == ["10 V", "9 V", "8 V"], repr(a))
a = run("[1..3] / s")
check("[1..3] / s prints exact", [repr(x) for x in a] == ["1 Hz", "2 Hz", "3 Hz"], repr(a))
a = run("[1..3] · V")
check("[1..3] · V prints exact", [repr(x) for x in a] == ["1 V", "2 V", "3 V"], repr(a))
a = run("[1..3] * 2")
check("[1..3] * 2 unaffected", [repr(x) for x in a] == ["2", "4", "6"], repr(a))
a = run("[1..5]")
check("[1..5] stays a numeric array", a.dtype != object and list(a) == [1, 2, 3, 4, 5], f"{a!r} dtype={a.dtype}")
a = run("[0.5..2.5..0.5] V")
check("[0.5..2.5..0.5] V keeps its precision", [repr(x) for x in a] == ["500 mV", "1.0 V", "1.5 V", "2.0 V", "2.5 V"], repr(a))
a = run("[1, 2, 3] V")
check("[1, 2, 3] V unchanged", [repr(x) for x in a] == ["1 V", "2 V", "3 V"], repr(a))
r = run("Σ([n for n in 1..4])")
check("bare 1..4 still a plain range for loops", r == 10, repr(r))

print()
print("=" * 70)
print("glued units — ``1.0V`` must behave exactly like ``1.0 V`` in every binary rewriter")
print("=" * 70)
for glued, spaced in [
    ("1.0V .. 20.0V", "1.0 V .. 20.0 V"),
    ("1.0V..20.0V", "1.0 V .. 20.0 V"),
    ("[1V..5V..2V]", "[1 V..5 V..2 V]"),
    ("1.0V ‥ 20.0V", "1.0 V ‥ 20.0 V"),
    ("1.0V ± 0.1V", "1.0 V ± 0.1 V"),
    ("4.7kΩ ‖ 4.7kΩ", "4.7 kΩ ‖ 4.7 kΩ"),
    ("-55.0°C..125.0°C", "-55.0 °C .. 125.0 °C"),
    # trailing-dot decimal glued to the unit — used to split into
    # ``110.`` and ``V..200V`` and evaluate to ``110 · [1 V .. 200 V]``
    ("110.V..200V", "110. V .. 200 V"),
    ("110.V ± 5.V", "110. V ± 5. V"),
    ("[10.V..20.V..5.V]", "[10. V..20. V..5. V]"),
]:
    try:
        a, b = run(glued), run(spaced)
        ok = repr(a) == repr(b)
        detail = "" if ok else f"{a!r} vs {b!r}"
    except Exception as e:
        ok, detail = False, f"<{type(e).__name__}: {e}>"
    check(f"{glued:<18} == {spaced}", ok, detail)

print()
print("=" * 70)
print(f"FINAL: {'ALL PASSED' if fails == 0 else f'{fails} FAILED'}")
print("=" * 70)
sys.exit(0 if fails == 0 else 1)
