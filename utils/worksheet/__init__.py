"""A Mathcad-style worksheet front end for the Engineering DSL.

    python -m utils.worksheet [notebook.ipynb]

Regions sit freely on a page; math is drawn as built-up notation with
its result beside it, and the sheet recalculates top to bottom whenever
something changes.  Worksheets are ordinary notebooks (``.ipynb``), so
they still open in JupyterLab and VS Code.
"""

__all__ = ["main"]

# What the worksheet needs beyond the toolkit itself: the "worksheet"
# extra in pyproject.toml, all of it present wherever JupyterLab is.
NEEDS = ("tornado", "jupyter_client", "nbformat", "ipykernel")


def main(argv=None):
    # Said before the server is imported: it imports some of these at
    # once and others only when a sheet is opened, so without this a
    # missing one shows as a traceback, or not until later.
    import sys
    from importlib.util import find_spec

    missing = [name for name in NEEDS if find_spec(name) is None]
    if missing:
        raise SystemExit(
            f"The worksheet needs {', '.join(missing)}: not installed for this Python ({sys.executable}).\n"
            f"Install with:  python -m pip install {' '.join(NEEDS)}")
    from .server import main as _main
    return _main(argv)
