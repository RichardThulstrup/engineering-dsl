"""Worksheets on disk: ordinary notebooks with a page layout.

A worksheet *is* an ``.ipynb`` file.  Each region is a cell — math
regions are code cells, text regions are markdown cells — stored in
reading order, so JupyterLab, VS Code and ``hardcopy()`` run the very
same calculation.  Where a region sits on the page is kept in the
cell's metadata (``edsl``), which other tools carry along untouched.
A picture is kept the way JupyterLab keeps a pasted one: attached to
the text cell that names it.

A sheet delivers values to other sheets: what its variables were when
it was saved is kept in the notebook's metadata, with the revision that
save made (``values``), and a region that was given values by another
sheet says in its cell's metadata where they are from (``given``).
Both are text and data — see ``values.py`` — and nothing here reads
more out of them than that they have the shape they should.

Opening a sheet does not, by itself, run it: a notebook that was not
last saved on this machine (Jupyter's own trust record decides) is
shown typeset but uncalculated until the user asks.
"""
from __future__ import annotations

import keyword
import os
import re
import uuid
from pathlib import Path

from .protocol import PREAMBLE

__all__ = ["load", "save", "trust", "listing", "published", "Conflict", "resolve", "warm_up"]

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

# Pictures a text region may carry: the attachments of its markdown cell,
# base64 whatever their kind.  The page shows them through <img> only,
# where a drawing (SVG) can neither run a script nor fetch anything.
_PICTURE_MIMES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp",
                  "image/svg+xml")
_BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}\Z")

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


def _pictures(attachments) -> dict:
    """The pictures among a cell's attachments, as {name: {type: base64}}.

    This is what the page is given, and so what it answers for when the
    sheet is saved; anything else attached to a cell is not its business.
    """
    found = {}
    for name, bundle in (attachments or {}).items():
        if not (isinstance(name, str) and name.strip() and isinstance(bundle, dict)):
            continue
        kept = {}
        for mime, data in bundle.items():
            if mime in _PICTURE_MIMES and isinstance(data, str):
                data = "".join(data.split())            # some tools break base64 into lines
                if _BASE64.match(data):
                    kept[mime] = data
        if kept:
            found[name] = kept
    return found


def _attach(cell, offered):
    """Give a text cell the pictures its region holds now.

    ``offered`` is what the page has for the region: the pictures its
    text still names.  They take the place of the pictures the page was
    given; whatever else is attached to the cell stays.  A page that says
    nothing about pictures (``None``) changes none.  Returns how many
    pictures were taken from the page.
    """
    if not isinstance(offered, dict):
        return 0
    old = cell.get("attachments") or {}
    given, offered = _pictures(old), _pictures(offered)
    attachments = {}
    for name in [*old, *(name for name in offered if name not in old)]:
        bundle = {mime: data for mime, data in (old.get(name) or {}).items()
                  if mime not in given.get(name, {})}
        bundle.update(offered.get(name, {}))
        if bundle:
            attachments[name] = bundle
    if attachments:
        cell["attachments"] = nbformat.from_dict(attachments)
    else:
        cell.pop("attachments", None)
    return len(offered)


def _shown(shown) -> dict:
    """How a value is displayed: its plain text and its LaTeX, no more."""
    if not isinstance(shown, dict):
        return {}
    return {kind: shown[kind] for kind in ("text/plain", "text/latex") if isinstance(shown.get(kind), str)}


def _name(name) -> bool:
    """Is this the name of a variable a sheet hands on?  A name as a
    sheet defines them: not one it keeps to itself (``_x``), which the
    kernel's own are, too."""
    return isinstance(name, str) and name.isidentifier() and not keyword.iskeyword(name) and not name.startswith("_")


def _values(listed) -> list:
    """Values by name, as far as they are that: each with its name as it
    is typeset (``tex``), and with its text (``form``) and its display —
    or with why it is not offered.  A name is there once."""
    kept, seen = [], set()
    for entry in listed if isinstance(listed, list) else []:
        if not (isinstance(entry, dict) and _name(entry.get("name"))) or entry["name"] in seen:
            continue
        one = {"name": entry["name"]}
        if isinstance(entry.get("tex"), str):
            one["tex"] = entry["tex"]
        if isinstance(entry.get("form"), str):
            one.update(form=entry["form"], shown=_shown(entry.get("shown")))
        elif isinstance(entry.get("why"), str):
            one["why"] = entry["why"]
        else:
            continue
        seen.add(one["name"])
        kept.append(one)
    return kept


def _revision(value):
    """A revision as it is counted or was typed: a number, or text — else ``None``."""
    if isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    return None


def _given(given):
    """What a region says of the values it was given: the sheet they are
    from (a path beside this sheet's own), that sheet's name and revision
    then, and the values — or ``None`` for what is no such thing."""
    if not (isinstance(given, dict) and isinstance(given.get("sheet"), str) and given["sheet"]):
        return None
    values = [entry for entry in _values(given.get("values")) if "form" in entry]
    if not values:
        return None
    return {"sheet": given["sheet"], "name": given["name"] if isinstance(given.get("name"), str) else "",
            "revision": _revision(given.get("revision")),
            "saved": given["saved"] if isinstance(given.get("saved"), str) else "", "values": values}


def _record(record):
    """What a sheet's variables were at a revision, as it is kept in the
    file — or ``None``."""
    if not isinstance(record, dict):
        return None
    return {"revision": _revision(record.get("revision")),
            "saved": record["saved"] if isinstance(record.get("saved"), str) else "",
            "toolkit": record["toolkit"] if isinstance(record.get("toolkit"), str) else "",
            "values": _values(record.get("values"))}


def published(path: Path, brief: bool = False) -> dict:
    """What the sheet in ``path`` delivers to other sheets: its name, its
    revision, and the values its variables had when it was last saved
    calculated (``values`` — ``None`` for a sheet that was never saved so).

    ``brief`` leaves the values themselves out: enough to tell whether a
    sheet has moved on since values were taken from it.  The file is
    read as the text it is; nothing of it is run.
    """
    import json
    meta = json.loads(path.read_text(encoding="utf-8")).get("metadata") or {}
    sheet = meta.get("edsl_worksheet") if isinstance(meta.get("edsl_worksheet"), dict) else {}
    settings = sheet.get("settings") if isinstance(sheet.get("settings"), dict) else {}
    about = settings.get("about") if isinstance(settings.get("about"), dict) else {}
    record = _record(sheet.get("values"))
    if record is not None and brief:
        record = {"revision": record["revision"], "saved": record["saved"], "count": sum("form" in e for e in record["values"])}
    return {"name": path.stem, "revision": _revision(about.get("revision")),
            "saved": about["saved"] if isinstance(about.get("saved"), str) else "", "values": record}


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
            given = _given(place.get("given"))
            if given:
                region["given"] = given
        else:
            pictures = _pictures(cell.get("attachments"))
            if pictures:
                region["attachments"] = pictures
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

    count = pictures = 0
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
        given = _given(region.get("given")) if want == "code" else None
        if given:
            place["given"] = given
        if place:
            cell.metadata["edsl"] = place
        else:
            cell.metadata.pop("edsl", None)
        if cell.cell_type == "code":
            count += 1
            cell.outputs = _clean_outputs(region.get("outputs"))
            cell.execution_count = count if cell.outputs else None
        else:
            pictures += _attach(cell, region.get("attachments"))
        cells.append(cell)
    nb.cells = cells
    # What the sheet's variables are at this save — or, said by a page that
    # could not ask (the sheet is not calculated), what they were before.
    before = nb.metadata.get("edsl_worksheet")
    had = _record(before.get("values")) if isinstance(before, dict) else None
    record = _record(doc["values"]) if "values" in doc else had
    # What a revision delivers is what it delivered first.  A sheet that is
    # saved again as it is — the same revision, of the same moment — keeps
    # the values it has, whatever a calculation on another day makes of it
    # (the time, a random number, a file that has changed): another sheet
    # that names the revision gets what the sheets before it got.
    if record and had and had["saved"] and (record["revision"], record["saved"]) == (had["revision"], had["saved"]):
        record = had
    nb.metadata["edsl_worksheet"] = {"version": FORMAT_VERSION,
                                     "settings": doc.get("settings") or {}}
    if record is not None:
        nb.metadata["edsl_worksheet"]["values"] = record
    if nb.nbformat_minor < 5:
        nb.nbformat_minor = 5            # cell ids
    nbformat.validate(nb)

    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        nbformat.write(nb, fh)
    os.replace(tmp, path)
    if doc.get("trusted"):
        trust(path)
    # (How many pictures were stored is said, so that a page can tell when
    # one it sent was turned down — or when a server from before pictures
    # could be kept has answered it.)
    return {"mtime": _stamp(path), "name": path.stem,
            "ids": [c.get("id") for c in cells], "pictures": pictures,
            "values": None if record is None else sum("form" in e for e in record["values"])}


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
