"""Generate the worksheet's library from the Windows symbol palette's list.

    python -m utils.worksheet.make_library

The palette (``SymbolPaletteWinUI``) keeps its tabs, sections and
buttons — what each one pastes, and what it says when the pointer rests
on it — in ``SymbolPaletteWinUI/Services/SymbolRepository.cs``.  The
worksheet offers the same library, read from
``static/data/library.json``.  That file is written here from the C#
list and never edited by hand, so the two cannot drift apart:
``tests/test_worksheet_library.py`` fails when it is out of date.
Re-run this after changing the palette's list.

Only the list's own, very regular notation is read (``new(…)``,
``S(…)``, ``E(…)``, ``Labelled(…)``, string literals joined with ``+``,
``null``); anything else is reported rather than guessed at.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

__all__ = ["SOURCE", "TARGET", "read_palette", "dump", "main"]

_REPO = Path(__file__).resolve().parents[2]
SOURCE = _REPO / "SymbolPaletteWinUI" / "Services" / "SymbolRepository.cs"
TARGET = Path(__file__).resolve().parent / "static" / "data" / "library.json"

_TOKEN = re.compile(r"""
    (?P<skip>\s+|//[^\n]*|/\*.*?\*/)
  | (?P<string>"(?:[^"\\\n]|\\.)*")
  | (?P<word>[A-Za-z_]\w*)
  | (?P<mark>[()\[\],:;+])
""", re.X | re.S)
_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|.)", re.S)
_PLAIN = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "'": "'", "\\": "\\"}


class _Reader:
    """The tokens of the list, with just enough grammar to walk it."""

    def __init__(self, text: str, start: int):
        self.text, self.at = text, start
        self.kind = self.value = None
        self.advance()

    def fail(self, wanted: str):
        line = self.text.count("\n", 0, self.at) + 1
        raise ValueError(f"{SOURCE.name}, line {line}: expected {wanted}, found {self.value!r}")

    def advance(self):
        while True:
            match = _TOKEN.match(self.text, self.at)
            if match is None:
                self.kind, self.value = "end", self.text[self.at:self.at + 20]
                return
            self.at = match.end()
            if match.lastgroup != "skip":
                self.kind, self.value = match.lastgroup, match.group()
                return

    def take(self, value: str):
        if self.value != value:
            self.fail(repr(value))
        self.advance()

    def maybe(self, value: str) -> bool:
        if self.value == value:
            self.advance()
            return True
        return False

    def string(self) -> str:
        """One or more literals joined with ``+``."""
        parts = []
        while True:
            if self.kind != "string":
                self.fail("a string")
            parts.append(_ESCAPE.sub(_unescape, self.value[1:-1]))
            self.advance()
            if not self.maybe("+"):
                return "".join(parts)

    def optional_string(self):
        return None if self.maybe("null") else self.string()

    def listed(self, item) -> list:
        """``[ item, item, … ]`` — a trailing comma is allowed."""
        self.take("[")
        items = []
        while not self.maybe("]"):
            items.append(item())
            if not self.maybe(","):
                self.take("]")
                break
        return items


def _unescape(match) -> str:
    code = match.group(1)
    if code[0] == "u" and len(code) == 5:
        return chr(int(code[1:], 16))
    if code not in _PLAIN:
        raise ValueError(f"{SOURCE.name}: string escape \\{code} is not handled")
    return _PLAIN[code]


def read_palette(path: Path = SOURCE) -> dict:
    """The palette's tabs, sections and entries, in its own order."""
    text = Path(path).read_text(encoding="utf-8")
    marker = re.search(r"\b_groups\s*=", text)
    if marker is None:
        raise ValueError(f"{path}: the list (_groups = [ … ]) was not found")
    reader = _Reader(text, marker.end())

    def entry() -> dict:
        maker = reader.value
        if maker not in ("E", "Labelled"):
            reader.fail("E(…) or Labelled(…)")
        reader.advance()
        reader.take("(")
        item = {"text": reader.string()}
        label = desc = detail = None
        if maker == "E":
            if reader.maybe(","):
                desc = reader.optional_string()
        else:
            reader.take(",")
            label = reader.optional_string()
            reader.take(",")
            desc = reader.optional_string()
            if reader.maybe(","):
                detail = reader.optional_string()
        reader.take(")")
        for key, value in (("label", label), ("desc", desc), ("detail", detail)):
            if value is not None:
                item[key] = value
        return item

    def section() -> dict:
        reader.take("S")
        reader.take("(")
        heading = reader.string()
        reader.take(",")
        entries = reader.listed(entry)
        reader.take(")")
        return {"heading": heading, "entries": entries}

    def tab() -> dict:
        reader.take("new")
        reader.take("(")
        made = {"name": reader.string(), "sections": []}
        while reader.maybe(","):
            field = reader.value
            reader.advance()
            reader.take(":")
            if field == "Sections":
                made["sections"] = reader.listed(section)
            elif field == "IconFile":
                reader.optional_string()        # the palette's own tab icon
            else:
                reader.fail("IconFile: or Sections:")
        reader.take(")")
        return made

    tabs = reader.listed(tab)
    reader.take(";")
    if not tabs or not all(t["sections"] and all(s["entries"] for s in t["sections"]) for t in tabs):
        raise ValueError(f"{path}: a tab or a section came out empty")
    return {"source": "/".join(Path(path).resolve().parts[-3:]), "tabs": tabs}


def dump(library: dict) -> str:
    """The library as JSON, one entry to a line (so that changes read well)."""
    def text(value) -> str:
        return json.dumps(value, ensure_ascii=False)

    def joined(items, indent):
        return ",\n".join(indent + item for item in items)

    tabs = []
    for tab in library["tabs"]:
        sections = [
            '{"heading": %s, "entries": [\n%s\n   ]}' % (
                text(section["heading"]), joined([text(e) for e in section["entries"]], "    "))
            for section in tab["sections"]]
        tabs.append('{"name": %s, "sections": [\n%s\n  ]}' % (text(tab["name"]), joined(sections, "   ")))
    return '{\n "source": %s,\n "tabs": [\n%s\n ]\n}\n' % (text(library["source"]), joined(tabs, "  "))


def main(argv=None) -> int:
    if not SOURCE.is_file():
        print(f"not found: {SOURCE}\n(the library is generated in a checkout of the repository, "
              "where the palette's source is)", file=sys.stderr)
        return 1
    library = read_palette()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with open(TARGET, "w", encoding="utf-8", newline="\n") as out:
        out.write(dump(library))
    entries = sum(len(s["entries"]) for t in library["tabs"] for s in t["sections"])
    print(f"{TARGET.relative_to(_REPO).as_posix()}: {len(library['tabs'])} tabs, {entries} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
