// Worksheet front end: ties the page to the kernel and the file.

import { $, api, debounce, el, toast, uid } from "./util.js";
import { Conn } from "./conn.js";
import { Sheet } from "./sheet.js";
import { makeHighlighter } from "./highlight.js";
import { loadLibrary } from "./library.js";
import { buildPanel } from "./panel.js";
import { ask, browse, info } from "./dialogs.js";

const DEFAULTS = { showResults: "auto", grid: true };

const state = {
  path: null,               // relative to the server's folder; null = never saved
  name: "Untitled",
  mtime: null,
  trusted: true,            // false: a sheet not calculated on this machine yet
  preamble: null,
  settings: { ...DEFAULTS },
  dirty: false,
  kernel: "none",
  online: false,
  vocab: null,
  calc: null,               // the pass in flight: {seq, sources: Map(id → source)}
  renders: new Map(),       // seq → Map(id → source) of typesetting requests
  wantCalc: false,
  settling: 0,
  greet: false,
  lastPass: "",
  died: false,
};

let highlighter = makeHighlighter(null);

const sheet = new Sheet({
  highlight: (text, caret) => highlighter(text, caret),
  settings: () => state.settings,
  changed: ({ calc }) => {
    setDirty(true);
    if (calc) scheduleCalc();
  },
  preview: (region, source) => requestPreview(region, source),
});

const session = sessionStorage.getItem("edsl-session") || uid();
sessionStorage.setItem("edsl-session", session);

const conn = new Conn({
  session,
  path: () => state.path || "",
  onMessage: receive,
  onState: (online, code) => {
    state.online = online;
    if (online) {
      state.greet = true;               // say hello once the server reports the kernel
    } else {
      state.calc = null;
      setKernel(code === 4000 ? "moved" : code === 4403 ? "signedout" : "offline");
    }
  },
});

// ---------------------------------------------------------------------------
// status, title, banner
// ---------------------------------------------------------------------------

function setDirty(dirty) {
  state.dirty = dirty;
  $("#doc-dirty").hidden = !dirty;
  document.title = `${dirty ? "• " : ""}${state.name} — Worksheet`;
}

function setTitle() {
  $("#doc-name").textContent = state.name;
  $("#doc-title").title = state.path || "not saved yet";
  setDirty(state.dirty);
}

const STATUS_TEXT = {
  none: "Starting…", starting: "Starting the kernel…", idle: "Ready", busy: "Calculating…",
  dead: "Kernel stopped", offline: "Server not reachable", moved: "Open in another tab",
  signedout: "Not signed in",
};

/** Status line: the kernel's state and, when idle, how many regions failed. */
function showStatus(detail) {
  const box = $("#status");
  const kernel = state.kernel;
  box.dataset.state = kernel;
  let text = STATUS_TEXT[kernel] || kernel;
  if (kernel === "idle" && state.trusted) {
    const failed = document.querySelectorAll(".region.has-error").length;
    if (failed) text = `${failed} ${failed === 1 ? "region" : "regions"} with errors`;
    box.classList.toggle("has-errors", failed > 0);
  } else box.classList.remove("has-errors");
  $("#status-text").textContent = text;
  box.title = detail || (kernel === "idle" ? state.lastPass : "");
}

function setKernel(kernel, detail) {
  state.kernel = kernel;
  showStatus(detail);
  $('[data-cmd="stop"]').disabled = kernel !== "busy";
  if (kernel === "dead") {
    state.died = true;
    state.calc = null;
    banner("error", detail || "The kernel stopped.", [{ label: "Restart kernel", run: () => restartKernel() }]);
  } else if (kernel === "offline") {
    banner("error", "The worksheet server is not reachable. Start it again with  python -m utils.worksheet  " +
                    "— unsaved changes stay on this page until it is back.", []);
  } else if (kernel === "signedout") {
    // A restarted server has a new access token; the page it opens
    // signs the whole browser in again, this tab included.
    banner("error", "The worksheet server was restarted. Use the page it has just opened (or the address " +
                    "it printed) to sign this browser in again, then reconnect — nothing here is lost.",
           [{ label: "Reconnect", primary: true, run: () => conn.reopen() }]);
  } else if (kernel === "moved") {
    banner("error", "This worksheet session was opened in another tab.", [{ label: "Use it here", run: () => conn.reopen() }]);
  } else if ($("#banner").dataset.kind === "kernel") {
    banner(null);
  }
}

function banner(kind, text, actions = []) {
  const box = $("#banner");
  if (!kind) { box.hidden = true; box.dataset.kind = ""; return; }
  box.className = kind === "error" ? "error" : "";
  box.dataset.kind = kind === "trust" ? "trust" : "kernel";
  const message = text.includes("\n") ? el("pre", { text }) : el("span", { class: "msg", text });
  box.replaceChildren(message, ...actions.map((action) => {
    const button = el("button", { class: "btn" + (action.primary ? " primary" : ""), type: "button", text: action.label });
    button.addEventListener("click", action.run);
    return button;
  }));
  box.hidden = false;
}

function showTrustBanner() {
  banner("trust", "This worksheet has not been calculated on this computer yet. " +
                  "Calculating runs the code it contains.",
         [{ label: "Calculate", primary: true, run: () => { trust(); } }]);
}

function trust() {
  state.trusted = true;
  sheet.calculated = true;
  banner(null);
  sheet.redrawAll();
  scheduleCalc({ now: true });
  // Remembered on this computer, so the sheet opens calculated next time.
  if (state.path && !state.dirty) {
    api("POST", `/api/trust?path=${encodeURIComponent(state.path)}`, { mtime: state.mtime });
  }
}

// ---------------------------------------------------------------------------
// talking to the kernel
// ---------------------------------------------------------------------------

function receive(message) {
  switch (message.op) {
    case "status":
      setKernel(message.kernel, message.detail);
      // "hello" starts the kernel if there is none, and its reply (the
      // toolkit's vocabulary) is the cue to calculate the sheet.
      if (state.greet && (message.kernel === "none" || message.kernel === "idle")) {
        state.greet = false;
        conn.send({ op: "hello" });
      }
      break;
    case "hello":
      state.vocab = message;
      highlighter = makeHighlighter(message);
      sheet.redrawAll();
      state.calc = null;
      if (!state.trusted) typesetAll();
      else if (state.died) {
        // The last kernel died calculating this very sheet: leave the
        // moment of trying again to the user.
        state.died = false;
        for (const region of sheet.mathRegions()) sheet.setStale(region, true);
        toast("The kernel was restarted. Press F9 to calculate.");
      } else scheduleCalc({ now: true });
      break;
    case "region": {
      const pass = state.calc;
      if (!pass || pass.seq !== message.seq) break;
      pass.seen.add(message.id);
      const region = sheet.find(message.id);
      if (!region) break;
      if (message.same) {
        if (region.result && region.source === pass.sources.get(message.id)) sheet.setStale(region, false);
      } else {
        sheet.setResult(message.id, { items: message.items, tail: message.tail || [], rev: message.rev },
                        pass.sources.get(message.id));
      }
      relayout();
      break;
    }
    case "rendered": {
      const request = state.renders.get(message.seq);
      if (request) sheet.setTypeset(message.id, message.items, request.sources.get(message.id));
      relayout();
      break;
    }
    case "done": {
      const request = state.renders.get(message.seq);
      if (request) {
        state.renders.delete(message.seq);
        // A whole sheet typeset (not yet calculated) has come to rest; a
        // preview while typing only needs the page laid out again.
        if (request.whole) settle(false); else relayout();
      } else if (state.calc && state.calc.seq === message.seq) finishPass(message);
      break;
    }
    case "fault":
      toast("The worksheet engine reported a fault — see the browser console.", "error");
      console.error(message.traceback);
      break;
    case "stray":
      if (message.type === "error") console.error("kernel:", message.text);
      else if (message.type === "stream") console.info("kernel:", message.text);
      break;
    default:
      break;
  }
}

const relayout = debounce(() => sheet.layout(), 16);

function finishPass(message) {
  const pass = state.calc;
  state.calc = null;
  // Regions a stopped (or failed) pass never reached keep their old
  // values — dimmed, so that they are not taken for current ones.
  const partial = Boolean(message.interrupted || message.failed);
  for (const region of sheet.mathRegions()) {
    if (partial && !pass.seen.has(region.id)) sheet.setStale(region, true);
  }
  if (message.failed) {
    if (message.detail) toast("Calculation failed: " + message.detail, "error");
    return;
  }
  if (message.interrupted) {
    toast("Calculation stopped. Press F9 to calculate again.");
  } else {
    state.lastPass = `Last calculation: ${message.executed} statements run, ` +
                     `${message.reused} unchanged, ${message.ms} ms`;
  }
  showStatus();
  settle(!message.interrupted);
  if (state.wantCalc) scheduleCalc({ now: true });
}

/** Positions regions were pushed to are kept once things stop moving. */
function settle(calculated) {
  relayout.cancel();
  const reordered = sheet.settle();
  if (reordered && calculated && state.settling < 3) {
    state.settling += 1;                 // a push changed the reading order
    scheduleCalc({ now: true });
  } else {
    state.settling = 0;
  }
}

const calcSoon = debounce(() => runCalc(false), 30);

function scheduleCalc({ now = false, fresh = false } = {}) {
  if (!state.trusted) return;
  if (!now && !$("#auto-calc").checked) {
    for (const region of sheet.mathRegions()) {
      if (!region.result || region.result.source !== region.source) sheet.setStale(region, true);
    }
    return;
  }
  if (fresh) { calcSoon.cancel(); runCalc(true); } else calcSoon();
}

function runCalc(fresh) {
  if (!conn.online || state.kernel === "dead") { state.wantCalc = true; return; }
  if (state.kernel === "none" || state.kernel === "starting") { state.wantCalc = true; return; }
  state.wantCalc = false;
  const regions = sheet.mathRegions();
  const sources = new Map(regions.map((r) => [r.id, r.source]));
  const seq = conn.send({
    op: "calc", fresh,
    regions: regions.map((r) => ({
      id: r.id, source: r.source,
      have: r.result && r.result.source === r.source ? r.result.rev : null })),
  });
  if (!seq) { state.wantCalc = true; return; }
  const pass = { seq, sources, seen: new Set() };
  state.calc = pass;
  // Dim what is still waiting to be recalculated — but only if the pass
  // takes long enough to notice.
  setTimeout(() => {
    if (state.calc !== pass) return;
    for (const region of regions) {
      if (!pass.seen.has(region.id)) sheet.setStale(region, true);
    }
  }, 180);
}

function typesetAll() {
  const regions = sheet.mathRegions();
  if (!regions.length) return;
  const seq = conn.send({ op: "render", reset: true,
                          regions: regions.map((r) => ({ id: r.id, source: r.source })) });
  if (seq) state.renders.set(seq, { whole: true, sources: new Map(regions.map((r) => [r.id, r.source])) });
}

const previewSoon = debounce((region, source) => {
  const seq = conn.send({ op: "render", regions: [{ id: region.id, source }] });
  if (seq) state.renders.set(seq, { whole: false, sources: new Map([[region.id, source]]) });
}, 110);

function requestPreview(region, source) {
  previewSoon(region, source);
}

function restartKernel() {
  if ($("#banner").dataset.kind === "kernel") banner(null);
  state.calc = null;
  state.greet = true;                   // hello again when the new kernel is up
  conn.send({ op: "restart", path: state.path || "" });
}

// ---------------------------------------------------------------------------
// files
// ---------------------------------------------------------------------------

async function discardOk() {
  if (!state.dirty) return true;
  const choice = await ask("Unsaved changes", `"${state.name}" has changes that are not saved.`, [
    { label: "Cancel", value: null }, { label: "Discard", value: "discard" },
    { label: "Save", value: "save", primary: true }]);
  if (choice === "save") return save();
  return choice === "discard";
}

function adopt(doc) {
  state.path = doc.path ?? null;
  state.name = doc.name || "Untitled";
  state.mtime = doc.mtime ?? null;
  state.trusted = doc.trusted !== false;
  state.preamble = doc.preamble || null;
  state.settings = { ...DEFAULTS, ...(doc.settings || {}) };
  state.calc = null;
  state.renders.clear();
  sheet.calculated = state.trusted;
  sheet.load(doc);
  applySettings();
  setTitle();
  setDirty(false);
  const url = new URL(location.href);
  if (state.path) url.searchParams.set("open", state.path); else url.searchParams.delete("open");
  history.replaceState(null, "", url);
  if (state.trusted) banner(null); else showTrustBanner();
}

async function openPath(path) {
  const { ok, data } = await api("GET", `/api/doc?path=${encodeURIComponent(path)}`);
  if (!ok) { toast(data.error || "Could not open that worksheet.", "error"); return false; }
  const sameFolder = folder(path) === folder(state.path);
  adopt(data);
  if (!conn.online) return true;
  if (!sameFolder) restartKernel();         // the kernel works in the sheet's own folder
  else if (state.trusted) scheduleCalc({ now: true });
  else typesetAll();
  return true;
}

function folder(path) {
  return path && path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
}

async function newSheet() {
  if (!(await discardOk())) return;
  adopt({ regions: [], name: "Untitled", trusted: true });
  if (conn.online) scheduleCalc({ now: true });
}

async function openDialog() {
  if (!(await discardOk())) return;
  const path = await browse({ title: "Open worksheet", action: "Open", dir: folder(state.path) });
  if (path) await openPath(path);
}

async function save(as = false) {
  sheet.commitEdit();
  let path = state.path;
  let create = false;
  if (!path || as) {
    path = await browse({ title: "Save worksheet as", action: "Save", dir: folder(state.path),
                          name: (state.path ? state.name : "Untitled") + ".ipynb" });
    if (!path) return false;
    create = path !== state.path;
  }
  sheet.settle();
  const doc = { settings: state.settings, trusted: state.trusted && sheet.calculated,
                preamble: state.preamble, regions: sheet.serialize(state.settings.showResults) };
  let body = { doc, create, mtime: create ? null : state.mtime };
  for (;;) {
    const { ok, status, data } = await api("PUT", `/api/doc?path=${encodeURIComponent(path)}`, body);
    if (ok) {
      const moved = folder(path) !== folder(state.path);
      state.path = data.path;
      state.name = data.name;
      state.mtime = data.mtime;
      setTitle();
      setDirty(false);
      const url = new URL(location.href);
      url.searchParams.set("open", state.path);
      history.replaceState(null, "", url);
      toast("Saved");
      if (moved) restartKernel();           // the kernel works in the sheet's own folder
      return true;
    }
    if (status === 409) {
      const what = data.exists ? "A worksheet with that name already exists."
        : "This file was changed by another program since it was opened.";
      const choice = await ask("Overwrite?", what, [
        { label: "Cancel", value: null }, { label: "Overwrite", value: "force", primary: true }]);
      if (choice !== "force") return false;
      body = { ...body, force: true };
      continue;
    }
    toast(data.error || "Could not save.", "error");
    return false;
  }
}

// ---------------------------------------------------------------------------
// commands, menus, keys
// ---------------------------------------------------------------------------

function applySettings() {
  $("#paper").classList.toggle("grid", Boolean(state.settings.grid));
  for (const mode of ["auto", "all", "explicit"]) {
    $(`[data-cmd="results-${mode}"]`).setAttribute("aria-checked", String(state.settings.showResults === mode));
  }
  $('.menu-list [data-cmd="toggle-grid"]').setAttribute("aria-checked", String(Boolean(state.settings.grid)));
}

function setResults(mode) {
  state.settings.showResults = mode;
  applySettings();
  sheet.redrawAll();
  setDirty(true);
}

let palette = null;         // the symbol panel, once built (see bindChrome)

/** Open the symbol panel if need be, and take the keyboard to its search field. */
function searchSymbols() {
  if ($("#palette").hidden) togglePalette();
  palette.focusSearch();
}

function togglePalette() {
  const panel = $("#palette");
  panel.hidden = !panel.hidden;
  localStorage.setItem("edsl-palette", panel.hidden ? "" : "1");
  if (panel.hidden) sheet.refocus();        // its search field may have had the keyboard
  $('.tool[data-cmd="toggle-palette"]').classList.toggle("on", !panel.hidden);
  $('.menu-list [data-cmd="toggle-palette"]').setAttribute("aria-checked", String(!panel.hidden));
}

const COMMANDS = {
  new: newSheet,
  open: openDialog,
  save: () => save(false),
  "save-as": () => save(true),
  print: () => { sheet.commitEdit(); sheet.select([]); sheet.settle(); window.print(); },
  undo: () => sheet.undo(),
  redo: () => sheet.redo(),
  "select-all": () => sheet.selectAll(),
  delete: () => sheet.deleteSelected(),
  "insert-space": () => sheet.shiftBelow(24),
  "remove-space": () => sheet.shiftBelow(-24),
  "align-rows": () => sheet.align("row"),
  "align-column": () => sheet.align("column"),
  split: () => { if (!sheet.split()) toast("Select a calculated region with several statements."); },
  // F9 brings the sheet up to date (statements whose inputs are unchanged
  // are not run again); Ctrl+F9 forgets every earlier result first.
  calc: () => { sheet.commitEdit(); if (!state.trusted) trust(); else scheduleCalc({ now: true }); },
  "calc-all": () => { sheet.commitEdit(); if (!state.trusted) trust(); else scheduleCalc({ now: true, fresh: true }); },
  stop: () => conn.send({ op: "interrupt" }),
  "stop-menu": () => conn.send({ op: "interrupt" }),
  "insert-math": () => sheet.create("math"),
  "insert-text": () => sheet.create("text"),
  "toggle-palette": togglePalette,
  "search-symbols": searchSymbols,
  "toggle-grid": () => { state.settings.grid = !state.settings.grid; applySettings(); setDirty(true); },
  "results-auto": () => setResults("auto"),
  "results-all": () => setResults("all"),
  "results-explicit": () => setResults("explicit"),
  "zoom-in": () => sheet.setZoom(sheet.zoom + 0.1),
  "zoom-out": () => sheet.setZoom(sheet.zoom - 0.1),
  "zoom-reset": () => sheet.setZoom(1),
  help: () => { sheet.commitEdit(); info("Keys and notation", helpNode()).then(() => sheet.focus()); },
  restart: () => restartKernel(),
};

function run(command) {
  const action = COMMANDS[command];
  if (action) action();
}

const HELP = [
  ["The page", [
    ["click on empty paper", "put the cursor (the red cross) there"],
    ["just type", "a new math region at the cursor"],
    ['"', "a new text region (markdown, with $math$)"],
    ["Enter", "finish the region; the cursor moves below it"],
    ["Shift+Enter", "another line in the same region"],
    ["Esc", "leave a math region unchanged / finish a text region"],
    ["click a region", "edit it"],
    ["drag a region", "move it (or its blue grip while editing)"],
    ["drag over empty paper", "select regions; arrows nudge, Del deletes"],
    ["Ctrl+Enter / Ctrl+Backspace", "open / close a line of space at the cursor"],
    ["Tab / Shift+Tab", "edit the next / previous region"],
  ]],
  ["Calculating", [
    ["top to bottom, left to right", "a name must be defined above (or to the left of) its use"],
    ["x := 5 V", "defines x.  Calculated values are shown after the definition"],
    ["x", "on its own shows the value of x  (so does  x =)"],
    ["x ▸ mV", "shows it in that unit (\\show)"],
    ["a line ending in ;", "shows no value"],
    ["f := x → x² + 1", "defines a function"],
    ["F9", "bring the sheet up to date (needed when Automatic is off, or after Stop)"],
    ["Ctrl+F9", "recalculate everything from scratch"],
  ]],
  ["Typing notation", [
    ["\\ohm  \\mu  \\pi  \\sqrt  \\cdot  \\par  \\pm  \\to", "Ω μ π √ · ‖ ± →  — a backslash and a name, ended by any other key"],
    ["cm^2   10^-3", "cm²  10⁻³ — digits after ^ become a power"],
    ["\\_1", "R₁ — an index into R.  R_1 with a plain underscore is a name of its own"],
    ["\\subseteq  \\and  \\partial  \\int  \\sum", "⊆ ∧ ∂ ∫ ∑ — sets, logic and calculus have names too"],
    ['"PT90M"\\time   "XIV"\\rome', "the marks ₜᵢₘₑ and ᵣₒₘₑ that make a string a time or a Roman numeral"],
    ["F2", "the symbol panel: the keys, and the library of functions, units and templates tab by tab"],
    ["Ctrl+E", "search the library — arrows choose, Enter puts the entry at the caret"],
    ["\\bode  \\plot  \\hex", "the library is also listed after a backslash; Tab or Enter picks an entry"],
  ]],
  ["Writing text", [
    ['"', "a text region: markdown, with $…$ for a formula and $$…$$ for one on its own line"],
    ["Formula tab (F2)", "a formula editor: a/b, ^, _ and \\names are drawn as you type; Enter puts the $…$ into the text"],
    ["Table tab (F2)", "a table to write over"],
    ["Tab / Shift+Tab", "in a table: the next / previous cell; after the last cell, a new row"],
    ["Esc", "finish the text region (Ctrl+Enter too)"],
  ]],
];

function helpNode() {
  const box = el("div", { class: "help" });
  for (const [title, rows] of HELP) {
    box.append(el("h4", { text: title }),
               el("table", {}, rows.map(([keys, what]) => el("tr", {}, el("td", {}, el("kbd", { text: keys })),
                                                                el("td", { text: what })))));
  }
  return box;
}

function closeMenus() {
  for (const menu of document.querySelectorAll(".menu.open")) {
    menu.classList.remove("open");
    $(".menu-list", menu).hidden = true;
  }
}

function bindChrome() {
  for (const menu of document.querySelectorAll(".menu")) {
    $(".menu-button", menu).addEventListener("pointerdown", (event) => {
      event.preventDefault();
      const open = menu.classList.contains("open");
      closeMenus();
      if (!open) { menu.classList.add("open"); $(".menu-list", menu).hidden = false; }
    });
  }
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest(".menu")) closeMenus();
  });
  $("#bar").addEventListener("pointerdown", (event) => {
    // Toolbar clicks must not take the focus from the region being edited.
    if (event.target.closest("[data-cmd]")) event.preventDefault();
  });
  $("#bar").addEventListener("click", (event) => {
    const button = event.target.closest("[data-cmd]");
    if (!button || button.disabled) return;
    closeMenus();
    run(button.dataset.cmd);
  });
  $("#auto-calc").addEventListener("change", (event) => {
    if (event.target.checked) scheduleCalc({ now: true });
    sheet.focus();
  });
  $("#status").addEventListener("click", () => {
    const failed = $(".region.has-error");          // the first one in the sheet
    if (failed) failed.scrollIntoView({ block: "center", behavior: "smooth" });
  });

  palette = buildPanel($("#palette"), {
    insert: (text, caret) => sheet.insertText(text, caret),
    refocus: () => sheet.refocus(),
    text: {
      insertMarkdown: (text, options) => sheet.insertMarkdown(text, options),
      textCaret: () => sheet.textCaret(),
      replaceText: (start, end, text) => sheet.replaceText(start, end, text),
      refocus: () => sheet.refocus(),
    },
  });
  // The library (the palette's tabs of functions, units and templates)
  // comes separately: the keys work at once, and without it.
  loadLibrary().then(() => palette.refresh(), () => {});
  if (localStorage.getItem("edsl-palette")) togglePalette();

  document.addEventListener("keydown", (event) => {
    if (!$("#overlay").hidden) return;
    const mod = event.ctrlKey || event.metaKey;
    const key = event.key.toLowerCase();
    let command = null;
    if (mod && key === "s") command = event.shiftKey ? "save-as" : "save";
    else if (mod && key === "o") command = "open";
    else if (mod && key === "p") command = "print";
    else if (event.altKey && key === "n") command = "new";
    else if (key === "f9") command = mod ? "calc-all" : "calc";
    else if (key === "f1") command = "help";
    else if (key === "f2") command = "toggle-palette";
    else if (mod && key === "e") command = "search-symbols";
    else if (mod && (key === "=" || key === "+")) command = "zoom-in";
    else if (mod && key === "-") command = "zoom-out";
    else if (mod && key === "0") command = "zoom-reset";
    else if (key === "escape" && state.kernel === "busy" && !sheet.editing) command = "stop";
    else if (event.target.closest?.("input, select, math-field")) command = null;   // a field's own keys
    else if (mod && key === "z" && !sheet.editing) command = event.shiftKey ? "redo" : "undo";
    else if (mod && key === "y" && !sheet.editing) command = "redo";
    if (command) {
      event.preventDefault();
      run(command);
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (state.dirty) { event.preventDefault(); event.returnValue = ""; }
  });
  window.addEventListener("beforeprint", () => { sheet.commitEdit(); sheet.select([]); });
  document.fonts?.ready.then(() => sheet.layout());
}

// ---------------------------------------------------------------------------

async function start() {
  bindChrome();
  applySettings();
  setTitle();
  setKernel("none");
  const wanted = new URL(location.href).searchParams.get("open");
  if (wanted) {
    const { ok, data } = await api("GET", `/api/doc?path=${encodeURIComponent(wanted)}`);
    if (ok) adopt(data);
    else if (/\.ipynb$/i.test(wanted)) {
      // Named on the command line but not there yet: a new sheet that
      // will be saved under that name.
      adopt({ regions: [], name: wanted.replace(/^.*\//, "").replace(/\.ipynb$/i, ""), trusted: true });
      state.path = null;
      toast(`${wanted} does not exist yet — it will be created when you save.`);
    }
  } else {
    adopt({ regions: [], name: "Untitled", trusted: true });
  }
  conn.connect();
  sheet.focus();
}

start();
