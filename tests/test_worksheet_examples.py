"""The example sheets (examples/) and the sheets a new sheet can start from
(utils/worksheet/starters/): each calculates without an error and hands on
what its file says it does, and the sheets that take values from others
have what those deliver now.

    python -m pytest tests/test_worksheet_examples.py -q
"""
from pathlib import Path

import pytest

from utils.worksheet import document
from worksheet_kernel import Kernel

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").rglob("*.ipynb"))
SHEETS = EXAMPLES + sorted(document.STARTERS.glob("*.ipynb"))


@pytest.fixture(scope="module")
def kernel(tmp_path_factory):
    k = Kernel(tmp_path_factory.mktemp("examples-kernel"))
    yield k
    k.close()


def test_there_are_examples_of_both_templates_and_a_chain_of_three_levels():
    titles = {path.relative_to(ROOT / "examples").as_posix(): document.load(path)["settings"]["frame"]["header"][1]
              for path in EXAMPLES}
    assert sum(header.startswith("**Technical note**") for header in titles.values()) >= 1
    assert sum(header.startswith("**Power requirements**") for header in titles.values()) >= 3
    # system takes from controller, which takes from the boards: three levels.
    taken = {path.stem: {region["given"]["sheet"] for region in document.load(path)["regions"] if "given" in region}
             for path in EXAMPLES}
    assert taken["system"] >= {"controller.ipynb"} and taken["controller"]


@pytest.mark.parametrize("path", SHEETS, ids=lambda path: path.relative_to(ROOT).as_posix())
def test_a_sheet_calculates_and_hands_on_what_its_file_says(kernel, path):
    doc = document.load(path)
    sources = [region["source"] for region in doc["regions"] if region["kind"] == "math"]
    done = kernel.calc(sources, fresh=True)
    errors = [(item["src"], item["error"]["ename"]) for region in done.regions for item in region["items"] if item.get("error")]
    assert not errors
    record = document.published(path)["values"]
    if path.parent == document.STARTERS:
        assert record is None                   # (a template delivers nothing: a sheet made from it will)
        return
    now = next(message for message in kernel.request({"op": "values"}) if message["op"] == "values")["values"]
    assert [(entry["name"], entry.get("form")) for entry in now] == \
        [(entry["name"], entry.get("form")) for entry in record["values"]]


@pytest.mark.parametrize("path", [path for path in EXAMPLES if any("given" in region for region in document.load(path)["regions"])],
                         ids=lambda path: path.stem)
def test_what_a_sheet_took_is_what_the_other_sheet_delivers_now(path):
    for region in document.load(path)["regions"]:
        if "given" not in region:
            continue
        given = region["given"]
        source = document.published(path.parent / given["sheet"])
        assert (given["revision"], given["saved"]) == (source["values"]["revision"], source["values"]["saved"]), given["sheet"]
        delivered = {entry["name"]: entry["form"] for entry in source["values"]["values"] if "form" in entry}
        assert all(delivered[entry["name"]] == entry["form"] for entry in given["values"])
