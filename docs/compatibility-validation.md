# Compatibility implementation validation

## Publication validation — 2026-09-16

- Python 3.14: **190 tests passed**, including four published notebooks and
  seven additional local notebooks, each executed in a fresh kernel. A clean
  checkout discovers only the notebooks included in that checkout.
- VS Code: **4 Node tests passed**; earlier editor integration checks verified
  diagnostics, Unicode completions, kernel execution, plots, and preservation
  of Pylance for ordinary Python.
- Python wheel and VSIX packaging succeeded. The Windows x64 symbol palette
  built with zero warnings or errors.
- Browser checks verified typeset math in standalone Voilà and the JupyterLab
  server extension after excluding the incompatible KaTeX plugin.
- The Python suite reports 12 upstream mpmath deprecation warnings and one
  Windows ZeroMQ event-loop compatibility warning. No tests failed or skipped.

These checks include the matrix, import-hook, equality glyph, symbolic equation,
VS Code kernel, and syntax-checker regressions. Earlier results below record
intermediate stages of the implementation.

## Notebook and matrix validation — 2026-09-16

Applied in `engineering-dsl`; restart the Jupyter kernel before running the
updated examples. This follow-up does not deploy to the separate `Engineering`
copy mentioned in the earlier validation below.

- Python 3.14: **141 tests passed**, including all 11 root-level notebooks
  executed end to end in separate fresh Jupyter kernels. Plots and rich display
  execute as well; currency checks use deterministic offline fallback rates.
- Examples now use explicit `Matrix(...)`, `==` comparisons and `Eq(...)`
  equations in the default mode. Corresponding manual text was migrated too.
- Explicit matrices support Unicode element reads and in-place writes while
  preserving native SymPy flat and tuple indexing. Regressions cover exact
  determinants, inverse, powers, transpose, products, negative indices,
  bounds, symbolic entries, sparse and immutable matrices, and unit arrays.
- Notebook matrix display honors radix tags and left alignment for explicit
  matrices. Ordinary Python imports and `%%python` cells retain native behavior.
- Edited code cells have their obsolete saved outputs cleared. Notebook tests
  read and execute copies without rewriting the user's notebook files.

Reproduce with `python -m pytest -q`; notebook-only validation is
`python -m pytest tests/test_notebook_examples.py -q`.

## Earlier runtime compatibility validation

The approved compatibility changes are applied in both `engineering-dsl` and
a separate local development copy. Restart the Jupyter kernel before using them.

- Python 3.13: 122 tests passed.
- Python 3.14: 120 tests passed; SciPy and pandas import checks skipped because
  those optional packages are not installed in that environment.
- Both deployed copies passed fresh IPython checks for mpmath, units, native
  NumPy cells, per-cell legacy mode, and persistent syntax-mode selection.
- A wheel built without network access included the new modules and passed
  import, constant, mpmath, and compiled-extension checks after extraction into
  a temporary installation directory.
- A 100-line unit-assignment microbenchmark improved from 67.42 ms to 58.43 ms
  in legacy mode (about 13%). These are local timings, not a speed guarantee.
- Torque, E96, and testranges notebook code cells passed legacy-mode source
  transformation checks. Notebooks were not executed or rewritten.

The new modules are `utils/runtime.py` (scoped notebook/module integration) and
`utils/numeric.py` (explicit dimensional conversion). The core notebook runtime
no longer installs the global ideas finder. The separate legacy `i_mul_fys`
helper and its ideas dependency were retained; the full DSL uses the isolated
runtime instead.

Migration instructions: [COMPATIBILITY.md](COMPATIBILITY.md).
