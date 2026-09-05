"""
Unit-aware plotting helpers.

This module provides one function — ``plot()`` — that wraps
``matplotlib.pyplot`` with three quality-of-life features tuned to the
toolkit's typical usage:

1. **Unit-aware axis labels.**  When the data passed in carries
   ``forallpeople.Physical`` values (either as bare Physicals or wrapped
   in ``Sig``), the function extracts the unit symbol and appends it to
   the axis label in square brackets, like ``"[V]"``.  If you don't pass
   ``xlabel=`` / ``ylabel=``, the bracketed unit shows on its own.

2. **Symbolic-expression plotting.**  Pass a sympy expression, the
   variable to sweep, and a range, and the function ``lambdify``s the
   expression and evaluates it.  No manual ``numpy.linspace`` +
   ``lambdify`` setup.

3. **Multi-series in a single call.**  ``plot(x, y, "data", expr,
   var, (xmin, xmax), "fit")`` draws both the scatter data and the
   symbolic curve on one axes, with separate legend entries.  Each
   series is identified by its shape — paired arrays are data,
   a sympy expression followed by var + range is symbolic.

Returns the matplotlib ``Axes`` object so any further customisation
flows naturally — ``ax.grid()``, ``ax.set_xlim(...)``, etc.

Examples::

    # Array data with units
    voltage  := [1.0, 2.0, 3.0, 4.0] V
    current  := [0.10, 0.21, 0.29, 0.40] A
    plot(voltage, current, title="I vs V")

    # Symbolic
    symbols: x
    expr := sin(x) * exp(-x/10)
    plot(expr, x, (0, 20), title="damped oscillation")

    # Data + symbolic fit in one call (the headline shape)
    plot(x_data, y_data, "data",
         fit_expr, x, (x_data[0], x_data[-1]), "fit",
         title="Gaussian fit")

The function is intentionally small — it's a convenience wrapper, not a
plotting framework.  For anything more elaborate (multi-panel,
subplots, log scales, custom per-series styles, etc.), drop down to
``matplotlib.pyplot`` directly.  ``plot()`` returns the axes so you can
continue customising there.
"""

from __future__ import annotations

import numpy as np
import datetime as _datetime

__all__ = ["plot", "linefit", "polyfit", "list_themes"]


# ---------------------------------------------------------------------------
# Unit-detection helpers
# ---------------------------------------------------------------------------

def _unit_symbol(value) -> str | None:
    """Return the unit-symbol string for ``value``, or ``None`` if it's
    not a unit-carrying type.

    Detection is duck-typed so we don't have a hard dependency on
    forallpeople.  Anything with ``.value`` and ``.dimensions`` is
    treated as a Physical.

    For a Physical, the str representation is ``"<number> <unit>"``
    (e.g. ``"1.500 V"``), so the last whitespace-separated token is the
    unit symbol.  Composite units like ``"m·s⁻²"`` come through intact.

    For a ``_InUnits`` (output of ``▶``), the unit-label is already
    stored as a field — return it directly.

    For a Sig wrapping a Physical or an ndarray, we unwrap and recurse.

    For an ndarray of Physicals (dtype=object — produced by
    ``[1, 2, 3] * V``), we inspect the first non-None element.

    For a Python list/tuple, we inspect the first non-None element.

    Returns ``None`` for plain numbers, plain ndarrays, or sympy
    expressions.

    Robust to forallpeople's ``__str__`` raising ``KeyError`` for
    magnitudes outside its prefix table (M☉, electron mass, etc.).
    In those cases falls back to reading ``.dimensions`` directly.
    """
    # _InUnits — the label is right there.  Check BEFORE Sig because
    # _InUnits has .value too.
    if type(value).__name__ == "_InUnits":
        return getattr(value, "unit_label", None)

    # Unwrap Sig
    if type(value).__name__ == "Sig" and hasattr(value, "value"):
        return _unit_symbol(value.value)

    # ndarray of objects — check the first element
    if isinstance(value, np.ndarray):
        if value.dtype == object and value.size > 0:
            return _unit_symbol(value.flat[0])
        return None

    # Python list / tuple — check first element
    if isinstance(value, (list, tuple)) and len(value) > 0:
        return _unit_symbol(value[0])

    # Bare Physical
    if hasattr(value, "value") and hasattr(value, "dimensions"):
        try:
            s = str(value).strip()
            parts = s.split()
            # Format is typically "<number> <unit>".  If the string is
            # just one token (a dimensionless scalar?), there's no unit.
            if len(parts) >= 2:
                return parts[-1]
        except Exception:
            # forallpeople's __str__ crashes for huge/tiny magnitudes
            # (M☉, mₑ).  Fall back to a dimensions-based string.
            return _render_dimensions_string(value.dimensions)

    return None


def _render_dimensions_string(dims) -> str | None:
    """Build a unit-symbol string from a forallpeople ``Dimensions``
    namedtuple.  Used as fallback when forallpeople's own ``__str__``
    can't render (because the magnitude is outside its prefix table).

    Output matches the same convention forallpeople uses for compound
    units: ``kg·m²·s⁻³`` etc., with Unicode superscript exponents.
    Returns ``None`` for dimensionless quantities.
    """
    parts = []
    sup_digits = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")
    for name in ("kg", "m", "s", "A", "cd", "K", "mol"):
        power = getattr(dims, name, 0)
        if power == 0:
            continue
        if power == 1:
            parts.append(name)
        else:
            if power == int(power):
                parts.append(f"{name}{int(power)}".translate(sup_digits))
            else:
                parts.append(f"{name}^{power}")
    if not parts:
        return None
    return "·".join(parts)


def _si_magnitude(v):
    """SI-base magnitude of a single value, prefix-independent.

    For a Physical, this is its ``.value`` attribute (SI base, e.g.
    ``0.0023`` for ``2.3 mm``).  For a Sig, unwrap then recurse.  For
    an ``_InUnits``, use its stored ``.value`` (already a plain number
    on a consistent scale).  Plain numbers pass through ``float()``.

    Unlike ``float(physical)`` — which gives the auto-prefix-scaled
    DISPLAY magnitude and therefore differs element-to-element — this
    is consistent across a whole array.
    """
    if type(v).__name__ == "_InUnits":
        return float(v.value)
    if type(v).__name__ == "Sig" and hasattr(v, "value"):
        return _si_magnitude(v.value)
    if hasattr(v, "value") and hasattr(v, "dimensions"):
        # forallpeople Physical — ``.value`` is the SI-base magnitude.
        try:
            return float(object.__getattribute__(v, "value"))
        except Exception:
            try:
                return float(v.value)
            except Exception:
                return float("nan")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _array_has_units(elements) -> bool:
    """``True`` if any element of ``elements`` is a unit-bearing value
    (Physical, or Sig/`_InUnits` wrapping one).  Used to decide whether
    an array needs common-scale stripping."""
    for v in elements:
        probe = v
        while type(probe).__name__ == "Sig" and hasattr(probe, "value"):
            probe = probe.value
        if type(probe).__name__ == "_InUnits":
            probe = getattr(probe, "value", probe)
        if hasattr(probe, "value") and hasattr(probe, "dimensions"):
            return True
    return False


def _strip_unit_array(elements):
    """Strip an iterable of unit-bearing values to a ``dtype=float``
    ndarray on a SINGLE common scale.

    This exists because forallpeople auto-prefixes every Physical
    *independently*: in an array like ``[0 m, 700 μm, 2 mm, 5 mm]``
    each element picks its own prefix, so the bare ``float()`` display
    magnitudes are ``[0, 700, 2, 5]`` — element 1 is on a μm scale
    while the rest are on a mm scale.  Plotting those numbers directly
    puts element 1 a thousandfold too high.

    The fix: reduce every element to its **SI-base** magnitude (which
    is prefix-independent), so all elements share one scale.  No
    rescaling is applied here — the SI-base values are returned
    directly.  The caller is responsible for any single, uniform
    rescale it wants for tick readability.

    Returns the ``dtype=float`` ndarray of SI-base magnitudes.
    """
    return np.asarray([_si_magnitude(v) for v in elements], dtype=float)


def _is_caption(value) -> bool:
    """True iff ``value`` carries a ``▸`` free-text caption rather than a
    real physical unit.

    The ``▸`` operator produces an ``_InUnits`` wrapper whose
    ``unit_label`` is whatever string the user wrote — ``"test date"``,
    ``"trial"``, ``"count"``.  That is an axis CAPTION, not a unit, so it
    must be shown verbatim, never wrapped in the ``[...]`` unit brackets.
    A real unit instead comes from a unit-bearing ``Physical`` array
    (``t s``, ``v V``) and is NOT an ``_InUnits`` — those keep the
    bracket convention (``[s]``, ``[V]``), which is informative.

    Detecting the wrapper type is the whole test: ``▸`` is the only
    thing that builds an ``_InUnits``.
    """
    return type(value).__name__ == "_InUnits"


def _seq_is_dates(seq) -> bool:
    """True iff ``seq`` is a non-empty iterable whose first element is a
    ``date`` / ``datetime``.

    Used to detect a date x-axis — both for the unit-stripping
    short-circuit and for picking the axis-label/tick-format style.
    Checking only the first element is enough: the range builders
    produce homogeneous arrays, and a mixed date/number axis is not a
    case the toolkit supports.  ``datetime`` subclasses ``date``, so the
    single ``isinstance`` covers both.
    """
    try:
        for v in seq:
            return isinstance(v, _datetime.date)
    except TypeError:
        pass
    return False


def _strip_with_unit(arr):
    """Strip an array to plain floats AND return the matching unit
    label, both derived from the SAME reference element so they're
    guaranteed consistent.

    Returns ``(stripped_ndarray, unit_label_or_None)``.

    This replaces the older "call ``_strip_units`` and ``_unit_symbol``
    separately" approach, which could disagree: ``_unit_symbol`` reads
    the FIRST element while ``_strip_units`` scaled per-element.  After
    an offset subtraction the first element is often ``0`` — which
    forallpeople prints as ``0 m`` (base unit, no prefix) — so the
    label came out ``m`` while the data was mm-scale.  Plot ticks then
    silently disagreed with the axis label by a factor of 1000.

    Strategy:
    - If the array carries no units → strip plainly, label ``None``.
    - If it carries units → reduce all elements to SI-base magnitudes
      (consistent across the array), then pick ONE common display
      unit from the element of largest magnitude, and express every
      element in that unit.  Label and data now share that unit.
    """
    # Date / datetime axis.  ``date`` / ``datetime`` values (from the
    # DSL's ``"..."ₜᵢₘₑ`` literals and date ranges) must NOT go through
    # the unit-stripping path below — ``_strip_scalar`` does ``float(v)``
    # to remove units, and ``float(date)`` raises TypeError.  matplotlib
    # handles a date axis natively: handed a sequence of date/datetime
    # objects, ``ax.plot`` renders proper date ticks on its own.  So we
    # short-circuit: return the values as an object array.
    #
    # This must also see THROUGH an ``_InUnits`` wrapper: ``x ▸ "label"``
    # produces ``_InUnits`` whose ``.value`` is the date array and whose
    # ``.unit_label`` is the axis caption the user wrote.  A date axis
    # has no physical unit, but the ``▸`` label is still a wanted axis
    # title — so when the wrapped value is dates we return the dates
    # together with that label (not ``None``, which would silently drop
    # the user's ``▸ "test date"`` caption).  The plain ``_InUnits``
    # branch below cannot be reached for dates anyway — it does
    # ``float()`` on each element and would raise.
    #
    # A ``time`` or ``timedelta`` is deliberately NOT treated as an axis
    # type here — matplotlib has no native unit for either; if those
    # ever need plotting they should be converted to a number first.
    _date_label = None
    _date_src = arr
    if type(arr).__name__ == "_InUnits":
        _date_src = arr.value
        _date_label = arr.unit_label
    try:
        _probe = list(_date_src)
    except TypeError:
        _probe = None
    if _probe and all(isinstance(v, _datetime.date) for v in _probe):
        # isinstance(..., date) is True for datetime too (subclass).
        return np.asarray(_probe, dtype=object), _date_label

    # _InUnits (from the ``▶`` operator) — the value is ALREADY on a
    # single consistent scale (in_units did the conversion uniformly),
    # and the label is stored.  Use them directly; no per-element
    # auto-prefix problem here.
    if type(arr).__name__ == "_InUnits":
        inner = arr.value
        try:
            stripped = np.asarray(inner, dtype=float)
        except (TypeError, ValueError):
            stripped = np.asarray([_strip_scalar(v) for v in inner],
                                  dtype=float)
        return stripped, arr.unit_label

    # Materialise to a list once (arr may be a numpy iterator / CommaArray).
    elements = list(arr)

    if not _array_has_units(elements):
        # Plain numbers — simple strip, no unit label.
        stripped = np.asarray([_strip_scalar(v) for v in elements],
                              dtype=float)
        return stripped, None

    # SI-base magnitudes — prefix-independent, consistent scale.
    si_values = np.asarray([_si_magnitude(v) for v in elements], dtype=float)

    # Pick the reference element: largest absolute SI magnitude.  Using
    # the peak (not element 0, which may be 0 after offset removal)
    # ensures the dominant data picks the prefix.
    finite = si_values[np.isfinite(si_values)]
    peak = np.max(np.abs(finite)) if finite.size else 0.0

    # Find the actual element at (or near) that peak so we can ask
    # forallpeople how IT chooses to display — that gives us a real
    # unit string with the right prefix.
    ref_label = None
    ref_display_per_si = 1.0   # display_magnitude / si_magnitude
    if peak > 0.0:
        peak_idx = int(np.argmax(np.abs(si_values)))
        ref = elements[peak_idx]
        # Unwrap Sig to reach the Physical / _InUnits.
        while type(ref).__name__ == "Sig" and hasattr(ref, "value"):
            ref = ref.value
        ref_label = _unit_symbol(ref)
        # Ratio between forallpeople's display magnitude and the SI
        # magnitude for the reference element.  Multiplying SI values
        # by this ratio expresses the whole array in the reference
        # element's display unit.
        ref_si = si_values[peak_idx]
        if ref_si != 0:
            try:
                ref_display_per_si = float(ref) / ref_si
            except Exception:
                ref_display_per_si = 1.0

    stripped = si_values * ref_display_per_si
    return stripped, ref_label


def _strip_units(value):
    """Strip units from ``value`` for handing to matplotlib.

    Returns a numpy-compatible numeric value: a float, an ndarray of
    floats, or the original value if no stripping is needed.

    The motivation: matplotlib can't natively plot ndarrays of Physical
    objects (it tries to convert them and fails with a confusing error).
    Stripping to plain floats gives matplotlib clean numbers while we
    carry the unit symbol separately in the axis label.

    Uses the **display magnitude** of each Physical — i.e. the
    auto-prefix-scaled value (``400.0`` for a Physical that displays
    as ``"400 nm"``), NOT the SI-base ``.value`` (``4e-7``).  Two
    reasons:

    1. The axis label shows the display unit, so the numbers on the
       axis ticks should match that unit.  An array of [400, 500, 600]
       nm should show ticks at 400, 500, 600 — not at 4e-7, 5e-7, 6e-7.

    2. The display magnitudes line up with what symbolic-plot ranges
       use (because that ``(400, 800)`` was typed as numbers in nm).
       So data series and overlaid symbolic curves share an x-scale.

    Handles six shapes:
    1. A bare ``Physical``: return ``float(value)`` (display magnitude)
    2. A ``Sig`` wrapping anything: unwrap and recurse
    3. An ``_InUnits`` (from ``▶`` operator): return its already-
       computed ``.value`` directly — the unit-conversion is the
       *whole point* of ``▶``, so the value is already in the right
       scale and we'd waste work re-stripping.
    4. An ndarray (dtype=object) of Physicals/Sigs: per-element strip
    5. An ndarray of plain floats: pass through
    6. A list/tuple: convert to ndarray of stripped values
    """
    # _InUnits from the ``▶`` operator — already converted to the
    # target unit's scale.  ``.value`` is a scalar or numpy array of
    # floats already.  We check this BEFORE Sig because _InUnits has
    # ``.value`` too but is not a Sig subclass.
    if type(value).__name__ == "_InUnits":
        return value.value

    # Unwrap Sig
    if type(value).__name__ == "Sig" and hasattr(value, "value"):
        return _strip_units(value.value)

    # ndarray
    if isinstance(value, np.ndarray):
        if value.dtype == object and value.size > 0:
            # An object-dtype array holds Python objects — could be
            # Physicals, Sigs, or plain numbers.  When ANY element
            # carries units, strip the whole array on a common SI-base
            # scale: forallpeople auto-prefixes each element on its own,
            # so per-element ``float()`` would put e.g. a 700 μm value
            # and a 2 mm value on different scales (700 vs 2) even
            # though 700 μm < 2 mm.  See _strip_unit_array.
            if _array_has_units(value.flat):
                return _strip_unit_array(value.flat).reshape(value.shape)
            # No units — plain per-element strip is fine.
            return np.array([_strip_scalar(v) for v in value.flat]) \
                     .reshape(value.shape)
        return value

    # Bare Physical
    if hasattr(value, "value") and hasattr(value, "dimensions"):
        try:
            return float(value)  # display magnitude
        except Exception:
            # forallpeople's __float__ can crash for magnitudes outside
            # its prefix table (yocto..yotta).  Fall back to SI-base
            # ``.value`` directly — this loses the "ticks-match-axis-
            # label" alignment, but the alternative is a hard crash.
            # See _strip_scalar for the same fallback at element level.
            return float(value.value)

    # List / tuple
    if isinstance(value, (list, tuple)):
        # Same common-scale rule as for object ndarrays above.
        if _array_has_units(value):
            return _strip_unit_array(value)
        stripped = [_strip_scalar(v) for v in value]
        return np.asarray(stripped, dtype=float)

    return value


def _strip_scalar(v):
    """Strip a single scalar element down to a plain Python number.

    Uses display magnitude for Physicals — see ``_strip_units`` for
    why.  Sig-of-Physical, Sig-of-int, bare Physical, plain int/float
    — all end up as a float (or pass through if already numeric).

    Robust to forallpeople's ``__float__`` raising ``KeyError`` for
    magnitudes outside its prefix table (electron mass at 9e-31, solar
    mass at 2e30, etc.).  Falls back to the SI-base ``.value`` in those
    cases — the ticks won't auto-prefix nicely, but the plot won't
    crash.  For these huge-magnitude cases users typically prefer the
    ``▶ M☉`` idiom anyway, which routes around the issue entirely.
    """
    if type(v).__name__ == "_InUnits":
        return v.value  # already stripped to the target's scale
    if type(v).__name__ == "Sig" and hasattr(v, "value"):
        return _strip_scalar(v.value)
    if hasattr(v, "value") and hasattr(v, "dimensions"):
        try:
            return float(v)  # display magnitude
        except Exception:
            # forallpeople choked — fall back to SI-base magnitude.
            return float(v.value)
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def _is_sympy(expr) -> bool:
    """``True`` if ``expr`` is a sympy expression (Basic subclass).

    Duck-typed by checking for ``free_symbols`` and ``subs``, both of
    which are core sympy.Basic methods.  This avoids importing sympy
    just to do an isinstance check.

    Also unwraps ``Sig`` — the DSL routinely wraps sympy expressions in
    ``Sig`` (e.g. ``fit_expr := 0.05 * x**2`` produces ``Sig(symbolic, 1)``)
    so a Sig-wrapping-symbolic should be treated as symbolic for plotting
    purposes.
    """
    # Unwrap Sig
    if type(expr).__name__ == "Sig" and hasattr(expr, "value"):
        return _is_sympy(expr.value)
    return hasattr(expr, "free_symbols") and hasattr(expr, "subs")


def _unwrap_sympy(expr):
    """If ``expr`` is a Sig wrapping a sympy expression, return the
    underlying sympy.  Otherwise return ``expr`` unchanged.

    Used by the symbolic-plot path before handing the expression to
    ``sympy.lambdify``, which doesn't know how to handle Sig.
    """
    if type(expr).__name__ == "Sig" and hasattr(expr, "value"):
        return _unwrap_sympy(expr.value)
    return expr


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def plot(*args, title=None, xlabel=None, ylabel=None,
         style=None, show=True, ax=None, return_ax=False,
         fit=None, fit_label=None, fit_style=None,
         theme=None, **kwargs):
    """Unit-aware, symbolic-aware plot wrapper.

    A single call may contain multiple series; each series is one of:

    - ``y_array`` — y-values only; x defaults to ``range(len(y))``.
    - ``x_array, y_array`` — paired arrays.
    - ``expr, var, (xmin, xmax)`` — sympy expression swept over a
      range.  ``expr`` is a sympy expression (possibly Sig-wrapped by
      the DSL); ``var`` is the sympy Symbol that varies; the tuple
      gives the inclusive range.

    Any series may be followed by an annotation:

    - a string — used as the legend label
    - a ``(label, style)`` tuple — for per-series styling, where
      ``style`` is a matplotlib format string like ``'o'`` (markers),
      ``'-'`` (line), ``'r--'`` (red dashed), etc.

    Multiple series in one call are drawn on the same axes, sharing
    xlabel / ylabel / title.  This is what makes the common "scatter
    data + line fit" plot a single-line call::

        plot(x_data, y_data, ("measured", "o"),
             fit_expr, x, (xmin, xmax), ("model", "-"))

    For the common "fit a line / polynomial to this data" pattern,
    pass ``fit=`` instead of constructing the symbolic expression
    manually::

        plot(x, y, fit=1)        # data + linear fit
        plot(x, y, fit=2)        # data + quadratic fit
        plot(x, y, fit=True)     # alias for fit=1

    ``fit_label=`` and ``fit_style=`` customize the fit line; defaults
    are ``"fit: y = ..."`` (with the polynomial spelled out) and a
    dashed line.

    Series colours come from the toolkit's NCS cycle (blue ``#218bc0``,
    red ``#cc1e3b``, green ``#19a86d``, orange, violet, teal,
    yellow-green, yellow — see ``_NCS_CYCLE``), matching the syntax
    highlighter's base palette.  A ``theme=`` changes background,
    grid, and fonts but the NCS cycle still decides series colours;
    per-series format strings (``'r--'`` etc.) override as usual.

    Pass ``theme=`` to change the visual appearance for THIS plot only
    (theme is applied via a matplotlib style context manager — it does
    NOT poison the rcParams of later plots in the same session).
    Short aliases that map to matplotlib's built-in style names::

        theme="dark"        → dark_background       (white-on-black)
        theme="darkgrid"    → seaborn-v0_8-darkgrid (seaborn dark with grid)
        theme="whitegrid"   → seaborn-v0_8-whitegrid (seaborn light with grid)
        theme="ggplot"      → ggplot                (R's ggplot2 colours)
        theme="538"         → fivethirtyeight       (FiveThirtyEight style)
        theme="solarized"   → Solarize_Light2       (low-contrast cream)
        theme="bmh"         → bmh                   (Bayesian Methods for Hackers)
        theme="paper"       → seaborn-v0_8-paper    (small font, publication)
        theme="poster"      → seaborn-v0_8-poster   (large font)
        theme="presentation"→ seaborn-v0_8-talk     (medium font, slides)
        theme="colorblind"  → seaborn-v0_8-colorblind (CVD-safe palette)

    Any full matplotlib style name (see ``matplotlib.style.available``)
    also works as a passthrough — ``theme="grayscale"``,
    ``theme="seaborn-v0_8-deep"``, etc.  Pass a list to compose styles
    (matplotlib applies them in order, later styles win on conflict):
    ``theme=["seaborn-v0_8-paper", "seaborn-v0_8-colorblind"]``.

    To list all available themes::

        from utils import plotting
        plotting.list_themes()

    Units carried by any series propagate to axis labels — if x is
    ``[1.0, 2.0, 3.0] V``, the x-axis label includes "[V]"
    automatically.  Symbolic series use the variable name as a
    fallback x-label when there's no unit to extract.

    Keyword arguments::

        title=...   plot title (no auto-detection — pass it if you
                    want one)
        xlabel=...  x-axis label override (default: extracted from
                    units, or variable name for purely symbolic)
        ylabel=...  y-axis label override
        style=...   matplotlib style string applied to ALL series
                    (e.g. 'o' for markers).  For per-series styling
                    use ``ax = plot(...)`` then ``ax.lines[k]...``.
        show=...    call plt.show() at the end (default True)
        ax=...      matplotlib Axes to draw into (default: gca())

    Returns the matplotlib ``Axes`` so further customisation works
    naturally — ``ax.set_xlim(...)``, ``ax.grid()``, etc.

    Examples::

        # One data series with units
        plot([1, 2, 3] * V, [0.1, 0.2, 0.3] * A, "I-V curve",
             title="Ohm's law")

        # One symbolic series
        symbols: x
        plot(sin(x)/x, x, (-10, 10))

        # Data + fit overlay in a single call
        plot(x_meas, y_meas, "data",
             fit_expr, x, (x_meas[0], x_meas[-1]), "fit",
             title="Gaussian fit")
    """
    import matplotlib.pyplot as plt
    import matplotlib.style as _mplstyle

    # Resolve the theme to a matplotlib style spec.  The NCS series-
    # colour cycle (author's base palette — see _NCS_CYCLE) applies to
    # EVERY plot: with an explicit ``theme=`` the theme is composed
    # under it (theme decides background/grid/fonts, the NCS cycle
    # still decides series colours); without one, only the cycle is
    # applied.  ``axes.prop_cycle`` takes effect when an Axes is
    # CREATED, so overlaying onto a pre-existing Axes keeps that Axes'
    # cycle — same overlay semantics as before.
    if theme is not None:
        resolved = _resolve_theme(theme)
        specs = list(resolved) if isinstance(resolved, (list, tuple)) \
            else [resolved]
        theme_ctx = _mplstyle.context(specs + [_ncs_rc()])
        # Force a fresh figure so the theme's background/grid/fonts
        # actually take effect.  When ax is supplied, we honour it.
        force_fresh = (ax is None)
    else:
        theme_ctx = _mplstyle.context(_ncs_rc())
        force_fresh = False

    with theme_ctx:
        if force_fresh:
            _fig, ax = plt.subplots()
        return _plot_impl(args, title, xlabel, ylabel, style, show, ax,
                          return_ax, fit, fit_label, fit_style, kwargs)


# ---------------------------------------------------------------------------
# NCS series-colour cycle
# ---------------------------------------------------------------------------
# Series colours are drawn from the NCS colour circle, anchored on the
# four elementary hues chosen for the toolkit (the same base palette
# the syntax highlighter uses): yellow #fbd924, red #cc1e3b, blue
# #218bc0, green #19a86d.  The other four are the circle's quadrant
# midpoints — Y50R orange, R50B violet, B50G teal, G50Y yellow-green —
# giving an 8-colour cycle.  Ordered for maximum adjacent contrast so
# consecutive series are easy to tell apart; the pure NCS yellow is
# deepened a touch (#e0b400) so a 1-px line stays visible on white.
_NCS_CYCLE = [
    "#218bc0",  # B    blue
    "#cc1e3b",  # R    red
    "#19a86d",  # G    green
    "#ec7c26",  # Y50R orange
    "#7d4a99",  # R50B violet
    "#1d9a97",  # B50G teal
    "#8fc03b",  # G50Y yellow-green
    "#e0b400",  # Y    yellow (deepened for line visibility)
]


def _ncs_rc():
    """rcParams dict applying the NCS series-colour cycle."""
    from cycler import cycler
    return {"axes.prop_cycle": cycler(color=_NCS_CYCLE)}


# ---------------------------------------------------------------------------
# Theme name resolution
# ---------------------------------------------------------------------------

# Short, memorable theme names → matplotlib style spec.  When the user
# passes one of these as ``theme=``, ``_resolve_theme`` maps it; if the
# name isn't here, it's passed through to matplotlib unchanged, so any
# of the 29 ``matplotlib.style.available`` entries works.
_THEME_ALIASES = {
    # Dark / light contrast switches
    "dark":          "dark_background",
    "light":         "default",
    "default":       "default",
    # The NCS series-colour cycle is ALWAYS active (see _NCS_CYCLE);
    # this alias exists so ``theme="ncs"`` reads naturally and simply
    # means "default style + the NCS cycle".
    "ncs":           "default",
    # Seaborn-flavoured (built into matplotlib as ``seaborn-v0_8-*``,
    # no actual seaborn import needed)
    "darkgrid":      "seaborn-v0_8-darkgrid",
    "whitegrid":     "seaborn-v0_8-whitegrid",
    "seaborn":       "seaborn-v0_8",            # default seaborn look
    "seaborn-dark":  "seaborn-v0_8-dark",
    "seaborn-white": "seaborn-v0_8-white",
    "ticks":         "seaborn-v0_8-ticks",
    "deep":          "seaborn-v0_8-deep",
    "muted":         "seaborn-v0_8-muted",
    "pastel":        "seaborn-v0_8-pastel",
    "bright":        "seaborn-v0_8-bright",
    "colorblind":    "seaborn-v0_8-colorblind",
    "colourblind":   "seaborn-v0_8-colorblind",  # for the British speakers
    # Sizing variants — useful for slides, papers, posters
    "paper":         "seaborn-v0_8-paper",
    "notebook":      "seaborn-v0_8-notebook",
    "talk":          "seaborn-v0_8-talk",
    "presentation":  "seaborn-v0_8-talk",
    "poster":        "seaborn-v0_8-poster",
    # Editorial / statistical styles
    "ggplot":        "ggplot",
    "538":           "fivethirtyeight",
    "fivethirtyeight": "fivethirtyeight",
    "bmh":           "bmh",
    "solarized":     "Solarize_Light2",
    "grayscale":     "grayscale",
    "greyscale":     "grayscale",
    # Tableau colour palette (CVD-friendly)
    "tableau":       "tableau-colorblind10",
}


def _resolve_theme(theme):
    """Map a user-supplied theme spec to a matplotlib style spec.

    Accepts:
    - a short alias (``"dark"``, ``"whitegrid"``, etc.) → mapped via
      ``_THEME_ALIASES``
    - a full matplotlib style name (``"seaborn-v0_8-deep"``, etc.) →
      passed through verbatim
    - a list of either kind → matplotlib applies them in order, later
      styles winning on conflict

    Doesn't validate against ``matplotlib.style.available`` — if you
    pass a typo, matplotlib raises its own clear error message at
    apply time.
    """
    if isinstance(theme, (list, tuple)):
        # Compose: each element resolved independently.
        return [_THEME_ALIASES.get(t, t) for t in theme]
    return _THEME_ALIASES.get(theme, theme)


def list_themes():
    """Print the available theme names — both the short aliases and
    the underlying matplotlib styles.  Useful in a Jupyter cell to
    figure out what to pass to ``plot(theme=...)``.
    """
    print("Short aliases (sorted):")
    seen_targets = set()
    for alias in sorted(_THEME_ALIASES):
        target = _THEME_ALIASES[alias]
        marker = " (alias)" if target in seen_targets else ""
        print(f"  {alias:<20} → {target}{marker}")
        seen_targets.add(target)

    print("\nAll matplotlib styles (passthrough):")
    import matplotlib.style as _mplstyle
    for s in _mplstyle.available:
        print(f"  {s}")


def _plot_impl(args, title, xlabel, ylabel, style, show, ax,
               return_ax, fit, fit_label, fit_style, kwargs):
    """Inner implementation of ``plot()`` — kept separate from ``plot``
    so the theme context-manager can wrap the entire body cleanly.
    All arguments are positional here because there's no public API
    benefit to keyword-only at this layer.

    Note: when ``theme`` was set, the outer ``plot()`` wrapper has
    already created a fresh figure inside the style context and
    passed the new axes through ``ax``.  When ``theme`` was None and
    ``ax`` is None, we fall through to ``plt.gca()`` so subsequent
    calls in the same cell can overlay on the same axes (the
    original behaviour, preserved for back-compat).
    """
    import matplotlib.pyplot as plt

    if ax is None:
        ax = plt.gca()

    if not args:
        raise TypeError("plot() requires at least one positional argument")

    # ---- Parse into series ----
    # A series is a dict with: x, y, label, xunit, yunit, var_name (for
    # symbolic — to use as a fallback xlabel).
    series_list = _parse_series(args)

    # ---- Optional auto-fit ----
    # If the user passed ``fit=N`` (or ``fit=True``), find the first
    # paired-data series and overlay a polynomial fit on it.  Built
    # in numpy-space (not symbolic) because the data is already in
    # display-magnitude floats — generating ``slope · k + intercept``
    # symbolically and re-evaluating would be a waste of work.
    if fit is not None and fit is not False:
        _fit_deg = 1 if fit is True else int(fit)
        _add_fit_series(series_list, _fit_deg, fit_label, fit_style)

    # ---- Plot each series ----
    deduced_xlabel = None
    deduced_ylabel = None

    for series in series_list:
        x_data = series["x"]
        y_data = series["y"]
        label = series.get("label")
        line_kwargs = dict(kwargs)
        if label is not None:
            line_kwargs.setdefault("label", label)

        # Per-series style trumps the global style= kwarg; if neither
        # is set, matplotlib's default applies (a coloured line).
        series_style = series.get("style") or style
        if series_style is not None:
            ax.plot(x_data, y_data, series_style, **line_kwargs)
        else:
            ax.plot(x_data, y_data, **line_kwargs)

        # Capture the FIRST deduced labels we find — later series
        # don't override them.  Rationale: the first series usually
        # determines the axis interpretation, and a fit-curve
        # overlay should adopt the data's axis labels.
        if deduced_xlabel is None:
            xunit = series.get("xunit")
            var_name = series.get("var_name")
            if xunit and var_name:
                # Symbolic series with explicit unit (``t ▶ ms`` idiom)
                # — combine: ``t [ms]``.  The symbol is what the user
                # wrote in source; the unit clarifies the axis scale.
                deduced_xlabel = f'{var_name} [{xunit}]'
            elif xunit:
                # A label on the x-axis is either a real physical unit
                # or a ``▸`` free-text caption.  The ``[...]`` brackets
                # specifically mean "this is the unit" — so they are
                # applied ONLY to a genuine unit (from a unit-bearing
                # array, ``t s`` → ``[s]``).  A ``▸`` caption is the
                # user's own axis title and is shown verbatim; if they
                # want brackets they can type them.  This is the same
                # rule on every axis type — a date axis is no longer a
                # special case, it just always carries a ``▸`` caption.
                if series.get("xcaption"):
                    deduced_xlabel = str(xunit)
                else:
                    deduced_xlabel = f'[{xunit}]'
            elif var_name:
                # Pure symbolic series — just the symbol name.
                deduced_xlabel = var_name
        if deduced_ylabel is None and series.get("yunit"):
            # Same rule as the x-axis: a ``▸`` caption verbatim, a real
            # unit bracketed.
            if series.get("ycaption"):
                deduced_ylabel = str(series["yunit"])
            else:
                deduced_ylabel = f'[{series["yunit"]}]'

    # ---- Apply labels ----
    if xlabel is None and deduced_xlabel:
        xlabel = deduced_xlabel
    if ylabel is None and deduced_ylabel:
        ylabel = deduced_ylabel
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)

    # ---- Date axis: tidy tick labels ----
    # When the x data is dates/datetimes, matplotlib's default tick
    # labelling produces wide ``MM-DD HH`` strings that collide when the
    # axis is narrow or has many ticks (they print on top of each
    # other).  ``AutoDateLocator`` + ``ConciseDateFormatter`` pick a
    # sensible tick spacing and the most compact label that still
    # disambiguates; ``autofmt_xdate`` rotates them so neighbours don't
    # overlap.  Only applied when the data really is temporal, so
    # numeric / unit plots are untouched.
    x_is_dates = any(_seq_is_dates(s["x"]) for s in series_list)
    if x_is_dates:
        import matplotlib.dates as _mdates
        _loc = _mdates.AutoDateLocator()
        ax.xaxis.set_major_locator(_loc)
        ax.xaxis.set_major_formatter(_mdates.ConciseDateFormatter(_loc))
        # Rotate + right-align the tick labels.  ``autofmt_xdate`` works
        # on the figure; fall back to a manual rotation if the Axes was
        # supplied by the caller and shares a figure with other plots.
        fig = ax.get_figure()
        try:
            fig.autofmt_xdate()
        except Exception:
            for _lbl in ax.get_xticklabels():
                _lbl.set_rotation(30)
                _lbl.set_horizontalalignment("right")

    if title:
        ax.set_title(title)

    # ---- Legend if any series had a label ----
    if any(line.get_label() and not line.get_label().startswith("_")
           for line in ax.get_lines()):
        ax.legend()

    if show:
        plt.show()

    # Return ``None`` by default — Jupyter auto-displays the last
    # expression in a cell, and the matplotlib ``Axes`` repr
    # ``<Axes: xlabel='[ly]', ylabel='[M☉]'>`` looks like an error
    # message printed after the figure.  Callers who want to chain
    # customizations (``plot(...).set_xlim(...)`` etc.) can pass
    # ``return_ax=True`` to get the Axes back; the more idiomatic
    # alternative is to grab it with ``plt.gca()`` after the call.
    if return_ax:
        return ax
    return None


# ---------------------------------------------------------------------------
# Curve fitting — unit-aware wrappers around numpy.polyfit
# ---------------------------------------------------------------------------

def polyfit(x, y, deg):
    """Unit-aware wrapper around ``numpy.polyfit``.

    ``numpy.polyfit`` requires ``dtype=float`` ndarrays and rejects the
    toolkit's unit-bearing ``Sig`` / ``Physical`` / ``CommaArray`` types
    (you get ``ValueError: data type <class 'numpy.object_'> not
    inexact``).  This wrapper strips the units, performs the fit, and
    re-attaches units to each coefficient based on its degree.

    For a fit ``y = a_n x^n + a_{n-1} x^{n-1} + ... + a_0``:

    - ``a_k`` has units ``[y] / [x]^k``

    so the constant term ``a_0`` has the same units as ``y``, and the
    linear term ``a_1`` has units ``[y] / [x]``.

    Returns the coefficients as a tuple ``(a_n, a_{n-1}, ..., a_0)``,
    matching ``numpy.polyfit``'s ordering (highest degree first).  Each
    coefficient is a Physical when its dimensions are non-trivial, a
    plain float otherwise.

    Examples::

        # Linear fit on indexed measurements in mm
        x ← [0, 1, 6, 12, 13, 14]
        y ← [129.9, 130.7, 132.3, 135.5, 135.8, 137.0] mm
        slope, intercept ← polyfit(x, y, 1)
        # slope is mm per index ≈ 0.45 mm
        # intercept is mm        ≈ 130.0 mm

        # Same but with units on x too
        t ← [0, 1, 2, 3, 4] s
        v ← [0, 9.8, 19.6, 29.4, 39.2] m/s
        a, v0 ← polyfit(t, v, 1)
        # a is m/s² (acceleration)
        # v0 is m/s

    For the common linear-fit case, see :func:`linefit` which is a
    thin wrapper that returns ``(slope, intercept)`` more readably.
    """
    deg = _to_int(deg)
    x_arr, x_unit_obj = _strip_for_fit(x)
    y_arr, y_unit_obj = _strip_for_fit(y)

    coeffs = np.polyfit(x_arr, y_arr, deg)

    # Re-attach units.  numpy returns coefficients highest-degree first,
    # so coeffs[k] is the coefficient of x^(deg-k), which has units
    # [y] / [x]^(deg-k).
    return tuple(
        _reapply_units(float(c), y_unit_obj, x_unit_obj, deg - k)
        for k, c in enumerate(coeffs)
    )


def linefit(x, y):
    """Linear fit ``y ≈ slope · x + intercept``.

    Convenience wrapper around :func:`polyfit` with ``deg=1`` — returns
    ``(slope, intercept)`` in that order (the more intuitive
    "rise-over-run, then y-zero" reading), where ``polyfit`` itself
    returns ``(slope, intercept)`` because numpy lists the highest-
    degree coefficient first.  The two functions return identical
    tuples for ``deg=1``; ``linefit`` exists mainly so the call site
    reads as the engineer would describe the operation.

    Units on the returned values:

    - ``slope`` carries ``[y] / [x]`` units
    - ``intercept`` carries ``[y]`` units

    Example::

        x ← [0, 1, 6, 12, 13, 14]               # dimensionless index
        y ← [129.9, 130.7, ..., 137.0] mm
        slope, intercept ← linefit(x, y)
        # slope ≈ 0.45 mm per index
        # intercept ≈ 130.0 mm
    """
    return polyfit(x, y, 1)


def _to_int(deg):
    """Unwrap a possibly Sig-wrapped polynomial degree to a plain int."""
    while type(deg).__name__ == "Sig" and hasattr(deg, "value"):
        deg = deg.value
    return int(deg)


def _add_fit_series(series_list, deg, label, style):
    """Find the first paired-data series in ``series_list`` and append
    a fit-line series.  Used by ``plot(x, y, fit=N)``.

    Operates on the already-stripped (display-magnitude) arrays, so
    the fit lives in the same numeric space as the data and lines up
    visually without any unit gymnastics.  Generates ``n=200`` points
    spanning the data range — enough resolution for any reasonable
    polynomial degree.

    Auto-generates a legend label if none given.  For degree 1 the
    label spells out ``y = m·x + b`` with the coefficients, otherwise
    it's just ``"fit (degree N)"`` — for high-degree polynomials the
    coefficient list would dwarf the plot.

    Style defaults to ``"--"`` (dashed) so the fit visually distinguishes
    from the data points.
    """
    # Find the first paired-data series.  Data series (as opposed to
    # symbolic) have integer x indices or unit-stripped float arrays
    # — distinguished from symbolic series by the absence of var_name.
    target = None
    for s in series_list:
        if s.get("var_name") is None and len(s["x"]) >= 2:
            target = s
            break
    if target is None:
        # No data to fit on — silently skip.  Could raise instead, but
        # the visual result (no fit line) is informative enough.
        return

    x = np.asarray(target["x"], dtype=float)
    y = np.asarray(target["y"], dtype=float)

    coeffs = np.polyfit(x, y, deg)            # highest-degree first
    # Build the fit curve as a dense sweep across the data x-range.
    x_fit = np.linspace(x.min(), x.max(), 200)
    y_fit = np.polyval(coeffs, x_fit)

    if label is None:
        if deg == 1:
            slope, intercept = float(coeffs[0]), float(coeffs[1])
            sign = "-" if intercept < 0 else "+"
            label = f"fit: y = {slope:.4g}·x {sign} {abs(intercept):.4g}"
        else:
            label = f"fit (degree {deg})"

    fit_style = style if style is not None else "--"

    series_list.append({
        "x": x_fit,
        "y": y_fit,
        "label": label,
        "style": fit_style,
        "xunit": target.get("xunit"),
        "yunit": target.get("yunit"),
        "var_name": None,
    })


def _strip_for_fit(arr):
    """Strip ``arr`` to a ``dtype=float`` ndarray, returning
    ``(stripped_array, unit_carrier)`` where ``unit_carrier`` is one of:

    - a sample ``Physical`` drawn from the input (when the data carries
      real forallpeople units) — used by ``_reapply_units`` to rebuild
      dimensioned coefficients,
    - a ``str`` (when the data is an ``_InUnits`` carrying a display
      label, or a units array reduced to a common display unit) —
      used to re-label coefficients,
    - ``None`` when the input is plain dimensionless numbers.

    Critically, the numeric strip is done on a SINGLE common scale via
    ``_strip_with_unit``.  forallpeople auto-prefixes each Physical
    independently, so a naive per-element ``float()`` puts e.g. a
    700 μm value and a 2 mm value on different scales (700 vs 2).  A
    fit on those mixed-scale numbers produces a nonsense slope.  The
    common-scale strip fixes this — every element ends up expressed in
    the same unit (that of the array's dominant element).
    """
    # ``_InUnits`` fast-path: the whole array is wrapped in a single
    # ``_InUnits`` (the ``y := [...] ▶ μm`` idiom).  Its ``.value`` is
    # already a clean numeric array on one scale; ``.unit_label`` is
    # the carrier.
    if type(arr).__name__ == "_InUnits":
        inner = arr.value
        try:
            stripped = np.asarray(inner, dtype=float)
        except (TypeError, ValueError):
            stripped = np.asarray([_strip_scalar(v) for v in inner],
                                  dtype=float)
        return stripped, arr.unit_label

    # Common-scale strip — returns the numeric array plus the unit
    # label of the dominant element.  This is the SAME routine the
    # plot's data-series path uses, so a fit overlaid on a data series
    # is guaranteed to share the data's scale.
    stripped, unit_label = _strip_with_unit(arr)

    if unit_label is None:
        # Dimensionless data — no unit to re-attach to coefficients.
        return stripped, None

    # The data carries units.  ``_reapply_units`` accepts either a
    # Physical sample or a string label.  We already have the common
    # display-unit label as a string, which is exactly the right
    # carrier — it tells _reapply_units "tag coefficients with this
    # unit name" without trying to do dimensional Physical arithmetic
    # (which would re-introduce the auto-prefix inconsistency).
    return stripped, unit_label


def _reapply_units(value, y_unit_obj, x_unit_obj, power):
    """Attach units to a fit coefficient.

    ``value`` is a plain float (the coefficient as numpy returned it).
    ``y_unit_obj`` / ``x_unit_obj`` are unit carriers from
    ``_strip_for_fit`` — each is a ``Physical``, a ``str`` label, or
    ``None``.  ``power`` is the x-exponent for this coefficient: 0 for
    the constant term, 1 for the linear term, etc.

    Returns:

    - a Physical with units ``[y] / [x]^power`` when y carries real
      forallpeople units,
    - an ``_InUnits`` with a string label when y carries a display
      label (the ``▶ μm`` / ``▶ "element"`` case) — the label is a
      display hint, so we don't attempt dimensional division; we just
      tag the coefficient with y's label (for power-0 / dimensionless-x
      cases) or a composed ``y/x`` label otherwise,
    - a plain float when y is dimensionless.

    Implementation note: for the Physical case we build the unit
    carriers by dividing each sample Physical by its **display
    magnitude** (``float(physical)``), not its SI-base ``.value``.
    Choosing the display magnitude preserves the user's prefix —
    ``129.9 mm / 129.9 = 1 mm`` keeps the result in mm, whereas
    dividing by ``0.1299 m`` would yield ``1 m`` and force
    ``0.4535 mm`` to render as ``453.5 μm``.
    """
    # Dimensionless y → result is a plain float regardless of x
    if y_unit_obj is None:
        return value

    # String-label carrier (the ``_InUnits`` case).  No genuine
    # dimensional arithmetic — the label is a display hint.  We tag
    # the coefficient with an ``_InUnits`` so it still prints with the
    # unit, but we don't try to divide labels.  For a dimensionless x
    # (the typical "element index" case) or the constant term, the
    # label is simply y's label.  When x ALSO has a label and power>0,
    # we compose a ``"y/x"`` (or ``"y/x²"``) string so the coefficient
    # at least reads sensibly.
    if isinstance(y_unit_obj, str):
        from .sigfig import _InUnits as _IU
        if x_unit_obj is None or power == 0 or not isinstance(x_unit_obj, str):
            label = y_unit_obj
        else:
            # Compose y/x or y/x^power.  Superscript the power for
            # readability (matches the unit-label prettifier elsewhere).
            if power == 1:
                label = f"{y_unit_obj}/{x_unit_obj}"
            else:
                sup = str(power).translate(str.maketrans("0123456789",
                                                          "⁰¹²³⁴⁵⁶⁷⁸⁹"))
                label = f"{y_unit_obj}/{x_unit_obj}{sup}"
        result = _IU.__new__(_IU)
        result.value = value
        result.unit_label = label
        result.sf = float("inf")
        result.quantity = None
        return result

    y_display_mag = float(y_unit_obj) or 1.0
    y_unit = y_unit_obj / y_display_mag

    if x_unit_obj is None or power == 0 or isinstance(x_unit_obj, str):
        # Constant term, dimensionless x, or x carries only a display
        # label (no real unit) — no x-power factor in the dimension.
        return value * y_unit

    # Defend against a zero first-element in x (e.g. ``t := [0, 1, 2] s``
    # — first sample is ``0 s``, ``float(0 s) = 0.0``, division blows
    # up).  Walk x looking for a non-zero sample if needed.
    x_display_mag = float(x_unit_obj)
    if x_display_mag == 0:
        # The sample carrier is zero — we can still extract its unit
        # by adding any positive offset, but the simpler fix is to
        # use SI-base for x only.  This is the case the user is
        # unlikely to hit if they pick reasonable sample data, but
        # ``[0, 1, 2] s`` is common enough to handle gracefully.
        x_si = getattr(x_unit_obj, "value", 0)
        if x_si != 0:
            x_unit = x_unit_obj / x_si
        else:
            # Both zero — can't recover the unit.  Fall back to
            # treating x as dimensionless and let the user notice
            # the units are off in the result.
            return value * y_unit
    else:
        x_unit = x_unit_obj / x_display_mag

    # ``y_unit / x_unit**power`` gives the correct dimensional carrier;
    # multiply by ``value`` to get the final coefficient.
    return value * y_unit / (x_unit ** power)


def _parse_series(args):
    """Split ``args`` into a list of series specs.

    Each spec dict has keys: ``x``, ``y``, ``label`` (str or None),
    ``style`` (str or None), ``xunit`` (str or None), ``yunit`` (str
    or None), ``var_name`` (str or None — set for symbolic series to
    use as a fallback xlabel).

    Walks the arg list left-to-right.  At each position, decide what
    kind of chunk starts here:

    - sympy expression at args[i]: a *symbolic* series consuming
      args[i:i+3] (expr, var, (xmin, xmax)).  Optional annotation
      follows.
    - two consecutive array-likes at args[i:i+2]: a *paired-data*
      series consuming args[i:i+2].  Optional annotation follows.
    - single array-like at args[i] with no second array following
      (end of args or next is a string/sympy/tuple): a *y-only*
      series with synthesised x.  Optional annotation follows.

    The optional annotation is either:
    - a plain string acting as the legend label, or
    - a ``(label, style)`` tuple where ``label`` is a string and
      ``style`` is a matplotlib format string like ``'o'`` or ``'r--'``.

    Per-series style is what makes the "scatter data + line fit"
    pattern work in one call::

        plot(x_data, y_data, ("data", "o"),
             fit_expr, x, (0, 5), ("fit", "-"))
    """
    series_list = []
    i = 0
    while i < len(args):
        a = args[i]

        if _is_sympy(a):
            # Symbolic series: expect expr, var, range
            if i + 2 >= len(args):
                raise TypeError(
                    "symbolic plot series needs (expr, var, (xmin, xmax)); "
                    f"got only {len(args) - i} arg(s) starting at position {i}"
                )
            expr = a
            var = args[i + 1]
            xrange = args[i + 2]
            if not (isinstance(xrange, tuple) and len(xrange) == 2):
                raise TypeError(
                    f"symbolic plot range must be (xmin, xmax) tuple; got {xrange!r}"
                )

            # The DSL's ``t ▶ ms`` idiom in the sweep-variable slot is
            # NOT a unit conversion — symbols can't be "converted to ms".
            # It's a request to label the x-axis with the unit name.
            # The fast-path in ``sigfig.in_units`` packages the symbol
            # into an ``_InUnits`` with the unit label preserved; we
            # detect that here and route the label to xunit so the
            # ordinary axis-label machinery picks it up.  The arithmetic
            # below uses the underlying symbol verbatim — no scaling.
            x_axis_unit = None
            if type(var).__name__ == "_InUnits":
                x_axis_unit = var.unit_label
                var = var.value          # the bare sympy Symbol

            x_data, y_data, var_name, yunit = _eval_symbolic(expr, var, xrange)
            i += 3
            label, style = _consume_annotation(args, i)
            if label is not None or style is not None:
                i += 1
            series_list.append({
                "x": x_data, "y": y_data, "label": label, "style": style,
                "xunit": x_axis_unit, "yunit": yunit,
                "var_name": var_name,
            })

        elif _is_arraylike(a):
            # Paired-data or y-only?
            if (i + 1 < len(args)
                    and _is_arraylike(args[i + 1])
                    and not isinstance(args[i + 1], str)
                    and not _is_sympy(args[i + 1])):
                x = a
                y = args[i + 1]
                # Coordinated strip: ``_strip_with_unit`` returns the
                # numeric array AND the matching unit label from the
                # SAME reference element, so the axis label can never
                # silently disagree with the plotted magnitudes (which
                # happened when an offset-removed array had a ``0``
                # first element printing as base-unit ``0 m``).
                x_stripped, x_unit = _strip_with_unit(x)
                y_stripped, y_unit = _strip_with_unit(y)
                series_list.append({
                    "x": x_stripped,
                    "y": y_stripped,
                    "label": None, "style": None,
                    "xunit": x_unit,
                    "yunit": y_unit,
                    # Whether the label is a ``▸`` free-text caption (as
                    # opposed to a real physical unit).  A caption is
                    # shown verbatim; a unit is bracketed ``[V]``.  See
                    # the label-deduction block in ``_plot_impl``.
                    "xcaption": _is_caption(x),
                    "ycaption": _is_caption(y),
                    "var_name": None,
                })
                i += 2
            else:
                # y-only
                y = a
                y_stripped, y_unit = _strip_with_unit(y)
                series_list.append({
                    "x": np.arange(len(y_stripped)),
                    "y": y_stripped,
                    "label": None, "style": None,
                    "xunit": None,
                    "yunit": y_unit,
                    "xcaption": False,
                    "ycaption": _is_caption(y),
                    "var_name": None,
                })
                i += 1

            # Pick up an optional label/(label, style)
            label, style = _consume_annotation(args, i)
            if label is not None or style is not None:
                series_list[-1]["label"] = label
                series_list[-1]["style"] = style
                i += 1

        else:
            raise TypeError(
                f"plot() arg {i} ({type(a).__name__}) is neither an "
                "array-like nor a sympy expression"
            )

    return series_list


def _consume_annotation(args, i):
    """If ``args[i]`` is a series annotation (label-string or
    ``(label, style)`` tuple), return ``(label, style)`` — possibly
    with ``None`` for one or both.  Otherwise return ``(None, None)``.

    The caller advances ``i`` only when at least one of the returned
    values is not ``None``.
    """
    if i >= len(args):
        return (None, None)
    a = args[i]
    if isinstance(a, str):
        return (a, None)
    if isinstance(a, tuple) and len(a) == 2 and \
            isinstance(a[0], (str, type(None))) and \
            isinstance(a[1], (str, type(None))):
        return a
    return (None, None)


def _is_arraylike(value) -> bool:
    """``True`` if ``value`` can be plotted as a 1-D series.

    Lists, tuples (other than the (xmin, xmax) range tuple — but the
    caller passes ranges only inside symbolic series), ndarrays, and
    Sigs/Physicals/_InUnits wrapping arrays all qualify.  Strings
    explicitly don't (they're treated as labels).  Plain numbers also
    don't.
    """
    if isinstance(value, str):
        return False
    if isinstance(value, (list, np.ndarray)):
        return True
    # An _InUnits wrapping an array — produced by ``arr ▶ unit``
    if type(value).__name__ == "_InUnits":
        inner = getattr(value, "value", None)
        if isinstance(inner, np.ndarray) and inner.ndim > 0:
            return True
        return False
    # A Sig wrapping a list/array
    if type(value).__name__ == "Sig" and hasattr(value, "value"):
        return _is_arraylike(value.value)
    # A Physical wrapping an array (forallpeople sometimes does this)
    if hasattr(value, "value") and hasattr(value, "dimensions"):
        return _is_arraylike(value.value)
    return False


def _eval_symbolic(expr, var, xrange, n=200):
    """Evaluate sympy ``expr`` over ``xrange`` and return
    ``(x, y, xlabel, yunit)``.

    ``var`` is the sympy Symbol that varies; ``xrange`` is ``(xmin, xmax)``
    as plain numbers.  Uses ``sympy.lambdify`` with the ``"numpy"``
    backend, so the result is a fast numpy ndarray.

    ``xlabel`` is the variable's string name (so ``x`` shows up on
    the axis label when the data series doesn't already supply one).

    ``yunit`` is a guess at the result's unit symbol, derived by
    looking at any unit-bearing constants in the expression: if all
    Physicals that were stripped share the same unit symbol, that
    symbol is returned.  Otherwise ``None``.  This makes
    ``V_0 * exp(-t/τ)`` correctly label the y-axis ``[V]`` —
    the user wrote a voltage expression, the result is a voltage,
    no explicit ``ylabel=`` needed.

    The heuristic is intentionally simple — it doesn't do real
    dimensional analysis.  For ``V_0² * exp(-t/τ)`` it still reports
    ``V`` rather than ``V²``.  For ``V_0 * I_0 * exp(-t/τ)`` (W) it
    reports whichever unit appears first (V) since they don't match
    — actually, *because* they don't match, it returns ``None`` and
    no auto-label is set.  The user can always override with
    ``ylabel="W"`` for the cases where the heuristic guesses wrong.

    Unit handling: if the expression contains ``forallpeople.Physical``
    constants (e.g. from a DSL line like ``λ := 646 nm`` then used in
    ``exp(-(x - λ)²/...)``), they get substituted with their
    auto-prefixed *display magnitude* (646.0, not the SI-base 6.46e-7).
    This makes the sweep variable's range and the expression's
    constants share a common implicit unit — the one forallpeople
    chose for display.  Without this, a sweep over [400, 800]
    against ``λ = 6.46e-7`` would produce all zeros from ``exp(-huge)``.
    """
    import sympy
    # Capture any _stripped_unit hint BEFORE unwrapping the Sig.  The
    # DSL wraps expressions in Sig and the toolkit's Sig._binop records
    # the implicit unit of any Physical it stripped during arithmetic,
    # so an expression like ``V_0 · exp(-t/τ)`` (where V_0 was
    # ``Sig(Physical(V), …)``) ends up as a Sig wrapping a sympy
    # expression with ``_stripped_unit = "V"``.  We use that hint
    # to label the y-axis when no Physical remains in the expression
    # tree for ``_strip_physical_atoms`` to find.
    pre_stripped_unit = None
    if type(expr).__name__ == "Sig" and hasattr(expr, "_stripped_unit"):
        pre_stripped_unit = expr._stripped_unit

    # The DSL wraps even pure-symbolic assignments in Sig (e.g.
    # ``fit_expr := 0.05 * x**2`` becomes ``Sig(0.05*x**2, 1)``), so
    # peel any Sig before handing to lambdify.  Strip unit-bearing
    # bounds for the same reason — symbolic plots are conventionally in
    # the variable's natural scale.
    expr = _unwrap_sympy(expr)
    var = _unwrap_sympy(var)
    xmin = _strip_scalar(xrange[0])
    xmax = _strip_scalar(xrange[1])

    # Replace any Physical/Sig constants in the expression with their
    # display magnitudes.  This MUST happen before lambdify or numpy
    # evaluation, because forallpeople's arithmetic refuses to combine
    # a Physical with a dimensionless sympy Symbol at substitution
    # time — the failure mode is a ValueError mid-evaluation.  Collect
    # the unit symbols so we can label the y-axis.
    expr, seen_units = _strip_physical_atoms(expr)

    # Y-unit guess: prefer the hint from the input Sig (recorded by
    # Sig._binop at strip time), then fall back to any Physicals we
    # find while walking the expression here.  If the walk finds
    # multiple different units, leave it unset.
    yunit = pre_stripped_unit
    if yunit is None and seen_units:
        unique = set(seen_units)
        if len(unique) == 1:
            yunit = next(iter(unique))

    x_data = np.linspace(float(xmin), float(xmax), n)
    f = sympy.lambdify(var, expr, modules="numpy")
    y_raw = f(x_data)
    # ``lambdify`` returns scalars for constant expressions; broadcast
    # to match x_data so matplotlib sees an array of the same length.
    y_data = np.broadcast_to(y_raw, x_data.shape) \
        if not isinstance(y_raw, np.ndarray) or y_raw.shape != x_data.shape \
        else y_raw
    return x_data, y_data, str(var), yunit


def _strip_physical_atoms(expr):
    """Walk a sympy expression and substitute any forallpeople-Physical
    (or Sig-wrapping-Physical) atoms with their *display magnitude*.

    Returns ``(stripped_expr, seen_units)`` — the rewritten expression
    plus a list of unit-symbol strings (e.g. ``["V", "V"]``) for every
    Physical encountered.  Callers use the unit list to label the
    y-axis: if all collected symbols match, the expression's result
    inherits that unit at first order.

    The display magnitude is what ``str(p)`` shows — 646.0 for a
    Physical that displays as ``646 nm``, not the SI-base 6.46e-7 m
    that ``p.value`` returns.  Using the display magnitude makes the
    expression's constants and the sweep variable's range share a
    common implicit unit at evaluation time.

    Sympy's ``.atoms()`` walk doesn't reach Physical objects because
    they aren't subclasses of ``sympy.Basic``.  Instead we rely on
    sympy's structural recursion via ``.args`` and ``.func``: rebuild
    each subexpression bottom-up, swapping out any Physical leaf for
    its float display value.

    Sigs wrapping Physicals are handled the same way — we unwrap to
    the underlying Physical and substitute its display magnitude.
    """
    import sympy

    def _is_physical(v):
        return (hasattr(v, "value")
                and hasattr(v, "dimensions")
                and not isinstance(v, sympy.Basic))

    def _display_magnitude(v):
        # ``float(Physical)`` returns the auto-prefix-scaled display
        # value (646 for ``646 nm``), which is what we want here.
        return float(v)

    seen_units = []

    def _record(physical):
        # Extract the unit-symbol portion of the Physical's str form
        # (``"12.000 V"`` → ``"V"``, ``"3.600 ks"`` → ``"ks"``).  The
        # display string is whitespace-separated value-then-unit, so
        # the last token is the symbol.  If anything goes wrong (a
        # dimensionless Physical, or a representation we don't expect),
        # we silently skip — the y-axis just won't get auto-labelled.
        try:
            s = str(physical).strip()
            parts = s.split()
            if len(parts) >= 2:
                seen_units.append(parts[-1])
        except Exception:
            pass

    def _walk(e):
        # Unwrap Sig-of-Physical
        if type(e).__name__ == "Sig" and hasattr(e, "value"):
            inner = e.value
            if _is_physical(inner):
                _record(inner)
                return sympy.Float(_display_magnitude(inner))
            return _walk(inner)
        # Direct Physical
        if _is_physical(e):
            _record(e)
            return sympy.Float(_display_magnitude(e))
        # Sympy expression — recurse into args
        if isinstance(e, sympy.Basic):
            if not e.args:
                return e
            new_args = tuple(_walk(a) for a in e.args)
            # Only rebuild if anything changed (cheap identity check)
            if new_args == e.args:
                return e
            try:
                return e.func(*new_args)
            except Exception:
                # If the constructor refuses (e.g. domain mismatch),
                # fall back to the original.  Failing soft here means
                # the user sees lambdify's own error later, which is
                # more informative than our fallback would be.
                return e
        return e

    return _walk(expr), seen_units
