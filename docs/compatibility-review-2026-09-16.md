# Engineering DSL compatibility and correctness review

Historical review from 16 September 2026, before the compatibility repairs.
See [current behavior](COMPATIBILITY.md) and [validation](compatibility-validation.md)
for the implemented changes. Findings below describe the earlier code; line numbers
refer to that snapshot.

**Conclusion:** the `=` rewrite contributes to incompatibility, but removing it alone will not solve the problem. Import interception, numeric wrappers, protected names, and automatic container rewriting independently change Python behavior. Restore ordinary Python semantics at library boundaries before optimizing individual rewrites.

This report records the pre-change findings. The approved implementation now provides isolated notebook imports, Python/legacy syntax modes, native indexing, numeric conversion adapters, and AST optimizations. See [migration instructions](../README.md#python-compatibility-and-migration).

**Scope and evidence.** Inspected the source-transform pipeline, numeric wrapper, startup and constants, matrix access, packaging, tests, and the highlighting/export integration. Executed isolated import and numeric probes and a temporary installation-layout reproduction. The main environment was Python 3.13, ideas 0.2.0, mpmath 1.3.0, SymPy 1.14.0, NumPy 2.5.3, and IPython 9.17.1. The previous fix also passed four compatibility tests on Python 3.14 with ideas 0.1.5. This is a focused runtime review, not an exhaustive audit of the WinUI application, every numerical formula, or browser rendering.

## Confirmed findings

**1. P1 — Global import interception can select the wrong loader, independently of syntax.**

Source: [hook registration](../utils/circuit_dsl.py), [source scope guard](../utils/circuit_dsl.py).

In a fresh process, `import zmq` succeeds. After `import utils.Engineer`, it fails with “Attempting to import zmq Cython backend, which has not been compiled.” Removing only the registered ideas hook restores the import.

The installed ideas finder selects `zmq/backend/cython/_zmq.py` with `IdeasLoader`; Python's normal `PathFinder` selects `_zmq.pyd` with `ExtensionFileLoader`. The DSL's source guard returns third-party source unchanged, but that happens after the wrong file has already been selected. Changing the equality rule cannot fix this.

Fresh imports of `scipy.linalg`, `pandas`, and `matplotlib.pyplot` succeeded in the tested environment both before and after activating the DSL. This is not a claim that all their APIs accept DSL values. Jupyter may preload zmq before the DSL, which can hide the loader problem in an already-running kernel.

**Repair:** use a notebook input transformer for notebook cells; restrict any import finder to explicitly designated DSL modules. Leave normal modules, packages, and compiled extensions to Python's loader. Avoid depending on a filename blacklist or import order.

**2. P1 — Import scope both rewrites ordinary modules and excludes the project's own DSL module when installed.**

Source: [path exclusions](../utils/circuit_dsl.py), [basename exclusions](../utils/circuit_dsl.py).

An ordinary local module containing `answer = 42` fails when imported after `Engineer`: its assignment becomes `_eq(answer, _S(42, _INF))`, and `_eq` is unavailable. Conversely, the same source with a filename ending in `symbolic.py` bypasses rewriting solely because of its basename.

A temporary copy of the package placed under `site-packages/utils` fails importing `Engineer` at `calc_symbols.py:48`, where `prefix_p := ...` is left as invalid Python. This was a copy-based installation-layout test, not a wheel installation. The path filter excludes all of `site-packages`, including the toolkit's own DSL-authored module. This contradicts the documented ordinary-install path.

**Repair:** make implementation modules, especially `calc_symbols.py`, valid Python. Transform user-authored DSL files only through explicit opt-in, retaining a documented legacy import mode if necessary.

**3. P1 — mpmath has three independent conflicts.**

Sources: [proton-mass alias](../utils/calc_symbols.py), [name protection](../utils/circuit_dsl.py), [equality rewriting](../utils/circuit_dsl.py), [numeric wrapping](../utils/circuit_dsl.py).

| Input in a DSL cell | Observed behavior | Cause |
|---|---|---|
| `import mpmath as mp` | Protected-name SyntaxError | `mp` already means proton mass |
| `import mpmath as mpm` | Works | Avoids the alias conflict |
| `mpm.mp.dps = 50` | Executes a comparison; precision remains 15 | Bare assignment becomes `_eq(...)` |
| `mpm.mp.dps := 50` | Precision becomes 50 | DSL assignment works |
| `mpm.sqrt(2)` | `TypeError: cannot create mpf from 2` | The apparent integer is a `Sig` |
| `mpm.quad(lambda x: x**2, [0, 1])` | Cannot create mpf from the lower bound | Wrapped literals reach mpmath |
| `mpm.findroot(lambda x: x**2 - 2, 1)` | Cannot create mpf from the initial value | Wrapped literals reach mpmath |
| `mpm.sqrt(mpm.mpf("2"))` | Works at 50 digits | A native mpmath argument is supplied |

A currently working example is:

```python
import mpmath as mpm
mpm.mp.dps := 50
mpm.sqrt(mpm.mpf("2"))
```

This is a limited workaround, not general interoperability for callbacks, solvers, or mixed DSL arithmetic.

**Repair:** provide a plain-Python cell mode and an explicit conversion boundary. The installed mpmath implementation supports `_mpmath_(prec, rounding)`; evaluate a precision-aware implementation on `Sig` for supported dimensionless values. Do not silently convert dimensional quantities or intervals to floats, and do not use binary float as an intermediate for arbitrary-precision values. Converting to mpmath also needs a documented policy for losing significant-figure metadata. Keep `m_p` as an unambiguous physical constant name; avoid globally reserving common library aliases in a Python-compatible mode.

**4. P1 — Native tuple indexing is changed into chained indexing, including writes.**

Sources: [AST subscript rewriting](../utils/circuit_dsl.py), [read dispatcher](../utils/circuit_dsl.py), [write dispatcher](../utils/circuit_dsl.py).

With this ordinary Python object supplied to a cell:

```python
d = {(1, 2): "pair", 1: {2: "nested"}}
```

DSL `d[1, 2]` returns `"nested"`; Python returns `"pair"`. DSL `d[1, 2] := "changed"` updates the nested dictionary while leaving the tuple-key entry untouched. This can silently mutate the wrong data. NumPy advanced indexing with `array[[0, 1], [1, 0]]` also fails in the tested DSL path.

**Repair:** preserve native bracket operations for ordinary Python objects. Keep the DSL's multi-index convention limited to its own matrices or explicit subscript-glyph operations. Distinguish a single tuple key from sequential indices in the runtime API.

**5. P2 — Fractional values are silently accepted as Python indices.**

Source: [Sig.__index__](../utils/sigfig.py).

`[10, 20, 30][1.9]` evaluates to `20`; `range(2.9)` produces `range(0, 2)`. `Sig.__index__` calls `int(value)`, so it truncates instead of enforcing Python's integer-index protocol.

**Repair:** delegate to `operator.index(value)` and preserve TypeError for non-integer values. Test integers, NumPy integers, fractional floats, whole-valued floats, slices, and range arguments.

**6. P2 — Mathematical equality depends on the spelling of its left operand.**

Sources: [bare-name/keyword heuristic](../utils/circuit_dsl.py), [existing equality tests](../tests/test_math_gaps.py).

After `symbols: x`, `(x = 2)` produces invalid Python, while `(x + 0 = 2)` produces `Eq(x, 2)`. Similarly, `solve(x = 2, x)` fails because `x = 2` is treated as a keyword argument. Existing tests cover compound expressions such as `x² = 4`, so they miss this ambiguity.

**Repair:** in a Python-compatible syntax, use `=` for assignment, `==` for ordinary comparison, and `Eq(x, 2)` for a symbolic equation. Verified that `solve(Eq(x, 2), x)` works through the current DSL. SymPy's `==` is structural equality; it is not a replacement for constructing an equation.

**7. P2 — NumPy interoperability and performance are limited by implicit object arrays.**

Sources: [Sig.__array__](../utils/sigfig.py), [automatic matrix conversion](../utils/circuit_dsl.py).

`np.linspace(0, 1, 5)` succeeds but produces an object array containing `Sig` values. `np.linalg.solve([[2, 0], [0, 2]], [4, 6])` fails because the inputs have object dtype, compounded by automatic SymPy matrix promotion. `Sig.__array__` also lacks NumPy's `copy` keyword support; `np.array(Sig(2), copy=False)` emits a protocol warning and fails. A request forbidding copies can legitimately fail, but the missing protocol argument should still be corrected.

In one local 10,000-element `np.sin` benchmark, native float64 took about 0.054 ms and Sig/object values took about 9.26 ms, roughly 170 times slower. The arrays were constructed before timing. They carry different metadata and dispatch behavior: this is evidence for an explicit fast numeric mode, not justification for silently discarding units or precision tracking.

**Repair:** offer explicit native numeric conversion with a unit policy and dtype, preserve ordinary lists in Python mode, and implement the modern NumPy array protocol. Verify linear algebra and advanced indexing as well as elementwise operations.

**8. P2 — The documented full-suite command does not provide a reliable gate.**

Sources: [unconditional exit](../tests/test_interval_dots.py), [second unconditional exit](../tests/test_nested_parens.py), [test dependencies](../pyproject.toml).

The earlier verification found that `python -m pytest tests/` fails during collection: the active import hook rewrites later test modules under ideas 0.2.0; under the older environment, a standalone test calls `sys.exit(0)` during collection. Running files separately produced 95 existing pytest passes plus four hook-compatibility passes and two successful standalone scripts. Thus those passes do not mean the one-command suite works.

**Repair:** isolate hook integration tests in fresh processes, convert standalone scripts to collected tests, add a development test dependency group, and test a supported dependency matrix. Include installed-package import, import order, compiled extensions, and notebook sessions. Dependency compatibility should be explicit rather than relying on unrestricted upgrades of `ideas`.

## Optimization opportunities

1. **Skip the matrix/index AST pass when the transformed source contains no `[` character.** A temporary in-process experiment reduced the time for a 100-line unit-assignment cell from about 57 ms to 49 ms, approximately 15%. This experiment did not change project files. Check traceback behavior as well as result equivalence before adoption: avoiding `ast.unparse` also preserves formatting.
2. **Parse once for final AST operations.** Matrix conversion currently parses, walks, fixes locations, and unparses; protected-name checking then parses and walks again. Share the final AST and unparse only if a transformation actually changes it. The profiler identified both passes as material costs.
3. **Preserve a native NumPy path.** The object-array cost is much larger than individual regex costs for numerical workloads. Separate unit/sig-fig computations from arrays intentionally prepared for native library kernels.
4. **Consolidate the pipeline after correctness repairs.** The central transformer is over 8,400 lines, with many ordered full-source rewrites and comments that no longer match behavior. Examples include the matrix pass described as list-of-lists-only despite wrapping a broader set, and a Sig arithmetic comment claiming `_sympy_` raises where the implementation now supports conversion. Extract passes behind behavioral tests; avoid a wholesale rewrite before those tests exist.

Timings are local microbenchmarks, not stable performance guarantees. They establish priorities rather than release targets.

## Recommended change sequence

1. Isolate notebook transformation from ordinary module imports; fix the constants-module installation path and establish a working one-command test suite.
2. Correct native tuple indexing and integer-index validation, with regressions for silent wrong results.
3. Add a plain-Python cell mode for direct use of external math libraries, then explicit numeric adapters. Preserve dimensional checks and high precision.
4. Introduce a Python-compatible syntax mode: `=` assigns, `==` compares, `Eq(...)` creates equations. Keep `:=` as an optional DSL assignment spelling where unambiguous. Retain the current mathematical-equality behavior as a documented legacy mode for existing notebooks, rather than silently changing old equations into assignments.
5. Apply the measured AST optimizations and then profile representative notebooks again.

Reverting `=` is a reasonable direction for predictable Python integration, but it should be an explicit migration choice. It cannot address the independent import-loader, alias, numeric-wrapper, container, and indexing problems found here.
