"""Typeset the statements the DSL transformer produces.

A worksheet shows each math region as built-up notation — fraction bars,
roots, real subscripts, upright units.  The LaTeX is derived from the
*transformed* Python, i.e. from the code that actually runs, so the
display cannot disagree with the calculation about grouping, precedence
or which names are units: ``12 V / 3 A`` is drawn as one quantity over
another because that is how the transformer bound it.

Entry point: :func:`render_statement`.  It returns ``None`` for anything
that has no sensible built-up form (loops, imports, ``pp(...)`` calls);
the worksheet shows those as source text instead.
"""
from __future__ import annotations

import ast
import re

from ..sigfig import _GREEK_GLYPH_TEX, _unit_label_to_latex

__all__ = ["render_statement", "name_to_latex", "NameInfo", "Unrenderable", "CLOCK_WORDS"]

# ``"now"ₜᵢₘₑ`` and friends: the toolkit's own way to ask the clock.
CLOCK_WORDS = frozenset({"now", "today", "utcnow"})


class Unrenderable(Exception):
    """The node has no built-up form; the caller shows source text."""


# Binding strength, loosest first.  ``QTY`` sits above ``MUL`` so that a
# quantity (``12 V``) reads as one thing inside a product or a parallel
# combination, yet still gets parentheses as the base of a power.
(P_TOP, P_LAMBDA, P_COND, P_OR, P_AND, P_NOT, P_CMP, P_BITOR, P_BITXOR,
 P_BITAND, P_SHIFT, P_ADD, P_PAR, P_MUL, P_NEG, P_QTY, P_POW, P_POST,
 P_ATOM) = range(19)

_GREEK = dict(_GREEK_GLYPH_TEX)
_GREEK.update({
    "ε": r"\varepsilon",          # drawn curly in engineering texts (ε₀)
    "φ": r"\varphi", "ϕ": r"\phi", "ϑ": r"\vartheta", "ϵ": r"\epsilon",
    "ϱ": r"\varrho", "ς": r"\varsigma", "ħ": r"\hbar", "ℏ": r"\hbar",
})

# Spelled-out Greek names, as sympy and ``pv`` read them (``omega_0``).
_GREEK_WORDS = {
    w: "\\" + w for w in (
        "alpha beta gamma delta epsilon zeta eta theta iota kappa mu nu xi "
        "rho sigma tau upsilon phi chi psi omega "
        "Gamma Delta Theta Lambda Xi Sigma Upsilon Phi Psi Omega").split()
}

# Toolkit constants whose source spelling uses subscript glyphs.  Python
# folds those glyphs into plain letters (``εₒ`` parses as ``εo``), so the
# subscript has to be put back by name.
_CONSTANT_TEX = {
    "εo": r"\varepsilon_{o}", "μo": r"\mu_{o}", "me": r"m_{e}", "mp": r"m_{p}",
    "kβ": r"k_{\beta}", "qe": r"q_{e}", "Rgas": r"R_{\mathrm{gas}}",
    "NA": r"N_{A}", "gn": r"g_{n}", "go": r"g_{o}", "To": r"T_{o}",
    "π": r"\pi", "pi": r"\pi", "inf": r"\infty", "oo": r"\infty",
    "_INF": r"\infty", "nan": r"\mathrm{NaN}",
}

_TEXT_ESCAPES = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$",
    "&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_",
    "^": r"\textasciicircum{}", "~": r"\textasciitilde{}",
}


def _text(s: str) -> str:
    return "".join(_TEXT_ESCAPES.get(ch, ch) for ch in s)


def _wrap(run: str, wrap: str) -> str:
    if wrap == r"\mathit" and len(run) == 1:
        return _text(run)                 # one letter is italic already
    return f"{wrap}{{{_text(run)}}}"


def _greek_run(s: str, wrap: str) -> str:
    """Latin runs inside ``wrap{…}``, Greek glyphs as their commands."""
    out, run = [], ""
    for ch in s:
        cmd = _GREEK.get(ch)
        if cmd is None:
            run += ch
            continue
        if run:
            out.append(_wrap(run, wrap))
            run = ""
        out.append(cmd + " ")
    if run:
        out.append(_wrap(run, wrap))
    return "".join(out).rstrip()


def _symbol(base: str, wrap: str) -> str:
    if base in _GREEK_WORDS:
        return _GREEK_WORDS[base]
    if len(base) == 1:
        return _GREEK.get(base, _text(base))
    return _greek_run(base, wrap)


def _subscript(tok: str) -> str:
    if tok.isdigit():
        return tok
    if tok in _GREEK_WORDS:
        return _GREEK_WORDS[tok]
    if len(tok) == 1:
        return _GREEK.get(tok, _text(tok))
    return _greek_run(tok, r"\mathrm")


def name_to_latex(name: str, *, function: bool = False) -> str:
    """An identifier as a mathematical symbol.

    ``V_out`` → ``V_{\\mathrm{out}}`` (the engineering convention: an
    underscore starts a subscript), ``R_θJA`` gets a real theta, a
    one-letter name is italic and a word is set as a word
    (``\\mathit{Diagonal}``) rather than as a product of letters.  A
    snake_case name with several underscores stays a word.  A trailing
    underscore — the usual way round a reserved name, ``h_`` — is kept
    visible so that two different names never draw alike.
    """
    wrap = r"\operatorname" if function else r"\mathit"
    lead = len(name) - len(name.lstrip("_"))
    core = name[lead:]
    trail = len(core) - len(core.rstrip("_"))
    if trail:
        core = core[:-trail]
    parts = core.split("_")
    if not core or "" in parts:
        return _wrap(name, wrap)
    base, subs = parts[0], parts[1:]
    one_letter = len(base) == 1 or base in _GREEK_WORDS
    if subs and (len(subs) > 1 or function) and not one_letter:
        tex = _wrap(core, wrap)                       # my_long_name, to_kelvin
    else:
        tex = _symbol(base, wrap)
        if subs:
            tex += "_{" + ",".join(_subscript(s) for s in subs) + "}"
    return r"\_" * lead + tex + r"\_" * trail


class NameInfo:
    """What the renderer needs to know about the names in scope.

    The defaults suit a renderer with no namespace at hand; the engine
    subclasses this to answer from the live worksheet namespace.
    """

    def is_unit(self, name: str) -> bool:
        return False

    def is_user(self, name: str) -> bool:
        """Bound by the worksheet itself — so a toolkit helper or
        constant of the same name has been replaced and gets no special
        notation."""
        return False

    def unit_label(self, name: str) -> str:
        return name


# Identifier spellings of units that are written with a glyph.
_UNIT_LABELS = {"deltaC": "ΔC", "deltaF": "ΔF", "deltaK": "ΔK",
                "degC": "°C", "degF": "°F", "degR": "°R"}
# The transformer spells a ``▸ °C`` display target as the delta unit.
_TARGET_LABELS = {"deltaC": "°C", "deltaF": "°F", "deltaK": "K",
                  "ΔC": "°C", "ΔF": "°F", "ΔK": "K"}


def _unit_tex(label: str) -> str:
    label = _UNIT_LABELS.get(label, label)
    return _unit_label_to_latex(label).replace("°", r"{}^{\circ}")


_FUNC_TEX = {
    "sin": r"\sin", "cos": r"\cos", "tan": r"\tan",
    "asin": r"\arcsin", "acos": r"\arccos", "atan": r"\arctan",
    "sinh": r"\sinh", "cosh": r"\cosh", "tanh": r"\tanh",
    "asinh": r"\operatorname{arsinh}", "acosh": r"\operatorname{arcosh}",
    "atanh": r"\operatorname{artanh}",
    "ln": r"\ln", "log": r"\log", "log10": r"\log_{10}", "log2": r"\log_{2}",
    "det": r"\det", "min": r"\min", "max": r"\max", "gcd": r"\gcd",
}

# Calls made for their effect rather than their value: shown as source.
_PROCEDURES = frozenset({
    "pp", "pv", "pn", "print", "display", "plot", "bode", "hardcopy",
    "print_view", "protect", "unprotect", "protect_all", "protect_constants",
    "protect_si_units", "clear_protections", "set_syntax_mode",
    "set_decimal_literals", "register_radix", "refresh_display",
    "launch_palette", "help", "input", "exec", "eval",
})

_MODULE_NAMES = frozenset({"np", "math", "sym", "si", "eng"})

_CMP_TEX = {
    ast.Eq: r"\stackrel{?}{=}", ast.NotEq: r"\ne", ast.Lt: "<", ast.LtE: r"\le",
    ast.Gt: ">", ast.GtE: r"\ge", ast.In: r"\in", ast.NotIn: r"\notin",
    ast.Is: r"\equiv", ast.IsNot: r"\not\equiv",
}
_AUG_TEX = {ast.Add: "+", ast.Sub: "-", ast.Mult: r"\cdot", ast.Div: "/",
            ast.Mod: r"\bmod"}
_BIT_TEX = {
    ast.BitOr: (r" \cup ", P_BITOR), ast.BitAnd: (r" \cap ", P_BITAND),
    ast.BitXor: (r" \oplus ", P_BITXOR),
    ast.LShift: (r" \ll ", P_SHIFT), ast.RShift: (r" \gg ", P_SHIFT),
}


class _R:
    """A rendered fragment: its LaTeX, how tightly it binds, and whether
    it begins with a digit (which decides ``3 b`` versus ``a · 3``)."""
    __slots__ = ("tex", "prec", "digit")

    def __init__(self, tex, prec=P_ATOM, digit=False):
        self.tex, self.prec, self.digit = tex, prec, digit


def _paren(tex: str) -> str:
    return r"\left(" + tex + r"\right)"


# Between the items of an unbracketed list at the top of a statement
# (``a, b, c := …``): a long one may be broken across lines there.
_LIST_SEP = ",\\allowbreak\\ "


def _call_name(node):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _str_const(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class Renderer:
    def __init__(self, source: str, names: NameInfo | None = None):
        self.source = source
        self.names = names or NameInfo()
        self._script = 0        # inside a sub/superscript: no built-up fractions
        self._frac = 0          # fraction nesting depth

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _toolkit(self, node, *names) -> bool:
        """``node`` calls one of the toolkit helpers ``names`` — and the
        worksheet has not replaced that helper with a function of its own."""
        n = _call_name(node)
        return n in names and not self.names.is_user(n)

    def _seg(self, node) -> str:
        return (ast.get_source_segment(self.source, node) or "").strip()

    def p(self, node, prec, *, strict=False) -> str:
        """Render ``node``, parenthesised if it binds looser than ``prec``."""
        r = self.e(node)
        if r.prec < prec or (strict and r.prec == prec):
            return _paren(r.tex)
        return r.tex

    def _in_script(self, node, prec=P_LAMBDA) -> str:
        self._script += 1
        try:
            return self.p(node, prec)
        finally:
            self._script -= 1

    def _number_node(self, node):
        """The literal inside ``_S(lit, sf)`` / ``_R('lit')``, or a bare
        numeric constant; ``None`` for anything else."""
        if self._toolkit(node, "_S", "_R") and node.args:
            node = node.args[0]
            if self._toolkit(node, "_R") and node.args:     # _S(_R('0.1'), 1)
                node = node.args[0]
            if _str_const(node) is not None:
                return node
        if (isinstance(node, ast.Constant) and not isinstance(node.value, bool)
                and isinstance(node.value, (int, float, complex))):
            return node
        return None

    def _number_value(self, node):
        inner = self._number_node(node)
        if inner is None:
            return None
        if isinstance(inner.value, str):
            try:
                return float(inner.value)
            except ValueError:
                return None
        return inner.value

    def _number_tex(self, inner) -> str:
        """A numeric literal exactly as written — ``10.`` keeps its dot,
        because the dot is what makes it two significant figures."""
        text = inner.value if isinstance(inner.value, str) else (
            self._seg(inner) or repr(inner.value))
        low = text.lower()
        for prefix, base in (("0x", 16), ("0b", 2), ("0o", 8)):
            if low.startswith(prefix):
                return r"\mathrm{" + text[2:].replace("_", "") + "}_{" + str(base) + "}"
        text = text.replace("_", r"\,")
        suffix = ""
        if low.endswith("j"):
            text, suffix = text[:-1], r"\,\mathrm{j}"
        m = re.fullmatch(r"(.+?)[eE]([-+]?)0*(\d+)", text)
        if m:
            mant, sign, exp = m.groups()
            text = rf"{mant} \cdot 10^{{{'-' if sign == '-' else ''}{exp}}}"
        return text + suffix

    # ------------------------------------------------------------------
    # units and quantities
    # ------------------------------------------------------------------
    def unit(self, node, *, denom=False):
        """LaTeX for a pure unit expression, else ``None``."""
        if self._toolkit(node, "_wu") and len(node.args) >= 2:
            # The transformer tags by spelling; a sheet may have given
            # that spelling a meaning of its own (``fs := [...]``), and
            # then ``2 fs`` multiplies by the sheet's value.
            label = _str_const(node.args[1])
            if label is None or not all(self.names.is_unit(n.id) for n in ast.walk(node.args[0])
                                        if isinstance(n, ast.Name)):
                return None
            tex = _unit_tex(label)
            return _paren(tex) if denom and ("/" in label or "·" in label) else tex
        if isinstance(node, ast.Name):
            if self.names.is_unit(node.id):
                return _unit_tex(self.names.unit_label(node.id))
            return None
        if not isinstance(node, ast.BinOp):
            return None
        if isinstance(node.op, ast.Mult):
            a, b = self.unit(node.left), self.unit(node.right)
            if a is None or b is None:
                return None
            tex = a + r" \cdot " + b
            return _paren(tex) if denom else tex
        if isinstance(node.op, ast.Div):
            a, b = self.unit(node.left), self.unit(node.right, denom=True)
            if a is None or b is None:
                return None
            tex = a + "/" + b
            return _paren(tex) if denom else tex
        if isinstance(node.op, ast.Pow):
            a = self.unit(node.left)
            if a is None:
                return None
            exp = node.right
            neg = isinstance(exp, ast.UnaryOp) and isinstance(exp.op, ast.USub)
            inner = self._number_node(exp.operand if neg else exp)
            if inner is None:
                return None
            return a + "^{" + ("-" if neg else "") + self._number_tex(inner) + "}"
        return None

    def quantity(self, node):
        """``(magnitude node, unit LaTeX)`` when ``node`` is a magnitude
        times a unit expression — ``5 m/s`` reaches us as ``(5·m)/s`` —
        else ``None``."""
        if not isinstance(node, ast.BinOp):
            return None
        if isinstance(node.op, ast.Mult):
            ru = self.unit(node.right)
            if ru is None:
                return None
            q = self.quantity(node.left)
            if q is not None:
                return q[0], q[1] + r" \cdot " + ru
            if self.unit(node.left) is None:
                return node.left, ru
            return None
        if isinstance(node.op, ast.Div):
            q = self.quantity(node.left)
            if q is None:
                return None
            if self._toolkit(node.right, "_per") and len(node.right.args) == 1:
                return q[0], q[1] + "/" + self.p(node.right.args[0], P_QTY)   # 5 L/100 km
            ru = self.unit(node.right, denom=True)
            if ru is not None:
                return q[0], q[1] + "/" + ru
        return None

    # ------------------------------------------------------------------
    # expressions
    # ------------------------------------------------------------------
    def e(self, node) -> _R:
        method = getattr(self, "e_" + type(node).__name__, None)
        if method is None:
            raise Unrenderable(type(node).__name__)
        return method(node)

    def e_Constant(self, node):
        v = node.value
        if isinstance(v, bool) or v is None:
            return _R(r"\mathrm{" + str(v) + "}")
        if isinstance(v, (int, float, complex)):
            return _R(self._number_tex(node), P_ATOM, digit=True)
        if isinstance(v, str):
            if "\n" in v or len(v) > 60:
                raise Unrenderable("long string")
            return _R(r"\text{“" + _text(v) + "”}")
        if v is Ellipsis:
            return _R(r"\dots")
        raise Unrenderable("constant")

    def e_Name(self, node):
        name = node.id
        if isinstance(node.ctx, ast.Store):
            # Being assigned: from here on it is the sheet's own name,
            # whatever unit or constant it shadows (``fs := […]``).
            return _R(name_to_latex(name))
        if self.names.is_unit(name):
            return _R(_unit_tex(self.names.unit_label(name)), P_QTY)
        if name in _CONSTANT_TEX and not self.names.is_user(name):
            return _R(_CONSTANT_TEX[name])
        return _R(name_to_latex(name))

    def e_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            # Read as mathematics, ¬ takes only what stands right after it:
            # tighter than Python's ``not``, which spans a whole comparison.
            inner = node.operand
            again = isinstance(inner, ast.UnaryOp) and isinstance(inner.op, ast.Not)
            return _R(r"\lnot " + self.p(inner, P_NOT if again else P_POST), P_NOT)
        sign = {ast.USub: "-", ast.UAdd: "+", ast.Invert: r"\mathord{\sim}"}[type(node.op)]
        return _R(sign + self.p(node.operand, P_NEG), P_NEG)

    def e_BoolOp(self, node):
        op, prec = (r" \land ", P_AND) if isinstance(node.op, ast.And) else (r" \lor ", P_OR)
        return _R(op.join(self.p(v, prec, strict=True) for v in node.values), prec)

    def e_Compare(self, node):
        parts = [self.p(node.left, P_BITOR)]
        for op, right in zip(node.ops, node.comparators):
            parts += [_CMP_TEX[type(op)], self.p(right, P_BITOR)]
        return _R(" ".join(parts), P_CMP)

    def e_IfExp(self, node):
        if self._script:
            raise Unrenderable("conditional in a script")
        rows, cur = [], node
        while isinstance(cur, ast.IfExp):
            rows.append(self.p(cur.body, P_COND) + r" & \text{if } " + self.p(cur.test, P_COND))
            cur = cur.orelse
        rows.append(self.p(cur, P_COND) + r" & \text{otherwise}")
        return _R(r"\begin{cases}" + r" \\ ".join(rows) + r"\end{cases}", P_COND)

    def e_Lambda(self, node):
        return _R(self._params(node.args, bare_single=True) + r" \mapsto "
                  + self.p(node.body, P_LAMBDA), P_LAMBDA)

    def _params(self, args, bare_single=False) -> str:
        if (args.vararg or args.kwarg or args.kwonlyargs or args.posonlyargs
                or args.defaults or args.kw_defaults):
            raise Unrenderable("signature")
        names = [name_to_latex(a.arg) for a in args.args]
        if bare_single and len(names) == 1:
            return names[0]
        return _paren(",\\ ".join(names))

    # ---- containers ---------------------------------------------------
    def _items(self, elts) -> str:
        return ",\\ ".join(self.p(x, P_LAMBDA) for x in elts)

    @staticmethod
    def _matrix_rows(node):
        """Rows of a rectangular list-of-lists literal, else ``None``."""
        if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
            return None
        rows = []
        for row in node.elts:
            if not isinstance(row, (ast.List, ast.Tuple)) or not row.elts:
                return None
            rows.append(row.elts)
        return rows if len({len(r) for r in rows}) == 1 else None

    def _bmatrix(self, rows) -> _R:
        if self._script:
            raise Unrenderable("matrix in a script")
        body = r" \\ ".join(" & ".join(self.p(c, P_LAMBDA) for c in row) for row in rows)
        return _R(r"\begin{bmatrix}" + body + r"\end{bmatrix}")

    def e_List(self, node):
        rows = self._matrix_rows(node)
        if rows is not None:
            return self._bmatrix(rows)
        if len(node.elts) == 1 and isinstance(node.elts[0], ast.Starred):
            inner = node.elts[0].value                  # ['C8'..'C13']
            if self._toolkit(inner, "_str_range"):
                return _R(r"\left[" + self.e(inner).tex + r"\right]")
        return _R(r"\left[" + self._items(node.elts) + r"\right]")

    def e_Tuple(self, node):
        return _R(_paren(self._items(node.elts) + ("," if len(node.elts) == 1 else "")))

    def e_Set(self, node):
        return _R(r"\left\{" + self._items(node.elts) + r"\right\}")

    def e_Starred(self, node):
        return _R(r"{*}" + self.p(node.value, P_ATOM), P_NEG)

    def _comprehension(self, node, left, right):
        conds = []
        for gen in node.generators:
            if gen.is_async:
                raise Unrenderable("async comprehension")
            target = self.target(gen.target)
            it = gen.iter
            if self._toolkit(it, "_range_ineq") and len(it.args) == 4:
                lo, hi, lc, rc = it.args                 # for 1 ≤ k ≤ 5
                lop = r"\le" if getattr(lc, "value", True) else "<"
                rop = r"\le" if getattr(rc, "value", True) else "<"
                conds.append(f"{self.p(lo, P_BITOR)} {lop} {target} {rop} {self.p(hi, P_BITOR)}")
            else:
                conds.append(target + r" \in " + self.p(it, P_BITOR))
            conds += [self.p(c, P_LAMBDA) for c in gen.ifs]
        return _R(left + self.p(node.elt, P_LAMBDA) + r" \;\middle|\; "
                  + ",\\ ".join(conds) + right)

    def e_ListComp(self, node):
        return self._comprehension(node, r"\left[", r"\right]")

    def e_SetComp(self, node):
        return self._comprehension(node, r"\left\{", r"\right\}")

    def e_GeneratorExp(self, node):
        return self._comprehension(node, r"\left(", r"\right)")

    # ---- access -------------------------------------------------------
    def _indices(self, nodes) -> str:
        self._script += 1
        try:
            return ",".join(self._slice(n) for n in nodes)
        finally:
            self._script -= 1

    def _slice(self, node) -> str:
        if isinstance(node, ast.Slice):
            if node.step is not None:
                raise Unrenderable("stepped slice")
            lo = self.p(node.lower, P_LAMBDA) if node.lower is not None else ""
            hi = self.p(node.upper, P_LAMBDA) if node.upper is not None else ""
            return lo + "{:}" + hi
        return self.p(node, P_LAMBDA)

    def e_Subscript(self, node):
        idx = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if any(_str_const(i) is not None for i in idx):          # d['key']
            return _R(self.p(node.value, P_ATOM) + r"\left[" + self._items(idx) + r"\right]")
        return _R(self.p(node.value, P_ATOM) + "_{" + self._indices(idx) + "}")

    def e_Attribute(self, node):
        owner = node.value
        if node.attr == "T" and not (isinstance(owner, ast.Name) and owner.id in _MODULE_NAMES):
            return _R(self.p(owner, P_ATOM) + r"^{\mathsf{T}}", P_POW)      # Mᵀ
        return _R(self.p(owner, P_ATOM) + r".\mathrm{" + _text(node.attr) + "}")

    # ---- arithmetic ---------------------------------------------------
    def _coefficient(self, node) -> bool:
        """A numeric factor that conventionally sits against what it
        multiplies: ``3 b``, ``½ ρ``, ``2π f``."""
        if self._number_node(node) is not None:
            return True
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Div):
                return (self._number_node(node.left) is not None
                        and self._number_node(node.right) is not None)
            if isinstance(node.op, ast.Mult):
                r = node.right
                return (self._coefficient(node.left) and isinstance(r, ast.Name)
                        and r.id in ("π", "pi") and not self.names.is_user(r.id))
        return False

    def e_BinOp(self, node):
        op = type(node.op)
        if op in (ast.Mult, ast.Div):
            ut = self.unit(node)
            if ut is not None:                           # a bare unit expression
                return _R(ut, P_QTY)
            q = self.quantity(node)
            if q is not None:
                mag = self.e(q[0])
                tex = mag.tex if mag.prec >= P_NEG else _paren(mag.tex)
                return _R(tex + r"\ " + q[1], P_QTY, digit=mag.digit)
        if op is ast.Mult:
            return self._product(node)
        if op is ast.Div:
            return self._quotient(node)
        if op is ast.Add:
            return _R(self.p(node.left, P_ADD) + " + " + self._addend(node.right), P_ADD)
        if op is ast.Sub:
            return _R(self.p(node.left, P_ADD) + " - " + self._addend(node.right, strict=True),
                      P_ADD)
        if op is ast.Pow:
            return self._power(node)
        if op is ast.Mod:
            return _R(self.p(node.left, P_MUL) + r" \bmod " + self.p(node.right, P_NEG), P_MUL)
        if op is ast.FloorDiv:
            return _R(r"\left\lfloor " + self.p(node.left, P_MUL) + "/"
                      + self.p(node.right, P_QTY) + r" \right\rfloor")
        if op is ast.MatMult:
            return _R(self.p(node.left, P_MUL) + r" \cdot " + self.p(node.right, P_NEG), P_MUL)
        sym, prec = _BIT_TEX[op]
        return _R(self.p(node.left, prec) + sym + self.p(node.right, prec, strict=True), prec)

    def _addend(self, node, strict=False) -> str:
        r = self.e(node)
        if r.prec < P_ADD or (strict and r.prec == P_ADD) or r.prec == P_NEG:
            return _paren(r.tex)                         # a − (b − c),  a + (−b)
        return r.tex

    def _product(self, node) -> _R:
        left, right = self.e(node.left), self.e(node.right)
        ltex = left.tex if left.prec >= P_MUL else _paren(left.tex)
        # A product on the right needs no brackets (· is associative);
        # anything else that binds no tighter than · does, as does a
        # leading sign: a · (−b).
        inner_product = (isinstance(node.right, ast.BinOp)
                         and isinstance(node.right.op, ast.Mult))
        if right.prec > P_NEG or (right.prec == P_MUL and inner_product):
            rtex = right.tex
        else:
            rtex = _paren(right.tex)
        juxtapose = self._coefficient(node.left) and not right.digit
        sep = r"\," if juxtapose else r" \cdot "
        return _R(ltex + sep + rtex, P_MUL, digit=left.digit and left.prec >= P_MUL)

    def _quotient(self, node) -> _R:
        deg = self._degrees(node)
        if deg is not None:
            return deg
        if self._script or self._frac >= 2:
            # Small type and deep nesting: a solidus stays legible.
            return _R(self.p(node.left, P_MUL) + "/" + self.p(node.right, P_QTY), P_MUL)
        self._frac += 1
        try:
            num, den = self.e(node.left), self.e(node.right)
        finally:
            self._frac -= 1
        return _R(r"\frac{" + num.tex + "}{" + den.tex + "}", P_QTY)

    def _degrees(self, node):
        """``x°`` reaches us as ``x·π/180``."""
        left = node.left
        if not (self._number_value(node.right) == 180
                and isinstance(left, ast.BinOp) and isinstance(left.op, ast.Mult)
                and isinstance(left.right, ast.Name) and left.right.id == "π"
                and not self.names.is_user("π")):
            return None
        mag = self.e(left.left)
        tex = mag.tex if mag.prec >= P_POST else _paren(mag.tex)
        return _R(tex + r"^{\circ}", P_QTY, digit=mag.digit)

    def _power(self, node) -> _R:
        exp = node.right
        if (isinstance(exp, ast.BinOp) and isinstance(exp.op, ast.Div)
                and self._number_value(exp.left) == 1
                and isinstance(self._number_value(exp.right), int)
                and not self._script):
            n = self._number_value(exp.right)            # x**(1/n) is a root
            body = self.e(node.left).tex
            return _R((r"\sqrt{" if n == 2 else rf"\sqrt[{n}]{{") + body + "}")
        base = self.e(node.left)
        btex = base.tex if base.prec == P_ATOM else _paren(base.tex)
        return _R("{" + btex + "}^{" + self._in_script(exp) + "}", P_POW,
                  digit=base.digit and base.prec == P_ATOM)

    # ---- calls --------------------------------------------------------
    def e_Call(self, node):
        name = _call_name(node)
        if name is not None and not self.names.is_user(name) and not node.keywords:
            handler = getattr(self, "c_" + name, None)
            if handler is not None:
                out = handler(node)
                if out is not None:
                    return out
            if name in _FUNC_TEX and node.args:
                if name == "log" and len(node.args) == 2:
                    return _R(r"\log_{" + self._in_script(node.args[1]) + "}"
                              + _paren(self.p(node.args[0], P_LAMBDA)))
                return _R(_FUNC_TEX[name] + _paren(self._items(node.args)))
        if isinstance(node.func, ast.Name):
            ftex = name_to_latex(node.func.id, function=True)
        elif isinstance(node.func, ast.Attribute):
            ftex = (self.p(node.func.value, P_ATOM) + r".\operatorname{"
                    + _text(node.func.attr) + "}")
        else:
            ftex = self.p(node.func, P_ATOM)
        args = [self.p(a, P_LAMBDA) for a in node.args]
        for kw in node.keywords:
            if kw.arg is None:
                args.append(r"{*}{*}" + self.p(kw.value, P_ATOM))
            else:
                args.append(r"\mathrm{" + _text(kw.arg) + "}{=}" + self.p(kw.value, P_LAMBDA))
        return _R(ftex + _paren(",\\ ".join(args)))

    def _one(self, node):
        return node.args[0] if len(node.args) == 1 else None

    def c__S(self, node):
        inner = self._number_node(node)
        if inner is not None:
            return _R(self._number_tex(inner), P_ATOM, digit=True)
        return self.e(node.args[0]) if node.args else None

    c__R = c__S

    def c__wu(self, node):
        ut = self.unit(node)
        if ut is not None:
            return _R(ut, P_QTY)
        return self.e(node.args[0]) if node.args else None      # not a unit after all

    def c__per(self, node):
        arg = self._one(node)
        return self.e(arg) if arg is not None else None

    def c_sqrt(self, node):
        arg = self._one(node)
        return _R(r"\sqrt{" + self.e(arg).tex + "}") if arg is not None else None

    def c_cbrt(self, node):
        arg = self._one(node)
        return _R(r"\sqrt[3]{" + self.e(arg).tex + "}") if arg is not None else None

    def c_parallel(self, node):
        if len(node.args) != 2:
            return None
        a, b = node.args
        left = self.e(a)
        # ‖ is associative, so a left-nested chain needs no brackets.
        chain = left.prec == P_PAR and self._toolkit(a, "parallel")
        ltex = left.tex if left.prec >= P_QTY or chain else _paren(left.tex)
        return _R(ltex + r" \parallel " + self.p(b, P_QTY), P_PAR, digit=left.digit)

    def c_plusminus(self, node):
        if len(node.args) != 2:
            return None
        return _R(self.p(node.args[0], P_MUL) + r" \pm " + self.p(node.args[1], P_MUL), P_ADD)

    def _postfix(self, node, mark):
        arg = self._one(node)
        if arg is None:
            return None
        r = self.e(arg)
        return _R((r.tex if r.prec >= P_POST else _paren(r.tex)) + mark, P_POST, digit=r.digit)

    def c_percent(self, node):
        return self._postfix(node, r"\%")

    def c_permille(self, node):
        return self._postfix(node, r"\text{‰}")

    def c_fact(self, node):
        return self._postfix(node, "!")

    def _fence(self, node, left, right):
        arg = self._one(node)
        return _R(left + self.e(arg).tex + right) if arg is not None else None

    def c__abs_or_size(self, node):
        return self._fence(node, r"\left|", r"\right|")

    c_abs = c__abs_or_size

    def c_norm(self, node):
        return self._fence(node, r"\left\lVert ", r" \right\rVert")

    def c_floor(self, node):
        return self._fence(node, r"\left\lfloor ", r" \right\rfloor")

    def c_ceil(self, node):
        return self._fence(node, r"\left\lceil ", r" \right\rceil")

    def c_exp(self, node):
        arg = self._one(node)
        return _R(r"e^{" + self._in_script(arg) + "}", P_POW) if arg is not None else None

    def c_phasor(self, node):
        if len(node.args) != 2:
            return None
        return _R(self.p(node.args[0], P_QTY) + r" \angle " + self.p(node.args[1], P_QTY), P_PAR)

    def _temperature(self, node, label):
        """``22 °C`` reaches us as ``from_degC(22)``."""
        arg = self._one(node)
        if arg is None:
            return None
        r = self.e(arg)
        tex = r.tex if r.prec >= P_NEG else _paren(r.tex)
        return _R(tex + r"\ " + _unit_tex(label), P_QTY, digit=r.digit)

    def c_from_degC(self, node):
        return self._temperature(node, "°C")

    def c_from_degF(self, node):
        return self._temperature(node, "°F")

    def c_from_degR(self, node):
        return self._temperature(node, "°R")

    def c__idx(self, node):
        if len(node.args) < 2:
            return None
        return _R(self.p(node.args[0], P_ATOM) + "_{" + self._indices(node.args[1:]) + "}")

    def c__bracket_get(self, node):
        if len(node.args) != 2:
            return None
        return _R(self.p(node.args[0], P_ATOM) + r"\left[" + self.p(node.args[1], P_LAMBDA)
                  + r"\right]")

    def _dots(self, node, dots):
        if not 2 <= len(node.args) <= 3:
            return None
        return _R(dots.join(self.p(a, P_ADD) for a in node.args), P_CMP)

    def c__range_inc(self, node):
        return self._dots(node, r" \mathbin{..} ")

    c__str_range = c__range_inc

    def c__interval(self, node):
        return self._dots(node, r" \mathbin{\text{‥}} ") if len(node.args) == 2 else None

    def c__CommaArray(self, node):
        inner = self._one(node)
        if isinstance(inner, ast.List):
            return self.e(inner)
        if self._toolkit(inner, "_range_inc"):
            return _R(r"\left[" + self.e(inner).tex + r"\right]")
        return None

    def c_in_units(self, node):
        if len(node.args) != 3:
            return None
        return _R(self.p(node.args[0], P_ADD) + r" \mathbin{\blacktriangleright} "
                  + self._target_tex(node), P_CMP)

    def _target_tex(self, node) -> str:
        """The display target of an ``in_units(value, target, label)``."""
        target, text = node.args[1], _str_const(node.args[2])
        if text is None:
            return self.p(target, P_ATOM)
        if _str_const(target) is not None:
            return r"\mathrm{" + _text(text) + "}"            # ▸ hex, ▸ roman
        if self._number_node(target) is not None:
            return self.e(target).tex                          # ▸ 3  (sig. figures)
        if text not in _TARGET_LABELS:
            ut = self.unit(target)
            if ut is not None:
                return ut
        return _unit_tex(_TARGET_LABELS.get(text, text))

    def _relation(self, node, sym):
        if len(node.args) != 2:
            return None
        return _R(self.p(node.args[0], P_BITOR) + sym + self.p(node.args[1], P_BITOR), P_CMP)

    def c_approx(self, node):
        return self._relation(node, r" \approx ")

    def c__eq(self, node):
        return self._relation(node, " = ")

    def c_Eq(self, node):
        return self._relation(node, r" \equiv ")

    def c_iso(self, node):
        text = _str_const(self._one(node))
        return _R(r"\text{" + _text(text) + "}") if text is not None else None

    def c_from_roman(self, node):
        text = _str_const(self._one(node))
        return _R(r"\mathrm{" + _text(text) + "}") if text is not None else None

    def c_set(self, node):
        return _R(r"\varnothing") if not node.args else None

    def c_Matrix(self, node):
        arg = self._one(node)
        rows = self._matrix_rows(arg)
        if rows is not None:
            return self._bmatrix(rows)
        if isinstance(arg, (ast.List, ast.Tuple)) and arg.elts:      # column vector
            return self._bmatrix([[x] for x in arg.elts])
        return None

    c__as_matrix = c_Matrix

    # ------------------------------------------------------------------
    # statements
    # ------------------------------------------------------------------
    def is_literal(self, node) -> bool:
        """A value written out in full — showing ``= value`` after it
        would only repeat the definition."""
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return self.is_literal(node.operand)
        if self._number_node(node) is not None or isinstance(node, ast.Constant):
            return True
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(self.is_literal(e) for e in node.elts)       # [1, 2, 3], {2, 3, 5}
        if self._toolkit(node, "_CommaArray", "set") and len(node.args) <= 1:
            return all(isinstance(a, ast.List) and self.is_literal(a) for a in node.args)
        q = self.quantity(node)
        if q is not None:
            return self.is_literal(q[0])
        if self._toolkit(node, "from_degC", "from_degF", "from_degR", "_per"):
            return len(node.args) == 1 and self.is_literal(node.args[0])
        if self._toolkit(node, "iso") and len(node.args) == 1:
            # A date or a duration says what it is.  The clock's words do
            # not, and a Roman numeral is worth reading back as a number.
            text = _str_const(node.args[0])
            return text is not None and text.strip().lower() not in CLOCK_WORDS
        if self._toolkit(node, "plusminus", "_interval") and len(node.args) == 2:
            return all(self.is_literal(a) or self._toolkit(a, "percent", "permille")
                       for a in node.args)
        return False

    def target(self, node) -> str:
        if isinstance(node, ast.Tuple):
            return _LIST_SEP.join(self.target(t) for t in node.elts)
        if isinstance(node, ast.Starred):
            return r"{*}" + self.target(node.value)
        return self.p(node, P_ATOM)

    def statement(self, node) -> dict:
        if isinstance(node, ast.Assign):
            return self._assign(node)
        if isinstance(node, ast.AugAssign):
            op = _AUG_TEX.get(type(node.op))
            if op is None:
                raise Unrenderable("augmented assignment")
            return {"latex": self.target(node.target) + r" \mathrel{{" + op + r"}{=}} "
                    + self.p(node.value, P_LAMBDA), "kind": "assign", "literal": False}
        if isinstance(node, ast.Expr):
            return self._expression(node.value)
        raise Unrenderable(type(node).__name__)

    def _assign(self, node) -> dict:
        value = node.value
        decl = self._symbol_declaration(node)
        if decl is not None:
            return decl
        if (isinstance(value, ast.Lambda) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            # f := x → x² + 1 is a function definition: f(x) := x² + 1
            lhs = name_to_latex(node.targets[0].id, function=True) + self._params(value.args)
            return {"latex": lhs + r" \coloneqq " + self.p(value.body, P_LAMBDA),
                    "kind": "def", "literal": True}
        lhs = r" \coloneqq ".join(self.target(t) for t in node.targets)
        if isinstance(value, ast.Tuple) and value.elts:
            rhs = _LIST_SEP.join(self.p(v, P_LAMBDA) for v in value.elts)   # a, b := 1, 2
            literal = all(self.is_literal(v) for v in value.elts)
        else:
            rhs = self.p(value, P_LAMBDA)
            literal = self.is_literal(value)
        return {"latex": lhs + r" \coloneqq " + rhs, "kind": "assign", "literal": literal}

    def _symbol_declaration(self, node):
        """``symbols: x, y`` — transformed to ``x, y = sym.symbols('x y')``."""
        v = node.value
        if not (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                and v.func.attr == "symbols" and isinstance(v.func.value, ast.Name)
                and v.func.value.id == "sym" and len(node.targets) == 1):
            return None
        flags = [kw.arg for kw in v.keywords
                 if kw.arg and isinstance(kw.value, ast.Constant) and kw.value.value is True]
        return {"latex": r"\text{" + _text(" ".join(flags + ["symbols"])) + r": }"
                + self.target(node.targets[0]), "kind": "decl", "literal": True}

    def _expression(self, value) -> dict:
        if not getattr(value, "keywords", None):
            if self._toolkit(value, "_idx_set") and len(value.args) >= 3:
                obj, val, *idx = value.args                     # R₁ := 5 Ω
                lhs = self.p(obj, P_ATOM) + "_{" + self._indices(idx) + "}"
                return {"latex": lhs + r" \coloneqq " + self.p(val, P_LAMBDA),
                        "kind": "assign", "literal": self.is_literal(val)}
            if self._toolkit(value, "_bracket_set") and len(value.args) == 3:
                obj, val, key = value.args
                lhs = self.p(obj, P_ATOM) + r"\left[" + self.p(key, P_LAMBDA) + r"\right]"
                return {"latex": lhs + r" \coloneqq " + self.p(val, P_LAMBDA),
                        "kind": "assign", "literal": self.is_literal(val)}
            if self._toolkit(value, "in_units") and len(value.args) == 3:
                # ``x ▸ cm`` is x *shown in* cm: the unit belongs to the
                # answer, so the question is just ``x``.
                return {"latex": self.p(value.args[0], P_LAMBDA), "kind": "expr",
                        "literal": False, "target": self._target_tex(value)}
        name = _call_name(value)
        if name in _PROCEDURES and not self.names.is_user(name):
            raise Unrenderable("procedure call")
        if isinstance(value, ast.Call) and name not in ("iso", "from_roman") and any(
                isinstance(a, ast.JoinedStr) or _str_const(a) is not None for a in value.args):
            raise Unrenderable("call with text arguments")
        return {"latex": self.e(value).tex, "kind": "expr", "literal": self.is_literal(value)}


def render_statement(node, source: str, names: NameInfo | None = None):
    """Typeset one top-level statement of transformed DSL code.

    ``source`` is the transformed text ``node`` was parsed from (numeric
    literals are copied from it verbatim).  Returns a dict with
    ``latex``, ``kind`` (``assign`` / ``expr`` / ``def`` / ``decl``) and
    ``literal`` — or ``None`` when the statement has no built-up form.
    """
    try:
        return Renderer(source, names).statement(node)
    except Unrenderable:
        return None
    except (KeyError, AttributeError, TypeError, IndexError, ValueError, RecursionError):
        # An AST shape the renderer did not anticipate is a display
        # matter only: show source text rather than fail the region.
        return None
