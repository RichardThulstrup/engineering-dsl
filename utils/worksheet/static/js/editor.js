// The in-place source editor of a region.
//
// A math region is edited as DSL text: a transparent <textarea> lies over
// a coloured copy of itself (the copy also gives the box its size).  The
// text is the notation the toolkit already reads, so everything the
// symbol palette types — and everything pasted from a notebook — works.
// Two aids on top: `\name` completes to a glyph (\ohm → Ω), and digits
// typed after `^` become superscripts (cm^2 → cm²).

import { el, ZWSP } from "./util.js";
import { complete, GLYPH, SUPERSCRIPT, SUPERSCRIPT_CHARS } from "./symbols.js";
import { nextSlot, slot, snippets } from "./library.js";
import { tableCell, tableRow } from "./textaids.js";

const LIST = 12;          // rows in the backslash list

// A name being typed after a backslash: letters (\ohm), or one of the
// two-character keys \^2 and \_1.
const COMPLETION = /\\([A-Za-z]*|[\^_].?)$/;

/** Brackets left open at the end of `text`, and the string it ends inside (or null). */
function unclosed(text) {
  let depth = 0, quote = null;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quote) {
      if (ch === "\\") i += 1;
      else if (text.startsWith(quote, i)) { i += quote.length - 1; quote = null; }
      else if (ch === "\n" && quote.length === 1) quote = null;
    } else if (ch === "#") {
      const end = text.indexOf("\n", i);
      if (end < 0) break;
      i = end;
    } else if (ch === '"' || ch === "'") {
      quote = text.startsWith(ch.repeat(3), i) ? ch.repeat(3) : ch;
      i += quote.length - 1;
    } else if ("([{".includes(ch)) depth += 1;
    else if (")]}".includes(ch)) depth = Math.max(0, depth - 1);
  }
  return { depth, quote };
}

/** Does `text` still need another line before it can be calculated? */
export function incomplete(text, caret) {
  const upto = text.slice(0, caret);
  const { depth, quote } = unclosed(upto);
  if (quote && quote.length === 3) return true;
  // An open bracket asks for another line — unless it is closed further
  // on: with the caret inside "atan2(3, 4)", Enter finishes the region.
  if (depth > 0 && unclosed(text).depth > 0) return true;
  const line = upto.slice(upto.lastIndexOf("\n") + 1);
  const code = line.replace(/#.*$/, "").trimEnd();
  if (code.endsWith(":") || code.endsWith("\\")) return true;
  return /^\s+\S/.test(line);              // inside an indented block, on a non-empty line
}

export class SourceEditor {
  /**
   * @param {object} opts
   * @param {"math"|"text"} opts.kind
   * @param {string} opts.value
   * @param {(text: string, caret?: number) => string} opts.highlight
   * @param {(value: string, how: string) => void} opts.onCommit  how: enter | blur | up | down
   * @param {() => void} opts.onCancel
   * @param {(value: string) => void} opts.onInput
   */
  constructor({ kind, value, highlight, onCommit, onCancel, onInput }) {
    this.kind = kind;
    this.highlight = highlight;
    this.onCommit = onCommit;
    this.onCancel = onCancel;
    this.onInput = onInput || (() => {});
    this.mirror = el("pre", { "aria-hidden": "true" });
    this.area = el("textarea", { spellcheck: kind === "text", autocomplete: "off",
                                 autocapitalize: "off", rows: 1, wrap: kind === "text" ? "soft" : "off" });
    this.el = el("div", { class: "ed " + kind }, this.mirror, this.area);
    this.popup = null;
    this.matches = [];
    this.choice = 0;
    this.superscripting = false;
    this.done = false;
    this.area.value = value;
    this.area.addEventListener("input", (event) => this.input(event));
    this.area.addEventListener("keydown", (event) => this.keydown(event));
    this.area.addEventListener("blur", () => this.blur());
    this.area.addEventListener("click", () => { this.superscripting = false; this.closePopup(); });
    this.refresh();
  }

  get value() { return this.area.value; }

  focus(caret) {
    const at = caret === undefined ? this.area.value.length : caret;
    this.area.focus({ preventScroll: true });
    this.area.setSelectionRange(at, at);
  }

  /**
   * Insert text at the caret (from the symbol palette).  `caret` is where
   * typing goes on, counted from the start of the text: an entry such as
   * `atan2(, )` leaves it in its first open place.  `select` characters
   * from there are selected, to be typed over (a table's first header).
   */
  insert(text, caret = text.length, select = 0) {
    const { selectionStart: a, selectionEnd: b } = this.area;
    this.area.setRangeText(text, a, b, "end");
    this.area.setSelectionRange(a + caret, a + caret + select);
    this.superscripting = false;
    this.changed();
  }

  refresh() {
    const text = this.area.value;
    if (this.kind === "math") this.mirror.innerHTML = this.highlight(text, this.area.selectionStart);
    else this.mirror.textContent = text + (text.endsWith("\n") || !text ? ZWSP : "");
  }

  changed() {
    this.refresh();
    this.updatePopup();
    this.onInput(this.area.value);
  }

  input(event) {
    if (this.kind === "math" && event.inputType === "insertText" && event.data) {
      this.named();
      this.typed(event.data);
    } else {
      this.superscripting = false;
    }
    this.changed();
  }

  /**
   * A backslash name typed out in full becomes its glyph, the way a
   * LaTeX command ends at the first non-letter: `\pi R` → `π R`,
   * `\sqrt(` → `√(`.  The two-character keys (`\^2`, `\_1`) convert at once.
   */
  named() {
    const area = this.area;
    const at = area.selectionStart;
    const upto = area.value.slice(0, at);
    let m = /\\([\^_].)$/.exec(upto);
    if (m && GLYPH.has(m[1])) {
      area.setRangeText(GLYPH.get(m[1]), at - m[0].length, at, "end");
      return;
    }
    m = /\\([A-Za-z]+)([^A-Za-z])$/.exec(upto);
    if (m && GLYPH.has(m[1])) {
      area.setRangeText(GLYPH.get(m[1]) + m[2], at - m[0].length, at, "end");
    }
  }

  /** `^2` → `²` as it is typed; further digits continue the exponent. */
  typed(data) {
    const area = this.area;
    const at = area.selectionStart;
    const text = area.value;
    const glyph = SUPERSCRIPT[data];
    const digitLike = /^[0-9\-+.]$/.test(data);
    if (digitLike && glyph && at >= 2 && text[at - 2] === "^" && /[\w)\]²³⁰-⁹Ͱ-Ͽ]/.test(text[at - 3] || "")) {
      area.setRangeText(glyph, at - 2, at, "end");
      this.superscripting = true;
    } else if (digitLike && glyph && this.superscripting && SUPERSCRIPT_CHARS.has(text[at - 2])) {
      area.setRangeText(glyph, at - 1, at, "end");
    } else {
      this.superscripting = false;
    }
  }

  // ---- completion --------------------------------------------------------
  prefix() {
    if (this.kind !== "math") return null;
    const upto = this.area.value.slice(0, this.area.selectionStart);
    const m = COMPLETION.exec(upto);
    return m ? m[1] : null;
  }

  updatePopup() {
    const prefix = this.prefix();
    if (prefix === null) { this.closePopup(); return; }
    // Names of glyphs first; then what the library has under that word
    // (functions, templates, ▸ units), which is picked, never auto-typed.
    const names = complete(prefix);
    this.matches = [...names, ...snippets(prefix, Math.max(0, LIST - names.length))];
    if (!this.matches.length) { this.closePopup(); return; }
    this.choice = 0;
    if (!this.popup) {
      this.popup = el("div", { class: "popup" });
      this.popup.addEventListener("pointerdown", (event) => {
        event.preventDefault();                       // keep the focus in the editor
        const row = event.target.closest("[data-i]");
        if (row) this.accept(Number(row.dataset.i));
      });
      this.el.append(this.popup);
    }
    this.drawPopup(prefix);
    const marker = this.mirror.querySelector(".caret");
    const base = this.el.getBoundingClientRect();
    const zoom = base.width / this.el.offsetWidth || 1;
    if (marker) {
      const at = marker.getBoundingClientRect();
      this.popup.style.left = `${(at.left - base.left) / zoom}px`;
      this.popup.style.top = `${(at.bottom - base.top) / zoom + 3}px`;
    }
  }

  drawPopup(prefix) {
    this.popup.replaceChildren(...this.matches.map((match, i) => {
      const row = { class: i === this.choice ? "on" : "", dataset: { i } };
      if (match.kind === "snippet") {
        return el("div", row, el("span", { class: "s", text: match.face }), el("span", { class: "d", text: match.said }));
      }
      const name = el("span", { class: "n" }, "\\", el("b", { text: match.name.slice(0, prefix.length) }),
                      match.name.slice(prefix.length));
      return el("div", row, el("span", { class: "g", text: match.glyph }), name);
    }));
    this.popup.querySelector(".on")?.scrollIntoView?.({ block: "nearest" });
  }

  closePopup() {
    if (this.popup) { this.popup.remove(); this.popup = null; }
    this.matches = [];
  }

  accept(index) {
    const match = this.matches[index];
    const prefix = this.prefix();
    if (!match || prefix === null) return false;
    const at = this.area.selectionStart;
    const from = at - prefix.length - 1;
    this.area.setRangeText(match.glyph, from, at, "end");
    if (match.kind === "snippet") {
      const open = from + slot(match.glyph);
      this.area.setSelectionRange(open, open);
    } else if (match.glyph === "⌊⌋" || match.glyph === "⌈⌉") {
      this.area.setSelectionRange(this.area.selectionStart - 1, this.area.selectionStart - 1);
    }
    this.closePopup();
    this.refresh();
    this.onInput(this.area.value);
    return true;
  }

  // ---- keys ----------------------------------------------------------------
  keydown(event) {
    const area = this.area;
    const key = event.key;
    if (this.popup) {
      if (key === "ArrowDown" || key === "ArrowUp") {
        const n = this.matches.length;
        this.choice = (this.choice + (key === "ArrowDown" ? 1 : n - 1)) % n;
        this.drawPopup(this.prefix() || "");
        event.preventDefault();
        return;
      }
      if (key === "Tab") {
        if (this.accept(this.choice)) { event.preventDefault(); return; }
      }
      if (key === "Enter") {
        // A name typed out in full is simply finished by Enter, which
        // then does what Enter does; otherwise Enter picks from the list.
        const exact = this.choice === 0
          ? this.matches.findIndex((m) => m.kind !== "snippet" && m.name === this.prefix()) : -1;
        if (exact >= 0) this.accept(exact);
        else if (this.accept(this.choice)) { event.preventDefault(); return; }
      }
      if (key === "Escape") { this.closePopup(); event.preventDefault(); event.stopPropagation(); return; }
    }
    if (key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (this.kind === "text") this.commit("escape");
      else this.cancel();
      return;
    }
    if (key === "Tab" && this.kind === "text" && !event.ctrlKey && !event.altKey && !event.metaKey) {
      // In a table, Tab goes from cell to cell (Shift+Tab back) with the
      // cell's text selected, and past the last cell it adds a row.
      // Elsewhere it indents; it never takes the keyboard away.
      event.preventDefault();
      const at = area.selectionStart;
      const cell = tableCell(area.value, at, event.shiftKey ? -1 : 1);
      if (cell) {
        area.setSelectionRange(cell.start, cell.end);
      } else if (!event.shiftKey) {
        const row = tableRow(area.value, at);
        if (row) {
          area.setRangeText(row.text, row.at, row.at, "end");
          area.setSelectionRange(row.caret, row.caret);
        } else {
          area.setRangeText("    ", at, area.selectionEnd, "end");
        }
        this.changed();
      }
      return;
    }
    if (key === "Enter") {
      if (this.kind === "text") {
        if (event.ctrlKey || event.metaKey) { event.preventDefault(); this.commit("enter"); }
        return;
      }
      event.preventDefault();
      const at = area.selectionStart;
      if (event.shiftKey || incomplete(area.value, at)) {
        const line = area.value.slice(area.value.lastIndexOf("\n", at - 1) + 1, at);
        let indent = /^\s*/.exec(line)[0];
        if (line.replace(/#.*$/, "").trimEnd().endsWith(":")) indent += "    ";
        area.setRangeText("\n" + indent, at, area.selectionEnd, "end");
        this.changed();
      } else {
        this.commit("enter");
      }
      return;
    }
    if (key === "Tab" && this.kind === "math") {
      event.preventDefault();
      // Inside a line, Tab goes to the next place an inserted entry left
      // open — atan2(3|, ) → atan2(3, |); otherwise it indents.
      const at = area.selectionStart;
      const before = area.value.slice(area.value.lastIndexOf("\n", at - 1) + 1, at);
      const open = before.trim() && at === area.selectionEnd ? nextSlot(area.value, at) : -1;
      if (open >= 0) {
        area.setSelectionRange(open, open);
        this.refresh();
        return;
      }
      area.setRangeText("    ", area.selectionStart, area.selectionEnd, "end");
      this.changed();
      return;
    }
    if (this.kind === "math" && !event.shiftKey && !event.ctrlKey && !event.altKey
        && area.selectionStart === area.selectionEnd) {
      const at = area.selectionStart;
      if (key === "ArrowUp" && !area.value.slice(0, at).includes("\n")) {
        event.preventDefault();
        this.commit("up");
        return;
      }
      if (key === "ArrowDown" && !area.value.slice(at).includes("\n")) {
        event.preventDefault();
        this.commit("down");
        return;
      }
    }
    if (key.startsWith("Arrow") || key === "Home" || key === "End") {
      this.superscripting = false;
      // The caret moves after this event: recolour then, so the popup
      // marker follows it.
      requestAnimationFrame(() => { if (!this.done) { this.refresh(); this.updatePopup(); } });
    }
  }

  blur() {
    // A click on the palette or the popup keeps the focus (they cancel
    // the pointer's default action), and the palette's search field
    // borrows it to put what is found here; anything else ends the edit.
    setTimeout(() => {
      const now = document.activeElement;
      if (!this.done && now !== this.area && !now?.closest?.("[data-keeps-edit]")) this.commit("blur");
    }, 0);
  }

  commit(how) {
    if (this.done) return;
    this.done = true;
    this.closePopup();
    this.onCommit(this.area.value, how);
  }

  cancel() {
    if (this.done) return;
    this.done = true;
    this.closePopup();
    this.onCancel();
  }
}
