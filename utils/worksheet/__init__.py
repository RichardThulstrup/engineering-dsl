"""A Mathcad-style worksheet front end for the Engineering DSL.

    python -m utils.worksheet [notebook.ipynb]

Regions sit freely on a page; math is drawn as built-up notation with
its result beside it, and the sheet recalculates top to bottom whenever
something changes.  Worksheets are ordinary notebooks (``.ipynb``), so
they still open in JupyterLab and VS Code.
"""

__all__ = ["main"]


def main(argv=None):
    from .server import main as _main
    return _main(argv)
