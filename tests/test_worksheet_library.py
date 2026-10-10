"""The worksheet's library is the Windows symbol palette's list, mechanically.

The palette keeps its tabs and buttons in a C# source file; the worksheet
reads a JSON file generated from it (``python -m utils.worksheet.make_library``).
These tests hold the two together, and check the generator's reading of
the C# notation.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from utils.worksheet import make_library

ROOT = Path(__file__).resolve().parents[1]


def entries(library):
    return [entry for tab in library["tabs"] for section in tab["sections"] for entry in section["entries"]]


def test_the_library_file_is_what_the_palette_lists_today():
    if not make_library.SOURCE.is_file():
        pytest.skip("the palette's source is not here")
    fresh = make_library.dump(make_library.read_palette())
    stored = make_library.TARGET.read_text(encoding="utf-8")
    assert stored == fresh, ("the palette's list changed: run  python -m utils.worksheet.make_library  "
                             "to bring the worksheet's library up to date")


def test_the_library_keeps_every_tab_section_and_entry():
    library = json.loads(make_library.TARGET.read_text(encoding="utf-8"))
    assert [tab["name"] for tab in library["tabs"]][:4] == ["Math", "Greek", "Sub/Sup", "Engineering"]
    assert len(library["tabs"]) >= 15 and len(entries(library)) >= 600
    by_text = {entry["text"]: entry for entry in entries(library)}
    assert by_text["bode(, , (, ))"]["desc"].startswith("Bode plot")
    assert by_text["▸ gal_us"]["label"] == "▸ gal (US)"                 # face and paste-text differ
    assert "for j in 1..10:\n    " in by_text                           # the newline is a newline
    assert by_text["⇥"]["detail"].startswith("Inserts the tab glyph")   # joined from two literals
    assert 'plot(, , theme="darkgrid")' in by_text                      # quotes inside a string


PALETTE = '''
namespace Demo;
public static class SymbolRepository
{
    private static SymbolEntry E(string text, string? desc = null) => new(text, desc);   // not the list
    private static readonly IReadOnlyList<SymbolGroup> _groups =
    [
        // a comment, with "quotes" and E("nothing")
        new("One", IconFile: "one.svg", Sections: [
            S("First", [
                E("π", "pi"), E("₀"),
                E("say \\"hi\\"", "a \\\\ and a tab\\t"),
                Labelled("\\u21b5", "NL", "newline", "long "
                    + "text"),
                Labelled("x", null, null),
            ]),
        ]),
        new("Two", Sections: [S("", [E("for:\\n    ", "loop")])]),
    ];
}
'''


def test_the_list_is_read_as_it_is_written(tmp_path):
    source = tmp_path / "SymbolRepository.cs"
    source.write_text(PALETTE, encoding="utf-8")
    library = make_library.read_palette(source)
    assert library["tabs"] == [
        {"name": "One", "sections": [{"heading": "First", "entries": [
            {"text": "π", "desc": "pi"},
            {"text": "₀"},
            {"text": 'say "hi"', "desc": "a \\ and a tab\t"},
            {"text": "↵", "label": "NL", "desc": "newline", "detail": "long text"},
            {"text": "x"},
        ]}]},
        {"name": "Two", "sections": [{"heading": "", "entries": [{"text": "for:\n    ", "desc": "loop"}]}]},
    ]
    written = make_library.dump(library)
    assert json.loads(written) == library                               # it reads back,
    assert sum(line.lstrip().startswith('{"text"') for line in written.splitlines()) == 6   # one entry to a line


@pytest.mark.parametrize("change,complaint", [
    (('E("₀")', 'Glyph("₀")'), r"line 11: expected E\(…\) or Labelled\(…\), found 'Glyph'"),
    (('E("π", "pi")', 'E("π", $"pi")'), r"line 11: expected a string"),
    (('"a \\\\ and a tab\\t"', '"a \\q"'), r"escape \\q is not handled"),
    (('Sections: [S("", [E("for:\\n    ", "loop")])]', "Sections: []"), "came out empty"),
])
def test_what_the_reader_does_not_understand_it_reports(tmp_path, change, complaint):
    source = tmp_path / "SymbolRepository.cs"
    assert change[0] in PALETTE
    source.write_text(PALETTE.replace(*change), encoding="utf-8")
    with pytest.raises(ValueError, match=complaint):
        make_library.read_palette(source)


def test_a_sheet_can_type_every_character_of_the_library():
    """A sheet does not lean on the Windows palette: its own keys, and its
    backslash names too, reach every character the palette's buttons paste."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    library = json.loads(make_library.TARGET.read_text(encoding="utf-8"))
    pasted = {ch for entry in entries(library) for ch in entry["text"] if ord(ch) > 127}
    assert len(pasted) > 150
    run = subprocess.run(
        [node, "--input-type=module", "-e",
         "import {PALETTE, COMPLETIONS} from './utils/worksheet/static/js/symbols.js';"
         "console.log(JSON.stringify({keys: PALETTE.flatMap((g) => g.keys),"
         " 'backslash names': COMPLETIONS.map((c) => c.glyph)}))"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert run.returncode == 0, run.stderr
    for way, glyphs in json.loads(run.stdout).items():
        missing = pasted - set("".join(glyphs))
        assert not missing, f"the sheet's {way} cannot type: " + " ".join(sorted(missing))
