"""Explicit, unit-checked conversion to native NumPy numbers."""
import numpy as np
from .sigfig import Sig


def as_numeric(value, *, unit=None, dtype=float):
    """Return a native scalar/array, deliberately dropping Sig metadata.

    Unit-bearing values require an explicit matching unit, e.g.
    as_numeric(readings, unit=mV). Mixed unitless/unit-bearing data is rejected.
    The dtype controls precision; use mpmath directly for arbitrary precision.
    """
    while isinstance(unit, Sig):
        unit = unit.value
    if unit is not None and not hasattr(unit, "dimensions"):
        raise TypeError("unit must be a physical unit such as V or mm")
    if np.dtype(dtype).hasobject:
        raise TypeError("as_numeric requires a native numeric dtype")
    if np.dtype(dtype).kind not in "biufc":
        raise TypeError("as_numeric requires a numeric dtype")

    def convert(item):
        while isinstance(item, Sig):
            if getattr(item, "_stripped_unit", None):
                raise TypeError("symbolic unit metadata requires explicit physical conversion")
            item = item.value
        if isinstance(item, (list, tuple)):
            return [convert(part) for part in item]
        if hasattr(item, "tolist") and not hasattr(item, "dimensions"):
            return convert(item.tolist())
        if hasattr(item, "dimensions"):
            if unit is None:
                raise TypeError("unit-bearing values require unit=...")
            if item.dimensions != unit.dimensions:
                raise TypeError("value and requested unit have different dimensions")
            return item.value / unit.value
        if unit is not None:
            raise TypeError("cannot mix dimensionless values with a requested physical unit")
        if hasattr(item, "low") or hasattr(item, "code"):
            raise TypeError("intervals and currencies require explicit scalar conversion")
        return item

    result = np.asarray(convert(value), dtype=dtype)
    return result.item() if result.ndim == 0 else result
