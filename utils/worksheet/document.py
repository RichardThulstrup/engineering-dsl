"""Worksheets on disk: ordinary notebooks with a page layout.

A worksheet *is* an ``.ipynb`` file.  Each region is a cell — math
regions are code cells, text regions are markdown cells — stored in
reading order, so JupyterLab, VS Code and ``hardcopy()`` run the very
same calculation.  Where a region sits on the page is kept in the
cell's metadata (``edsl``), which other tools carry along untouched.

Opening a sheet does not, by itself, run it: a notebook that was not
last saved on this machine (Jupyter's own trust record decides) is
shown typeset but uncalculated until the user asks.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from .protocol import PREAMBLE

__all__ = ["load", "save", "trust", "listing", "Conflict", "resolve", "warm_up"]

# nbformat takes a second or two to import (its schema validator), which
# the server would rather spend opening the page: it is imported when
# first needed, or ahead of that by ``warm_up()`` on a thread.
nbformat = NotebookNotary = new_code_cell = new_markdown_cell = new_notebook = None


def warm_up():
    """Import what reading and writing notebooks needs."""
    global nbformat, NotebookNotary, new_code_cell, new_markdown_cell, new_notebook
    if nbformat is None:
        import nbformat as module
        from nbformat.sign import NotebookNotary
        from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook
        nbformat = module               # last: it is what says "ready"

FORMAT_VERSION = 1

# Saved output a sheet may show before it has been calculated here.
# Deliberately no HTML or scripts: this is what an untrusted file gets.
_SAFE_MIMES = ("text/plain", "text/latex", "image/png", "image/jpeg")
# What the front end may store as output (it comes from the user's own kernel).
_STORED_MIMES = _SAFE_MIMES + ("text/html", "text/markdown", "image/svg+xml")

_IMPORT_LINE = re.compile(r"^\s*from\s+utils\.(Engineer|dsl_setup)\s+import\s+\*\s*$", re.M)


class Conflict(Exception):
    """The file changed on disk after the sheet was opened."""


def resolve(root: Path, relative: str) -> Path:
    """``relative`` inside ``root`` — refusing anything that escapes it."""
    root = root.resolve()
    target = (root / (relative or "")).resolve()
    if target != root and root not in target.parents:
        raise PermissionError("outside the worksheet folder")
    return target


def _stamp(path: Path) -> str:
    """The file's modification time as an opaque token.  A string, because
    nanosecond counts do not survive a trip through JavaScript numbers."""
    return str(path.stat().st_mtime_ns)


def _notary():
    warm_up()
    try:
        return NotebookNotary()
    except Exception:                    # an unwritable Jupyter data dir
        return None


def _text(value) -> str:
    return "".join(value) if isinstance(value, list) else (value or "")


def _is_preamble(source: str) -> bool:
    lines = [ln for ln in source.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return len(lines) == 1 and _IMPORT_LINE.match(lines[0]) is not None


def _saved_outputs(cell):
    """A cell's stored outputs, reduced to inert text and pictures."""
    out = []
    for o in cell.get("outputs", []):
        kind = o.get("output_type")
        if kind == "stream":
            out.append({"type": "stream", "name": o.get("name", "stdout"),
                        "text": _text(o.get("text"))})
        elif kind in ("display_data", "execute_result"):
            data = {k: _text(v) for k, v in o.get("data", {}).items() if k in _SAFE_MIMES}
            if data:
                out.append({"type": "display", "data": data})
        elif kind == "error":
            out.append({"type": "error", "ename": o.get("ename", ""),
                        "evalue": o.get("evalue", "")})
    return out


def load(path: Path) -> dict:
    """Read a notebook as a worksheet document for the front end."""
    warm_up()
    nb = nbformat.read(str(path), as_version=4)
    notary = _notary()
    trusted = bool(notary and notary.check_signature(nb))
    meta = nb.metadata.get("edsl_worksheet") or {}
    regions, preamble = [], None
    seen_code = False
    for cell in nb.cells:
        cid = cell.get("id") or uuid.uuid4().hex[:8]
        source = _text(cell.source)
        if cell.cell_type == "code":
            if not seen_code and _is_preamble(source):
                seen_code = True
                preamble = {"id": cid, "source": source}     # kept, not shown
                continue
            seen_code = True
            kind = "math"
        else:
            kind = "text"
        place = cell.metadata.get("edsl") or {}
        region = {"id": cid, "kind": kind, "source": source,
                  "x": place.get("x"), "y": place.get("y"), "w": place.get("w")}
        if kind == "math":
            saved = _saved_outputs(cell)
            if saved:
                region["saved"] = saved
        regions.append(region)
    return {
        "name": path.stem, "trusted": trusted, "mtime": _stamp(path),
        "settings": meta.get("settings") or {}, "laidOut": "version" in meta,
        "preamble": preamble, "regions": regions,
    }


def _clean_outputs(outputs):
    """Outputs offered by the front end, as valid nbformat."""
    out = []
    for o in outputs or []:
        kind = o.get("output_type")
        if kind == "stream" and o.get("name") in ("stdout", "stderr"):
            out.append(nbformat.from_dict({"output_type": "stream", "name": o["name"],
                                           "text": str(o.get("text", ""))}))
        elif kind == "display_data":
            data = {k: v for k, v in (o.get("data") or {}).items()
                    if k in _STORED_MIMES and isinstance(v, str)}
            if data:
                out.append(nbformat.from_dict({"output_type": "display_data", "data": data,
                                               "metadata": {}}))
        elif kind == "error":
            out.append(nbformat.from_dict({
                "output_type": "error", "ename": str(o.get("ename", "")),
                "evalue": str(o.get("evalue", "")),
                "traceback": [str(t) for t in o.get("traceback", [])]}))
    return out


def save(path: Path, doc: dict, *, expect_mtime=None, force=False) -> dict:
    """Write the document back as a notebook.

    Cells keep whatever else they carried (tags, attachments, other
    tools' metadata): the file on disk is updated, not regenerated.
    Raises :class:`Conflict` when the file was changed by something else
    since ``expect_mtime``.
    """
    warm_up()
    existing = None
    if path.exists():
        if not force and expect_mtime is not None and _stamp(path) != str(expect_mtime):
            raise Conflict(str(path))
        try:
            existing = nbformat.read(str(path), as_version=4)
        except Exception:
            existing = None
    nb = existing or new_notebook()
    if existing is None:
        nb.metadata["kernelspec"] = {"display_name": "Python 3 (ipykernel)",
                                     "language": "python", "name": "python3"}
        nb.metadata["language_info"] = {"name": "python"}
    old = {c.get("id"): c for c in nb.cells if c.get("id")}

    cells = []
    regions = doc.get("regions") or []
    has_import = any(r.get("kind") == "math" and _IMPORT_LINE.search(r.get("source", ""))
                     for r in regions)
    if not has_import:
        # The sheet must also run as a plain notebook: keep the import
        # cell the worksheet itself hides.
        pre = doc.get("preamble") or {}
        cell = old.get(pre.get("id"))
        if cell is None or cell.cell_type != "code":
            cell = new_code_cell(PREAMBLE)
        cell.source = pre.get("source") or PREAMBLE
        cell.outputs, cell.execution_count = [], None
        cells.append(cell)

    count = 0
    for region in regions:
        want = "code" if region.get("kind") == "math" else "markdown"
        cell = old.get(region.get("id"))
        if cell is None or (cell.cell_type != want and not (
                want == "markdown" and cell.cell_type == "raw")):
            cell = new_code_cell() if want == "code" else new_markdown_cell()
            rid = str(region.get("id") or "")
            if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", rid) and rid not in {c.get("id") for c in cells}:
                cell["id"] = rid
        cell.source = region.get("source", "")
        place = {k: region[k] for k in ("x", "y", "w")
                 if isinstance(region.get(k), (int, float))}
        if place:
            cell.metadata["edsl"] = place
        else:
            cell.metadata.pop("edsl", None)
        if cell.cell_type == "code":
            count += 1
            cell.outputs = _clean_outputs(region.get("outputs"))
            cell.execution_count = count if cell.outputs else None
        cells.append(cell)
    nb.cells = cells
    nb.metadata["edsl_worksheet"] = {"version": FORMAT_VERSION,
                                     "settings": doc.get("settings") or {}}
    if nb.nbformat_minor < 5:
        nb.nbformat_minor = 5            # cell ids
    nbformat.validate(nb)

    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        nbformat.write(nb, fh)
    os.replace(tmp, path)
    if doc.get("trusted"):
        trust(path)
    return {"mtime": _stamp(path), "name": path.stem,
            "ids": [c.get("id") for c in cells]}


def trust(path: Path, expect_mtime=None) -> bool:
    """Record a notebook as trusted on this machine, without changing it.

    Called when the user has chosen to calculate a sheet (and after
    every save from here): the next time it opens calculated straight
    away.  This is Jupyter's own trust record, so JupyterLab agrees.
    The signature is taken from the file as it reads back, which is what
    :func:`load` will compare against.
    """
    if expect_mtime is not None and _stamp(path) != str(expect_mtime):
        return False                     # not the content the user saw
    notary = _notary()
    if notary is None:
        return False
    try:
        notary.sign(nbformat.read(str(path), as_version=4))
    except Exception:
        return False
    return True


_SKIP_DIRS = {"__pycache__", "node_modules", "build", "dist"}


def listing(root: Path, relative: str) -> dict:
    """Notebooks and folders under ``root/relative`` for the open dialog."""
    folder = resolve(root, relative)
    entries = []
    for entry in os.scandir(folder):
        name = entry.name
        if name.startswith(".") or name in _SKIP_DIRS or name.endswith(".egg-info"):
            continue
        try:
            if entry.is_dir():
                entries.append({"name": name, "type": "dir"})
            elif name.lower().endswith(".ipynb"):
                stat = entry.stat()
                entries.append({"name": name, "type": "notebook",
                                "mtime": stat.st_mtime, "size": stat.st_size})
        except OSError:
            continue
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    rel = folder.relative_to(root.resolve()).as_posix()
    return {"dir": "" if rel == "." else rel, "root": str(root.resolve()), "entries": entries}
