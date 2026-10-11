"""Values of a sheet as text: what one sheet stores, another is given — the same value, and nothing but a value.

    python -m pytest tests/test_worksheet_values.py -q

One kernel serves the module.  "The same value" is judged as the engine
judges it when it decides what need not be calculated again: by its
fingerprint, which captures everything a statement could observe.
"""
import ast
import json
import shutil
import subprocess
import threading

import pytest

from worksheet_kernel import ROOT, Kernel, Pass


@pytest.fixture(scope="module")
def kernel(tmp_path_factory):
    k = Kernel(tmp_path_factory.mktemp("worksheet-values"))
    yield k
    k.close()


# A sheet with one of every kind of value the toolkit makes.
ZOO = [
    "U := 5.5 V\nI := 250. mA\nP := U · I\ncar_mass := 1500 kg\nT_room := 22 °C",
    "R_1 := 10. kΩ\nR_tol := R_1 ± 5%\nprec := 1 ± 0.29%\nEOL := 0.04%\nfine := 5‰",
    "hex_color := fed₁₆\nbinary_val := 11111111₂\nshown_hex := 255 ▸ hex\nclock_four := \"IIII\"ᵣₒₘₑ",
    "voltage := [1.0, 2.0, 3.0, 4.0, 5.0] V\nP_grid := [[1, 2, 3], [4, 5, 6]] mW\n"
    "y_data := [0.05, 0.15, 0.55, 0.35, 0.05]▸ \"rel. intensity\"",
    "v_final := (100. kph) ▸ kph\nT_shown := T_room ▸ degF\nlength := 22735 mm ▸ mm",
    "squares := {1, 4, 9, 16, 25}\nprimes := {2, 3, 5, 7}\neither := squares ∪ primes\nboth := squares ∩ primes\n"
    "words := {\"pear\", \"apple\", \"fig\", \"kiwi\", \"plum\", \"lime\", \"sloe\", \"nut\"}",
    "n := 3\nx := 0.1 + 0.2\nbig := 2**80\nz := 3 + 4j\nname := \"probe\"\nflag := True\nnothing := None\n"
    "not_a_number := float(\"nan\")\nendless := -float(\"inf\")",
    "plain := [1, 2, 3]\nmixed := (1, \"two\", 3.0, None)\nlookup := {\"a\": 1 V, \"b\": [2 V, 3 V]}\nunit_itself := kΩ",
    "turned := 5 ∠ 30°\nduration := \"PT90M\"ₜᵢₘₑ\ngains := [20 log₁₀(v/(1 V)) for v in voltage]",
    "M := Matrix([[1,2],[3,4]])\nMf := Matrix([[1.5, 2], [3, 0.1]])\nMi := M⁻¹\nMh := M ▸ hex\nthird := M[0, 0] / 3",
    "import numpy as np\nraw := np.array([1.5, 2.5, 3.5])\nwhole := np.arange(6).reshape(2, 3)\none := np.float64(2.5)\n"
    "bits := np.array([1.5, 2.5])",
    "import datetime\nday := datetime.date(2026, 10, 11)\nmoment := datetime.datetime(2026, 10, 11, 9, 5)",       # (day is a unit, too)
    # ... and what is no value to hand on.
    "f := x → x² + 1\nMs := Matrix([[1, sqrt(2)]])\nroot := sqrt(2)\nlong := list(range(60000))\n_hidden := 7",
]
NOT_OFFERED = {"f": "a function", "Ms": "a symbolic value", "root": "a symbolic value", "long": "too large"}

# In the kernel: every variable of the sheet written as text and read back,
# and how what came back compares with what was written.
ROUND_TRIP = r'''%%python
from utils.worksheet import values as _v
_e, _out = __edsl_ws__, {}
for _name, _value in list(_e.ns.items()):
    if _name.startswith("_") or _e.baseline.get(_name, _out) is _value:
        continue
    try:
        _text = _v.dump(_value, _e.fp._base)
    except _v.NotOffered as _why:
        _out[_name] = str(_why)
        continue
    _back = _v.load(_text)
    _print = _e.fp(_value)
    _shown = (lambda v: sorted(map(repr, v))) if isinstance(_value, (set, frozenset)) else repr     # (a set holds its elements in no order)
    _out[_name] = [type(_back) is type(_value), _print is not None and _e.fp(_back) == _print, _shown(_back) == _shown(_value),
                   _back is _value, _text]
_e._publish({"op": "probe", "names": _out})
'''


def probe(kernel, code):
    return next(m for m in kernel.execute(code) if m.get("op") == "probe")


def test_every_kind_of_value_a_sheet_holds_comes_back_as_it_was(kernel):
    done = kernel.calc(ZOO)
    assert [item["src"] for region in done.regions for item in region["items"] if item.get("error")] == []
    names = probe(kernel, ROUND_TRIP)["names"]
    assert {name: said for name, said in names.items() if isinstance(said, str)} == dict(
        NOT_OFFERED, datetime="a module")                       # (the probe looks at everything; the engine leaves modules out)
    offered = {name: said for name, said in names.items() if not isinstance(said, str)}
    assert len(offered) == 52
    # The same kind of thing, the same to every statement that could look at
    # it, shown the same — and another object, but for what is the toolkit's own.
    assert [name for name, (kind, same, shown, *_) in offered.items() if not (kind and same and shown)] == []
    assert offered["unit_itself"][3] and not any(offered[name][3] for name in ("P", "R_tol", "voltage", "plain", "lookup", "M", "raw"))
    # The text says what a value is made of: its number as it is to the last
    # digit, its unit by its dimensions, what the toolkit has by its name.
    text = {name: said[4] for name, said in offered.items()}
    assert text["unit_itself"] == '["B","kΩ"]' and text["flag"] == "true" and text["nothing"] == "null"
    assert json.loads(text["x"]) == ["o", "utils.sigfig:Sig", {"value": ["f", "0.30000000000000004"], "sf": ["i", "1"]}, {}]
    assert json.loads(text["big"])[2]["value"] == ["i", "1208925819614629174706176"]
    assert json.loads(text["not_a_number"]) == ["f", "nan"] and json.loads(text["endless"]) == ["f", "-inf"]
    assert json.loads(text["P"])[2]["value"][:2] == ["o", "forallpeople:Physical"]
    assert json.loads(text["P"])[2]["value"][2]["dimensions"] == ["dims", [["i", "1"], ["i", "2"], ["i", "-3"]] + [["i", "0"]] * 4]
    assert [json.loads(text[name])[0] for name in ("M", "bits", "one", "raw", "P_grid", "squares", "mixed", "lookup", "day", "duration")] == [
        "M", "A", "n", "A", "A", "S", "T", "d", "when", "span"]
    # What is there more than once in a value — the unit of each element of an array — is written once.
    elements = json.loads(text["voltage"])[3]
    assert elements[0][2]["_unit_pref"][:2] == ["o", "utils.extra_units:_DisplayUnit"] and elements[1][2]["_unit_pref"][0] == "same"
    assert elements[1][2]["_unit_pref"] == elements[4][2]["_unit_pref"] and elements[1][2]["value"][2]["dimensions"][0] == "same"
    assert len(text["voltage"]) < 1500 and all(len(said[4]) < 2000 for said in offered.values())
    # The same value is the same text on another day: a set is written in
    # the order of its elements' texts, not in the one it happens to hold
    # them in (which is another one in the next kernel).
    assert json.loads(text["words"]) == ["S", [["s", word] for word in ("apple", "fig", "kiwi", "lime", "nut", "pear", "plum", "sloe")]]
    numbers = [element[2]["value"][1] for element in json.loads(text["either"])[1]]
    assert numbers == sorted(numbers) and sorted(numbers, key=int) == ["1", "2", "3", "4", "5", "7", "9", "16", "25"]


def test_the_engine_says_what_the_variables_of_the_sheet_are(kernel):
    done = kernel.calc(ZOO)
    replies = kernel.request({"op": "values"})
    assert [m["op"] for m in replies] == ["values", "done"]
    said = replies[0]
    listed = {entry["name"]: entry for entry in said["values"]}
    # In the order the sheet defines them — after a name of the toolkit's
    # that the sheet gave a value of its own, which stands where the toolkit
    # has it; nothing of the toolkit's own, no module, nothing the sheet
    # keeps to itself (a name with _ before it).
    assert list(listed)[:7] == ["day", "U", "I", "P", "car_mass", "T_room", "R_1"] and len(listed) == 56
    assert list(listed)[-5:] == ["moment", "f", "Ms", "root", "long"]
    assert not {"V", "kΩ", "np", "datetime", "_hidden", "Matrix"} & set(listed)
    assert listed["unit_itself"]["form"] == '["B","kΩ"]'       # (but the toolkit's own under a name of the sheet's)
    # A value is there as text and as it is shown; one that cannot be handed on says why.
    assert listed["U"]["shown"]["text/plain"] == "5.5 V" and "\\mathrm{V}" in listed["U"]["shown"]["text/latex"]
    assert set(listed["U"]) == {"name", "tex", "form", "shown"} and listed["U"]["form"].startswith('["o","utils.sigfig:Sig"')
    assert listed["name"]["shown"] == {"text/plain": "'probe'"}
    assert {name: entry["why"] for name, entry in listed.items() if "why" in entry} == NOT_OFFERED
    assert all(set(entry) == {"name", "tex", "why"} for entry in listed.values() if "why" in entry)
    assert isinstance(said["toolkit"], str)
    # A name is there as the sheet typesets it.
    assert [listed[name]["tex"] for name in ("U", "R_tol", "car_mass", "f")] == [
        "U", "R_{\\mathrm{tol}}", "\\mathit{car}_{\\mathrm{mass}}", "f"]
    # Which calculation these are the values of: the last one that got to
    # its end.  One that was stopped leaves the kernel with half a sheet,
    # and the values say that they are of no calculation the page knows.
    assert said["pass"] == done.done["seq"] == kernel.seq - 1
    timer = threading.Timer(1.5, kernel.manager.interrupt_kernel)
    timer.start()
    try:
        stopped = kernel.calc(["a := 1", "while True:\n    pass", "b := 2"])
    finally:
        timer.cancel()
    assert stopped.done["interrupted"]
    half = kernel.request({"op": "values"})[0]
    assert half["pass"] is None and [entry["name"] for entry in half["values"]] == ["a"]
    again = kernel.calc(["a := 1", "b := 2"])
    assert kernel.request({"op": "values"})[0]["pass"] == again.done["seq"]


def test_a_sheet_hands_on_no_more_than_a_sheet_should_carry_about(kernel):
    sheet = ["one := list(range(10, 50))", "two := list(range(50, 90))", "small := 7",
             'globals()["no name"] = 1\nglobals()["class"] = 2\nglobals()[5] = 3']
    kernel.calc(sheet)
    listed = kernel.request({"op": "values"})[0]["values"]
    # Only under names a sheet could define: another sheet could not say any other.
    assert [entry["name"] for entry in listed] == ["one", "two", "small"] and all("form" in entry for entry in listed)
    size = {entry["name"]: len(entry["form"]) for entry in listed}
    assert size["one"] == size["two"] > 300 > size["small"]
    # All the values of a sheet together are no more text than a sheet
    # should carry about.  What no longer fits is not offered, and says so
    # — and what is small enough after it still is.
    limit = "%%python\nfrom utils.worksheet import values as _v\n_was, _v.SHEET = _v.SHEET, {}\n__edsl_ws__._publish({{'op': 'probe', 'was': _was}})"
    was = probe(kernel, limit.format(size["one"] + size["small"]))["was"]
    try:
        assert was == 4_000_000
        fits = {entry["name"]: entry.get("why", len(entry.get("form", ""))) for entry in kernel.request({"op": "values"})[0]["values"]}
        assert fits == {"one": size["one"], "two": "more than a sheet hands on", "small": size["small"]}
        probe(kernel, limit.format(size["one"] + size["small"] - 1))
        fits = {entry["name"]: entry.get("why", len(entry.get("form", ""))) for entry in kernel.request({"op": "values"})[0]["values"]}
        assert fits == {"one": size["one"], "two": "more than a sheet hands on", "small": "more than a sheet hands on"}
    finally:
        probe(kernel, limit.format(was))
    assert all("form" in entry for entry in kernel.request({"op": "values"})[0]["values"])
    # How a value is displayed is kept with it in brief — a line for the
    # list another sheet chooses from: its LaTeX where that is short, and
    # no more than the beginning of its plain text.
    kernel.calc(["short := [1.5, 2.5] V", "long := list(range(1000, 1700))", "wide := [k · 1.0 V for k in range(150)]"])
    shown = {entry["name"]: entry["shown"] for entry in kernel.request({"op": "values"})[0]["values"]}
    assert set(shown["short"]) == {"text/latex", "text/plain"} and shown["short"]["text/plain"] == "[1.5 V, 2.5 V]"
    assert set(shown["long"]) == {"text/plain"} and len(shown["long"]["text/plain"]) == 2002
    assert shown["long"]["text/plain"].startswith("[1000,\n 1001,\n") and shown["long"]["text/plain"].endswith(" \u2026")
    assert set(shown["wide"]) == {"text/plain"} and len(shown["wide"]["text/plain"]) <= 2000 and shown["wide"]["text/plain"].endswith(" V]")


def given(forms):
    """The source of a region that is given these values: {name: text}."""
    names = ", ".join(forms)
    return f"from utils.worksheet.values import given as _given\n({names},) = _given({json.dumps(forms, ensure_ascii=False)!r})"


def test_values_given_to_a_sheet_are_the_values_the_other_sheet_had(kernel):
    uses = "U · 2\nP ▸ mW\nR_tol\nT_room ▸ degF\nvoltage[1] · I\nM · Mf\nlookup[\"b\"][1] + U\nduration\ny_data\neither - primes\nturned"
    first = kernel.calc(ZOO + [uses])
    wanted = ["U", "I", "P", "R_tol", "T_room", "voltage", "M", "Mf", "lookup", "duration", "y_data", "either", "primes", "turned"]
    forms = {entry["name"]: entry["form"] for entry in kernel.request({"op": "values"})[0]["values"] if entry["name"] in wanted}
    assert list(forms) != wanted and sorted(forms) == sorted(wanted)
    forms = {name: forms[name] for name in wanted}
    # Another sheet: a region that is given them, and the same uses of them.
    regions = [{"id": "g", "source": given(forms), "given": True}, {"id": "u", "source": uses}]
    second = Pass(kernel.request({"op": "calc", "regions": regions}))
    shows = lambda done, region: [(item["src"], item["result"][0].get("text/plain"), item["result"][0].get("text/latex"))    # noqa: E731
                                  for item in done.items(region)]
    assert shows(second, 1) == shows(first, len(ZOO)) and len(shows(second, 1)) == 11
    assert second.done["names"] == sorted(wanted)              # (what it was given is the sheet's; how it was given is not)
    # The region is run, and the page shows it by itself: its statements
    # are not typeset.  What it says instead is what each of its names is
    # now, in the kernel: the name typeset, the value as it is displayed.
    assert [(item["kind"], "latex" in item, "result" in item, item.get("error")) for item in second.items(0)] == [
        ("other", False, False, None)] * 2
    assert "given" not in second.item(0, 0)
    told = second.item(0, 1)["given"]
    assert [entry["name"] for entry in told] == wanted and all(set(entry) == {"name", "latex", "value"} for entry in told)
    assert told[0] == {"name": "U", "latex": "U", "value": {"text/plain": "5.5 V", "text/latex": told[0]["value"]["text/latex"]}}
    assert "\\mathrm{V}" in told[0]["value"]["text/latex"] and told[3]["latex"] == "R_{\\mathrm{tol}}"
    assert [entry["value"]["text/plain"] for entry in told[:3]] == ["5.5 V", "250. mA", "1.4 W"]
    # (A figure or a table is no value given: what is said is text.)
    assert all(set(entry["value"]) <= {"text/plain", "text/latex"} for entry in told)
    # It is a calculation like another: not run again while it is the same.
    again = Pass(kernel.request({"op": "calc", "regions": regions}))
    assert (again.executed, again.reused) == (0, 13)
    changed = dict(forms, U='["o","utils.sigfig:Sig",{"value":["f","7.5"],"sf":["i","2"]},{}]')
    third = Pass(kernel.request({"op": "calc", "regions": [dict(regions[0], source=given(changed)), regions[1]]}))
    assert third.value(1, 0) == "15" and third.value(1, 2) == second.value(1, 2)
    assert third.item(0, 1)["given"][0]["value"]["text/plain"] == "7.5" and third.item(0, 1)["given"][1:] == told[1:]
    # Shown like other regions (not marked as given), it is ordinary code
    # with ordinary values — the very same text, too, which was run as
    # given before and is remembered as that; and the other way round.
    plain = Pass(kernel.request({"op": "calc", "regions": [{"id": "g", "source": given({"U": forms["U"]})}]}))
    assert plain.item(0, 1)["result"][0]["text/plain"] == "5.5 V" and plain.item(0, 1)["kind"] == "assign"
    assert "given" not in plain.item(0, 1)
    kernel.request({"op": "calc", "regions": regions})
    ordinary = Pass(kernel.request({"op": "calc", "regions": [dict(regions[0], given=False), regions[1]]}))
    assert "given" not in ordinary.item(0, 1) and len(ordinary.item(0, 1)["result"]) == len(wanted)
    assert ordinary.item(0, 1)["result"][0]["text/plain"] == "5.5 V" and shows(ordinary, 1) == shows(second, 1)
    back = Pass(kernel.request({"op": "calc", "regions": regions}))
    assert back.item(0, 1)["given"] == told and "result" not in back.item(0, 1)
    # Values that cannot be read are an error of the region, and it says nothing of names it did not get.
    broken = Pass(kernel.request({"op": "calc", "regions": [dict(regions[0], source=given(dict(forms, U='["i"]')))]}))
    assert broken.error(0) == "ValueError" and "given" not in broken.item(0, 1)


BAD = [
    # no text of a value at all
    "", "x", "{", "[]", "7", '"text"', '{"x": 1}', '[1, 2]', '[["i", "1"]]',
    # kinds that are none, or are the reader's own workings
    '["value", null]', '["many", [null]]', '["text", "x"]', '["shape", [1]]', '["__class__"]', '["read_i", "1"]', '["_read_i", "1"]',
    # the right kind, put together wrongly
    '["i"]', '["i", 1]', '["i", "one"]', '["f", "1e"]', '["c", "j+"]', '["q", "1", "0"]', '["D", "x"]', '["when", "week", "2026-10-11"]',
    '["when", "date", "tomorrow"]', '["l", null]', '["d", [[1]]]', '["d", [[["l", []], null]]]', '["dims", [["i", "1"]]]',
    # classes: only the toolkit's kinds of value, only what they hold
    '["o", "os:system", {}, {}]', '["o", "builtins:eval", {}, {}]', '["o", "utils.worksheet.engine:Engine", {}, {}]',
    '["o", "utils.sigfig:Sig", {"__class__": ["s", "x"]}, {}]', '["o", "utils.sigfig:Sig", {"nope": ["i", "1"]}, {}]',
    '["o", "forallpeople:Physical", {}, {"more": ["i", "1"]}]', '["o", "utils.sigfig:Sig", [], {}]', '["o", 5, {}, {}]',
    # arrays: numbers only, as many as the shape says
    '["a", "plain", "O", [1], "AAAAAAAAAAA="]', '["a", "plain", "<f8", [3], "AAAAAAAAAAA="]', '["a", "plain", "<f8", [1], "not base64!"]',
    '["a", "plain", "<f8", [-1], "AAAAAAAAAAA="]', '["a", "other", "<f8", [1], "AAAAAAAAAAA="]', '["a", "plain", "S8", [1], "AAAAAAAAAAA="]',
    '["a", "plain", "<f8,<f8", [1], "AAAAAAAAAAAAAAAAAAAAAA=="]', '["A", "plain", [2], [null]]', '["n", "O", "AAAAAAAAAAA="]',
    # matrices: of numbers; what the toolkit has: by a name it has
    '["M", [1, 1], [["s", "x**2"]], null]', '["M", [2, 2], [["I", "1"]], null]', '["M", [1, 1], [["I", "1"]], ["i", "1"]]',
    '["I", "__import__(\'os\')"]', '["R", "0", "1", "x", "1", "53"]', '["B", "no_such_name_in_the_toolkit"]', '["B", 5]',
    # a part named again that was never written
    '["same", 0]', '["l", [["dims", [["i", "1"]]], ["same", 1]]]', '["same", "0"]', '["same", -1]',
]


def test_text_that_is_no_stored_value_makes_nothing_and_runs_nothing(kernel):
    # What a sheet is given comes out of another file, which may be anyone's.
    # Text that is not a value as this toolkit writes one is turned down as
    # a whole; no part of it is acted on.
    for text in BAD:
        done = kernel.calc([given({"taken": text}), "taken"])
        assert done.error(0) == "ValueError" and done.error(1) == "NameError", text
        said = done.item(0, 1)["error"]["evalue"]
        assert "stored value" in said or "toolkit" in said or "has no" in said or "holds nothing" in said, (text, said)
    # (Good text beside it does not help: the names are given together or not at all.)
    done = kernel.calc([given({"good": '["i", "1"]', "bad": '["o", "os:system", {}, {}]'}), "good"])
    assert done.error(0) == "ValueError" and done.error(1) == "NameError"
    for payload in ('"text"', "[1]", "", "{"):
        done = kernel.calc([f"from utils.worksheet.values import given as _given\n(a,) = _given({payload!r})"])
        assert done.error(0) == "ValueError" and "no stored values" in done.item(0, 1)["error"]["evalue"]
    # The kinds of value that can be made are a list in the toolkit, not whatever a text names.
    kinds = probe(kernel, "%%python\nfrom utils.worksheet import values as _v\n"
                          "__edsl_ws__._publish({'op': 'probe', 'kinds': sorted(_v._kinds())})")["kinds"]
    assert kinds == ["forallpeople:Physical", "utils.circuit_dsl:Range", "utils.circuit_dsl:_Ratio", "utils.currencies:Currency",
                     "utils.extra_units:_DeltaUnit", "utils.extra_units:_DisplayUnit", "utils.sigfig:Sig", "utils.sigfig:_DeltaTemp",
                     "utils.sigfig:_HMSDisplay", "utils.sigfig:_InUnits", "utils.sigfig:_PMDisplay", "utils.sigfig:_Radix",
                     "utils.sigfig:_TempScale"]


def test_a_sheet_that_was_given_values_is_still_a_notebook(kernel):
    # In JupyterLab there is no worksheet engine: the region is a cell like
    # another, and what the toolkit has is found by its name where the
    # notebook's first line put it.
    kernel.calc(ZOO)
    forms = {entry["name"]: entry["form"] for entry in kernel.request({"op": "values"})[0]["values"] if "form" in entry}
    cell = given({name: forms[name] for name in ("U", "unit_itself", "voltage", "T_room")})
    said = probe(kernel, "%%python\n_e = get_ipython().user_ns.pop('__edsl_ws__')\ntry:\n"
                         f"    exec(compile({cell!r}, 'cell', 'exec'), get_ipython().user_ns)\n"
                         "    _said = [repr(U), unit_itself is kΩ, repr(voltage), repr(T_room), '__edsl_ws__' in get_ipython().user_ns]\n"
                         "finally:\n    get_ipython().user_ns['__edsl_ws__'] = _e\n"
                         "_e._publish({'op': 'probe', 'said': _said})")["said"]
    assert said == ["5.5 V", True, "[1.0 V, 2.0 V, 3.0 V, 4.0 V, 5.0 V]", "22 °C", False]


# Text with everything in it that could end a line, a text or a comment
# early — in Python, or in the toolkit's own reading of a cell.
AWKWARD = ("it's \"quoted\" \\ back\\\\slash := 5 V \u2192 mV\u00b2 # no % \n line \r\t \u2028 \u2029 \x85 \x7f \U0001f600 \xe6 "
           "''' \"\"\" \\n \\u0041 \\' {x} $y$ ; x = 1")


def page_code(given):
    """The code the page makes for what a region was given (js/given.js), as node runs it."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module = (ROOT / "utils" / "worksheet" / "static" / "js" / "given.js").as_uri()
    script = (f"import {{ sourceOf }} from {json.dumps(module)}; let text = ''; process.stdin.on('data', (part) => {{ text += part; }})"
              ".on('end', () => process.stdout.write(JSON.stringify(sourceOf(JSON.parse(text)))));")
    run = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(given), capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_the_code_the_page_makes_for_given_values_gives_them_back_as_they_were(kernel):
    forms = {"text": json.dumps(["s", AWKWARD], ensure_ascii=False), "\u03b8_JA": '["i","40"]', "R_tol": '["f","0.05"]'}
    code = page_code({"sheet": "a.ipynb", "name": "a\nimport os", "revision": "');import os;('",
                      "values": [{"name": name, "form": form} for name, form in forms.items()]})
    # To Python: a comment, an import and one assignment of a call with one text — which is the forms, to the letter.
    assert len(code.split("\n")) == len(code.splitlines()) == 3 and code.replace("\u03b8_JA", "").isascii()
    tree = ast.parse(code)
    assert [type(node).__name__ for node in tree.body] == ["ImportFrom", "Assign"]
    call = tree.body[1].value
    assert [target.id for target in tree.body[1].targets[0].elts] == list(forms) and call.func.id == "_given" and len(call.args) == 1
    assert json.loads(call.args[0].value) == forms and list(json.loads(call.args[0].value)) == list(forms)
    # To the worksheet's engine (which reads a region as the toolkit's notation first): the values, as they were.
    done = Pass(kernel.request({"op": "calc", "regions": [{"id": "g", "source": code, "given": True},
                                                          {"id": "u", "source": "text\n\u03b8_JA + 2\nR_tol"}]}))
    assert [item.get("error") for item in done.items(0)] == [None, None]
    assert [entry["name"] for entry in done.item(0, 1)["given"]] == list(forms)
    assert [done.value(1, 0), done.value(1, 1), done.value(1, 2)] == [repr(AWKWARD), "42", "0.05"]
    # To a notebook without the engine, through the toolkit's reading of every cell.
    cell = ("%%python\n_e = get_ipython().user_ns.pop('__edsl_ws__')\ntry:\n"
            f"    _ran = get_ipython().run_cell({code!r}, store_history=False)\n"
            "    _said = [_ran.success, text, repr(\u03b8_JA), repr(R_tol)]\nfinally:\n    get_ipython().user_ns['__edsl_ws__'] = _e\n"
            "_e._publish({'op': 'probe', 'said': _said})")
    assert probe(kernel, cell)["said"] == [True, AWKWARD, "40", "0.05"]
    # (What is no name makes no code at all.)
    assert page_code({"sheet": "a.ipynb", "values": [{"name": "x);import os;(", "form": "1"}]}) is None

