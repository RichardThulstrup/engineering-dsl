"""What a sheet reads from outside itself — the clock, random numbers, files, the day's currency rates — is read
once and kept: in the kernel, and (as text) with the sheet's file, for another kernel on another day.

    python -m pytest tests/test_worksheet_kept.py -q

A kernel serves each test: what is kept is the kernel's until it is handed on.
"""
import datetime
import re

import numpy as np
import pytest

from utils.worksheet.engine import Engine, _digest
from worksheet_kernel import Kernel, Pass


@pytest.fixture
def kernel(tmp_path):
    (tmp_path / "data.txt").write_text("1.5\n2.5\n3.5\n", encoding="utf-8")
    k = Kernel(tmp_path)
    yield k
    k.close()


IMPORTS = "import numpy as np\nimport random\nimport time\nimport datetime"
# One of every way of reading outside the sheet — and, between them, what reads nothing outside.
SHEET = [
    IMPORTS,
    "scale := 2",
    "r_1 := np.random.rand() · scale",
    "r_2 := random.random()",
    "r_3 := np.random.normal(0, 1, 3)",
    "rng := np.random.default_rng()",
    "r_4 := rng.normal()",
    't_1 := "now"ₜᵢₘₑ',
    "t_2 := time.time()",
    "t_3 := datetime.datetime.now()",
    "t_4 := time.perf_counter()",
    "d_1 := np.loadtxt('data.txt')",
    "d_2 := open('data.txt').read()",
    "with open('data.txt') as f_h:\n    d_3 = f_h.read()",
    "price := 100 USD",
    "in_dkk := price ▸ DKK",
    "twice := price · 2",
    "np.random.rand()",
    "np.random.rand()",
    "print('drawn', np.random.rand())",
    'words := {"pear", "apple", "fig", "kiwi", "plum", "lime", "sloe", "nut"}',
    "pick := random.choice(sorted(words))",
    "total := r_1 + r_2 + d_1[0]",
]
KEPT = {"r_1": ["random"], "r_2": ["random"], "r_3": ["random"], "r_4": ["random"], "t_1": ["clock"], "t_2": ["clock"],
        "t_3": ["clock"], "t_4": ["clock"], "d_1": ["file"], "d_2": ["file"], "with": ["file"], "in_dkk": ["rates"],
        "np.random.rand()": ["random"], "print": ["random"], "pick": ["random"]}
ADDRESS = re.compile(r"0x[0-9A-Fa-f]{6,}")


def regions(sheet):
    return [{"id": f"r{i}", "source": source} for i, source in enumerate(sheet)]


def calc(kernel, sheet, **more):
    return Pass(kernel.request(dict({"op": "calc", "regions": regions(sheet)}, **more)))


def statements(done):
    return [item for region in done.regions for item in region.get("items", ()) if item["t"] == "stmt"]


def shown(done):
    """What each statement shows: its text, its value, what it printed — and whether that is kept."""
    out = []
    for item in statements(done):
        value = [ADDRESS.sub("0x", bundle.get("text/plain", "")) for bundle in item.get("result") or ()]
        if item["src"].startswith("words"):
            value = [sorted(value[0].strip("{}").split(", "))]      # (a set shows its elements in whatever order it holds them)
        printed = [event.get("text", "") for event in item.get("outputs", ())]
        error = item["error"]["ename"] if item.get("error") else None
        out.append((item["src"], value, printed, error, bool(item.get("kept"))))
    return out


def records(done):
    """What a pass kept anew, as the page gets it: by what names each record."""
    kept = {}
    for message in done.regions:
        kept.update(message.get("kept", {}))
    return kept


def named(src):
    """A statement by the name it binds — or by how it begins."""
    head = src.split("\n")[0]
    if " :=" in head:
        return head.split(" :=")[0]
    if " = " in head and not head.startswith("with "):
        return head.split(" = ")[0]                                 # (in plain Python)
    return src.split("(")[0].split(" ")[0] if not src.startswith("np.") else src


def marked(done):
    """The statements of a pass whose value is kept, by name."""
    return {named(row[0]) for row in shown(done) if row[4]}


def test_what_is_read_outside_the_sheet_is_read_once_and_kept(kernel):
    first = calc(kernel, SHEET)
    assert [item["src"] for item in statements(first) if item.get("error")] == []
    # Kept: what read the clock, a random stream, a file, the day's rates —
    # each saying which, and when.  Not kept: what is a calculation, and
    # what is no value (a random stream itself).
    kept = {named(item["src"]): item["kept"] for item in statements(first) if item.get("kept")}
    assert {name: said["why"] for name, said in kept.items()} == KEPT
    assert len([item for item in statements(first) if item.get("kept")]) == 16          # (np.random.rand() stands there twice)
    now = datetime.datetime.now(datetime.timezone.utc)
    for said in kept.values():
        assert set(said) == {"id", "at", "why"}
        at = datetime.datetime.fromisoformat(said["at"].replace("Z", "+00:00"))
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", said["at"]) and abs((now - at).total_seconds()) < 120
    # The page is given each record as it is made — once — and, with the
    # end of the pass, word of all that there is.
    made = records(first)
    assert sorted(made) == sorted(item["kept"]["id"] for item in statements(first) if item.get("kept")) == sorted(first.done["kept"])
    assert sum(len(message.get("kept", {})) for message in first.regions) == 16
    assert all(record["id"] == ident and not any(key.startswith("_") for key in record) for ident, record in made.items())
    assert {key for record in made.values() for key in record} == {"id", "at", "why", "state", "reads", "writes", "value", "outputs", "closed"}
    drawn = next(record for record in made.values() if "r_3" in record["writes"])
    # (What it read of the sheet: the names it says, and the toolkit's own that stand for its numbers.)
    assert {"np", "r_3"} <= set(drawn["reads"]) and drawn["writes"]["r_3"].startswith('["a","plain","<f8",[3],')
    assert [record["outputs"][0]["text"][:6] for record in made.values() if "outputs" in record] == ["drawn "]
    # A file that one statement opened, read and closed: what is left of it is its name.
    assert [(sorted(record["writes"]), record["closed"]) for record in made.values() if "closed" in record] == [(["d_3"], {"f_h": ["data.txt", "r"]})]
    # Two statements that say the same are two readings.
    twice = [item for item in statements(first) if item["src"] == "np.random.rand()"]
    assert twice[0]["result"] != twice[1]["result"] and twice[0]["kept"]["id"] != twice[1]["kept"]["id"]
    assert [item["kept"]["id"].split(".")[1] for item in twice] == ["1", "2"]

    # Calculated again, nothing is read again: only what is no value is
    # made anew (the modules, the stream), and all shows what it showed.
    again = calc(kernel, SHEET)
    assert shown(again) == shown(first) and records(again) == {} and again.done["kept"] == first.done["kept"]
    assert (again.executed, again.reused) == (4, 22)                # (three of the modules, and the stream)
    assert [item["kept"] for item in statements(again) if item.get("kept")] == [item["kept"] for item in statements(first) if item.get("kept")]
    # (To the page a region that shows the same is the same.)
    have = {message["id"]: message["rev"] for message in first.regions}
    told = Pass(kernel.request({"op": "calc", "regions": [dict(region, have=have[region["id"]]) for region in regions(SHEET)]}))
    assert len(told.regions) == len(SHEET)
    # (But for the stream, which says where in memory it is.)
    assert all(message.get("same") for message, source in zip(told.regions, SHEET) if not source.startswith("rng"))


def test_what_was_kept_stands_until_what_its_statement_reads_changes(kernel, tmp_path):
    first = shown(calc(kernel, SHEET))
    value = lambda done, name: next(row[1] for row in done if named(row[0]) == name)      # noqa: E731
    # (Statement by statement; the sheet's own input and the stream, which says where in memory it is, apart.)
    differing = lambda a, b: sorted(named(x[0]) for x, y in zip(a, b) if x != y and named(x[0]) not in ("rng", "scale"))     # noqa: E731
    # Something else is written, a comment is put beside it, it is moved: it stands.
    edited = SHEET[:1] + ["scale := 2  # as before", "other := 7", "r_2 := random.random()  # a draw", SHEET[2]] + SHEET[4:]
    done = shown(calc(kernel, edited))
    stands = ("r_1", "r_2", "t_2", "d_1", "pick")
    assert [value(done, name) for name in stands] == [value(first, name) for name in stands]
    # (The page is told what stands beside it all the same.  And a
    # statement whose value is hidden is the statement it was.)
    hidden = statements(calc(kernel, SHEET[:3] + ["r_2 := random.random();  # hidden"] + SHEET[4:]))[6]
    assert (hidden["comment"], hidden["suppress"], bool(hidden["kept"])) == ("hidden", True, True)
    assert [bundle["text/plain"] for bundle in hidden["result"]] == value(first, "r_2")
    assert differing(first, shown(calc(kernel, SHEET))) == []
    # What it reads of the sheet changes: it is read again — and so is what was made of it.
    changed = shown(calc(kernel, SHEET[:1] + ["scale := 3"] + SHEET[2:]))
    assert differing(first, changed) == ["r_1", "total"]
    # (There is one record to a statement: back at 2, the reading for 2 is not there any more.)
    back = shown(calc(kernel, SHEET))
    assert differing(first, back) == ["r_1", "total"] and differing(changed, back) == ["r_1", "total"]
    # So when it comes to be something that cannot be compared at all ...
    odd = shown(calc(kernel, SHEET[:1] + ["%%python\nclass Odd:\n    def __rmul__(self, other):\n        return other\nscale = Odd()"] + SHEET[2:]))
    assert value(odd, "r_1") not in (value(back, "r_1"), []) and not next(row[4] for row in odd if named(row[0]) == "r_1")
    back = shown(calc(kernel, SHEET))
    # ... a random stream — which a record does not go by — or what was one.
    sheet = ["import numpy as np", "thing := 5", "n := len(str(thing)) + np.random.rand()", "rng := np.random.default_rng()",
             "r := rng.normal() + np.random.rand()"]
    was = shown(calc(kernel, sheet))
    now = shown(calc(kernel, sheet[:1] + ["thing := np.random.default_rng()", sheet[2], "%%python\nclass Fixed:\n    def normal(self):\n"
                                                                                        "        return 100\nrng = Fixed()", sheet[4]]))
    assert value(now, "n") not in (value(was, "n"), []) and value(now, "r")[0].startswith("100.") and value(was, "r") != []
    # One statement is read anew when that is asked for by what names it ...
    ident = next(item["kept"]["id"] for item in statements(calc(kernel, SHEET)) if item["src"].startswith("r_2"))
    one = calc(kernel, SHEET, anew=[ident])
    assert differing(back, shown(one)) == ["r_2", "total"] and [sorted(record["writes"]) for record in records(one).values()] == [["r_2"]]
    assert (one.executed, one.reused) == (4 + 2, 22 - 2)
    # ... and all of them when everything is calculated from scratch.
    fresh = calc(kernel, SHEET, fresh=True)
    again = differing(shown(one), shown(fresh))
    assert [name for name in again if name != "pick"] == ["np.random.rand()", "np.random.rand()", "print", "r_1", "r_2", "r_3", "r_4",
                                                         "t_1", "t_2", "t_3", "t_4", "total"]       # (one of eight words may be drawn twice)
    assert len(records(fresh)) == 16 and fresh.reused == 0
    assert differing(shown(fresh), shown(calc(kernel, SHEET))) == []
    # A pass that remembers nothing — the tests' run to compare with — reads everything, and keeps nothing of it.
    reference = calc(kernel, SHEET, reuse=False)
    assert not any(row[4] for row in shown(reference)) and records(reference) == {}
    assert differing(shown(fresh), shown(calc(kernel, SHEET))) == []
    # Asked for anew, and not to be had: there is no reading of it any more
    # — the page is told what there is — until it can be had again.
    holds = calc(kernel, SHEET).done["kept"]
    ident = next(item["kept"]["id"] for item in statements(calc(kernel, SHEET)) if item["src"].startswith("d_1"))
    (tmp_path / "data.txt").rename(tmp_path / "away.txt")
    assert differing(shown(fresh), shown(calc(kernel, SHEET))) == []                    # (as long as nobody asks, it stands)
    gone = calc(kernel, SHEET, anew=[ident])
    assert {named(row[0]): row[3] for row in shown(gone) if row[3]} == {"d_1": "FileNotFoundError", "total": "NameError"}
    assert gone.done["kept"] == [held for held in holds if held != ident]
    (tmp_path / "away.txt").rename(tmp_path / "data.txt")
    there = calc(kernel, SHEET)
    assert differing(shown(fresh), shown(there)) == [] and sorted(there.done["kept"]) == sorted(holds) and list(records(there)) == [ident]
    # The same words in plain Python say something else — a number is
    # another thing there, and what stands for it another name of the
    # toolkit's: what was read for the one is not what the other reads.
    raw = shown(calc(kernel, ["import numpy as np", "%%python\nq = np.random.rand() + 1\nq"]))
    dsl = shown(calc(kernel, ["import numpy as np", "q = np.random.rand() + 1\nq"]))
    assert raw[1][4] and dsl[1][4] and raw[2][1] != dsl[2][1] != []
    # What a statement unbinds is unbound again.
    sheet = ["import numpy as np", "%%python\ntmp = 1", "%%python\ndel tmp; drawn = np.random.rand()", "'tmp' in dir()"]
    once, again = calc(kernel, sheet), calc(kernel, sheet)
    assert marked(once) == {"del tmp; drawn"} and [record["gone"] for record in records(once).values()] == [["tmp"]]
    assert shown(once)[-1][1] == shown(again)[-1][1] == ["False"] and shown(again)[2] == shown(once)[2]


def test_what_was_kept_goes_to_another_kernel_as_text(kernel, tmp_path):
    more = ["%%python\nplain_ = 7", "unit := np.random.choice([kN, kN])", "f_h.name, f_h.mode, f_h.closed", "first := d_1[0] + 0", "d_1[0] = 9",
            "d_1[0]"]
    first = calc(kernel, SHEET + more)
    held = list(records(first).values())
    assert len(held) == 17 and shown(first)[-4][1] == ["('data.txt', 'r', True)"]
    # (What is the toolkit's own is written by its name.)
    assert [record["writes"]["unit"] for record in held if "unit" in record["writes"]] == ['["B","kN"]']
    # Another kernel, another day: the file is gone, the clock has moved
    # on, the random streams are others — and the sheet shows what it showed.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    other = Kernel(elsewhere)
    try:
        sheet = SHEET + more
        seeded = calc(other, sheet, kept=held)
        assert shown(seeded) == shown(first) and records(seeded) == {}
        # (What is kept is not run; all else is, here, for the first time.)
        assert (seeded.executed, seeded.reused) == (len(statements(seeded)) - 17, 17)
        # What a later statement changes in place is made afresh the next time, from the text.
        again = calc(other, sheet)
        assert shown(again) == shown(first) and [row[1] for row in shown(again)[-3:]] == [["1.5"], [], ["np.float64(9.0)"]]
        # The page says what there is: told that there is nothing, the kernel reads — what it can.
        bare = calc(other, sheet, kept=[])
        failed = {named(row[0]): row[3] for row in shown(bare) if row[3]}
        assert failed["d_1"] == failed["d_2"] == failed["with"] == "FileNotFoundError" and len(records(bare)) == 17 - 3
        # What is no record is none: the statements are run — no file is
        # found, and nothing else comes out as it did.
        assert len(records(calc(other, sheet, kept=[None, "r_2", {"id": 5}]))) == 17 - 3
        spoilt = [{"id": 5}, {"id": ["x"]}, {"at": None}, {"state": 7}, {"reads": ["scale"]}, {"reads": {"scale": 5}}, {"writes": {"r_2": 5}},
                  {"writes": ["r_2"]}, {"why": "random"}, {"gone": "x"}, {"gone": 5}, {"gone": [["x"]]}, {"value": 1.5}, {"outputs": "drawn"},
                  {"outputs": 5}, {"outputs": [{"type": "widget"}]}, {"outputs": [None]},
                  {"outputs": [{"type": "stream", "name": "stdout", "text": 5}]}, {"outputs": [{"type": "stream", "name": 1, "text": "drawn"}]},
                  {"outputs": [{"type": "display", "data": {"text/plain": 3}}]}, {"outputs": [{"type": "display", "data": "3"}]},
                  {"closed": ["f_h"]}, {"closed": {"f_h": ["data.txt"]}}, {"closed": {"f_h": ["data.txt", 5]}}, {"closed": {"f_h": "ab"}}]
        for wrong in spoilt:
            refused = shown(calc(other, sheet, kept=[dict(record, **wrong) for record in held]))
            assert {named(row[0]) for row in refused if row[3] == "FileNotFoundError"} == {"d_1", "d_2", "with"}, wrong
            same = {named(row[0]) for row, old in zip(refused, shown(first)) if old[4] and old[1] and row[1] == old[1]}
            assert same <= {"pick", "in_dkk", "unit"}, wrong       # (one of eight words; the rates of the same day; one of one)
        # (What only a kernel can say of a record — the very objects it
        # made — is not taken from a file.)
        assert shown(calc(other, sheet, kept=[dict(record, _made=[{}, None], _prints={}) for record in held])) == shown(first)
        # A record whose text is no value, one that binds another name than
        # its statement does, one made when the notation was in another
        # state: the statement is run, and none of them does anything.
        by_name = {next(iter(record["writes"]), record["id"]): record for record in held}
        forged = [dict(by_name["r_2"], writes={"r_2": '["o","os:system",{},{}]'}),
                  dict(by_name["r_1"], writes={"scale": '["i","99"]'}),
                  dict(by_name["t_2"], writes={"t_2": '["f","1.0"]', "scale": '["i","99"]'}),
                  dict(by_name["t_4"], gone=["scale"]),
                  dict(by_name["t_3"], closed={"scale": ["data.txt", "r"]}),
                  dict(by_name["r_3"], state="another"),
                  by_name["d_1"]]
        done = calc(other, sheet, kept=forged)
        told = {named(row[0]): row for row in shown(done)}
        assert told["scale"][1] == ["2"] and told["t_2"][1] != ["1.0"] and told["d_1"][3] is None and told["d_1"][1] == ["array([1.5, 2.5, 3.5])"]
        anew = {name for record in records(done).values() for name in record["writes"]}
        assert {"r_1", "r_2", "r_3", "t_2", "t_3", "t_4"} <= anew and "d_1" not in anew and "scale" not in anew
        # A record stands in only for a statement that reads outside the
        # sheet: what is a calculation is calculated, whatever a file says
        # it came to.  (So for the day's rates: only where there is a
        # currency in hand.)
        told = lambda done, name: {named(row[0]): row for row in shown(done)}[name][1]      # noqa: E731
        lie = {"id": _digest("plain_ = 7") + ".1", "at": "2026-10-11T10:00:00Z", "state": by_name["r_2"]["state"],
               "reads": {"plain_": _digest(["missing"])}, "writes": {"plain_": '["i","99"]'}}       # (all a record of it would say)
        for why in (["rates"], ["random"]):
            assert told(calc(other, sheet, kept=[dict(lie, why=why)]), "plain_") == ["7"], why
        # (And where there is one, only a record of the day's rates.)
        lie = dict(by_name["in_dkk"], writes={"in_dkk": '["i","99"]'})
        assert told(calc(other, sheet, kept=[dict(lie, why=["random"])]), "in_dkk") == told(first, "in_dkk") != ["99"]
        assert told(calc(other, sheet, kept=[lie]), "in_dkk") == ["99"]
    finally:
        other.close()


def test_a_random_stream_that_is_given_its_seed_is_drawn_from_on_every_pass(kernel):
    # The sheet says how its random numbers come out; a run from the top
    # gives the same ones each time.  Kept, they would not move the stream
    # along: a draw that is added would be the first one again.
    sheet = ["import numpy as np\nimport random", "before := np.random.rand()", "np.random.seed(3)", "one := np.random.rand()",
             "two := np.random.rand()"]
    first = calc(kernel, sheet)
    value = lambda done, name: next(row[1] for row in shown(done) if named(row[0]) == name)      # noqa: E731
    assert [(row[3], row[4]) for row in shown(first)] == [(None, False), (None, False), (None, True), (None, False), (None, False), (None, False)]
    again = calc(kernel, sheet)
    assert shown(again) == shown(first) and (again.executed, again.reused) == (4, 2)
    more = calc(kernel, sheet + ["three := np.random.rand()"])
    assert value(more, "one") == value(first, "one") != [] and value(more, "two") == value(first, "two") != value(first, "one")
    assert value(more, "three") not in (value(first, "one"), value(first, "two"), []) and value(more, "before") == value(first, "before")
    # (As a pass that remembers nothing has them: a run from the top.)
    assert [row[1] for row in shown(more)[3:]] == [row[1] for row in shown(calc(kernel, sheet + ["three := np.random.rand()"], reuse=False))[3:]]
    # So for a stream of its own that is made with a seed, and one of the
    # standard library's.  (In plain Python: a seed is a bare number.)
    for seeded, draw in (("rng = np.random.default_rng(42)", "rng.normal()"), ("rng = np.random.default_rng(seed=42)", "rng.normal()"),
                         ("random.seed(7)", "random.random()"), ("rng = random.Random(5)", "rng.random()"),
                         ("from numpy.random import seed\nseed(9)", "np.random.rand()"), ("rng = np.random.RandomState(3)", "rng.rand()"),
                         ("rng = np.random.Generator(np.random.PCG64(7))", "rng.normal()"),
                         ("rng = np.random.Generator(np.random.MT19937(7))", "rng.normal()"),
                         ("rng = np.random.Generator(np.random.Philox(7))", "rng.normal()"),
                         ("rng = np.random.Generator(np.random.SFC64(7))", "rng.normal()")):
        sheet = ["import numpy as np\nimport random", "%%python\n" + seeded, f"one := {draw}", f"two := {draw}"]
        first = calc(kernel, sheet)
        assert not any(row[3] or row[4] for row in shown(first)), seeded
        assert value(first, "one") != value(first, "two") != [] and value(calc(kernel, sheet), "one") == value(first, "one"), seeded
        assert [row[1] for row in shown(calc(kernel, sheet))[-2:]] == [row[1] for row in shown(first)[-2:]], seeded
        assert value(calc(kernel, sheet + [f"three := {draw}"]), "three") not in (value(first, "one"), value(first, "two"), []), seeded
    # A stream without a seed is another one each time it is made: what is drawn from it is kept.
    sheet = ["import numpy as np", "rng := np.random.default_rng()", "one := rng.normal()"]
    assert value(calc(kernel, sheet), "one") == value(calc(kernel, sheet), "one") != []
    # What reads a file and draws, too, is a draw like any other: after the
    # seed it is made on every pass — and the draws after it are the ones
    # that a run from the top has.
    sheet = ["import numpy as np", "np.random.seed(3)", "both := np.loadtxt('data.txt')[0] + np.random.rand()", "after := np.random.rand()"]
    first, again, top = calc(kernel, sheet), calc(kernel, sheet), calc(kernel, sheet, reuse=False)
    assert shown(first) == shown(again) == shown(top) and marked(first) == set() and value(first, "after") != []
    # A function of the sheet's own gives the seed: its definition says so,
    # and the pass has its seed from there.
    sheet = ["import numpy as np", "def reset():\n    np.random.seed(3)", "reset()", "one := np.random.rand()", "two := np.random.rand()"]
    first, more = calc(kernel, sheet), calc(kernel, sheet + ["three := np.random.rand()"])
    assert shown(more)[:-1] == shown(first) and marked(more) == set() and value(more, "three") not in (value(first, "one"), value(first, "two"), [])
    assert shown(more) == shown(calc(kernel, sheet + ["three := np.random.rand()"], reuse=False))


def test_what_cannot_be_kept_is_made_anew_every_time(kernel, tmp_path):
    log = tmp_path / "log.txt"
    sheet = [
        "import numpy as np\nimport random\nimport uuid",
        "draw := () → np.random.rand()",                            # a function: no value
        "handle := open('data.txt')",                               # an open file: no value
        "line := handle.readline()",                                # ... and what is read from it depends on what was read before
        f"open({str(log)!r}, 'a').write('x')",                      # a file that is written: something done, every time
        "pv()",                                                     # what looks at everything
        "uuid.uuid4().hex",                                         # a call into a module the clock and random numbers are not had from
        "xs := [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]",
        "random.shuffle(xs)",                                       # what changes what it reads
        "got := len(globals()) + np.random.rand()",                 # what reads the sheet wholesale
        f"open({str(log)!r}, mode='a').write('y')",                 # a file that is written, however that is said ...
        "how := 'a'",
        f"open({str(log)!r}, how).write('z')",                      # ... or may be, for all that can be told
        "mixed := uuid.uuid4().hex[:4] + str(np.random.rand())",    # a draw — and a call into a module that is no source
        "%%python\nclass Meter:\n    gain = 2\nmeter = Meter()\n"
        "scaled = np.random.rand() * meter.gain",                   # a draw — and something the sheet cannot compare
        "%%python\ndef setter():\n    global side\n    side = np.random.rand()\n    return side\n"
        "one = setter()",                                           # what binds a name behind the statement's back
        "plot([1, 2, 3], np.random.rand(3), show=False)",           # what leaves a figure open
        f"open({str(log)!r}, mode=how).write('k')",                 # (the ways of saying that a file is written, each of them)
        f"open({str(tmp_path / 'w.txt')!r}, 'w').write('w')",
        f"open({str(log)!r}, 'r+').read()",
        f"pathlib.Path({str(log)!r}).open('a').write('p')",
        f"open({str(tmp_path / 'x.txt')!r}, 'x').write('x')",       # (... which fails the second time: the file is there)
        "%%python\ndef odd():\n    globals()[5] = np.random.rand()\n    return 1\n"
        "two = odd() + 1",                                          # what binds what is no name at all
    ]
    sheet[0] += "\nimport pathlib"
    first = calc(kernel, sheet)
    assert [item["src"] for item in statements(first) if item.get("error")] == []
    assert marked(first) == set()
    second = calc(kernel, sheet)
    assert second.executed == len(statements(first)) - 2            # (all but numpy, which is no module that keeps a state, and the mode)
    assert [row[3] for row in shown(second) if row[3]] == ["FileExistsError"]
    rows = {named(row[0]): row for row in shown(first)}, {named(row[0]): row for row in shown(second)}
    assert rows[0]["line"] == rows[1]["line"] and rows[0]["uuid.uuid4"] != rows[1]["uuid.uuid4"] and rows[0]["got"] != rows[1]["got"]
    assert all(rows[0][name][1] != rows[1][name][1] != [] for name in ("mixed", "scaled", "one"))
    assert log.read_text(encoding="utf-8") == "xyzkpxyzkp"
    plotted = sheet.index("plot([1, 2, 3], np.random.rand(3), show=False)")
    assert all("image/png" in done.regions[plotted]["tail"][0]["data"] for done in (first, second))     # (the figure is shown where its region ends)
    # A file that is held open is read from where the reads before left
    # it: a read that is added reads on, as in a run from the top.
    held = ["f := open('data.txt')", "a_1 := f.readline()", "a_2 := f.readline()"]
    calc(kernel, held)
    on = calc(kernel, held + ["a_3 := f.readline()"])
    assert [row[1] for row in shown(on)[1:]] == [["'1.5\\n'"], ["'2.5\\n'"], ["'3.5\\n'"]] and marked(on) == set()
    # What a statement binds must be all it did: one that also changes the
    # notation's state, or leaves a figure open, is run again.
    sheet = ["import numpy as np", "%%python\nset_syntax_mode('legacy'); k_1 = np.random.rand()\nk_1", "after := np.random.rand()"]
    first, second = calc(kernel, sheet), calc(kernel, sheet)
    assert [(row[3], row[4]) for row in shown(first)] == [(None, False), (None, False), (None, False), (None, True)]
    assert shown(first)[2][1] != shown(second)[2][1] != [] and shown(first)[3][1] == shown(second)[3][1] != []
    # What was read in one state of the notation is not what is read in
    # another — whichever part of that state it is.
    draw = ["import numpy as np", "after := np.random.rand()"]
    value = lambda sheet: shown(calc(kernel, sheet))[-1][1]         # noqa: E731
    for state in ("%%python\nset_syntax_mode('legacy')", "%%python\nset_decimal_literals(True)", "symbols: x₁", "%%python\nprotect('zz_')",
                  "%%python\nregister_radix('octal', oct)"):
        plain = value(draw)
        assert value(draw) == plain != value(draw[:1] + [state] + draw[1:]) != [], state
    # A value that is more than a sheet should carry about is read again, too.
    big = ["import numpy as np", "noise := np.random.rand(60000)", "noise[0]"]
    assert not any(row[4] for row in shown(calc(kernel, big))) and shown(calc(kernel, big))[2][1] != shown(calc(kernel, big))[2][1]
    # (Also where it is a function of the sheet's own that reads: what is not kept is not remembered as a calculation, either.)
    big = ["import numpy as np", "def many():\n    return np.random.rand(60000)", "noise := many()", "noise[0]"]
    assert not any(row[4] for row in shown(calc(kernel, big))) and shown(calc(kernel, big))[3][1] != shown(calc(kernel, big))[3][1]
    # So for one that printed more than that.
    loud = ["import numpy as np", "%%python\nprint('x' * 500000, np.random.rand())\nquiet = np.random.rand()"]
    assert marked(calc(kernel, loud)) == {"quiet"}


def test_what_is_a_part_of_something_else_is_not_kept(kernel):
    # A row picked out of a list of rows is the list's own row: changed in
    # place, it changes the list.  Read back from text it would be a copy
    # — so it is picked on every pass, as in a run from the top.
    sheet = ["import random", "rows := [[1], [2]]", "pick := random.choice(rows)", "pick.append(9)", "rows", "len(rows[0]) + len(rows[1])"]
    for done in (calc(kernel, sheet), calc(kernel, sheet), calc(kernel, sheet, reuse=False)):
        assert shown(done)[-1][1] == ["3"] and shown(done)[-2][1][0] in ("[[1, 9], [2]]", "[[1], [2, 9]]") and marked(done) == set()
    # What shares something that can change in place with what it read, and what does not.
    sheet = ["%%python\nimport random\nimport numpy as np\n"
             "rows = [[1], [2]]\ntable = {'a': [1], 'b': [2]}\narr = np.arange(6.0)\npairs = [(1, 2), (3, 4)]\nmixed = [(1, [2]), (3, [4])]\n"
             "row = random.choice(rows)\n"                          # a row of rows
             "cell = table[random.choice(['a', 'b'])]\n"            # a list that the table holds
             "tail = arr[random.randint(1, 3):]\n"                  # a view: the memory is arr's
             "both = [rows, random.random()]\n"                     # a list of its own, with rows in it
             "half = (random.random(), mixed[0])\n"                 # ... and one that cannot change, with a list far inside
             "def one():\n    return random.choice(rows)\n"
             "through = one()\n"                                    # a row, handed on by a function
             "copy = list(random.choice(rows))\n"                   # a list of its own
             "part = arr[random.randint(1, 3):] + 0\n"              # an array of its own
             "pair = random.choice(pairs)\n"                        # one of the pairs itself — which nothing can change
             "number = random.choice([1, 2, 3])\n"
             # So for what else can change in place: a dictionary, a set, a matrix.
             "flat = {'a': 1}\ntags = {1, 2}\nmat = Matrix([[1, 2], [3, 4]])\n"
             "h_1 = (flat, random.random())\nh_2 = (tags, random.random())\nh_3 = (mat, random.random())\n"
             "own = (dict(flat), set(tags), mat.copy(), random.random())\n"
             "fixed = frozenset(tags)\nwith_it = (fixed, random.random())"]     # (a set that cannot change is like a pair: no harm in having it)
    first = calc(kernel, sheet)
    assert [item["src"] for item in statements(first) if item.get("error")] == []
    assert marked(first) == {"copy", "part", "pair", "number", "own", "with_it"}
    assert [row for row in shown(calc(kernel, sheet)) if row[4]] == [row for row in shown(first) if row[4]]


def test_a_name_that_only_looks_like_a_way_out_is_none(kernel):
    # ``input`` and ``load`` are fine names for a voltage and a force, and
    # the time of day of a date is no look at the clock.
    plain = ["import datetime\nimport time as tm\nimport os\nfrom numpy.random import rand\nfrom random import random, randint",
             "input := 5 V", "gain := input · 2", "load := 5 kN", "area := 2 m²", "def stress():\n    return load / area", "sigma := stress()",
             "%%python\nlunch = datetime.time(12, 30)\nwhen = datetime.datetime(2026, 10, 11, 9, 30)\nhour = when.time()\n"
             "stamp = datetime.datetime.time(when)\nnow = 7\nlater = now + 1",
             # (A date that is written out is no look at the clock, whatever the code of the toolkit's ``iso`` names.)
             'day := "2026-10-11"ₜᵢₘₑ']
    # What is one all the same: by another name, through a class, as a name of its own.
    reads = ["%%python\nfrom numpy.random import randn\n"
             "t_a = tm.time()\nt_b = datetime.date.today()\nt_c = datetime.datetime.now().time()\nt_d = tm.monotonic()\n"
             "home = os.environ['PATH'][:2]\nroot = os.environ.get('PATH')[:2]\nr_a = rand()\nr_b = random()\nr_c = randint(1, 6)\nr_d = randn()\n"
             "import numpy.random as nr\nr_e = nr.rand()\nr_f = nr.randn()\nr_g = nr.randint(1, 6)"]
    first = calc(kernel, plain + reads)
    assert [item["src"] for item in statements(first) if item.get("error")] == []
    assert {named(item["src"]): item["kept"]["why"] for item in statements(first) if item.get("kept")} == {
        "t_a": ["clock"], "t_b": ["clock"], "t_c": ["clock"], "t_d": ["clock"], "home": ["file"], "root": ["file"],
        "r_a": ["random"], "r_b": ["random"], "r_c": ["random"], "r_d": ["random"], "r_e": ["random"], "r_f": ["random"], "r_g": ["random"]}
    value = lambda done, name: next(row[1] for row in shown(done) if named(row[0]) == name)      # noqa: E731
    assert (value(first, "gain"), value(first, "sigma"), value(first, "hour")) == (["10 V"], ["2.5 kPa"], ["datetime.time(9, 30)"])
    assert value(first, "stamp") == ["datetime.time(9, 30)"] and value(first, "day") == ["datetime.date(2026, 10, 11)"]
    # A calculation follows what it is calculated from — in a kernel that
    # is told otherwise, too.
    told = [dict(record, id=_digest(text) + ".1", writes={name: '["i","99"]'}, reads={})
            for record in list(records(first).values())[:1] for name, text in (("gain", "gain := input · 2"), ("sigma", "sigma := stress()"))]
    again = calc(kernel, plain[:3] + ["load := 10 kN"] + plain[4:] + reads, kept=list(records(first).values()) + told)
    assert (value(again, "gain"), value(again, "sigma")) == (["10 V"], ["5 kPa"]) and shown(again)[-14:] == shown(first)[-14:]


def test_a_file_is_read_through_whatever_reads_files(kernel, tmp_path):
    (tmp_path / "data.json").write_text('{"gain": 2.5, "taps": [1, 2, 3]}', encoding="utf-8")
    sheet = ["import numpy as np\nimport pathlib\nimport io\nimport json\nimport csv\nimport pandas as pd",
             "p_1 := pathlib.Path('data.txt').read_text()", "p_2 := io.open('data.txt').read()", "p_3 := json.load(open('data.json'))",
             "p_4 := list(csv.reader(open('data.txt')))", "p_5 := pd.read_csv('data.txt', header=None).to_numpy()",
             "p_6 := open('data.txt', 'r').read()", "p_7 := open('data.txt', encoding='utf-8').read()",
             "p_8 := np.genfromtxt('data.txt')", "p_9 := np.fromfile('data.txt', sep=' ')", "p_10 := np.load('data.npy')",
             "table := pd.read_csv('data.txt', header=None)"]           # (a table is no value that a sheet hands on)
    np.save(tmp_path / "data.npy", np.array([1.5, 2.5]))
    first = calc(kernel, sheet)
    assert [item["src"] for item in statements(first) if item.get("error")] == []
    assert {named(item["src"]): item["kept"]["why"] for item in statements(first) if item.get("kept")} == {f"p_{n}": ["file"] for n in range(1, 11)}
    assert shown(first)[8][1] == ["{'gain': 2.5, 'taps': [1, 2, 3]}"] and shown(first)[9][1] == ["[['1.5'], ['2.5'], ['3.5']]"]
    assert shown(first)[13][1] == shown(first)[14][1] == ["array([1.5, 2.5, 3.5])"]
    # The files are others now: the sheet has what it read — but for the
    # table, which is read on every pass.
    (tmp_path / "data.txt").write_text("9.5\n8.5\n", encoding="utf-8")
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")
    again = calc(kernel, sheet)
    assert shown(again)[:-1] == shown(first)[:-1] and shown(again)[-1][1] != shown(first)[-1][1] != []


def test_a_function_of_the_sheets_own_that_reads_outside_is_one_that_reads_outside(kernel):
    sheet = ["import numpy as np", "def draw():\n    return np.random.rand()", "measure := () → np.loadtxt('data.txt')[1] + draw()",
             "x := draw()", "y := measure()", "z := x + 1", "def jitter(x):\n    return x + draw()",
             "%%python\nseveral = list(map(jitter, range(3)))",
             "%%python\ndef fact(n):\n    return 1 if n < 2 else n * fact(n - 1)\nsix = fact(3) + draw()"]      # (a function may name itself)
    first = calc(kernel, sheet)
    assert [item["src"] for item in statements(first) if item.get("error")] == []
    kept = {named(item["src"]): item["kept"]["why"] for item in statements(first) if item.get("kept")}
    assert kept == {"x": ["random"], "y": ["file", "random"], "several": ["random"], "six": ["random"]}    # (called, or handed to what calls it)
    again = calc(kernel, sheet)
    assert shown(again) == shown(first) and calc(kernel, sheet[:-1]).executed == 3      # (the three functions that draw)
    # The function is part of what the statement reads: another function, another reading.
    changed = calc(kernel, sheet[:1] + ["def draw():\n    return np.random.rand() + 10"] + sheet[2:])
    assert [row[1] != old[1] for row, old in zip(shown(changed), shown(first))] == [False, False, False, True, True, True, False, True, False, True]


def test_the_days_currency_rates_are_of_the_day_they_were_read(kernel, tmp_path):
    sheet = ["price := 100 USD", "in_dkk := price ▸ DKK", "twice := price · 2", "rate := get_currency_rate('USD')", "both := price + 50 EUR",
             "def worth():\n    return price + 50 EUR", "in_all := worth()"]
    first = calc(kernel, sheet)
    assert {named(item["src"]): item["kept"]["why"] for item in statements(first) if item.get("kept")} == {
        "in_dkk": ["rates"], "rate": ["rates"], "both": ["rates"], "in_all": ["rates"]}
    held = list(records(first).values())
    # Another day, other rates: what was worked out with the rates of that day stands.
    other_day = ("%%python\nfrom utils import currencies as _c\n_c._ensure_rates_fresh()\n_c._rates['USD'] = _c._rates['USD'] * 2\n"
                 "__edsl_ws__._publish({'op': 'probe'})")
    kernel.execute(other_day)
    assert shown(calc(kernel, sheet)) == shown(first)
    # In another kernel, too — which has the rates of its own day, and has
    # worked out nothing yet.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    other = Kernel(elsewhere)
    try:
        other.execute(other_day)
        there = calc(other, sheet, kept=held)
        assert shown(there) == shown(first) and records(there) == {} and there.reused == 4
    finally:
        other.close()
    # One of them, asked for anew, is of the new day ...
    ident = next(item["kept"]["id"] for item in statements(first) if item["src"].startswith("in_dkk"))
    one = shown(calc(kernel, sheet, anew=[ident]))
    assert [row[1] != old[1] for row, old in zip(one, shown(first))] == [False, True, False, False, False, False, False]
    # ... and so are all of them, when everything is calculated from scratch.
    fresh = shown(calc(kernel, sheet, fresh=True))
    assert [row[1] != old[1] for row, old in zip(fresh, shown(first))] == [False, True, False, True, True, False, True]
    # What uses the kept amount follows from it, as any calculation does.
    more = calc(kernel, sheet + ["fee := in_dkk · 2"])
    assert not statements(more)[-1].get("kept") and shown(more)[:7] == fresh
    # What draws from a stream that has its seed is made on every pass,
    # whatever else it reads.
    sheet = ["import numpy as np", "price := 100 USD", "%%python\nrng = np.random.default_rng(5)", "cost := rng.normal() · price ▸ DKK",
             "after := rng.normal()"]
    assert marked(calc(kernel, sheet)) == set() and shown(calc(kernel, sheet)) == shown(calc(kernel, sheet, reuse=False))
    assert [row[3] for row in shown(calc(kernel, sheet))] == [None] * 5


def test_what_was_read_for_statements_that_are_gone_is_let_go_in_the_end(kernel):
    # Every time a statement that reads outside is rewritten, the reading
    # of what it said before is left over.  The last of these are held on
    # to — an undo brings the statement back, and its reading with it.
    sheet = lambda n: ["import numpy as np", "base := np.random.rand()", f"n_{n} := np.random.rand()"]     # noqa: E731
    value = lambda done: (shown(done)[1][1], shown(done)[2][1])     # noqa: E731
    drawn = {}
    for n in range(Engine.SPARE + 5):
        done = calc(kernel, sheet(n))
        base, drawn[n] = value(done)
        assert len(done.done["kept"]) == 2 + min(n, Engine.SPARE)
    assert Engine.SPARE == 40 and len(set(map(str, drawn.values()))) == len(drawn)
    # (A run that remembers nothing lets nothing go.)
    calc(kernel, sheet(44), reuse=False)
    assert len(calc(kernel, sheet(44)).done["kept"]) == 42
    # What the sheet still says stands, whatever its age; of what it said
    # before, the last forty.
    assert value(calc(kernel, sheet(4))) == (base, drawn[4])
    assert value(calc(kernel, sheet(3)))[1] != drawn[3] and value(calc(kernel, sheet(43))) == (base, drawn[43])
