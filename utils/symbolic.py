"""
Symbolic math, via sympy.

This module is a thin re-export of the sympy names that engineering work
most commonly reaches for, plus a helper namespace ``sym`` that exposes
all of sympy without polluting the global namespace.

Quick declaration shorthand
---------------------------
The DSL extends Python with a special right-hand-side token, ``symbols``
(and a couple of constraint variants), so you don't have to spell out
the symbol names twice::

    x, y, z := symbols                  # x, y, z = sympy.symbols('x y z')
    n, k := positive_symbols            # ...positive=True
    α, β := real_symbols                # ...real=True
    j := integer_symbols                # ...integer=True

Without this shorthand you'd write::

    import sympy
    x, y, z = sympy.symbols('x y z')

The shorthand is recognised only when the right-hand side is the bare
token ``symbols`` (or one of the variants) — no parens, no string list,
nothing else.  That makes it unambiguous: any other use of ``symbols``
in the right-hand position is left alone, in case sympy's own
``symbols(...)`` function call is ever wanted there.

Identifiers on the left are converted to sympy symbol names with a
small Unicode-to-ASCII step: subscript digits become underscored
suffixes (``x₁`` → ``x_1``), Greek letters are kept as-is, and other
characters pass through.  This means ``x₁ := symbols`` declares a
symbol that sympy renders cleanly as ``x_1`` in LaTeX/Unicode output.

Subscripts and the math DSL
---------------------------
The math/EE DSL rewrites ``xₙ`` to ``x[n]`` (indexing) by default.  A
symbol declaration is the exception: ``x₁ := symbols`` (or
``symbols: x₁``) registers ``x₁`` for the session, and every later
``x₁`` in user code is spelled ``x_1`` — the declared symbol — before
the subscript-as-index pass runs (``circuit_dsl._DECLARED_SUBSCRIPT_SYMBOLS``).
Names that were never declared keep the indexing meaning.

Mixing with the numeric DSL
---------------------------
Numeric literals in the user's source are wrapped by the math-DSL pass
into ``Sig`` objects, which sympy treats as floats.  This means an
expression like ``x**2`` ends up with sympy showing ``x**2.0`` rather
than ``x**2``:

    expr := (x + y)**2          # displays as (x + y)**2.0

The math is unchanged (sympy's ``expand``, ``factor``, ``simplify``
all give the right answer), only the display has a trailing ``.0``.
For cleaner display, use ``Rational(n)`` or ``Integer(n)`` from sympy
explicitly:

    expr := (x + y)**Rational(2)   # displays as (x + y)**2

Or use ``sym.nsimplify(expr)`` to convert the floats back to integers
at display time.

What's re-exported
------------------
The names ``Symbol``, ``Eq``, ``solve``, ``expand``, ``factor``,
``simplify``, ``diff``, ``integrate``, ``limit``, ``series``,
``Rational``, ``oo`` (sympy infinity), and ``sym`` (the full sympy
module) are exposed at the top level so ``from utils.Engineer import *``
makes them available.  For other sympy functions, reach into ``sym``
directly: ``sym.collect``, ``sym.dsolve``, ``sym.Matrix``, etc.

Sympy's single-letter constants (``S``, ``I``, ``E``, ``N``) are NOT
re-exported because they'd collide with common variable names in
engineering work.  Use ``sym.I`` for the imaginary unit, ``sym.E`` for
Euler's number, etc.  (Note: ``forallpeople`` already provides ``A`` for
amperes, so ``sym.A`` is also the safer reference.)
"""

import re
import sympy as sym
from sympy import (
    Symbol, Eq, Rational, oo,
    solve, expand, factor, simplify,
    diff, integrate, limit, series,
    lambdify, nsimplify,
    Matrix, det, eye, zeros, ones, diag,
    Reals, Integers, Naturals,
    # Trig, exponential, log — re-exported so users don't have to
    # remember the ``sym.`` prefix in plotting expressions or
    # ordinary numeric work.  Note the gotcha documented below: when
    # combined with a forallpeople Physical from the right
    # (``sin(0.5) * V``) the unit is lost.  For unit-bearing trig,
    # apply the function to the numeric magnitude first and reattach
    # the unit, or evaluate the symbolic form explicitly.
    log,
    pi, E,
)
# The trig / hyperbolic / ``exp`` names are the toolkit's own wrappers
# (``circuit_dsl``): numeric with sf-tracking on numbers, symbolic on
# symbols, and an error on a dimensioned argument.  Re-exported here so
# the historic ``from utils.symbolic import sin`` keeps working.
from .circuit_dsl import (                       # noqa: E402
    sin, cos, tan, asin, acos, atan, atan2,
    sinh, cosh, tanh, asinh, acosh, atanh, exp,
)
# ``sqrt`` gets a small wrapper below — sympy's own ``sqrt`` refuses
# to take a square root of a ``forallpeople.Physical`` (it tries to
# sympify the operand first and the Engineer patch makes Physicals
# refuse sympification, which is correct for catching accidental
# unit-loss but means ``√(4 m²)`` raises).  Import sympy's version
# under a private name so the wrapper below can delegate to it.
from sympy import sqrt as _sym_sqrt
from sympy import nsolve as _sym_nsolve
from sympy import latex as _sym_latex


def latex(expr, **settings):
    """``sympy.latex`` that first peels the DSL's ``Sig`` wrapper — the
    expression ``x² + 1`` reaches Python as ``x ** Sig(2)``, which is a
    ``Sig`` carrying a sympy value; without the peel sympy would print
    it as ``\\mathtt{\\text{x**2 + 1}}``."""
    from .sigfig import _unwrap
    return _sym_latex(_unwrap(expr), **settings)


def nsolve(*args, **kwargs):
    """``sympy.nsolve`` that accepts the DSL's ``Sig``-wrapped numbers:
    ``nsolve(cos(x) - x, x, 1)`` — the starting guess ``1`` arrives as a
    ``Sig`` and mpmath cannot convert that, so unwrap first."""
    from .sigfig import _unwrap

    def _plain(v):
        if isinstance(v, (list, tuple)):
            return type(v)(_plain(i) for i in v)
        return _unwrap(v)
    return _sym_nsolve(*[_plain(a) for a in args],
                       **{k: _plain(v) for k, v in kwargs.items()})
from sympy.core.sympify import SympifyError as _SympifyError


def sqrt(x):
    """Square root with a fallback for unit-bearing operands.

    The DSL's ``√`` operator rewrites to a ``sqrt(...)`` call, and
    historically that call went straight to ``sympy.sqrt``.  That's
    the right choice for pure symbolic work (``sqrt(x)`` is sympy's
    canonical form, simplifies cleanly in subsequent algebra) and
    for plain numbers (``sqrt(16)`` returns ``Integer(4)`` — exact).

    But ``sympy.sqrt`` refuses unit-bearing operands: it tries to
    sympify the input, and the toolkit deliberately patches
    ``forallpeople.Physical`` to refuse sympification (so accidental
    ``sym.sin(0.5 * V)`` loses-units bugs are loud rather than
    silent).  The result: the natural Pythagoras expression
    ``√(a² + b²)`` raises ``SympifyError`` whenever the legs carry
    units.

    This wrapper catches that case and falls back to ``x ** 0.5`` —
    forallpeople's ``Physical`` has its own ``__pow__`` that
    correctly halves dimensions (``m² → m``, ``kg² → kg``,
    ``V⁴ → V²``).  The numeric value goes through forallpeople's
    own root logic too, so significant figures from a Sig wrapper
    are preserved.

    Three flavours of input, three behaviours:

    - Plain int / float / sympy expression: ``sympy.sqrt`` wins.
      ``sqrt(16) == Integer(4)``; ``sqrt(x_symbol) == sqrt(x_symbol)``.
    - Sig wrapping a plain number: also goes through ``sympy.sqrt``
      (sympy successfully sympifies a Sig of a plain number,
      yielding an Integer or Rational).  The Sig wrapper is dropped
      on this path — use ``x ** ½`` directly if you need to keep
      sf-tracking on the result.
    - Physical, or Sig wrapping a Physical: the sympify refusal
      triggers the fallback, and ``x ** 0.5`` returns the
      dimensionally-correct root with units halved.
    """
    try:
        return _sym_sqrt(x)
    except (_SympifyError, TypeError):
        # Physical (refuses sympification) or an interval ``Range``
        # (sympy tries ``float()`` on it) — both have a proper ``**``.
        return x ** 0.5

# NOTE: the trig / hyperbolic / ``exp`` names exported here are the
# toolkit's wrappers from ``circuit_dsl`` (see ``_numeric_or_symbolic``
# there): on a number or ``Sig`` they compute with ``math`` and keep the
# significant-figure count (``sin(0.500)`` has 3 sf; ``exp(1.0)`` is
# ``2.7``, not the symbol ``E``); on a symbolic expression they defer to
# sympy; on a dimensioned ``Physical`` they raise.  Only ``log`` is still
# sympy's own — use ``ln`` / ``log10`` / ``log2`` for the numeric,
# sf-aware forms.  The old left-handed gotcha (``sym.sin(0.5) * V``
# silently dropping the unit) still applies to the ``sym.``-prefixed
# functions, which is one more reason to use the bare names.


import datetime as _datetime


def _is_temporal(x) -> bool:
    """True for a standard-library ``date`` / ``datetime`` / ``time`` /
    ``timedelta`` — the types the DSL's ``"..."ₜᵢₘₑ`` literals produce.

    ``datetime`` subclasses ``date``, so the ``date`` check covers it.
    """
    return isinstance(
        x, (_datetime.date, _datetime.time, _datetime.timedelta)
    )


def _format_temporal_container(a):
    """Render a ``pp`` / ``pn`` argument, fixing temporal display.

    The problem: a bare date prints fine — ``str(date(2026,5,6))`` is
    the ISO ``'2026-05-06'`` — but a date *inside a container* does not.
    ``str({date, date})`` / ``str([date, date])`` fall back to each
    element's ``repr``, which is the verbose ``datetime.date(2026,5,6)``.
    A ``set`` has no separate ``str`` at all; ``list`` / ``tuple``
    ``str`` is element-``repr`` too.

    So this helper renders the common one-level-deep cases — a ``set``,
    ``frozenset``, ``list`` or ``tuple`` whose elements are ALL temporal
    — by formatting each element with ``str()`` (which is ISO for these
    types) and rebuilding the bracket form.  Mixed or non-temporal
    containers, and everything else, fall through to plain ``str(a)`` so
    nothing else changes.

    One level is deliberate: it covers ``pp({dates})`` and
    ``pp([dates])`` without chasing arbitrary nesting, which ``pp`` was
    never intended to pretty-print (the stdlib ``pprint`` is for that).
    """
    if isinstance(a, (set, frozenset, list, tuple)) and len(a) > 0:
        items = list(a)
        if all(_is_temporal(v) for v in items):
            # Sets are unordered; sort so the output is stable run to
            # run.  date / datetime / time / timedelta are all sortable.
            if isinstance(a, (set, frozenset)):
                try:
                    items = sorted(items)
                except TypeError:
                    pass  # mixed temporal types — keep insertion order
                open_b, close_b = "{", "}"
            elif isinstance(a, tuple):
                open_b, close_b = "(", ")"
            else:
                open_b, close_b = "[", "]"
            return open_b + ", ".join(str(v) for v in items) + close_b
    return str(a)


def _pp_text_atom(a):
    """Render one ``pp`` argument to TEXT (the non-typeset form).

    Mirrors the per-argument rules used wherever ``pp`` produces text: a
    sympy expression becomes its Unicode ``sym.pretty`` art, an empty
    set becomes ``∅`` (since ``str(set())`` is the literal ``'set()'``),
    and everything else — strings, numbers, ``Physical``, ``Sig``, date
    containers — goes through ``_format_temporal_container`` (which
    itself falls back to ``str`` for non-temporal values).
    """
    if isinstance(a, sym.Basic):
        return sym.pretty(a, use_unicode=True)
    if isinstance(a, (set, frozenset)) and len(a) == 0:
        return "∅"
    return _format_temporal_container(a)


def _has_latex(a) -> bool:
    """True when ``a`` exposes a ``_repr_latex_`` that yields a string.

    Used by ``pp`` to decide whether a scalar should be ``display()``-ed
    (so it typesets like a bare cell) rather than printed as text.  We
    actually *call* the hook and check for a non-empty string, because
    some types (e.g. ``Sig``) define ``_repr_latex_`` but return ``None``
    to decline for certain values — those should still print as text.
    Strings are excluded (a ``str`` has no ``_repr_latex_`` anyway, but
    guard explicitly).  Any exception → treat as no LaTeX.
    """
    if isinstance(a, (str, bytes)):
        return False
    hook = getattr(a, "_repr_latex_", None)
    if hook is None:
        return False
    try:
        out = hook()
    except Exception:
        return False
    return isinstance(out, str) and out.strip() != ""


def _is_sympy_matrix(a) -> bool:
    """True for a sympy ``Matrix`` / ``Array`` / other ``MatrixBase`` —
    including the DSL's ``_as_matrix`` shim subclass.

    These aren't ``sym.Basic`` instances, but they carry their own
    ``_repr_latex_`` (``\\left[\\begin{matrix}…\\right]``), so ``pp``
    hands them to ``display()`` directly rather than rebuilding the
    LaTeX.  Detected via the class **MRO** rather than the direct module:
    the DSL wraps auto-matrices in a subclass whose own module is the
    toolkit (``utils.circuit_dsl``), but which inherits from
    ``sympy.matrices`` — so a direct-module check would miss it.
    """
    return any(
        (c.__module__ or "").startswith("sympy.matrices")
        or (c.__module__ or "").startswith("sympy.tensor")
        for c in type(a).__mro__
    )


def _is_matrix(a) -> bool:
    """True when ``a`` is a 2-D list/tuple — a non-empty sequence whose
    every element is itself a non-empty sequence, and all rows the same
    length (a rectangular grid).

    This is what ``pp`` renders as a LaTeX ``bmatrix``.  A ``str`` is a
    sequence too, so it's explicitly excluded; a row of strings (a list
    of words) is still a valid matrix row, but the *outer* value being a
    bare string is not a matrix.
    """
    if isinstance(a, (str, bytes)):
        return False
    if not isinstance(a, (list, tuple)):
        # numpy / CommaArray: treat anything with 2-D shape as a matrix.
        shape = getattr(a, "shape", None)
        if shape is not None and len(shape) == 2:
            return True
        return False
    if len(a) == 0:
        return False
    rows = []
    for row in a:
        if isinstance(row, (str, bytes)) or not isinstance(row, (list, tuple)):
            return False
        rows.append(len(row))
    return len(rows) > 0 and all(n == rows[0] and n > 0 for n in rows)


def _is_renderable_vector(a) -> bool:
    """True when ``a`` is a 1-D list/tuple whose EVERY element is a
    LaTeX-renderable scalar (a unit value, ``Sig``, ``_Radix``, sympy
    expression, or plain number) — i.e. something that should typeset as
    a vector.

    This makes ``x := [34 mV, 35 mV, …]`` (a plain Python ``list`` of
    unit values) render the same as ``y := [34, 35, …] mV`` (a
    ``CommaArray``) — semantically identical inputs that previously
    displayed differently (text vs typeset) purely because of literal
    syntax.  Mixed lists (one plain string among numbers), lists of
    strings, ragged/2-D structures, and empty lists are rejected, so
    ordinary non-mathematical lists keep their plain text display.
    """
    if isinstance(a, (str, bytes)) or not isinstance(a, (list, tuple)):
        return False
    if len(a) == 0:
        return False
    if any(isinstance(e, (list, tuple)) for e in a):
        return False  # 2-D / nested → matrix path, not a vector
    for e in a:
        if isinstance(e, (str, bytes)):
            return False
        # A renderable element: has usable LaTeX, is a sympy object, or a
        # plain real number.  ``bool`` is excluded (True/False stay words).
        if isinstance(e, bool):
            return False
        if _has_latex(e) or isinstance(e, sym.Basic) or isinstance(e, (int, float)):
            continue
        return False
    return True


def _vector_to_latex(a) -> str:
    """Render a 1-D renderable list as a bracketed LaTeX row vector,
    each element through :func:`_matrix_cell_latex` (so units, scientific
    notation, radix, and sympy all format correctly).  Assumes
    :func:`_is_renderable_vector` already accepted ``a``.
    """
    inner = ", ".join(_matrix_cell_latex(v) for v in a)
    return r"\left[" + inner + r"\right]"


def _matrix_cell_latex(v, radix_fmt=None) -> str:
    """Render one matrix cell as a LaTeX fragment.

    A sympy expression uses ``sym.latex`` (so ``x**2`` shows as
    ``x^{2}``, ``sin(x)`` as ``\\sin x`` …).  Crucially we first peel any
    ``Sig`` / display wrapper: the DSL wraps numeric literals in ``Sig``,
    so a symbolic cell like ``x**2`` arrives as ``Sig(x**2)`` (the
    integer exponent made ``Sig.__rpow__`` fire) — unwrapping reveals the
    sympy ``Pow`` underneath, which then latexes correctly instead of
    falling back to the Python-notation ``str`` (``x**2``).  A ``_Radix``
    / ``_InUnits`` cell keeps its own ``repr`` (so ``1001₂`` keeps its
    base-marker subscript and ``5 V`` keeps its unit).  Everything else
    falls back to ``str``.

    ``radix_fmt`` (e.g. ``"hex"``) — when the matrix carries a ``▸``
    radix display tag — formats an integer cell in that base via the
    toolkit's ``radix`` (so ``M ▸ hex`` shows each cell in hex without
    the matrix itself ceasing to be a matrix).
    """
    if radix_fmt is not None:
        try:
            from .sigfig import radix as _radix
            return str(_radix(int(v), radix_fmt))
        except Exception:
            pass  # not an integer cell — fall through to normal handling
    # Peel a precision/display wrapper to see whether a sympy expression
    # is hiding underneath.  ``_Radix`` / ``_InUnits`` are deliberately
    # NOT unwrapped — their own repr is the wanted cell text.
    inner = v
    while type(inner).__name__ == "Sig":
        inner = inner.value
    if isinstance(inner, sym.Basic):
        return sym.latex(inner)
    if isinstance(v, sym.Basic):
        return sym.latex(v)
    # A cell that carries its OWN LaTeX — a ``Sig``-wrapped unit, a
    # forallpeople ``Physical`` (``5 V``), a ``_Radix`` (``FF₁₆``) — must
    # be rendered through that, not ``str(v)``: otherwise a unit cell
    # would show the raw ``1.98842e+30 kg`` (sci-notation un-typeset, unit
    # not in ``\mathrm{}``).  Use the cell's ``_repr_latex_``, then strip
    # the outer ``$`` and any ``\displaystyle`` so it embeds cleanly in
    # the surrounding matrix (which is one big ``$…$``).
    hook = getattr(v, "_repr_latex_", None)
    if hook is not None:
        try:
            lx = hook()
            if isinstance(lx, str) and lx.strip():
                from .circuit_dsl import (_strip_displaystyle,
                                          _strip_inner_dollars)
                return _strip_inner_dollars(
                    _strip_displaystyle(lx)).strip("$")
        except Exception:
            pass
    return str(v)


def _matrix_to_latex(a) -> str:
    """Build a LaTeX ``bmatrix`` string from a 2-D list/tuple/array or a
    sympy matrix.

    Assumes :func:`_is_matrix` or :func:`_is_sympy_matrix` already
    accepted ``a``.  Rows become ``&``-separated cells joined by
    ``\\\\``; each cell goes through :func:`_matrix_cell_latex`.  If the
    matrix carries a ``_dsl_radix`` display tag (from ``M ▸ hex``), the
    tag's base is threaded into each cell so they render in that base.
    """
    radix_fmt = getattr(a, "_dsl_radix", None)
    if not isinstance(a, (list, tuple)):
        a = a.tolist() if hasattr(a, "tolist") else [list(row) for row in a]
    rows = [" & ".join(_matrix_cell_latex(v, radix_fmt) for v in row)
            for row in a]
    body = r" \\ ".join(rows)
    return r"\begin{bmatrix} " + body + r" \end{bmatrix}"


def _matrix_to_html(a) -> str:
    """Build a portable HTML-``<table>`` rendering of a matrix.

    ``\\begin{bmatrix}`` is an AMS-math environment that renders live
    (MathJax) but is frequently dropped on EXPORT, leaking raw
    ``$\\begin{bmatrix}…$`` source.  An HTML table needs no math renderer
    for its layout and survives export.  Two refinements over a naive
    table:

    * **Per-cell math only when needed.**  Wrapping *every* cell in
      ``$…$`` backfires on export: the export path doesn't run MathJax
      over table-cell contents, so plain cells showed literal
      ``$FF₁₆$``.  Numeric / hex / radix / simple-symbol cells are
      already plain text (``FF₁₆``, ``42``, ``a``) and are emitted
      verbatim — no ``$``; only a cell that contains real LaTeX syntax
      (``^``, ``\\``, ``{``, ``_``, e.g. ``a^{2}`` or ``\\frac{3}{2}``)
      is wrapped in ``$…$`` so it still math-renders live.
    * **Explicit styling.**  Inline styles reset Jupyter's default table
      look (which adds alternating-row gray "zebra" stripes and makes a
      matrix read like a data grid) and draw thin square-bracket borders
      on the left and right edges so it reads as a matrix.
    """
    radix_fmt = getattr(a, "_dsl_radix", None)
    if not isinstance(a, (list, tuple)):
        a = a.tolist() if hasattr(a, "tolist") else [list(row) for row in a]

    _latex_chars = set("\\^_{}")

    def _cell_html(v):
        tex = _matrix_cell_latex(v, radix_fmt)
        # Only wrap in math mode if the content actually contains LaTeX
        # syntax; otherwise emit the plain text (export-safe, no stray $).
        content = f"${tex}$" if (set(tex) & _latex_chars) else tex
        return ('<td style="padding:1px 10px;text-align:right;'
                'background:transparent;border:none;">' + content + '</td>')

    rows = "".join(
        '<tr style="background:transparent;border:none;">'
        + "".join(_cell_html(v) for v in row) + "</tr>"
        for row in a
    )
    # ``border-collapse`` + transparent cell/row backgrounds defeat the
    # default zebra striping; left/right borders form the matrix brackets.
    return (
        '<table style="display:inline-table;vertical-align:middle;'
        'border-collapse:collapse;background:transparent;'
        'border-left:2px solid currentColor;'
        'border-right:2px solid currentColor;margin:2px 6px;">'
        '<tbody style="background:transparent;">' + rows + '</tbody></table>'
    )


def _text_to_latex(s: str) -> str:
    """Render a plain ``pp`` label for inclusion in a combined math line.

    Most of the label is escaped and wrapped in ``\\text{…}`` so an
    arbitrary string (``50%``, ``a&b``) renders safely.  BUT an
    identifier of the form ``name_sub`` — the engineering convention for
    a subscript, e.g. ``R_eq``, ``R_1``, ``V_out`` — is rendered as a
    real LaTeX subscript (``R_{eq}``, ``R_{1}``, ``V_{out}``) instead of
    a literal underscore.  This is the same rule sympy applies to symbol
    names, now applied to the *text label* in ``pp("R_eq =", R_eq)`` —
    the one place a variable's name is actually present to render.  A
    Greek-letter base (``alpha_n``) is also converted to its symbol.
    Everything that isn't such an identifier stays literal text.
    """
    # Greek spelled-out names LaTeX knows, so ``omega_0`` → ``\omega_{0}``.
    _greek = {
        "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta",
        "theta", "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron",
        "pi", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi",
        "omega", "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi",
        "Sigma", "Phi", "Psi", "Omega",
    }
    # Literal Greek GLYPHS (the form the DSL actually uses, e.g.
    # ``ν_yellow``, ``λ_0``) → their LaTeX command, so the base renders as
    # a real Greek symbol rather than a literal character stuck in
    # ``\text{}``.
    _greek_glyph = {
        "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
        "ε": r"\epsilon", "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta",
        "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu",
        "ν": r"\nu", "ξ": r"\xi", "ο": "o", "π": r"\pi", "ρ": r"\rho",
        "σ": r"\sigma", "τ": r"\tau", "υ": r"\upsilon", "φ": r"\phi",
        "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
        "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
        "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma", "Φ": r"\Phi",
        "Ψ": r"\Psi", "Ω": r"\Omega",
    }

    def _esc(text):
        repl = {
            "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}",
            "$": r"\$", "&": r"\&", "%": r"\%", "#": r"\#",
            "_": r"\_", "^": r"\textasciicircum{}",
            "~": r"\textasciitilde{}",
        }
        return "".join(repl.get(ch, ch) for ch in text)

    def _base_latex(name):
        # A single Greek glyph base (``ν``) → its LaTeX command.
        if name in _greek_glyph:
            return _greek_glyph[name]
        # A spelled-out Greek name (``omega``) → its command.
        if name in _greek:
            return "\\" + name
        return name

    # An identifier with a single trailing ``_subscript``: a base that is
    # either ASCII letters OR a single Greek glyph, then ``_``, then an
    # alphanumeric subscript.  The Greek glyphs are listed explicitly so
    # the base can start with one (``ν_yellow``, ``λ_0``).
    glyphs = "".join(_greek_glyph.keys())
    ident = re.compile(
        r'(?:[A-Za-z][A-Za-z]*|[' + glyphs + r'])_[A-Za-z0-9]+'
    )
    out = []
    last = 0
    for m in ident.finditer(s):
        if m.start() > last:
            out.append(r"\text{" + _esc(s[last:m.start()]) + "}")
        base, sub = m.group(0).split("_", 1)
        out.append(f"{_base_latex(base)}_{{{sub}}}")  # math subscript
        last = m.end()
    if last < len(s):
        out.append(r"\text{" + _esc(s[last:]) + "}")
    if not out:
        out.append(r"\text{" + _esc(s) + "}")
    return "".join(out)


def _arg_to_latex(a):
    """Return the inner LaTeX (no surrounding ``$``) for a pp argument
    that should be typeset, or ``None`` if it has no usable LaTeX.

    Handles the same value families ``pp``'s per-argument routing does:
    radix-tagged matrices, sympy expressions / matrices, 2-D Python
    matrices, and any value carrying its own ``_repr_latex_`` (``Sig``,
    ``_Radix``).  The leading ``\\displaystyle`` is stripped so combined
    output stays left-aligned like the rest.
    """
    try:
        from .circuit_dsl import _strip_displaystyle, _strip_inner_dollars
    except Exception:
        def _strip_displaystyle(x):
            return x
        def _strip_inner_dollars(x):
            return x

    def _clean(lx):
        # Strip ``\displaystyle`` (centring) and any spurious internal
        # ``$`` (forallpeople's ``\mathrm{$\Omega$}`` ohm bug), then drop
        # the outer ``$`` so it embeds in the combined formula.
        return _strip_inner_dollars(_strip_displaystyle(lx)).strip("$")

    try:
        if _is_sympy_matrix(a) and getattr(a, "_dsl_radix", None):
            return _matrix_to_latex(a)
        if isinstance(a, sym.Basic) or _is_sympy_matrix(a):
            return _clean(a._repr_latex_())
        if _is_matrix(a):
            return _matrix_to_latex(a)
        if _is_renderable_vector(a):
            return _vector_to_latex(a)
        if _has_latex(a):
            return _clean(a._repr_latex_())
        # A plain Python number (``(255 ▸ hex) + 1`` → int ``256``) has no
        # ``_repr_latex_`` of its own, but should still typeset.  ``bool``
        # is excluded so ``True`` / ``False`` stay words.
        if isinstance(a, (int, float)) and not isinstance(a, bool):
            try:
                from .sigfig import _sci_to_latex
                return _sci_to_latex(repr(a))
            except Exception:
                return repr(a)
    except Exception:
        return None
    return None


def _rich_display():
    """Return IPython's ``display`` callable when a *rich* frontend (a
    Jupyter notebook / qtconsole, with MathJax) is active, else ``None``.

    Used by ``pp`` to decide whether it can emit a typeset formula.  The
    plain terminal IPython and a bare ``python`` REPL both lack MathJax,
    so for them this returns ``None`` and ``pp`` keeps its text
    (``sym.pretty``) output.  Detection is by the active shell class
    name, which avoids importing IPython at module load (the toolkit
    must work without IPython installed).
    """
    try:
        from IPython import get_ipython
        shell = get_ipython()
        if shell is not None and type(shell).__name__ == "ZMQInteractiveShell":
            from IPython.display import display
            return display
    except Exception:
        pass
    return None


def pp(*args, sep=' ', end='\n', file=None, flush=False):
    """Pretty-printing ``print``: same signature, sympy expressions show as
    Unicode (``100⋅π``) instead of ASCII (``100*pi``).

    Sympy's own ``str()`` (which ``print`` calls) deliberately produces a
    round-trippable Python serialisation — ``str(100*sym.pi) == "100*pi"``
    — because that's the right contract for ``str``.  ``sym.pprint`` does
    Unicode pretty-printing but takes a single expression and discards
    the format-string ergonomics of ``print`` (separators, ``\\t``, ``\\n``,
    multiple labelled values).  This helper combines the two:

        x := 2π * 50
        y := 3π / 4
        pp("frequency =", x, "\\tphase =", y)
        # → frequency = 100⋅π        phase = 3⋅π/4

    Conversion is per-argument:

      - ``isinstance(arg, sym.Basic)`` — anything in sympy's expression
        hierarchy, including symbols, numbers, equations, matrices —
        is rendered with ``sym.pretty(arg, use_unicode=True)``.
      - Everything else passes through ``str()``: plain strings keep
        their format characters, ints/floats stay numeric, and types
        with their own ``__str__`` (forallpeople ``Physical``, sigfig
        ``Sig``) keep their existing presentation.

    Multi-line pretty forms — fractions, matrices, integrals — flow as
    multi-line strings, which can look ragged when interleaved with
    single-line labels.  For those, prefer ``display(x)`` in Jupyter:
    that uses sympy's LaTeX form with MathJax rendering, the same path
    the cell auto-display takes.

    Note on the name: ``pp`` shadows ``pprint.pp`` (the stdlib
    data-structure pretty-printer added in 3.8).  The two have unrelated
    purposes — this one is for math display, that one is for nested
    containers.  If you need both, import the stdlib one under another
    name: ``from pprint import pp as ppd``.

    Empty sets render as ``∅``: ``pp`` walks its arguments and special-
    cases an empty ``set`` / ``frozenset`` to the mathematical symbol.
    This is needed because ``pp`` (like ``print``) ultimately goes
    through ``str()``, and ``str(set())`` is the fixed CPython form
    ``'set()'`` — the IPython display formatter that handles bare cell
    output does NOT see ``print``/``pp`` arguments, so the substitution
    has to happen here too.  Non-empty sets keep their normal form.
    """
    # Rich (Jupyter) path — per-argument routing.  When a MathJax
    # frontend is active, each sympy argument is typeset with
    # ``display()`` (the real fraction-bar rendering the cell value
    # uses), while runs of non-sympy arguments are printed as text in
    # between.  Order is preserved, so
    #   pp(diff(sin(x), x), U1·R2/(R1+R2), "Nice!")
    # shows the two formulas typeset, each on its own line, then the
    # label ``Nice!`` as text — the same result as hand-writing
    # ``display(expr1, expr2); pp("Nice!")``.
    #
    # Consecutive text/other arguments are grouped into a single
    # ``print`` so separators still apply (``pp("a", "b")`` → ``a b`` on
    # one line); each sympy argument is its own typeset block, since a
    # rendered formula can't share a text line.  Skipped entirely when
    # output is redirected (``file=``) or no rich frontend is present —
    # those fall through to the all-text path below.
    if file is None and args:
        display = _rich_display()
        if display is not None:
            # --- Combine renderable args onto ONE line ------------------
            # Assemble a SINGLE inline formula from the arguments — text
            # wrapped in ``\text{…}``, each LaTeX-capable value inserted as
            # its LaTeX — and emit one ``Math``.  This makes ``pp`` produce
            # typeset output for everything renderable and keeps multiple
            # values on one row: ``pp("M =", M)`` (label + matrix),
            # ``pp(1, 2, 3, 4)`` (a row of numbers), ``pp([..] ▸ hex)`` (a
            # radix vector) all render as one centred line.
            #
            # Taken when EVERY argument is convertible (a string, or a
            # value with usable LaTeX) AND at least one is renderable math,
            # with no text arg containing a newline/tab (those need real
            # ``print`` formatting).  A purely-text call (``pp("a", "b")``)
            # has no math and falls through to the plain ``print`` path, so
            # text stays text.  Anything non-convertible also falls through.
            has_math = any(_arg_to_latex(a) is not None for a in args)
            all_convertible = all(
                isinstance(a, str) or _arg_to_latex(a) is not None
                for a in args
            )
            # A newline still needs real ``print`` (multi-line output);
            # a TAB is now a column separator handled by the table branch
            # below, so it no longer blocks the LaTeX path.
            no_newline = all(
                "\n" not in a for a in args if isinstance(a, str)
            )
            has_tab = any("\t" in a for a in args if isinstance(a, str))

            # --- Tab-aligned single row ---------------------------------
            # If any string arg contains a TAB, treat tabs as column
            # breaks and render ONE aligned row via a math ``array``
            # (MathJax renders ``array``; it does not render text-mode
            # ``tabular``).  Cells split on ``\t``; each cell is text
            # (→ ``\text{…}``, left-aligned) or a renderable value
            # (→ its LaTeX, right-aligned), so numbers line up on the
            # right and labels on the left automatically.  ``pp('R_eq\t',
            # R_eq)`` → ``R_eq  &  <value>``.
            if has_tab and all_convertible and no_newline:
                cells = []          # list of (latex, is_math)
                ok = True
                for a in args:
                    if isinstance(a, str):
                        # Split this string into cells on tabs.  An empty
                        # piece (from a leading/trailing/double tab) is a
                        # pure separator — ``'R_eq\t'`` means "column break
                        # before the next arg", not "an empty column" — so
                        # empty pieces are dropped; the tab's only job is
                        # to start a new column for whatever follows.
                        for piece in a.split("\t"):
                            if piece:
                                cells.append((_text_to_latex(piece), False))
                    else:
                        lx = _arg_to_latex(a)
                        if lx is None:
                            ok = False
                            break
                        cells.append((lx, True))
                if ok and cells:
                    align = "".join("r" if is_math else "l"
                                    for _, is_math in cells)
                    body = " & ".join(lx for lx, _ in cells)
                    try:
                        from IPython.display import Math
                        display(Math(r"\begin{array}{" + align + "}"
                                     + body + r"\end{array}"))
                        return
                    except Exception:
                        pass  # fall through

            if has_math and all_convertible and no_newline and not has_tab:
                pieces = []
                ok = True
                for a in args:
                    if isinstance(a, str):
                        # Skip an empty string entirely (no stray ``\text{}``
                        # spacer) so ``pp("", x)`` == ``pp(x)``.
                        if a == "":
                            continue
                        pieces.append(_text_to_latex(a))
                    else:
                        lx = _arg_to_latex(a)
                        if lx is None:
                            ok = False
                            break
                        pieces.append(lx)
                if ok and pieces:
                    try:
                        from IPython.display import Math
                        # ``\;`` = a thin space between parts.
                        display(Math(r"\;".join(pieces)))
                        return
                    except Exception:
                        pass  # fall through to per-argument routing

            pending: list = []  # buffered non-sympy args awaiting a flush

            def _flush_text():
                if pending:
                    print(*pending, sep=sep, end=end, flush=flush)
                    pending.clear()

            for a in args:
                if _is_sympy_matrix(a) and getattr(a, "_dsl_radix", None):
                    # A matrix carrying a ``▸`` radix display tag
                    # (``M ▸ hex``): build the bmatrix ourselves so each
                    # cell shows in the tagged base.
                    _flush_text()
                    try:
                        from IPython.display import Math
                        display(Math(_matrix_to_latex(a)))
                    except Exception:
                        print(_pp_text_atom(a), end=end, flush=flush)
                elif isinstance(a, sym.Basic) or _is_sympy_matrix(a):
                    # A sympy expression OR an untagged sympy Matrix.
                    # sympy's own ``_repr_latex_`` prepends
                    # ``\displaystyle`` (which MathJax centres), so to
                    # keep ``pp`` output LEFT-aligned like every other
                    # value we render the cleaned LaTeX through ``Math``
                    # rather than ``display(a)`` directly.
                    _flush_text()
                    try:
                        from IPython.display import Math
                        from .circuit_dsl import _strip_displaystyle
                        lx = a._repr_latex_()
                        lx = _strip_displaystyle(lx).strip("$")
                        display(Math(lx))
                    except Exception:
                        display(a)
                elif _is_matrix(a):
                    # A 2-D Python list / tuple / numpy array → typeset
                    # as a LaTeX ``bmatrix``.
                    _flush_text()
                    try:
                        from IPython.display import Math
                        display(Math(_matrix_to_latex(a)))
                    except Exception:
                        # If Math/LaTeX display fails for any reason, fall
                        # back to plain text rather than dropping the arg.
                        print(_pp_text_atom(a), end=end, flush=flush)
                elif _has_latex(a):
                    # A value that carries its own LaTeX (a ``Sig``-wrapped
                    # unit/number, a ``Physical``, …) → ``display()`` it so
                    # ``pp(12 mV)`` typesets exactly like a bare ``12 mV``
                    # cell, instead of falling to plain stdout text.  Only
                    # taken when ``_repr_latex_`` actually yields a string
                    # (``_has_latex`` checks), so values that *have* the
                    # method but decline (return ``None``) still print as
                    # text.
                    _flush_text()
                    display(a)
                else:
                    pending.append(_pp_text_atom(a))
            _flush_text()
            return

    parts = [_pp_text_atom(a) for a in args]
    print(*parts, sep=sep, end=end, file=file, flush=flush)


def pn(*args, sep=' ', end='\n', file=None, flush=False, prec=6):
    """Numeric ``print``: same signature as ``pp``, but sympy expressions
    are evaluated to a number at ``prec`` significant figures (default 6).

    Symmetric counterpart to ``pp``: where ``pp`` keeps the symbolic form
    (``100⋅π``), ``pn`` shows the actual number you'd get if you evaluated
    it (``314.159``).  Use whichever matches what you're after::

        x := 2π * 50
        pp(x)     # 100⋅π        — symbolic, exact
        pn(x)     # 314.159      — numeric, 6 sig figs

    The ``prec`` keyword controls how many significant figures sympy
    evaluates to::

        pn(sym.exp(1))            # 2.71828
        pn(sym.exp(1), prec=12)   # 2.71828182846

    Conversion is per-argument:

      - ``isinstance(arg, sym.Basic)`` — evaluated with ``sym.N(arg, prec)``.
        Equations and matrices evaluate element-wise.  Expressions with
        free symbols partially evaluate (``α + 2⋅β`` → ``α + 2.0⋅β``);
        this is sympy's behaviour, not something we override.
      - Everything else passes through ``str()`` — including ``Physical``
        (already numeric with units), ``Sig`` (already sf-formatted), and
        plain ints/floats.  ``prec`` does NOT affect non-sympy arguments;
        format those at the call site if you want truncation.

    Note: evaluating to a number drops the sigfig tracking that the
    toolkit's ``Sig`` system maintains.  ``pn(x)`` always shows ``prec``
    digits regardless of what the inputs claim to know — convenient for
    a-quick-look output, but if you care about the propagated precision
    of a calculation, work with ``Sig`` values and let ``str()`` handle
    them (which is what the bare ``print`` path already does).
    """
    parts = []
    for a in args:
        if isinstance(a, sym.Basic):
            # ``sym.N`` returns a sympy Float (or partially-evaluated
            # expression for things with free symbols), which str()s
            # cleanly to a decimal at the requested precision.
            parts.append(str(sym.N(a, prec)))
        elif isinstance(a, (set, frozenset)) and len(a) == 0:
            # Empty set → ∅, consistent with ``pp`` and the cell-output
            # display formatter.  See the note in ``pp``.
            parts.append("∅")
        else:
            # Date / list-of-dates rendering, same as ``pp`` — see
            # ``_format_temporal_container``.  ``prec`` does not apply to
            # temporal values (they aren't sympy numbers).
            parts.append(_format_temporal_container(a))
    print(*parts, sep=sep, end=end, file=file, flush=flush)


def _pv_recover_names(args_count):
    """Recover the source-text names of the arguments in the ``pv(...)``
    call one frame up, returning a list of ``args_count`` label strings
    (or ``None`` if the call source can't be parsed).

    Robust against two hazards specific to this toolkit:

    * **The source transform.**  The ``ideas`` import hook rewrites the
      cell before execution, so a ``▸`` display tag becomes a call like
      ``in_units(R_eq, 'hex', 'hex')``.  We parse the (possibly rewritten)
      call with ``ast`` and, for such wrapper calls, recover the ORIGINAL
      variable name from the first argument rather than the rewritten form.
    * **Commas inside arguments.**  A naive ``split(", ")`` breaks on
      ``pv(f(a, b), c)`` or ``pv([1, 2], x)``.  Parsing with ``ast`` and
      unparsing each argument node is comma-safe.

    Falls back to the unparsed expression text for anything that isn't a
    plain name (``pv(a + b)`` → label ``"a + b"``).
    """
    import inspect
    import ast as _ast

    frame = inspect.currentframe()
    # Walk up: _pv_recover_names → pv → caller.
    caller = frame.f_back.f_back if frame and frame.f_back else None
    if caller is None:
        return None
    try:
        info = inspect.getframeinfo(caller, context=1)
        ctx = info.code_context
    except Exception:
        return None
    if not ctx:
        return None

    line = ctx[0]
    m = re.search(r'\bpv\s*\(', line)
    if not m:
        return None
    # Extract a balanced parenthesised argument list from the ``pv(`` we
    # found, so trailing code on the line doesn't confuse the parse.
    start = m.end() - 1            # index of the '('
    depth = 0
    end = None
    for i in range(start, len(line)):
        c = line[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        return None
    call_src = line[m.start():end + 1]

    try:
        node = _ast.parse(call_src, mode="eval").body
        if not isinstance(node, _ast.Call):
            return None
        arg_nodes = node.args
    except Exception:
        return None

    # Wrapper calls the source transform emits whose FIRST argument is the
    # original variable/expression we want the name of.
    _display_wrappers = {"in_units", "radix", "_idx"}

    def _name_of(a):
        if (isinstance(a, _ast.Call) and isinstance(a.func, _ast.Name)
                and a.func.id in _display_wrappers and a.args):
            return _name_of(a.args[0])
        if isinstance(a, _ast.Name):
            return a.id
        try:
            return _ast.unparse(a)
        except Exception:
            return "?"

    names = [_name_of(a) for a in arg_nodes]
    if len(names) != args_count:
        return None
    return names


def _pv_user_variables():
    """Return ``[(name, value), …]`` for the user-defined variables in the
    caller's (caller-of-``pv``) global namespace, in definition order.

    "User-defined" = a name present in the caller's globals that is NOT
    part of the toolkit's ``import *`` surface (``utils.Engineer.__all__``)
    and is not a dunder, module, or imported submodule.  Because a dict
    preserves insertion order, iterating the globals yields names roughly
    in the order they were first assigned — the "definition order if
    recoverable" the empty ``pv()`` aims for.  Returns ``None`` if the
    caller frame or the toolkit baseline can't be reached.
    """
    import inspect
    import types

    frame = inspect.currentframe()
    # Walk up: _pv_user_variables → pv → caller.
    caller = frame.f_back.f_back if frame and frame.f_back else None
    if caller is None:
        return None
    g = caller.f_globals

    try:
        from . import Engineer as _eng
        baseline = set(getattr(_eng, "__all__", ()))
    except Exception:
        baseline = set()
    # Also exclude the toolkit's own ``__all__`` symbol and common import
    # artifacts that may sit in globals without being in ``__all__``.
    baseline |= {"__all__", "si", "np", "inspect", "re", "math"}
    # IPython/Jupyter inject their own session names into the user
    # namespace (the input/output history, exit helpers, the kernel
    # accessor).  These aren't the user's variables, so exclude them.
    baseline |= {"In", "Out", "exit", "quit", "get_ipython", "open"}

    # IPython also creates dynamic history names: the output cache ``_``,
    # ``__``, ``___``; input lines ``_i``, ``_ii``, ``_iii``, ``_iN``;
    # and output refs ``_N``, ``_oh``, ``_ih``, ``_dh``.  Most are caught
    # by the leading-underscore skip below, but the bare ``_``/``__`` and
    # any stray ones are covered by that same rule.

    out = []
    for name, val in g.items():
        if name.startswith("_"):
            continue                      # dunders / privates / IPython history
        if name in baseline:
            continue                      # toolkit surface + IPython session names
        if isinstance(val, types.ModuleType):
            continue                      # imported modules
        if isinstance(val, type):
            continue                      # user-defined classes
        if callable(val) and isinstance(
                val, (types.FunctionType, types.LambdaType,
                      types.BuiltinFunctionType, types.MethodType)):
            continue                      # user-defined functions/lambdas
        # IPython's ``exit``/``quit`` are autocall objects (not plain
        # functions); catch them and any similar REPL helpers by type name.
        if type(val).__name__ in ("ZMQExitAutocall", "ExitAutocall"):
            continue
        out.append((name, val))
    return out


def pv(*args):
    """Print each argument labelled with the *name* you passed it as —
    ``pv(R_eq, V_out)`` prints ``R_eq = …`` then ``V_out = …`` without
    you retyping the names.

    With NO arguments, ``pv()`` prints every variable YOU have defined —
    each name in the namespace that isn't part of the toolkit's
    ``import *`` surface — as one labelled block, in definition order.
    Handy as a quick "show my workspace".

    The name of each argument is recovered from the call's source text,
    so the labels track whatever expression you wrote (a bare variable
    shows its name; ``pv(a + b)`` labels the line ``a + b``).  A ``▸``
    display tag is handled too: ``pv(R_eq ▸ hex)`` recovers ``R_eq`` as
    the label and shows the hex value.  Each labelled value is rendered
    through :func:`pp`, so it typesets in a notebook (with the
    ``name_subscript`` → ``name_{sub}`` convention applied to the label)
    and prints cleanly outside one.

    If the call source can't be recovered (a dynamically-built call, or
    one split across lines so the single-line context is incomplete), the
    values are still shown via ``pp`` — just without the name labels.
    """
    if not args:
        # ``pv()`` — dump the user's own variables.
        try:
            user_vars = _pv_user_variables()
        except Exception:
            user_vars = None
        if not user_vars:
            return
        for name, val in user_vars:
            pp(f"{name} =", val)
        return
    try:
        names = _pv_recover_names(len(args))
    except Exception:
        names = None
    for k, val in enumerate(args):
        if names is not None:
            pp(f"{names[k]} =", val)
        else:
            pp(val)


__all__ = [
    "sym",
    "Symbol", "Eq", "Rational", "oo",
    "solve", "expand", "factor", "simplify",
    "diff", "integrate", "limit", "series",
    "nsolve", "lambdify", "latex", "nsimplify",
    "Matrix", "det", "eye", "zeros", "ones", "diag",
    "Reals", "Integers", "Naturals",
    # Trig / exp / log / sqrt — see the comment near the imports for
    # the unit-shadowing caveat.
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "sinh", "cosh", "tanh", "asinh", "acosh", "atanh",
    "exp", "log", "sqrt",
    "pi", "E",
    "pp", "pn", "pv",
]
