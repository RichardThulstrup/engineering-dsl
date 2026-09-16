# Voilà math rendering

If Render with Voilà shows raw LaTeX such as
`$\displaystyle \text{x =}\;100 \pi$`, check the browser console.
With Voilà 0.5.13 and the installed JupyterLab KaTeX extension, the cause was:

```text
Plugin '@jupyterlab/katex-extension:plugin' failed to activate.
TypeError: No provider for: @jupyterlab/coreutils:ISettingRegistry.
```

Voilà loads installed JupyterLab extensions. This KaTeX extension requests a
JupyterLab service that Voilà does not provide, so it fails to supply a math
renderer. The notebook's LaTeX is valid: changing `pv()`, escaping backslashes,
or switching dollar-sign delimiters does not fix this failure.

## Fix

The project-root `voila.json` excludes this extension **only in Voilà**:

```json
{
  "VoilaConfiguration": {
    "extension_denylist": ["@jupyterlab/katex-extension"]
  }
}
```

For JupyterLab launched from another directory, merge the same setting into
`~/.jupyter/voila.json` (Windows: `%USERPROFILE%\.jupyter\voila.json`).
Keep existing settings and denylist entries. An explicit `extension_allowlist`
takes priority; if configured, omit KaTeX from that list too.

1. Save your notebooks.
2. Stop and restart the JupyterLab **server**. Its Voilà extension reads this
   configuration on startup; restarting only a notebook kernel is insufficient.
3. Open the notebook and click **Render with Voilà** again.

KaTeX remains available in the normal JupyterLab editor. Voilà uses its available
MathJax renderer. No package removal or formula changes are required.

A standalone launch from the repository also reads the configuration:

```sh
python -m voila DSL_Manual.ipynb
```

The fix was checked in a browser using both `pv(x)` / `pv(y)` and standard
`IPython.display.Math` / `Latex` outputs. Before the setting, all five remained
raw text; excluding KaTeX produced five correctly typeset formulas.

See [Voilà's Jupyter Server configuration documentation](https://voila.readthedocs.io/en/latest/customize.html#configure-voila-for-the-jupyter-server).
