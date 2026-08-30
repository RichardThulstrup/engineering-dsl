#!/usr/bin/env python
"""Build hardcopies of the DSL notebooks: execute once, export many.

The expensive part of any nbconvert run on these notebooks is kernel
execution — ``from utils.Engineer import *`` alone loads forallpeople,
installs the ``ideas`` hook, and pulls in sympy.  So this script executes
each notebook exactly once into a temporary copy, then fans out every
requested export format from that executed copy.  Notebooks build in
parallel (they are independent); the exports of a single notebook run
sequentially because they are cheap next to the execution.

Usage::

    python hardcopy.py                          # all manuals, html + md + webpdf
    python hardcopy.py DSL_Manual.ipynb         # one notebook
    python hardcopy.py --formats html,md        # skip PDF
    python hardcopy.py --no-execute             # reuse the stored outputs
    python hardcopy.py --no-input --formats webpdf   # code-free PDF

Formats
-------
html    nbconvert "lab" template.  MathJax stays on its CDN default so
        the .html file remains portable.
md      GitHub-renderable Markdown; images land in ``<name>_files/``.
        LaTeX outputs (``$\\displaystyle …$``) are rewritten into
        GitHub ```math fences — raw inline ``$…$`` is mangled by
        Markdown's emphasis parsing and renders nowhere else; a fence
        renders on GitHub and degrades to a readable code block in
        plain editors.  Tune with ``--md-math fence|dollars|keep``.
webpdf  Chromium-rendered PDF (best Unicode/MathJax fidelity).  Needs
        ``pip install "nbconvert[webpdf]"`` and
        ``playwright install chromium``; the script says so if missing.
pdf     LaTeX route (xelatex).  Off by default — the DSL's Unicode
        source needs a coverage-rich mono font to render fully.

Execution runs with the repo root as the kernel cwd (so
``from utils.Engineer import *`` resolves), with ``SYMBOL_PALETTE_EXE``
cleared and ``ENGINEER_HEADLESS=1`` set so a batch build never pops up
the symbol palette, and with inline matplotlib figures switched to SVG
(vector plots in the PDFs; disable with ``--no-svg``).

If ``qpdf`` is on PATH, produced PDFs are linearized in place
(fast-web-view); disable with ``--no-qpdf``.

A local MathJax (``--mathjax <path-or-url>``) speeds up webpdf a lot on
math-dense notebooks and makes the build work offline: point it at a
MathJax 2.7.x tree's ``MathJax.js`` (a vendored copy dropped at
``hardcopy/_mathjax/MathJax.js`` is picked up automatically).  It is
applied to webpdf only — the standalone .html keeps the CDN so it works
when mailed around.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import pathlib
import shutil
import subprocess
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent
DEFAULT_OUT = REPO_ROOT / "hardcopy"
TMP_PREFIX = "_hardcopy_"          # temp executed copies, created in REPO_ROOT
ALL_FORMATS = ("html", "md", "webpdf", "pdf")
DEFAULT_FORMATS = ("html", "md", "webpdf")

# Injected as the first cell before execution and stripped again before
# export.  Runs before the DSL import, so it is plain Python; it must not
# contain bare ``=`` assignments anyway, in case the hook is ever active.
SVG_SETUP_SOURCE = """\
try:
    from matplotlib_inline.backend_inline import set_matplotlib_formats
    set_matplotlib_formats("svg")
except Exception:
    pass
"""
SETUP_CELL_TAG = "hardcopy_setup"


def default_notebooks() -> list[pathlib.Path]:
    """The publishable notebooks: every .ipynb at the repo root that is
    not a leftover temp copy."""
    return sorted(
        p for p in REPO_ROOT.glob("*.ipynb")
        if not p.name.startswith(TMP_PREFIX)
    )


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["SYMBOL_PALETTE_EXE"] = ""        # falsy → no palette via env var
    env["ENGINEER_HEADLESS"] = "1"        # honored by Engineer.py when it grows the guard
    env["PYTHONIOENCODING"] = "utf-8"     # Unicode-heavy tracebacks on Windows consoles
    return env


def run(cmd: list[str], *, tag: str) -> subprocess.CompletedProcess:
    """Run a subprocess from the repo root; on failure print the tail of
    its output under the notebook's tag and raise."""
    proc = subprocess.run(
        cmd, cwd=REPO_ROOT, env=child_env(),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-15:])
        print(f"[{tag}] FAILED: {' '.join(cmd)}\n{tail}", file=sys.stderr)
        raise RuntimeError(f"{tag}: command failed")
    return proc


def nbconvert(args: list[str], *, tag: str, webpdf: bool = False) -> None:
    # webpdf exports go through the shim launcher: on Windows nbconvert
    # forces an event-loop policy that cannot spawn Chromium (see
    # utils/_webpdf_shim.py).  Never combine the shim with --execute.
    if webpdf:
        entry = [sys.executable, str(REPO_ROOT / "utils" / "_webpdf_shim.py")]
    else:
        entry = [sys.executable, "-m", "nbconvert"]
    run([*entry, *args], tag=tag)


def resolve_mathjax(arg: str | None) -> str | None:
    """Resolve --mathjax to a URL nbconvert can use, or None for the CDN
    default.  Accepts a URL, a path to MathJax.js, or falls back to a
    vendored copy at hardcopy/_mathjax/MathJax.js."""
    candidate = None
    if arg:
        if "://" in arg:
            return arg
        candidate = pathlib.Path(arg)
    else:
        vendored = DEFAULT_OUT / "_mathjax" / "MathJax.js"
        if vendored.is_file():
            candidate = vendored
    if candidate is None:
        return None
    if not candidate.is_file():
        sys.exit(f"--mathjax: {candidate} not found")
    return candidate.resolve().as_uri() + "?config=TeX-AMS_CHTML-full,Safe"


def inject_svg_setup(src: pathlib.Path, dst: pathlib.Path) -> None:
    import nbformat

    nb = nbformat.read(src, as_version=4)
    cell = nbformat.v4.new_code_cell(SVG_SETUP_SOURCE)
    cell.metadata[SETUP_CELL_TAG] = True
    nb.cells.insert(0, cell)
    nbformat.write(nb, dst)


def strip_svg_setup(path: pathlib.Path) -> None:
    import nbformat

    nb = nbformat.read(path, as_version=4)
    nb.cells = [c for c in nb.cells if not c.metadata.get(SETUP_CELL_TAG)]
    nbformat.write(nb, path)


def rewrite_latex_outputs(src: pathlib.Path, dst: pathlib.Path,
                          style: str) -> None:
    """Write a copy of ``src`` where every ``text/latex`` cell output is
    replaced by a ``text/markdown`` math block.

    Operating on the notebook (rather than on the exported .md) means
    only genuine outputs are touched — authored prose in markdown cells
    passes through untouched.  ``text/html`` is dropped from rewritten
    outputs so the markdown exporter's MIME priority picks the fence.
    """
    import nbformat

    nb = nbformat.read(src, as_version=4)
    for cell in nb.cells:
        if cell.cell_type != "code":
            continue
        for out in cell.get("outputs", []):
            data = out.get("data")
            if not data or "text/latex" not in data:
                continue
            latex = data["text/latex"]
            if isinstance(latex, list):
                latex = "".join(latex)
            body = latex.strip().strip("$").strip()
            body = body.removeprefix(r"\displaystyle").strip()
            if style == "fence":
                data["text/markdown"] = f"```math\n{body}\n```"
            else:
                data["text/markdown"] = f"$$\n{body}\n$$"
            data.pop("text/latex", None)
            data.pop("text/html", None)
    nbformat.write(nb, dst)


def build_notebook(nb_path: pathlib.Path, opts: argparse.Namespace,
                   mathjax_url: str | None) -> list[str]:
    """Execute (optionally) and export one notebook.  Returns a list of
    'format: error' strings; empty means full success."""
    stem = nb_path.stem
    tag = stem
    errors: list[str] = []
    export_src = nb_path
    tmp = REPO_ROOT / f"{TMP_PREFIX}{stem}.ipynb"

    try:
        if opts.execute:
            t0 = time.perf_counter()
            if opts.svg:
                inject_svg_setup(nb_path, tmp)
            else:
                shutil.copyfile(nb_path, tmp)
            nbconvert(
                ["--to", "notebook", "--execute", "--inplace",
                 f"--ExecutePreprocessor.timeout={opts.timeout}",
                 str(tmp)],
                tag=tag,
            )
            if opts.svg:
                strip_svg_setup(tmp)
            export_src = tmp
            print(f"[{tag}] executed in {time.perf_counter() - t0:.1f}s")

        for fmt in opts.formats:
            t0 = time.perf_counter()
            to = {"md": "markdown"}.get(fmt, fmt)
            args = ["--to", to, "--output", stem,
                    "--output-dir", str(opts.out)]
            if opts.no_input:
                args.append("--no-input")
            if fmt == "webpdf" and mathjax_url:
                args.append(f"--WebPDFExporter.mathjax_url={mathjax_url}")
            if fmt == "pdf":
                # No TOC/cross-references in the manuals → one pass is enough.
                args.append("--PDFExporter.latex_count=1")
            fmt_src = export_src
            if fmt == "md" and opts.md_math != "keep":
                fmt_src = REPO_ROOT / f"{TMP_PREFIX}{stem}.md.ipynb"
                rewrite_latex_outputs(export_src, fmt_src, opts.md_math)
            try:
                nbconvert([*args, str(fmt_src)], tag=tag, webpdf=(fmt == "webpdf"))
            except RuntimeError as exc:
                errors.append(f"{fmt}: {exc}")
                continue
            finally:
                if fmt_src is not export_src:
                    fmt_src.unlink(missing_ok=True)

            if fmt in ("webpdf", "pdf") and opts.qpdf:
                pdf = opts.out / f"{stem}.pdf"
                if pdf.is_file():
                    run(["qpdf", "--linearize", "--replace-input", str(pdf)],
                        tag=tag)
            print(f"[{tag}] {fmt} in {time.perf_counter() - t0:.1f}s")
    except RuntimeError as exc:
        errors.append(f"execute: {exc}")
    finally:
        tmp.unlink(missing_ok=True)
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Execute the DSL notebooks once and export each to "
                    "several hardcopy formats.")
    ap.add_argument("notebooks", nargs="*", type=pathlib.Path,
                    help="notebooks to build (default: all *.ipynb at the repo root)")
    ap.add_argument("--formats", default=",".join(DEFAULT_FORMATS),
                    help=f"comma-separated subset of {'/'.join(ALL_FORMATS)} "
                         f"(default: %(default)s)")
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT,
                    help="output directory (default: hardcopy/)")
    ap.add_argument("--jobs", type=int, default=0,
                    help="parallel notebook builds (default: one per notebook, "
                         "capped at CPU count)")
    ap.add_argument("--timeout", type=int, default=300,
                    help="per-cell execution timeout in seconds (default: 300)")
    ap.add_argument("--md-math", choices=("fence", "dollars", "keep"),
                    default="fence",
                    help="how LaTeX outputs appear in the Markdown export: "
                         "GitHub ```math fences (default), $$…$$ blocks, or "
                         "the raw nbconvert $…$ lines")
    ap.add_argument("--mathjax", metavar="PATH_OR_URL",
                    help="local MathJax 2.7.x (path to MathJax.js, or URL) "
                         "used for webpdf rendering")
    ap.add_argument("--no-execute", dest="execute", action="store_false",
                    help="export the notebooks as saved, without re-running them")
    ap.add_argument("--no-svg", dest="svg", action="store_false",
                    help="keep the default PNG inline figures")
    ap.add_argument("--no-qpdf", dest="qpdf", action="store_false",
                    help="skip qpdf linearization of produced PDFs")
    ap.add_argument("--no-input", action="store_true",
                    help="exclude code cells from the exports")
    opts = ap.parse_args()

    opts.formats = [f.strip() for f in opts.formats.split(",") if f.strip()]
    unknown = [f for f in opts.formats if f not in ALL_FORMATS]
    if unknown:
        ap.error(f"unknown format(s): {', '.join(unknown)}")

    notebooks = [p.resolve() for p in opts.notebooks] or default_notebooks()
    if not notebooks:
        ap.error("no notebooks found")
    for p in notebooks:
        if not p.is_file():
            ap.error(f"not found: {p}")

    if "webpdf" in opts.formats:
        try:
            import playwright  # noqa: F401
        except ImportError:
            print("webpdf needs Playwright — run:\n"
                  '    pip install "nbconvert[webpdf]"\n'
                  "    playwright install chromium\n"
                  "Skipping webpdf for this build.", file=sys.stderr)
            opts.formats = [f for f in opts.formats if f != "webpdf"]

    if opts.qpdf and not shutil.which("qpdf"):
        opts.qpdf = False   # silently skip; it is only a nicety

    mathjax_url = resolve_mathjax(opts.mathjax)
    opts.out = opts.out.resolve()
    opts.out.mkdir(parents=True, exist_ok=True)

    jobs = opts.jobs or min(len(notebooks), os.cpu_count() or 1)
    t0 = time.perf_counter()
    failures: dict[str, list[str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {
            pool.submit(build_notebook, nb, opts, mathjax_url): nb
            for nb in notebooks
        }
        for fut in concurrent.futures.as_completed(futures):
            nb = futures[fut]
            errs = fut.result()
            if errs:
                failures[nb.name] = errs

    print(f"done in {time.perf_counter() - t0:.1f}s → {opts.out}")
    for name, errs in failures.items():
        print(f"{name}: {'; '.join(errs)}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
