"""Complement to pyproject.toml: ship the prebuilt JupyterLab extension
and the "Engineering DSL" kernel.

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

The kernel goes the same way, into ``<sys.prefix>/share/jupyter/kernels/``
(which is how ipykernel ships its own ``python3``): notebooks saved with
the "Engineering DSL" kernel, as some of the bundled examples are, then
open in a fresh install without a "select a kernel" question.

NB: ``pip install -e .`` (editable) does NOT install data_files — for
live-editor highlighting in a development setup, copy the built
extension into a Jupyter data path manually (again, see the extension
README), and register the kernel with
``python -m utils.vscode_kernel --install --user``.
"""
from pathlib import Path

_EXT_SRC = Path(__file__).parent / "jupyterlab-edsl-highlight"
_EXT_DEST = "share/jupyter/labextensions/jupyterlab-edsl-highlight"
# ``python`` in its argv is replaced by Jupyter with the interpreter the
# server itself runs on, so the one file suits every environment.
_KERNEL_SPEC = "utils/kernelspec/kernel.json"
_KERNEL_DEST = "share/jupyter/kernels/engineering-dsl"


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


def data_files():
    """Everything placed outside the package: the extension and the kernel."""
    return _labextension_data_files() + [(_KERNEL_DEST, [_KERNEL_SPEC])]


# pip and ``python setup.py`` both run this file as ``__main__``; the
# guard lets the tests read the mapping above without building anything.
if __name__ == "__main__":
    from setuptools import setup

    setup(data_files=data_files())
