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
