// The page: regions placed freely on it, the cursor, selection, dragging.
//
// A region has a stored position (x, y in CSS px from the top-left of
// the printable area) and a displayed one.  They differ only while a
// region above has grown into it: `layout()` then pushes it down, and
// once a calculation has settled the pushed position is kept.  Regions
// are calculated in reading order of their stored positions — top to
// bottom, left to right — exactly as they are seen.
//
// A notebook that was never laid out has no stored positions: its
// regions *flow*, one under the other in file order, until the user
// places something (`freeze()`).

import { $, clamp, el, snap, uid } from "./util.js";
import { SourceEditor } from "./editor.js";
import { renderMarkdown } from "./markdown.js";
import { notebookOutputs, outputsNode, renderItems, renderSource } from "./mathview.js";
import { blockPadding } from "./textaids.js";

// Page geometry — mirrored in css/worksheet.css.
export const CONTENT_W = 680;          // A4 less 15 mm margins, at 96 dpi
export const PAGE_H = 1009;
const GRID = 8;
const ROW = 24;                         // one "line" for the cursor
const GAP = 8;                          // clearance kept between regions
const DRAG_START = 4;

class Region {
  constructor(data) {
    this.id = data.id || uid();
    this.kind = data.kind;
    this.x = data.x ?? null;
    this.y = data.y ?? null;
    this.w = data.w ?? null;
    this.source = data.source ?? "";
    this.saved = data.saved || null;    // outputs stored in the file (shown until calculated)
    this.result = null;                 // {items, tail, rev} from the kernel
    this.typeset = null;                // {source, items}: typeset without results
    this.stale = false;
    this.el = null;
    this.body = null;
    this.dx = 0;                        // displayed position and size
    this.dy = 0;
    this.h = 0;
    this.wpx = 0;
    this.rank = 0;                      // ties in reading order: newer first
  }
}

export class Sheet {
  /**
   * @param {object} hooks
   *   changed({calc})          the document was modified
   *   preview(region, source)  typeset this source (reply via setTypeset)
   *   highlight                (text, caret) => html
   *   settings()               {showResults, grid}
   */
  constructor(hooks) {
    this.hooks = hooks;
    this.desk = $("#desk");
    this.paper = $("#paper");
    this.content = $("#content");
    this.crossEl = $("#cross");
    this.band = $("#band");
    this.sink = $("#sink");
    this.regions = [];
    this.byEl = new WeakMap();
    this.selected = new Set();
    this.editing = null;                // {region, editor, original, fresh}
    this.cross = { x: 0, y: 0 };
    this.zoom = 1;
    this.flow = false;                  // regions without stored positions: stack them
    this.calculated = true;             // false: show typeset form only (untrusted sheet)
    this.history = [];
    this.future = [];
    this.rankCounter = 0;
    this.press = null;
    this.breaks = [];
    this.bind();
  }

  // ====================================================================
  // document
  // ====================================================================
  load(doc) {
    this.commitEdit();
    for (const region of this.regions) region.el.remove();
    this.regions = (doc.regions || []).map((data) => new Region(data));
    this.flow = this.regions.some((r) => r.y === null || r.x === null);
    this.selected.clear();
    this.history = [];
    this.future = [];
    for (const region of this.regions) this.mount(region);
    this.layout();
    this.desk.scrollTop = 0;
    this.crossAtEnd();
  }

  /** Park the cursor below the last region — until the user places it. */
  crossAtEnd() {
    this.placeCross(0, this.regions.length ? this.bottom() + GAP + GRID / 2 : 0, { scroll: false });
    this.crossParked = true;
  }

  /** Regions in reading order. */
  order() {
    if (this.flow) return this.regions.slice();
    return this.regions.slice().sort((a, b) => a.y - b.y || a.x - b.x || a.rank - b.rank);
  }

  mathRegions() {
    return this.order().filter((r) => r.kind === "math");
  }

  find(id) {
    return this.regions.find((r) => r.id === id) || null;
  }

  bottom() {
    return this.regions.reduce((max, r) => Math.max(max, r.dy + r.h), 0);
  }

  /** The document as it will be saved. */
  serialize(mode) {
    return this.order().map((r) => ({
      id: r.id, kind: r.kind, source: r.source,
      x: r.x, y: r.y, w: r.kind === "text" ? r.w : null,
      outputs: r.kind === "math" && r.result ? notebookOutputs(r.result, mode) : [],
    }));
  }

  // ---- undo ------------------------------------------------------------
  snapshot() {
    return JSON.stringify(this.regions.map((r) => (
      { id: r.id, kind: r.kind, x: r.x, y: r.y, w: r.w, source: r.source, rank: r.rank })));
  }

  /** Call before a change the user may want to take back. */
  checkpoint() {
    const state = this.snapshot();
    if (this.history[this.history.length - 1] !== state) this.history.push(state);
    if (this.history.length > 200) this.history.shift();
    this.future = [];
  }

  restore(state) {
    this.commitEdit();
    const known = new Map(this.regions.map((r) => [r.id, r]));
    const next = [];
    for (const data of JSON.parse(state)) {
      let region = known.get(data.id);
      if (region && region.kind === data.kind) {
        known.delete(data.id);
        if (region.source !== data.source) { region.source = data.source; region.typeset = null; region.result = null; }
        Object.assign(region, { x: data.x, y: data.y, w: data.w, rank: data.rank });
        this.draw(region);
      } else {
        region = new Region(data);
        region.rank = data.rank;
        this.mount(region);
      }
      next.push(region);
    }
    for (const gone of known.values()) gone.el.remove();
    this.regions = next;
    this.flow = next.some((r) => r.y === null || r.x === null);
    this.selected.clear();
    this.layout();
    this.hooks.changed({ calc: true });
  }

  undo() {
    if (!this.history.length) return;
    this.future.push(this.snapshot());
    this.restore(this.history.pop());
  }

  redo() {
    if (!this.future.length) return;
    this.history.push(this.snapshot());
    this.restore(this.future.pop());
  }

  // ====================================================================
  // drawing regions
  // ====================================================================
  mount(region) {
    region.body = el("div", { class: "body" });
    region.el = el("div", { class: "region " + region.kind, dataset: { id: region.id } },
                   el("div", { class: "grip", title: "Drag to move" }), region.body);
    if (region.kind === "text") region.el.append(el("div", { class: "sizer", title: "Drag to change the width" }));
    this.byEl.set(region.el, region);
    this.content.append(region.el);
    this.draw(region);
  }

  draw(region) {
    if (this.editing && this.editing.region === region) return;
    const node = region.el;
    if (region.kind === "text") {
      region.body.replaceChildren(renderMarkdown(region.source));
      node.classList.remove("has-error");
      return;
    }
    const settings = this.hooks.settings();
    const options = { mode: settings.showResults, highlight: this.hooks.highlight };
    const current = region.result && region.result.source === region.source ? region.result : null;
    if (current && this.calculated) {
      renderItems(region.body, current.items, { ...options, tail: current.tail });
    } else if (region.typeset && region.typeset.source === region.source) {
      renderItems(region.body, region.typeset.items, { ...options, results: false });
    } else if (region.result && this.calculated) {
      // Edited since it was calculated: keep the old picture, dimmed,
      // rather than flash raw source for the few milliseconds it takes.
      renderItems(region.body, region.result.items, { ...options, tail: region.result.tail });
    } else {
      renderSource(region.body, region.source, this.hooks.highlight);
    }
    if (!this.calculated && region.saved && region.saved.length) {
      region.body.append(outputsNode(region.saved));
    }
    const items = (current || region.typeset || {}).items || [];
    node.classList.toggle("has-error", items.some((item) => item.error));
    node.classList.toggle("stale", region.stale);
  }

  redrawAll() {
    for (const region of this.regions) this.draw(region);
    this.layout();
  }

  setResult(id, result, source) {
    const region = this.find(id);
    if (!region || region.source !== source) return;      // edited meanwhile
    region.result = { ...result, source };
    this.setStale(region, false);
    this.draw(region);
  }

  setTypeset(id, items, source) {
    const region = this.find(id);
    if (!region) return;
    if (this.editing && this.editing.region === region) {
      this.showPreview(items, source);
      return;
    }
    if (region.source !== source) return;
    region.typeset = { source, items };
    if (!region.result || region.result.source !== source) this.draw(region);
  }

  setStale(region, stale) {
    region.stale = stale;
    region.el.classList.toggle("stale", stale);
  }

  // ====================================================================
  // layout
  // ====================================================================
  measure(region) {
    const rect = region.el.getBoundingClientRect();
    region.h = rect.height / this.zoom;
    region.wpx = rect.width / this.zoom;
  }

  /** Keep a region whole on one printed page where it fits on one. */
  static offBreak(y, h) {
    const page = Math.floor(y / PAGE_H);
    return h <= PAGE_H && y + h > (page + 1) * PAGE_H ? (page + 1) * PAGE_H : y;
  }

  layout() {
    for (const region of this.regions) {
      const x = region.x ?? 0;
      if (region.kind === "text") {
        region.el.style.width = `${Math.max(60, region.w ?? CONTENT_W - x)}px`;
      } else {
        // A formula longer than the rest of the line wraps at the page
        // edge (at an operator) rather than running off the paper.
        region.el.style.maxWidth = `${Math.max(260, CONTENT_W - x)}px`;
      }
      this.measure(region);
    }
    if (this.flow) {
      let y = 0;
      for (const region of this.regions) {
        region.dx = 0;
        region.dy = Sheet.offBreak(y, region.h);
        y = snap(region.dy + region.h + GAP + GRID / 2, GRID);
      }
    } else {
      const placed = [];
      for (const region of this.order()) {
        let y = region.y;
        for (let moved = true; moved;) {
          moved = false;
          for (const other of placed) {
            if (region.x < other.dx + other.wpx && other.dx < region.x + region.wpx
                && y < other.dy + other.h + GAP && other.dy < y + region.h + GAP) {
              y = snap(other.dy + other.h + GAP + GRID / 2, GRID);
              moved = true;
            }
          }
          const clear = Sheet.offBreak(y, region.h);
          if (clear !== y) { y = clear; moved = true; }
        }
        region.dx = region.x;
        region.dy = y;
        placed.push(region);
      }
    }
    for (const region of this.regions) {
      if (this.press && this.press.dragging && this.selected.has(region)) continue;
      region.el.style.left = `${region.dx}px`;
      region.el.style.top = `${region.dy}px`;
    }
    const anchor = this.crossBelow;
    if (anchor && this.regions.includes(anchor) && !this.editing) {
      this.cross = { x: anchor.dx, y: Sheet.under(anchor) };
      this.drawCross();
    }
    this.drawBreaks();
  }

  drawBreaks() {
    const pages = Math.max(1, Math.ceil((Math.max(this.bottom(), this.cross.y) + 200) / PAGE_H));
    this.content.style.minHeight = `${pages * PAGE_H}px`;
    while (this.breaks.length > pages - 1) this.breaks.pop().remove();
    while (this.breaks.length < pages - 1) {
      const n = this.breaks.length + 1;
      const line = el("div", { class: "pagebreak" }, el("span", { text: `page ${n + 1}` }));
      line.style.top = `${n * PAGE_H}px`;
      this.content.prepend(line);
      this.breaks.push(line);
    }
  }

  /**
   * After a calculation has settled: keep the positions regions were
   * pushed to.  Returns true when that changed the reading order (so:
   * calculate again).  A sheet that still flows has nothing to keep.
   */
  settle() {
    this.layout();
    let reordered = false;
    // (Not while a region is open: its editor may be pushing regions
    // that will be back in place the moment it closes.)
    if (!this.flow && !this.editing) {
      const before = this.order().map((r) => r.id).join();
      for (const region of this.regions) {
        region.x = region.dx;
        region.y = region.dy;
      }
      reordered = this.order().map((r) => r.id).join() !== before;
    }
    if (this.crossParked && !this.editing && !this.selected.size) this.crossAtEnd();
    return reordered;
  }

  /**
   * Give every region the position it is shown at.  A notebook that was
   * never laid out flows like a document — one region under the other —
   * and stays that way until the user places something; from then on
   * regions stay where they are put.
   */
  freeze() {
    if (!this.flow) return;
    this.layout();
    for (const region of this.regions) {
      region.x = region.dx;
      region.y = region.dy;
    }
    this.flow = false;
  }

  setZoom(zoom) {
    this.zoom = clamp(Math.round(zoom * 20) / 20, 0.5, 3);
    this.paper.style.zoom = this.zoom;
    this.layout();
    return this.zoom;
  }

  // ====================================================================
  // cursor and selection
  // ====================================================================
  point(event) {
    const rect = this.content.getBoundingClientRect();
    return { x: (event.clientX - rect.left) / this.zoom, y: (event.clientY - rect.top) / this.zoom };
  }

  /**
   * Put the cursor at (x, y).  With `below`, it belongs under that
   * region: when the region grows (its result or plot arrives a moment
   * after Enter) the cursor moves down with its lower edge.
   */
  placeCross(x, y, { scroll = true, below = null } = {}) {
    this.crossParked = false;
    this.crossBelow = below;
    this.cross = { x: clamp(snap(x, GRID), 0, CONTENT_W - GRID), y: Math.max(0, snap(y, GRID)) };
    this.drawCross();
    this.crossEl.hidden = this.selected.size > 0;
    this.drawBreaks();
    this.focus();
    if (scroll) this.reveal(this.crossEl, 60);
  }

  drawCross() {
    this.crossEl.style.left = `${this.cross.x}px`;
    this.crossEl.style.top = `${this.cross.y}px`;
    this.sink.style.left = `${this.cross.x}px`;
    this.sink.style.top = `${this.cross.y}px`;
  }

  static under(region) {
    return snap(region.dy + region.h + GAP + GRID / 2, GRID);
  }

  focus() {
    if (!this.editing) this.sink.focus({ preventScroll: true });
  }

  reveal(node, margin = 30) {
    const box = node.getBoundingClientRect();
    const view = this.desk.getBoundingClientRect();
    if (box.top < view.top + margin) this.desk.scrollTop -= view.top + margin - box.top;
    else if (box.bottom > view.bottom - margin) this.desk.scrollTop += box.bottom - (view.bottom - margin);
  }

  select(regions, { add = false } = {}) {
    if (!add) {
      for (const region of this.selected) region.el.classList.remove("selected");
      this.selected.clear();
    }
    for (const region of regions) {
      this.selected.add(region);
      region.el.classList.add("selected");
    }
    this.crossEl.hidden = this.selected.size > 0;
  }

  toggle(region) {
    if (this.selected.delete(region)) region.el.classList.remove("selected");
    else { this.selected.add(region); region.el.classList.add("selected"); }
    this.crossEl.hidden = this.selected.size > 0;
  }

  // ====================================================================
  // editing
  // ====================================================================
  edit(region, { line = null, fresh = false } = {}) {
    this.commitEdit();
    this.select([]);
    let caret;
    if (line !== null) {
      const lines = region.source.split("\n");
      caret = lines.slice(0, line).join("\n").length;      // end of that source line
    }
    const editor = new SourceEditor({
      kind: region.kind,
      value: region.source,
      highlight: this.hooks.highlight,
      onCommit: (value, how) => this.finishEdit(value, how),
      onCancel: () => this.cancelEdit(),
      onInput: (value) => this.typing(value),
    });
    this.editing = { region, editor, original: region.source, fresh, preview: null, items: null };
    // Moving the caret to another line shows that line's statement.
    const follow = () => {
      const editing = this.editing;
      if (editing && editing.editor === editor && editing.items) this.showPreview(editing.items, editor.value);
    };
    editor.area.addEventListener("keyup", follow);
    editor.area.addEventListener("pointerup", follow);
    region.el.classList.add("editing");
    region.el.classList.remove("has-error", "stale");
    region.body.replaceChildren(editor.el);
    this.crossEl.hidden = true;
    editor.focus(caret);
    this.layout();
    this.reveal(region.el);
    if (region.kind === "math" && region.source.trim()) this.typing(region.source);
  }

  /** Source as it will be kept: "x =" (Mathcad's way to ask for a value)
   *  is the bare "x" here, and trailing blanks go. */
  static canonical(value) {
    return value.replace(/\xA0/g, " ").replace(/\s+$/, "").replace(/\s*(?<![:=<>!])=$/, "");
  }

  typing(value) {
    const editing = this.editing;
    if (!editing) return;
    this.measure(editing.region);
    if (editing.region.kind === "math") {
      if (editing.preview) editing.preview.classList.add("stale");
      const source = Sheet.canonical(value);
      if (source.trim()) this.hooks.preview(editing.region, source);
      else this.showPreview([], source);
    } else {
      this.layout();
    }
  }

  /** The typeset form of the statement the caret is in, under the editor. */
  showPreview(items, source) {
    const editing = this.editing;
    if (!editing || Sheet.canonical(editing.editor.value) !== source) return;
    editing.items = items;
    editing.itemsSource = source;
    const area = editing.editor.area;
    const line = source.slice(0, area.selectionStart).split("\n").length;
    const statements = items.filter((item) => item.t === "stmt");
    const here = statements.filter((item) => item.lines[0] <= line && line <= item.lines[1]);
    const shown = here.length ? here : statements.filter((item) => item.lines[1] < line).slice(-1);
    if (!shown.some((item) => item.latex || item.error)) {
      if (editing.preview) { editing.preview.remove(); editing.preview = null; }
      return;
    }
    if (!editing.preview) {
      editing.preview = el("div", { class: "preview" });
      editing.region.el.append(editing.preview);
    }
    const box = el("div");
    renderItems(box, shown, { mode: "explicit", highlight: this.hooks.highlight, results: false });
    for (const item of shown) {
      if (item.error) box.append(el("div", { class: "perr", text: `${item.error.ename}: ${item.error.evalue}` }));
    }
    editing.preview.classList.remove("stale");
    editing.preview.replaceChildren(box);
  }

  commitEdit() {
    if (this.editing) this.editing.editor.commit("blur");
  }

  finishEdit(value, how) {
    const editing = this.editing;
    if (!editing) return;
    const region = editing.region;
    this.editing = null;
    region.el.classList.remove("editing");
    if (editing.preview) editing.preview.remove();
    const source = region.kind === "math" ? Sheet.canonical(value) : value.replace(/\s+$/, "");
    const below = { x: region.x ?? region.dx, y: region.dy };
    if (!source.trim()) {
      if (!editing.fresh) this.pushHistory(editing);
      this.remove(region);
      this.layout();
      this.placeCross(below.x, below.y);
      if (!editing.fresh) this.hooks.changed({ calc: region.kind === "math" });
      return;
    }
    const changed = source !== editing.original;
    if (changed) {
      this.pushHistory(editing);
      region.source = source;
      if (region.kind === "math") {
        this.setStale(region, true);
        // Until its result arrives the region shows the preview just
        // typeset for this text, or failing that its previous picture
        // (dimmed) — but not for long: an old formula must not stand in
        // for a new one while a slow calculation runs.
        if (editing.items && editing.itemsSource === source) region.typeset = { source, items: editing.items };
        setTimeout(() => this.showAsSource(region, source), 300);
      }
    }
    this.draw(region);
    this.layout();
    if (how === "enter" || how === "down") {
      this.placeCross(region.dx, Sheet.under(region), { below: region });
    } else if (how === "up") {
      this.placeCross(region.dx, Math.max(0, region.dy - ROW));
    } else if (how === "escape") {
      this.select([region]);
      this.focus();
    }
    if (changed) this.hooks.changed({ calc: region.kind === "math" });
  }

  /** Show a region's text as text, if nothing typeset for it has come yet. */
  showAsSource(region, source) {
    if (!this.regions.includes(region) || region.source !== source) return;
    if (this.editing && this.editing.region === region) return;
    const known = (region.result && region.result.source === source)
      || (region.typeset && region.typeset.source === source);
    if (known) return;
    renderSource(region.body, source, this.hooks.highlight);
    this.layout();
  }

  /** History entry for an edit: the sheet as it was before the edit began. */
  pushHistory(editing) {
    const region = editing.region;
    const now = region.source;
    region.source = editing.original;
    const state = editing.fresh
      ? JSON.stringify(JSON.parse(this.snapshot()).filter((r) => r.id !== region.id))
      : this.snapshot();
    region.source = now;
    if (this.history[this.history.length - 1] !== state) this.history.push(state);
    this.future = [];
  }

  cancelEdit() {
    const editing = this.editing;
    if (!editing) return;
    this.editing = null;
    const region = editing.region;
    region.el.classList.remove("editing");
    if (editing.preview) editing.preview.remove();
    if (editing.fresh) {
      this.remove(region);
      this.layout();
      this.placeCross(region.x, region.y);
      return;
    }
    this.draw(region);
    this.layout();
    this.select([region]);
    this.focus();
  }

  /**
   * Text for the active editor, or a new math region at the cursor.
   * `caret` (an offset into the text) is where typing goes on.
   */
  insertText(text, caret = text.length) {
    if (this.editing) {
      this.editing.editor.insert(text, caret);
      this.editing.editor.area.focus({ preventScroll: true });
    } else {
      this.create("math", text, caret);
    }
  }

  /**
   * Markdown for the text region being written — or, when none is, for a
   * new one at the cursor.  A `block` (a table, a displayed formula) is
   * set apart from the text around it by blank lines; `select`, as
   * [offset, length] in the text, is what is left selected to type over.
   */
  insertMarkdown(text, { block = false, select = null } = {}) {
    if (!this.editing || this.editing.region.kind !== "text") this.create("text");
    const { editor } = this.editing;
    const area = editor.area;
    const { lead, tail } = block
      ? blockPadding(area.value.slice(0, area.selectionStart), area.value.slice(area.selectionEnd))
      : { lead: "", tail: "" };
    if (select) editor.insert(lead + text + tail, lead.length + select[0], select[1]);
    else editor.insert(lead + text + tail, lead.length + text.length);
    area.focus({ preventScroll: true });
  }

  /** The text region being written, and where its caret is — or null. */
  textCaret() {
    const editing = this.editing;
    if (!editing || editing.region.kind !== "text") return null;
    return { value: editing.editor.area.value, caret: editing.editor.area.selectionStart };
  }

  /** Put `text` in place of [start, end) of the text region being written. */
  replaceText(start, end, text) {
    const { editor } = this.editing;
    editor.area.setSelectionRange(start, end);
    editor.insert(text);
    editor.area.focus({ preventScroll: true });
  }

  /** Give the keyboard back to the sheet: the open editor, or the cursor. */
  refocus() {
    if (this.editing) this.editing.editor.area.focus({ preventScroll: true });
    else this.focus();
  }

  create(kind, text = "", caret = text.length) {
    this.commitEdit();
    this.freeze();
    const region = new Region({ kind, x: this.cross.x, y: this.cross.y, source: "" });
    region.rank = -(++this.rankCounter);
    if (kind === "text") region.w = Math.max(160, CONTENT_W - region.x);
    this.regions.push(region);
    this.mount(region);
    this.edit(region, { fresh: true });
    if (text) this.editing.editor.insert(text, caret);
  }

  remove(region) {
    region.el.remove();
    this.regions = this.regions.filter((r) => r !== region);
    this.selected.delete(region);
  }

  // ====================================================================
  // operations on the selection
  // ====================================================================
  deleteSelected() {
    if (!this.selected.size) return;
    this.checkpoint();
    const first = this.order().find((r) => this.selected.has(r));
    const math = [...this.selected].some((r) => r.kind === "math");
    for (const region of [...this.selected]) this.remove(region);
    this.layout();
    this.placeCross(first.dx, first.dy);
    this.hooks.changed({ calc: math });
  }

  nudge(dx, dy) {
    if (!this.selected.size) return;
    this.checkpoint();
    this.move(() => {
      for (const region of this.selected) {
        region.x = clamp(region.dx + dx, 0, CONTENT_W - GRID);
        region.y = Math.max(0, region.dy + dy);
        region.rank = -(++this.rankCounter);
      }
    });
  }

  /** Reposition regions via `mutate`; recalculate if that changed the
   *  order in which the math regions are read. */
  move(mutate) {
    this.freeze();
    const key = () => this.mathRegions().map((r) => r.id).join();
    const before = key();
    mutate();
    this.layout();
    this.hooks.changed({ calc: key() !== before });
  }

  /** Open (dy > 0) or close (dy < 0) vertical space at the cursor. */
  shiftBelow(dy) {
    const y0 = this.cross.y;
    this.freeze();
    const below = this.regions.filter((r) => r.y >= y0);
    if (!below.length) return;
    if (dy < 0) {
      const room = Math.min(...below.map((r) => r.y)) - y0;
      const blocked = this.regions.some((r) => r.y < y0 && r.dy + r.h + GAP > y0 + room + dy);
      if (room + dy < 0 || blocked) return;
    }
    this.checkpoint();
    for (const region of below) region.y = Math.max(0, region.y + dy);
    this.layout();
    this.hooks.changed({ calc: false });
  }

  align(axis) {
    if (this.selected.size < 2) return;
    this.checkpoint();
    this.move(() => {
      const group = this.order().filter((r) => this.selected.has(r));
      if (axis === "row") {
        const y = Math.min(...group.map((r) => r.dy));
        let x = Math.min(...group.map((r) => r.dx));
        for (const region of group.sort((a, b) => a.dx - b.dx)) {
          region.y = y;
          region.x = Math.max(region.dx, x);
          x = snap(region.x + region.wpx + 2 * GAP + GRID / 2, GRID);
        }
      } else {
        const x = Math.min(...group.map((r) => r.dx));
        for (const region of group) { region.x = x; region.y = region.dy; }
      }
    });
  }

  /** One region per statement, stacked where the region stood. */
  split() {
    const targets = [...this.selected].filter((r) => r.kind === "math" && r.result
      && r.result.source === r.source && r.result.items.length > 1);
    if (!targets.length) return false;
    this.checkpoint();
    this.freeze();
    for (const region of targets) {
      const lines = region.source.split("\n");
      // The parts start one below the other, in order; layout then
      // spaces them out and settle() keeps them where they land.
      const made = region.result.items.map((item, i) => new Region({
        kind: "math", x: region.dx, y: region.dy + i,
        source: lines.slice(item.lines[0] - 1, item.lines[1]).join("\n") }));
      this.remove(region);
      for (const part of made) { this.regions.push(part); this.mount(part); }
      this.select(made, { add: true });
    }
    this.settle();
    this.hooks.changed({ calc: true });
    return true;
  }

  selectAll() {
    this.commitEdit();
    this.select(this.regions);
    this.focus();
  }

  // ---- clipboard -------------------------------------------------------
  clip() {
    const group = this.order().filter((r) => this.selected.has(r));
    if (!group.length) return null;
    const x0 = Math.min(...group.map((r) => r.dx));
    const y0 = Math.min(...group.map((r) => r.dy));
    return {
      text: group.map((r) => r.source).join("\n\n"),
      json: JSON.stringify(group.map((r) => (
        { kind: r.kind, source: r.source, w: r.w, x: r.dx - x0, y: r.dy - y0 }))),
    };
  }

  pasteRegions(json) {
    let items;
    try { items = JSON.parse(json); } catch (error) { return; }
    if (!Array.isArray(items) || !items.length) return;
    this.checkpoint();
    this.freeze();
    const made = items.map((item, i) => {
      const region = new Region({
        kind: item.kind === "text" ? "text" : "math", source: String(item.source ?? ""),
        x: clamp(this.cross.x + (item.x || 0), 0, CONTENT_W - GRID), y: this.cross.y + (item.y || 0),
        w: item.w ?? null });
      region.rank = -(this.rankCounter + items.length - i);
      this.regions.push(region);
      this.mount(region);
      return region;
    });
    this.rankCounter += items.length;
    this.layout();
    this.select(made);
    this.hooks.changed({ calc: made.some((r) => r.kind === "math") });
  }

  // ====================================================================
  // pointer and keyboard
  // ====================================================================
  bind() {
    this.paper.addEventListener("pointerdown", (event) => this.pointerDown(event));
    window.addEventListener("pointermove", (event) => this.pointerMove(event));
    window.addEventListener("pointerup", (event) => this.pointerUp(event));
    this.desk.addEventListener("pointerdown", (event) => {
      if (event.target === this.desk) { this.commitEdit(); this.select([]); this.focus(); event.preventDefault(); }
    });
    this.sink.addEventListener("keydown", (event) => this.idleKey(event));
    this.sink.addEventListener("input", (event) => {
      if (event.isComposing) return;
      const text = this.sink.value;
      this.sink.value = "";
      if (!text.trim()) return;
      if (text === '"') this.create("text");
      else this.create("math", text.replace(/^\n+|\n+$/g, ""));
    });
    this.sink.addEventListener("compositionend", () => {
      const text = this.sink.value;
      this.sink.value = "";
      if (text) this.create("math", text);
    });
    for (const type of ["copy", "cut"]) {
      this.sink.addEventListener(type, (event) => {
        const clip = this.clip();
        if (!clip) return;
        event.clipboardData.setData("text/plain", clip.text);
        event.clipboardData.setData("application/x-edsl-regions", clip.json);
        this.lastClip = clip;
        event.preventDefault();
        if (type === "cut") this.deleteSelected();
      });
    }
    this.sink.addEventListener("paste", (event) => {
      // Regions travel in a format of their own; a browser that will not
      // carry it still hands back the plain text we put beside it.
      const text = event.clipboardData.getData("text/plain");
      const json = event.clipboardData.getData("application/x-edsl-regions")
        || (this.lastClip && this.lastClip.text === text ? this.lastClip.json : "");
      if (json) {
        event.preventDefault();
        this.pasteRegions(json);
      }                                    // plain text arrives as an input event
    });
  }

  pointerDown(event) {
    if (event.button !== 0) return;
    const node = event.target.closest(".region");
    const region = node ? this.byEl.get(node) : null;
    const point = this.point(event);
    const onGrip = Boolean(event.target.closest(".grip"));
    const onSizer = Boolean(event.target.closest(".sizer"));
    if (region && this.editing && this.editing.region === region && !onGrip && !onSizer) return;
    if (event.target.closest(".popup, .preview")) return;
    event.preventDefault();
    this.commitEdit();
    if (!region) {
      this.press = { kind: "band", start: point, moved: false };
      return;
    }
    if (!this.regions.includes(region)) return;          // emptied by the commit above
    if (onSizer) {
      this.press = { kind: "size", region, start: point, width: region.wpx, moved: false };
      return;
    }
    if (event.shiftKey || event.ctrlKey || event.metaKey) {
      if (event.target.closest("a[href]") && (event.ctrlKey || event.metaKey)) {
        window.open(event.target.closest("a[href]").href, "_blank", "noopener");
        return;
      }
      this.toggle(region);
      this.focus();
      return;
    }
    const rowNode = event.target.closest("[data-line]");
    this.press = { kind: "region", region, start: point, moved: false, dragging: false,
                   line: rowNode ? Number(rowNode.dataset.line) : null, grip: onGrip };
  }

  pointerMove(event) {
    const press = this.press;
    if (!press) return;
    const point = this.point(event);
    const dx = point.x - press.start.x, dy = point.y - press.start.y;
    if (!press.moved && Math.hypot(dx, dy) * this.zoom < DRAG_START) return;
    press.moved = true;
    if (press.kind === "band") {
      const x = Math.min(point.x, press.start.x), y = Math.min(point.y, press.start.y);
      Object.assign(this.band.style, { left: `${x}px`, top: `${y}px`,
                                       width: `${Math.abs(dx)}px`, height: `${Math.abs(dy)}px` });
      this.band.hidden = false;
      const hit = this.regions.filter((r) => r.dx < x + Math.abs(dx) && x < r.dx + r.wpx
        && r.dy < y + Math.abs(dy) && y < r.dy + r.h);
      this.select(hit);
    } else if (press.kind === "size") {
      const region = press.region;
      region.w = clamp(snap(press.width + dx, GRID), 80, CONTENT_W - region.dx);
      region.el.style.width = `${region.w}px`;
    } else {
      if (!press.dragging) {
        press.dragging = true;
        if (!this.selected.has(press.region)) this.select([press.region]);
        for (const region of this.selected) region.el.classList.add("dragging");
      }
      for (const region of this.selected) {
        region.el.style.left = `${region.dx + dx}px`;
        region.el.style.top = `${region.dy + dy}px`;
      }
    }
  }

  pointerUp(event) {
    const press = this.press;
    if (!press) return;
    this.press = null;
    const point = this.point(event);
    if (press.kind === "band") {
      this.band.hidden = true;
      if (!press.moved) {
        this.select([]);
        this.placeCross(point.x, point.y - 8);
      } else {
        this.focus();
      }
      return;
    }
    if (press.kind === "size") {
      if (press.moved) {
        const width = press.region.w;
        press.region.w = press.width;
        this.checkpoint();
        press.region.w = width;
        this.layout();
        this.hooks.changed({ calc: false });
      }
      return;
    }
    if (!press.dragging) {
      if (press.grip) { this.select([press.region]); this.focus(); }
      else this.edit(press.region, { line: press.line });
      return;
    }
    const dx = point.x - press.start.x, dy = point.y - press.start.y;
    this.checkpoint();
    this.move(() => {
      for (const region of this.selected) {
        region.el.classList.remove("dragging");
        region.x = clamp(snap(region.dx + dx, GRID), 0, CONTENT_W - GRID);
        region.y = Math.max(0, snap(region.dy + dy, GRID));
        region.rank = -(++this.rankCounter);
      }
    });
    this.focus();
  }

  idleKey(event) {
    const key = event.key;
    const mod = event.ctrlKey || event.metaKey;
    const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (arrows[key] && !mod) {
      const [ax, ay] = arrows[key];
      event.preventDefault();
      if (this.selected.size) this.nudge(ax * GRID, ay * GRID);
      else this.placeCross(this.cross.x + ax * 2 * GRID, this.cross.y + ay * ROW);
      return;
    }
    if (key === "Enter") {
      event.preventDefault();
      if (mod) this.shiftBelow(ROW);
      else if (this.selected.size === 1) this.edit([...this.selected][0]);
      else this.placeCross(this.cross.x, this.cross.y + ROW);
      return;
    }
    if (key === "Backspace" || key === "Delete") {
      event.preventDefault();
      if (this.selected.size) this.deleteSelected();
      else if (mod || key === "Backspace") {
        if (key === "Backspace" && !mod) this.placeCross(this.cross.x, this.cross.y - ROW);
        else this.shiftBelow(-ROW);
      }
      return;
    }
    if (key === "Escape") { this.select([]); this.placeCross(this.cross.x, this.cross.y, { scroll: false }); return; }
    if (key === "Home" && !mod) { event.preventDefault(); this.placeCross(0, this.cross.y); return; }
    if (key === "Tab") { event.preventDefault(); this.step(event.shiftKey ? -1 : 1); return; }
    if (mod && key.toLowerCase() === "a") { event.preventDefault(); this.selectAll(); }
  }

  /** Tab: edit the next (or previous) region in reading order. */
  step(direction) {
    const order = this.order();
    if (!order.length) return;
    const here = this.selected.size === 1 ? order.indexOf([...this.selected][0]) : -1;
    let index;
    if (here >= 0) index = here + direction;
    else {
      const after = order.findIndex((r) => r.dy > this.cross.y || (r.dy === this.cross.y && r.dx >= this.cross.x));
      index = direction > 0 ? (after < 0 ? order.length : after) : (after < 0 ? order.length : after) - 1;
    }
    if (index >= 0 && index < order.length) this.edit(order[index]);
  }
}
