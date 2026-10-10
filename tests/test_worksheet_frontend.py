"""The worksheet front end: its pure logic under node, and the page itself in a browser.

Both tests skip when their tool is missing (node; Playwright with a
Chromium).  To point the browser test at a particular Chromium or Edge,
set ``WORKSHEET_TEST_BROWSER`` to its executable.
"""
import glob
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

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


