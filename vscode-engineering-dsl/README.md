# Engineering DSL for VS Code

Syntax highlighting, equation/glyph completions and live syntax diagnostics for
engineering-dsl notebooks. Valid DSL operators no longer go through Pylance's
Python parser. Real errors are checked against the same transformer used when
the notebook executes.

## Install

1. Install the toolkit and kernel support in the Python environment you use:

   ```sh
   pip install -e ".[notebook]"
   python -m utils.vscode_kernel --install --user
   ```

2. Build and install the VSIX from the repository root (requires Microsoft's
   Python and Jupyter extensions):

   ```sh
   python vscode-engineering-dsl/build_vsix.py
   code --install-extension vscode-engineering-dsl/dist/engineering-dsl-0.1.0.vsix
   ```

3. Reload VS Code. Open a notebook and use its top-right kernel picker:
   **Select Another Kernel → Jupyter Kernel → Engineering DSL**.
   Switching kernels starts a fresh session; run the notebook from the beginning.
   Keep `from utils.Engineer import *` in the first cell.

Selecting the DSL kernel changes code cells to **Engineering DSL**. Saving the
notebook remembers this choice. Ordinary Python notebooks and `.py` files retain
Pylance. To return a notebook to Python, select its normal Python kernel.

The kernel registration points to the Python executable used to register it.
Re-run the registration command after moving the checkout or changing Python
environments. A later registration replaces the same `engineering-dsl` entry.
Remote Jupyter servers need the package and kernel installed on the server too.

## Editor commands

- **Engineering DSL: Install Notebook Kernel** registers the kernel using the
  selected Python interpreter. It does not run cells or switch a running kernel.
- **Engineering DSL: Select Notebook Kernel** opens VS Code's kernel picker.
- **Engineering DSL: Check Notebook Syntax** refreshes diagnostics immediately.
- **Engineering DSL: Show Transformed Cell** opens a read-only Python preview.

Type `equation`, `equal`, `sqrt`, `transpose`, etc. and use completion to insert
`≡`, `≟`, `√`, `ᵀ`, and other symbols. Highlighting reuses the JupyterLab symbol
vocabulary and respects strings/comments and the current VS Code theme.

## Checker settings and limits

`engineeringDsl.pythonPath` selects the Python executable used for editor
checks. Leave it empty to use the registered local Engineering DSL kernel's
interpreter (falling back to the Python extension's selection), or set it
explicitly for a different environment or a remote kernel. They need the same version
of the toolkit. If the notebook is outside the checkout and the package is not
installed, set `engineeringDsl.runtimePath` to the checkout directory.

The status bar shows **DSL** while this kernel is selected. A warning icon means
the checker could not start; click it for the error or inspect the **Engineering
DSL** output channel. Highlighting and execution remain independent of checking.
Checking starts only in trusted workspaces. It never executes notebook cells.

Checks follow document order, including symbol declarations, literal top-level
`set_syntax_mode('legacy')` calls, `%%dsl`, `%%python` and IPython magics. The
initial mode is configured with `engineeringDsl.syntaxMode`. Dynamic mode
changes, out-of-order execution, protected names, undefined names and Python
types are runtime concerns; this extension is a syntax checker, not a Pylance
replacement. Transformed error locations are aligned back to the original
source; errors raised before a transformation finishes may mark the first
nonempty line of the cell.

The worker is reused, edits are debounced, stale results are discarded, and
workers stop when DSL notebooks close. Live checking is limited to 1 MB of
notebook code and a 30-second request timeout. Disable it with
`engineeringDsl.diagnostics` if necessary. Python-specific Jupyter features such
as its debugger and variable explorer may not be available for the DSL language.

## Build and test

The extension has no npm dependencies and does not need a JavaScript build:

```sh
python vscode-engineering-dsl/generate_grammar.py
python vscode-engineering-dsl/build_vsix.py
python -m pytest tests/test_vscode_checker.py tests/test_vscode_kernel.py -q
cd vscode-engineering-dsl
npm test
```

The grammar generator consumes the generated JupyterLab rules; regenerate
those first after editing `utils/Engineer_Style.py`.

`test/extension.test.js` is an extension-host integration test. It runs with real
VS Code, Pylance and Jupyter, in a separate test profile. Set
`EDSL_TEST_NOTEBOOK` to a disposable fixture notebook, `EDSL_TEST_KERNEL_ID` to
its discovered Jupyter controller ID and `EDSL_TEST_RESULT` to a log path. Launch
VS Code with `--extensionDevelopmentPath` pointing here and `--extensionTestsPath`
pointing to that test. Never use a notebook containing unsaved work as a fixture.
