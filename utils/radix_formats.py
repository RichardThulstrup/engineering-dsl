"""Additional integer display formats for the engineering DSL.

This module is the home for integer notations *beyond* the built-in
``hex`` / ``bin`` / ``oct`` / ``dec`` that ``sigfig.py`` registers itself.
It exists so that the *mechanism* (the ``_Radix`` display wrapper and the
``_RADIX_FORMATTERS`` registry, both in ``sigfig.py``) stays separate from
the *content* (specific extra formats) — the same split the project uses
for ``extra_units.py`` sitting beside forallpeople's base units.

HOW IT WORKS
------------
Each format is just a function ``int -> str``.  Calling
``register_radix(name, fn)`` drops it into the toolkit's radix registry
under ``name``; from that moment ``value ▸ name`` resolves to it — the
``▸`` rewriter queries the registry live, so no other wiring is needed.

This module performs its registrations as an IMPORT SIDE EFFECT: simply
importing it (which ``Engineer.py`` does at startup) makes every format
here available.  There is nothing to call.

ADDING MORE FORMATS
-------------------
Define a function, register it, and — if you also want to call the
function directly from DSL code (e.g. ``to_roman(2024)``) — add its name
to ``__all__``.  A format that should only be reachable via ``▸`` does
not need to be in ``__all__``; the ``register_radix`` call is enough.

A formatter should accept ANY Python int — negative, zero, very large —
and return a display string.  When a value falls outside what the
notation can express, returning ``str(n)`` (plain decimal) is the sane
fallback rather than raising.

NOTE ON THE ``▸`` CONTRACT
--------------------------
Radix tags are display preferences only.  ``year ▸ roman`` renders in
Roman numerals, but ``(year ▸ roman) + 1`` is a plain integer — the tag
is consumed by the arithmetic, never propagated.  Re-apply ``▸ roman`` at
the point of display.  This is the same transparent-wrapper behaviour as
the unit ``▸`` and the built-in ``▸ hex``.
"""

from .sigfig import register_radix

# Names exported for ``from .radix_formats import *`` in Engineer.py.
# Only the formatter functions that are useful to call directly belong
# here; the registrations themselves happen unconditionally on import.
__all__ = ["to_roman", "from_roman"]


# ---------------------------------------------------------------------------
# Roman numerals
# ---------------------------------------------------------------------------

# Subtractive-notation value table, largest first.  Greedy emission down
# this list yields the standard compact form (e.g. 1994 -> MCMXCIV, not
# MDCCCCLXXXXIIII).  The subtractive pairs (CM, CD, XC, XL, IX, IV) are
# included as their own entries so the greedy walk produces them
# naturally.
_ROMAN_TABLE = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)


def to_roman(n) -> str:
    """Render an integer as a Roman numeral string.

    Standard Roman numerals cover 1..3999 only — there is no zero, no
    negatives, and no universally-agreed notation above 3999.  For any
    value outside that range this returns plain decimal (``str(n)``) so
    a stray out-of-range value degrades gracefully instead of raising
    and aborting a whole cell.

    Examples::

        to_roman(2024)   ->  'MMXXIV'
        to_roman(1994)   ->  'MCMXCIV'
        to_roman(49)     ->  'XLIX'
        to_roman(0)      ->  '0'        (out of range -> decimal)
        to_roman(-5)     ->  '-5'       (out of range -> decimal)
    """
    n = int(n)
    if not (0 < n < 4000):
        return str(n)
    out = []
    remaining = n
    for value, symbol in _ROMAN_TABLE:
        # Emit ``symbol`` as many times as ``value`` divides in, then
        # carry the remainder down to smaller denominations.
        count, remaining = divmod(remaining, value)
        out.append(symbol * count)
    return "".join(out)


# Single-symbol values, for the parse-time validation walk below.
_ROMAN_SYMBOL = {"M": 1000, "D": 500, "C": 100, "L": 50,
                 "X": 10, "V": 5, "I": 1}


def from_roman(s) -> int:
    """Parse a Roman numeral string into an integer.

    Backs the DSL's ``"MCMIX"ᵣₒₘₑ`` input literal.  Accepts BOTH the
    minimal subtractive notation (``IV`` = 4, ``CM`` = 900) AND the
    older additive forms (``IIII`` = 4, ``XXXX`` = 40, ``VIIII`` = 9).
    The additive forms are not mistakes — ``IIII`` for four is the
    near-universal convention on clock faces, and additive numerals are
    common on monuments and in historical texts.  A reader should accept
    what is genuinely written.

    Input is upper-cased first, so ``"mcmix"`` parses too — paste
    tolerance, consistent with the DSL accepting alternate glyphs on
    input.  The result is a plain ``int``.

    The parse is the classic right-to-left scan: each symbol's value is
    added, unless it is smaller than the largest value seen so far to
    its right, in which case it is subtracted.  This reads ``IV`` (4)
    and ``IIII`` (4) alike without either being privileged.  Only a
    genuinely un-Roman character (anything outside ``M D C L X V I``)
    raises ``ValueError`` — form is not policed beyond that.

    Examples::

        from_roman("MCMIX")   ->  1909
        from_roman("MMXXIV")  ->  2024
        from_roman("IIII")    ->  4       (clock-face form — accepted)
        from_roman("XLIX")    ->  49
        from_roman("VIIII")   ->  9       (additive form — accepted)
        from_roman("mcmxciv") ->  1994    (case-insensitive)
        from_roman("XYZ")     ->  ValueError (not Roman symbols)

    Note ``from_roman`` is intentionally more permissive than the
    inverse of :func:`to_roman`: ``to_roman`` always emits the minimal
    subtractive form, but ``from_roman`` reads minimal and additive
    spellings of the same value identically.  ``from_roman(to_roman(n))
    == n`` still holds for every ``n`` in 1..3999.
    """
    if not isinstance(s, str):
        raise TypeError(
            f"from_roman expects a string, got {type(s).__name__}")

    text = s.strip().upper()
    if not text:
        raise ValueError("from_roman: empty string is not a Roman numeral")

    # Reject any character that is not a Roman symbol, pointing at the
    # offender.  This is the ONLY validity gate — a string of real Roman
    # symbols is read for the value it denotes, additive or subtractive,
    # without second-guessing the spelling.
    for ch in text:
        if ch not in _ROMAN_SYMBOL:
            raise ValueError(
                f"from_roman: {s!r} contains {ch!r}, which is not a "
                f"Roman numeral symbol (M D C L X V I)")

    # Right-to-left scan.  Walk symbols from the end; add each value,
    # but subtract it when it is smaller than the largest value seen so
    # far (i.e. it sits to the LEFT of a bigger symbol — the subtractive
    # ``IV`` / ``IX`` / ``XC`` case).  A run of equal symbols (``IIII``)
    # is all additive, since none is smaller than the max so far.
    total = 0
    max_seen = 0
    for ch in reversed(text):
        value = _ROMAN_SYMBOL[ch]
        if value < max_seen:
            total -= value
        else:
            total += value
            max_seen = value

    return total


# ---------------------------------------------------------------------------
# Registrations — performed on import.  After this, ``value ▸ roman``
# works anywhere in DSL code.
# ---------------------------------------------------------------------------

register_radix("roman", to_roman)
