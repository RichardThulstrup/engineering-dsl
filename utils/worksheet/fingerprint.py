"""Value fingerprints for incremental recalculation.

When a worksheet changes, most statements see exactly the values they
saw last time and need not run again.  "The same values" is decided here:
:class:`Fingerprinter` reduces an object to a plain comparable structure
that captures everything a statement could observe about it.

The rule that keeps this sound is *refuse when unsure*.  A type whose
state cannot be read off reliably (a matplotlib figure, a file, a user
class) has no fingerprint — ``None`` — and a statement touching such a
value is simply run again every time.
"""
from __future__ import annotations

import builtins
import datetime
import decimal
import fractions
import functools
import hashlib
import sys
import types

from ..sigfig import Sig

__all__ = ["Fingerprinter", "MISSING"]

MISSING = object()          # stands for "this name is not bound"

# How much structure one fingerprint may walk before the value counts as
# too large to track.  Generous for engineering data, yet it bounds the
# cost of discovering that something is not plain data after all.
_BUDGET = 200_000

_VALUE_TYPES = (datetime.date, datetime.time, datetime.timedelta, datetime.tzinfo,
                fractions.Fraction, decimal.Decimal, range, slice)

# Pure-data classes from these packages are walked attribute by attribute.
_PLAIN_DATA_MODULES = ("utils.", "forallpeople")


class _Opaque(Exception):
    """Raised inside a walk when a value has no reliable fingerprint."""


def _plain_module(name: str) -> bool:
    return name == "forallpeople" or name.startswith(_PLAIN_DATA_MODULES)


class Fingerprinter:
    """Fingerprints values living in the worksheet namespace ``ns``.

    ``baseline`` is the namespace as it stood before any region ran (the
    toolkit's units, constants and functions).  Those objects never
    change, so one of them is identified by name alone — which also keeps
    the walk from descending into the toolkit's own internals.
    """

    def __init__(self, ns: dict, baseline: dict):
        self.ns = ns
        self._base = {id(v): k for k, v in baseline.items()}
        self._budget = 0
        self._stack: set = set()
        self.mutable = False
        self.parts = None           # a list, where a caller wants what can change in place

    def __call__(self, obj):
        """The fingerprint of ``obj``, or ``None`` if it has none.

        Afterwards :attr:`mutable` tells whether the value contains
        anything that can change in place (a list, an array, a matrix).
        Two such values that compare equal are still two objects — code
        can tell them apart by modifying one — so callers treat a mutable
        value as "the same" only when it is the very same object.

        A caller that sets :attr:`parts` to a list finds every such part
        in it afterwards, as far as the walk got.
        """
        self._budget = _BUDGET
        self._stack.clear()
        self.mutable = False
        try:
            return self._fp(obj)
        except (_Opaque, RecursionError):
            return None
        except Exception:
            # A misbehaving __repr__/__iter__ must not break recalculation;
            # it just means this value is not tracked.
            return None

    def of_name(self, name: str):
        """``(fingerprint, object)`` for a name; the object is ``None``
        unless the value is mutable (see :meth:`__call__`)."""
        obj = self.ns.get(name, MISSING)
        if obj is MISSING:
            return (("builtin", name) if hasattr(builtins, name) else ("missing",)), None
        fp = self(obj)
        return fp, (obj if self.mutable or fp is None else None)

    # ------------------------------------------------------------------
    def _fp(self, obj):
        t = type(obj)
        # Exact primitives first: equal values must agree whatever object
        # happens to hold them (small ints and '' are shared singletons).
        if obj is None or t is bool:
            return ("k", obj)
        if t is int:
            return ("i", obj)
        if t is float:
            return ("f", repr(obj))
        if t is str:
            return ("s", obj)
        if t is complex:
            return ("c", repr(obj))
        if t is bytes:
            return ("y", obj)
        name = self._base.get(id(obj))
        if name is not None:
            return ("B", name)
        self._budget -= 1
        if self._budget < 0:
            raise _Opaque
        oid = id(obj)
        if oid in self._stack:
            if isinstance(obj, types.FunctionType):
                return ("recursion",)            # f calls g calls f
            raise _Opaque                        # a self-containing structure
        self._stack.add(oid)
        try:
            return self._compound(obj, t)
        finally:
            self._stack.discard(oid)

    def _changeable(self, obj):
        self.mutable = True
        if self.parts is not None:
            self.parts.append(obj)

    def _seq(self, items):
        return tuple(self._fp(x) for x in items)

    def _compound(self, obj, t):
        fp = self._fp
        if t is Sig:
            return ("Sig",) + self._slots(obj, t)
        if isinstance(obj, tuple):               # includes namedtuples (Physical)
            return ("t", t.__module__, t.__qualname__, self._seq(obj))
        if isinstance(obj, list):
            self._changeable(obj)
            return ("l", t.__qualname__, self._seq(obj))
        if isinstance(obj, dict):
            self._changeable(obj)
            return ("d", t.__qualname__, tuple((fp(k), fp(v)) for k, v in obj.items()))
        if isinstance(obj, (set, frozenset)):
            if isinstance(obj, set):
                self._changeable(obj)
            return ("S", t.__qualname__, frozenset(fp(x) for x in obj))
        if isinstance(obj, types.FunctionType):
            return self._function(obj)
        if isinstance(obj, types.MethodType):
            return ("bm", fp(obj.__self__), fp(obj.__func__))
        if isinstance(obj, types.ModuleType):
            return ("mod", obj.__name__)
        if isinstance(obj, type):
            if obj.__module__ == self.ns.get("__name__", "__main__"):
                raise _Opaque                    # a class defined in the worksheet
            return ("cls", obj.__module__, obj.__qualname__)
        if isinstance(obj, (types.BuiltinFunctionType, types.MethodDescriptorType,
                            types.WrapperDescriptorType, types.MethodWrapperType)):
            owner = getattr(obj, "__self__", None)
            return ("bf", getattr(obj, "__module__", None), obj.__qualname__,
                    None if owner is None or isinstance(owner, types.ModuleType) else fp(owner))
        if isinstance(obj, functools.partial):
            return ("partial", fp(obj.func), fp(obj.args), fp(obj.keywords))
        if isinstance(obj, _VALUE_TYPES):
            return ("r", t.__module__, t.__qualname__, repr(obj))

        module = t.__module__ or ""
        np = sys.modules.get("numpy")
        if np is not None:
            if isinstance(obj, np.ndarray):
                return self._array(obj, t)
            if isinstance(obj, np.generic):
                return ("n", obj.dtype.str, fp(obj.item()))
        sympy = sys.modules.get("sympy")
        if sympy is not None and isinstance(obj, (sympy.MatrixBase, sympy.Basic)):
            return self._sympy(obj, t, sympy)       # also the DSL's Matrix subclass
        if _plain_module(module):
            return ("o", module, t.__qualname__) + self._slots(obj, t)
        raise _Opaque

    def _slots(self, obj, t):
        """State of a plain-data object: every slot plus its ``__dict__``.

        Read with ``object.__getattribute__`` so that a class which
        forwards unknown attributes to a wrapped value (``Sig`` does)
        cannot answer for a slot that is simply unset.
        """
        state = []
        for klass in t.__mro__:
            slots = klass.__dict__.get("__slots__", ())
            for slot in (slots,) if isinstance(slots, str) else slots:
                try:
                    value = object.__getattribute__(obj, slot)
                except AttributeError:
                    state.append((slot, ("unset",)))
                else:
                    state.append((slot, self._fp(value)))
        try:
            d = object.__getattribute__(obj, "__dict__")
        except AttributeError:
            d = None
        if d:
            state.append(("__dict__", self._fp(d)))
        return tuple(state)

    def _array(self, obj, t):
        self._changeable(obj)
        extra = self._slots(obj, t) if type(obj).__module__ != "numpy" else ()
        if obj.dtype.hasobject:
            return ("a", t.__qualname__, obj.shape, self._seq(obj.flat), extra)
        digest = hashlib.blake2b(obj.tobytes(), digest_size=16).digest()
        return ("a", t.__qualname__, obj.dtype.str, obj.shape, digest, extra)

    def _sympy(self, obj, t, sympy):
        if isinstance(obj, sympy.MatrixBase):
            self._changeable(obj)
            extra = self._fp(getattr(obj, "_dsl_radix", None))   # ``M ▸ hex`` display tag
            return ("M", t.__qualname__, obj.shape, self._seq(obj), extra)
        # Immutable expression trees; srepr is their exact structure.
        return ("sym", sympy.srepr(obj))

    # ---- functions -----------------------------------------------------
    def _code(self, code):
        consts = tuple(self._code(c) if isinstance(c, types.CodeType) else self._fp(c)
                       for c in code.co_consts)
        return ("code", code.co_code, consts, code.co_names, code.co_varnames,
                code.co_freevars, code.co_cellvars, code.co_argcount,
                code.co_kwonlyargcount, code.co_flags)

    @staticmethod
    def _global_names(code, out):
        out.update(code.co_names)
        for c in code.co_consts:
            if isinstance(c, types.CodeType):
                Fingerprinter._global_names(c, out)
        return out

    def _function(self, fn):
        if fn.__globals__ is not self.ns:
            # A library function is fixed if it is what its module exports
            # under its own name; a generated one (sympy.lambdify) is
            # identified by its code instead.
            owner = sys.modules.get(fn.__module__ or "")
            target = owner
            for part in (fn.__qualname__ or "").split("."):
                target = getattr(target, part, None)
            if target is fn:
                return ("F", fn.__module__, fn.__qualname__)
            return ("fx", self._code(fn.__code__), self._fp(fn.__defaults__),
                    self._closure(fn))
        # Defined in the worksheet: it reads its globals when *called*, so
        # the values of the names it refers to are part of what it is.
        names = self._global_names(fn.__code__, set())
        globs = tuple((n, self._fp(self.ns[n])) for n in sorted(names) if n in self.ns)
        return ("fn", self._code(fn.__code__), self._fp(fn.__defaults__),
                self._fp(fn.__kwdefaults__), self._closure(fn), globs)

    def _closure(self, fn):
        out = []
        for cell in fn.__closure__ or ():
            try:
                out.append(self._fp(cell.cell_contents))
            except ValueError:                   # an empty cell
                out.append(("unset",))
        return tuple(out)
