"""The worksheet engine, in a real kernel: every pass equals a clean top-to-bottom run.

    python -m pytest tests/test_worksheet_engine.py -q

One kernel serves the whole module; each test hands it a complete sheet,
so tests do not depend on each other's variables.
"""
import random
import re
import threading

import pytest

from worksheet_kernel import Kernel


@pytest.fixture(scope="module")
def kernel(tmp_path_factory):
    k = Kernel(tmp_path_factory.mktemp("worksheet-kernel"))
    yield k
    k.close()


# ---------------------------------------------------------------------------
# what a region reports
# ---------------------------------------------------------------------------

def test_each_statement_reports_its_own_value(kernel):
    p = kernel.calc(["U_in := 5.5 V\nI_out := 250. mA  # load current\nP := U_in · I_out\nP ▸ mW"])
    u, i, power, shown = p.items()
    assert u["kind"] == "assign" and u["literal"] and u["result"][0]["text/plain"] == "5.5 V"
    assert i["comment"] == "load current" and i["src"] == "I_out := 250. mA"
    assert not power["literal"]
    assert "W" in power["result"][0]["text/latex"]
    assert shown["latex"] == "P" and shown["result"][0]["text/plain"].endswith("mW")


def test_comments_blank_lines_and_suppression(kernel):
    p = kernel.calc(["# Divider\na := 1\n\nb := a + 1;\n# done"])
    kinds = [(i["t"], i.get("text") or i["src"]) for i in p.regions[0]["items"]]
    assert kinds == [("comment", "Divider"), ("stmt", "a := 1"), ("stmt", "b := a + 1;"),
                     ("comment", "done")]
    b = p.item(0, 1)
    assert b["gap"] and b["suppress"]


def test_definitions_of_functions_show_no_value(kernel):
    p = kernel.calc(["f := x → x² + 1\nf(3)"])
    definition, call = p.items()
    assert definition["kind"] == "def" and "result" not in definition
    assert call["result"][0]["text/plain"] == "10"


def test_printed_and_displayed_output_stays_in_order(kernel):
    p = kernel.calc(["for 1 ≤ k ≤ 2:\n    print('k is', k)\n    pp('k =', k)"])
    kinds = [(o["type"], o.get("text", "").strip()) for o in p.item()["outputs"]]
    assert kinds == [("stream", "k is 1"), ("display", ""), ("stream", "k is 2"), ("display", "")]


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

def test_an_error_stops_one_statement_not_the_sheet(kernel):
    p = kernel.calc(["a := 5 V\nb := a + nothing_defined\nd := a · 2", "d"])
    assert [p.error(0, n) for n in range(3)] == [None, "NameError", None]
    assert p.item(0, 1)["error"]["line"] == 2
    assert p.value(1) == "10 V"


def test_a_statement_that_does_not_transform_is_reported_alone(kernel):
    p = kernel.calc(["a := 5 V\nb := (2 + )\nd := a · 2"])
    assert [p.error(0, n) for n in range(3)] == [None, "SyntaxError", None]
    assert p.item(0, 1)["error"]["line"] == 2
    assert p.value(0, 2) == "10 V"


def test_the_mathcad_spelling_of_a_function_gets_a_hint(kernel):
    p = kernel.calc(["f(x) := x² + 1"])
    assert p.error() == "SyntaxError"
    assert "f := x →" in p.item()["error"]["hint"]


def test_regions_are_calculated_top_to_bottom(kernel):
    # A name used above its definition is not defined there.
    assert kernel.calc(["y := x + 1", "x := 2"]).error(0) == "NameError"
    assert kernel.calc(["x := 2", "y := x + 1"]).value(1) == "3"


def test_a_deleted_definition_is_gone(kernel):
    assert kernel.calc(["x := 2", "y := x + 1"]).value(1) == "3"
    # Nothing lingers in the kernel from the pass before.
    assert kernel.calc(["y := x + 1"]).error(0) == "NameError"


def test_stopping_a_calculation(kernel):
    timer = threading.Timer(1.5, kernel.manager.interrupt_kernel)
    timer.start()
    try:
        p = kernel.calc(["a := 1", "while True:\n    pass", "b := 2"])
    finally:
        timer.cancel()
    assert p.done["interrupted"]
    assert [r["id"] for r in p.regions] == ["r0", "r1"]         # the last was never reached
    assert p.error(1) == "Stopped"
    assert kernel.calc(["a := 1", "b := 2"]).value(1) == "2"    # and the kernel is fine


# ---------------------------------------------------------------------------
# recalculation
# ---------------------------------------------------------------------------

SHEET = ["a := 2 V", "b := 3 A", "p := a · b", "q := b + 1 A", "p ▸ mW"]


def test_an_unchanged_sheet_is_not_recalculated(kernel):
    kernel.calc(SHEET, fresh=True)
    again = kernel.calc(SHEET)
    assert (again.executed, again.reused) == (0, 5)


def test_only_what_depends_on_a_change_is_recalculated(kernel):
    kernel.calc(SHEET, fresh=True)
    p = kernel.calc(["a := 4 V"] + SHEET[1:])
    assert p.executed == 3                      # a, p, and p ▸ mW — not b or q
    assert p.value(2) == "12 W"


def test_an_unchanged_region_is_only_confirmed(kernel):
    first = kernel.request({"op": "calc", "fresh": True,
                            "regions": [{"id": "r0", "source": "a := 2 V"},
                                        {"id": "r1", "source": "a · 2"}]})
    revs = {m["id"]: m["rev"] for m in first if m["op"] == "region"}
    second = kernel.request({"op": "calc", "regions": [
        {"id": "r0", "source": "a := 3 V", "have": revs["r0"]},
        {"id": "r1", "source": "a · 2", "have": revs["r1"]}]})
    r0, r1 = [m for m in second if m["op"] == "region"]
    assert "items" in r0 and "items" in r1      # both changed
    third = kernel.request({"op": "calc", "regions": [
        {"id": "r0", "source": "a := 3 V", "have": r0["rev"]},
        {"id": "r1", "source": "a · 2", "have": r1["rev"]}]})
    assert all(m.get("same") for m in third if m["op"] == "region")


def test_aliases_stay_aliases(kernel):
    sheet = ["xs := [1, 2, 3]", "ys := xs", "ys.append(4)", "xs"]
    assert kernel.calc(sheet, fresh=True).value(3) == "[1, 2, 3, 4]"
    # Recreating the list must not leave ``ys`` on the old one.
    assert kernel.calc(["xs := [1, 2, 3]  # same"] + sheet[1:]).value(3) == "[1, 2, 3, 4]"
    assert kernel.calc(sheet[:2] + sheet[3:]).value(2) == "[1, 2, 3]"


def test_an_equal_list_is_not_the_same_list(kernel):
    # ``ys := xs`` was calculated against one list; when ``xs`` becomes a
    # new, equal list, replaying it would leave ``ys`` on the old one —
    # and the append below would miss ``xs``.
    kernel.calc(["xs := [1, 2, 3]", "ys := xs", "xs"], fresh=True)
    p = kernel.calc(["xs := [1, 2, 3]  # again", "ys := xs", "ys.append(4)", "xs"])
    assert p.value(3) == "[1, 2, 3, 4]"


def test_a_modified_object_is_made_afresh(kernel):
    sheet = ["xs := [1, 2, 3]", "xs.append(4)", "xs"]
    assert kernel.calc(sheet, fresh=True).value(2) == "[1, 2, 3, 4]"
    assert kernel.calc(sheet).value(2) == "[1, 2, 3, 4]"        # not [1, 2, 3, 4, 4]


def test_a_statement_that_modifies_its_input_always_runs(kernel):
    # The list is back to [1, 2, 3] when the pass ends, so nothing looks
    # changed afterwards — the append must still happen on the next pass.
    sheet = ["xs := [1, 2, 3]", "xs.append(4)", "n := len(xs)", "xs.pop()", "n"]
    assert kernel.calc(sheet, fresh=True).value(4) == "4"
    assert kernel.calc(sheet).value(4) == "4"


def test_values_without_a_reliable_fingerprint_are_recalculated(kernel):
    sheet = ["class Part:\n    mass = {}", "part := Part()", "part.mass"]
    assert kernel.calc([s.format(1) for s in sheet], fresh=True).value(2) == "1"
    assert kernel.calc([s.format(2) for s in sheet]).value(2) == "2"


def test_statements_that_look_outside_the_sheet_always_run(kernel):
    clock = "%%python\nimport time\ntime.perf_counter()"
    assert kernel.calc([clock], fresh=True).value(0) != kernel.calc([clock]).value(0)
    # ``pv()`` lists every variable, so it depends on all of them.
    before = kernel.calc(["a := 1", "pv()"]).item(1)["outputs"]
    after = kernel.calc(["a := 2", "pv()"]).item(1)["outputs"]
    assert before != after
    # The toolkit's own clock literal, and a file read through numpy.
    for source in ('t_now := "now"ₜᵢₘₑ', "d_1 := np.loadtxt('nothing.csv')"):
        kernel.calc([source], fresh=True)
        assert kernel.calc([source]).executed == 1, source
    # ... whereas ``time`` and ``load`` are perfectly good names for values.
    kernel.calc(["load := 250 mA", "p_load := 5 V · load"], fresh=True)
    assert kernel.calc(["load := 250 mA", "p_load := 5 V · load"]).reused == 2


def test_a_function_sees_the_values_at_the_time_of_the_call(kernel):
    sheet = ["f := x → k_1 · x", "k_1 := 5", "f(2)"]
    assert kernel.calc(sheet).value(2) == "10"
    assert kernel.calc([sheet[0], "k_1 := 7", sheet[2]]).value(2) == "14"
    assert kernel.calc([sheet[0], sheet[2]]).error(1) == "NameError"


def test_a_mode_switch_applies_to_the_regions_below_it(kernel):
    sheet = ["m_1 := [[1, 2], [3, 4]]", "set_syntax_mode('legacy')", "m_2 := [[1, 2], [3, 4]]",
             "type(m_1).__name__", "type(m_2).__name__"]
    for _ in range(2):                          # the switch happens on every pass
        p = kernel.calc(sheet)
        assert p.value(3) == "'list'" and p.value(4) != "'list'"
    # ... and is not left behind when the switch is removed.
    p = kernel.calc([sheet[0]] + sheet[2:])
    assert p.value(2) == "'list'" and p.value(3) == "'list'"


def test_a_declared_symbol_lasts_as_long_as_its_declaration(kernel):
    assert kernel.calc(["symbols: x₁", "x₁ + 1"], fresh=True).value(1) == "x_1 + 1"
    again = kernel.calc(["symbols: x₁", "x₁ + 1"])
    assert again.reused == 2 and again.value(1) == "x_1 + 1"
    # The declaration is replayed even when the region is not transformed again.
    assert kernel.calc(["symbols: x₁", "x₁ + 2"]).value(1) == "x_1 + 2"
    assert kernel.calc(["x₁ + 1"]).error(0) == "NameError"


def test_a_plot_is_drawn_once(kernel):
    sheet = ["a := 2 V", "plot([1, 2, 3] s, [1, 2, 3] V)", "note := 'x'"]
    first = kernel.calc(sheet, fresh=True)
    assert "image/png" in first.item(1)["outputs"][0]["data"]
    edited = kernel.calc(["a := 3 V"] + sheet[1:])
    assert edited.executed == 1                 # the plot does not depend on ``a``
    assert edited.item(1)["outputs"] == first.item(1)["outputs"]


def test_pyplot_figures_appear_at_the_end_of_their_region(kernel):
    region = "import matplotlib.pyplot as plt\nplt.plot([1, 2], [1, 4])\nplt.title('t')"
    pictures = []
    for _ in range(2):
        p = kernel.calc([region])
        assert all(not i["outputs"] for i in p.items())
        tail, = p.regions[0]["tail"]
        pictures.append(tail["data"]["image/png"])
    # pyplot keeps state the sheet cannot see, so these statements run on
    # every pass — the second figure has its line and its title too.
    assert pictures[0] == pictures[1]


def test_a_figure_left_open_is_not_replayed_as_if_closed(kernel):
    # ``show=False`` leaves the figure for the next statement to finish;
    # replaying the plot would leave that statement an empty figure.
    region = ("plot([1, 2, 3] s, [1, 2, 3] V, show=False)\n"
              "import matplotlib.pyplot as plt\nplt.title('late')")
    first = kernel.calc([region], fresh=True).regions[0]["tail"][0]["data"]["image/png"]
    again = kernel.calc([region]).regions[0]["tail"][0]["data"]["image/png"]
    assert first == again


def test_calls_into_other_modules_always_run(kernel):
    # Only the toolkit and the numeric libraries are known to be pure
    # computation; a call into anything else may depend on more than its
    # arguments (here: it returns a new value every time).
    region = "%%python\nimport uuid\nstr(uuid.uuid4())"
    assert kernel.calc([region], fresh=True).value(0) != kernel.calc([region]).value(0)


def test_python_cells_and_magics(kernel):
    p = kernel.calc(["%%python\nvalue = 2 ** 10\nvalue", "%precision 3", "value"])
    assert p.value(0) == "1024"                 # plain Python: 2 ** 10 stays an int
    assert p.error(1) is None
    assert p.value(2) == "1024"


# ---------------------------------------------------------------------------
# typesetting without running
# ---------------------------------------------------------------------------

def test_render_does_not_run_anything(kernel, tmp_path):
    marker = tmp_path / "must-not-exist"
    source = f"open({str(marker)!r}, 'w').write('ran')\nV_out := V_in · R_2/(R_1 + R_2)"
    replies = kernel.request({"op": "render", "regions": [{"id": "r0", "source": source}]})
    rendered = next(m for m in replies if m["op"] == "rendered")
    assert r"\frac" in rendered["items"][1]["latex"]
    assert not marker.exists()
    assert kernel.calc(["V_out"]).error(0) == "NameError"


def test_render_leaves_the_session_as_it_was(kernel):
    kernel.request({"op": "render", "regions": [{"id": "r0", "source": "symbols: q₁"}]})
    # Had the declaration leaked, ``q₁`` would now mean the symbol q_1.
    assert kernel.calc(["q := [5, 6]", "q₁"]).value(1) == "6"


# ---------------------------------------------------------------------------
# reuse is sound
# ---------------------------------------------------------------------------

POOL = [
    "a := {n} V", "b := {n} A", "p := a · b", "p ▸ mW", "r_1 := a / b",
    "xs := [1, 2, {n}]", "ys := xs", "ys.append({n})", "n_x := len(xs)", "zs := [xs, xs]",
    "zs[0].append(0)", "f := x → k_1 · x + {n}", "k_1 := {n}", "y := f(2)", "g := x → f(x) + 1",
    "w := g(1)", "M := Matrix([[1, 2], [3, {n}]])", "M[0, 0] = {n}", "d := M.det()",
    "total := 0\nfor 1 ≤ j ≤ {n}:\n    total += j", "pp('total', total)",
    "symbols: t\nexpr := t² + {n}", "s_1 := solve(expr ≡ 5, t)", "zz := undefined_q + 1",
    "undefined_q := {n}", "cnt := 0\ndef bump():\n    global cnt\n    cnt += 1\nbump()\ncnt",
    "bump()\ncnt", "import numpy as np\narr := np.array([1.0, 2.0, {n}.0])", "arr[0] = {n}",
    "tot := arr.sum()", "np.random.seed({n})\nr_x := np.random.rand()", "q := a + 1 A",
    "h_ := √(a² + a²)", "v := 100 Ω ± {n}%", "t_1 := {n}0 °C\nt_2 := t_1 + 5 ΔC", "a", "xs",
    "pv(a, b)", "pv()", "x := {n}; y := x + 1\ny", "set_syntax_mode('legacy')",
    "m_2 := [[1, 2], [3, {n}]]", "x₁ := {n}", "symbols: x₁\nx₁ + 1",
    "lst := [{n}, 2]\nlst₀ := 9\nlst", "def area(r):\n    return π r²\narea({n})",
    "class P:\n    v = {n}\npo := P()", "po.v", "e_1 := 1/0", "u := a if b > 2 A else -a",
    "k_1 := k_1 + 1", "dct := {{'k': {n}}}\ndct['k']", "dct['z'] = {n}", "len(dct)",
    "tup := (xs, {n})", "tup[0].append(7)", "fr := lambda: len(xs)\nfr()",
    "%%python\nraw_v = {n} + 1\nraw_v", "plot([1, 2, 3] s, [1, 2, {n}] V)",
]
ADDRESS = re.compile(r"0x[0-9A-Fa-f]{6,}")


def shown(pass_):
    """Everything a pass puts on the sheet, without timings and addresses."""
    out = []
    for region in pass_.regions:
        for item in region["items"]:
            if item["t"] == "comment":
                out.append(item["text"])
                continue
            result = [(r.get("text/latex"), ADDRESS.sub("0x", r.get("text/plain") or ""))
                      for r in item.get("result") or ()]
            outputs = [(o["type"], o.get("name"), ADDRESS.sub("0x", o.get("text", "")),
                        sorted(o.get("data", {}))) for o in item.get("outputs", ())]
            error = item.get("error")
            out.append((item["src"], item.get("latex"), result, outputs,
                        (error["ename"], ADDRESS.sub("0x", error["evalue"])) if error else None))
        out.append(len(region.get("tail", ())))
    return out


@pytest.mark.parametrize("seed", [1, 2])
def test_incremental_passes_equal_clean_recalculation(kernel, seed):
    """Random edits to a sheet of aliasing, mutation, late binding, mode
    switches and errors: what an incremental pass shows must be exactly
    what recalculating everything shows."""
    rng = random.Random(seed)

    def statement():
        return rng.choice(POOL).format(n=rng.randint(1, 9))

    sheet = [statement() for _ in range(12)]
    kernel.calc(sheet, fresh=True)
    reused = 0
    for step in range(45):
        action = rng.choice(["edit", "edit", "edit", "insert", "insert", "delete", "swap", "move"])
        if action == "edit":
            sheet[rng.randrange(len(sheet))] = statement()
        elif action == "insert" and len(sheet) < 26:
            sheet.insert(rng.randint(0, len(sheet)), statement())
        elif action == "delete" and len(sheet) > 4:
            del sheet[rng.randrange(len(sheet))]
        elif action == "swap":
            i, j = rng.sample(range(len(sheet)), 2)
            sheet[i], sheet[j] = sheet[j], sheet[i]
        elif action == "move":
            sheet.insert(rng.randrange(len(sheet)), sheet.pop(rng.randrange(len(sheet))))
        incremental = kernel.calc(sheet)
        reference = kernel.calc(sheet, reuse=False)
        reused += incremental.reused
        assert shown(incremental) == shown(reference), (seed, step, sheet)
    assert reused > 100                         # the comparison did exercise reuse
