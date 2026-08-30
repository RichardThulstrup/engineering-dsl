# jupyterlab-edsl-highlight

Live CodeMirror highlighting for the engineering DSL inside JupyterLab,
mirroring the Pygments lexer (`utils/Engineer_Style.py`) that colours
nbconvert exports and hardcopies — so the notebook looks the same live
as in print.

It also repairs a stock-parser artifact: `10. kΩ` (trailing-dot decimal,
the DSL's "trailing zeros are significant" notation) parses as member
access in CodeMirror's Python grammar, colouring `kΩ` as a *property*
while `4.7 kΩ` leaves it plain. The DSL decorations override both to the
unit colour.

## How it works

- `generate_rules.py` reads `EngineeringDSLLexer.EXTRA_TOKENS` from
  `utils/Engineer_Style.py` and writes `src/dslRules.js` — one source of
  truth for both highlighters. **Re-run it after changing the lexer**,
  then rebuild.
- `src/index.js` registers a CodeMirror `ViewPlugin` through JupyterLab's
  `IEditorExtensionRegistry` that lays mark decorations over the visible
  ranges of every Python editor (string/comment interiors are skipped via
  the syntax tree).
- `style/index.css` carries the light and dark palettes, matching
  `EngineeringDSLStyle` / `EngineeringDSLStyleDark`.

## Install

**Regular users need to do nothing** — the built extension under
`labextension/` is committed and ships inside the `engineering-dsl`
wheel (`setup.py` maps it into
`share/jupyter/labextensions/jupyterlab-edsl-highlight/`), so a plain
`pip install` of the toolkit sets it up. Restart or refresh JupyterLab
afterwards; `jupyter labextension list` should show
`jupyterlab-edsl-highlight … enabled ok`.

**Development setups** (`pip install -e .`) are the exception: pip does
not install `data_files` for editable installs, so copy the build once
into a Jupyter data path, e.g. on Windows:

```bash
cp -r labextension/* "$APPDATA/jupyter/labextensions/jupyterlab-edsl-highlight/"
```

## Rebuild after changing the highlighter

```bash
python generate_rules.py   # regenerates src/dslRules.js from the Pygments lexer
npm install                # first time only
npm run build              # writes labextension/ — commit the result
```

Then redo the copy above (dev) or reinstall the package, and refresh
JupyterLab.
