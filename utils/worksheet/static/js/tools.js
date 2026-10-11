// The symbol panel's two tools for text regions, after the Windows
// palette's own: a formula editor that writes the `$…$` for you, and a
// table to start from.
//
// The formula editor is MathLive (vendored, MIT), the editor the palette
// uses; it is fetched when the Formula tab is first opened, and draws
// with the KaTeX fonts the sheet already has.

import { el } from "./util.js";
import { tableMarkdown } from "./textaids.js";

export const TOOLS = ["Formula", "Table"];

let mathlive = null;

function loadMathlive() {
  if (!mathlive) {
    mathlive = import("../vendor/mathlive/mathlive.min.mjs").then((module) => {
      module.MathfieldElement.fontsDirectory = new URL("../vendor/katex/fonts", import.meta.url).href;
      module.MathfieldElement.soundsDirectory = null;         // a sheet is quiet
      return module;
    });
  }
  return mathlive;
}

/**
 * @param {object} hooks
 * @param {(text: string, options?: object) => void} hooks.insertMarkdown   into the text region (or a new one)
 * @param {() => ({start, end, latex, display}|null)} hooks.formula          the formula the caret is in (or that is selected)
 * @param {(latex, display, found, replace) => void} hooks.putFormula       in its place, beside it, or at the caret
 * @param {() => void} hooks.refocus                                        the keyboard, back to the sheet
 */
function formulaTool(hooks) {
  const holder = el("div", { class: "mf" }, el("span", { class: "wait", text: "Loading the formula editor…" }));
  const latex = el("div", { class: "latex" });
  const inline = el("button", { class: "btn primary", type: "button", disabled: true });
  const display = el("button", { class: "btn", type: "button", disabled: true });
  const clear = el("button", { class: "btn", type: "button", text: "Clear", disabled: true });
  const keyboard = el("input", { type: "checkbox" });
  const note = el("p", { class: "hint" });
  const pane = el("div", { class: "tool-pane" }, holder, latex,
                  el("div", { class: "row" }, inline, display),
                  el("div", { class: "row" }, clear, el("label", {}, keyboard, " On-screen keyboard")),
                  note);
  let field = null;
  let editing = null;                 // the formula in the text that the field was filled from
  let handedBack = 0;                 // when the keyboard last went back to the text (see `put`)

  const value = () => (field ? field.getValue("latex-expanded").trim() : "");

  function refresh() {
    const now = value();
    latex.textContent = now ? `$${now}$` : "";
    inline.disabled = display.disabled = clear.disabled = !now;
    inline.textContent = editing ? "Replace, in the line" : "Insert in the line";
    display.textContent = editing ? "Replace, on its own line" : "Insert on its own line";
    // Enter does what the highlighted button does: a formula that was
    // on its own line stays there.
    const apart = Boolean(editing && editing.display);
    inline.classList.toggle("primary", !apart);
    display.classList.toggle("primary", apart);
    note.textContent = editing
      ? "This is the formula the caret is in, or that is selected in the text. Change it here and put it back."
      : "Write the formula as you would say it: a/b is a fraction, ^ a power, _ an index, and \\ begins a name " +
        "(\\alpha, \\sqrt, \\int). Enter puts it into the text you are writing — or starts a text region.";
  }

  const same = (a, b) => Boolean(a && b && a.start === b.start && a.latex === b.latex);

  /** With the caret inside a formula of the text, take that formula up for changing. */
  function adopt() {
    if (!field) return;
    const found = hooks.formula();
    if (!found) editing = null;                          // the caret is elsewhere: what is here is new
    else if (!same(found, editing) && (editing || !value())) {
      editing = found;                                   // (a formula begun here is not thrown away for it)
      field.value = found.latex;
    }
    refresh();
  }

  function put(asDisplay) {
    const now = value();
    if (!now) return;
    const around = hooks.formula();
    handedBack = performance.now();
    hooks.putFormula(now, asDisplay, around, same(around, editing));
    editing = null;
    field.value = "";
    refresh();
  }

  function focused() {
    // MathLive takes the focus once more 60 ms after its field was
    // entered.  When the formula has gone into the text sooner than that
    // (Enter typed very fast), that would take the keyboard away from
    // the text again: send it back.
    if (performance.now() - handedBack < 150) { hooks.refocus(); return; }
    adopt();
    if (keyboard.checked) window.mathVirtualKeyboard.show();
  }

  inline.addEventListener("click", () => put(false));
  display.addEventListener("click", () => put(true));
  clear.addEventListener("click", () => { editing = null; field.value = ""; refresh(); field.focus(); });
  // The on-screen keyboard belongs to the field: it is up while the box
  // is ticked and the field has the focus, and gone with either.
  keyboard.addEventListener("change", () => {
    if (keyboard.checked) field.focus(); else window.mathVirtualKeyboard.hide();
  });
  refresh();

  let asked = false;
  /** The tab has come up: fetch the editor the first time, and look at the caret. */
  function shown() {
    if (asked) { adopt(); return; }
    asked = true;
    loadMathlive().then((module) => {
      field = new module.MathfieldElement();
      field.mathVirtualKeyboardPolicy = "manual";       // only when the box is ticked
      field.setAttribute("aria-label", "Formula");
      field.addEventListener("input", refresh);
      field.addEventListener("pointerdown", () => { handedBack = 0; }, true);     // a click asks for the field
      field.addEventListener("focusin", focused);
      field.addEventListener("focusout", () => window.mathVirtualKeyboard.hide());
      // Enter puts the formula into the text (Shift+Enter: on its own
      // line) — except while a \name is being typed, which Enter finishes.
      field.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && field.mode !== "latex" && !event.ctrlKey && !event.metaKey && !event.altKey) {
          event.preventDefault();
          event.stopPropagation();
          put(event.shiftKey || Boolean(editing && editing.display));
        }
      }, true);
      holder.replaceChildren(field);
      adopt();
    }, () => {
      holder.replaceChildren(el("span", { class: "wait", text: "The formula editor could not be loaded." }));
    });
  }

  return { pane, shown };
}

function tableTool(hooks) {
  const columns = el("input", { type: "number", min: "1", max: "10", value: "3", "aria-label": "Columns" });
  const rows = el("input", { type: "number", min: "1", max: "30", value: "3", "aria-label": "Rows" });
  const align = el("select", { "aria-label": "Alignment" },
                   ["Left", "Center", "Right"].map((name) => el("option", { value: name.toLowerCase(), text: name })));
  const preview = el("pre", { class: "sample" });
  const insert = el("button", { class: "btn primary", type: "button", text: "Insert the table" });
  const pane = el("div", { class: "tool-pane" },
                  el("div", { class: "row" }, el("label", {}, "Columns ", columns), el("label", {}, "Rows ", rows)),
                  el("div", { class: "row" }, el("label", {}, "Columns are set ", align)),
                  preview, el("div", { class: "row" }, insert),
                  el("p", { class: "hint", text: "A table to write over, put into the text you are writing — or into a " +
                                                 "new text region. Tab goes from cell to cell." }));
  const table = () => tableMarkdown(Number(columns.value), Number(rows.value), align.value);
  const refresh = () => { preview.textContent = table(); };
  for (const control of [columns, rows, align]) control.addEventListener("input", refresh);
  insert.addEventListener("click", () => {
    // The first header is selected, to be typed over.
    hooks.insertMarkdown(table(), { block: true, select: [2, "Header 1".length] });
  });
  refresh();
  return { pane, shown: refresh };
}

/** Build the tools once: {name: {pane, shown()}}. */
export function buildTools(hooks) {
  return { Formula: formulaTool(hooks), Table: tableTool(hooks) };
}
