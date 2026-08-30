"""Complement to pyproject.toml: ship the prebuilt JupyterLab extension.

All package metadata lives in pyproject.toml.  This file exists only to
add ``data_files`` — the declarative pyproject config cannot express
them — so that a plain ``pip install`` places the prebuilt federated
extension in ``<sys.prefix>/share/jupyter/labextensions/``, where every
JupyterLab in that environment discovers it automatically (no node, no
``jupyter labextension`` command, no rebuild).

The extension's build output (``jupyterlab-edsl-highlight/labextension``)
is committed to the repo precisely so installs stay node-free.  To
rebuild it after changing the highlighter, see
``jupyterlab-edsl-highlight/README.md``.

NB: ``pip install -e .`` (editable) does NOT install data_files — for
live-editor highlighting in a development setup, copy the built
extension into a Jupyter data path manually (again, see the extension
README).
"""
from pathlib import Path

from setuptools import setup

_EXT_SRC = Path(__file__).parent / "jupyterlab-edsl-highlight"
_EXT_DEST = "share/jupyter/labextensions/jupyterlab-edsl-highlight"


def _labextension_data_files():
    """Map every file of the prebuilt extension (plus install.json)
    into the share/jupyter/labextensions tree, grouped per directory
    as ``data_files`` requires."""
    by_dir = {}
    build_dir = _EXT_SRC / "labextension"
    for p in build_dir.rglob("*"):
        if p.is_file():
            dest = (Path(_EXT_DEST) / p.relative_to(build_dir).parent).as_posix()
            by_dir.setdefault(dest, []).append(p.relative_to(Path(__file__).parent).as_posix())
    install_json = _EXT_SRC / "install.json"
    if install_json.is_file():
        by_dir.setdefault(_EXT_DEST, []).append(
            install_json.relative_to(Path(__file__).parent).as_posix())
    return sorted(by_dir.items())


setup(data_files=_labextension_data_files())
