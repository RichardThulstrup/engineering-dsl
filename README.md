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

It works by installing a source-transform hook (via the
[`ideas`](https://github.com/aroberge/ideas) import hook) that rewrites each
cell before Python sees it — so the notation above is real, executable code,
not string parsing.

## Features

- **Units everywhere** — powered by
  [forallpeople](https://github.com/connorferster/forallpeople): `12 V / 3 A`
  is `4 Ω`, results auto-scale their SI prefix (`0.0082 V` displays as
  `8.2 mV`). Value–unit binding is tight: `12 V / 3 A` parses as
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
  Roman numerals (`"MCMXCIV"ᵣₒₘₑ`), inclusive ranges (`[1..10]`),
  string/label ranges (`['C8'..'C13']`).
- **Matrices** — a `[[…]]` literal is a real sympy matrix with linear
  algebra (`M.inv()`, `M.det()`, `Mᵀ`), 2-D subscript access `M₀͵₁`
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

That one import activates the unit environment, installs the syntax hook for
all subsequent cells, and loads the physical constants. See it in action:

- **[DSL_Examples.ipynb](DSL_Examples.ipynb)** — a gallery of one-cell
  examples (electrical, mechanical, fluids, plotting, matrices, radix, …).
- **[DSL_Manual.ipynb](DSL_Manual.ipynb)** — the full reference.
- **[A_Practical_Manual_for_the_Engineering_DSL.ipynb](A_Practical_Manual_for_the_Engineering_DSL.ipynb)**
  — a task-oriented walkthrough.

## How it works

`utils/circuit_dsl.py` is the heart: a pipeline of source rewrites (regex,
token-level, and AST passes) that turn the notation into ordinary Python,
applied per-cell by the `ideas` import hook. `utils/sigfig.py` implements
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
python -m pytest tests/
```

## License

[MIT](LICENSE)
