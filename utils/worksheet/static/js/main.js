// Worksheet front end: ties the page to the kernel and the file.

import { $, api, debounce, el, toast, uid } from "./util.js";
import { Conn } from "./conn.js";
import { Sheet } from "./sheet.js";
import { makeHighlighter } from "./highlight.js";
import { loadLibrary } from "./library.js";
import { buildPanel } from "./panel.js";
import { ask, browse, info } from "./dialogs.js";
import { fitStrokes, restoreStrokes } from "./strokes.js";
import { NEEDED } from "./arrange.js";
import { LIMITS, megabytes, weight } from "./pictures.js";
import { band, BUILT_IN, heights, normal } from "./pageframe.js";
import { frameDialog } from "./framedialog.js";
import { relative, resolve, standing, taken } from "./given.js";
import { givenDialog } from "./givendialog.js";

const DEFAULTS = { showResults: "auto" };          // of a sheet: saved with it

const state = {
  path: null,               // relative to the server's folder; null = never saved
  name: "Untitled",
  mtime: null,
  root: "",                 // the server's folder, as the computer names it
  trusted: true,            // false: a sheet not calculated on this machine yet
  preamble: null,
  settings: { ...DEFAULTS },
  // How the user likes the page: kept by the server for this computer,
  // not in the sheet (and not in the browser, whose storage goes by port).
  prefs: { grid: false, symbols: false, markdown: false, template: "" },
  templates: [],            // header and footer, by name: the user's own (kept by the server, too)
  dirty: false,
  changes: 0,               // counts them: has anything changed since a save set out?
  kernel: "none",
  online: false,
  vocab: null,
  calc: null,               // the pass in flight: {seq, sources: Map(id → source), wanted, at}
  renders: new Map(),       // seq → Map(id → source) of typesetting requests
  wantCalc: false,
  // Does the kernel have the sheet as it stands?  Every time a calculation
  // is asked for counts (`wanted`): what the kernel has may then no longer
  // be what the sheet says.  It may be that again once the pass which was
  // asked for last has come to its end (`passed`) — whether that pass got
  // to the end of the sheet, the kernel says with its values.
  wanted: 0,
  passed: null,             // {seq, wanted} of the last pass that came to its end
  pending: false,           // a pass is about to be asked for (calcSoon)
  waiting: new Set(),       // what is woken when a pass comes to its end (a save: ownValues)
  asked: new Map(),         // seq → what takes the kernel's word on the sheet's values
  values: false,            // the server hands a sheet's values on (one started before it could does not)
  settling: 0,
  greet: false,
  lastPass: "",
  died: false,
};

let highlighter = makeHighlighter(null);

const sheet = new Sheet({
  highlight: (text, caret) => highlighter(text, caret),
  settings: () => state.settings,
  prefs: () => state.prefs,
  changed: ({ calc }) => {
    setDirty(true);
    if (calc) scheduleCalc();
    // What another sheet gave has come, or come back: how does it stand to what that sheet delivers now?
    if (sheet.givenRegions().some((region) => region.mark === undefined)) checkGiven();
  },
  selection: (count) => {
    // Each arranging command is there once enough regions are selected.
    for (const [how, needed] of Object.entries(NEEDED)) {
      for (const button of document.querySelectorAll(`[data-cmd$="-${how}"]`)) button.disabled = count < needed;
    }
  },
  preview: (region, source) => requestPreview(region, source),
  frame: () => run("page-frame"),
  given: (region) => takeValues(region),
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

/** The sheet has been changed (true), or is as its file has it (false). */
function setDirty(dirty) {
  state.dirty = dirty;
  if (dirty) state.changes += 1;
  showDirty();
}

function showDirty() {
  $("#doc-dirty").hidden = !state.dirty;
  document.title = `${state.dirty ? "• " : ""}${state.name} — Worksheet`;
}

function setTitle() {
  $("#doc-name").textContent = state.name;
  $("#doc-title").title = state.path || "not saved yet";
  showDirty();
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
    // (The button goes with the banner: the keyboard returns to the sheet.)
    button.addEventListener("click", () => { action.run(); sheet.focus(); });
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
// header and footer
// ---------------------------------------------------------------------------

/**
 * What a sheet says of itself, as far as a file's word for it is of the
 * kind it should be: {title, author, revision, saved}.
 */
function aboutOf(given) {
  const about = {};
  if (!given || typeof given !== "object") return about;
  for (const name of ["title", "author", "saved"]) {
    if (typeof given[name] === "string" && given[name]) about[name] = given[name].slice(0, 200);
  }
  if (typeof given.revision === "string" && given.revision) about.revision = given.revision.slice(0, 20);
  else if (Number.isSafeInteger(given.revision) && given.revision >= 0) about.revision = given.revision;
  return about;
}

/** What the fields of a header or footer say about this sheet (pageframe.js). */
function aboutSheet() {
  const own = state.settings.about || {};
  // When it was saved: as the sheet says itself — or, of one that does not (or says no date), as its file does.
  let saved = new Date(own.saved || NaN);
  try {
    if (Number.isNaN(saved.getTime())) saved = state.mtime ? new Date(Number(BigInt(state.mtime) / 1000000n)) : null;
  } catch (error) {
    saved = null;                           // a time that is none
  }
  const inside = folder(state.path);
  return {
    file: `${state.name}.ipynb`, name: state.name,
    folder: state.path === null ? "" : [state.root, ...(inside ? inside.split("/") : [])].join(state.root.includes("\\") ? "\\" : "/"),
    title: own.title || state.name, author: own.author || "", revision: own.revision ?? 0, saved, now: new Date(),
  };
}

let frameStamp = 0;

/** Draw the pages with the header and the footer the sheet has, saying what is so now. */
function showFrame() {
  const frame = normal(state.settings.frame);
  const about = aboutSheet();
  sheet.setFrame(frame && { ...heights(frame), stamp: ++frameStamp,
                            band: (which, page, pages) => band(frame, which, { ...about, page, pages }) });
}

/** The user's own templates as the server gave them: each a frame this page understands, under a name. */
function templatesOf(data) {
  return (Array.isArray(data.templates) ? data.templates : [])
    .map((template) => ({ name: String(template?.name ?? "").trim(), frame: normal(template?.frame) }))
    .filter((template) => template.name && template.frame && !BUILT_IN.some((ready) => ready.name === template.name));
}

/** Have the server keep (PUT) or let go of (DELETE) a template of the user's own; resolves to them all — or to null. */
async function ownTemplates(method, url, body) {
  const { ok, status, data } = await api(method, url, body);
  if (!ok) {
    toast(status === 404
      ? "Templates of your own are kept by the worksheet server, and the one running here was started before it could: " +
        "close the worksheet and start it again."
      : data.error || "That did not work: the template is as it was.", "error");
    return null;
  }
  state.templates = templatesOf(data);
  return state.templates;
}

/** The header and footer a new sheet starts with: the template chosen for that on this computer. */
function usualFrame() {
  const found = [...BUILT_IN, ...state.templates].find((template) => template.name === state.prefs.template);
  return found ? { frame: found.frame } : {};
}

/** File ▸ Header and footer…: ask, and give the sheet what was chosen. */
async function editFrame() {
  sheet.commitEdit();
  const frame = normal(state.settings.frame);
  const had = state.settings.about || {};
  const made = await frameDialog({
    frame, about: had, sample: { ...aboutSheet(), pages: sheet.printedPages() },
    templates: state.templates, usual: state.prefs.template,
    keep: (name, kept) => ownTemplates("PUT", "/api/templates", { name, frame: kept }),
    drop: (name) => ownTemplates("DELETE", `/api/templates?name=${encodeURIComponent(name)}`),
  });
  if (!made) return;
  if (made.usual !== state.prefs.template) setPref("template", made.usual);
  // What the sheet says of itself: what was typed, and — untouched here — when it was saved.
  const about = { ...had, ...made.about };
  for (const name of Object.keys(about)) {
    if (about[name] === undefined || about[name] === "") delete about[name];
  }
  if (JSON.stringify([made.frame, about]) === JSON.stringify([frame, had])) return;         // nothing of the sheet's was changed
  const settings = { ...state.settings };
  delete settings.frame;
  delete settings.about;
  if (made.frame) settings.frame = made.frame;
  if (Object.keys(about).length) settings.about = about;
  state.settings = settings;
  setDirty(true);
  showFrame();
}

/**
 * What a sheet says of itself once it is saved now — or null, where a
 * save leaves that as it is.  A sheet says when it was last saved with
 * something `changed`, and counts those saves: its revision — unless
 * that was typed as text, which stays as it was typed.  (A header says
 * it; and it is what another sheet names the values by that it takes
 * from this one — so a sheet that has no revision yet gets its first
 * one with any save.)
 */
function stamp(settings, changed) {
  const had = settings.about || {};
  if (!changed && had.revision !== undefined) return null;
  const about = { ...had, saved: new Date().toISOString() };
  if (typeof (about.revision ?? 0) === "number") about.revision = (about.revision ?? 0) + 1;
  return about;
}

// ---------------------------------------------------------------------------
// values: what the sheet hands on to other sheets, and what it is given
// ---------------------------------------------------------------------------

const PATIENCE = 4000;      // ms a save waits for a calculation that is under way

/** Has the pass that was asked for last come to its end? */
const passed = () => state.passed !== null && state.passed.wanted === state.wanted;

/** Is a pass under way, or about to be asked for? */
const calculating = () => state.calc !== null || state.pending;

/**
 * Ask the kernel what the sheet's variables are: its word {values,
 * toolkit, pass} — or null, where none comes (it reports a fault, or is
 * gone and says nothing for ten seconds).
 */
function kernelValues() {
  return new Promise((resolve) => {
    const seq = conn.send({ op: "values" });
    if (!seq) { resolve(null); return; }
    const answer = (said) => { clearTimeout(timer); state.asked.delete(seq); resolve(said); };
    const timer = setTimeout(() => answer(null), 10000);
    state.asked.set(seq, answer);
  });
}

/**
 * What the sheet's variables are, for its file to keep with the revision
 * a save makes: {wanted, toolkit, values} — or null, where the kernel
 * does not have the sheet as it stands (it is not calculated; the
 * calculation was stopped, or takes its time).  A calculation that is
 * under way is waited for a moment, so that what was typed just before
 * Ctrl+S is in the values as it is in the file.
 */
async function ownValues() {
  if (!state.values) return null;
  const until = (state.calc ? state.calc.at : Date.now()) + PATIENCE;
  const slow = setTimeout(() => toast("Saving once this is calculated…"), 400);
  while (!passed() && calculating() && Date.now() < until) {
    await new Promise((wake) => {
      const done = () => { clearTimeout(timer); state.waiting.delete(done); wake(); };
      const timer = setTimeout(done, until - Date.now());
      state.waiting.add(done);
    });
  }
  clearTimeout(slow);
  if (!passed()) return null;
  const wanted = state.wanted;
  const said = await kernelValues();
  // The kernel says which pass they are the values of: the one this page
  // knows the sheet by (and it got to the end of the sheet), or they are
  // not kept.
  if (!said || said.pass !== state.passed.seq) return null;
  return { wanted, toolkit: said.toolkit, values: said.values };
}

/** The name of a sheet by its path. */
const sheetName = (path) => path.replace(/^.*\//, "").replace(/\.ipynb$/i, "");

/**
 * Values from another sheet: choose the sheet, and which of its values —
 * they are put on the page at the cursor.  For a `region` that holds
 * such values, what it holds is chosen anew: other values, another
 * sheet, or the same values as a newer revision has them.
 */
async function takeValues(region = null) {
  // (What is being written is finished when a dialog takes the keyboard: the values go below it.)
  const writing = sheet.editing ? sheet.editing.region : null;
  if (!state.values) {
    toast("The worksheet server running here was started before sheets could hand values on: " +
          "close the worksheet and start it again.", "error");
    return;
  }
  const home = folder(state.path);
  let path = region ? resolve(home, region.given.sheet) : undefined;
  for (;;) {
    if (path === undefined) {
      path = await browse({ title: "Values from which sheet?", action: "Choose", dir: home });
      if (!path) return;
    }
    let source;
    if (path === null) {
      source = { name: region.given.name, missing: `${region.given.sheet} is outside the folder this worksheet was started in.` };
    } else if (path === state.path) {
      source = { name: state.name, missing: "A sheet takes values from other sheets, not from itself." };
    } else {
      const { ok, data } = await api("GET", `/api/values?path=${encodeURIComponent(path)}`);
      source = ok ? data : { name: sheetName(path), missing: data.error };
    }
    const made = await givenDialog({ source, taken: region ? region.given : null });
    if (made === "other") { path = undefined; continue; }
    if (!made) return;
    const given = taken(source, relative(home, path), made.names);
    if (region) sheet.regive(region, given); else sheet.addGiven(given, writing);
    return;
  }
}

/** See how what the regions were given stands to what their sheets deliver now, and mark those that have not got that. */
function checkGiven() {
  const home = folder(state.path);
  const asked = new Map();                  // path → what the server says of that sheet
  for (const region of sheet.givenRegions()) {
    const given = region.given;
    const path = resolve(home, given.sheet);        // (null: outside the folder the server has — which it says is not there)
    if (!asked.has(path)) asked.set(path, api("GET", `/api/values?path=${encodeURIComponent(path ?? "")}&brief=1`));
    asked.get(path).then(({ ok, status, data }) => {
      // A server that does not answer says nothing of the sheet: the mark
      // stays as it is.  Nor is an answer that comes after the region was
      // given something else an answer about what it has.
      if (!ok && status === 0) return;
      if (region.given === given) sheet.setMark(region, standing(given, ok ? data : null));
    });
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
    case "values":
      state.asked.get(message.seq)?.(message);
      break;
    case "fault":
      state.asked.get(message.seq)?.(null);           // (no values will come of that)
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
  state.passed = { seq: pass.seq, wanted: pass.wanted };
  for (const wake of [...state.waiting]) wake();
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
  state.wanted += 1;
  if (!state.trusted) return;
  if (!now && !$("#auto-calc").checked) {
    for (const region of sheet.mathRegions()) {
      if (!region.result || region.result.source !== region.source) sheet.setStale(region, true);
    }
    return;
  }
  if (fresh) { calcSoon.cancel(); runCalc(true); } else { state.pending = true; calcSoon(); }
}

function runCalc(fresh) {
  state.pending = false;
  if (!conn.online || state.kernel === "dead") { state.wantCalc = true; return; }
  if (state.kernel === "none" || state.kernel === "starting") { state.wantCalc = true; return; }
  state.wantCalc = false;
  const regions = sheet.mathRegions();
  const sources = new Map(regions.map((r) => [r.id, r.source]));
  const seq = conn.send({
    op: "calc", fresh,
    regions: regions.map((r) => ({
      id: r.id, source: r.source,
      have: r.result && r.result.source === r.source ? r.result.rev : null,
      // (What another sheet gave is run, and shown by the page itself.)
      given: r.given ? true : undefined })),
  });
  if (!seq) { state.wantCalc = true; return; }
  const pass = { seq, sources, seen: new Set(), wanted: state.wanted, at: Date.now() };
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
  const regions = sheet.mathRegions().filter((r) => !r.given);        // (what another sheet gave is not typeset: the page shows it)
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
  await saving;                             // (a save under way is of this sheet: it is done before another takes its place)
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
  delete state.settings.grid;           // sheets saved before the grid became a preference carry it
  // What it says of itself: as far as that is of the kind it should be.
  state.settings.about = aboutOf(state.settings.about);
  if (!Object.keys(state.settings.about).length) delete state.settings.about;
  state.calc = null;
  state.renders.clear();
  state.passed = null;                  // (what the kernel has is another sheet)
  sheet.calculated = state.trusted;
  showFrame();
  sheet.load(doc);
  checkGiven();
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
  adopt({ regions: [], name: "Untitled", trusted: true, settings: usualFrame() });
  if (conn.online) scheduleCalc({ now: true });
}

async function openDialog() {
  if (!(await discardOk())) return;
  const path = await browse({ title: "Open worksheet", action: "Open", dir: folder(state.path) });
  if (path) await openPath(path);
}

let saving = Promise.resolve();

/** Save the sheet — `as`: under a name that is asked for.  One save at a time: the next waits for the one under way. */
function save(as = false) {
  const turn = saving.then(() => write(as));
  // (The next save does not wait for one that tripped — whose error is the page's to report all the same.)
  saving = turn.catch((error) => { setTimeout(() => { throw error; }); });
  return turn;
}

async function write(as) {
  sheet.commitEdit();
  let path = state.path;
  let create = false;
  if (!path || as) {
    path = await browse({ title: "Save worksheet as", action: "Save", dir: folder(state.path),
                          name: (state.path ? state.name : "Untitled") + ".ipynb" });
    if (!path) return false;
    create = path !== state.path;
  }
  // What the sheet's variables are is kept with the revision this save
  // makes, for other sheets to take: asked of the kernel, which may take
  // a moment — and what is done to the sheet in that moment is not in them.
  const said = await ownValues();
  sheet.commitEdit();
  if (sheet.settle()) scheduleCalc({ now: true });      // (regions were moved into another order: calculated anew)
  const values = said && said.wanted === state.wanted ? said : null;
  const changes = state.changes;            // (what is changed from here on is not in what is saved)
  const about = stamp(state.settings, state.dirty);
  // In another folder, the sheets it was given values by are reached another way.
  const home = folder(state.path), there = folder(path);
  const reached = (given) => {
    const whole = resolve(home, given.sheet);
    return whole === null ? given : { ...given, sheet: relative(there, whole) };
  };
  const doc = { settings: about ? { ...state.settings, about } : state.settings, trusted: state.trusted && sheet.calculated,
                preamble: state.preamble,
                regions: sheet.serialize(state.settings.showResults).map((r) => (r.given ? { ...r, given: reached(r.given) } : r)) };
  if (values) {
    const now = about || state.settings.about;      // (what the sheet says of itself once it is saved)
    doc.values = { revision: now.revision, saved: now.saved, toolkit: values.toolkit, values: values.values };
  } else if (create) {
    doc.values = null;                      // (a file that is written over keeps no values of the sheet it was)
  }
  if (weight(doc.regions) > LIMITS.sheet) {
    toast(`The pictures on this sheet take ${megabytes(weight(doc.regions))} MB: more than the ` +
          `${megabytes(LIMITS.sheet)} MB a sheet is saved with.`, "error");
    return false;
  }
  let body = { doc, create, mtime: create ? null : state.mtime };
  for (;;) {
    const { ok, status, data } = await api("PUT", `/api/doc?path=${encodeURIComponent(path)}`, body);
    if (ok) {
      const moved = folder(path) !== folder(state.path);
      state.path = data.path;
      state.name = data.name;
      state.mtime = data.mtime;
      if (about) state.settings = { ...state.settings, about };
      setTitle();
      // Saved — but for what was changed while the server was writing.
      if (state.changes === changes) setDirty(false);
      showFrame();                          // (its name, its revision, when it was saved)
      const url = new URL(location.href);
      url.searchParams.set("open", state.path);
      history.replaceState(null, "", url);
      if (moved) sheet.rebase(home, there);
      if (moved) restartKernel();           // the kernel works in the sheet's own folder
      // The server says how many pictures it stored.  One that turned
      // some down — or was started before pictures could be kept, and
      // knows nothing of them — must not lose them without a word.
      const sent = doc.regions.reduce((count, r) => count + Object.keys(r.attachments || {}).length, 0);
      const stored = data.pictures ?? 0;
      if (stored !== sent) {
        setDirty(true);
        toast(`Not all of it is saved: the worksheet server stored ${stored} of the ${sent} pictures. ` +
              "They are still on this page — copy them into a sheet of a freshly started worksheet.", "error");
        return false;
      }
      // Without its values, a sheet that could have had them says so:
      // another sheet gets the ones of the revision before.
      toast(values || !state.values || !state.trusted ? "Saved"
        : "Saved. Its values are not kept with this revision: the sheet is not calculated.");
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
  $("#paper").classList.toggle("grid", state.prefs.grid);
  for (const mode of ["auto", "all", "explicit"]) {
    $(`[data-cmd="results-${mode}"]`).setAttribute("aria-checked", String(state.settings.showResults === mode));
  }
  $('.menu-list [data-cmd="toggle-grid"]').setAttribute("aria-checked", String(state.prefs.grid));
  $('.menu-list [data-cmd="toggle-markdown"]').setAttribute("aria-checked", String(state.prefs.markdown));
}

/** A choice about the page itself: applied, and remembered for this computer. */
function setPref(name, value) {
  state.prefs[name] = value;
  applySettings();
  api("PUT", "/api/prefs", { [name]: value });
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

function togglePalette({ remember = true } = {}) {
  const panel = $("#palette");
  panel.hidden = !panel.hidden;
  if (remember) setPref("symbols", !panel.hidden);
  if (panel.hidden) sheet.refocus();        // its search field may have had the keyboard
  $('.tool[data-cmd="toggle-palette"]').classList.toggle("on", !panel.hidden);
  $('.menu-list [data-cmd="toggle-palette"]').setAttribute("aria-checked", String(!panel.hidden));
}

const COMMANDS = {
  new: newSheet,
  open: openDialog,
  save: () => save(false),
  "save-as": () => save(true),
  "page-frame": editFrame,
  print: () => { sheet.commitEdit(); sheet.select([]); sheet.settle(); window.print(); },
  undo: () => sheet.undo(),
  redo: () => sheet.redo(),
  "select-all": () => sheet.selectAll(),
  delete: () => sheet.deleteSelected(),
  "insert-space": () => sheet.shiftBelow(24),
  "remove-space": () => sheet.shiftBelow(-24),
  ...Object.fromEntries(Object.keys(NEEDED).map((how) => [(NEEDED[how] < 3 ? "align-" : "spread-") + how, () => { sheet.arrange(how); sheet.focus(); }])),
  split: () => { if (!sheet.split()) toast("Select a calculated region with several statements."); },
  // F9 brings the sheet up to date (statements whose inputs are unchanged
  // are not run again); Ctrl+F9 forgets every earlier result first.
  calc: () => { sheet.commitEdit(); if (!state.trusted) trust(); else scheduleCalc({ now: true }); },
  "calc-all": () => { sheet.commitEdit(); if (!state.trusted) trust(); else scheduleCalc({ now: true, fresh: true }); },
  stop: () => conn.send({ op: "interrupt" }),
  "stop-menu": () => conn.send({ op: "interrupt" }),
  "insert-math": () => sheet.create("math"),
  "insert-text": () => sheet.create("text"),
  "insert-picture": () => $("#picture-file").click(),
  "take-values": () => takeValues(),
  "toggle-palette": () => togglePalette(),
  "search-symbols": searchSymbols,
  "toggle-grid": () => setPref("grid", !state.prefs.grid),
  "toggle-markdown": () => setPref("markdown", !state.prefs.markdown),
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
    ['"', "a new text region, written as it looks"],
    ["Enter", "finish the region; the cursor moves below it"],
    ["Shift+Enter", "another line in the same region"],
    ["Esc", "leave a math region unchanged / finish a text region"],
    ["click a region", "edit it"],
    ["drag a region", "move it (or its blue grip while editing)"],
    ["drag over empty paper", "select regions; arrows nudge, Del deletes"],
    ["Arrange menu", "line the selection up by its edges or middles, or space it out evenly"],
    ["Ctrl+Enter / Ctrl+Backspace", "open / close a line of space at the cursor"],
    ["Tab / Shift+Tab", "edit the next / previous region"],
  ]],
  ["Pages", [
    ["File ▸ Header and footer…", "a header and a footer on every page: text, a logo, and fields such as {file}, {saved} and {page}"],
    ["double-click a header or a footer", "change it"],
    ["Ctrl+P", "print the sheet, or save it as a PDF, on the pages it fills"],
  ]],
  ["Pictures", [
    ["Ctrl+V", "the picture on the clipboard — a screenshot, a copied drawing, a copied picture file — at the cursor"],
    ["Picture (in the bar), or a file dropped on the page", "a picture from a file: PNG, JPEG, GIF, WebP, BMP, or an SVG drawing"],
    ["click a picture", "select it: drag it to move it, its right edge to resize it, Del deletes it"],
    ["Ctrl+V while writing text", "the picture goes into the text, at the caret"],
  ]],
  ["Values from other sheets", [
    ["Values (in the bar)   Edit ▸ Values from another sheet…", "variables of another sheet, as they were when it was saved: held here with that sheet's name and revision"],
    ["click what another sheet gave", "select it: drag it to move it, Del deletes it"],
    ["double-click it", "choose other values, or another sheet"],
    ["a mark such as “rev. 4 is there”", "the other sheet has been saved with other values since: a click shows what is different, for you to take"],
    ["saving a sheet", "keeps what its variables are with the revision that makes — once it is calculated"],
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
    ['"', "a text region.  It is written as it looks; the bar above it — or these keys — set the formatting"],
    ["Ctrl+B   Ctrl+I   Ctrl+U", "bold, italic, underlined"],
    ["Ctrl+Shift+X   Ctrl+,   Ctrl+.   Ctrl+<", "struck through, lowered (H₂O), raised (x²), code"],
    ["Ctrl+Shift+1 … 6   Ctrl+Shift+0", "a heading of that level / ordinary text again"],
    ["Ctrl+Shift+8   Ctrl+Shift+9   Ctrl+'", "a bulleted list, a numbered list, a quotation.  Tab moves an item in, Shift+Tab out"],
    ["Ctrl+K", "a link on the selected text"],
    ["#   -   1.   >   ```   **bold**   *italic*   $x^2$", "markdown's marks work as they are typed; Backspace takes one back"],
    ["Shift+Enter", "a new line in the same paragraph; out of a block of code"],
    ["Ctrl+V", "formatted text keeps the formatting markdown has; plain text is read as markdown (Ctrl+Shift+V: just as it is)"],
    ["Ctrl+V on the page", "formatted text from another program becomes a text region; plain text, and code, is taken as typed (so is all with Ctrl+Shift+V)"],
    ["Formula tab (F2)", "a formula editor: a/b, ^, _ and \\names are drawn as you type; Enter puts the formula into the text"],
    ["click a formula   double click it", "select it (the Formula tab takes it up) / write its LaTeX where it stands"],
    ["Table tab (F2)", "a table to write over.  Tab / Shift+Tab: the next / previous cell; after the last cell, a new row"],
    ["↑ or ↓ out of a table the text begins or ends with", "room to write above or below it (so for a formula, a list, code)"],
    ["Esc", "finish the text region (Ctrl+Enter too)"],
    ["Markdown (in the bar)   View ▸ Write text as Markdown", "go on with this text as its markdown / write all text that way"],
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
  $("#picture-file").addEventListener("change", (event) => {
    const files = [...event.target.files];
    event.target.value = "";                         // the same file may be chosen again
    // Into the text being written, if one is; otherwise onto the page.
    const writing = sheet.editing && sheet.editing.region.kind === "text" ? sheet.editing.region : null;
    if (files.length) sheet.takePictures({ files }, writing);
  });

  palette = buildPanel($("#palette"), {
    insert: (text, caret) => sheet.insertText(text, caret),
    refocus: () => sheet.refocus(),
    text: {
      insertMarkdown: (text, options) => sheet.insertMarkdown(text, options),
      formula: () => sheet.formula(),
      putFormula: (latex, display, found, replace) => sheet.putFormula(latex, display, found, replace),
      refocus: () => sheet.refocus(),
    },
  });
  // The library (the palette's tabs of functions, units and templates)
  // comes separately: the keys work at once, and without it.
  loadLibrary().then(() => palette.refresh(), () => {});

  document.addEventListener("keydown", (event) => {
    // (A key the text being written has taken for itself — Ctrl+Shift+0 for
    // plain text, say — is not the page's as well.)
    if (!$("#overlay").hidden || event.defaultPrevented) return;
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
  window.addEventListener("beforeprint", () => {
    sheet.commitEdit();
    sheet.select([]);
    showFrame();                            // (the day and the time of printing are now)
    fitStrokes();
  });
  window.addEventListener("afterprint", restoreStrokes);
  // Back from another window — where another sheet may have been saved.
  window.addEventListener("focus", () => checkGiven());
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
  const [chosen, own, where] = await Promise.all([api("GET", "/api/prefs"), api("GET", "/api/templates"),
                                                  api("GET", "/api/info")]);
  if (chosen.ok) {
    Object.assign(state.prefs, chosen.data);
    applySettings();
    if (state.prefs.symbols && $("#palette").hidden) togglePalette({ remember: false });
  }
  if (own.ok) state.templates = templatesOf(own.data);
  if (where.ok) state.root = String(where.data.root || "");
  state.values = where.ok && where.data.values === true;
  // A sheet that is new starts with the template chosen for new sheets.
  if (state.path === null && !state.settings.frame) Object.assign(state.settings, usualFrame());
  showFrame();
}

start();
