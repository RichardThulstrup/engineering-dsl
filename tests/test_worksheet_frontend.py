"""The worksheet front end: its pure logic under node, and the page itself in a browser.

Both tests skip when their tool is missing (node; Playwright with a
Chromium).  To point the browser test at a particular Chromium or Edge,
set ``WORKSHEET_TEST_BROWSER`` to its executable.
"""
import base64
import glob
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import zlib

import nbformat
import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "ui-test"


def test_markdown_highlighting_and_editor_rules():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    run = subprocess.run([node, str(ROOT / "tests" / "worksheet_frontend.test.mjs")],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert run.returncode == 0 and "FRONTEND_OK" in run.stdout, run.stdout + run.stderr


def _browser(playwright):
    """A Chromium to drive, or skip: the one Playwright manages, one named
    by WORKSHEET_TEST_BROWSER, or an earlier Playwright download."""
    candidates = [None, os.environ.get("WORKSHEET_TEST_BROWSER")]
    cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "ms-playwright"
    candidates += sorted(glob.glob(str(cache / "chromium-*" / "chrome-*" / "chrome*")), reverse=True)
    for executable in candidates:
        if executable is not None and not os.path.isfile(executable):
            continue
        try:
            return playwright.chromium.launch(executable_path=executable)
        except Exception:
            continue
    pytest.skip("no Chromium for Playwright (run: playwright install chromium)")


@pytest.fixture
def page_and_folder(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
               JUPYTER_DATA_DIR=str(tmp_path / ".jupyter-data"),
               MPLCONFIGDIR=str(tmp_path / ".matplotlib"), IPYTHONDIR=str(tmp_path / ".ipython"))
    server = subprocess.Popen(
        [sys.executable, "-B", "-m", "utils.worksheet", str(tmp_path), "--no-browser",
         "--port", str(port), "--token", TOKEN],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(300):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        with sync_api.sync_playwright() as playwright:
            browser = _browser(playwright)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            problems = []
            page.on("pageerror", lambda error: problems.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/?token={TOKEN}")
            yield page, tmp_path
            browser.close()
            assert not problems, problems
    finally:
        server.terminate()
        server.wait(timeout=15)


def settled(page):
    """Wait until the kernel is idle and nothing on the sheet is stale."""
    page.wait_for_timeout(200)
    page.wait_for_function(
        "document.querySelector('#status').dataset.state === 'idle'"
        " && !document.querySelector('.region.stale')", timeout=90_000)
    page.wait_for_timeout(200)


def shown(page):
    return page.evaluate(
        "[...document.querySelectorAll('.region')].map(r => r.innerText.replace(/\\s+/g, ' ').trim())")


def test_a_sheet_typed_from_scratch(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard

    # A text region, with the nested emphasis that once hung the page.
    keys.type('"')
    keys.type("# Divider with **bold and *nested*** text")
    keys.press("Escape")
    page.mouse.click(420, 200)                      # empty paper below it

    # Math regions: backslash names, ^ powers, one Enter per line.
    for line in ("V_in := 12.0 V", "R_1 := 4.7 k\\ohm", "R_2 := 10. k\\ohm",
                 "V_out := V_in R_2/(R_1 + R_2)", "P := V_in^2/(R_1 + R_2)"):
        keys.type(line)
        keys.press("Enter")
    settled(page)
    text = shown(page)
    assert len(text) == 6
    assert text[0] == "Divider with bold and nested text"
    assert "8.2 V" in text[4] and "mW" in text[5]
    assert page.evaluate("document.querySelectorAll('.region.math .katex').length") >= 7
    assert page.evaluate("document.querySelectorAll('.region.has-error').length") == 0

    # Change an input: what depends on it follows.
    page.locator(".region.math").first.click()
    keys.press("Control+a")
    keys.type("V_in := 24.0 V")
    keys.press("Enter")
    settled(page)
    assert "16. V" in shown(page)[4]

    # Undo takes the edit back, and the sheet follows again.
    keys.press("Control+z")
    settled(page)
    assert "8.2 V" in shown(page)[4]

    # Save as a notebook; reopen: same page.
    keys.press("Control+s")
    page.fill(".dialog input[type=text]", "divider")
    keys.press("Enter")
    page.wait_for_function("document.title.startsWith('divider')", timeout=20_000)
    places = page.evaluate("[...document.querySelectorAll('.region')].map(r => [r.style.left, r.style.top])")

    notebook = nbformat.read(str(folder / "divider.ipynb"), as_version=4)
    nbformat.validate(notebook)
    assert [c.cell_type for c in notebook.cells] == ["code", "markdown"] + ["code"] * 5
    assert notebook.cells[0].source == "from utils.Engineer import *"
    assert notebook.cells[2].source == "V_in := 12.0 V"
    assert notebook.cells[3].source == "R_1 := 4.7 kΩ"
    assert "8.2" in json.dumps(notebook.cells[5].outputs)

    page.reload()
    page.wait_for_selector(".region.math .katex", timeout=90_000)
    settled(page)
    assert page.evaluate(
        "[...document.querySelectorAll('.region')].map(r => [r.style.left, r.style.top])") == places
    assert "8.2 V" in shown(page)[4]
    assert page.evaluate("document.querySelector('#banner').hidden")       # its own sheet: trusted


def test_the_kernel_follows_the_sheet_and_dialogs_own_the_keyboard(page_and_folder):
    page, folder = page_and_folder
    (folder / "sub").mkdir()
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard

    # One region that shows where the kernel works.
    for line in ("%%python", "import os"):
        keys.type(line)
        keys.press("Shift+Enter")
    keys.type("os.path.basename(os.getcwd())")
    keys.press("Enter")
    settled(page)
    assert repr(folder.name) in shown(page)[0]

    # Saved into the folder below, the sheet is calculated there.
    keys.press("Control+s")
    page.wait_for_selector(".dialog input[type=text]")
    page.click(".dialog .files .dir")
    page.wait_for_function("document.querySelector('.dialog .crumbs').textContent.includes('sub')")
    page.fill(".dialog input[type=text]", "there")
    keys.press("Enter")
    page.wait_for_function("document.title.startsWith('there')", timeout=20_000)
    assert (folder / "sub" / "there.ipynb").is_file()
    page.wait_for_function("document.querySelector('.region').innerText.includes(\"'sub'\")",
                           timeout=90_000)
    settled(page)

    # While a dialog is up, what is typed does not land on the sheet behind it …
    keys.press("Control+o")
    page.wait_for_selector(".dialog .files div")
    keys.type("x := 1")
    assert page.query_selector(".region .ed") is None
    # … and the dialog can be worked from the keyboard.
    keys.press("ArrowDown")
    assert page.evaluate("document.querySelector('.dialog .files .on .name').textContent") == "there.ipynb"
    keys.press("Escape")
    page.wait_for_timeout(200)
    assert len(shown(page)) == 1
    assert page.evaluate("document.activeElement.id") == "sink"         # typing is the sheet's again
    assert page.evaluate("document.querySelectorAll('.region.has-error').length") == 0


def test_the_library_is_at_hand_by_key_by_search_and_by_name(page_and_folder):
    page, _ = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard

    def editor():
        """The open editor's text, with the caret marked."""
        return page.evaluate("""() => { const a = document.querySelector('.region .ed textarea');
            return a.value.slice(0, a.selectionStart) + '|' + a.value.slice(a.selectionStart); }""")

    # The panel shows the palette's tabs beside the everyday keys.
    keys.press("F2")
    page.wait_for_function("document.querySelectorAll('.lib-tabs button').length > 10")
    page.click(".lib-tabs button[data-tab='Math']")
    assert page.evaluate("document.querySelector('.lib-body h4').textContent") == "Constants & basic operators"

    # A key puts its entry at the caret, and the caret in its first open
    # place; Tab goes to the next; Enter finishes from inside the brackets.
    keys.type("a := ")
    page.click(".lib-body button:text-is('atan2(, )')")
    assert editor() == "a := atan2(|, )"
    page.hover(".lib-body button:text-is('hypot(, )')")
    assert "unit-aware" in page.evaluate("document.querySelector('.lib-info').innerText")
    keys.type("3")
    keys.press("Tab")
    assert editor() == "a := atan2(3, |)"
    keys.type("4")
    keys.press("Enter")

    # Ctrl+E searches everything without ending the edit; Enter inserts.
    keys.type("b := ")
    keys.press("Control+e")
    keys.type("hypot")
    page.wait_for_selector(".hit")
    keys.press("Enter")
    assert editor() == "b := hypot(|, )"
    keys.type("3.0 m")
    keys.press("Tab")
    keys.type("4.0 m")
    keys.press("Enter")

    # After a backslash the library is listed under its words; Tab picks.
    keys.type("kept := \\clam")
    page.wait_for_selector(".popup .s")
    keys.press("Tab")
    assert editor() == "kept := clamp(|, , )"
    keys.type("7")
    keys.press("Tab")
    keys.type("1")
    keys.press("Tab")
    keys.type("5")
    keys.press("Enter")
    # A glyph's name typed in full is finished by Enter (∑) — unless the
    # arrows chose another row: then that row is put, and the edit goes on.
    keys.type("total := \\sum")
    page.wait_for_selector(".popup .s")
    keys.press("ArrowDown")
    keys.press("Enter")
    assert editor() == "total := sum(|)"
    keys.type("[1, 2, 3]")
    keys.press("Enter")
    settled(page)
    text = shown(page)
    assert "0.64" in text[0] and "5.0 m" in text[1] and text[2].endswith("5") and text[3].endswith("6")
    assert page.evaluate("document.querySelectorAll('.region.has-error').length") == 0


def test_text_regions_get_a_table_and_formulas_from_the_panel(page_and_folder):
    page, _ = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard

    def editor():
        """The open editor's text, with what is selected in brackets."""
        return page.evaluate("""() => { const a = document.querySelector('.region .ed textarea');
            const v = a.value, s = a.selectionStart, e = a.selectionEnd;
            return v.slice(0, s) + '[' + v.slice(s, e) + ']' + v.slice(e); }""")

    keys.press("F2")
    page.wait_for_function("document.querySelectorAll('.lib-tabs button').length > 10")

    # The table builder starts a text region with a table to write over;
    # Tab goes from cell to cell, and after the last one adds a row.
    page.click(".lib-tabs button[data-tab='Table']")
    page.fill(".tool-pane input[aria-label='Columns']", "2")
    page.fill(".tool-pane input[aria-label='Rows']", "1")
    page.select_option(".tool-pane select", "right")
    page.click(".tool-pane .btn.primary")
    assert editor() == "| [Header 1] | Header 2 |\n| ---: | ---: |\n| Cell | Cell |"
    for cell in ("Part", "Value", "R1", "10 kΩ", "C1"):
        keys.type(cell)
        keys.press("Tab")
    keys.type("100 nF")
    assert editor() == "| Part | Value |\n| ---: | ---: |\n| R1 | 10 kΩ |\n| C1 | 100 nF[] |"
    keys.press("Escape")
    page.wait_for_selector(".region.text table")
    assert page.evaluate("[...document.querySelectorAll('.region.text td')].map(c => [c.textContent, c.className])") \
        == [["R1", "al-right"], ["10 kΩ", "al-right"], ["C1", "al-right"], ["100 nF", "al-right"]]

    # The formula editor writes the dollars: Enter puts what was composed
    # into the text being written, and the writing goes on.
    page.mouse.click(420, 420)                      # empty paper below the table
    keys.type('"')
    keys.type("Ohm: ")
    page.click(".lib-tabs button[data-tab='Formula']")
    page.wait_for_selector(".tool-pane math-field", timeout=30_000)
    page.click(".tool-pane math-field")
    keys.type("U=R*I")
    keys.press("Enter")
    assert editor() == "Ohm: $U=R\\cdot I$[]"
    keys.type(", so")
    page.click(".tool-pane math-field")
    keys.type("I=U/R")
    keys.press("Shift+Enter")                       # on a line of its own
    assert editor() == "Ohm: $U=R\\cdot I$, so\n\n$$I=\\frac{U}{R}$$[]"

    # With the caret in a formula, the editor takes that formula up, and
    # Enter puts the changed one in its place.
    page.evaluate("document.querySelector('.region .ed textarea').setSelectionRange(8, 8)")
    page.click(".tool-pane math-field")
    page.wait_for_function("document.querySelector('.tool-pane math-field').value === 'U=R\\\\cdot I'")
    keys.press("End")
    keys.type("+1")
    keys.press("Enter")
    assert editor() == "Ohm: $U=R\\cdot I+1$[], so\n\n$$I=\\frac{U}{R}$$"
    # While a \name is being typed, Enter finishes the name, not the formula.
    page.click(".tool-pane math-field")
    keys.type("\\alpha")
    keys.press("Enter")
    keys.type("^2")
    keys.press("Enter")
    assert editor() == "Ohm: $U=R\\cdot I+1$$\\alpha^2$[], so\n\n$$I=\\frac{U}{R}$$"
    # MathLive takes the focus once more 60 ms after its field is entered.
    # This formula went into the text sooner than that, and the keyboard
    # must stay with the text.
    page.wait_for_timeout(200)
    assert page.evaluate("document.activeElement.tagName") == "TEXTAREA"
    keys.press("Escape")
    page.wait_for_selector(".region.text .katex-display")
    assert page.evaluate("document.querySelectorAll('.region.text .katex-error').length") == 0


LONG_DRAWINGS = r"""() => [...document.querySelectorAll('.katex svg')]
    .filter((svg) => (svg.getAttribute('preserveAspectRatio') || '').endsWith('slice')).map((svg) => {
        const box = svg.parentElement.getBoundingClientRect();
        const height = Number(svg.getAttribute('viewBox').split(/\s+/)[3]);
        const numbers = [...svg.querySelectorAll('path')].flatMap((p) => p.getAttribute('d').match(/\d+(?:\.\d+)?/g).map(Number));
        return {width: svg.getAttribute('width'), span: Number(svg.getAttribute('viewBox').split(/\s+/)[2]),
                box: Math.ceil(box.width * height / box.height), reach: Math.max(...numbers)};
    })"""


def test_roots_arrows_and_braces_end_at_their_box_while_a_page_is_printed(page_and_folder):
    # KaTeX draws them 400em long and lets their box hide the rest.  On paper
    # the bar of a root has been seen running to the left of its sign instead:
    # at 600 dpi the stroke is longer than a 16-bit printer driver can count.
    page, folder = page_and_folder
    text = (r"Arrows $\overrightarrow{AB}$ and $\xleftrightarrow{abc}$, a brace $\overbrace{a+b+c}^{n}$, "
            r"roots $\sqrt[3]{x^2+1}$ and $$\sqrt{1+\left(\frac{f}{f_c}\right)^2}$$")
    nbformat.write(nbformat.v4.new_notebook(cells=[
        nbformat.v4.new_code_cell("from utils.Engineer import *"), nbformat.v4.new_markdown_cell(text),
        nbformat.v4.new_code_cell("g := 12.0/√(1 + (3.0/2)²)")]), str(folder / "strokes.ipynb"))
    page.goto(page.url.split("?")[0] + "?open=strokes.ipynb")
    page.click("#banner button:has-text('Calculate')")           # a sheet new to this computer
    assert page.evaluate("document.activeElement.id") == "sink"         # the keyboard is back on the sheet at once
    settled(page)

    drawn = page.evaluate(LONG_DRAWINGS)
    assert len(drawn) >= 8 and all(d["width"] == "400em" and d["reach"] >= 199_000 for d in drawn)

    # Making a PDF is printing: the page hears "beforeprint", then "afterprint".
    page.evaluate("(state) => window.addEventListener('beforeprint', () => { window.printed = eval(state)(); })",
                  LONG_DRAWINGS)
    page.pdf()
    printed = page.evaluate("window.printed")
    assert len(printed) == len(drawn)
    # Each is drawn half an em (500 units) longer than its box is wide, whatever the zoom,
    # and nothing reaches further.
    assert all(p["span"] == p["box"] + 500 and p["reach"] <= p["span"] for p in printed), printed
    assert page.evaluate(LONG_DRAWINGS) == drawn                 # and afterwards they are KaTeX's again


BOXES = """() => [...document.querySelectorAll('.region')].map((r) => {
    const box = r.getBoundingClientRect();
    return {left: parseFloat(r.style.left), top: parseFloat(r.style.top), width: box.width, height: box.height};
})"""
TOOLS = "() => [...document.querySelectorAll('#arrange [data-cmd]')].filter((b) => !b.disabled).map((b) => b.dataset.cmd)"


def test_a_selection_is_aligned_and_spaced_out_from_the_bar_and_the_menu(page_and_folder):
    page, _ = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    paper = page.locator("#content").bounding_box()
    for (x, y), line in (((40, 40), "k_one := 1"), ((300, 130), "k_two := 22.5/3"), ((170, 250), "k_three := 333.25 + k_one")):
        page.mouse.click(paper["x"] + x, paper["y"] + y)
        keys.type(line)
        keys.press("Enter")
        keys.press("Escape")
    settled(page)
    typed = page.evaluate(BOXES)
    lefts = lambda: [box["left"] for box in page.evaluate(BOXES)]       # noqa: E731
    tops = lambda: [box["top"] for box in page.evaluate(BOXES)]         # noqa: E731
    assert len({box["left"] for box in typed}) == 3 and len({box["top"] for box in typed}) == 3

    # The tools are there for as many regions as they need: none, three, one.
    assert page.evaluate(TOOLS) == []
    keys.press("Control+a")
    assert len(page.evaluate(TOOLS)) == 8
    page.click("#arrange [data-cmd='align-left']")
    assert lefts() == [typed[0]["left"]] * 3 and tops() == [box["top"] for box in typed]
    assert page.evaluate("!document.querySelector('#doc-dirty').hidden")
    keys.press("Control+z")                                             # one step back, and all of it
    assert page.evaluate(BOXES) == typed

    keys.press("Control+a")
    page.click("#arrange [data-cmd='align-right']")
    rights = [box["left"] + box["width"] for box in page.evaluate(BOXES)]
    assert max(rights) - min(rights) < 1 and abs(rights[0] - max(b["left"] + b["width"] for b in typed)) < 1
    keys.press("Control+z")

    # From the menu: evenly down.  The outer two stay; the one between has as
    # much room above as below.
    keys.press("Control+a")
    page.click("#arrange-menu .menu-button")
    page.click("#arrange-menu [data-cmd='spread-down']")
    spread = page.evaluate(BOXES)
    assert (spread[0]["top"], spread[2]["top"]) == (typed[0]["top"], typed[2]["top"])
    above = spread[1]["top"] - (spread[0]["top"] + spread[0]["height"])
    below = spread[2]["top"] - (spread[1]["top"] + spread[1]["height"])
    assert abs(above - below) <= 1 and spread[1]["top"] != typed[1]["top"]
    keys.press("Control+z")

    # On one line: the tops agree, and a region that would lie on its
    # neighbour has moved to the right of it.
    keys.press("Control+a")
    page.click("#arrange [data-cmd='align-top']")
    row = sorted(page.evaluate(BOXES), key=lambda box: box["left"])
    assert {box["top"] for box in row} == {typed[0]["top"]}
    assert all(b["left"] >= a["left"] + a["width"] + 8 for a, b in zip(row, row[1:]))
    keys.press("Control+z")

    # One region alone is aligned on the page (680 wide), and only sideways.
    page.locator(".region .grip").first.click()
    assert page.evaluate(TOOLS) == ["align-left", "align-centre", "align-right"]
    page.click("#arrange [data-cmd='align-centre']")
    centred = page.evaluate(BOXES)[0]
    assert abs(centred["left"] + centred["width"] / 2 - 340) <= 1 and centred["top"] == typed[0]["top"]
    keys.press("Control+z")

    # Across: the outer two stay, the middle one has as much room on its left as on its right.
    keys.press("Control+a")
    page.click("#arrange [data-cmd='align-top']")
    page.click("#arrange [data-cmd='spread-across']")
    across = sorted(page.evaluate(BOXES), key=lambda box: box["left"])
    gaps = [b["left"] - (a["left"] + a["width"]) for a, b in zip(across, across[1:])]
    assert abs(gaps[0] - gaps[1]) <= 1 and {box["top"] for box in across} == {typed[0]["top"]}


def test_the_grid_and_the_symbol_panel_are_as_the_user_left_them(page_and_folder):
    # How the page is set up is the user's, not the sheet's: off until asked
    # for, kept for the next time, and no change to the document.
    page, _ = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    grid = "document.querySelector('#paper').classList.contains('grid')"
    panel = "!document.querySelector('#palette').hidden"
    assert not page.evaluate(grid) and not page.evaluate(panel)
    page.click("#view-menu .menu-button")
    page.click("#view-menu [data-cmd='toggle-grid']")
    page.keyboard.press("F2")
    assert page.evaluate(grid) and page.evaluate(panel)
    assert page.evaluate("document.querySelector('#doc-dirty').hidden")
    page.reload()
    page.wait_for_function(f"{grid} && {panel}", timeout=30_000)        # as they were left
    assert page.evaluate("document.querySelector('#view-menu [data-cmd=\"toggle-grid\"]').getAttribute('aria-checked')") == "true"
    page.keyboard.press("F2")
    page.click("#view-menu .menu-button")
    page.click("#view-menu [data-cmd='toggle-grid']")
    page.reload()
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    page.wait_for_timeout(300)
    assert not page.evaluate(grid) and not page.evaluate(panel)


def test_text_without_a_width_of_its_own_keeps_its_width_when_it_is_arranged(page_and_folder):
    # Such a text region reaches to the edge of the page from wherever it
    # starts: centred with others, it must not grow as it moves to the left.
    page, folder = page_and_folder
    cell = nbformat.v4.new_markdown_cell("A note", metadata={"edsl": {"x": 100, "y": 96}})        # no "w"
    place = nbformat.v4.new_code_cell("k_one := 1", metadata={"edsl": {"x": 0, "y": 16}})
    notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("from utils.Engineer import *"), place, cell])
    notebook.metadata["edsl_worksheet"] = {"version": 1}
    nbformat.write(notebook, str(folder / "placed.ipynb"))
    page.goto(page.url.split("?")[0] + "?open=placed.ipynb")
    page.click("#banner button:has-text('Calculate')")
    settled(page)
    text = page.evaluate(BOXES)[1]
    assert (text["left"], round(text["width"])) == (100, 580)
    page.keyboard.press("Control+a")
    page.click("#arrange [data-cmd='align-centre']")
    moved = page.evaluate(BOXES)[1]
    assert (moved["left"], round(moved["width"])) == (50, 580)                 # centred between 0 and 680


@pytest.mark.skipif(sys.platform != "win32", reason="the widths at which the bar gives things up are set for Segoe UI")
def test_the_bar_fits_its_window_whatever_the_width(page_and_folder):
    # As the window gets narrower the tools give up their labels, then the
    # arranging tools go (the Arrange menu has them too), then more labels:
    # nothing is ever cut off, and the sheet's name keeps some room.
    page, _ = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    page.evaluate("() => { document.querySelector('#doc-name').textContent = 'A sheet with a fairly long name'; }")
    seen = {}
    for width in range(1700, 630, -10):
        page.set_viewport_size({"width": width, "height": 700})
        seen[width] = page.evaluate("""() => { const bar = document.querySelector('#bar');
            const shown = (selector) => getComputedStyle(document.querySelector(selector)).display !== 'none';
            return {cut: bar.scrollWidth - bar.clientWidth, name: document.querySelector('#doc-title').getBoundingClientRect().width,
                    labels: shown('[data-cmd=insert-picture] span'), arrange: shown('#arrange')}; }""")
    assert [width for width, bar in seen.items() if bar["cut"] > 0] == []
    assert all(bar["name"] >= 100 for bar in seen.values() if bar["labels"])
    # A window of 1280 still has the arranging tools; a wide one has every label.
    assert seen[1280]["arrange"] and not seen[1280]["labels"] and seen[1700]["labels"] and not seen[900]["arrange"]


# ---- pictures ---------------------------------------------------------------

def png(width, height, rgb=(31, 95, 191)):
    """A real PNG of one colour."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    row = b"\x00" + bytes(rgb) * width
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))


BITMAP = base64.b64encode(png(120, 60)).decode()
DRAWING = ('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="120" viewBox="0 0 240 120">'
           '<rect width="240" height="120" fill="#eef3fb"/><path d="M20 100 L80 30 L130 80 L220 100 Z" fill="#1f5fbf"/></svg>')
FILE = """([name, type, data]) => new File([Uint8Array.from(atob(data), (c) => c.charCodeAt(0))], name, {type})"""
# The paste a browser sends, made by hand — no clipboard is touched.  Given
# files as [name, kind, base64] and strings as [kind, text]; answers whether
# the page took the paste for itself.
PASTE = """([files, strings]) => {
    const data = new DataTransfer();
    for (const file of files) data.items.add((%s)(file));
    for (const [type, text] of strings) data.setData(type, text);
    const event = new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true});
    document.activeElement.dispatchEvent(event);
    return event.defaultPrevented;
}""" % FILE
# A file let go of over a point of the window (or over `on`, an element);
# answers whether the page took the hovering and the letting go for itself.
DROP = """([x, y, file, on]) => {
    const data = new DataTransfer();
    data.items.add((%s)(file));
    const target = on ? document.querySelector(on) : document.elementFromPoint(x, y);
    return ['dragover', 'drop'].map((kind) => {
        const event = new DragEvent(kind, {bubbles: true, cancelable: true, clientX: x, clientY: y, dataTransfer: data});
        target.dispatchEvent(event);
        return event.defaultPrevented;
    });
}""" % FILE
# Every region with a picture in it, in the order of the page's elements.
PICTURES = """() => [...document.querySelectorAll('.region')].filter((r) => r.querySelector('img')).map((r) => {
    const img = r.querySelector('img'), box = img.getBoundingClientRect();
    return {left: parseFloat(r.style.left), top: parseFloat(r.style.top), width: Math.round(r.getBoundingClientRect().width),
            kind: img.src.slice(5, img.src.indexOf(';')), natural: [img.naturalWidth, img.naturalHeight],
            shown: [Math.round(box.width), Math.round(box.height)],
            picture: r.classList.contains('picture'), selected: r.classList.contains('selected')};
})"""
TOAST = "(document.querySelector('#toast').hidden ? '' : document.querySelector('#toast').textContent)"
LIMIT = "async ([name, value]) => { (await import('/static/js/pictures.js')).LIMITS[name] = value; }"
EDITOR = "document.querySelector('.region .ed textarea').value"


def pictures(page, count):
    """The regions with pictures, once the page shows ``count`` pictures and all have been read."""
    page.wait_for_function(
        "(count) => { const all = [...document.querySelectorAll('.region img')];"
        " return all.length === count && all.every((img) => img.complete && img.naturalWidth > 0); }",
        arg=count, timeout=20_000)
    page.wait_for_timeout(100)                                  # the page is laid out again when one is read
    return page.evaluate(PICTURES)


def save_as(page, name):
    page.keyboard.press("Control+s")
    page.fill(".dialog input[type=text]", name)
    page.keyboard.press("Enter")


def test_a_picture_is_pasted_at_the_cursor_resized_and_saved_with_the_sheet(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    paper = page.locator("#content").bounding_box()

    # Pasted where the cursor is, at its own size; it is left selected, and
    # the cursor waits below it.
    page.mouse.click(paper["x"] + 40, paper["y"] + 120)
    assert page.evaluate(PASTE, [[["image.png", "image/png", BITMAP]], []]) is True
    [shown] = pictures(page, 1)
    assert shown == {"left": 40, "top": 112, "width": 132, "kind": "image/png", "natural": [120, 60],
                     "shown": [120, 60], "picture": True, "selected": True}
    assert page.evaluate("[document.querySelector('#cross').style.left, document.querySelector('#cross').style.top]") \
        == ["40px", "192px"]
    assert page.evaluate("!document.querySelector('#doc-dirty').hidden")
    keys.press("Control+z")                                     # pasting is one step to take back, and to redo
    assert pictures(page, 0) == []
    keys.press("Control+y")
    assert pictures(page, 1)[0]["shown"] == [120, 60]

    # So typing goes on below it.
    keys.type("k_one := 1")
    keys.press("Enter")
    settled(page)
    below = page.evaluate(BOXES)[1]
    assert (below["left"], below["top"]) == (40, 192)

    # Tab, which opens the next region, passes a picture by.
    page.mouse.click(paper["x"] + 40, paper["y"] + 40)
    keys.press("Tab")
    assert page.evaluate("document.querySelector('.region.editing').classList.contains('math')")
    keys.press("Escape")

    # A click selects a picture (there is no text in it to edit); its right
    # edge makes it narrower or wider, and it keeps its shape.
    page.locator(".region.picture").click()
    assert page.query_selector(".region.editing") is None and pictures(page, 1)[0]["selected"]
    page.wait_for_timeout(600)
    page.locator(".region.picture").click()                    # not a double click: too long after the first
    assert page.query_selector(".region.editing") is None
    page.evaluate("() => { window.kept = document.querySelector('.region.picture img'); }")
    box = page.locator(".region.picture").bounding_box()
    page.mouse.move(box["x"] + box["width"] + 1, box["y"] + 20)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] + 125, box["y"] + 20, steps=4)
    page.mouse.up()
    assert (pictures(page, 1)[0]["width"], pictures(page, 1)[0]["shown"]) == (256, [244, 122])
    page.mouse.move(box["x"] + 257, box["y"] + 20)
    page.mouse.down()
    page.mouse.move(box["x"] - 200, box["y"] + 20, steps=4)                            # as narrow as it gets
    page.mouse.up()
    assert pictures(page, 1)[0]["width"] == 24
    keys.press("Control+z")
    keys.press("Control+z")
    assert pictures(page, 1)[0]["shown"] == [120, 60]
    # (A picture is not read again for an undo: it is the very element it was.)
    assert page.evaluate("document.querySelector('.region.picture img') === window.kept")
    # A double click opens the text that names it — for a caption, say.
    page.locator(".region.picture").dblclick()
    assert re.fullmatch(r"!\[picture\]\(attachment:[0-9a-f]{12}\.png\)", page.evaluate(EDITOR))
    keys.press("Escape")
    assert pictures(page, 1)[0]["shown"] == [120, 60]           # and closed, the text is the picture again

    # Saved, it is a text cell with the picture attached, as a notebook keeps one.
    save_as(page, "pictured")
    page.wait_for_function("document.title.startsWith('pictured')", timeout=20_000)
    notebook = nbformat.read(str(folder / "pictured.ipynb"), as_version=4)
    nbformat.validate(notebook)
    cell = notebook.cells[1]
    name = re.fullmatch(r"!\[picture\]\(attachment:([0-9a-f]{12}\.png)\)", cell.source).group(1)
    assert cell.cell_type == "markdown" and cell.attachments == {name: {"image/png": BITMAP}}
    assert cell.metadata["edsl"] == {"x": 40, "y": 112, "w": 132}

    # Opened again, it is where it was, as large as it was.
    page.reload()
    assert pictures(page, 1)[0] == dict(shown, selected=False)

    # One pasted low on the screen is brought into view when it has been read.
    page.mouse.click(paper["x"] + 300, 880)
    page.evaluate(PASTE, [[["image.png", "image/png", base64.b64encode(png(60, 300)).decode()]], []])
    pictures(page, 2)
    assert page.evaluate("document.querySelector('.region.selected').getBoundingClientRect().bottom <= window.innerHeight")
    page.evaluate("() => { document.querySelector('#desk').scrollTop = 0; }")

    # Where a picture goes is settled when it is pasted: the cursor, put
    # elsewhere while the picture was being read, does not take it along.
    page.mouse.click(paper["x"] + 400, paper["y"] + 40)
    page.evaluate("""([paste, file, x, y]) => {
        eval(paste)([[file], []]);
        const point = {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0};
        document.querySelector('#content').dispatchEvent(new PointerEvent('pointerdown', point));
        window.dispatchEvent(new PointerEvent('pointerup', point));
    }""", [PASTE, ["image.png", "image/png", BITMAP], paper["x"] + 40, paper["y"] + 600])
    settled_at = pictures(page, 3)[2]
    assert (settled_at["left"], settled_at["top"]) == (400, 32)

    # One wider than the page is as wide as the page, and starts at its left edge.
    page.mouse.click(paper["x"] + 304, paper["y"] + 304)
    page.evaluate(PASTE, [[["image.png", "image/png", base64.b64encode(png(1000, 50)).decode()]], []])
    wide = pictures(page, 4)[3]
    assert (wide["left"], wide["top"], wide["width"], wide["shown"]) == (0, 296, 680, [668, 33])


def test_a_drawing_is_pasted_as_a_drawing_and_kept_with_a_bitmap_beside_it(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    paper = page.locator("#content").bounding_box()
    drawn = lambda: [p["kind"] for p in page.evaluate(PICTURES)]             # noqa: E731

    # SVG as plain text — here cut out of a web page, so without its
    # namespace — is a picture; other text is left to be pasted as text.
    page.mouse.click(paper["x"] + 40, paper["y"] + 40)
    assert page.evaluate(PASTE, [[], [["text/plain", "x := 5 V"]]]) is False
    assert page.evaluate(PASTE, [[], [["text/plain", "<svg> is how a drawing starts, </svg> how it ends"]]]) is False
    assert page.evaluate(PASTE, [[], [["text/plain", "<svg><rect></svg>"]]]) is False       # not well-formed
    cut_out = DRAWING.replace(' xmlns="http://www.w3.org/2000/svg"', "").replace(
        "</svg>", '<defs><circle id="dot" r="9"/></defs><use xlink:href="#dot" x="200" y="30"/></svg>')
    assert page.evaluate(PASTE, [[], [["text/plain", cut_out]]]) is True
    [first] = pictures(page, 1)
    assert (first["kind"], first["width"], first["shown"]) == ("image/svg+xml", 252, [240, 120])

    # An application's drawing: the browser names it in the paste and hands
    # it over only to a page that may read the clipboard.  Allowed, the
    # drawing is taken — not the bitmap or the words put beside it …
    page.evaluate("""(svg) => { window.asked = 0; Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
        read: async () => { window.asked += 1; if (window.refuse) throw new DOMException('no', 'NotAllowedError');
                            return [{types: ['text/plain', 'image/svg+xml'],
                                     getType: async (type) => new Blob([svg], {type})}]; }}}); }""", DRAWING)
    announced = [[["image.png", "image/png", BITMAP]], [["image/svg+xml", ""], ["text/plain", "Chart title"]]]
    page.mouse.click(paper["x"] + 40, paper["y"] + 200)
    assert page.evaluate(PASTE, announced) is True
    pictures(page, 2)
    assert drawn() == ["image/svg+xml"] * 2 and page.evaluate("window.asked") == 1
    # … and refused, the bitmap is, and the page says what would help.
    page.evaluate("window.refuse = true")
    page.mouse.click(paper["x"] + 40, paper["y"] + 360)
    page.evaluate(PASTE, announced)
    pictures(page, 3)
    assert drawn() == ["image/svg+xml", "image/svg+xml", "image/png"]
    assert "Allow this page to read the clipboard" in page.evaluate(TOAST)
    # With no bitmap beside it either, nothing is pasted, and that is said.
    page.evaluate(PASTE, [[], [["image/svg+xml", ""]]])
    page.wait_for_function(f"{TOAST}.includes('can be pasted once this page is allowed')")
    assert len(page.evaluate(PICTURES)) == 3

    # A drawing says how large it is — in units of its own, perhaps — or
    # not at all; one wider than the page is as wide as the page.
    shape = '<rect width="10" height="10"/></svg>'
    for count, ((x, y), drawing) in enumerate((
            ((40, 440), '<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="25mm">' + shape),
            ((304, 440), '<svg xmlns="http://www.w3.org/2000/svg">' + shape),
            ((40, 620), '<svg xmlns="http://www.w3.org/2000/svg" width="3000" height="100">' + shape)), start=4):
        page.mouse.click(paper["x"] + x, paper["y"] + y)
        page.evaluate(PASTE, [[], [["text/plain", drawing]]])
        pictures(page, count)                                   # (on the page before the next click: the cursor goes below it)
    sized = pictures(page, 6)[3:]
    assert [(p["left"], p["width"], p["shown"]) for p in sized] == [(40, 201, [189, 95]), (304, 312, [300, 150]), (0, 680, [668, 22])]

    # In the file a drawing has its bitmap copy first: JupyterLab shows the
    # first of an attachment's forms and no drawings; nbconvert takes the drawing.
    save_as(page, "drawn")
    page.wait_for_function("document.title.startsWith('drawn')", timeout=20_000)
    cells = [c for c in json.loads((folder / "drawn.ipynb").read_text(encoding="utf-8"))["cells"] if c["cell_type"] == "markdown"]
    assert ["".join(c["source"])[:11] for c in cells] == ["![drawing](", "![drawing](", "![picture]("] + ["![drawing]("] * 3
    copies = []
    for cell in cells[:2] + cells[3:]:
        [forms] = cell["attachments"].values()
        assert list(forms) == ["image/png", "image/svg+xml"]
        kept = base64.b64decode("".join(forms["image/svg+xml"])).decode("utf-8")
        assert kept.startswith('<svg xmlns="http://www.w3.org/2000/svg"') and kept.endswith("</svg>")
        assert ('xmlns:xlink="http://www.w3.org/1999/xlink"' in kept) == (cell is cells[0])     # given to the one that needs it
        copy = base64.b64decode(forms["image/png"])
        assert copy[:8] == b"\x89PNG\r\n\x1a\n"
        copies.append(struct.unpack(">II", copy[16:24]))
    # The copy has the size the drawing asks for (JupyterLab shows it pixel for pixel) — within reason.
    assert copies == [(240, 120), (240, 120), (189, 94), (300, 150), (2400, 80)]
    nbformat.validate(nbformat.read(str(folder / "drawn.ipynb"), as_version=4))

    page.reload()
    pictures(page, 6)
    assert drawn() == ["image/svg+xml", "image/svg+xml", "image/png"] + ["image/svg+xml"] * 3


def test_pictures_come_from_files_go_into_text_and_survive_copying_and_undo(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    paper = page.locator("#content").bounding_box()
    (folder / "plot [v2].png").write_bytes(png(120, 60))
    (folder / "sketch.svg").write_text('<?xml version="1.0"?>\n' + DRAWING.replace(' width="240" height="120"', ""),
                                       encoding="utf-8")                    # a drawing without a size of its own
    for name in ("notes.txt", "broken.png", "broken.svg"):
        (folder / name).write_text("words")
    choose = lambda *names: page.set_input_files("#picture-file", [str(folder / name) for name in names])     # noqa: E731

    # Chosen with the Picture button: at the cursor, one under the other,
    # called what their files are called (as far as markdown can say it).
    page.mouse.click(paper["x"] + 24, paper["y"] + 24)
    choose("plot [v2].png", "sketch.svg")
    chosen = pictures(page, 2)
    assert [(p["kind"], p["shown"], p["left"]) for p in chosen] == [("image/png", [120, 60], 24), ("image/svg+xml", [240, 120], 24)]
    assert chosen[1]["top"] >= chosen[0]["top"] + 66 and all(p["selected"] for p in chosen)
    assert page.evaluate("[...document.querySelectorAll('.region img')].map((img) => img.alt)") == ["plot v2", "sketch"]
    # What cannot be taken is refused in so many words.
    page.evaluate(LIMIT, ["picture", 100])                                  # (bytes: less than the picture has)
    for name, said in (("notes.txt", "is not a picture"), ("broken.png", "cannot be read as a picture"),
                       ("broken.svg", "is not an SVG drawing"), ("plot [v2].png", "is too large")):
        choose(name)
        page.wait_for_function(f"{TOAST}.includes('{said}')")
    page.evaluate(LIMIT, ["picture", 20e6])
    assert len(page.evaluate(PICTURES)) == 2

    # A picture file let go of over the page lands there (moved in from the
    # right edge as far as it needs to fit); another kind of file is refused.
    assert page.evaluate(DROP, [paper["x"] + 640, paper["y"] + 400, ["dropped.png", "image/png", BITMAP], None]) == [True, True]
    dropped = pictures(page, 3)[2]
    assert (dropped["left"], dropped["top"], dropped["selected"]) == (680 - 132, 392, True)
    page.evaluate(DROP, [paper["x"] + 100, paper["y"] + 500, ["notes.txt", "text/plain", "d29yZHM="], None])
    page.wait_for_function(f"{TOAST}.includes('Only pictures can be dropped')")
    assert len(page.evaluate(PICTURES)) == 3

    # Copied and pasted on the sheet, a picture takes what it shows along;
    # deleted, it comes back with undo.
    copied = page.evaluate("""() => { window.copied = new DataTransfer();
        document.activeElement.dispatchEvent(new ClipboardEvent('copy', {clipboardData: window.copied, bubbles: true, cancelable: true}));
        return [...window.copied.types]; }""")
    assert "application/x-edsl-regions" in copied
    page.mouse.click(paper["x"] + 304, paper["y"] + 24)
    page.evaluate("document.activeElement.dispatchEvent(new ClipboardEvent('paste', {clipboardData: window.copied, bubbles: true, cancelable: true}))")
    copy = pictures(page, 4)[3]
    assert (copy["left"], copy["kind"], copy["shown"], copy["selected"]) == (304, "image/png", [120, 60], True)
    keys.press("Delete")
    assert len(pictures(page, 3)) == 3
    keys.press("Control+z")
    assert pictures(page, 4)[3]["shown"] == [120, 60]

    # While a text is being written a picture goes into it, at the caret —
    # pasted or chosen — unless the clipboard has words, which are then
    # what is pasted.
    page.mouse.click(paper["x"] + 24, paper["y"] + 560)
    keys.type('"')
    keys.type("Before  after")
    for _ in range(6):
        keys.press("ArrowLeft")
    assert page.evaluate(PASTE, [[["image.png", "image/png", BITMAP]], [["text/plain", "words"]]]) is False
    assert page.evaluate(PASTE, [[["image.png", "image/png", BITMAP]], []]) is True
    page.wait_for_function(f"{EDITOR}.includes('attachment:')")
    assert re.fullmatch(r"Before !\[picture\]\(attachment:[0-9a-f]{12}\.png\) after", page.evaluate(EDITOR))
    choose("plot [v2].png")
    page.wait_for_function(f"{EDITOR}.split('attachment:').length === 3")
    assert re.fullmatch(r"Before !\[picture\]\(attachment:\w+\.png\)!\[plot v2\]\(attachment:\w+\.png\) after", page.evaluate(EDITOR))
    keys.press("Escape")
    within = pictures(page, 6)[4]
    assert (within["picture"], within["shown"]) == (False, [120, 60])          # a text with pictures in it
    # One of them is taken out of the text again (and so, further down, is not saved).
    page.locator(".region.text:not(.picture)").click(position={"x": 30, "y": 30})
    page.evaluate("() => { const area = document.querySelector('.region .ed textarea');"
                  " area.setSelectionRange(area.value.indexOf('![plot v2]'), area.value.indexOf(' after')); }")
    keys.press("Backspace")
    keys.press("Escape")
    assert len(pictures(page, 5)) == 5

    # One that arrives when its text has been finished goes below the text.
    page.mouse.click(paper["x"] + 24, paper["y"] + 640)
    keys.type('"')
    keys.type("A caption")
    page.evaluate("""(paste) => { const area = document.querySelector('.region .ed textarea');
        eval(paste)([[['image.png', 'image/png', '%s']], []]);
        area.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true})); }""" % BITMAP, PASTE)
    late = pictures(page, 6)[5]
    caption = page.evaluate(BOXES)[5]
    assert late["picture"] and late["left"] == 24 and late["top"] >= caption["top"] + caption["height"]
    assert page.evaluate("document.querySelectorAll('.region')[5].innerText.trim()") == "A caption"

    # A math region takes no picture, and the page says where one goes.
    # Chosen while one is being written, a picture goes below it.
    page.mouse.click(paper["x"] + 304, paper["y"] + 128)
    keys.type("k_one := 1")
    assert page.evaluate(PASTE, [[["image.png", "image/png", BITMAP]], []]) is True
    assert "not into a math region" in page.evaluate(TOAST)
    assert page.evaluate(EDITOR) == "k_one := 1"
    choose("plot [v2].png")
    under = pictures(page, 7)[6]
    settled(page)
    math = page.evaluate(BOXES)[7]
    assert page.query_selector(".region.editing") is None
    assert under["left"] == math["left"] == 304 and under["top"] >= math["top"] + math["height"]
    # Dropped while one is being written, it still lands where it is dropped.
    page.mouse.click(paper["x"] + 480, paper["y"] + 128)
    keys.type("k_two := 2")
    page.evaluate(DROP, [paper["x"] + 480, paper["y"] + 300, ["dropped.png", "image/png", BITMAP], None])
    landed = pictures(page, 8)[7]
    settled(page)
    assert (landed["left"], landed["top"]) == (480, 296) and page.query_selector(".region.editing") is None
    # Let go of beside the page — over the bar, say — a picture goes to the
    # cursor, as a pasted one does; the browser never shows a file instead.
    assert page.evaluate(DROP, [0, 0, ["dropped.png", "image/png", BITMAP], "#bar"]) == [True, True]
    beside = pictures(page, 9)[8]
    assert (beside["left"], beside["top"], beside["selected"]) == (480, 376, True)
    assert page.evaluate(DROP, [0, 0, ["notes.txt", "text/plain", "d29yZHM="], "#bar"]) == [True, True]

    # A sheet with more pictures than can be sent to the server is not sent, and that is said.
    page.evaluate(LIMIT, ["sheet", 1000])
    save_as(page, "filed")
    page.wait_for_function(f"{TOAST}.includes('more than the')")
    assert not (folder / "filed.ipynb").exists()
    page.evaluate(LIMIT, ["sheet", 100e6])

    # The server says how many pictures it stored.  One that does not —
    # one started before pictures could be kept, say — is not believed:
    # the sheet stays unsaved, and the page says why.
    def from_before(route):
        reply = route.fetch()
        answer = reply.json()
        answer.pop("pictures", None)
        route.fulfill(response=reply, json=answer)

    page.route("**/api/doc?*", lambda route: from_before(route) if route.request.method == "PUT" else route.continue_())
    save_as(page, "filed")
    page.wait_for_function(f"{TOAST}.includes('stored 0 of the 9 pictures')")
    assert page.evaluate("!document.querySelector('#doc-dirty').hidden")
    page.unroute("**/api/doc?*")

    # All of it is in the file, and nothing that is not named any more.
    page.keyboard.press("Control+s")
    page.wait_for_function("document.querySelector('#doc-dirty').hidden", timeout=20_000)
    notebook = nbformat.read(str(folder / "filed.ipynb"), as_version=4)
    texts = [c for c in notebook.cells if c.cell_type == "markdown"]
    assert [len(c.attachments) for c in texts if "attachments" in c] == [1] * 9
    assert sum("image/svg+xml" in forms for c in texts for forms in c.get("attachments", {}).values()) == 1
    assert all(f"attachment:{name}" in c.source for c in texts for name in c.get("attachments", {}))


def test_a_notebook_with_pictures_pasted_in_jupyterlab_shows_them_as_they_are(page_and_folder):
    # JupyterLab keeps a pasted picture attached to its text cell.  Such a
    # notebook has no page layout: its cells flow, and none has a width.
    page, folder = page_and_folder
    pasted = "6f3c1d0e-2a7b-4c55-9d0e-0a1b2c3d4e5f.png"
    nbformat.write(nbformat.v4.new_notebook(cells=[
        nbformat.v4.new_markdown_cell(f"![image.png](attachment:{pasted})", attachments={pasted: {"image/png": BITMAP}}),
        nbformat.v4.new_markdown_cell("Words under the picture."),
        nbformat.v4.new_markdown_cell("![sketch](attachment:sketch.svg)", attachments={      # a drawing with no bitmap beside it
            "sketch.svg": {"image/svg+xml": base64.b64encode(DRAWING.encode()).decode()}}),
        nbformat.v4.new_markdown_cell("![wide](attachment:wide.png)", attachments={
            "wide.png": {"image/png": base64.b64encode(png(1000, 50)).decode()}}),
        nbformat.v4.new_markdown_cell(f"Words, and a picture among them: ![image.png](attachment:{pasted})",
                                      attachments={pasted: {"image/png": BITMAP}}),
    ]), str(folder / "fromlab.ipynb"))
    page.goto(page.url.split("?")[0] + "?open=fromlab.ipynb")
    first, drawing, wide, among = pictures(page, 4)

    # A picture is as large as it is — not as wide as the page, as a text
    # without a width of its own is, unless it is wider than that — and
    # the cells under it make room for it once it has been read.
    assert (first["picture"], first["width"], first["shown"]) == (True, 132, [120, 60])
    assert (drawing["kind"], drawing["width"], drawing["shown"]) == ("image/svg+xml", 252, [240, 120])
    assert (wide["width"], wide["shown"]) == (680, [668, 33])
    assert (among["picture"], among["width"], among["shown"]) == (False, 680, [120, 60])       # that one is a text
    boxes = page.evaluate(BOXES)
    assert round(boxes[1]["width"]) == 680
    assert boxes[1]["top"] >= boxes[0]["top"] + 66 and boxes[2]["top"] >= boxes[1]["top"] + boxes[1]["height"]
    assert page.evaluate("document.querySelector('#doc-dirty').hidden")            # looking has changed nothing

    # Its right edge makes it wider (or narrower) all the same.
    box = page.locator(".region.picture").first.bounding_box()
    page.mouse.move(box["x"] + 60, box["y"] + 20)               # (the edge shows its handle to a pointer over the picture)
    page.mouse.move(box["x"] + box["width"] + 1, box["y"] + 20)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] + 125, box["y"] + 20, steps=4)
    page.mouse.up()
    wider = pictures(page, 4)[0]
    assert (wider["width"], wider["shown"]) == (256, [244, 122])
