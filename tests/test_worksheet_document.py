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
