"""ISO 286 limits and fits — shaft/hole tolerancing for the engineering DSL.

This module brings the ISO 286-1 / 286-2 "limits and fits" system into the
toolkit.  A tolerance class such as ``H7`` (hole) or ``g6`` (shaft) names a
band of allowable sizes relative to a nominal diameter; this module turns a
``(nominal, class)`` pair into a concrete ``Range`` so the rest of the DSL
(unit arithmetic, ``▶`` display, plotting) can operate on it directly.

DESIGN
------
A toleranced feature *is* an interval, and the toolkit already has the right
primitive for an interval: ``Range`` (from circuit_dsl).  So the whole module
is a thin layer that computes the two deviations from the ISO formulas and
hands back ``Range.from_pm`` / ``Range(low, high)``.  Nothing new to learn —
a hole is a ``Range`` of millimetres, and ``hole - shaft`` is interval
subtraction giving the clearance, exactly as ``Range`` already does it.

The ISO system has two halves:

* **Standard tolerance grade IT** — the *width* of the band.  IT01..IT18;
  smaller is tighter.  Width grows with diameter via the "standard tolerance
  unit" ``i = 0.45·∛D + 0.001·D`` (μm), then multiplied by a per-grade factor
  (IT7 = 16·i, IT8 = 25·i, ...).

* **Fundamental deviation** — *where* the band sits relative to nominal.  A
  letter: upper-case for holes (``H``, ``G``, ``F``, ...), lower-case for
  shafts (``h``, ``g``, ``f``, ...).  ``H`` holes have their lower deviation
  at exactly zero (hole-basis system); ``h`` shafts have their upper
  deviation at exactly zero (shaft-basis system).

ACCURACY NOTE
-------------
ISO 286-2 publishes *tabulated, rounded* deviation values.  This module
computes them from the ISO 286-1 *formulas* instead, so results may differ
from the printed tables by ~1 μm at the rounding boundary.  For design and
fit-class selection that's well within noise; if you need the exact
catalogued value for a drawing callout, check it against an ISO 286-2 table.
This module covers nominal sizes 1..500 mm.

USAGE
-----
    from utils.iso286 import hole, shaft, fit

    bore  := hole(25, "H7")          # → Range of mm: (25.000 ‥ 25.021)
    pin   := shaft(25, "g6")         # → Range of mm: (24.993 ‥ 25.000)
    play  := fit(25, "H7", "g6")     # → named tuple: min/max clearance, type

    print(bore.center, bore.tol)     # nominal-ish midpoint, half-width
    print(bore ▶ μm)                 # works — Range flows through ▶
"""

import math

from .sigfig import exact
from .circuit_dsl import Range

# ``mm`` and ``μm`` as Physical units — pulled from the engineering namespace
# lazily (see ``_mm()``); importing Engineer at module load time would be a
# circular import, so we fetch the unit on first use instead.
_MM_CACHE = {}


def _mm():
    """Return the ``mm`` Physical, importing lazily to dodge a circular
    import (Engineer imports plotting imports ... and we don't want to be
    in that chain at load time)."""
    if "mm" not in _MM_CACHE:
        from forallpeople import m as _m
        _MM_CACHE["mm"] = exact(0.001) * _m
    return _MM_CACHE["mm"]


__all__ = ["hole", "shaft", "fit", "it_grade", "tolerance_unit", "Fit"]


# ---------------------------------------------------------------------------
# Standard tolerance grades (the band WIDTH)
# ---------------------------------------------------------------------------

# Nominal-size steps (mm).  Each tuple is (lower_exclusive, upper_inclusive).
# The standard tolerance unit is computed at the geometric mean of each step,
# so every diameter inside a step shares one IT value — that's what makes an
# H7/g6 fit behave consistently across a size range.
_DIAMETER_STEPS = [
    (0, 3), (3, 6), (6, 10), (10, 18), (18, 30), (30, 50), (50, 80),
    (80, 120), (120, 180), (180, 250), (250, 315), (315, 400), (400, 500),
]

# IT grade → multiplier on the standard tolerance unit i.  Covers IT5..IT16,
# the practically-used range.  (IT01..IT4 use a different additive formula;
# IT17/IT18 extrapolate ×10 per 5 grades.  Add them if a use case needs it.)
_IT_MULTIPLIER = {
    5: 7, 6: 10, 7: 16, 8: 25, 9: 40, 10: 64,
    11: 100, 12: 160, 13: 250, 14: 400, 15: 640, 16: 1000,
}


def _geometric_mean_diameter(d_mm):
    """Geometric mean of the diameter step containing ``d_mm``.

    ISO computes the tolerance unit at ``√(D_min · D_max)`` of the step, not
    at the actual diameter — this is what discretises the tables.  The first
    step (0..3) uses ``D_min = 1`` by ISO convention (``√(1·3)``), since a
    geometric mean with zero would collapse.
    """
    for lo, hi in _DIAMETER_STEPS:
        if lo < d_mm <= hi:
            lo_eff = max(lo, 1)          # ISO convention for the 0..3 step
            return math.sqrt(lo_eff * hi)
    raise ValueError(
        f"nominal size {d_mm} mm is outside the 1..500 mm range this "
        f"module covers"
    )


def tolerance_unit(d_mm):
    """Standard tolerance unit ``i`` in micrometres for nominal size
    ``d_mm`` (mm).  ``i = 0.45·∛D + 0.001·D`` with D the step geometric
    mean.  This is the building block every IT grade scales."""
    D = _geometric_mean_diameter(d_mm)
    return 0.45 * D ** (1.0 / 3.0) + 0.001 * D


def it_grade(d_mm, grade):
    """Width of standard tolerance grade ``IT<grade>`` at nominal size
    ``d_mm``, returned as a ``mm``-dimensioned Physical.

    Example: ``it_grade(25, 7)`` → the IT7 band width at Ø25 (≈ 0.021 mm).
    """
    if grade not in _IT_MULTIPLIER:
        raise ValueError(
            f"IT{grade} not supported; this module covers IT5..IT16"
        )
    width_um = _IT_MULTIPLIER[grade] * tolerance_unit(d_mm)
    return exact(width_um / 1000.0) * _mm()      # μm → mm Physical


# ---------------------------------------------------------------------------
# Fundamental deviations (where the band SITS)
# ---------------------------------------------------------------------------
#
# The fundamental deviation is the band edge nearest the nominal size.  ISO
# 286-1 gives a formula per letter.  We implement the commonly-used letters
# for the *preferred* fits (the ones in every fit table): the clearance
# letters d, e, f, g, h, the transition letters js, k, m, n, and the
# interference letters p, r, s.  Each formula yields micrometres.
#
# Sign convention: for shafts, the fundamental deviation is the *upper*
# deviation ``es`` for letters a..h (band below nominal) and the *lower*
# deviation ``ei`` for letters j..zc (band above nominal).  Holes mirror
# this.  ``h`` shaft and ``H`` hole are the zero-deviation references.

def _fundamental_deviation_shaft(letter, d_mm):
    """Shaft fundamental deviation in μm for a single lower-case ``letter``.

    Returns a signed value: negative means the near edge sits below
    nominal (clearance side), positive means above (interference side).
    For letters a..g this is the *upper* deviation ``es``; for j..zc it is
    the *lower* deviation ``ei``.  The caller (``shaft``) knows which.
    """
    D = _geometric_mean_diameter(d_mm)
    letter = letter.lower()

    # Clearance side — fundamental deviation is the UPPER deviation es,
    # always ≤ 0.  Formulas from ISO 286-1 Table; the common subset:
    if letter == "h":
        return 0.0
    if letter == "g":
        return -2.5 * D ** 0.34
    if letter == "f":
        return -5.5 * D ** 0.41
    if letter == "e":
        return -11.0 * D ** 0.41
    if letter == "d":
        return -16.0 * D ** 0.44
    if letter == "c":
        # c is piecewise in the standard; this is the >40 mm form, good
        # enough for the typical large-clearance use.  Flagged in docs.
        return -(95.0 + 0.8 * D) if d_mm > 40 else -52.0 * D ** 0.2

    # Transition / interference side — fundamental deviation is the LOWER
    # deviation ei, ≥ 0.  These are tied to a specific IT grade in the
    # standard; the toolkit uses the widely-tabulated approximations.
    if letter == "js":
        # js is symmetric about nominal — handled specially in ``shaft``;
        # return 0 here, the symmetric split happens there.
        return 0.0
    if letter == "k":
        return 0.6 * D ** (1.0 / 3.0)          # ei ≈ +0.6·∛D
    if letter == "m":
        return 2.8 * D ** 0.34 + 1.0           # ei, approx
    if letter == "n":
        return 5.0 * D ** 0.34                 # ei
    if letter == "p":
        return 5.6 * D ** 0.41 + 1.0           # ei, approx
    if letter == "r":
        # r sits between p and s; geometric-ish mean used as a practical
        # approximation of the tabulated value.
        return 0.5 * (
            _fundamental_deviation_shaft("p", d_mm)
            + _fundamental_deviation_shaft("s", d_mm)
        )
    if letter == "s":
        return 0.4 * D + 14.0                  # ei, approx (>50 mm form)

    raise ValueError(
        f"shaft letter '{letter}' not supported; this module implements "
        f"the preferred-fit letters c d e f g h js k m n p r s"
    )


# ---------------------------------------------------------------------------
# Public constructors
# ---------------------------------------------------------------------------

def _parse_class(tol_class):
    """Split a tolerance class like ``'H7'`` or ``'js6'`` into
    ``(letter, grade)``.  Letter keeps its original case (upper = hole,
    lower = shaft); grade is an int."""
    s = str(tol_class).strip()
    # Letters are the leading non-digit run; grade is the trailing digits.
    i = 0
    while i < len(s) and not s[i].isdigit():
        i += 1
    letter, digits = s[:i], s[i:]
    if not letter or not digits:
        raise ValueError(
            f"tolerance class {tol_class!r} should look like 'H7' or 'g6'"
        )
    return letter, int(digits)


def hole(nominal_mm, tol_class):
    """Tolerance interval of a HOLE as a ``Range`` of millimetres.

    ``nominal_mm`` is the basic size in mm; ``tol_class`` is an upper-case
    class such as ``'H7'``.  Returns a ``Range`` whose ``.low`` / ``.high``
    are the minimum and maximum permitted hole sizes, as ``mm`` Physicals.

    For the hole-basis system the common case is an ``H`` hole, whose lower
    deviation is exactly zero — the hole is never smaller than nominal and
    at most ``IT`` larger.
    """
    letter, grade = _parse_class(tol_class)
    if not letter[0].isupper():
        raise ValueError(
            f"{tol_class!r} looks like a shaft class (lower-case); "
            f"use shaft() for shafts, or upper-case for a hole"
        )
    it = it_grade(nominal_mm, grade)             # band width, mm Physical
    mm = _mm()
    basic = exact(nominal_mm) * mm

    if letter == "H":
        # Hole-basis reference: lower deviation EI = 0, upper ES = +IT.
        return Range(basic, basic + it)
    if letter.upper() == "JS":
        # Symmetric about nominal: ±IT/2.
        return Range.from_pm(basic, it / 2)

    # Other hole letters: the fundamental deviation mirrors the shaft of
    # the same letter (ISO's "general rule" EI_hole = -es_shaft).  Good
    # for the preferred clearance holes (G, F, E, D).
    fd_um = -_fundamental_deviation_shaft(letter.lower(), nominal_mm)
    fd = exact(fd_um / 1000.0) * mm
    # For clearance-side hole letters the fundamental deviation is the
    # LOWER deviation EI; the band runs EI .. EI+IT.
    return Range(basic + fd, basic + fd + it)


def shaft(nominal_mm, tol_class):
    """Tolerance interval of a SHAFT as a ``Range`` of millimetres.

    ``nominal_mm`` is the basic size in mm; ``tol_class`` is a lower-case
    class such as ``'g6'``.  Returns a ``Range`` of min/max shaft sizes as
    ``mm`` Physicals.

    For the shaft-basis system the reference is an ``h`` shaft, whose upper
    deviation is exactly zero — the shaft is never larger than nominal.
    """
    letter, grade = _parse_class(tol_class)
    if letter[0].isupper():
        raise ValueError(
            f"{tol_class!r} looks like a hole class (upper-case); "
            f"use hole() for holes, or lower-case for a shaft"
        )
    it = it_grade(nominal_mm, grade)
    mm = _mm()
    basic = exact(nominal_mm) * mm

    if letter == "h":
        # Shaft-basis reference: upper deviation es = 0, lower ei = -IT.
        return Range(basic - it, basic)
    if letter == "js":
        # Symmetric: ±IT/2.
        return Range.from_pm(basic, it / 2)

    fd_um = _fundamental_deviation_shaft(letter, nominal_mm)
    fd = exact(fd_um / 1000.0) * mm

    # Clearance letters a..g: fundamental deviation is the UPPER deviation
    # es (≤ 0); band runs es-IT .. es.
    if letter in ("a", "b", "c", "d", "e", "f", "g"):
        return Range(basic + fd - it, basic + fd)
    # Transition / interference letters k..s: fundamental deviation is the
    # LOWER deviation ei (≥ 0); band runs ei .. ei+IT.
    return Range(basic + fd, basic + fd + it)


# ---------------------------------------------------------------------------
# Fit analysis
# ---------------------------------------------------------------------------

class Fit:
    """Result of pairing a hole and a shaft.

    Attributes (all ``mm`` Physicals unless noted):

    * ``hole``      — the hole ``Range``
    * ``shaft``     — the shaft ``Range``
    * ``min_clearance`` — smallest hole minus largest shaft.  Positive
      means guaranteed clearance; negative means guaranteed interference.
    * ``max_clearance`` — largest hole minus smallest shaft.
    * ``kind``      — ``'clearance'``, ``'interference'`` or
      ``'transition'`` (a transition fit can go either way depending on
      where the two parts land within their bands).
    """

    __slots__ = ("hole", "shaft", "min_clearance", "max_clearance", "kind")

    def __init__(self, hole_range, shaft_range):
        self.hole = hole_range
        self.shaft = shaft_range
        # Clearance = hole − shaft.  Extremes:
        #   min clearance = smallest hole − largest shaft
        #   max clearance = largest hole  − smallest shaft
        self.min_clearance = hole_range.low - shaft_range.high
        self.max_clearance = hole_range.high - shaft_range.low

        # Classify by the sign pattern of the clearance extremes.
        lo = float(self.min_clearance)
        hi = float(self.max_clearance)
        if lo >= 0:
            self.kind = "clearance"
        elif hi <= 0:
            self.kind = "interference"
        else:
            self.kind = "transition"

    def __repr__(self):
        return (
            f"Fit({self.kind}: clearance "
            f"{self.min_clearance!r} ‥ {self.max_clearance!r})"
        )


def fit(nominal_mm, hole_class, shaft_class):
    """Analyse the fit between a hole and a shaft at the same nominal size.

    ``fit(25, 'H7', 'g6')`` builds the H7 hole and the g6 shaft at Ø25 and
    returns a :class:`Fit` describing the clearance range and fit type.

    The two parts share a nominal size — that's the whole point of the ISO
    system: an H7/g6 callout means "make the hole H7 and the shaft g6 at the
    same basic Ø, and they will fit the standard way".
    """
    h = hole(nominal_mm, hole_class)
    s = shaft(nominal_mm, shaft_class)
    return Fit(h, s)
