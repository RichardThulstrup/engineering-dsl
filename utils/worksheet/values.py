"""Values of a sheet as text: kept with its revision, taken up by other sheets.

A sheet at a revision delivers the values its variables had when it was
saved.  Here a value is written as text that says what it is made of — a
number, a quantity with its unit and its significant figures, an
interval, an array of those — and is read back as the same value: on
another day, in another sheet, on another computer.

The text is data.  Reading it builds values out of the fixed set of
kinds listed below and of nothing else; it names no code and runs none.
A sheet that takes values from another sheet therefore runs nothing of
that sheet, which matters: the other sheet may be someone else's.  (No
pickle, no ``eval``, and no symbolic expressions for that reason —
sympy reads text by evaluating it.)

What can be written: numbers, text, the toolkit's quantities and their
wrappers (``Sig``, a ``Physical`` with its dimensions, ``Range``, values
shown in a unit, a radix, a temperature scale …), lists, tuples, sets,
dicts, numpy arrays and matrices of those — and the toolkit's own units
and constants, by name.  Not written: functions, modules, symbolic
values (a matrix with a symbol in it is one), figures, and anything
larger than a sheet should carry about.

:func:`given` is what an import region of a worksheet calls::

    from utils.worksheet.values import given as _given
    (V_bus, I_max,) = _given('{"V_bus": ..., "I_max": ...}')

so that a sheet with imported values is still an ordinary notebook, and
holds what it was given.
"""
from __future__ import annotations

import base64
import builtins
import datetime
import decimal
import fractions
import json
import re
import sys
import types

__all__ = ["dump", "load", "given", "NotOffered"]

# How much one value may be: parts walked, and characters of its text —
# and how much text all the values are that one sheet hands on.
_PARTS = 50_000
_TEXT = 400_000
SHEET = 4_000_000

_NUMERIC = re.compile(r"[<>|=]?[biufc]\d{1,2}\Z")        # the kinds of numpy array that are numbers


class NotOffered(Exception):
    """This value cannot be written — the message says what it is."""


def _kinds() -> dict:
    """The toolkit's kinds of value that are plain data, by the name a text uses for them."""
    import forallpeople

    from .. import circuit_dsl, currencies, extra_units, sigfig
    kinds = (sigfig.Sig, sigfig._InUnits, sigfig._Radix, sigfig._TempScale, sigfig._DeltaTemp,
             sigfig._PMDisplay, sigfig._HMSDisplay, circuit_dsl._Ratio, circuit_dsl.Range,
             extra_units._DisplayUnit, extra_units._DeltaUnit, currencies.Currency,
             forallpeople.Physical)
    return {f"{kind.__module__}:{kind.__qualname__}": kind for kind in kinds}


def _slots(kind) -> list:
    found = []
    for klass in kind.__mro__:
        slots = klass.__dict__.get("__slots__", ())
        found += [slots] if isinstance(slots, str) else list(slots)
    return found


def _what(obj) -> str:
    """Words for a value that is not offered."""
    if isinstance(obj, (types.FunctionType, types.BuiltinFunctionType, types.MethodType)):
        return "a function"
    if isinstance(obj, types.ModuleType):
        return "a module"
    if isinstance(obj, type):
        return "a class"
    sympy = sys.modules.get("sympy")
    if sympy is not None and isinstance(obj, (sympy.Basic, sympy.MatrixBase)):
        return "a symbolic value"
    return f"a value of the kind {type(obj).__name__}"


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

class _Writer:
    def __init__(self, own):
        self.own = own                  # id of a toolkit object -> its name
        self.left = _PARTS
        self.open = set()
        self.kinds = {kind: name for name, kind in _kinds().items()}
        # A part that is there more than once — the unit of every element
        # of an array — is written once, and named by its number after that.
        self.written = {}               # id of such a part -> its number
        self.parts = []                 # (the parts, kept: an id is an object's only while the object is there)

    def form(self, obj):
        t = type(obj)
        if obj is None or t is bool:
            return obj
        if t is int:
            return ["i", str(obj)]
        if t is float:
            return ["f", repr(obj)]
        if t is str:
            return ["s", obj]
        if t is complex:
            return ["c", repr(obj)]
        name = self.own.get(id(obj))
        if name is not None:
            return ["B", name]
        self.left -= 1
        if self.left < 0:
            raise NotOffered("too large")
        if id(obj) in self.open:
            raise NotOffered("a value that holds itself")
        self.open.add(id(obj))
        try:
            return self._compound(obj, t)
        finally:
            self.open.discard(id(obj))

    def _once(self, obj, made):
        self.written[id(obj)] = len(self.parts)
        self.parts.append(obj)
        return made

    def _compound(self, obj, t):
        form = self.form
        if id(obj) in self.written:
            return ["same", self.written[id(obj)]]
        if t in self.kinds:
            held = {}
            for slot in _slots(t):
                try:
                    held[slot] = form(object.__getattribute__(obj, slot))
                except AttributeError:
                    pass                # a slot that is not set stays unset
            try:
                more = object.__getattribute__(obj, "__dict__")
            except AttributeError:
                more = {}
            return self._once(obj, ["o", self.kinds[t], held, {str(key): form(value) for key, value in more.items()}])
        if t is fractions.Fraction:
            return ["q", str(obj.numerator), str(obj.denominator)]
        if t is decimal.Decimal:
            return ["D", str(obj)]
        if t in (datetime.datetime, datetime.date, datetime.time):
            return ["when", t.__name__, obj.isoformat()]
        if t is datetime.timedelta:
            return ["span", str(obj.days), str(obj.seconds), str(obj.microseconds)]
        if t is list:
            return ["l", [form(item) for item in obj]]
        if t is tuple:
            return ["T", [form(item) for item in obj]]
        if t in (set, frozenset):
            # In the order of its elements' texts, not in the one the set
            # happens to hold them in — which is another one in the next
            # kernel: the same value is the same text on another day.
            if len(obj) > self.left:
                raise NotOffered("too large")
            return ["S" if t is set else "F", [form(item) for item in sorted(obj, key=lambda item: dump(item, self.own))]]
        if t is dict:
            return ["d", [[form(key), form(value)] for key, value in obj.items()]]
        if isinstance(obj, tuple) and t.__module__ == "forallpeople.dimensions" and t.__qualname__ == "Dimensions":
            return self._once(obj, ["dims", [form(item) for item in obj]])
        from .. import circuit_dsl
        if t is circuit_dsl._Polar:
            return ["polar", repr(complex(obj))]
        np = sys.modules.get("numpy")
        if np is not None:
            if t in (np.ndarray, circuit_dsl.CommaArray):
                kind = "comma" if t is circuit_dsl.CommaArray else "plain"
                if obj.dtype.hasobject:
                    return ["A", kind, list(obj.shape), [form(item) for item in obj.flat]]
                if not _NUMERIC.match(obj.dtype.str):
                    raise NotOffered(f"an array of {obj.dtype}")
                self.left -= obj.size // 8
                if self.left < 0:
                    raise NotOffered("too large")
                return ["a", kind, obj.dtype.str, list(obj.shape),
                        base64.b64encode(np.ascontiguousarray(obj).tobytes()).decode("ascii")]
            if isinstance(obj, np.generic) and _NUMERIC.match(obj.dtype.str):
                return ["n", obj.dtype.str, base64.b64encode(obj.tobytes()).decode("ascii")]
        sympy = sys.modules.get("sympy")
        if sympy is not None:
            # sympy's numbers, exactly: an integer, a ratio of two, or a
            # float as the bits it is made of.  (Anything else of sympy's
            # is symbolic.)
            if isinstance(obj, sympy.Integer):
                return ["I", str(int(obj))]
            if isinstance(obj, sympy.Rational):
                return ["Q", str(obj.p), str(obj.q)]
            if isinstance(obj, sympy.Float):
                sign, digits, power, bits = obj._mpf_
                return ["R", str(int(sign)), str(int(digits)), str(int(power)), str(int(bits)), str(int(obj._prec))]
            if t is sympy.Matrix:
                radix = getattr(obj, "_dsl_radix", None)
                return ["M", list(obj.shape), [form(entry) for entry in obj], form(radix)]
        raise NotOffered(_what(obj))


def dump(value, own=None) -> str:
    """``value`` as text, to be kept and read back by :func:`load`.

    ``own`` maps the ``id`` of each of the toolkit's own objects (its
    units and constants) to its name: such an object is written as that
    name.  Raises :class:`NotOffered`, saying what the value is, for one
    that cannot be written.
    """
    try:
        text = json.dumps(_Writer(own or {}).form(value), ensure_ascii=False, separators=(",", ":"))
    except RecursionError:
        raise NotOffered("too large") from None
    if len(text) > _TEXT:
        raise NotOffered("too large")
    return text


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

class _NoValue(Exception):
    """Text that is no stored value — the message says in what way."""


class _Reader:
    def __init__(self, named):
        self.named = named              # name -> the toolkit's own object of that name
        self.kinds = _kinds()
        self.parts = []                 # the parts that may be named again, in the order they were written

    def value(self, form):
        if form is None or isinstance(form, bool):
            return form
        if not isinstance(form, list) or not form or not isinstance(form[0], str):
            raise _NoValue("this is no stored value")
        make = getattr(self, "_read_" + form[0], None)
        if make is None:
            raise _NoValue(f"a stored value of a kind this toolkit does not know: {form[0]!r}")
        return make(*form[1:])

    def _many(self, forms):
        if not isinstance(forms, list):
            raise _NoValue("this is no stored value")
        return [self.value(form) for form in forms]

    @staticmethod
    def _text(text):
        if not isinstance(text, str):
            raise _NoValue("this is no stored value")
        return text

    def _read_i(self, text):
        return int(self._text(text))

    def _read_f(self, text):
        return float(self._text(text))

    def _read_s(self, text):
        return self._text(text)

    def _read_c(self, text):
        return complex(self._text(text))

    def _read_B(self, name):
        found = self.named(self._text(name))
        if found is None:
            raise _NoValue(f"the toolkit here has no {name!r}: these values were stored with another version of it")
        return found

    def _read_o(self, name, held, more):
        kind = self.kinds.get(name) if isinstance(name, str) else None
        if kind is None or not isinstance(held, dict) or not isinstance(more, dict):
            raise _NoValue(f"a stored value of a kind this toolkit does not know: {name!r}")
        made = object.__new__(kind)
        slots = set(_slots(kind))
        for slot, form in held.items():
            if slot not in slots:
                raise _NoValue(f"{kind.__name__} has no {slot!r}")
            object.__setattr__(made, slot, self.value(form))
        if more and not hasattr(made, "__dict__"):
            raise _NoValue(f"{kind.__name__} holds nothing by the name of {next(iter(more))!r}")
        for key, form in more.items():
            object.__getattribute__(made, "__dict__")[str(key)] = self.value(form)
        self.parts.append(made)
        return made

    def _read_same(self, number):
        if type(number) is not int or not 0 <= number < len(self.parts):
            raise _NoValue("this is no stored value")
        return self.parts[number]

    def _read_q(self, above, below):
        return fractions.Fraction(int(self._text(above)), int(self._text(below)))

    def _read_D(self, text):
        return decimal.Decimal(self._text(text))

    def _read_when(self, kind, text):
        kinds = {"datetime": datetime.datetime, "date": datetime.date, "time": datetime.time}
        if kind not in kinds:
            raise _NoValue("this is no stored value")
        return kinds[kind].fromisoformat(self._text(text))

    def _read_span(self, days, seconds, micro):
        return datetime.timedelta(int(self._text(days)), int(self._text(seconds)), int(self._text(micro)))

    def _read_l(self, forms):
        return self._many(forms)

    def _read_T(self, forms):
        return tuple(self._many(forms))

    def _read_S(self, forms):
        return set(self._many(forms))

    def _read_F(self, forms):
        return frozenset(self._many(forms))

    def _read_d(self, pairs):
        if not isinstance(pairs, list) or not all(isinstance(pair, list) and len(pair) == 2 for pair in pairs):
            raise _NoValue("this is no stored value")
        return {self.value(key): self.value(value) for key, value in pairs}

    def _read_dims(self, forms):
        from forallpeople.dimensions import Dimensions
        made = Dimensions._make(self._many(forms))
        self.parts.append(made)
        return made

    def _read_polar(self, text):
        from ..circuit_dsl import _Polar
        return _Polar(complex(self._text(text)))

    @staticmethod
    def _array_kind(kind):
        import numpy as np

        from ..circuit_dsl import CommaArray
        if kind not in ("plain", "comma"):
            raise _NoValue("this is no stored value")
        return np, (CommaArray if kind == "comma" else np.ndarray)

    @staticmethod
    def _shape(shape):
        if not isinstance(shape, list) or not all(type(side) is int and side >= 0 for side in shape):
            raise _NoValue("this is no stored value")
        return tuple(shape)

    def _read_a(self, kind, dtype, shape, data):
        np, cls = self._array_kind(kind)
        if not isinstance(dtype, str) or not _NUMERIC.match(dtype):
            raise _NoValue("this is no stored value")
        made = np.frombuffer(base64.b64decode(self._text(data), validate=True), dtype=np.dtype(dtype))
        return made.reshape(self._shape(shape)).copy().view(cls)

    def _read_A(self, kind, shape, forms):
        np, cls = self._array_kind(kind)
        items = self._many(forms)
        made = np.empty(len(items), dtype=object)
        for k, item in enumerate(items):
            made[k] = item
        return made.reshape(self._shape(shape)).view(cls)

    def _read_n(self, dtype, data):
        import numpy as np
        if not isinstance(dtype, str) or not _NUMERIC.match(dtype):
            raise _NoValue("this is no stored value")
        return np.frombuffer(base64.b64decode(self._text(data), validate=True), dtype=np.dtype(dtype))[0]

    def _read_I(self, text):
        import sympy
        return sympy.Integer(int(self._text(text)))

    def _read_Q(self, above, below):
        import sympy
        return sympy.Rational(int(self._text(above)), int(self._text(below)))

    def _read_R(self, sign, digits, power, bits, precision):
        import sympy
        parts = tuple(int(self._text(part)) for part in (sign, digits, power, bits))
        return sympy.Float._new(parts, int(self._text(precision)))

    def _read_M(self, shape, forms, radix):
        import sympy
        rows, columns = self._shape(shape)
        entries = self._many(forms)
        if len(entries) != rows * columns or not all(isinstance(entry, sympy.Number) for entry in entries):
            raise _NoValue("this is no stored value")
        made = sympy.Matrix(rows, columns, entries)
        radix = self.value(radix)
        if radix is not None:
            made._dsl_radix = self._text(radix)
        return made


def load(text, named=None):
    """The value that :func:`dump` wrote as ``text``.

    ``named`` gives the toolkit's own object for a name (or ``None``);
    without it, names are looked up where the toolkit put them: in the
    notebook's namespace and among the builtins.  Raises ``ValueError``
    for text that is no stored value.
    """
    try:
        return _Reader(named or _toolkit()).value(json.loads(text))
    except _NoValue as said:
        raise ValueError(str(said)) from None
    except (ValueError, TypeError, ArithmeticError, LookupError, RecursionError):
        # (Text that is put together wrongly, whatever tripped over it.)
        raise ValueError("this is no stored value") from None


def _toolkit():
    """Where the toolkit's units and constants are found by name: as they
    were before the sheet defined anything, where a worksheet's engine
    knows that; else in the notebook's namespace and the builtins."""
    try:
        from IPython import get_ipython
        space = get_ipython().user_ns
    except Exception:
        space = {}
    engine = space.get("__edsl_ws__")
    space = getattr(engine, "baseline", space)
    return lambda name: space.get(name, getattr(builtins, name, None))


def given(text):
    """The values an import region was given, in the order of their names.

    ``text`` is JSON: an object with, for each name, the text
    :func:`dump` wrote for its value.  Always a tuple — of one value for
    one name — so that the names can stand before it: ``a, b = given(…)``.
    """
    try:
        forms = json.loads(text)
    except (TypeError, ValueError):
        forms = None
    if not isinstance(forms, dict):
        raise ValueError("these are no stored values")
    named = _toolkit()
    return tuple(load(form, named) for form in forms.values())
