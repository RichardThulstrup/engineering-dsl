"""In-notebook hardcopy helpers: ``print_view()`` and ``hardcopy()``.

JupyterLab's built-in print command (Ctrl+P) renders the notebook into a
sandboxed iframe with scripts disabled, so MathJax can never typeset
there — DSL results print as raw ``$\\displaystyle …$`` source.  That is
a JupyterLab design decision, not a configuration problem, so the
toolkit ships two fuss-free replacements that work from a plain
``git clone`` with no server configuration:

``print_view()``
    Displays a link that opens the server's own HTML rendering of this
    notebook (the exact page the print command uses) in a browser tab,
    where MathJax *does* run.  Print from there with Ctrl+P.  The link
    is built in the browser at click time from ``window.location``, and
    because it is clicked from inside Jupyter it passes the server's
    same-origin checks — no token or password juggling.

``hardcopy()``
    Renders the notebook's last-saved state to a PDF (Chromium-based
    ``webpdf`` — best Unicode and math fidelity) next to the notebook.
    Needs the optional dependencies once::

        pip install "nbconvert[webpdf]"
        playwright install chromium

    Without them it falls back to a standalone ``.html`` (math typeset
    via CDN MathJax, so it needs internet when opened) and says how to
    unlock PDF.

Both use the notebook path Jupyter advertises to the kernel
(``JPY_SESSION_NAME``); pass ``notebook='Name.ipynb'`` explicitly when
running in an environment that doesn't provide it.

This module is plain Python (bare ``=`` assignments) and must be seeded
before the DSL source-transform hook is installed — see the pre-hook
imports in ``Engineer.py``.
"""

import html as _html
import importlib.util as _importlib_util
import os
import pathlib
import subprocess
import sys
import urllib.parse

__all__ = ["print_view", "hardcopy"]


def _session_path():
    """The notebook's path from ``JPY_SESSION_NAME``, or None.

    ``ipykernel`` (6.22+) exports the Jupyter session name.  Depending
    on the frontend this is the API path relative to the server root, an
    absolute filesystem path, or a bare title — callers must tolerate
    all three.
    """
    name = os.environ.get("JPY_SESSION_NAME", "")
    return name if name.endswith(".ipynb") else None


def _server_is_alive(info):
    """True when the server described by a runtime-info dict answers on
    its port.  ``list_running_servers`` happily returns stale entries
    from servers that died without cleaning up, so probe the socket."""
    import socket

    try:
        with socket.create_connection(
                ("127.0.0.1", int(info["port"])), timeout=0.3):
            return True
    except OSError:
        return False


def _api_path():
    """The notebook's path relative to the Jupyter server root — the
    form the ``/nbconvert/html/…`` URL needs — or None.

    An absolute ``JPY_SESSION_NAME`` (as some frontends provide) is
    relativized against the root directory of a live Jupyter server
    that contains it; with several candidates the deepest root wins
    (the most specific server).  Falls back to the bare filename, which
    is correct whenever the notebook sits in the server root.
    """
    name = _session_path()
    if not name:
        return None
    p = pathlib.PurePath(name)
    if not p.is_absolute():
        return str(p).replace("\\", "/")

    target = pathlib.Path(name)
    best = None
    try:
        from jupyter_server import serverapp

        for info in serverapp.list_running_servers():
            root = pathlib.Path(info.get("root_dir", ""))
            try:
                rel = target.resolve().relative_to(root.resolve())
            except (OSError, ValueError):
                continue
            if not _server_is_alive(info):
                continue
            if best is None or len(root.parts) > len(best[0].parts):
                best = (root, rel)
    except Exception:
        pass
    if best:
        return best[1].as_posix()
    return p.name


def _resolve_file(notebook):
    """Resolve ``notebook`` (or the current session) to an existing file."""
    if notebook is not None:
        p = pathlib.Path(notebook)
        if not p.is_file():
            raise FileNotFoundError(f"notebook not found: {p}")
        return p
    session = _session_path()
    if session:
        # The kernel's cwd is normally the notebook's own directory, and
        # the session path is relative to the server root — try both.
        for candidate in (pathlib.Path(session),
                          pathlib.Path(pathlib.PurePosixPath(session).name)):
            if candidate.is_file():
                return candidate
    raise RuntimeError(
        "Could not determine the current notebook — pass it explicitly, "
        "e.g. hardcopy('DSL_Manual.ipynb')."
    )


_EXPORTER_CACHE = {}


def _exporter_registered(name):
    """True when the given nbconvert exporter entry point is installed
    (requires ``pip install -e .`` once; a running server additionally
    needs a restart to see new entry points).

    Deliberately checks package *metadata* only, without loading the
    exporter: loading would import ``utils.print_exporter`` inside the
    kernel, where the DSL's ``ideas`` hook would rewrite its plain
    Python source and break it.  Only the server and the nbconvert
    subprocesses (both hook-free) ever load the exporter class.
    """
    if name not in _EXPORTER_CACHE:
        try:
            from importlib.metadata import entry_points

            eps = entry_points(group="nbconvert.exporters")
            _EXPORTER_CACHE[name] = any(ep.name == name for ep in eps)
        except Exception:
            _EXPORTER_CACHE[name] = False
    return _EXPORTER_CACHE[name]


def _print_format():
    """The nbconvert format for the print view: the toolkit's own
    ``edsl_print`` rendering when its entry point is installed, else the
    stock lab HTML."""
    return "edsl_print" if _exporter_registered("edsl_print") else "html"


def print_view(notebook=None):
    """Show a link to this notebook's printable HTML rendering.

    Click the link (a new tab opens with the math fully typeset), then
    print with the browser's own Ctrl+P.  Use this instead of
    JupyterLab's print command, which cannot render math.
    """
    from IPython.display import display, HTML

    if notebook is not None:
        api_path = str(notebook).replace("\\", "/")
    else:
        api_path = _api_path()
        if not api_path:
            raise RuntimeError(
                "Could not determine the current notebook — pass it "
                "explicitly, e.g. print_view('DSL_Manual.ipynb')."
            )
    url_path = ("/nbconvert/" + _print_format() + "/"
                + urllib.parse.quote(api_path) + "?download=false")
    # URLs are assembled in the browser at click time so they work for
    # any host/port the server happens to be on.  Inline handlers run
    # only in trusted notebooks — i.e. ones the user ran themselves.
    #
    # The button opens the print view and, as soon as MathJax has
    # finished typesetting there (its Queue drains after the initial
    # typeset), pops the browser's print dialog — so printing is one
    # click, with no Ctrl+P habit required.  The plain link is the
    # fallback for just looking at the page.
    button_js = (
        "const w = window.open(window.location.origin + '" + url_path + "');"
        "const t = setInterval(() => {"
        "  try {"
        "    if (w.MathJax && w.MathJax.Hub) {"
        "      clearInterval(t);"
        "      w.MathJax.Hub.Queue(() => w.print());"
        "    }"
        "  } catch (e) {}"
        "}, 250);"
        "setTimeout(() => clearInterval(t), 20000);"
    )
    # The wrapper class hides these controls wherever they must not
    # appear: the edsl-print template removes the whole cell, and the
    # @media rule keeps them off paper even in stock html/webpdf output.
    display(HTML(
        '<div class="edsl-printview">'
        "<style>@media print { .edsl-printview { display: none !important; } }</style>"
        f'<button onclick="{_html.escape(button_js, quote=True)}">'
        f"\N{PRINTER} Print\N{HORIZONTAL ELLIPSIS}</button> "
        '&nbsp;or&nbsp; <a href="#" target="_blank" rel="noopener" '
        f'''onclick="this.href = window.location.origin + '{url_path}'">'''
        f"open the print view of <code>{_html.escape(api_path)}</code>"
        "</a> and press Ctrl+P there (never in JupyterLab itself)"
        "</div>"
    ))


def hardcopy(notebook=None, to=None, timeout=600, execute=False):
    """Render the notebook's last-saved state to a hardcopy file.

    ``to`` is any nbconvert exporter name; the default picks the
    toolkit's own Chromium-based PDF rendering (``edsl_pdf`` —
    math-style code typesetting, ``no-print`` tags honoured) when
    installed, else stock ``webpdf``.  When Chromium/Playwright is
    missing, falls back to a standalone ``.html`` and prints the
    one-time install commands that unlock PDF.

    This reads the file on disk, so save (Ctrl+S) first — outputs that
    exist only in the live session are invisible to it.  Pass
    ``execute=True`` to run the notebook fresh as part of the export
    (slower: the DSL import runs again), which guarantees outputs
    regardless of what was saved.
    """
    nb = _resolve_file(notebook)

    if not execute:
        # The most common "my PDF is empty" cause: the saved file holds
        # no outputs (unsaved session, or saved after a kernel restart).
        import json

        try:
            data = json.loads(nb.read_text(encoding="utf-8"))
            has_outputs = any(c.get("outputs") for c in data.get("cells", [])
                              if c.get("cell_type") == "code")
        except Exception:
            has_outputs = True
        if not has_outputs:
            print("Note: the saved notebook file contains no cell outputs, so "
                  "the hardcopy will show code only.  Save after running the "
                  "cells (Ctrl+S), or call hardcopy(execute=True) to run the "
                  "notebook fresh during export.")

    if to is None:
        to = "edsl_pdf" if _exporter_registered("edsl_pdf") else "webpdf"
    if to in ("webpdf", "edsl_pdf") and _importlib_util.find_spec("playwright") is None:
        print(
            "PDF output needs a one-time install:\n"
            '    pip install "nbconvert[webpdf]"\n'
            "    playwright install chromium\n"
            "Falling back to HTML for now (math needs internet to display)."
        )
        to = "html"

    # Chromium-based exports go through the shim launcher: on Windows
    # nbconvert forces an event-loop policy that cannot spawn Chromium
    # (see _webpdf_shim).  Execution (when requested) runs separately,
    # through plain nbconvert, which needs that policy for its kernel.
    if to in ("webpdf", "edsl_pdf"):
        shim = pathlib.Path(__file__).with_name("_webpdf_shim.py")
        entry = [sys.executable, str(shim)]
    else:
        entry = [sys.executable, "-m", "nbconvert"]
    cwd = str(nb.parent if nb.parent != pathlib.Path("") else ".")
    env = dict(os.environ)
    env["SYMBOL_PALETTE_EXE"] = ""       # a batch run must not pop the palette
    env["ENGINEER_HEADLESS"] = "1"
    run_kwargs = dict(
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, cwd=cwd, env=env,
    )

    export_src, extra_args, tmp = nb, [], None
    try:
        if execute:
            tmp = nb.with_name(f"_hardcopy_{nb.stem}.exec.ipynb")
            proc = subprocess.run(
                [sys.executable, "-m", "nbconvert", "--to", "notebook",
                 "--execute", "--output", tmp.name, str(nb)],
                **run_kwargs)
            if proc.returncode != 0:
                tail = "\n".join((proc.stderr or "").splitlines()[-12:])
                raise RuntimeError(f"notebook execution failed:\n{tail}")
            export_src = tmp
            extra_args = ["--output", nb.stem]

        proc = subprocess.run(
            [*entry, "--to", to, *extra_args, str(export_src)], **run_kwargs)
        if proc.returncode != 0:
            tail = "\n".join((proc.stderr or "").splitlines()[-12:])
            raise RuntimeError(f"nbconvert failed:\n{tail}")
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)

    # nbconvert names the file after the notebook; extension depends on
    # the exporter ('webpdf' → .pdf).  Recover it from the log line
    # ("Writing 12345 bytes to <path>") with a sensible fallback.
    out = None
    for line in (proc.stderr or "").splitlines():
        if " to " in line and line.lstrip().startswith("[NbConvertApp] Writing"):
            out = line.split(" to ", 1)[1].strip()
    if out is None:
        ext = {"webpdf": ".pdf", "pdf": ".pdf",
               "edsl_pdf": ".pdf", "edsl_print": ".html"}.get(to, "." + to)
        out = str(nb.with_suffix(ext))
    print(f"Hardcopy written: {out}")
    return out
