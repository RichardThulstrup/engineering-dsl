from ideas import import_hook
import token_utils
import re
import ast
import statistics
import numpy as np
import math

from .sigfig import (
    Sig, _S, _INF, _R, exact, measured, sigfigs_of,
    wrap_numeric_literals, in_units, radix, register_radix,
    set_decimal_literals, get_decimal_literals, decimal_literals,
    _format_sig, _magnitude,
)


# Names the source-transformed user code references at runtime.
# Underscore-prefixed names need to be in __all__ for `from ... import *`
# to pull them in.
__all__ = [
    "_S", "_INF", "_R", "Sig", "exact", "measured", "sigfigs_of",
    "in_units", "radix", "register_radix", "_wu",
    "set_decimal_literals", "get_decimal_literals", "decimal_literals",
    "Range", "parallel", "percent", "permille", "fact", "mod",
    "plusminus", "σ", "Σ", "mean", "sqrt", "add_hook",
    # Math/engineering additions:
    "Γ", "Π", "log10", "log2", "ln", "floor", "ceil",
    "phasor", "to_dB_v", "to_dB_p", "from_dB_v", "from_dB_p",
    "approx",
    # Identifier-protection API:
    "PROTECTED_NAMES", "protect", "unprotect",
    "protect_si_units", "protect_constants", "protect_all",
    "list_protected", "clear_protections",
    # Emitted by rewrite_list_unit_multiply — must be importable.
    "_CommaArray", "CommaArray", "_range_inc", "_range_ineq", "_str_range", "_as_matrix",
    # Emitted by rewrite_interval_dots — the ``a ‥ b`` closed-interval form.
    "_interval",
    "_idx", "_idx_set",
    # Emitted by rewrite_abs_bars — the |…| bars dispatch through this.
    "_abs_or_size",
]

# ---------------------------------------------------------------------------
# Identifier protection
# ---------------------------------------------------------------------------
# Python has no native ``const`` keyword, but the source-rewriting hook gives
# us a natural choke-point: every cell or module passes through
# ``transform_source``, which can refuse to compile code that rebinds names
# we've declared off-limits.  The check is at the AST level, so it catches
# every form of binding — plain assignment, augmented assignment, walrus,
# tuple unpacking, ``for``-loop targets, ``with ... as``, function/class
# definitions, imports, ``except ... as``, and ``del``.
#
# Default name sets are provided but the protection is OPT-IN: nothing is
# protected until the user calls one of ``protect_*()``.  This keeps imports
# of ``calc_symbols`` (which themselves assign to ``V``, ``Ω``, ``c`` …)
# from tripping over their own bindings.

PROTECTED_NAMES: set = set()

_SI_UNIT_NAMES_UNAMBIGUOUS = frozenset({
    # Multi-letter units don't normally collide with engineering variable
    # names, so they are safe to protect by default.
    "kg", "mol", "cd",
    "Hz", "Pa", "Wb", "rad", "sr",
})

_SI_UNIT_NAMES_AMBIGUOUS = frozenset({
    # Single-letter unit names that are commonly also variable names in
    # engineering work — `C` for capacitance, `F` for force, `H` for
    # height, `T` for temperature, `m` for mass, `V` for a voltage
    # variable, `g` for gravity, etc.  Off by default; turn them on
    # with ``protect_si_units(strict=True)``.
    "m", "s", "A", "K", "g",
    "V", "W", "F", "H", "J", "N", "C", "T", "S", "Ω",
})

# Combined set retained for backwards compat — the union of both.
_SI_UNIT_NAMES = _SI_UNIT_NAMES_UNAMBIGUOUS | _SI_UNIT_NAMES_AMBIGUOUS

_SI_PREFIX_NAMES = frozenset({
    "prefix_p", "prefix_n", "prefix_μ", "prefix_d", "prefix_c",
    "prefix_m", "prefix_k", "prefix_M", "prefix_G", "prefix_T",
})

_PREFIXED_UNIT_NAMES = frozenset({
    "pA", "nA", "μA", "mA",
    "mΩ", "kΩ", "MΩ", "GΩ",
    "nW", "mW",
    "pF", "nF", "μF", "mF",
    "pH", "nH", "μH", "mH",
    "kHz", "MHz", "GHz", "THz",
    "pV", "nV", "μV", "mV",
    "ps", "ns", "μs", "ms",
    "pm", "nm", "μm", "mm", "cm",
    "ptm", "ptc", "ppm",
    "degC", "degF", "degR",
    "deltaK", "deltaC", "deltaF",
})


# Names that bind TIGHTLY to a preceding value — the implicit ``*``
# between ``<value> <unit>`` gets wrapped in parens so subsequent ``/``
# and ``*`` operate on the whole quantity rather than the bare value.
# Same set is used by ``rewrite_parallel``, ``rewrite_plusminus``, etc.
# via the ``_VALUE_WITH_OPTIONAL_UNIT`` shape further down, so that
# ``12 Ω ‖ 13 Ω`` correctly captures ``12 Ω`` as the LHS rather than
# splitting at the space and seeing just ``12``.
#
# The set covers SI base/derived names (``m``, ``s``, ``V``, ``A``,
# ``N``, ``Ω``…), prefixed forms (``mV``, ``kΩ``, ``nF``, ``MHz``…),
# and the imperial / non-SI / engineering names from
# ``extra_units.py`` (``inch``, ``psi``, ``kN``, ``hp``…) plus
# currencies (``USD``, ``DKK``…).  Adding a new unit to
# ``extra_units.py`` that should bind tightly requires adding its
# name here too — there's no automatic discovery.
_UNIT_NAMES_FOR_BINDING = (
    _SI_UNIT_NAMES_UNAMBIGUOUS
    | _SI_UNIT_NAMES_AMBIGUOUS
    | _PREFIXED_UNIT_NAMES
    | frozenset({
        # Imperial / non-SI from extra_units that users frequently
        # multiply by a value.  Adding them to the binding set means
        # ``5 ft / 2 s`` reads as ``(5 ft)/(2 s)``.
        "inch", "ft", "yard", "mile", "nautical_mile", "thou", "mil",
        "parsec", "ly", "au",
        "lb", "lbm", "oz", "grain", "slug", "stone",
        "ton_us", "ton_uk", "tonne", "ozt",
        "newton", "lbf", "ozf", "kgf", "kp", "gf", "dyne",
        "μN", "mN", "kN", "MN", "GN",
        "hPa", "kPa", "MPa", "GPa", "bar", "mbar", "atm",
        "torr", "mmHg", "psi", "ksi", "inH2O",
        "kJ", "MJ", "GJ", "cal", "Cal", "kcal", "BTU", "kWh", "Wh",
        "eV", "keV", "MeV", "GeV", "erg",
        "kW", "MW", "GW", "hp", "hp_metric", "hp_electrical",
        "liter", "litre", "mL", "dL", "cc",
        "gal_us", "gal_uk", "qt_us", "pt_us", "fl_oz_us",
        "qt_uk", "pt_uk", "fl_oz_uk", "barrel",
        "minute", "hour", "day", "week",
        "year_julian", "year_tropical",
        "mph", "kph", "knot",
        "km", "hm", "dam", "dm", "fm", "Å",
        "ns", "ps", "fs", "ks",
        "mg", "μg", "ng",
        "kHz", "MHz", "GHz", "THz",
        "kV", "MV", "kA", "coulomb",
        "μC", "mC", "nC", "pC", "kC",
        "pF", "nF", "mF",
        "Pa", "J", "Wb", "rad", "sr",
        # Currencies
        "DKK", "USD", "EUR", "GBP", "JPY", "SEK", "NOK",
        "CHF", "CAD", "AUD", "CNY", "HKD", "INR", "PLN", "CZK",
    })
)

# NFKC lookalikes.  Python normalises identifiers (PEP 3131), so
# ``µN`` typed with the MICRO SIGN is the same name as ``μN``
# with GREEK SMALL MU, and ``kΩ`` (OHM SIGN) the same as ``kΩ``.
# Keyboards and symbol palettes produce both; the token-level unit
# match above sees the raw text, so without the variants ``10⁶
# µN·m`` was neither tightly bound nor tagged and collapsed to
# ``1 J`` while the Greek-mu spelling printed ``μN·m``.  Mirrors
# ``_NFKC_VARIANTS`` / ``_expand_variants`` in ``Engineer_Style.py``.
_NFKC_VARIANTS = {
    "Ω": ("Ω", "Ω"),   # GREEK CAPITAL OMEGA / OHM SIGN
    "μ": ("μ", "µ"),   # GREEK SMALL MU / MICRO SIGN
    "Å": ("Å", "Å"),   # A WITH RING ABOVE / ANGSTROM SIGN
    "K":      ("K",      "K"),   # LATIN CAPITAL K / KELVIN SIGN
}
# Lookalike -> canonical, for the label a tagged literal displays with.
_NFKC_CANONICAL = str.maketrans({
    alt: canon for canon, forms in _NFKC_VARIANTS.items() for alt in forms[1:]
})


def _expand_nfkc_variants(word):
    """All spellings of ``word`` over the NFKC lookalike codepoints."""
    forms = [""]
    for ch in word:
        forms = [f + v for f in forms for v in _NFKC_VARIANTS.get(ch, (ch,))]
    return forms


_UNIT_NAMES_FOR_BINDING = frozenset(
    form for name in _UNIT_NAMES_FOR_BINDING
    for form in _expand_nfkc_variants(name)
)

# Regex character class equivalent.  ``re`` doesn't have a direct
# "match any string from this set" facility, so we sort longest-first
# (so ``mΩ`` matches before ``Ω``) and join with ``|`` inside a
# non-capturing group.  ``re.escape`` handles any special chars; the
# Unicode unit names (``Ω``, ``μA``, ``Å``) all work because Python
# regex is Unicode-aware by default.
_UNIT_NAME_ALT = (
    "(?:" +
    "|".join(re.escape(u) for u in
             sorted(_UNIT_NAMES_FOR_BINDING, key=len, reverse=True)) +
    ")"
)


def _wu(unit, label: str):
    """Written-unit marker for a ``<value> <unit>`` literal.

    The transform turns ``22735 mm`` into ``(_S(22735, _INF) *
    _wu(mm, 'mm'))``.  For a plain forallpeople unit this returns a
    ``_DisplayUnit`` (the same marker ``Nm`` and ``inch`` are built
    from), so the resulting ``Sig`` remembers the unit it was WRITTEN
    in and displays as ``22735 mm`` — not forallpeople's auto-prefixed
    ``22.735 m``.  The tag follows the existing ``_unit_pref`` rules:
    it persists through arithmetic (left operand wins for ``+``/``-``,
    matching tags survive ``*``/``/``), and the formatter drops it the
    moment the value's dimensions stop matching, so it can never label
    a number wrongly.

    Anything that is not a bare Physical unit is returned untouched: a
    ``_DisplayUnit`` already carries its canonical label (``Nm`` →
    ``N·m``); ``_DeltaUnit`` (``ΔC``), currencies, ``HMS`` and other
    sentinels have their own operator routing and must keep it.
    """
    if type(unit).__name__ == "_DisplayUnit":
        return unit
    inner = unit.value if isinstance(unit, Sig) else unit
    if not (hasattr(inner, "dimensions") and hasattr(inner, "value")):
        return unit
    try:
        from .extra_units import _DisplayUnit
        return _DisplayUnit(inner, label)
    except Exception:
        return unit

_CONSTANT_NAMES = frozenset({
    # Physical constants
    "c", "h", "ℏ", "ħ",   # both NFKC variants
    "k_B", "N_A", "q_e", "R_gas", "g_n", "T_0",
    "ε_0", "μ_0", "m_e", "m_p",
    # Subscript/superscript-bearing aliases for the constants above.
    # Python normalizes identifiers via NFKC at parse time (PEP 3131),
    # so the user's typed ``εₒ`` becomes ``εo`` in the parse tree.  The
    # protected names here are the *normalized* forms — the check
    # operates on parsed AST names, not raw source.  See the matching
    # block of ``:=`` definitions in ``calc_symbols.py``.
    "εo", "μo", "me", "mp", "kβ", "qe", "Rgas", "NA", "gn", "go", "To",
    # Math
    "π", "pi", "i", "inf",
})


# Identifiers that are KNOWN to be scalar values, not callables.  When
# one is followed by ``(`` in source, that's implicit multiplication
# (``εₒ (r²)`` = ``εₒ * (r²)``), NOT a function call.  Without listing
# these, the implicit-mul pass conservatively treats every
# identifier-then-``(`` as a call, which is wrong here.
#
# Two reasons we maintain this list separately from ``_CONSTANT_NAMES``:
# (1) the implicit-mul pass operates on the raw token stream BEFORE
# Python's NFKC normalization, so the source-form glyphs (``εₒ``, etc.)
# need to be in this set as well as the normalized forms; (2) some
# names in ``_CONSTANT_NAMES`` like ``c`` (speed of light) are
# single-letter — adding them to the implicit-mul rule would wreck
# legitimate ``c(x)`` function-call patterns elsewhere.  This list is
# conservatively narrow: only the multi-character, unambiguously-
# non-callable physical constants.
_NON_CALLABLE_NAMES = frozenset({
    # Raw source forms (NFKC-pre-normalized) — what the implicit-mul
    # pass actually sees in the token stream.
    "ε_0", "μ_0", "k_B", "q_e", "N_A", "R_gas", "g_n", "T_0",
    "εₒ", "μₒ", "kᵦ", "qₑ", "Nᴬ", "Rᵍᵃˢ", "gₙ", "gₒ", "Tₒ",
    "mₑ", "mₚ", "mₙ",
    # Post-NFKC forms — in case the token has already been normalized
    # by some earlier pass.
    "εo", "μo", "kβ", "qe", "NA", "Rgas", "gn", "go", "To",
    "me", "mp",
    # Plus the existing π special-case (kept here for parity; the
    # ``prev_token == "π"`` branch above remains for backwards
    # compatibility but this set covers it too).
    "π",
})


def protect(*names: str) -> None:
    """Register one or more identifier names as protected (unassignable)."""
    PROTECTED_NAMES.update(names)


def unprotect(*names: str) -> None:
    """Remove names from the protected set."""
    PROTECTED_NAMES.difference_update(names)


def protect_constants() -> None:
    """Protect physical and mathematical constants."""
    PROTECTED_NAMES.update(_CONSTANT_NAMES)


def protect_si_units(strict: bool = False) -> None:
    """Protect prefixed SI units, prefix multipliers, and unambiguous unit names.

    Single-letter SI unit names — ``V``, ``A``, ``F``, ``H``, ``C``, ``T``,
    ``K``, ``m``, ``s``, ``W``, ``J``, ``N``, ``S``, ``Ω`` — are commonly
    used as variable names in engineering work (capacitance ``C``, force
    ``F``, temperature ``T``, height ``H``, …) so they are NOT included
    by default.  Pass ``strict=True`` to add them — useful when you'd
    rather see an error than silently shadow a unit.
    """
    PROTECTED_NAMES.update(
        _SI_UNIT_NAMES_UNAMBIGUOUS, _SI_PREFIX_NAMES, _PREFIXED_UNIT_NAMES
    )
    if strict:
        PROTECTED_NAMES.update(_SI_UNIT_NAMES_AMBIGUOUS)


def protect_all(strict: bool = False) -> None:
    """Convenience: ``protect_si_units(strict)`` + ``protect_constants()``."""
    protect_si_units(strict=strict)
    protect_constants()


def clear_protections() -> None:
    """Empty the protected set (back to opt-in default)."""
    PROTECTED_NAMES.clear()


def list_protected() -> list:
    """Return the current protected names, sorted."""
    return sorted(PROTECTED_NAMES)


_KIND_VERB = {
    "assign":  "assign to",
    "define":  "define",
    "import":  "import as",
    "except":  "bind in except clause",
    "delete":  "delete",
    "loop":    "use as loop variable",
    "with":    "bind in 'with ... as'",
}


def _check_protected_names(source: str, filename: str = "<cell>") -> None:
    """Walk the AST of ``source`` and raise on any write to a protected name.

    No-op when ``PROTECTED_NAMES`` is empty.  Intended to run as the last
    step of ``transform_source``: by then the source is valid Python (DSL
    glyphs have been rewritten) and ``ast.parse`` will accept it.
    """
    if not PROTECTED_NAMES:
        return
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Let the regular compile step report the syntax problem with its
        # own context — there's no point doubling up.
        return

    violations = []

    def report(name, node, kind):
        if name in PROTECTED_NAMES:
            violations.append((node.lineno, node.col_offset, name, kind))

    def walk_target(target, kind="assign"):
        """An assignment LHS: Name | Tuple | List | Starred | Attribute | Subscript."""
        if isinstance(target, ast.Name):
            report(target.id, target, kind)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                walk_target(elt, kind)
        elif isinstance(target, ast.Starred):
            walk_target(target.value, kind)
        # Attribute (obj.V) and Subscript (xs[V]) are not name bindings —
        # they don't shadow the protected name, so we leave them alone.

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                walk_target(tgt, "assign")
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            walk_target(node.target, "assign")
        elif isinstance(node, ast.NamedExpr):       # walrus
            walk_target(node.target, "assign")
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            walk_target(node.target, "loop")
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    walk_target(item.optional_vars, "with")
        elif isinstance(node, (ast.FunctionDef,
                               ast.AsyncFunctionDef,
                               ast.ClassDef)):
            report(node.name, node, "define")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                report(bound, node, "import")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound = alias.asname or alias.name
                if bound != "*":
                    report(bound, node, "import")
        elif isinstance(node, ast.ExceptHandler) and node.name:
            report(node.name, node, "except")
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    report(target.id, target, "delete")

    if not violations:
        return

    line, col, name, kind = violations[0]
    verb = _KIND_VERB.get(kind, kind)
    src_lines = source.splitlines()
    text = src_lines[line - 1] if 0 < line <= len(src_lines) else ""
    msg = (
        f"cannot {verb} '{name}': name is protected "
        f"(reserved as a unit, constant, or symbol). "
        f"Use a different identifier, "
        f"or call dsl.unprotect('{name}') to release the protection."
    )
    raise SyntaxError(msg, (filename, line, col + 1, text))

IDENT_START = r"A-Za-z_πμΩεℏΓΠΣσαβγδζηθικλνξορςτυφχψωΦΘΛΨΔΞΥΑΒΕΖΗΙΚΜΝΟΡΤΧ"
IDENT_CONT = r"A-Za-z0-9_πμΩεℏΓΠΣσαβγδζηθικλνξορςτυφχψωΦΘΛΨΔΞΥΑΒΕΖΗΙΚΜΝΟΡΤΧ"
# Full set of Unicode subscript characters — digits, signs, parens, and
# letters (both Subscripts block and Phonetic Extensions block).  This
# defines what counts as the "trailing subscript" of an identifier-like
# atom, so e.g. ``Rₙ``, ``aᵢ``, ``x₍ₙ₊₁₎`` all match as single tokens
# for the regex passes that work on whole atoms.  Must stay in sync with
# ``SUBSCRIPT_TRANS`` (the ASCII translation table) — anything in here
# that doesn't have a translation entry will pass through verbatim and
# probably break Python's parser later.
SUBSCRIPT_CHARS = r"₀₁₂₃₄₅₆₇₈₉₊₋₍₎ₐₑₒₓₔₕₖₗₘₙₚₛₜᵢⱼᵣᵤᵥ"

# A subscript suffix on an assignment target: one run, OR several runs
# joined by the U+0375 dimension separator (``mat₀͵₁ := 99``).  Including
# ``͵`` and the inter-run whitespace here lets ``rewrite_math_assignment``
# recognise a 2-D subscript LHS as an assignment target (it runs before
# ``rewrite_subscript_indices`` turns the run into ``mat[0][1]``).
SUBSCRIPT_SUFFIX = rf"(?:[{SUBSCRIPT_CHARS}]+(?:\s*\u0375\s*[{SUBSCRIPT_CHARS}]+)*)?"

TARGET_ATOM = rf"[{IDENT_START}][{IDENT_CONT}]*{SUBSCRIPT_SUFFIX}"
TARGET = rf"{TARGET_ATOM}(?:\s*(?:\[[^\]\n]+\]|\.{TARGET_ATOM})\s*)*"

# Atom pattern shared by every binary-operator rewriter (‖, ±, ≈, ∠, …).
# Matches a single identifier (with optional trailing subscript digits like
# ``R₂``) or a plain numeric literal.  This is INTENTIONALLY narrower than
# TARGET — we don't try to match attribute access or subscript brackets,
# because that creates regex ambiguity with the binary operators themselves.
# Users who need attribute or index expressions on either side should
# parenthesise: ``(obj.x) ‖ R₂``.
_BINOP_ATOM = (
    rf"[{IDENT_START}][{IDENT_CONT}]*(?:[{SUBSCRIPT_CHARS}]+)?"
    # Python numeric literals: hex (0x...), octal (0o...), binary (0b...),
    # plus plain decimal/float.  Hex/oct/bin must come first so that the
    # leading "0" doesn't get eaten by the decimal alternative below.
    r"|0[xX][0-9a-fA-F]+|0[oO][0-7]+|0[bB][01]+"
    # Decimal / float — accept both ``\d+\.\d+`` (the normal form) AND
    # ``\d+\.`` (trailing-dot, like ``12.``).  Python accepts both as
    # float literals, and treating ``12.`` as the bare integer ``12``
    # with a dangling dot would break the value-with-unit pattern below
    # (``12. Ω`` would split at the dot).
    r"|\d+\.\d+|\d+\.(?![A-Za-z_])|\d+"
)
# Allow an optional trailing comma so single-tuple targets like
# ``line1, ← ax.plot(...)``  match.
TARGET_LIST_RE = re.compile(rf"^\s*{TARGET}(?:\s*,\s*{TARGET})*\s*,?\s*$")


NO_EQ_REWRITE_PREFIXES = (
    "def ",
    "class ",
    "import ",
    "from ",
    "for ",
    "with ",
    "except ",
    "lambda ",
    "@",
)


# ---------- subscript index rewrite ----------

# ---------- range type ----------

class Range:
    def __init__(self, low, high=None):
        if high is None:
            high = low
        self.low = low
        self.high = high
        # Normalize so ``low <= high``.  The comparison may itself raise
        # if the endpoints are non-orderable types (e.g. Sig-wrapping-
        # Range produced by some compositions) — in that case, accept
        # the user's order rather than crashing.  This was the root of
        # the ``parallel(R1, R2)`` failure with Range-typed operands:
        # ``1/x + 1/y`` builds intermediate Ranges whose endpoints are
        # themselves Ranges, and Range had no comparison operators.
        try:
            if self.low > self.high:
                self.low, self.high = self.high, self.low
        except TypeError:
            # Endpoints aren't pairwise comparable — accept as-given.
            pass

    @classmethod
    def from_pm(cls, center, delta):
        return cls(center - delta, center + delta)

    @staticmethod
    def coerce(x):
        return x if isinstance(x, Range) else Range(x)

    @property
    def center(self):
        return (self.low + self.high) / 2

    @property
    def tol(self):
        return (self.high - self.low) / 2

    def __repr__(self):
        return f"({self.low!r} ‥ {self.high!r})"

    # Comparison operators — required for Range to interoperate with
    # the rest of the toolkit when it gets wrapped in a Sig (Sig
    # forwards ``__gt__`` etc. to ``self.value`` directly).  Without
    # these, ``parallel(R, R)`` with Range-typed resistors crashed at
    # the ``low > high`` check during the intermediate Range
    # constructor calls in ``1/x + 1/y``.
    #
    # Two Ranges compare by midpoint — the most common intent when a
    # caller wants to order intervals.  This is NOT the strict
    # interval-order semantics (where ``[1,5] > [2,3]`` would be
    # False because they overlap); it's just a stable ordering for
    # housekeeping like the ``__init__`` swap.
    def _cmp_key(self):
        return self.center

    def __lt__(self, other):
        other = Range.coerce(other)
        return self._cmp_key() < other._cmp_key()

    def __le__(self, other):
        other = Range.coerce(other)
        return self._cmp_key() <= other._cmp_key()

    def __gt__(self, other):
        other = Range.coerce(other)
        return self._cmp_key() > other._cmp_key()

    def __ge__(self, other):
        other = Range.coerce(other)
        return self._cmp_key() >= other._cmp_key()

    def __eq__(self, other):
        if not isinstance(other, Range):
            try:
                other = Range.coerce(other)
            except Exception:
                return NotImplemented
        return self.low == other.low and self.high == other.high

    def __hash__(self):
        # Ranges with comparable, hashable endpoints are themselves
        # hashable — important for use as dict keys or set members.
        try:
            return hash((self.low, self.high))
        except TypeError:
            return id(self)

    def __add__(self, other):
        other = Range.coerce(other)
        return Range(self.low + other.low, self.high + other.high)

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        other = Range.coerce(other)
        return Range(self.low - other.high, self.high - other.low)

    def __rsub__(self, other):
        other = Range.coerce(other)
        return other.__sub__(self)

    def __neg__(self):
        return Range(-self.high, -self.low)

    def __mul__(self, other):
        other = Range.coerce(other)
        vals = [
            self.low * other.low,
            self.low * other.high,
            self.high * other.low,
            self.high * other.high,
        ]
        return Range(min(vals), max(vals))

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        other = Range.coerce(other)
        if other.low <= 0 <= other.high:
            raise ZeroDivisionError("division by a range spanning zero")
        vals = [
            self.low / other.low,
            self.low / other.high,
            self.high / other.low,
            self.high / other.high,
        ]
        return Range(min(vals), max(vals))

    def __rtruediv__(self, other):
        other = Range.coerce(other)
        return other.__truediv__(self)

    def __pow__(self, exponent):
        """Raise the interval to a numeric (typically integer) power.

        For positive integer ``n`` and a positive interval, the result
        is simply ``(low**n, high**n)``.  For a 0-crossing interval and
        even ``n``, the minimum is at 0; for odd ``n``, the interval
        is monotonic.  Non-integer exponents are handled the same way
        but on the assumption that the interval is positive — caller
        beware.

        This is the interval-arithmetic version of squaring/cubing —
        ``(2 ± 0.1)**2`` gives ``(3.61, 4.41)``, which matches the
        worst-case bounds (NOT the statistical ``4 ± 0.4`` you'd get
        from uncertainty-propagation packages like ``uncertainties``).
        """
        try:
            n = int(exponent)
            if n == exponent and n >= 0:
                # Integer exponent — special-case zero-crossing for evens
                if self.low >= 0 or n % 2 == 1:
                    # Monotonic on this interval — endpoints stay in order
                    lo, hi = self.low ** n, self.high ** n
                    if lo > hi:
                        lo, hi = hi, lo
                    return Range(lo, hi)
                elif self.high <= 0:
                    # Both endpoints non-positive, even power → reverses
                    return Range(self.high ** n, self.low ** n)
                else:
                    # Crosses zero, even power → min is 0
                    return Range(self.low ** n * 0, max(self.low ** n, self.high ** n))
        except (TypeError, ValueError):
            pass
        # Fallback: non-integer or unhandled — assume positive interval
        lo = self.low ** exponent
        hi = self.high ** exponent
        if lo > hi:
            lo, hi = hi, lo
        return Range(lo, hi)


# Register a sf-aware formatter for Range so that Sig(Range, sf) displays
# both endpoints at the appropriate precision.  Without this, the Sig's
# __repr__ would call __format__ on the Range, which has no understanding
# of significant figures and falls back to its own __repr__ — losing the
# precision context.
#
# The complication is that Sig's sf was computed for the interval as a
# whole (via the addsub or muldiv rule on the input centre and delta).
# When we display the TWO endpoints separately, applying that same sf to
# each endpoint can collapse them visually if their difference is below
# the sf's resolution — that was the original ``(2.2 MΩ ‥ 2.2 MΩ)`` bug.
#
# So the rule for Range display: format each endpoint with enough digits
# to make the two endpoints clearly distinct, but no more than the sf
# would warrant.  We achieve this by computing the minimum sf needed to
# show ``high - low`` to two significant figures (i.e. the width is
# resolved) and using ``max(outer_sf, that_width_sf)`` for each endpoint.
@_format_sig.register(Range)
def _format_range(value, sf, temp_scale=None):
    import math
    low = value.low
    high = value.high
    # Figure out the sf needed to make low and high visually distinct.
    # The width's order of magnitude relative to the endpoints tells us
    # how many sig figs the endpoints need.  Example: low=2.156 MΩ,
    # high=2.244 MΩ — width=0.088 MΩ, endpoints have magnitude ~2 MΩ.
    # log10(width/endpoint) ≈ -1.36, so we need ~2 + 1.36 = 3.4 sf to
    # show the width with one significant figure.  Round up to 4.
    try:
        # Use the toolkit's _magnitude helper so Physicals work too.
        mag = _magnitude(value)            # midpoint magnitude
        width = abs(_magnitude(high) - _magnitude(low))
        if width > 0 and abs(mag) > 0:
            ratio = width / abs(mag)
            # sf to resolve the width by 2 digits: ceil(-log10(ratio)) + 2
            needed = math.ceil(-math.log10(ratio)) + 2
            display_sf = max(int(sf) if math.isfinite(sf) else 0, needed)
        else:
            # Degenerate (zero-width or zero-magnitude) — fall back.
            display_sf = sf if math.isfinite(sf) else 6
    except (TypeError, ValueError):
        display_sf = sf if math.isfinite(sf) else 6

    # A temperature interval (``25 °C ± 10 ΔC``) displays its endpoints in
    # the centre's scale (°C/°F), offset-correct, rather than kelvin.
    if temp_scale and temp_scale != "K":
        from .sigfig import _format_temperature, _is_pure_temperature
        try:
            if _is_pure_temperature(low) and _is_pure_temperature(high):
                low_str = _format_temperature(low, display_sf, temp_scale)
                high_str = _format_temperature(high, display_sf, temp_scale)
                return f"({low_str} ‥ {high_str})"
        except Exception:
            pass

    low_str = _format_sig(low, display_sf)
    high_str = _format_sig(high, display_sf)
    return f"({low_str} ‥ {high_str})"


SUBSCRIPT_TRANS = str.maketrans({
    "₀": "0",
    "₁": "1",
    "₂": "2",
    "₃": "3",
    "₄": "4",
    "₅": "5",
    "₆": "6",
    "₇": "7",
    "₈": "8",
    "₉": "9",
    "₊": "+",
    "₋": "-",
    # Subscript parentheses — let users group sub-expressions inside an
    # index: ``x₍ₙ₊₁₎`` decodes to ``x[(n+1)]``.  The parens enable
    # everything the bare-digit-run form couldn't express.
    "₍": "(",
    "₎": ")",
    "ₐ": "a",
    "ₑ": "e",
    "ₒ": "o",
    "ₓ": "x",
    "ₔ": "ə",
    "ₕ": "h",
    "ₖ": "k",
    "ₗ": "l",
    "ₘ": "m",
    "ₙ": "n",
    "ₚ": "p",
    "ₛ": "s",
    "ₜ": "t",
    # Letter subscripts from the Phonetic Extensions block — Unicode
    # treats these as separate code points from the main Subscript
    # block but they're conventionally used as subscripts.  Adding
    # them lets ``aᵢ`` and ``vᵤ`` work like ``aₙ`` does.
    "ᵢ": "i",
    "ⱼ": "j",
    "ᵣ": "r",
    "ᵤ": "u",
    "ᵥ": "v",
})


def subscript_to_ascii(text: str) -> str:
    return text.translate(SUBSCRIPT_TRANS)


# ----- superscript digits and signs ----- 
# Mirror of SUBSCRIPT_TRANS for the postfix-power rewriter.  We map the
# Unicode superscript digits, plus and minus, parens, and letters to ASCII
# so a multi-character run like ``⁶⁴`` becomes ``"64"``, ``⁻¹`` becomes
# ``"-1"``, and ``⁽ⁿ⁺³⁾ⁱ`` becomes ``"(n+3)i"`` (which the implicit-mul
# pass later turns into ``(n+3)*i``).
SUPERSCRIPT_TRANS = str.maketrans({
    "⁰": "0",
    "¹": "1",
    "²": "2",
    "³": "3",
    "⁴": "4",
    "⁵": "5",
    "⁶": "6",
    "⁷": "7",
    "⁸": "8",
    "⁹": "9",
    "⁺": "+",
    "⁻": "-",
    # Superscript decimal point.  Unicode has no superscript period, so
    # a non-integer exponent written in superscript form — ``k⁰˙⁵⁵`` for
    # ``k**0.55`` — uses U+02D9 DOT ABOVE (``˙``) as the raised decimal
    # point.  It decodes to an ordinary ``.``; the surrounding digit run
    # then reads as a normal float literal (``0˙55`` → ``0.55``).  U+02D9
    # is chosen over the middle dot ``·`` deliberately: ``·`` is already
    # the multiplication glyph elsewhere, and reusing it would give one
    # character two meanings.  NB: this entry is only reachable from the
    # postfix-power rewriter — the ``√`` root rewriter has its own digit
    # class and never admits ``˙`` (a root index must be an integer).
    "˙": ".",
    # Superscript parentheses — let the user group sub-expressions inside
    # an exponent: ``2⁽ⁿ⁺³⁾`` decodes to ``2**(n+3)``.  Without these the
    # only multi-token exponents the rewriter could express were bare
    # digit runs, forcing the user to fall back to ``**`` for anything
    # more complex.
    "⁽": "(",
    "⁾": ")",
    # Lowercase letter superscripts (Phonetic Extensions / Spacing Modifier Letters).
    # Used to transcribe a single-letter exponent: ``xⁿ`` → ``x**n``.
    # Only single-character runs are recognised — see
    # ``rewrite_postfix_superscripts`` for the rule.
    "ᵃ": "a",
    "ᵇ": "b",
    "ᶜ": "c",
    "ᵈ": "d",
    "ᵉ": "e",
    "ᶠ": "f",
    "ᵍ": "g",
    "ʰ": "h",
    "ⁱ": "i",
    "ʲ": "j",
    "ᵏ": "k",
    "ˡ": "l",
    "ᵐ": "m",
    "ⁿ": "n",
    "ᵒ": "o",
    "ᵖ": "p",
    "ʳ": "r",
    "ˢ": "s",
    "ᵗ": "t",
    "ᵘ": "u",
    "ᵛ": "v",
    "ʷ": "w",
    "ˣ": "x",
    "ʸ": "y",
    "ᶻ": "z",
    # Uppercase letter superscripts (Phonetic Extensions Supplement).
    "ᴬ": "A",
    "ᴮ": "B",
    "ᴰ": "D",
    "ᴱ": "E",
    "ᴳ": "G",
    "ᴴ": "H",
    "ᴵ": "I",
    "ᴶ": "J",
    "ᴷ": "K",
    "ᴸ": "L",
    "ᴹ": "M",
    "ᴺ": "N",
    "ᴼ": "O",
    "ᴾ": "P",
    "ᴿ": "R",
    "ᵀ": "T",
    "ᵁ": "U",
    "ⱽ": "V",
    "ᵂ": "W",
})


def superscript_to_ascii(text: str) -> str:
    return text.translate(SUPERSCRIPT_TRANS)


# ---------------------------------------------------------------------------
# Base-suffixed integer literals: 1101₂ → 0b1101, fedeabba₁₆ → 0xfedeabba, etc.
# ---------------------------------------------------------------------------

# Characters allowed before the base subscript.  Digits 0–9 cover bases 2..10,
# and letters a–f / A–F extend that to base 16.  Bases between 11 and 36
# could in principle use more letters (g-z), but the convention in the wild
# is hex-or-decimal-or-octal-or-binary; we accept hex letters specifically
# because base-16 is common, and refuse other letters.
# Two separate patterns for base-suffixed literals:
#   1. Digit-led run, hex-permissive ``[0-9][0-9a-fA-F]*``.  Restricted
#      to hex digits so that implicit-multiplication patterns like
#      ``2x₃`` (meaning ``2 * x[3]``) are NOT eaten — ``x`` isn't a hex
#      digit, so the regex won't match.  This costs us bases 17-36 with
#      letter digits (``1Z₃₆`` etc.); those are vanishingly rare in
#      practice, and the user can fall back to ``int("1Z", 36)``.
#      Bases 11-16 with hex letters work fine (``a3₁₂`` raises because
#      ``a`` is too large for base 12, but ``9a₁₆`` works).
#   2. Hex-letter-only run (a-f / A-F) followed specifically by ``₁₆``.
#      For hex constants from spec sheets that start with a letter, like
#      ``fedeabba₁₆`` or ``DEADBEEF₁₆``.  Restricted to base 16 so that
#      ``samples₃`` doesn't get misparsed as "samples in base 3".
_BASE_LITERAL_RES = [
    re.compile(
        r'(?<![A-Za-z_0-9])'
        r'([0-9][0-9a-fA-F]*)'                # digit-led, hex-permissive only
        r'([₀₁₂₃₄₅₆₇₈₉]+)'
    ),
    re.compile(
        r'(?<![A-Za-z_0-9])'
        r'([a-fA-F]+)'                        # hex-letter-led
        r'(₁₆)'                               # base 16 specifically
    ),
]


def _digit_value(ch: str) -> int:
    """Return the integer value of a single digit character.  Letters
    a-z / A-Z return 10..35.  Anything else raises ValueError."""
    if '0' <= ch <= '9':
        return ord(ch) - ord('0')
    if 'a' <= ch <= 'z':
        return ord(ch) - ord('a') + 10
    if 'A' <= ch <= 'Z':
        return ord(ch) - ord('A') + 10
    raise ValueError(f"not a digit: {ch!r}")


def rewrite_base_suffixed_numbers(source: str) -> str:
    """
    Rewrites integer literals with a Unicode-subscript base suffix.

        01101110₂      -> 0b01101110
        0715₈          -> 0o0715
        123456789₁₀    -> 123456789
        fedeabba₁₆     -> 0xfedeabba
        02101210₃      -> int("02101210", base=3)

    The base after the subscript can be any value from 2 to 36.  Bases 2,
    8, and 16 emit Python's native literal prefixes (``0b``, ``0o``, ``0x``)
    so the result is a normal integer literal — fast, and visible at a
    glance in error messages.  Base 10 emits the digits unchanged.  All
    other bases emit ``int("digits", base=N)``.

    The digit run is validated against the named base.  A literal like
    ``123₂`` raises ``SyntaxError`` because ``2`` and ``3`` are not valid
    binary digits.  Identifiers are NOT touched: ``R₁₂`` is left alone
    (it will be picked up by ``rewrite_subscript_indices`` as ``R[12]``)
    because the leading character is a letter, not a digit.

    Hex letters are accepted only when the named base is 16 or higher.
    """

    def replace(m):
        digits, base_sub = m.group(1), m.group(2)
        base = int(subscript_to_ascii(base_sub))

        if not (2 <= base <= 36):
            raise SyntaxError(
                f"invalid base {base} in {digits}{base_sub}: must be 2-36"
            )

        # Validate that every digit in the run is legal for this base.
        # ``_digit_value`` raises on totally invalid chars; we then check
        # the value against the base.
        for ch in digits:
            try:
                v = _digit_value(ch)
            except ValueError:
                # Shouldn't happen given the regex, but guard anyway.
                raise SyntaxError(
                    f"invalid digit {ch!r} in {digits}{base_sub}"
                )
            if v >= base:
                raise SyntaxError(
                    f"digit {ch!r} is not valid in base {base} "
                    f"(in {digits}{base_sub})"
                )

        # Pick the most readable Python form for this base.
        if base == 2:
            return f'0b{digits}'
        if base == 8:
            return f'0o{digits}'
        if base == 10:
            return digits
        if base == 16:
            return f'0x{digits}'
        # Any other base: use int(str, base).
        return f'int("{digits}", base={base})'

    for pattern in _BASE_LITERAL_RES:
        source = pattern.sub(replace, source)
    return source


def rewrite_subscript_indices(source: str) -> str:
    """
    Rewrites postfix Unicode subscripts as Python index expressions.
    Any maximal run of subscript characters becomes the index, decoded
    character-by-character into its ASCII equivalent and wrapped in
    square brackets::

        x₁              -> x[1]
        x₁₂             -> x[12]
        x₋₁             -> x[-1]
        xₙ              -> x[n]
        xₙ₊₁            -> x[n+1]
        x₍ₙ₊₁₎          -> x[(n+1)]
        x₍ₙ₊₁₎₃         -> x[(n+1)3]    # implicit-mul makes this x[(n+1)*3]
        (a+b)₂          -> (a+b)[2]
        f(x)ₙ           -> f(x)[n]

    The decoded run is always wrapped in ``[...]``, so structure inside
    the run — implicit multiplication (``(n+1)3``), arithmetic
    (``n+1``, ``12-3``), nested grouping with ``₍ ₎`` — is preserved
    for the rest of the pipeline.  In particular, the implicit-
    multiplication pass turns ``(n+1)3`` into ``(n+1)*3`` downstream,
    so ``x₍ₙ₊₁₎₃`` reads as ``x[(n+1)*3]`` at runtime.

    The base operand may be a parenthesised group, an identifier (with
    optional further indexing), a function call result, or a numeric
    literal — the same operand set used by the superscript rewriter.

    The earlier version of this function only handled bare-identifier
    operands and rejected expressions with grouping or arithmetic in
    the subscript itself.  The current rule is uniform: decode the
    run, wrap in brackets — predictable, no special cases.

    A note on identifier-vs-index ambiguity: the toolkit treats every
    subscript-suffixed name as an indexing expression, NOT as a
    distinct identifier.  ``x₁ = 5`` does not bind a name "x sub one";
    it tries to assign to ``x[1]`` and fails (or works) depending on
    whether ``x`` is indexable.  If you want distinct named variables
    use ``x_1``, ``x1``, or any other naming you like — the math-style
    subscript notation is reserved for indexing.
    """

    SUB_DIGIT_OR_SIGN = r'₀₁₂₃₄₅₆₇₈₉₊₋₍₎'
    SUB_LETTER = r'ₐₑₒₓₔₕₖₗₘₙₚₛₜᵢⱼᵣᵤᵥ'
    SUB_RUN = rf'[{SUB_DIGIT_OR_SIGN}{SUB_LETTER}]+'
    SUB_RUN_RE = re.compile(SUB_RUN)

    def _format_sub(lhs: str, sub: str) -> str:
        """Replacement for ``<lhs><sub>``: decode the run and wrap as a
        runtime-dispatched ``_idx(lhs, decoded)`` call.  ``_idx`` gives
        bounds-checked 0-based access on a DSL matrix and ordinary
        0-based access on a plain list/tuple/array, so the same notation
        works for both — everything is Python-style 0-indexed.
        """
        decoded = sub.translate(SUBSCRIPT_TRANS)
        return f'_idx({lhs}, {decoded})'

    def replace_with_index(m):
        # Wrapper for the regex-driven bare-operand pass.  Matches
        # an identifier-like atom followed by a subscript run.
        lhs, sub = m.group(1), m.group(2)
        return _format_sub(lhs, sub)

    # ---- Multi-dimensional subscript pass (runs FIRST) ----
    # A subscripted atom followed by one or more SPACE-separated bare
    # subscript runs chains as successive indices — the 2-D access
    # spelling::
    #
    #     M₁ ₁        -> M[1][1]
    #     M₀ ₂        -> M[0][2]
    #     T₁ ₂ ₃      -> T[1][2][3]   (n-dimensional)
    #     A₍ᵢ₊₁₎ ⱼ    -> A[(i+1)][j]
    #
    # This must run before the single-index passes below, because once
    # ``M₁`` becomes ``M[1]`` the base for the next index is ``]`` (not a
    # bare atom) and the trailing ``₁`` would be stranded.
    #
    # The continuation runs are matched ONLY when bare (a subscript run
    # with no identifier base of its own).  That is what keeps an
    # ordinary two-term expression like ``a₁ b₂`` — two separately
    # indexed names — from being mis-read as ``a[1][b2]``: ``b₂`` has an
    # identifier base, so it is not a bare continuation.  A run that
    # should chain (``M₁ ₁``) has no base on the second token.
    SUB_BASE_ATOM = (
        rf"[{IDENT_START}][{IDENT_CONT}]*"
        rf"|0[xX][0-9a-fA-F]+|0[oO][0-7]+|0[bB][01]+"
        rf"|\d+(?:\.\d+)?"
    )
    # base + first run, then ≥1 occurrences of (separator + bare run).
    # The dimension separator is U+0375 GREEK LOWER NUMERAL SIGN (``͵``),
    # a spacing modifier symbol chosen as the 2-D subscript-index comma:
    # Unicode has no subscript comma, and ``͵`` reads as a low separating
    # stroke between the subscripts (``M₁͵₁`` → ``M[1][1]``).  It's a
    # *spacing* character (combining class 0), so it tokenises and
    # copy-pastes cleanly, unlike a combining mark.  Optional whitespace
    # is allowed around it (``M₁ ͵ ₁`` works), but whitespace ALONE no
    # longer chains indices — a bare space between two subscripted names
    # (``a₁ b₂``) must stay two separate terms, not ``a[1][b2]``.  The
    # ``͵`` is what makes the continuation unambiguous.
    SUB_DIM_SEP = r'\u0375'
    MULTI_SUB_RE = re.compile(
        rf'({SUB_BASE_ATOM})({SUB_RUN})'
        rf'((?:\s*{SUB_DIM_SEP}\s*{SUB_RUN})+)'
    )

    def _replace_multi(m):
        base, first, rest = m.group(1), m.group(2), m.group(3)
        # Collect all index expressions (first + each ``͵``-separated
        # continuation) and emit a single ``_idx(base, i, j, …)`` call.
        # ``_idx`` dispatches at runtime: a DSL matrix → 0-based
        # bounds-checked access; any other container → ordinary 0-based
        # ``base[i][j]…``.  This is why we route through ``_idx`` instead
        # of literal brackets — the rewriter can't know the base's type.
        idxs = [first.translate(SUBSCRIPT_TRANS)]
        for run in re.findall(SUB_RUN, rest):
            idxs.append(run.translate(SUBSCRIPT_TRANS))
        return f'_idx({base}, {", ".join(idxs)})'

    previous = None
    while source != previous:
        previous = source
        source = MULTI_SUB_RE.sub(_replace_multi, source)

    # Paren-form pass — handles operands with parens (``(a+b)₂``,
    # ``f(x)ₙ``, ``arr[i]ₖ``).  Walks back past any preceding
    # identifier so a function call gets indexed as a whole.
    def _find_sub_run(s, pos):
        m = SUB_RUN_RE.search(s, pos)
        if not m:
            return None
        return (m.start(), m.end(), m.group(0))

    previous = None
    while source != previous:
        previous = source
        source = _postfix_paren_pass(source, find_op=_find_sub_run,
                                     callback=_format_sub)

    # Bare-operand pass — identifiers and numeric literals only.
    # We can't reuse ``_BINOP_ATOM`` directly here because that pattern
    # already includes an optional trailing subscript run (so identifiers
    # like ``R₂`` match as ONE atom), and reusing it would let the
    # operand greedily eat the FIRST subscript character, leaving the
    # rest for our SUB_RUN to match — turning ``x₁₂`` into ``x₁[2]``
    # instead of ``x[12]``.  Use a simpler atom pattern here that
    # explicitly excludes subscript characters from its tail, ensuring
    # the SUB_RUN match captures the whole subscript sequence.
    SUB_BARE_ATOM = (
        rf"[{IDENT_START}][{IDENT_CONT}]*"
        rf"|0[xX][0-9a-fA-F]+|0[oO][0-7]+|0[bB][01]+"
        rf"|\d+(?:\.\d+)?"
    )
    previous = None
    while source != previous:
        previous = source
        source = re.sub(
            rf'({SUB_BARE_ATOM})\s*({SUB_RUN})',
            replace_with_index,
            source,
        )

    return source


# ---------- runtime helper functions ----------

def _peel(x):
    """Strip any Sig wrappers and return (raw_value, sf)."""
    sf = _INF
    while isinstance(x, Sig):
        sf = min(sf, x.sf)
        x = x.value
    return x, sf


def parallel(x, y):
    return 1 / (1/x + 1/y)


def _range_inc(start, stop, step=None):
    """Inclusive both-ends range used by the ``[a..b]`` / ``a..b`` syntax.

    Returns a Python ``range`` for integer-like inputs (with the
    appropriate adjustment for inclusive endpoints), or a list of
    floats when any input is a float.

    >>> list(_range_inc(1, 3))
    [1, 2, 3]
    >>> list(_range_inc(5, 1))
    [5, 4, 3, 2, 1]
    >>> list(_range_inc(0, 10, 2))
    [0, 2, 4, 6, 8, 10]

    When ``step`` is omitted the direction is inferred from the
    endpoints — ascending if ``stop >= start``, descending otherwise.
    A zero step raises ``ValueError``.

    Float endpoints are supported via a list comprehension, since
    Python's ``range`` is integers-only:

    >>> _range_inc(0.0, 1.0, 0.25)
    [0.0, 0.25, 0.5, 0.75, 1.0]

    Date / datetime endpoints walk the calendar.  When both endpoints
    are ``date`` or ``datetime`` objects (typically from the DSL's
    ``"..."ₜᵢₘₑ`` literals), the result is a list of dates from start
    to stop inclusive:

    >>> from datetime import date
    >>> _range_inc(date(2026, 5, 6), date(2026, 5, 9))
    [date(2026, 5, 6), date(2026, 5, 7), date(2026, 5, 8), date(2026, 5, 9)]

    The default step is one day; pass a ``timedelta`` for a different
    stride (``_range_inc(d0, d1, iso("PT6H"))`` for six-hourly points,
    ``_range_inc(d0, d1, iso("P7D"))`` for weekly).  Direction is
    inferred from the endpoints when no step is given, exactly as for
    numbers.
    """
    import datetime as _dt

    # ---- Date / datetime endpoints — walk the calendar ----
    # ``datetime`` subclasses ``date``, so this one isinstance covers
    # both.  A ``time`` is excluded: a bare time-of-day has no sensible
    # "+1 day" semantics for a range, so it falls through to the numeric
    # path (and its _peel will raise, which is the right signal).
    if isinstance(start, _dt.date) and isinstance(stop, _dt.date):
        if step is None:
            stride = _dt.timedelta(days=1)
        elif isinstance(step, _dt.timedelta):
            stride = step
        else:
            # A bare number as the step means "that many days".
            stride = _dt.timedelta(days=_peel(step)[0])
        if stride == _dt.timedelta(0):
            raise ValueError("range step cannot be zero")
        # Infer direction when the step's sign and the endpoints
        # disagree — ``[later .. earlier]`` with a default +1 day step
        # should still descend, matching the numeric behaviour.
        ascending = stop >= start
        if ascending and stride < _dt.timedelta(0):
            stride = -stride
        elif not ascending and stride > _dt.timedelta(0):
            stride = -stride
        out = []
        cur = start
        if ascending:
            while cur <= stop:
                out.append(cur)
                cur = cur + stride
        else:
            while cur >= stop:
                out.append(cur)
                cur = cur + stride
        # Return a CommaArray, not a bare list, so the result prints in
        # ISO form (``[2026-05-06, 2026-05-07, ...]``) rather than the
        # verbose ``[datetime.date(2026, 5, 6), ...]`` — and so the bare
        # form ``d0..d1`` and the bracketed form ``[d0..d1]`` display
        # identically.  ``CommaArray`` is an ndarray subclass; an
        # object-dtype array of dates iterates and indexes normally, and
        # ``date`` arithmetic in a loop body is unaffected.  An empty
        # range stays a plain list — an empty object-array is awkward
        # and nothing needs to display it.
        if not out:
            return out
        return CommaArray(np.array(out, dtype=object))

    # ---- Unit-carrying endpoints — ``(-55 °C .. 125 °C)``, ``[1 kΩ .. 5 kΩ]`` ----
    # A ``Physical`` can't be handed to ``range()`` (that was the
    # ``'Physical' object cannot be interpreted as an integer`` crash).
    # Enumerate in the unit the endpoints were WRITTEN in, then put the
    # unit back — so ``(-55.0 °C .. 125.0 °C)`` and ``(-55.0 .. 125.0) °C``
    # produce the same array.
    if _is_physical(_peel(start)[0]) or _is_physical(_peel(stop)[0]):
        return _range_inc_physical(start, stop, step)

    s = _peel(start)[0]
    e = _peel(stop)[0]
    if step is None:
        st = 1 if e >= s else -1
    else:
        st = _peel(step)[0]

    if st == 0:
        raise ValueError("range step cannot be zero")

    # Precision for the generated points: a measured range comes from one
    # instrument at a FIXED ABSOLUTE RESOLUTION, so what stays constant
    # across the sweep is the number of DECIMAL PLACES, not the sig-fig
    # count.  (A fixed sig-fig count would silently coarsen resolution as
    # the magnitude grows — ``-9.9`` to tenths but ``100`` to ones — which
    # no fixed-range instrument does.)  We recover each endpoint's decimal
    # places from its sf and magnitude, take the FINER (larger) of the two
    # (never lose resolution), then give every element the sf that
    # reproduces that decimal-place resolution at its own magnitude.  So
    # ``[-9.900 .. 100.1] °C`` → 3 dp everywhere (…, 99.100, 100.100), and
    # ``[-9.9 .. 100.1] °C`` → 1 dp everywhere (…, 99.1, 100.1) with no
    # drift to ``10.`` / ``100``.
    def _int_digits(v):
        av = abs(float(v))
        if av < 1:
            return 0
        return int(math.floor(math.log10(av))) + 1

    def _dp_of(endpoint):
        sf = sigfigs_of(endpoint)
        if not math.isfinite(sf):
            return None
        val = _peel(endpoint)[0]
        # A pure-zero endpoint (``0.0``) carries sf = (decimals)+1 by the
        # zero-literal convention, but its DECIMAL PLACES are just the
        # written decimals; subtract the convention's extra count so
        # ``0.0`` reads as 1 dp, ``0.00`` as 2 dp.
        if float(val) == 0:
            return max(int(sf) - 1, 0)
        return max(int(sf) - _int_digits(val), 0)

    dp_start = _dp_of(start)
    dp_stop = _dp_of(stop)
    dps = [d for d in (dp_start, dp_stop) if d is not None]
    range_dp = max(dps) if dps else None        # finer endpoint wins

    def _sf_for(value):
        # sf that renders ``value`` at the fixed ``range_dp`` decimal places.
        if range_dp is None:
            return _INF
        return _int_digits(value) + range_dp

    # Float fallback: build the list explicitly so we don't depend on
    # range() (which is integer-only).
    if isinstance(s, float) or isinstance(e, float) or isinstance(st, float):
        n = int(round((e - s) / st)) + 1
        if n <= 0:
            return []
        pts = [s + i * st for i in range(n)]
        if range_dp is not None:
            return [Sig(p, _sf_for(p)) for p in pts]
        return pts

    # Integer path: use range() with the appropriate inclusive offset.
    # Integer endpoints are typically exact counts (``[1..5]``) → no
    # decimal-place precision to carry; but if the user wrote them with an
    # explicit precision (rare for ints) honour it via Sig elements.
    if st > 0:
        rng = range(s, e + 1, st)
    else:
        rng = range(s, e - 1, st)
    if range_dp is not None and range_dp > 0:
        return [Sig(v, _sf_for(v)) for v in rng]
    return rng


def _is_physical(x) -> bool:
    """Duck-typed test for a forallpeople ``Physical`` (has ``.value``
    and ``.dimensions``).  A ``_DeltaTemp`` is deliberately NOT one —
    it has neither attribute."""
    return hasattr(x, "value") and hasattr(x, "dimensions")


# Kelvin → reading in a written scale, and the factor that turns a kelvin
# SPAN into a span in that scale (1 K = 1 °C-degree = 9/5 °F-degrees).
# The display mirror of these lives in ``sigfig._format_temperature``.
_TEMP_READING = {
    "K":    lambda k: k,
    "degC": lambda k: k - 273.15,
    "degF": lambda k: (k - 273.15) * 9.0 / 5.0 + 32.0,
    "degR": lambda k: k * 9.0 / 5.0,
}
_TEMP_SPAN_FACTOR = {"K": 1.0, "degC": 1.0, "degF": 9.0 / 5.0, "degR": 9.0 / 5.0}


def _snap(x: float) -> float:
    """Kill binary-float dust from a unit conversion (``218.15 - 273.15``
    is ``-55.00000000000003``) so an enumerated range starts on the
    number the user actually wrote."""
    return float(f"{x:.12g}")


def _range_inc_physical(start, stop, step=None):
    """``_range_inc`` for endpoints that carry a unit.

    Both endpoints must be ``Physical`` (optionally ``Sig``-wrapped) of
    the same dimension.  The sequence is enumerated NUMERICALLY in the
    unit the start was written in — the auto-prefixed display unit for
    ordinary quantities (``1 kΩ .. 5 kΩ`` steps in kΩ, not Ω), the
    written scale for absolute temperatures (``-55 °C .. 125 °C`` steps
    in °C-degrees, and comes back tagged so it displays in °C) — and
    the unit is then re-applied element-wise.  The numeric enumeration
    goes through ``_range_inc`` itself, so decimal-place precision
    behaves exactly as for ``(-55.0 .. 125.0) °C``.

    ``step`` may be a plain number (meaning "that many of the unit"), a
    ``Physical`` of the same dimension, or — for temperatures — a
    ``ΔC``/``ΔK``/``ΔF`` span.
    """
    from .sigfig import Sig, _is_pure_temperature
    raw_s, sf_s = _peel(start)
    raw_e, sf_e = _peel(stop)
    if not (_is_physical(raw_s) and _is_physical(raw_e)):
        raise TypeError(
            f"range endpoints must both carry a unit or both be plain "
            f"numbers: got {start!r} and {stop!r}")
    if raw_s.dimensions != raw_e.dimensions:
        raise TypeError(
            f"range endpoints have different units: {start!r} and {stop!r}")

    raw_step = None if step is None else _peel(step)[0]

    # ---- Absolute temperatures: enumerate in the written scale ----
    if _is_pure_temperature(raw_s):
        scale = (getattr(start, "_temp_scale", None)
                 or getattr(stop, "_temp_scale", None) or "K")
        reading = _TEMP_READING.get(scale, _TEMP_READING["K"])
        s_num = Sig(_snap(reading(raw_s.value)), sf_s)
        e_num = Sig(_snap(reading(raw_e.value)), sf_e)
        if raw_step is None:
            st = None
        elif _is_physical(raw_step) and getattr(step, "_temp_scale", None) \
                and _is_pure_temperature(raw_step):
            # ``.. 5 °C`` as a step means five degrees on that scale.
            st = _snap(reading(raw_step.value))
        elif _is_physical(raw_step):
            st = _snap(raw_step.value * _TEMP_SPAN_FACTOR.get(scale, 1.0))
        else:
            # A ``ΔC``/``ΔK``/``ΔF`` span floats to its kelvin span; a
            # plain number is already "degrees on this scale".
            span_k = float(raw_step)
            st = _snap(span_k * _TEMP_SPAN_FACTOR.get(scale, 1.0)) \
                if type(raw_step).__name__ == "_DeltaTemp" else span_k
        nums = _range_inc(s_num, e_num, st)
        from .extra_units import from_degC, from_degF, from_degR, _tag_temp_scale
        ctor = {"degC": from_degC, "degF": from_degF, "degR": from_degR}.get(scale)
        if ctor is not None:
            return ctor(list(nums))
        one_K = raw_s / raw_s.value
        vals = [float(n) for n in nums]
        return _tag_temp_scale(np.array(vals, dtype=float) * one_K, list(nums), "K")

    # ---- Ordinary quantities: enumerate in the endpoints' display unit ----
    # forallpeople auto-prefixes by magnitude (``0.5 V`` shows as
    # ``500 mV``), so take the COARSER prefix of the two ends — for
    # ``0.5 V .. 2.5 V`` that is volts, giving 0.5, 1.0, … exactly as
    # ``[0.5..2.5..0.5] V`` does.  A zero endpoint has no prefix of its
    # own and defers to the other end.
    #
    # An endpoint WRITTEN with a unit (``0.5 V``, tagged by ``_wu``)
    # settles the question outright: enumerate in that unit and hand
    # the tag on, so ``[0.5 V..2.5 V..0.5 V]`` prints ``0.5 V`` like
    # ``[0.5..2.5..0.5] V`` — never auto-prefixed to ``500 mV``.
    unit = None
    for end in (start, stop):
        pref = getattr(end, "_unit_pref", None)
        if type(pref).__name__ != "_DisplayUnit":
            continue
        pu = getattr(pref, "physical", None)
        pu = pu.value if isinstance(pu, Sig) else pu
        if (_is_physical(pu) and pu.dimensions == raw_s.dimensions
                and pu.value != 0):
            ratio = abs(pu.value)
            unit = pref
            break
    if unit is None:
        ratios = []
        for r in (raw_s, raw_e):
            if r.value != 0:
                ratio = abs(r.value / float(r))    # display-unit size in SI (1000 for kΩ)
                pow10 = 10.0 ** round(math.log10(ratio))
                if abs(ratio / pow10 - 1.0) < 1e-9:
                    ratio = pow10                  # snap 999.9999999 → 1000
                ratios.append(ratio)
        if not ratios:
            return CommaArray(np.array([start], dtype=object))
        ratio = max(ratios)
        base = raw_s if raw_s.value != 0 else raw_e
        unit = (base / base.value) * ratio         # one display unit, as a Physical

    def _num(x):
        # ``1 kΩ`` is 1000.0 / 1000 = 1.0 — hand the numeric range an int
        # when the reading is whole, so ``[1 kΩ..5 kΩ]`` takes the same
        # integer path (and prints ``1 kΩ``) as ``[1..5] kΩ`` does.
        x = _snap(x)
        return int(x) if x.is_integer() else x

    s_num = Sig(_num(raw_s.value / ratio), sf_s)
    e_num = Sig(_num(raw_e.value / ratio), sf_e)
    if raw_step is None:
        st = None
    elif _is_physical(raw_step):
        if raw_step.dimensions != raw_s.dimensions:
            raise TypeError(
                f"range step {step!r} has a different unit than {start!r}")
        st = _num(raw_step.value / ratio)
    else:
        st = raw_step
    # Every element goes back as a ``Sig`` (exact when the range carried
    # no decimal-place precision) so the unit multiplication yields
    # sf-aware values — a bare float times a Physical would print at
    # forallpeople's fixed 3-decimal default (``1.000 kΩ``).
    elems = [n if isinstance(n, Sig) else Sig(n, _INF) for n in _range_inc(s_num, e_num, st)]
    return CommaArray(np.array(elems, dtype=object)) * unit


def _range_ineq(start, stop, left_closed, right_closed):
    """Range for an inequality-style ``for`` header.

    Backs the ``for a ≤ j ≤ b:`` family of loops.  ``left_closed`` /
    ``right_closed`` say whether each bound is inclusive — ``≤`` / ``≥``
    are closed (``True``), ``<`` / ``>`` are open (``False``).
    Direction is inferred from the endpoints: ascending when
    ``stop >= start``, descending otherwise.

    Two kinds of bound are supported:

    * **Numbers** — the result is a ``range`` (or float list), with the
      strict bounds handled by a ``±1`` offset, exactly as a hand-written
      ``range(start+1, stop+1)`` would.  ``for 1 < j <= 5:`` → 2,3,4,5.

    * **Dates / datetimes** — from the DSL's ``"..."ₜᵢₘₑ`` literals.
      The result is a list of dates stepping one day at a time; a strict
      bound drops the corresponding endpoint.  ``for d0 ≤ d ≤ d1:``
      walks d0 through d1 inclusive; ``for d0 < d < d1:`` excludes both.

    Mixed or unsupported bound types fall through to the numeric path,
    whose ``_peel`` raises an informative error.
    """
    import datetime as _dt

    # ---- Date / datetime bounds ----
    if isinstance(start, _dt.date) and isinstance(stop, _dt.date):
        one = _dt.timedelta(days=1)
        ascending = stop >= start
        lo, hi = start, stop
        # An open (strict) bound nudges that endpoint inward by one day,
        # so the excluded date never appears.
        if ascending:
            if not left_closed:
                lo = lo + one
            if not right_closed:
                hi = hi - one
            return _range_inc(lo, hi, one) if lo <= hi else []
        else:
            if not left_closed:
                lo = lo - one
            if not right_closed:
                hi = hi + one
            return _range_inc(lo, hi, one) if lo >= hi else []

    # ---- Numeric bounds — replicate the classic ±1 offset logic ----
    s = _peel(start)[0]
    e = _peel(stop)[0]
    ascending = e >= s
    if ascending:
        lo = s if left_closed else s + 1
        hi = e if right_closed else e - 1
        return _range_inc(lo, hi) if lo <= hi else []
    else:
        lo = s if left_closed else s - 1
        hi = e if right_closed else e + 1
        return _range_inc(lo, hi) if lo >= hi else []


def _as_matrix(value):
    """Wrap a 2-D rectangular numeric/symbolic list as a sympy ``Matrix``.

    Called (via an AST rewrite) around every list-of-lists *literal* in
    DSL source, so ``[[1,2],[3,4]]`` becomes a real matrix supporting
    linear-algebra operators (``*`` product, ``.T`` / ``ᵀ`` transpose,
    ``.det()``, ``.inv()``, …) without an explicit ``Matrix(...)`` call.

    Defensive by design — only a genuine matrix shape is converted; for
    anything else the original list is returned UNCHANGED, so ordinary
    list-of-lists uses keep working:

      * not a list, or empty               → unchanged
      * rows aren't all lists              → unchanged (not 2-D)
      * ragged rows / empty rows           → unchanged
      * any cell is a str/list/tuple/dict  → unchanged (not a scalar grid)
      * sympy import fails / build fails   → unchanged

    Numeric cells wrapped in ``Sig`` are unwrapped to their underlying
    value first, so an integer matrix stays integer (``1`` not ``1.0``).
    """
    if not isinstance(value, list) or len(value) == 0:
        return value
    if not all(isinstance(r, list) for r in value):
        return value
    width = len(value[0])
    if width == 0 or not all(len(r) == width for r in value):
        return value

    cells = []
    for row in value:
        row_cells = []
        for c in row:
            # A matrix cell must be a scalar — reject any nested
            # container or string, leaving the whole thing a plain list.
            if isinstance(c, (list, tuple, dict, set, str, bytes)):
                return value
            # Unwrap a ``Sig`` to its underlying value so an integer
            # matrix stays integer rather than coercing to float.
            row_cells.append(c.value if type(c).__name__ == "Sig" else c)
        cells.append(row_cells)

    try:
        import sympy as _sym
        return _DSLMatrix(_sym.Matrix(cells))
    except Exception:
        return value


# Cache the shim class so we build it once (it subclasses sympy's
# MutableDenseMatrix, which is only importable when sympy is present).
_DSL_MATRIX_CLS = None


def _strip_inner_dollars(latex):
    """Remove spurious math-mode ``$`` toggles from inside a ``$…$`` LaTeX
    string.

    forallpeople emits a stray ``$`` *inside* the ``\\mathrm{}`` for some
    symbols — notably ohm: ``\\mathrm{$\\Omega$}`` (and
    ``\\mathrm{k$\\Omega$}``).  Since the whole string is already wrapped
    in one outer ``$…$``, those inner ``$`` are always spurious and leave
    MathJax with unbalanced delimiters (``20 Ω`` renders broken).  Take
    the body between the outer ``$…$`` and drop every internal ``$``.
    A string without an outer wrapper just has stray ``$`` removed.
    Returns non-strings unchanged.
    """
    if not isinstance(latex, str) or len(latex) < 2:
        return latex
    if latex.startswith("$") and latex.endswith("$"):
        return "$" + latex[1:-1].replace("$", "") + "$"
    return latex.replace("$", "")


def _strip_displaystyle(latex):
    """Remove a leading ``\\displaystyle`` from a ``$…$`` LaTeX string.

    sympy prepends ``\\displaystyle`` to its LaTeX, which makes MathJax
    render the result CENTRED (display math) rather than left-aligned
    inline.  Our scalar/unit/radix outputs have no ``\\displaystyle`` and
    so render left; stripping it here makes matrices and sympy
    expressions match, for one consistent left-aligned look.  Returns
    the input unchanged if it isn't a string or has no ``\\displaystyle``.
    """
    if not isinstance(latex, str):
        return latex
    # Handle both ``$\displaystyle …$`` and a bare ``\displaystyle …``.
    out = latex.replace(r"$\displaystyle ", "$", 1)
    if out is latex or out == latex:
        out = latex.replace(r"\displaystyle ", "", 1)
    return out


def _DSLMatrix(m):
    """Wrap a sympy ``Matrix`` ``m`` in a row-indexable subclass.

    Auto-wrapped matrix literals must keep working with the DSL's 2-D
    subscript notation ``M₁͵₁`` → ``M[1][1]``, which is Python nested-
    list indexing.  A bare sympy ``Matrix`` indexes differently: ``M[1]``
    is a FLAT scalar (element 1 in row-major order), so ``M[1][1]`` then
    tries to subscript a scalar and fails.

    This shim overrides ``__getitem__`` so a single *integer* index
    returns that ROW (a 1×n matrix, itself integer-indexable), making
    ``M[1][1]`` resolve to the (1,1) element — while the native sympy
    forms ``M[1, 1]`` (tuple) and ``M[1:3]`` (slice) are passed straight
    through.  Every other matrix operation is inherited unchanged, so
    ``*`` / ``.T`` / ``.det()`` / ``.inv()`` and the rest behave exactly
    as sympy's.
    """
    global _DSL_MATRIX_CLS
    if _DSL_MATRIX_CLS is None:
        import sympy as _sym

        class _DSLMatrixCls(_sym.MutableDenseMatrix):
            # ---- 0-indexing contract -----------------------------------
            # A DSL matrix is ZERO-indexed, like everything else in
            # Python.  *User* indexing in the DSL is rewritten to call
            # ``_dsl_get`` / ``_dsl_set``, which add bounds-checking with
            # clear errors and sensible row/element semantics; the indices
            # themselves are passed through unshifted, matching plain
            # lists.  ``__getitem__`` and ``__setitem__`` stay native for
            # sympy's internal use (``det``, ``inv``, multiplication, …),
            # and the ``M[i][j]`` row-chaining behaviour is preserved so
            # internal and notational forms agree.

            @staticmethod
            def _to_int(k):
                if isinstance(k, bool):
                    return None
                if isinstance(k, int):
                    return k
                try:
                    return k.__index__()
                except (AttributeError, TypeError):
                    return None

            def __getitem__(self, key):
                # Native 0-based, but keep the single-int → row behaviour
                # so sympy internals and any 0-based ``M[i][j]`` still work.
                idx = None if isinstance(key, (tuple, slice)) else self._to_int(key)
                if idx is not None and self.rows != 1:
                    return self.row(idx)
                return super().__getitem__(key)

            def _bounds(self, i, limit, what):
                # Python semantics: negative indices count from the end.
                orig = i
                if i < 0:
                    i += limit
                if i < 0 or i >= limit:
                    plural = "s" if limit != 1 else ""
                    raise IndexError(
                        f"{what} {orig} is out of range for a matrix "
                        f"with {limit} {what.lower()}{plural} "
                        f"(valid 0..{limit - 1})"
                    )
                return i

            def _dsl_get(self, *idx):
                """0-based element access for DSL notation ``M₀͵₁`` /
                ``M[0,1]``.  One index on a vector → that element; one on
                a matrix → that row (itself ``_dsl_get``-able);
                two → the (row, col) element."""
                if len(idx) == 1:
                    i = self._to_int(idx[0])
                    if i is None:
                        return self[idx[0]]            # symbolic → native
                    if self.rows == 1:
                        return super().__getitem__(self._bounds(i, self.cols, "Column"))
                    if self.cols == 1:
                        return super().__getitem__(self._bounds(i, self.rows, "Row"))
                    return self.row(self._bounds(i, self.rows, "Row"))
                if len(idx) == 2:
                    i, j = self._to_int(idx[0]), self._to_int(idx[1])
                    if i is None or j is None:
                        return self[idx]               # symbolic → native
                    r = self._bounds(i, self.rows, "Row")
                    c = self._bounds(j, self.cols, "Column")
                    return super().__getitem__((r, c))
                # Higher arity: chain element-wise.
                out = self
                for k in idx:
                    out = out._dsl_get(k) if hasattr(out, "_dsl_get") else out[k]
                return out

            def _dsl_set(self, value, *idx):
                """0-based assignment for ``M₀͵₁ := value``."""
                if len(idx) == 1:
                    i = self._to_int(idx[0])
                    if self.rows == 1:
                        return super().__setitem__(self._bounds(i, self.cols, "Column"), value)
                    if self.cols == 1:
                        return super().__setitem__(self._bounds(i, self.rows, "Row"), value)
                    return super().__setitem__(self._bounds(i, self.rows, "Row"), value)
                if len(idx) == 2:
                    i, j = self._to_int(idx[0]), self._to_int(idx[1])
                    r = self._bounds(i, self.rows, "Row")
                    c = self._bounds(j, self.cols, "Column")
                    return super().__setitem__((r, c), value)
                raise IndexError("unsupported index arity for assignment")

            def _repr_latex_(self):
                # Honour a ``▸`` radix display tag (``M ▸ hex``) for the
                # cell's auto-output, so a bare ``M ▸ hex`` looks the
                # same as ``pp(M ▸ hex)``.  Without a tag, defer to
                # sympy's own LaTeX.  The leading ``\displaystyle`` is
                # stripped so the matrix renders LEFT-aligned inline,
                # matching the scalar/unit outputs.  This is the standard
                # ``\begin{bmatrix}`` form, which renders well in the
                # live JupyterLab notebook (MathJax).
                fmt = getattr(self, "_dsl_radix", None)
                if fmt is None:
                    return _strip_displaystyle(super()._repr_latex_())
                try:
                    from .sigfig import radix as _radix

                    def _cell(v):
                        try:
                            return str(_radix(int(v), fmt))
                        except Exception:
                            return str(v)

                    rows = [" & ".join(_cell(v) for v in row)
                            for row in self.tolist()]
                    body = r" \\ ".join(rows)
                    return (r"$\begin{bmatrix} "
                            + body + r" \end{bmatrix}$")
                except Exception:
                    return _strip_displaystyle(super()._repr_latex_())

        _DSL_MATRIX_CLS = _DSLMatrixCls
    return _DSL_MATRIX_CLS(m)


def _idx(obj, *indices):
    """Runtime index dispatcher for DSL subscript notation.

    The subscript rewriter emits ``_idx(M, 0, 1)`` for ``M₀͵₁`` because
    it can't know at rewrite-time whether ``M`` is a DSL matrix or a
    plain Python list.  Here, at runtime, we choose:

      * a DSL matrix (has ``_dsl_get``) → 0-based access with bounds
        checking and clear errors (``M₀͵₁`` is row 0, col 1);
      * anything else (list, tuple, numpy array, dict, …) → ordinary
        Python ``obj[i][j]…``.

    Both paths are 0-indexed — the dispatch exists for the matrix's
    row/element access semantics and error messages, not for a different
    numbering convention.
    """
    if hasattr(obj, "_dsl_get"):
        # A slice (or any non-integer key) on a matrix has no single-
        # element meaning — defer to the object's native ``[]`` so
        # ``M[1:3]``-style access still works as sympy defines it.
        if any(isinstance(i, slice) for i in indices):
            return obj[indices[0]] if len(indices) == 1 else obj[indices]
        return obj._dsl_get(*indices)
    out = obj
    for i in indices:
        out = out[i]
    # Indexing a numpy-backed container (the DSL's ``CommaArray``) yields
    # a numpy scalar (``np.int64(6)``) whose ``repr`` is the noisy
    # ``np.int64(6)`` in a bare cell — even though ``str`` is just ``6``.
    # A single element accessed by the user should be a clean Python
    # scalar, so unwrap a 0-d numpy scalar via ``.item()``.  An actual
    # numpy *array* (``.shape`` non-empty) is left as-is — only true
    # scalars are unwrapped.
    if type(out).__module__ == "numpy" and getattr(out, "shape", None) == ():
        try:
            return out.item()
        except Exception:
            pass
    return out


def _idx_set(obj, value, *indices):
    """Assignment companion of :func:`_idx` for ``M₀͵₁ := value``.

    A DSL matrix routes to ``_dsl_set`` (0-based, bounds-checked, and —
    crucially — mutating the matrix itself rather than a row copy); any
    other container uses ordinary assignment, walking to the last
    container and setting the final key.
    """
    if hasattr(obj, "_dsl_set"):
        return obj._dsl_set(value, *indices)
    target = obj
    for i in indices[:-1]:
        target = target[i]
    target[indices[-1]] = value
    return value


def _str_range(start, stop, step=None):
    """Inclusive range between two STRING endpoints — the string-aware
    companion of :func:`_range_inc`.

    Backs string-literal ranges in the DSL: ``['1'..'5']``,
    ``['A'..'E']``, ``['C8'..'C13']``.  Dispatches on the *shape* of the
    endpoints:

    * **Pure digits** — ``'1'..'5'`` → ``['1','2','3','4','5']``.  The
      result stays strings (not ints).  If the start is zero-padded
      (``'01'``) the width is preserved.

    * **Single letter** — ``'A'..'E'`` → ``['A',…,'E']``; lowercase and
      Greek (``'α'..'δ'``) work the same way, by code point.  Multi-
      letter endpoints are NOT sequenced as letters (see prefix+number).

    * **Prefix + numeric tail** — ``'C8'..'C13'`` →
      ``['C8','C9',…,'C13']``; the non-digit prefix is held constant and
      the trailing number is incremented.  Zero-padding is preserved
      ONLY when the start's number is zero-padded: ``'R099'..'R102'`` →
      ``['R099','R100','R101','R102']`` (the natural number wins when it
      outgrows the pad), but ``'R8'..'R12'`` → ``['R8',…,'R12']``
      unpadded.  Both prefixes must match.

    ``step`` is an optional integer stride (sign inferred from the
    endpoints when omitted).  A shape that fits none of the above raises
    ``ValueError`` — e.g. a two-letter endpoint like ``'aa'``.
    """
    if not (isinstance(start, str) and isinstance(stop, str)):
        raise TypeError(
            f"_str_range endpoints must both be strings, got "
            f"{type(start).__name__} and {type(stop).__name__}")

    def _walk(a, b, width, prefix=""):
        s = step if step else (1 if b >= a else -1)
        if s == 0:
            raise ValueError("string range step cannot be zero")
        stop_exclusive = b + (1 if s > 0 else -1)
        return [f"{prefix}{str(n).zfill(width)}"
                for n in range(a, stop_exclusive, s)]

    # Shape 1 — pure digits.  Width preserved when the start is padded.
    if start.isdigit() and stop.isdigit():
        width = len(start) if (len(start) > 1 and start[0] == "0") else 0
        return _walk(int(start), int(stop), width)

    # Shape 2 — a single alphabetic character (ASCII or Greek …).
    if (len(start) == 1 and len(stop) == 1
            and start.isalpha() and stop.isalpha()):
        a, b = ord(start), ord(stop)
        s = step if step else (1 if b >= a else -1)
        stop_exclusive = b + (1 if s > 0 else -1)
        return [chr(c) for c in range(a, stop_exclusive, s)]

    # Shape 3 — prefix + numeric tail (``C8``, ``R099`` …).
    m1 = re.fullmatch(r"(.*?)(\d+)", start)
    m2 = re.fullmatch(r"(.*?)(\d+)", stop)
    if m1 and m2:
        pre1, num1 = m1.group(1), m1.group(2)
        pre2, num2 = m2.group(1), m2.group(2)
        if pre1 != pre2:
            raise ValueError(
                f"string range prefixes differ: {pre1!r} vs {pre2!r} "
                f"(in {start!r}..{stop!r})")
        width = len(num1) if (len(num1) > 1 and num1[0] == "0") else 0
        return _walk(int(num1), int(num2), width, prefix=pre1)

    raise ValueError(
        f"cannot build a string range from {start!r}..{stop!r} — expected "
        f"digits ('1'..'5'), a single letter ('A'..'E'), or a prefix with "
        f"a numeric tail ('C8'..'C13')")


def percent(x):
    return x / 100


def permille(x):
    return x / 1000


def fact(x):
    """Factorial.

    Numeric: ``fact(5) == 120``, requires a non-negative integer (or
    integer-valued float).  Symbolic: ``fact(n)`` becomes
    ``sympy.factorial(n)``, which has the right calculus behaviour
    (``diff(fact(n), n)`` involves ``polygamma``, etc.) and survives
    sympy operations like ``simplify`` and ``expand``.
    """
    if _is_symbolic(x):
        import sympy
        return sympy.factorial(x)
    raw, sf = _peel(x)
    if isinstance(raw, int):
        return Sig(math.factorial(raw), sf)
    if isinstance(raw, float) and raw.is_integer():
        return Sig(math.factorial(int(raw)), sf)
    raise TypeError(f"factorial ! requires a non-negative integer, got {x!r}")


def mod(x, y):
    return x % y


def _looks_numeric(x):
    """True when ``x`` should be treated as a number by ``_abs_or_size``
    rather than as a collection.  A ``Sig`` is numeric; a bare int /
    float / complex is numeric; a ``forallpeople.Physical`` is numeric.
    Anything exposing ``__len__`` that ISN'T one of these is a
    collection."""
    if isinstance(x, (int, float, complex)):
        return True
    if isinstance(x, Sig):
        return True
    # forallpeople Physical — has ``.value`` and ``.dimensions`` and
    # supports ``abs()``; treat as numeric.
    if hasattr(x, "value") and hasattr(x, "dimensions"):
        return True
    return False


def _abs_or_size(x):
    """Runtime target of the ``|…|`` bar notation.

    In mathematics ``|·|`` carries two meanings, disambiguated by what
    sits between the bars:

    * **absolute value / magnitude** for a number — ``|−3| = 3``;
    * **cardinality** for a set, or length for any sized collection —
      ``|{1, 2, 3}| = 3``.

    ``rewrite_abs_bars`` cannot tell which is meant from the source text
    alone (``|x|`` could be either, depending on what ``x`` holds at
    runtime), so it always emits ``_abs_or_size(...)`` and the decision
    is made here, on the actual value.

    Dispatch rule:

    * If ``x`` is a sized collection — ``set``, ``frozenset``, ``dict``,
      ``list``, ``tuple``, ``str``, or anything else exposing
      ``__len__`` that is not number-like — return ``len(x)``.
    * Otherwise treat ``x`` as a number and return ``abs(x)``.

    A value that is BOTH sized and number-like is vanishingly rare; the
    ``_looks_numeric`` guard resolves it in favour of the numeric
    reading.  ``Sig``-wrapped values take the numeric branch — ``Sig``
    forwards ``__abs__`` to its inner value and keeps the
    significant-figure count, so ``|measured_value|`` still tracks
    precision.
    """
    if isinstance(x, (set, frozenset, dict, list, tuple, str)):
        return len(x)
    if hasattr(x, "__len__") and not _looks_numeric(x):
        return len(x)
    return abs(x)


def plusminus(x, y):
    """Build an interval from a centre and a half-width.

    ``plusminus(5, 0.1)`` → ``Range(4.9, 5.1)``.

    If either input is a ``Sig`` (carrying significant-figures
    information), the resulting Range is itself wrapped in a Sig with
    the appropriate sf — so subsequent arithmetic on the result still
    threads sf through correctly.  The endpoints inside the Range are
    bare (Physicals or floats), not Sigs — that's what avoids the
    visual collapse where both ``2.156 MΩ`` and ``2.244 MΩ`` print
    identically at sf=2.

    Without this unification, two semantically equivalent forms
    produced visually different output:

    ``val ± val · tol``                — Range(Sig, Sig), each endpoint
                                          rounded to sf=2 → both print
                                          as ``2.2 MΩ``
    ``val · (1 ± tol)``                — Sig(Range(Physical, Physical),
                                          sf=2), endpoints print at
                                          forallpeople's default
                                          precision → ``2.16 MΩ`` ‥
                                          ``2.244 MΩ``

    Now both go through the second path.
    """
    from .sigfig import (Sig, _unwrap, _sf_of, _addsub_sf,
                         _is_pure_temperature)  # local import to
    # avoid a circular reference at module-load time; sigfig imports
    # this module's Range class.
    if isinstance(x, Sig) or isinstance(y, Sig):
        # Unwrap each to a bare value, build the Range from bare
        # endpoints, then wrap.  sf is computed by the addsub rule
        # because the natural ``a - b`` and ``a + b`` operations
        # produce an interval whose width carries the larger sf of
        # the two inputs' uncertainty.
        xv = _unwrap(x)
        yv = _unwrap(y)
        xs = _sf_of(x)
        ys = _sf_of(y)
        # Remember the centre's temperature scale (if it was written in
        # °C/°F) so the resulting interval can display in that scale —
        # ``25 °C ± 10 ΔC`` → ``(15 °C ‥ 35 °C)`` rather than kelvin.
        centre_scale = getattr(x, "_temp_scale", None)

        # --- Unary ``±`` with a temperature tolerance --------------------
        # ``±10 °C`` rewrites to ``plusminus(0, <temp>)``: a plain-zero
        # centre with a temperature/Δ-temperature tolerance.  By the
        # neutral-element logic the result should be the symmetric interval
        # in the tolerance's scale — ``±10 °C`` → ``(-10 °C ‥ 10 °C)``,
        # ``±10 ΔC`` → ``(-10 ΔC ‥ 10 ΔC)`` — NOT a kelvin interval centred
        # at absolute zero.  So when the centre is exactly zero (the unary
        # marker) and the tolerance carries temperature, treat the centre
        # as "zero in the tolerance's scale": adopt that scale for display
        # and let the offset-strip below run by lifting the centre to the
        # 0-point (273.15 K) of an absolute °C/°F tolerance.
        _y_is_delta = type(yv).__name__ == "_DeltaTemp" or \
            type(y).__name__ == "_DeltaTemp"
        if (not _is_pure_temperature(xv) and float(xv) == 0
                and (_is_pure_temperature(yv) or _y_is_delta)):
            tol_scale = getattr(y, "_temp_scale", None)
            if _y_is_delta:
                # Δ tolerance → symmetric Δ interval (no absolute offset):
                # ``±10 ΔC`` → ``(-10 ‥ 10)`` in kelvin-span terms.  We do
                # NOT tag an absolute °C scale here (that would wrongly
                # apply the 273.15 offset to a span); the interval is a
                # plain symmetric difference range.
                span = _unwrap(y)              # kelvin span as Physical/number
                return Sig(Range(-span, span), ys)
            # Absolute-temperature tolerance (``±10 °C``): centre at the
            # 0-point of that scale so the interval is symmetric in °C.
            centre_scale = "degC"  # ``±`` of a bare °C tolerance reads as °C
            xv = 273.15 * (yv / float(yv)) if float(yv) != 0 else yv * 0
            # fall through to the standard offset-strip + Range build.

        # Temperature tolerance is an INTERVAL, not an absolute point.
        # ``45 °C ± 55 °C`` means "45 °C give-or-take a 55-degree span",
        # but ``55 °C`` is stored as the absolute 328.15 K.  When BOTH
        # operands are pure temperatures, convert the tolerance from
        # absolute kelvin to its interval magnitude (subtract the
        # 273.15 K offset) so the result is ``(-10 °C ‥ 100 °C)`` rather
        # than the nonsensical ``(-10 K ‥ 650 K)``.  A 55-degree °C span
        # equals a 55 K span, so no further scaling is needed.  Only the
        # tolerance (``yv``) is adjusted; the centre (``xv``) stays the
        # absolute temperature it is.  (A ``_DeltaTemp`` tolerance is
        # already an interval — its ``_unwrap`` gives the kelvin span — so
        # this offset-strip applies only to the absolute-±-absolute form.)
        if _is_pure_temperature(xv) and _is_pure_temperature(yv):
            try:
                yv = yv - 273.15 * (yv / float(yv))  # strip offset, keep unit
            except Exception:
                pass
        low = xv - yv
        high = xv + yv
        new_sf = _addsub_sf(xv, yv, xs, ys, low)
        result = Sig(Range(low, high), new_sf)
        if centre_scale and centre_scale != "K":
            try:
                result._temp_scale = centre_scale
            except Exception:
                pass
        return result
    return Range.from_pm(x, y)


def _interval(low, high):
    """Closed interval from its two ends: ``low ‥ high``.

    This is the INPUT form of ``Range``'s own printout — ``5 ± 2``
    displays as ``(3 ‥ 7)``, and pasting ``(3 ‥ 7)`` back in rebuilds
    the same ``Range``.  Not to be confused with ``a..b`` (two ASCII
    dots), which ENUMERATES every step between the ends: ``3..7`` is
    ``[3, 4, 5, 6, 7]``, ``3 ‥ 7`` is the single interval ``[3, 7]``.

    Mirrors ``plusminus``: a ``Sig`` endpoint makes the result a
    ``Sig(Range(...))`` with bare endpoints inside (so the two ends
    never collapse visually), carrying the smaller sf of the two, and
    a temperature written in °C/°F keeps its scale so the interval
    displays as ``(-55.0 °C ‥ 125. °C)`` rather than in kelvin.
    """
    from .sigfig import Sig, _unwrap, _sf_of
    if isinstance(low, Sig) or isinstance(high, Sig):
        sfs = [s for s in (_sf_of(low), _sf_of(high)) if s is not None]
        result = Sig(Range(_unwrap(low), _unwrap(high)), min(sfs) if sfs else _INF)
        scale = (getattr(low, "_temp_scale", None)
                 or getattr(high, "_temp_scale", None))
        if scale and scale != "K":
            try:
                result._temp_scale = scale
            except Exception:
                pass
        return result
    return Range(low, high)


def σ(data, ddof=0):
    """Standard deviation, significant-figure- and UNIT-aware.

    For plain numbers this is ``np.std`` with sf tracking.  For
    unit-carrying values (``[1.1, 2.2] mV``) the computation runs on the
    bare float magnitudes (base-SI) and the unit is re-attached to the
    result — ``np.std`` itself cannot operate on ``Physical`` objects.
    All elements must share a dimension (a mixed list raises naturally
    when the unit is re-applied).
    """
    data = list(data)
    sfs = [sigfigs_of(v) for v in data]
    raw = [_peel(v)[0] for v in data]
    # Detect unit-carrying elements: a forallpeople Physical converts to
    # its base-SI magnitude via float(); reconstruct a 1-magnitude unit
    # carrier from the first element to re-attach afterwards.
    unit = None
    if raw and any(type(r).__name__ == "Physical" for r in raw):
        first = next(r for r in raw if type(r).__name__ == "Physical")
        mag = float(first)
        if mag != 0:
            unit = first / mag          # Physical of magnitude 1
        floats = [float(r) for r in raw]
    else:
        floats = raw
    result = np.std(np.asarray(floats, dtype=float), ddof=ddof)
    sf = min(sfs) if sfs else _INF
    if unit is not None:
        return Sig(float(result) * unit, sf)
    return Sig(float(result), sf)


def Σ(data, axis=None):
    # Element-wise add propagates sf via decimal-place rule when any operand
    # is a Sig.  We piggy-back on Sig.__add__.
    it = iter(data)
    try:
        total = next(it)
    except StopIteration:
        return Sig(0, _INF)
    for v in it:
        total = total + v
    return total


def mean(data):
    """Arithmetic mean, significant-figure- and UNIT-aware.

    Built as ``Σ(data) / n`` so it inherits the toolkit's sf propagation
    and works for unit-carrying values (``mean([1.1, 2.2] mV)`` →
    ``… mV``) — unlike ``statistics.mean``, which requires exact integer
    ratios and cannot accept unit values.  NOTE: if you also
    ``from statistics import mean`` AFTER importing the toolkit, the
    stdlib version shadows this one; for unit data, import order matters
    (or call ``utils.circuit_dsl.mean`` explicitly).
    """
    data = list(data)
    if not data:
        raise ValueError("mean requires at least one data point")
    return Σ(data) / len(data)


def sqrt(x):
    return x ** 0.5


# ---------- new runtime helpers ----------

class CommaArray(np.ndarray):
    """ndarray subclass that prints with comma separators.

    Engineering notation reads ``[1.2, 3.4, 5.6] mV`` with commas; numpy's
    default ``str()`` strips the commas and shows ``[1.2 3.4 5.6]``.  This
    subclass uses ``np.array2string(separator=', ')`` for both ``str`` and
    ``repr``, so the rendered form matches the input form.

    The class is also used as the constructor for the rewriter that turns
    list-literal-times-unit expressions into ndarrays — see
    ``rewrite_list_unit_multiply``.  Because it's an ndarray subclass,
    ufuncs and slicing preserve the ``CommaArray`` type, so multiplication
    by units, indexing, and arithmetic all keep the comma display.
    """

    def __new__(cls, input_array):
        return np.asarray(input_array).view(cls)

    def __array_finalize__(self, obj):
        # Required for subclassing ndarray; nothing extra to track.
        pass

    # ---- unit binding for numeric-dtype arrays ---------------------------
    # ``[1..5] V`` is emitted as ``_CommaArray(_range_inc(1, 5)) * V``.  A
    # range of exact integers arrives as an int64 array, and int64 times
    # a BARE forallpeople unit (``V``, ``s``, ``m`` — the base units live
    # in builtins as plain Physicals) gives bare Physicals, which repr at
    # forallpeople's fixed three decimals: ``[1.000 V, 2.000 V, …]``.  A
    # PREFIXED unit (``kΩ``, ``ms`` — ``Sig``-wrapped in calc_symbols)
    # gives ``Sig`` elements and prints ``[1 kΩ, 2 kΩ, …]``.  Same range,
    # two looks.  So before a numeric array meets a Physical, lift its
    # elements to exact ``Sig``s (``sf = ∞`` — the endpoints were exact,
    # or ``_range_inc`` would already have produced ``Sig`` elements at
    # the endpoints' decimal-place precision).  Object-dtype arrays
    # (list literals, float ranges with precision, dates) pass through
    # untouched, and so does everything that isn't a unit multiplication
    # — ``[1..N]`` stays a fast numeric array for loops and numpy maths.
    def _lifted_for(self, other):
        if self.dtype == object or self.dtype.kind not in "iuf":
            return self
        if not _is_physical(other):
            return self
        flat = [Sig(v, _INF) for v in self.ravel().tolist()]
        return CommaArray(np.array(flat, dtype=object).reshape(self.shape))

    def __mul__(self, other):
        return np.ndarray.__mul__(self._lifted_for(other), other)

    def __rmul__(self, other):
        return np.ndarray.__rmul__(self._lifted_for(other), other)

    def __truediv__(self, other):
        return np.ndarray.__truediv__(self._lifted_for(other), other)

    def __rtruediv__(self, other):
        return np.ndarray.__rtruediv__(self._lifted_for(other), other)

    def _render(self):
        """Produce the bracketed, comma-separated string.

        For a normal numeric array this is just numpy's ``array2string``.
        For an OBJECT array of dates/datetimes (what a date range
        produces — see ``_range_inc``'s calendar path) numpy would call
        ``repr`` on each element and print the verbose
        ``datetime.date(2026, 5, 6)`` form.  An engineer wants the ISO
        form ``2026-05-06`` — the same spelling the ``"..."ₜᵢₘₑ`` input
        literals use — so for a date/datetime array we format the
        elements ourselves with ``str()`` (``date.__str__`` is ISO).
        A long range is elided in the middle, mirroring numpy.
        """
        import datetime as _dt
        flat = self.ravel().tolist()
        if flat and all(isinstance(v, (_dt.date, _dt.time, _dt.timedelta))
                        for v in flat):
            # date.__str__ / datetime.__str__ / time.__str__ are ISO;
            # timedelta.__str__ is the readable "1:30:00" form.
            if len(flat) > 12:
                head = ", ".join(str(v) for v in flat[:6])
                tail = ", ".join(str(v) for v in flat[-6:])
                return f"[{head}, ..., {tail}]"
            return "[" + ", ".join(str(v) for v in flat) + "]"
        return np.array2string(self, separator=', ')

    def __str__(self):
        return self._render()

    def __repr__(self):
        return self._render()

    def _repr_latex_(self):
        """Typeset a 1-D array as a bracketed math row so its elements
        render like every other value — units in ``\\mathrm{}``,
        scientific notation as ``·10ⁿ`` — instead of the raw comma-joined
        text repr (``[1.98842e+30 kg, …]``).

        Only attempted for a genuine 1-D numeric/unit array.  An object
        array of dates/times/timedeltas (a calendar range) has no math
        form, so we decline (return ``None``) and let the text repr show.
        A 2-D+ array also declines — the matrix path handles those.  Very
        long arrays are elided in the middle, mirroring the text form.
        """
        import datetime as _dt
        try:
            if self.ndim != 1:
                return None
            flat = self.ravel().tolist()
            if not flat:
                return None
            if all(isinstance(v, (_dt.date, _dt.time, _dt.timedelta))
                   for v in flat):
                return None  # calendar range → keep ISO text
            from .symbolic import _matrix_cell_latex
            if len(flat) > 20:
                head = [_matrix_cell_latex(v) for v in flat[:10]]
                tail = [_matrix_cell_latex(v) for v in flat[-10:]]
                inner = ", ".join(head) + r", \dots, " + ", ".join(tail)
            else:
                inner = ", ".join(_matrix_cell_latex(v) for v in flat)
            return r"$\left[" + inner + r"\right]$"
        except Exception:
            return None


# Alias used by the rewriter; underscore signals "internal but importable".
_CommaArray = CommaArray


def _is_symbolic(x):
    """Return True iff ``x`` is a sympy expression that should be
    handled symbolically rather than numerically.

    Returns True for:
    - Sympy constants like ``sym.pi``, ``sym.E``, ``sym.I`` (NumberSymbol
      / ImaginaryUnit / etc.).  These have ``is_number=True`` but are
      symbolic identities — we want ``Γ(π)`` to mean ``gamma(pi)``, not
      ``gamma(3.14159...)``.
    - Symbolic expressions with free symbols (``Symbol``, ``Add``,
      ``Mul``, function calls, etc.).

    Returns False for:
    - Plain sympy numbers (``Integer``, ``Float``, ``Rational``).  These
      are just numbers wearing sympy clothing and flow through the
      numeric path fine.
    - Non-sympy values (Python ints, floats, ``Sig`` instances, etc.).

    Sympy is imported lazily so the toolkit doesn't pay the import cost
    for users who never touch symbolic work.  If sympy isn't installed,
    this always returns False.
    """
    try:
        import sympy
    except ImportError:
        return False
    if not isinstance(x, sympy.Basic):
        return False
    # Plain numeric sympy types go through the numeric path.
    if isinstance(x, (sympy.Integer, sympy.Float, sympy.Rational)):
        return False
    return True


def _math_or_sympy(x, math_fn, sympy_fn_name):
    """Polymorphic dispatch for math wrappers.

    If ``x`` is a symbolic expression, return ``sympy.<fn>(x)``.
    Otherwise unwrap any ``Sig`` and call ``math_fn`` on the raw value,
    re-wrapping the result with the same sf.

    Used by ``Γ``, ``log10``, ``log2``, ``ln``, ``floor``, ``ceil`` so
    they all transparently delegate to sympy when handed a symbol —
    ``Γ(x)`` becomes ``sympy.gamma(x)``, ``ln(x+1)`` becomes
    ``sympy.log(x+1)``, etc.
    """
    if _is_symbolic(x):
        import sympy
        return getattr(sympy, sympy_fn_name)(x)
    raw, sf = _peel(x)
    return Sig(math_fn(float(raw)), sf)


def Γ(x):
    """Gamma function.

    On numeric input: ``Γ(5) == 24``, with sf preserved (gamma is a
    smooth analytic function so the input's precision carries through).
    On symbolic input: ``Γ(x)`` becomes ``sympy.gamma(x)`` so it
    survives ``expand``, ``diff``, etc.
    """
    if _is_symbolic(x):
        import sympy
        return sympy.gamma(x)
    raw, sf = _peel(x)
    # ``_peel`` may unwrap a ``Sig`` to expose a sympy expression
    # underneath — common after the source-transform turns ``n+1``
    # into ``n + Sig(1, ∞)``, where Sig's higher ``_op_priority``
    # makes the addition produce ``Sig(value=n+1, sf=∞)``.  Re-check
    # so we still dispatch to ``sympy.gamma`` for that case.
    if _is_symbolic(raw):
        import sympy
        return sympy.gamma(raw)
    if isinstance(raw, complex):
        # cmath has no gamma; fall back to math for real
        if raw.imag == 0:
            raw = raw.real
        else:
            raise TypeError("Γ of a complex number is not supported")
    return Sig(math.gamma(float(raw)), sf)


def Π(data):
    """Product of an iterable. Sf propagates by min (multiplication rule)."""
    sfs = []
    raw_values = []
    for v in data:
        rv, s = _peel(v)
        sfs.append(s)
        raw_values.append(rv)
    if not raw_values:
        return Sig(1, _INF)
    result = raw_values[0]
    for v in raw_values[1:]:
        result = result * v
    return Sig(result, min(sfs))


# Note on alternative glyphs.  ``Σ`` (U+03A3 GREEK CAPITAL LETTER SIGMA)
# and ``Π`` (U+03A0 GREEK CAPITAL LETTER PI) are Greek letters,
# repurposed here as sum and product.  The dedicated math glyphs
# ``∑`` (U+2211 N-ARY SUMMATION) and ``∏`` (U+220F N-ARY PRODUCT)
# are typographically distinct and visually larger, which many
# writers prefer.
#
# Python's parser rejects ``∑`` and ``∏`` as identifier characters
# (they're in Unicode category Sm — Math Symbol — which Python doesn't
# accept for identifiers), so we can't simply write ``∑ = Σ`` at module
# level.  Instead, ``normalize_source`` rewrites ``∑ → Σ`` and ``∏ → Π``
# in user code BEFORE Python's tokenizer sees it.  From the user's
# perspective both forms work; from Python's perspective only the
# Greek-letter forms ever appear.


def log10(x):
    """Base-10 logarithm.

    Numeric: preserves sf.  Symbolic: returns ``sympy.log(x, 10)``.
    """
    if _is_symbolic(x):
        import sympy
        return sympy.log(x, 10)
    raw, sf = _peel(x)
    return Sig(math.log10(float(raw)), sf)


def log2(x):
    """Base-2 logarithm.

    Numeric: preserves sf.  Symbolic: returns ``sympy.log(x, 2)``.
    """
    if _is_symbolic(x):
        import sympy
        return sympy.log(x, 2)
    raw, sf = _peel(x)
    return Sig(math.log2(float(raw)), sf)


def ln(x):
    """Natural logarithm.

    Numeric: preserves sf.  Symbolic: returns ``sympy.log(x)``.
    """
    if _is_symbolic(x):
        import sympy
        return sympy.log(x)
    raw, sf = _peel(x)
    return Sig(math.log(float(raw)), sf)


def floor(x):
    """Floor (largest integer ≤ x).

    Numeric: preserves sf.  Symbolic: returns ``sympy.floor(x)``.
    """
    if _is_symbolic(x):
        import sympy
        return sympy.floor(x)
    raw, sf = _peel(x)
    return Sig(math.floor(raw), sf)


def ceil(x):
    """Ceiling (smallest integer ≥ x).

    Numeric: preserves sf.  Symbolic: returns ``sympy.ceiling(x)``.

    Note the sympy spelling: ``sympy.ceiling`` not ``sympy.ceil``.
    Our DSL name ``ceil`` is consistent with ``math.ceil``; the
    underlying sympy call is ``ceiling``.
    """
    if _is_symbolic(x):
        import sympy
        return sympy.ceiling(x)
    raw, sf = _peel(x)
    return Sig(math.ceil(raw), sf)


def phasor(magnitude, angle_rad):
    """Polar/phasor: magnitude · e^(j·angle).

    Returns a complex Sig. ``magnitude`` and ``angle_rad`` must be real
    numerics (int, float, or Sig wrapping one); they may NOT carry
    forallpeople units, because forallpeople's Physical can't multiply
    with complex.  Apply units only after collapsing to a real magnitude
    via ``abs()``.

    Used by the ``∠`` operator: ``5 ∠ 30°`` becomes ``phasor(5, 30°)`` →
    after the ``°`` rewrite → ``phasor(5, (30*π/180))``.

    For symbolic angles (e.g. ``π/6``), the rotor is computed via
    ``sympy.exp(I*angle)`` rather than ``complex(cos, sin)``.  Sympy
    knows the exact values for clean fractions of π — ``exp(I*pi/2)``
    is ``I`` exactly, ``exp(I*pi)`` is ``-1`` exactly — so the numeric
    conversion at the end gives ``(5+0j)`` for ``5 ∠ 90°`` instead of
    ``(3e-16+5j)``.  For arbitrary numeric angles, ``math.cos``/
    ``math.sin`` are used directly.
    """
    raw_mag, sf_mag = _peel(magnitude)
    raw_ang, sf_ang = _peel(angle_rad)
    if hasattr(raw_mag, "_value") and hasattr(raw_mag, "_dimensions"):
        raise TypeError(
            "phasor() cannot accept a Physical/forallpeople magnitude. "
            "Compute the phasor with unitless numerics, then attach a "
            "unit after abs(): V_out := abs(V_complex) * V"
        )
    if hasattr(raw_ang, "_value") and hasattr(raw_ang, "_dimensions"):
        raise TypeError("phasor() angle must be in unitless radians.")
    sf = min(sf_mag, sf_ang)

    # Symbolic angle path: use sympy.exp(I*angle) so that clean fractions
    # of pi simplify to exact values before we collapse to complex.
    try:
        import sympy as _sym
        if isinstance(raw_ang, _sym.Basic):
            rotor_sym = _sym.exp(_sym.I * raw_ang)
            # Convert to a Python complex.  ``complex(...)`` works on
            # sympy expressions whose value is a finite complex number.
            rotor = complex(rotor_sym)
            return Sig(float(raw_mag) * rotor, sf)
    except (ImportError, TypeError, ValueError):
        # Fall through to numeric path on any conversion failure.
        pass

    # Numeric angle path: standard floating-point trig.
    a = float(raw_ang)
    rotor = complex(math.cos(a), math.sin(a))
    return Sig(float(raw_mag) * rotor, sf)


def to_dB_v(x):
    """Voltage/amplitude ratio → decibels: 20·log₁₀(x).

    For amplitudes, fields, voltages, currents — anything that scales
    quadratically with power. Use ``to_dB_p`` for power ratios.
    """
    raw, sf = _peel(x)
    val = abs(raw) if isinstance(raw, complex) else float(raw)
    return Sig(20 * math.log10(val), sf)


def to_dB_p(x):
    """Power ratio → decibels: 10·log₁₀(x)."""
    raw, sf = _peel(x)
    return Sig(10 * math.log10(float(raw)), sf)


def from_dB_v(x):
    """Decibels → amplitude ratio: 10^(x/20)."""
    raw, sf = _peel(x)
    return Sig(10 ** (float(raw) / 20), sf)


def from_dB_p(x):
    """Decibels → power ratio: 10^(x/10)."""
    raw, sf = _peel(x)
    return Sig(10 ** (float(raw) / 10), sf)


def approx(a, b, rtol=1e-9, atol=1e-12):
    """Approximately equal: |a − b| ≤ max(rtol·max(|a|,|b|), atol).

    Used by the ``≈`` operator: ``a ≈ b`` becomes ``approx(a, b)``.
    Default tolerances are tight (1 ppb relative) — pass keyword args
    explicitly if you want a looser comparison: ``approx(a, b, rtol=1e-3)``.
    """
    ra, _ = _peel(a)
    rb, _ = _peel(b)
    try:
        diff = abs(ra - rb)
        scale = max(abs(ra), abs(rb))
    except TypeError:
        return ra == rb
    return diff <= max(rtol * scale, atol)



# ---------- source normalization ----------

def rewrite_set_membership_swap(source: str) -> str:
    """
    Rewrites the "contains" set-membership operators ``∋`` and ``∌`` to
    Python's ``in`` / ``not in`` with operands swapped.

    Examples::

        A ∋ x        →  x in A          (read: A contains x)
        S ∌ y        →  y not in S      (read: S does not contain y)
        my_set ∋ k   →  k in my_set
        {1, 2, 3} ∋ x →  x in {1, 2, 3}

    Mathematics writes ``A ∋ x`` to mean "A has x as a member", with
    the *container* on the left.  Python writes ``x in A`` with the
    container on the right.  This rewriter swaps the operands when
    translating to bridge the convention difference.

    Implementation note: the simpler glyphs (``∈``, ``∉``, etc.) are
    handled by ``normalize_source``'s straight character-substitution
    table because they don't change operand order.  ``∋`` and ``∌`` are
    here because they do.

    The LHS and RHS each use the standard ``_BINOP_RHS`` pattern, which
    accepts a function call, an indexed access, a parenthesised group,
    or a bare identifier/number.  For more elaborate expressions on
    either side, parenthesise them explicitly:

        (S ∪ T) ∋ x   →  x in (S | T)
    """
    swap_in = re.compile(
        rf'({_BINOP_RHS})\s*∋\s*({_BINOP_RHS})'
    )
    swap_not_in = re.compile(
        rf'({_BINOP_RHS})\s*∌\s*({_BINOP_RHS})'
    )

    def _emit_in(m):
        return f'{m.group(2)} in {m.group(1)}'

    def _emit_not_in(m):
        return f'{m.group(2)} not in {m.group(1)}'

    # Run each substitution in a fix-point loop so chained-but-uncommon
    # forms like ``A ∋ x ∋ y`` get rewritten left-to-right.  In practice
    # this loop terminates in one or two iterations.
    previous = None
    while source != previous:
        previous = source
        source = swap_in.sub(_emit_in, source)
        source = swap_not_in.sub(_emit_not_in, source)
    return source


def _rewrite_absolute_temperatures(source: str) -> str:
    """Rewrite absolute-temperature literals into offset-applying calls.

        22 °C      ->  from_degC(22)
        -5 ℃       ->  from_degC(-5)
        72.5 °F    ->  from_degF(72.5)
        491.67 °R  ->  from_degR(491.67)
        (x + 5) °C ->  from_degC((x + 5))

    A ``°C`` literal denotes an ABSOLUTE temperature — a point on the
    Celsius scale — so the 273.15 K offset is part of its meaning.  A
    plain string substitution (the old ``"°C": " degC"``) cannot apply
    an offset; it can only rename.  This pass captures the value sitting
    in front of the glyph and wraps the whole thing in the matching
    ``from_deg*`` constructor (defined in ``extra_units``), which
    returns a true Kelvin ``Physical``.

    Captured operand forms:

    * a numeric literal — optional sign, digits, optional decimal,
      optional exponent: ``22``, ``-5``, ``3.7``, ``1.2e3``;
    * a parenthesised expression — ``(x + 5)``, ``(2*a - 1)`` — matched
      with depth counting so nested parens are handled.

    The DELTA forms ``ΔC`` / ``ΔF`` / ``deltaC`` / ``deltaF`` are NOT
    touched here — they are differences, not points, carry no offset,
    and are handled by the plain substitution table as before.  This
    pass only ever consumes the degree-glyph spellings ``°C`` ``℃``
    ``°F`` ``℉`` ``°R``.

    Runs before the substitution table and before the postfix-° angle
    pass, so afterwards the only ``°`` left in the source is the bare
    angle operator.
    """
    # Glyph spellings → constructor name.  ``℃`` (U+2103) and ``℉``
    # (U+2109) are the precomposed single-character forms; ``°C`` etc.
    # are the two-character degree-sign + letter forms.
    _TEMP_GLYPHS = [
        ("°C", "from_degC"), ("℃", "from_degC"),
        ("°F", "from_degF"), ("℉", "from_degF"),
        ("°R", "from_degR"),
    ]

    # Numeric-literal pattern for the operand immediately left of the
    # glyph: optional sign, digits with optional decimal/exponent.  A
    # bare leading ``.5`` form is allowed too — but NOT when the dot is
    # the second half of a range ``..``: in ``[0 °C..100 °C]`` the second
    # operand is ``100``, not ``.100`` (that mis-capture used to leave
    # ``from_degC(0).from_degC(.100)`` behind, which then parsed as an
    # attribute access on the first temperature).
    _num = re.compile(
        r'([+-]?(?:\d+\.?\d*|(?<!\.)\.\d+)(?:[eE][+-]?\d+)?)\s*$'
    )

    def _find_paren_start(s, close_idx):
        """Given the index of a ``)``, return the index of its matching
        ``(`` — or ``None`` if unbalanced."""
        depth = 0
        i = close_idx
        while i >= 0:
            c = s[i]
            if c == ')':
                depth += 1
            elif c == '(':
                depth -= 1
                if depth == 0:
                    return i
            i -= 1
        return None

    def _find_bracket_start(s, close_idx):
        """Given the index of a ``]``, return the index of its matching
        ``[`` — or ``None`` if unbalanced.  Used so ``[..] °C`` (a range
        or list of absolute temperatures) wraps the whole bracketed
        expression in the absolute constructor."""
        depth = 0
        i = close_idx
        while i >= 0:
            c = s[i]
            if c == ']':
                depth += 1
            elif c == '[':
                depth -= 1
                if depth == 0:
                    return i
            i -= 1
        return None

    # Glyph → delta-unit name, for the case where the glyph is used as
    # a UNIT rather than an absolute literal (e.g. ``ppm/℃`` — a
    # temperature coefficient).  A unit in a denominator or as a
    # multiplicand cannot carry an offset; it denotes a per-degree or
    # per-difference scale, which IS the delta unit.
    _DELTA_NAME = {
        "°C": "deltaC", "℃": "deltaC",
        "°F": "deltaF", "℉": "deltaF",
        "°R": "deltaF",   # Rankine degree == Fahrenheit degree in size
    }

    # Process each glyph spelling.  We scan left-to-right, and for every
    # occurrence capture the operand that immediately precedes it.
    for glyph, ctor in _TEMP_GLYPHS:
        result = []
        pos = 0
        while True:
            j = source.find(glyph, pos)
            if j == -1:
                result.append(source[pos:])
                break
            before = source[pos:j]
            # The operand to capture sits at the end of ``before`` — an
            # operand never spans a previous glyph rewrite, so working
            # on ``before`` alone is sufficient.
            stripped = before.rstrip()  # trailing whitespace is dropped

            # --- Unit case: the glyph is used as a UNIT, not a literal.
            # When the glyph immediately follows ``/``, ``*`` or ``·``
            # (a multiplicative operator), it is a unit in an
            # expression like ``100 ppm/℃`` — a temperature
            # coefficient.  A unit cannot carry the absolute-scale
            # offset, so it resolves to the toolkit's DELTA unit
            # (``deltaC`` etc.), with a leading space so the
            # unit-binding pass tokenises it cleanly.  This must be
            # checked BEFORE the operand-capture cases below, because
            # those would otherwise mishandle (or skip) it.
            if stripped and stripped[-1] in "/*·×":
                result.append(before)
                result.append(" " + _DELTA_NAME[glyph])
                pos = j + len(glyph)
                continue

            if stripped.endswith(')'):
                # Parenthesised operand — match its opening paren.
                start = _find_paren_start(stripped, len(stripped) - 1)
                if start is not None:
                    expr = stripped[start:]
                    head = stripped[:start]
                    result.append(head)
                    result.append(f"{ctor}({expr})")
                    pos = j + len(glyph)
                    continue
                # Unbalanced parens — leave the text untouched.
                result.append(before)
                result.append(glyph)
                pos = j + len(glyph)
                continue

            if stripped.endswith(']'):
                # List / range operand — ``[-10.0 .. 100.0] °C`` is a range
                # of ABSOLUTE temperatures, so apply the absolute
                # constructor to the whole bracketed expression:
                # ``from_degC([-10.0 .. 100.0])``.  (Without this it would
                # fall through to the bare-unit case and become
                # ``[...] * deltaC`` — a range of DIFFERENCES, wrong.)
                start = _find_bracket_start(stripped, len(stripped) - 1)
                if start is not None:
                    expr = stripped[start:]
                    head = stripped[:start]
                    result.append(head)
                    result.append(f"{ctor}({expr})")
                    pos = j + len(glyph)
                    continue
                # Unbalanced brackets — leave untouched.
                result.append(before)
                result.append(glyph)
                pos = j + len(glyph)
                continue

            m = _num.search(stripped)
            if m:
                num = m.group(1)
                head = stripped[:m.start()]
                result.append(head)
                result.append(f"{ctor}({num})")
                pos = j + len(glyph)
                continue

            # No number and no paren before the glyph, and it is not
            # after a ``/``/``*``.  This is the glyph used as a bare
            # unit token — after a unit identifier (``ppm ℃``), at the
            # start of an expression, or similar.  Treat it as the
            # delta unit: a bare ``℃`` with no value is never an
            # absolute literal (those always have a number in front).
            result.append(before)
            result.append(" " + _DELTA_NAME[glyph])
            pos = j + len(glyph)

        source = "".join(result)

    return source


def normalize_source(source: str) -> str:
    # Absolute-temperature literals (``22 °C``, ``72 °F``, ``491 °R``)
    # are rewritten value-and-all into ``from_degC(22)`` etc. by
    # ``_rewrite_absolute_temperatures`` below — a plain string
    # substitution cannot do this because it must capture the numeric
    # value preceding the glyph and apply the scale's offset.  This pass
    # runs FIRST, before the substitution table and before the
    # postfix-° angle pass, so that ``°C``/``°F``/``°R`` are gone by the
    # time those run and only the bare angle ``°`` remains.
    source = _rewrite_absolute_temperatures(source)

    replacements = {
        # Δ-temperature markers — written by users to flag that a
        # temperature is a DIFFERENCE, not an absolute reading.  These
        # genuinely are plain substitutions (no offset, no value
        # capture): ``ΔC`` is just a unit name.  They are deliberately
        # left here, distinct from the absolute ``°C``/``°F``/``°R``
        # forms which ``_rewrite_absolute_temperatures`` handled above.
        # Map to the toolkit's identifier-friendly names so the
        # unit-binding pass recognizes them.  Leading space so the
        # value-unit-binding pass sees ``25 deltaC`` not ``25deltaC``.
        "ΔK": " deltaK",
        "ΔC": " deltaC",
        "ΔF": " deltaF",
        # Astronomy / astrophysics — journal-style glyphs.  ``☉`` (U+2609
        # SUN), ``⊕`` (U+2295 CIRCLED PLUS, the Earth symbol in
        # astronomy), and ``♃`` (U+2643 JUPITER) are the conventional
        # symbols for the Sun, Earth, and Jupiter in scientific papers.
        # They're in Unicode category So (Symbol, Other) and Python's
        # identifier rules reject them, so we rewrite each composite
        # ``X☉``/``X⊕``/``X♃`` to its underscore form here.  This means
        # users can write ``2 · M☉ + R⊕²`` in source and get the same
        # behaviour as ``2 · M_sun + R_earth**2``.
        #
        # Ordering matters: do the longer keys first to avoid partial
        # matches.  ``M⊙`` (U+2299 CIRCLED DOT) is also accepted as a
        # variant of M☉ — some papers use it interchangeably.
        "M☉": "M_sun",
        "R☉": "R_sun",
        "L☉": "L_sun",
        "T☉": "T_sun",
        "M⊙": "M_sun",   # circled-dot variant
        "R⊙": "R_sun",
        "L⊙": "L_sun",
        "M⊕": "M_earth",
        "R⊕": "R_earth",
        "M♃": "M_jupiter",
        "R♃": "R_jupiter",
        "·": "*",
        "⋅": "*",
        "×": "*",
        "↑": "**",            # Knuth-style power arrow — math-textbook
                              # notation for exponentiation.  ``2 ↑ 10``
                              # becomes ``2 ** 10``.  We deliberately do
                              # NOT rewrite ``^`` to ``**`` because ``^``
                              # is Python's XOR operator and silently
                              # changing its meaning would break bit-
                              # twiddling code; ``↑`` is unambiguous.
        "−": "-",
        "÷": "/",
        "≠": "!=",
        "≤": "<=",
        "≥": ">=",
        "‖": "||",
        "∞": "inf",
        # Alternative glyph for the Mathcad-style target-unit operator.
        # ``▶`` (U+25B6 BLACK RIGHT-POINTING TRIANGLE) is the canonical
        # form the rest of the pipeline expects; ``▸`` (U+25B8 BLACK
        # RIGHT-POINTING SMALL TRIANGLE) is a smaller, less assertive
        # alternative some users prefer typographically.  They have the
        # exact same DSL semantics — normalising to the canonical form
        # here means only ``rewrite_target_unit`` needs to know about
        # one glyph, not two.
        "▸": "▶",
        # Alternative glyphs for the sum/product functions.  ``Σ`` and
        # ``Π`` are Greek letters that Python accepts as identifier
        # characters; ``∑`` (U+2211 N-ARY SUMMATION) and ``∏`` (U+220F
        # N-ARY PRODUCT) are dedicated math symbols in Unicode category
        # Sm, which Python's parser does NOT accept for identifiers.
        # We can't make them aliases at the Python level (``∑ = Σ``
        # fails to parse), so we rewrite at the source level: every
        # ``∑`` and ``∏`` in user code becomes ``Σ`` / ``Π`` before
        # Python sees it.  Visual choice in the editor, single
        # identity at runtime.
        "∑": "Σ",
        "∏": "Π",
        "½": "(1/2)",
        "⅓": "(1/3)",
        "¼": "(1/4)",
        "¾": "(3/4)",
        "⅔": "(2/3)",
        "⅕": "(1/5)",
        "⅖": "(2/5)",
        "⅗": "(3/5)",
        "⅘": "(4/5)",
        "⅙": "(1/6)",
        "⅚": "(5/6)",
        "⅛": "(1/8)",
        "⅜": "(3/8)",
        "⅝": "(5/8)",
        "⅞": "(7/8)",
        # ---------- Set-theory glyphs ----------
        # Direct character substitutions.  The ``∋`` and ``∌`` (membership
        # with swapped operands) are NOT here because they need operand
        # capture — see ``rewrite_set_membership_swap`` which runs ahead
        # of this pass.
        "∅": "set()",       # empty set — note: Python's ``{}`` is an empty
                             # *dict*, not an empty set; sets need set()
        "∈": " in ",         # element-of → in
        "∉": " not in ",     # not element-of → not in
        "∩": "&",            # intersection (Python set ``&`` operator)
        "∪": "|",            # union (Python set ``|`` operator)
        "∖": "-",            # set difference (Python set ``-`` operator).
                             # Use the math glyph U+2216 SET MINUS, NOT
                             # the backslash — backslash is Python's
                             # line-continuation character and replacing
                             # it would break multi-line statements.
        "△": "^",            # symmetric difference (Python set ``^``)
        "⊕": "^",            # alternate symbol some texts use for sym diff
        # Subset / superset.  Python's set type already defines <, <=, >,
        # >= as proper/non-proper subset/superset comparisons, so these
        # translate to ordinary operators rather than method calls — same
        # semantics, more natural reading.
        "⊆": "<=",           # subset (or equal)
        "⊇": ">=",           # superset (or equal)
        "⊂": "<",            # proper subset (strict)
        "⊃": ">",            # proper superset (strict)
        # NB: The blanket ``)(`` → ``)*(`` substitution that used to live
        # here was overzealous — it correctly handled math like
        # ``(a+b)(c-d)`` (multiplication) but also clobbered chained
        # function calls like ``f(x)(y)`` (currying) and was visible in
        # any DSL code that used the ``→`` lambda syntax.  The
        # ``)``-then-``(`` adjacency is now handled in the implicit-mul
        # pass, which has the token-level context to distinguish "this
        # ``)`` closed a grouping paren" (insert ``*``) from "this ``)``
        # closed a function-call paren" (don't).
    }

    # A middle dot BETWEEN TWO KNOWN UNIT NAMES is juxtaposition, not a
    # bare product: ``1 N·m`` should read exactly like ``1 N m`` and
    # display ``1 N·m``.  Translating the dot to ``*`` first (the table
    # below) hides the right-hand unit from the tight-binding pass, so
    # only the left one is tagged and the value renders in whatever
    # forallpeople picks (``1 N·m`` → ``1 J``, ``0.1 kp·m`` → ``1000
    # mJ``).  Turn ``unit·unit`` into ``unit unit`` before the table
    # runs; the loop handles chains (``kg·m·s``).  Variables are never
    # touched — both sides must be names from ``_UNIT_NAMES_FOR_BINDING``.
    _unit_dot = re.compile(
        rf'(?<![A-Za-z0-9_])({_UNIT_NAME_ALT})\s*[·⋅]\s*'
        rf'({_UNIT_NAME_ALT})(?![A-Za-z0-9_])'
    )
    previous = None
    while source != previous:
        previous = source
        source = _unit_dot.sub(r'\1 \2', source)

    for old, new in replacements.items():
        source = source.replace(old, new)

    # Make π separable when glued to adjacent math text,
    # but do not break a standalone line like: π = pi
    source = re.sub(r'(?<=[\w\)])π(?=[\w\(])', ' π ', source)
    source = re.sub(r'(?<=[\w\)])π', ' π', source)
    source = re.sub(r'π(?=[\w\(])', 'π ', source)

    return source


# ---------- postfix percent rewrite ----------

def rewrite_postfix_percent(source: str) -> str:
    """
    Rewrites:
        25%      -> percent(25)
        x%       -> percent(x)
        (a+b)%   -> percent((a+b))
        (1+(3+2))%        -> percent((1+(3+2)))     (nested parens)
        ((1+(2+3)))%      -> percent(((1+(2+3))))   (deeper nesting)

    Does NOT rewrite:
        x % y    (binary modulo: ``%`` followed by an identifier or number
                  is treated as Python's modulo operator and left alone)
    """

    # Paren-form pass with bracket-balanced operand matching for any
    # depth of nesting.  Wrapped in a fixpoint loop so that nested
    # cases like ``((a+b)%)%`` are resolved inside-out across passes.
    def _find_percent(s, pos):
        i = pos
        while True:
            i = s.find('%', i)
            if i == -1:
                return None
            # Binary-modulo guard: ``%`` followed by an identifier, digit,
            # ``(`` or one of the unit-prefix glyphs is Python's modulo,
            # not postfix percent.  Skip and look for the next ``%``.
            j = i + 1
            while j < len(s) and s[j] in ' \t':
                j += 1
            if j < len(s) and (s[j].isalnum() or s[j] == '_'
                               or s[j] == '(' or s[j] in 'πμΩ'):
                i += 1
                continue
            return (i, i + 1, '%')

    def _wrap(operand_with_parens, _op):
        return f'percent({operand_with_parens})'

    previous = None
    while source != previous:
        previous = source
        source = _postfix_paren_pass(source, find_op=_find_percent,
                                     callback=_wrap)

    # Numeric-literal-then-percent rule.  This runs BEFORE the general
    # bare-operand pass below and resolves the ambiguous case the
    # general pass cannot: a numeric literal followed by ``%`` and then
    # an *identifier* or ``(``.
    #
    #   21 % p        — "21 percent of p"  -> percent(21) p   (× via the
    #                   implicit-multiplication pass)
    #   21 % (a + b)  — likewise           -> percent(21) (a+b)
    #
    # Postfix-percent binds to the NUMBER on its left; whatever follows
    # is a separate factor.  ``<number> %`` is overwhelmingly percent in
    # an engineering DSL — genuine "modulo a literal by a variable" is
    # rare, and anyone who wants it has the explicit ``mod(a, b)``
    # function.  So a numeric literal directly before ``%`` is treated
    # as percent whenever a non-numeric operand follows.
    #
    # The ONE case kept as modulo is ``<number> % <number>`` — both
    # operands bare numbers (``50 % 7``) is unambiguous modulo and
    # common, so the lookahead below still excludes a following digit.
    # (``<identifier> % …`` is left entirely to Python's modulo; this
    # rule only fires when the left operand is a numeric literal.)
    #
    # Two sub-cases, because the following operand needs different
    # handling:
    #   * followed by ``(``  — emit ``percent(N) * `` with an EXPLICIT
    #     ``*``.  The implicit-multiplication pass treats ``)(`` after a
    #     *call* as currying, not multiplication, so it would leave
    #     ``percent(21)(a+b)`` — a call of the Sig — and crash.  We know
    #     it is multiplication, so we insert the ``*`` here.
    #   * followed by an identifier — emit plain ``percent(N) name``;
    #     the implicit-mul pass correctly inserts the ``*`` for
    #     ``identifier``-after-``)``.
    _NUM_ATOM = (
        r"0[xX][0-9a-fA-F]+|0[oO][0-7]+|0[bB][01]+"
        r"|\d+\.\d+|\d+\.(?![A-Za-z_])|\d+"
    )
    # number % (  ->  percent(number) * (
    source = re.sub(
        rf'(?<![%\w)])({_NUM_ATOM})\s*%(?=[ \t]*\()',
        r'percent(\1) * ',
        source
    )
    # number % identifier  ->  percent(number) identifier
    source = re.sub(
        rf'(?<![%\w)])({_NUM_ATOM})\s*%(?=[ \t]*[A-Za-z_πμΩ])',
        r'percent(\1) ',
        source
    )

    # Bare-operand pass (numbers and identifiers) — unchanged.  The
    # existing lookbehind ``(?<![%\w)])`` excludes operands that begin
    # immediately after a ``)`` (already handled by the paren-form pass
    # above), a ``%`` (avoid double-rewriting in degenerate cases like
    # ``x%%``), or another word char (avoid splitting ``f%`` weirdly).
    #
    # The negative lookahead ``(?!\s*[A-Za-z_πμΩ\(0-9])`` distinguishes
    # postfix percent (``25%`` end-of-statement) from binary modulo
    # (``25 % 4``).  We treat newlines as statement boundaries so that
    # ``25%`` at end of line is recognised — ``\s*`` was matching the
    # newline and then "seeing" the next statement's first identifier,
    # which incorrectly looked like modulo.  Now we require the lookahead
    # whitespace to NOT include a newline.
    source = re.sub(
        rf'(?<![%\w)])({_BINOP_ATOM})\s*%(?![ \t]*[A-Za-z_πμΩ\(0-9])',
        r'percent(\1)',
        source
    )

    return source


def rewrite_postfix_permille(source: str) -> str:
    """
    Rewrites:
        5‰       -> permille(5)
        x‰       -> permille(x)
        (a+b)‰   -> permille((a+b))
        (1+(3+2))‰   -> permille((1+(3+2)))   (nested parens)

    Does NOT try to behave like a binary operator.
    """

    def _find_permille(s, pos):
        i = s.find('‰', pos)
        if i == -1:
            return None
        return (i, i + 1, '‰')

    def _wrap(operand_with_parens, _op):
        return f'permille({operand_with_parens})'

    previous = None
    while source != previous:
        previous = source
        source = _postfix_paren_pass(source, find_op=_find_permille,
                                     callback=_wrap)

    # Bare-operand pass — unchanged.
    source = re.sub(
        rf'({_BINOP_ATOM})\s*‰',
        r'permille(\1)',
        source
    )

    return source


# ---------- postfix factorial rewrite ----------

def rewrite_postfix_factorial(source: str) -> str:
    """
    Rewrites:
        5!           -> fact(5)
        x!           -> fact(x)
        (a+b)!       -> fact((a+b))
        (1+(1+(1)))! -> fact((1+(1+(1))))   (arbitrary nesting depth)
        ((3)!)!      -> fact(fact((3)))     (chained / nested parens)
        f(x)!        -> fact(f(x))          (function-call operand)
        3!!          -> fact(fact(3))       (right-to-left chain)

    Does NOT rewrite:
        x != y       (inequality stays as Python's ``!=``)

    Implementation: paren-form operands are handled by ``_postfix_paren_pass``
    using ``_find_balanced_paren``, which scans bracket-balanced and so works
    at arbitrary nesting depth.  Bare-operand cases (``5!``, ``x!``, ``f(x)!``
    where the helper has already extended the operand to the whole call) are
    covered by a regex fallthrough using ``_BINOP_RHS`` for the few remaining
    shapes that the paren pass doesn't own (i.e. operands that don't end in
    a ``)``).
    """
    # Paren-form pass for ``)<whitespace>!`` where the parens may nest
    # arbitrarily deep.  Works for ``(1+(1+(1)))!`` and ``f(x)!`` alike,
    # because _postfix_paren_pass extends the operand back across any
    # preceding identifier.

    # Paren-form and bare-operand passes share a single fixpoint loop.
    # The chained case ``3!!`` needs both passes to converge together:
    # the bare-operand pass turns ``3!`` into ``fact(3)``, exposing
    # ``fact(3)!`` — which is now a function-call form the paren pass
    # can pick up.  Running the two passes in separate loops would stop
    # after the first transformation and leave the outer ``!`` behind.

    def _find_factorial(s, pos):
        # Find the next ``!`` at or after ``pos`` that is a factorial
        # operator, not part of ``!=``.  The ``(?<![<>=!])`` lookbehind
        # of the original regex is enforced inline here.
        i = pos
        while True:
            i = s.find('!', i)
            if i == -1:
                return None
            # Skip ``!=``: the next char is ``=``.
            if i + 1 < len(s) and s[i + 1] == '=':
                i += 2
                continue
            # Skip ``<!``, ``>!``, ``=!``: previous char is a comparison
            # tail.  These shouldn't appear in well-formed Python but
            # the original regex guards against them, so we keep parity.
            if i > 0 and s[i - 1] in '<>=':
                i += 1
                continue
            return (i, i + 1, '!')

    def _wrap(operand, _op):
        return f'fact({operand})'

    # Bare-operand fallthrough for operands that don't end in ``)`` —
    # bare numbers and identifiers.  IMPORTANT: ``_BINOP_ATOM`` is an
    # unparenthesised alternation (``A|B|C``), so it MUST be wrapped in
    # ``(?:...)`` here — without the wrapper, ``{_BINOP_ATOM}\s*!``
    # parses as ``A | B | C\s*!`` and the first alternative (identifier)
    # matches without requiring the ``!`` at all, causing the fixpoint
    # loop to rewrite ``fact`` → ``fact(fac)`` ad infinitum.
    pattern = rf'(?<![<>=!])(?:{_BINOP_ATOM})\s*!(?!\s*=)'

    def replace(m):
        text = m.group(0)
        operand = text[:-1].rstrip()
        return f'fact({operand})'

    previous = None
    while source != previous:
        previous = source
        source = _postfix_paren_pass(source, find_op=_find_factorial,
                                     callback=_wrap)
        source = re.sub(pattern, replace, source)

    return source


# ---------- parallel operator rewrite ----------

# A "side" of a binary operator: either a function call ``name(...)``,
# a parenthesised group ``(...)``, an identifier (with optional subscript
# digits), or a numeric literal.  Together these cover everything the
# binary-operator rewriters (‖, ±, ≈, ∠) want to accept as a complete
# operand.  We keep the union explicit (rather than collapsing into a
# single mega-pattern) because regex alternation is order-sensitive: the
# function-call alternative must come first, so ``f(x)`` is captured as
# a whole rather than just ``f``.
_BINOP_RHS_BARE = (
    # Function call ``name(args)`` — the args may themselves contain one
    # layer of balanced parens (e.g. ``parallel((a+b), c)``).  Two layers
    # of nesting are rare in DSL one-liners; if you need them, parenthesise
    # the whole call: ``(f(g(x))) ‖ y``.
    rf'(?:[A-Za-z_][A-Za-z0-9_]*\((?:[^()\n]|\([^()\n]*\))*\)'
    # Subscripted/indexed access ``name[...]`` — most often produced by
    # the subscript-numerals pass turning ``R₂`` into ``R[2]``.
    rf'|[A-Za-z_][A-Za-z0-9_]*(?:\[[^\[\]\n]*\])+'
    # Parenthesised group — also one layer of nested parens, so that
    # ``((R₃) ‖ R₄)`` registers as a complete RHS rather than two
    # separate things.
    rf'|\((?:[^()\n]|\([^()\n]*\))+\)'
    rf'|{_BINOP_ATOM})'                          # bare ident-or-number
)
_BINOP_RHS = (
    # Same as above, but optionally followed by a unit name so that
    # ``12 Ω``, ``5 kN``, ``42.195 km`` register as a SINGLE operand
    # rather than splitting at the space.  Without this, rewriters
    # like ``rewrite_parallel`` and ``rewrite_plusminus`` would treat
    # ``12 Ω ‖ 13 Ω`` as ``parallel(12, Ω)`` followed by leftover
    # ``‖ 13 Ω``, which is both wrong and a parse error.
    #
    # The unit suffix is a fixed alternation built from
    # ``_UNIT_NAMES_FOR_BINDING``; we don't accept arbitrary trailing
    # identifiers because that would silently consume the next variable
    # in expressions like ``a ± b c`` (where ``a ± b`` is intended).
    #
    # The whitespace before the unit is OPTIONAL: ``1.0V`` is as legal
    # as ``1.0 V`` in the DSL (the token pass binds a glued unit to its
    # number just the same), so the operand pattern must see ``1.0V``
    # as one thing.  Otherwise ``1.0V .. 20.0V`` split into ``1.0`` and
    # ``V .. 20.0`` and died in the tokenizer, and ``1.0V ± 0.1V``
    # silently became ``1.0 · (V ± 0.1) · V`` — a V² interval.  There is
    # no ambiguity risk: an identifier operand is consumed greedily by
    # ``_BINOP_ATOM``, so a unit can only glue onto a number, ``)``
    # or ``]`` — juxtaposition, which in the DSL means multiplication.
    #
    # A trailing-dot decimal glued to a unit — ``110.V`` — is the one
    # number shape ``_BINOP_ATOM`` cannot supply: its ``\d+\.`` form
    # refuses a following letter (so ``x = 3.real`` style attribute
    # access is never mistaken for a number).  Python itself tokenises
    # ``110.V`` as the float ``110.`` and the name ``V``, and the token
    # pass binds them, so the operand must too — or ``110.V..200V``
    # matched as ``110`` followed by ``V..200V``, and evaluated to
    # ``110 · [1 V .. 200 V]``.  Tried first so the plain-atom branch
    # can't settle for the bare integer.
    rf'(?:\d+\.(?=[A-Za-z_])\s*{_UNIT_NAME_ALT}(?![A-Za-z0-9_])'
    rf'|{_BINOP_RHS_BARE}(?:\s*{_UNIT_NAME_ALT}(?![A-Za-z0-9_]))?)'
)


def _find_balanced_paren(source: str, pos: int, *, direction: int = +1) -> int:
    """
    Find the matching paren for the one at ``source[pos]``.

    With ``direction=+1`` (default), ``source[pos]`` must be ``(`` and
    we scan forward for the matching ``)``.  With ``direction=-1``,
    ``source[pos]`` must be ``)`` and we scan backward for the matching
    ``(``.  Returns the index of the matching paren, or ``-1`` on failure.

    Why this exists: regex character classes like ``[^()]`` only match
    characters that aren't parens, so a pattern like ``\\(([^()]+)\\)``
    matches ``(a+b)`` but not ``(a+(b+c))`` — the inner pair makes the
    body cease to be ``[^()]+``.  We could write a one-level-of-nesting
    version like ``\\(([^()]|\\([^()]*\\))*\\)`` — that's what
    ``_BINOP_RHS`` does — but it still breaks at three levels.  For a
    regular language nothing finite suffices; balanced parens aren't a
    regular property.  Hence this scanner.

    Returns ``-1`` if:

      - ``pos`` is out of bounds
      - the char at ``pos`` isn't the expected paren for the direction
      - the parens don't balance before EOL or EOS
      - a newline is encountered (multi-line operands aren't supported —
        the rewriters are line-level by convention)

    Strings and comments don't need special handling: ``_protect_strings``
    upstream replaces them with ``__dsl_str_N__``-style placeholders that
    contain no parens.
    """
    if not (0 <= pos < len(source)):
        return -1
    expected = '(' if direction == +1 else ')'
    if source[pos] != expected:
        return -1

    open_ch = '(' if direction == +1 else ')'
    close_ch = ')' if direction == +1 else '('
    depth = 1
    i = pos + direction
    while 0 <= i < len(source):
        ch = source[i]
        if ch == '\n':
            return -1
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += direction
    return -1


def _postfix_paren_pass(source: str, *, find_op, callback) -> str:
    """
    Generic single-pass rewriter for postfix-with-paren-operand forms.

    Used by the postfix superscript / percent / permille rewriters to
    handle arbitrarily-nested operands.  For each operator occurrence
    found by ``find_op``, this function:

      1. Scans backwards past any whitespace to look for ``)``.
      2. Calls ``_find_balanced_paren`` to locate the matching ``(``.
      3. Calls ``callback(operand_with_parens, op_text)`` to compute
         the replacement, and substitutes it for the
         ``(operand)`` + whitespace + ``op`` span.

    Cases where the operator isn't preceded by ``)`` (bare-operand forms
    like ``5%`` or ``x²``) are skipped here and handled by the rewriter's
    existing regex-based bare-operand pass — we only own the paren forms.

    ``find_op(source, pos)`` returns ``(start, end, op_text)`` for the
    next operator at or after ``pos``, or ``None`` if none is found.
    Operator-specific guards (e.g. percent's binary-modulo lookahead)
    live inside ``find_op``.

    To handle nested cases like ``((a+b)%)%``, run this function in a
    fixpoint loop on the calling side: each pass replaces the innermost
    matches, then the next pass picks up the outer ones whose operands
    are now syntactically simpler.
    """
    out = []
    pos = 0
    while pos < len(source):
        match = find_op(source, pos)
        if match is None:
            out.append(source[pos:])
            break
        op_start, op_end, op_text = match
        # Walk back from op_start past whitespace, looking for ``)``.
        k = op_start - 1
        while k >= 0 and source[k] in ' \t':
            k -= 1
        if k < 0 or source[k] != ')':
            # Not a paren-form — bare-operand pass will handle this if
            # the operator's regex lets it.  Emit unchanged and skip.
            out.append(source[pos:op_end])
            pos = op_end
            continue
        paren_open = _find_balanced_paren(source, k, direction=-1)
        if paren_open == -1 or paren_open < pos:
            # Either the parens don't balance, or the matching ``(`` lies
            # in already-consumed text (the operator is the outer one of
            # a multi-level nest, with the inner already replaced this
            # pass).  In the latter case the calling fixpoint loop's next
            # iteration will retry against the modified source.
            out.append(source[pos:op_end])
            pos = op_end
            continue
        # If the matching ``(`` is preceded directly (no whitespace) by
        # an identifier, the parenthesised group is a function-call
        # argument list, and the *operand* of the postfix operator is
        # the whole call ``name(args)`` — not just the ``(args)`` part.
        # Without this step, ``f(x)%`` would be rewritten to
        # ``fpercent((x))`` (orphaning the ``f``), and ``abs(-3)!`` to
        # ``absfact((-3))``.  We walk backward past word characters to
        # find the start of the identifier; ``.`` and ``]`` are
        # deliberately excluded so the scope matches what _BINOP_RHS
        # accepts as a function call (bare-identifier-call only).
        operand_start = paren_open
        if paren_open > 0:
            j = paren_open - 1
            while j >= 0 and (source[j].isalnum() or source[j] == '_'):
                j -= 1
            # Only consume the identifier if at least one ident char
            # was found AND the result is a valid identifier start
            # (begins with letter or underscore, not a digit — so the
            # implicit-mul case ``5(x)%`` doesn't get mis-grouped).
            if j + 1 < paren_open and (source[j + 1].isalpha() or source[j + 1] == '_'):
                operand_start = j + 1
        out.append(source[pos:operand_start])
        operand_with_parens = source[operand_start:k + 1]
        out.append(callback(operand_with_parens, op_text))
        pos = op_end
    return ''.join(out)


def _wrap_binop(fn_name: str, op_chars: str, matched: str) -> str:
    """Split ``matched`` (e.g. ``"R₁ || R₂"``) on the first top-level
    occurrence of the operator characters and emit ``fn_name(lhs, rhs)``.

    A stateful split (rather than re-using regex capture groups) handles
    operands that themselves contain parens, like ``f(x) ± g(y)``, where
    a naive ``(_BINOP_RHS)\\s*op\\s*(_BINOP_RHS)`` capture might split
    incorrectly because of greedy/non-greedy interactions.
    """
    depth = 0
    op_len = len(op_chars)
    i = 0
    while i < len(matched):
        c = matched[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif depth == 0 and matched[i:i + op_len] == op_chars:
            lhs = matched[:i].strip()
            rhs = matched[i + op_len:].strip()
            return f'{fn_name}({lhs}, {rhs})'
        i += 1
    return matched


def rewrite_parallel(source: str) -> str:
    """
    Rewrites:
        a || b           -> parallel(a, b)
        (a+b) || c       -> parallel((a+b), c)
        a || (b+c)       -> parallel(a, (b+c))
        a || b || c      -> parallel(parallel(a, b), c)
        f(x) || g(y)     -> parallel(f(x), g(y))
    """

    previous = None
    while source != previous:
        previous = source
        # Negative lookbehind ``(?<![A-Za-z_])`` prevents matching when
        # the LHS would start mid-identifier (e.g. don't grab ``arallel``
        # out of ``parallel``).
        source = re.sub(
            rf'(?<![A-Za-z_]){_BINOP_RHS}\s*\|\|\s*{_BINOP_RHS}',
            lambda m: _wrap_binop('parallel', '||', m.group(0)),
            source,
        )

    return source


def rewrite_plusminus(source: str) -> str:
    """
    Rewrites:
        1±2              -> plusminus(1, 2)
        x±y              -> plusminus(x, y)
        (a+b)±c          -> plusminus((a+b), c)
        a±(b+c)          -> plusminus(a, (b+c))
        a±b±c            -> plusminus(plusminus(a, b), c)
        f(x)±g(y)        -> plusminus(f(x), g(y))

    Precedence: ``±`` binds LESS tightly than ``* /``, so
    ``a ± b * c`` reads as ``plusminus(a, b*c)`` — matching the
    scientific convention where ``5 V ± 0.1·V`` means "5 V with
    uncertainty 0.1·V", not "(5 ± 0.1) all multiplied by V".
    The earlier conservative implementation only consumed a single
    atom on each side, leaving trailing operators dangling outside
    the ``plusminus(...)`` call — that gave surprising results for
    expressions like ``val ± val · (tol + tcr·t + oth)``.

    The right-hand side now greedily eats an arithmetic expression
    (atoms joined by ``* /``, ``+ -`` aren't included because we
    want to stop at the next ``±`` or statement boundary).  For
    additive RHS the user should parenthesise explicitly.
    """
    # Greedy RHS pattern: one ``_BINOP_RHS``-shaped atom, optionally
    # followed by a chain of ``(* /) atom``.  We intentionally do NOT
    # eat ``+ -`` chains — Mathcad's convention treats ``±`` as roughly
    # additive-level, so ``a ± b + c`` is ambiguous; we err on the side
    # of consuming less and letting the user parenthesise.  Same with
    # ``±`` itself: a chained ``a ± b ± c`` is rewritten left-to-right
    # by the outer loop, not by a single greedy match.
    _PM_RHS = rf'{_BINOP_RHS}(?:\s*[*/]\s*{_BINOP_RHS})*'

    previous = None
    while source != previous:
        previous = source
        source = re.sub(
            rf'(?<![A-Za-z_]){_BINOP_RHS}\s*±\s*{_PM_RHS}',
            lambda m: _wrap_binop('plusminus', '±', m.group(0)),
            source,
        )

    # --- Unary ``±`` --------------------------------------------------
    # 0 is the neutral element of ``±``, so a leading ``±x`` (no left
    # operand) means the symmetric interval centred at zero — the same as
    # ``0 ± x``.  ``±10`` → ``(-10 ‥ 10)``, ``±10 °C`` → ``(-10 °C ‥
    # 10 °C)``.  Any ``±`` still present after the binary pass above has
    # no left operand; it fires when preceded by a statement/expression
    # opener — start of string, ``=``, ``(``, ``[``, ``,``, ``:``, or
    # another operator — i.e. nothing that could be a left value.  We
    # rewrite it to ``plusminus(0, <RHS>)``; the centre ``0`` is exact
    # (infinite sf) so it adds no spurious precision, and a temperature
    # RHS (already ``from_degC(...)``) makes the centre adopt that scale
    # via ``plusminus``'s normal handling.
    _UNARY_OPENER = r'(?:^|(?<=[=(\[,:+\-*/])|(?<=\bin\b)|(?<=\breturn\b))'
    previous = None
    while source != previous:
        previous = source
        source = re.sub(
            rf'{_UNARY_OPENER}(\s*)±\s*({_PM_RHS})',
            lambda m: f'{m.group(1)}plusminus(0, {m.group(2)})',
            source,
        )
    return source


def rewrite_string_range(source: str) -> str:
    """
    Rewrites a range between two STRING-LITERAL endpoints into a call to
    the runtime helper :func:`_str_range`:

        ['1'..'5']          -> _CommaArray(_str_range('1', '5'))
        ['A'..'E']          -> _CommaArray(_str_range('A', 'E'))
        ['C8'..'C13']       -> _CommaArray(_str_range('C8', 'C13'))
        'a'..'f'            -> _str_range('a', 'f')    (bare form)

    A mixed list keeps non-range items verbatim and is flattened at
    runtime (see the ``_CommaArray`` / list-splice handling): e.g.
    ``['C8'..'C13', 'R20', 'a'..'f']`` becomes a concatenation of the
    expanded ranges and the lone ``'R20'``.

    Runs BEFORE :func:`rewrite_range_dots` so the numeric ``..`` rewriter
    doesn't grab these string endpoints first, and AFTER
    ``_protect_strings`` — so by the time we see them the literals are
    masked placeholders like ``'__dsl_str_5__'``.  We match the *masked*
    shape and keep the quoted placeholders intact in the emitted call, so
    ``_restore_strings`` later swaps the real contents back inside
    ``_str_range(...)``.  Matching the masked form (rather than real
    string bodies) means a stray ``..`` inside a string body can never be
    mistaken for a range operator.
    """
    # A masked string literal: a quote, the opaque ``__dsl_str_N__``
    # placeholder, the same quote.  (``_protect_strings`` keeps the
    # original quote character.)
    STR = r'''(?P<q{n}>["'])__dsl_str_\d+__(?P=q{n})'''
    DOT_DOT = r'(?<!\.)\.\.(?!\.)'

    # Stepped string range:  'a'..'z'..2
    source = re.sub(
        rf'({STR.format(n=1)})\s*{DOT_DOT}\s*({STR.format(n=2)})\s*'
        rf'{DOT_DOT}\s*([+-]?\d+)',
        lambda m: f'*_str_range({m.group(1)}, {m.group(3)}, {m.group(5)})',
        source,
    )
    # Two-endpoint string range:  'C8'..'C13'
    #
    # Emitted as a SPLICE — ``*_str_range(a, b)`` — Python's in-list
    # unpacking.  Inside a list this flattens correctly whether the range
    # fills the whole bracket (``['1'..'5']`` → ``[*_str_range('1','5')]``
    # → ``['1',…,'5']``) or shares it with other items
    # (``['C8'..'C13', 'R20', 'a'..'f']`` →
    # ``[*_str_range('C8','C13'), 'R20', *_str_range('a','f')]``).  Using
    # one uniform form keeps every string range a plain ``list`` of
    # strings, matching the examples, and avoids a CommaArray/​list split.
    #
    # The quoted placeholders are kept verbatim so ``_restore_strings``
    # later swaps the real string contents back inside the call.
    source = re.sub(
        rf'({STR.format(n=1)})\s*{DOT_DOT}\s*({STR.format(n=2)})',
        lambda m: f'*_str_range({m.group(1)}, {m.group(3)})',
        source,
    )
    return source


def rewrite_range_dots(source: str) -> str:
    """
    Rewrites mathematical range-dot notation:

        [a..b]      -> _CommaArray(_range_inc(a, b))
        [a..b..s]   -> _CommaArray(_range_inc(a, b, s))
        a..b        -> _range_inc(a, b)            (bare form, returns a range)
        a..b..s     -> _range_inc(a, b, s)

    Both endpoints are inclusive — ``[1..3]`` evaluates to ``[1, 2, 3]``
    matching whiteboard convention, not Python's half-open ``range(1, 3)``.

    The bracketed form yields a ``CommaArray`` (numpy ndarray subclass)
    so it can be multiplied by units in the engineering DSL: ``[1..5] mV``
    works the same way as a plain list literal would.  The bare form
    yields whatever ``_range_inc`` returns — a ``range`` for integer
    bounds, a list for float bounds — both iterable, suitable for
    ``for n in 1..N:`` loops.

    Operands can be integer/float literals, identifiers (including ones
    with subscript suffixes like ``R₁``), or parenthesised expressions.
    For non-decimal numeric bases or arbitrary expressions, parenthesise:
    ``[(int("ff", 16))..1024]``.

    The rewriter is careful not to match ``...`` (ellipsis): only exactly
    two dots, with no leading or trailing third dot.  This rewrite must
    run *before* tokenization, because Python's tokenizer interprets
    ``1..3`` as the floats ``1.`` followed by ``.3`` — once tokenized,
    the original ``..`` pattern is irretrievable.

    Note: this rewriter is regex-based and does not skip strings or
    comments.  Writing ``..`` inside a string literal or comment will
    currently be rewritten — same caveat as the other rewriters.  If
    that bites you, parenthesise: ``"... range is " + str([1..3])``
    keeps the dots in code, where they're meant to be.
    """

    # The dots: exactly two, not part of an ellipsis ``...``.
    DOT_DOT = r'(?<!\.)\.\.(?!\.)'

    # Range operands accept everything _BINOP_RHS accepts, plus an
    # optional leading sign ``-``/``+`` so descending steps written as
    # ``[5..1..-1]`` are recognised.  The leading sign is kept simple
    # rather than nested in ``_BINOP_RHS`` itself, which would risk
    # shadowing arithmetic minus elsewhere in the language.
    RANGE_OP = rf'(?:[+-]\s*)?{_BINOP_RHS}'

    # Three-arg bracketed form (with step) — must come before the 2-arg form
    # so a `[a..b..s]` doesn't get partially-eaten as `[a..b]`.
    source = re.sub(
        rf'\[\s*({RANGE_OP})\s*{DOT_DOT}\s*({RANGE_OP})\s*'
        rf'{DOT_DOT}\s*({RANGE_OP})\s*\]',
        r'_CommaArray(_range_inc(\1, \2, \3))',
        source,
    )
    # Two-arg bracketed form
    source = re.sub(
        rf'\[\s*({RANGE_OP})\s*{DOT_DOT}\s*({RANGE_OP})\s*\]',
        r'_CommaArray(_range_inc(\1, \2))',
        source,
    )
    # Three-arg bare form
    source = re.sub(
        rf'({RANGE_OP})\s*{DOT_DOT}\s*({RANGE_OP})\s*'
        rf'{DOT_DOT}\s*({RANGE_OP})',
        r'_range_inc(\1, \2, \3)',
        source,
    )
    # Two-arg bare form
    source = re.sub(
        rf'({RANGE_OP})\s*{DOT_DOT}\s*({RANGE_OP})',
        r'_range_inc(\1, \2)',
        source,
    )
    return source


def rewrite_interval_dots(source: str) -> str:
    """
    Rewrites the closed-interval operator ``‥`` (U+2025 TWO DOT LEADER):

        a ‥ b            -> _interval(a, b)
        (3 ‥ 7)          -> (_interval(3, 7))
        12 Ω ‥ 15 Ω      -> _interval(12 Ω, 15 Ω)

    ``‥`` is the glyph ``Range`` prints itself with — ``5 ± 2`` shows
    ``(3 ‥ 7)`` — so this pass makes that printout valid input: what
    you see is what you can paste back.  It is distinct from the
    two-ASCII-dot ``a..b`` handled by :func:`rewrite_range_dots`, which
    ENUMERATES the steps between the ends (``3..7`` → ``[3,4,5,6,7]``).

    Runs before ``rewrite_range_dots``; the two never overlap because
    ``‥`` is a single code point that the ``..`` pattern can't match.
    Operands are the same shapes ``..`` accepts (literals, identifiers,
    calls, parenthesised groups, optionally unit-suffixed, optional
    leading sign).  Same caveat as the other regex rewriters: a ``‥``
    inside a string literal is rewritten too.
    """
    RANGE_OP = rf'(?:[+-]\s*)?{_BINOP_RHS}'
    previous = None
    while source != previous:
        previous = source
        source = re.sub(
            rf'(?<![A-Za-z0-9_.]){RANGE_OP}\s*‥\s*{RANGE_OP}',
            lambda m: _wrap_binop('_interval', '‥', m.group(0)),
            source,
        )
    return source

# ---------- inequality-style for-loop ----------

# Mapping from the (left_op, right_op) pair to the (start_expr, stop_expr,
# step) triple that turns the user's inequality bounds into a range() call.
#
# Operators arrive at this pass already normalized — ``≤`` → ``<=`` and
# ``≥`` → ``>=`` — courtesy of ``normalize_source``, so we only need to
# match the ASCII forms here.
#
# Ascending forms (start on the left, stop on the right; expects start ≤ stop):
#   start <= j <= stop  ->  range(start,    stop+1)
#   start <  j <= stop  ->  range(start+1,  stop+1)
#   start <= j <  stop  ->  range(start,    stop)
#   start <  j <  stop  ->  range(start+1,  stop)
#
# Descending forms (start on the left, stop on the right; expects start ≥ stop):
#   start >= j >= stop  ->  range(start,    stop-1, -1)
#   start >  j >= stop  ->  range(start-1,  stop-1, -1)
#   start >= j >  stop  ->  range(start,    stop,   -1)
#   start >  j >  stop  ->  range(start-1,  stop,   -1)
#
# The descending stop sentinel is `stop-1` (when the right inequality is
# closed) or `stop` (when open) — the exact mirror of the ascending case.
# It is NOT `stop+1`: that would terminate the loop early.  Mirror logic
# applies on the start side: a strict left inequality (``>``) means the
# first iterate is `start-1`, not `start+1`.
# Each inequality operator is either CLOSED (inclusive: ``<=`` / ``>=``)
# or OPEN (strict: ``<`` / ``>``).  The loop range is emitted as a call
# to the runtime helper ``_range_ineq(start, stop, left_closed,
# right_closed)`` — which handles BOTH numeric bounds (the classic
# ``range`` with ``±1`` offsets for strict ends) AND date / datetime
# bounds (walking the calendar a day at a time).  Routing through the
# helper rather than building a bare ``range()`` here is what lets a
# date-bounded header — ``for "2026-05-06"ₜᵢₘₑ ≤ d ≤ "2026-05-26"ₜᵢₘₑ:``
# — work: ``range()`` is integer-only and could never accept a date.
#
# Direction (ascending vs descending) is inferred by the helper from the
# endpoint values, so the operator pair only needs to yield the two
# closed/open flags.  A direction-mismatched pair (one ``<``-family, one
# ``>``-family) is rejected — left for Python's parser to flag.
_INEQ_CLOSED = {'<=': True, '>=': True, '<': False, '>': False}
_INEQ_ASCENDING = {'<=', '<'}
_INEQ_DESCENDING = {'>=', '>'}


# A comparison operator: two-character forms must come first so the regex
# engine doesn't greedily eat the leading ``<`` or ``>`` and leave the
# trailing ``=`` dangling.
_INEQ_OP = r'<=|>=|<|>'


# Anchored to start-of-line (with leading indent) so we only match the
# header of a for-statement, never a comparison nested inside another
# expression.  The trailing ``:.*`` keeps any ``# comment`` or one-line
# suite (``for 1 <= j <= 10: pass``) intact.
_INEQ_FOR_RE = re.compile(
    rf'^(?P<indent>[ \t]*)for\s+'
    rf'(?P<start>{_BINOP_RHS})\s*'
    rf'(?P<op1>{_INEQ_OP})\s*'
    rf'(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*'
    rf'(?P<op2>{_INEQ_OP})\s*'
    rf'(?P<stop>{_BINOP_RHS})\s*'
    rf'(?P<tail>:.*)$',
    re.MULTILINE,
)


def rewrite_inequality_for(source: str) -> str:
    """
    Rewrites inequality-style for-loop headers into ``for ... in range(...):``.

    Ascending direction (uses ``<`` and ``<=``; equivalently ``≤``)::

        for start <= j <= stop:    →  for j in range(start,   stop+1):
        for start <  j <= stop:    →  for j in range(start+1, stop+1):
        for start <= j <  stop:    →  for j in range(start,   stop):
        for start <  j <  stop:    →  for j in range(start+1, stop):

    Descending direction (uses ``>`` and ``>=``; equivalently ``≥``).
    Caller is responsible for ensuring start ≥ stop — Python's ``range``
    silently yields the empty sequence if you get the direction wrong::

        for start >= j >= stop:    →  for j in range(start,   stop-1, -1):
        for start >  j >= stop:    →  for j in range(start-1, stop-1, -1):
        for start >= j >  stop:    →  for j in range(start,   stop,   -1):
        for start >  j >  stop:    →  for j in range(start-1, stop,   -1):

    Direction-mismatched headers like ``for 1 < j > 5:`` are not matched
    by this pass; they'll fall through to Python's parser, which will
    raise a SyntaxError on the bare comparison expression.

    The bounds may be any expression that ``_BINOP_RHS`` recognises:
    a number, an identifier (with optional subscripts), a function call,
    an indexed access, or a parenthesised expression.  For arbitrary
    arithmetic in a bound, parenthesise it::

        for (n*2) <= j <= (m + k):

    is the same convention used by every other binary-operator rewriter
    in this module (``‖``, ``±``, ``∠``, etc.).

    The companion form ``for j in 1..10:`` is handled separately by
    ``rewrite_range_dots`` and goes through ``_range_inc`` rather than a
    plain ``range()`` — that path supports float bounds as well as ints,
    while the inequality form here produces a vanilla ``range()`` call
    (integer bounds only, matching Python's ``range``).
    """
    def _sub(m):
        op1, op2 = m.group('op1'), m.group('op2')
        # Both operators must point the same direction — both
        # ascending (``<`` family) or both descending (``>`` family).
        # A mismatch (``for 1 < j > 5:``) is left untouched for Python's
        # parser to reject with a pointer at the user's actual text.
        same_dir = (
            (op1 in _INEQ_ASCENDING and op2 in _INEQ_ASCENDING)
            or (op1 in _INEQ_DESCENDING and op2 in _INEQ_DESCENDING)
        )
        if not same_dir:
            return m.group(0)

        left_closed = _INEQ_CLOSED[op1]
        right_closed = _INEQ_CLOSED[op2]

        # ``_range_ineq`` handles direction, strictness, and the
        # numeric-vs-date split at runtime.
        range_call = (
            f"_range_ineq({m.group('start')}, {m.group('stop')}, "
            f"{left_closed}, {right_closed})"
        )
        return f"{m.group('indent')}for {m.group('var')} in {range_call}{m.group('tail')}"

    return _INEQ_FOR_RE.sub(_sub, source)


# Comprehension form: ``for start ≤ j ≤ stop`` appearing mid-expression
# inside [ ], { }, or ( ).  Differs from the statement form in that it
# does NOT require a trailing ``:`` and it isn't anchored to start-of-line —
# the clause is delimited by the next ``for`` / ``if`` keyword, by a
# closing ``)`` / ``]`` / ``}``, or by the end of the line.  Look-ahead
# at the end of the regex enforces that delimiter so we don't accidentally
# eat the body of a comprehension that has more clauses after the range.
#
# Two design notes:
#
#   1. The look-ahead doesn't consume the terminator, so the rewrite
#      replaces just the ``for ... ≤ ... ≤ ...`` portion and leaves the
#      following ``for`` / ``if`` / closing bracket in place to be
#      processed normally by Python (or by another DSL pass).
#
#   2. We require either whitespace, ``[``, ``(``, ``{``, or ``,``
#      *before* the ``for`` so we don't accidentally match the start of a
#      method or attribute name that happens to begin with ``for``
#      (none exist in stdlib but user code might define one).  The
#      look-behind is implemented as an explicit alternation rather than
#      a Python look-behind because the statement form's regex (which
#      ran first) won't have consumed comprehension-form headers, but it
#      WILL have rewritten any line-anchored ``for`` — so by the time
#      this regex runs, every remaining ``for`` is mid-expression and
#      the prefix-context check is just a sanity check.
_INEQ_FOR_COMP_RE = re.compile(
    rf'(?P<prefix>[\s\[\(\{{,])for\s+'
    rf'(?P<start>{_BINOP_RHS})\s*'
    rf'(?P<op1>{_INEQ_OP})\s*'
    rf'(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*'
    rf'(?P<op2>{_INEQ_OP})\s*'
    rf'(?P<stop>{_BINOP_RHS})'
    rf'(?=\s*(?:for\b|if\b|\]|\)|\}}|$))',
    re.MULTILINE,
)


def rewrite_inequality_for_comprehension(source: str) -> str:
    """
    Rewrites inequality-style ``for`` clauses inside comprehensions and
    generator expressions, mirroring :func:`rewrite_inequality_for` for
    statement-level loops.

    Examples::

        [j² for 1 ≤ j ≤ 10]              →  [j² for j in range(1, 11)]
        {j² for 1 < j ≤ 10}              →  {j² for j in range(2, 11)}
        {j: j² for 1 ≤ j ≤ 10}           →  {j: j² for j in range(1, 11)}
        sum(j for 1 ≤ j ≤ n)             →  sum(j for j in range(1, n+1))

    The same eight comparison shapes (ascending and descending, four
    open/closed combinations each) are supported as in the statement
    form — both routes emit ``_range_ineq`` (see that helper and
    :data:`_INEQ_CLOSED` for how the operators map to range bounds).

    Multi-clause comprehensions work; the rewriter handles each clause
    independently::

        [i*j for 1 ≤ i ≤ 3 for 1 ≤ j ≤ 3]
            →  [i*j for i in range(1, 4) for j in range(1, 4)]

    Filter clauses (``if``) terminate a range and are left untouched::

        [j² for 1 ≤ j ≤ 10 if j % 2 == 0]
            →  [j² for j in range(1, 11) if j % 2 == 0]

    Direction-mismatched headers (``for 1 < j > 10``) are not rewritten
    here either; the result is a bare-comparison expression that Python
    will reject at parse time, pointing the user at the real problem.
    """
    def _sub(m):
        op1, op2 = m.group('op1'), m.group('op2')
        same_dir = (
            (op1 in _INEQ_ASCENDING and op2 in _INEQ_ASCENDING)
            or (op1 in _INEQ_DESCENDING and op2 in _INEQ_DESCENDING)
        )
        if not same_dir:
            return m.group(0)

        left_closed = _INEQ_CLOSED[op1]
        right_closed = _INEQ_CLOSED[op2]
        range_call = (
            f"_range_ineq({m.group('start')}, {m.group('stop')}, "
            f"{left_closed}, {right_closed})"
        )

        # Re-emit the prefix character so we don't swallow the bracket
        # or comma that delimited this ``for`` clause from what came
        # before.  Same trick the statement-form rewriter uses for
        # ``indent``.
        return f"{m.group('prefix')}for {m.group('var')} in {range_call}"

    # Run as a fix-point: when a comprehension has multiple inequality
    # ``for`` clauses, each substitution shifts subsequent positions, so
    # rerunning until stable is the simplest way to handle them all
    # cleanly.  Each pass strictly reduces the count of unrewritten
    # clauses, so termination is guaranteed.
    previous = None
    while source != previous:
        previous = source
        source = _INEQ_FOR_COMP_RE.sub(_sub, source)
    return source


# ---------- arrow → return-type annotation ----------

# Matches the return-type arrow in a ``def`` signature: ``def NAME(...) → T``.
# The match is anchored on a preceding ``def NAME(<balanced parens>)`` so
# any other ``→`` on a def line (e.g. inside a default value like
# ``def f(cb = x → x*2):``) is left for the lambda-arrow pass.
#
# The parameter-list balancer accepts up to 3 levels of nested parens.
# Counting the outer pair as level 1, that's enough for things like
# ``def f(x = compute(g(h)))`` — depth 3, with ``h`` un-nested.  Deeper
# nesting in a parameter list is rare in real code; if you hit it, hoist
# the default into a top-level binding.  Square brackets and braces inside
# the param list (type annotations like ``List[Tuple[int, int]]``) don't
# affect the count — only paren depth matters here.
_DEF_ARROW_RE = re.compile(
    r'(?P<head>'
    r'\bdef\s+[A-Za-z_]\w*\s*'
    r'\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)'
    r'\s*)→'
)


def rewrite_def_arrow(source: str) -> str:
    """
    Rewrites the return-type arrow in ``def`` signatures::

        def f(x) → int:               →  def f(x) -> int:
        def f(x: V, R: Ω) → V:        →  def f(x: V, R: Ω) -> V:
        async def g() → bool:         →  async def g() -> bool:

    This is a typographic upgrade — ``→`` here means exactly what
    Python's ``->`` does (the return-type annotation), and the rewrite
    is a one-character substitution with no semantic change.

    The pattern is anchored on ``def NAME(<balanced>)`` so it only
    touches arrows that sit between the close-paren of a signature and
    the trailing colon.  Arrows that appear elsewhere on a def line —
    inside a default value, for example::

        def f(cb = x → x*2): ...      →  def f(cb = lambda x: x*2): ...

    — are left for ``rewrite_lambda_arrow`` to handle.

    The parameter-list balancer tolerates up to 3 levels of paren
    nesting, which covers virtually every real-world signature.
    Pathologically deep defaults (``def f(x = a(b(c(d())))):``) won't
    match — break them out into a top-level binding instead.
    """
    return _DEF_ARROW_RE.sub(r'\g<head>->', source)


# ---------- arrow → lambda ----------

# A bare identifier accepted as a lambda parameter.  Subscript-bearing
# names (``x₁``) are deliberately excluded: they get rewritten to
# indexed access (``x[1]``) later in the pipeline, which can't be a
# lambda parameter.
_LAMBDA_IDENT = r'[A-Za-z_]\w*'


# Lambda parameter form: a bare identifier OR a parenthesised
# comma-separated list of identifiers (possibly empty).  Defaults and
# annotations inside lambdas are NOT supported here:
#
#   - Annotations: Python's lambda syntax doesn't accept them at all
#     (``lambda x: int: x+1`` is a syntax error).  Use ``def`` instead.
#   - Defaults: Python's lambda *does* accept these, but the regex
#     stays simple by routing them to ``def`` as well — losing this is
#     a tiny ergonomic cost compared to the parsing complexity of
#     comma-separating defaults that may themselves contain commas.
#
# The two negative lookbehinds keep the pattern from misreading
# ordinary Python constructs as lambda parameters:
#
#   - bare ident:  not preceded by ``\w`` or ``.``, so ``a.b → c`` and
#                  any ident running into preceding text don't match
#   - parens:      not preceded by ``\w``, ``)``, or ``]``, so
#                  ``f(x) → y`` and ``a[i](x) → y`` aren't read as
#                  lambdas — those parens belong to a function call,
#                  not to a parameter list
#
# As a side effect of the bare-ident branch, ``int → int`` (someone
# trying to write a Haskell-style "function type") gets rewritten to
# ``lambda int: int``, which is valid Python but probably not what the
# user meant.  Python doesn't have first-class function-type syntax —
# use ``Callable[[int], int]`` for that.
_LAMBDA_PATTERN = re.compile(
    r'(?:'
    rf'(?<![\w.])(?P<bare>{_LAMBDA_IDENT})'
    r'|'
    rf'(?<![\w)\]])(?P<parens>\(\s*'
    rf'(?:{_LAMBDA_IDENT}(?:\s*,\s*{_LAMBDA_IDENT})*)?'
    rf'\s*\))'
    r')'
    r'\s*→'
)


def _lambda_sub(m) -> str:
    if m.group('bare') is not None:
        return f"lambda {m.group('bare')}:"
    inner = m.group('parens')[1:-1].strip()
    if not inner:
        # ``() →`` becomes ``lambda:`` — Python's no-arg lambda form.
        # Whitespace between ``lambda`` and ``:`` is also accepted by
        # Python's parser, so the simpler ``"lambda:"`` is fine here.
        return 'lambda:'
    # Normalise inner whitespace: split on commas, strip each, re-join
    # with a single ``", "`` so the output looks consistent regardless
    # of how the user spaced their original parameter list.
    parts = [p.strip() for p in inner.split(',')]
    return f"lambda {', '.join(parts)}:"


def rewrite_lambda_arrow(source: str) -> str:
    """
    Rewrites the math "maps to" arrow into a Python ``lambda``::

        x → x**2                  →  lambda x: x**2
        (x, y) → x + y            →  lambda x, y: x + y
        () → 5                    →  lambda: 5
        f → (g → f(g(0)))         →  lambda f: (lambda g: f(g(0)))

    Combined with the DSL's ``:=`` assignment, this gives a one-line
    function-definition idiom that reads as math::

        square := x → x**2        →  square = lambda x: x**2

    What's matched as a parameter list:

      - a bare identifier (``x``)
      - a parenthesised comma-separated list of identifiers
        (``()``, ``(x)``, ``(x, y, z)``)

    What's NOT matched (and intentionally so):

      - subscripted names (``x₁``) — rewritten to ``x[1]`` later, which
        can't be a lambda parameter
      - typed parameters (``(x: int) → ...``) — Python's lambda doesn't
        accept annotations; use ``def`` for those
      - defaults (``(x=5) → ...``) — also routed to ``def`` for
        simplicity (Python allows them on lambdas, but the regex stays
        cleaner without them)
      - attribute access (``a.b → c``) — left alone; if someone genuinely
        intended a lambda here, Python's parser will surface the
        leftover ``→`` as a syntax error
      - function-call results (``f(x) → y``) — the parens belong to the
        call, not to a lambda parameter list; the negative lookbehind on
        ``)`` and ``]`` rules this out

    Body extent: the rewriter just replaces ``params →`` with
    ``lambda params:`` and lets Python's parser figure out where the
    body ends, exactly as it does for native lambda syntax.  Body ends
    at the next top-level comma, close-bracket, or end-of-statement —
    same rules as a normal Python lambda.

    Chained arrows (``x → y → z``) curry naturally: the rewriter finds
    each match left-to-right and emits ``lambda x: lambda y: z``, which
    Python parses right-associatively as ``lambda x: (lambda y: z)``.

    Order with respect to ``rewrite_def_arrow``: this pass runs after,
    so any return-type ``→`` in a ``def`` signature is already ``->``
    by the time we get here.  (Even without that ordering the negative
    lookbehinds would protect def signatures, but doing the specific
    case first is clearer.)
    """
    return _LAMBDA_PATTERN.sub(_lambda_sub, source)


# ---------- ▶  Mathcad-style target-unit operator ----------

# The ``▶`` glyph rewrites ``LHS ▶ RHS`` into ``in_units(LHS, RHS, "<RHS source>")``.
# The third argument — a string of the literal source text on the RHS —
# is what makes the result render the way the user typed it.  Without it,
# ``in_units`` falls back to ``repr(target)``, which for a compound
# Physical like ``mm/s`` shows the reduced base form ``m·s⁻¹`` rather
# than the user's preferred prefix.  Capturing the source preserves
# exactly what the user wrote (``mm/s``, ``μm/s²``, ``kg·m/s²``, etc).
#
# Boundary detection is hand-rolled rather than regex because we need
# balanced-paren scanning on both sides — a regex would have to bound
# nesting depth.  The LHS extent walks backward from the ``▶`` until it
# hits a newline, an enclosing open-bracket (``(`` ``[`` ``{``) at depth
# zero, a top-level comma, or an assignment operator (``:=`` ``≔`` ``←``
# ``=``).  The RHS extent walks forward symmetrically: stops at newline,
# ``#`` comment, an enclosing close-bracket at depth zero, or a top-level
# comma.
#
# Limitation noted in DSL_Manual: inside a list comprehension or
# generator, the trailing ``for`` keyword is a soft boundary the scanner
# doesn't recognise.  Wrap in parens for those contexts:
#     [(v ▶ mm/s) for v in vs]      # works
#     [v ▶ mm/s for v in vs]        # RHS extends through "for v in vs" — wrong

_TARGET_UNIT_GLYPH = "▶"

# Multi-char operators preceded by these chars are NOT plain ``=``;
# detecting them lets us treat them as compound and not as an LHS
# boundary.  See ``replace_top_level_single_equals`` for the same set.
_EQ_COMPOUND_PREVS = "<>!=:+-*/%&|^@"


def rewrite_target_unit(source: str) -> str:
    """Rewrite ``LHS ▶ RHS`` to ``in_units(LHS, RHS, "<RHS source>")``.

    Runs *early* in the pipeline, before tokenisation / numeric-literal
    wrapping / the implicit-multiply pass, because the captured RHS
    source text becomes the display label and we want the user's
    original spelling — ``mm/s`` — not the post-rewrite form
    (``_S(0.001, _INF) * m / s`` or similar).

    Strings and comments are already stashed by ``_protect_strings``
    upstream, so a stray ``▶`` inside a string literal won't be
    interpreted here.

    See the module-level note for boundary detection rules and the
    list-comprehension caveat.
    """
    # Iterate: rewrite the leftmost ``▶`` each pass and rescan, so that
    # multiple ``▶`` operators on the same line (rare but valid) all
    # get processed.  Each pass removes one glyph, guaranteeing
    # termination.  Linear-time per pass; total O(n × m) for m glyphs.
    while _TARGET_UNIT_GLYPH in source:
        idx = source.index(_TARGET_UNIT_GLYPH)

        # ------------------------------------------------------------------
        # Find LHS extent — scan backward from just before ``▶``.
        # Track an inverted paren depth: closing brackets bump it up
        # (we're "entering" a bracket from the right), opening brackets
        # bump it down — when depth would go negative we've hit the
        # enclosing bracket and the LHS starts just after it.
        # ------------------------------------------------------------------
        lhs_start = 0
        depth = 0
        i = idx - 1
        while i >= 0:
            c = source[i]
            if c == "\n":
                lhs_start = i + 1
                break
            if c in ")]}":
                depth += 1
            elif c in "([{":
                if depth == 0:
                    lhs_start = i + 1
                    break
                depth -= 1
            elif depth == 0:
                if c in ",;":
                    lhs_start = i + 1
                    break
                # Detect bare ``=`` (comparison/assignment in toolkit terms).
                # Skip when ``=`` is part of a compound like ``==`` ``!=``
                # ``<=`` ``>=`` ``:=`` ``+=`` ``-=`` ``*=`` ``/=`` etc.
                if c == "=":
                    nxt = source[i + 1] if i + 1 < len(source) else ""
                    prev = source[i - 1] if i > 0 else ""
                    if nxt != "=" and prev not in _EQ_COMPOUND_PREVS:
                        lhs_start = i + 1
                        break
                # Detect ``:`` of a ``:=``: the ``=`` was already covered
                # above by the prev-char check, but if we hit the ``:``
                # directly (e.g. on its own), advance past the whole ``:=``.
                if c == ":" and i + 1 < len(source) and source[i + 1] == "=":
                    lhs_start = i + 2
                    break
                if c in "≔←":
                    lhs_start = i + 1
                    break
            i -= 1

        # ------------------------------------------------------------------
        # Find RHS extent — scan forward from just after ``▶``.
        # Symmetric to the backward scan: open brackets bump depth up,
        # close brackets at depth 0 mean we've left the enclosing context.
        # ------------------------------------------------------------------
        rhs_end = len(source)
        depth = 0
        i = idx + 1
        while i < len(source):
            c = source[i]
            if c == "\n":
                rhs_end = i
                break
            if depth == 0 and c == "#":
                rhs_end = i
                break
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    rhs_end = i
                    break
                depth -= 1
            elif depth == 0 and c in ",;":
                rhs_end = i
                break
            elif depth == 0 and c == _TARGET_UNIT_GLYPH:
                # A second ``▶`` ends this one's RHS.  Chained display
                # operators (``255 ▶ hex ▶ dec``) must associate left to
                # right: rewrite ``255 ▶ hex`` first (RHS just ``hex``),
                # then the next pass sees ``radix(255,"hex") ▶ dec`` and
                # applies ``dec`` to that result.  Without this the
                # leftmost ``▶`` would swallow ``hex ▶ dec`` as one RHS
                # and mis-handle it.
                rhs_end = i
                break
            i += 1

        lhs_raw = source[lhs_start:idx]
        # Preserve any leading whitespace from the LHS extent — e.g. the
        # space after ``:=`` or ``,`` — so the rewritten line keeps its
        # original spacing.  Without this preservation the output reads
        # ``r :=in_units(...)`` and ``f(a, b),in_units(...)``, which is
        # parseable Python but visually ugly.
        n_leading = len(lhs_raw) - len(lhs_raw.lstrip())
        leading_ws = lhs_raw[:n_leading]
        lhs_text = lhs_raw[n_leading:].rstrip()

        rhs_text = source[idx + 1:rhs_end].strip()

        # Special case: the RHS is a string literal — ``x ▶ "element"``.
        # This is the "label-only" form: the user wants the axis /
        # printout labelled with that exact string, no unit conversion.
        # ``_protect_strings`` (which ran earlier) has already replaced
        # the literal with a quoted placeholder like ``"__dsl_str_3__"``.
        #
        # Here we must NOT generate our own label placeholder — if we
        # did, ``_restore_strings`` would turn the string-literal RHS
        # back into ``"element"`` AND our label placeholder into
        # ``"element"`` too, yielding the broken ``in_units(x,
        # "element", ""element"")`` (doubled quotes).  Instead we pass
        # the SAME string-placeholder as both the target and the label
        # arguments — after restoration both become ``"element"`` and
        # the call reads ``in_units(x, "element", "element")``, which
        # ``in_units``'s string-target fast-path handles cleanly.
        _str_rhs = re.fullmatch(r'(["\'])__dsl_str_\d+__\1', rhs_text)
        if _str_rhs is not None:
            replacement = leading_ws + f'in_units({lhs_text}, {rhs_text}, {rhs_text})'
            source = source[:lhs_start] + replacement + source[rhs_end:]
            continue

        # Special case: the RHS is a bare radix-format name — ``x ▶ hex``,
        # ``▶ bin``, ``▶ oct``, ``▶ dec``, or a user-registered format
        # like ``▶ roman``.  ``hex``/``bin``/``oct`` happen to be Python
        # builtins so they would resolve, but ``dec`` and any custom
        # format name are NOT defined names — passing them as bare
        # identifiers would raise ``NameError`` before ``in_units`` ever
        # runs.  So for ANY known radix name we pass it as a STRING
        # literal in both the target and label position; ``in_units``
        # detects a registered radix name and delegates to ``radix()``.
        #
        # The set of names is queried live from the toolkit's radix
        # registry, so registering a new format (``register_radix(...)``)
        # immediately makes ``▶ <thatname>`` work — no edit here needed.
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', rhs_text):
            try:
                from .sigfig import _RADIX_FORMATTERS as _radix_reg
                _is_radix_name = rhs_text in _radix_reg
            except Exception:
                # Fallback to the built-in names if the import is not
                # available for some reason (keeps the rewriter robust).
                _is_radix_name = rhs_text in {
                    "hex", "bin", "oct", "dec",
                    "base16", "base2", "base8", "base10",
                }
            if _is_radix_name:
                replacement = (leading_ws
                               + f'in_units({lhs_text}, "{rhs_text}", "{rhs_text}")')
                source = source[:lhs_start] + replacement + source[rhs_end:]
                continue

        # Display label: take the raw RHS text and prettify it for
        # human consumption (``MeV_per_c2`` → ``MeV/c²``,
        # ``kg_per_m3`` → ``kg/m³``).  The arithmetic still operates
        # on the un-prettified RHS expression — only the string
        # embedded in the in_units call gets the polish.
        #
        # IMPORTANT: we cannot just embed the prettified string here.
        # ``rewrite_target_unit`` runs early in the pipeline, before
        # ``rewrite_postfix_superscripts`` and ``rewrite_subscript_indices``
        # — and the prettified label contains characters those passes
        # love to mangle.  ``c²`` would become ``(c)**(2)``; ``m³``
        # would too.  ``_protect_strings`` ran BEFORE us, so the new
        # string literal we're about to embed isn't in its protected
        # set either.
        #
        # Solution: store the prettified label in a module-level stash
        # under a pure-ASCII placeholder, embed the placeholder in the
        # source, and substitute back at the very end of the pipeline
        # via ``_restore_unit_labels``.  Placeholders survive every
        # subsequent regex/token rewriter untouched.
        label = _prettify_unit_label(rhs_text)
        placeholder = _stash_unit_label(label)
        # Escape defensively, though placeholders are pure ASCII.
        placeholder = placeholder.replace("\\", "\\\\").replace('"', '\\"')

        replacement = leading_ws + f'in_units({lhs_text}, {rhs_text}, "{placeholder}")'
        source = source[:lhs_start] + replacement + source[rhs_end:]

    return source


# Module-level stash for prettified unit labels.  See the discussion in
# ``rewrite_target_unit`` for why we route through a stash rather than
# embedding the prettified string directly.
_unit_label_stash: dict[str, str] = {}


def _stash_unit_label(label: str) -> str:
    """Store a prettified label under a fresh placeholder name and
    return that name for use in the source.  The placeholder is pure
    ASCII (``__dsl_unit_label_N__``) so no downstream rewriter touches
    it.  Restored to the original label by ``_restore_unit_labels``
    at the end of ``transform_source``.
    """
    idx = len(_unit_label_stash)
    placeholder = f"__dsl_unit_label_{idx}__"
    _unit_label_stash[placeholder] = label
    return placeholder


def _restore_unit_labels(source: str) -> str:
    """Substitute every ``__dsl_unit_label_N__`` placeholder back to
    its stored prettified label.  Called once at the end of
    ``transform_source``, after every other pass has had its chance to
    process the source text.  Clears the stash so subsequent
    transformations start from a clean slate.
    """
    if not _unit_label_stash:
        return source
    for placeholder, label in _unit_label_stash.items():
        source = source.replace(placeholder, label)
    _unit_label_stash.clear()
    return source


# Map common Python-identifier unit names to their conventional math
# notation, used ONLY for the ``▸`` display label.  Doesn't affect any
# arithmetic — the underlying Physical / Sig values are unchanged.
#
# Examples:
#   ``MeV_per_c2`` → ``MeV/c²``
#   ``kg_per_m3``  → ``kg/m³``
#   ``W_per_m2_K`` → ``W/(m²·K)``  (multi-_per_ left untouched, hard case)
#
# Two-pass translation: (1) handle ``_per_`` separators, (2) translate
# trailing digit suffixes on tokens into Unicode superscripts.  Keep it
# simple — anything fancier (full unit grammar) belongs in a real units
# package, not a display polish.
_SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")

# Mapping from underscore-form astronomy names back to journal-style
# glyphs for display.  The DSL's ``normalize_source`` does the FORWARD
# direction (``M☉`` → ``M_sun`` so Python can parse it); this is the
# REVERSE direction for the display label only, so when the user
# writes ``mass ▸ M_sun`` (or the equivalent ``mass ▸ M☉``) the
# result reads ``2.5 M☉`` rather than ``2.5 M_sun``.
#
# Only triggered for whole-label matches — partial-token matches would
# wreck legitimate names like ``M_sun_per_pc3`` (mass density).
_ASTRO_DISPLAY_GLYPHS = {
    "M_sun":     "M☉",
    "R_sun":     "R☉",
    "L_sun":     "L☉",
    "T_sun":     "T☉",
    "M_earth":   "M⊕",
    "R_earth":   "R⊕",
    "M_jupiter": "M♃",
    "R_jupiter": "R♃",
    "M_moon":    "M☾",   # crescent moon — no source-side rewrite, just display
}

def _prettify_unit_label(label: str) -> str:
    """Polish a Python-identifier unit name into math notation for
    display.  See module-level comment for the (limited) scope.
    """
    out = label
    # Astronomical glyph mapping — applied FIRST so subsequent passes
    # (``_per_`` and digit→superscript) don't break the multi-character
    # journal glyphs.  Only whole-label matches; partial matches could
    # mangle compound names.
    if out in _ASTRO_DISPLAY_GLYPHS:
        return _ASTRO_DISPLAY_GLYPHS[out]
    # ``_per_`` → ``/`` for the first occurrence (common case is one /).
    # For multiple ``_per_`` the user is probably writing something
    # complex enough they want explicit parens — leave alone after the
    # first to avoid mangling.
    if "_per_" in out:
        out = out.replace("_per_", "/", 1)
    # Token-level: trailing digit suffix becomes a superscript.  Match
    # a letter followed by a digit and translate just the digit.  Done
    # at character level rather than identifier level so we catch both
    # ``c2`` (c²) and ``m3`` (m³) within compound labels.
    out = re.sub(
        r'([A-Za-zµμΩ])(\d+)',
        lambda m: m.group(1) + m.group(2).translate(_SUPERSCRIPT_DIGITS),
        out,
    )
    return out


UNIT_SUFFIX_PREFIX = {
    "p": "prefix_p",
    "n": "prefix_n",
    "u": "prefix_μ",
    "µ": "prefix_μ",
    "μ": "prefix_μ",
    "m": "prefix_m",
    "d": "prefix_d",
    "c": "prefix_c",
    "k": "prefix_k",
    "M": "prefix_M",
    "G": "prefix_G",
    "T": "prefix_T",
}

UNIT_NAMES = r"(?:F|H|Hz|Ω|Ohm|V|A|W|s|m)"

# ---------- degree → radian ----------

def rewrite_degrees(source: str) -> str:
    """
    Rewrites a postfix degree mark on numbers, identifiers, or parenthesised
    expressions as a radian-equivalent multiplication:

        30°       -> (30*π/180)
        θ°        -> (θ*π/180)
        (a+b)°    -> ((a+b)*π/180)

    ``°C`` and ``℃`` were already replaced by ``degC`` in normalize_source,
    so this rule only ever sees angle-degree marks, never temperature.

    The conversion uses the user-facing ``π`` (which is ``sympy.pi`` in
    this toolkit), so clean angles like ``30°`` stay symbolic as ``pi/6``
    until they enter a numeric function.  The benefit: ``math.sin(30°)``
    evaluates as ``math.sin(float(pi/6))`` which gives exactly ``0.5``,
    rather than the floating-point noise (``0.499999...``) you'd get if
    we computed ``30 * math.pi / 180`` numerically and then took its sine.
    Phasor results, plot coordinates, and any downstream complex
    arithmetic see clean values.

    Cost: in pure-numeric contexts where you'd want a float, ``5°``
    displays as ``pi/36`` rather than ``0.0873``.  Apply ``float()``,
    ``sym.N(...)``, or any math/numpy function to collapse it.
    """
    source = re.sub(r'(\([^()\n]+\))\s*°', r'(\1*π/180)', source)
    source = re.sub(
        rf'({_BINOP_ATOM})\s*°',
        r'(\1*π/180)',
        source,
    )
    return source

_NUM_BEFORE_BAR_RE = re.compile(
    r'(?<![\w.])'
    r'(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)'
    r'\|'
)

'''
def rewrite_abs_bars(source: str) -> str:
    """
    Rewrite absolute-value bars:

        |x|          -> abs(x)
        |x + y|      -> abs(x + y)
        2·|x|        -> 2*abs(x)

    Deliberately does NOT rewrite binary-or forms:

        a | b
        int | float
        x |= y

    The rule is conservative: a bar opens abs only in expression-start
    position, and a matching close bar is found at the same nesting depth.
    """

    out = []
    i = 0
    n = len(source)

    def prev_nonspace_char():
        for c in reversed(out):
            if not c.isspace():
                return c
        return None

    def is_abs_open_position(prev):
        # Start of expression, or after operators / delimiters that expect
        # a new expression.
        return prev is None or prev in "([{=:+-*/%,<>!&^~;\n"

    while i < n:
        ch = source[i]

        # Do not touch |=
        if ch == "|" and i + 1 < n and source[i + 1] == "=":
            out.append("|=")
            i += 2
            continue

        if ch != "|":
            out.append(ch)
            i += 1
            continue

        prev = prev_nonspace_char()

        # If this is not expression-start position, treat as normal Python |
        if not is_abs_open_position(prev):
            out.append("|")
            i += 1
            continue

        # Find matching closing |, respecting (), [], {} nesting.
        j = i + 1
        depth = 0
        found = None

        while j < n:
            cj = source[j]

            if cj in "([{":
                depth += 1
            elif cj in ")]}":
                if depth > 0:
                    depth -= 1
            elif cj == "|" and depth == 0:
                # Do not close on ||= impossible, but avoid |=.
                if not (j + 1 < n and source[j + 1] == "="):
                    found = j
                    break

            # Stop at newline if no close before line end.
            if cj == "\n":
                break

            j += 1

        if found is None:
            out.append("|")
            i += 1
            continue

        inner = source[i + 1:found].strip()

        # Empty || is not meaningful.
        if not inner:
            out.append("|")
            i += 1
            continue

        # Conservative ambiguity guard: do not rewrite if the contents
        # themselves contain top-level |.
        if "|" in inner:
            out.append("|")
            i += 1
            continue

        out.append(f"abs({inner})")
        i = found + 1

    return "".join(out)
'''

def rewrite_abs_bars(source: str) -> str:
    """
    Rewrites:
        |x|        -> abs(x)
        2|x|       -> 2*abs(x)

    Deliberately does not rewrite:
        a | b      # Python bitwise-or / type union
        x |= y
        a|b|c      # compact bitwise-or chain; require a·|b| instead
    """
    out = []
    i = 0
    n = len(source)

    def find_matching_bar(start):
        depth = 0
        j = start + 1
        while j < n:
            c = source[j]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth > 0:
                    depth -= 1
            elif c == "|" and depth == 0:
                if not (j + 1 < n and source[j + 1] == "="):
                    return j
            elif c == "\n":
                return None
            j += 1
        return None

    def prev_nonspace():
        for c in reversed(out):
            if not c.isspace():
                return c
        return None

    def expression_start(prev):
        return prev is None or prev in "([{=:+-*/%,<>!&^~;\n"

    while i < n:
        ch = source[i]

        if ch != "|":
            out.append(ch)
            i += 1
            continue

        if i + 1 < n and source[i + 1] == "=":
            out.append("|=")
            i += 2
            continue

        close = find_matching_bar(i)
        if close is None:
            out.append("|")
            i += 1
            continue

        inner = source[i + 1:close].strip()
        if not inner or "|" in inner:
            out.append("|")
            i += 1
            continue

        prev = prev_nonspace()

        if expression_start(prev):
            out.append(f"_abs_or_size({inner})")
            i = close + 1
            continue

        # Special implicit-multiplication case:
        # only NUMBER|expr|, not name|expr|.
        if prev is not None and (prev.isdigit() or prev == "."):
            out.append(f"*_abs_or_size({inner})")
            i = close + 1
            continue

        out.append("|")
        i += 1

    return "".join(out)
# ---------- ∠ phasor ----------

def rewrite_phasor(source: str) -> str:
    """
    Rewrites:
        a ∠ b            -> phasor(a, b)
        (a+b) ∠ c        -> phasor((a+b), c)
        a ∠ (b+c)        -> phasor(a, (b+c))
        5 ∠ 30°          -> phasor(5, (30*π/180))   (degrees runs first)
        5 ∠ -30°         -> phasor(5, -(30*π/180))  (signed angle)
        f(x) ∠ g(y)      -> phasor(f(x), g(y))

    The RHS may have a leading ``-`` or ``+`` sign — common for "below
    the real axis" phasor angles like ``5 ∠ -45°``.  The signed RHS is
    captured as part of the operand and passed through to the runtime
    ``phasor`` helper, which interprets it as a real-valued angle.
    """
    # Like _BINOP_RHS but allows a leading sign on the right-hand side
    # so that ``5 ∠ -30°`` doesn't choke on the minus.
    PHASOR_RHS = rf'(?:[+-]\s*)?{_BINOP_RHS}'

    previous = None
    while source != previous:
        previous = source
        source = re.sub(
            rf'(?<![A-Za-z_]){_BINOP_RHS}\s*∠\s*{PHASOR_RHS}',
            lambda m: _wrap_binop('phasor', '∠', m.group(0)),
            source,
        )
    return source


# ---------- floor / ceiling ----------

def rewrite_floor_ceil(source: str) -> str:
    """
    Rewrites Knuth-style floor/ceiling brackets:
        ⌊x⌋    -> floor(x)
        ⌈x⌉    -> ceil(x)
    """
    source = re.sub(r'⌊([^⌊⌋\n]+)⌋', r'floor(\1)', source)
    source = re.sub(r'⌈([^⌈⌉\n]+)⌉', r'ceil(\1)', source)
    return source


# ---------- ISO 8601 postfix: "..."ₜᵢₘₑ → iso("...") ----------

def rewrite_iso_postfix(source: str) -> str:
    """
    Rewrites a string literal followed by the subscript ``ₜᵢₘₑ`` into a
    call to ``iso(...)``:

        "2026-05-05"ₜᵢₘₑ           -> iso("2026-05-05")
        '2026-05-05T14:30:00'ₜᵢₘₑ  -> iso('2026-05-05T14:30:00')
        'now'ₜᵢₘₑ                   -> iso('now')
        'today'ₜᵢₘₑ                 -> iso('today')

    The trigger is restricted to *string literals* on the LHS so it
    can't shadow the existing subscript-as-index rule for arbitrary
    identifiers (``xₜᵢₘₑ`` still becomes ``x[time]``).  This is unambiguous
    because Python doesn't allow indexing a string with an arbitrary
    name like ``"foo"[time]`` — that's a runtime ``TypeError`` — so
    claiming this shape for ISO parsing doesn't change the meaning of
    any code that was working before.

    Both single and double quotes are accepted.  The string contents
    aren't inspected here — that's ``iso``'s job at runtime.

    Implementation note: this rewriter runs while string contents are
    still masked by ``_protect_strings`` (e.g. the source it sees looks
    like ``"__dsl_str_0__"ₜᵢₘₑ``).  The regex matches the *shape* of a
    string-followed-by-``ₜᵢₘₑ``, not its contents, so the masking is
    transparent.
    """
    return re.sub(
        # Match a string literal (single OR double quoted, no embedded
        # newlines or matching quotes), immediately followed by the
        # subscript ``ₜᵢₘₑ``.  The trailing characters are exact —
        # ``ₜ`` ``ᵢ`` ``ₘ`` ``ₑ`` — so an unrelated subscript like
        # ``ₜₐₓ`` doesn't accidentally trigger.
        r'''((?:"[^"\n]*"|'[^'\n]*'))\s*ₜᵢₘₑ''',
        r'iso(\1)',
        source,
    )


def rewrite_roman_postfix(source: str) -> str:
    """
    Rewrites a string literal followed by the subscript ``ᵣₒₘₑ`` into a
    call to ``from_roman(...)``:

        "MCMIX"ᵣₒₘₑ    -> from_roman("MCMIX")
        'MMXXIV'ᵣₒₘₑ   -> from_roman('MMXXIV')

    This is the input counterpart of the ``▸ roman`` display tag:
    ``▸ roman`` renders an integer AS Roman numerals, ``"…"ᵣₒₘₑ`` reads
    a Roman-numeral string back into an integer.  The result is a plain
    ``int`` and behaves as one in all arithmetic.

    Like the ``ₜᵢₘₑ`` rewriter this triggers only on a *string literal*
    LHS, so it cannot shadow the subscript-as-index rule for ordinary
    identifiers (``xᵣₒₘₑ`` still means ``x[rome]``).  Indexing a string
    literal with a bare name (``"foo"[rome]``) is a runtime TypeError in
    plain Python, so claiming this shape changes no working code.

    Runs while string contents are still masked by ``_protect_strings``
    — it matches the *shape* ``string + ᵣₒₘₑ``, not the contents, so the
    masking is transparent.  ``from_roman`` validates the contents at
    runtime and raises a clear ``ValueError`` on a malformed numeral.
    """
    return re.sub(
        # String literal immediately followed by the exact subscript
        # run ``ᵣ`` ``ₒ`` ``ₘ`` ``ₑ``.
        r'''((?:"[^"\n]*"|'[^'\n]*'))\s*ᵣₒₘₑ''',
        r'from_roman(\1)',
        source,
    )


# ---------- subscripted log: log₁₀, log₂ ----------

def rewrite_subscript_logs(source: str) -> str:
    """
    Rewrites function calls with a subscripted base into the appropriate
    Python form, BEFORE the generic subscript-as-index rule turns them
    into ``log[10]`` (which would index the ``log`` function itself).

        log₁₀(x)   -> log10(x)        (uses math.log10 — most accurate)
        log₂(x)    -> log2(x)         (uses math.log2 — most accurate)
        log₃(x)    -> math.log(x, 3)  (general formula)
        log₅(x)    -> math.log(x, 5)
        log₁₂(x)   -> math.log(x, 12)
        log_e(x)   -> still falls through to plain ``log_e`` if user wrote
                     it that way; ``log(x)`` itself remains the natural log.

    Bases 2 and 10 keep their dedicated functions (``math.log10`` and
    ``math.log2``) because they're significantly more accurate than the
    change-of-base formula ``log(x)/log(b)`` for those values.
    """
    def replace(m):
        base_str = subscript_to_ascii(m.group(1))
        try:
            base = int(base_str)
        except ValueError:
            # malformed subscript — leave it alone
            return m.group(0)
        if base == 2:
            return 'log2('
        if base == 10:
            return 'log10('
        # General base: math.log(x, base).  We emit the leading
        # ``math.log(`` and a placeholder; the closing paren and base
        # argument are appended by walking the source for the matching
        # close-paren below.
        # But that's awkward — instead we emit a wrapper that takes one
        # argument and applies math.log(_, base).  Cleanest: a small
        # closure-style pattern using a trailing ``, base)``.  We
        # accomplish this with a sentinel: emit ``math.log(`` and remember
        # we need to inject ``, {base})`` before the matching ``)``.
        # Implement via re.sub on the matched call site as a whole, using
        # a balanced-paren scan.
        return None  # signal: needs special handling

    # First, handle the easy log2/log10 cases with a simple substitution.
    source = re.sub(
        r'log([₀₁₂₃₄₅₆₇₈₉]+)\s*\(',
        lambda m: (
            f'log{subscript_to_ascii(m.group(1))}('
            if subscript_to_ascii(m.group(1)) in ('2', '10')
            else m.group(0)              # leave untouched for second pass
        ),
        source,
    )

    # Now handle the general case: log_b(arg) → math.log(arg, b).  We need
    # to find the matching closing paren of the call site and inject
    # ``, base`` just before it.  Do this by scanning the source.
    out = []
    i = 0
    n = len(source)
    log_call_re = re.compile(r'log([₀₁₂₃₄₅₆₇₈₉]+)\s*\(')

    while i < n:
        m = log_call_re.match(source, i)
        if m is None:
            out.append(source[i])
            i += 1
            continue

        base_str = subscript_to_ascii(m.group(1))
        try:
            base = int(base_str)
        except ValueError:
            out.append(source[i])
            i += 1
            continue

        # Find the matching closing paren.
        start = m.end()  # right after the '('
        depth = 1
        j = start
        while j < n and depth > 0:
            c = source[j]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
            j += 1

        if depth != 0:
            # Unbalanced — give up on this match.
            out.append(source[i])
            i += 1
            continue

        # source[start : j-1] is the argument; source[j-1] is the ')'.
        arg = source[start:j - 1]
        out.append(f'math.log({arg}, {base})')
        i = j

    return ''.join(out)


# ---------- ≈ approximately equal ----------

def rewrite_approx(source: str) -> str:
    """
    Rewrites:
        a ≈ b            -> approx(a, b)
        (a+b) ≈ c        -> approx((a+b), c)
        a ≈ (b+c)        -> approx(a, (b+c))
        f(x) ≈ g(y)      -> approx(f(x), g(y))
    """
    previous = None
    while source != previous:
        previous = source
        source = re.sub(
            rf'(?<![A-Za-z_]){_BINOP_RHS}\s*≈\s*{_BINOP_RHS}',
            lambda m: _wrap_binop('approx', '≈', m.group(0)),
            source,
        )
    return source


# ---------- list/array elementwise multiplication with units ----------

def rewrite_list_unit_multiply(source: str) -> str:
    """Rewrite ``[a, b, c] * X`` and ``X * [a, b, c]`` to use np.array().

    Plain Python lists don't multiply by scalars meaningfully (they only
    *replicate* on int multiplication), and ``forallpeople.Physical * list``
    raises a hard error rather than returning ``NotImplemented``.  But
    ``np.array * Physical`` already does the right thing — element-wise
    multiplication that yields an ndarray of Physicals.

    So whenever we see a list literal adjacent to a ``*``, wrap it in
    ``np.array(...)``::

        [1.2, 3.4, 34] * mV    →    np.array([1.2, 3.4, 34]) * mV
        mV * [1.2, 3.4, 34]    →    mV * np.array([1.2, 3.4, 34])

    The wrapping is cheap when the LHS already happens to be a numpy
    array (np.array of an ndarray is a no-op view), so this is safe even
    if the user wrote ``np.array([...]) * mV`` themselves.

    The rewriter walks the source skipping strings and comments, uses a
    bracket counter to find balanced ``[...]`` runs, and only wraps
    those that are followed by — or preceded by — a ``*``.  Empty lists
    and list comprehensions are left alone (a comprehension is rare on
    the LHS of ``*`` and would risk accidental double-wrapping).
    """
    out = []
    i = 0
    n = len(source)
    in_single = False
    in_double = False
    in_comment = False
    escape = False

    def is_comprehension(text):
        # crude but effective: a list comprehension contains a top-level `for`
        depth = 0
        in_s = False
        in_d = False
        esc = False
        j = 0
        while j < len(text):
            c = text[j]
            if esc:
                esc = False; j += 1; continue
            if c == "\\" and (in_s or in_d):
                esc = True; j += 1; continue
            if c == "'" and not in_d:
                in_s = not in_s; j += 1; continue
            if c == '"' and not in_s:
                in_d = not in_d; j += 1; continue
            if in_s or in_d:
                j += 1; continue
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif depth == 0 and c == " ":
                # check for "for" or "async for" at depth 0
                rest = text[j:]
                if rest.startswith(" for ") or rest.startswith(" async for "):
                    return True
            j += 1
        return False

    while i < n:
        ch = source[i]

        # ---- string and comment passthrough ----
        if in_comment:
            out.append(ch)
            if ch == "\n":
                in_comment = False
            i += 1
            continue
        if escape:
            escape = False
            out.append(ch)
            i += 1
            continue
        if ch == "\\" and (in_single or in_double):
            escape = True
            out.append(ch)
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            i += 1
            continue
        if in_single or in_double:
            out.append(ch)
            i += 1
            continue
        if ch == "#":
            in_comment = True
            out.append(ch)
            i += 1
            continue

        # ---- look for [ ... ] ----
        if ch == "[":
            # Find matching ]
            depth = 1
            j = i + 1
            j_in_s = j_in_d = j_esc = False
            while j < n and depth > 0:
                c = source[j]
                if j_esc:
                    j_esc = False; j += 1; continue
                if c == "\\" and (j_in_s or j_in_d):
                    j_esc = True; j += 1; continue
                if c == "'" and not j_in_d:
                    j_in_s = not j_in_s; j += 1; continue
                if c == '"' and not j_in_s:
                    j_in_d = not j_in_d; j += 1; continue
                if j_in_s or j_in_d:
                    j += 1; continue
                if c in "([{":
                    depth += 1
                elif c in ")]}":
                    depth -= 1
                j += 1
            if depth != 0:
                # unbalanced — give up on this one, write the [ literally
                out.append(ch)
                i += 1
                continue

            # j is now just past the closing ]
            list_text = source[i:j]                   # includes [...]
            inside = list_text[1:-1].strip()

            # Is this ``[`` actually a subscript?  A subscript has an
            # identifier or closing-paren/bracket character immediately
            # before it, with NO whitespace.  ``xs[0]`` and ``f()[0]`` and
            # ``a.b[0]`` are all subscripts; ``xs [0]`` and ``a + [0]``
            # would be (admittedly weird) list expressions.  Most relevant
            # to this rewriter: ``C₁`` was rewritten to ``C[1]`` by the
            # subscript-indices pass earlier in the pipeline, and that
            # ``[1]`` must NOT be misread as a list literal.
            is_subscript = (
                len(out) > 0
                and (out[-1].isalnum() or out[-1] in "_)]}πμΩεℏ")
            )

            # Is this preceded by *  (i.e., previous non-space chars are ` * `) ?
            #   pattern:  X * [ ... ]
            # Look backwards in `out` skipping spaces.
            k = len(out) - 1
            while k >= 0 and out[k] in (" ", "\t"):
                k -= 1
            preceded_by_star = (k >= 0 and out[k] == "*"
                                and (k == 0 or out[k - 1] != "*"))

            # Is this followed by *  (i.e., next non-space char in source is `*` not part of `**`) ?
            #   pattern:  [ ... ] * X
            kk = j
            while kk < n and source[kk] in (" ", "\t"):
                kk += 1
            followed_by_star = (
                kk < n and source[kk] == "*"
                and (kk + 1 >= n or source[kk + 1] != "*")
            )

            should_wrap = (
                (preceded_by_star or followed_by_star)
                and inside                               # not empty
                and not is_subscript                     # not foo[i]
                and not is_comprehension(inside)         # not a comprehension
            )

            if should_wrap:
                if preceded_by_star:
                    # Pattern was  X * [...]  — forallpeople's Physical
                    # cannot multiply with an ndarray on its right, so
                    # rewrite to  np.array([...]) * X  by deleting the
                    # earlier '*' and emitting a new '*' here.  Mult is
                    # commutative for the values we care about, so this
                    # change of order is semantically transparent.
                    #
                    # We only do this swap when the LHS of the '*' is a
                    # bare identifier — i.e. plausibly a unit name.  Any
                    # more complex LHS (a previously-wrapped list, a call,
                    # an arithmetic expression) is left in place: in those
                    # cases ``X * np.array([...])`` works fine because
                    # the LHS isn't a ``Physical`` to begin with.
                    star_idx = k          # index in `out` of the '*'
                    end_lhs = star_idx
                    while end_lhs > 0 and out[end_lhs - 1] in (" ", "\t"):
                        end_lhs -= 1
                    j2 = end_lhs - 1
                    # Walk back over identifier characters only.
                    while j2 >= 0 and (
                        out[j2].isalnum() or out[j2] in "_πμΩεℏ"
                    ):
                        j2 -= 1
                    start_lhs = j2 + 1

                    lhs_chars = "".join(out[start_lhs:end_lhs])
                    # Heuristic: only swap if LHS is a non-empty bare
                    # identifier with no leading dot (excludes things
                    # like ``a.foo`` partial captures).
                    is_bare_ident = (
                        lhs_chars
                        and not lhs_chars[0].isdigit()
                        and (start_lhs == 0 or out[start_lhs - 1] != ".")
                    )
                    if is_bare_ident:
                        del out[start_lhs:]
                        out.append(f"_CommaArray({list_text}) * {lhs_chars}")
                    else:
                        # leave the earlier '*' alone, just wrap and
                        # emit normally
                        out.append("_CommaArray(")
                        out.append(list_text)
                        out.append(")")
                else:
                    # Pattern was  [...] * X  — array already on LHS,
                    # just wrap.
                    out.append("_CommaArray(")
                    out.append(list_text)
                    out.append(")")
            else:
                out.append(list_text)
            i = j
            continue

        out.append(ch)
        i += 1

    return "".join(out)


# ---------- Knuth up-arrow as power operator ----------

def rewrite_uparrow_power(source: str) -> str:
    """
    Rewrites Knuth's up-arrow ``↑`` (U+2191) as Python's ``**`` power
    operator:

        x ↑ 2          -> x ** 2
        2 ↑ 64         -> 2 ** 64
        x ↑ (n + 1)    -> x ** (n + 1)
        x ↑ y ↑ z      -> x ** y ** z   (right-associative, like Python)
        f(t) ↑ 2       -> f(t) ** 2

    Implementation note: this is a literal character substitution.
    No operand parsing is needed because ``↑`` and ``**`` are
    operator tokens with the same Python precedence and associativity
    rules — Python's tokenizer/parser handles operand grouping after
    the substitution, so ``x ↑ y ↑ z`` correctly evaluates as
    ``x ** (y ** z)`` (right-associative), and ``a ↑ b * c`` correctly
    binds as ``(a ** b) * c`` (power binds tighter).

    String and comment bodies have already been masked by
    ``_protect_strings`` upstream, so ``↑`` inside a string literal
    is preserved verbatim.

    The rewriter does NOT support ``↑↑`` for Knuth tetration — the
    literal substitution would produce ``****`` which Python rejects
    as a syntax error.  That's the right outcome: tetration is rarely
    needed in engineering work, and silently giving it a different
    meaning would be a footgun.
    """
    return source.replace('↑', '**')


def rewrite_transpose_superscript(source: str) -> str:
    """Rewrite a trailing transpose superscript ``ᵀ`` as ``.T``::

        Mᵀ          -> (M).T
        (A*B)ᵀ      -> (A*B).T
        matᵀ        -> (mat).T

    ``ᵀ`` (U+1D40) on a matrix means transpose — sympy matrices expose
    ``.T`` for it.  This runs BEFORE ``rewrite_postfix_superscripts`` so
    the general power rewriter doesn't first turn ``Mᵀ`` into the
    meaningless ``M**T``.  Only a STANDALONE trailing ``ᵀ`` is treated as
    transpose; a ``ᵀ`` mixed into a larger superscript run (rare and
    ambiguous) is left for the power rewriter.

    The operand is an identifier (with optional subscript suffix), a
    parenthesised group, or a function call — found by walking back over
    balanced brackets, the same operand set the power/√ passes use.
    """
    GLYPH = "ᵀ"
    if GLYPH not in source:
        return source

    out = source
    # Iterate leftmost-first, rescanning, so multiple ``ᵀ`` resolve.
    while True:
        idx = out.find(GLYPH)
        if idx < 0:
            break
        # A standalone transpose: the char before ``ᵀ`` must NOT itself
        # be a superscript (else it's part of a run like ``x²ᵀ`` — leave
        # that to the power pass).  And the char after must not be a
        # superscript either.
        nxt = out[idx + 1] if idx + 1 < len(out) else ""
        SUPERS = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾˙ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿᵁⱽᵂ"
        prev = out[idx - 1] if idx > 0 else ""
        if (prev and prev in SUPERS) or (nxt and nxt in SUPERS):
            # Mixed run — skip this glyph, let the power pass handle it.
            # Replace temporarily to avoid an infinite loop, then restore.
            out = out[:idx] + "\u0000" + out[idx + 1:]
            continue
        # Walk back to find the operand start.
        j = idx - 1
        # Skip trailing spaces between operand and ``ᵀ``.
        while j >= 0 and out[j] in " \t":
            j -= 1
        end = j + 1
        if j >= 0 and out[j] in ")]}":
            # Balanced bracket walk back.
            depth = 0
            close = out[j]
            while j >= 0:
                c = out[j]
                if c in ")]}":
                    depth += 1
                elif c in "([{":
                    depth -= 1
                    if depth == 0:
                        break
                j -= 1
            start = j
            # Include a preceding function-name identifier, if any.
            k = start - 1
            while k >= 0 and (out[k].isalnum() or out[k] in "_."):
                k -= 1
            start = k + 1
        else:
            # Identifier (with subscript chars) or number.
            ident = SUBSCRIPT_CHARS + "\u0375"
            while j >= 0 and (out[j].isalnum() or out[j] in "_." or out[j] in ident):
                j -= 1
            start = j + 1
        operand = out[start:end]
        out = out[:start] + f"({operand}).T" + out[idx + 1:]

    # Restore any temporarily-masked ``ᵀ`` (mixed-run cases).
    out = out.replace("\u0000", GLYPH)
    return out


def rewrite_postfix_superscripts(source: str) -> str:
    """
    Rewrites postfix Unicode superscripts as Python power expressions.
    Any maximal run of superscript characters becomes the exponent,
    decoded character-by-character into its ASCII equivalent and wrapped
    in parentheses::

        x²              -> (x)**(2)
        x⁻¹             -> (x)**(-1)
        2⁶⁴             -> (2)**(64)
        (a+b)¹⁰         -> ((a+b))**(10)
        c⁻²             -> (c)**(-2)
        xⁿ              -> (x)**(n)
        eᵏ              -> (e)**(k)
        2⁽ⁿ⁺³⁾          -> (2)**((n+3))
        2⁽ⁿ⁺³⁾ⁱ         -> (2)**((n+3)i)
        xⁿ⁺¹            -> (x)**(n+1)
        2¹⁶⁻¹           -> (2)**(16-1)

    The decoded run is always wrapped in parens, so any structure inside
    the run — implicit multiplication (``(n+3)i``), arithmetic (``n+1``,
    ``16-1``), nested grouping — is preserved for the rest of the
    pipeline to handle.  In particular, the implicit-multiplication pass
    will turn ``(n+3)i`` into ``(n+3)*i`` downstream, so the user's
    natural ``2⁽ⁿ⁺³⁾ⁱ`` reads as ``2**((n+3)*i)`` at runtime.

    The LHS may be a parenthesised group, an identifier (with optional
    subscript suffix), a function call, or a numeric literal — the same
    operand set used by ``√`` and the other postfix passes.

    Earlier versions rejected mixed-character runs (``2¹⁶⁻¹``, ``xⁿ⁺¹``)
    on grounds of ambiguity, requiring users to fall back to ASCII for
    anything beyond a digit run or a single letter.  That ambiguity
    concern was overblown: a superscript run is visually a single
    contiguous unit, and reading it as one parenthesised expression is
    what mathematical typesetting has done for centuries.  The rule now
    is just "decode the run, wrap in parens" — predictable and uniform.
    """

    # ``˙`` (U+02D9) is the superscript decimal point — see the note in
    # SUPERSCRIPT_TRANS.  Including it here lets a run like ``⁰˙⁵⁵`` be
    # matched whole and decoded to ``0.55``.  It is deliberately NOT
    # added to the ``√`` rewriter's digit class: a root index must be a
    # positive integer.
    SUPER_DIGIT_OR_SIGN = r'⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾˙'
    SUPER_LETTER = r'ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿᵀᵁⱽᵂ'
    SUPER_RUN = rf'[{SUPER_DIGIT_OR_SIGN}{SUPER_LETTER}]+'
    SUPER_RUN_RE = re.compile(SUPER_RUN)

    def _format_super(lhs: str, sup: str) -> str:
        """Compute the replacement for ``<lhs><sup>``.  ``lhs`` is the
        operand text WITH any surrounding parens — we wrap an additional
        layer of parens unconditionally, matching the original behaviour
        of producing ``((a+b))**(2)`` (the extra layer is harmless and
        keeps unary-minus operands like ``(-x)²`` parsing cleanly).

        The decoded exponent is also wrapped in parens unconditionally.
        For a simple digit run this gives ``(2)**(2)`` where the outer
        parens are technically redundant — Python's parser handles them
        fine, and the uniform wrapping keeps the rule easy to reason
        about (no special-casing of "is this a simple positive integer?").
        """
        decoded = sup.translate(SUPERSCRIPT_TRANS)
        return f'({lhs})**({decoded})'

    def replace_with_paren(m):
        # Wrapper for the regex-driven bare-operand pass.
        lhs, sup = m.group(1), m.group(2)
        return _format_super(lhs, sup)

    # Paren-form pass: bracket-balanced matching for arbitrary depth.
    # Handles operands with parens — ``(a+b)²``, ``f(x, y)²``, etc. — by
    # walking back past the enclosing parens (and any preceding
    # function-name identifier) before emitting the rewrite.
    def _find_super_run(s, pos):
        m = SUPER_RUN_RE.search(s, pos)
        if not m:
            return None
        return (m.start(), m.end(), m.group(0))

    previous = None
    while source != previous:
        previous = source
        source = _postfix_paren_pass(source, find_op=_find_super_run,
                                     callback=_format_super)

    # Bare-operand pass — identifiers and numeric literals only (no
    # parens, so no nesting issue).  Keep the fixpoint loop in case the
    # bare-operand pass re-enables itself; with the new always-wrap
    # output rule that's unlikely, but harmless if so.
    previous = None
    while source != previous:
        previous = source
        source = re.sub(
            rf'({_BINOP_ATOM})\s*({SUPER_RUN})',
            replace_with_paren,
            source,
        )

    return source


def rewrite_prefix_sqrt(source: str) -> str:
    """
    Rewrites prefix-radical expressions:

        √x         -> sqrt(x)
        √5         -> sqrt(5)
        √(a+b)     -> sqrt((a+b))
        ³√x        -> (x)**(1/3)         (cube root)
        ⁴√16       -> (16)**(1/4)        (fourth root)
        ⁵√(a+b)    -> ((a+b))**(1/5)     (fifth root)
        ⁿ√x        -> (x)**(1/n)         (nth root, n is a name)
        ⁻¹√x       -> reserved, raises   (negative root makes no sense)

    The optional superscript prefix names the root index.  Without it,
    ``√`` is the square root.  With a superscript run before, ``ⁿ√`` is
    the nth root — where ``n`` may be a digit run (numeric index) or a
    single letter (an identifier holding the index).

    Bases 2 (default) and the special form ``√`` use the more accurate
    ``sqrt`` function from ``math``; other roots use ``x**(1/n)`` because
    Python's ``math`` module doesn't ship a generic nth-root function.

    Negative or zero numeric indices raise ``SyntaxError`` — ``⁰√x`` is
    undefined, and ``⁻²√x`` is more clearly written ``1/(²√x)`` if that's
    what's actually meant.
    """

    SUPER_DIGIT = r'[⁰¹²³⁴⁵⁶⁷⁸⁹]'
    SUPER_LETTER = r'[ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿᵀᵁⱽᵂ]'
    # Either a (possibly signed) digit run, or a single letter — same
    # shape as the postfix-superscript rule, no mixing.
    SUPER_RUN = rf'(?:(?:[⁺⁻]?{SUPER_DIGIT}+)|{SUPER_LETTER})'

    def _format_root(sup_text: str, operand: str) -> str:
        """Compute the replacement for ``<sup>√(<operand>)`` or
        ``<sup>√<bare-operand>``.  ``operand`` is the operand text
        WITHOUT surrounding parens — we wrap exactly one set of parens
        around it in the output, matching the original behaviour.
        """
        if not sup_text:
            return f'sqrt({operand})'

        # Letter-only superscript = name-as-index
        if re.fullmatch(SUPER_LETTER, sup_text):
            name = superscript_to_ascii(sup_text)
            return f'({operand})**(1/{name})'

        # Otherwise it's a digit run, possibly signed.
        if '⁻' in sup_text or '⁺' in sup_text:
            ascii_sup = superscript_to_ascii(sup_text)
            raise SyntaxError(
                f"signed root index {sup_text!r} in {sup_text}√{operand}: "
                f"only positive integer roots are supported. "
                f"Did you mean ``{operand}**(1/{ascii_sup.lstrip('+-')})`` or "
                f"``1/(²√{operand})``?"
            )
        n = int(superscript_to_ascii(sup_text))
        if n == 0:
            raise SyntaxError(
                f"zero root index in {sup_text}√{operand}: 0th roots are undefined"
            )
        if n == 1:
            # ¹√x is just x; no transformation needed but emit a no-op
            # for consistency.
            return f'({operand})'
        if n == 2:
            # Same as plain √ — use sqrt for precision.
            return f'sqrt({operand})'
        return f'({operand})**(1/{n})'

    def replace_nth_root(m):
        # Wrapper for the regex-driven bare-operand pass (operand without parens).
        sup_text, operand = m.group(1), m.group(2)
        return _format_root(sup_text, operand)

    # Paren-form pass: walk source forward looking for ``<sup>?√(``,
    # then use the balanced-paren helper to find the matching ``)``.
    # This handles arbitrarily nested operands like ``√(1+(3+2))`` and
    # ``√((1+2)*(3+4))`` that the regex-only approach can't.
    PAREN_PROBE = re.compile(rf'(?:({SUPER_RUN})\s*)?√\s*(?=\()')

    def _paren_form_pass(s: str) -> str:
        out = []
        pos = 0
        while pos < len(s):
            m = PAREN_PROBE.search(s, pos)
            if not m:
                out.append(s[pos:])
                break
            paren_start = m.end()  # PAREN_PROBE consumed up to but not including '('
            paren_end = _find_balanced_paren(s, paren_start, direction=+1)
            if paren_end == -1:
                # Unbalanced — keep original text, advance past the √
                out.append(s[pos:m.end()])
                pos = m.end()
                continue
            sup_text = m.group(1) or ''
            operand = s[paren_start + 1:paren_end]
            out.append(s[pos:m.start()])
            out.append(_format_root(sup_text, operand))
            pos = paren_end + 1
        return ''.join(out)

    # Run paren-form pass to fixpoint to handle nested cases like
    # ``√(√(1+2))`` — outer match is processed first, then re-scan
    # picks up the inner one (which is now exposed at top level).
    previous = None
    while source != previous:
        previous = source
        source = _paren_form_pass(source)

    # Bare-operand pass — unchanged.  Operands here are bare idents or
    # numbers, so no nested-paren bug to worry about.
    previous = None
    while source != previous:
        previous = source

        # ⁿ√name or ⁿ√number  — superscript-prefix root with bare operand
        source = re.sub(
            rf'({SUPER_RUN})\s*√\s*({_BINOP_ATOM})',
            replace_nth_root,
            source,
        )

        # √name or √number
        source = re.sub(
            rf'√\s*({_BINOP_ATOM})',
            r'sqrt(\1)',
            source
        )

    return source


def split_comment_outside_strings(line: str):
    in_single = False
    in_double = False
    escape = False

    for i, ch in enumerate(line):
        if escape:
            escape = False
            continue
        if ch == "\\" and (in_single or in_double):
            escape = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == "#" and not in_single and not in_double:
            return line[:i], line[i:]

    return line, ""


def find_top_level_assignment_ops(code: str):
    """
    Find top-level assignment-like operators outside strings and brackets:
        ≔
        :=
        ←   (also recognised, but written-out spelling — handles tuple
             unpacking, which := cannot, so it's rewritten to plain '=')
    Returns a list of (start, end, glyph) tuples.
    """
    spans = []
    depth = 0
    in_single = False
    in_double = False
    escape = False
    i = 0
    n = len(code)

    while i < n:
        ch = code[i]

        if escape:
            escape = False
            i += 1
            continue

        if ch == "\\" and (in_single or in_double):
            escape = True
            i += 1
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue

        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue

        if in_single or in_double:
            i += 1
            continue

        if ch in "([{":
            depth += 1
            i += 1
            continue

        if ch in ")]}":
            depth = max(depth - 1, 0)
            i += 1
            continue

        if depth == 0:
            if ch == "≔":
                spans.append((i, i + 1, "≔"))
                i += 1
                continue
            if ch == "←":
                spans.append((i, i + 1, "←"))
                i += 1
                continue
            if ch == ":" and i + 1 < n and code[i + 1] == "=":
                spans.append((i, i + 2, ":="))
                i += 2
                continue

        i += 1

    return spans


def replace_top_level_single_equals(code: str) -> str:
    """
    Rewrite bare ``=`` into ``==`` for use as a comparison operator,
    while preserving every position where Python uses ``=`` for its own
    syntactic purposes (assignment, kwargs, default parameters).

    Always leaves these alone:
        ==  !=  <=  >=  :=  +=  -=  *=  /=  //=  **=  %=  &=  |=  ^=  @=  >>=  <<=

    Two contexts where ``=`` becomes ``==``:

      1. **Top level** (depth == 0).  ``x = 5`` at statement level becomes
         ``x == 5``.  This is the math-style convention the toolkit chose:
         ``:=`` and ``←`` are the assignment operators; bare ``=`` is the
         equality operator.  An ordinary Python ``x = 5`` will be rewritten
         here, which is intentional — users who want assignment write
         ``x := 5``.

      2. **Inside ``(``, ``[``, ``{``** (depth >= 1) when the LHS of ``=``
         is NOT a bare identifier.  This is the case that lets you write
         ``print(0.1 + 0.2 = 0.3)`` and have it mean comparison instead
         of triggering Python's "expression cannot contain assignment"
         error.  A bare-identifier LHS (``f(x = 5)``) is preserved so that
         ordinary kwarg syntax keeps working.

    The "bare identifier" test is conservative — it accepts only
    ``[A-Za-z_]\\w*`` (with optional Greek/Unicode letter classes via
    ``str.isidentifier``).  Anything more complex — attribute access
    (``x.y``), indexing (``x[i]``), arithmetic (``a + b``), function
    calls (``f(x)``), literal numbers — is treated as a comparison LHS.
    Those forms are all Python errors when used as kwargs anyway, so
    rewriting them to ``==`` strictly turns errors into comparisons.

    Strings, comments, and tracked bracket depth are handled inline by
    the same single-pass scanner the function has always used.
    """
    out = []
    depth = 0
    in_single = False
    in_double = False
    escape = False
    i = 0
    n = len(code)

    # Sentinel positions where a comparison's LHS could begin: the start
    # of the line, or the most recent ``(``, ``[``, ``{``, or ``,``.  We
    # track this so we can extract the LHS text for the bare-identifier
    # test below — without it, a token-scan would have to walk back
    # character-by-character every time it saw an ``=``.
    lhs_start_stack = [0]

    def _is_kwarg_lhs(text: str) -> bool:
        """Return True if the ``=`` after ``text`` is in a position where
        Python uses ``=`` syntactically (so we must NOT rewrite to ``==``).

        Three cases qualify:

          1. **Bare identifier** — ``f(x=5)`` and ``def f(x=5):`` use
             ``=`` to bind a kwarg or default.  The LHS extracted is just
             ``x`` (or any Unicode-valid identifier).

          2. **Lambda parameter list** — ``(lambda x=5: x)`` puts a
             default on a lambda parameter.  The LHS extracted at the
             ``=`` is ``lambda x``: the keyword ``lambda`` is present and
             we haven't crossed the ``:`` that separates params from
             body.  If the ``:`` *is* in the LHS, we're in the lambda
             body (e.g. ``(lambda x: x = 5)`` — illegal Python that the
             user almost certainly meant as comparison) — in that case
             we DON'T preserve the ``=`` and let the rewriter convert it.

        Top-level annotated assignment (``x: int = 5``) and ``def`` lines
        with typed defaults are handled upstream by the line-prefix skip
        (``NO_EQ_REWRITE_PREFIXES``), so they never reach this check.
        """
        s = text.strip()
        # 1. Bare identifier (kwarg name or simple default-arg target).
        # ``str.isidentifier`` accepts Unicode letters — α, π, μ — exactly
        # matching what Python allows as a parameter name.
        if s.isidentifier():
            return True
        # 2. Lambda parameter list.  We're inside a lambda's params if the
        # word ``lambda`` appears AND no ``:`` has appeared after it yet
        # (the ``:`` is the param/body boundary).  ``split()`` keeps this
        # robust against false positives like an identifier ``lambdaX``.
        words = s.split()
        if 'lambda' in words:
            after_lambda = s[s.index('lambda'):]
            if ':' not in after_lambda:
                return True
        return False

    while i < n:
        ch = code[i]

        if escape:
            out.append(ch)
            escape = False
            i += 1
            continue

        if ch == "\\" and (in_single or in_double):
            out.append(ch)
            escape = True
            i += 1
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
            i += 1
            continue

        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            i += 1
            continue

        if in_single or in_double:
            out.append(ch)
            i += 1
            continue

        if ch in "([{":
            depth += 1
            out.append(ch)
            i += 1
            # New nesting level — a fresh LHS could start right after
            # the opening bracket.  Record the position in *out* so the
            # LHS slice we extract later is on the rewritten side, not
            # the source side (they can drift if we've already rewritten
            # something on the same line).
            lhs_start_stack.append(len(out))
            continue

        if ch in ")]}":
            depth = max(depth - 1, 0)
            out.append(ch)
            i += 1
            if len(lhs_start_stack) > 1:
                lhs_start_stack.pop()
            continue

        # A comma at depth >= 1 starts a fresh LHS for the next kwarg
        # or comparison: ``f(a=1, b=2)`` and ``f(x=5, p+q=r)`` both
        # need their second LHS evaluated independently.
        if ch == "," and depth >= 1:
            out.append(ch)
            i += 1
            lhs_start_stack[-1] = len(out)
            continue

        # A newline always resets the LHS marker, regardless of depth
        # (multi-line bracketed expressions still want fresh LHS
        # tracking on each physical line for readability).
        if ch == "\n":
            out.append(ch)
            i += 1
            lhs_start_stack[-1] = len(out)
            continue

        if ch == "=":
            prev = code[i - 1] if i > 0 else ""
            nxt = code[i + 1] if i + 1 < n else ""

            # Leave compound operators alone: == != <= >= := and the
            # augmented assigns += -= *= /= //= **= %= &= |= ^= @= >>= <<=.
            # The signal is the preceding character (turns a bare `=`
            # into a known multi-char operator) or a following `=`
            # (this `=` is the first half of `==`).
            is_compound = prev in "<>!=:+-*/%&|^@" or nxt == "="

            if not is_compound:
                if depth == 0:
                    # Top-level: always rewrite (math-style convention).
                    out.append("==")
                    i += 1
                    continue
                # Inside brackets: rewrite only if the LHS isn't a bare
                # identifier (i.e. not a kwarg / default-param target).
                lhs_text = "".join(out[lhs_start_stack[-1]:])
                if not _is_kwarg_lhs(lhs_text):
                    out.append("==")
                    i += 1
                    continue

        out.append(ch)
        i += 1

    return "".join(out)


# ---------- symbolic-math symbol declarations ----------

# Map from the bare RHS token (recognised at source level) to the kwargs
# we'll pass to ``sympy.symbols(...)``.  Add new constraint flavors here
# as the need arises.
_SYMBOL_DECL_KWARGS = {
    "symbols":          {},
    "positive_symbols": {"positive": True},
    "real_symbols":     {"real": True},
    "integer_symbols":  {"integer": True},
    "complex_symbols":  {"complex": True},
}


# Subscript characters need to be converted to underscore-prefixed ASCII
# for sympy.  ``SUBSCRIPT_TRANS`` is already defined above and maps each
# subscript char to its plain-letter/digit equivalent — we just have to
# decide where to insert the underscore.
def _sympy_symbol_name(ident: str) -> str:
    """Convert a DSL identifier to a sympy-friendly symbol name.

    The transformation is conservative — it only intervenes when the
    identifier contains subscript characters, and turns the subscript
    run into an underscored ASCII suffix.  Greek letters and other
    Unicode pass through unchanged because sympy renders them just fine.

    >>> _sympy_symbol_name("x")        # 'x'
    >>> _sympy_symbol_name("x₁")       # 'x_1'
    >>> _sympy_symbol_name("α")        # 'α'
    >>> _sympy_symbol_name("V_out")    # 'V_out'
    """
    # Find the first subscript character; everything before it is the
    # base name, everything from it onwards is the subscript run.
    for i, ch in enumerate(ident):
        if ch in SUBSCRIPT_CHARS or ch in 'ₐₑₒₓₔₕₖₗₘₙₚₛₜ':
            base = ident[:i]
            sub = subscript_to_ascii(ident[i:])
            # Avoid ``_-1`` and ``_+2`` style suffixes — sympy's parser
            # is happier with bare integers and bare letters.  Strip
            # any leading sign from a numeric subscript.
            sub = sub.lstrip('+').replace('-', 'neg')
            return f"{base}_{sub}" if base else sub
    return ident


# A single symbol-declaration target item: either a normal ``TARGET`` or
# a name range ``atom..atom`` (``x..z``, ``R1..R4``).  The range form is
# only recognised in symbol declarations — that's why it's a separate
# pattern from the shared ``TARGET`` rather than folded into it.
SYMBOL_DECL_TARGET = rf'(?:{TARGET_ATOM}\s*(?<!\.)\.\.(?!\.)\s*{TARGET_ATOM}|{TARGET})'


def _expand_symbol_targets(targets_text: str) -> list:
    """Split a symbol-declaration target list at top-level commas and
    expand any ``a..b`` range item into its sequence of names.

    Each comma-separated item is either a plain target (``n``, ``α``,
    ``R1``, ``x₁``) or a *name range* (``x..z``, ``R1..R4``).  A range is
    expanded with the same shape rules as the string-literal range
    helper :func:`_str_range` — single letters (incl. Greek) sequence by
    code point, and a ``prefix+number`` tail increments the number with
    zero-padding preserved only when the start is padded.

    >>> _expand_symbol_targets("x..z, n, k, α, β, R1..R4, U1")
    ['x', 'y', 'z', 'n', 'k', 'α', 'β', 'R1', 'R2', 'R3', 'R4', 'U1']

    A malformed range endpoint (e.g. a two-letter name like ``aa..az``)
    raises ``ValueError`` via ``_str_range`` — multi-letter sequencing is
    deliberately not supported.
    """
    out = []
    for item in targets_text.split(','):
        item = item.strip()
        if not item:
            continue
        # ``..`` between two name atoms → expand as a range.  Exactly two
        # dots (not an attribute access ``a.b`` and not an ellipsis).
        m = re.fullmatch(r'(.+?)\s*(?<!\.)\.\.(?!\.)\s*(.+)', item)
        if m:
            out.extend(_str_range(m.group(1).strip(), m.group(2).strip()))
        else:
            out.append(item)
    return out


# Match a whole-line declaration of the form
#   <targets>  :=  <bare token>
# where the targets are an identifier or a comma-separated list of
# identifiers, and the bare token is one of the keys in
# ``_SYMBOL_DECL_KWARGS``.  Any leading indentation is preserved.
_SYMBOL_DECL_RE = re.compile(
    rf'^(\s*)'                                          # indent
    rf'({SYMBOL_DECL_TARGET}(?:\s*,\s*{SYMBOL_DECL_TARGET})*)'  # targets (one or more, ranges allowed)
    rf'\s*(?::=|≔|←)\s*'                                # assignment glyph
    rf'(' + '|'.join(_SYMBOL_DECL_KWARGS) + r')'        # one of the recognised tokens
    rf'\s*$',                                           # nothing after
    flags=re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Prefix-keyword form — companion to the postfix ``:= symbols`` form above.
# ---------------------------------------------------------------------------
# Same five recognised declarations, written with the keyword first instead
# of last:
#
#     symbols: x, y, z              ≡  x, y, z := symbols
#     positive_symbols: n, k        ≡  n, k    := positive_symbols
#     real_symbols: α, β            ≡  α, β    := real_symbols
#
# Two notations cover two reading flows: the postfix form reads as
# "these names are: symbols", which fits naturally into the toolkit's
# right-arrow assignment style.  The prefix form reads as "declare the
# following symbols: x, y, z", which feels more declarative and is
# closer to how mathematicians typically introduce variables on paper.
# Both are first-class — pick whichever fits the cell you're writing.
_SYMBOL_DECL_PREFIX_RE = re.compile(
    rf'^(\s*)'                                          # indent
    rf'(' + '|'.join(_SYMBOL_DECL_KWARGS) + r')'        # keyword (e.g. "symbols")
    rf'\s*:\s*'                                         # the colon
    rf'({SYMBOL_DECL_TARGET}(?:\s*,\s*{SYMBOL_DECL_TARGET})*)'  # one or more targets (ranges allowed)
    rf'\s*$',                                           # nothing after
    flags=re.MULTILINE,
)


def rewrite_symbol_declaration_prefix(source: str) -> str:
    """
    Recognise prefix-keyword symbol declarations and rewrite them into
    plain Python tuple-assignments calling ``sym.symbols(...)``.

    Examples::

        symbols: x, y, z              ->  x, y, z = sym.symbols('x y z')
        symbols: n                    ->  n = sym.symbols('n')
        positive_symbols: n, k        ->  n, k = sym.symbols('n k', positive=True)
        real_symbols: α, β            ->  α, β = sym.symbols('α β', real=True)
        integer_symbols: i, j         ->  i, j = sym.symbols('i j', integer=True)

    The list after the colon is split at top-level commas; each name is
    converted to a sympy-friendly form by ``_sympy_symbol_name`` (so
    ``x₁`` becomes ``x_1`` on both the Python LHS and inside the sympy
    name string).  The declaration must occupy a whole line — no
    trailing expression, no trailing colon-suite.

    Five keywords are recognised, matching the postfix form:
    ``symbols``, ``positive_symbols``, ``real_symbols``,
    ``integer_symbols``, ``complex_symbols``.  Each maps to the
    corresponding ``sym.symbols(..., kwarg=True)`` call.

    The rule "must occupy a whole line" is what disambiguates this
    rewrite from ordinary uses of the keywords.  Lines like
    ``ans = symbols('x y z')`` (a regular function call), or
    ``symbols: see paper`` (a comment-style annotation in a docstring),
    don't match because the regex requires the trailing target list to
    be a comma-separated list of identifier-shaped targets.

    Single-name declarations work — sympy's ``symbols('x')`` returns a
    single Symbol (not a 1-tuple) so ``symbols: x → x = sym.symbols('x')``
    is correctly an assignment of a Symbol to a single name.
    """
    def replace(m):
        indent, token, targets_text = m.group(1), m.group(2), m.group(3)
        targets = _expand_symbol_targets(targets_text)

        # Same ASCII-translation policy as the postfix form so that
        # ``symbols: x₁, x₂`` and ``x₁, x₂ := symbols`` produce the
        # SAME Python output.  Users mixing the two notations across
        # cells get consistent behaviour.
        ascii_targets = [_sympy_symbol_name(t) for t in targets]
        lhs = ", ".join(ascii_targets)
        names = " ".join(ascii_targets)

        kwargs = _SYMBOL_DECL_KWARGS[token]
        if kwargs:
            kwargs_text = ", " + ", ".join(f"{k}=True" for k in kwargs)
        else:
            kwargs_text = ""

        # Emit ``:=`` rather than ``=`` for the same reason as the
        # postfix-form rewriter: the toolkit's bare-equality pass
        # rewrites top-level ``=`` to ``==`` (math-style comparison),
        # which would mangle our output.  ``:=`` gets recognised as an
        # assignment glyph by ``rewrite_math_assignment`` later in the
        # pipeline, which emits the plain ``=`` Python wants — same
        # final result, just routed through the established channel.
        return f"{indent}{lhs} := sym.symbols('{names}'{kwargs_text})"

    return _SYMBOL_DECL_PREFIX_RE.sub(replace, source)


def rewrite_symbol_declaration(source: str) -> str:
    """
    Recognise the symbolic-math symbol-declaration shorthand and expand
    it into a real sympy.symbols(...) call:

        x, y, z := symbols           -> x, y, z = sym.symbols('x y z')
        n, k := positive_symbols     -> n, k = sym.symbols('n k', positive=True)
        α := real_symbols            -> α = sym.symbols('α', real=True)
        x₁, x₂ := symbols            -> x_1, x_2 = sym.symbols('x_1 x_2')

    The trigger is a bare RHS token (no parens, no string list).  This
    runs *before* ``rewrite_math_assignment`` so the ``:=`` is intact;
    the output uses plain ``=`` so the math-assignment pass leaves the
    rewritten line alone.

    Any other use of ``symbols``, ``positive_symbols`` etc. on the RHS
    is left alone — for example ``ans := symbols(...)`` (a regular
    sympy call) doesn't match because the RHS isn't bare.
    """

    def replace(m):
        indent, targets_text, token = m.group(1), m.group(2), m.group(3)

        # Split the targets at top-level commas AND expand any ``a..b``
        # name range (``x..z``, ``R1..R4``) into its sequence — see
        # ``_expand_symbol_targets``.
        targets = _expand_symbol_targets(targets_text)

        # We use ASCII-translated names on BOTH the Python LHS and the
        # sympy ``symbols(...)`` string.  This is the cleanest option:
        # ``x₁`` becomes the Python variable ``x_1`` (a normal identifier
        # that Python and sympy both render the same way), and the sympy
        # symbol name matches.  This means a later ``x₁`` reference in
        # the user's code STILL goes through the subscript-as-index
        # rewriter and gets rewritten to ``x[1]`` — that's a known
        # consequence of the math-DSL/symbolic-DSL interaction and is
        # documented in ``utils/symbolic.py``.  Workaround: spell the
        # symbol with an underscore (``x_1``) in subsequent uses too.
        ascii_targets = [_sympy_symbol_name(t) for t in targets]

        lhs = ", ".join(ascii_targets)
        names = " ".join(ascii_targets)

        kwargs = _SYMBOL_DECL_KWARGS[token]
        if kwargs:
            kwargs_text = ", " + ", ".join(f"{k}=True" for k in kwargs)
        else:
            kwargs_text = ""

        # Emit ``:=`` so the next pass (rewrite_math_assignment) handles
        # the conversion to ``=``.  Emitting ``=`` directly here doesn't
        # work because math_assignment unconditionally rewrites bare ``=``
        # to ``==``; using ``:=`` flows through cleanly because
        # math_assignment recognises it as an assignment glyph and emits
        # plain ``=``.
        return f"{indent}{lhs} := sym.symbols('{names}'{kwargs_text})"

    return _SYMBOL_DECL_RE.sub(replace, source)


def rewrite_math_assignment(source: str) -> str:
    """
    Assignment glyphs:
        x ≔ 5        -> x = 5
        x := 5       -> x = 5
        a, b := f()  -> a, b = f()

    Expression equality:
        x = 5        -> x == 5
        if x = y:    -> if x == y:

    Conservative choice:
    - ≔ and := are treated as assignment only when the line begins with an
      assignable target list.
    - otherwise := is left alone, so Python walrus still exists in non-assignment
      contexts such as: if (x := f()) > 0:

    The function processes the source line-by-line for the
    assignment-target check (which is inherently per-line — a multi-line
    statement that's also an assignment is rare and a one-liner-at-the-
    start fits ``TARGET_LIST_RE`` even with a trailing multi-line value).
    But for the bare-equals-to-double-equals rewrite, we need bracket
    depth tracked ACROSS lines so that a multi-line ``plot(...)`` call
    with ``title="foo"`` on a continuation line correctly treats
    ``title=`` as a kwarg (depth > 0) rather than a top-level
    comparison.  We do this by accumulating "non-assignment" lines into
    a single buffer and flushing it through ``replace_top_level_single_equals``
    whenever bracket depth returns to 0.
    """
    new_lines = []
    # Buffer for accumulating lines of a multi-line non-assignment
    # statement.  We flush when bracket depth reaches zero so
    # ``replace_top_level_single_equals`` sees the whole statement.
    pending_buffer = []
    pending_depth = 0

    def _depth_after(text: str) -> int:
        """Net bracket-depth change introduced by ``text``, ignoring
        strings and comments.  Positive means more opens than closes.
        Used to decide when a multi-line statement has finished."""
        depth = 0
        in_single = in_double = False
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\\" and (in_single or in_double):
                i += 2
                continue
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif not in_single and not in_double:
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == "#":
                    break  # rest is comment
            i += 1
        return depth

    def _flush_pending():
        if pending_buffer:
            combined = "".join(pending_buffer)
            rewritten = replace_top_level_single_equals(combined)
            new_lines.append(rewritten)
            pending_buffer.clear()

    for line in source.splitlines(keepends=True):
        code, comment = split_comment_outside_strings(line)
        stripped = code.lstrip()

        # If we're already inside a multi-line statement, this line
        # must continue it; don't try to start a new assignment scan.
        if pending_depth > 0:
            pending_buffer.append(code + comment)
            pending_depth += _depth_after(code)
            if pending_depth <= 0:
                pending_depth = 0
                _flush_pending()
            continue

        if stripped.startswith(NO_EQ_REWRITE_PREFIXES):
            _flush_pending()
            new_lines.append(code + comment)
            continue

        spans = find_top_level_assignment_ops(code)

        if spans:
            first_start, _first_end, _glyph = spans[0]
            lhs = code[:first_start]

            if TARGET_LIST_RE.match(lhs):
                # Reconstruct: each top-level :=/≔/← becomes a plain '=',
                # producing the LHS-side prefix.  Anything after the LAST
                # such glyph is the RHS of the assignment — that part
                # still needs ``replace_top_level_single_equals`` so any
                # bare '=' it contains (typically inside parens, written
                # as a math-style equality check) becomes '=='.  Skipping
                # it here meant lines like ``result := (a + b = c)``
                # never got the inner '=' rewritten and crashed at parse
                # time with "cannot assign to expression".
                _flush_pending()
                pieces = []
                last = 0
                for start, end, _glyph in spans:
                    pieces.append(code[last:start])
                    pieces.append("=")
                    last = end
                prefix = "".join(pieces)        # ends with the last assignment '='
                suffix = code[last:]            # everything to its right
                suffix = replace_top_level_single_equals(suffix)
                code = prefix + suffix

                new_lines.append(code + comment)
                continue

        # Not an assignment.  Check whether this line opens a multi-line
        # statement (unbalanced brackets).  If so, accumulate; otherwise
        # rewrite immediately.
        line_depth = _depth_after(code)
        if line_depth > 0:
            pending_buffer.append(code + comment)
            pending_depth = line_depth
        else:
            _flush_pending()
            code = replace_top_level_single_equals(code)
            new_lines.append(code + comment)

    _flush_pending()

    result = "".join(new_lines)
    # Any ← still surviving is inside a parenthesised expression — that means
    # it's a keyword argument like ``f(label ← 'foo')`` or a walrus-style
    # target inside a comprehension's `if`.  In Python both want a plain ``=``,
    # so rewrite unconditionally now (skipping strings).
    result = _replace_arrow_in_parens(result)
    return result


def _replace_arrow_in_parens(code: str) -> str:
    """Rewrite ``←`` to ``=`` everywhere except inside strings and comments.

    By the time this runs, any top-level ``←`` should already have been
    consumed by ``rewrite_math_assignment``; what's left is in argument
    lists or other parenthesised contexts where ``=`` is the correct
    Python form (keyword argument, default value, etc.).
    """
    out = []
    in_single = False
    in_double = False
    in_comment = False
    escape = False

    for ch in code:
        if in_comment:
            out.append(ch)
            if ch == "\n":
                in_comment = False
            continue
        if escape:
            escape = False
            out.append(ch)
            continue
        if ch == "\\" and (in_single or in_double):
            escape = True
            out.append(ch)
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            continue
        if in_single or in_double:
            out.append(ch)
            continue
        if ch == "#":
            in_comment = True
            out.append(ch)
            continue
        if ch == "←":
            out.append("=")
            continue
        out.append(ch)

    return "".join(out)


# ---------- ⇥ and ↵ glyph rewriter ----------

# Visual variants accepted as "tab" and "newline" markers.  More than one
# codepoint is included for each role so that:
#   1. Users with old sources written using the original ``⇥``/``↵`` keep
#      working without modification.
#   2. UI palettes can pick whichever glyph renders well in the chosen
#      font — Segoe UI Variable on Windows doesn't carry a glyph for
#      ``↵`` (U+21B5) on current builds, so the palette may insert
#      ``↩`` (U+21A9) or ``⏎`` (U+23CE) instead.  Either way the DSL
#      treats it the same.
#
# Note that whichever character ends up in the source has to also be a
# character the user can comfortably type or paste — the original ``↵``
# was chosen for its descriptive name and clean visual; ``⏎`` matches
# the "Return" key symbol used by Apple and modern editors.  Aliases
# are inclusive, not prescriptive.
_TAB_GLYPHS = frozenset({
    "⇥",      # U+21E5 RIGHTWARDS ARROW TO BAR — the original choice
    "⭾",      # U+2B7E HORIZONTAL TAB KEY — explicit "tab" semantics
})
_NEWLINE_GLYPHS = frozenset({
    "↵",      # U+21B5 DOWNWARDS ARROW WITH CORNER LEFTWARDS — original choice
    "⏎",      # U+23CE RETURN SYMBOL — the Unicode name says it all
    "↩",      # U+21A9 LEFTWARDS ARROW WITH HOOK — keyboard-cap style
    "⮐",      # U+2B90 RETURN LEFT — modern UI variant
})


def rewrite_tab_newline_chars(source: str) -> str:
    """
    Translates two arrow glyphs into Python equivalents, with a different
    rule depending on whether they appear inside or outside a string
    literal.

    Outside string literals — used as a quick way to interleave tabs or
    newlines into a function-argument list::

        ⇥        →   , "\\t",         (tab as a separate argument)
        ↵        →   , "\\n",         (newline as a separate argument)

    Example::

        print(⇥ b ⇥ 2b/(x‖y‖z))
            →   print("\\t", b, "\\t", 2b/(x‖y‖z))

    Inside (non-raw) string literals — used as a visually-distinct way
    to spell tab/newline escapes::

        ⇥        →   \\t              (escape sequence: tab)
        ↵        →   \\n              (escape sequence: newline)

    Example::

        "row 1⇥col 2"   →   "row 1\\tcol 2"

    Inside RAW strings (``r"..."`` / ``R"..."``) the glyphs are left
    unchanged.  In a raw string the escape-sequence form would be a
    literal two-character ``\\t`` rather than a tab; nobody writes
    ``r"⇥"`` expecting that, so we leave the arrow alone — it shows up
    as a literal U+21E5 in the resulting raw string, which the user
    can fix by removing the ``r`` prefix.

    Comma elision: the outside-string form is ``, "\\t",`` by default,
    but the LEADING comma is elided whenever it would produce invalid
    syntax — that is, when the most recent non-whitespace character in
    the output is an opener (``(``, ``[``, ``{``), a separator (``,``),
    or doesn't exist yet (start of source).  The trailing comma is
    always emitted; Python accepts trailing commas in calls/tuples/lists
    so this is always safe.  Concretely::

        f(⇥ b)         →   f("\\t", b)              # after ( — no leading comma
        f(a, ⇥ b)      →   f(a, "\\t", b)           # after , — no leading comma
        f(a ⇥ b)       →   f(a, "\\t", b)           # mid-args — both commas
        f(⇥)           →   f("\\t",)                # trailing OK
        f(⇥ ⇥ b)       →   f("\\t", "\\t", b)       # adjacent OK
        [⇥ x ⇥ y]      →   ["\\t", x, "\\t", y]     # works for [, { too

    Limitations:

      - Inside an f-string interpolation (``f"…{⇥}…"``), the glyph is
        treated as inside-string and translated to ``\\t``, but Python
        rejects backslash escapes in f-string code regions.  Don't use
        ``⇥``/``↵`` inside f-string ``{…}`` braces; write the regular
        Python form there.
      - The pass is character-scan-based (not full Python tokenisation),
        so a very pathological nested-string situation could in principle
        confuse it.  Standard single/double/triple quoting with proper
        escapes works fine.
    """
    result = []
    last_nonws = None  # most recent non-whitespace char appended to result

    def emit(text):
        # Append ``text`` and refresh ``last_nonws`` from the right.
        nonlocal last_nonws
        result.append(text)
        for c in reversed(text):
            if not c.isspace():
                last_nonws = c
                return

    def emit_separator(escape_text):
        # Outside-string emission of a glyph.  Elides the leading
        # comma when context makes it invalid; trailing comma is
        # always safe (Python accepts trailing commas).
        if last_nonws is None or last_nonws in "([{,":
            emit(f'"\\{escape_text}", ')
        else:
            emit(f', "\\{escape_text}", ')

    i = 0
    n = len(source)
    in_str = None       # None, or one of "'", '"', "'''", '"""'
    is_raw = False
    in_comment = False

    while i < n:
        ch = source[i]

        # Comments — leave their bodies alone.
        if in_comment:
            if ch == '\n':
                in_comment = False
            emit(ch)
            i += 1
            continue

        # Inside a string literal.
        if in_str is not None:
            # Backslash-escape consumes the next character verbatim
            # in non-raw strings.  Raw strings don't process escapes
            # at all, but neither do we — they just pass through.
            if ch == '\\' and not is_raw:
                emit(ch)
                i += 1
                if i < n:
                    emit(source[i])
                    i += 1
                continue

            # String termination check.  Triple-quoted strings must
            # be checked before single-quoted to avoid splitting a
            # triple delimiter into two single quotes.
            if in_str in ("'''", '"""'):
                if i + 3 <= n and source[i:i + 3] == in_str:
                    emit(in_str)
                    i += 3
                    in_str = None
                    is_raw = False
                    continue
            elif ch == in_str:
                emit(ch)
                i += 1
                in_str = None
                is_raw = False
                continue

            # In-string glyph translation — only for non-raw strings.
            if not is_raw:
                if ch in _TAB_GLYPHS:
                    emit('\\t')
                    i += 1
                    continue
                if ch in _NEWLINE_GLYPHS:
                    emit('\\n')
                    i += 1
                    continue

            emit(ch)
            i += 1
            continue

        # Outside any string or comment.
        if ch == '#':
            in_comment = True
            emit(ch)
            i += 1
            continue

        # String start.  Triple-quote takes precedence over single.
        if ch == '"' or ch == "'":
            if i + 3 <= n and source[i:i + 3] == ch * 3:
                in_str = source[i:i + 3]
                is_raw = _is_raw_prefix(source, i)
                emit(in_str)
                i += 3
                continue
            in_str = ch
            is_raw = _is_raw_prefix(source, i)
            emit(ch)
            i += 1
            continue

        # The glyph variants we care about — outside-string form.
        if ch in _TAB_GLYPHS:
            emit_separator('t')
            i += 1
            continue
        if ch in _NEWLINE_GLYPHS:
            emit_separator('n')
            i += 1
            continue

        emit(ch)
        i += 1

    return ''.join(result)


def _is_raw_prefix(source: str, quote_pos: int) -> bool:
    """Return True if the quote at ``source[quote_pos]`` is part of a
    raw-string literal (``r"…"``, ``rb"…"``, ``Rf"…"`` etc.).

    Walks backward from ``quote_pos - 1`` over the string-literal prefix
    letters (``r`` / ``R`` / ``b`` / ``B`` / ``f`` / ``F``) and returns
    True if any of them is an ``r`` / ``R``.  The caller is expected to
    only invoke this at a real string boundary, so we don't need to
    verify that the prefix isn't part of a longer identifier — Python
    would reject that as a syntax error anyway and we'll have produced
    the right answer for valid input.
    """
    j = quote_pos - 1
    while j >= 0 and source[j] in 'rRbBfF':
        if source[j] in 'rR':
            return True
        j -= 1
    return False


# ---------- implicit multiplication ----------

# ---------- subscript/superscript-bearing constant names ----------
#
# These identifiers contain Unicode subscript/superscript characters
# that would normally be decomposed by ``rewrite_subscript_indices``
# (e.g. ``εₒ`` → ``ε[o]``) or ``rewrite_postfix_superscripts``
# (e.g. ``Nᴬ`` → ``N**A``).  But the toolkit's physical-constants
# module exposes them as whole-name identifiers in user namespace,
# so the rewriters must leave them alone.
#
# Mechanism: before the subscript / superscript / iso / power passes
# run, we replace each occurrence of these names with an opaque
# placeholder.  After the rewrites finish, the placeholders are
# substituted back to their original Unicode form.  The placeholders
# are pure-ASCII identifiers so no later pass tries to "fix" them.
#
# To register a new whole-name constant, add it to this set.  The
# rewriters' negative-lookbehind / lookahead heuristics aren't the
# right tool here because the names mix base letters with subscript
# letters; pre-stashing is much more robust.
_WHOLE_NAME_CONSTANTS = (
    # Vacuum permittivity, permeability — Greek base + subscript-o
    "εₒ", "μₒ",
    # Particle masses — Latin base + subscript letter
    "mₑ", "mₚ", "mₙ",
    # Boltzmann (subscript B), elementary charge (subscript e)
    "kᵦ", "qₑ",
    # Gas constant — modifier letters "ᵍᵃˢ" spell "gas" in superscript.
    # The "s" is U+02E2 (MODIFIER LETTER SMALL S), NFKC-normalized to
    # plain "s" — earlier drafts used U+1DB3 (s WITH HOOK) which
    # normalizes to "ʂ" (a non-ASCII character that's not in the DSL's
    # identifier regex).  Stick with U+02E2 for clean normalization.
    "Rᵍᵃˢ",
    # Avogadro — superscript A
    "Nᴬ",
    # Standard gravity, ice point.  Two variants of standard gravity:
    # ``gₙ`` (subscript-n, matching the older ``g_n`` spelling) and
    # ``gₒ`` (subscript-o, matching the modern ``g_0`` spelling).  Both
    # point to the same value via aliasing in calc_symbols.py.  Users
    # whose mental model is "g sub zero" naturally type ``gₒ``, which
    # would otherwise be decomposed by the subscript-as-index rewriter
    # into ``g[o]`` and fail at runtime with ``NameError: name 'o'``.
    "gₙ", "gₒ", "Tₒ",
)


def _protect_constant_names(source: str):
    """Replace each occurrence of a whole-name constant (subscript-/
    superscript-bearing) with an ASCII placeholder so the rewriters
    don't decompose them.  Returns ``(masked, replacements)`` —
    ``replacements`` is a list of (placeholder, original) pairs to
    pass back to ``_restore_constant_names``.
    """
    replacements = []
    for i, name in enumerate(_WHOLE_NAME_CONSTANTS):
        placeholder = f"__dsl_const_{i}__"
        if name in source:
            source = source.replace(name, placeholder)
            replacements.append((placeholder, name))
    return source, replacements


def _restore_constant_names(source: str, replacements):
    """Inverse of ``_protect_constant_names``."""
    for placeholder, original in replacements:
        source = source.replace(placeholder, original)
    return source


def _protect_strings(source: str):
    """Mask string-literal *contents* with a placeholder so the regex-based
    source rewriters don't dig inside string bodies and mangle them.

    The DSL rewriters operate on raw source text and most of them aren't
    string-aware.  That's normally fine (DSL syntax doesn't usually appear
    inside strings), but ISO 8601 timestamps like ``"2026-05-05T09:00:00"``
    contain ``05T09`` which the resistor-notation rewriter would happily
    chew into ``(05.09*prefix_T)``.

    This helper walks the source character-by-character, tracking whether
    we're inside a single-quoted, double-quoted, or triple-quoted string,
    and replaces each string body with an opaque placeholder of the form
    ``__dsl_str_{N}__``.  The quote characters around the body are kept so
    the source still tokenises correctly.

    Returns ``(masked_source, contents)`` where ``contents`` is a list of
    the original string bodies in match order — pass it to
    ``_restore_strings`` to put them back.

    The placeholder body is intentionally constructed from word characters
    only and starts with an underscore.  That guards against the various
    regex rewriters which use ``(?<!\\w)`` lookbehinds: every ``\\d+``
    inside the placeholder is preceded by an underscore, so resistor
    notation (and every similar leading-non-word-anchor rule) skips it.
    """
    bodies = []
    out = []
    i = 0
    n = len(source)

    while i < n:
        # Triple-quoted strings — check before single/double so we don't
        # match the leading quote of ``"""`` as a single-quoted string.
        if source[i:i + 3] in ('"""', "'''"):
            quote = source[i:i + 3]
            j = source.find(quote, i + 3)
            if j == -1:
                # Unterminated — preserve as-is and stop scanning.
                out.append(source[i:])
                break
            body = source[i + 3:j]
            idx = len(bodies)
            bodies.append(body)
            out.append(f"{quote}__dsl_str_{idx}__{quote}")
            i = j + 3
            continue

        ch = source[i]

        # Single-line string literals.  Walk until we hit the matching
        # quote, respecting escapes; bail out at newline (Python doesn't
        # allow unescaped newlines inside ``"..."`` or ``'...'``).
        if ch == '"' or ch == "'":
            quote = ch
            j = i + 1
            while j < n:
                c = source[j]
                if c == '\\' and j + 1 < n:
                    j += 2
                    continue
                if c == quote or c == '\n':
                    break
                j += 1
            if j >= n or source[j] != quote:
                # Unterminated single-line string — leave the source alone.
                out.append(ch)
                i += 1
                continue
            body = source[i + 1:j]
            idx = len(bodies)
            bodies.append(body)
            out.append(f"{quote}__dsl_str_{idx}__{quote}")
            i = j + 1
            continue

        # Comments — protect the body too, in case it contains DSL-y text.
        if ch == '#':
            j = source.find('\n', i)
            if j == -1:
                j = n
            body = source[i + 1:j]
            idx = len(bodies)
            bodies.append(body)
            out.append(f"#__dsl_str_{idx}__")
            i = j
            continue

        out.append(ch)
        i += 1

    return ''.join(out), bodies


_PROTECT_STR_RE = re.compile(r'__dsl_str_(\d+)__')


def _restore_strings(source: str, bodies):
    """Inverse of ``_protect_strings``.  Substitute each placeholder with
    the original string body."""
    return _PROTECT_STR_RE.sub(lambda m: bodies[int(m.group(1))], source)


# Modules in this package that are plain Python (no DSL syntax) and
# must NOT be transformed by the import hook.  These modules contain
# bare ``=`` assignments that the math-assignment rewriter would turn
# into ``==`` comparisons, breaking the module.  Listed by basename so
# the check is path-agnostic — works whether the user installed the
# package at ``/home/claude/utils/`` or some other location.
_PLAIN_PYTHON_SIBLINGS = frozenset({
    "chrono.py",
    "symbolic.py",
    "iso286.py",
    "radix_formats.py",
    # Add other plain-Python siblings here as the project grows.
})


def _check_bitwise_andor(source: str, filename: str = "<cell>") -> None:
    """Raise if ``and`` / ``or`` is used with a bit-pattern operand.

    Python's ``and`` / ``or`` are *logical* short-circuit operators that
    return one of the operands — ``26 and 71`` is ``71``, NOT a bitwise
    AND (which would be ``2``).  Someone reaching for bitwise logic on
    bit patterns and writing ``and`` / ``or`` instead of ``&`` / ``|``
    gets a silently wrong answer.  We can't safely redefine the keywords
    (they drive real control flow like ``if x and y``), so instead we
    detect the *narrow, unambiguous* bitwise-intent case and raise a
    clear error pointing at ``&`` / ``|``.

    The trigger is deliberately strong-signal only: ``and`` / ``or`` with
    an immediately adjacent **bit-pattern operand** — a base-suffixed
    literal (``1101₂``, ``FF₁₆``, ``17₈``), a Python ``0b`` / ``0x`` /
    ``0o`` literal, or a ``▸ bin`` / ``▸ hex`` / ``▸ oct`` display tag.
    Ordinary boolean logic (``if ready and armed``) has no such operand
    and is never touched.  Runs after string protection, so keywords
    inside string literals don't trigger it.
    """
    # A bit-pattern token next to the keyword.  Base-suffix uses Unicode
    # subscript digits; ``0b/0x/0o`` are Python literals; ``▸ bin`` etc.
    # The base-suffix literal needs a token boundary in front — otherwise
    # an identifier tail like ``data₁`` (the ``a₁``) would match it; a
    # negative lookbehind rejects a preceding identifier character.
    bitpat = (
        r"(?:(?<![0-9A-Za-z_])[0-9A-Fa-f]+[₀₁₂₃₄₅₆₇₈₉]+"  # base-suffixed literal
        r"|0[bBxXoO][0-9A-Fa-f]+"                  # 0b/0x/0o literal
        r"|▸\s*(?:bin|hex|oct))"                   # ▸ bin/hex/oct tag
    )
    kw = r"\b(?:and|or)\b"
    # bit-pattern  <kw>   OR   <kw>  bit-pattern
    pat = re.compile(rf"(?:{bitpat}\s*{kw})|(?:{kw}\s*{bitpat})")
    for lineno, line in enumerate(source.split("\n"), start=1):
        if pat.search(line):
            # Which keyword, for the message.
            which = "and" if re.search(r"\band\b", line) else "or"
            repl = "&" if which == "and" else "|"
            raise SyntaxError(
                f"'{which}' is Python's logical operator and returns one "
                f"operand (e.g. ``26 {which} 71`` is "
                f"{'71' if which == 'and' else '26'}), not a bitwise "
                f"result. For bitwise logic on bit patterns use "
                f"'{repl}'. If you really meant the logical '{which}', "
                f"rewrite without the bit-pattern literal/tag to silence "
                f"this check.",
                (filename, lineno, 1, line),
            )


def _rewrite_idx_assignment(source: str) -> str:
    """Convert ``_idx(obj, i, j) = rhs`` into ``_idx_set(obj, rhs, i, j)``.

    The subscript rewriter turns ``M₀͵₁`` into a call ``_idx(M, 0, 1)`` so
    that container-appropriate access can be dispatched at runtime.  On
    the LHS of an assignment that call isn't assignable, so this pass —
    run right after subscript rewriting, when ``:=`` has already become
    ``=`` — rewrites such a statement to the ``_idx_set`` form, which
    performs the store (0-based everywhere; on a DSL matrix it mutates
    the matrix in place rather than a row copy).

    Operates line by line and only on a statement that *starts* with
    ``_idx(`` (after indentation) and has a top-level ``=`` (not ``==``)
    following the balanced call.  Anything else is left untouched.
    """
    out_lines = []
    for line in source.split("\n"):
        stripped = line.lstrip()
        indent = line[:len(line) - len(stripped)]
        if not stripped.startswith("_idx("):
            out_lines.append(line)
            continue
        depth = 0
        end = None
        for k, c in enumerate(stripped):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    end = k
                    break
        if end is None:
            out_lines.append(line)
            continue
        after = stripped[end + 1:].lstrip()
        if not after.startswith("=") or after.startswith("=="):
            out_lines.append(line)
            continue
        rhs = after[1:].strip()
        # Split off any trailing comment so it doesn't land inside the
        # ``_idx_set(...)`` parens.  A ``#`` inside a string literal isn't
        # a comment, so skip those.
        comment = ""
        in_str = None
        for k, ch in enumerate(rhs):
            if in_str:
                if ch == in_str and rhs[k-1:k] != "\\":
                    in_str = None
            elif ch in "'\"":
                in_str = ch
            elif ch == "#":
                comment = rhs[k:]
                rhs = rhs[:k].rstrip()
                break
        inner = stripped[len("_idx("):end]
        parts, d, buf = [], 0, ""
        for c in inner:
            if c in "([{":
                d += 1
            elif c in ")]}":
                d -= 1
            if c == "," and d == 0:
                parts.append(buf); buf = ""
            else:
                buf += c
        if buf.strip():
            parts.append(buf)
        parts = [p.strip() for p in parts]
        obj, idxs = parts[0], parts[1:]
        tail = f"  {comment}" if comment else ""
        out_lines.append(
            f"{indent}_idx_set({obj}, {rhs}, {', '.join(idxs)}){tail}"
        )
    return "\n".join(out_lines)


def _wrap_matrix_literals(source: str) -> str:
    """Wrap every list-of-lists *literal* in a ``_as_matrix(...)`` call.

    Runs as an AST pass near the end of ``transform_source`` (when the
    source is already valid Python).  A ``List`` node whose elements are
    ALL ``List`` nodes is a 2-D literal — the matrix shape — so we wrap
    it; ``_as_matrix`` then decides at runtime whether it's a genuine
    rectangular numeric/symbolic grid (→ sympy ``Matrix``) or should
    stay a plain list (ragged, strings, etc.).

    Why AST rather than regex: nested-bracket literals can't be matched
    reliably with a regex, but the structural test "a List of Lists" is
    trivial and exact on the parse tree.  Only LITERAL lists are touched
    — a variable holding a list, or a comprehension, is not a ``List``
    node and is left alone, so this changes the meaning of written
    ``[[...]]`` literals only, nothing computed.

    Falls back to the original source unchanged if the text doesn't
    parse (a later compile step will report the real error) or if AST
    unparsing isn't available.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    class _MatrixWrapper(ast.NodeTransformer):
        def visit_List(self, node):
            # Recurse first so inner lists are handled, then decide.
            self.generic_visit(node)
            if not node.elts:
                return node
            # A 2-D structure to promote to a matrix takes two forms:
            #   (1) a LITERAL matrix ``[[..],[..]]`` — every element is a
            #       list literal; or
            #   (2) a ROW-BUILT matrix ``M := [r0, r1, r2]`` where each
            #       element is a *name/expression* that evaluates to a row
            #       (``POS_0_15`` etc.).  Without this second case, a
            #       matrix assembled from row variables stayed a plain
            #       list while a literal became a matrix — the same value
            #       silently getting different operations (``*``, ``.T``,
            #       ``.det()``…).  We wrap whenever every element is "row-like"
            #       (a list, name, call, subscript, or attribute — things
            #       that can evaluate to a row), and let ``_as_matrix``
            #       decide at runtime: it promotes only a rectangular
            #       numeric/symbolic 2-D structure and returns anything
            #       else (ragged, 1-D, string/мixed rows) unchanged, so a
            #       plain list like ``['R1', 'R2']`` (string constants) is
            #       never touched and ragged input gracefully stays a list.
            row_like = (ast.List, ast.Name, ast.Call,
                        ast.Subscript, ast.Attribute)
            if all(isinstance(e, row_like) for e in node.elts):
                return ast.Call(
                    func=ast.Name(id="_as_matrix", ctx=ast.Load()),
                    args=[node],
                    keywords=[],
                )
            return node

        def visit_Assign(self, node):
            # Route a *bracket* assignment ``obj[key] = value`` through
            # ``_idx_set`` so a DSL matrix gets bounds-checked in-place
            # mutation — matching the bracket READ above and the
            # ``M₀͵₁ := v`` subscript assignment.  Only a single-target
            # subscript Store with an integer/tuple key is converted;
            # slices and multi-target or augmented assignments are left
            # untouched.
            self.generic_visit(node)
            if len(node.targets) != 1:
                return node
            tgt = node.targets[0]
            if not (isinstance(tgt, ast.Subscript)
                    and isinstance(tgt.ctx, ast.Store)):
                return node
            key = tgt.slice
            if isinstance(key, ast.Slice):
                return node
            if isinstance(key, ast.Tuple):
                if any(isinstance(e, ast.Slice) for e in key.elts):
                    return node
                idx_args = list(key.elts)
            else:
                idx_args = [key]
            call = ast.Call(
                func=ast.Name(id="_idx_set", ctx=ast.Load()),
                args=[tgt.value, node.value] + idx_args,
                keywords=[],
            )
            return ast.Expr(value=call)

        def visit_Subscript(self, node):
            # Route a *read* subscript ``obj[key]`` through ``_idx`` so a
            # DSL matrix gets its bounds-checked element/row access
            # (matching the ``M₀͵₁`` notation) while plain
            # lists/dicts/arrays keep native access.  CRUCIAL scoping:
            #   * Only ``Load`` context — an assignment target ``M[i] =``
            #     (Store) or ``del M[i]`` (Del) must stay a real subscript;
            #     subscript assignment is handled by the ``_idx_set`` pass.
            #   * A pure *slice* key (``M[1:3]``) is left as a native
            #     subscript — slice semantics aren't element access
            #     and rebuilding a slice as a call arg is needless.
            # This only ever sees genuine subscript ACCESS, never a list
            # literal (that's a ``List`` node), so list literals are safe.
            self.generic_visit(node)
            if not isinstance(node.ctx, ast.Load):
                return node
            key = node.slice
            # Skip slices (and tuples that contain a slice).
            if isinstance(key, ast.Slice):
                return node
            if isinstance(key, ast.Tuple):
                if any(isinstance(e, ast.Slice) for e in key.elts):
                    return node
                args = [node.value] + list(key.elts)
            else:
                args = [node.value, key]
            return ast.Call(
                func=ast.Name(id="_idx", ctx=ast.Load()),
                args=args,
                keywords=[],
            )

    try:
        new_tree = _MatrixWrapper().visit(tree)
        ast.fix_missing_locations(new_tree)
        return ast.unparse(new_tree)
    except Exception:
        # ast.unparse is 3.9+; if anything goes wrong, leave source as-is
        # (the feature simply doesn't apply rather than breaking the cell).
        return source


def transform_source(source, **_kwargs):
    # SCOPE GUARD — transform interactive CELLS and the toolkit's own DSL
    # modules, but NEVER third-party library files.  The ``ideas`` hook
    # installs globally, so without this guard it runs the DSL rewriters
    # over every module imported afterwards — including matplotlib and its
    # dependency ``dateutil``, whose perfectly valid Python (e.g.
    # ``comp = lambda dc, dtc: dc >= dtc``) the rewriters then corrupt into
    # a SyntaxError deep inside an unrelated import.  (It only surfaced
    # after a Jupyter/matplotlib upgrade because the new import order pulls
    # ``dateutil`` in fresh, through the active hook, rather than from a
    # pre-hook cache.)
    #
    # We must be surgical: some of the toolkit's OWN modules (e.g.
    # ``calc_symbols.py``) are themselves written in the DSL and MUST be
    # transformed at import.  So we key off the file path:
    #   * no path / a ``<...>`` marker  → an interactive cell → transform.
    #   * a path under a known third-party location (site-packages,
    #     dist-packages, miniconda/anaconda, lib/python…) → skip.
    #   * any other real path (the toolkit's own package dir) → transform.
    filename = _kwargs.get("filename", "")
    if filename:
        import os.path
        base = os.path.basename(filename)
        is_cell = filename.startswith("<") or base.startswith("<")
        if not is_cell:
            norm = filename.replace("\\", "/").lower()
            _THIRD_PARTY_MARKERS = (
                "/site-packages/", "/dist-packages/",
                "/miniconda3/", "/anaconda3/", "/miniconda/", "/anaconda/",
                "/lib/python", "/lib64/python", "/python313/", "/python312/",
                "/python311/", "/python310/", "/.venv/", "/venv/",
            )
            if any(marker in norm for marker in _THIRD_PARTY_MARKERS):
                # A third-party library file — must NOT be DSL-transformed.
                return source
            # A real .py path that isn't third-party.  If it's a
            # plain-Python sibling of this module (implementation code, not
            # DSL), skip it; otherwise (a DSL-authored toolkit module such
            # as ``calc_symbols.py``, or a user's own DSL .py) transform it.
            if base in _PLAIN_PYTHON_SIBLINGS:
                return source

    # Clear the unit-label stash so any leftovers from a previous
    # call (e.g. a transform that raised mid-pipeline) don't bleed
    # into this one.  The stash is module-level for simplicity rather
    # than threaded through every rewriter as a parameter.
    _unit_label_stash.clear()

    # ⇥ / ↵ rewriter runs FIRST — before string protection — because it
    # needs to see inside string literals to translate the in-string form
    # of these glyphs to escape sequences.  By the time _protect_strings
    # runs, string contents are stashed and inaccessible to later passes.
    source = rewrite_tab_newline_chars(source)

    # Stash string and comment bodies so the regex rewriters can't mangle
    # them.  Restored at the end, just before the ``_check_protected_names``
    # AST scan (which is happy to see strings unmolested).
    source, _stashed_strings = _protect_strings(source)

    # Guard against the ``and`` / ``or`` bitwise footgun — only fires on a
    # strong bitwise signal (a bit-pattern literal / ``▸ bin`` tag next to
    # the keyword).  Runs now that string bodies are stashed, so keywords
    # inside strings can't trigger it.
    _check_bitwise_andor(source, _kwargs.get("filename", "<cell>"))

    # Stash whole-name constants (``εₒ``, ``μₒ``, ``Nᴬ`` etc.) — these
    # contain subscript/superscript characters that the subscript and
    # superscript rewriters would otherwise decompose.  Restored after
    # all those rewriters have run so the original Unicode form is
    # what reaches the Python parser and the runtime namespace.
    source, _stashed_constants = _protect_constant_names(source)

    # Set-membership swap (∋ / ∌) — runs before normalize_source so the
    # operand-capture happens while the source still looks the way the
    # user wrote it.  After this pass the only set-theory glyphs left
    # are the ones with direct character-level translations, which
    # normalize_source handles.
    source = rewrite_set_membership_swap(source)

    source = normalize_source(source)
    # Base-suffixed numeric literals must be expanded before range-dots
    # sees them, so that ``021₃`` becomes ``int("021", base=3)`` (a
    # function call accepted by ``_BINOP_RHS``) rather than a subscripted
    # numeric form that the range pattern can't recognise.
    source = rewrite_base_suffixed_numbers(source)
    # ISO time/date literals (``"2026-05-06"ₜᵢₘₑ`` → ``iso("2026-05-06")``)
    # must be rewritten BEFORE range-dots, so a date range
    # ``["2026-05-06"ₜᵢₘₑ .. "2026-05-26"ₜᵢₘₑ]`` presents range-dots with
    # ``iso(...)`` function-call operands (which ``_BINOP_RHS`` accepts)
    # rather than string-literal-plus-subscript (which it does not).
    source = rewrite_iso_postfix(source)
    # Roman-numeral input literals (``"MCMIX"ᵣₒₘₑ`` → ``from_roman(...)``).
    # Like the ISO rewrite this runs before range-dots so a Roman range
    # ``["I"ᵣₒₘₑ .. "X"ᵣₒₘₑ]`` (if ever written) presents range-dots with
    # function-call operands.  Order relative to the ISO pass does not
    # matter — the two subscripts (``ₜᵢₘₑ`` vs ``ᵣₒₘₑ``) are disjoint.
    source = rewrite_roman_postfix(source)
    # Symbol declarations run BEFORE the range rewriters.  A name range
    # in a declaration — ``symbols: x..z`` / ``R1..R4 := symbols`` —
    # must be expanded by the declaration rewriter (into ``x, y, z`` …),
    # not grabbed by the numeric ``..`` rewriter (which would turn it
    # into ``_range_inc(x, z)``).  Putting these first claims the ``..``
    # inside a declaration line; ``rewrite_range_dots`` then only sees
    # the ``..`` that remain in ordinary expressions.
    source = rewrite_symbol_declaration(source)
    source = rewrite_symbol_declaration_prefix(source)
    # String-literal ranges (``['C8'..'C13']``) before the numeric ``..``
    # rewriter, so string endpoints are turned into ``_str_range`` splices
    # and the numeric rewriter never sees them.
    source = rewrite_string_range(source)
    # Closed interval ``a ‥ b`` (the glyph ``Range`` prints with) before
    # the enumerating ``a..b`` — distinct code points, no overlap, but
    # keeping them adjacent documents that they are siblings.
    source = rewrite_interval_dots(source)
    source = rewrite_range_dots(source)
    # Inequality-style for-loop headers.  Must run after normalize_source
    # (so ``≤``/``≥`` have already become ``<=``/``>=``) and after
    # rewrite_range_dots (which handles the companion ``for j in a..b:``
    # form via _range_inc rather than range).  Order with respect to the
    # other rewriters below doesn't matter — this pass only touches lines
    # that begin with ``for``, which the other rewriters don't generate
    # or modify in their captured spans.
    source = rewrite_inequality_for(source)
    # Comprehension-form companion: same notation, same range mapping,
    # but for ``for`` clauses inside [ ] / { } / ( ) rather than at
    # statement level.  Run after the statement form so any line that
    # was already rewritten doesn't re-match here (the statement form
    # produces ``for j in range(...):`` which has no inequality
    # operators left for the comprehension regex to find).
    source = rewrite_inequality_for_comprehension(source)
    # Arrow rewrites.  Order: def-arrow first (specific case — the ``→``
    # between a def's close-paren and the trailing ``:``), then
    # lambda-arrow (general case — every other ``→`` becomes a lambda).
    # The lambda pass's negative lookbehinds would already skip def
    # signatures on their own, but doing the specific case first is
    # clearer and easier to verify.  Both run before tokenization, so
    # the resulting ``lambda`` keyword and ``->`` arrow are seen by the
    # tokenizer as ordinary Python.
    source = rewrite_def_arrow(source)
    source = rewrite_lambda_arrow(source)
    # ▶ runs early so the captured RHS source text is the user's literal
    # spelling (``mm/s``), not anything pre-mangled by later passes.
    source = rewrite_target_unit(source)
    source = rewrite_math_assignment(source)
    source = rewrite_parallel(source)
    source = rewrite_postfix_percent(source)
    source = rewrite_postfix_permille(source)
    source = rewrite_prefix_sqrt(source)
    source = rewrite_postfix_factorial(source)
    source = rewrite_uparrow_power(source)
    source = rewrite_transpose_superscript(source)
    source = rewrite_postfix_superscripts(source)
    source = rewrite_floor_ceil(source)
    source = rewrite_degrees(source)
    source = rewrite_phasor(source)
    source = rewrite_abs_bars(source)
    source = rewrite_subscript_logs(source)
    source = rewrite_subscript_indices(source)
    # Implicit-multiplication repair for ``2x₅``-style input.  The
    # subscript pass turns ``x₅`` into ``_idx(x, 5)`` in place, so a digit
    # (or close-paren) immediately preceding the subscripted name ends up
    # glued to the call — ``2x₅`` → ``2_idx(x, 5)``, which the tokenizer
    # reads as the invalid literal ``2_idx``.  The normal implicit-mul
    # pass (token-based, further down) can't see the boundary because
    # ``2_idx`` is already one bad token.  So we insert the ``*`` here,
    # right after the call was introduced: a digit or ``)`` directly
    # before ``_idx(`` is multiplication by juxtaposition.  (A space, as
    # in ``2 x₅``, already separates them and is handled normally.)
    source = re.sub(r'(?<=\d)(_idx\()', r'*\1', source)
    source = re.sub(r'(?<=\))(_idx\()', r'*\1', source)
    source = _rewrite_idx_assignment(source)
    source = rewrite_plusminus(source)
    source = rewrite_approx(source)
    tokens = token_utils.tokenize(source)
    if not tokens:
        return source

    # Implicit-multiplication insertion.  Walks the token stream once,
    # inserting a ``*`` between adjacent token pairs that should multiply
    # (numbers stuck to identifiers, ``(...)`` stuck to ``(...)``, etc.).
    #
    # The tricky case is ``)`` followed by ``(``.  In math notation that
    # means "group times group" — ``(a+b)(c-d)`` is ``(a+b)*(c-d)``.  In
    # Python it means a chained call — ``f(x)(y)`` is ``f`` called with
    # ``x``, then the result called with ``y``.  Same character pair,
    # different intent.  To tell them apart we track each open paren's
    # "kind" on a stack:
    #
    #   - ``call``: the ``(`` introduced a function-call argument list.
    #     This is true when ``(`` was preceded by an identifier, by
    #     ``]``, or by another call paren's ``)``.
    #   - ``group``: the ``(`` was a grouping paren — preceded by an
    #     operator, a comma, the start of an expression, or a grouping
    #     paren's ``)``.
    #
    # When we hit ``)``, we pop the stack and remember that kind as
    # ``last_close_kind``.  When we then hit ``(`` after a ``)``, we
    # insert ``*`` only if the previous ``)`` closed a *group* paren —
    # so ``(a)(b)`` multiplies but ``f(x)(y)`` chains.
    #
    # Limitation: an immediately-invoked parenthesised callable —
    # ``(lambda x: x+1)(5)``, ``(f if cond else g)(x)`` — gets a
    # spurious ``*`` because the closing paren is classified as a group.
    # This pattern was already broken in the pre-paren-tracking version
    # (the old ``)(`` → ``)*(`` substitution had the same flaw); the
    # workaround is to bind the callable to a name first.
    paren_kind_stack = []
    last_close_kind = None

    if prev_token := tokens[0]:
        # Bookkeeping for the very first token, in case it's already a ``(``.
        if prev_token == "(":
            paren_kind_stack.append("group")  # nothing precedes — group

    # Names that bind TIGHTLY when preceded by a value — wrapping the
    # ``value * unit`` adjacency in parens so subsequent ``/`` and ``*``
    # operate on the whole quantity rather than the bare value.  Without
    # this, ``12 V / 3 A`` parses as ``12 * V / 3 * A`` = ``(12·V/3)·A`` =
    # power (V·A), not resistance (V/A), because Python's ``*`` and ``/``
    # are equal-precedence and left-associate — definitely not what
    # ``V/A`` reads as in scientific writing.
    #
    # The set itself lives at module scope (so multiple rewriters can
    # share it).  See ``_UNIT_NAMES_FOR_BINDING`` near the top of the
    # file.

    def _is_id(t):
        """``True`` if ``t`` is an identifier-shaped token.

        Handles both ``token_utils.Token`` objects (which have
        ``.is_identifier()``) and bare strings (like the ``"*"`` we
        splice into ``new_tokens`` during this pass — they aren't
        identifiers and can be checked by ``str.isidentifier()``).
        """
        if hasattr(t, "is_identifier"):
            return t.is_identifier()
        return str(t).isidentifier()

    def _is_num(t):
        """``True`` if ``t`` is a number-shaped token (Token or str)."""
        if hasattr(t, "is_number"):
            return t.is_number()
        try:
            float(str(t))
            return True
        except ValueError:
            return False

    def _gap_before(left, right):
        """``True`` if there is whitespace between two adjacent tokens.

        Uses the tokens' source positions: a gap exists when they sit on
        the same row and ``left`` ends at an earlier column than
        ``right`` starts.  Returns ``False`` if either token lacks
        position info (e.g. a ``"*"`` string spliced in during this
        pass) — those are never the subject of this check anyway, since
        the caller only consults it for an original identifier followed
        by an original ``(``.

        ``token_utils`` tokens expose ``start_row``/``start_col`` and
        ``end_row``/``end_col``.  Different rows (a line continuation)
        count as a gap too — an identifier and a ``(`` split across
        lines is not a tight call.
        """
        for attr in ("end_row", "end_col", "start_row", "start_col"):
            if not hasattr(left, attr) and attr.startswith("end"):
                return False
        if not hasattr(right, "start_row") or not hasattr(right, "start_col"):
            return False
        try:
            if left.end_row != right.start_row:
                return True   # split across lines — not a tight call
            return left.end_col < right.start_col
        except Exception:
            return False

    def _atom_start_index(toks):
        """Return the index in ``toks`` where the rightmost atom begins.

        Walks backward over the existing emitted-tokens list to find
        where the current "atom" started.  An atom is one of:
        - a single number or identifier (1 token back)
        - a parenthesised group: walk back over balanced ``(`` / ``)``
        - a bracketed list: walk back over balanced ``[`` / ``]``
        - a function call ``f(x)``: stop at the function name (so
          ``f(x) V`` wraps as ``(f(x) * V)``, treating the call as
          one atom)

        Returns the index, or ``len(toks)`` if no atom was found (which
        shouldn't happen in valid input but is a defensive fallback).
        """
        if not toks:
            return 0
        j = len(toks) - 1
        last = toks[j]
        # Skip whitespace-only tokens — token_utils strips most, but
        # be defensive.
        if not str(last).strip():
            return j

        # Case 1: a bracketed/parenthesised closer.  Walk back over the
        # balanced group, then if an identifier sits to the LEFT of the
        # open bracket/paren, that's a function call — include it as
        # part of the atom.
        if str(last) == ")" or str(last) == "]":
            depth = 1
            k = j - 1
            while k >= 0 and depth > 0:
                t = str(toks[k])
                if t == ")" or t == "]":
                    depth += 1
                elif t == "(" or t == "[":
                    depth -= 1
                k -= 1
            # k is now one position before the opener.  If the token
            # there is an identifier (or ``]``/``)`` from a chained
            # call), include it.
            while k >= 0 and (_is_id(toks[k]) or str(toks[k]) in (")", "]")):
                if str(toks[k]) in (")", "]"):
                    # Recurse into another balanced group.
                    k = _atom_start_index(toks[:k + 1]) - 1
                else:
                    k -= 1
            return k + 1

        # Case 2: a number or identifier — atom is just this token.
        if _is_num(last) or _is_id(last):
            return j

        # Fallback: don't try to wrap.
        return len(toks)

    # Insertion-marker tokens.  We can't emit ``(`` and ``)`` directly
    # into ``new_tokens`` because token_utils' tokens are objects with
    # their own machinery; instead, track INSERTION POINTS as (index,
    # marker) tuples and apply them in a second pass.
    wrap_inserts = []   # list of (index_in_new_tokens, "(") or (index, ")")
    unit_rewrites = []  # indices in new_tokens of unit tokens to tag via _wu

    new_tokens = [prev_token]

    for token in tokens[1:]:
        insert_mul = (
            (
                prev_token.is_number()
                and (token.is_identifier() or token.is_number() or token == "(")
            )
            or (
                prev_token.is_identifier()
                and (token.is_identifier() or token.is_number())
            )
            or (
                prev_token == ")"
                and (token.is_identifier() or token.is_number())
            )
            or (
                prev_token == "]"
                and (token.is_identifier() or token.is_number() or token == "(")
            )
            or (
                prev_token == "π" and token == "("
            )
            # Known scalar constants behave like ``π`` — they're values,
            # not callables — so when one is followed by ``(`` it's
            # implicit multiplication, not a function call.  Without
            # this, ``4π εₒ (r²)`` parses as ``εₒ(r²)`` (a call to a
            # non-callable) and dies at runtime.
            #
            # Two flavours to handle:
            # (a) Bare-name constants (``π``, ``c``, ``ε_0`` etc.) that
            #     reach this pass unchanged — match by membership in
            #     ``_NON_CALLABLE_NAMES``.
            # (b) Subscript/superscript-bearing constants (``εₒ``,
            #     ``Nᴬ`` etc.) that the constant-name protection pass
            #     replaced with ``__dsl_const_N__`` placeholders earlier
            #     in the pipeline — match by the placeholder's distinctive
            #     ``__dsl_const_`` prefix.  These ARE non-callable by
            #     construction (only physical constants are stashed in
            #     this way), so matching the prefix is safe.
            or (
                prev_token.is_identifier()
                and token == "("
                and (
                    str(prev_token) in _NON_CALLABLE_NAMES
                    or str(prev_token).startswith("__dsl_const_")
                )
            )
            # An identifier followed by ``(`` with WHITESPACE between
            # them — ``Mass_water (T2 - T1)`` — is implicit
            # multiplication, not a function call.  The signal is the
            # gap: a deliberate call is written ``f(x)`` with no space,
            # whereas multiplication-by-juxtaposition naturally leaves
            # one (and the rest of such a formula uses spaces between
            # its factors too — ``cw Mass_water (…)``).  ``f (x)`` with
            # a stray space is rare and, if it is genuinely a call, the
            # author can simply close the gap.  Without this clause the
            # juxtaposition is read as ``Mass_water(T2 - T1)`` and dies
            # with "'Sig' object is not callable" (or silently calls a
            # real function with the wrong argument).
            #
            # ``_gap_before(prev_token, token)`` is true only when both
            # tokens sit on the same source row and the columns leave a
            # gap — see the helper defined above this loop.
            or (
                prev_token.is_identifier()
                and token == "("
                and _gap_before(prev_token, token)
            )
            # ``)`` followed by ``(`` where the ``)`` closed a *group*
            # paren — that's group×group multiplication, not a chained
            # call.  When the previous ``)`` closed a *call*, leave it
            # alone (it's currying).
            or (
                prev_token == ")"
                and token == "("
                and last_close_kind == "group"
            )
        )

        if insert_mul:
            new_tokens.append("*")

            # Tight-binding: if the just-inserted ``*`` is between a
            # value-like atom and a known unit name, mark a wrap.  The
            # rationale and rule are documented near the
            # ``_UNIT_NAMES_FOR_BINDING`` set above.  We mark insertion
            # points now and apply them after the loop so they don't
            # disturb indices used by ``_atom_start_index``.
            if token.is_identifier() and str(token) in _UNIT_NAMES_FOR_BINDING:
                # Find where the LHS atom starts in ``new_tokens`` —
                # remember we already appended ``*`` so the atom ends
                # at index ``len(new_tokens) - 2`` (before the ``*``).
                atom_start = _atom_start_index(new_tokens[:-1])
                # A power binds tighter than the unit: ``10⁷ N`` reaches
                # this pass as ``(10)**(7) N``, and the atom found above
                # is just the exponent ``(7)``.  Wrapping that alone
                # gives ``10**((7)*N)`` — a unit in the exponent, which
                # dies at runtime.  Walk back over ``**`` and its base
                # (repeatedly, for chained powers) so the whole power
                # expression is the LHS: ``((10)**(7) * N)``.
                while (atom_start >= 1
                       and str(new_tokens[atom_start - 1]) == "**"):
                    base_start = _atom_start_index(
                        new_tokens[:atom_start - 1])
                    if base_start >= atom_start - 1:
                        break   # no atom before ``**`` — leave as is
                    atom_start = base_start
                wrap_inserts.append((atom_start, "open"))
                wrap_inserts.append((len(new_tokens) + 1, "close"))
                # Remember the WRITTEN unit: emit ``_wu(mm, 'mm')`` in
                # place of the bare ``mm`` so the literal displays in
                # the unit the author typed (see ``_wu``).  The label
                # goes through the unit-label stash — a placeholder
                # survives every later pass untouched and is restored
                # to the quoted name at the end of ``transform_source``.
                # The rewrite is deferred to after the loop (the token
                # must still look like an identifier to the adjacency
                # rules that follow — ``5 Ω x`` needs its second ``*``),
                # and it edits the token's text in place rather than
                # splicing in a bare string, so the token keeps the
                # source position ``untokenize`` uses for spacing.
                unit_rewrites.append(len(new_tokens))

        new_tokens.append(token)

        # Update paren-kind tracking AFTER the insert decision, since the
        # decision uses the *previous* state.
        if token == "(":
            # Classify based on what immediately precedes the ``(``.
            # ``)`` after a call paren still produces a call (currying);
            # ``)`` after a group paren produces a group (the parens are
            # likely the second operand of an implied multiplication).
            #
            # Known non-callable scalars (``εₒ`` etc., recognised either
            # by name membership in ``_NON_CALLABLE_NAMES`` or by the
            # ``__dsl_const_`` placeholder prefix) introduce a *group*
            # paren, not a call paren — they're values, and the paren
            # is grouping the multiplied factor.  This pairs with the
            # ``*``-insertion rule above so that ``εₒ (r²)`` becomes
            # ``εₒ * (r²)`` rather than ``εₒ(r²)`` (a non-callable call).
            prev_str = str(prev_token)
            prev_is_known_scalar = (
                prev_token.is_identifier()
                and (prev_str in _NON_CALLABLE_NAMES
                     or prev_str.startswith("__dsl_const_"))
            )
            if prev_is_known_scalar:
                paren_kind_stack.append("group")
            elif (
                prev_token.is_identifier()
                or prev_token == "]"
                or (prev_token == ")" and last_close_kind == "call")
            ):
                paren_kind_stack.append("call")
            else:
                paren_kind_stack.append("group")
        elif token == ")":
            if paren_kind_stack:
                last_close_kind = paren_kind_stack.pop()

        prev_token = token

    # Apply wrap insertions in reverse order so earlier indices stay
    # valid as we splice into the list.  ``token_utils.untokenize``
    # accepts plain strings interleaved with ``Token`` objects, so we
    # can splice in raw ``"("`` and ``")"`` strings.
    # Tag the written units first — this changes token TEXT only, so
    # the indices recorded for the paren wraps below stay valid.
    for idx in unit_rewrites:
        tok = new_tokens[idx]
        name = str(tok)
        # The label is the canonical spelling: a literal typed with the
        # MICRO SIGN displays with the same μ as one typed with the
        # Greek letter, so the two spellings print identically.
        label = name.translate(_NFKC_CANONICAL)
        tok.string = f"_wu({name}, {_stash_unit_label(repr(label))})"

    if wrap_inserts:
        # Sort by index descending; for the same index, "close" comes
        # before "open" so we don't accidentally close before opening.
        wrap_inserts.sort(key=lambda t: (-t[0], 0 if t[1] == "close" else 1))
        for idx, kind in wrap_inserts:
            new_tokens.insert(idx, "(" if kind == "open" else ")")

    source = token_utils.untokenize(new_tokens)
    source = rewrite_list_unit_multiply(source)
    source = wrap_numeric_literals(source)
    # Put the original string and comment bodies back before the AST scan,
    # so user-visible text (in errors, in protected-name checks of class
    # attribute lookups, etc.) sees what the user wrote, not the placeholder.
    source = _restore_strings(source, _stashed_strings)
    # Restore the whole-name constants — must happen after string restore
    # (the placeholders aren't in strings; this order is just convention)
    # and before the AST scan so Python sees ``εₒ`` etc. as actual
    # identifiers in the parse tree.
    source = _restore_constant_names(source, _stashed_constants)
    # Restore prettified unit labels — placeholders introduced by
    # ``rewrite_target_unit`` for the ``▶`` operator are substituted
    # back with their human-readable form (``MeV/c²``, ``kg/m³``).
    # Done after string/constant restoration; the placeholders are
    # pure ASCII so they survive the AST scan if any remain (they
    # shouldn't, since every introduction is paired with a restore).
    source = _restore_unit_labels(source)
    # Wrap list-of-lists literals as sympy matrices (``[[1,2],[3,4]]`` →
    # ``_as_matrix([[1,2],[3,4]])``).  AST pass — runs now that the
    # source is valid Python and strings/labels are restored.
    source = _wrap_matrix_literals(source)
    _check_protected_names(source, _kwargs.get("filename", "<cell>"))
    return source


def add_hook(**_kwargs):
    return import_hook.create_hook(
        transform_source=transform_source,
        hook_name="circuit_dsl",
    )
