"""Kernel side of the worksheet: run regions statement by statement.

A worksheet is a list of regions in reading order.  Each math region is
DSL source; this module transforms it exactly as a notebook cell would
be, then executes the resulting statements one at a time so that every
definition can show its own value beside it.

A calculation pass always produces what a clean top-to-bottom run would
produce: the namespace is rebuilt from the post-import baseline each
time.  Statements whose inputs are unchanged are not executed again —
their recorded effect is replayed — which is what makes editing a large
sheet feel immediate (see :class:`_Memo` for when that is allowed).

Results travel to the front end as ``display_data`` messages carrying
the :data:`MIME` type; nothing here knows about the web server.
"""
from __future__ import annotations

import ast
import base64
import builtins
import hashlib
import json
import linecache
import sys
import time
import traceback
import types

from .. import circuit_dsl as _dsl
from .. import runtime as _runtime
from .. import sigfig as _sigfig
from . import mathrender
from .fingerprint import MISSING, Fingerprinter
from .protocol import MIME, PREAMBLE

__all__ = ["Engine", "install"]

# What a value may be shown as.  Anything else a formatter offers
# (widgets, javascript) has no place on a calculation sheet.
_SHOW_MIMES = ("text/plain", "text/latex", "text/html", "text/markdown",
               "image/png", "image/jpeg", "image/svg+xml")
_MAX_TEXT = 20_000

# A statement mentioning one of these reads the namespace wholesale or
# depends on something outside the sheet (the clock, a random stream, a
# file), so its result is never reused — it runs on every pass.
_VOLATILE_NAMES = frozenset({
    "globals", "locals", "vars", "dir", "eval", "exec", "get_ipython",
    "__import__", "input", "open",
    "random", "rand", "randn", "randint", "default_rng",
    "perf_counter", "monotonic", "environ",
})
# The same, for names that are too ordinary to act on by themselves
# (``time`` and ``load`` are fine variables) but telling as attributes:
# ``datetime.now()``, ``np.random.seed(1)``, ``np.loadtxt(…)``, ``f.read()``.
_VOLATILE_ATTRIBUTES = _VOLATILE_NAMES | frozenset({
    "seed", "now", "today", "utcnow", "time",
    "load", "loadtxt", "genfromtxt", "fromfile", "read", "readlines",
    "read_text", "read_bytes", "read_csv", "read_excel",
})
_CLOCK_LITERALS = mathrender.CLOCK_WORDS          # ``"now"ₜᵢₘₑ`` and friends

# Calls into these packages are computations: same arguments, same answer.
# A call into any other module (pyplot, pandas I/O, os …) may touch state
# the sheet cannot see, so the statement making it is not reused either.
_PURE_PACKAGES = frozenset({
    "builtins", "utils", "forallpeople", "numpy", "math", "cmath", "sympy",
    "mpmath", "scipy", "statistics", "fractions", "decimal", "itertools",
    "functools", "operator", "collections", "string", "re", "numbers",
})


def _pure_module(name) -> bool:
    return (name or "").split(".")[0] in _PURE_PACKAGES


# What a statement shows when the user stopped the calculation in it.
_STOPPED = {"ename": "Stopped", "evalue": "the calculation was interrupted here"}


# ---------------------------------------------------------------------------
# DSL session state
# ---------------------------------------------------------------------------
# The transformer keeps a little state between cells: the syntax mode,
# decimal-literal mode, symbols declared with subscripts, protected names
# and registered radix formats.  A pass must start from the state a fresh
# kernel would have, and code that depends on it must notice a change.

def _state_get():
    return (_runtime._syntax_mode, _sigfig._DECIMAL_LITERAL_MODE,
            dict(_dsl._DECLARED_SUBSCRIPT_SYMBOLS), set(_dsl.PROTECTED_NAMES),
            dict(_sigfig._RADIX_FORMATTERS))


def _state_set(state):
    mode, decimal_mode, declared, protected, radix = state
    _runtime._syntax_mode = mode
    _sigfig._DECIMAL_LITERAL_MODE = decimal_mode
    for live, saved in ((_dsl._DECLARED_SUBSCRIPT_SYMBOLS, declared),
                        (_sigfig._RADIX_FORMATTERS, radix)):
        live.clear()
        live.update(saved)
    _dsl.PROTECTED_NAMES.clear()
    _dsl.PROTECTED_NAMES.update(protected)


def _state_token():
    return (_runtime._syntax_mode, _sigfig._DECIMAL_LITERAL_MODE,
            tuple(sorted(_dsl._DECLARED_SUBSCRIPT_SYMBOLS.items())),
            frozenset(_dsl.PROTECTED_NAMES),
            tuple(sorted((k, id(v)) for k, v in _sigfig._RADIX_FORMATTERS.items())))


# ---------------------------------------------------------------------------
# Output capture
# ---------------------------------------------------------------------------

def _jsonable(value):
    """Reduce a MIME bundle to what JSON can carry."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


class _Capture:
    """Collect what a statement prints and displays, in order.

    IPython's own ``capture_output`` keeps text and rich output in
    separate piles; a loop that prints a caption and then shows a value
    needs them interleaved as they happened.
    """

    def __init__(self, shell):
        self.shell = shell
        self.events = []

    def write(self, name, text):
        if not text:
            return
        last = self.events[-1] if self.events else None
        if last and last["type"] == "stream" and last["name"] == name:
            last["text"] += text
        else:
            self.events.append({"type": "stream", "name": name, "text": text})

    def display(self, data, metadata):
        shown = {k: v for k, v in (data or {}).items() if k in _SHOW_MIMES}
        if shown:
            self.events.append({"type": "display", "data": _jsonable(shown),
                                "metadata": _jsonable(metadata or {})})

    def __enter__(self):
        from IPython.core.displaypub import CapturingDisplayPublisher

        capture = self

        class _Stream:
            encoding = "utf-8"

            def __init__(self, name):
                self.name = name

            def write(self, text):
                text = text if isinstance(text, str) else str(text)
                capture.write(self.name, text)
                return len(text)

            def writelines(self, lines):
                for line in lines:
                    self.write(line)

            def flush(self):
                pass

            def isatty(self):
                return False

        class _Publisher(CapturingDisplayPublisher):
            def publish(self, data, metadata=None, source=None, *, transient=None,
                        update=False):
                capture.display(data, metadata)

            def clear_output(self, wait=False):
                capture.events[:] = [e for e in capture.events if e["type"] != "display"]

        self._saved = (sys.stdout, sys.stderr, self.shell.display_pub)
        sys.stdout, sys.stderr = _Stream("stdout"), _Stream("stderr")
        self.shell.display_pub = _Publisher()
        return self

    def __exit__(self, *exc):
        sys.stdout, sys.stderr, self.shell.display_pub = self._saved
        return False


def _open_figures():
    """Numbers of the pyplot figures currently open — without importing
    pyplot (a second's work) for sheets that never plot."""
    plt = sys.modules.get("matplotlib.pyplot")
    return tuple(plt.get_fignums()) if plt is not None else ()


# ---------------------------------------------------------------------------
# Splitting a region into statements
# ---------------------------------------------------------------------------

class _Item:
    """One line of a region as the worksheet shows it: a statement with
    room for its result, or a comment."""
    __slots__ = ("kind", "first", "last", "src", "comment", "gap", "nodes", "code",
                 "mode", "tsrc", "offset", "suppress", "reads", "volatile", "calls",
                 "error", "key")

    def __init__(self, kind, first, last, src):
        self.kind, self.first, self.last, self.src = kind, first, last, src
        self.comment = ""
        self.gap = False            # a blank line precedes it
        self.nodes = ()
        self.code = None
        self.mode = None
        self.tsrc = ""
        self.offset = 0             # region line = transformed line + offset
        self.suppress = False       # ends with ``;``: value not shown
        self.reads = frozenset()
        self.volatile = False
        self.calls = ()
        self.error = None
        self.key = None


class _Parsed:
    __slots__ = ("items", "state_after")

    def __init__(self, items, state_after):
        self.items = items
        self.state_after = state_after      # set when transforming changed DSL state


def _strip_comment(line):
    """``(code, comment text)`` for one source line."""
    code, comment = _dsl.split_comment_outside_strings(line)
    return code, comment[1:].strip() if comment else ""


def _logical_chunks(lines):
    """Line ranges (0-based, inclusive) of the top-level statements of
    DSL source, found without parsing it — used to keep the good
    statements of a region running when one of them does not transform."""
    chunks, start = [], None
    depth, quote, continued = 0, None, False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (depth == 0 and quote is None and not continued and stripped
                and line[0] not in " \t"):
            word = stripped.split(None, 1)[0].rstrip(":")
            after_decorator = start is not None and lines[i - 1].lstrip().startswith("@")
            if word not in ("else", "elif", "except", "finally") and not after_decorator:
                if start is not None:
                    chunks.append((start, i - 1))
                start = i
        j, n = 0, len(line)
        while j < n:
            ch = line[j]
            if quote:
                if ch == "\\":
                    j += 1
                elif line.startswith(quote, j):
                    j += len(quote) - 1
                    quote = None
            elif ch == "#":
                break
            elif ch in "\"'":
                quote = line[j:j + 3] if line[j:j + 3] in ('"""', "'''") else ch
                j += len(quote) - 1
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(0, depth - 1)
            j += 1
        if quote and len(quote) == 1:
            quote = None                        # an unterminated one-line string
        continued = stripped.endswith("\\")
    if start is not None:
        chunks.append((start, len(lines) - 1))
    trimmed = []
    for a, b in chunks:
        while b > a and not lines[b].strip():
            b -= 1
        trimmed.append((a, b))
    return trimmed


def _analyse(nodes):
    """``(reads, volatile, calls)`` for a statement.

    ``reads`` is every name it may look up — a superset is fine: an
    extra name can only cause a needless recalculation, never a stale
    result.  ``calls`` lists what is called, for the check at run time
    that those callables are computations (see ``Engine._impure``).
    """
    reads, calls, volatile = set(), [], False
    for root in nodes:
        for node in ast.walk(root):
            if isinstance(node, ast.Name):
                reads.add(node.id)              # targets too: ``x += 1`` reads x
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                reads.add(node.name)
            elif isinstance(node, ast.Attribute):
                if node.attr in _VOLATILE_ATTRIBUTES:
                    volatile = True
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                module = node.module if isinstance(node, ast.ImportFrom) else None
                for alias in node.names:
                    if alias.name == "*" or not _pure_module(module or alias.name):
                        volatile = True
            elif isinstance(node, (ast.Global, ast.Nonlocal, ast.Await, ast.Yield,
                                   ast.YieldFrom)):
                volatile = True
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    calls.append((func.id, False))
                    if func.id == "pv" and not node.args:
                        volatile = True         # pv() lists every variable
                    if (func.id == "iso" and node.args and isinstance(node.args[0], ast.Constant)
                            and str(node.args[0].value).strip().lower() in _CLOCK_LITERALS):
                        volatile = True
                else:
                    while isinstance(func, ast.Attribute):
                        func = func.value
                    if isinstance(func, ast.Name):
                        calls.append((func.id, True))
    if reads & _VOLATILE_NAMES:
        volatile = True
    return frozenset(reads), volatile, tuple(dict.fromkeys(calls))


# ---------------------------------------------------------------------------
# Reuse of earlier results
# ---------------------------------------------------------------------------

class _Entry:
    """What running a statement did, for replaying it without running."""
    __slots__ = ("inputs", "writes", "write_fps", "deletes", "result", "last_pass")


class _Memo:
    """Results of earlier runs, keyed by statement text and DSL state.

    An entry may stand in for running its statement again when

    * every name the statement reads has the same *value* as when the
      entry was made — and, where that value can change in place (a list,
      an array), is the very same object, so that aliases stay aliases;
    * every object the statement bound is still as the statement left it
      (nothing has modified it since).

    Binding those same objects to the same names is then
    indistinguishable from executing the statement.  Entries are only
    made for statements that are plain computations — see
    ``Engine.run_item`` for what disqualifies one.
    """
    KEEP_PASSES = 3
    PER_KEY = 4

    def __init__(self):
        self.entries = {}

    def candidates(self, key):
        return self.entries.get(key, ())

    def add(self, key, entry):
        bucket = self.entries.setdefault(key, [])
        bucket.insert(0, entry)
        del bucket[self.PER_KEY:]

    def prune(self, pass_no):
        horizon = pass_no - self.KEEP_PASSES
        for key in list(self.entries):
            bucket = [e for e in self.entries[key] if e.last_pass > horizon]
            if bucket:
                self.entries[key] = bucket
            else:
                del self.entries[key]

    def clear(self):
        self.entries.clear()


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

class _Names(mathrender.NameInfo):
    """Name classification for the typesetter, from the live namespace."""

    def __init__(self, engine):
        self.engine = engine
        self.units = frozenset(_dsl._UNIT_NAMES_FOR_BINDING) - frozenset(_dsl._CONSTANT_NAMES)

    def is_user(self, name):
        e = self.engine
        value = e.ns.get(name, MISSING)
        return value is not MISSING and e.baseline.get(name, MISSING) is not value

    def is_unit(self, name):
        return name in self.units and not self.is_user(name)

    def unit_label(self, name):
        obj = self.engine.ns.get(name, MISSING)
        if obj is MISSING:
            obj = getattr(builtins, name, None)
        for attr in ("label", "name", "code"):      # display unit / delta unit / currency
            try:
                label = object.__getattribute__(obj, attr)
            except AttributeError:
                continue
            if isinstance(label, str) and label:
                return label
        return name


def _digest(obj) -> str:
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


class Engine:
    def __init__(self, shell):
        self.shell = shell
        self.ns = shell.user_ns
        self.baseline = dict(self.ns)
        self.state0 = _state_get()
        self.names = _Names(self)
        self.fp = Fingerprinter(self.ns, self.baseline)
        self.memo = _Memo()
        self.parse_cache = {}
        self.pass_no = 0
        self.executed = self.reused = 0
        self.interrupted = False
        self._registered = set()

    # ---- transport -----------------------------------------------------
    def _publish(self, payload):
        from IPython.display import publish_display_data
        publish_display_data({MIME: payload, "text/plain": ""})

    def handle(self, encoded):
        """Entry point for requests from the server (base64 JSON)."""
        request = json.loads(base64.b64decode(encoded).decode("utf-8"))
        seq = request.get("seq")
        try:
            getattr(self, "op_" + request["op"])(request)
        except KeyboardInterrupt:
            self._publish({"op": "done", "seq": seq, "interrupted": True})
        except Exception:
            self._publish({"op": "fault", "seq": seq, "traceback": traceback.format_exc()})

    # ---- parsing -------------------------------------------------------
    def _register_source(self, tsrc):
        """Give transformed source a file name ``linecache`` can serve, so
        tracebacks — and ``pv``, which reads its own call line — find it."""
        digest = hashlib.blake2b(tsrc.encode("utf-8"), digest_size=6).hexdigest()
        filename = f"<worksheet {digest}>"
        if filename not in self._registered:
            linecache.cache[filename] = (len(tsrc), None, tsrc.splitlines(True), filename)
            self._registered.add(filename)
        return filename

    def parse(self, source):
        """Split region ``source`` into items under the current DSL state."""
        token = _state_token()
        key = (source, token)
        hit = self.parse_cache.get(key)
        if hit is not None:
            if hit.state_after is not None:
                _state_set(hit.state_after)     # e.g. ``symbols: x₁`` registers x₁
            return hit
        items = self._parse(source)
        parsed = _Parsed(items, _state_get() if _state_token() != token else None)
        if len(self.parse_cache) > 4000:
            self.parse_cache.clear()
        self.parse_cache[key] = parsed
        return parsed

    def _parse(self, source):
        lines = source.split("\n")
        header = lines[0].strip() if lines else ""
        mode, raw = None, False
        body = list(lines)
        if header == "%%python":
            raw = True
            body[0] = ""                        # keeps line numbers in step
        elif header == "%%dsl" or header.startswith("%%dsl "):
            mode = header[5:].strip() or _runtime.get_syntax_mode()
            body[0] = ""
        elif header.startswith("%%") or any(
                line.lstrip().startswith(("%", "!", "?")) for line in lines):
            # A cell magic, or a cell with line magics: IPython's business.
            item = _Item("magic", 1, len(lines), source)
            item.key = ("magic", source)
            item.volatile = True
            return [item]
        try:
            return self._parse_block(body, 0, len(body) - 1, mode, raw)
        except Exception:
            pass
        # Something in the region does not transform.  Take it statement
        # by statement so that the others still calculate.
        items = []
        for a, b in _logical_chunks(body):
            try:
                items += self._parse_block(body, a, b, mode, raw)
            except Exception as exc:
                item = _Item("stmt", a + 1, b + 1, "\n".join(lines[a:b + 1]))
                item.offset = a
                item.error = self._error(exc, item)
                item.key = ("error", item.src)
                items.append(item)
        return items

    def _parse_block(self, lines, a, b, mode, raw):
        """Items for lines ``a..b`` (0-based) transformed as one unit."""
        block = lines[a:b + 1]
        text = "\n".join(block)
        if raw:
            tsrc = text
        elif mode is not None:
            tsrc = _dsl.transform_source(text, syntax_mode=mode)
        else:
            tsrc = _dsl.transform_source(text)
        filename = self._register_source(tsrc)
        tree = ast.parse(tsrc, filename)
        tag = "raw" if raw else mode
        if len(tsrc.split("\n")) != len(block):
            # The transformer keeps lines in step.  Should it ever not,
            # per-line bookkeeping would be off: run the block whole.
            item = _Item("stmt", a + 1, b + 1, text)
            item.offset = a
            self._compile(item, tree.body, tsrc, filename, tag)
            return [item] if tree.body else []

        items, gap = [], False

        def fill(first, last):
            """Comment and blank lines ``first..last`` between statements."""
            nonlocal gap
            pending = []

            def flush():
                nonlocal gap
                if pending:
                    item = _Item("comment", a + pending[0][0], a + pending[-1][0],
                                 "\n".join(t for _, t in pending))
                    item.gap, gap = gap, False
                    items.append(item)
                    pending.clear()

            for n in range(first, last + 1):
                stripped = block[n - 1].strip()
                if stripped.startswith("#"):
                    pending.append((n, stripped[1:].strip()))
                else:
                    flush()
                    gap = gap or bool(items)
            flush()

        body, i, cursor = tree.body, 0, 1
        while i < len(body):
            node = body[i]
            first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
            last = node.end_lineno
            nodes = [node]
            while i + 1 < len(body) and body[i + 1].lineno <= last:
                i += 1                           # ``a = 1; b = 2`` share a line
                nodes.append(body[i])
                last = max(last, body[i].end_lineno)
            fill(cursor, first - 1)
            item = _Item("stmt", a + first, a + last, "\n".join(block[first - 1:last]))
            item.offset = a
            item.gap, gap = gap, False
            if first == last:
                code, item.comment = _strip_comment(block[first - 1])
                item.suppress = code.rstrip().endswith(";")
                item.src = code.strip()
            self._compile(item, nodes, tsrc, filename, tag)
            items.append(item)
            cursor, i = last + 1, i + 1
        fill(cursor, len(block))
        return items

    def _compile(self, item, nodes, tsrc, filename, tag):
        item.nodes = tuple(nodes)
        item.tsrc = tsrc
        if len(nodes) == 1 and isinstance(nodes[0], ast.Expr):
            item.mode = "eval"
            item.code = compile(ast.Expression(nodes[0].value), filename, "eval",
                                dont_inherit=True)
        else:
            item.mode = "exec"
            item.code = compile(ast.Module(list(nodes), []), filename, "exec",
                                dont_inherit=True)
        item.reads, item.volatile, item.calls = _analyse(nodes)
        item.key = (tag, item.src, item.comment, item.suppress)

    # ---- errors --------------------------------------------------------
    def _error(self, exc, item=None):
        """A compact description of ``exc`` for display under a region."""
        offset = item.offset if item is not None else 0
        line = None
        if isinstance(exc, SyntaxError):
            message = exc.msg or str(exc)
            if exc.lineno:
                line = exc.lineno + offset
        else:
            message = str(exc)
            tb = exc.__traceback__
            while tb is not None:               # innermost frame in the sheet itself
                if tb.tb_frame.f_code.co_filename in self._registered:
                    line = tb.tb_lineno + offset
                tb = tb.tb_next
        if line is None and item is not None:
            line = item.first
        info = {"ename": type(exc).__name__, "evalue": message, "line": line}
        hint = self._hint(exc, message)
        if hint:
            info["hint"] = hint
        if not isinstance(exc, SyntaxError):
            info["traceback"] = self._traceback(exc)
        return info

    def _traceback(self, exc):
        """The traceback from the sheet's own frames inward — without the
        frames of this engine that happened to make the call."""
        frames = traceback.extract_tb(exc.__traceback__)
        start = next((i for i, f in enumerate(frames) if f.filename in self._registered), None)
        frames = frames[start:] if start is not None else frames[-3:]
        text = "".join(traceback.format_list(frames[:2] + frames[2:][-4:])
                       + traceback.format_exception_only(type(exc), exc))
        return text[-4000:]

    @staticmethod
    def _hint(exc, message):
        if isinstance(exc, NameError):
            return ("Regions are calculated top to bottom, left to right: a name must be "
                    "defined above, or to the left on the same line, of where it is used.")
        if isinstance(exc, SyntaxError) and "function call" in message and "assign" in message:
            return "To define a function write  f := x → x² + 1  (or  f := (x, y) → …)."
        return None

    # ---- executing -----------------------------------------------------
    def _reset(self):
        ns = self.ns
        for name in [n for n in ns if n not in self.baseline]:
            del ns[name]
        ns.update(self.baseline)
        _state_set(self.state0)
        plt = sys.modules.get("matplotlib.pyplot")
        if plt is not None and plt.get_fignums():
            plt.close("all")

    def _format(self, value):
        """MIME bundle for a value, as a notebook would display it."""
        try:
            data, _ = self.shell.display_formatter.format(value, include=set(_SHOW_MIMES))
        except Exception:
            data = {}
        if not data:
            try:
                data = {"text/plain": repr(value)}
            except Exception as exc:
                data = {"text/plain": f"<unprintable {type(value).__name__}: {exc}>"}
        data = _jsonable(data)
        latex = data.get("text/latex")
        if isinstance(latex, str) and len(latex) > _MAX_TEXT:
            del data["text/latex"]
        plain = data.get("text/plain")
        if isinstance(plain, str) and len(plain) > _MAX_TEXT:
            data["text/plain"] = plain[:_MAX_TEXT] + " …"
        return data

    @staticmethod
    def _shows_value(value):
        return not (value is None or isinstance(value, (
            types.FunctionType, types.BuiltinFunctionType, types.MethodType,
            types.ModuleType, type)))

    @staticmethod
    def _assigned_names(node):
        """Plain names a statement assigns, for showing their new values."""
        if isinstance(node, ast.Assign):
            target = node.targets[-1]
        elif isinstance(node, ast.AugAssign):
            target = node.target
        else:
            return []
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, ast.Tuple) and all(isinstance(t, ast.Name) for t in target.elts):
            return [t.id for t in target.elts]
        return []

    def _impure(self, item):
        """Does the statement call into a module that may keep state of
        its own?  Decided when it runs, from what the names are bound to."""
        ns = self.ns
        for name, through_attribute in item.calls:
            obj = ns.get(name, MISSING)
            if obj is MISSING or self.baseline.get(name, MISSING) is obj:
                continue                        # a builtin, or the toolkit's own
            if isinstance(obj, types.ModuleType):
                if not _pure_module(obj.__name__):
                    return True
            elif not through_attribute:
                if isinstance(obj, types.FunctionType) and obj.__globals__ is ns:
                    continue                    # defined in the sheet: tracked by value
                if isinstance(obj, (types.FunctionType, types.BuiltinFunctionType, type)) \
                        and not _pure_module(getattr(obj, "__module__", None)):
                    return True
        return False

    def _run_magic(self, item, out):
        with _Capture(self.shell) as cap:
            result = self.shell.run_cell(item.src, store_history=False)
        out["outputs"] = cap.events
        exc = result.error_before_exec or result.error_in_exec
        if isinstance(exc, KeyboardInterrupt):
            self.interrupted = True
            out["error"] = dict(_STOPPED, line=item.first)
        elif exc is not None:
            out["error"] = self._error(exc, item)
        elif self._shows_value(result.result):
            out["result"] = [self._format(result.result)]

    def _execute(self, item, out):
        ns = self.ns
        rendered = None
        if len(item.nodes) == 1:
            rendered = mathrender.render_statement(item.nodes[0], item.tsrc, self.names)
        if rendered:
            out.update(latex=rendered["latex"], kind=rendered["kind"],
                       literal=rendered["literal"])
            if rendered.get("target"):
                out["target"] = rendered["target"]
        else:
            out["kind"] = "expr" if item.mode == "eval" else "other"
        value = None
        with _Capture(self.shell) as cap:
            try:
                if item.mode == "eval":
                    value = eval(item.code, ns)
                else:
                    exec(item.code, ns)
            except KeyboardInterrupt:
                # The statement is reported as stopped and the pass ends
                # after it (``op_calc`` looks at the flag).
                self.interrupted = True
                out["error"] = dict(_STOPPED, line=item.first)
            except BaseException as exc:        # SystemExit too: report it, carry on
                out["error"] = self._error(exc, item)
        out["outputs"] = cap.events
        if "error" not in out:
            if item.mode == "eval":
                if self._shows_value(value):
                    out["result"] = [self._format(value)]
            elif len(item.nodes) == 1:
                names = self._assigned_names(item.nodes[0])
                values = [ns.get(n, MISSING) for n in names]
                if names and all(v is not MISSING and self._shows_value(v) for v in values):
                    out["result"] = [self._format(v) for v in values]
                    if "kind" in out and out["kind"] == "other":
                        out["kind"] = "assign"

    def _reusable(self, entry):
        ns, fp = self.ns, self.fp
        for name, (recorded, obj) in entry.inputs.items():
            current, _ = fp.of_name(name)
            if current != recorded or (obj is not None and ns.get(name, MISSING) is not obj):
                return False
        for name, obj in entry.writes.items():
            if fp(obj) != entry.write_fps[name]:
                return False                    # modified since: not what was bound
        return True

    def _placed(self, result, item):
        """A recorded result, at the place its statement now occupies."""
        lines = [item.first, item.last]
        if result["lines"] == lines and result.get("gap", False) == item.gap:
            return result
        return dict(result, lines=lines, gap=item.gap)

    def run_item(self, item, use_memo=True):
        """Result for one item — replayed if nothing it reads has changed."""
        if item.kind == "comment":
            return {"t": "comment", "text": item.src, "gap": item.gap,
                    "lines": [item.first, item.last], "h": _digest(item.src)}
        base = {"t": "stmt", "src": item.src, "lines": [item.first, item.last],
                "gap": item.gap}
        if item.error is not None:
            base.update(error=item.error, outputs=[])
            base["h"] = _digest([item.src, item.error])
            return base

        key = (item.key, _state_token())
        memoizable = (use_memo and item.kind == "stmt" and not item.volatile
                      and not self._impure(item))
        if memoizable:
            for entry in self.memo.candidates(key):
                if self._reusable(entry):
                    ns = self.ns
                    ns.update(entry.writes)
                    for name in entry.deletes:
                        ns.pop(name, None)
                    entry.last_pass = self.pass_no
                    self.reused += 1
                    return self._placed(entry.result, item)

        out = base
        if item.comment:
            out["comment"] = item.comment
        if item.suppress:
            out["suppress"] = True

        ns, fp = self.ns, self.fp
        state_before = key[1]
        if memoizable:
            inputs = {name: fp.of_name(name) for name in item.reads}
            seen = {name: ns.get(name, MISSING) for name in item.reads}
        figures = _open_figures()
        before = dict(ns)
        t0 = time.perf_counter()
        try:
            if item.kind == "magic":
                self._run_magic(item, out)
            else:
                self._execute(item, out)
        finally:
            ms = round((time.perf_counter() - t0) * 1000, 2)
            self.executed += 1
        out["h"] = _digest(out)
        out["ms"] = ms

        # Record the run for reuse — but only if replaying it later would
        # be the same as running it:
        #   * the DSL state is as it was (a mode switch must happen again);
        #   * no figure was left open (pyplot state the sheet cannot see);
        #   * every input and every bound object has a fingerprint;
        #   * the objects it read are unchanged (an in-place modification
        #     cannot be replayed by binding names).
        if (memoizable and not self.interrupted and _state_token() == state_before
                and _open_figures() == figures):
            writes = {k: v for k, v in ns.items() if before.get(k, MISSING) is not v}
            write_fps = {k: fp(v) for k, v in writes.items()}
            sound = (all(f is not None for f, _ in inputs.values())
                     and all(f is not None for f in write_fps.values())
                     and all(obj is MISSING or fp(obj) == inputs[name][0]
                             for name, obj in seen.items()))
            if sound:
                entry = _Entry()
                entry.inputs, entry.writes, entry.write_fps = inputs, writes, write_fps
                entry.deletes = [k for k in before if k not in ns]
                entry.result, entry.last_pass = out, self.pass_no
                self.memo.add(key, entry)
        return out

    def _close_region(self):
        """Outputs that belong to a region as a whole: figures its
        statements left open are shown at its end, as a notebook cell
        would show them."""
        if not _open_figures():
            return []
        with _Capture(self.shell) as cap:
            try:
                from matplotlib_inline.backend_inline import flush_figures
                flush_figures()
            except Exception:
                sys.modules["matplotlib.pyplot"].close("all")
                cap.write("stderr", traceback.format_exc(limit=1))
        return cap.events

    # ---- operations ----------------------------------------------------
    def op_calc(self, request):
        """Calculate the sheet: ``regions`` is ``[{id, source, have}]`` in
        reading order.  One ``region`` message per region, then ``done``.

        ``have`` is the revision the front end already shows for a
        region; when the new result is the same, only that is said.

        ``fresh`` forgets every earlier result first ("recalculate
        everything").  ``reuse: false`` runs every statement without
        consulting or touching what is remembered — the reference run
        the tests compare an incremental pass against.
        """
        seq = request.get("seq")
        self.pass_no += 1
        self.executed = self.reused = 0
        if request.get("fresh"):
            self.memo.clear()
        use_memo = bool(request.get("reuse", True))
        t0 = time.perf_counter()
        self._reset()
        self.interrupted = False
        for region in request.get("regions", ()):
            results, tail = [], []
            started = time.perf_counter()
            try:
                for item in self.parse(region.get("source", "")).items:
                    results.append(self.run_item(item, use_memo))
                    if self.interrupted:
                        break
                tail = self._close_region()
            except KeyboardInterrupt:           # between statements
                self.interrupted = True
            rev = _digest([[r["h"], r["lines"], r.get("gap", False)] for r in results] + [tail])
            message = {"op": "region", "seq": seq, "id": region.get("id"), "rev": rev}
            if rev == region.get("have") and not self.interrupted:
                message["same"] = True
            else:
                message["items"] = results
                if tail:
                    message["tail"] = tail
                message["ms"] = round((time.perf_counter() - started) * 1000, 2)
            self._publish(message)
            if self.interrupted:
                break                           # the regions below keep what they showed
        self.memo.prune(self.pass_no)
        self._publish({
            "op": "done", "seq": seq, "interrupted": self.interrupted,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "executed": self.executed, "reused": self.reused,
            "names": self.user_names(),
        })

    def op_render(self, request):
        """Typeset regions without running anything — for a sheet that is
        not trusted yet, and for the preview while typing."""
        seq = request.get("seq")
        saved = _state_get()
        try:
            if request.get("reset"):
                _state_set(self.state0)
            for region in request.get("regions", ()):
                results = []
                for item in self._parse(region.get("source", "")):
                    if item.kind == "comment":
                        results.append({"t": "comment", "text": item.src, "gap": item.gap,
                                        "lines": [item.first, item.last]})
                        continue
                    out = {"t": "stmt", "src": item.src, "gap": item.gap,
                           "lines": [item.first, item.last]}
                    if item.comment:
                        out["comment"] = item.comment
                    if item.error is not None:
                        out["error"] = item.error
                    elif len(item.nodes) == 1:
                        rendered = mathrender.render_statement(
                            item.nodes[0], item.tsrc, self.names)
                        if rendered:
                            out.update(latex=rendered["latex"], kind=rendered["kind"],
                                       literal=rendered["literal"])
                    results.append(out)
                self._publish({"op": "rendered", "seq": seq, "id": region.get("id"),
                               "items": results})
        finally:
            _state_set(saved)
        self._publish({"op": "done", "seq": seq, "rendered": True})

    def user_names(self):
        """Names the sheet has defined, for completion in the editor."""
        return sorted(name for name, value in self.ns.items()
                      if isinstance(name, str) and not name.startswith("_")       # (``globals()[5] = 3`` names nothing)
                      and self.baseline.get(name, MISSING) is not value)

    def op_hello(self, request):
        """Describe the toolkit to the front end (names for completion)."""
        import platform
        functions, constants = [], []
        for name, value in self.baseline.items():
            if name.startswith("_") or name in self.names.units:
                continue
            if isinstance(value, (types.FunctionType, types.BuiltinFunctionType)):
                functions.append(name)
            elif name in _dsl._CONSTANT_NAMES:
                constants.append(name)
        seq = request.get("seq")
        self._publish({
            "op": "hello", "seq": seq,
            "units": sorted(self.names.units, key=str.lower),
            "functions": sorted(functions), "constants": sorted(constants),
            "python": platform.python_version(),
            "syntax_mode": _runtime.get_syntax_mode(),
        })
        self._publish({"op": "done", "seq": seq})


def install(preamble: str = PREAMBLE):
    """Prepare the running kernel for worksheets.

    Runs the one-line toolkit import, as the first cell of a notebook
    would, then records the namespace as the baseline that every
    calculation pass starts from.  Returns the engine, which is also
    bound as ``__edsl_ws__`` for the server to address.
    """
    from IPython import get_ipython
    shell = get_ipython()
    if shell is None:
        raise RuntimeError("the worksheet engine runs inside an IPython kernel")
    existing = shell.user_ns.get("__edsl_ws__")
    if isinstance(existing, Engine):
        return existing
    shell.run_cell(preamble, store_history=False).raise_error()
    try:
        from matplotlib_inline.backend_inline import set_matplotlib_formats
        set_matplotlib_formats("retina")
    except Exception:
        pass
    engine = Engine.__new__(Engine)
    shell.user_ns["__edsl_ws__"] = engine       # in the baseline, so it survives resets
    Engine.__init__(engine, shell)
    return engine
