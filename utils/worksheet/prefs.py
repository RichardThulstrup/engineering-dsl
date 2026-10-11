"""How someone likes the page set up: kept on this computer, for every sheet.

Whether the grid is shown, the symbol panel open and text written as its
markdown is a matter of how a person works, not part of a worksheet, so
it is not stored in notebooks.
The browser cannot keep it either: its storage goes by port, and the
server takes the next free port when its usual one is busy.  It lives in
a small file in Jupyter's data folder, beside Jupyter's record of which
notebooks are trusted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

__all__ = ["DEFAULTS", "load", "save"]

DEFAULTS = {"grid": False, "symbols": False, "markdown": False}


def _file() -> Path:
    from jupyter_core.paths import jupyter_data_dir
    return Path(jupyter_data_dir()) / "edsl_worksheet.json"


def load() -> dict:
    """The preferences, with the default for whatever was never chosen."""
    try:
        stored = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    return {key: stored[key] if type(stored.get(key)) is type(default) else default
            for key, default in DEFAULTS.items()}


def save(changes: dict) -> dict:
    """Take over what ``changes`` says about the known preferences; returns them all."""
    chosen = load()
    chosen.update({key: value for key, value in changes.items()
                   if key in DEFAULTS and type(value) is type(DEFAULTS[key])})
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(chosen, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return chosen
