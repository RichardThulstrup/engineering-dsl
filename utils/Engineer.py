"""
One-import notebook setup for the engineering toolkit.

Replace the seven-line preamble

    import forallpeople as si
    si.environment('default', top_level=True)
    import utils.circuit_dsl as dsl
    dsl.add_hook()
    from utils.circuit_dsl import *
    from utils.calc_symbols import *
    import math

with a single line:

    from utils.Engineer import *

Side effects performed during this import:

1. ``forallpeople`` is loaded and its 'default' environment is activated
   with ``top_level=True``.  This injects unit names (``V``, ``Ω``, ``m``,
   ``kg``, ``Hz``, ``mV``, ``kΩ``, ...) into Python's ``builtins`` module
   so they're visible everywhere — no per-cell re-import.
2. The ``ideas`` source-transform hook is installed via
   ``circuit_dsl.add_hook()`` so the ergonomic syntax (``:=``, ``‖``, ``°``,
   ``∠``, ``²``, ``√``, ``log₁₀(...)``, ``Γ(x)``, ``≈``,
   ``[a..b]``, ...) is rewritten in every subsequent cell or imported
   module.
3. ``circuit_dsl`` and ``calc_symbols`` are loaded; their public names
   (helpers, runtime functions, physical constants, prefixed units) are
   re-exported through this module's ``__all__``.
4. ``calc_symbols`` calls ``protect_all()`` at the end of its own load,
   so identifier protection is active by the time you reach your cells.
   Names like ``V``, ``c``, ``ε_0``, ``nF`` cannot be reassigned.

Optional: launch the symbol palette
-----------------------------------
The toolkit ships an optional native helper — a floating glyph picker
that types Unicode symbols (``≈``, ``∠``, ``√``, ``Ω``, ``μ``, etc.)
straight into whichever app has focus.  It lives at ``utils/bin/`` in
the canonical layout:

    utils/
    └── bin/
        ├── SymbolPaletteWinUI.exe      (Windows)
        ├── SymbolPalette.app/          (macOS)
        └── SymbolPalette                (Linux)

If a binary for the host platform is found there, it's launched
automatically when ``Engineer`` is imported — no setup required.

To use a binary in a non-canonical location, set the
``SYMBOL_PALETTE_EXE`` environment variable before importing::

    import os
    os.environ['SYMBOL_PALETTE_EXE'] = r'C:\\Tools\\SymbolPaletteWinUI.exe'
    from utils.Engineer import *

Or call ``launch_palette('/path/to/binary')`` to bypass both lookups.
If no binary can be found, the import is silent — no error, no log
spam, just no palette.

Re-importing
------------
``from utils.Engineer import *`` is idempotent within a session — running
it again won't double-install the hook or re-launch the palette.  In the
unusual case you want to opt out of identifier protection after import:

    import utils.circuit_dsl as eng     # or any name you like
    eng.clear_protections()             # release everything
    eng.unprotect('V', 'Ω')             # release a few names

The module also re-exports the ``circuit_dsl`` module under the alias
``eng`` for the same purpose, so ``eng.unprotect('V')`` works directly.
"""

import math as _math
import os as _os
import subprocess as _subprocess
import sys as _sys

import numpy as np  # exposed in __all__ — used at runtime by the
                    # ``[1, 2, 3] mV`` list-as-array rewrite, and useful
                    # to have available without a separate import line.


# ---------------------------------------------------------------------------
# Step 1: forallpeople with top-level (builtins) injection.
# ---------------------------------------------------------------------------
import forallpeople as si  # noqa: E402  -- intentional non-top import order

# environment() pushes unit symbols into Python's `builtins` module when
# called with top_level=True, so this needs to happen exactly once per
# process.  Guard the call against the unlikely re-import case.
if not getattr(si, "_engineer_environment_loaded", False):
    si.environment("default", top_level=True)
    si._engineer_environment_loaded = True


# ---------------------------------------------------------------------------
# Step 1.5: sympy ↔ forallpeople interop adapter.
# ---------------------------------------------------------------------------
# Without this patch, ``sym.pi * (50 Hz)`` evaluates to ``50.0*pi`` —
# sympy's ``__mul__`` tries to sympify the Physical, gets a plain float
# via ``float(50 Hz) == 50.0``, and the units are silently dropped.
#
# Adding ``_sympy_`` to Physical that raises ``SympifyError`` makes
# sympy refuse the conversion — its ``__mul__`` returns ``NotImplemented``,
# Python falls back to ``Physical.__rmul__``, which converts the sympy
# expression to a float (``float(2*pi) == 6.283``) and produces a
# unit-bearing result like ``314.159 Hz``.
#
# The trade-off (intentional, per "option A''" in the design discussion):
# mixed sympy×Physical expressions lose their symbolic structure — you
# get a numeric Physical, not a sympy expression carrying units.  Pure
# symbolic expressions (no Physical involved) still keep their full
# symbolic identity, so ``e**(i*π)`` still simplifies to ``-1``.
if not getattr(si.Physical, "_engineer_sympy_patch", False):
    import sympy as _sym_for_patch
    from sympy.core.sympify import SympifyError as _SympifyError

    def _physical_sympify_refuses(self):
        # Tell sympy we have no symbolic representation.  Sympy's
        # sympify will then return NotImplemented for the calling op,
        # letting Physical's own arithmetic handle the multiplication.
        raise _SympifyError(self)

    si.Physical._sympy_ = _physical_sympify_refuses
    si.Physical._engineer_sympy_patch = True


# ---------------------------------------------------------------------------
# Step 1.6: Sig-aware Physical arithmetic.
# ---------------------------------------------------------------------------
# When a ``Sig``-wrapped Physical meets a bare ``Physical`` in an arithmetic
# operation, we want Sig's ``__rop__`` to handle it — Sig knows how to unwrap
# its inner value and recombine units correctly, then re-wrap the result with
# proper sf-tracking.
#
# Without this patch, forallpeople's ``Physical.__truediv__`` (and friends)
# happily accept any object that implements ``__float__``.  ``Sig.__float__``
# returns the magnitude of whatever it wraps — for ``Sig(Physical(317, s))``
# that's just ``317.0``, with the seconds dimension silently discarded.
# Physical then computes ``self.value / 317.0`` and returns a result wearing
# only ``self``'s units.  Concretely: ``(628.319 m) / Sig(317 s)`` gave
# ``1.982 m`` instead of ``1.982 m·s⁻¹``.  Same shape of bug for ``__mul__``,
# ``__add__``, ``__sub__``, etc.
#
# The asymmetric case (one operand bare Physical, the other Sig-wrapped)
# arises naturally when an expression mixes sympy with units: ``2π·100 m``
# loses its Sig wrapper because Sig steps aside for sympy expressions, then
# the sympy×Physical interop produces a bare Physical.  A subsequent
# operation against another Sig-wrapped Physical hits the asymmetric path.
#
# Fix: monkey-patch Physical's arithmetic dunder methods so they return
# ``NotImplemented`` whenever the other operand is a ``Sig``.  Python's
# operator-dispatch fallback then routes to ``Sig.__rop__``, which has the
# correct unwrap-compute-rewrap logic.  The patch leaves all Physical×non-Sig
# behaviour untouched.
if not getattr(si.Physical, "_engineer_sig_aware", False):
    from .sigfig import Sig as _Sig
    # ``Range`` is the toolkit's interval type — produced by the ``±``
    # operator (``plusminus(x, tol)``).  When a Physical sees a Range
    # as the other operand, it must step aside so Range's own arithmetic
    # runs — Range.__rmul__ handles Physical correctly (it wraps each
    # endpoint), while forallpeople's __mul__ raises ValueError because
    # it can't ``float()`` a Range.
    from .circuit_dsl import Range as _Range
    # Currency is in a separate module to avoid a hard dependency between
    # ``sigfig`` and the network-using currency machinery.  Try to import it
    # for the step-aside; if it's not available (rare — would mean a
    # custom truncated install), skip currency-awareness and only step
    # aside for Sig + Range.
    # ``_DisplayUnit`` (``Nm``, ``inch``, ``lbf`` … from ``extra_units``)
    # also needs the step-aside: forallpeople would otherwise coerce the
    # marker through ``__float__`` — silently multiplying by a bare
    # number and getting the DIMENSIONS wrong (``(5 N) * inch`` would
    # stay a force).  Stepping aside routes to the marker's reflected
    # operator, which produces a genuine Physical with correct
    # dimensions (and a composed display tag where applicable).
    from .extra_units import _DisplayUnit as _DisplayU
    try:
        from .currencies import Currency as _Currency
        _STEP_ASIDE_TYPES = (_Sig, _Range, _Currency, _DisplayU)
    except ImportError:
        _STEP_ASIDE_TYPES = (_Sig, _Range, _DisplayU)

    _PHYSICAL_ARITH_METHODS = (
        "__add__", "__sub__", "__mul__", "__truediv__",
        "__floordiv__", "__mod__", "__pow__",
        "__radd__", "__rsub__", "__rmul__", "__rtruediv__",
        "__rfloordiv__", "__rmod__", "__rpow__",
    )

    def _make_sig_aware(orig_method):
        # Closure preserves a reference to the original unbound method
        # so we can delegate when ``other`` isn't a Sig or Currency.
        # When it is one of those types, we return ``NotImplemented``
        # and Python's operator-dispatch fallback routes to the other
        # operand's ``__rop__`` — Sig knows how to handle Physical
        # (unwrap/compute/rewrap), Currency raises a clear TypeError
        # rather than silently coercing through ``__float__``.
        def wrapper(self, other):
            if isinstance(other, _STEP_ASIDE_TYPES):
                return NotImplemented
            return orig_method(self, other)
        wrapper.__name__ = orig_method.__name__
        wrapper.__qualname__ = orig_method.__qualname__
        wrapper.__doc__ = orig_method.__doc__
        return wrapper

    for _opname in _PHYSICAL_ARITH_METHODS:
        _orig = getattr(si.Physical, _opname, None)
        if _orig is not None:
            setattr(si.Physical, _opname, _make_sig_aware(_orig))

    si.Physical._engineer_sig_aware = True


# ---------------------------------------------------------------------------
# Step 2: install the source-transform hook.  Idempotent — `ideas` itself
# is happy to be told to re-register, but we guard anyway to avoid
# duplicating the hook in sys.meta_path.
#
# Modules that aren't authored in DSL style (i.e. don't use ``:=`` for
# assignment) need to be imported BEFORE the hook is installed.  Once
# they're cached in ``sys.modules`` the hook won't re-run them on later
# imports.  That's how ``circuit_dsl`` itself escapes self-rewriting,
# and we use the same trick for ``chrono`` because it's plain Python.
# ---------------------------------------------------------------------------
from . import circuit_dsl as eng  # noqa: E402
from . import chrono as _chrono   # noqa: E402, F401  (pre-hook seed)
# ``extra_units`` is plain Python with bare ``=`` assignments — it must
# be pre-loaded here so the DSL hook doesn't see its source and rewrite
# every top-level ``=`` into ``==`` (math-style equality), which would
# break every line of the module.  Same trick used for ``chrono`` above.
from . import extra_units as _extra_units   # noqa: E402, F401
# ``plotting`` is also plain Python (bare ``=`` assignments) and needs
# the same pre-hook treatment.  It also has a soft dependency on
# matplotlib — but the import itself is fine even without matplotlib
# installed, since the matplotlib import only happens inside ``plot()``.
from . import plotting as _plotting          # noqa: E402, F401
# Three more plain-Python modules that a notebook may import later:
# ``Engineer_Style`` (Pygments lexer/style for nbconvert),
# ``i_mul_fys`` (the standalone implicit-multiplication transformer),
# and ``netlist_parser``.  Imported post-hook their bare ``=``
# assignments get rewritten to ``==`` and the modules break with
# NameErrors — so seed them here like ``chrono`` above.  Each is an
# optional extra with its own soft dependencies (pygments; ideas /
# token_utils), so a failing seed must not block the core toolkit.
for _plain_mod in ("Engineer_Style", "i_mul_fys", "netlist_parser"):
    try:
        __import__(f"{__package__}.{_plain_mod}")
    except Exception:                        # pragma: no cover - optional
        pass
del _plain_mod

if not getattr(eng, "_engineer_hook_installed", False):
    eng.add_hook()
    eng._engineer_hook_installed = True


# ---------------------------------------------------------------------------
# Step 3: pull the names from circuit_dsl and calc_symbols into THIS
# module's globals, and re-export them through __all__ so that
# `from utils.Engineer import *` makes them visible in the caller.
# ---------------------------------------------------------------------------
from .circuit_dsl import *      # noqa: F401, F403
from .calc_symbols import *     # noqa: F401, F403  (also enables protection)
from .chrono import *           # noqa: F401, F403  (ISO 8601 date/time)
from .symbolic import *         # noqa: F401, F403  (sympy bridge)
from .iso286 import *           # noqa: F401, F403  (ISO 286 limits & fits)
from .radix_formats import *    # noqa: F401, F403  (extra integer formats: roman)

# Currency markers (``DKK``, ``USD``, ``EUR`` …) and helpers.  The
# currency module is a soft dependency — it is imported defensively
# elsewhere in this file for the ``Currency`` step-aside type — but the
# pre-built markers also need to reach a wildcard import so that
# ``salary := 50000 DKK`` resolves ``DKK``.  Guarded in a try/except so
# a build without the currency module still loads the rest of the
# toolkit.
try:
    from .currencies import *   # noqa: F401, F403  (DKK, USD, EUR … markers)
except Exception:               # pragma: no cover - optional component
    pass

# ``extra_units`` MUST be imported LAST among the star-imports above.  Two
# reasons:
#
#   1. ``forallpeople.environment(top_level=True)`` (called earlier in
#      this file) injects ``N``, ``V``, ``A``, ``Pa``, ``J``, ``W``, ``Hz``,
#      ``C``, ``F``, ``H``, ``Ω``, ``T`` into Python's builtins — they're
#      the SI derived units the toolkit relies on.  Some of these names
#      collide with sympy:
#         - ``sympy.N`` is a numeric-evaluator function, NOT a newton
#         - ``sympy.C`` is a polynomial-coefficient class
#         - ``sympy.S`` is the singleton manager
#         - ``sympy.O`` is the big-O symbol
#      If a user does ``from sympy import *`` (which several toolkit
#      modules do for the symbolic functions) AFTER ``forallpeople``
#      set up, the sympy names land in the importing module's globals
#      and shadow the builtins.  Then ``1.0 * N`` tries ``float * function``
#      and fails with TypeError.
#
#   2. ``extra_units`` also exports ``N`` (as a Physical newton, equal
#      to the forallpeople builtin but explicit) and the full prefix
#      family ``μN/mN/kN/MN/GN`` plus ``C`` (coulomb), capacitance
#      prefixes, and ``g_0`` / ``g_n``.  Importing it LAST means its
#      ``N`` and ``C`` definitions land in this module's globals
#      AFTER the symbolic-module's sympy-derived names, taking
#      precedence at lookup time.
#
# If a downstream user does ``from sympy import *`` in their own
# notebook AFTER importing Engineer, that will re-shadow ``N`` etc.
# The fix is to do ``from utils.extra_units import N, kN, MN, ...``
# (or ``from utils.extra_units import *``) AFTER the sympy import in
# their notebook.  Or just use ``newton`` as an alias when worried
# about shadowing — see the ``newton`` export below.
from .extra_units import *      # noqa: F401, F403  (imperial, prefixed,
                                # currency-adjacent SI units)
# ``plotting`` exports just ``plot()``.  Its name doesn't collide with
# anything sympy- or forallpeople-shadowed, so import order doesn't
# matter for it the way it does for ``extra_units``.
from .plotting import *         # noqa: F401, F403  (unit-aware plot helper)

# math is small enough to be worth re-exporting wholesale; users routinely
# reach for math.cos, math.atan2, etc. in engineering notebooks.
math = _math


# Build __all__ by unioning the source modules' __all__ lists.  This
# keeps the re-export list automatically in sync with whatever those
# modules expose.  We pull in every module that contributes names via
# the star-imports above so that ``from utils.Engineer import *``
# transparently surfaces names from all of them — including
# ``extra_units`` (force/charge/etc. prefixes, currency markers),
# ``plotting`` (the ``plot()`` helper), ``iso286`` (limits & fits) and
# ``radix_formats`` (integer-display formats such as ``to_roman``).
# Without listing a module here, its names would be reachable as
# ``Engineer.<name>`` but invisible to a wildcard import.
from . import circuit_dsl as _cd, calc_symbols as _cs, chrono as _ch, symbolic as _sm  # noqa: E402
from . import extra_units as _eu, plotting as _pl  # noqa: E402
from . import iso286 as _iso, radix_formats as _rf  # noqa: E402
try:
    from . import currencies as _cur  # noqa: E402
except Exception:  # pragma: no cover - optional component
    _cur = None

__all__ = sorted(set(getattr(_cd, "__all__", []))
                 | set(getattr(_cs, "__all__", []))
                 | set(getattr(_ch, "__all__", []))
                 | set(getattr(_sm, "__all__", []))
                 | set(getattr(_eu, "__all__", []))
                 | set(getattr(_pl, "__all__", []))
                 | set(getattr(_iso, "__all__", []))
                 | set(getattr(_rf, "__all__", []))
                 | set(getattr(_cur, "__all__", []) if _cur else [])
                 | {"si", "eng", "math", "np", "launch_palette"})


# ---------------------------------------------------------------------------
# Step 4: optional symbol-palette launcher.
# ---------------------------------------------------------------------------

_PALETTE_ENV_VAR = "SYMBOL_PALETTE_EXE"
_palette_proc = None  # holds the Popen handle so the GC doesn't reap it


def _autodetect_palette_path() -> "str | None":
    """Look for a bundled symbol-palette binary in the canonical layout.

    The toolkit ships an optional native helper at ``utils/bin/`` —
    Windows ``.exe``, macOS ``.app`` (resolved to the inner binary),
    and Linux executables.  This function picks the first one that
    exists for the host platform, anchored relative to *this module's*
    file path (not the process working directory), so the lookup
    works regardless of where Jupyter was started.

    Returns the absolute path string, or ``None`` if no candidate
    binary is present.  The function never raises — a missing palette
    shouldn't break the import.
    """
    here = _os.path.dirname(_os.path.abspath(__file__))
    bin_dir = _os.path.join(here, "bin")

    if not _os.path.isdir(bin_dir):
        return None

    # Per-platform candidates, in priority order.  Listed by basename;
    # we join with bin_dir before testing existence.
    if _sys.platform.startswith("win"):
        candidates = ["SymbolPaletteWinUI.exe", "SymbolPalette.exe"]
    elif _sys.platform == "darwin":
        # macOS .app bundles aren't directly executable — point at the
        # binary inside.  If you ship a single-file binary, list it
        # alongside.
        candidates = [
            "SymbolPalette.app/Contents/MacOS/SymbolPalette",
            "SymbolPalette",
        ]
    else:
        candidates = ["SymbolPalette", "symbol-palette"]

    for name in candidates:
        path = _os.path.join(bin_dir, name)
        if _os.path.isfile(path):
            return path
    return None


def launch_palette(exe_path: str = None) -> "subprocess.Popen | None":
    """Start the external symbol-palette app.

    The executable path is resolved in this priority order:

    1. The ``exe_path`` argument passed to this function.
    2. The ``SYMBOL_PALETTE_EXE`` environment variable.
    3. Auto-detected from ``utils/bin/`` next to this module
       (the canonical layout — Windows, macOS, Linux variants).

    Returns the ``subprocess.Popen`` handle, or ``None`` if no path
    could be resolved.  A second call while the previous instance is
    still running is a no-op (returns the existing handle).  Errors
    during launch are reported on stderr but never raised — a missing
    palette shouldn't break your notebook setup.
    """
    global _palette_proc

    if _palette_proc is not None and _palette_proc.poll() is None:
        # Still running — don't spawn a duplicate.
        return _palette_proc

    path = (
        exe_path
        or _os.environ.get(_PALETTE_ENV_VAR)
        or _autodetect_palette_path()
    )
    if not path:
        return None

    if not _os.path.exists(path):
        print(f"[Engineer] palette not found at {path!r}; skipping launch.",
              file=_sys.stderr)
        return None

    try:
        _palette_proc = _subprocess.Popen([path])
        return _palette_proc
    except OSError as exc:
        print(f"[Engineer] could not launch palette: {exc}",
              file=_sys.stderr)
        return None


# Auto-launch on import when any of the lookup paths resolves.  The
# resolution itself is silent — no errors and no log spam if no palette
# binary is available, which is the right default for the headless or
# CI case.  Set ``SYMBOL_PALETTE_EXE`` to override the auto-detected
# location, or call ``launch_palette(path)`` explicitly to bypass both.
if (_os.environ.get(_PALETTE_ENV_VAR) or _autodetect_palette_path()):
    launch_palette()


# ---------------------------------------------------------------------------
# Empty-set display as ∅
# ---------------------------------------------------------------------------
#
# Python's empty set reprs as ``set()`` — readable, but the mathematical
# notation is ``∅``.  The DSL already accepts ``∅`` as INPUT (normalised to
# ``set()`` by circuit_dsl); this block adds the OUTPUT side so an empty
# set/frozenset DISPLAYS as ``∅`` too, closing the round-trip.
#
# This is display-only, by deliberate design.  An empty set is a plain
# built-in ``set`` — we cannot (and must not) change ``set``'s real
# ``__repr__``, and subclassing ``set`` would be fragile (set operations
# return plain ``set``, so any subclass identity is lost after the first
# ``|`` / ``&`` / ``-``).  Instead we hook the *display layer*:
#
#   * Under IPython / Jupyter — register a formatter for ``set`` and
#     ``frozenset`` on the active display formatter.  This makes even a
#     BARE value on a cell's last line show ``∅`` for the empty case,
#     with zero change to the ``set`` type and zero effect on set
#     arithmetic.  Non-empty sets fall through to normal ``repr``.
#
#   * Outside IPython (plain script / REPL) — there is no display layer
#     to hook, so ``set()`` still reprs as ``set()``.  For that case the
#     ``fmt()`` helper below gives an explicit opt-in: ``fmt(value)``
#     returns a string with empty sets rendered as ``∅``.
#
# The whole block is guarded — if IPython is absent or has no display
# formatter (a bare kernel, a CI run), it silently does nothing.

def _empty_set_str(value) -> str:
    """Return the ∅-aware string form of ``value``.

    Empty ``set`` / ``frozenset`` → ``"∅"``.  Anything else → its normal
    ``repr``.  This is the single source of truth for the ∅ rendering,
    used by both the IPython formatter and the ``fmt`` fallback so the
    two never drift apart.
    """
    if isinstance(value, (set, frozenset)) and len(value) == 0:
        return "∅"
    return repr(value)


def fmt(value) -> str:
    """Render ``value`` as a string, showing an empty set as ``∅``.

    This is the plain-Python fallback for the empty-set notation: in a
    Jupyter notebook the display formatter (registered below) already
    makes a bare empty set show as ``∅``, but in a plain script or REPL
    there is no display hook, so call ``fmt(x)`` explicitly when you
    want the symbol — e.g. ``print(fmt(my_set))``.

    Non-empty values are returned via ordinary ``repr``, so ``fmt`` is
    always safe to wrap around anything.
    """
    return _empty_set_str(value)


def _register_empty_set_formatter() -> bool:
    """Register the ∅ display formatter with IPython, if available.

    Returns ``True`` when the formatter was registered, ``False`` when
    IPython / a display formatter could not be found (the plain-script
    case).  Safe to call more than once — IPython's ``for_type`` simply
    replaces any prior registration.
    """
    try:
        from IPython import get_ipython
    except Exception:
        return False

    ip = get_ipython()
    if ip is None or not hasattr(ip, "display_formatter"):
        # Not inside an interactive IPython/Jupyter session.
        return False

    # The plain-text formatter is the one that renders a cell's last
    # value.  Registering ``for_type`` on the BUILTIN ``set`` type
    # overrides its display without touching the type itself.
    text_formatter = ip.display_formatter.formatters.get("text/plain")
    if text_formatter is None:
        return False

    # The callback signature IPython expects is ``(obj, printer, cycle)``;
    # ``printer`` is a pretty-printer we ``.text(...)`` into.  For the
    # empty case we emit ``∅``; for a non-empty set we reproduce the
    # standard ``{a, b, c}`` rendering so behaviour is unchanged there.
    def _format_set(obj, printer, _cycle):
        if len(obj) == 0:
            printer.text("∅")
        else:
            # Normal set/frozenset repr — keep IPython's default look.
            printer.text(repr(obj))

    text_formatter.for_type(set, _format_set)
    text_formatter.for_type(frozenset, _format_set)
    return True


def _register_matrix_list_formatter() -> bool:
    """Register a LaTeX display formatter for matrix-shaped lists.

    A list-of-lists that's a rectangular grid (built e.g. from row
    variables, ``M := [row1, row2, …]``) is a plain Python ``list`` — it
    has no ``_repr_latex_``, so a bare ``M`` cell would show the multi-
    line list text instead of the typeset matrix.  This registers a
    ``text/latex`` formatter on ``list`` so such a cell renders the same
    bracketed ``\\begin{bmatrix}`` matrix ``pp(M)`` produces, which
    renders well in the live JupyterLab notebook.

    Only matrix-shaped lists are handled: the formatter returns ``None``
    for anything ``_is_matrix`` rejects (1-D lists, ragged lists, lists
    of strings, …), and IPython then falls back to the normal list
    display — so ordinary lists are completely unaffected.  Real sympy
    matrices already carry their own ``_repr_latex_`` and don't need
    this.  No-op outside an interactive IPython/Jupyter session.
    """
    try:
        from IPython import get_ipython
    except Exception:
        return False

    ip = get_ipython()
    if ip is None or not hasattr(ip, "display_formatter"):
        return False

    latex_formatter = ip.display_formatter.formatters.get("text/latex")
    if latex_formatter is None:
        return False

    try:
        from .symbolic import (_is_matrix, _matrix_to_latex,
                               _is_renderable_vector, _vector_to_latex)
    except Exception:
        return False

    def _format_matrix_list(obj):
        # A 2-D matrix-shaped list → bmatrix; a 1-D list whose elements
        # are all renderable (units, Sig, radix, numbers) → row vector,
        # so ``[34 mV, 35 mV, …]`` typesets like ``[34, 35, …] mV``.
        # ``None`` for anything else → normal list display, so ordinary
        # (mixed / string / plain) lists are unaffected.
        try:
            if _is_matrix(obj):
                return "$" + _matrix_to_latex(obj) + "$"
            if _is_renderable_vector(obj):
                return "$" + _vector_to_latex(obj) + "$"
        except Exception:
            return None
        return None

    latex_formatter.for_type(list, _format_matrix_list)
    return True


def _register_scalar_latex_formatter() -> bool:
    """Register a LaTeX display formatter for plain ``int`` / ``float``.

    Most DSL numbers are ``Sig``-wrapped and render via ``Sig``'s own
    ``_repr_latex_``; but a few paths yield a bare builtin number — e.g.
    indexing a numpy-backed array (``x₅`` → a plain ``int``).  Builtins
    can't carry a ``_repr_latex_`` method, so this registers a
    ``text/latex`` formatter on ``int`` and ``float`` that wraps the
    number in ``$…$`` — giving the same typeset look as every other
    scalar, per the "use the LaTeX renderer by default" guideline.

    ``bool`` is intentionally NOT registered (it's an ``int`` subclass,
    but ``True`` / ``False`` should stay as words, not ``$True$``).  The
    formatter returns ``None`` for a ``bool`` so it falls through.
    No-op outside an interactive IPython/Jupyter session.
    """
    try:
        from IPython import get_ipython
    except Exception:
        return False

    ip = get_ipython()
    if ip is None or not hasattr(ip, "display_formatter"):
        return False

    latex_formatter = ip.display_formatter.formatters.get("text/latex")
    if latex_formatter is None:
        return False

    def _format_scalar(obj):
        if isinstance(obj, bool):
            return None          # keep True/False as words
        try:
            return f"${obj}$"
        except Exception:
            return None

    latex_formatter.for_type(int, _format_scalar)
    latex_formatter.for_type(float, _format_scalar)
    return True


def _register_sympy_leftalign_formatter() -> bool:
    """Register a ``text/latex`` formatter for sympy objects that strips
    the leading ``\\displaystyle``.

    sympy's own ``_repr_latex_`` prepends ``\\displaystyle``, which makes
    MathJax render the result CENTRED — whereas all the toolkit's other
    LaTeX outputs (scalars, units, radix, matrices) are left-aligned.
    A *bare* sympy expression cell (``x**2 + 1``) uses sympy's repr
    directly, so the only place to intervene is a display formatter.
    This registers one on ``sympy.Basic`` that re-emits the LaTeX with
    ``\\displaystyle`` removed, so sympy results left-align like
    everything else.  No-op outside Jupyter.
    """
    try:
        from IPython import get_ipython
    except Exception:
        return False
    ip = get_ipython()
    if ip is None or not hasattr(ip, "display_formatter"):
        return False
    latex_formatter = ip.display_formatter.formatters.get("text/latex")
    if latex_formatter is None:
        return False
    try:
        import sympy as _sym
        from .circuit_dsl import _strip_displaystyle
    except Exception:
        return False

    def _format_sympy(obj):
        try:
            lx = obj._repr_latex_()
        except Exception:
            return None
        return _strip_displaystyle(lx) if isinstance(lx, str) else None

    latex_formatter.for_type(_sym.Basic, _format_sympy)
    return True


# Attempt registration on import.  No-op (and no error) outside Jupyter.
_register_empty_set_formatter()
_register_matrix_list_formatter()
_register_scalar_latex_formatter()
_register_sympy_leftalign_formatter()


def refresh_display() -> bool:
    """(Re)register the IPython display formatters and report success.

    The ∅-set and matrix-shaped-list LaTeX formatters are registered
    automatically when this module is imported.  If a notebook kernel
    was started *before* this version of the toolkit was installed — so
    the import-time registration ran against an older module, or before
    the ``text/latex`` formatter existed — a bare matrix-shaped ``M``
    cell may still show plain list text.  Calling ``refresh_display()``
    once re-runs the registration against the live IPython session,
    after which bare ``M`` renders as the typeset matrix (same as
    ``pp(M)``).  Returns ``True`` if a formatter was registered.

    Safe to call any number of times — IPython's ``for_type`` simply
    replaces any prior registration.  Outside Jupyter it's a harmless
    no-op returning ``False``.
    """
    set_ok = _register_empty_set_formatter()
    list_ok = _register_matrix_list_formatter()
    scalar_ok = _register_scalar_latex_formatter()
    sympy_ok = _register_sympy_leftalign_formatter()
    return bool(set_ok or list_ok or scalar_ok or sympy_ok)


if "refresh_display" not in __all__:
    __all__.append("refresh_display")

# ``fmt`` is defined after the ``__all__`` union above, so append it
# explicitly — this keeps it reachable via ``from utils.Engineer import *``
# (and therefore unprefixed in DSL notebook cells).
if "fmt" not in __all__:
    __all__.append("fmt")
