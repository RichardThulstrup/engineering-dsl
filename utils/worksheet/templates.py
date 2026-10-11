"""Headers and footers someone made their own: kept on this computer, by name.

A template is the header and the footer of a page — their text, the
logo, the way dates are written — just as a sheet holds them (see
``static/js/pageframe.js``), under a name.  The ready-made ones are part
of the page.  Those a user saves are that person's, for every sheet, so
they live where the preferences do: in a file in Jupyter's data folder.
A sheet that is given a template gets a copy of it in its own file, and
goes on without the template being there.

What a frame may hold is the page's to say, and the page reads every
template again before it uses one.  Here a template only has to be a
JSON object of a size that a logo explains, under a name that is one.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

__all__ = ["load", "save", "delete", "LIMIT", "MOST"]

LIMIT = 4_000_000       # characters of one template: a logo may be 2 MB, which is 2.7 million of them
MOST = 100              # templates


def _file() -> Path:
    from jupyter_core.paths import jupyter_data_dir
    return Path(jupyter_data_dir()) / "edsl_worksheet_templates.json"


def _name(name) -> str:
    """A template's name, or ValueError: a line of one to sixty characters."""
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60 or not name.isprintable():
        raise ValueError("a template's name is a line of up to 60 characters")
    return name.strip()


def load() -> list:
    """The templates, in the order of their names: ``[{"name": ..., "frame": {...}}, ...]``."""
    try:
        stored = json.loads(_file().read_text(encoding="utf-8"))["templates"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    kept = {}
    for entry in stored if isinstance(stored, list) else []:
        try:
            name = _name(entry["name"])
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(entry.get("frame"), dict):
            kept[name] = {"name": name, "frame": entry["frame"]}
    return _in_order(kept.values())


def _in_order(templates) -> list:
    return sorted(templates, key=lambda entry: (entry["name"].casefold(), entry["name"]))


def _write(templates: list) -> list:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"templates": _in_order(templates)}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return load()


def save(name, frame) -> list:
    """Keep ``frame`` under ``name``, in place of one of that name; returns them all.

    Raises ValueError for what is no template: a name that is none, a
    frame that is no JSON object or is larger than any could be, or one
    template too many.
    """
    name = _name(name)
    if not isinstance(frame, dict):
        raise ValueError("a template is the header and footer of a page")
    if len(json.dumps(frame, ensure_ascii=False)) > LIMIT:
        raise ValueError("that template is too large to keep: its logo can be up to 2 MB")
    others = [entry for entry in load() if entry["name"] != name]
    if len(others) >= MOST:
        raise ValueError(f"{MOST} templates are kept already: delete one first")
    return _write(others + [{"name": name, "frame": frame}])


def delete(name) -> list:
    """Let go of the template called ``name`` (if there is one); returns those that are left."""
    kept = load()
    left = [entry for entry in kept if entry["name"] != name]
    return _write(left) if len(left) != len(kept) else kept
