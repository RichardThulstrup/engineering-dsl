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

One thing a clean run would not give again: what a statement reads from
outside the sheet — the clock, a random number, a file, the day's
currency rates.  Such a statement is run once, and what it made is kept:
here, and as text with the sheet's file, so that a revision of a sheet
shows what it showed (see ``Engine._outside`` and ``Engine._keep``).

Results travel to the front end as ``display_data`` messages carrying
the :data:`MIME` type; nothing here knows about the web server.
"""
from __future__ import annotations

import ast
import base64
import builtins
import datetime
import hashlib
import io
import json
import linecache
import re
import sys
import time
import traceback
import types

from .. import circuit_dsl as _dsl
from .. import currencies as _currencies
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

# A statement mentioning one of these is never reused as a calculation
# is.  It reads the namespace wholesale — then it runs on every pass, for
# anything may have changed that it sees …
_WHOLESALE_NAMES = frozenset({
    "globals", "locals", "vars", "dir", "eval", "exec", "get_ipython", "__import__",
})
# … or it depends on something outside the sheet: a random stream, the
# clock, a file, the day's currency rates.  What such a statement makes is
# taken once and kept, with the sheet's file too (see ``Engine._keep``).
_OUTSIDE_NAMES = {
    "random": "random", "rand": "random", "randn": "random", "randint": "random",
    "default_rng": "random",
    "perf_counter": "clock", "monotonic": "clock",
    "input": "file", "open": "file", "environ": "file",
    "get_currency_rate": "rates", "update_currency_rates": "rates",
}
_VOLATILE_NAMES = _WHOLESALE_NAMES | frozenset(_OUTSIDE_NAMES)
# The same, for names that are too ordinary to act on by themselves
# (``time`` and ``load`` are fine variables) but telling as attributes:
# ``datetime.now()``, ``np.loadtxt(…)``, ``f.read()``.
_OUTSIDE_ATTRIBUTES = dict(_OUTSIDE_NAMES, **{
    "now": "clock", "today": "clock", "utcnow": "clock", "time": "clock",
    "load": "file", "loadtxt": "file", "genfromtxt": "file", "fromfile": "file",
    "read": "file", "readlines": "file", "read_text": "file", "read_bytes": "file",
    "read_csv": "file", "read_excel": "file",
})
_VOLATILE_ATTRIBUTES = _VOLATILE_NAMES | frozenset(_OUTSIDE_ATTRIBUTES)
_CLOCK_LITERALS = mathrender.CLOCK_WORDS          # ``"now"ₜᵢₘₑ`` and friends
# A random stream that is given its seed — ``np.random.seed(1)``,
# ``default_rng(42)`` — comes out the same on every run by the sheet's own
# doing.  From there on a pass draws its random numbers anew, as a run
# from the top would: kept ones would not move the stream along.
_SEEDED = frozenset({"default_rng", "RandomState", "Random", "PCG64", "MT19937", "Philox", "SFC64"})
_OPEN_MODE = re.compile(r"[rwxabt+U]{1,4}\Z")

# Calls into these packages are computations: same arguments, same answer.
# A call into any other module (pyplot, pandas I/O, os …) may touch state
# the sheet cannot see, so the statement making it is not reused either.
_PURE_PACKAGES = frozenset({
    "builtins", "utils", "forallpeople", "numpy", "math", "cmath", "sympy",
    "mpmath", "scipy", "statistics", "fractions", "decimal", "itertools",
    "functools", "operator", "collections", "string", "re", "numbers",
})


# These, though, are where the clock, random numbers and files are had
# from: a statement that reads outside the sheet may call into them and
# still have what it made kept.
_SOURCE_PACKAGES = frozenset({
    "random", "time", "datetime", "os", "pathlib", "io", "json", "csv", "pandas",
})


def _pure_module(name, also=frozenset()) -> bool:
    package = (name or "").split(".")[0]
    return package in _PURE_PACKAGES or package in also


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
                 "outside", "whole", "seeds", "binds", "error", "key")

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
        self.outside = frozenset()  # what it reads outside the sheet: "clock", "random", "file", "rates"
        self.whole = False          # it reads the namespace wholesale, or does what cannot be told: run on every pass
        self.seeds = False          # it gives a random stream its seed
        self.binds = frozenset()    # every name it may bind (or unbind)
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


def _opens_to_write(call) -> bool:
    """Is this ``open(…)`` for writing — as far as can be told?  (Writing
    a file is something done, not something read: it is done every time.)"""
    stated = list(call.args[:2]) + [k.value for k in call.keywords if k.arg == "mode"]
    for arg in stated:
        if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                and _OPEN_MODE.match(arg.value) and set(arg.value) & set("wax+")):
            return True
    mode = next((k.value for k in call.keywords if k.arg == "mode"), None)
    if mode is None and len(call.args) > 1:
        mode = call.args[1]
    return mode is not None and not isinstance(mode, ast.Constant)     # a mode that is worked out: who knows


def _through(node):
    """The names an attribute is had through, from the first one on —
    ``("np", "random", "rand")`` for ``np.random.rand`` — or ``None``
    where it hangs on something that is worked out."""
    names = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    return (node.id, *reversed(names)) if isinstance(node, ast.Name) else None


def _analyse(nodes):
    """``(reads, volatile, calls, outside, whole, seeds, binds)`` for a statement.

    ``reads`` is every name it may look up — a superset is fine: an
    extra name can only cause a needless recalculation, never a stale
    result.  ``calls`` lists what is called, for the check at run time
    that those callables are computations (see ``Engine._impure``).
    ``volatile`` says that its result is never reused as a calculation
    is, and the three after it why: it reads something ``outside`` the
    sheet, it reads the namespace as a ``whole`` (or does what cannot be
    told), it ``seeds`` a random stream.  ``binds`` is every name it may
    bind or unbind.

    ``outside`` is pairs ``(which, through)``: which of "clock",
    "random", "file", "rates" — and the names it is had through
    (``("np", "random", "rand")`` for ``np.random.rand()``,
    ``("input",)`` for ``input()``; ``None`` where it hangs on no name).
    What those names lead to when the statement runs says whether it
    is a way out of the sheet at all (``Engine._way_out``): ``load := 5
    kN`` is a fine variable.
    """
    reads, calls, binds, outside = set(), [], set(), set()
    volatile = whole = seeds = False
    for root in nodes:
        for node in ast.walk(root):
            if isinstance(node, ast.Name):
                reads.add(node.id)              # targets too: ``x += 1`` reads x
                if not isinstance(node.ctx, ast.Load):
                    binds.add(node.id)
                elif node.id in _OUTSIDE_NAMES:
                    outside.add((_OUTSIDE_NAMES[node.id], (node.id,)))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                reads.add(node.name)
                binds.add(node.name)
            elif isinstance(node, ast.ExceptHandler):
                if node.name:
                    binds.add(node.name)
            elif isinstance(node, ast.Attribute):
                if node.attr in _VOLATILE_ATTRIBUTES:
                    volatile = True
                    if node.attr in _OUTSIDE_ATTRIBUTES:
                        outside.add((_OUTSIDE_ATTRIBUTES[node.attr], _through(node)))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                module = node.module if isinstance(node, ast.ImportFrom) else None
                for alias in node.names:
                    binds.add(alias.asname or alias.name.split(".")[0])
                    if alias.name == "*" or not _pure_module(module or alias.name):
                        volatile = True
            elif isinstance(node, (ast.Global, ast.Nonlocal, ast.Await, ast.Yield,
                                   ast.YieldFrom)):
                volatile = whole = True
            if isinstance(node, ast.Call):
                func = node.func
                called = (func.id if isinstance(func, ast.Name)
                          else func.attr if isinstance(func, ast.Attribute) else None)
                if called == "seed" or (called in _SEEDED and (node.args or node.keywords)):
                    volatile = seeds = True
                if called == "open" and _opens_to_write(node):
                    whole = True
                if isinstance(func, ast.Name):
                    calls.append((func.id, False))
                    if func.id == "pv" and not node.args:
                        volatile = whole = True     # pv() lists every variable
                    if (func.id == "iso" and node.args and isinstance(node.args[0], ast.Constant)
                            and str(node.args[0].value).strip().lower() in _CLOCK_LITERALS):
                        volatile = True
                        outside.add(("clock", None))
                else:
                    while isinstance(func, ast.Attribute):
                        func = func.value
                    if isinstance(func, ast.Name):
                        calls.append((func.id, True))
    for name in reads & _VOLATILE_NAMES:
        volatile = True
        if name not in _OUTSIDE_NAMES:
            whole = True
    return (frozenset(reads), volatile, tuple(dict.fromkeys(calls)), frozenset(outside), whole, seeds,
            frozenset(binds))


# ---------------------------------------------------------------------------
# What is read from outside the sheet, and kept
# ---------------------------------------------------------------------------

def _stream_kinds():
    """What a random stream may be, as far as the modules are loaded."""
    streams = []
    module = sys.modules.get("random")
    if module is not None:
        streams.append(module.Random)
    numpy = sys.modules.get("numpy")
    if numpy is not None:
        streams += [numpy.random.Generator, numpy.random.RandomState, numpy.random.BitGenerator]
    return tuple(streams)


def _draws(obj, streams) -> bool:
    """Is this a random stream — or what draws from one: ``rand``, of
    ``from numpy.random import rand``?"""
    if isinstance(obj, (types.MethodType, types.BuiltinFunctionType)):
        obj = obj.__self__
    return isinstance(obj, streams)


def _closed_file(name, mode):
    """What is left of a file that a statement opened, read and closed
    (``with open(…) as f``), for the name it is bound to: a file that is
    closed, of that name."""
    file = io.StringIO()
    file.name, file.mode = name, mode
    file.close()
    return file


def _plain(print_):
    """A fingerprint as JSON can carry it — the same for the same value in
    another kernel, on another day (a set of prints has no order of its own)."""
    if isinstance(print_, tuple):
        return [_plain(part) for part in print_]
    if isinstance(print_, frozenset):
        return ["{}", sorted(_digest(_plain(part)) for part in print_)]
    return print_


def _state_digest() -> str:
    """The DSL's state as far as it is the same in another kernel (see ``_state_token``)."""
    return _digest([_runtime._syntax_mode, _sigfig._DECIMAL_LITERAL_MODE,
                    sorted(_dsl._DECLARED_SUBSCRIPT_SYMBOLS.items()), sorted(_dsl.PROTECTED_NAMES),
                    sorted(_sigfig._RADIX_FORMATTERS)])


def _events_ok(events) -> bool:
    """Is this what a statement printed and displayed, as the engine says it (``_Capture``)?"""
    if not isinstance(events, list):
        return False
    for event in events:
        if not isinstance(event, dict):
            return False
        if event.get("type") == "stream":
            if not (isinstance(event.get("name"), str) and isinstance(event.get("text"), str)):
                return False
        elif event.get("type") == "display":
            data = event.get("data")
            if not (isinstance(data, dict) and all(isinstance(text, str) for text in data.values())):
                return False
        else:
            return False
    return True


def _closed_ok(held) -> bool:
    return isinstance(held, dict) and all(
        isinstance(said, list) and len(said) == 2 and all(isinstance(word, str) for word in said)
        for said in held.values())


def _record_ok(record) -> bool:
    """Is this what a statement made, as ``Engine._keep`` says it — as far
    as its shape goes?  (A sheet's file says it, and that may be anyone's:
    it comes as JSON.  What it says is looked at in ``Engine._replay``,
    where a text that is no value makes no record.)"""
    return (isinstance(record, dict)
            and all(isinstance(record.get(key), str) for key in ("id", "at"))
            and isinstance(record.get("reads"), dict) and isinstance(record.get("writes"), dict)
            and _closed_ok(record.get("closed", {}))
            and isinstance(record.get("gone", []), list)
            and all(isinstance(name, str) for name in record.get("gone", []))
            and isinstance(record.get("why", []), list)
            and _events_ok(record.get("outputs", [])))


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
    # How many readings of statements that the sheet has no more are held
    # on to: an undo brings its statement back, and its reading with it.
    SPARE = 40

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
        self.calculated = None      # the pass (its ``seq``) whose values the namespace holds, once it got to its end
        # What statements made that read outside the sheet, by what names
        # them (``_ident``): kept, and standing in for running them again.
        self.kept = {}
        self.seeded = False         # this pass has given a random stream its seed
        self._met = {}              # this pass: how often each statement's text has come by
        self._taken = {}            # this region: what was kept anew, for the page (and the file)
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
        (item.reads, item.volatile, item.calls, item.outside, item.whole, item.seeds,
         item.binds) = _analyse(nodes)
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

    def _impure(self, item, also=frozenset(), known=frozenset()):
        """Does the statement call into a module that may keep state of
        its own?  Decided when it runs, from what the names are bound to.
        (``also``: packages that are let pass as well; ``known``: names
        that are known for what they are.)"""
        ns = self.ns
        for name, through_attribute in item.calls:
            obj = ns.get(name, MISSING)
            if obj is MISSING or self.baseline.get(name, MISSING) is obj or name in known:
                continue                        # a builtin, or the toolkit's own
            if isinstance(obj, types.ModuleType):
                if not _pure_module(obj.__name__, also):
                    return True
            elif not through_attribute:
                if isinstance(obj, types.FunctionType) and obj.__globals__ is ns:
                    continue                    # defined in the sheet: tracked by value
                if isinstance(obj, (types.FunctionType, types.BuiltinFunctionType, type)) \
                        and not _pure_module(getattr(obj, "__module__", None), also):
                    return True
        return False

    # ---- what is read from outside the sheet, and kept --------------------
    def _way_out(self, through):
        """Is what these names lead to — ``("np", "random", "rand")`` for
        ``np.random.rand`` — a way out of the sheet: a function, a
        module, something the sheet cannot look into?  A plain value is
        none (``load := 5 kN`` is a variable like any other, and
        ``when.time()`` reads no clock where ``when`` is a date), and
        neither is a class (``datetime.time(12, 30)`` makes a time of
        day)."""
        obj = self.ns.get(through[0], MISSING)
        for name in through[1:]:
            if not isinstance(obj, (types.ModuleType, type)):
                break                           # (a value is not asked for its attributes: asking may run code)
            obj = getattr(obj, name, MISSING)
        if isinstance(obj, (types.ModuleType, types.FunctionType, types.BuiltinFunctionType)):
            return True
        # What the sheet can compare is data — and so is a class it knows.
        # What it cannot is a way out: ``os.environ``, a stream, and what
        # is no name of the sheet at all (``open``).
        return self.fp(obj) is None

    def _function_names(self, item):
        """Every name in the code of the sheet's own functions that a
        statement has in hand — and of the ones those name in turn.  (A
        function reads, when it is called, what the names in its code
        read.)"""
        ns = self.ns
        names, seen, queue = set(), set(), list(item.reads)
        while queue:
            fn = ns.get(queue.pop(), MISSING)
            if isinstance(fn, types.FunctionType) and fn.__globals__ is ns and id(fn) not in seen:
                seen.add(id(fn))
                own = Fingerprinter._global_names(fn.__code__, set())
                names |= own
                queue += own
        return names

    def _outside(self, item):
        """What a statement reads from outside the sheet — some of
        "clock", "random", "file", "rates" — where what it makes of that
        can be kept; nothing, for a statement that reads nothing out
        there; and ``None`` for one that is run on every pass, whatever
        it reads:

        * it reads the namespace wholesale, or gives a random stream its
          seed (and after that, in the same pass, it draws from one);
        * it calls into a module that may keep state of its own, other
          than the ones the clock, random numbers and files come from.

        (What it makes is not kept either where it reads something that
        cannot be compared — a file it has in hand, say: see ``_keep``.)
        """
        if item.whole or item.seeds:
            return None
        ns = self.ns
        found = {which for which, through in item.outside if through is None or self._way_out(through)}
        streams = _stream_kinds()
        drawn = set()                           # the names that are a random stream, or draw from one
        for name in item.reads:
            if _draws(ns.get(name, MISSING), streams):
                drawn.add(name)
                found.add("random")
        # (A function of the sheet's own that gives a seed: the statement
        # that defines it says ``seed``, and the pass has its seed from there.)
        found |= {_OUTSIDE_ATTRIBUTES[name] for name in self._function_names(item)
                  if name in _OUTSIDE_ATTRIBUTES and self._way_out((name,))}
        if "random" in found and self.seeded:
            return None
        if found and self._impure(item, _SOURCE_PACKAGES, drawn):
            return None
        return found

    def _has_currency(self, item):
        """Has the statement a currency in hand — itself, or in a function
        of the sheet's own that it names?"""
        ns = self.ns
        return any(isinstance(ns.get(name), _currencies.Currency)
                   for name in item.reads | self._function_names(item))

    def _parts(self, objects):
        """Every part of these values that can be changed in place — a
        list, an array — as far as a fingerprint looks into them."""
        fp = self.fp
        fp.parts = found = []
        try:
            for obj in objects:
                fp(obj)
        finally:
            fp.parts = None
        return found

    def _entangled(self, read, made):
        """Is something a statement made a part of what it read, so that
        changing the one in place changes the other?  (``pick :=
        random.choice(rows)`` hands on a row of ``rows`` itself.)"""
        mine = self._parts(made)
        if not mine:
            return False
        theirs = self._parts(read)
        if {id(part) for part in mine} & {id(part) for part in theirs}:
            return True
        numpy = sys.modules.get("numpy")
        if numpy is None:
            return False
        arrays = lambda parts: [part for part in parts if isinstance(part, numpy.ndarray)]     # noqa: E731
        return any(numpy.may_share_memory(one, other) for one in arrays(mine) for other in arrays(theirs))

    def _ident(self, item):
        """What names a statement among the ones that are kept: its text,
        and the how-manieth statement of that text it is in this pass (two
        lines that say ``random()`` are two draws)."""
        said = item.src.rstrip("; \t") if item.suppress else item.src      # (hiding its value changes nothing of it)
        text = _digest(said)
        self._met[text] = count = self._met.get(text, 0) + 1
        return f"{text}.{count}"

    def _named(self, name):
        """The toolkit's own object of this name (for values read from text)."""
        return self.baseline.get(name)

    def _keep(self, ident, item, out, why, inputs, seen, before, value):
        """Keep what a statement made that read outside the sheet — where
        it can be kept: binding the same names to the same values, and
        showing what it showed, must be all that running it did.

        Kept are the names it bound, and its value, as text (``values``);
        what it printed and displayed; and what it read of the sheet, as
        fingerprints — while those are what they were, the record stands
        in for the statement (``_replay``).
        """
        from . import values
        ns, fp = self.ns, self.fp
        streams = _stream_kinds()
        reads = {}
        for name, (print_, _) in inputs.items():
            obj = seen[name]
            if print_ is None:
                # It reads what cannot be compared: who knows what it depends
                # on.  (A file that is held open, for one: what is read from
                # it depends on what was read before — and kept, the reads
                # before it would not have moved the file along.)
                if not _draws(obj, streams):
                    return None
            elif obj is not MISSING and fp(obj) != print_:
                return None                     # it changed what it read: that is not done again by binding names
            else:
                reads[name] = _digest(_plain(print_))
        writes = {name: obj for name, obj in ns.items() if before.get(name, MISSING) is not obj}
        forms, made, closed = {}, {}, {}
        try:
            for name, obj in writes.items():
                if name not in item.binds:
                    if isinstance(name, str) and name.startswith("__"):
                        continue                # Python's own notes (``__warningregistry__``): not the statement's
                    return None                 # bound in a way that cannot be read off the statement
                made[name] = obj
                if isinstance(obj, io.IOBase) and obj.closed:
                    # ``with open(…) as f``: the file is done with — what is left of it is its name.
                    closed[name] = [str(getattr(obj, "name", "")), str(getattr(obj, "mode", ""))]
                else:
                    forms[name] = values.dump(obj, fp._base)
            shown = values.dump(value, fp._base) if item.mode == "eval" else None
        except values.NotOffered:
            return None                         # (a function, a table, a file that is open: made anew every time)
        if self._entangled([obj for obj in seen.values() if obj is not MISSING], [made[name] for name in forms]):
            return None                         # (read back from text, it would be a copy of that part)
        now = datetime.datetime.now(datetime.timezone.utc)
        record = {"id": ident, "why": sorted(why), "state": _state_digest(), "reads": reads, "writes": forms,
                  "at": now.isoformat(timespec="seconds").replace("+00:00", "Z")}
        gone = [name for name in before if name not in ns]
        if gone:
            record["gone"] = gone
        if closed:
            record["closed"] = closed
        if shown is not None:
            record["value"] = shown
        if out.get("outputs"):
            if len(json.dumps(out["outputs"])) > values._TEXT:
                return None                     # (it printed more than a sheet should carry about)
            record["outputs"] = out["outputs"]
        # In this kernel the very objects are bound again — as long as nothing has changed them since.
        record["_made"] = (made, value)
        record["_prints"] = {name: fp(obj) for name, obj in made.items()}
        self.kept[ident] = record
        self._taken[ident] = {key: held for key, held in record.items() if not key.startswith("_")}
        return record

    def _replay(self, item, record, given):
        """What a statement shows, from what was kept of it — with the
        names it bound then bound again.  ``None`` where the record does
        not stand: the statement reads other things of the sheet than it
        did, the notation is in another state, or the record is not one
        of this statement (it binds other names) or cannot be read.
        """
        from . import values
        ns, fp = self.ns, self.fp
        if record.get("state") != _state_digest():
            return None
        if not (set(record["writes"]) | set(record.get("gone", ())) | set(record.get("closed", ()))) <= item.binds:
            return None
        streams = _stream_kinds()
        reads = record["reads"]
        for name in item.reads:
            print_, _ = fp.of_name(name)
            if print_ is None:
                if name in reads or not _draws(ns.get(name, MISSING), streams):
                    return None
            elif reads.get(name) != _digest(_plain(print_)):
                return None
        made = record.get("_made")
        if made is not None and any(fp(obj) != record["_prints"][name] for name, obj in made[0].items()):
            made = None                         # changed in place since: made afresh, from the text
        if made is None:
            try:
                bound = {name: values.load(form, self._named) for name, form in record["writes"].items()}
                value = values.load(record["value"], self._named) if "value" in record else None
            except ValueError:
                return None
            bound.update((name, _closed_file(*said)) for name, said in record.get("closed", {}).items())
            made = record["_made"] = (bound, value)
            record["_prints"] = {name: fp(obj) for name, obj in bound.items()}
        bound, value = made
        ns.update(bound)
        for name in record.get("gone", ()):
            ns.pop(name, None)
        out = {"t": "stmt", "src": item.src, "lines": [item.first, item.last], "gap": item.gap}
        if item.comment:
            out["comment"] = item.comment
        if item.suppress:
            out["suppress"] = True
        self._typeset(item, out)
        out["outputs"] = record.get("outputs", [])
        self._shown(item, out, value, given)
        out["kept"] = {"id": record["id"], "at": record["at"], "why": record.get("why", [])}
        out["h"] = _digest(out)
        out["ms"] = 0.0
        return out

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

    def _brief(self, value, most=2000):
        """How a value is displayed, in brief — for the list another sheet
        chooses from, which is kept in the sheet's file at every save: its
        LaTeX where that is short, and the beginning of its plain text."""
        shown = self._format(value)
        latex, plain = shown.get("text/latex"), shown.get("text/plain", "")
        brief = {"text/plain": plain if len(plain) <= most else plain[:most] + " …"}
        if isinstance(latex, str) and len(latex) <= most:
            brief["text/latex"] = latex
        return brief

    def _name_latex(self, name):
        """A name as the sheet typesets it where it stands alone ("" where it has no such form)."""
        rendered = mathrender.render_statement(ast.parse(name).body[0], name, self.names)
        return rendered["latex"] if rendered else ""

    def _typeset(self, item, out):
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

    def _execute(self, item, out, given=False):
        """Run a statement; what it shows goes into ``out``.  Returns its
        value (of a statement that is an expression, and ran)."""
        ns = self.ns
        self._typeset(item, out)
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
            self._shown(item, out, value, given)
        return value

    def _shown(self, item, out, value, given):
        """What a statement that ran shows of its result: into ``out``."""
        ns = self.ns
        if given:
            # What the names it binds are now, for the page to show: each
            # name as the sheet typesets it, each value as it is displayed.
            names = self._assigned_names(item.nodes[0]) if len(item.nodes) == 1 else []
            if names:
                out["given"] = [{"name": n, "latex": self._name_latex(n), "value": self._format(ns[n])} for n in names]
        elif item.mode == "eval":
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

    def run_item(self, item, use_memo=True, given=False):
        """Result for one item — replayed if nothing it reads has changed.

        ``given`` runs a statement of a region that holds values given
        by another sheet, which the page draws by itself: what the
        statement binds is said name by name (``given``: ``[{name,
        latex, value}]``), as the kernel has it.  (The statement has no
        typeset form: it is a call with text in it.)
        """
        if item.kind == "comment":
            return {"t": "comment", "text": item.src, "gap": item.gap,
                    "lines": [item.first, item.last], "h": _digest(item.src)}
        base = {"t": "stmt", "src": item.src, "lines": [item.first, item.last],
                "gap": item.gap}
        if item.error is not None:
            base.update(error=item.error, outputs=[])
            base["h"] = _digest([item.src, item.error])
            return base

        key = (item.key, given, _state_token())
        # A statement that reads outside the sheet: what was kept of it
        # stands, while what it reads of the sheet is what it was.
        # (``outside``: what it reads out there; nothing, for a
        # calculation; ``None``: it is run on every pass.)
        outside = ident = None
        if use_memo and item.kind == "stmt":
            outside = self._outside(item)
            ident = self._ident(item)
            record = self.kept.get(ident)
            # (Whether a statement reads the day's currency rates shows only
            # when it runs: one that did is taken at its record's word —
            # if it has a currency in hand at all.)
            if record is not None and (outside or (record.get("why") == ["rates"] and self._has_currency(item))):
                out = self._replay(item, record, given)
                if out is not None:
                    self.reused += 1
                    return out
        memoizable = (use_memo and item.kind == "stmt" and not item.volatile and outside is not None
                      and not outside and not self._impure(item))
        if memoizable:
            for entry in self.memo.candidates(key):
                # (A result that holds a reading of the day's rates is good
                # only while that reading is kept: asked for anew, it is not.)
                held = entry.result.get("kept")
                if self._reusable(entry) and (held is None or held["id"] in self.kept):
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
        state_before = key[-1]
        if memoizable or outside:
            inputs = {name: fp.of_name(name) for name in item.reads}
            seen = {name: ns.get(name, MISSING) for name in item.reads}
        figures = _open_figures()
        before = dict(ns)
        rates, value = _currencies._reads, None
        t0 = time.perf_counter()
        try:
            if item.kind == "magic":
                self._run_magic(item, out)
            else:
                value = self._execute(item, out, given)
        finally:
            ms = round((time.perf_counter() - t0) * 1000, 2)
            self.executed += 1
        if item.seeds:
            self.seeded = True
        if memoizable and _currencies._reads != rates:
            outside = {"rates"}                 # (it read the day's rates: its result is of that day)
        if outside and "error" not in out and _state_token() == state_before and _open_figures() == figures:
            record = self._keep(ident, item, out, outside, inputs, seen, before, value)
            if record is not None:
                out["kept"] = {"id": ident, "at": record["at"], "why": record["why"]}
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

        A region marked ``given`` holds values from another sheet, which
        the page shows by itself: its reply says what each of its names
        is now (see ``run_item``).

        ``fresh`` forgets every earlier result first ("recalculate
        everything").  ``reuse: false`` runs every statement without
        consulting or touching what is remembered — the reference run
        the tests compare an incremental pass against.

        What statements read outside the sheet is kept (``_keep``).
        ``kept`` is what the page has of that — from the sheet's file —
        for a kernel that has just come: the records, which are what
        there is from then on.  ``anew`` names records that are to be
        read again.  A region's reply carries the records made in this
        pass (``kept``: id → record), and ``done`` the ids of all that
        there are now (``kept``): the page holds what the kernel holds.
        """
        seq = request.get("seq")
        self.pass_no += 1
        self.executed = self.reused = 0
        self.calculated = None                  # (until this pass has got to its end)
        if "kept" in request:
            # What the page has of what was read outside the sheet — from
            # the sheet's file — is what there is of it from here on.
            self.kept = {record["id"]: {key: held for key, held in record.items() if not key.startswith("_")}
                         for record in request["kept"] if _record_ok(record)}
        if request.get("fresh"):
            self.memo.clear()
            self.kept.clear()
        for ident in request.get("anew", ()):
            self.kept.pop(ident, None)
        self.seeded, self._met = False, {}
        use_memo = bool(request.get("reuse", True))
        t0 = time.perf_counter()
        self._reset()
        self.interrupted = False
        for region in request.get("regions", ()):
            results, tail = [], []
            self._taken = {}
            started = time.perf_counter()
            try:
                for item in self.parse(region.get("source", "")).items:
                    results.append(self.run_item(item, use_memo, bool(region.get("given"))))
                    if self.interrupted:
                        break
                tail = self._close_region()
            except KeyboardInterrupt:           # between statements
                self.interrupted = True
            rev = _digest([[r["h"], r["lines"], r.get("gap", False)] for r in results] + [tail])
            message = {"op": "region", "seq": seq, "id": region.get("id"), "rev": rev}
            if self._taken:
                message["kept"] = self._taken   # what was read outside the sheet in this pass: the page keeps it, too
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
        if not self.interrupted:
            self.calculated = seq
            if use_memo:
                # What was read for statements that the sheet has no more
                # is let go — but for the last of it.
                met = {f"{text}.{n}" for text, count in self._met.items() for n in range(1, count + 1)}
                loose = [ident for ident in self.kept if ident not in met]
                for ident in loose[:max(0, len(loose) - self.SPARE)]:
                    del self.kept[ident]
        self._publish({
            "op": "done", "seq": seq, "interrupted": self.interrupted,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "executed": self.executed, "reused": self.reused,
            "names": self.user_names(), "kept": list(self.kept),
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

    def op_values(self, request):
        """What the sheet's variables are now, to be kept with its revision
        and offered to other sheets: one ``values`` message, then ``done``.

        For each name, in the order the sheet defines them (a name of
        the toolkit's that the sheet gives a value of its own stands
        where the toolkit has it: first): ``tex`` (the name, typeset),
        ``form`` (the value as text, see :mod:`values`) and ``shown``
        (how it is displayed, in brief) — or ``why``, for a value that
        cannot be offered.

        ``pass`` names the calculation these are the values of: the
        ``seq`` of the last pass that got to its end — ``None`` in a
        kernel that has calculated nothing, or was stopped at it.  The
        page keeps values only of the pass it knows the sheet by.
        """
        import keyword

        from . import values
        seq = request.get("seq")
        listed, room = [], values.SHEET
        for name, value in self.ns.items():
            if not (isinstance(name, str) and name.isidentifier()) or keyword.iskeyword(name):
                continue                        # (what no sheet could define, another sheet could not name)
            if name.startswith("_") or self.baseline.get(name, MISSING) is value:
                continue
            if isinstance(value, (types.ModuleType, type)):
                continue                        # (what a sheet imports for itself is no variable of it)
            entry = {"name": name, "tex": self._name_latex(name)}
            try:
                form = values.dump(value, self.fp._base)
                if len(form) > room:
                    raise values.NotOffered("more than a sheet hands on")
            except values.NotOffered as why:
                entry["why"] = str(why)
            else:
                room -= len(form)
                entry.update(form=form, shown=self._brief(value))
            listed.append(entry)
        self._publish({"op": "values", "seq": seq, "values": listed, "toolkit": _toolkit_version(),
                       "pass": self.calculated})
        self._publish({"op": "done", "seq": seq})

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
            "toolkit": _toolkit_version(),
        })
        self._publish({"op": "done", "seq": seq})


def _toolkit_version() -> str:
    """The toolkit's version, as installed ("" where it is not installed as a package)."""
    try:
        from importlib.metadata import version
        return version("engineering-dsl")
    except Exception:
        return ""


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
