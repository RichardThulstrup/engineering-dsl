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
//
// A picture is a text region whose text is one attached picture and
// nothing else (see pictures.js): it fills its region, so the region's
// width is the picture's, and a click selects it instead of opening its
// text.

import { $, clamp, el, snap, toast, uid } from "./util.js";
import { aligned, distributed, NEEDED } from "./arrange.js";
import { SourceEditor } from "./editor.js";
import { formatted, pastedText, RichEditor } from "./richtext.js";
import { renderMarkdown } from "./markdown.js";
import { notebookOutputs, outputsNode, renderItems, renderSource } from "./mathview.js";
import { bringsFiles, carried, collect, KINDS_SAID, lonePicture, named, reference } from "./pictures.js";

// Page geometry — mirrored in css/worksheet.css.
export const CONTENT_W = 680;          // A4 less 15 mm margins, at 96 dpi
export const PAGE_H = 1009;
const GRID = 8;
const ROW = 24;                         // one "line" for the cursor
const GAP = 8;                          // clearance kept between regions
const DRAG_START = 4;
const FRAME = 12;                       // a region is this much wider than what is in it

class Region {
  constructor(data) {
    this.id = data.id || uid();
    this.kind = data.kind;
    this.x = data.x ?? null;
    this.y = data.y ?? null;
    this.w = data.w ?? null;
    this.source = data.source ?? "";
    this.attachments = data.attachments || {};    // of a text region: {name: {type: base64}}
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
   *   selection(count)         so many regions are selected now
   *   preview(region, source)  typeset this source (reply via setTypeset)
   *   highlight                (text, caret) => html
   *   settings()               {showResults}
   *   prefs()                  {markdown}: text is written as its markdown, not as it looks
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
    // Pictures of every region there has been, by region: a snapshot for
    // undo holds a region's text, not the megabytes attached to it.
    this.attic = new Map();
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
    this.attic.clear();
    this.regions = (doc.regions || []).map((data) => new Region(data));
    this.flow = this.regions.some((r) => r.y === null || r.x === null);
    this.selected.clear();
    this.selectionChanged();
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
      // The pictures a text still names; one it has let go of is not kept.
      attachments: r.kind === "text" ? named(r.source, r.attachments) : undefined,
    }));
  }

  isPicture(region) {
    return region.kind === "text" && lonePicture(region.source) !== null;
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
        region = new Region({ ...data, attachments: this.attic.get(data.id) });
        region.rank = data.rank;
        this.mount(region);
      }
      next.push(region);
    }
    for (const gone of known.values()) gone.el.remove();
    this.regions = next;
    this.flow = next.some((r) => r.y === null || r.x === null);
    this.selected.clear();
    this.selectionChanged();
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
    this.attic.set(region.id, region.attachments);
    this.content.append(region.el);
    this.draw(region);
  }

  draw(region) {
    if (this.editing && this.editing.region === region) return;
    const node = region.el;
    if (region.kind === "text") {
      // Text is drawn again only when it has changed: a picture in it
      // would otherwise be read anew, and flicker, at every undo.
      if (region.drawn === region.source) return;
      region.drawn = region.source;
      region.body.replaceChildren(renderMarkdown(region.source, region.attachments));
      node.classList.remove("has-error");
      node.classList.toggle("picture", this.isPicture(region));
      // A picture has its size once it has been read: the page is laid out again then.
      for (const img of region.body.querySelectorAll("img")) {
        if (!img.complete) img.addEventListener("load", () => this.layout(), { once: true });
      }
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
        // Text without a width of its own reaches to the edge of the
        // page.  A picture without one (in a notebook from elsewhere) is
        // as wide as it is — at most that far — once it has been read.
        // (A picture may be small; text needs room for a word or two.)
        const picture = this.isPicture(region);
        const own = picture && region.w == null ? region.body.querySelector("img") : null;
        const wanted = region.w ?? (own ? Math.min(own.naturalWidth + FRAME, CONTENT_W - x) : CONTENT_W - x);
        region.el.style.width = `${Math.max(picture ? 3 * GRID : 60, wanted)}px`;
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
    // On paper the sheet ends with its last region (see the print rules
    // in worksheet.css): the room kept here for the cursor, and for
    // going on, would come out of the printer as an empty page.
    this.content.style.setProperty("--printed", `${this.bottom()}px`);
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
    this.selectionChanged();
  }

  toggle(region) {
    if (this.selected.delete(region)) region.el.classList.remove("selected");
    else { this.selected.add(region); region.el.classList.add("selected"); }
    this.selectionChanged();
  }

  selectionChanged() {
    this.crossEl.hidden = this.selected.size > 0;
    this.hooks.selection?.(this.selected.size);
  }

  // ====================================================================
  // editing
  // ====================================================================
  /**
   * Open a region for writing.  A math region is written as its text,
   * with the caret at the end of source line `line`; a text region as it
   * looks, with the caret at `point` of the window — unless text is to
   * be written as markdown (prefs().markdown).
   */
  edit(region, { line = null, fresh = false, point = null } = {}) {
    this.commitEdit();
    this.select([]);
    this.editing = { region, editor: null, original: region.source, fresh, preview: null, items: null };
    region.el.classList.add("editing");
    region.el.classList.remove("has-error", "stale");
    region.drawn = null;                 // the editor takes the place of what was drawn
    this.crossEl.hidden = true;
    if (region.kind === "text" && !this.hooks.prefs().markdown) {
      this.open(new RichEditor({
        value: region.source,
        pictures: region.attachments,
        onCommit: (value, how) => this.finishEdit(value, how),
        onInput: () => this.typing(""),
        onSource: (text, why) => { this.asMarkdown(text); if (why) toast(why, "error"); },
      })).focus(point);
    } else {
      let caret;
      if (line !== null) {
        const lines = region.source.split("\n");
        caret = lines.slice(0, line).join("\n").length;      // end of that source line
      }
      this.asMarkdown(region.source, caret);
    }
    this.layout();
    // (A text has its bar of tools above it: that is to be on the paper, and in view as well.)
    this.editing.editor.place?.();
    this.reveal(region.el, region.kind === "text" ? 30 + 44 * this.zoom : 30);
    if (region.kind === "math" && region.source.trim()) this.typing(region.source);
  }

  /** Put an editor into the region being written. */
  open(editor) {
    this.editing.editor = editor;
    this.editing.region.body.replaceChildren(editor.el);
    return editor;
  }

  /** Write the open region as its text: a math region always, a text region as markdown. */
  asMarkdown(value, caret) {
    const editor = this.open(new SourceEditor({
      kind: this.editing.region.kind,
      value,
      highlight: this.hooks.highlight,
      onCommit: (text, how) => this.finishEdit(text, how),
      onCancel: () => this.cancelEdit(),
      onInput: (text) => this.typing(text),
    }));
    // Moving the caret to another line shows that line's statement.
    const follow = () => {
      const editing = this.editing;
      if (editing && editing.editor === editor && editing.items) this.showPreview(editing.items, editor.value);
    };
    editor.area.addEventListener("keyup", follow);
    editor.area.addEventListener("pointerup", follow);
    editor.focus(caret);
    this.layout();
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
      this.editing.editor.refocus();
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
  insertMarkdown(text, options = {}) {
    if (!this.writing()) this.create("text");
    this.editing.editor.insertMarkdown(text, options);
  }

  /** The editor of the text region being written — or null. */
  writing() {
    return this.editing && this.editing.region.kind === "text" ? this.editing.editor : null;
  }

  /** In the text being written, the formula the caret is in (or that is selected) — or null. */
  formula() {
    return this.writing() ? this.writing().formula() : null;
  }

  /**
   * A formula into the text being written (or into a new text region):
   * in place of the one `found` when `replace`, beside it, or at the caret.
   */
  putFormula(latex, display, found, replace) {
    if (this.writing()) this.writing().putFormula(latex, display, found, replace);
    else this.insertMarkdown(display ? `$$${latex}$$` : `$${latex}$`, { block: display });
  }

  /** Give the keyboard back to the sheet: the open editor, or the cursor. */
  refocus() {
    if (this.editing) this.editing.editor.refocus();
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
    if (this.selected.delete(region)) this.selectionChanged();
  }

  // ====================================================================
  // pictures
  // ====================================================================
  /**
   * Take the pictures a clipboard, a drop or a file chooser `offer`s (as
   * `carried` describes them): into the text being written when `region`
   * is that text region, and otherwise onto the page.
   */
  async takePictures(offer, region = null) {
    // Where they go is settled now.  Reading them takes a moment — the
    // browser may even ask before it hands a drawing over — and the
    // cursor may be somewhere else by then.
    const writing = this.editing ? this.editing.region : null;
    const cursor = this.cross;
    const { made, problems, asked } = await collect(offer);
    if (problems.length) toast(problems[0], "error");
    else if (asked && made.length) {
      toast("Pasted as a bitmap. Allow this page to read the clipboard, and a drawing is pasted as a drawing.");
    } else if (asked) {
      toast("The drawing on the clipboard can be pasted once this page is allowed to read the clipboard.", "error");
    }
    if (!made.length) return;
    const editing = this.editing;
    if (region && editing && editing.region === region) {
      for (const picture of made) {
        region.attachments[picture.name] = picture.bundle;
        editing.editor.insertMarkdown(reference(picture));
      }
      return;
    }
    // Onto the page: where the cursor was — or, asked for while a region
    // was being written, below that region.
    this.commitEdit();
    const above = writing && this.regions.includes(writing) ? writing : null;
    this.addPictures(made, above ? { x: above.dx, y: Sheet.under(above) } : cursor);
  }

  /** Text pasted on the page as a text region of its own, at the cursor: left
   *  selected, as a pasted picture is, and the cursor waits below it. */
  addText({ text, pictures }) {
    this.checkpoint();
    this.freeze();
    const region = new Region({ kind: "text", x: this.cross.x, y: this.cross.y, w: Math.max(160, CONTENT_W - this.cross.x),
                                source: text, attachments: pictures });
    region.rank = -(++this.rankCounter);
    this.regions.push(region);
    this.mount(region);
    this.layout();
    this.placeCross(region.dx, Sheet.under(region), { scroll: false });
    this.select([region]);
    this.reveal(region.el);
    this.hooks.changed({ calc: false });
  }

  /** New pictures at a place on the page, one under the other and at
   *  their own size where the page has room for it; they are left selected. */
  addPictures(pictures, at) {
    this.checkpoint();
    this.freeze();
    // They start one below the other, in order; layout spaces them out
    // as they are read, and settle() keeps them where they land.
    const made = pictures.map((picture, i) => {
      const w = clamp(Math.round(picture.width) + FRAME, 3 * GRID, CONTENT_W);
      const region = new Region({
        kind: "text", w, x: Math.max(0, Math.min(at.x, CONTENT_W - w)), y: at.y + i,
        source: reference(picture), attachments: { [picture.name]: picture.bundle } });
      region.rank = -(++this.rankCounter);
      this.regions.push(region);
      this.mount(region);
      return region;
    });
    this.layout();
    // The cursor waits below them: typing goes on there.
    const last = made[made.length - 1];
    this.placeCross(last.dx, Sheet.under(last), { scroll: false });
    this.select(made);
    this.reveal(last.el);
    this.hooks.changed({ calc: false });
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

  /**
   * Arrange the selection: align it by a side or by its middles ("left",
   * "centre", "right", "top", "middle", "bottom"), or spread it out with
   * equal space between the regions ("across", "down").  One region alone
   * is aligned left, centre or right on the page.
   */
  arrange(how) {
    const spread = how === "across" || how === "down";
    if (this.selected.size < NEEDED[how]) return;
    this.checkpoint();
    this.move(() => {
      const group = this.order().filter((r) => this.selected.has(r));
      const boxes = group.map((r) => ({ x: r.dx, y: r.dy, w: r.wpx, h: r.h }));
      // (One pixel more than regions must keep between them: places are
      // whole pixels, heights are not.)
      const places = spread ? distributed(boxes, how === "across" ? "x" : "y", { gap: GAP + 1 })
        : aligned(boxes, how, { pageWidth: CONTENT_W, gap: GAP });
      group.forEach((region, i) => {
        // A text region without a width of its own reaches to the edge of
        // the page; moved sideways it keeps the width it is seen with.
        if (region.kind === "text" && region.w == null && places[i].x !== boxes[i].x) region.w = Math.round(boxes[i].w);
        region.x = clamp(places[i].x, 0, CONTENT_W - GRID);
        region.y = Math.max(0, places[i].y);
      });
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
      // For another program: text regions as formatted text, math regions as their text.
      html: group.map((r) => (r.kind === "text" ? formatted(r.source, r.attachments) : el("pre", { text: r.source }).outerHTML)).join("\n"),
      json: JSON.stringify(group.map((r) => (
        { kind: r.kind, source: r.source, w: r.w, x: r.dx - x0, y: r.dy - y0,
          attachments: r.kind === "text" ? named(r.source, r.attachments) : undefined }))),
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
        w: item.w ?? null,
        // (Shown only as far as they are pictures: see pictureUrl.)
        attachments: item.attachments && typeof item.attachments === "object" ? { ...item.attachments } : {} });
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
        event.clipboardData.setData("text/html", clip.html);
        event.clipboardData.setData("application/x-edsl-regions", clip.json);
        this.lastClip = clip;
        event.preventDefault();
        if (type === "cut") this.deleteSelected();
      });
    }
    this.paper.addEventListener("paste", (event) => this.paste(event));
    // A picture file let go of over the page lands where it is dropped;
    // let go of elsewhere in the window, it goes where a pasted one would.
    // Either way the browser must not show the file in the sheet's place.
    window.addEventListener("dragover", (event) => {
      if (bringsFiles(event.dataTransfer)) event.preventDefault();
    });
    window.addEventListener("drop", (event) => {
      if (!bringsFiles(event.dataTransfer)) return;
      event.preventDefault();
      const offer = carried(event.dataTransfer);
      if (!offer || !offer.files.length) {
        toast(`Only pictures can be dropped on the page: ${KINDS_SAID}.`, "error");
        return;
      }
      if (event.target.closest?.("#desk")) {
        this.commitEdit();
        this.select([]);
        const point = this.point(event);
        this.placeCross(clamp(point.x, 0, CONTENT_W), Math.max(0, point.y - 8), { scroll: false });
      }
      this.takePictures({ files: offer.files });
    });
  }

  /** Ctrl+V on the page or in a region being written. */
  paste(event) {
    const data = event.clipboardData;
    const editing = this.editing;
    const text = data.getData("text/plain");
    const asTyped = this.shiftPaste;                 // Ctrl+Shift+V: the words, whatever came with them
    this.shiftPaste = false;
    if (!editing) {
      // Regions travel in a format of their own; a browser that will not
      // carry it still hands back the plain text we put beside it.
      const json = data.getData("application/x-edsl-regions")
        || (this.lastClip && this.lastClip.text === text ? this.lastClip.json : "");
      if (json) {
        event.preventDefault();
        this.pasteRegions(json);
        return;
      }
    }
    // A picture: on the page it comes before any words the clipboard has
    // beside it (and words that are an SVG drawing are a picture); into a
    // text being written it goes only when there are no words to paste.
    const offer = carried(data);
    if (!offer && !editing && !asTyped) {
      // Formatted text from another program — a word processor, a web
      // page — is text, whatever its words would be as a calculation: a
      // text region of its own.  (Not code that was copied in an editor,
      // which is formatted as well: that is taken as it is typed.)
      const words = pastedText(data.getData("text/html"));
      if (words) {
        event.preventDefault();
        this.addText(words);
        return;
      }
    }
    if (!offer || (editing && text)) return;         // plain text arrives as an input event
    event.preventDefault();
    if (editing && editing.region.kind !== "text") {
      toast("A picture goes on the page or into a text region, not into a math region.");
      return;
    }
    this.takePictures(offer, editing ? editing.region : null);
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
      const least = this.isPicture(region) ? 3 * GRID : 80;
      region.w = clamp(snap(press.width + dx, GRID), least, CONTENT_W - region.dx);
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
      // A click on a picture selects it — there is no text in it to edit;
      // a double click opens the text that names it (for a caption, say).
      const again = this.clicked && this.clicked.region === press.region
        && event.timeStamp - this.clicked.at < 500;
      this.clicked = { region: press.region, at: event.timeStamp };
      if (press.grip || (this.isPicture(press.region) && !again)) { this.select([press.region]); this.focus(); }
      else this.edit(press.region, { line: press.line, point: { x: event.clientX, y: event.clientY } });
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
    if (mod && key.toLowerCase() === "v") this.shiftPaste = event.shiftKey;      // (the paste that follows asks)
  }

  /** Tab: edit the next (or previous) region in reading order — pictures are passed by. */
  step(direction) {
    const chosen = this.selected.size === 1 ? [...this.selected][0] : null;
    const order = this.order().filter((r) => r === chosen || !this.isPicture(r));
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
