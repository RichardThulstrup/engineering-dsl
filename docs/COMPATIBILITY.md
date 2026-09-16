# Engineering DSL compatibility update

## Python compatibility and migration

**The default now uses `=` for assignment and `==` for comparison.** Engineering
notation (`:=`, units, superscripts, and the other DSL operators) remains
available. Use `x ≡ 2` (equivalent to `Eq(x, 2)`) to create a symbolic equation,
for example `solve(x² ≡ 4, x)`. Ordinary lists and bracket indexing retain Python semantics.
Use `Matrix([[1, 2], [3, 4]])` when you explicitly want a symbolic matrix.

The comparison glyphs `﹦` (U+FE66), `＝` (U+FF1D), and `≟` (U+225F) are
aliases for `==` in DSL cells, in both default and legacy modes:

```text
R_test = 220 Ω
if R_test ≟ 220 Ω:
    pp("Matched")
pp(2 + 2 ﹦ 4, 2 + 2 ＝ 4)   # True, True
```

Plain `=` still assigns in the default mode. These glyphs behave like `==`
for symbolic values too; use `≡` or `Eq(...)` to construct a symbolic equation.
Strings and comments retain the original glyphs. `%%python` cells and ordinary
Python imports bypass DSL rewriting and require normal Python operators.

The `≡` operator (U+2261) is an infix alias for `Eq(lhs, rhs)` in both DSL modes:

```text
symbols: x, y
quadratic = x² ≡ 4
pp(solve(quadratic, x))                         # [-2, 2]
pp(solve([x + y ≡ 3, x − y ≡ 1], [x, y]))     # {x: 2, y: 1}
```

Arithmetic belongs to each side of the equation, so `x + 1 ≡ 2*y` means
`Eq(x + 1, 2*y)`. Use a list for multiple equations: `[a ≡ b, b ≡ c]`.
The alias preserves SymPy's normal evaluation: `2 ≡ 2` simplifies to true;
use `Eq(lhs, rhs, evaluate=False)` when an unevaluated equation is needed.
It is a DSL convention for symbolic equations, not an identity/congruence test.

The default still wraps numeric literals in `Sig` to track significant figures.
For completely ordinary Python—including native numbers and no protected-name
checks—put `%%python` on the first line of a cell:

```python
%%python
import numpy as np
solution = np.linalg.solve([[2, 0], [0, 2]], [4, 6])
```

This executes in the **same notebook namespace**, so values remain available in
subsequent cells. It bypasses the DSL; it does not remove metadata from objects
created in earlier cells. Other IPython cell magics are left to IPython. Cells
containing line magics or shell escapes are also passed through unchanged; put
DSL calculations in a separate cell.

For an existing notebook using mathematical `=` or automatic matrix literals,
add the following to its initial setup cell:

```python
from utils.Engineer import *
set_syntax_mode("legacy")
```

This selects the old notation for subsequent cells. Switch back with
`set_syntax_mode("python")`. A cell can override the selection without changing
later cells:

```python
%%dsl legacy
M := [[1, 2], [3, 4]]
check := (2 + 2 = 4)
```

`%%dsl python` selects the new DSL assignment/list semantics for one cell.
Changing the mode inside a cell takes effect on the **next** cell, because the
whole current cell is transformed before execution. Existing saved notebooks
are not rewritten automatically. Restart the kernel after updating the toolkit.

### Matrices and unit grids

The example notebooks use the default mode. Construct matrices explicitly:

```text
M = Matrix([[1, 2], [3, 4]])
pp(M.det(), M⁻¹, M², Mᵀ)
pp(M₀͵₁)                     # 2; same as M[0, 1]
M₁͵₀ := 9                    # mutates M in place
pp(M ▸ hex)                  # display only; M keeps numeric entries
```

On explicit SymPy matrices, single indices (`M₀` or `M[0]`) use native flat
indexing; use `M.row(0)` to select a row. Negative indices work as in Python.
Legacy automatic matrices retain their historical row-indexing convention.
Nested lists retain list operations; `[[1, 2], [3, 4]] N` is a unit array for
elementwise arithmetic. For linear algebra with measured quantities, choose
and strip units explicitly with `as_numeric(..., unit=...)` before NumPy/SciPy.

### mpmath and native numeric arrays

`mp` is available as a library alias in the default mode. Proton mass remains
available as `m_p` or `mₚ`, including after importing mpmath:

```python
from mpmath import mp
mp.dps = 60
root = mp.sqrt(2)
area = mp.quad(lambda x: x**2, [0, 1])
```

The mpmath conversion protocol accepts dimensionless `Sig` values and drops
their significant-figure metadata. Physical units, intervals, and currencies
need an explicit scalar conversion. For decimal precision beyond native floats,
construct values from strings, e.g. `mp.mpf("1.000000000000000000001")`.

Use `as_numeric` at a NumPy/SciPy boundary. It intentionally produces native
numbers, drops significant-figure metadata, and defaults to float64:

```python
readings = [1.2, 2.3, 3.4] mV
native = as_numeric(readings, unit=mV)
solution = np.linalg.solve(as_numeric([[2, 0], [0, 2]]), as_numeric([4, 6]))
```

Unit-bearing inputs require `unit=...` with matching dimensions. Use
`dtype=complex` for complex data. Use mpmath's conversion protocol rather than
`as_numeric` when arbitrary precision must be retained.

Ordinary `.py` imports are never transformed by default. To explicitly opt in a
DSL-authored module, register its exact fully qualified name before importing:

```python
eng.add_hook(modules=("my_calculations",))
import my_calculations
```

The module must import the DSL runtime helpers it uses. This opt-in applies only
to the named source module; its dependencies retain normal Python loading.
