// The symbol panel (F2): the everyday keys, then the library tab by tab —
// the tabs, sections and entries of the Windows symbol palette — and a
// search over all of it (Ctrl+E).  A click puts an entry at the caret;
// resting the pointer on one says what it is for, as the palette's
// status line does.  Its last two tabs are the palette's tools for text:
// the formula editor and the table builder (tools.js).

import { el } from "./util.js";
import { PALETTE, namesFor, noteFor } from "./symbols.js";
import { about, face, search, slot, tabs } from "./library.js";
import { buildTools, TOOLS } from "./tools.js";

const KEYS = "Keys";
const IDLE = "Click a key to put it at the caret. In a math region a backslash and a name type the same.";

/**
 * Fill `panel` and make it work.
 * @param {HTMLElement} panel
 * @param {object} hooks
 * @param {(text: string, caret: number) => void} hooks.insert  put text into the sheet
 * @param {() => void} hooks.refocus                           hand the keyboard back to the sheet
 * @param {object} hooks.text                                  what the tools for text regions need (tools.js)
 * @returns {{focusSearch: () => void, refresh: () => void}}
 */
export function buildPanel(panel, { insert, refocus, text }) {
  const tools = buildTools(text);
  const field = el("input", { type: "search", placeholder: "Search symbols and functions", autocomplete: "off",
                              spellcheck: "false", "aria-label": "Search symbols and functions" });
  const strip = el("div", { class: "lib-tabs", role: "tablist" });
  const body = el("div", { class: "lib-body" });
  const info = el("div", { class: "lib-info" });
  panel.dataset.keepsEdit = "";             // typing in the search field does not end an edit
  panel.replaceChildren(el("div", { class: "lib-top" }, field, strip), body, info);

  const items = new WeakMap();              // key or row → {text, shown, said}
  let current = stored() || KEYS;
  let hits = [];
  let chosen = 0;

  function stored() {
    try { return localStorage.getItem("edsl-palette-tab"); } catch (error) { return null; }
  }

  /** A key that inserts `text`; `look` is {text, shift, style} (see library.face). */
  function key(text, look, said) {
    const button = el("button", { type: "button", class: look.style === "glyph" ? null : look.style },
                      look.shift ? el("span", { class: look.shift, text: look.text }) : look.text);
    items.set(button, { text, shown: look.text, said });
    return button;
  }

  function keysTab() {
    return PALETTE.flatMap((group) => {
      const shift = group.raised ? "raised" : group.lowered ? "lowered" : "";
      return [
        el("h4", { text: group.title }),
        el("div", { class: "keys" + (group.wide ? " wide" : "") }, group.keys.map((glyph, i) =>
          key(glyph, { text: group.faces ? group.faces[i] : glyph, shift, style: "glyph" },
              about(glyph) || noteFor(glyph)))),
      ];
    });
  }

  function libraryTab(tab) {
    return tab.sections.flatMap((section) => [
      section.heading.trim() ? el("h4", { text: section.heading }) : null,
      el("div", { class: "chips" }, section.entries.map((entry) =>
        key(entry.text, face(entry), entry.detail || entry.desc || ""))),
    ]);
  }

  function found() {
    if (!hits.length) return [el("p", { class: "none", text: `Nothing found for “${field.value.trim()}”.` })];
    return hits.map((hit, i) => {
      const look = face(hit.entry);
      const row = el("button", { type: "button", class: "hit" + (i === chosen ? " on" : "") },
                     el("span", { class: "t " + look.style },
                        look.shift ? el("span", { class: look.shift, text: look.text }) : look.text),
                     hit.said ? el("span", { class: "d", text: hit.said }) : null,
                     el("span", { class: "w", text: [hit.tab, hit.section].filter((s) => s.trim()).join(" › ") }));
      items.set(row, { text: hit.entry.text, shown: look.text, said: hit.entry.detail || hit.said });
      return row;
    });
  }

  function draw() {
    const searching = Boolean(field.value.trim());
    const names = [KEYS, ...tabs().map((tab) => tab.name), ...TOOLS];
    if (!names.includes(current)) current = KEYS;
    strip.replaceChildren(...names.map((name) =>
      el("button", { type: "button", role: "tab", dataset: { tab: name }, text: name,
                     class: [!searching && name === current ? "on" : "", TOOLS.includes(name) ? "tool" : ""].join(" ") })));
    const tab = tabs().find((t) => t.name === current);
    const tool = searching ? null : tools[current];
    body.replaceChildren(...(searching ? found() : tool ? [tool.pane] : tab ? libraryTab(tab) : keysTab()).filter(Boolean));
    body.classList.toggle("found", searching);
    if (!searching) body.scrollTop = 0;
    if (tool) tool.shown();
    tell(null);
  }

  /** The status line: what the entry under the pointer (or chosen in a search) is. */
  function tell(item) {
    if (!item) { info.replaceChildren(el("div", { class: "said", text: IDLE })); return; }
    const names = namesFor(item.text).join("  ");
    info.replaceChildren(
      el("div", { class: "what" }, el("b", { text: item.shown }), names ? el("span", { text: names }) : null),
      item.said ? el("div", { class: "said", text: item.said }) : null);
  }

  function put(item) {
    insert(item.text, slot(item.text));
  }

  function choose(index) {
    chosen = Math.max(0, Math.min(hits.length - 1, index));
    const rows = body.querySelectorAll(".hit");
    rows.forEach((row, i) => row.classList.toggle("on", i === chosen));
    if (rows[chosen]) {
      rows[chosen].scrollIntoView({ block: "nearest" });
      tell(items.get(rows[chosen]));
    }
  }

  panel.addEventListener("pointerdown", (event) => {
    // The search field and the tools' own fields take the keyboard …
    if (event.target.closest("input, select, math-field")) return;
    if (event.target === body && event.offsetX >= body.clientWidth) return;     // the scroll bar
    event.preventDefault();                               // … elsewhere the editor keeps it
    const tab = event.target.closest("[data-tab]");
    if (tab) {
      current = tab.dataset.tab;
      field.value = "";
      try { localStorage.setItem("edsl-palette-tab", current); } catch (error) { /* private mode */ }
      draw();
      return;
    }
    const item = items.get(event.target.closest("button"));
    if (item) put(item);
  });
  body.addEventListener("pointerover", (event) => {
    const item = items.get(event.target.closest("button"));
    if (item) tell(item);
  });
  body.addEventListener("pointerleave", () => {
    const row = body.querySelector(".hit.on");
    tell(row ? items.get(row) : null);
  });

  field.addEventListener("input", () => {
    hits = search(field.value);
    chosen = 0;
    draw();
    if (hits.length) choose(0);
  });
  field.addEventListener("keydown", (event) => {
    const { key: pressed } = event;
    if (pressed === "ArrowDown" || pressed === "ArrowUp") {
      event.preventDefault();
      choose(chosen + (pressed === "ArrowDown" ? 1 : -1));
    } else if (pressed === "Enter") {
      event.preventDefault();
      const row = body.querySelectorAll(".hit")[chosen];
      if (row) put(items.get(row));
    } else if (pressed === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (field.value) { field.value = ""; hits = []; draw(); } else refocus();
    }
  });

  draw();
  return {
    /** Take the keyboard for a search (the sheet's open editor stays open). */
    focusSearch() { field.focus(); field.select(); },
    /** Draw again — the library has arrived. */
    refresh: draw,
  };
}
