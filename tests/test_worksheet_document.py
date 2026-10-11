"""A worksheet is a notebook on disk: what the page adds must not cost the notebook anything."""
import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook, new_output
import pytest

from utils.worksheet import document


@pytest.fixture(autouse=True)
def private_trust_store(tmp_path, monkeypatch):
    # Trust is recorded in Jupyter's data directory; keep the tests out
    # of the real one.
    monkeypatch.setenv("JUPYTER_DATA_DIR", str(tmp_path / "jupyter-data"))


def write(path, cells, **metadata):
    nb = new_notebook(cells=cells)
    nb.metadata.update(metadata)
    nbformat.write(nb, str(path))
    return path


def notebook(tmp_path):
    return write(tmp_path / "sheet.ipynb", [
        new_markdown_cell("# Divider"),
        new_code_cell("from utils.Engineer import *"),
        new_code_cell("V_in := 12.0 V\nV_out := V_in / 2", metadata={"tags": ["keep-me"]}),
    ])


def test_cells_become_regions_and_the_import_cell_is_kept_out_of_sight(tmp_path):
    doc = document.load(notebook(tmp_path))
    assert [(r["kind"], r["source"].splitlines()[0]) for r in doc["regions"]] == [
        ("text", "# Divider"), ("math", "V_in := 12.0 V")]
    assert doc["preamble"]["source"] == "from utils.Engineer import *"
    assert all(r["x"] is None and r["y"] is None for r in doc["regions"])      # never laid out


def test_an_import_cell_that_does_more_is_an_ordinary_region(tmp_path):
    path = write(tmp_path / "legacy.ipynb", [
        new_code_cell("from utils.Engineer import *\nset_syntax_mode('legacy')")])
    doc = document.load(path)
    assert doc["preamble"] is None and len(doc["regions"]) == 1
    document.save(path, doc)
    assert len(nbformat.read(str(path), as_version=4).cells) == 1              # none added


def test_saving_keeps_the_notebook_a_notebook(tmp_path):
    path = notebook(tmp_path)
    doc = document.load(path)
    text, math = doc["regions"]
    text.update(x=0, y=0, w=400)
    math.update(x=40, y=64)
    math["outputs"] = [{"output_type": "display_data",
                        "data": {"text/latex": "$V_{out} = 6.0\\ \\mathrm{V}$",
                                 "text/plain": "V_out = 6.0 V"}}]
    doc["regions"].append({"id": "new1", "kind": "math", "source": "V_out ▸ mV", "x": 40, "y": 120})
    document.save(path, doc)

    nb = nbformat.read(str(path), as_version=4)
    nbformat.validate(nb)
    assert [c.cell_type for c in nb.cells] == ["code", "markdown", "code", "code"]
    assert nb.cells[0].source == "from utils.Engineer import *"                # still runs in Jupyter
    assert nb.cells[2].metadata["tags"] == ["keep-me"]                         # what it carried, it keeps
    assert nb.cells[2].metadata["edsl"] == {"x": 40, "y": 64}
    assert nb.cells[1].metadata["edsl"] == {"x": 0, "y": 0, "w": 400}
    assert nb.cells[2].outputs[0].data["text/plain"] == "V_out = 6.0 V"
    assert nb.cells[3].id == "new1"

    again = document.load(path)
    assert [(r["x"], r["y"]) for r in again["regions"]] == [(0, 0), (40, 64), (40, 120)]
    assert again["preamble"]["id"] == doc["preamble"]["id"]


def test_a_sheet_that_was_never_arranged_is_saved_without_positions(tmp_path):
    path = notebook(tmp_path)
    doc = document.load(path)
    document.save(path, doc)
    assert all("edsl" not in c.metadata for c in nbformat.read(str(path), as_version=4).cells)


def test_a_new_sheet_gets_the_import_cell(tmp_path):
    path = tmp_path / "new.ipynb"
    document.save(path, {"regions": [{"id": "a1", "kind": "math", "source": "x := 1", "x": 0, "y": 0}]})
    nb = nbformat.read(str(path), as_version=4)
    assert [c.source for c in nb.cells] == ["from utils.Engineer import *", "x := 1"]
    assert nb.metadata["kernelspec"]["name"] == "python3"


def test_saved_output_shown_before_calculating_is_inert(tmp_path):
    cell = new_code_cell("x", outputs=[
        new_output("display_data", data={"text/html": "<img src=x onerror=alert(1)>",
                                         "text/plain": "1"}),
        new_output("display_data", data={"application/javascript": "alert(1)"}),
        new_output("stream", name="stdout", text="hello\n"),
    ])
    doc = document.load(write(tmp_path / "foreign.ipynb", [cell]))
    saved = doc["regions"][0]["saved"]
    assert saved == [{"type": "display", "data": {"text/plain": "1"}},
                     {"type": "stream", "name": "stdout", "text": "hello\n"}]


def test_trust_is_given_by_saving_or_by_choosing_to_calculate(tmp_path):
    path = notebook(tmp_path)
    assert document.load(path)["trusted"] is False        # came from somewhere else
    doc = document.load(path)
    document.save(path, doc)                              # saved, but never calculated here
    assert document.load(path)["trusted"] is False
    assert document.trust(path, expect_mtime=document.load(path)["mtime"])
    assert document.load(path)["trusted"] is True
    doc = document.load(path)
    doc["regions"][1]["source"] = "V_in := 24.0 V"
    document.save(path, doc)                              # ``trusted`` travels with the document
    assert document.load(path)["trusted"] is True


def test_trust_is_not_given_to_a_file_that_changed_meanwhile(tmp_path):
    path = notebook(tmp_path)
    assert not document.trust(path, expect_mtime="1")
    assert document.load(path)["trusted"] is False


def test_a_file_changed_by_another_program_is_not_overwritten(tmp_path):
    path = notebook(tmp_path)
    doc = document.load(path)
    stale = doc["mtime"]
    document.save(path, doc, expect_mtime=stale)          # our own save moves the stamp on
    with pytest.raises(document.Conflict):
        document.save(path, doc, expect_mtime=stale)
    document.save(path, doc, expect_mtime=stale, force=True)


def test_paths_stay_inside_the_worksheet_folder(tmp_path):
    root = tmp_path / "sheets"
    (root / "sub").mkdir(parents=True)
    assert document.resolve(root, "sub/a.ipynb") == (root / "sub" / "a.ipynb").resolve()
    for escape in ("../secret.ipynb", "sub/../../secret.ipynb", str(tmp_path / "elsewhere.ipynb")):
        with pytest.raises(PermissionError):
            document.resolve(root, escape)


def test_listing_shows_notebooks_and_folders_only(tmp_path):
    notebook(tmp_path)
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "project").mkdir()
    (tmp_path / ".ipynb_checkpoints").mkdir()
    (tmp_path / "__pycache__").mkdir()
    names = [(e["name"], e["type"]) for e in document.listing(tmp_path, "")["entries"]]
    assert names == [("project", "dir"), ("sheet.ipynb", "notebook")]


# Pictures: a notebook keeps one as an attachment of a markdown cell,
# base64 whatever its kind.  (Any base64 will do here: nothing looks inside.)
PNG, OTHER, DRAWING = "iVBORw0KGgo=", "R0lGODlhAQABAAAAACw=", "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciLz4="


def pictured(tmp_path):
    """A text cell with two pictures, and two attachments that are none."""
    return write(tmp_path / "pictures.ipynb", [new_markdown_cell(
        "![one](attachment:one.png) and ![two](attachment:two.svg)", attachments={
            "one.png": {"image/png": PNG[:4] + "\n" + PNG[4:] + "\n"},      # broken into lines, as some tools do
            "two.svg": {"image/png": PNG, "image/svg+xml": DRAWING},      # a drawing, and its bitmap copy
            "notes.pdf": {"application/pdf": PNG},
            "odd.svg": {"image/svg+xml": "<svg xmlns='http://www.w3.org/2000/svg'/>"}})])     # not base64


def attached(path):
    cell = nbformat.read(str(path), as_version=4).cells[-1]
    return {name: dict(bundle) for name, bundle in cell.get("attachments", {}).items()}


def test_the_pictures_attached_to_a_text_cell_come_with_its_region(tmp_path):
    region = document.load(pictured(tmp_path))["regions"][0]
    assert region["attachments"] == {"one.png": {"image/png": PNG},
                                     "two.svg": {"image/png": PNG, "image/svg+xml": DRAWING}}


def test_saving_keeps_the_pictures_the_page_still_has_and_lets_go_of_the_others(tmp_path):
    path = pictured(tmp_path)
    before = attached(path)
    doc = document.load(path)
    region = doc["regions"][0]
    region["source"] = "![one](attachment:one.png) and ![new](attachment:new.gif)"
    region["attachments"] = {"one.png": region["attachments"]["one.png"], "new.gif": {"image/gif": OTHER}}
    assert document.save(path, doc)["pictures"] == 2        # it says how many it took
    nbformat.validate(nbformat.read(str(path), as_version=4))
    assert attached(path) == {"one.png": {"image/png": PNG}, "new.gif": {"image/gif": OTHER},
                              # What the page was never given, it has not touched.
                              "notes.pdf": before["notes.pdf"], "odd.svg": before["odd.svg"]}

    region["attachments"] = {}                              # the text names no picture any more
    document.save(path, doc)
    assert attached(path) == {"notes.pdf": before["notes.pdf"], "odd.svg": before["odd.svg"]}


def test_a_page_that_says_nothing_about_pictures_changes_none(tmp_path):
    path = pictured(tmp_path)
    before = attached(path)
    doc = document.load(path)
    del doc["regions"][0]["attachments"]                    # as a page from before pictures would save
    assert document.save(path, doc)["pictures"] == 0
    assert attached(path) == before


def test_only_pictures_are_taken_from_the_page(tmp_path):
    path = tmp_path / "new.ipynb"
    region = {"id": "p1", "kind": "text", "source": "![a](attachment:a.png)", "x": 0, "y": 0, "w": 132,
              "attachments": {"a.png": {"image/png": PNG, "text/html": "<script>alert(1)</script>"},
                              "b.png": {"image/png": "\"><script>alert(1)</script>"},     # no base64
                              "c.html": {"text/html": PNG}, "d.png": "iVBORw0KGgo=", "": {"image/png": PNG}}}
    assert document.save(path, {"regions": [region]})["pictures"] == 1     # of the five it was offered
    nbformat.validate(nbformat.read(str(path), as_version=4))
    assert attached(path) == {"a.png": {"image/png": PNG}}
    assert document.load(path)["regions"][0]["attachments"] == {"a.png": {"image/png": PNG}}

    region["attachments"] = {}
    document.save(path, {"regions": [region]})
    assert "attachments" not in nbformat.read(str(path), as_version=4).cells[-1]


# ---------------------------------------------------------------------------
# values: what a sheet delivers to other sheets, and what a region was given
# ---------------------------------------------------------------------------

VALUES = [{"name": "U", "form": '["f","5.5"]', "shown": {"text/plain": "5.5", "text/latex": "$5.5$"}},
          {"name": "f", "why": "a function"}]


def metadata(path):
    return nbformat.read(str(path), as_version=4).metadata["edsl_worksheet"]


def test_what_a_sheets_variables_were_is_kept_with_its_revision(tmp_path):
    path = tmp_path / "source.ipynb"
    sheet = {"regions": [{"id": "r1", "kind": "math", "source": "U := 5.5", "x": 0, "y": 0}],
             "settings": {"about": {"revision": 3, "saved": "2026-10-11T10:00:00.000Z"}}}
    # A sheet that was never saved calculated delivers no values — and says which revision it is all the same.
    document.save(path, sheet)
    assert "values" not in metadata(path)
    assert document.published(path) == {"name": "source", "revision": 3, "saved": "2026-10-11T10:00:00.000Z", "values": None}
    # Saved with them, it keeps them — as far as they are values by name:
    # the name as it is typeset, text and how it is shown, or why there is
    # none to hand on.  A name is one a sheet can define, and is there once.
    typeset = {"name": "R_tol", "tex": "R_{\\mathrm{tol}}", "form": '["f","0.05"]', "shown": {"text/plain": "5%"}}
    offered = VALUES + [{"name": "not a name!", "form": "x"}, {"name": "x"}, {"name": 5, "form": "x"}, "junk", None,
                        {"name": "V", "form": '["i","1"]', "shown": {"text/html": "<b>no</b>", "text/plain": 7}},
                        {"name": "_kept", "form": "x"}, {"name": "__builtins__", "form": "x"}, {"name": "class", "form": "x"},
                        {"name": "U", "form": "again"}, {"name": "f", "form": "now a value"}, dict(typeset, more="than it has"),
                        {"name": "g", "tex": 5, "why": "a function"}, {"name": "h", "tex": "h", "form": 5, "why": 6}]
    said = document.save(path, dict(sheet, values={"revision": 3, "saved": "2026-10-11T10:00:00.000Z", "toolkit": "0.1.0",
                                                   "values": offered, "more": "than a record has"}))
    assert said["values"] == 3
    record = {"revision": 3, "saved": "2026-10-11T10:00:00.000Z", "toolkit": "0.1.0",
              "values": VALUES + [{"name": "V", "form": '["i","1"]', "shown": {}}, typeset, {"name": "g", "why": "a function"}]}
    assert metadata(path)["values"] == record
    assert document.published(path) == {"name": "source", "revision": 3, "saved": "2026-10-11T10:00:00.000Z", "values": record}
    assert document.published(path, brief=True)["values"] == {"revision": 3, "saved": "2026-10-11T10:00:00.000Z", "count": 3}
    # A save that says nothing of values — the sheet was not calculated —
    # keeps the ones there are: those of the revision they were taken at.
    later = dict(sheet, settings={"about": {"revision": 4, "saved": "2026-10-12T08:00:00.000Z"}})
    assert document.save(path, later)["values"] == 3
    assert metadata(path)["values"] == record and document.published(path)["revision"] == 4
    assert document.published(path, brief=True)["values"]["revision"] == 3
    # One that says there are none takes them out; what is no record is none.
    assert document.save(path, dict(later, values=None))["values"] is None and "values" not in metadata(path)
    assert document.save(path, dict(later, values=["U"]))["values"] is None and "values" not in metadata(path)
    document.save(path, dict(later, values={"revision": {"no": 1}, "saved": 5, "values": "U"}))
    assert metadata(path)["values"] == {"revision": None, "saved": "", "toolkit": "", "values": []}
    # A notebook that is no worksheet delivers nothing, and says so plainly.
    plain = write(tmp_path / "plain.ipynb", [new_code_cell("x = 1")])
    assert document.published(plain) == {"name": "plain", "revision": None, "saved": "", "values": None}
    odd = write(tmp_path / "odd.ipynb", [new_code_cell("x = 1")], edsl_worksheet={"settings": "none", "values": [1, 2]})
    assert document.published(odd) == {"name": "odd", "revision": None, "saved": "", "values": None}


def test_what_a_revision_delivers_is_what_it_delivered_first(tmp_path):
    path = tmp_path / "source.ipynb"
    sheet = {"regions": [{"id": "r1", "kind": "math", "source": "U := now()", "x": 0, "y": 0}],
             "settings": {"about": {"revision": 3, "saved": "2026-10-11T10:00:00.000Z"}}}
    first = {"revision": 3, "saved": "2026-10-11T10:00:00.000Z", "toolkit": "0.1.0", "values": [VALUES[0]]}
    assert document.save(path, dict(sheet, values=first))["values"] == 1
    # Saved again as it is — the same revision, of the same moment — the
    # sheet keeps the values it has, whatever a calculation on another day
    # makes of it: a sheet that names the revision gets what others got.
    later = dict(first, toolkit="0.2.0", values=[{"name": "U", "form": '["f","6.5"]', "shown": {"text/plain": "6.5"}}, VALUES[1]])
    assert document.save(path, dict(sheet, values=later))["values"] == 1 and metadata(path)["values"] == first
    # Another revision is new values; so is the same one saved at another
    # moment (a revision that was typed is not counted on).
    for stamp in ({"revision": 4}, {"revision": "3"}, {"saved": "2026-10-11T11:00:00.000Z"}):
        document.save(path, dict(sheet, values=first))
        assert metadata(path)["values"] == first
        assert document.save(path, dict(sheet, values=dict(later, **stamp)))["values"] == 1
        assert metadata(path)["values"] == dict(later, **stamp)
        document.save(path, dict(sheet, values=None))
    # (Values that say of no moment when they were saved are of no revision that could be named.)
    document.save(path, dict(sheet, values=dict(first, saved="")))
    document.save(path, dict(sheet, values=dict(later, saved="")))
    assert metadata(path)["values"] == dict(later, saved="")


def test_what_a_region_was_given_is_kept_with_its_cell(tmp_path):
    path = tmp_path / "taking.ipynb"
    given = {"sheet": "source.ipynb", "name": "source", "revision": 3, "saved": "2026-10-11T10:00:00.000Z",
             "values": [VALUES[0]]}
    code = "from utils.worksheet.values import given as _given\n(U,) = _given('{}')"
    document.save(path, {"regions": [
        {"id": "g1", "kind": "math", "source": code, "x": 16, "y": 32, "given": dict(given, more="than it has")},
        {"id": "t1", "kind": "text", "source": "words", "x": 16, "y": 96, "given": given},          # (text is given nothing)
        {"id": "g2", "kind": "math", "source": "a := 1", "x": 16, "y": 160, "given": {"sheet": "", "values": [VALUES[0]]}},
        {"id": "g3", "kind": "math", "source": "b := 1", "x": 16, "y": 200, "given": {"sheet": "s.ipynb", "values": [VALUES[1]]}},
        {"id": "g4", "kind": "math", "source": "c := 1", "x": 16, "y": 240, "given": "source.ipynb"},
        {"id": "g5", "kind": "math", "source": "d := 1", "given": dict(given, revision="B", name=7, saved=None)}]})
    cells = {cell["id"]: cell for cell in nbformat.read(str(path), as_version=4).cells}
    assert cells["g1"].metadata["edsl"] == {"x": 16, "y": 32, "given": given} and cells["g1"].source == code
    assert all(cells[name].metadata["edsl"] == {"x": 16, "y": y} for name, y in (("t1", 96), ("g2", 160), ("g3", 200), ("g4", 240)))
    # (A region that flows — no place of its own yet — is given values all the same.)
    assert cells["g5"].metadata["edsl"] == {"given": dict(given, revision="B", name="", saved="")}
    regions = {region["id"]: region for region in document.load(path)["regions"]}
    assert regions["g1"]["given"] == given and regions["g5"]["given"]["revision"] == "B"
    assert not any("given" in regions[name] for name in ("t1", "g2", "g3", "g4"))
    # What a file from elsewhere says a region was given is read as far as it is that.
    cells["g1"].metadata["edsl"]["given"] = {"sheet": "x.ipynb", "values": [{"name": "U", "form": 5}]}
    cells["g5"].metadata["edsl"]["given"] = ["source.ipynb"]
    nb = nbformat.read(str(path), as_version=4)
    for cell in nb.cells:
        if cell["id"] in ("g1", "g5"):
            cell.metadata["edsl"]["given"] = cells[cell["id"]].metadata["edsl"]["given"]
    nbformat.write(nb, str(path))
    assert not any("given" in region for region in document.load(path)["regions"])
