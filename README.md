# Engineering DSL

Engineering math in plain notation, inside Jupyter. One import turns a
notebook cell into something you can write the way you'd write on paper:

```text
V_in := 12.0 V
R_top := 4.7 kΩ
R_bot := 10. kΩ
V_out := V_in · R_bot/(R_top + R_bot)     # 8.2 V

R_eq := 100. Ω ‖ 220. Ω ‖ 470. Ω          # parallel resistors
τ := 10. kΩ · 100. nF                     # → 1.0 ms, units simplify
hyp := √(30.cm² + 40.cm²)                 # → 50 cm
```

It installs an IPython input transformer that rewrites notebook cells before
Python executes them. Ordinary imported Python packages keep their native
loaders and source semantics.

## Features

- **Units everywhere** — powered by
  [forallpeople](https://github.com/connorferster/forallpeople): `12 V / 3 A`
  is `4 Ω`. A literal keeps the unit it was written in (`22735 mm`
  prints `22735 mm`); computed results with no written unit auto-scale
  their SI prefix. Value–unit binding is tight: `12 V / 3 A` parses as
  `(12 V)/(3 A)`.
- **Significant figures** — numeric literals carry their precision
  (`4.70 kΩ` is three sig-figs), propagated through arithmetic and honored in
  display. `exact()` and `measured()` opt in and out.
- **Math notation** — `:=` assignment, `·` multiplication, `‖` parallel,
  superscript powers (`V²`, `k⁰˙⁵⁵`), `√`, vulgar fractions (`½`),
  subscript indexing (`R₁`, `M₀͵₁`), `∠` phasors, `Γ`, `Σ`, `π`, `≈`,
  inequality for-loops (`for 1 ≤ k ≤ 5:`), set operators (`∩`, `∪`),
  `%` and `‰` as numeric suffixes.
- **Engineering literals** — base-subscript integers (`fed₁₆`, `1011₂`),
  Roman numerals (`"MCMXCIV"ᵣₒₘₑ`), inclusive ranges (`[1..10]`, also
  between unit-carrying ends: `[-55 °C..125 °C]`), closed intervals
  (`3 ‥ 7` — the form a `±` result prints in, so output pastes back as input),
  string/label ranges (`['C8'..'C13']`).
- **Matrices** — use `Matrix([[…]])` for a sympy matrix, or select legacy
  mode for automatic `[[…]]` promotion. Matrices support linear algebra
  (`M.inv()`, `M.det()`, `Mᵀ`) and 2-D subscript access `M₀͵₁`
  (0-indexed, like the rest of Python), and LaTeX rendering.
- **Symbolic math** — a thin sympy bridge: declare `symbols: x, R1..R4`,
  build expressions with units, solve and plot them.
- **Unit-aware plotting** — `plot()` reads units off the data and labels
  axes; mixes measured series and symbolic fit curves in one call.
- **Temperature semantics** — Mathcad-style delta convention (`25 °C` is a
  25 K difference; `to_kelvin()` / `to_fahrenheit()` for absolute
  conversions).
- **Extras** — ISO 286 limits & fits tables, ISO 8601 date/duration
  literals, DKK-based currency conversion with live rates from Danmarks
  Nationalbank (24 h on-disk cache, offline fallback), radix display tags
  (`255 ▸ hex`, `M ▸ bin`, `1994 ▸ roman`), identifier protection so unit
  names can't be clobbered.
- **Symbol palette** — an optional native Windows app
  ([`SymbolPaletteWinUI/`](SymbolPaletteWinUI/)) that types `Ω ≈ ∠ √ μ ₀ ͵ …`
  into whatever has focus. The DSL works fine without it.

## Quick start

Requires Python 3.x with Jupyter (developed and tested on Python 3.14).

```bash
git clone https://github.com/RichardThulstrup/engineering-dsl.git
cd engineering-dsl
pip install -e .
```

(For a non-development install on a fresh machine, see
[Installing on a fresh machine](#installing-on-a-fresh-machine) — a
plain `pip install` also sets up the print/PDF exporters and the
JupyterLab editor highlighting automatically.)

Then in a notebook started from the repo root (or any environment where the
package is installed):

```python
from utils.Engineer import *
```

That one import activates the unit environment, installs the notebook transformer,
and loads the physical constants. The bundled example notebooks use the older
notation: add `set_syntax_mode("legacy")` to their setup cell when running them
with this version. See it in action:

- **[DSL_Examples.ipynb](DSL_Examples.ipynb)** — a gallery of one-cell
  examples (electrical, mechanical, fluids, plotting, matrices, radix, …).
- **[DSL_Manual.ipynb](DSL_Manual.ipynb)** — the full reference.
- **[A_Practical_Manual_for_the_Engineering_DSL.ipynb](A_Practical_Manual_for_the_Engineering_DSL.ipynb)**
  — a task-oriented walkthrough.

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

## How it works

`utils/circuit_dsl.py` is the heart: a pipeline of source rewrites (regex,
token-level, and AST passes) that turn the notation into ordinary Python,
applied per-cell by the notebook adapter in `utils/runtime.py`. `utils/sigfig.py` implements
the significant-figures number type; `utils/symbolic.py` bridges to sympy;
`utils/Engineer.py` ties it all together as the single import.

Because the transforms produce plain Python, everything composes with the
normal ecosystem — the rewritten cells call into numpy, sympy, and
matplotlib like any other code.

## Printing and PDFs

JupyterLab's own print command (Ctrl+P) cannot render the math — it uses
a script-blocked iframe, so results print as raw LaTeX. The toolkit
ships two replacements that work out of the box:

- **`print_view()`** — run it in any cell to get a **🖨 Print…** button
  that opens the notebook's printable rendering in a new tab and pops
  the print dialog once the math is typeset. The rendering is the
  toolkit's own `edsl_print` nbconvert exporter: code keeps the DSL
  colour scheme from the JupyterLab editor (bold stripped for print),
  print controls are hidden, and cells tagged `no-print` are omitted
  (add the tag in JupyterLab's property inspector). It also appears in
  Lab's *File → Save and Export Notebook As* menu. Prefer monochrome?
  `c.EDSLPrintExporter.code_style = "math"` in your Jupyter config
  typesets code as engineering notation instead — variables italic
  with real subscripts, units upright, comments grey.
- **`hardcopy()`** — renders the notebook's last-saved state to a PDF
  next to it (`hardcopy('DSL_Manual.ipynb')` to pick another). It warns
  when the saved file has no outputs; `hardcopy(execute=True)` runs the
  notebook fresh during export instead of relying on what was saved.
  PDF output needs a one-time install:

  ```bash
  pip install "engineering-dsl[hardcopy]"     # or: pip install "nbconvert[webpdf]"
  playwright install chromium
  ```

  Without it, `hardcopy()` falls back to a standalone `.html`.

For batch exports of all the manuals (HTML + GitHub-renderable Markdown
+ PDF in one go, executed once per notebook), use
[`hardcopy.py`](hardcopy.py):

```bash
python hardcopy.py
```

## Editor highlighting (JupyterLab)

The DSL's notation is highlighted live in JupyterLab by a bundled
extension (`jupyterlab-edsl-highlight/`): units and constants in NCS
blue, numbers and reserved words in NCS green, strings in NCS red,
helpers in amber, subscript indices in purple, comments grey italic —
matching the print/PDF exporters, which use the same vocabulary via the
Pygments lexer in `utils/Engineer_Style.py`. Plots use the same NCS
base palette for their series-colour cycle.

The extension ships prebuilt inside the package (a regular
`pip install` places it where JupyterLab finds it — no node, no
`jupyter labextension install`). Restart or refresh JupyterLab after
installing. `jupyter labextension list` should show
`jupyterlab-edsl-highlight … enabled ok`.

## Editor support (VS Code)

A companion extension now provides DSL highlighting, symbol completions and
live syntax checks against the actual transformer. Use its **Engineering DSL**
Jupyter kernel so Pylance does not try to parse DSL symbols as ordinary Python.
Normal Python files and notebooks retain Pylance.

```sh
pip install -e ".[notebook]"
python -m utils.vscode_kernel --install --user
python vscode-engineering-dsl/build_vsix.py
code --install-extension vscode-engineering-dsl/dist/engineering-dsl-0.1.0.vsix
```

Reload VS Code, then choose **Select Another Kernel → Jupyter Kernel →
Engineering DSL** in the notebook's kernel picker and run the notebook from the
first cell. Keep the `from utils.Engineer import *` preamble. Selecting a new
kernel starts a fresh session.

See [the VS Code extension guide](vscode-engineering-dsl/README.md) for interpreter
settings, commands, and the limits of syntax checking. The extension checks
transformed syntax without executing cells; it does not provide Python type
analysis for DSL cells.

## Voilà rendering

The project includes `voila.json` to keep the incompatible JupyterLab KaTeX
extension out of Voilà, restoring typeset math when `pv()` / `pp()` outputs
appear as raw LaTeX. This setting only affects Voilà.

After changing the setting, restart the **JupyterLab server**, then reopen
**Render with Voilà**. See [the Voilà guide](docs/VOILA.md) for configuration
outside the project directory and troubleshooting.

## Installing on a fresh machine

Everything a new user needs, end to end:

```bash
# 1. The toolkit — also registers the edsl_print / edsl_pdf exporters
#    (entry points) and installs the JupyterLab highlighting extension
#    (prebuilt, bundled in the package):
pip install git+https://github.com/RichardThulstrup/engineering-dsl.git

# 2. PDF hardcopies (optional — print_view()'s browser printing works
#    without it; hardcopy() falls back to HTML):
pip install "engineering-dsl[hardcopy]"
playwright install chromium
```

Then start JupyterLab and put `from utils.Engineer import *` in the
first cell. Checklist of what each piece gives you:

| Piece | Installed by | Check |
|---|---|---|
| DSL + units + sig-figs + sympy bridge | step 1 | `from utils.Engineer import *` runs |
| `edsl_print` / `edsl_pdf` exporters | step 1 (entry points) | `jupyter nbconvert --list-exporters` lists them; *File → Save and Export Notebook As* shows *Edsl_print* |
| Live editor highlighting | step 1 (bundled labextension) | `jupyter labextension list` shows `jupyterlab-edsl-highlight` |
| `hardcopy()` PDF output | step 2 | `hardcopy()` produces a `.pdf`, not `.html` |
| Symbol palette (optional, Windows) | download from [Releases](https://github.com/RichardThulstrup/engineering-dsl/releases), place per [Symbol palette app](#symbol-palette-app) | palette auto-launches on import |

Two caveats worth knowing:

- **Editable installs** (`pip install -e .`) do *not* install the
  bundled labextension (pip skips `data_files` for editables). For a
  development setup, copy it once into a Jupyter data path — see
  [jupyterlab-edsl-highlight/README.md](jupyterlab-edsl-highlight/README.md).
- The exporters and highlighting install **per Python environment**;
  if JupyterLab runs from a different environment than the one you
  installed into, install there instead.

## Symbol palette app

`SymbolPaletteWinUI/` contains a WinUI 3 (Windows App SDK) floating symbol
keyboard — see its [README](SymbolPaletteWinUI/README.md) for build
instructions. If a built binary is placed at `utils/bin/SymbolPaletteWinUI.exe`
it is auto-launched on import; otherwise the import stays silent.

A prebuilt Windows x64 build is attached to the
[latest release](https://github.com/RichardThulstrup/engineering-dsl/releases/latest)
as `SymbolPaletteWinUI-win-x64.tar.xz` — extract it (`tar -xf …`, built into
Windows 10+) and move the extracted folder's contents into `utils/bin/`. It vendors
[MathLive](https://cortexjs.io/mathlive/) (MIT) and the KaTeX fonts (MIT) for
its formula editor.

## Running the tests

```bash
python -m pip install -e ".[test]"
python -m pytest tests/
```

The suite executes every root-level example notebook in a fresh Jupyter kernel
using the same Python executable as pytest, including plots and rich display.
Currency examples use deterministic offline fallback rates and the optional
symbol palette is disabled. Saved notebook outputs are not modified by tests.
To run only the notebooks:

```bash
python -m pytest tests/test_notebook_examples.py -q
```

## License

[MIT](LICENSE)
