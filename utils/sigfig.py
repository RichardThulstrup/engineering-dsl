"""
sigfig.py
---------

Significant-figures tracking for the `circuit_dsl` math-like Python.

The idea
========
Every numeric literal that appears in source code carries information about
how precisely the user knows it.  ``11.2340`` is a 6-significant-figure
quantity; ``1.5e3`` is a 2-sig-fig quantity; a bare integer like ``2`` (the
``2`` in ``2*pi*r``) is treated as exact, and so are fundamental constants
the user marks with :func:`exact`.

This module provides a :class:`Sig` wrapper that tracks the number of
significant figures (``sf``) of a value through arithmetic, and formats
the result using exactly that many digits.

Propagation rules (standard engineering conventions):
    multiplication / division / power:  sf_result = min(sf_operands)
    addition / subtraction:             decimal places of result =
                                           min(decimals of operands)
    unary, abs, neg:                    sf preserved
    int exponent on Sig base:           sf preserved

The wrapper is *transparent* to the underlying value type.  Sig wraps
``int``, ``float``, ``complex``, the ``Range`` type from circuit_dsl,
and ``forallpeople.Physical`` — arithmetic delegates to the wrapped value,
so units and intervals are preserved.  The Sig only adds digit-tracking
and a smarter ``__repr__``.

Source transform
================
:func:`wrap_numeric_literals` is meant to be called *last* in your source
transform pipeline (after implicit multiplication, percent rewrite, etc.).
It walks the token stream and replaces every NUMBER token with
``_S(<literal>, <sf>)``.  The string ``_S`` is short and namespaces the
helper so users can still name a variable ``Sig`` if they want.
"""

from __future__ import annotations

import math
import re
from functools import singledispatch
from typing import Any

import token_utils


__all__ = [
    "Sig",
    "exact",
    "measured",
    "sigfigs_of",
    "register_formatter",
    "wrap_numeric_literals",
    "in_units",
    "radix",
    "register_radix",
    "_S",
    "_INF",
    "_R",
    "set_decimal_literals",
    "get_decimal_literals",
    "decimal_literals",
]


_INF = math.inf  # exposed so source-transform output can reference it


def _sci_to_latex(s: str) -> str:
    """Convert a number string in ``e`` notation to LaTeX ``·10^{…}`` form.

    A plain repr like ``5.3e+18`` wrapped in ``$…$`` renders oddly in
    math mode — MathJax reads ``e``, ``+``, ``18`` as separate tokens
    (variable, operator, number) and spaces them out (``5.3e + 18``).
    This rewrites the mantissa/exponent into proper scientific notation:
    ``5.3e+18`` → ``5.3 \\cdot 10^{18}``, ``1.6e-19`` →
    ``1.6 \\cdot 10^{-19}``.  A leading-``1`` mantissa is kept (``1e10``
    → ``1 \\cdot 10^{10}``).  Strings without an exponent are returned
    unchanged, so only sci-notation is affected.
    """
    if not isinstance(s, str):
        return s
    m = re.match(r'^([-+]?[0-9.]+)[eE]([-+]?[0-9]+)$', s.strip())
    if not m:
        return s
    mant, exp = m.group(1), m.group(2)
    # Normalise the exponent: drop a leading ``+`` and leading zeros,
    # keep a ``-`` sign.  ``+18`` → ``18``; ``-09`` → ``-9``.
    neg = exp.startswith("-")
    digits = exp.lstrip("+-").lstrip("0") or "0"
    exp_clean = ("-" if neg else "") + digits
    return rf"{mant} \cdot 10^{{{exp_clean}}}"


# Map of Unicode superscript glyphs to their plain-digit/sign equivalents,
# used to turn a unit label like ``c²`` or ``s⁻¹`` into LaTeX exponents.
_SUPERSCRIPT_MAP = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
    "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁻": "-", "⁺": "+",
}


def _unit_label_to_latex(label: str) -> str:
    """Convert a unit label (``MeV/c²``, ``m·s⁻¹``, ``Ω``) to LaTeX.

    Alphabetic runs are set upright in ``\\mathrm{}``; runs of Unicode
    superscript glyphs become real ``^{…}`` exponents; ``·`` becomes a
    thin-spaced ``\\cdot``; ``/`` is kept as a literal slash (simpler and
    unambiguous for a one-line label than a built-up fraction).  A label
    that doesn't parse cleanly still round-trips as ``\\mathrm{…}`` over
    its safe characters.
    """
    if not isinstance(label, str) or not label:
        return r"\mathrm{}"
    out = []
    i = 0
    n = len(label)
    while i < n:
        ch = label[i]
        if ch in _SUPERSCRIPT_MAP:
            # Gather a run of superscripts → one exponent.
            j = i
            exp = ""
            while j < n and label[j] in _SUPERSCRIPT_MAP:
                exp += _SUPERSCRIPT_MAP[label[j]]
                j += 1
            out.append("^{" + exp + "}")
            i = j
        elif ch == "·":
            out.append(r" \cdot ")
            i += 1
        elif ch == "/":
            out.append("/")
            i += 1
        else:
            # Gather a run of ordinary characters → one \mathrm{}.
            j = i
            run = ""
            while (j < n and label[j] not in _SUPERSCRIPT_MAP
                   and label[j] not in "·/"):
                run += label[j]
                j += 1
            # Escape the few LaTeX-special chars that can appear in a
            # unit string.  Greek glyphs become their LaTeX commands
            # (``kΩ`` → ``k\Omega``, ``μH`` → ``\mu H``) so they typeset
            # exactly as forallpeople's own LaTeX did; a command is
            # followed by a space when a letter comes next, so
            # ``\Omega`` never swallows it.
            run = run.replace("%", r"\%").replace("&", r"\&")
            tex = ""
            for k, c in enumerate(run):
                cmd = _GREEK_GLYPH_TEX.get(c)
                if cmd is None:
                    tex += c
                else:
                    nxt = run[k + 1] if k + 1 < len(run) else ""
                    tex += cmd + (" " if nxt.isalpha() else "")
            out.append(r"\mathrm{" + tex + "}")
            i = j
    return "".join(out)


# Greek letters that appear in unit labels (``Ω``, ``μ``, ``Δ``) and
# their LaTeX commands — used by ``_unit_label_to_latex`` above.
_GREEK_GLYPH_TEX = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\epsilon", "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta",
    "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu",
    "µ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho",
    "σ": r"\sigma", "τ": r"\tau", "υ": r"\upsilon", "φ": r"\phi",
    "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
    "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma", "Φ": r"\Phi",
    "Ψ": r"\Psi", "Ω": r"\Omega",
}


# ---------------------------------------------------------------------------
# Decimal-literal mode
# ---------------------------------------------------------------------------
#
# When this mode is enabled, ``wrap_numeric_literals`` rewrites floating-point
# literals so they evaluate to exact ``sympy.Rational`` values rather than
# IEEE-754 floats.  ``0.1 + 0.2 == 0.3`` then returns True (because
# Rational(1, 10) + Rational(2, 10) really is Rational(3, 10)), at the cost
# of (a) slower arithmetic and (b) results that are sympy expressions rather
# than Python floats.
#
# Scope: only "decimal" literals — those containing ``.`` or ``e``/``E`` —
# get rationalised.  Bare integers (``5``, ``1_000``) are already exact and
# stay as Python ``int``.  Hex/oct/bin literals (``0xff``, ``0b101``) and
# complex literals (``0.5j``) cannot be expressed as Rational and are also
# left alone.  Sig wrapping applies in all cases.
#
# Trade-offs the user should know about:
#   - ``math.sin(_R('0.5'))`` fails — stdlib math takes float, not Rational.
#     Use ``sym.sin`` instead, or convert with ``float(...)``.
#   - Multiplying a rationalised literal by a ``forallpeople.Physical`` falls
#     through the existing sympy×Physical adapter, which converts to float
#     before constructing the Physical.  Exactness is lost at that boundary.
#   - Numpy doesn't understand Rational; ``np.array([_R('0.1'), ...])`` works
#     but yields a dtype=object array with slow operations.

_DECIMAL_LITERAL_MODE = False


def set_decimal_literals(enabled: bool = True) -> None:
    """Globally turn decimal-literal mode on or off.

    Once enabled, every subsequent source transform (every Jupyter cell
    you run, every module imported under the DSL hook) rewrites floating-
    point literals as ``_S(_R('0.1'), 1)`` instead of ``_S(0.1, 1)``.
    Cells transformed *before* the toggle keep whatever wrapping they
    already had — re-import or re-run a cell to pick up a change.

    Idiomatic use: call once at the top of a notebook to opt in for the
    whole session.

    >>> from utils.sigfig import set_decimal_literals
    >>> set_decimal_literals(True)
    >>> # subsequent cells: 0.1 + 0.2 == 0.3 now evaluates to True
    """
    global _DECIMAL_LITERAL_MODE
    _DECIMAL_LITERAL_MODE = bool(enabled)


def get_decimal_literals() -> bool:
    """Return whether decimal-literal mode is currently enabled."""
    return _DECIMAL_LITERAL_MODE


class decimal_literals:
    """Context-manager / decorator form of :func:`set_decimal_literals`.

    Note that this only matters at *source-transform time*, not at runtime
    — entering the ``with`` block doesn't retroactively re-rationalise
    literals already evaluated.  Useful mainly when calling
    ``transform_source`` (or the import hook) from inside the block, or
    when you want a clear opt-in/opt-out fence in scripts that do their
    own source rewriting.

    >>> with decimal_literals():
    ...     transformed = transform_source("x := 0.1 + 0.2")
    """

    def __init__(self, enabled: bool = True) -> None:
        self._target = bool(enabled)
        self._saved: bool | None = None

    def __enter__(self) -> "decimal_literals":
        self._saved = _DECIMAL_LITERAL_MODE
        set_decimal_literals(self._target)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._saved is not None:
            set_decimal_literals(self._saved)


def _R(literal_text: str):
    """Construct a sympy ``Rational`` from a Python numeric literal string.

    The wrapping pass emits calls to this function when decimal-literal
    mode is on.  We accept the *string form* of the literal, not the
    parsed float — passing the float would lose precision before we ever
    get here (``Rational(0.1)`` returns a 50-digit fraction matching the
    binary representation, not ``1/10``).

    PEP 515 underscores are stripped because sympy's parser doesn't accept
    them.  Everything else passes through unchanged: ``Rational`` handles
    plain decimals (``'0.1'``), trailing dots (``'120.'``), and scientific
    notation (``'1.5e-3'``) directly.
    """
    from sympy import Rational
    return Rational(literal_text.replace("_", ""))


def _is_rationalisable_literal(literal: str) -> bool:
    """Return True iff ``literal`` is a real-valued decimal literal that
    can be safely converted to a sympy ``Rational``.

    Integers (``5``, ``1_000``) pass False — they're already exact as
    Python ``int`` and don't need wrapping.  Hex/oct/bin (``0xff``) and
    complex literals (``0.5j``) pass False because ``Rational`` rejects
    them.  Anything containing ``.`` or ``e``/``E`` and lacking those
    disqualifiers passes True.
    """
    s = literal.strip().replace("_", "").lower()
    if not s:
        return False
    s = s.lstrip("+-")
    if s.endswith("j"):
        return False
    if s.startswith(("0x", "0o", "0b")):
        return False
    return ("." in s) or ("e" in s)


# ---------------------------------------------------------------------------
# 1. Determine sig figs from a numeric literal string
# ---------------------------------------------------------------------------

def sigfigs_of_literal(text: str) -> float:
    """Number of significant figures implied by a Python numeric literal.

    Conventions:
        '11.2340'  -> 6        (every digit kept, trailing zeros included)
        '0.01230'  -> 4        (leading zeros are not significant)
        '1.5e3'    -> 2        (mantissa rules)
        '120.'     -> 3        (trailing dot makes trailing zeros count)
        '120'      -> inf      (bare integer = exact: counts, coefficients)
        '0'        -> inf
        '1_000.50' -> 6        (PEP 515 underscores are ignored)
        '3j'       -> inf      (bare complex literal)
        '3.0j'     -> 2

    A bare integer is treated as *exact* on purpose.  In physics-style
    code, integers usually denote either counts (``range(10)``) or
    mathematical constants (``2`` in ``2*pi*r``), not measurements.
    Writing the same value with a trailing dot (``120.``) marks it as
    a measurement with the trailing zero(s) significant — this is the
    textbook sig-fig convention.

    Use :func:`measured` if you want to mark an integer as a measurement
    without altering the literal.
    """
    s = text.strip().replace("_", "")
    if not s:
        return 1
    s = s.lstrip("+-")

    # complex-literal suffix
    if s.endswith(("j", "J")):
        s = s[:-1]
        if not s:
            return _INF

    lower = s.lower()
    if "e" in lower:
        mantissa = lower.split("e", 1)[0]
        return _sigfigs_of_decimal(mantissa)

    if "." in s:
        return _sigfigs_of_decimal(s)

    return _INF  # bare integer literal


def _sigfigs_of_decimal(s: str) -> int:
    """Sig-figs of a decimal mantissa string (no exponent, may end in '.')."""
    if "." not in s:
        stripped = s.lstrip("0")
        return len(stripped) if stripped else 1

    int_part, frac_part = s.split(".", 1)
    int_part = int_part.lstrip("0")
    if int_part:
        # nonzero integer part -> all kept digits are significant
        return len(int_part) + len(frac_part)

    # form is .xxx or 0.xxx
    stripped_frac = frac_part.lstrip("0")
    if not stripped_frac:
        # A pure ZERO literal (``0.00``, ``0.000``, ``.0``).  There are no
        # significant *digits*, but the written decimal places ARE a
        # precision claim — ``0.00`` states "zero to hundredths", the same
        # resolution as ``1.00`` (3 sf) or ``25.00`` (4 sf).  Counting it
        # as a single sig fig under-reports the measurement.  Convention
        # here: a measured zero carries (digits after the point) + 1 sig
        # figs, so ``0.00`` → 3, ``0.000`` → 4 — i.e. it reads with the
        # same decimal resolution as a nonzero value written the same way.
        # A bare ``0`` (no decimals) stays exact (handled by the integer
        # path), and ``0.`` (trailing dot, no fraction) → 1.
        return len(frac_part) + 1 if frac_part else 1
    return len(stripped_frac)


# ---------------------------------------------------------------------------
# 2. The Sig wrapper class
# ---------------------------------------------------------------------------

def _unwrap(x):
    while isinstance(x, Sig):
        x = x.value
    return x


def _sf_of(x) -> float:
    if isinstance(x, Sig):
        return x.sf
    # A ``_DeltaTemp`` (temperature difference) carries its own ``sf`` and
    # isn't a ``Sig``; read it so sig-fig tracking survives Δ arithmetic.
    if type(x).__name__ == "_DeltaTemp":
        return getattr(x, "sf", _INF)
    return _INF


def _format_unit_pref(value, sf, pref):
    """Render ``value`` in the display unit the user wrote it in.

    ``pref`` is a ``_DisplayUnit`` marker (``Nm`` and friends from
    ``extra_units``, soft-coupled by shape: ``.physical`` + ``.label``).
    A torque written ``5.000 Nm`` stays ``5.000 N·m`` on display instead
    of reducing to the dimensionally-identical ``5.000 J``.  Precision
    follows the ``Sig`` rule: a finite sf rounds, an exact literal
    (``5 Nm``, ``22735 inch``) prints in full — ``5 N·m``, ``22735 inch``.

    Returns ``None`` when the preference no longer applies — the value's
    dimensions don't match the preferred unit's (a torque divided by a
    time is a power; it must show ``W``, not ``N·m``).  That dimension
    guard is what makes propagating the tag through arithmetic safe: a
    stale tag can never render a wrong number, only decline, letting the
    caller fall back to the normal reduced-SI display.
    """
    try:
        phys = getattr(pref, "physical", None)
        label = getattr(pref, "label", None)
        if phys is None or not label:
            return None
        ratio = value / phys
        dims = getattr(ratio, "dimensions", None)
        if dims is not None and any(
                getattr(dims, f, 0)
                for f in ("kg", "m", "s", "A", "cd", "K", "mol")):
            return None
        return f"{_format_in_unit(float(ratio), sf)} {label}"
    except Exception:
        return None


def _format_in_unit(mag: float, sf) -> str:
    """Format a magnitude that has already been expressed in a chosen
    unit (a written-unit tag or a ``▶`` target) with the SAME precision
    rule ``_format_sig`` applies to a Physical — so ``22735 mm``,
    ``22735 mm ▶ mm`` and the bare SI form all agree:

      * ``sf`` infinite (exact) → ``.15g``, which prints the value in
        full and hides the float noise of the ``value / unit`` ratio
        (``1 mm`` in ``μm`` is ``1000.0000000000001`` as a float, and
        must read ``1000``);
      * ``sf`` finite → ``#.{sf}g`` with the usual scientific-notation
        expansion, so a significant trailing zero keeps its marker
        (``10. kΩ`` stays ``10.``, exactly as the Physical form does).
    """
    if math.isnan(mag) or math.isinf(mag):
        return repr(mag)
    if math.isinf(sf):
        return _expand_sci(format(mag, ".15g"))
    n = max(int(sf), 1)
    return _expand_sci(format(mag, f"#.{n}g"))


def _is_currency(x) -> bool:
    """Duck-type test for a ``Currency`` (from the soft-dependency
    ``currencies`` module).

    A ``Currency`` is a money amount: it has a numeric ``value`` and a
    string ``code`` (``"DKK"``), and — unlike a forallpeople
    ``Physical`` — it has no ``dimensions``.  Checking the shape rather
    than importing the class keeps ``sigfig`` free of a hard dependency
    on the currency module (currency support is optional).

    Used by :meth:`Sig._binop` to step aside — ``Sig`` arithmetic must
    hand a ``Currency`` operand back to ``Currency``'s own operators so
    the result stays a ``Currency`` and prints as money, not as a
    ``Sig`` in scientific notation.
    """
    return (
        hasattr(x, "value")
        and hasattr(x, "code")
        and isinstance(getattr(x, "code", None), str)
        and not hasattr(x, "dimensions")
    )


# ``datetime`` is always available — a plain ``isinstance`` check is fine
# here (no soft-dependency duck-typing needed, unlike ``Currency``).
import datetime as _datetime


def _is_temporal(x) -> bool:
    """True for a standard-library ``date``, ``datetime``, ``time`` or
    ``timedelta``.

    These types — produced by the DSL's ``"..."ₜᵢₘₑ`` literals via the
    ``chrono`` module's ``iso()`` — carry their own complete and correct
    arithmetic (``date + timedelta`` → ``date``, ``datetime - datetime``
    → ``timedelta``, and so on).  ``Sig`` must not wrap them: a
    ``Sig``-wrapped ``timedelta`` formats through ``Sig``'s numeric
    formatter and prints as a meaningless ``.15g`` number instead of an
    ISO duration / date.

    Used by :meth:`Sig._binop` to step aside (``return NotImplemented``)
    so the temporal type's own operator runs and the result keeps its
    proper date/duration type.

    Note ``datetime`` is a subclass of ``date``, and ``bool``/``int``
    are unrelated — so a single ``isinstance`` against the four classes
    is exact.
    """
    return isinstance(
        x,
        (_datetime.date, _datetime.time, _datetime.timedelta),
    )
    # (``datetime`` is caught by ``date`` — it subclasses it.)


def _magnitude(value) -> float:
    """Return the SI-base magnitude of ``value`` as a float.

    For ``forallpeople.Physical`` this returns ``.value`` (the base-SI
    magnitude — e.g. ``3600.0`` for ``3.6 ks``) NOT ``float(value)``
    (which returns the auto-prefix-scaled display magnitude, ``3.6``).

    For everything else, returns ``float(value)``.

    For ``Range`` intervals, returns the midpoint (using its own
    ``_magnitude`` recursively so a Range of Physicals still gives a
    proper SI magnitude).  Without this, addition of Sig(Range)
    values silently lost sf because ``_decimals`` couldn't compute
    a characteristic scale and fell through to ``inf``.

    Why this matters: the sf-from-decimals math in ``_addsub_sf``
    needs the *true* magnitude to compute the right number of decimal
    places.  ``3.6 ks + 30.0 s`` should reduce to 5 sf in the sum
    (last significant digit at the 0.1 s place; sum is 7170.0 s; sf
    is 5).  Without magnitude-correction, ``float(3.6 ks) = 3.6``
    treats the 3.6 as having decimal places at 0.1, which combined
    with ``30.0``'s decimal place gives the wrong rounding by orders
    of magnitude.
    """
    if hasattr(value, "value") and hasattr(value, "dimensions"):
        # Duck-type detection of forallpeople.Physical — avoid importing
        # forallpeople here so this module stays a soft dependency.
        try:
            return float(value.value)
        except (TypeError, ValueError):
            pass
    # Range — uses the midpoint as a characteristic scale.  Recurse
    # to handle Range-of-Physical correctly.
    if hasattr(value, "low") and hasattr(value, "high"):
        try:
            mid = (value.low + value.high) / 2
            return _magnitude(mid)
        except (TypeError, ValueError):
            pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return _INF


def _decimals(value, sf: float) -> float:
    """Number of decimal places implied by 'sf' sig figs of 'value'."""
    if math.isinf(sf):
        return _INF
    av = abs(_magnitude(value))
    if not math.isfinite(av) or av == 0:
        return _INF
    return sf - 1 - math.floor(math.log10(av))


def _sf_from_decimals(value, decimals: float) -> float:
    if math.isinf(decimals):
        return _INF
    av = abs(_magnitude(value))
    if not math.isfinite(av) or av == 0:
        return max(decimals + 1, 1)
    return max(decimals + 1 + math.floor(math.log10(av)), 1)


class Sig:
    """A numeric value tagged with a significant-figures count.

    ``Sig`` wraps any numeric-like value (int, float, complex, ``Range``,
    ``forallpeople.Physical``, …).  Arithmetic propagates ``sf`` by the
    standard rules, and ``repr(Sig)`` formats the value with that many
    significant digits.

    Use :func:`exact` for fundamental constants and :func:`measured`
    for explicitly-precision-tagged measurements.
    """

    __slots__ = ("value", "sf", "_stripped_unit", "_temp_scale",
                 "_unit_pref")

    # ---------------------------------------------------------------------
    # Sympy operator-dispatch priority.
    # ---------------------------------------------------------------------
    # Sympy's ``Expr`` class sets ``_op_priority = 10.01``; its arithmetic
    # methods are decorated with ``call_highest_priority`` which checks
    # whether the *other* operand has a higher ``_op_priority`` and, if
    # so, defers to its ``__rop__`` instead of running sympy's own.
    #
    # By setting ``Sig._op_priority`` higher, we ensure that ``π * Sig(2)``
    # routes to ``Sig.__rmul__`` rather than letting sympy absorb the Sig
    # via ``_sympy_``.  Combined with ``Sig.__mul__`` already winning the
    # left-operand case (Sig is on the left), this means *every* sympy×Sig
    # arithmetic operation produces a Sig-wrapped result with sf preserved.
    #
    # ``_sympy_`` then only fires for explicit sympy-side conversions —
    # ``sympify(s)``, ``simplify(s)``, ``diff(s, x)``, ``s.subs(...)`` —
    # which are all cases where the user has explicitly asked for symbolic
    # processing and accepts losing the Sig wrapper at that boundary.
    _op_priority = 11.0

    def __init__(self, value: Any, sf: float = _INF):
        # Auto-flatten nested Sigs and take the smaller sf
        if isinstance(value, Sig):
            sf = min(sf, value.sf)
            value = value.value
        self.value = value
        self.sf = sf

    # ---- constructors used by the source transform / user code -----------

    @classmethod
    def lit(cls, value, sf):
        return cls(value, sf)

    # ---- conversions to plain python values ------------------------------

    def __index__(self):
        # Required so a Sig int can be used as a slice/index/range arg.
        v = self.value
        try:
            return int(v)
        except TypeError:
            raise TypeError(
                f"Sig wrapping {type(v).__name__} cannot be used as an index"
            )

    def __int__(self):
        return int(self.value)

    def __float__(self):
        return float(self.value)

    def __complex__(self):
        return complex(self.value)

    def __bool__(self):
        return bool(self.value)

    def _sympy_(self):
        """Conversion protocol used by ``sympify``.

        Called when sympy itself needs to absorb a Sig — typically by
        explicit user request (``simplify(s)``, ``diff(s, x)``,
        ``s.subs(...)``, etc.).  Arithmetic operations *don't* normally
        get here because ``Sig._op_priority`` ensures sympy defers to
        ``Sig.__rop__`` instead; see the class-level note.

        Conversion strategy by the wrapped value's type:

          - Already a sympy ``Basic`` (e.g. ``Sig(value=sin(x)**2, sf=∞)``
            after a ``Sig**Sig`` or ``Sig*sympy`` chain): return it
            unchanged.  This is the case that lets
            ``simplify(Sig(sin(x)**2 + cos(x)**2, ∞))`` resolve to ``1``.

          - Plain ``int``/``float``/``complex``: return the matching sympy
            type.  Whole-valued floats become ``Integer`` so that
            ``Sig(2.0, ∞)`` used as an exponent triggers nice sympy
            behaviour like binomial expansion.

          - ``forallpeople.Physical`` (or anything else sympy can't natively
            handle): defer to ``sym.sympify``, which usually raises
            ``SympifyError`` — that's the right signal to upstream
            sympy operations that the value can't be processed
            symbolically.  Common case: ``simplify(Sig(value=Physical))``
            fails, which is correct behaviour (there's nothing symbolic
            to simplify in a Physical).

        The sf annotation is intentionally discarded.  Sympy has no place
        for sig-fig metadata, and operations that explicitly invoke sympy
        (``simplify`` etc.) are asking for a pure symbolic result.  If you
        need sf-tracked numeric output, do the symbolic work first then
        re-apply Sigs at the numeric step::

            symbolic = simplify(my_expr)
            numeric  = symbolic.subs({x: measured_value})
        """
        # Local import — sympy is only a soft dependency.
        import sympy as _sym

        v = self.value

        # Already a sympy object — pass through unchanged.  This is the
        # common path now that arithmetic wraps results: a Sig of value
        # ``sin(x)**2`` is what we get from ``sin(x) ** Sig(2, ∞)``.
        if isinstance(v, _sym.Basic):
            return v
        if hasattr(v, "_sympy_"):
            # Other sympy-aware objects — let them convert themselves.
            return v._sympy_()

        if isinstance(v, bool):
            return _sym.Integer(1) if v else _sym.Integer(0)

        if isinstance(v, int):
            return _sym.Integer(v)

        if isinstance(v, float):
            # Whole-valued floats convert to integers — common case
            # for literals like ``2.0`` that came in as ``2``.
            if v.is_integer():
                return _sym.Integer(int(v))
            return _sym.Float(v)

        if isinstance(v, complex):
            return _sym.Float(v.real) + _sym.Float(v.imag) * _sym.I

        # Last-ditch: defer to sympy.  For things like ``Physical`` whose
        # own ``_sympy_`` raises, this propagates the SympifyError up to
        # the calling sympy operation — which is correct behaviour.
        return _sym.sympify(v)

    def __array__(self, dtype=None):
        """NumPy array protocol.

        Behaviour depends on what ``self.value`` is:

        - **Scalar value** (the common case — a Sig wraps a single number,
          a sympy expr, or a forallpeople.Physical): return a 0-d object
          array containing ``self``.  This is what makes
          ``np.array([Sig(34, 3), Sig(35, 4)])`` produce an object-dtype
          array of ``Sig`` instances rather than a numeric array with the
          wrapping discarded.  Subsequent operations (``arr * mV``,
          ``np.sin(arr)``, etc.) then flow through ``Sig``'s arithmetic
          and ufunc methods, so sf propagates element-wise.

        - **Array value** (less common but reachable: a Sig wraps a
          numpy ndarray because some upstream operation produced one):
          return ``self.value`` directly so consumers like matplotlib
          see the actual N-element array.  Wrapping it inside a 0-d
          object array would give consumers shape ``()`` containing a
          single object that *happens* to be an array — which is what
          caused matplotlib's ``shape (1,) vs shape (50,)`` error.  The
          sf is lost on this path; if you need per-element sf tracking,
          start with an array of Sigs rather than a Sig of an array.

        If a numeric dtype is requested explicitly (``np.asarray(s, float)``,
        ``np.asarray(s, np.float64)``), we honour it and drop the sf — the
        caller has asked for a real numeric value.
        """
        import numpy as _np
        if dtype is not None and dtype != object and not _np.dtype(dtype).hasobject:
            return _np.asarray(self.value, dtype=dtype)
        # If the wrapped value is already an array, surface it directly.
        # Detect by ``ndim > 0`` rather than ``isinstance(_, ndarray)`` so
        # this also handles array-like values that quack like ndarrays
        # (pandas Series, dask arrays, etc.).
        if hasattr(self.value, "ndim") and getattr(self.value, "ndim", 0) > 0:
            return _np.asarray(self.value)
        a = _np.empty((), dtype=object)
        a[()] = self
        return a

    # ---- arithmetic ------------------------------------------------------

    def _binop(self, other, op, sf_rule, swap=False, op_name=None):
        # Engineering convention: ``Sig`` wraps the result of EVERY binary
        # operation, including those involving sympy expressions.  When the
        # other operand is ``sym.pi``, ``sym.E``, a derived expression like
        # ``2*sym.pi``, or any sympy ``Basic``, we still go through the
        # numeric path here — ``op(self.value, other_value)`` produces a
        # sympy expression (or a unit-bearing ``Physical`` if the sympy×
        # Physical adapter has already collapsed it), and we wrap that as
        # the new Sig's value.
        #
        # Why this matters: ``2π·100 m`` should produce a Sig-wrapped
        # Physical so sf-tracking and unit display work the way an
        # engineer expects.  The earlier "step aside for sympy" policy
        # preserved symbolic structure but at the cost of dropping the
        # Sig wrapper, which then dropped sf-tracking, which then
        # produced verbose-precision output and inconsistent unit
        # rendering.  See the discussion of the asymmetric Physical/Sig
        # division bug for the cascade this caused.
        #
        # Cost: sympy operations that try to sympify a ``Sig`` (e.g.
        # ``sym.sin(Sig(0.5))``, ``sym.diff(Sig(2)*x**2, x)``) now fail
        # because ``_sympy_`` raises ``SympifyError``.  For pure-symbolic
        # work, unwrap first via ``float(s)`` or ``s.value``.
        # Currency step-aside.  ``Currency`` (from the soft-dependency
        # ``currencies`` module) is a money amount with a currency code —
        # ``10920 DKK``.  It is NOT a physical quantity and NOT a plain
        # number, and its own ``__repr__`` already formats money the way
        # an engineer expects (``10,920.00 DKK`` — comma-grouped, two
        # decimals).  If ``_binop`` handled it like any other operand it
        # would unwrap the ``Currency`` to its bare ``.value`` (via
        # ``float()``), compute, and rewrap as a ``Sig`` — losing the
        # ``Currency`` type, so the result would print in ``Sig``'s
        # scientific-notation style (``1.092e+04 DKK``) instead.
        #
        # So when the other operand is a ``Currency``, step aside:
        # ``return NotImplemented`` hands the operation to ``Currency``'s
        # own reflected operator (``__rmul__`` / ``__radd__`` / …), which
        # produces a proper ``Currency``.  This is the same soft-coupling
        # pattern used elsewhere — detected by duck-typing (``.value`` +
        # ``.code``, and crucially NO ``.dimensions``, which distinguishes
        # it from a forallpeople ``Physical``) so ``sigfig`` keeps no
        # hard import of the currency module.
        if _is_currency(other):
            return NotImplemented

        # Temporal interaction — ``date`` / ``datetime`` / ``time`` /
        # ``timedelta`` (produced by the DSL's ``"..."ₜᵢₘₑ`` literals
        # through ``chrono.iso()``).
        #
        # The problem: if ``_binop`` treated a ``timedelta`` like any
        # operand it would absorb it into a ``Sig`` — ``10 "PT1H30M"ₜᵢₘₑ``
        # becomes ``Sig(10) * timedelta`` and the result is a ``Sig``
        # *wrapping* a ``timedelta``, which then prints through ``Sig``'s
        # numeric formatter as a meaningless ``.15g``.  It is also
        # intermittent: a temporal value that never meets a ``Sig``
        # stays a clean ``date``, so the same notebook shows a correct
        # date in one cell and ``.15g`` in another.
        #
        # Stepping aside (``return NotImplemented``) is not enough on its
        # own: ``timedelta`` only multiplies by a genuine ``int`` /
        # ``float`` and would reject the ``Sig`` too, raising TypeError.
        # So instead we UNWRAP — strip ``self`` to its plain numeric
        # value and apply the operator directly, letting Python's own
        # ``datetime`` arithmetic produce the result.  ``Sig(10) *
        # timedelta`` → ``10 * timedelta`` → a proper scaled
        # ``timedelta``; ``date + timedelta`` stays a ``date``.  The
        # result is a bare temporal object — never ``Sig``-wrapped — so
        # it always prints as a date / duration.
        if _is_temporal(other):
            self_plain = _unwrap(self)
            if swap:
                return op(other, self_plain)
            return op(self_plain, other)

        # Δ-temperature UNIT marker (``ΔC`` / ``ΔK`` / ``ΔF``).  ``45 ΔC``
        # arrives as ``Sig(45) * deltaC``; we step aside so ``_DeltaUnit``'s
        # own reflected operator runs — ``__rmul__`` builds a ``_DeltaTemp``
        # (a temperature difference that displays as ``45 ΔC``), and
        # ``__rtruediv__`` (for ``x / ΔF`` coefficients) keeps the plain
        # kelvin-Physical behaviour.  Returning ``NotImplemented`` hands
        # the op to ``_DeltaUnit``; ``self`` (the ``Sig``) is passed to its
        # reflected method, which unwraps it.
        if type(other).__name__ == "_DeltaUnit":
            return NotImplemented

        # Display-preserving unit marker (``Nm``, ``inch``, ``lbf`` …
        # from ``extra_units``).  Same step-aside as ``_DeltaUnit``:
        # ``5 Nm`` arrives as ``Sig(5) * Nm``; handing the op to the
        # marker's reflected operator builds a ``_unit_pref``-tagged
        # ``Sig`` (and composes tags, so ``20 ozf inch`` labels as
        # ``ozf·inch``) instead of a Physical that would display as
        # the reduced SI form.
        if type(other).__name__ == "_DisplayUnit":
            return NotImplemented

        # A ready-made ``_DeltaTemp`` operand (e.g. ``2 * ΔT`` where ΔT is
        # already a difference) — step aside to its own arithmetic so the
        # Δ type and unit propagate per its truth-table.
        if type(other).__name__ == "_DeltaTemp":
            return NotImplemented

        ov = _unwrap(other)
        os = _sf_of(other)

        # Sympy / unit-bearing interaction.  If one side is a dimensionless
        # sympy expression and the other is a unit-bearing Physical,
        # forallpeople's arithmetic refuses to combine them — Physicals
        # only operate with plain numbers or other Physicals.  The user
        # almost always wants the Physical's *display magnitude* (646.0
        # for ``646 nm``) treated as a number in the data's implicit
        # unit, so the resulting symbolic expression carries a consistent
        # scale.  Without this fix, ``λ := 646 nm`` followed by a
        # symbolic ``(x - λ)`` raises mid-construction.
        #
        # We check after unwrapping (``ov``) and against ``self.value``
        # so both directions are caught:
        #   Sig(sympy) / Sig(Physical)    — sympy in self, Physical in other
        #   Sig(Physical) / sympy_expr     — Physical in self, sympy in other
        # Either way, we strip the Physical to its display magnitude
        # before the operation.
        #
        # When stripping happens, we also record the unit symbol on the
        # resulting Sig as ``_stripped_unit`` — a hint that the
        # arithmetic was done in this unit's implicit scale.  Plotting
        # uses that to label the y-axis: ``V_0 · exp(-t/τ)`` carries
        # ``_stripped_unit="V"`` because V_0 was the stripped Physical,
        # and the result's y-axis says ``[V]`` even though no Physical
        # remains in the sympy expression.
        def _is_sympy(v):
            """``True`` if ``v`` is a sympy expression with a free
            variable.  We deliberately require ``free_symbols`` to be
            non-empty: a bare numeric sympy constant like ``pi`` or
            ``E`` is mathematically just a number, and combining it
            with a Physical should preserve units (``π · εₒ · r²``
            keeps farad-meter units).  Only sympy expressions with
            free symbols trigger the unit-stripping branch — those
            are the ``lambdify``-target cases where the result will
            be evaluated numerically over a sweep and the unit hint
            survives as a separate ``_stripped_unit`` attribute on the
            Sig wrapper.
            """
            return (
                hasattr(v, "free_symbols")
                and hasattr(v, "subs")
                and bool(v.free_symbols)
            )

        def _is_physical(v):
            return hasattr(v, "value") and hasattr(v, "dimensions")

        def _unit_of_physical(p):
            # Extract the unit-symbol portion of the Physical's str form
            # (``"12.000 V"`` → ``"V"``; ``"3.600 ks"`` → ``"ks"``).
            # The display string is whitespace-separated value-then-unit,
            # so the last token is the symbol.  Falls back to None on
            # any unexpected string shape.
            try:
                parts = str(p).strip().split()
                if len(parts) >= 2:
                    return parts[-1]
            except Exception:
                pass
            return None

        # Self's wrapped value is Physical, other (or other's wrapped
        # value, ``ov``) is sympy → strip self's Physical.
        if _is_physical(self.value) and _is_sympy(ov):
            unit_str = _unit_of_physical(self.value)
            stripped_self = float(self.value)
            if swap:
                new_value = op(ov, stripped_self)
            else:
                new_value = op(stripped_self, ov)
            result = Sig(new_value, self.sf)
            # If other was a Sig carrying a stripped-unit hint, propagate
            # if compatible (matching unit on both sides).  Otherwise
            # the freshly-stripped Physical's unit wins.
            existing_other = getattr(other, "_stripped_unit", None)
            if unit_str and (existing_other is None or existing_other == unit_str):
                result._stripped_unit = unit_str
            return result

        # Self's wrapped value is sympy, other's wrapped value is Physical
        # → strip other's Physical.
        if _is_sympy(self.value) and _is_physical(ov):
            unit_str = _unit_of_physical(ov)
            stripped_other = float(ov)
            if swap:
                new_value = op(stripped_other, self.value)
            else:
                new_value = op(self.value, stripped_other)
            result = Sig(new_value, self.sf)
            existing_self = getattr(self, "_stripped_unit", None)
            if unit_str and (existing_self is None or existing_self == unit_str):
                result._stripped_unit = unit_str
            return result

        if swap:
            new_value = op(ov, self.value)
            new_sf = sf_rule(ov, self.value, os, self.sf, new_value)
        else:
            new_value = op(self.value, ov)
            new_sf = sf_rule(self.value, ov, self.sf, os, new_value)

        # Stage 3 — ``A − A → Δ``.  Subtracting two ABSOLUTE temperatures
        # yields a temperature *difference*, not another absolute point:
        # ``100 °C − 10 °C`` is a 90-degree span, a ``_DeltaTemp``, not
        # ``90 K`` absolute.  Fire ONLY when the op is subtraction AND both
        # operands are pure temperatures (so ordinary numeric/​unit
        # subtraction is untouched).  Result unit: ``ΔC`` when both inputs
        # were written °C, ``ΔK`` when both K; mixed → ``ΔK`` (SI default).
        # We can't see the written scale from the kelvin-stored value, so
        # the scale is inferred from a per-operand display-scale hint when
        # present (set by the °C/°F input constructors); absent a hint we
        # default to ΔK.
        if (op_name == "sub"
                and _is_pure_temperature(self.value)
                and _is_pure_temperature(ov)):
            try:
                kelvin_span = float(new_value)
                unit = _delta_unit_for_pair(self, other)
                return _DeltaTemp(kelvin_span, unit, new_sf)
            except Exception:
                pass  # fall through to the normal Sig result

        result = Sig(new_value, new_sf)
        # Propagate _stripped_unit through regular arithmetic too —
        # ``Sig(sympy, _stripped_unit="V") + Sig(sympy)`` (both already
        # stripped) should keep the V hint.  Conservative rule: if
        # both sides have a hint, they must match; if exactly one
        # side has a hint, propagate it.  Mismatches → drop the hint
        # (we don't try to combine units across types).  For *,/ this
        # can be wrong (V * V → V², not V), but the alternative is
        # real dimensional analysis; the user can override with
        # ``ylabel=`` for the few mixed cases.
        existing_self = getattr(self, "_stripped_unit", None)
        existing_other = getattr(other, "_stripped_unit", None)
        if existing_self and existing_other:
            if existing_self == existing_other:
                result._stripped_unit = existing_self
        elif existing_self:
            result._stripped_unit = existing_self
        elif existing_other:
            result._stripped_unit = existing_other
        # Propagate the display-unit preference (``5 Nm`` → ``_unit_pref``).
        # Propagating is safe because the tag is only a display hint:
        # ``_format_unit_pref`` re-checks dimensions at render time, so a
        # torque divided by a time drops its ``N·m`` tag automatically
        # and shows ``W``.  A wrongly-kept tag can never render a wrong
        # number.  Which side's tag survives depends on the operator:
        #
        #   * ADDITIVE (+/−): the LEFT operand's written unit dominates —
        #     ``5 inch + 2 mm`` reads in inches, ``2 mm + 5 inch`` stays
        #     SI.  A right-side tag never hijacks the display; the unit
        #     you led with is the unit you think in.  (``swap`` means the
        #     reflected operator ran, so the left operand is ``other``.)
        #   * MULTIPLICATIVE (*, /, **): either side's tag survives —
        #     ``2 · M`` and ``M · 2`` are both still torques — with the
        #     conservative both-sides rule: matching labels keep the tag,
        #     mismatched labels drop it.
        pref_self = getattr(self, "_unit_pref", None)
        pref_other = getattr(other, "_unit_pref", None)
        if sf_rule is _addsub_sf:
            pref = pref_other if swap else pref_self
        elif pref_self is not None and pref_other is not None:
            pref = pref_self if pref_self.label == pref_other.label else None
        else:
            pref = pref_self if pref_self is not None else pref_other
        if pref is not None:
            try:
                result._unit_pref = pref
            except Exception:
                pass
        return result

    def __add__(self, other):
        return self._binop(other, lambda a, b: a + b, _addsub_sf)

    def __radd__(self, other):
        return self._binop(other, lambda a, b: a + b, _addsub_sf, swap=True)

    def __sub__(self, other):
        return self._binop(other, lambda a, b: a - b, _addsub_sf,
                           op_name="sub")

    def __rsub__(self, other):
        return self._binop(other, lambda a, b: a - b, _addsub_sf, swap=True,
                           op_name="sub")

    def __mul__(self, other):
        return self._binop(other, lambda a, b: a * b, _muldiv_sf)

    def __rmul__(self, other):
        return self._binop(other, lambda a, b: a * b, _muldiv_sf, swap=True)

    def __truediv__(self, other):
        return self._binop(other, lambda a, b: a / b, _muldiv_sf)

    def __rtruediv__(self, other):
        return self._binop(other, lambda a, b: a / b, _muldiv_sf, swap=True)

    def __floordiv__(self, other):
        return self._binop(other, lambda a, b: a // b, _muldiv_sf)

    def __rfloordiv__(self, other):
        return self._binop(other, lambda a, b: a // b, _muldiv_sf, swap=True)

    def __mod__(self, other):
        return self._binop(other, lambda a, b: a % b, _muldiv_sf)

    def __rmod__(self, other):
        return self._binop(other, lambda a, b: a % b, _muldiv_sf, swap=True)

    def __pow__(self, other):
        return self._binop(other, lambda a, b: a ** b, _pow_sf)

    def __rpow__(self, other):
        return self._binop(other, lambda a, b: a ** b, _pow_sf, swap=True)

    def _copy_display_hints(self, result):
        """Carry the display hints a unary op preserves (``-τ`` is still
        a torque; ``abs`` of a stripped-unit value keeps its unit)."""
        if hasattr(self, "_stripped_unit"):
            result._stripped_unit = self._stripped_unit
        pref = getattr(self, "_unit_pref", None)
        if pref is not None:
            try:
                result._unit_pref = pref
            except Exception:
                pass
        return result

    def __neg__(self):
        return self._copy_display_hints(Sig(-self.value, self.sf))

    def __pos__(self):
        return self._copy_display_hints(Sig(+self.value, self.sf))

    def __abs__(self):
        return self._copy_display_hints(Sig(abs(self.value), self.sf))

    # ---- rounding protocol ---------------------------------------------
    # ``round(x, n)`` is the everyday "give me two decimals" — it used to
    # raise ``TypeError: type Sig doesn't define __round__``.  The result
    # stays a ``Sig`` so units survive (``round(1.2345 V, 2)`` → ``1.23 V``)
    # and the sf is capped at what the rounded digits can claim.  A
    # ``Physical`` has no ``__round__`` of its own, so round its SI
    # magnitude and rebuild through the unit.  ``math.floor/ceil/trunc``
    # return plain ints, as Python's protocol requires.
    def __round__(self, ndigits=None):
        import math as _m
        v = self.value
        if hasattr(v, "value") and hasattr(v, "dimensions"):      # Physical
            mag = float(v.value)
            r = round(mag, ndigits)
            new = v * (r / mag) if mag else v
        else:
            r = round(v, ndigits)
            new = r
            mag = float(v) if not isinstance(v, complex) else abs(v)
        if ndigits is None:
            sf = self.sf
        else:
            # sf implied by ``ndigits`` decimals at this magnitude.
            try:
                order = _m.floor(_m.log10(abs(float(r)))) if float(r) else 0
                sf = min(self.sf, max(1, order + 1 + ndigits))
            except (ValueError, OverflowError, TypeError):
                sf = self.sf
        return self._copy_display_hints(Sig(new, sf))

    def __floor__(self):
        import math as _m
        return _m.floor(self.value)

    def __ceil__(self):
        import math as _m
        return _m.ceil(self.value)

    def __trunc__(self):
        import math as _m
        return _m.trunc(self.value)

    def __contains__(self, item):
        # ``102 Ω in (95 Ω ‥ 105 Ω)`` — the interval sits inside the Sig.
        return item in self.value

    def __divmod__(self, other):
        return (self // other, self % other)

    def __rdivmod__(self, other):
        return (other // self, other % self)

    # ---- bitwise & shift (integer-only) ----------------------------------
    # The DSL hands numeric literals in as ``Sig``, so ``data₁ << 2`` arrives
    # as ``<Integer> << Sig(2)``; without these, the shift amount being a
    # ``Sig`` makes the operation fail (``Sig`` isn't an ``int``).  Each
    # operator coerces both sides to a plain ``int`` and returns a plain
    # ``int`` — bit operations have no meaningful significant-figure count,
    # so the ``Sig`` precision wrapper is intentionally dropped.  Mirrors
    # ``&`` / ``|`` / ``^`` so the whole integer-bit-twiddling family works
    # whether a ``Sig`` is on the left or the right.
    @staticmethod
    def _as_int(o):
        if isinstance(o, Sig):
            o = o.value
        return int(o)

    def __and__(self, o):      return self._as_int(self) & self._as_int(o)
    def __rand__(self, o):     return self._as_int(o) & self._as_int(self)
    def __or__(self, o):       return self._as_int(self) | self._as_int(o)
    def __ror__(self, o):      return self._as_int(o) | self._as_int(self)
    def __xor__(self, o):      return self._as_int(self) ^ self._as_int(o)
    def __rxor__(self, o):     return self._as_int(o) ^ self._as_int(self)
    def __lshift__(self, o):   return self._as_int(self) << self._as_int(o)
    def __rlshift__(self, o):  return self._as_int(o) << self._as_int(self)
    def __rshift__(self, o):   return self._as_int(self) >> self._as_int(o)
    def __rrshift__(self, o):  return self._as_int(o) >> self._as_int(self)
    def __invert__(self):      return ~self._as_int(self)

    # ---- numpy ufunc dispatch (object-dtype arrays) ----------------------
    # When numpy applies a ufunc like ``np.sin`` to an object-dtype array of
    # Sig instances, it iterates and calls a method of the same name on each
    # element.  We provide those methods so that:
    #   * ``np.sin(arr)``  works element-wise on Sig arrays
    #   * sf is preserved (smooth functions of one variable preserve sf)
    #   * the result is a Sig — which goes back into another object array
    # This means the matplotlib pattern  ``y = np.sin(np.linspace(0, 10, 500))``
    # still works under the DSL without losing sf along the way.
    def _smooth_apply(self, fn):
        return Sig(fn(float(self.value)), self.sf)

    # math has these by these exact names:
    def sin(self):     import math as _m; return self._smooth_apply(_m.sin)
    def cos(self):     import math as _m; return self._smooth_apply(_m.cos)
    def tan(self):     import math as _m; return self._smooth_apply(_m.tan)
    def sinh(self):    import math as _m; return self._smooth_apply(_m.sinh)
    def cosh(self):    import math as _m; return self._smooth_apply(_m.cosh)
    def tanh(self):    import math as _m; return self._smooth_apply(_m.tanh)
    def exp(self):     import math as _m; return self._smooth_apply(_m.exp)
    def log(self):     import math as _m; return self._smooth_apply(_m.log)
    def log2(self):    import math as _m; return self._smooth_apply(_m.log2)
    def log10(self):   import math as _m; return self._smooth_apply(_m.log10)
    def sqrt(self):    import math as _m; return self._smooth_apply(_m.sqrt)
    # numpy's inverse-trig ufuncs are arcsin/arccos/arctan, but the method
    # numpy looks up on objects is the same name as the ufunc.
    def arcsin(self):  import math as _m; return self._smooth_apply(_m.asin)
    def arccos(self):  import math as _m; return self._smooth_apply(_m.acos)
    def arctan(self):  import math as _m; return self._smooth_apply(_m.atan)
    def arcsinh(self): import math as _m; return self._smooth_apply(_m.asinh)
    def arccosh(self): import math as _m; return self._smooth_apply(_m.acosh)
    def arctanh(self): import math as _m; return self._smooth_apply(_m.atanh)
    def log1p(self):   import math as _m; return self._smooth_apply(_m.log1p)
    def expm1(self):   import math as _m; return self._smooth_apply(_m.expm1)
    def exp2(self):    return self._smooth_apply(lambda v: 2.0 ** v)
    def cbrt(self):    import math as _m; return self._smooth_apply(_m.cbrt)
    def square(self):  return self._smooth_apply(lambda v: v * v)
    def degrees(self): import math as _m; return self._smooth_apply(_m.degrees)
    def radians(self): import math as _m; return self._smooth_apply(_m.radians)
    def deg2rad(self): import math as _m; return self._smooth_apply(_m.radians)
    def rad2deg(self): import math as _m; return self._smooth_apply(_m.degrees)
    # Rounding / sign ufuncs — exact results, sf kept as a formality.
    def floor(self):   import math as _m; return Sig(_m.floor(self.value), self.sf)
    def ceil(self):    import math as _m; return Sig(_m.ceil(self.value), self.sf)
    def trunc(self):   import math as _m; return Sig(_m.trunc(self.value), self.sf)
    def rint(self):    return Sig(round(self.value), self.sf)
    def sign(self):
        v = self.value
        return Sig((v > 0) - (v < 0), _INF)
    def absolute(self): return abs(self)
    def fabs(self):     return abs(self)
    def conjugate(self): return Sig(self.value.conjugate() if hasattr(self.value, "conjugate") else self.value, self.sf)
    def isnan(self):   import math as _m; return _m.isnan(float(self.value))
    def isinf(self):   import math as _m; return _m.isinf(float(self.value))
    def isfinite(self): import math as _m; return _m.isfinite(float(self.value))

    # ---- comparisons (return plain bool) ---------------------------------

    def __eq__(self, other): return self.value == _unwrap(other)
    def __ne__(self, other): return self.value != _unwrap(other)
    def __lt__(self, other): return self.value < _unwrap(other)
    def __le__(self, other): return self.value <= _unwrap(other)
    def __gt__(self, other): return self.value > _unwrap(other)
    def __ge__(self, other): return self.value >= _unwrap(other)

    def __hash__(self):
        return hash(self.value)

    # ---- iteration / container protocols (delegated) ---------------------

    def __iter__(self):
        return iter(self.value)

    def __len__(self):
        return len(self.value)

    def __getitem__(self, key):
        # If somebody indexes into a Sig (e.g. Sig wraps a list), delegate
        # without losing sf.  Useful when units multiplied in to lists.
        return Sig(self.value[key], self.sf)

    # ---- formatting ------------------------------------------------------

    def _formatted(self):
        """Text form, honouring a remembered temperature scale.

        An absolute temperature written in °C/°F/°R carries a
        ``_temp_scale`` hint (set by the ``from_deg*`` constructors); when
        present we render in that scale (offset-correct) so ``100 °C``
        displays as ``100 °C`` and ``295.15 K`` (no hint) stays ``K``.  A
        temperature *interval* (``25 °C ± 10 ΔC``) carries the same hint
        and renders both endpoints in that scale."""
        scale = getattr(self, "_temp_scale", None)
        if scale and scale != "K":
            # Temperature interval (Range of two temperatures).
            if type(self.value).__name__ == "Range":
                try:
                    from .circuit_dsl import _format_range
                    return _format_range(self.value, self.sf, scale)
                except Exception:
                    pass
            try:
                if _is_pure_temperature(self.value):
                    return _format_temperature(self.value, self.sf, scale)
            except Exception:
                pass
        # A display-unit preference (``5 Nm`` → ``5.000 N·m``, not the
        # reduced ``5.000 J``).  ``_format_unit_pref`` declines (None)
        # when the dimensions no longer match the preferred unit, and we
        # fall through to the normal reduced-SI display.
        pref = getattr(self, "_unit_pref", None)
        if pref is not None:
            preferred = _format_unit_pref(self.value, self.sf, pref)
            if preferred is not None:
                return preferred
        return _format_sig(self.value, self.sf)

    def __repr__(self):
        return self._formatted()

    def __str__(self):
        return self._formatted()

    def __format__(self, spec):
        # If the user gives an explicit format spec, honour it on the raw
        # value (so f"{q:.5e}" works as a manual override).
        if spec:
            return format(self.value, spec)
        return self._formatted()

    def __getattr__(self, name):
        """Forward attribute access to the wrapped value.

        Without this, ``Sig(Range(...), sf).low`` would raise
        ``AttributeError: 'Sig' object has no attribute 'low'``, forcing
        users to write ``.value.low`` — an awkward extra hop that breaks
        previously-working ergonomics.  The unification of ``plusminus``
        (which started returning ``Sig(Range, sf)`` instead of a bare
        ``Range``) is what introduced the indirection; this transparent
        delegation papers over it.

        ``__getattr__`` is only consulted on misses, so this doesn't
        slow down normal Sig operations.  It also doesn't recurse on
        ``value`` / ``sf`` / ``_stripped_unit`` because those are real
        slots and never miss.

        Dunder names (``__foo__``) are not forwarded: Python's special
        method lookup goes through the type, not the instance, so
        forwarding them here wouldn't help and would be a footgun for
        anything that checks ``hasattr(x, '__iter__')`` etc.  Plain
        attribute names like ``low``, ``high``, ``center``, ``tol``,
        ``dimensions``, ``magnitude`` go through.
        """
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        # Avoid recursing on the slot names themselves — if ``value``
        # somehow isn't set yet (mid-construction), bail out cleanly.
        try:
            inner = object.__getattribute__(self, "value")
        except AttributeError:
            raise AttributeError(name)
        return getattr(inner, name)

    # ---- rich display (Jupyter) -----------------------------------------
    #
    # Jupyter renders a bare cell value by trying the rich-display hooks
    # ``_repr_html_`` / ``_repr_latex_`` / ``_repr_markdown_`` BEFORE it
    # falls back to ``__repr__``.  These are single-underscore names, so
    # they are NOT caught by the dunder guard in ``__getattr__`` above —
    # without the explicit methods here, attribute access forwards them
    # to the wrapped ``forallpeople.Physical``.
    #
    # That forwarding is a problem: ``Physical._repr_html_`` crashes with
    # ``KeyError: None`` for any magnitude smaller than yocto (10⁻²⁴) —
    # forallpeople's SI-prefix table bottoms out there, so a value like
    # ``m_e`` (9.1 × 10⁻³¹ kg) has no prefix to look up.  The bare value
    # ``str()``s fine, but the bare-cell display in Jupyter would raise.
    #
    # Defining the hooks explicitly on ``Sig`` takes precedence over the
    # forwarded ones.  Each tries the inner Physical's rich repr; on ANY
    # failure it returns ``None``.  Returning ``None`` is IPython's
    # documented "this representation is unavailable" signal — IPython
    # then moves on to the next hook and ultimately to plain ``__repr__``
    # (which always works).  Returning ``None`` rather than forcing a
    # plain string keeps each hook honest: we never hand text to the
    # LaTeX slot, where it would be re-parsed as math and mangled.
    #
    # Net effect: a bare ``Sig`` in a notebook cell ALWAYS displays —
    # forallpeople's rich formatting when the magnitude allows it, the
    # plain ``__repr__`` form otherwise.
    def _repr_latex_(self):
        return self._rich_repr("_repr_latex_", latex_fallback=True)

    def _repr_markdown_(self):
        # Decline Markdown so IPython falls through to ``_repr_latex_``.
        # forallpeople's relayed Markdown formats the number with its own
        # fixed precision (ignoring this ``Sig``'s significant figures),
        # so it would show e.g. ``12.000 mV`` for a 2-s.f. ``12. mV``.
        # The LaTeX hook below renders the sf-correct form, and giving up
        # Markdown here makes that the one consistent typeset path.
        return None

    def _repr_html_(self):
        # Decline HTML for the same reason as Markdown above.  IMPORTANT:
        # Jupyter prefers HTML *over* LaTeX in its MIME priority, so a
        # relayed ``_repr_html_`` (``12.000 mV``) would otherwise win over
        # the sf-correct ``_repr_latex_`` (``12. mV``) and reintroduce the
        # inconsistency.  Returning ``None`` lets the LaTeX form show.
        return None

    def _rich_repr(self, method_name, latex_fallback=False):
        """Shared body for the three rich-display hooks.  Try the inner
        value's hook of the same name; on any exception return ``None``
        so IPython falls through to the next representation (ultimately
        plain ``__repr__``).  ``Sig`` itself has no rich form to offer —
        it only relays the wrapped value's, when that works.

        Exception: a pure-temperature ``Physical`` is NOT relayed.
        forallpeople's rich repr would render it with a ``°C`` symbol
        and no offset (the ``300 K`` → ``300 °C`` bug); returning
        ``None`` here makes IPython fall back to ``Sig.__repr__``, which
        formats temperature correctly in kelvin via ``_format_sig``.

        ``latex_fallback`` (only set for ``_repr_latex_``): when the
        wrapped value has no LaTeX hook of its own — a plain ``int`` /
        ``float`` — fall back to wrapping ``Sig``'s own formatted string
        in ``$…$`` so even a bare number renders through MathJax.  This
        gives a consistent typeset look for simple scalars (``2`` shows
        as LaTeX-rendered ``2``) instead of plain text.
        """
        inner = object.__getattribute__(self, "value")
        hook = getattr(inner, method_name, None)
        # Build LaTeX from ``Sig``'s OWN formatted string — used as the
        # fallback when the wrapped value has no LaTeX hook, or its hook
        # raises (forallpeople's ``Physical._repr_latex_`` raises
        # ``KeyError`` for magnitudes outside its SI-prefix table, e.g.
        # ``1.98842e+30 kg``).  Splits the ``<number> <unit>`` string,
        # converts the number's e-notation to ``·10^{…}``, and wraps the
        # unit in ``\mathrm{}`` so it typesets as a proper unit.
        def _own_latex():
            # Use the scale-aware formatted string so a °C/°F temperature
            # typesets in its remembered scale (``100\ ^{\circ}\mathrm{C}``)
            # rather than always kelvin.  The degree forms get the proper
            # ``^{\circ}`` marker; everything else wraps the unit upright.
            own = self._formatted()                       # "100 °C" / "295.15 K" / "1.98842e+30 kg"
            parts = own.split(" ", 1)
            num = _sci_to_latex(parts[0])
            if len(parts) == 2 and parts[1]:
                unit = parts[1]
                if unit.startswith("\u00b0"):             # °C / °F / °R
                    letter = unit[1:]
                    unit_tex = r"{}^{\circ}\mathrm{" + letter + "}"
                else:
                    unit_tex = r"\mathrm{" + unit.replace(" ", r"\ ") + "}"
                return f"${num}\\ {unit_tex}$"
            return f"${num}$"

        if _is_pure_temperature(inner):
            # forallpeople's temperature LaTeX has a ``K``↔``°C`` offset
            # bug, so we never relay its hook for a temperature.  But the
            # value still SHOULD typeset: build it from ``Sig``'s own
            # correct string (``300.15 K``) for the LaTeX path, and
            # decline the other hooks (html/markdown) so LaTeX wins.
            if latex_fallback:
                try:
                    return _own_latex()
                except Exception:
                    return None
            return None

        # A display-unit preference (``5 Nm``) — never relay
        # forallpeople's rich repr, which would typeset the reduced
        # ``J``.  Build LaTeX from the preference-honouring string
        # instead, and decline the other hooks so LaTeX wins.  When the
        # tag no longer applies (dimensions changed downstream),
        # ``_format_unit_pref`` declines and we fall through to the
        # normal relay.
        pref = getattr(self, "_unit_pref", None)
        if pref is not None:
            preferred = _format_unit_pref(inner, self.sf, pref)
            if preferred is not None:
                if latex_fallback:
                    try:
                        num, unit = preferred.split(" ", 1)
                        return (f"${_sci_to_latex(num)}\\ "
                                f"{_unit_label_to_latex(unit)}$")
                    except Exception:
                        return None
                return None

        if hook is None:
            if latex_fallback:
                try:
                    return _own_latex()
                except Exception:
                    return None
            return None
        try:
            relayed = hook()
        except Exception:
            # forallpeople raised (e.g. magnitude beyond its prefix
            # table).  For LaTeX, build from our own string instead of
            # declining — so a huge unit value still typesets.  Other
            # hooks (html/markdown) decline as before.
            if latex_fallback:
                try:
                    return _own_latex()
                except Exception:
                    return None
            return None
        # For the LaTeX hook on a UNIT-carrying value, forallpeople
        # formats the *number* with its own fixed precision (3 decimals),
        # ignoring this ``Sig``'s significant-figure count — so a value
        # the DSL shows as ``12. mV`` (2 s.f.) would relay as the wrong
        # ``12.000 mV``.  Splice in ``Sig``'s sf-correct number while
        # keeping forallpeople's nicely typeset *unit* part, so the LaTeX
        # matches ``str(self)``.  Only attempted when the relayed string
        # has the expected ``$<number> <unit>$`` shape; otherwise the
        # relayed form is returned unchanged.
        def _sanitize_latex(s):
            # forallpeople emits a spurious math-mode toggle *inside* the
            # ``\mathrm{}`` for some symbols — notably ohm:
            # ``\mathrm{$\Omega$}`` (and ``\mathrm{k$\Omega$}``).  Because
            # the whole string is already wrapped in one outer ``$…$``,
            # those inner ``$`` are stray and leave MathJax with unbalanced
            # delimiters, so ``20 Ω`` renders broken.  Strip every ``$``
            # that isn't the outer pair: take the body between the outer
            # ``$…$`` and remove all internal ``$`` (always spurious, since
            # the body is unconditionally in math mode already).
            if not isinstance(s, str) or len(s) < 2:
                return s
            if s.startswith("$") and s.endswith("$"):
                inner_body = s[1:-1].replace("$", "")
                return f"${inner_body}$"
            # No outer wrapper — just drop stray ``$`` defensively.
            return s.replace("$", "")

        if latex_fallback and isinstance(relayed, str):
            try:
                own = _format_sig(self.value, self.sf)   # e.g. "12. mV" / "0.9999 m³"
                own_num = own.split(" ", 1)[0]            # "12." / "0.9999"
                own_unit = own.split(" ", 1)[1] if " " in own else ""
                own_num_tex = _sci_to_latex(own_num)
                body = relayed.strip("$")
                m = re.match(r'^\s*[-+0-9.eE]+(.*)$', body, re.S)
                relay_unit_tex = m.group(1) if m else ""
                # The natural-prefix rule can swap the displayed unit
                # (``mm³`` → ``m³``) so the relayed LaTeX unit goes stale.
                # Detect this precisely: re-relay forallpeople's LaTeX for
                # the prefix-CORRECTED value and compare its unit text to
                # the original relay's.  If they differ, the prefix
                # changed → build from our own corrected unit; otherwise
                # keep forallpeople's nicely typeset relay unit (so simple
                # units like ``\Omega`` are untouched — no glyph-vs-command
                # false positives).
                changed = False
                try:
                    inner = object.__getattribute__(self, "value")
                    corrected = _prefer_natural_prefix(inner)
                    if corrected is not inner:
                        crelay = corrected._repr_latex_()
                        cbody = crelay.strip("$") if isinstance(crelay, str) else ""
                        cm = re.match(r'^\s*[-+0-9.eE]+(.*)$', cbody, re.S)
                        relay_unit_tex = cm.group(1) if cm else relay_unit_tex
                        changed = True
                except Exception:
                    changed = False
                if m and own_num_tex:
                    return _sanitize_latex(f"${own_num_tex}{relay_unit_tex}$")
            except Exception:
                pass
            return _sanitize_latex(relayed)
        return relayed


# ---------------------------------------------------------------------------
# 3. Sig-fig propagation rules
# ---------------------------------------------------------------------------

def _muldiv_sf(_a, _b, sa, sb, _result):
    return min(sa, sb)


def _addsub_sf(a, b, sa, sb, result):
    da = _decimals(a, sa)
    db = _decimals(b, sb)
    return _sf_from_decimals(result, min(da, db))


def _pow_sf(a, _b, sa, sb, _result):
    # If the exponent is exact (math.inf sf), result inherits base sf.
    # Otherwise the result is no better than the noisier operand.
    if math.isinf(sb):
        return sa
    return min(sa, sb)


# ---------------------------------------------------------------------------
# 4. Top-level helpers
# ---------------------------------------------------------------------------

def exact(x) -> Sig:
    """Mark ``x`` as exact (infinite significant figures)."""
    return Sig(_unwrap(x), _INF)


def measured(x, sf: int) -> Sig:
    """Mark ``x`` as a measured quantity with ``sf`` significant figures."""
    return Sig(_unwrap(x), sf)


def sigfigs_of(x) -> float:
    """Return the sf count of ``x``, or ``math.inf`` if untracked."""
    return _sf_of(x)


# Short alias used by the source-transformed code.  Single-letter so the
# rewritten source stays compact.
_S = Sig.lit


# ---------------------------------------------------------------------------
# 5. Formatting (extensible via singledispatch)
# ---------------------------------------------------------------------------

def _readability_penalty(mantissa: float) -> float:
    """Score how 'unnatural' a displayed mantissa is (lower = better).

    The engineering-friendly range is roughly [1, 1000): a mantissa there
    reads cleanly (``0.5``, ``12``, ``999``).  Values far outside — a huge
    ``9.999e8`` or a tiny ``1e-6`` — score worse.  We use the absolute
    base-10 distance from that band, so the comparison naturally prefers
    the form that keeps the number human-sized.
    """
    m = abs(mantissa)
    if m == 0:
        return 0.0
    import math as _m
    log = _m.log10(m)
    # Ideal band: 0 ≤ log10 < 3  (i.e. 1 ≤ |m| < 1000).
    if 0 <= log < 3:
        return 0.0
    if log < 0:
        return -log               # below 1 → distance under the band
    return log - 3 + 1e-9         # at/above 1000 → distance over the band


def _prefer_natural_prefix(value):
    """For a forallpeople ``Physical``, choose between its auto-selected
    prefix and the un-prefixed base unit, returning whichever displays
    with the more natural mantissa.

    forallpeople picks a prefix on the *linear* dimension and then raises
    it to the unit's power, so for a POWERED unit (``m²``, ``m³``) the
    prefix step is a factor of ``10^(3·power)``.  A volume just under
    ``1 m³`` therefore jumps to ``9.999e8 mm³`` — mathematically right,
    visually absurd.  This compares the auto form's displayed mantissa
    against the base-unit (``prefix('unity')``) form's mantissa using
    :func:`_readability_penalty` and returns the better one, so
    ``0.9999 m³`` stays ``0.9999 m³`` while a genuinely small/large value
    still gets a sensible prefix.  Non-Physical or single-power units are
    returned unchanged (the auto choice is already fine for those).
    """
    if not (hasattr(value, "value") and hasattr(value, "dimensions")):
        return value
    prefix = getattr(value, "prefix", None)
    if not callable(prefix):
        return value
    try:
        # The auto-prefixed displayed mantissa: forallpeople stores the
        # base-SI magnitude in ``.value`` and applies the prefix only for
        # display, so parse the mantissa out of its repr.
        auto_repr = repr(value)
        m = re.match(r'^\s*([-+0-9.eE]+)', auto_repr)
        auto_mant = float(m.group(1)) if m else float(value)
        unity = prefix("unity")
        unity_mant = float(object.__getattribute__(unity, "value"))
    except Exception:
        return value
    # Keep the auto choice unless the base unit reads clearly better.
    # The strict ``<`` (plus the tiny epsilon in the penalty) means ties
    # go to the auto form, so simple units are untouched.
    if _readability_penalty(unity_mant) < _readability_penalty(auto_mant):
        return unity
    return value


@singledispatch
def _format_sig(value, sf) -> str:
    """Format ``value`` using ``sf`` significant figures.

    Default implementation uses Python's ``g`` format spec, which works
    for plain floats AND for forallpeople ``Physical`` quantities (it formats
    the prefix-scaled magnitude and re-attaches the unit string). Scientific
    notation in the output is then expanded to plain decimal where the
    exponent is comfortable (see :func:`_expand_sci`).

    Exception handling: we broaden from ``(TypeError, ValueError)`` to
    catch any ``Exception`` because forallpeople's ``__format__`` can
    raise ``KeyError: None`` when the magnitude is outside its prefix
    table (smaller than yocto ``1e-24`` or larger than yotta ``1e24``).
    Electron and proton mass land in this range.  When that happens,
    we fall back to ``_format_physical_fallback`` which renders the
    SI-base magnitude with manually-rendered dimensions.

    Pure-temperature quantities are special-cased BEFORE the normal
    path: forallpeople would render them with a ``°C`` symbol and no
    offset (so ``300 K`` prints as ``300 °C`` — wrong by 273.15°), so
    the toolkit formats them itself in kelvin.  See
    :func:`_is_pure_temperature`.
    """
    is_physical = hasattr(value, "value") and hasattr(value, "dimensions")

    # Temperature special-case — must come first, because the value IS
    # a Physical and would otherwise take the (mislabelling) normal path.
    if is_physical and _is_pure_temperature(value):
        try:
            return _format_temperature(value, sf)
        except Exception:
            # If anything unexpected happens, fall through to the
            # normal path rather than failing to render at all.
            pass

    # Prefer a natural prefix for powered units: forallpeople would show
    # ``0.9999 m³`` as ``9.999e8 mm³``; swap to the base unit when that
    # reads better.  Non-Physical / single-power values are unchanged.
    if is_physical:
        try:
            value = _prefer_natural_prefix(value)
        except Exception:
            pass

    if math.isinf(sf):
        # For exact values use ``.15g`` rather than ``repr(value)``.  ``g``
        # mode strips trailing zeros, so an exact ``1e-12 * F`` prints as
        # "1 pF" rather than the "1.000 pF" that forallpeople's default
        # ``.{precision}f`` repr produces.
        try:
            return _expand_sci(format(value, ".15g"))
        except Exception:
            if is_physical:
                try:
                    return _format_physical_fallback(value, sf)
                except Exception:
                    pass
            try:
                return _expand_sci(repr(value))
            except Exception:
                return str(object.__getattribute__(value, "value")) \
                    if is_physical else "<unformattable>"
    n = max(int(sf), 1)
    try:
        return _expand_sci(format(value, f"#.{n}g"))
    except Exception:
        try:
            return _expand_sci(format(value, f".{n}g"))
        except Exception:
            if is_physical:
                try:
                    return _format_physical_fallback(value, sf)
                except Exception:
                    pass
            try:
                return str(value)
            except Exception:
                return "<unformattable>"


def _is_pure_temperature(value) -> bool:
    """True when ``value`` is a forallpeople ``Physical`` whose dimension
    is exactly temperature — ``K`` to the first power, every other base
    dimension zero.

    This is the case forallpeople mishandles: its default environment
    registers the temperature dimension with the ``°C`` symbol and has
    no separate ``K`` display unit, so a Kelvin quantity reprs as
    ``°C`` *without applying the 273.15 offset* — ``300 K`` prints as
    the flatly-wrong ``300 °C``.  Compound units that merely involve
    temperature (``J/K`` entropy, ``W/(m·K)`` conductivity, the gas
    constant) are NOT pure temperature — ``K`` appears at a power
    other than +1 or alongside other dimensions — and forallpeople
    renders those correctly, so they must be left alone.
    """
    if not (hasattr(value, "value") and hasattr(value, "dimensions")):
        return False
    try:
        dims = object.__getattribute__(value, "dimensions")
    except Exception:
        return False
    # Exactly K=1, all other base dimensions 0.
    if getattr(dims, "K", 0) != 1:
        return False
    for name in ("kg", "m", "s", "A", "cd", "mol"):
        if getattr(dims, name, 0) != 0:
            return False
    return True


def _is_pure_time(value) -> bool:
    """True when ``value`` is a forallpeople ``Physical`` whose dimension
    is exactly time — ``s`` to the first power, every other base
    dimension zero.

    Used to gate the ``▶ HMS`` display: a duration can be broken into
    hours / minutes / seconds, but a velocity (``m·s⁻¹``) or a frequency
    (``s⁻¹``) cannot, so those must fall through to the normal path.
    """
    if not (hasattr(value, "value") and hasattr(value, "dimensions")):
        return False
    try:
        dims = object.__getattribute__(value, "dimensions")
    except Exception:
        return False
    if getattr(dims, "s", 0) != 1:
        return False
    for name in ("kg", "m", "K", "A", "cd", "mol"):
        if getattr(dims, name, 0) != 0:
            return False
    return True


def _format_temperature(value, sf, scale="K") -> str:
    """Render a pure-temperature ``Physical`` in a chosen scale.

    forallpeople stores temperature in SI base, so the Physical's
    ``.value`` attribute is the kelvin magnitude (``273.15`` for
    ``T_0``).  ``scale`` selects how that kelvin value is presented:

      * ``"K"``  — kelvin, the stored magnitude, suffix ``K``;
      * ``"degC"`` — Celsius, ``K - 273.15``, suffix ``°C``;
      * ``"degF"`` — Fahrenheit, ``(K - 273.15)·9/5 + 32``, suffix ``°F``;
      * ``"degR"`` — Rankine, ``K·9/5``, suffix ``°R``.

    The conversion is offset-correct — this is the *display* mirror of
    the ``from_degC`` / ``from_degF`` input constructors.  ``scale``
    only changes the rendering; the underlying value is, and stays,
    kelvin.

    Formatting mirrors ``_format_sig``: an exact value (``sf`` infinite)
    uses plain ``.15g`` so trailing zeros are stripped; a finite ``sf``
    uses ``#.{n}g`` so significant trailing zeros show.
    """
    kelvin = float(object.__getattribute__(value, "value"))

    # Convert the kelvin magnitude to the requested scale.
    if scale == "degC":
        mag, suffix = kelvin - 273.15, "\u00b0C"
    elif scale == "degF":
        mag, suffix = (kelvin - 273.15) * 9.0 / 5.0 + 32.0, "\u00b0F"
    elif scale == "degR":
        mag, suffix = kelvin * 9.0 / 5.0, "\u00b0R"
    else:  # "K" — the default
        mag, suffix = kelvin, "K"

    try:
        if math.isinf(sf):
            return _expand_sci(format(mag, ".15g")) + " " + suffix
        n = max(int(sf), 1)
        return _expand_sci(format(mag, f"#.{n}g")) + " " + suffix
    except Exception:
        return format(mag, ".6g") + " " + suffix


def _format_hms(seconds, sf=None) -> str:
    """Render a duration (a number of seconds) as a ``d/h/m/s`` string.

    Examples::

        45                 -> "45s"
        3661               -> "1h 01m 01s"
        90061              -> "1d 01h 01m 01s"
        3661.5             -> "1h 01m 01.5s"
        -3661              -> "-1h 01m 01s"
        33.3333…, sf=4     -> "33.33s"
        8439, sf=2         -> "2h 20m"        (seconds not significant)
        3660, sf=3         -> "1h 01m"        (seconds not significant)
        3600, sf=2         -> "1h"            (minutes & seconds not sig.)

    Rules:

    * The largest non-zero unit leads; smaller units are zero-padded to
      two digits so the fields line up (``1h 01m 01s``).
    * Units above the largest non-zero one are omitted — a 45-second
      duration is just ``45s``, not ``0d 00h 00m 45s``.
    * A negative duration gets a single leading ``-``.

    Significant-figure awareness — ``sf``:

    * ``sf`` is the significant-figure count of the *whole duration*
      (the number of seconds), carried from the value's ``Sig``
      precision.  The total is rounded to that many significant
      figures BEFORE being decomposed, so a 4-sf ``100.0 s / 3`` shows
      as ``33.33s`` rather than ``33.333333s``.
    * Rounding the total (not the seconds field) is what sf actually
      means: ``7170.0 s`` at 5 sf is ``7170.0`` — unchanged — and
      decomposes cleanly to ``1h 59m 30s``; the ``30`` is genuine, not
      truncated.
    * **Trailing fields that carry no significant digits are dropped.**
      When sf-rounding leaves the precision at, say, hundreds of
      seconds, the seconds field holds no information — showing
      ``2h 20m 00s`` would falsely imply the seconds were *measured*
      as zero.  The honest display is ``2h 20m``.  A field is dropped
      only when it is both zero and below the rounding resolution; a
      non-zero field is always kept (``2h 20m 39s`` never loses its
      ``39s``), and trimming is strictly right-to-left.
    * ``sf=None`` or an infinite ``sf`` (an exact value) keeps every
      field at full precision, trimmed only of trailing decimal zeros
      — an exact ``3661.5`` is still ``1h 01m 01.5s``.
    * Rounding can only ever leave digits at or after the decimal
      point of the seconds field — the hours/minutes split is exact
      integer arithmetic on the rounded total — so the d/h/m fields
      are always whole numbers regardless of ``sf``.

    This is a *display* helper — it takes a plain number of seconds.
    The :class:`_HMSDisplay` wrapper unwraps a ``Physical`` duration to
    its second-magnitude and passes the value's ``sf`` through.
    """
    total = float(seconds)
    sign = "-" if total < 0 else ""
    total = abs(total)

    # Sigfig rounding of the WHOLE duration, before decomposition.
    # ``round(x, -int(floor(log10 x)) + (n-1))`` rounds x to n
    # significant figures.  Done here so every field below reflects a
    # value the caller actually claims to know.
    #
    # ``lsd_place`` records the power-of-ten place of the rounded
    # value's least-significant digit — e.g. rounding 8439 to 2 sf
    # gives 8400 with ``lsd_place = 2`` (the hundreds place).  It is
    # used afterwards to decide which trailing fields are below the
    # resolution and should be dropped.  For an exact value (no finite
    # ``sf``) it stays at ``-inf`` — every field is significant.
    lsd_place = float("-inf")
    if sf is not None and math.isfinite(sf) and total > 0:
        n = max(int(sf), 1)
        exp = math.floor(math.log10(total))
        lsd_place = exp - (n - 1)
        total = round(total, -lsd_place)

    days, rem = divmod(total, 86_400.0)
    hours, rem = divmod(rem, 3_600.0)
    minutes, secs = divmod(rem, 60.0)
    days, hours, minutes = int(days), int(hours), int(minutes)

    # Render the seconds field.  Whole → integer.  Fractional → a
    # trimmed decimal; rounded to a sane number of places (12) purely to
    # kill binary-float dust like ``...0000001`` — the significant-
    # figure rounding above has already done the real precision work.
    if secs == int(secs):
        sec_str = str(int(secs))
    else:
        sec_str = f"{round(secs, 12):.12f}".rstrip("0").rstrip(".")

    # Significant-figure field trimming.  When sf-rounding leaves the
    # least-significant digit at a coarse place, the smaller fields
    # carry no real information — showing ``2h 20m 00s`` for a value
    # known only to 2 sf falsely implies the seconds were *measured*
    # as zero.  The honest display is ``2h 20m``.
    #
    # A trailing field is dropped only when BOTH:
    #   * it is zero — a non-zero field is always shown (you can never
    #     drop the ``39`` from ``2h 20m 39s``), and
    #   * the rounding resolution does not reach it — ``lsd_place``
    #     sits strictly above the field's own place.
    # Trimming is right-to-left and stops at the first field that must
    # stay, so only a genuine run of trailing, zero, unresolved fields
    # is removed.  The leading (largest) field is never dropped.
    #
    # Field place thresholds — the power of ten at or below which the
    # rounding must reach for the field to be significant:
    #   * seconds resolved when lsd_place <= 0  (units of seconds);
    #   * minutes resolved when lsd_place <= 1  (a minute is ~10^1.78 s,
    #     so a tens-of-seconds resolution still pins the minute);
    #   * hours   resolved when lsd_place <= 3  (an hour is ~10^3.56 s).
    # Days are never trimmed (if present they are the leader).
    drop_seconds = (secs == 0 and lsd_place > 0)
    drop_minutes = (minutes == 0 and drop_seconds and lsd_place > 1)
    drop_hours   = (hours == 0 and drop_minutes and lsd_place > 3)

    # Build from the largest non-zero unit down.  ``parts`` collects
    # ``(value, suffix, pad)`` for every field at or below the leader.
    if days:
        parts = [(days, "d", False), (hours, "h", True),
                 (minutes, "m", True), (sec_str, "s", True)]
    elif hours:
        parts = [(hours, "h", False), (minutes, "m", True),
                 (sec_str, "s", True)]
    elif minutes:
        parts = [(minutes, "m", False), (sec_str, "s", True)]
    else:
        parts = [(sec_str, "s", False)]

    # Apply the trailing-field trim.  ``parts`` is ordered large→small,
    # so drop matching suffixes from the end; never drop the last
    # remaining (leading) field.
    def _trim(parts):
        drop = {"s": drop_seconds, "m": drop_minutes, "h": drop_hours}
        while len(parts) > 1:
            suffix = parts[-1][1]
            if drop.get(suffix, False):
                parts = parts[:-1]
            else:
                break
        return parts

    parts = _trim(parts)

    chunks = []
    for value, suffix, pad in parts:
        if pad and suffix != "s":
            chunks.append(f"{int(value):02d}{suffix}")
        elif pad and suffix == "s":
            # Pad the integer part of the seconds field to two digits,
            # keeping any fractional tail.
            s = str(value)
            int_part, _, frac = s.partition(".")
            int_part = int_part.zfill(2)
            chunks.append(int_part + ("." + frac if frac else "") + "s")
        else:
            chunks.append(f"{value}{suffix}")

    return sign + " ".join(chunks)


def _format_physical_fallback(value, sf):
    """Format a forallpeople ``Physical`` whose normal ``__format__``
    crashes — typically because the magnitude is outside the prefix
    table (smaller than yocto ``1e-24`` or larger than yotta ``1e24``),
    which makes forallpeople's ``_auto_prefix_value`` lookup return
    ``KeyError: None``.

    We bypass forallpeople's auto-prefix machinery entirely and render
    the SI-base magnitude in scientific notation with ``sf`` significant
    figures, then append the dimension string built from the
    ``Dimensions`` namedtuple.  Result: ``9.1094e-31 kg`` for the
    electron mass — readable, scientifically conventional, and won't
    crash.

    Used only as a fallback; well-behaved Physical magnitudes still go
    through forallpeople's prettier auto-prefix path.
    """
    val = object.__getattribute__(value, "value")
    dims = object.__getattribute__(value, "dimensions")
    n = max(int(sf), 1) if math.isfinite(sf) else 6
    mag_str = format(val, f".{n}g")
    dim_str = _render_dimensions(dims)
    if dim_str:
        return f"{mag_str} {dim_str}"
    return mag_str


def _render_dimensions(dims) -> str:
    """Render a forallpeople ``Dimensions`` namedtuple as ``kg·m²·s⁻³``
    etc., using superscript exponents.  Returns an empty string for
    dimensionless quantities.

    Used by ``_format_physical_fallback`` when forallpeople's own
    rendering chokes on out-of-range magnitudes.
    """
    # Order matches forallpeople's convention: kg, m, s, A, cd, K, mol
    parts = []
    sup_digits = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")
    for name in ("kg", "m", "s", "A", "cd", "K", "mol"):
        power = getattr(dims, name, 0)
        if power == 0:
            continue
        if power == 1:
            parts.append(name)
        else:
            # Stringify as integer if integral, else fall back to plain text.
            if power == int(power):
                parts.append(f"{name}{int(power)}".translate(sup_digits))
            else:
                parts.append(f"{name}^{power}")
    return "·".join(parts)


@_format_sig.register(int)
def _(value, _sf):
    return str(value)


@_format_sig.register(float)
def _(value, sf):
    if math.isnan(value) or math.isinf(value):
        return repr(value)
    if math.isinf(sf):
        return _expand_sci(repr(value))
    n = max(int(sf), 1)
    if value == 0:
        return f"{value:.{n-1}f}"
    order = int(math.floor(math.log10(abs(value))))
    if -3 <= order <= 5:
        # Comfortable magnitude — round to n sig figs and emit plain decimal.
        round_to = order - n + 1
        if round_to >= 0:
            factor = 10 ** round_to
            return f"{round(value / factor) * factor:.0f}"
        return f"{value:.{-round_to}f}"
    # Out-of-range magnitude — keep scientific.
    return f"{value:#.{n}g}"


# Default precision for complex values whose ``sf`` is infinite.  Pure
# integer constants like the speed of light should keep all their digits,
# but irrational complex results from phasor arithmetic shouldn't show
# all 17 float-repr digits — most engineering uses want 4-8 significant
# figures.  4 matches typical "engineering display" conventions.
_COMPLEX_INF_DISPLAY_SF = 4


def _register_polar_formatter():
    try:
        from .circuit_dsl import _Polar, _format_polar
    except Exception:                       # pragma: no cover
        return

    @_format_sig.register(_Polar)
    def _(value, sf):
        return _format_polar(value, sf if sf != _INF else _COMPLEX_INF_DISPLAY_SF)


@_format_sig.register(complex)
def _(value, sf):
    # ``sf=inf`` on a complex quantity typically means "exact within
    # float precision" — but most complex results come from numeric trig
    # (phasors, FFTs) where the underlying value is irrational and the
    # float just happens to round-trip via ``repr`` to 17 digits.  Capping
    # at a sensible default makes the display readable; users who want
    # full precision can format the value themselves with an explicit
    # spec like ``f"{val:.15g}"`` or pull ``val.value`` out and inspect.
    display_sf = sf if math.isfinite(sf) else _COMPLEX_INF_DISPLAY_SF
    re_part = _format_sig(value.real, display_sf)
    im_part = _format_sig(abs(value.imag), display_sf)
    sign = "+" if value.imag >= 0 else "-"
    return f"({re_part}{sign}{im_part}j)"


# ---- scientific-to-plain expansion -----------------------------------------

_SCI_RE = re.compile(r'(-?\d+(?:\.\d*)?)[eE]([+-]?\d+)')


def _expand_sci(s: str) -> str:
    """Replace ``Xe+NN`` patterns inside ``s`` with plain decimal whenever
    the exponent is small enough that the result reads naturally AND
    the plain-decimal form doesn't hide significance.

    The trade-off here is significance visibility vs. compact reading.
    Plain ``500`` could mean 1, 2, or 3 sig figs — the reader can't tell.
    Three cases distinguished by where the last significant digit lands:

    - ``round_to < 0`` — significance extends into fractional digits.
      Plain decimal with explicit precision ('12.30', '0.0123') reads
      naturally and the trailing zeros are unambiguous.  Expand.

    - ``round_to == 0`` — significance ends exactly at the ones digit.
      Plain decimal ('500.', '123') is fine. When the value has
      trailing zeros (500), add a trailing dot to flag that the zeros
      ARE significant — the conventional engineering reading is
      "the dot means there's nothing more, but the zeros count".

    - ``round_to > 0`` — significance ends BEFORE the ones digit.
      The trailing zeros aren't significant ("500" with sf=2 means
      "5 × 10² ± 5 × 10¹"; the ones digit is a placeholder).  Plain
      decimal can't show this without ambiguity, so KEEP scientific.

    The exponent range ``[-3, 5]`` is the readable band; outside it,
    scientific stays regardless.
    """
    def repl(m):
        mantissa_str = m.group(1)
        exp = int(m.group(2))
        if not (-3 <= exp <= 5):
            return m.group(0)
        try:
            v = float(m.group(0))
        except ValueError:
            return m.group(0)
        # Recover the sf from the mantissa's significant digits.
        digits = mantissa_str.lstrip('-').replace('.', '').lstrip('0')
        sig_figs = len(digits) or 1
        order = 0 if v == 0 else int(math.floor(math.log10(abs(v))))
        round_to = order - sig_figs + 1

        if round_to > 0:
            # Significance ends BEFORE the ones digit — e.g. ``5.0e+02``
            # at sf=2 means 500 ± 5; the trailing zero isn't part of the
            # measurement.  An older policy kept this in scientific form
            # to flag the rounding, but ``5.0e+02 mm`` and ``4.7e+02 kJ``
            # read poorly for the typical engineering case, where the
            # value is comfortably below 10⁵ and a plain integer is what
            # the engineer would write by hand: ``500 mm``, ``470 kJ``.
            #
            # So for values inside the readable band (the ``-3 ≤ exp ≤ 5``
            # gate above already guarantees that), render as a plain
            # integer.  Precision is NOT lost — the ``Sig`` object's sf
            # is unchanged, so subsequent arithmetic still propagates it
            # correctly; the rendering layer just trades sf-visibility
            # for readability at the display surface.  An engineer who
            # needs the precise rounding place can ask ``exact(x)`` or
            # ``sigfigs_of(x)``.
            return f"{round(v):.0f}"

        if round_to == 0:
            # Significance ends at the ones digit.  Integer form is
            # fine, but if there are trailing zeros we add a dot to
            # disambiguate ('500' → '500.').  Negative values follow
            # the same rule, just keep the sign.
            integer_form = f"{round(v):.0f}"
            # A trailing zero means "...0" at the end of the digit
            # sequence; ignore a possible leading minus.
            digit_part = integer_form.lstrip('-')
            if digit_part.endswith('0'):
                return f"{integer_form}."
            return integer_form

        # round_to < 0 — precision extends into fractional digits.
        return f"{v:.{-round_to}f}"
    return _SCI_RE.sub(repl, s)


def register_formatter(*types):
    """Decorator: register a custom (value, sf) -> str formatter for a type.

    Use this if you have a numeric type whose default ``__format__`` doesn't
    play nicely with the ``#.{n}g`` spec — e.g. matrices, custom Physical
    classes that don't accept format specs, etc.
    """
    def deco(fn):
        for t in types:
            _format_sig.register(t)(fn)
        return fn
    return deco


# Register a sympy ``Basic`` formatter — fires when a Sig wraps a sympy
# expression.  This happens routinely now that Sig._binop wraps sympy
# results: ``Sig(2) * sym.pi`` produces ``Sig(value=2*pi, sf=∞)`` rather
# than dropping the wrapper.
#
# The display strategy: evaluate to float and format normally.  Engineers
# want to see "6.283" or "200" not "2*pi" or "200*pi" — that's the whole
# point of preserving Sig through symbolic operations rather than letting
# the symbolic form take over.  If the expression has free symbols and
# can't be evaluated to a finite float, fall back to sympy's own string
# form (gracefully degraded — at least the user sees something).
def _try_register_sympy_formatter():
    try:
        import sympy as _sym
    except ImportError:
        return

    @_format_sig.register(_sym.Basic)
    def _(value, sf):
        # Try to evaluate to a Python float.  ``float(value)`` works when
        # the expression is purely numeric (``2*pi``, ``sqrt(7)``,
        # ``exp(1)``).  It fails with TypeError when free symbols are
        # present (``a*x + b``) or when the expression is non-real
        # (``I + 2``); in those cases we fall back to the sympy string.
        try:
            f = float(value)
        except (TypeError, ValueError):
            # Non-evaluable — show the sympy form.  This shouldn't happen
            # in normal engineering use; it's a safety net for code that
            # stuffs Symbol-bearing expressions into Sig.
            return str(value)
        # Dispatch on the float — same code path as a Sig with a plain
        # float value, so sf-aware rounding/scaling kicks in correctly.
        return _format_sig(f, sf)


_try_register_sympy_formatter()


# ---------------------------------------------------------------------------
# 6. Mathcad-style "express in units" helper
# ---------------------------------------------------------------------------

class _InUnits:
    """A numeric value paired with a label describing the units it's in.

    Created by :func:`in_units` (and by the DSL's ``▶`` operator, which
    rewrites to a call to :func:`in_units` at source-transform time).
    The point of this wrapper is to render Mathcad-style — "value label" —
    without depending on ``forallpeople``'s built-in compound-unit
    rendering (which collapses derived units to base SI regardless of
    what the source code wrote).

    Sf-aware: the display uses exactly the precision rule of a bare
    ``Sig`` (``_format_sig``).  A finite ``sf`` rounds to that many
    significant figures; an infinite ``sf`` (the source was exact —
    integer literals, ``22735 mm``) prints the value in full, so a typed
    ``22735 mm ▶ mm`` reads back as ``22735 mm`` and never as a rounded
    ``22740 mm``.  What you see in ``print(v)`` and ``v ▶ unit`` honour
    the same precision.

    The wrapper is transparent to numeric use: ``float()``, ``int()``,
    ``__format__`` all return the display-unit scalar, so an ``_InUnits``
    can be plotted or fed to plain-number code.  Arithmetic (``+ - * /``,
    comparisons) acts on the DIMENSIONED source quantity — see the
    arithmetic section below — so ``(a ▶ mm) + (b ▶ m)`` is a correct
    length, not a sum of two unrelated numbers.  It only acts special
    when *displayed* (``repr`` / ``str`` / ``print``).

    >>> v = in_units(0.0003155, mm_per_s_unit, 'mm/s')   # built directly
    >>> v                                                  # repr — Mathcad-style
    0.3155 mm/s
    >>> float(v)                                           # plain numeric
    0.3155
    >>> f"{v:.2g}"                                         # custom spec wins
    '0.32 mm/s'
    """

    # ``value``      — the display number: ``quantity / target``, a float
    #                  (or float array) in the display unit.  Plotting and
    #                  ``float()`` read this.
    # ``unit_label`` — the text shown after the number.
    # ``sf``         — significant figures of the source quantity.
    # ``quantity``   — the dimensioned source (a ``Physical``, or an array
    #                  of them), kept so ARITHMETIC on the wrapper stays
    #                  unit-aware.  ``None`` when there was no dimensioned
    #                  source (symbolic / hand-built wrappers), in which
    #                  case arithmetic falls back to ``value``.
    __slots__ = ("value", "unit_label", "sf", "quantity")

    def __init__(self, value, unit_label: str, sf: float = _INF,
                 quantity=None):
        # Accept either a scalar or an array-like.  The plotting code
        # routinely passes numpy arrays of Physicals through ``in_units``
        # (the ``y := [...] · M☉ ▸ M☉`` idiom for axis labelling); a
        # scalar-only constructor would force per-element wrapping and
        # lose the shared unit-label across the series.
        #
        # ``np.asarray(...)`` accepts numpy arrays, lists, tuples, and
        # plain scalars.  For scalars we recover ``float(value)`` via
        # ``.item()`` to preserve the original storage shape — most of
        # this class's methods (``__lt__``, ``__int__``) assume a scalar
        # ``self.value``, so we keep that as the default.
        try:
            import numpy as _np
            arr = _np.asarray(value)
            if arr.ndim == 0:
                self.value = float(arr.item())
            else:
                # Array case — keep the ndarray; downstream code is
                # responsible for handling it.  ``__float__`` /
                # ``__int__`` will raise TypeError on arrays, which is
                # standard numpy behaviour.
                self.value = arr
        except (ImportError, TypeError, ValueError):
            # No numpy or some unsupported type — fall back to scalar.
            self.value = float(value)
        self.unit_label = unit_label
        self.sf = sf
        self.quantity = quantity

    def __repr__(self) -> str:
        # Array case — render a compact summary rather than the full
        # contents (which can be enormous for a 10000-point dataset).
        try:
            import numpy as _np
            if isinstance(self.value, _np.ndarray) and self.value.ndim > 0:
                n = self.value.size
                if n == 0:
                    return f"array([]) {self.unit_label}"
                # Show first / last element with sf-aware formatting.
                first = self._fmt_number(float(self.value.flat[0]))
                last = self._fmt_number(float(self.value.flat[-1]))
                return f"array([{first}, ..., {last}], n={n}) {self.unit_label}"
        except (ImportError, AttributeError):
            pass
        # Scalar fallback — same precision rule as a bare ``Sig`` holding
        # a Physical: finite sf rounds, infinite sf prints in full.
        return f"{self._fmt_number(self.value)} {self.unit_label}"

    def _fmt_number(self, v) -> str:
        """Format the display number with the precision rule a bare
        ``Sig`` applies to a *Physical*: a finite ``sf`` rounds to that
        many significant figures; an exact value (``sf`` infinite)
        prints via ``.15g``, the same as ``_format_sig`` does for an
        exact Physical.  ``.15g`` rather than ``repr`` matters here
        because ``self.value`` is a computed ratio (``quantity /
        target``) and carries float noise a Physical's own repr never
        shows — ``1 mm ▶ μm`` is ``1000.0000000000001`` as a float, and
        must read ``1000 μm``, not expose the noise.  Non-float values
        (a sympy symbol on the symbolic path) fall through to
        ``_format_sig``."""
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            try:
                return _format_in_unit(float(v), self.sf)
            except Exception:
                pass
        return _format_sig(v, self.sf)

    def __str__(self) -> str:
        return self.__repr__()

    # ----- rich display -----
    def _repr_latex_(self):
        """Typeset the Mathcad-style ``value label`` form so a
        display-tagged value (``mₚ ▸ MeV/c²``) renders as LaTeX like every
        other scalar, instead of falling to plain text.  Built from the
        same sf-aware number string as ``__repr__`` (so display matches),
        with the unit label set upright in ``\\mathrm{}`` and any Unicode
        superscripts in the label (``c²``) converted to real exponents.
        Declines (returns ``None``) for the array form — a series has no
        single scalar to typeset.  HTML/Markdown are declined so the LaTeX
        hook wins Jupyter's MIME priority.
        """
        try:
            import numpy as _np
            if isinstance(self.value, _np.ndarray) and self.value.ndim > 0:
                return None
        except ImportError:
            pass
        try:
            num = _sci_to_latex(self._fmt_number(self.value))
            unit = _unit_label_to_latex(self.unit_label)
            return f"${num}\\ {unit}$"
        except Exception:
            return None

    def _repr_html_(self):
        return None

    def _repr_markdown_(self):
        return None

    def __float__(self) -> float:
        # Arrays don't have a single float representation — match numpy
        # behaviour and raise.  Callers wanting per-element values should
        # iterate or use ``np.asarray(self.value)``.
        try:
            import numpy as _np
            if isinstance(self.value, _np.ndarray) and self.value.ndim > 0:
                raise TypeError("only 0-dimensional arrays can be converted to Python scalars")
        except ImportError:
            pass
        return float(self.value)

    def __int__(self) -> int:
        return int(self.__float__())

    def __iter__(self):
        # Allow ``for v in (x ▶ unit):`` to iterate the array form.
        # Scalars are not iterable (would raise here too); matches the
        # behaviour of the wrapped value.
        try:
            import numpy as _np
            if isinstance(self.value, _np.ndarray) and self.value.ndim > 0:
                return iter(self.value)
        except ImportError:
            pass
        raise TypeError(f"'_InUnits' wrapping {type(self.value).__name__} is not iterable")

    def __len__(self):
        try:
            import numpy as _np
            if isinstance(self.value, _np.ndarray) and self.value.ndim > 0:
                return len(self.value)
        except ImportError:
            pass
        raise TypeError(f"'_InUnits' wrapping {type(self.value).__name__} has no len()")

    def __getitem__(self, idx):
        # Index into the array form.  ``y₀`` (DSL-rewritten to ``y[0]``)
        # must work when ``y`` is an ``_InUnits`` wrapping an array —
        # otherwise the common "subtract the first sample" idiom
        # ``y - y₀`` is impossible.  The result keeps the same unit
        # label so a single element prints as e.g. ``125200 μm`` and
        # arithmetic with it stays unit-consistent.
        try:
            import numpy as _np
            if isinstance(self.value, _np.ndarray) and self.value.ndim > 0:
                picked = self.value[idx]
                # A scalar element → wrap as a scalar _InUnits.  A slice
                # → wrap the sub-array.  Either way reuse the label/sf.
                result = _InUnits.__new__(_InUnits)
                if isinstance(picked, _np.ndarray):
                    result.value = picked
                else:
                    result.value = float(picked)
                result.unit_label = self.unit_label
                result.sf = self.sf
                # Keep the dimensioned element too, so ``y - y₀`` stays
                # unit-aware (see the arithmetic section below).
                q = getattr(self, "quantity", None)
                try:
                    result.quantity = None if q is None else q[idx]
                except Exception:
                    result.quantity = None
                return result
        except ImportError:
            pass
        raise TypeError(
            f"'_InUnits' wrapping {type(self.value).__name__} is not subscriptable"
        )

    # ----- arithmetic: transparent unwrap -----
    #
    # DESIGN — ``_InUnits`` is a DISPLAY-PREFERENCE wrapper, nothing more.
    # ``value ▶ mm/s`` means "when you show me this, prefer mm/s".  It
    # does NOT change the value and it does NOT survive computation.
    #
    # Therefore every arithmetic operator here UNWRAPS to the underlying
    # value, performs the operation on that, and returns the plain
    # result WITHOUT re-wrapping.  The display preference is consumed by
    # the operation — exactly as if ``▶ mm/s`` had never been written.
    #
    # This is the deliberate fix for the old surprise where
    # ``(a ▶ mm/s) / (b ▶ mm/s)`` came back tagged ``mm/s`` (or, after a
    # partial fix, needed special-case label cancellation).  Under the
    # transparent model there is no special case: both operands unwrap,
    # ``a / b`` runs on the bare Sigs/Physicals — which DO track real
    # dimensions correctly via forallpeople — and the result is a plain
    # dimensionless Sig.  No label arithmetic, no cancellation rules, no
    # surprises.
    #
    # IMPORTANT — what "unwrap" unwraps TO.  ``self.value`` is the
    # display number (``quantity / target``, e.g. ``22735.0`` for
    # ``22735 mm ▶ mm``).  Arithmetic must NOT use that: it has already
    # lost its unit, so ``(1 m ▶ mm) + (1 m ▶ m)`` would come out as
    # ``1000 + 1 = 1001`` and ``(22735 mm ▶ mm) + (19600 mm ▶ mm)`` as a
    # unit-less ``42335.0``.  The wrapper therefore keeps the original
    # dimensioned ``quantity`` and unwraps to THAT — so the sum above is
    # ``42.335 m``, a real length, and mixed display units add correctly.
    # Only wrappers with no dimensioned source (``quantity`` is ``None``:
    # symbolic, free-text label, hand-built) fall back to ``value``.
    #
    # Consequence the user must know: a display preference does not
    # propagate through a calculation.  If you write
    #     y := [...] mm ▶ μm
    #     y := y - y₀
    # the subtraction unwraps ``y``; the result is a plain Physical array
    # (in reduced SI) with NO ``μm`` preference.  Re-apply the preference
    # at the point of display: ``plot(x, y ▶ μm)`` or ``pp(result ▶ μm)``.
    # ``▶`` belongs at the EDGE of a computation (on the thing about to
    # be shown), not in the middle of one.
    def _self_value(self):
        """The value this wrapper should expose to arithmetic.

        The dimensioned source ``quantity`` when we have one (a Physical,
        or an array of them), re-wrapped in a ``Sig`` carrying this
        wrapper's sf for the scalar case so significant-figure tracking
        survives (``pp`` rounds the result instead of dumping 17
        digits).  Arrays pass through as-is (their elements are already
        Sigs / Physicals).  Without a dimensioned source (symbolic,
        free-text label or hand-built wrappers) fall back to the display
        number, sf-tagged when the sf is finite."""
        q = getattr(self, "quantity", None)
        v = self.value if q is None else q
        try:
            import numpy as _np
            if isinstance(v, _np.ndarray):
                return v
        except ImportError:
            pass
        if isinstance(v, (list, tuple)) or type(v).__name__ == "CommaArray":
            return v
        if isinstance(v, Sig):
            return v
        if q is not None or math.isfinite(self.sf):
            try:
                return Sig(v, self.sf)
            except Exception:
                return v
        return v

    @staticmethod
    def _unwrap_operand(other):
        """Reduce ``other`` to the value arithmetic should act on.
        ``_InUnits`` → its underlying value with sf re-attached (display
        preference discarded, precision kept); everything else is
        returned untouched so Sig / Physical / number arithmetic
        proceeds normally."""
        if type(other).__name__ == "_InUnits":
            return other._self_value()
        return other

    def __add__(self, other):
        return self._self_value() + self._unwrap_operand(other)

    def __radd__(self, other):
        return self._unwrap_operand(other) + self._self_value()

    def __sub__(self, other):
        return self._self_value() - self._unwrap_operand(other)

    def __rsub__(self, other):
        return self._unwrap_operand(other) - self._self_value()

    def __mul__(self, other):
        return self._self_value() * self._unwrap_operand(other)

    def __rmul__(self, other):
        return self._unwrap_operand(other) * self._self_value()

    def __truediv__(self, other):
        return self._self_value() / self._unwrap_operand(other)

    def __rtruediv__(self, other):
        return self._unwrap_operand(other) / self._self_value()

    def __pow__(self, other):
        return self._self_value() ** self._unwrap_operand(other)

    def __rpow__(self, other):
        return self._unwrap_operand(other) ** self._self_value()

    def __neg__(self):
        return -self._self_value()

    def __pos__(self):
        return +self._self_value()

    def __abs__(self):
        return abs(self._self_value())

    # Comparisons also unwrap — ``(x ▶ mm) < 5 mm`` compares the
    # dimensioned quantities, so the display unit plays no part.  A bare
    # number on the other side (``(x ▶ mm) < 5``; the DSL hands it in as
    # a dimensionless ``Sig``) has no unit to compare against, so it is
    # read in the display unit — i.e. against ``self.value`` — which is
    # what a reader of ``x ▶ mm`` expects.
    def _cmp_pair(self, other):
        bare = _unwrap(other)
        if type(bare).__name__ == "_InUnits":
            return self._self_value(), self._unwrap_operand(bare)
        is_number = (isinstance(bare, (int, float))
                     and not isinstance(bare, bool)) \
            or type(bare).__module__ == "numpy" and not hasattr(bare, "shape") \
            or (type(bare).__module__ == "numpy" and getattr(bare, "shape", None) == ())
        if is_number and not hasattr(bare, "dimensions"):
            return self.value, bare
        return self._self_value(), self._unwrap_operand(other)

    def __lt__(self, other):
        a, b = self._cmp_pair(other)
        return a < b

    def __le__(self, other):
        a, b = self._cmp_pair(other)
        return a <= b

    def __gt__(self, other):
        a, b = self._cmp_pair(other)
        return a > b

    def __ge__(self, other):
        a, b = self._cmp_pair(other)
        return a >= b

    def __eq__(self, other):
        a, b = self._cmp_pair(other)
        return a == b

    def __ne__(self, other):
        a, b = self._cmp_pair(other)
        return a != b

    def __format__(self, spec: str) -> str:
        # ``f"{v:.6g}"`` honours the user's spec on the numeric part
        # while keeping the unit label.  An empty spec falls back to the
        # default ``__repr__`` form.
        if spec:
            return f"{self.value:{spec}} {self.unit_label}"
        return self.__repr__()

    def __hash__(self):
        return hash((self.value, self.unit_label))


def in_units(quantity, target, label: str | None = None) -> _InUnits:
    """Express ``quantity`` in units of ``target``, returning a Mathcad-style
    display wrapper.

    The numeric value of the result is ``float(quantity) / float(target)``
    when both are dimensioned, after unwrapping any ``Sig`` and ``Physical``
    layers.  Conceptually: "how many ``target``-units does ``quantity``
    contain?"  For a velocity ``v = 0.000 315 5 m/s`` and a target
    ``mm/s = Physical(0.001, m·s⁻¹)``, the ratio is ``0.3155`` — i.e.
    ``v`` expressed in mm/s.

    Args:
        quantity:  The value to convert.  Typically a ``Sig`` wrapping a
            ``forallpeople.Physical``, or a bare ``Physical``, or a plain
            number.  ``Sig`` layers are unwrapped before the ratio.
        target:  The unit to express in.  Same accepted shapes — usually
            something like ``mm/s`` or ``μm/s²`` built from the toolkit's
            unit names.  When both ``quantity`` and ``target`` have
            dimensions (Physicals), they must be compatible; mismatched
            dimensions raise ``ValueError``.
        label:  The string to display next to the number.  When ``None``
            (the default), the label is derived from ``repr(target)`` by
            stripping the leading numeric portion — for ``mm/s`` this
            gives ``"m·s⁻¹"`` because forallpeople reduces the prefix.
            When the DSL's ``▶`` operator is used the label is supplied
            automatically as the literal source text after ``▶`` (so
            ``v ▶ mm/s`` displays as ``"… mm/s"`` rather than the
            reduced ``"… m·s⁻¹"``).

    Returns an :class:`_InUnits`, which prints Mathcad-style and behaves
    as a number for arithmetic and formatting.

    Raises:
        ValueError: when ``quantity`` and ``target`` have incompatible
            dimensions (their ratio doesn't reduce to a dimensionless
            scalar).

    >>> # Manual call (function form)
    >>> v_phys = 100 * mm / (317 * s)               # 0.000315… m·s⁻¹
    >>> in_units(v_phys, mm/s, "mm/s")              # 0.3155 mm/s
    >>> in_units(v_phys, mm/s)                      # 0.3155 m·s⁻¹  (default label)
    >>>
    >>> # DSL form (preferred — labels are captured automatically)
    >>> # (d/t) ▶ mm/s   → 0.3155 mm/s
    """
    # Display-preserving unit marker (``Nm`` and friends from
    # ``extra_units``).  Unwrap to the underlying Physical for the
    # ratio, and use the marker's canonical label (``N·m``) rather than
    # the raw source text — so ``τ ▶ Nm`` and the literal ``5 Nm``
    # render identically.  Duck-typed by name — the same soft-coupling
    # as ``_DeltaUnit`` — so sigfig keeps no import of extra_units.
    if type(target).__name__ == "_DisplayUnit":
        label = target.label
        target = target.physical

    # Capture the precision of both operands.  ``in_units`` is
    # conceptually a division — ``quantity / target`` — so the result's
    # precision follows the multiplication/division rule: take the
    # smaller of the two sf counts.  The target unit is usually exact
    # (a literal like ``mm/s`` carries no measured imprecision), so in
    # practice the result's sf is normally the quantity's sf.  Doing
    # this generally rather than assuming the target is exact handles
    # the unusual case where someone expresses one measurement in
    # another (e.g. converting a clock measurement against a tolerance).
    q_sf = _sf_of(quantity)
    t_sf = _sf_of(target)
    result_sf = min(q_sf, t_sf)

    # Radix-target dispatch: ``value ▶ hex`` / ``▶ bin`` / ``▶ oct`` /
    # ``▶ dec``.  The DSL passes ``target`` as whatever the name resolves
    # to — and ``hex``/``bin``/``oct`` are Python BUILTINS, so ``target``
    # is the builtin function object.  ``dec`` isn't a builtin; the DSL
    # passes the string ``"dec"`` via the label, or the bare name fails
    # to resolve — so we also accept the label as a radix-name hint.
    #
    # Detect and delegate to ``radix()``, which returns a ``_Radix``
    # display wrapper (integer base presentation) instead of an
    # ``_InUnits`` (physical-unit presentation).  Both are transparent
    # under arithmetic — same ``▶`` contract — so the user sees one
    # consistent operator.
    _builtin_radix = {hex: "hex", bin: "bin", oct: "oct"}
    if target in _builtin_radix:
        return radix(quantity, _builtin_radix[target])
    # A string label that names a registered radix format (covers
    # ``dec`` and any user-registered format like ``roman``).  The DSL
    # hands the post-``▶`` source text in as ``label``; if that text is
    # a known radix name, treat the whole thing as a radix request.
    if isinstance(label, str) and label.strip() in _RADIX_FORMATTERS:
        return radix(quantity, label.strip())
    # Also: ``target`` itself being a radix-name string (function-form
    # call ``in_units(x, "hex")``).
    if isinstance(target, str) and target.strip() in _RADIX_FORMATTERS:
        return radix(quantity, target.strip())

    # Temperature-scale dispatch: ``temp ▶ degC`` / ``▶ degF`` / ``▶ degR``
    # / ``▶ K``.  The signal is the LABEL string — the DSL hands the
    # post-``▶`` source text in as ``label``, and the unambiguous
    # ``deg*`` spellings reliably name the intended scale.  (``▶ °C``
    # and ``▶ ΔC`` both rewrite to the delta unit and are deliberately
    # NOT treated as scale display — see ``_TEMP_SCALE_NAMES``.)
    #
    # This only applies when ``quantity`` is a genuine pure-temperature
    # Physical — ``_is_pure_temperature`` guards it.  A non-temperature
    # value tagged ``▶ degC`` falls through to the normal unit path
    # (where it raises a dimension mismatch, as it should).
    #
    # The result is a ``_TempScale`` display wrapper: the value stays in
    # kelvin, the chosen scale governs only the rendering, and the tag
    # is transparent under arithmetic — the same contract as ``▶`` for
    # units and integer bases.
    _temp_target = None
    if isinstance(label, str) and label.strip() in _TEMP_SCALE_NAMES:
        _temp_target = _TEMP_SCALE_NAMES[label.strip()]
    elif isinstance(target, str) and target.strip() in _TEMP_SCALE_NAMES:
        _temp_target = _TEMP_SCALE_NAMES[target.strip()]
    if _temp_target is not None:
        # Peel a Sig to inspect the underlying Physical's dimensions.
        _probe = quantity.value if isinstance(quantity, Sig) else quantity
        if _is_pure_temperature(_probe):
            return _TempScale(quantity, _temp_target, sf=result_sf)
        # Not a temperature — ``▶ K`` on a length, say.  Fall through;
        # the normal unit path raises an informative dimension error.

    # Duration display dispatch: ``duration ▶ HMS``.  Like the radix and
    # temperature dispatches, the signal is the LABEL string — ``HMS``
    # (case-insensitive).  ``HMS`` is not a unit, so it is recognised by
    # name, not by a conversion ratio; ``in_units`` is reached because
    # the toolkit defines ``HMS`` / ``hms`` as harmless sentinel objects
    # purely so ``▶ HMS`` resolves to *something* the DSL can pass here.
    #
    # Guarded by ``_is_pure_time`` — ``▶ HMS`` only means "break into
    # d/h/m/s" for a genuine duration.  A frequency or a velocity tagged
    # ``▶ HMS`` falls through to the normal unit path (dimension error).
    # ``R ▶ plusminus`` / ``R ▶ percent`` / ``R ▶ permille`` — interval
    # display forms; ``Z ▶ polar`` / ``Z ▶ rect`` — complex display forms.
    _fn_name = getattr(target, "__name__", None)
    if not isinstance(_fn_name, str):
        _fn_name = label.strip() if isinstance(label, str) else None
    if _fn_name in ("plusminus", "percent", "permille"):
        _probe = _unwrap(quantity)
        if type(_probe).__name__ == "Range":
            return _PMDisplay(quantity, _fn_name, sf=q_sf)
    if _fn_name in ("polar", "rect") and callable(target):
        return target(quantity)

    # A ``datetime.timedelta`` expressed in a time unit: ``td ▶ hour``.
    if type(quantity).__name__ == "timedelta" and hasattr(_unwrap(target), "dimensions"):
        from forallpeople import s as _second
        quantity = Sig(quantity.total_seconds() * _second, _INF)
        q_sf = _INF
        result_sf = t_sf

    _hms_label = None
    if isinstance(label, str):
        _hms_label = label.strip()
    elif isinstance(target, str):
        _hms_label = target.strip()
    if _hms_label is not None and _hms_label.lower() == "hms":
        _probe = quantity.value if isinstance(quantity, Sig) else quantity
        if _is_pure_time(_probe):
            return _HMSDisplay(quantity, sf=result_sf)
        # Not a duration — fall through to the normal path's error.

    # String-target fast-path: ``x ▶ "element"`` (or the function call
    # ``in_units(x, "element")``) is NOT a unit conversion — it's a
    # pure label assignment.  Common when the quantity is a bare count
    # or index that has no physical dimension but the user still wants
    # the plot axis / printout to read "element" rather than showing
    # the value anonymously.  The quantity passes through untouched —
    # scalar or array — and the string becomes the display label.
    #
    # This is distinct from the normal path where ``target`` is a
    # Physical (a real unit like ``mm``); here the conversion ratio
    # would be meaningless, so we skip it entirely.
    if isinstance(target, str):
        result = _InUnits.__new__(_InUnits)
        # Preserve arrays as arrays, scalars as scalars.  Unwrap any
        # Sig layer first so the stored value is a plain number / array
        # (``_unwrap`` is a no-op for non-Sig inputs).  For an array of
        # Sigs (the common ``[0, 1, 6] ▶ "element"`` case), unwrap each
        # element so the result is a clean numeric array rather than a
        # dtype=object array of Sig wrappers.
        unwrapped = _unwrap(quantity)
        try:
            import numpy as _np
            if isinstance(unwrapped, (list, tuple)) or (
                    isinstance(unwrapped, _np.ndarray) and unwrapped.ndim > 0):
                # Element-wise unwrap, then let numpy infer a clean dtype.
                elems = [_unwrap(e) for e in unwrapped]
                try:
                    result.value = _np.asarray(elems, dtype=float)
                except (TypeError, ValueError):
                    # Non-floatable elements — keep as object array.
                    result.value = _np.asarray(elems, dtype=object)
            else:
                arr = _np.asarray(unwrapped)
                result.value = arr.item() if arr.ndim == 0 else arr
        except (ImportError, TypeError, ValueError):
            result.value = unwrapped
        result.unit_label = target
        result.sf = result_sf
        # A free-text label attaches no unit; arithmetic acts on the
        # value itself.
        result.quantity = None
        return result

    # Symbolic fast-path: when ``quantity`` is a sympy Symbol or
    # expression, this isn't a dimensional conversion at all — it's a
    # label-routing request from the DSL's ``▶`` operator.  Users write
    # things like ``plot(expr, t ▶ ms, (0, 5))`` to say "sweep the
    # symbolic variable t, and label the x-axis as ``t [ms]``".  No
    # numeric conversion happens; the symbol is preserved verbatim
    # inside the returned ``_InUnits`` so the plot machinery can spot
    # it and route the label.
    #
    # Detection is duck-typed (avoids importing sympy here) — anything
    # with ``free_symbols`` AND ``subs`` AND ``_op_priority`` is a
    # sympy Basic.  Sigs and Physicals fail the ``_op_priority`` test.
    if (hasattr(quantity, "free_symbols")
            and hasattr(quantity, "subs")
            and hasattr(quantity, "_op_priority")):
        # Build _InUnits with the symbol as the value.  Our ``__init__``
        # tries ``np.asarray(value).item()`` first — that'd reduce a
        # symbol to a 0-dim object array — so we set fields by hand to
        # preserve the symbol verbatim.
        result = _InUnits.__new__(_InUnits)
        result.value = quantity
        result.unit_label = label if label is not None else repr(target)
        result.sf = result_sf
        result.quantity = None
        return result

    # Unwrap Sig layers so we end up with the inner Physical or plain number.
    q_inner = _unwrap(quantity)
    t_inner = _unwrap(target)

    # Compute the ratio.  When both are forallpeople ``Physical``, this
    # produces another Physical whose dimensions reduce to all-zero if the
    # inputs were compatible — that's our dimension check.
    ratio = q_inner / t_inner

    # If the ratio still has non-zero dimensions, the user passed
    # incompatible units.  Inspect via duck-typing rather than importing
    # forallpeople, so this module stays free of that dependency.
    dims = getattr(ratio, "dimensions", None)
    if dims is not None:
        # forallpeople ``Dimensions`` is a NamedTuple of integers;
        # ``any()`` on a tuple works fine.
        if any(getattr(dims, f, 0) for f in
               ("kg", "m", "s", "A", "cd", "K", "mol")):
            raise ValueError(
                f"in_units: dimension mismatch — cannot express "
                f"{q_inner!r} in units of {t_inner!r}"
            )

    # Reduce to plain float (or array of floats).  forallpeople's
    # ``Physical`` defines ``__float__`` to return its magnitude, so
    # the scalar case is one call.  For arrays of Physicals (produced
    # by ``[1, 2, 3] * M_sun ▸ M_sun``), the ratio is itself an array
    # and ``float(ratio)`` raises — convert element-wise instead.
    try:
        import numpy as _np
        if isinstance(ratio, _np.ndarray) and ratio.ndim > 0:
            # Use SI-base ``.value`` directly when a Physical's
            # ``__float__`` crashes (M☉, EeV scales above forallpeople's
            # prefix table).  This is the SI-base magnitude divided by
            # the target's SI-base magnitude — already gives the
            # natural ratio (e.g. 0.122 for ``0.122·M_sun ÷ M_sun``).
            def _to_float_robust(v):
                # Fast path — most elements convert cleanly.
                try:
                    return float(v)
                except Exception:
                    pass
                # forallpeople ``__float__`` crashed.  Compute the
                # ratio via SI-base ``.value`` directly.
                v_si = getattr(v, "value", v)
                t_si = getattr(t_inner, "value", t_inner)
                try:
                    return float(v_si) / float(t_si)
                except Exception:
                    return float("nan")
            # The ``ratio`` array came from element-wise division, but
            # individual entries may still be Physicals — strip them.
            ratio_val = _np.array([_to_float_robust(v) for v in ratio.flat],
                                  dtype=float).reshape(ratio.shape)
        else:
            ratio_val = float(ratio)
    except ImportError:
        ratio_val = float(ratio)

    # Derive a label if the caller didn't supply one.  We do this only
    # when ``in_units`` is called directly — the DSL operator passes the
    # source text explicitly, so this fallback is rarely hit in practice.
    if label is None:
        try:
            target_repr = repr(t_inner)
        except Exception:
            target_repr = ""
        # forallpeople format is "<number> <unit-string>"; split on the
        # first whitespace to recover the unit portion.  When the repr
        # doesn't contain whitespace (e.g. plain ``int`` target), fall
        # back to using the repr verbatim.
        parts = target_repr.split(None, 1)
        label = parts[1] if len(parts) == 2 else (target_repr or "")

    # Keep the dimensioned source so arithmetic on the wrapper stays
    # unit-correct.  A ``Sig`` is kept as-is (its sf and any written-unit
    # tag travel with it, so ``(22735 mm ▶ mm) + (19600 mm ▶ mm)`` prints
    # ``42335 mm`` exactly like the untagged sum); anything else is the
    # bare Physical / array, and ``_self_value`` re-attaches the sf.
    src = quantity if isinstance(quantity, Sig) else q_inner
    return _InUnits(ratio_val, label, sf=result_sf, quantity=src)


# ---------------------------------------------------------------------------
# 6b. Integer radix display — ``value ▶ hex`` and friends
# ---------------------------------------------------------------------------
#
# This mirrors the ``_InUnits`` / ``in_units`` pair, but for integer base
# presentation instead of physical units.  The design contract is identical
# and deliberately so — one mental model for the whole ``▶`` operator:
#
#   * ``value ▶ hex`` attaches a DISPLAY PREFERENCE.  It does not change
#     the integer and it does not survive arithmetic.
#   * Every operator on a ``_Radix`` unwraps to the underlying int, computes
#     there, and returns the plain result.  ``(x ▶ hex) + 1`` is ``x + 1``
#     as a plain int; if you want the sum in hex, write ``(x + 1) ▶ hex``.
#   * Rendering (``repr`` / ``pp`` / ``print``) consults the format tag.
#
# Input vs output are separate concerns — by design.  Typing a literal in
# hex (``1A₁₆``) is a PARSE-TIME job handled by ``rewrite_base_suffixed_numbers``
# in circuit_dsl; by the time a value exists it is a plain ``int`` with no
# memory of how it was written (exactly like Python's own ``0x1A``).  The
# ``▶ hex`` wrapper is the OUTPUT side: an explicit, opt-in, at-the-edge
# request to render in a given base.  The toolkit does not auto-remember a
# literal's input base — that would mean silently wrapping every based
# literal and is the "where did my tag go" trap the unit ``▶`` was just
# fixed to avoid.

# Registry: format-name -> callable(int) -> str.  ``register_radix`` adds
# entries; ``▶ <name>`` looks them up.  Adding a brand-new integer notation
# (Roman numerals, base-12, a custom grouping style) is just registering a
# function here — no core change.
_RADIX_FORMATTERS = {}


def _to_subscript(n: int) -> str:
    """Render a non-negative int as Unicode subscript digits (``16`` →
    ``₁₆``).  Used so the OUTPUT base tag matches the INPUT literal
    notation — ``1A₁₆`` in, ``1A₁₆`` out — for a consistent look."""
    sub = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
    return str(n).translate(sub)


def _format_in_base(value: int, base: int) -> str:
    """Render ``value`` (a Python int) in ``base`` (2..36) using the
    toolkit's subscript-suffix notation: digits followed by the base as
    a Unicode subscript.  Negative values keep a leading ``-``.

    Examples: ``_format_in_base(26, 16)`` → ``'1A₁₆'``;
    ``_format_in_base(10, 2)`` → ``'1010₂'``.
    """
    if not (2 <= base <= 36):
        raise ValueError(f"base must be 2..36, got {base}")
    n = int(value)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n == 0:
        digits = "0"
    else:
        _DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        out = []
        while n:
            out.append(_DIGITS[n % base])
            n //= base
        digits = "".join(reversed(out))
    return f"{sign}{digits}{_to_subscript(base)}"


def register_radix(name, formatter):
    """Register an integer-display format under ``name`` so ``▶ name``
    can use it.  ``formatter`` is a callable ``int -> str``.

    The built-ins (``hex``, ``bin``, ``oct``, ``dec``) are registered at
    import time.  To add e.g. Roman numerals::

        def _roman(n):
            ...                       # int -> 'X..' string
        register_radix("roman", _roman)

    after which ``year ▶ roman`` works.  ``formatter`` should accept any
    Python int (including negative / zero) and return a display string.
    """
    _RADIX_FORMATTERS[str(name)] = formatter


class _Radix:
    """Display wrapper: an integer plus a preferred base/format name.

    Sibling of :class:`_InUnits`.  Created by :func:`radix` (and by the
    DSL's ``▶`` operator when the right-hand side is a radix name like
    ``hex``).  Transparent under arithmetic — see the module comment
    above — so it never surprises a calculation; it only changes how the
    value prints.
    """

    __slots__ = ("value", "fmt_name")

    def __init__(self, value, fmt_name: str):
        # Store as a plain int.  ``_Radix`` is integer-only by intent;
        # base notation for floats isn't meaningful.
        self.value = int(value)
        self.fmt_name = str(fmt_name)

    # ----- rendering -----
    def __repr__(self) -> str:
        fmt = _RADIX_FORMATTERS.get(self.fmt_name)
        if fmt is None:
            # Unknown format name — fail soft: show decimal with a note
            # rather than crashing a whole cell over a typo'd tag.
            return f"{self.value} (unknown format {self.fmt_name!r})"
        return fmt(self.value)

    def __str__(self) -> str:
        return self.__repr__()

    def __format__(self, spec: str) -> str:
        # A non-empty spec means the caller wants standard numeric
        # formatting on the underlying int; honour it.  Empty spec →
        # the radix rendering.
        if spec:
            return format(self.value, spec)
        return self.__repr__()

    # ----- rich display -----
    # Give the bare value a LaTeX form so ``6 ▸ hex`` typesets in a
    # notebook just like ``[[6]] ▸ hex`` (a tagged matrix) and like a
    # plain ``Sig`` number — consistent "use the LaTeX renderer by
    # default" behaviour.  The base-marker subscript (``₁₆``) is kept as
    # the literal Unicode glyph, matching the matrix-cell convention.
    # HTML / Markdown are declined (return ``None``) so the LaTeX form
    # wins Jupyter's MIME priority.
    def _repr_latex_(self):
        try:
            return f"${self.__repr__()}$"
        except Exception:
            return None

    def _repr_html_(self):
        return None

    def _repr_markdown_(self):
        return None

    def __int__(self) -> int:
        return self.value

    def __float__(self) -> float:
        return float(self.value)

    def __index__(self) -> int:
        # Lets a ``_Radix`` be used directly as a list index / in
        # ``range`` / as a slice bound.
        return self.value

    # ----- arithmetic: transparent unwrap (identical contract to _InUnits) -----
    # Every operator unwraps to the underlying int, computes, and returns
    # the PLAIN result.  The format tag is consumed, never propagated:
    # ``(x ▶ hex) + 1`` → plain int.  Re-tag at the display edge.
    @staticmethod
    def _unwrap(other):
        # Peel display/precision wrappers down to the raw value.
        # ``_Radix`` / ``_InUnits`` → their ``.value``; ``Sig`` →
        # unwrap (the DSL wraps every numeric literal in ``Sig`` via
        # ``_S(...)``, so the other operand of ``radix_val & 0x0F`` is a
        # ``Sig``-wrapped int — bitwise ops need it unwrapped).
        t = type(other).__name__
        if t == "_Radix" or t == "_InUnits":
            return _Radix._unwrap(other.value)
        if isinstance(other, Sig):
            return _Radix._unwrap(other.value)
        return other

    @staticmethod
    def _unwrap_int(other):
        """Like ``_unwrap`` but coerces to a plain ``int`` — bitwise
        operators (``& | ^ << >>``) are integer-only and won't accept a
        ``Sig`` or float on either side."""
        v = _Radix._unwrap(other)
        return int(v)

    def __add__(self, o):      return self.value + self._unwrap(o)
    def __radd__(self, o):     return self._unwrap(o) + self.value
    def __sub__(self, o):      return self.value - self._unwrap(o)
    def __rsub__(self, o):     return self._unwrap(o) - self.value
    def __mul__(self, o):      return self.value * self._unwrap(o)
    def __rmul__(self, o):     return self._unwrap(o) * self.value
    def __truediv__(self, o):  return self.value / self._unwrap(o)
    def __rtruediv__(self, o): return self._unwrap(o) / self.value
    def __floordiv__(self, o): return self.value // self._unwrap(o)
    def __rfloordiv__(self, o):return self._unwrap(o) // self.value
    def __mod__(self, o):      return self.value % self._unwrap(o)
    def __rmod__(self, o):     return self._unwrap(o) % self.value
    def __pow__(self, o):      return self.value ** self._unwrap(o)
    def __rpow__(self, o):     return self._unwrap(o) ** self.value
    def __neg__(self):         return -self.value
    def __pos__(self):         return +self.value
    def __abs__(self):         return abs(self.value)

    # Bitwise operators — natural to want on hex/bin-displayed integers.
    # These are integer-only, so the operand is coerced via _unwrap_int
    # (the DSL hands literals in as Sig-wrapped, which ``&`` won't take).
    def __and__(self, o):      return self.value & self._unwrap_int(o)
    def __rand__(self, o):     return self._unwrap_int(o) & self.value
    def __or__(self, o):       return self.value | self._unwrap_int(o)
    def __ror__(self, o):      return self._unwrap_int(o) | self.value
    def __xor__(self, o):      return self.value ^ self._unwrap_int(o)
    def __rxor__(self, o):     return self._unwrap_int(o) ^ self.value
    def __lshift__(self, o):   return self.value << self._unwrap_int(o)
    def __rlshift__(self, o):  return self._unwrap_int(o) << self.value
    def __rshift__(self, o):   return self.value >> self._unwrap_int(o)
    def __rrshift__(self, o):  return self._unwrap_int(o) >> self.value
    def __invert__(self):      return ~self.value

    # Comparisons unwrap too.
    def __eq__(self, o):       return self.value == self._unwrap(o)
    def __ne__(self, o):       return self.value != self._unwrap(o)
    def __lt__(self, o):       return self.value < self._unwrap(o)
    def __le__(self, o):       return self.value <= self._unwrap(o)
    def __gt__(self, o):       return self.value > self._unwrap(o)
    def __ge__(self, o):       return self.value >= self._unwrap(o)
    def __hash__(self):        return hash(self.value)


# Built-in radix formats.  ``hex``/``bin``/``oct`` use the toolkit's
# subscript-suffix notation (``1A₁₆``, ``1010₂``, ``17₈``) so OUTPUT
# matches the INPUT literal style; ``dec`` is plain decimal.
register_radix("hex", lambda n: _format_in_base(n, 16))
register_radix("bin", lambda n: _format_in_base(n, 2))
register_radix("oct", lambda n: _format_in_base(n, 8))
register_radix("dec", lambda n: str(int(n)))
# ``base16`` / ``base2`` / ``base8`` aliases, plus a generic note: any
# 2..36 base is reachable via ``radix(value, N)`` directly.
register_radix("base16", _RADIX_FORMATTERS["hex"])
register_radix("base2", _RADIX_FORMATTERS["bin"])
register_radix("base8", _RADIX_FORMATTERS["oct"])
register_radix("base10", _RADIX_FORMATTERS["dec"])


def radix(value, fmt):
    """Attach an integer-display preference to ``value``.

    ``fmt`` may be:
      * a registered format name — ``'hex'``, ``'bin'``, ``'oct'``,
        ``'dec'``, or anything added via :func:`register_radix`
      * an int 2..36 — display in that base directly (``radix(255, 16)``
        → ``FF₁₆``)

    Returns a :class:`_Radix` — transparent under arithmetic, renders in
    the chosen format.  This is the function form; the DSL's ``▶``
    operator calls it automatically, so ``value ▶ hex`` is the same as
    ``radix(value, "hex")``.

    Integer-only: ``value`` is coerced with ``int()``.  A base notation
    for a float is not meaningful, so passing one truncates — wrap the
    integer part deliberately if that is what you want.
    """
    # An int fmt means "this base".  The DSL wraps numeric literals in
    # ``Sig`` (via ``_S(...)``), so a literal ``16`` arrives here as a
    # Sig-wrapped int — unwrap it first.  ``value`` may be Sig-wrapped
    # too; ``_Radix.__init__`` coerces with ``int()`` which handles it.
    if isinstance(fmt, Sig):
        fmt = fmt.value
    # Re-tagging: if ``value`` is ALREADY a display wrapper (a prior
    # ``▶`` tag), unwrap it to its underlying number before applying the
    # new format.  This makes chained display operators behave as a
    # human expects — the LAST tag wins, none of them nest:
    #   255 ▶ hex          → FF₁₆
    #   255 ▶ hex ▶ hex    → FF₁₆      (idempotent — re-tagging hex with hex)
    #   255 ▶ hex ▶ dec    → 255       (dec reverts the display)
    # A ``_Radix`` / ``_InUnits`` carries its raw value in ``.value``;
    # peel one (or a stack) off so we re-tag the number, not the wrapper.
    while type(value).__name__ in ("_Radix", "_InUnits"):
        value = value.value
    # Broadcast over a sequence: ``[10, 20, 255] ▶ hex`` tags each
    # element, so a radix display works on an array the same way a unit
    # does (``[10,20,30] V`` → ``[10 V, 20 V, 30 V]``).  We recurse so a
    # nested list works too, and so each element passes through the same
    # unwrap/​coerce path.  A ``CommaArray`` (the DSL's array literal)
    # and plain ``list``/``tuple`` are all handled; the result is a
    # ``list`` of per-element ``_Radix`` whose ``repr`` shows the
    # formatted elements (``[A₁₆, 14₁₆, FF₁₆]``).
    if isinstance(value, (list, tuple)) or type(value).__name__ == "_CommaArray":
        return [radix(v, fmt) for v in value]
    # A sympy matrix: ``M ▸ hex`` is a DISPLAY preference, not a
    # transformation — consistent with ``▸`` being inert under operations
    # everywhere else in the toolkit.  So we keep ``M`` a real matrix and
    # just record the radix format on it (``_dsl_radix``); the rendering
    # layer (``pp`` / ``_repr_latex_``) reads that tag and formats each
    # cell in the chosen base.  Because the tag lives on the instance and
    # operations build fresh matrices, it naturally does NOT survive
    # ``Mᵀ`` / ``M*N`` / ``M.det()`` — those give plain matrices — which
    # is the intended "display-only" behaviour.  Detected via the class
    # MRO so the DSL's ``_as_matrix`` shim subclass is recognised.
    if hasattr(value, "tolist") and any(
        (c.__module__ or "").startswith("sympy.matrices")
        for c in type(value).__mro__
    ):
        try:
            tagged = value.copy()
            tagged._dsl_radix = str(fmt)
            return tagged
        except Exception:
            # If the matrix can't carry the attribute for any reason,
            # fall back to a list-of-_Radix (still displays; loses ops).
            return [[radix(c, fmt) for c in row] for row in value.tolist()]
    if isinstance(fmt, (int, float)) and not isinstance(fmt, bool):
        base = int(fmt)
        name = f"_base{base}"
        if name not in _RADIX_FORMATTERS:
            register_radix(name, lambda n, _b=base: _format_in_base(n, _b))
        return _Radix(value, name)
    return _Radix(value, str(fmt))


# ---------------------------------------------------------------------------
# 6c. Temperature-scale display — ``temp ▶ degC`` and friends
# ---------------------------------------------------------------------------
#
# A sibling of ``_Radix``, with the identical display-preference
# contract.  ``temp ▶ degC`` tags a temperature for display on the
# Celsius scale; the underlying value stays kelvin, and arithmetic
# unwraps the tag away.  This is the OUTPUT mirror of the ``from_degC``
# / ``from_degF`` input constructors:
#
#   from_degC(22)        — INPUT  : 22 °C reading  -> 295.15 K stored
#   (295.15 K) ▶ degC    — OUTPUT : 295.15 K value -> shown as "22 °C"
#
# Both halves are offset-correct and both leave the stored value in
# kelvin; only the rendering differs.

# Recognised temperature-scale target names → the ``scale`` code that
# ``_format_temperature`` understands.
#
# Only the EXPLICIT ``deg*`` names trigger scale display.  This is a
# deliberate, conservative choice: the DSL's temperature-rewrite turns
# a bare ``°C`` *after* ``▶`` into ``deltaC`` — the SAME text that a
# literal ``▶ ΔC`` produces — so ``▶ °C`` and ``▶ ΔC`` are
# indistinguishable by the time ``in_units`` runs.  One means "display
# on the Celsius scale", the other means "express as the delta-Celsius
# unit", and those give different numbers for a temperature difference.
# Rather than guess, the toolkit reserves scale display for the
# unambiguous spellings ``degC`` / ``degF`` / ``degR`` (which the
# rewrite never produces) and ``K``.  Write ``temp ▶ degC`` to display
# in Celsius; ``▶ °C`` / ``▶ ΔC`` stay delta-unit conversions.
_TEMP_SCALE_NAMES = {
    "degC": "degC", "celsius": "degC",
    "degF": "degF", "fahrenheit": "degF",
    "degR": "degR", "rankine": "degR",
    "K": "K", "kelvin": "K",
}


class _TempScale:
    """Display wrapper: a temperature ``Physical`` plus a target scale.

    Created by :func:`in_units` when the target names a temperature
    scale and the quantity is a pure-temperature ``Physical``.
    Transparent under arithmetic — every operator unwraps to the
    underlying kelvin value, computes there, and returns the plain
    result; the scale tag is consumed by the computation, never
    propagated.  ``temp ▶ degC`` only changes how ``temp`` prints.
    """

    __slots__ = ("value", "scale", "sf")

    def __init__(self, value, scale: str, sf=None):
        # ``value`` is the temperature Physical (kelvin-stored), possibly
        # Sig-wrapped.  Keep it as handed in; arithmetic unwraps later.
        self.value = value
        self.scale = scale            # "degC" / "degF" / "degR" / "K"
        if sf is None:
            sf = _sf_of(value)
        self.sf = sf

    # ----- rendering -----
    def __repr__(self) -> str:
        # Reach the underlying Physical (peel a Sig if present) and hand
        # it to the shared temperature formatter with this scale.
        phys = self.value
        if isinstance(phys, Sig):
            phys = phys.value
        try:
            return _format_temperature(phys, self.sf, self.scale)
        except Exception:
            # If anything is off (not actually a temperature, say),
            # fall back to a plain repr rather than crashing display.
            return repr(self.value)

    def __str__(self) -> str:
        return self.__repr__()

    # ----- rich display -----
    def _repr_latex_(self):
        """Typeset the scale-converted temperature (``T ▸ degC`` →
        ``22 °C``) as LaTeX, so it renders like every other scalar
        instead of falling to plain text.  Built from the same
        ``_format_temperature`` string as ``__repr__`` (so display
        matches), splitting the number from the scale suffix and setting
        the degree marker as ``^{\\circ}`` with the scale letter upright.
        HTML/Markdown decline so the LaTeX hook wins MIME priority.
        """
        try:
            text = self.__repr__()              # e.g. "22.000 °C" / "295.15 K"
            parts = text.split(" ", 1)
            num = _sci_to_latex(parts[0])
            if len(parts) == 2 and parts[1]:
                suffix = parts[1]
                if suffix.startswith("°"):
                    # ``°C`` / ``°F`` → ``^{\circ}\mathrm{C}``.
                    letter = suffix[1:]
                    unit = r"{}^{\circ}\mathrm{" + letter + "}"
                else:
                    # Plain ``K`` (or ``degR`` spelled out).
                    unit = r"\mathrm{" + suffix + "}"
                return f"${num}\\ {unit}$"
            return f"${num}$"
        except Exception:
            return None

    def _repr_html_(self):
        return None

    def _repr_markdown_(self):
        return None

    def __format__(self, spec: str) -> str:
        if spec:
            return format(_temp_unwrap(self), spec)
        return self.__repr__()

    # ----- arithmetic: transparent unwrap (same contract as _Radix) -----
    # Every operator unwraps to the underlying value and returns a plain
    # result — the scale tag does not survive a computation.
    def __add__(self, o):      return _temp_unwrap(self) + _temp_unwrap(o)
    def __radd__(self, o):     return _temp_unwrap(o) + _temp_unwrap(self)
    def __sub__(self, o):      return _temp_unwrap(self) - _temp_unwrap(o)
    def __rsub__(self, o):     return _temp_unwrap(o) - _temp_unwrap(self)
    def __mul__(self, o):      return _temp_unwrap(self) * _temp_unwrap(o)
    def __rmul__(self, o):     return _temp_unwrap(o) * _temp_unwrap(self)
    def __truediv__(self, o):  return _temp_unwrap(self) / _temp_unwrap(o)
    def __rtruediv__(self, o): return _temp_unwrap(o) / _temp_unwrap(self)
    def __pow__(self, o):      return _temp_unwrap(self) ** _temp_unwrap(o)
    def __rpow__(self, o):     return _temp_unwrap(o) ** _temp_unwrap(self)
    def __neg__(self):         return -_temp_unwrap(self)
    def __pos__(self):         return +_temp_unwrap(self)
    def __abs__(self):         return abs(_temp_unwrap(self))

    # Comparisons unwrap too.
    def __eq__(self, o):       return _temp_unwrap(self) == _temp_unwrap(o)
    def __ne__(self, o):       return _temp_unwrap(self) != _temp_unwrap(o)
    def __lt__(self, o):       return _temp_unwrap(self) < _temp_unwrap(o)
    def __le__(self, o):       return _temp_unwrap(self) <= _temp_unwrap(o)
    def __gt__(self, o):       return _temp_unwrap(self) > _temp_unwrap(o)
    def __ge__(self, o):       return _temp_unwrap(self) >= _temp_unwrap(o)
    def __hash__(self):        return hash(_temp_unwrap(self))

    def __float__(self):
        # The kelvin magnitude — float() of a temperature is its SI
        # value, consistent with how a bare Physical behaves.
        return float(_temp_unwrap(self))


def _temp_unwrap(other):
    """Reduce ``other`` to the value arithmetic should act on.  A
    ``_TempScale`` → its underlying temperature value (scale tag
    discarded); everything else passes through untouched."""
    if type(other).__name__ == "_TempScale":
        return other.value
    return other


# ---------------------------------------------------------------------------
# 6c-bis. Temperature DIFFERENCE — ``ΔC`` / ``ΔK`` / ``ΔF``
# ---------------------------------------------------------------------------
#
# A persistent temperature-*difference* (interval) type, distinct from an
# absolute temperature.  Unlike ``_TempScale`` (a display tag that
# vanishes under arithmetic), a ``_DeltaTemp`` is SEMANTIC: it survives and
# propagates per the truth-table below, because ``10 ΔC + 5 ΔC`` must stay
# ``15 ΔC`` and ``100 °C − 10 °C`` must PRODUCE a ``ΔC``.
#
# Storage: the span magnitude in KELVIN (a ΔC of 1 and a ΔK of 1 are both
# 1 K of span; a ΔF of 1 is 5/9 K), plus the natural display unit so the
# value prints in whatever Δ-flavour it was written in unless ``▸``-converted.
#
# Arithmetic (D=delta, A=absolute temp, n=number, X=other quantity):
#   D + D → D   (spans add; keep LEFT operand's unit)
#   D − D → D
#   D · n → D   n · D → D   D / n → D     (scale a span)
#   D / D → n   (dimensionless ratio of spans)
#   D + A → A   A + D → A   A − D → A      (handled in Stage 2 via Physical)
#   D · X → plain Physical (the ΔK-magnitude leaks into the product;
#           e.g. ``α · ΔT`` thermal-expansion — tag drops, value is ΔK)
#
# Display: ``15 ΔC`` / ``15 ΔK`` / ``27 ΔF`` (sleek form, no ``°`` glyph),
# LaTeX ``\Delta\mathrm{C}`` etc.

_DELTA_UNIT_SCALE = {"ΔK": 1.0, "ΔC": 1.0, "ΔF": 5.0 / 9.0}


def _delta_unit_for_pair(a, b):
    """Choose the Δ-unit for a temperature DIFFERENCE ``a − b``.

    Absolute temperatures written in °C/°F now carry a ``_temp_scale``
    hint (set by the ``from_deg*`` constructors); a Kelvin literal carries
    none.  So: if either operand is °C-scaled → ``ΔC``; °F-scaled → ``ΔF``;
    otherwise (both untagged ⇒ Kelvin) → ``ΔK``.  The check prefers a
    concrete °C/°F hint over the bare-K default, and if the two disagree
    falls back to ``ΔK`` (SI).  ``ΔC`` and ``ΔK`` are numerically equal so
    a wrong guess is only cosmetic.
    """
    sa = getattr(a, "_temp_scale", None)
    sb = getattr(b, "_temp_scale", None)
    hints = {s for s in (sa, sb) if s and s != "K"}
    if hints == {"degC"}:
        return "ΔC"
    if hints == {"degF"}:
        return "ΔF"
    if not hints:
        return "ΔK"          # both Kelvin (no scale hint)
    # Mixed/ambiguous (e.g. one °C, one °F) → SI default.
    return "ΔK"


class _DeltaTemp:
    """A temperature difference (interval): a kelvin span + a natural unit.

    ``unit`` is one of ``"ΔK"`` / ``"ΔC"`` / ``"ΔF"``.  ``kelvin`` is the
    span magnitude in kelvin (unit-independent); the displayed number is
    ``kelvin / scale(unit)`` so ``ΔF`` shows the larger Fahrenheit count.
    """

    __slots__ = ("kelvin", "unit", "sf")

    def __init__(self, kelvin, unit="ΔK", sf=None):
        self.kelvin = float(kelvin)
        self.unit = unit if unit in _DELTA_UNIT_SCALE else "ΔK"
        self.sf = _INF if sf is None else sf

    # ----- the displayed number in the natural unit -----
    def _display_value(self):
        return self.kelvin / _DELTA_UNIT_SCALE[self.unit]

    # ----- rendering -----
    def __repr__(self):
        num = _format_sig(self._display_value(), self.sf)
        return f"{num} {self.unit}"

    def __str__(self):
        return self.__repr__()

    def _repr_latex_(self):
        try:
            num = _sci_to_latex(_format_sig(self._display_value(), self.sf))
            letter = self.unit[1:]               # "K" / "C" / "F"
            return f"${num}\\ \\Delta\\mathrm{{{letter}}}$"
        except Exception:
            return None

    def _repr_html_(self):
        return None

    def _repr_markdown_(self):
        return None

    def __format__(self, spec):
        if spec:
            return format(self._display_value(), spec)
        return self.__repr__()

    # ----- conversion (``▸ ΔK`` / ``▸ ΔC`` / ``▸ ΔF``) -----
    def to_unit(self, unit):
        """Return a new ``_DeltaTemp`` displaying in ``unit`` (same span)."""
        if unit not in _DELTA_UNIT_SCALE:
            return self
        return _DeltaTemp(self.kelvin, unit, self.sf)

    # ----- arithmetic (the truth-table) -----
    def __add__(self, o):
        if isinstance(o, _DeltaTemp):
            # D + D → D, keep LEFT unit; sf by addsub rule on kelvin spans.
            sf = _addsub_sf(self.kelvin, o.kelvin, self.sf, o.sf,
                            self.kelvin + o.kelvin)
            return _DeltaTemp(self.kelvin + o.kelvin, self.unit, sf)
        ov = _unwrap(o)
        if _is_pure_temperature(ov):
            # D + A (absolute) → A: shift the point by the span.  Wrap in
            # ``Sig`` so the result goes through the toolkit's temperature
            # formatter (proper ``K``) rather than forallpeople's raw
            # ``Physical`` repr, which mislabels kelvin as ``°C``.
            sf = min(self.sf, _sf_of(o))
            return Sig(ov + self._as_kelvin(), sf)
        # D + plain number / quantity → kelvin span plus it (tag drops).
        return self._as_kelvin() + ov if _is_physical(ov) else self.kelvin + ov

    def __radd__(self, o):
        return self.__add__(o)

    def __sub__(self, o):
        if isinstance(o, _DeltaTemp):
            sf = _addsub_sf(self.kelvin, o.kelvin, self.sf, o.sf,
                            self.kelvin - o.kelvin)
            return _DeltaTemp(self.kelvin - o.kelvin, self.unit, sf)
        ov = _unwrap(o)
        if _is_pure_temperature(ov):
            sf = min(self.sf, _sf_of(o))
            return Sig(self._as_kelvin() - ov, sf)
        return self._as_kelvin() - ov if _is_physical(ov) else self.kelvin - ov

    def __rsub__(self, o):
        # A − D → A (shift a point down by the span).
        ov = _unwrap(o)
        if _is_pure_temperature(ov):
            sf = min(self.sf, _sf_of(o))
            return Sig(ov - self._as_kelvin(), sf)
        if _is_physical(ov):
            return ov - self._as_kelvin()
        return ov - self.kelvin

    def __mul__(self, o):
        if isinstance(o, _DeltaTemp):
            # D · D → a kelvin² Physical (rare); use both spans.
            return self._as_kelvin() * o._as_kelvin()
        ov = _unwrap(o)
        if _is_number(ov):
            # D · n → D (scale the span); keep unit.
            sf = min(self.sf, _sf_of(o))
            return _DeltaTemp(self.kelvin * ov, self.unit, sf)
        # D · X → plain Physical: the Δ contributes its kelvin span as a
        # plain ``K`` Physical, then normal unit algebra runs (so
        # ``α[K⁻¹] · ΔT`` → dimensionless).  Wrap in ``Sig`` carrying the
        # combined sf so sig-fig tracking survives the product.
        return Sig(self._as_kelvin() * ov, min(self.sf, _sf_of(o)))

    def __rmul__(self, o):
        return self.__mul__(o)

    def __truediv__(self, o):
        if isinstance(o, _DeltaTemp):
            # D / D → dimensionless ratio.
            return Sig(self.kelvin / o.kelvin, min(self.sf, o.sf))
        ov = _unwrap(o)
        if _is_number(ov):
            sf = min(self.sf, _sf_of(o))
            return _DeltaTemp(self.kelvin / ov, self.unit, sf)
        # D / X → plain Physical (kelvin span / X); keep sf.
        return Sig(self._as_kelvin() / ov, min(self.sf, _sf_of(o)))

    def __rtruediv__(self, o):
        # X / D → plain Physical (X / kelvin span); keep sf.
        return Sig(_unwrap(o) / self._as_kelvin(), min(self.sf, _sf_of(o)))

    def _as_kelvin(self):
        """The span as a plain forallpeople ``Physical`` in kelvin, for
        mixing into ordinary unit algebra (products with coefficients,
        absolute temperatures, …)."""
        import forallpeople as _si
        return self.kelvin * _si.K

    def __neg__(self):
        return _DeltaTemp(-self.kelvin, self.unit, self.sf)

    def __pos__(self):
        return self

    def __abs__(self):
        return _DeltaTemp(abs(self.kelvin), self.unit, self.sf)

    # ----- comparisons (compare kelvin spans) -----
    def __eq__(self, o):
        return self.kelvin == (o.kelvin if isinstance(o, _DeltaTemp)
                               else _unwrap(o))

    def __ne__(self, o):
        return not self.__eq__(o)

    def __lt__(self, o):
        return self.kelvin < (o.kelvin if isinstance(o, _DeltaTemp)
                              else _unwrap(o))

    def __le__(self, o):
        return self.kelvin <= (o.kelvin if isinstance(o, _DeltaTemp)
                               else _unwrap(o))

    def __gt__(self, o):
        return self.kelvin > (o.kelvin if isinstance(o, _DeltaTemp)
                              else _unwrap(o))

    def __ge__(self, o):
        return self.kelvin >= (o.kelvin if isinstance(o, _DeltaTemp)
                               else _unwrap(o))

    def __hash__(self):
        return hash(self.kelvin)

    def __float__(self):
        # float() of a Δ is its KELVIN span (SI magnitude), consistent
        # with how a bare Physical's float() is its SI value.
        return float(self.kelvin)


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)

def _is_physical(x):
    return hasattr(x, "value") and hasattr(x, "dimensions")


# ---------------------------------------------------------------------------
# 6d. Duration display — ``duration ▶ HMS``
# ---------------------------------------------------------------------------
#
# Another sibling of ``_Radix`` / ``_TempScale``, same display-preference
# contract.  ``duration ▶ HMS`` tags a time quantity to print broken
# into days / hours / minutes / seconds (``1h 01m 01s``).  The stored
# value is unchanged — still a duration in seconds — and the tag is
# consumed by any arithmetic.

class _PMDisplay:
    """Display wrapper: render an interval as ``centre ± tol`` (``▶ plusminus``)
    or ``centre ± tol%`` (``▶ percent`` / ``▶ permille``).

    Created by :func:`in_units` when the target is one of those three
    helpers and the quantity unwraps to a ``Range``.  Transparent under
    arithmetic: every operator unwraps to the underlying interval.
    """

    __slots__ = ("value", "sf", "mode")

    def __init__(self, value, mode, sf=None):
        self.value = value
        self.mode = mode
        self.sf = _sf_of(value) if sf is None else sf

    def _range(self):
        v = self.value
        while isinstance(v, Sig):
            v = v.value
        return v

    def __repr__(self) -> str:
        r = self._range()
        centre, tol = r.center, r.tol
        c_txt = _format_sig(centre, self.sf)
        if self.mode == "plusminus":
            return f"{c_txt} ± {_format_sig(tol, self.sf)}"
        try:
            ratio = float(tol / centre)
        except Exception:
            return f"{c_txt} ± {_format_sig(tol, self.sf)}"
        if self.mode == "permille":
            return f"{c_txt} ± {_format_sig(ratio * 1000, min(self.sf, 3))}‰"
        return f"{c_txt} ± {_format_sig(ratio * 100, min(self.sf, 3))}%"

    def __str__(self) -> str:
        return self.__repr__()

    def __format__(self, spec: str) -> str:
        return format(self.value, spec) if spec else self.__repr__()

    def _repr_latex_(self):
        return None

    def __add__(self, o):      return _pm_unwrap(self) + _pm_unwrap(o)
    def __radd__(self, o):     return _pm_unwrap(o) + _pm_unwrap(self)
    def __sub__(self, o):      return _pm_unwrap(self) - _pm_unwrap(o)
    def __rsub__(self, o):     return _pm_unwrap(o) - _pm_unwrap(self)
    def __mul__(self, o):      return _pm_unwrap(self) * _pm_unwrap(o)
    def __rmul__(self, o):     return _pm_unwrap(o) * _pm_unwrap(self)
    def __truediv__(self, o):  return _pm_unwrap(self) / _pm_unwrap(o)
    def __rtruediv__(self, o): return _pm_unwrap(o) / _pm_unwrap(self)
    def __pow__(self, o):      return _pm_unwrap(self) ** _pm_unwrap(o)
    def __neg__(self):         return -_pm_unwrap(self)
    def __abs__(self):         return abs(_pm_unwrap(self))
    def __eq__(self, o):       return _pm_unwrap(self) == _pm_unwrap(o)
    def __hash__(self):        return hash(_pm_unwrap(self))
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.value, name)


def _pm_unwrap(x):
    return x.value if isinstance(x, _PMDisplay) else x


class _HMSDisplay:
    """Display wrapper: render a duration as ``d/h/m/s`` (``▶ HMS``).

    Created by :func:`in_units` when the target is ``HMS`` and the
    quantity is a pure-time ``Physical``.  Transparent under arithmetic
    — every operator unwraps to the underlying duration, computes
    there, and returns the plain result; the HMS tag never propagates.
    """

    __slots__ = ("value", "sf")

    def __init__(self, value, sf=None):
        # ``value`` is the duration — a Physical of dimension time,
        # possibly Sig-wrapped.  Kept as handed in; arithmetic unwraps.
        self.value = value
        # ``sf`` is the significant-figure count of the duration,
        # carried so the rendered seconds field reflects the precision
        # the value actually claims (``100.0 s / 3`` → ``33.33s``, not
        # ``33.333333s``).  Captured from the value when not supplied.
        if sf is None:
            sf = _sf_of(value)
        self.sf = sf

    def _seconds(self) -> float:
        """The duration as a plain number of seconds.  forallpeople
        stores time in SI base, so a time Physical's ``.value`` IS the
        second-count; peel a Sig first if present."""
        v = self.value
        if isinstance(v, Sig):
            v = v.value
        if hasattr(v, "value") and hasattr(v, "dimensions"):
            return float(object.__getattribute__(v, "value"))
        return float(v)

    def __repr__(self) -> str:
        try:
            return _format_hms(self._seconds(), self.sf)
        except Exception:
            return repr(self.value)

    def __str__(self) -> str:
        return self.__repr__()

    def __format__(self, spec: str) -> str:
        if spec:
            return format(_hms_unwrap(self), spec)
        return self.__repr__()

    # ----- arithmetic: transparent unwrap (same contract as _TempScale) -----
    def __add__(self, o):      return _hms_unwrap(self) + _hms_unwrap(o)
    def __radd__(self, o):     return _hms_unwrap(o) + _hms_unwrap(self)
    def __sub__(self, o):      return _hms_unwrap(self) - _hms_unwrap(o)
    def __rsub__(self, o):     return _hms_unwrap(o) - _hms_unwrap(self)
    def __mul__(self, o):      return _hms_unwrap(self) * _hms_unwrap(o)
    def __rmul__(self, o):     return _hms_unwrap(o) * _hms_unwrap(self)
    def __truediv__(self, o):  return _hms_unwrap(self) / _hms_unwrap(o)
    def __rtruediv__(self, o): return _hms_unwrap(o) / _hms_unwrap(self)
    def __pow__(self, o):      return _hms_unwrap(self) ** _hms_unwrap(o)
    def __rpow__(self, o):     return _hms_unwrap(o) ** _hms_unwrap(self)
    def __neg__(self):         return -_hms_unwrap(self)
    def __pos__(self):         return +_hms_unwrap(self)
    def __abs__(self):         return abs(_hms_unwrap(self))

    def __eq__(self, o):       return _hms_unwrap(self) == _hms_unwrap(o)
    def __ne__(self, o):       return _hms_unwrap(self) != _hms_unwrap(o)
    def __lt__(self, o):       return _hms_unwrap(self) < _hms_unwrap(o)
    def __le__(self, o):       return _hms_unwrap(self) <= _hms_unwrap(o)
    def __gt__(self, o):       return _hms_unwrap(self) > _hms_unwrap(o)
    def __ge__(self, o):       return _hms_unwrap(self) >= _hms_unwrap(o)
    def __hash__(self):        return hash(_hms_unwrap(self))

    def __float__(self):
        # The second-count.  Use ``_seconds()`` (which reads the
        # Physical's SI-base ``.value``) rather than ``float()`` of the
        # unwrapped Physical — the latter returns forallpeople's
        # prefix-scaled display magnitude (``3661 s`` would give
        # ``3.661``, in kiloseconds), not the plain second-count.
        return self._seconds()


def _hms_unwrap(other):
    """Reduce ``other`` to the value arithmetic should act on.  An
    ``_HMSDisplay`` → its underlying duration (display tag discarded);
    everything else passes through untouched."""
    if type(other).__name__ == "_HMSDisplay":
        return other.value
    return other


# ---------------------------------------------------------------------------
# 7. Source-transform helper
# ---------------------------------------------------------------------------

def wrap_numeric_literals(source: str) -> str:
    """Wrap every numeric literal in ``source`` with ``_S(<value>, <sf>)``.

    Run this *last* in your transform pipeline, after implicit multiplication
    and after rewrites that match raw digits (percent, factorial, …).  By the
    time we get here every digit run that survives is a genuine numeric
    literal, and wrapping it with a function call is safe.

    When :func:`set_decimal_literals` is on, the inner value of every
    *floating-point* literal (anything with ``.`` or ``e``/``E``) is also
    wrapped in ``_R('...')`` — a call to :func:`_R` that constructs a
    sympy ``Rational`` from the literal string.  Integer, hex/oct/bin,
    and complex literals are unaffected by the toggle and stay as Python
    native types.
    """
    tokens = token_utils.tokenize(source)
    if not tokens:
        return source

    decimal_mode = _DECIMAL_LITERAL_MODE

    for tok in tokens:
        if tok.is_number():
            literal = tok.string
            sf = sigfigs_of_literal(literal)
            sf_str = "_INF" if math.isinf(sf) else str(int(sf))
            if decimal_mode and _is_rationalisable_literal(literal):
                # Pass the literal as a *string* so Rational gets the
                # exact decimal value, not the lossy float.
                tok.string = f"_S(_R('{literal}'), {sf_str})"
            else:
                tok.string = f"_S({literal}, {sf_str})"

    return token_utils.untokenize(tokens)


# The polar (``5 ∠ 30°``) formatter needs ``circuit_dsl._Polar``; that
# module imports this one, so register lazily on first use of ``_format_sig``
# via ``_register_polar_formatter`` — called from ``circuit_dsl`` once it
# has finished loading.
