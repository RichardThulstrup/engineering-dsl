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
            page.problems = problems                # (a test that provokes an error looks for it here, and takes it out)
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


def test_text_written_as_markdown_gets_a_table_and_formulas_from_the_panel(page_and_folder):
    page, _ = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    # Text is written as it looks unless its markdown is asked for (View >
    # Write text as Markdown): then the panel's tools write markdown.
    page.click("#view-menu .menu-button")
    page.click("#view-menu [data-cmd='toggle-markdown']")

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


def test_a_sheet_prints_on_the_pages_it_fills_and_no_more(page_and_folder):
    # On screen a sheet always has room below its last region and below the
    # cursor: another page is shown before the last one is full.  That room
    # is not paper — it used to come out of the printer as an empty page.
    page, _ = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard

    def click(y):
        """Click on the sheet at height y, scrolled into view."""
        page.evaluate("(y) => { document.querySelector('#desk').scrollTop = Math.max(0, y - 300); }", y)
        paper = page.locator("#content").bounding_box()
        page.mouse.click(paper["x"] + 40, paper["y"] + y)

    def sheet():
        """[pages shown on screen, pages printed, where the last region ends, how long the sheet is on paper]"""
        ends = page.evaluate("Math.max(...[...document.querySelectorAll('.region')].map("
                             "(r) => parseFloat(r.style.top) + r.getBoundingClientRect().height))")
        pdf = page.pdf(prefer_css_page_size=True)               # the sheet's own paper: A4, 15 mm margins
        page.emulate_media(media="print")
        long = page.evaluate("document.querySelector('#content').getBoundingClientRect().height")
        page.emulate_media(media="null")
        return [page.evaluate("document.querySelectorAll('.pagebreak').length + 1"),
                len(re.findall(rb"/Type\s*/Page\b", pdf)), round(ends), round(long)]

    # A region low on the first page: the screen shows a second page already.
    click(808)
    keys.type("k_one := 1")
    keys.press("Enter")
    settled(page)
    assert sheet() == [2, 1, 830, 830]
    # The cursor on that second page does not make paper of it …
    click(1300)
    assert page.evaluate("parseFloat(document.querySelector('#cross').style.top)") > 1009
    assert sheet() == [2, 1, 830, 830]
    # … a region there does, and a full second page is still two pages.
    click(1300)                                 # (switching the print layout on, as sheet() does, costs the sheet the keyboard)
    keys.type("k_two := 2")
    keys.press("Enter")
    settled(page)
    assert sheet()[:2] == [2, 2]
    click(1880)
    keys.type("k_three := 3")
    keys.press("Enter")
    settled(page)
    shown, printed, ends, long = sheet()
    assert (shown, printed) == (3, 2) and 1818 < ends == long <= 2018


def linked(pdf):
    """Where the links of a printed sheet are: ``([(address, page, px down the paper), ...], pages)``.

    Chromium writes a PDF's pages, and the links on each with their
    places, as plain text into the file: that is read here, so that a
    test can tell what was printed where without a library for PDFs.
    """
    objects = {int(m.group(1)): m.group(2) for m in re.finditer(rb"(\d+) 0 obj\s*(.*?)\s*endobj", pdf, re.S)}

    def leaves(number):                 # (the pages hang in a tree, eight to a branch)
        body = objects[number]
        if b"/Type /Pages" not in body:
            return [number]
        kids = re.search(rb"/Kids \[(.*?)\]", body, re.S).group(1)
        return [leaf for kid in re.findall(rb"(\d+) 0 R", kids) for leaf in leaves(int(kid))]

    pages = leaves(next(number for number, body in objects.items() if b"/Type /Pages" in body and b"/Parent" not in body))
    found = []
    for number, sheet in enumerate(pages, start=1):
        high = float(re.search(rb"/MediaBox \[0 0 [\d.]+ ([\d.]+)\]", objects[sheet]).group(1))
        named = re.search(rb"/Annots (\d+) 0 R|/Annots \[(.*?)\]", objects[sheet], re.S)
        if not named:
            continue
        for ref in re.findall(rb"(\d+) 0 R", objects[int(named.group(1))] if named.group(1) else named.group(2)):
            address = re.search(rb"/URI \((.*?)\)", objects[int(ref)])
            box = re.search(rb"/Rect \[([\d.\s-]+)\]", objects[int(ref)])
            if address and box:
                corners = [float(value) for value in box.group(1).split()]
                found.append((address.group(1).decode(), number, round((high - max(corners[1], corners[3])) * 96 / 72, 2)))
    return found, len(pages)


def test_every_page_of_a_long_sheet_is_printed_where_the_screen_has_it(page_and_folder):
    # A page of the sheet is 1009 px high.  A printed page took 1010 of the
    # sheet — a browser gives a page a whole number of px, and A4 less two
    # margins of 15 mm is 1009.6 — so that everything came out one px
    # higher up with each page, and from the 32nd page on a region at the
    # top of a page was printed at the foot of the page before.
    page, folder = page_and_folder
    cells = [nbformat.v4.new_code_cell("from utils.Engineer import *")]
    count = 12
    for k in range(count):
        for where, y in (("top", k * 1009 + 16), ("end", (k + 1) * 1009 - 48)):
            cell = nbformat.v4.new_markdown_cell(f"[{where} {k + 1}](https://example.org/{where}/{k + 1})")
            cell.metadata["edsl"] = {"x": 40, "y": y, "w": 200}
            cells.append(cell)
    nbformat.write(nbformat.v4.new_notebook(cells=cells), str(folder / "long.ipynb"))
    page.goto(page.url.split("?")[0] + f"?token={TOKEN}&open=long.ipynb")
    page.wait_for_function(f"document.querySelectorAll('.region a').length === {2 * count}")
    tops = page.evaluate("[...document.querySelectorAll('.region')].map((r) => parseFloat(r.style.top))")
    assert tops == [y for k in range(count) for y in (k * 1009 + 16, (k + 1) * 1009 - 48)]             # (as they were placed)
    found, pages = linked(page.pdf(prefer_css_page_size=True))
    places = {address: (number, down) for address, number, down in found}
    assert pages == count and len(found) == len(places) == 2 * count
    for where in ("top", "end"):
        first = places[f"https://example.org/{where}/1"][1]
        for k in range(count):
            number, down = places[f"https://example.org/{where}/{k + 1}"]
            assert number == k + 1 and abs(down - first) < 0.3, (where, k + 1, number, down, first)
    # (57 px of margin above the sheet's page: the first link is some px into its region.)
    assert 57 + 16 < places["https://example.org/top/1"][1] < 57 + 16 + 12
    assert places["https://example.org/end/1"][1] - places["https://example.org/top/1"][1] == 1009 - 48 - 16


def test_what_is_changed_while_a_sheet_is_being_saved_is_still_to_be_saved(page_and_folder):
    # Saving takes a moment — a long one for a sheet full of pictures on a
    # slow disk.  What is typed in that moment is not in the file; the sheet
    # said it was saved all the same, and would have closed without a word.
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    sources = lambda: [cell.source for cell in nbformat.read(str(folder / "slow.ipynb"), as_version=4).cells[1:]]      # noqa: E731
    keys.type("first := 5")
    keys.press("Enter")
    settled(page)
    save_as(page, "slow.ipynb")
    page.wait_for_function("document.querySelector('#doc-dirty').hidden")
    keys.type("second := 6")
    keys.press("Enter")
    settled(page)
    # The save is held up on its way to the server.
    held = []
    page.route("**/api/doc?*", lambda route: held.append(route) if route.request.method == "PUT" else route.continue_())
    page.evaluate("document.querySelector('#toast').hidden = true")
    keys.press("Control+s")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    keys.type("third := 7")
    keys.press("Enter")
    page.wait_for_function("document.querySelectorAll('.region').length === 3")
    held[0].continue_()
    page.wait_for_function(f"{TOAST} === 'Saved'")
    page.unroute("**/api/doc?*")
    assert sources() == ["first := 5", "second := 6"]
    assert page.evaluate("!document.querySelector('#doc-dirty').hidden") and page.title().startswith("\u2022 ")
    # Saved again, with nothing changed meanwhile, it is all in the file, and the sheet says so.
    save(page)
    assert sources() == ["first := 5", "second := 6", "third := 7"]
    assert page.evaluate("document.querySelector('#doc-dirty').hidden") and page.title() == "slow \u2014 Worksheet"


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
# The text being written as it looks, in short: a formula as its LaTeX
# between dollars, a picture as [what it is called].
WRITTEN = """() => {
    const copy = document.querySelector('.region .ProseMirror').cloneNode(true);
    copy.querySelectorAll('.ProseMirror-separator, .ProseMirror-trailingBreak').forEach((node) => node.remove());
    copy.querySelectorAll('.formula').forEach((node) => {
        const tex = node.querySelector('annotation').textContent;
        node.replaceWith(node.tagName === 'DIV' ? '$$' + tex + '$$' : '$' + tex + '$');
    });
    copy.querySelectorAll('img').forEach((node) => node.replaceWith('[' + node.alt + ']'));
    return copy.innerHTML;
}"""
# What a copy takes along from where the keyboard is — made by hand, no clipboard is touched.
COPIED = """() => {
    const data = new DataTransfer();
    const event = new ClipboardEvent('copy', {clipboardData: data, bubbles: true, cancelable: true});
    document.activeElement.dispatchEvent(event);
    return {text: data.getData('text/plain'), html: data.getData('text/html'), types: [...data.types]};
}"""
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


def save(page):
    """Ctrl+S on a sheet that has a file, and the server's answer taken in."""
    page.evaluate("document.querySelector('#toast').hidden = true")
    with page.expect_response(lambda response: "/api/doc" in response.url and response.request.method == "PUT"):
        page.keyboard.press("Control+s")
    page.wait_for_function(f"{TOAST} === 'Saved'")


def frame(page):
    """Wait for the next frame.  A browser tells of a caret moved (by an
    arrow key, a click) with the next frame it draws; only then does the
    editor of a text know where the caret is."""
    page.evaluate("() => new Promise((done) => requestAnimationFrame(() => setTimeout(done)))")


def kept(page):
    """Finish the text being written (which leaves its region selected) and give its markdown."""
    page.keyboard.press("Escape")
    return page.evaluate(COPIED)["text"]


def given_the_keyboard(page):
    """Let the editor of a text settle after it was opened.  For 20 ms
    after it has been given the keyboard, ProseMirror puts a caret that
    the browser moved back where it had it: no one types that fast, but
    a test does."""
    page.wait_for_timeout(50)


def reopen(page):
    """Enter opens the region that is selected: a text with the caret at its end."""
    page.keyboard.press("Enter")
    given_the_keyboard(page)


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
    assert page.evaluate(WRITTEN) == "<p>[picture]</p>"
    assert re.fullmatch(r"!\[picture\]\(attachment:[0-9a-f]{12}\.png\)", kept(page))
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
    given_the_keyboard(page)
    keys.type("Before  after")
    for _ in range(6):
        keys.press("ArrowLeft")
    frame(page)
    assert page.evaluate(PASTE, [[["image.png", "image/png", BITMAP]], [["text/plain", "words"]]]) is True
    assert page.evaluate(WRITTEN) == "<p>Before words after</p>"
    assert page.evaluate(PASTE, [[["image.png", "image/png", BITMAP]], []]) is True
    page.wait_for_selector(".region .ProseMirror img[alt=picture]")
    assert page.evaluate(WRITTEN) == "<p>Before words[picture] after</p>"
    choose("plot [v2].png")
    page.wait_for_selector(".region .ProseMirror img[alt='plot v2']")
    assert page.evaluate(WRITTEN) == "<p>Before words[picture][plot v2] after</p>"
    assert re.fullmatch(r"Before words!\[picture\]\(attachment:\w+\.png\)!\[plot v2\]\(attachment:\w+\.png\) after", kept(page))
    within = pictures(page, 6)[4]
    assert (within["picture"], within["shown"]) == (False, [120, 60])          # a text with pictures in it
    # One of them is taken out of the text again (and so, further down, is not saved).
    page.locator(".region.text:not(.picture)").click(position={"x": 30, "y": 8})
    page.locator(".region .ProseMirror img[alt='plot v2']").click()            # a click on a picture selects it
    frame(page)
    keys.press("Backspace")
    assert re.fullmatch(r"Before words!\[picture\]\(attachment:\w+\.png\) after", kept(page))
    assert len(pictures(page, 5)) == 5

    # One that arrives when its text has been finished goes below the text.
    page.mouse.click(paper["x"] + 24, paper["y"] + 640)
    keys.type('"')
    keys.type("A caption")
    page.evaluate("""(paste) => { const text = document.querySelector('.region .ProseMirror');
        eval(paste)([[['image.png', 'image/png', '%s']], []]);
        text.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true})); }""" % BITMAP, PASTE)
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


# ---- text written as it looks -----------------------------------------------

# What the bar above a text shows as on, and the kind of paragraph it names.
BAR = """() => {
    const bar = document.querySelector('.region .formatbar');
    return [...bar.querySelectorAll('.fmt.on')].map((b) => b.dataset.mark || b.dataset.tool).join(' ') + ' | '
        + bar.querySelector('select').selectedOptions[0].textContent;
}"""
IN_TEXT = "document.activeElement.classList.contains('ProseMirror')"
# Where the keyboard is, a moment after it went there: a text is finished a
# tick after the keyboard has left it, unless it went to a field of its own.
WENT_TO = """() => new Promise((done) => setTimeout(() => done(
    (document.querySelector('.region.editing') ? 'writing, in ' : 'finished, in ') + (document.activeElement.className || document.activeElement.tagName)), 60))"""
# A key as another keyboard sends it; answers whether the page took it for itself.
KEY = """([key, code, keyCode, held]) => {
    const event = new KeyboardEvent('keydown', {key, code, keyCode, bubbles: true, cancelable: true, ...held});
    document.activeElement.dispatchEvent(event);
    return event.defaultPrevented;
}"""


def middle(page, selector):
    box = page.locator(selector).first.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def test_text_is_written_as_it_looks_with_keys_and_a_bar_for_its_formatting(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    paper = page.locator("#content").bounding_box()
    bar = lambda what: page.locator(f".formatbar [data-{what}]")                 # noqa: E731

    def afresh():
        """Open the region that was just finished, and empty it."""
        reopen(page)
        keys.press("Control+a")
        keys.press("Delete")

    # A text region is written as it looks: what is bold is bold while it is typed.
    page.mouse.click(paper["x"] + 40, paper["y"] + 40)
    keys.type('"')
    assert page.query_selector(".region .ed textarea") is None and page.locator(".region .formatbar").is_visible()
    keys.type("Plain ")
    keys.press("Control+b")
    assert page.evaluate(BAR) == "strong | Text"                # (the bar says so before a letter of it is typed)
    keys.press("Control+b")
    for key, word in (("b", "bold"), ("i", "slanted"), ("u", "under")):
        keys.press(f"Control+{key}")
        keys.type(word)
        keys.press(f"Control+{key}")
        keys.type(" ")
    keys.type("H")
    keys.press("Control+,")
    keys.type("2")
    keys.press("Control+,")
    keys.type("O x")
    keys.press("Control+.")
    keys.type("2")
    shown = "<p>Plain <strong>bold</strong> <em>slanted</em> <u>under</u> H<sub>2</sub>O x<sup>2</sup></p>"
    assert page.evaluate(WRITTEN) == shown
    assert page.evaluate(BAR) == "sup | Text"                   # the bar shows what the text at the caret is
    # In the file it is markdown — with HTML's own tags for what markdown has no mark for.
    assert kept(page) == "Plain **bold** *slanted* <u>under</u> H<sub>2</sub>O x<sup>2</sup>"
    assert page.evaluate("document.querySelector('.region.text .md').innerHTML") == shown         # and shown as it was written

    # The bar does the same to what is selected, and the keyboard stays in the text.
    afresh()
    keys.type("one two three")
    for _ in range(5):
        keys.press("Shift+ArrowLeft")
    frame(page)
    bar("mark=strong").click()
    assert page.evaluate(WRITTEN) == "<p>one two <strong>three</strong></p>"
    assert page.evaluate(BAR) == "strong | Text" and page.evaluate(IN_TEXT)
    bar("mark=em").click()
    bar("mark=strong").click()
    assert page.evaluate(WRITTEN) == "<p>one two <em>three</em></p>"
    for mark in ("del", "code"):
        bar(f"mark={mark}").click()
        assert page.evaluate(WRITTEN) == f"<p>one two <em><{mark}>three</{mark}></em></p>"
        bar(f"mark={mark}").click()
    keys.press("Control+Shift+x")
    assert kept(page) == "one two *~~three~~*"
    reopen(page)
    keys.press("Home")                                          # (right after it was opened: the key is the caret's)
    frame(page)
    assert page.evaluate(BAR) == " | Text"                      # nothing at the start of the line
    keys.press("ArrowUp")                                       # (above a paragraph there is nothing to make room for)
    keys.press("ArrowDown")
    assert page.evaluate(WRITTEN) == "<p>one two <em><del>three</del></em></p>"
    keys.press("Home")
    frame(page)

    # What kind of paragraph it is: chosen in the bar, or by key.
    page.locator(".formatbar select").focus()                  # (the list has the keyboard while it is open)
    assert page.evaluate(WENT_TO) == "writing, in SELECT"
    page.locator(".formatbar select").select_option(label="Heading 2")
    assert page.evaluate(WRITTEN) == "<h2>one two <em><del>three</del></em></h2>" and page.evaluate(IN_TEXT)
    assert page.evaluate(BAR) == " | Heading 2"
    assert kept(page) == "## one two *~~three~~*"
    reopen(page)
    keys.press("Control+Shift+0")
    assert page.evaluate(WRITTEN) == "<p>one two <em><del>three</del></em></p>"
    keys.press("Control+Shift+3")
    assert page.evaluate(BAR) == "em del | Heading 3"           # (opened by key, the caret is at the end)
    keys.press("Home")
    frame(page)
    keys.press("Backspace")                                     # at the start of a heading: plain text again
    assert page.evaluate(WRITTEN) == "<p>one two <em><del>three</del></em></p>"

    # A keyboard may make something else of Shift and a digit — "(" of 8, on
    # a Danish one — and the key is the text's all the same.  AltGr and a
    # digit, which a browser tells of as Ctrl+Alt, is a character (@, £, })
    # and never a key of the text's.
    held = {"ctrlKey": True, "shiftKey": True}
    assert page.evaluate(KEY, ["!", "Digit1", 49, held]) is True
    assert page.evaluate(WRITTEN) == "<h1>one two <em><del>three</del></em></h1>"
    assert page.evaluate(KEY, ["=", "Digit0", 48, held]) is True
    assert page.evaluate(KEY, [")", "Digit9", 57, held]) is True
    assert page.evaluate(WRITTEN) == "<ol><li><p>one two <em><del>three</del></em></p></li></ol>"
    assert page.evaluate(KEY, ["(", "Digit8", 56, held]) is True
    assert page.evaluate(WRITTEN) == "<ul><li><p>one two <em><del>three</del></em></p></li></ul>"
    # Code is Ctrl and <: a key of its own there, Shift and the comma on an American keyboard.
    assert page.evaluate(KEY, ["<", "IntlBackslash", 226, {"ctrlKey": True}]) is True and page.evaluate(BAR) == "code bullets | Text"
    assert page.evaluate(KEY, ["<", "Comma", 188, held]) is True and page.evaluate(BAR) == "bullets | Text"
    for key, code, number in (("@", "Digit2", 50), ("£", "Digit3", 51), ("}", "Digit0", 48)):
        assert page.evaluate(KEY, [key, code, number, {"ctrlKey": True, "altKey": True}]) is False
    assert page.evaluate(WRITTEN) == "<ul><li><p>one two <em><del>three</del></em></p></li></ul>"
    assert page.evaluate("document.querySelector('#paper').style.zoom || '1'") == "1"      # (Ctrl and = were the text's, not the page's)

    # Lists: begun from the bar, gone on with Enter, nested with Tab; an
    # empty item ends the list.  A quotation likewise.
    keys.press("Escape")
    afresh()
    keys.type("first")
    bar("tool=bullets").click()
    keys.press("Enter")
    keys.type("second")
    keys.press("Enter")
    keys.press("Tab")
    keys.type("inner")
    keys.press("Enter")
    keys.press("Shift+Tab")
    keys.type("third")
    assert page.evaluate(BAR) == "bullets | Text" and page.evaluate(IN_TEXT)
    assert kept(page) == "- first\n- second\n  - inner\n- third"
    keys.press("Enter")
    bar("tool=numbers").click()
    assert page.evaluate(BAR) == "numbers | Text"
    keys.press("Enter")
    keys.press("Enter")
    keys.type("after")
    keys.press("Control+'")
    assert page.evaluate(BAR) == "quote | Text"
    assert kept(page) == "1. first\n2. second\n   - inner\n3. third\n\n> after"
    keys.press("Enter")
    bar("tool=quote").click()
    bar("tool=bullets").click()
    assert page.evaluate(BAR) == "bullets | Text"
    bar("tool=bullets").click()                                 # once more: out of the list
    assert page.evaluate(BAR) == " | Text"
    bar("tool=numbers").click()
    assert kept(page) == "1. first\n2. second\n   - inner\n3. third\n\n1. after"

    # A link: Ctrl+K asks for the address the selected text is to go to.
    afresh()
    keys.type("See the manual")
    for _ in range(6):
        keys.press("Shift+ArrowLeft")
    frame(page)
    keys.press("Control+k")
    assert page.evaluate(WENT_TO) == "writing, in link"
    keys.type("https://example.org/a b(c)")
    keys.press("Enter")
    assert page.evaluate(WENT_TO).startswith("writing, in md ProseMirror") and page.evaluate(BAR) == "link | Text"
    linked = "See the [manual](https://example.org/a%20b(c%29)"        # (as markdown can hold it between its brackets)
    assert kept(page) == linked
    assert page.evaluate("document.querySelector('.region.text a').href") == "https://example.org/a%20b(c%29"
    # With the caret in a link the field shows where it goes; one that could
    # do harm is not taken, and the field says so; an empty one takes the link off.
    reopen(page)
    keys.press("ArrowLeft")
    frame(page)
    assert page.evaluate(BAR) == "link | Text"
    bar("tool=link").click()
    assert page.evaluate("document.activeElement.value") == "https://example.org/a%20b(c%29"
    keys.press("Control+a")
    keys.type("javascript:alert(1)")
    keys.press("Enter")
    assert page.evaluate("document.activeElement.className") == "link bad"
    keys.press("Escape")
    assert page.evaluate(IN_TEXT) and page.evaluate("document.querySelector('.formatbar .link').hidden")
    assert kept(page) == linked
    reopen(page)
    keys.press("ArrowLeft")
    frame(page)
    keys.press("Control+k")
    keys.press("Control+a")
    keys.press("Delete")
    keys.press("Enter")
    assert kept(page) == "See the manual"
    # With nothing selected the address itself is put, as a link; one that
    # begins with www. is on the web.
    reopen(page)
    keys.type(" at ")
    keys.press("Control+k")
    keys.type("www.example.org/x")
    keys.press("Enter")
    assert page.evaluate(WRITTEN) == ('<p>See the manual at <a href="https://www.example.org/x" target="_blank"'
                                      ' rel="noopener noreferrer">https://www.example.org/x</a></p>')
    assert kept(page) == "See the manual at https://www.example.org/x"

    # Markdown's own marks work while they are typed: "# " begins a heading,
    # "- " a list, **…** is bold and $…$ a formula.  In code, Enter is a new line.
    afresh()
    keys.type("# Title")
    keys.press("Enter")
    keys.type("Some **bold**, *slanted*, ~~struck~~, `code` and $x^2$ here, 2*3*4 not.")
    keys.press("Enter")
    keys.type("- item")
    keys.press("Enter")
    keys.press("Enter")
    keys.type("1. numbered")
    keys.press("Enter")
    keys.press("Enter")
    keys.type("> quoted")
    keys.press("Enter")
    keys.press("Enter")
    keys.type("```")
    keys.type("x = 1")
    keys.press("Enter")
    keys.type("y = 2")
    keys.press("Shift+Enter")                                   # out of the code
    keys.type("Line")
    keys.press("Shift+Enter")                                   # a new line in the same paragraph
    keys.type("break")
    assert page.evaluate(WRITTEN) == (
        "<h1>Title</h1><p>Some <strong>bold</strong>, <em>slanted</em>, <del>struck</del>, <code>code</code> and $x^2$ here,"
        " 2*3*4 not.</p><ul><li><p>item</p></li></ul><ol><li><p>numbered</p></li></ol><blockquote><p>quoted</p></blockquote>"
        "<pre><code>x = 1\ny = 2</code></pre><p>Line<br>break</p>")
    typed = ("# Title\n\nSome **bold**, *slanted*, ~~struck~~, `code` and $x^2$ here, 2\\*3\\*4 not.\n\n- item\n\n1. numbered\n\n"
             "> quoted\n\n```\nx = 1\ny = 2\n```\n\nLine\\\nbreak")
    assert kept(page) == typed
    # Backspace takes such a mark back at once: the dollars were dollars.
    afresh()
    keys.type("costs $5 or 6$")
    assert page.evaluate(WRITTEN) == "<p>costs $5 or 6$</p>" and page.locator(".region .formula").count() == 1
    keys.press("Backspace")
    assert page.locator(".region .formula").count() == 0
    assert kept(page) == "costs \\$5 or 6\\$"

    # $$…$$ is a formula on a line of its own: a block of its own where it
    # is all the paragraph has — and the writing goes on below it.
    afresh()
    keys.type("$$E=mc^2$$")
    keys.type("so")
    assert page.evaluate(WRITTEN) == "$$E=mc^2$$<p>so</p>"
    keys.type(" $$a$$ too")
    assert kept(page) == "$$E=mc^2$$\n\nso $$a$$ too"
    # A formula whose LaTeX is emptied is gone, wherever it stood.
    afresh()
    keys.type("$$x$$")
    keys.press("ArrowUp")                                       # onto the formula
    keys.press("Enter")                                         # into its LaTeX
    assert page.evaluate(WENT_TO) == "writing, in tex-source"
    keys.press("Control+a")
    keys.press("Delete")
    keys.press("Enter")
    assert page.evaluate(WENT_TO).startswith("writing, in md ProseMirror")
    keys.type("in its place: $y$")
    keys.press("ArrowLeft")
    keys.press("Enter")
    keys.press("Control+a")
    keys.press("Delete")
    keys.press("Enter")
    assert page.evaluate(WRITTEN) == "<p>in its place: </p><p></p>"
    assert kept(page) == "in its place:"
    # Room is made beside a formula, a list and code as beside a table: the
    # arrow key that would leave the text makes a paragraph there.
    afresh()
    keys.type("$$x$$")
    keys.press("ArrowUp")                                       # onto the formula
    keys.press("ArrowUp")                                       # and out of the text: room above it
    keys.type("### Above")
    assert page.evaluate(WRITTEN) == "<h3>Above</h3>$$x$$<p></p>"
    keys.press("Control+End")
    keys.type("- item")
    keys.press("ArrowDown")
    keys.type("```")
    keys.type("code")
    keys.press("ArrowDown")
    keys.type("below")
    assert kept(page) == "### Above\n\n$$x$$\n\n- item\n\n```\ncode\n```\n\nbelow"
    # A text that ends with a formula is opened, by key, with the caret in
    # its words: on the formula, what is typed next would take its place.
    afresh()
    keys.type("text")
    keys.press("Enter")
    keys.type("$$x$$")
    assert kept(page) == "text\n\n$$x$$"
    reopen(page)
    keys.type("!")
    assert page.evaluate(WRITTEN) == "<p>text!</p>$$x$$"
    # The keyboard taken elsewhere — not to the bar, not to the symbol
    # panel — finishes the text.
    page.evaluate("document.querySelector('#sink').focus()")
    page.wait_for_function("!document.querySelector('.region.editing')")
    keys.press("Control+a")
    assert page.evaluate(COPIED)["text"] == "text!\n\n$$x$$"
    # The page's own keys work from a formula's LaTeX as well: Ctrl+S saves,
    # and what is being written into the formula is saved with the rest.
    keys.press("Enter")
    keys.press("ArrowDown")                                     # onto the formula
    keys.press("Enter")                                         # into its LaTeX
    keys.type("^2")
    save_as(page, "written")
    page.wait_for_function("document.title.startsWith('written')", timeout=20_000)
    assert nbformat.read(str(folder / "written.ipynb"), as_version=4).cells[1].source == "text!\n\n$$x^2$$"
    # Opened at the very top of the window, a text is brought down far
    # enough for the bar above it to be in view.
    page.evaluate("() => { const desk = document.querySelector('#desk');"
                  " desk.scrollTop += document.querySelector('.region').getBoundingClientRect().top - desk.getBoundingClientRect().top; }")
    page.locator(".region.text").click(position={"x": 30, "y": 8})
    assert page.evaluate("document.querySelector('.formatbar').getBoundingClientRect().top"
                         " >= document.querySelector('#desk').getBoundingClientRect().top")
    keys.press("Escape")

    # Ctrl+Enter finishes a text as Enter finishes a math region: the cursor goes below it.
    afresh()
    keys.type("done")
    keys.press("Control+Enter")
    assert page.query_selector(".region.editing") is None and page.evaluate("document.activeElement.id") == "sink"
    below = page.evaluate("[parseFloat(document.querySelector('#cross').style.top),"
                          " parseFloat(document.querySelector('.region').style.top) + document.querySelector('.region').offsetHeight]")
    assert below[0] > below[1] - 1 and page.evaluate("!document.querySelector('#cross').hidden")
    page.locator(".region.text").click(modifiers=["Shift"])

    # The markdown is one click away: the bar's Markdown goes on with the text as markdown.
    afresh()
    keys.type("A **bold** word")
    bar("tool=markdown").click()
    assert page.evaluate(EDITOR) == "A **bold** word" and page.evaluate("document.activeElement.tagName") == "TEXTAREA"
    keys.type(" and ~~more~~")
    assert kept(page) == "A **bold** word and ~~more~~"
    keys.press("Enter")
    assert page.evaluate(WRITTEN) == "<p>A <strong>bold</strong> word and <del>more</del></p>"   # opened again: as it looks
    keys.press("Escape")

    # View > Write text as Markdown makes that the way text is written — on
    # this computer, from now on; it is no change to the sheet.
    keys.press("Control+s")
    page.wait_for_function("document.querySelector('#doc-dirty').hidden", timeout=20_000)
    assert nbformat.read(str(folder / "written.ipynb"), as_version=4).cells[1].source == "A **bold** word and ~~more~~"
    page.click("#view-menu .menu-button")
    page.click("#view-menu [data-cmd='toggle-markdown']")
    assert page.evaluate("document.querySelector('#doc-dirty').hidden")
    page.reload()
    page.wait_for_selector(".region.text strong")
    page.wait_for_function("document.querySelector('#view-menu [data-cmd=\"toggle-markdown\"]').getAttribute('aria-checked') === 'true'")
    page.locator(".region.text").click()
    assert page.evaluate(EDITOR) == "A **bold** word and ~~more~~" and page.query_selector(".region .formatbar") is None
    keys.press("Escape")
    page.click("#view-menu .menu-button")
    page.click("#view-menu [data-cmd='toggle-markdown']")
    page.locator(".region.text").click()
    assert page.evaluate(WRITTEN) == "<p>A <strong>bold</strong> word and <del>more</del></p>"


def test_formulas_and_tables_in_a_text_written_as_it_looks(page_and_folder):
    page, _ = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    keys.press("F2")
    page.wait_for_function("document.querySelectorAll('.lib-tabs button').length > 10")
    page.wait_for_timeout(200)
    paper = page.locator("#content").bounding_box()            # (where the page is with the panel open)
    field = "document.activeElement.className + ': ' + document.activeElement.value"

    # The formula editor puts a formula into the text being written: it is
    # drawn there, and the writing goes on after it.
    page.mouse.click(paper["x"] + 40, paper["y"] + 24)
    keys.type('"')
    keys.type("Ohm: ")
    page.click(".lib-tabs button[data-tab='Formula']")
    page.wait_for_selector(".tool-pane math-field", timeout=30_000)
    page.click(".tool-pane math-field")
    assert page.evaluate(WENT_TO) == "writing, in MATH-FIELD"   # (the panel may have the keyboard: the text stays open)
    keys.type("U=R*I")
    keys.press("Enter")
    keys.type(", so")
    assert page.evaluate(WRITTEN) == "<p>Ohm: $U=R\\cdot I$, so</p>"
    # On a line of its own (Shift+Enter): the writing goes on below it.
    page.click(".tool-pane math-field")
    keys.type("I=U/R")
    keys.press("Shift+Enter")
    page.wait_for_timeout(200)                                  # (MathLive must not take the keyboard back: see above)
    assert page.evaluate(IN_TEXT)
    keys.type("below")
    assert page.evaluate(WRITTEN) == "<p>Ohm: $U=R\\cdot I$, so</p>$$I=\\frac{U}{R}$$<p>below</p>"
    assert kept(page) == "Ohm: $U=R\\cdot I$, so\n\n$$I=\\frac{U}{R}$$\n\nbelow"
    assert page.evaluate("document.querySelectorAll('.region.text .katex-error').length") == 0

    # A click on a formula selects it; the formula editor takes it up, and
    # Enter puts the changed one in its place.
    page.mouse.click(*middle(page, ".region.text .md > p .katex"))             # opens the text, the formula selected
    assert page.evaluate("document.querySelector('.region span.formula').classList.contains('ProseMirror-selectednode')")
    page.click(".tool-pane math-field")
    page.wait_for_function("document.querySelector('.tool-pane math-field').value === 'U=R\\\\cdot I'")
    keys.press("End")
    keys.type("+1")
    keys.press("Enter")
    keys.type("!")
    assert page.evaluate(WRITTEN) == "<p>Ohm: $U=R\\cdot I+1$!, so</p>$$I=\\frac{U}{R}$$<p>below</p>"
    # A double click opens its LaTeX in the text; a key of the panel goes
    # into it, dollars typed into it are dropped, and Enter puts it back.
    page.mouse.dblclick(*middle(page, ".region span.formula"))
    assert page.evaluate(WENT_TO) == "writing, in tex-source" and page.evaluate(field) == "tex-source: U=R\\cdot I+1"
    page.click(".lib-tabs button[data-tab='Greek']")
    page.click(".lib-body button:text-is('α')")
    keys.type("$")
    assert page.evaluate(field) == "tex-source: U=R\\cdot I+1α$"
    keys.press("Enter")
    assert page.evaluate(IN_TEXT)
    assert page.evaluate(WRITTEN) == "<p>Ohm: $U=R\\cdot I+1α$!, so</p>$$I=\\frac{U}{R}$$<p>below</p>"
    # A click elsewhere in the text puts what was written into a formula
    # back as well; a click elsewhere on the page finishes the text.
    page.mouse.dblclick(*middle(page, ".region div.formula"))
    keys.type("^2")
    page.mouse.click(*middle(page, ".region .ProseMirror > p:last-child"))
    assert page.evaluate(WRITTEN) == "<p>Ohm: $U=R\\cdot I+1α$!, so</p>$$I=\\frac{U}{R}^2$$<p>below</p>"
    assert page.query_selector(".region.editing") is not None
    page.mouse.click(paper["x"] + 300, paper["y"] + 500)
    assert page.query_selector(".region.editing") is None
    page.locator(".region.text").click(modifiers=["Shift"])
    assert page.evaluate(COPIED)["text"] == "Ohm: $U=R\\cdot I+1α$!, so\n\n$$I=\\frac{U}{R}^2$$\n\nbelow"
    # Opened by key, the caret is at the end of the text; an arrow key goes
    # onto a formula on its own line, and Enter opens it.
    reopen(page)
    keys.press("ArrowUp")
    frame(page)
    keys.press("Enter")
    assert page.evaluate(field) == "tex-source: I=\\frac{U}{R}^2"
    keys.type("+7")
    keys.press("Escape")                                        # not this time: it stays as it was
    assert page.evaluate(IN_TEXT)
    keys.press("Escape")
    # Put back as the other kind, a formula on its own line becomes one in a
    # line — of a paragraph of its own: nothing around it is lost …
    page.mouse.click(*middle(page, ".region.text .md > .katex-display .katex"))
    page.click(".lib-tabs button[data-tab='Formula']")
    page.click(".tool-pane math-field")
    page.wait_for_function("document.querySelector('.tool-pane math-field').value !== ''")
    page.click(".tool-pane .btn:text-is('Replace, in the line')")
    assert page.evaluate(WRITTEN) == "<p>Ohm: $U=R\\cdot I+1α$!, so</p><p>$I=\\frac{U}{R}^2$</p><p>below</p>"
    # … and one written while another is selected goes beside that one.
    page.click(".tool-pane math-field")
    keys.type("x")
    page.mouse.click(*middle(page, ".region .ProseMirror > p:nth-child(2) span.formula"))
    page.click(".tool-pane math-field")
    keys.press("Enter")
    assert page.evaluate(WRITTEN) == "<p>Ohm: $U=R\\cdot I+1α$!, so</p><p>$I=\\frac{U}{R}^2$$x$</p><p>below</p>"
    # In a line, put back on a line of its own.
    page.mouse.click(*middle(page, ".region .ProseMirror > p:nth-child(1) span.formula"))
    page.click(".tool-pane math-field")
    page.wait_for_function("document.querySelector('.tool-pane math-field').value !== ''")
    page.click(".tool-pane .btn:text-is('Replace, on its own line')")
    # (The formula editor writes a letter it was given as the name it has.)
    several = "Ohm: !, so\n\n$$U=R\\cdot I+1\\alpha$$\n\n$I=\\frac{U}{R}^2$$x$\n\nbelow"
    assert kept(page) == several
    # What cannot be written as markdown the way it is shown — a formula
    # that ends in a backslash, say — is not saved as something else: the
    # text goes on as the markdown it would be, and the page says why.
    reopen(page)
    page.mouse.dblclick(*middle(page, ".region .ProseMirror > p span.formula"))
    keys.press("End")
    keys.type("\\")
    keys.press("Enter")
    keys.press("Escape")
    page.wait_for_function(f"{TOAST}.includes('could not be written as markdown the way it is shown')")
    assert page.evaluate(EDITOR) == several.replace("^2$$x$", "^2\\$$x$") and page.evaluate("document.activeElement.tagName") == "TEXTAREA"
    keys.press("Control+a")
    keys.type("mended")
    assert kept(page) == "mended"

    # The table builder: a table to write over.  Tab goes from cell to
    # cell, and after the last one adds a row.
    page.mouse.click(paper["x"] + 40, paper["y"] + 240)
    page.click(".lib-tabs button[data-tab='Table']")
    page.fill(".tool-pane input[aria-label='Columns']", "2")
    page.fill(".tool-pane input[aria-label='Rows']", "1")
    page.select_option(".tool-pane select", "right")
    page.click(".tool-pane .btn.primary")
    for cell in ("Part", "Value", "R1", "10 k", "C"):
        keys.type(cell)
        keys.press("Tab")
    keys.press("Shift+Tab")                                     # back a cell (what is in it is selected)
    keys.press("End")
    frame(page)
    keys.type("1")
    keys.press("Tab")
    keys.type("100 n")
    # The up and down keys go from row to row, to the end of the cell's text.
    keys.press("ArrowUp")
    keys.type("Ω")
    keys.press("ArrowDown")
    keys.type("F")
    assert page.evaluate("[...document.querySelectorAll('.region .ProseMirror tr')].map((r) => [...r.children].map((c) =>"
                         " [c.tagName, c.className, c.textContent]))") == [
        [["TH", "al-right", "Part"], ["TH", "al-right", "Value"]], [["TD", "al-right", "R1"], ["TD", "al-right", "10 kΩ"]],
        [["TD", "al-right", "C1"], ["TD", "al-right", "100 nF"]]]
    table = "| Part | Value |\n| --: | --: |\n| R1 | 10 kΩ |\n| C1 | 100 nF |"
    # A text that begins or ends with a table has room made beside it: the
    # arrow key that would leave the table makes a paragraph there.  Room
    # that is not written in is not kept.
    keys.press("ArrowDown")
    assert page.evaluate(WRITTEN).startswith("<table>") and page.evaluate(WRITTEN).endswith("</table><p></p>")
    assert kept(page) == table
    assert page.evaluate("[...document.querySelectorAll('.region.text td')].map(c => [c.textContent, c.className])") \
        == [["R1", "al-right"], ["10 kΩ", "al-right"], ["C1", "al-right"], ["100 nF", "al-right"]]
    reopen(page)                                                # opened by key: the caret is in the last cell
    keys.press("ArrowDown")
    keys.type("Below")
    keys.press("Home")                                          # (from a paragraph beside the table the key makes no room:
    frame(page)                                                 #  it goes into the table)
    keys.press("ArrowUp")
    frame(page)
    assert page.evaluate(WRITTEN).startswith("<table>")
    keys.press("Control+Home")
    keys.press("ArrowUp")
    keys.type("Above")
    keys.press("ArrowUp")                                       # (a paragraph is there already)
    # Between paragraphs the keys go into the table, and out of it again
    # from its first row and its last.
    cell = ("(() => { const node = getSelection().anchorNode;"
            " return (node.nodeType === 3 ? node.parentElement : node).closest('th, td')?.textContent ?? null; })()")
    keys.press("ArrowDown")
    frame(page)
    assert page.evaluate(cell) in ("Part", "Value")
    keys.press("ArrowUp")
    keys.type("!")
    keys.press("Control+End")
    keys.press("ArrowUp")
    frame(page)
    assert page.evaluate(cell) in ("C1", "100 nF")
    keys.press("ArrowDown")
    keys.type("?")
    assert kept(page) == "Above!\n\n" + table + "\n\n?Below"

    # A text that grows while it is written pushes what is below it down at once.
    tops = "[...document.querySelectorAll('.region')].map((r) => parseFloat(r.style.top))"
    before = page.evaluate(tops)
    page.locator(".region.text").first.click()
    for _ in range(12):
        keys.press("Enter")
    grown = page.evaluate(tops)
    assert page.query_selector(".region.editing") is not None and grown[0] == before[0] and grown[1] > before[1] + 100
    keys.press("Control+z")                                     # (the lines are taken back: they were one step of typing)
    keys.press("Escape")
    keys.press("Escape")

    # With no text being written, a formula starts a text region at the cursor.
    page.mouse.click(paper["x"] + 40, paper["y"] + 520)
    page.click(".lib-tabs button[data-tab='Formula']")
    page.click(".tool-pane math-field")
    keys.type("a+b")
    keys.press("Enter")
    page.wait_for_selector(".region .ProseMirror .formula")
    assert page.evaluate(WRITTEN) == "<p>$a+b$</p>"
    keys.type(" is new")
    assert kept(page) == "$a+b$ is new"
    assert page.evaluate("document.querySelectorAll('.region.has-error').length") == 0


WORD = ('<html><body><!--StartFragment--><h2 style="color:red">Results</h2>'
        '<p class=MsoNormal>Plain <b>bold</b> <i>slanted</i> <span style="font-weight:700">heavy</span> '
        '<span style="font-style:italic">leaning</span> <u>under</u> <s>struck</s> H<sub>2</sub>O x<sup>2</sup> '
        '<a href="https://en.wikipedia.org/wiki/Ohm_(unit)">ohm</a> <a href="javascript:alert(1)">bad</a> '
        '<span style="color:#f00;font-size:30pt">big red</span><script>window.hacked = 1</script>'
        '<img src="file:///C:/Temp/clip_image001.png" onerror="window.hacked = 2"><img src="data:image/png;base64,%s" alt="pic"></p>'
        # What other programs write for the same things: a style where a tag
        # would do, a tag (bold) that its own style takes back.
        '<p><b style="font-weight:normal">not bold</b> <span style="text-decoration:underline">lined</span> '
        '<span style="text-decoration: line-through">through</span> <span style="vertical-align:sub">low</span> '
        '<span style="vertical-align:super">high</span> <a href="https://example.org/"><u>link</u></a> '
        '<img src="data:image/tiff;base64,AAAA" alt="tiff"><span data-tex="x$^2$">f</span> <span data-tex="  ">gone</span> '
        '<i style="font-style:normal">upright</i> <strike>old</strike> <ins>new</ins> <code>mono</code> <img src="https://example.org/web (1).png" alt="web"></p>'
        '<blockquote><p>quoted<br>twice</p></blockquote><hr><pre>a = 1\n  b = 2</pre><h4>Small</h4>'
        '<ul><li>one</li><li>two<ul><li>inner</li></ul></li></ul><ol><li>counted</li></ol>'
        '<table><tr><td>a</td><td style="text-align:right">1</td><td align="center">c</td><td class="al-left">d</td></tr>'
        '<tr><td colspan="2">wide</td></tr></table>'
        '<iframe src="https://example.org"></iframe><!--EndFragment--></body></html>') % BITMAP

# What a word processor puts on the clipboard, as far as the editor goes by
# it: every cell of a table a paragraph (or two); a list as paragraphs that
# begin with their bullet or number, and say in their style that they do.
LISTED = ('<p class=MsoListParagraph style="text-indent:-18.0pt;mso-list:l%d level%d lfo1"><![if !supportLists]>'
          '<span style="font-family:Symbol"><span style="mso-list:Ignore">%s<span style="font:7.0pt &quot;Times New Roman&quot;">'
          '&nbsp;&nbsp;&nbsp; </span></span></span><![endif]>%s<o:p></o:p></p>\n')
OFFICE = ('<table class=MsoTableGrid>\n <tr>\n  <td><p class=MsoNormal><span>Part</span></p></td>\n  <td><p class=MsoNormal><b>Value</b></p></td>\n </tr>\n'
          ' <tr>\n  <td>\n   <p class=MsoNormal>R1</p>\n   <p class=MsoNormal>second line</p>\n  </td>\n'
          '  <td><p class=MsoNormal>10 k<o:p></o:p></p></td>\n </tr>\n</table>\n'
          + "".join(LISTED % item for item in ((0, 1, "·", "One"), (0, 2, "o", "Within"), (0, 1, "·", "Two")))
          + '<p class=MsoNormal>Between</p>\n' + "".join(LISTED % item for item in ((1, 1, "1.", "First"), (1, 1, "2.", "Second")))
          + '<ul><li><p>an item</p><p>in two paragraphs</p></li></ul>')


def test_text_copied_and_pasted_keeps_the_formatting_markdown_can_say(page_and_folder):
    page, _ = page_and_folder
    page.set_viewport_size({"width": 1280, "height": 1500})    # room for what is pasted, and for more below it
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    paper = page.locator("#content").bounding_box()

    def below():
        """Put the cursor on empty paper under everything that is on the page."""
        bottom = page.evaluate("Math.max(...[...document.querySelectorAll('.region')].map((r) => r.getBoundingClientRect().bottom))")
        page.mouse.click(paper["x"] + 40, bottom + 40)

    # Formatted text from another program — a word processor, a web page —
    # keeps the formatting markdown can say and loses the rest; a picture
    # that comes with it as data is attached to the region; nothing of it
    # is run, and a link that could do harm is no link.
    # (A picture on the web is not fetched from the web here.)
    page.route("https://example.org/**", lambda route: route.fulfill(body=png(30, 20), content_type="image/png"))
    page.mouse.click(paper["x"] + 40, paper["y"] + 24)
    keys.type('"')
    assert page.evaluate(PASTE, [[], [["text/html", WORD], ["text/plain", "Results Plain bold"]]]) is True
    page.wait_for_function("document.querySelector('.region .ProseMirror img[alt=pic]')?.naturalWidth === 120")
    assert page.evaluate(WRITTEN) == (
        '<h2>Results</h2><p>Plain <strong>bold</strong> <em>slanted</em> <strong>heavy</strong> <em>leaning</em> <u>under</u>'
        ' <del>struck</del> H<sub>2</sub>O x<sup>2</sup> <a href="https://en.wikipedia.org/wiki/Ohm_(unit%29" target="_blank"'
        ' rel="noopener noreferrer">ohm</a> bad big red[pic]</p>'
        '<p>not bold <u>lined</u> <del>through</del> <sub>low</sub> <sup>high</sup> <a href="https://example.org/" target="_blank"'
        ' rel="noopener noreferrer">link</a> $x^2$ gone upright <del>old</del> <u>new</u> <code>mono</code> [web]</p>'
        '<blockquote><p>quoted<br>twice</p></blockquote><hr contenteditable="false"><pre><code>a = 1\n  b = 2</code></pre><h4>Small</h4>'
        '<ul><li><p>one</p></li><li><p>two</p><ul><li><p>inner</p></li></ul></li></ul><ol><li><p>counted</p></li></ol>'
        '<table><tbody><tr><th>a</th><th class="al-right">1</th><th class="al-center">c</th><th class="al-left">d</th></tr>'
        '<tr><td>wide</td><td class="al-right"></td><td class="al-center"></td><td class="al-left"></td></tr></tbody></table>')
    assert page.evaluate("window.hacked") is None
    pasted = kept(page)
    assert re.fullmatch(
        r"## Results\n\nPlain \*\*bold\*\* \*slanted\* \*\*heavy\*\* \*leaning\* <u>under</u> ~~struck~~ H<sub>2</sub>O x<sup>2</sup> "
        r"\[ohm\]\(https://en\.wikipedia\.org/wiki/Ohm_\(unit%29\) bad big red!\[pic\]\(attachment:[0-9a-f]{12}\.png\)\n\n"
        r"not bold <u>lined</u> ~~through~~ <sub>low</sub> <sup>high</sup> \[link\]\(https://example\.org/\) \$x\^2\$ gone "
        r"upright ~~old~~ <u>new</u> `mono` !\[web\]\(https://example\.org/web%20\(1%29\.png\)\n\n"
        r"> quoted\\\n> twice\n\n---\n\n```\na = 1\n  b = 2\n```\n\n#### Small\n\n"
        r"- one\n- two\n  - inner\n\n1\. counted\n\n\| a \| 1 \| c \| d \|\n\| --- \| --: \| :-: \| :-- \|\n\| wide \|  \|  \|  \|", pasted), pasted
    assert pictures(page, 2)[0]["shown"] == [120, 60]

    # A word processor's table stays a table (a paragraph in a cell is a line
    # of the cell) and its list is a list.
    below()
    keys.type('"')
    assert page.evaluate(PASTE, [[], [["text/html", OFFICE], ["text/plain", "Part Value"]]]) is True
    assert kept(page) == ("| Part | **Value** |\n| --- | --- |\n| R1<br>second line | 10 k |\n\n- One\n  - Within\n- Two\n\nBetween\n\n"
                          "1. First\n2. Second\n\n- an item<br>in two paragraphs")
    keys.press("Delete")                                        # (the region is selected: it has served)

    # Copied out of a text, it goes as formatted text for programs that take
    # that (a picture with what it shows), and as its markdown for the others.
    page.locator(".region.text").first.click(modifiers=["Shift"])
    reopen(page)
    keys.press("Control+a")
    copied = page.evaluate(COPIED)
    assert copied["text"] == pasted
    assert "<h2" in copied["html"] and "<strong>bold</strong>" in copied["html"] and "<sub>2</sub>" in copied["html"]
    assert 'src="data:image/png;base64,' + BITMAP in copied["html"] and "attachment:" not in copied["html"]
    # A piece from inside a paragraph is that piece and no more.
    keys.press("Control+Home")
    keys.press("ArrowDown")
    for _ in range(10):
        keys.press("Shift+ArrowRight")
    frame(page)
    assert page.evaluate(COPIED)["text"] == "Plain **bold**"
    keys.press("Escape")
    # A region copied on the page goes as formatted text as well — the same
    # text, whichever way it was copied.
    region = page.evaluate(COPIED)
    assert region["text"] == pasted and "application/x-edsl-regions" in region["types"]
    assert region["html"] == copied["html"].replace(' data-pm-slice="0 0 []"', "")
    assert 'data-tex="x^2">$x^2$</span>' in region["html"] and "katex" not in region["html"]

    # Formatted text pasted into another region arrives formatted — formulas and pictures with it.
    below()
    keys.type('"')
    keys.type("From $E=mc^2$ on:")
    keys.press("Enter")
    keys.type("$$a^2$$")
    keys.press("Control+a")
    formula = page.evaluate(COPIED)
    assert formula["text"] == "From $E=mc^2$ on:\n\n$$a^2$$"
    assert ">$E=mc^2$</span>" in formula["html"] and ">$$a^2$$</div>" in formula["html"]      # (what another program shows of them)
    keys.press("Control+End")
    assert page.evaluate(PASTE, [[], [["text/html", formula["html"]], ["text/plain", "not this"]]]) is True
    assert page.locator(".region .ProseMirror .formula").count() == 4
    assert kept(page) == "From $E=mc^2$ on:\n\n$$a^2$$\n\nFrom $E=mc^2$ on:\n\n$$a^2$$"
    keys.press("Enter")

    # So a region copied on the page can be pasted into a text being written.
    keys.press("Control+a")
    keys.press("Delete")
    assert page.evaluate(PASTE, [[], [["text/html", region["html"]], ["text/plain", region["text"]], ["application/x-edsl-regions", "[]"]]]) is True
    page.wait_for_function("document.querySelector('.region.editing img[alt=pic]')?.naturalWidth === 120")
    assert page.locator(".region.editing .formula").count() == 1 and page.locator(".region.editing table").count() == 1
    assert kept(page).startswith("## Results\n\nPlain **bold**")
    keys.press("Enter")

    # Plain text is read as the markdown it may be, each of its lines a
    # line; pasted with Shift held as well, it is put in just as it is.
    keys.press("Control+a")
    keys.press("Delete")
    plain = "first line\nsecond *line*\n\n- an item\n- $x^2$ and <u>tags</u>, a_b_c and $5"
    assert page.evaluate(PASTE, [[], [["text/plain", plain]]]) is True
    assert page.evaluate(WRITTEN) == ("<p>first line<br>second <em>line</em></p><ul><li><p>an item</p></li>"
                                      "<li><p>$x^2$ and <u>tags</u>, a_b_c and $5</p></li></ul>")
    assert kept(page) == "first line\\\nsecond *line*\n\n- an item\n- $x^2$ and <u>tags</u>, a_b_c and $5"
    keys.press("Enter")
    keys.press("Control+a")
    keys.press("Delete")
    keys.down("Shift")
    assert page.evaluate(PASTE, [[], [["text/plain", "as *it* is: $5$\nline two"], ["text/html", "<b>not this</b>"]]]) is True
    keys.up("Shift")
    assert page.evaluate(WRITTEN) == "<p>as *it* is: $5$</p><p>line two</p>"
    assert kept(page) == "as \\*it\\* is: \\$5\\$\n\nline two"
    # A picture alone is the sheet's to place: into the text, at the caret
    # (and the keyboard never leaves the text for it).
    keys.press("Enter")
    assert page.evaluate("([paste, file]) => [eval(paste)([[file], []]), %s]" % IN_TEXT,
                         [PASTE, ["image.png", "image/png", BITMAP]]) == [True, True]
    page.wait_for_selector(".region .ProseMirror img[alt=picture]")
    assert page.evaluate(WRITTEN) == "<p>as *it* is: $5$</p><p>line two[picture]</p>"
    # So is a picture file let go of over the text, whatever comes with it
    # (a web page sends the picture's address along): the text is finished,
    # and the picture lands on the page — once.
    assert page.evaluate("""([file, make]) => {
        const data = new DataTransfer();
        data.items.add(eval(make)(file));
        data.setData('text/html', '<img src="https://example.org/remote.png" alt="remote">');
        const text = document.querySelector('.region .ProseMirror'), box = text.getBoundingClientRect();
        return ['dragover', 'drop'].map((kind) => {
            const event = new DragEvent(kind, {bubbles: true, cancelable: true, clientX: box.left + 20, clientY: box.top + 10, dataTransfer: data});
            text.dispatchEvent(event);
            return event.defaultPrevented;
        });
    }""", [["dropped.png", "image/png", BITMAP], FILE]) == [True, True]
    dropped = pictures(page, 4)
    assert [p["picture"] for p in dropped] == [False, False, True] and page.query_selector(".region.editing") is None
    keys.press("Control+a")
    assert "remote" not in page.evaluate(COPIED)["text"]

    # A math region goes to other programs as its text.
    below()
    keys.type("k := 1")
    keys.press("Enter")
    settled(page)
    keys.press("Control+a")
    assert "<pre>k := 1</pre>" in page.evaluate(COPIED)["html"]
    assert page.evaluate("document.querySelectorAll('.region.has-error').length") == 0


ODD = ("Setext heading?\n===\n\n__bold__ and _slanted_ text,   with   blanks\nand a second line\n\n"
       "* star item\n* another\n\n+ plus item\n\n| a | b |\n|:-|-:|\n| 1 | 2 |\n\nLast paragraph.")
BEGINS = "\n\n  Begins after empty lines."
# The middle of a letter of the text shown: the `into`-th of the first place `word` stands.
LETTER = """([word, into]) => {
    const walker = document.createTreeWalker(document.querySelector('#content'), NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const at = node.data.indexOf(word);
        if (at < 0) continue;
        const range = document.createRange();
        range.setStart(node, at + into);
        range.setEnd(node, at + into + 1);
        const box = range.getBoundingClientRect();
        return [box.left + 1, box.top + box.height / 2];
    }
}"""


def test_a_text_someone_else_wrote_stays_as_it_was_written_where_it_is_not_changed(page_and_folder):
    # A notebook's text may be spelled in ways this editor would not spell
    # it.  What is not changed is written back as it stood, letter for letter.
    page, folder = page_and_folder
    nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_markdown_cell(ODD), nbformat.v4.new_markdown_cell(BEGINS)]),
                   str(folder / "odd.ipynb"))
    page.goto(page.url.split("?")[0] + "?open=odd.ipynb")
    page.wait_for_selector(".region.text table")
    keys = page.keyboard
    click = lambda word, into=2: page.mouse.click(*page.evaluate(LETTER, [word, into]))        # noqa: E731

    # Opened and closed, nothing has changed — not the empty lines a text
    # begins with, either.
    click("Last")
    assert page.query_selector(".region .formatbar") is not None
    assert kept(page) == ODD
    click("Begins")
    assert kept(page) == BEGINS and page.evaluate("document.querySelector('#doc-dirty').hidden")
    # The caret goes where the text was clicked; only that paragraph is written anew.
    click("Last", 2)
    keys.type("#")
    keys.press("End")
    keys.type(" More.")
    changed = ODD.replace("Last paragraph.", "La#st paragraph. More.")
    assert kept(page) == changed and page.evaluate("!document.querySelector('#doc-dirty').hidden")
    # Undo on the sheet takes the edit back in one step; redo brings it again.
    keys.press("Control+z")
    keys.press("Control+a")
    assert page.evaluate(COPIED)["text"] == ODD + "\n\n" + BEGINS
    keys.press("Control+y")
    keys.press("Control+a")
    assert page.evaluate(COPIED)["text"] == changed + "\n\n" + BEGINS
    keys.press("Escape")
    # While a text is written Ctrl+Z is the text's own; what is changed and
    # changed back is as it stood.
    click("star", 4)
    keys.type("XY")
    keys.press("Control+z")
    assert kept(page) == changed
    click("Begins")
    keys.press("End")
    keys.type("!")
    keys.press("Control+z")
    keys.press("Control+y")                                     # and Ctrl+Y brings back what Ctrl+Z took
    assert kept(page) == "Begins after empty lines.!"
    # Touched, a block is spelled this editor's way — that block alone.
    click("star", 4)
    keys.type("s")
    changed = changed.replace("* star item\n* another", "- stars item\n- another")
    assert kept(page) == changed
    click("slanted", 3)
    keys.type("#")
    changed = changed.replace("__bold__ and _slanted_ text,   with   blanks\nand a second line",
                              "**bold** and *sla#nted* text,   with   blanks and a second line")
    assert kept(page) == changed
    # Zoomed, the caret still goes where the text was clicked.
    keys.press("Escape")
    for _ in range(3):
        keys.press("Control+=")
    assert page.evaluate("document.querySelector('#paper').style.zoom") == "1.3"
    click("another", 3)
    keys.type("#")
    assert kept(page) == changed.replace("- another", "- ano#ther")
    # The bar of a text that stands far to the right stays on the paper
    # (it reaches to the left of the text instead), enlarged as it is here.
    keys.press("Escape")
    box = page.locator("#content").bounding_box()
    bottom = page.evaluate("Math.max(...[...document.querySelectorAll('.region')].map((r) => r.getBoundingClientRect().bottom))")
    page.mouse.click(box["x"] + 0.85 * box["width"], bottom + 60)
    keys.type('"')
    assert page.evaluate("""() => {
        const bar = document.querySelector('.formatbar').getBoundingClientRect();
        const paper = document.querySelector('#paper').getBoundingClientRect(), desk = document.querySelector('#desk');
        return [bar.width > 300, bar.left >= paper.left, bar.right <= paper.right, desk.scrollWidth <= desk.clientWidth];
    }""") == [True, True, True, True]
    keys.press("Escape")
    assert page.evaluate("document.querySelectorAll('.region.has-error').length") == 0


# What Word puts on the clipboard for prose — the shape of a real one (Word
# 15, read off a clipboard with every letter replaced): a whole document, its
# head full of styles and of settings in a comment; one paragraph; and the
# paragraph that was not selected to its end as bare spans, a bullet made
# by hand before it.
PROSE = ('<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word">\n\n<head>\n'
         '<meta http-equiv=Content-Type content="text/html; charset=utf-8">\n<meta name=Generator content="Microsoft Word 15">\n'
         '<!--[if gte mso 9]><xml>\n <w:WordDocument>\n  <w:View>Normal</w:View>\n  <w:Zoom>0</w:Zoom>\n </w:WordDocument>\n</xml><![endif]-->\n'
         '<style>\n<!--\n p.MsoNormal {margin:0cm; font-size:11.0pt; font-family:"Calibri",sans-serif;}\n-->\n</style>\n</head>\n\n'
         "<body lang=EN-GB style='tab-interval:36.0pt;word-wrap:break-word'>\n<!--StartFragment-->\n\n"
         "<p class=MsoNormal style='margin-top:0cm;margin-left:48.0pt;text-indent:-24.0pt;line-height:normal'><span style='font-size:16.0pt;\n"
         "font-family:\"Helvetica\",sans-serif;color:black;mso-font-kerning:0pt'>The gain is set by two resistors.\n"
         "It (really) is \u2018that\u2019 simple, if x = 5 and the load is light. <o:p></o:p></span></p>\n\n"
         "<span style='font-size:16.0pt;line-height:107%;font-family:\"Helvetica\",sans-serif;color:black'>-</span><span style='font-size:9.0pt;line-height:\n"
         "107%;font-family:\"TimesNewRomanPSMT\",serif;color:black'>&nbsp;&nbsp;&nbsp;\n</span><span style='font-size:16.0pt;line-height:107%;"
         "font-family:\"Helvetica\",sans-serif;color:black'>Each stage\nadds noise </span><!--EndFragment-->\n</body>\n\n</html>\n")
PROSE_KEPT = ("The gain is set by two resistors. It (really) is \u2018that\u2019 simple, if x = 5 and the load is light.\n\n"
              "\\-\xa0\xa0\xa0 Each stage adds noise")
# A web page's, with the styles a browser writes into what it copies; and code, as editors of programs write it.
WEB = ('<meta charset="utf-8"><b style="font-weight:normal;" id="docs-internal-guid-1"><p dir="ltr" style="line-height:1.38;"><span style="font-size:11pt;'
       'font-family:Arial,sans-serif;font-weight:700;white-space:pre-wrap;">Bold</span><span style="font-size:11pt;white-space:pre-wrap;"> words </span>'
       '<img src="data:image/png;base64,%s" alt="pic"></p></b>') % BITMAP
CODE = ('<meta charset=\'utf-8\'><div style="color: #cccccc;background-color: #1f1f1f;font-family: Consolas, \'Courier New\', monospace;'
        'font-size: 14px;line-height: 19px;white-space: pre;"><div><span style="color: #9cdcfe;">x</span><span style="color: #cccccc;"> := </span>'
        '<span style="color: #b5cea8;">2</span><span style="color: #cccccc;"> V</span></div></div>')


def test_formatted_text_pasted_on_the_page_is_a_text_region_and_code_is_taken_as_typed(page_and_folder):
    page, _ = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    paper = page.locator("#content").bounding_box()
    kinds = "[...document.querySelectorAll('.region')].map((r) => r.className.replace('region ', ''))"
    pasted = lambda html, words: page.evaluate(PASTE, [[], [["text/html", html], ["text/plain", words]]])        # noqa: E731

    # Prose from a word processor, pasted with the cursor on the page, is
    # text — a text region of its own — whatever its words would be as a
    # calculation.  It is left selected, and the cursor waits below it.
    page.mouse.click(paper["x"] + 40, paper["y"] + 40)
    assert pasted(PROSE, "The gain is set by two resistors. It (really) is 'that' simple, if x = 5 and the load is light.\r\n- Each stage adds noise") is True
    assert page.evaluate(kinds) == ["text selected"] and page.evaluate("document.activeElement.id") == "sink"
    assert page.evaluate(COPIED)["text"] == PROSE_KEPT
    assert page.evaluate("[...document.querySelectorAll('.region.text .md > *')].map((block) => [block.tagName, block.textContent])") == [
        ["P", "The gain is set by two resistors. It (really) is \u2018that\u2019 simple, if x = 5 and the load is light."],
        ["P", "-\xa0\xa0\xa0 Each stage adds noise"]]
    box = page.evaluate(BOXES)[0]
    assert (box["left"], box["top"], round(box["width"])) == (40, 32, 640)
    cross = page.evaluate("[parseFloat(document.querySelector('#cross').style.left), parseFloat(document.querySelector('#cross').style.top)]")
    assert cross[0] == 40 and cross[1] >= box["top"] + box["height"] and page.evaluate("!document.querySelector('#doc-dirty').hidden")
    keys.press("Control+z")                                     # one step to take back, and to redo
    assert page.evaluate(kinds) == []
    keys.press("Control+y")
    assert page.evaluate(kinds) == ["text"]

    # So with what a web page gives — a picture that comes with it as data is attached to the region.
    page.mouse.click(paper["x"] + 40, paper["y"] + 300)
    assert pasted(WEB, "Bold words") is True
    assert pictures(page, 1)[0]["shown"] == [120, 60]
    assert re.fullmatch(r"\*\*Bold\*\* words !\[pic\]\(attachment:[0-9a-f]{12}\.png\)", page.evaluate(COPIED)["text"])
    assert page.evaluate(kinds) == ["text", "text selected"]
    # A word processor's table and its list come out as they do in a text that is being written.
    assert pasted(OFFICE, "Part Value") is True
    assert page.evaluate(COPIED)["text"] == ("| Part | **Value** |\n| --- | --- |\n| R1<br>second line | 10 k |\n\n- One\n  - Within\n- Two\n\n"
                                             "Between\n\n1. First\n2. Second\n\n- an item<br>in two paragraphs")
    keys.press("Delete")
    assert page.evaluate(kinds) == ["text", "text"]

    # Code is formatted, too, where it was copied in an editor of programs
    # — in a <pre>, or with its line ends kept by its style: it is taken
    # as it is typed, like plain text (which a browser hands to the sheet
    # by itself: the page does not take the paste).
    page.mouse.click(paper["x"] + 40, paper["y"] + 520)
    for html in (CODE, "<pre>x := 2 V\ny := 3 V</pre>", "<p><code>x := 2 V</code></p>", '<span style="white-space:pre;">x := 2 V</span>',
                 "<p> </p>", ""):
        assert pasted(html, "x := 2 V") is False
    assert pasted("<p>The words <code>x := 2 V</code> in a sentence.</p>", "The words x := 2 V in a sentence.") is True
    assert page.evaluate(COPIED)["text"] == "The words `x := 2 V` in a sentence."
    keys.press("Delete")
    # Pasted with Shift held as well, everything is taken as it is typed.
    page.evaluate(KEY, ["V", "KeyV", 86, {"ctrlKey": True, "shiftKey": True}])
    assert pasted(PROSE, "The gain") is False
    assert pasted(PROSE, "The gain") is True                    # (the next one — from the browser's menu, no key at all — is not held to that)
    keys.press("Delete")
    page.evaluate(KEY, ["v", "KeyV", 86, {"ctrlKey": True}])
    assert pasted(PROSE, "The gain") is True
    keys.press("Delete")
    # What is pasted while a math region is being written is that region's.
    keys.type("k := 1 + ")
    assert pasted(PROSE, "2") is False and page.evaluate(kinds) == ["text", "text", "math editing"]
    keys.type("2")
    keys.press("Enter")
    settled(page)
    assert page.evaluate(kinds) == ["text", "text", "math"] and "3" in shown(page)[2]
    assert page.evaluate("document.querySelectorAll('.region.has-error').length") == 0


# ---- header and footer -------------------------------------------------------

# The headers and footers on the sheet, from its top down: which, where, how
# high, and what the lines of their three cells say (a logo as "[logo]").
FRAMES = """() => [...document.querySelectorAll('#content > .pageframe')].map((band) => ({
    part: band.className.replace('pageframe ', ''), top: parseFloat(band.style.top), height: band.getBoundingClientRect().height,
    says: [...band.querySelectorAll('.cell')].map((cell) => [...cell.querySelectorAll('.words')].map((line) =>
        line.textContent + (line.querySelector('img') ? '[logo]' : '')).join(' / ')),
})).sort((a, b) => a.top - b.top)"""
TOPS = "[...document.querySelectorAll('.region')].map((r) => parseFloat(r.style.top))"
CROSS = "parseFloat(document.querySelector('#cross').style.top)"
DIRTY = "!document.querySelector('#doc-dirty').hidden"
LOGO = ('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40" viewBox="0 0 120 40">'
        '<rect width="120" height="40" rx="6" fill="#1f5fbf"/></svg>')


def frame_dialog(page):
    """File > Header and footer…"""
    page.click("#file-menu .menu-button")
    page.click("#file-menu [data-cmd='page-frame']")
    page.wait_for_selector(".dialog.frame")


def frame_ok(page):
    page.click(".dialog.frame .foot .btn.primary")
    page.wait_for_selector(".dialog.frame", state="detached")


def cells(page):
    """What the six cells of the dialog hold, and the template it names."""
    return page.evaluate("[document.querySelector('.dialog.frame select').value,"
                         " ...[...document.querySelectorAll('.dialog.frame textarea')].map((area) => area.value)]")


def saved_settings(folder, name):
    return json.loads((folder / name).read_text(encoding="utf-8"))["metadata"]["edsl_worksheet"]["settings"]


def test_a_header_and_a_footer_on_every_page_take_their_room_from_the_page(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    paper = page.locator("#content").bounding_box()

    def click(y, x=40, **how):
        """Click the paper at height y of the sheet's page (of what is in view)."""
        page.evaluate("(y) => { document.querySelector('#desk').scrollTop = Math.max(0, y - 400); }", y)
        box = page.locator("#content").bounding_box()
        page.mouse.click(box["x"] + x, box["y"] + y, **how)

    # Four regions: one at the top, three near the end of the first page.
    for y, source in ((8, "aa := 1"), (888, "bb := 2"), (928, "cc := 3"), (968, "dd := 4")):
        click(y)
        keys.type(source)
        keys.press("Enter")
    settled(page)
    assert page.evaluate(TOPS) == [0, 880, 920, 960] and page.evaluate(FRAMES) == []
    assert page.evaluate("document.querySelectorAll('.pagebreak').length") == 1
    click(408, x=300)
    assert page.evaluate(CROSS) == 400

    # A template gives every page a header and a footer.  A line of text is
    # a step of the grid (16 px), and a step more is for the rule.
    frame_dialog(page)
    assert cells(page) == ["", "", "", "", "", "", ""]
    page.select_option(".dialog.frame select[aria-label='Template']", "File and date")
    assert cells(page) == ["File and date", "{file}", "", "{saved}", "", "Page {page} of {pages}", ""]
    frame_ok(page)
    assert page.evaluate(DIRTY) and page.evaluate("document.activeElement.id") == "sink"
    assert page.evaluate(CROSS) == 432                          # (the cursor is where it was on the sheet)
    # They take their room from the page: 945 px of the sheet are a page's
    # now.  A region stays where it is on the sheet; one that the page's
    # new end would cut goes to the next page, and what it meets there
    # makes room.  On the page, the frames stand between the pages' parts.
    assert page.evaluate(FRAMES) == [
        {"part": "header", "top": 0, "height": 32, "says": ["Untitled.ipynb", "", ""]},
        {"part": "footer", "top": 977, "height": 32, "says": ["", "Page 1 of 2", ""]},
        {"part": "header", "top": 1009, "height": 32, "says": ["Untitled.ipynb", "", ""]},
        {"part": "footer", "top": 1986, "height": 32, "says": ["", "Page 2 of 2", ""]}]
    # The left cell begins at the left edge, the right one ends at the right, the centre is the page's; a line is 16 px.
    words = """(part) => [...document.querySelector('#content > .pageframe.' + part).querySelectorAll('.words')].map((line) => {
        const range = document.createRange();
        range.selectNodeContents(line);
        const edge = document.querySelector('#content').getBoundingClientRect().left, box = range.getBoundingClientRect();
        return [Math.round(box.left - edge), Math.round(box.right - edge), line.getBoundingClientRect().height]; })"""
    left, centre, right = page.evaluate(words, "header"), page.evaluate(words, "footer")[1], None
    assert left[0][0] == 0 and left[0][2] == 16 and abs((centre[0] + centre[1]) / 2 - 340) <= 1 and centre[2] == 16
    onto_paper = lambda y: y + (y // 945) * 64 + 32                                                    # noqa: E731
    assert page.evaluate(TOPS) == [onto_paper(y) for y in (0, 880, 945, 984)] == [32, 912, 1041, 1080]
    assert page.evaluate("[...document.querySelectorAll('.region')].map((r) => r.innerText.replace(/\\s+/g, ''))") == [
        "aa:=1", "bb:=2", "cc:=3", "dd:=4"]

    # A click on a header or a footer puts the cursor at the nearest place
    # of that page; the arrows take it from page to page past them.
    click(1020)                                                 # the header of the second page
    assert page.evaluate(CROSS) == onto_paper(952) == 1048      # (the first line of the grid on that page)
    click(990)                                                  # the footer of the first
    assert page.evaluate(CROSS) == onto_paper(936) == 968
    keys.press("ArrowDown")
    assert page.evaluate(CROSS) == onto_paper(960) == 1056
    keys.press("ArrowUp")
    assert page.evaluate(CROSS) == 968
    # A page with nothing on it yet is shown for going on: its frame says what it will say.
    click(1900)
    ahead = page.evaluate(FRAMES)[4:]
    assert [(band["part"], band["top"], band["says"][1] or band["says"][0]) for band in ahead] == [
        ("header ahead", 2018, "Untitled.ipynb"), ("footer ahead", 2995, "Page 3 of 3")]
    assert page.evaluate(FRAMES)[1]["says"][1] == "Page 1 of 2"
    assert page.evaluate("getComputedStyle(document.querySelector('.pageframe.ahead')).opacity") == "0.4"
    click(40, x=300)
    assert len(page.evaluate(FRAMES)) == 4
    # A region on a third page: every footer counts three now — and two again when it is gone.
    click(1900)                                                 # (far down: a third page is shown)
    click(2100)
    keys.type("ee := 5")
    keys.press("Enter")
    settled(page)
    assert [band["says"][1] for band in page.evaluate(FRAMES) if band["part"] == "footer"] == ["Page 1 of 3", "Page 2 of 3", "Page 3 of 3"]
    keys.press("Control+z")
    settled(page)
    click(40, x=300)
    assert [band["says"][1] for band in page.evaluate(FRAMES) if band["part"] == "footer"] == ["Page 1 of 2", "Page 2 of 2"]
    # (A double click on the paper itself opens nothing.)
    box = page.locator("#content").bounding_box()
    page.mouse.dblclick(box["x"] + 300, box["y"] + 200)
    page.wait_for_timeout(200)
    assert page.evaluate("document.querySelector('#overlay').hidden")
    # Shown larger, the paper is read as it is shown.
    keys.press("Control+=")
    keys.press("Control+=")
    assert page.evaluate("document.querySelector('#paper').style.zoom") == "1.2"
    page.evaluate("document.querySelector('#desk').scrollTop = 900")
    box = page.locator("#content").bounding_box()
    page.mouse.click(box["x"] + 48, box["y"] + 1020 * 1.2)      # the header of the second page again
    assert page.evaluate(CROSS) == 1048
    keys.press("Control+0")

    # A region is dragged from one page to the next: across the frames, it
    # goes on with the pointer.  A rubber band reaches over them, too.
    box = page.locator("#content").bounding_box()
    page.evaluate("document.querySelector('#desk').scrollTop = 600")
    box = page.locator("#content").bounding_box()
    page.mouse.move(box["x"] + 50, box["y"] + 922)              # on bb, 10 px below its top
    page.mouse.down()
    page.mouse.move(box["x"] + 50, box["y"] + 1000, steps=3)
    page.mouse.move(box["x"] + 50, box["y"] + 1138, steps=4)
    assert page.evaluate("parseFloat(document.querySelector('.region.dragging').style.top)") == 1128
    page.mouse.up()
    settled(page)
    assert page.evaluate(TOPS) == [32, 1128, 1041, 1080]        # (bb is on the second page now, below dd)
    page.mouse.move(box["x"] + 300, box["y"] + 900)
    page.mouse.down()
    page.mouse.move(box["x"] + 200, box["y"] + 1000, steps=3)
    page.mouse.move(box["x"] + 20, box["y"] + 1100, steps=3)
    band = page.evaluate("(() => { const b = document.querySelector('#band'); return [parseFloat(b.style.top), parseFloat(b.style.height), b.hidden]; })()")
    assert band == [900, 200, False]
    page.mouse.up()
    assert page.evaluate("[...document.querySelectorAll('.region.selected')].map((r) => r.innerText.replace(/\\s+/g, ''))") == ["cc:=3", "dd:=4"]
    keys.press("Escape")

    # A double click on a header or a footer opens the dialog; Esc leaves all as it is.
    page.evaluate("document.querySelector('#desk').scrollTop = 0")
    page.locator("#content > .pageframe.header").first.dblclick()
    page.wait_for_selector(".dialog.frame")
    assert cells(page)[0] == "File and date"
    assert page.evaluate("[...document.querySelectorAll('.framepreview .cell.centre')].map((cell) => cell.textContent)") == ["", "Page 1 of 2"]
    page.fill(".dialog.frame textarea[aria-label='Header, centre']", "not wanted")
    keys.press("Escape")
    page.wait_for_selector(".dialog.frame", state="detached")
    assert page.evaluate(FRAMES)[0]["says"] == ["Untitled.ipynb", "", ""]

    # Saved, the sheet says of itself when, and counts the saves that changed it.
    save_as(page, "framed.ipynb")
    page.wait_for_function("!document.querySelector('#doc-name').textContent.includes('Untitled')")
    today = time.strftime("%Y-%m-%d")
    assert page.evaluate(FRAMES)[0]["says"] == ["framed.ipynb", "", today]
    assert page.evaluate(words, "header")[2][1] == 680         # (the date ends at the right edge)
    kept = saved_settings(folder, "framed.ipynb")
    assert kept["frame"] == {"header": ["{file}", "", "{saved}"], "footer": ["", "Page {page} of {pages}", ""],
                             "rule": {"header": True, "footer": True}, "dates": "iso", "logo": None}
    assert kept["about"]["revision"] == 1 and kept["about"]["saved"].startswith(time.strftime("%Y-%m-%dT", time.gmtime()))
    # Places are kept as places on the sheet — not on the paper, where the frames stand between them.
    cells_kept = nbformat.read(str(folder / "framed.ipynb"), as_version=4).cells
    assert [cell.metadata["edsl"]["y"] for cell in cells_kept if "edsl" in cell.metadata] == [0, 945, 984, 1032]

    # Without the frame a page is 1009 px of the sheet again, and its end
    # is elsewhere: dd, which it would cut, begins the second page, and bb
    # makes room for it.
    frame_dialog(page)
    page.select_option(".dialog.frame select[aria-label='Template']", "")
    assert cells(page) == ["", "", "", "", "", "", ""]
    frame_ok(page)
    assert page.evaluate(FRAMES) == [] and page.evaluate(TOPS) == [0, 1048, 945, 1009] and page.evaluate(DIRTY)
    save(page)
    assert "frame" not in saved_settings(folder, "framed.ipynb")
    assert saved_settings(folder, "framed.ipynb")["about"]["revision"] == 2                           # (counted all the same: a sheet has its revision, whatever it shows of it)


def test_what_a_header_says_is_set_in_a_dialog_and_tried_out_there(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    keys.type("x := 5")
    keys.press("Enter")
    settled(page)
    (folder / "logo.svg").write_text(LOGO, encoding="utf-8")
    (folder / "logo.png").write_bytes(png(60, 30))
    (folder / "notes.txt").write_text("no picture", encoding="utf-8")
    shown_now = """() => [...document.querySelectorAll('.framepreview')].map((box) => box.querySelector('.pageframe')
        ? [...box.querySelectorAll('.cell')].map((cell) => [...cell.querySelectorAll('.words')].map((line) =>
            line.textContent + (line.querySelector('img') ? '[logo]' : '')).join(' / '))
        : box.textContent)"""
    area = lambda which: f".dialog.frame textarea[aria-label='{which}']"                                # noqa: E731

    frame_dialog(page)
    assert page.evaluate(shown_now) == ["no header", "no footer"]
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Template"
    # Tab goes round in the dialog, past what is not to be used now: from its last button to its first field.
    keys.press("Shift+Tab")
    assert page.evaluate("document.activeElement.textContent") == "OK"
    keys.press("Tab")
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Template"
    keys.press("Tab")
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Header, left"       # (nothing to keep or delete yet)
    keys.press("Escape")
    frame_dialog(page)
    assert page.evaluate("[...document.querySelectorAll('.dialog.frame .field')].map((b) => b.textContent)") == [
        "{file}", "{name}", "{folder}", "{title}", "{author}", "{revision}", "{saved}", "{saved-time}", "{date}", "{time}",
        "{page}", "{pages}", "{logo}"]
    # What is typed into a cell is shown at once, its fields saying what
    # they say of this sheet.  A field's button writes it at the caret —
    # in the header's middle, until a cell has had the keyboard.
    page.locator(".dialog.frame .field", has_text="{pages}").click()
    assert cells(page) == ["*", "", "{pages}", "", "", "", ""]
    page.fill(area("Header, centre"), "")
    assert cells(page)[0] == ""
    page.fill(area("Header, left"), "**ACME** Engineering\n")
    page.locator(".dialog.frame .field", has_text="{name}").click()
    keys.type(" by ")
    page.locator(".dialog.frame .field", has_text="{author}").click()
    assert page.input_value(area("Header, left")) == "**ACME** Engineering\n{name} by {author}"
    for _ in range(len("{author}")):                            # (in the middle of what is there: at the caret, not at the end)
        keys.press("ArrowLeft")
    page.locator(".dialog.frame .field", has_text="{page}").click()
    keys.type("!")
    assert page.input_value(area("Header, left")) == "**ACME** Engineering\n{name} by {page}!{author}"
    page.fill(area("Header, left"), "**ACME** Engineering\n{name} by {author}")
    assert page.evaluate(shown_now) == [["ACME Engineering / Untitled by ", "", ""], "no footer"]
    assert page.evaluate("document.querySelector('.framepreview strong').textContent") == "ACME"
    assert cells(page)[0] == "*"                                 # no template: as set below
    page.fill(area("Footer, right"), "{page}/{pages}")
    page.fill(".dialog.frame input[aria-label='Title']", "  A <b>title</b> ")
    page.fill(".dialog.frame input[aria-label='Author']", "R. T.")
    page.fill(area("Header, centre"), "{title}")
    assert page.evaluate(shown_now) == [["ACME Engineering / Untitled by R. T.", "A <b>title</b>", ""], ["", "", "1/1"]]
    assert page.evaluate("document.querySelectorAll('.framepreview b').length") == 0
    heights = "[...document.querySelectorAll('.framepreview')].map((box) => box.getBoundingClientRect().height)"
    assert page.evaluate(heights) == [48 + 2, 32 + 2]           # (two lines and the rule; a line and the rule: in a frame of 1 px)
    page.fill(area("Header, right"), "a line that is longer than its third of the page, and longer, and longer, and longer still " * 2)
    assert page.evaluate(heights) == [48 + 2, 32 + 2]
    assert page.evaluate("[...document.querySelectorAll('.framepreview .words')].every((line) => line.getBoundingClientRect().height === 16)")
    page.fill(area("Header, right"), "")
    # The centre is the page's middle whatever stands at the sides, and each of its lines is set in the middle.
    middles = """() => { const box = document.querySelector('.framepreview').getBoundingClientRect();
        return [...document.querySelector('.framepreview').querySelectorAll('.cell.centre .words')].map((line) => {
            const range = document.createRange();
            range.selectNodeContents(line);
            const words = range.getBoundingClientRect();
            return Math.round((words.left + words.right) / 2 - (box.left + box.right) / 2); }); }"""
    page.fill(area("Header, centre"), "{title}\nx")
    assert page.evaluate(middles) == [0, 0]
    page.fill(area("Header, centre"), "{title}")
    # The rule under the header and over the footer; how dates are written.
    rules = "[...document.querySelectorAll('.framepreview .rule')].map((rule) => rule.classList.contains('drawn'))"
    assert page.evaluate(rules) == [True, True]
    page.uncheck(".dialog.frame label.check:has-text('a line over it') input")
    assert page.evaluate(rules) == [True, False]
    page.fill(area("Footer, left"), "{date}")
    assert page.evaluate(shown_now)[1][0] == time.strftime("%Y-%m-%d")
    page.select_option(".dialog.frame select[aria-label='How dates are written']", "dots")
    assert page.evaluate(shown_now)[1][0] == time.strftime("%d.%m.%Y")

    # A logo is a picture, shown where {logo} stands — where no cell names
    # it yet, the header's left one does — at the height it is given.
    assert page.evaluate("[...document.querySelectorAll('.dialog.frame .row')].find((row) => row.textContent.startsWith('Logo')).querySelector('.note').textContent").startswith("none")
    page.set_input_files(".dialog.frame input[type=file]", str(folder / "notes.txt"))
    page.wait_for_function(f"{TOAST}.includes('not a picture')")
    page.evaluate(LIMIT.replace("pictures.js", "pageframe.js").replace("LIMITS", "LOGO"), ["bytes", 50])
    page.set_input_files(".dialog.frame input[type=file]", str(folder / "logo.png"))
    page.wait_for_function(f"{TOAST}.includes('too large for a logo')")
    page.evaluate(LIMIT.replace("pictures.js", "pageframe.js").replace("LIMITS", "LOGO"), ["bytes", 2e6])
    assert page.evaluate("document.querySelectorAll('.framepreview img').length") == 0
    page.set_input_files(".dialog.frame input[type=file]", str(folder / "logo.svg"))
    page.wait_for_selector(".framepreview img")
    assert page.input_value(area("Header, left")) == "{logo} **ACME** Engineering\n{name} by {author}"
    logo = "(() => { const img = document.querySelector('.framepreview img').getBoundingClientRect(); return [Math.round(img.width), Math.round(img.height)]; })()"
    assert page.evaluate(logo) == [113, 38]                      # 10 mm high, and as wide as its shape makes it
    assert page.evaluate("document.querySelector('.framepreview .pageframe').getBoundingClientRect().height") == 80
    assert page.input_value(".dialog.frame input[aria-label='Height of the logo in mm']") == "10"
    page.fill(".dialog.frame input[aria-label='Height of the logo in mm']", "5")
    assert page.evaluate(logo) == [57, 19]
    assert page.evaluate("document.querySelector('.framepreview .pageframe').getBoundingClientRect().height") == 64
    for beyond in ("99", "3", ""):                              # (4 to 40 mm it can be)
        page.fill(".dialog.frame input[aria-label='Height of the logo in mm']", beyond)
        assert page.evaluate(logo) == [57, 19]
    page.set_input_files(".dialog.frame input[type=file]", str(folder / "logo.png"))                   # another picture, as high
    page.wait_for_function("document.querySelector('.framepreview img').src.startsWith('data:image/png')")
    assert page.evaluate(logo) == [38, 19]
    page.fill(area("Header, left"), "{logo}")
    page.fill(area("Header, right"), "{logo}")                   # (it may stand in more than one place)
    assert page.evaluate(shown_now)[0] == ["[logo]", "A <b>title</b>", "[logo]"]
    page.click(".dialog.frame button:has-text('Remove')")
    assert page.evaluate(shown_now)[0] == ["[logo]", "A <b>title</b>", "[logo]"] and page.evaluate("document.querySelectorAll('.framepreview img').length") == 0
    page.set_input_files(".dialog.frame input[type=file]", str(folder / "logo.png"))
    page.wait_for_selector(".framepreview img")
    page.fill(area("Header, right"), "")
    frame_ok(page)
    bands = page.evaluate(FRAMES)
    assert [(band["part"], band["top"], band["height"], band["says"]) for band in bands] == [
        ("header", 0, 64, ["[logo]", "A <b>title</b>", ""]), ("footer", 977, 32, [time.strftime("%d.%m.%Y"), "", "1/1"])]
    assert page.evaluate("document.querySelector('#content > .pageframe img').src").startswith("data:image/png;base64,iVBOR")
    assert page.evaluate("['header', 'footer'].map((part) => getComputedStyle("
                         "document.querySelector(`#content > .pageframe.${part} .rule`), '::after').borderTopWidth)") == ["1px", "0px"]
    assert page.evaluate(TOPS) == [64]

    # The day and the hour of printing are those of the printing, not of when the sheet was last drawn.
    page.evaluate("""() => { const Real = Date;
        window.Date = class extends Real { constructor(...given) { super(...(given.length ? given : [Real.now() + 49 * 3600e3])); } }; }""")
    assert page.evaluate(FRAMES)[1]["says"][0] == time.strftime("%d.%m.%Y")
    page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
    assert page.evaluate(FRAMES)[1]["says"][0] == time.strftime("%d.%m.%Y", time.localtime(time.time() + 49 * 3600))
    page.evaluate("window.dispatchEvent(new Event('afterprint'))")

    # The revision: a number is counted on from with every save that changed
    # the sheet; anything else stays as it was typed.
    frame_dialog(page)
    page.fill(area("Footer, left"), "{folder}")
    page.fill(area("Header, right"), "Rev. {revision}")
    assert page.evaluate(shown_now)[0][2] == "Rev. 0"           # (no revision yet: none counted)
    page.fill(".dialog.frame input[aria-label='Revision']", "41")
    assert page.evaluate(shown_now)[0][2] == "Rev. 41"
    frame_ok(page)
    assert page.evaluate(FRAMES)[0]["says"][2] == "Rev. 41" and page.evaluate(FRAMES)[1]["says"][0] == ""
    save_as(page, "set.ipynb")
    page.wait_for_function("document.querySelector('#doc-dirty').hidden")
    assert page.evaluate(FRAMES)[0]["says"][2] == "Rev. 42" and page.evaluate(FRAMES)[1]["says"][0] == str(folder)
    save(page)                                                  # nothing changed: the same revision
    assert saved_settings(folder, "set.ipynb")["about"]["revision"] == 42
    keys.type("y := 2")
    keys.press("Enter")
    settled(page)
    save(page)
    kept = saved_settings(folder, "set.ipynb")
    assert kept["about"] == {"title": "A <b>title</b>", "author": "R. T.", "revision": 43, "saved": kept["about"]["saved"]}
    assert kept["frame"]["logo"]["data"] == {"image/png": base64.b64encode(png(60, 30)).decode()} and kept["frame"]["logo"]["name"] == "logo.png"
    assert kept["frame"]["dates"] == "dots" and kept["frame"]["rule"] == {"header": True, "footer": False}
    frame_dialog(page)
    assert page.input_value(".dialog.frame input[aria-label='Revision']") == "43"
    page.fill(".dialog.frame input[aria-label='Revision']", " B ")
    frame_ok(page)
    assert page.evaluate(FRAMES)[0]["says"][2] == "Rev. B" and page.evaluate(DIRTY)
    save(page)
    assert saved_settings(folder, "set.ipynb")["about"]["revision"] == "B" and page.evaluate(FRAMES)[0]["says"][2] == "Rev. B"
    # What is emptied is the sheet's no more: no author; a revision that begins again.
    frame_dialog(page)
    page.fill(".dialog.frame input[aria-label='Revision']", "")
    page.fill(".dialog.frame input[aria-label='Author']", "")
    frame_ok(page)
    assert page.evaluate(FRAMES)[0]["says"][2] == "Rev. 0"
    save(page)
    kept = saved_settings(folder, "set.ipynb")
    assert sorted(kept["about"]) == ["revision", "saved", "title"] and kept["about"]["revision"] == 1
    frame_dialog(page)                                          # (and "B" again, for what follows)
    page.fill(".dialog.frame input[aria-label='Revision']", "B")
    frame_ok(page)
    save(page)
    # OK with nothing changed changes nothing: the sheet has nothing new to save.
    frame_dialog(page)
    frame_ok(page)
    assert not page.evaluate(DIRTY)
    # Opened again, the sheet is as it was saved.
    page.reload()
    page.wait_for_selector("#content > .pageframe img")
    bands = page.evaluate(FRAMES)
    assert [(band["part"], band["height"], band["says"]) for band in bands] == [
        ("header", 64, ["[logo]", "A <b>title</b>", "Rev. B"]), ("footer", 32, [str(folder), "", "1/1"])]


def test_a_template_of_ones_own_is_kept_for_every_sheet_and_new_sheets_start_with_one(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    (folder / "logo.svg").write_text(LOGO, encoding="utf-8")
    names = "[...document.querySelectorAll(\".dialog.frame select[aria-label='Template'] option\")].filter((o) => !o.hidden).map((o) => o.textContent)"
    area = lambda which: f".dialog.frame textarea[aria-label='{which}']"                                # noqa: E731

    # What the server has kept is read again before it is offered: a
    # template is a header or a footer, under a name that no ready-made one has.
    kept_file = folder / ".jupyter-data" / "edsl_worksheet_templates.json"
    kept_file.parent.mkdir(exist_ok=True)
    kept_file.write_text(json.dumps({"templates": [
        {"name": "Good", "frame": {"header": ["good", "", ""], "more": "than a frame has"}},
        {"name": "Title block", "frame": {"header": ["not the ready-made one", "", ""]}},
        {"name": "Nothing in it", "frame": {"header": ["", " ", ""], "footer": []}}]}), encoding="utf-8")
    nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_markdown_cell("a sheet that has no header")]), str(folder / "plain.ipynb"))
    page.reload()
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    frame_dialog(page)
    assert page.evaluate(names) == ["None", "Page numbers", "File and date", "Title block", "Good"]
    page.select_option(".dialog.frame select[aria-label='Template']", "Title block")
    assert cells(page)[1] == "{logo}"
    page.select_option(".dialog.frame select[aria-label='Template']", "Good")
    assert cells(page) == ["Good", "good", "", "", "", "", ""]
    page.click(".dialog.frame button:has-text('Delete')")
    page.wait_for_function(f"!{names}.includes('Good')")
    page.select_option(".dialog.frame select[aria-label='Template']", "")
    assert page.evaluate(names) == ["None", "Page numbers", "File and date", "Title block"]
    assert page.is_disabled(".dialog.frame button:has-text('Save as template')") and page.is_disabled(".dialog.frame button:has-text('Delete')")
    page.select_option(".dialog.frame select[aria-label='Template']", "Title block")
    assert cells(page) == ["Title block", "{logo}", "**{title}**\n{file}", "Rev. {revision}\n{saved}", "{author}", "", "Page {page} of {pages}"]
    page.set_input_files(".dialog.frame input[type=file]", str(folder / "logo.svg"))
    page.wait_for_selector(".framepreview img")
    assert cells(page)[0] == "*" and page.evaluate(names)[-1] == "(as set below)"
    # Kept under a name — not that of a ready-made one — it is one of the templates.
    page.click(".dialog.frame button:has-text('Save as template')")
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Name of the template"
    for taken in ("Title block", "None"):
        page.evaluate("document.querySelector('#toast').hidden = true")
        page.fill(".dialog.frame input[aria-label='Name of the template']", taken)
        keys.press("Enter")
        page.wait_for_function(f"{TOAST}.includes('ready-made template')")
    # (A server that was started before templates could be kept does not know the address: that is said.)
    page.route("**/api/templates*", lambda route: route.fulfill(status=404, body="<html>404: Not Found</html>"))
    page.fill(".dialog.frame input[aria-label='Name of the template']", "ACME")
    keys.press("Enter")
    page.wait_for_function(f"{TOAST}.includes('start it again')")
    assert page.evaluate(names) == ["None", "Page numbers", "File and date", "Title block", "(as set below)"]
    page.unroute("**/api/templates*")
    # (And what a server will not keep, it says why.)
    page.route("**/api/templates*", lambda route: route.fulfill(status=400, content_type="application/json", body='{"error": "not this one"}'))
    keys.press("Enter")
    page.wait_for_function(f"{TOAST} === 'not this one'")
    page.unroute("**/api/templates*")
    page.fill(".dialog.frame input[aria-label='Name of the template']", "  ACME ")
    keys.press("Enter")
    page.wait_for_function(f"{names}.includes('ACME')")
    assert page.evaluate(names) == ["None", "Page numbers", "File and date", "Title block", "ACME"] and cells(page)[0] == "ACME"
    assert not page.is_visible(".dialog.frame input[aria-label='Name of the template']")
    assert not page.is_disabled(".dialog.frame button:has-text('Delete')")
    # A template that has no logo leaves the sheet the logo it has.
    page.select_option(".dialog.frame select[aria-label='Template']", "Page numbers")
    assert page.evaluate("document.querySelectorAll('.framepreview img').length") == 0
    page.select_option(".dialog.frame select[aria-label='Template']", "Title block")
    assert page.evaluate("document.querySelectorAll('.framepreview img').length") == 1 and cells(page)[0] == "ACME"
    # New sheets on this computer start with the template chosen for them.
    page.select_option(".dialog.frame select[aria-label='Template of a new sheet']", "ACME")
    page.fill(area("Footer, centre"), "only this sheet's")
    frame_ok(page)
    assert page.evaluate(FRAMES)[0]["says"] == ["[logo]", "Untitled / Untitled.ipynb", "Rev. 0 / "]
    assert page.evaluate(FRAMES)[1]["says"] == ["", "only this sheet's", "Page 1 of 1"]
    stored = {entry["name"]: entry["frame"] for entry in json.loads(kept_file.read_text(encoding="utf-8"))["templates"]}
    assert sorted(stored) == ["ACME", "Nothing in it", "Title block"]       # (what the page did not take is not its to throw away)
    assert stored["ACME"]["footer"] == ["{author}", "", "Page {page} of {pages}"]
    assert stored["ACME"]["logo"]["name"] == "logo.svg" and list(stored["ACME"]["logo"]["data"]) == ["image/svg+xml"]
    assert json.loads((folder / ".jupyter-data" / "edsl_worksheet.json").read_text(encoding="utf-8"))["template"] == "ACME"
    keys.press("Alt+n")
    page.click(".dialog .btn:has-text('Discard')")
    page.wait_for_function("document.querySelectorAll('#content > .pageframe').length === 2 && document.querySelector('#doc-dirty').hidden")
    assert [band["says"] for band in page.evaluate(FRAMES)] == [["[logo]", "Untitled / Untitled.ipynb", "Rev. 0 / "], ["", "", "Page 1 of 1"]]
    assert not page.evaluate(DIRTY)                             # (a new sheet: there is nothing to save yet)
    # A sheet that is there already is as it is: without a header, if it has none.
    page.goto(page.url.split("?")[0] + f"?token={TOKEN}&open=plain.ipynb")
    page.wait_for_selector(".region.text")
    page.wait_for_timeout(500)
    assert page.evaluate(FRAMES) == []
    page.goto(page.url.split("?")[0])
    # So does the sheet a new window begins with.
    page.reload()
    page.wait_for_selector("#content > .pageframe img")
    assert [band["says"] for band in page.evaluate(FRAMES)] == [["[logo]", "Untitled / Untitled.ipynb", "Rev. 0 / "], ["", "", "Page 1 of 1"]]
    assert not page.evaluate(DIRTY)
    frame_dialog(page)
    assert cells(page)[0] == "ACME" and page.input_value(".dialog.frame select[aria-label='Template of a new sheet']") == "ACME"
    # A template is changed by keeping another under its name, and let go of with Delete.
    page.fill(area("Footer, left"), "ACME Ltd.")
    assert cells(page)[0] == "*"
    page.click(".dialog.frame button:has-text('Save as template')")
    page.fill(".dialog.frame input[aria-label='Name of the template']", "ACME")
    page.click(".dialog.frame .row:has(input[aria-label='Name of the template']) button:has-text('Save')")
    page.wait_for_function("document.querySelector('.dialog.frame select').value === 'ACME'")
    page.select_option(".dialog.frame select[aria-label='Template']", "Page numbers")
    page.select_option(".dialog.frame select[aria-label='Template']", "ACME")
    assert cells(page)[4] == "ACME Ltd."
    page.click(".dialog.frame button:has-text('Delete')")
    page.wait_for_function(f"!{names}.includes('ACME')")
    assert cells(page)[0] == "*" and cells(page)[4] == "ACME Ltd."                                      # (what the sheet has stays)
    assert page.evaluate("[...document.querySelector(\".dialog.frame select[aria-label='Template of a new sheet']\").selectedOptions]"
                         ".map((option) => option.textContent)") == ["None"]
    frame_ok(page)
    assert sorted(entry["name"] for entry in json.loads(kept_file.read_text(encoding="utf-8"))["templates"]) == ["Nothing in it", "Title block"]
    assert json.loads((folder / ".jupyter-data" / "edsl_worksheet.json").read_text(encoding="utf-8"))["template"] == ""
    keys.press("Alt+n")
    page.click(".dialog .btn:has-text('Discard')")
    page.wait_for_function("document.querySelectorAll('#content > .pageframe').length === 0")
    # On paper a sheet with a header alone ends with its last region — and an empty one not before its header.
    printed = "parseFloat(document.querySelector('#content').style.getPropertyValue('--printed'))"
    frame_dialog(page)
    page.fill(area("Header, left"), "only a header")
    frame_ok(page)
    assert [band["part"] for band in page.evaluate(FRAMES)] == ["header"] and page.evaluate(printed) == 32
    keys.type("x := 5")
    keys.press("Enter")
    settled(page)
    region = page.evaluate("(() => { const r = document.querySelector('.region'); return parseFloat(r.style.top) + r.getBoundingClientRect().height; })()")
    assert 32 + 20 < region == page.evaluate(printed) < 32 + 40


def test_a_sheet_from_elsewhere_gets_no_more_of_a_frame_than_a_frame_is_and_prints_it_on_every_page(page_and_folder):
    page, folder = page_and_folder
    cells_ = [nbformat.v4.new_code_cell("from utils.Engineer import *")]
    for k in range(3):
        cell = nbformat.v4.new_markdown_cell(f"[body {k + 1}](https://example.org/body/{k + 1})")
        cell.metadata["edsl"] = {"x": 40, "y": k * 929 + 16, "w": 200}
        cells_.append(cell)
    nb = nbformat.v4.new_notebook(cells=cells_)
    nb.metadata["edsl_worksheet"] = {"version": 1, "settings": {
        "frame": {"header": ["<img src=x onerror=\"window.hacked = 1\"> [head](https://example.org/head)", "{title}\n{author}{logo}", 7, "more"],
                  "footer": ["[foot](https://example.org/foot) [bad](javascript:window.hacked=2)", "{page} of {pages} in {folder}", "{revision} {saved}"],
                  "rule": {"header": "yes", "footer": True}, "dates": "never",
                  "logo": {"data": {"text/html": "PHNjcmlwdD53aW5kb3cuaGFja2VkID0gMzwvc2NyaXB0Pg==", "image/png": "<svg onload=window.hacked=4>"}, "height": 500}},
        "about": {"title": "<script>window.hacked = 5</script>", "author": ["not", "a", "name"], "revision": {"a": 1}, "saved": "yesterday"}}}
    (folder / "from" / "them").mkdir(parents=True)
    nbformat.write(nb, str(folder / "from" / "them" / "theirs.ipynb"))
    page.goto(page.url.split("?")[0] + f"?token={TOKEN}&open=from/them/theirs.ipynb")
    page.wait_for_function("document.querySelectorAll('#content > .pageframe').length === 6")
    page.wait_for_function("document.querySelector('#content > .pageframe.footer').textContent.includes('them')")      # (where the folder is has been asked)
    bands = page.evaluate(FRAMES)
    # Markup is text; a link that would run something is none; what is no
    # picture is no logo; a date that is none says nothing.
    assert bands[0] == {"part": "header", "top": 0, "height": 48, "says": [
        '<img src=x onerror="window.hacked = 1"> head', "<script>window.hacked = 5</script> / ", ""]}
    assert bands[1] == {"part": "footer", "top": 977, "height": 32, "says": [
        "foot [bad](javascript:window.hacked=2)", f"1 of 3 in {folder / 'from' / 'them'}", "0 " + time.strftime("%Y-%m-%d")]}   # (as old as its file)
    assert page.evaluate("[document.querySelectorAll('.pageframe img, .pageframe script').length, window.hacked]") == [0, None]
    assert page.evaluate("[...document.querySelectorAll('#content > .pageframe.header a')].map((a) => a.getAttribute('href'))") == ["https://example.org/head"] * 3
    assert page.evaluate("['header', 'footer'].map((part) => document.querySelector(`#content > .pageframe.${part} .rule`).classList.contains('drawn'))") == [False, True]
    assert [band["part"] for band in bands] == ["header", "footer"] * 3
    # A click on a link there is a click on the header: the cursor's, and no page is opened.
    link = page.locator("#content > .pageframe.header[style*='top: 0px'] a").bounding_box()            # (the first page's)
    page.mouse.click(link["x"] + 5, link["y"] + 5)
    page.wait_for_timeout(300)
    assert len(page.context.pages) == 1 and page.evaluate(CROSS) == 48 and page.url.endswith("theirs.ipynb")
    # With the cursor far down, the screen shows a page more, for going on.
    page.evaluate("document.querySelector('#desk').scrollTop = 2600")
    box = page.locator("#content").bounding_box()
    page.mouse.click(box["x"] + 300, box["y"] + 2990)
    assert [band["part"] for band in page.evaluate(FRAMES)] == ["header", "footer"] * 3 + ["header ahead", "footer ahead"]
    # On paper: the three pages with something on them, each with its
    # header and its footer where the screen has them; the page that is
    # shown for going on is not paper.
    assert page.evaluate(TOPS) == [48 + 16, 1009 + 48 + 16, 2 * 1009 + 48 + 16]
    places, pages = linked(page.pdf(prefer_css_page_size=True))
    assert pages == 3
    for name, down in (("head", 57 + 16), ("foot", 57 + 1009 - 16)):                             # (a line down in its part)
        found = sorted((number, at) for address, number, at in places if address == f"https://example.org/{name}")
        assert [number for number, at in found] == [1, 2, 3] and len({at for number, at in found}) == 1, (name, found)
        assert down <= found[0][1] < down + 6, (name, found)
    assert sorted((address, number) for address, number, at in places if "/body/" in address) == [
        (f"https://example.org/body/{k}", k) for k in (1, 2, 3)]
    page.emulate_media(media="print")
    assert page.evaluate("[...document.querySelectorAll('#content > .pageframe')].map((band) => "
                         "[band.classList.contains('ahead'), getComputedStyle(band).display === 'none'])") == [[False, False]] * 6 + [[True, True]] * 2 \
        or page.evaluate("[...document.querySelectorAll('#content > .pageframe')].every((band) => "
                         "band.classList.contains('ahead') === (getComputedStyle(band).display === 'none'))")
    assert page.evaluate("document.querySelector('#content').getBoundingClientRect().height") == 3 * 1009
    page.emulate_media(media="screen")
    # A sheet that says when it was saved is of that day, and of that hour, however new its file is.
    nb.metadata["edsl_worksheet"]["settings"] = {"frame": {"header": ["{saved} {saved-time}", "", ""]},
                                                 "about": {"saved": "2021-03-04T10:05:00", "revision": 12}}
    nbformat.write(nb, str(folder / "dated.ipynb"))
    page.goto(page.url.split("?")[0] + f"?token={TOKEN}&open=dated.ipynb")
    page.wait_for_selector("#content > .pageframe")
    assert page.evaluate(FRAMES)[0]["says"] == ["2021-03-04 10:05", "", ""]


# ---- values from another sheet ------------------------------------------------

# What the regions hold that were given values (without the blanks KaTeX sets), and the marks on them.
GIVEN = "[...document.querySelectorAll('.region.given')].map((r) => r.innerText.replace(/[\\s\\u200b]+/g, ''))"
MARKS = "[...document.querySelectorAll('.region.given .mark')].map((m) => [m.textContent, m.classList.contains('lost')])"
# The dialog: each row's name, whether it is chosen (null: it cannot be), and what it says.
ROWS = """[...document.querySelectorAll('.dialog.given .value')].map((row) => [row.dataset.name,
          row.querySelector('input') ? row.querySelector('input').checked : null, row.innerText.replace(/[\\s\\u200b]+/g, '')])"""
NOTES = "[...document.querySelectorAll('.dialog.given p.note')].map((note) => note.textContent)"
TAKE = ".dialog.given .foot .btn.primary"
SELECTED = "[...document.querySelectorAll('.region.selected')].map((r) => r.classList.contains('given'))"
NOT_KEPT = "Saved. Its values are not kept with this revision: the sheet is not calculated."


def typed(page, *lines):
    for line in lines:
        page.keyboard.type(line)
        page.keyboard.press("Enter")


def new_sheet(page):
    page.keyboard.press("Alt+n")
    page.wait_for_function("document.querySelector('#doc-name').textContent === 'Untitled' && !document.querySelector('.region')")
    settled(page)


def opened(page, name):
    page.goto(page.url.split("?")[0] + f"?token={TOKEN}&open={name}")
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    settled(page)


def named(page, name):
    """The sheet has been saved under this name."""
    page.wait_for_function("(name) => document.querySelector('#doc-name').textContent === name && document.querySelector('#doc-dirty').hidden", arg=name)


def sheet_says(folder, name):
    """What a sheet's file says of the sheet: its settings, and what its variables were."""
    return json.loads((folder / name).read_text(encoding="utf-8"))["metadata"]["edsl_worksheet"]


def handed_on(folder, name):
    """The values a sheet's file keeps, briefly: its revision, and each name with how it is shown (or why it is not there)."""
    record = sheet_says(folder, name).get("values")
    return record and [record["revision"], {entry["name"]: entry.get("shown", {}).get("text/plain", entry.get("why")) for entry in record["values"]}]


def given_cells(folder, name):
    return [cell for cell in nbformat.read(str(folder / name), as_version=4).cells if "given" in cell.metadata.get("edsl", {})]


def values_dialog(page, sheet):
    """Edit > Values from another sheet…, and the sheet chosen."""
    page.click("#edit-menu .menu-button")
    page.click("#edit-menu [data-cmd='take-values']")
    page.wait_for_selector(".dialog .files")
    page.click(f".files div:has-text('{sheet}')")
    page.keyboard.press("Enter")
    page.wait_for_selector(".dialog.given")


def take(page, *names):
    """Choose these names in the dialog, and take them."""
    for name in names:
        page.check(f".dialog.given .value[data-name='{name}'] input")
    page.click(TAKE)
    page.wait_for_selector(".dialog.given", state="detached")
    settled(page)


def test_a_sheet_takes_values_of_another_sheet_as_that_sheet_had_them(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    # The sheet that hands them on.  Saved, it keeps what its variables are
    # with the revision that save made — as far as they are values.
    typed(page, "U := 5.5 V", "R_tol := 10. k\u03a9 \u00b1 5%", "volts := [1.0, 2.0, 3.0] V", "f := x \u2192 x\u00b2 + 1", "n := 3")
    settled(page)
    save_as(page, "limits.ipynb")
    named(page, "limits")
    about = saved_settings(folder, "limits.ipynb")["about"]
    record = sheet_says(folder, "limits.ipynb")["values"]
    assert about["revision"] == 1 and {key: record[key] for key in ("revision", "saved")} == about
    try:
        from importlib.metadata import version
        toolkit = version("engineering-dsl")
    except Exception:
        toolkit = ""                                            # (where the toolkit is not installed as a package)
    assert record["toolkit"] == toolkit
    assert handed_on(folder, "limits.ipynb") == [1, {"U": "5.5 V", "R_tol": "(9.50 k\u03a9 \u2025 10.5 k\u03a9)", "volts": "[1.0 V, 2.0 V, 3.0 V]",
                                                    "f": "a function", "n": "3"}]
    assert [sorted(entry) for entry in record["values"]] == [["form", "name", "shown", "tex"]] * 3 + [["name", "tex", "why"], ["form", "name", "shown", "tex"]]

    # Another sheet takes some of them: the sheet is chosen, then what of it.
    new_sheet(page)
    page.mouse.click(420, 200)                                  # (the cursor is put somewhere on the page)
    page.evaluate("() => { window.passes = 0; const send = WebSocket.prototype.send;"
                  " WebSocket.prototype.send = function (text) { if (JSON.parse(text).op === 'calc') window.passes += 1; return send.call(this, text); }; }")
    keys.type("k := 2")                                         # (still being written when the values are asked for: it is finished first)
    page.click(".tool[data-cmd='take-values']")
    page.wait_for_selector(".dialog .files")
    assert page.inner_text(".dialog h3") == "Values from which sheet?" and page.locator(".region.editing").count() == 0
    page.click(".files div:has-text('limits.ipynb')")
    keys.press("Enter")
    page.wait_for_selector(".dialog.given")
    assert page.inner_text(".dialog.given h3") == "Values from another sheet"
    assert page.evaluate("document.querySelector('.dialog.given .source').innerText.replace(/\\s+/g, ' ')").startswith("limits rev. 1 \u00b7 saved ")
    assert page.evaluate(NOTES) == [] and page.locator(".dialog.given input[type=text]").count() == 0       # (so few need no finding)
    page.wait_for_function("document.activeElement.getAttribute('aria-label') === 'U'")                     # (the keyboard is at the first of them)
    assert page.evaluate(ROWS) == [["U", False, "U5.5V"], ["R_tol", False, "Rtol(9.50k\u03a9\u202510.5k\u03a9)"], ["volts", False, "volts[1.0V,2.0V,3.0V]"],
                                   ["f", None, "fafunction:nothandedon"], ["n", False, "n3"]]
    # Nothing is taken while nothing is chosen; the button says how much is.
    assert page.inner_text(TAKE) == "Take values" and page.is_disabled(TAKE)
    keys.press("Enter")
    page.wait_for_timeout(100)
    assert page.locator(".dialog.given").count() == 1
    page.check(".dialog.given .value[data-name='U'] input")
    assert page.inner_text(TAKE) == "Take 1 value" and page.is_enabled(TAKE)
    page.click(".dialog.given .foot .btn:has-text('All')")
    assert page.inner_text(TAKE) == "Take 4 values" and [row[1] for row in page.evaluate(ROWS)] == [True, True, True, None, True]
    page.click(".dialog.given .foot .btn:has-text('None')")
    assert page.inner_text(TAKE) == "Take values" and page.is_disabled(TAKE)
    page.click(".dialog.given .value[data-name='volts'] .is")            # (a click anywhere on its row chooses a value)
    assert page.inner_text(TAKE) == "Take 1 value"
    settled(page)
    passes = page.evaluate("window.passes")
    take(page, "U", "R_tol")
    assert page.evaluate("window.passes") == passes + 1         # (the sheet is calculated with them)
    # They stand at the cursor — below what was being written — with where
    # they are from, as definitions are written, in the other sheet's
    # order.  The region is left selected.
    assert page.evaluate(GIVEN) == ["Fromlimits\u00b7rev.1U:=5.5VRtol:=(9.50k\u03a9\u202510.5k\u03a9)volts:=[1.0V,2.0V,3.0V]"]
    assert page.evaluate("document.querySelector('.region.given .whence span').title") == "limits.ipynb"
    tops = page.evaluate(TOPS)
    lefts = page.evaluate("[...document.querySelectorAll('.region')].map((r) => parseFloat(r.style.left))")
    assert tops[1] == tops[0] + 40 > 100 and lefts[0] == lefts[1] > 100
    assert page.evaluate(SELECTED) == [True] and page.evaluate(DIRTY) and page.evaluate(MARKS) == []
    assert page.evaluate("document.querySelectorAll('.region.given .line .katex').length") == 6            # (names and values, typeset)
    # Undo takes it off the page again, and redo puts it back.
    keys.press("Control+z")
    settled(page)
    assert page.evaluate(GIVEN) == [] and len(shown(page)) == 1
    keys.press("Control+y")
    settled(page)
    assert len(page.evaluate(GIVEN)) == 1 and page.evaluate(TOPS) == tops

    # It is math like the rest: what follows calculates with what it holds.
    # (The cursor waits below it.)
    keys.press("Escape")
    typed(page, "U \u00b7 k", "volts[1] / R_tol")
    settled(page)
    assert "= 11. V" in shown(page)[2] and "190. \u03bcA" in shown(page)[3]
    assert page.evaluate("document.querySelectorAll('.region.has-error').length") == 0
    below = page.evaluate("document.querySelector('.region.given').getBoundingClientRect().height") + tops[1]
    assert below + 8 <= page.evaluate(TOPS)[2] <= below + 24 < page.evaluate(TOPS)[3]

    # A click selects it — there is nothing in it to write — and a double
    # click, or Enter, asks what it is to hold; Esc leaves it as it is.
    box = page.locator(".region.given").bounding_box()
    middle_of = (box["x"] + 60, box["y"] + 40)
    page.mouse.click(*middle_of)
    assert page.evaluate(SELECTED) == [True] and page.locator(".dialog").count() == 0 and page.locator(".region.editing").count() == 0
    page.wait_for_timeout(600)
    page.mouse.dblclick(*middle_of)
    page.wait_for_selector(".dialog.given")
    keys.press("Escape")
    page.wait_for_selector(".dialog.given", state="detached")
    page.mouse.click(*middle_of)                                # (a third click, close upon the two, is a click: not half of another two)
    page.wait_for_timeout(150)
    assert page.locator(".dialog").count() == 0 and page.evaluate(SELECTED) == [True]
    page.wait_for_timeout(600)
    page.mouse.dblclick(*middle_of)
    page.wait_for_selector(".dialog.given")
    assert [row[:2] for row in page.evaluate(ROWS)] == [["U", True], ["R_tol", True], ["volts", True], ["f", None], ["n", False]]
    assert page.inner_text(TAKE) == "Take 3 values" and page.evaluate(NOTES) == []
    keys.press("Escape")
    page.wait_for_selector(".dialog.given", state="detached")
    keys.press("Enter")
    page.wait_for_selector(".dialog.given")
    page.click(".dialog.given .foot .btn:has-text('Cancel')")
    page.wait_for_selector(".dialog.given", state="detached")
    assert len(page.evaluate(GIVEN)) == 1 and page.evaluate(SELECTED) == [True]
    # Tab, which goes from region to region to write them, passes it by; it is not split into statements.
    page.locator(".region.math").first.click()
    keys.press("Escape")
    keys.press("Tab")
    assert page.evaluate(EDITOR) == "U \u00b7 k"
    keys.press("Escape")
    page.mouse.click(*middle_of)
    page.click("#arrange-menu .menu-button")
    page.click("#arrange-menu [data-cmd='split']")
    assert len(page.evaluate(GIVEN)) == 1 and page.evaluate("document.querySelectorAll('.region').length") == 4
    # Chosen anew, it holds what is chosen: less, here.  Undo gives it back; so does redo take it again.
    page.wait_for_timeout(600)
    page.mouse.dblclick(*middle_of)
    page.wait_for_selector(".dialog.given")
    page.uncheck(".dialog.given .value[data-name='volts'] input")
    take(page)
    assert page.evaluate(GIVEN) == ["Fromlimits\u00b7rev.1U:=5.5VRtol:=(9.50k\u03a9\u202510.5k\u03a9)"]
    assert page.evaluate("document.querySelectorAll('.region.has-error').length") == 1          # (volts is no longer there to divide)
    keys.press("Control+z")
    settled(page)
    assert page.evaluate(GIVEN)[0].endswith("volts:=[1.0V,2.0V,3.0V]") and page.evaluate("document.querySelectorAll('.region.has-error').length") == 0
    keys.press("Control+y")
    settled(page)
    assert page.evaluate(GIVEN)[0].endswith("10.5k\u03a9)")
    keys.press("Control+z")
    settled(page)

    # Copied and pasted, it is what it was; deleted, it is gone — and back with undo.
    page.wait_for_timeout(600)
    page.mouse.click(*middle_of)
    keys.press("Control+c")
    page.mouse.click(500, 700)
    keys.press("Control+v")
    settled(page)
    assert page.evaluate(GIVEN) == ["Fromlimits\u00b7rev.1U:=5.5VRtol:=(9.50k\u03a9\u202510.5k\u03a9)volts:=[1.0V,2.0V,3.0V]"] * 2
    assert page.evaluate(SELECTED) == [True]
    keys.press("Delete")
    settled(page)
    assert len(page.evaluate(GIVEN)) == 1
    keys.press("Control+z")
    settled(page)
    assert len(page.evaluate(GIVEN)) == 2
    keys.press("Control+z")
    settled(page)
    assert len(page.evaluate(GIVEN)) == 1

    # Saved, the sheet is a notebook any Jupyter runs: the region is a cell
    # of code that holds the values, with what it shows as its output; and
    # the cell says where they are from.
    save_as(page, "uses.ipynb")
    named(page, "uses")
    cell, = given_cells(folder, "uses.ipynb")
    taken = cell.metadata["edsl"]["given"]
    assert {key: taken[key] for key in ("sheet", "name", "revision", "saved")} == {
        "sheet": "limits.ipynb", "name": "limits", "revision": 1, "saved": about["saved"]}
    assert taken["values"] == [entry for entry in record["values"] if entry["name"] in ("U", "R_tol", "volts")]
    lines = cell.source.split("\n")
    assert lines[0].startswith("# Values given by another sheet") and lines[1] == "from utils.worksheet.values import given as _given"
    assert lines[2].startswith("(U, R_tol, volts,) = _given('{") and len(lines) == 3
    assert [(out.output_type, out.get("text") or out.data["text/plain"]) for out in cell.outputs] == [
        ("stream", "From limits \u00b7 rev. 1\n"), ("display_data", "U = 5.5 V"), ("display_data", "R_tol = (9.50 k\u03a9 \u2025 10.5 k\u03a9)"),
        ("display_data", "volts = [1.0 V, 2.0 V, 3.0 V]")]
    assert cell.outputs[2].data["text/latex"].startswith("$\\displaystyle R_{\\mathrm{tol}} = ")
    # What it was given is the sheet's own now, and handed on with the rest: a third sheet can take it from here.
    assert handed_on(folder, "uses.ipynb") == [1, {"k": "2", "U": "5.5 V", "R_tol": "(9.50 k\u03a9 \u2025 10.5 k\u03a9)", "volts": "[1.0 V, 2.0 V, 3.0 V]"}]

    # Opened again, it is what it was.
    opened(page, "uses.ipynb")
    assert page.evaluate(GIVEN) == ["Fromlimits\u00b7rev.1U:=5.5VRtol:=(9.50k\u03a9\u202510.5k\u03a9)volts:=[1.0V,2.0V,3.0V]"]
    assert "= 11. V" in shown(page)[2] and page.evaluate(MARKS) == [] and not page.evaluate(DIRTY)
    new_sheet(page)
    values_dialog(page, "uses.ipynb")
    assert [row[0] for row in page.evaluate(ROWS)] == ["k", "U", "R_tol", "volts"]
    take(page, "U")
    assert page.evaluate(GIVEN) == ["Fromuses\u00b7rev.1U:=5.5V"]

    # A sheet with many variables: a field in its dialog finds them by name.
    many = [{"name": f"v_{k}", "tex": f"v_{{{k}}}", "form": f'["i","{k}"]', "shown": {"text/plain": str(k)}} for k in range(1, 10)]
    many.append({"name": "Other", "form": '["i","0"]', "shown": {"text/plain": "0"}})
    nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("from utils.Engineer import *")])
    nb.metadata["edsl_worksheet"] = {"version": 1, "settings": {"about": {"revision": "B", "saved": "2026-10-11T10:00:00.000Z"}},
                                     "values": {"revision": "B", "saved": "2026-10-11T10:00:00.000Z", "toolkit": "", "values": many}}
    nbformat.write(nb, str(folder / "many.ipynb"))
    keys.press("Escape")
    values_dialog(page, "many.ipynb")
    in_sight = "[...document.querySelectorAll('.dialog.given .value')].filter((row) => row.getClientRects().length).map((row) => row.dataset.name)"
    page.wait_for_function("document.activeElement.getAttribute('aria-label') === 'Find a name'")
    assert len(page.evaluate(in_sight)) == 10
    keys.type("V_1")                                            # (in capitals or not)
    assert page.evaluate(in_sight) == ["v_1"]
    page.click(".dialog.given .foot .btn:has-text('All')")      # (all that are in sight)
    assert page.inner_text(TAKE) == "Take 1 value"
    page.fill(".dialog.given input[type=text]", " oth ")
    assert page.evaluate(in_sight) == ["Other"]
    page.fill(".dialog.given input[type=text]", "")
    assert len(page.evaluate(in_sight)) == 10 and [row[1] for row in page.evaluate(ROWS)] == [True] + [False] * 9
    page.click(".dialog.given .foot .btn:has-text('All')")
    page.fill(".dialog.given input[type=text]", "v_")
    page.click(".dialog.given .foot .btn:has-text('None')")
    assert page.inner_text(TAKE) == "Take 1 value"
    keys.press("Enter")                                         # (on a button, Enter presses that button)
    page.wait_for_timeout(100)
    assert page.locator(".dialog.given").count() == 1 and page.inner_text(TAKE) == "Take 1 value"
    page.focus(".dialog.given input[type=text]")
    keys.press("Enter")                                         # (in the field, too, Enter takes what is chosen)
    page.wait_for_selector(".dialog.given", state="detached")
    settled(page)
    assert page.evaluate(GIVEN)[-1] == "Frommany\u00b7rev.BOther:=0"


# (values from another sheet: what changes)

def rewritten(folder, name, change):
    """Another program changes what a sheet's file says of the sheet."""
    book = json.loads((folder / name).read_text(encoding="utf-8"))
    change(book["metadata"]["edsl_worksheet"])
    (folder / name).write_text(json.dumps(book), encoding="utf-8")


def refocused(page):
    """The user comes back to the window: the page looks at the other sheets again."""
    page.evaluate("window.dispatchEvent(new Event('focus'))")


def test_a_sheet_is_told_when_the_other_sheet_delivers_other_values(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    typed(page, "U := 5.5 V", "n := 3", "spare := 1")
    settled(page)
    save_as(page, "source.ipynb")
    named(page, "source")
    first = sheet_says(folder, "source.ipynb")["values"]
    new_sheet(page)
    values_dialog(page, "source.ipynb")
    take(page, "U", "n")
    keys.press("Escape")
    typed(page, "U \u00b7 n")
    settled(page)
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.1U:=5.5Vn:=3"] and "= 16. V" in shown(page)[1]
    save_as(page, "top.ipynb")
    named(page, "top")
    assert page.evaluate(MARKS) == []
    # (A server that does not answer says nothing about the other sheet: nothing is marked for that.)
    asked_for_values = lambda url: "/api/values" in url                                    # noqa: E731
    page.route(asked_for_values, lambda route: route.abort())
    refocused(page)
    page.wait_for_timeout(300)
    page.unroute(asked_for_values)
    assert page.evaluate(MARKS) == []

    # The other sheet is changed, and saved while the change is still being
    # written: the save waits for the calculation, and keeps the new values
    # with the new revision.
    opened(page, "source.ipynb")
    page.locator(".region.math").first.click()
    keys.press("Control+a")
    keys.type("U := 6.0 V")
    save(page)
    assert saved_settings(folder, "source.ipynb")["about"]["revision"] == 2
    assert handed_on(folder, "source.ipynb") == [2, {"U": "6.0 V", "n": "3", "spare": "1"}]

    # The sheet that took them is as it was — and says that there is more.
    opened(page, "top.ipynb")
    page.wait_for_selector(".region.given .mark")
    assert page.evaluate(MARKS) == [["rev. 2 is there", False]] and page.evaluate(GIVEN) == ["Fromsource\u00b7rev.1rev.2isthereU:=5.5Vn:=3"]
    assert "= 16. V" in shown(page)[1] and not page.evaluate(DIRTY)
    # One click on the mark shows what is different, and what this sheet has of it.
    # (Meanwhile the page asks about the other sheet once more, and the answer is slow in coming.)
    held = []
    asked_briefly = lambda url: "/api/values" in url and "brief=1" in url                  # noqa: E731
    page.route(asked_briefly, lambda route: held.append(route))
    refocused(page)
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    page.uncheck("#auto-calc")
    page.click(".region.given .mark")
    page.wait_for_selector(".dialog.given")
    assert page.evaluate(NOTES) == ["This sheet has them as of rev. 1."]
    assert page.evaluate(ROWS) == [["U", True, "U6.0Vhere:5.5V"], ["n", True, "n3"], ["spare", False, "spare1"]]
    keys.press("Enter")                                         # (Enter takes what is chosen)
    page.wait_for_selector(".dialog.given", state="detached")
    page.wait_for_timeout(200)
    # Until the sheet is calculated the region shows what was taken, as the
    # other sheet's file has it — not what the kernel still holds.
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.2U:=6.0Vn:=3"] and "= 16. V" in shown(page)[1]
    page.check("#auto-calc")
    settled(page)
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.2U:=6.0Vn:=3"] and "= 18. V" in shown(page)[1]
    assert page.evaluate(MARKS) == [] and page.evaluate(DIRTY) and page.evaluate(SELECTED) == [True]
    # The slow answer was about what the region had: it marks nothing now.
    held[0].continue_()
    page.unroute(asked_briefly)
    page.wait_for_timeout(300)
    assert page.evaluate(MARKS) == []
    # Undo gives back what it had — which is behind again; redo takes the new values again.
    keys.press("Control+z")
    settled(page)
    page.wait_for_selector(".region.given .mark")
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.1rev.2isthereU:=5.5Vn:=3"] and "= 16. V" in shown(page)[1]
    keys.press("Control+y")
    settled(page)
    page.wait_for_selector(".region.given .mark", state="detached")
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.2U:=6.0Vn:=3"]
    # (What undo gave back is what the next undo remembers: the region is
    # moved, the move taken back — and it still has what it had.)
    keys.press("Control+z")
    settled(page)
    page.wait_for_selector(".region.given .mark")
    page.wait_for_timeout(600)
    box = page.locator(".region.given").bounding_box()
    page.mouse.click(box["x"] + 40, box["y"] + 30)
    keys.press("ArrowDown")
    assert page.evaluate(TOPS)[0] == 8
    keys.press("Control+z")
    settled(page)
    page.wait_for_timeout(300)
    assert page.evaluate(TOPS)[0] == 0 and page.evaluate(GIVEN) == ["Fromsource\u00b7rev.1rev.2isthereU:=5.5Vn:=3"]
    page.click(".region.given .mark")
    page.wait_for_selector(".dialog.given")
    take(page)
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.2U:=6.0Vn:=3"]
    save(page)
    cell, = given_cells(folder, "top.ipynb")
    assert cell.metadata["edsl"]["given"]["revision"] == 2 and cell.outputs[0].text == "From source \u00b7 rev. 2\n"
    assert cell.outputs[1].data["text/plain"] == "U = 6.0 V"

    # The same values under another revision are taken without anything being calculated anew.
    def counted(meta):
        meta["values"]["revision"] = meta["settings"]["about"]["revision"] = "C"
    rewritten(folder, "source.ipynb", counted)
    refocused(page)
    page.wait_for_selector(".region.given .mark")
    assert page.evaluate(MARKS) == [["rev. C is there", False]]
    page.click(".region.given .mark")
    page.wait_for_selector(".dialog.given")
    assert page.evaluate(NOTES) == ["This sheet has them as of rev. 2."] and [row[2] for row in page.evaluate(ROWS)] == ["U6.0V", "n3", "spare1"]
    page.evaluate("() => { window.passes = 0; const send = WebSocket.prototype.send;"
                  " WebSocket.prototype.send = function (text) { if (JSON.parse(text).op === 'calc') window.passes += 1; return send.call(this, text); }; }")
    take(page)
    page.wait_for_timeout(300)
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.CU:=6.0Vn:=3"] and page.evaluate(MARKS) == [] and page.evaluate(DIRTY)
    assert page.evaluate("window.passes") == 0

    # A value that is no longer handed on, and one that is no longer there, go when the new ones are taken.
    def fewer(meta):
        values = meta["values"]["values"]
        values[1] = {"name": "n", "tex": "n", "why": "a function"}
        del values[0]
        meta["values"]["saved"] = "2030-01-02T03:04:05.000Z"
    rewritten(folder, "source.ipynb", fewer)
    refocused(page)
    page.wait_for_selector(".region.given .mark")
    page.click(".region.given .mark")
    page.wait_for_selector(".dialog.given")
    assert page.evaluate(ROWS) == [["n", None, "nafunction:nothandedonhere:3"], ["spare", False, "spare1"], ["U", None, "Unotthereanymorehere:6.0V"]]
    assert page.is_disabled(TAKE)
    take(page, "spare")
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.Cspare:=1"] and page.evaluate("document.querySelectorAll('.region.has-error').length") == 1

    # A sheet that keeps no values (any more), and one that is not there: the region says so, and keeps what it has.
    rewritten(folder, "source.ipynb", lambda meta: meta.pop("values"))
    refocused(page)
    page.wait_for_selector(".region.given .mark.lost")
    assert page.evaluate(MARKS) == [["keeps no values", True]]
    page.click(".region.given .mark")
    page.wait_for_selector(".dialog.given")
    assert page.evaluate(NOTES)[0].startswith("This sheet keeps no values yet. Open it and save it once it is calculated")
    assert page.evaluate(ROWS) == [] and page.is_disabled(TAKE) and page.evaluate("document.querySelector('.dialog.given .values').hidden")
    keys.press("Escape")
    page.wait_for_selector(".dialog.given", state="detached")
    assert page.evaluate(SELECTED) == [True]                    # (the region that was asked about)
    (folder / "source.ipynb").rename(folder / "moved.ipynb")
    rewritten(folder, "moved.ipynb", lambda meta: meta.update(values=dict(first, revision=7)))
    refocused(page)
    page.wait_for_function(f"JSON.stringify({MARKS}) === JSON.stringify([['not found', true]])")
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.Cnotfoundspare:=1"]
    # Its dialog says what is the matter, and lets another sheet be chosen: the one it is now.
    page.click(".region.given .mark")
    page.wait_for_selector(".dialog.given")
    assert page.evaluate(NOTES) == ["source.ipynb was not found"] and page.evaluate(ROWS) == [] and page.is_disabled(TAKE)
    assert page.evaluate("document.querySelector('.dialog.given .source').innerText.replace(/\\s+/g, ' ')") == "source Other sheet\u2026"
    page.click(".dialog.given .btn:has-text('Other sheet')")
    page.wait_for_selector(".dialog .files")
    page.click(".files div:has-text('moved.ipynb')")
    keys.press("Enter")
    page.wait_for_selector(".dialog.given")
    assert page.evaluate(NOTES) == ["These are its values as of rev. 7: it has been saved since without being calculated.",
                                    "This sheet has them as of rev. C."]
    assert [row[:2] for row in page.evaluate(ROWS)] == [["U", False], ["n", False], ["spare", True]]
    take(page, "U")
    assert page.evaluate(GIVEN) == ["Frommoved\u00b7rev.7U:=5.5Vspare:=1"] and page.evaluate(MARKS) == []
    save(page)
    assert given_cells(folder, "top.ipynb")[0].metadata["edsl"]["given"]["sheet"] == "moved.ipynb"
    # A sheet does not take values from itself.
    page.wait_for_timeout(600)
    box = page.locator(".region.given").bounding_box()
    page.mouse.dblclick(box["x"] + 40, box["y"] + 30)
    page.wait_for_selector(".dialog.given")
    page.click(".dialog.given .btn:has-text('Other sheet')")
    page.wait_for_selector(".dialog .files")
    page.click(".files div:has-text('top.ipynb')")
    keys.press("Enter")
    page.wait_for_selector(".dialog.given")
    assert page.evaluate(NOTES) == ["A sheet takes values from other sheets, not from itself."] and page.is_disabled(TAKE)
    keys.press("Escape")
    page.wait_for_selector(".dialog", state="detached")
    assert page.evaluate(GIVEN) == ["Frommoved\u00b7rev.7U:=5.5Vspare:=1"]


MEDDLE = """() => { const parse = JSON.parse;
    JSON.parse = function (text, ...more) {
        const made = parse.call(this, text, ...more);
        return made && made.op === 'values' && window.meddle ? window.meddle(made) : made; }; }"""


def test_saving_keeps_what_a_sheets_variables_are_with_the_revision_it_makes(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    typed(page, "aa := 2")
    settled(page)
    save_as(page, "kept.ipynb")
    named(page, "kept")
    stamp = lambda name="kept.ipynb": saved_settings(folder, name)["about"]             # noqa: E731
    record = lambda: sheet_says(folder, "kept.ipynb")["values"]                         # noqa: E731
    sources = lambda name="kept.ipynb": [cell.source for cell in nbformat.read(str(folder / name), as_version=4).cells[1:]]     # noqa: E731

    def saved_saying():
        """Ctrl+S: what the sheet says of it, and how long that took."""
        page.evaluate("document.querySelector('#toast').hidden = true")
        began = time.time()
        keys.press("Control+s")
        page.wait_for_function(f"{TOAST}.startsWith('Saved')")
        return page.evaluate(TOAST), time.time() - began

    # Every sheet counts the saves that changed it — it has no need of a header for that.
    first = stamp()
    assert first["revision"] == 1 and handed_on(folder, "kept.ipynb") == [1, {"aa": "2"}] and "frame" not in saved_settings(folder, "kept.ipynb")
    save(page)
    assert stamp() == first and record()["saved"] == first["saved"]
    page.wait_for_timeout(600)
    assert page.evaluate(TOAST) == "Saved"                      # (and nothing comes after it to say that it waits)

    # Ctrl+S while a line is still being written: it is part of what is
    # saved, and so is what it comes to — the save waits for that.
    keys.type("bb := aa + 1")
    said, took = saved_saying()
    assert said == "Saved" and took < 2                         # (and no longer than the calculation takes)
    assert stamp()["revision"] == 2 and handed_on(folder, "kept.ipynb") == [2, {"aa": "2", "bb": "3"}]
    assert record()["saved"] == stamp()["saved"] != first["saved"]
    assert sources() == ["aa := 2", "bb := aa + 1"]

    # A sheet that is not calculated as it stands is saved all the same — at
    # once: nothing is coming that could be waited for — and says that the
    # values of this revision are not with it.  The file keeps the ones of
    # the revision before, under that revision's name.
    page.uncheck("#auto-calc")
    page.locator(".region.math").first.click()
    keys.press("Control+a")
    keys.type("aa := 5")
    keys.press("Enter")
    page.wait_for_selector(".region.stale")
    said, took = saved_saying()
    assert said == NOT_KEPT and took < 1.5
    assert stamp()["revision"] == 3 and handed_on(folder, "kept.ipynb") == [2, {"aa": "2", "bb": "3"}] and not page.evaluate(DIRTY)
    # Calculated and saved again — nothing changed, so the revision is the same — it has them.
    keys.press("F9")
    settled(page)
    third = stamp()
    save(page)
    assert stamp() == third and handed_on(folder, "kept.ipynb") == [3, {"aa": "5", "bb": "6"}] and record()["saved"] == third["saved"]
    page.check("#auto-calc")

    # A file that is written over keeps nothing of the sheet it was: the
    # values of a sheet that is saved in its place uncalculated are not
    # there — and the other sheet's are not either.
    keys.press("Control+Shift+s")
    page.fill(".dialog input[type=text]", "copy")
    keys.press("Enter")
    named(page, "copy")
    assert handed_on(folder, "copy.ipynb") == [3, {"aa": "5", "bb": "6"}] and stamp("copy.ipynb") == third
    opened(page, "kept.ipynb")
    page.uncheck("#auto-calc")
    page.locator(".region.math").first.click()
    keys.press("Control+a")
    keys.type("aa := 6")
    keys.press("Enter")
    page.wait_for_selector(".region.stale")
    page.evaluate("document.querySelector('#toast').hidden = true")
    keys.press("Control+Shift+s")
    page.fill(".dialog input[type=text]", "copy")
    keys.press("Enter")
    page.wait_for_selector(".dialog h3:has-text('Overwrite?')")
    page.click(".dialog .btn.primary")
    page.wait_for_function(f"{TOAST} === {json.dumps(NOT_KEPT)}")
    assert "values" not in sheet_says(folder, "copy.ipynb") and stamp("copy.ipynb")["revision"] == 4 and sources("copy.ipynb")[0] == "aa := 6"
    assert handed_on(folder, "kept.ipynb") == [3, {"aa": "5", "bb": "6"}]

    # A calculation that takes its time is waited for a moment, not for
    # ever: the sheet is saved while it still runs, without its values —
    # and at once when it has been running that long already.
    opened(page, "kept.ipynb")
    keys.type('nap := __import__("time").sleep(7)')
    began = time.time()
    keys.press("Enter")
    page.wait_for_selector("#status[data-state='busy']")
    page.evaluate("document.querySelector('#toast').hidden = true")
    keys.press("Control+s")
    page.wait_for_function(f"{TOAST} === 'Saving once this is calculated…'")
    page.wait_for_function(f"{TOAST} === {json.dumps(NOT_KEPT)}")
    waited = time.time() - began
    assert 3 < waited < 6.5 and page.evaluate("document.querySelector('#status').dataset.state") == "busy"
    assert stamp()["revision"] == 4 and handed_on(folder, "kept.ipynb")[0] == 3
    said, took = saved_saying()
    assert said == NOT_KEPT and took < 1.5 and time.time() - began < 7 and stamp()["revision"] == 4
    settled(page)
    save(page)
    assert handed_on(folder, "kept.ipynb") == [4, {"aa": "5", "bb": "6", "nap": "None"}]
    # A calculation that was stopped leaves the kernel with half a sheet: no values of that.
    page.locator(".region.math").first.click()
    keys.press("Control+a")
    keys.type("aa := 7")
    keys.press("Enter")
    page.wait_for_selector("#status[data-state='busy']")
    page.wait_for_timeout(500)
    keys.press("Escape")
    page.wait_for_function(f"{TOAST}.startsWith('Calculation stopped')")
    said, took = saved_saying()
    assert said == NOT_KEPT and took < 1.5 and stamp()["revision"] == 5 and handed_on(folder, "kept.ipynb")[0] == 4

    # What is written while a save waits is part of the sheet that is
    # saved — and the values, which were asked for before, are not its.
    page.locator(".region.math").nth(2).click()                 # (the one that takes its time: less of it)
    keys.press("Control+a")
    keys.type('nap := __import__("time").sleep(2)')
    keys.press("Enter")
    page.wait_for_selector("#status[data-state='busy']")
    page.evaluate("document.querySelector('#toast').hidden = true")
    keys.press("Control+s")
    keys.type("late := 1")
    page.wait_for_function(f"{TOAST}.startsWith('Saved')")
    assert page.evaluate(TOAST) == NOT_KEPT and sources() == ["aa := 7", "bb := aa + 1", 'nap := __import__("time").sleep(2)', "late := 1"]
    assert stamp()["revision"] == 6 and handed_on(folder, "kept.ipynb")[0] == 4
    settled(page)
    page.locator(".region.math").nth(2).click()
    keys.press("Escape")
    keys.press("Delete")
    settled(page)
    save(page)
    assert handed_on(folder, "kept.ipynb") == [7, {"aa": "7", "bb": "8", "late": "1"}]

    # A sheet that has no revision yet gets its first one with any save —
    # also one that is not calculated here, which has no values to keep.
    place = lambda source, x, y: nbformat.v4.new_code_cell(source, metadata={"edsl": {"x": x, "y": y}})          # noqa: E731
    words = nbformat.v4.new_markdown_cell("one line", metadata={"edsl": {"x": 0, "y": 0, "w": 200}})
    nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("from utils.Engineer import *"), words, place("q := 1", 0, 40),
                                         place("q := 2", 304, 48), place("q", 0, 240)])
    nb.metadata["edsl_worksheet"] = {"version": 1, "settings": {}}
    nbformat.write(nb, str(folder / "order.ipynb"))
    opened(page, "order.ipynb")
    assert page.is_visible("#banner") and saved_saying()[0] == "Saved"
    assert stamp("order.ipynb")["revision"] == 1 and "values" not in sheet_says(folder, "order.ipynb")
    page.click("#banner .btn.primary")
    settled(page)
    assert "= 2" in shown(page)[3]
    save(page)
    assert stamp("order.ipynb")["revision"] == 1 and handed_on(folder, "order.ipynb") == [1, {"q": "2"}]
    # Regions that were pushed into another order are calculated anew, in
    # that order: the values the kernel had were of the order before, and
    # are not kept with a file that has the new one.
    page.locator(".region.text").click()
    given_the_keyboard(page)
    keys.press("Control+End")
    for word in ("two", "three"):
        keys.press("Enter")
        keys.type(word)
    keys.press("Escape")
    page.wait_for_timeout(200)
    tops = page.evaluate(TOPS)
    assert tops[1] > tops[2] == 48 and "= 2" in shown(page)[3]
    said, took = saved_saying()
    assert said == NOT_KEPT and handed_on(folder, "order.ipynb") == [1, {"q": "2"}] and stamp("order.ipynb")["revision"] == 2
    assert sources("order.ipynb")[1:] == ["q := 2", "q := 1", "q"]
    settled(page)
    assert "= 1" in shown(page)[3]
    save(page)
    assert handed_on(folder, "order.ipynb") == [2, {"q": "1"}]

    # The kernel says which calculation its values are of, and is taken at
    # its word: values of another pass than the one this page knows the
    # sheet by are not kept; nor are values of a sheet that has been given
    # something to calculate since they were asked for.
    page.evaluate(MEDDLE)
    keys.press("Escape")
    typed(page, "rr := 4")
    settled(page)

    def meddled(how):
        page.evaluate(f"() => {{ window.meddle = {how}; }}")
        return saved_saying()

    assert meddled("(said) => ({ ...said, pass: said.pass + 1 })")[0] == NOT_KEPT
    assert stamp("order.ipynb")["revision"] == 3 and handed_on(folder, "order.ipynb")[0] == 2
    assert meddled("(said) => { document.querySelector('.tool[data-cmd=\"calc\"]').click(); return said; }")[0] == NOT_KEPT
    settled(page)
    # A kernel that reports a fault instead is not waited for.
    said, took = meddled("(said) => ({ op: 'fault', seq: said.seq, traceback: 'meddled with' })")
    assert said == NOT_KEPT and took < 3 and handed_on(folder, "order.ipynb")[0] == 2
    assert meddled("null")[0] == "Saved" and handed_on(folder, "order.ipynb") == [3, {"q": "1", "rr": "4"}]


# (values from another sheet: files from elsewhere, folders, and servers of before)

def test_what_a_file_says_a_region_was_given_gives_way_to_what_the_kernel_makes_of_it(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    code = lambda values: page.evaluate("async (values) => (await import('/static/js/given.js')).sourceOf({ values })", values)     # noqa: E731
    place = lambda source, y, **more: nbformat.v4.new_code_cell(source, metadata={"edsl": dict({"x": 0, "y": y}, **more)})          # noqa: E731
    # A sheet from elsewhere.  One region was given values, and the file
    # says what they look like — which is not what they are; ...
    values = [{"name": "U", "tex": "U", "form": '["f","5.5"]', "shown": {"text/plain": "999", "text/latex": "$\\displaystyle 999$"}},
              {"name": "n", "form": '["i","3"]', "shown": {"text/plain": "three"}}]
    given = {"sheet": "source.ipynb", "name": "source", "revision": 4, "saved": "2026-10-11T10:00:00.000Z", "values": values}
    # ... one says that it was given values, but its text is more than the
    # code that holds them; one says so of other names than its code has;
    # ... and one holds what is no value.
    forged = dict(given, values=[{"name": "w", "form": '["i","1"]', "shown": {"text/plain": "1"}}])
    other = dict(given, values=[{"name": "v", "form": '["i","1"]', "shown": {"text/plain": "1"}}])
    broken = dict(given, values=[{"name": "bad", "form": '["o","os:system",{},{}]', "shown": {"text/plain": "harmless"}}])
    nb = nbformat.v4.new_notebook(cells=[
        nbformat.v4.new_code_cell("from utils.Engineer import *"), place(code(values), 0, given=given), place("U \u00b7 n", 120),
        place(code(forged["values"]) + "\nmore_than_that = 2", 200, given=forged), place(code(forged["values"]), 400, given=other),
        place(code(broken["values"]), 600, given=broken)])
    nb.cells[1].outputs = [nbformat.v4.new_output("stream", name="stdout", text="what the file showed\n")]
    nb.metadata["edsl_worksheet"] = {"version": 1, "settings": {}}
    nbformat.write(nb, str(folder / "theirs.ipynb"))
    # It is opened in the place of a sheet that was calculated here.
    keys = page.keyboard
    typed(page, "mine := 7")
    settled(page)
    save_as(page, "mine.ipynb")
    named(page, "mine")
    page.evaluate("() => { window.typeset = []; const send = WebSocket.prototype.send;"
                  " WebSocket.prototype.send = function (text) { const said = JSON.parse(text);"
                  " if (said.op === 'render') window.typeset.push(said.regions.length); return send.call(this, text); }; }")
    keys.press("Control+o")
    page.wait_for_selector(".dialog .files")
    page.click(".files div:has-text('theirs.ipynb')")
    keys.press("Enter")
    page.wait_for_selector("#banner:not([hidden])")
    page.wait_for_function("document.querySelectorAll('.region.given .mark').length === 2")

    # Not calculated here yet: what the file says is shown, as its outputs
    # would be (and they are not shown beside it).  Nothing is typeset for
    # it by the kernel; where the values are from was looked for all the same.
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.4notfoundU:=999n:=three", "Fromsource\u00b7rev.4notfoundbad:=harmless"]
    assert page.evaluate("document.querySelectorAll('.region.given .outs, .region.given .eq').length") == 0
    assert page.evaluate("[...document.querySelectorAll('.region.given .line')].map((line) => [line.dataset.name, line.querySelectorAll('.katex').length])") == [
        ["U", 2], ["n", 0], ["bad", 0]]
    # What is not the code for what it says is math like any other: it shows what it would run.
    texts = shown(page)
    assert len(texts) == 5 and all("given" in text and "From source" not in text for text in texts[2:4]) and "more" in texts[2]
    assert page.evaluate("[...document.querySelectorAll('.region')].map((r) => r.classList.contains('given'))") == [True, False, False, False, True]
    assert page.evaluate("window.typeset") == [3]               # (what was given is not typeset by the kernel: the page shows it)
    # Saved as it is — not calculated — it keeps no values: what the kernel
    # has is the sheet that was here before.
    save(page)
    assert "values" not in sheet_says(folder, "theirs.ipynb") and page.is_visible("#banner")
    assert [out.get("text") or out.data["text/plain"] for out in given_cells(folder, "theirs.ipynb")[0].outputs] == [
        "From source \u00b7 rev. 4\n", "U = 999", "n = three"]

    # Calculated, a region that was given values shows what the kernel has:
    # the file's word on how they look is gone.  What is no value is an
    # error, and nothing of it is in force.
    page.click("#banner .btn.primary")
    settled(page)
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.4notfoundU:=5.5n:=3",
                                    "Fromsource\u00b7rev.4notfoundbad:=harmlessValueError:astoredvalueofakindthistoolkitdoesnotknow:'os:system'"]
    assert "= 16.5" in shown(page)[1]
    assert page.evaluate("[...document.querySelectorAll('.region')].map((r) => r.classList.contains('has-error'))") == [False, False, False, False, True]
    assert page.evaluate("getComputedStyle(document.querySelector('.region.given.has-error .line .res')).opacity") == "0.35"
    assert page.evaluate("getComputedStyle(document.querySelector('.region.given:not(.has-error) .line .res')).opacity") == "1"
    assert page.inner_text("#status-text") == "1 region with errors"
    assert page.evaluate("typeof window.os") == "undefined"

    # On paper: where it is from, and the values; not the mark, which is the screen's.
    page.emulate_media(media="print")
    assert page.evaluate("['.whence', '.mark', '.line'].map((part) => getComputedStyle(document.querySelector(`.region.given ${part}`)).display)") == [
        "flex", "none", "flex"]
    page.emulate_media(media="screen")
    # Saved, the region is in the file as the kernel has it — and what was not that code is still not.
    save(page)
    cells_ = nbformat.read(str(folder / "theirs.ipynb"), as_version=4).cells
    assert ["given" in cell.metadata["edsl"] for cell in cells_[1:]] == [True, False, False, False, True]
    assert [out.get("text") or out.data["text/plain"] for out in cells_[1].outputs] == ["From source \u00b7 rev. 4\n", "U = 5.5", "n = 3"]
    assert list(handed_on(folder, "theirs.ipynb")[1]) == ["U", "n", "w", "more_than_that"]       # (and nothing of the sheet before)

    # What the clipboard says a region was given is taken as a file's word
    # is: a text is given nothing; nor is math that is more than the code.
    held = code(forged["values"])
    regions = [{"kind": "text", "source": held, "x": 0, "y": 0, "w": 300, "given": forged},
               {"kind": "math", "source": held + "\nmore = 1", "x": 0, "y": 120, "given": forged},
               {"kind": "math", "source": held, "x": 0, "y": 300, "given": forged}]
    assert page.evaluate(PASTE, [[], [["application/x-edsl-regions", json.dumps(regions)]]]) is True
    page.wait_for_function("document.querySelectorAll('.region.given .mark').length === 3")
    assert page.evaluate("[...document.querySelectorAll('.region')].slice(5).map((r) => [r.classList.contains('text'), r.classList.contains('given')])") == [
        [True, False], [False, False], [False, True]]
    assert page.evaluate(GIVEN)[2] == "Fromsource\u00b7rev.4notfoundw:=1"
    page.locator(".region.text").last.click()                   # (that text is written like any text)
    page.wait_for_selector(".region.text.editing")
    assert page.locator(".dialog").count() == 0
    keys.press("Escape")


def test_the_other_sheet_is_named_as_it_is_reached_from_the_sheets_own_folder(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    (folder / "deep" / "er").mkdir(parents=True)
    typed(page, "U := 5.5 V")
    settled(page)
    save_as(page, "source.ipynb")
    named(page, "source")
    new_sheet(page)
    values_dialog(page, "source.ipynb")
    take(page, "U")
    reference = "document.querySelector('.region.given .whence span').title"
    assert page.evaluate(reference) == "source.ipynb"

    def save_into(name, *down, up=None):
        """Save as `name`: in the folder that is reached from the one the dialog opens in — the sheet's own — by going
        up to the folder `up` and then down these."""
        keys.press("Control+Shift+s")
        page.wait_for_selector(".dialog .files")
        if up is not None:
            page.click(f".dialog .crumbs a:has-text('{up}')")
        for part in ([up] if up is not None else []) + list(down):
            if part != up:
                page.click(f".files div.dir:has-text('{part}')")
            page.wait_for_function("(part) => document.querySelector('.dialog .crumbs').textContent.trim().endsWith(part)", arg=part)
        page.fill(".dialog input[type=text]", name)
        keys.press("Enter")
        named(page, name)
        settled(page)

    # Saved two folders down, the sheet reaches the other one from there —
    # in its file, and on the page that is still open.
    save_into("below", "deep", "er")
    assert given_cells(folder / "deep" / "er", "below.ipynb")[0].metadata["edsl"]["given"]["sheet"] == "../../source.ipynb"
    assert page.evaluate(reference) == "../../source.ipynb" and page.evaluate(GIVEN) == ["Fromsource\u00b7rev.1U:=5.5V"]
    assert "open=deep%2Fer%2Fbelow.ipynb" in page.url
    # It finds it there: its dialog, and the mark when the other sheet has more.
    box = page.locator(".region.given").bounding_box()
    page.wait_for_timeout(600)
    page.mouse.dblclick(box["x"] + 40, box["y"] + 30)
    page.wait_for_selector(".dialog.given")
    assert [row[:2] for row in page.evaluate(ROWS)] == [["U", True]] and page.evaluate(NOTES) == []
    page.click(".dialog.given .btn:has-text('Other sheet')")    # (another sheet is looked for where this sheet is)
    page.wait_for_selector(".dialog .files")
    page.wait_for_function("document.querySelector('.dialog .crumbs').textContent.trim().endsWith('/ deep / er')")
    keys.press("Escape")
    page.wait_for_selector(".dialog", state="detached")
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.1U:=5.5V"]
    rewritten(folder, "source.ipynb", lambda meta: meta["values"].update(revision=2))
    refocused(page)
    page.wait_for_selector(".region.given .mark")
    assert page.evaluate(MARKS) == [["rev. 2 is there", False]]
    # Undo remembers the other sheet as it is reached from here, too.
    page.click(".region.given .mark")
    page.wait_for_selector(".dialog.given")
    take(page)
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.2U:=5.5V"]
    save_into("between", up="deep")
    assert page.evaluate(reference) == "../source.ipynb"
    keys.press("Control+z")
    settled(page)
    page.wait_for_selector(".region.given .mark")
    assert page.evaluate(GIVEN) == ["Fromsource\u00b7rev.1rev.2isthereU:=5.5V"] and page.evaluate(reference) == "../source.ipynb"

    # A file from elsewhere that names a sheet outside the folder the worksheet was started in: not found, and said so.
    book = json.loads((folder / "deep" / "between.ipynb").read_text(encoding="utf-8"))
    for cell in book["cells"]:
        if "given" in cell["metadata"].get("edsl", {}):
            cell["metadata"]["edsl"]["given"]["sheet"] = "../../../elsewhere/source.ipynb"
    (folder / "deep" / "between.ipynb").write_text(json.dumps(book), encoding="utf-8")
    opened(page, "deep/between.ipynb")
    page.wait_for_selector(".region.given .mark.lost")
    assert page.evaluate(MARKS) == [["not found", True]]
    page.click(".region.given .mark")
    page.wait_for_selector(".dialog.given")
    assert page.evaluate(NOTES) == ["../../../elsewhere/source.ipynb is outside the folder this worksheet was started in."]
    assert page.is_disabled(TAKE)
    keys.press("Escape")
    page.wait_for_selector(".dialog", state="detached")
    # Saved into another folder, a sheet that cannot be reached is named as it was.
    save_into("above", up=folder.name)
    assert given_cells(folder, "above.ipynb")[0].metadata["edsl"]["given"]["sheet"] == "../../../elsewhere/source.ipynb"
    assert page.evaluate(reference) == "../../../elsewhere/source.ipynb" and page.evaluate(MARKS) == [["not found", True]]


def test_a_sheet_is_not_left_while_it_is_being_saved(page_and_folder):
    # A save takes a moment, and may wait for a calculation.  Another sheet
    # that took this one's place in that moment would be given its name
    # when the save is done — and be saved over its file the next time.
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    typed(page, "aa := 2")
    settled(page)
    save_as(page, "staying.ipynb")
    named(page, "staying")
    # (A save that trips over something says so, where a page's errors are
    # said — and the next save is not held up by it.)
    page.evaluate("() => { window.trip = true; const write = JSON.stringify;"
                  " JSON.stringify = function (value, ...more) {"
                  " if (window.trip && value && value.doc) { window.trip = false; throw new Error('tripped while saving'); }"
                  " return write.call(this, value, ...more); }; }")
    keys.press("Control+s")
    for _ in range(100):
        if page.problems:
            break
        page.wait_for_timeout(50)
    assert len(page.problems) == 1 and "tripped while saving" in page.problems[0]
    page.problems.clear()
    save(page)
    held = []
    page.route("**/api/doc?*", lambda route: held.append(route) if route.request.method == "PUT" else route.continue_())
    keys.press("Control+s")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    keys.press("Control+s")                                     # (a second save waits for the first: one at a time)
    keys.press("Alt+n")
    page.wait_for_timeout(400)
    assert len(held) == 1 and page.inner_text("#doc-name") == "staying" and page.evaluate("document.querySelectorAll('.region').length") == 1
    held[0].continue_()
    for _ in range(100):
        if len(held) == 2:
            break
        page.wait_for_timeout(50)
    page.wait_for_timeout(200)
    assert len(held) == 2 and page.inner_text("#doc-name") == "staying"          # (and the new sheet for the second)
    held[1].continue_()
    page.wait_for_function("document.querySelector('#doc-name').textContent === 'Untitled' && !document.querySelector('.region')")
    page.unroute("**/api/doc?*")
    page.wait_for_timeout(300)
    assert page.inner_text("#doc-name") == "Untitled" and "open=" not in page.url
    assert [cell.source for cell in nbformat.read(str(folder / "staying.ipynb"), as_version=4).cells[1:]] == ["aa := 2"]


def test_a_sheet_is_saved_at_once_while_its_kernel_cannot_be_reached(page_and_folder):
    # The page and the server talk about the kernel over a line of their
    # own; the file is saved over another.  With the first one down, a save
    # is not held up by a question nobody can be asked.
    page, folder = page_and_folder
    page.add_init_script("window.lines = []; window.WebSocket = class extends WebSocket {"
                         " constructor(...given) { super(...given); window.lines.push(this); } };")
    page.reload()
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    typed(page, "aa := 2")
    settled(page)
    save_as(page, "cut.ipynb")
    named(page, "cut")
    typed(page, "bb := 3")
    settled(page)
    page.evaluate("document.querySelector('#toast').hidden = true")
    began = time.time()
    page.evaluate("window.lines.at(-1).close()")
    page.keyboard.press("Control+s")
    page.wait_for_function(f"{TOAST}.startsWith('Saved')")
    assert page.evaluate(TOAST) == NOT_KEPT and time.time() - began < 3
    assert handed_on(folder, "cut.ipynb") == [1, {"aa": "2"}] and saved_settings(folder, "cut.ipynb")["about"]["revision"] == 2
    # Connected again, and calculated, it is saved with its values.
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    settled(page)
    save(page)
    assert handed_on(folder, "cut.ipynb") == [2, {"aa": "2", "bb": "3"}]


def test_a_server_started_before_sheets_handed_values_on_is_not_asked_to(page_and_folder):
    # The page is read from the disk and is the new one at once; a server
    # that has been running since before is not.  It would drop what a
    # region was given when the sheet is saved, so nothing is given there.
    page, folder = page_and_folder
    page.route("**/api/info", lambda route: route.fulfill(json={"root": str(folder), "name": folder.name}))
    page.reload()
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    page.evaluate("() => { window.asked = []; const send = WebSocket.prototype.send;"
                  " WebSocket.prototype.send = function (text) { window.asked.push(JSON.parse(text).op); return send.call(this, text); }; }")
    typed(page, "aa := 2")
    settled(page)
    save_as(page, "before.ipynb")
    named(page, "before")
    page.wait_for_function(f"{TOAST} === 'Saved'")
    assert "values" not in sheet_says(folder, "before.ipynb") and "values" not in page.evaluate("window.asked") and "calc" in page.evaluate("window.asked")
    page.click(".tool[data-cmd='take-values']")
    page.wait_for_function(f"{TOAST}.startsWith('The worksheet server running here was started before sheets could hand values on')")
    assert page.locator(".dialog").count() == 0


# ---- figures ------------------------------------------------------------------

# The figures the math regions show: the size each one's element says, the dots of its file, and how large it is drawn.
FIGURES = """() => [...document.querySelectorAll('.region.math img')].map((img) => {
    const box = img.getBoundingClientRect();
    return {said: [Number(img.getAttribute('width')), Number(img.getAttribute('height'))],
            dots: [img.naturalWidth, img.naturalHeight], drawn: [Math.round(box.width), Math.round(box.height)]};
})"""


def figures(page, count):
    """The figures, once the page shows ``count`` of them and all have been read."""
    page.wait_for_function(
        "(count) => { const all = [...document.querySelectorAll('.region.math img')];"
        " return all.length === count && all.every((img) => img.complete && img.naturalWidth > 0); }",
        arg=count, timeout=20_000)
    return page.evaluate(FIGURES)


def test_a_figure_is_as_large_in_the_sheets_file_as_it_is_on_the_page(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    typed(page, "plot([1, 2, 3], [2, 3, 5], figsize=(3, 2))")
    settled(page)
    # A figure is drawn with twice the dots, and says how large it is meant to be: the page goes by that.
    (drawn,) = figures(page, 1)
    (wide, high), (across, down) = drawn["said"], drawn["dots"]
    assert wide > 100 and abs(across - 2 * wide) <= 1 and abs(down - 2 * high) <= 1
    assert abs(drawn["drawn"][0] - wide) <= 1 and abs(drawn["drawn"][1] - high) <= 1
    # So do JupyterLab and a hardcopy, which show the sheet's file: the size is saved with the figure.
    save_as(page, "figure.ipynb")
    named(page, "figure")
    stored = [output for cell in nbformat.read(str(folder / "figure.ipynb"), as_version=4).cells if cell.cell_type == "code"
              for output in cell.outputs if "image/png" in output.get("data", {})]
    assert [output.metadata for output in stored] == [{"image/png": {"width": wide, "height": high}}]
    # Where the sheet has not been calculated — on another computer, say — its
    # stored figure is shown until it is: as large as the calculated one.
    rewritten(folder, "figure.ipynb", lambda sheet: sheet["settings"]["about"].update(author="Someone else"))
    opened(page, "figure.ipynb")
    assert page.is_visible("#banner") and figures(page, 1) == [drawn]
    page.click("#banner .btn.primary")
    settled(page)
    assert not page.is_visible("#banner") and figures(page, 1) == [drawn]


def test_a_figure_that_a_statement_comes_to_is_as_large_as_one_that_is_shown(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    # The figure is shown at the end of the first region (it is left open there), and is what the second comes to.
    typed(page, "ax := plot([1, 2, 3], [2, 3, 5], figsize=(3, 2), show=False, return_ax=True)", "ax.figure")
    settled(page)
    shown, value = figures(page, 2)
    assert value == shown and shown["dots"][0] > 2 * 100
    # In the sheet's file both have the size; where the sheet is not calculated, both are drawn by it.
    save_as(page, "value.ipynb")
    named(page, "value")
    stored = [output for cell in nbformat.read(str(folder / "value.ipynb"), as_version=4).cells if cell.cell_type == "code"
              for output in cell.outputs if "image/png" in output.get("data", {})]
    assert [output.metadata for output in stored] == [{"image/png": dict(zip(("width", "height"), shown["said"]))}] * 2
    rewritten(folder, "value.ipynb", lambda sheet: sheet["settings"]["about"].update(author="Someone else"))
    opened(page, "value.ipynb")
    assert page.is_visible("#banner") and figures(page, 2) == [shown, value]


# ---- what a sheet reads outside itself ------------------------------------------

# Each line of the math regions as it reads (without the blanks KaTeX sets), and the marks on what is kept.
LINES = "[...document.querySelectorAll('.region.math .line')].map((l) => l.innerText.replace(/[\\s\\u200b]+/g, ''))"
KEPT = "[...document.querySelectorAll('.line .kept')].map((k) => [k.closest('.line').innerText.replace(/[\\s\\u200b]+/g, '').split(':=')[0], k.title])"
FAILED = "[...document.querySelectorAll('.line.bad .flag')].map((flag) => flag.getAttribute('aria-label').split(':')[0])"
READS = ["import numpy as np", "noise := np.random.rand()", 'at_ := "now"\\time', "bench := np.loadtxt('bench.txt')",
         "level := bench.mean()", "gain := 2", "out := level \\cdot gain + noise"]


def kept_cells(folder, name):
    """What a sheet's file keeps of what was read outside the sheet: for each cell that has any, the names read and why."""
    out = {}
    for cell in nbformat.read(str(folder / name), as_version=4).cells:
        records = cell.metadata.get("edsl", {}).get("kept")
        if records:
            out[cell.source.split(" :=")[0]] = [(record["why"], sorted(record["writes"])) for record in records]
    return out


def changed(before, after):
    """The lines that read otherwise now, by the name they begin with."""
    return [old.split(":=")[0] for old, new in zip(before, after) if old != new]


def test_what_a_sheet_reads_outside_itself_is_read_once_and_stays_with_it(page_and_folder):
    page, folder = page_and_folder
    (folder / "bench.txt").write_text("4.98\n5.02\n5.01\n", encoding="utf-8")
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    typed(page, *READS)                                         # (line upon line: one calculation overtakes another)
    settled(page)
    first = page.evaluate(LINES)
    assert "array([4.98,5.02,5.01])" in first[3] and first[5] == "gain:=2" and len(first) == 7
    # A random number, the clock and a file were read: each is marked, and says what it read and when.
    marks = page.evaluate(KEPT)
    assert [name for name, title in marks] == ["noise", "at_", "bench"]
    assert [title.split(")")[0] for name, title in marks] == ["Read from outside the sheet (a random number", "Read from outside the sheet (the clock",
                                                               "Read from outside the sheet (a file"]
    assert all(title.endswith(", and kept since. Click to read it again; Ctrl+F9 reads everything again.") for name, title in marks)
    assert page.evaluate("[...document.querySelectorAll('.line .kept')].every((k) => /^kept \\d/.test(k.textContent) && k === k.parentNode.lastChild)")
    # Saved, the file has what was read, with the statement that read it.
    save_as(page, "bench.ipynb")
    named(page, "bench")
    assert kept_cells(folder, "bench.ipynb") == {"noise": [(["random"], ["noise"])], "at_": [(["clock"], ["at_"])], "bench": [(["file"], ["bench"])]}
    assert saved_settings(folder, "bench.ipynb")["about"]["revision"] == 1
    # (And the sheet is what its file is, however often it is calculated.)
    keys.press("F9")
    settled(page)
    assert not page.evaluate(DIRTY)

    # Another day: the file that was read is another one, the clock has
    # moved on.  The sheet shows what it showed, and is what its file is.
    (folder / "bench.txt").write_text("1.0\n1.0\n1.0\n", encoding="utf-8")
    opened(page, "bench.ipynb")
    assert page.evaluate(LINES) == first and not page.evaluate(DIRTY) and page.evaluate(TOAST) == ""
    assert page.evaluate("document.querySelector('#status').title").startswith("Last calculation: 1 statements run, 6 unchanged")
    assert [name for name, title in page.evaluate(KEPT)] == ["noise", "at_", "bench"]
    # Saved again as it is, it is the same revision, with the same readings.
    before = kept_cells(folder, "bench.ipynb"), (folder / "bench.ipynb").read_text(encoding="utf-8").count('"at": ')
    save(page)
    assert saved_settings(folder, "bench.ipynb")["about"]["revision"] == 1
    assert (kept_cells(folder, "bench.ipynb"), (folder / "bench.ipynb").read_text(encoding="utf-8").count('"at": ')) == before

    # Something else is changed: what was read stands, and what follows from both follows.
    page.locator(".region.math").nth(5).click()
    keys.press("Control+a")
    keys.type("gain := 3")
    keys.press("Enter")
    settled(page)
    assert changed(first, page.evaluate(LINES)) == ["gain", "out"]
    # A calculation that brings the sheet up to date reads nothing again (F9) ...
    keys.press("F9")
    settled(page)
    third = page.evaluate(LINES)
    assert changed(first, third) == ["gain", "out"]
    save(page)
    assert saved_settings(folder, "bench.ipynb")["about"]["revision"] == 2 and not page.evaluate(DIRTY)
    # ... a click on a mark reads that one again — the file, here: the sheet
    # follows, is not opened for writing, and is no longer what its file is.
    page.locator(".line .kept").nth(2).click()
    settled(page)
    fourth = page.evaluate(LINES)
    assert changed(third, fourth) == ["bench", "level", "out"] and "array([1.,1.,1.])" in fourth[3]
    assert page.locator(".region.editing").count() == 0 and page.evaluate(DIRTY)
    # ... and calculating everything from scratch reads everything again.
    save(page)
    assert saved_settings(folder, "bench.ipynb")["about"]["revision"] == 3 and not page.evaluate(DIRTY)
    # (That one click read it once: the calculations after it read nothing again.)
    (folder / "bench.txt").write_text("2.0\n2.0\n2.0\n", encoding="utf-8")
    keys.press("F9")
    settled(page)
    assert page.evaluate(LINES) == fourth and not page.evaluate(DIRTY)
    (folder / "bench.txt").write_text("1.0\n1.0\n1.0\n", encoding="utf-8")
    keys.press("Control+F9")
    settled(page)
    fifth = page.evaluate(LINES)
    assert changed(fourth, fifth) == ["noise", "at_", "out"] and page.evaluate(DIRTY)
    # What is asked for anew and is not to be had is not there any more ...
    (folder / "bench.txt").rename(folder / "away.txt")
    page.locator(".line .kept").nth(2).click()
    settled(page)
    assert page.evaluate(FAILED) == ["FileNotFoundError", "NameError", "NameError"]
    assert [name for name, title in page.evaluate(KEPT)] == ["noise", "at_"]
    # The kernel is started anew: the sheet has what was read — also what
    # is not in its file yet — and not what it has let go of.
    page.click("#calc-menu .menu-button")
    page.click("#calc-menu [data-cmd='restart']")
    page.wait_for_selector("#status[data-state='starting'], #status[data-state='none']", timeout=20_000)
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    settled(page)
    assert [page.evaluate(LINES)[at] for at in (1, 2, 5)] == [fifth[at] for at in (1, 2, 5)]
    assert page.evaluate(FAILED) == ["FileNotFoundError", "NameError", "NameError"]
    assert [name for name, title in page.evaluate(KEPT)] == ["noise", "at_"]
    # (The file is there again: a calculation reads what there is no reading of.)
    (folder / "away.txt").rename(folder / "bench.txt")
    keys.press("F9")
    settled(page)
    assert page.evaluate(LINES) == fifth and page.evaluate(FAILED) == []
    save(page)
    assert saved_settings(folder, "bench.ipynb")["about"]["revision"] == 4
    # On paper the marks are not there: they are for whoever works on the sheet.
    page.emulate_media(media="print")
    assert page.evaluate("[...document.querySelectorAll('.line .kept')].map((k) => getComputedStyle(k).display)") == ["none"] * 3
    page.emulate_media(media="screen")

    # A sheet that is saved while it is not calculated keeps what its file
    # has for the regions that are as they were, and nothing for one that
    # has been rewritten.
    page.uncheck("#auto-calc")
    page.locator(".region.math").nth(2).click()
    keys.press("Control+a")
    keys.type('at_ := "today"\\time')
    keys.press("Enter")
    page.wait_for_selector(".region.stale")
    keys.press("Control+s")
    page.wait_for_function(f"{TOAST} === {json.dumps(NOT_KEPT)}")
    assert kept_cells(folder, "bench.ipynb") == {"noise": [(["random"], ["noise"])], "bench": [(["file"], ["bench"])]}
    page.check("#auto-calc")
    settled(page)
    save(page)
    assert sorted(kept_cells(folder, "bench.ipynb")) == ["at_", "bench", "noise"]


def test_a_sheet_from_before_reads_anew_and_a_server_from_before_says_what_it_cannot_keep(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    keys = page.keyboard
    # A sheet that was saved before what it read could be kept — or by
    # another program: opened, it reads, and is then no longer what its
    # file is (which has none of that).
    place = lambda source, y, **more: nbformat.v4.new_code_cell(source, metadata={"edsl": dict({"x": 0, "y": y}, **more)})          # noqa: E731
    forged = {"id": "?", "at": "2026-10-11T10:00:00Z", "why": ["random"], "state": "?", "reads": {}, "writes": {"plain": '["i","99"]'}}
    nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("from utils.Engineer import *"), place("import numpy as np", 0),
                                         place("noise := np.random.rand()", 40), place("plain := 2 + 2", 80, kept=[forged])])
    nb.metadata["edsl_worksheet"] = {"version": 1, "settings": {"about": {"revision": 7, "saved": "2026-10-11T10:00:00.000Z"}},
                                     "values": {"revision": 7, "saved": "2026-10-11T10:00:00.000Z", "toolkit": "0.0.1", "values": []}}
    nbformat.write(nb, str(folder / "before.ipynb"))
    try:
        from importlib.metadata import version
        toolkit = version("engineering-dsl")
    except Exception:
        toolkit = ""
    opened(page, "before.ipynb")
    # (It says, too, that another toolkit made what it has than the one that calculates it now.)
    if toolkit:
        assert page.evaluate(TOAST) == (f"This sheet was last saved with version 0.0.1 of the toolkit; this is {toolkit}. "
                                        "What is calculated may come out otherwise than it did.")
    assert page.is_visible("#banner") and not page.evaluate(DIRTY) and page.evaluate(KEPT) == []
    # (Saved as it was opened — not calculated — it has what its file had.)
    save(page)
    assert kept_cells(folder, "before.ipynb") == {"plain": [(["random"], ["plain"])]}
    assert saved_settings(folder, "before.ipynb")["about"]["revision"] == 7
    page.click("#banner .btn.primary")
    settled(page)
    assert [name for name, title in page.evaluate(KEPT)] == ["noise"] and page.evaluate(DIRTY)
    # What its file says a calculation came to is not asked: a calculation is calculated.
    assert page.evaluate(LINES)[2] == "plain:=2+2=4"
    drawn = page.evaluate(LINES)[1]

    # A worksheet server that was started before this could be kept writes
    # the sheet without it: that is not done without a word.
    def as_before(route):
        response = route.fetch()
        said = response.json()
        said.pop("kept", None)
        route.fulfill(response=response, json=said)

    wrote = lambda url: "/api/doc" in url                                                  # noqa: E731
    page.route(wrote, lambda route: as_before(route) if route.request.method == "PUT" else route.continue_())
    page.evaluate("document.querySelector('#toast').hidden = true")
    keys.press("Control+s")
    page.wait_for_function(f"{TOAST}.startsWith('Not all of it is saved: what the sheet read outside itself')")
    assert "started before it could keep them" in page.evaluate(TOAST) and page.evaluate(DIRTY)
    page.unroute(wrote)
    save(page)
    assert kept_cells(folder, "before.ipynb") == {"noise": [(["random"], ["noise"])]} and not page.evaluate(DIRTY)
    assert saved_settings(folder, "before.ipynb")["about"]["revision"] == 9          # (the server of before did write it: that was 8)
    # Opened once more, in the place of another sheet, it has what it read.
    new_sheet(page)
    typed(page, "import numpy as np", "noise := np.random.rand()")       # (the same statement, on another sheet: another reading)
    settled(page)
    other = page.evaluate(LINES)[1]
    assert other != drawn
    save_as(page, "other.ipynb")
    named(page, "other")
    keys.press("Control+o")
    page.wait_for_selector(".dialog .files")
    page.click(".files div:has-text('before.ipynb')")
    keys.press("Enter")
    page.wait_for_function("document.querySelector('#doc-name').textContent === 'before'")
    settled(page)
    assert page.evaluate(LINES)[1] == drawn and not page.evaluate(DIRTY)


def test_a_picture_of_what_was_read_is_kept_as_it_was_drawn(page_and_folder):
    page, folder = page_and_folder
    page.wait_for_selector("#status[data-state='idle']", timeout=90_000)
    typed(page, "import numpy as np", "plot([1, 2, 3], np.random.rand(3))")
    settled(page)
    # (A figure is drawn with twice the dots, and says how large it is meant to be.)
    picture = ("(() => { const img = document.querySelector('.region.math img');"
               " return img && [img.src.length, img.src.slice(-80), img.width, img.height, img.naturalWidth, img.naturalHeight]; })()")
    page.wait_for_function(f"{picture} && document.querySelector('.region.math img').naturalWidth > 0")
    drawn = page.evaluate(picture)
    assert drawn[2] > 100 and abs(drawn[4] - 2 * drawn[2]) <= 2 and abs(drawn[5] - 2 * drawn[3]) <= 2
    assert page.locator(".line .kept").count() == 1
    save_as(page, "drawn.ipynb")
    named(page, "drawn")
    # Opened again it is the picture that was drawn — of the numbers that were drawn — and as large.
    opened(page, "drawn.ipynb")
    page.wait_for_function(f"{picture} && document.querySelector('.region.math img').naturalWidth > 0")
    assert page.evaluate(picture) == drawn and not page.evaluate(DIRTY)
    assert page.evaluate("document.querySelector('#status').title").startswith("Last calculation: 0 statements run, 2 unchanged")
