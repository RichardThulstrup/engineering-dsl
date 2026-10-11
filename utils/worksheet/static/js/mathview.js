// Drawing what a math region shows: typeset statements, their results,
// printed output, plots and errors — or the values it was given by
// another sheet.

import { el, escapeHtml } from "./util.js";
import { renderMarkdown } from "./markdown.js";
import { caption, markText } from "./given.js";

const KATEX_OPTIONS = { throwOnError: false, strict: "ignore", trust: false, output: "html" };

/** Strip the `$…$` wrapper and `\displaystyle` that notebook reprs carry. */
export function cleanTex(source) {
  let s = String(source).trim();
  if (s.startsWith("$$") && s.endsWith("$$") && s.length >= 4) s = s.slice(2, -2);
  else if (s.startsWith("$") && s.endsWith("$") && s.length >= 2) s = s.slice(1, -1);
  return s.replace(/^\s*\\displaystyle\s*/, "").trim();
}

export function tex(latex, cls = "tex") {
  const span = el("span", { class: cls });
  try {
    window.katex.render("\\displaystyle " + latex, span, KATEX_OPTIONS);
  } catch (error) {
    span.textContent = latex;
  }
  return span;
}

// ---- HTML output (tables from pandas and the like) ------------------------
// Parsed inertly and rebuilt from an allow-list: no scripts, styles,
// event handlers or links survive.
const HTML_TAGS = new Set(("table thead tbody tfoot tr th td caption div span p b i em strong code pre " +
  "br hr ul ol li h1 h2 h3 h4 h5 h6 sub sup small img").split(" "));
const HTML_ATTRS = new Set(["colspan", "rowspan", "title", "alt"]);

function sanitize(node, parent) {
  for (const child of node.childNodes) {
    if (child.nodeType === Node.TEXT_NODE) {
      parent.append(child.textContent);
    } else if (child.nodeType === Node.ELEMENT_NODE) {
      const tag = child.tagName.toLowerCase();
      if (tag === "script" || tag === "style" || tag === "iframe" || tag === "object") continue;
      if (!HTML_TAGS.has(tag)) { sanitize(child, parent); continue; }
      const copy = document.createElement(tag);
      for (const attr of child.attributes) {
        if (HTML_ATTRS.has(attr.name)) copy.setAttribute(attr.name, attr.value);
      }
      if (tag === "img") {
        const src = child.getAttribute("src") || "";
        if (!/^data:image\/(png|jpeg|gif);base64,/i.test(src)) continue;
        copy.setAttribute("src", src);
      }
      sanitize(child, copy);
      parent.append(copy);
    }
  }
  return parent;
}

function htmlNode(html) {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  return sanitize(parsed.body, el("div", { class: "html" }));
}

function imageNode(mime, data, metadata) {
  const img = el("img", { alt: "" });
  img.src = mime === "image/svg+xml"
    ? "data:image/svg+xml;charset=utf-8," + encodeURIComponent(data)
    : `data:${mime};base64,${String(data).replace(/\s+/g, "")}`;
  const size = metadata && metadata[mime];
  if (size && size.width) img.width = size.width;       // "retina" figures carry their CSS size
  if (size && size.height) img.height = size.height;
  return img;
}

/** The richest safe rendering of a MIME bundle. */
export function bundleNode(data, metadata, { inline = false } = {}) {
  if (data["text/latex"]) return tex(cleanTex(data["text/latex"]), inline ? "tex res" : "tex out-tex");
  for (const mime of ["image/png", "image/jpeg", "image/svg+xml"]) {
    if (data[mime]) return imageNode(mime, data[mime], metadata);
  }
  if (data["text/html"]) return htmlNode(data["text/html"]);
  if (data["text/markdown"]) return renderMarkdown(data["text/markdown"]);
  const plain = data["text/plain"] ?? "";
  return inline ? el("span", { class: "plain res", text: plain }) : el("pre", { class: "stream", text: plain });
}

export function outputsNode(events) {
  const box = el("div", { class: "outs" });
  for (const event of events || []) {
    if (event.type === "stream") {
      box.append(el("pre", { class: "stream " + (event.name || "stdout"), text: event.text.replace(/\n$/, "") }));
    } else if (event.type === "display") {
      const node = bundleNode(event.data, event.metadata);
      box.append(node.tagName === "IMG" || node.tagName === "PRE" ? node : el("div", {}, node));
    } else if (event.type === "error") {
      box.append(el("pre", { class: "stream stderr", text: `${event.ename}: ${event.evalue}` }));
    }
  }
  return box;
}

export function errorNode(error) {
  const box = el("div", { class: "err" }, el("b", { text: error.ename }), ": " + (error.evalue || ""));
  if (error.traceback) {
    const more = el("span", { class: "more", text: "details" });
    more.addEventListener("click", () => {
      const shown = box.querySelector("pre");
      if (shown) shown.remove();
      else box.append(el("pre", { text: error.traceback.trim() }));
    });
    box.append(more);
  }
  if (error.hint) box.append(el("span", { class: "hint", text: error.hint }));
  return box;
}

/**
 * Mark a row as failed.  The row turns red and gets a flag; the message
 * itself appears on hover (or stays, after a click on the flag) in a box
 * that floats over the sheet.  It takes no room of its own, so an error
 * — often a passing state while something above is being edited — never
 * pushes the regions around it.
 */
function flagError(line, error) {
  const flag = el("span", { class: "flag", text: "!", role: "button",
                            "aria-label": `${error.ename}: ${error.evalue || ""}` });
  let box = null, pinned = false;
  const show = () => {
    if (box) return;
    box = el("div", { class: "errpop" }, errorNode(error));
    // Reading or selecting the message is not a click into the region.
    box.addEventListener("pointerdown", (event) => event.stopPropagation());
    line.append(box);
  };
  const hide = () => {
    if (box && !pinned) { box.remove(); box = null; }
  };
  line.addEventListener("pointerenter", show);
  line.addEventListener("pointerleave", hide);
  flag.addEventListener("pointerdown", (event) => {
    event.stopPropagation();
    event.preventDefault();
    pinned = !pinned;
    flag.classList.toggle("pinned", pinned);
    if (pinned) show(); else hide();
  });
  line.classList.add("bad");
  line.append(flag);
}

// ---- what was read outside the sheet, and is kept ---------------------------

const READ_FROM = { clock: "the clock", random: "a random number", file: "a file", rates: "the day's currency rates" };

/** When something was read, in a word or two: the time of day if that was today, else the day (with its year, if that is another). */
export function keptWhen(at, now = new Date()) {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return "";
  if (date.toDateString() === now.toDateString()) return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return date.toLocaleDateString([], { day: "numeric", month: "short",
                                       ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }) });
}

/**
 * The mark on a statement whose result was read outside the sheet and
 * is kept with it ({id, at, why}, as the engine says): when it was read.
 * A click on it asks for it to be read again (sheet.js).
 */
function keptNode(kept) {
  const from = (Array.isArray(kept.why) ? kept.why : []).map((why) => READ_FROM[why]).filter(Boolean).join(", ");
  const date = new Date(kept.at);
  const when = Number.isNaN(date.getTime()) ? "" : ` on ${date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
  return el("span", { class: "kept", role: "button", dataset: { id: kept.id }, text: `kept ${keptWhen(kept.at)}`.trim(),
                      title: `Read from outside the sheet${from ? ` (${from})` : ""}${when}, and kept since. ` +
                             "Click to read it again; Ctrl+F9 reads everything again." });
}

/** Whether a statement's value is drawn after it, given the view setting. */
export function showsResult(item, mode) {
  if (!item.result || item.suppress || item.error) return false;
  if (item.kind === "def" || item.kind === "decl") return false;
  if (item.kind === "expr") return true;
  if (mode === "explicit") return false;
  if (mode === "all") return true;
  return !item.literal;
}

function resultNode(item) {
  const nodes = item.result.map((bundle) => bundleNode(bundle, null, { inline: true }));
  if (nodes.length === 1) return nodes[0];
  const group = el("span", { class: "res" }, "(");
  nodes.forEach((node, i) => group.append(i ? ", " : "", node));
  group.append(")");
  return group;
}

/**
 * Fill `body` with the rows of a math region.
 * @param {HTMLElement} body
 * @param {object[]} items   statement and comment items from the engine
 * @param {object} opts      {mode, highlight, tail, results}
 */
export function renderItems(body, items, { mode, highlight, tail, results = true }) {
  body.replaceChildren();
  for (const item of items) {
    if (item.t === "comment") {
      body.append(el("span", { class: "cmtline" + (item.gap ? " gap" : ""),
                               text: item.text, dataset: { line: item.lines[1] } }));
      continue;
    }
    const line = el("div", { class: "line" + (item.gap ? " gap" : ""), dataset: { line: item.lines[1] } });
    if (item.latex) {
      line.append(tex(item.latex, "tex expr"));
    } else {
      const code = el("code", { class: "src" });
      code.innerHTML = highlight(item.src);          // our own escaping tokenizer
      line.append(code);
    }
    if (results && showsResult(item, mode)) {
      line.append(el("span", { class: "eq", text: "=" }), resultNode(item));
    }
    if (results && item.kept) line.append(keptNode(item.kept));
    if (item.error) flagError(line, item.error);
    if (item.comment) line.append(el("span", { class: "cmt", text: item.comment }));
    body.append(line);
    if (results && item.outputs && item.outputs.length) body.append(outputsNode(item.outputs));
  }
  if (results && tail && tail.length) body.append(outputsNode(tail));
}

// ---- values given by another sheet ------------------------------------------

/** What the kernel said of a region that was given values: {told: [{name, latex, value}] | null, error}. */
export function givenSaid(result) {
  const items = result ? result.items : [];
  return { told: items.find((item) => item.given)?.given ?? null, error: items.find((item) => item.error)?.error ?? null };
}

/** Each name a region was given with how it is typeset and shown: by the kernel's word (`told`) where there is one, else by the file's. */
function givenRows(given, told) {
  const live = new Map((told || []).map((entry) => [entry.name, entry]));
  return given.values.map((entry) => {
    const now = live.get(entry.name);
    return { name: entry.name, tex: (now && now.latex) || entry.tex || "", shown: now ? now.value : entry.shown };
  });
}

/**
 * Fill `body` with what a region was given (see given.js): where it is
 * from, then each name with its value, as a definition is written.
 * @param {object} opts  {told, error}: the kernel's word on it (givenSaid);
 *                       {mark}: how it stands to what the other sheet
 *                       delivers now (given.js: standing) — null: it has that
 */
export function renderGiven(body, given, { told = null, error = null, mark = null } = {}) {
  const head = el("div", { class: "whence" }, el("span", { text: caption(given), title: given.sheet }));
  if (mark) {
    head.append(el("span", { class: "mark" + (mark.lost ? " lost" : ""), text: markText(mark), role: "button",
                             title: mark.lost ? "Choose the sheet again, or other values" : "See what has changed, and take the new values" }));
  }
  body.replaceChildren(head);
  for (const row of givenRows(given, told)) {
    const line = el("div", { class: "line", dataset: { name: row.name } });
    // (As one formula with what follows it, ≔ would have room after it: the {} gives it that here.)
    line.append(row.tex ? tex(`${row.tex} \\coloneqq{}`, "tex expr") : el("code", { class: "src", text: `${row.name} := ` }),
                bundleNode(row.shown, null, { inline: true }));
    body.append(line);
  }
  if (error) body.append(el("div", { class: "err" }, el("b", { text: error.ename }), ": " + (error.evalue || "")));
}

/** What a region was given, as the outputs of its notebook cell: where it is from, then each name with its value. */
export function givenOutputs(given, told) {
  const outs = [{ output_type: "stream", name: "stdout", text: caption(given) + "\n" }];
  for (const row of givenRows(given, told)) {
    const data = { "text/plain": `${row.name} = ${row.shown["text/plain"] ?? ""}` };
    if (row.tex && row.shown["text/latex"]) data["text/latex"] = `$\\displaystyle ${row.tex} = ${cleanTex(row.shown["text/latex"])}$`;
    outs.push({ output_type: "display_data", data, metadata: {} });
  }
  return outs;
}

/** Source text shown while a region waits for its first typeset form. */
export function renderSource(body, source, highlight) {
  const pre = el("code", { class: "pending-src" });
  pre.innerHTML = highlight(source);
  body.replaceChildren(pre);
}

// ---- saving ----------------------------------------------------------------

/** What a region shows, as notebook outputs — so the saved file reads
 *  the same in JupyterLab or on GitHub as it does here. */
export function notebookOutputs(result, mode) {
  const outs = [];
  if (!result) return outs;
  const push = (events) => {
    for (const event of events || []) {
      if (event.type === "stream") outs.push({ output_type: "stream", name: event.name, text: event.text });
      else if (event.type === "display") outs.push({ output_type: "display_data", data: event.data, metadata: {} });
    }
  };
  for (const item of result.items) {
    if (item.t !== "stmt") continue;
    if (showsResult(item, mode)) {
      const texts = item.result.map((b) => b["text/latex"] ? cleanTex(b["text/latex"]) : null);
      const plain = item.result.map((b) => b["text/plain"] ?? "").join(", ");
      const label = item.kind === "assign" ? item.src.split(/:=|≔|←|=/)[0].trim() : item.src;
      const data = { "text/plain": `${label} = ${plain}` };
      if (item.latex && texts.every((t) => t !== null)) {
        const lhs = item.kind === "assign" ? item.latex.split("\\coloneqq")[0].trim() : item.latex;
        const rhs = texts.length === 1 ? texts[0] : `\\left(${texts.join(",\\ ")}\\right)`;
        data["text/latex"] = `$\\displaystyle ${lhs} = ${rhs}$`;
      } else {
        for (const mime of ["image/png", "image/jpeg", "image/svg+xml", "text/html"]) {
          if (item.result[0][mime]) data[mime] = item.result[0][mime];
        }
      }
      outs.push({ output_type: "display_data", data, metadata: {} });
    }
    push(item.outputs);
    if (item.error) {
      outs.push({ output_type: "error", ename: item.error.ename, evalue: item.error.evalue || "",
                  traceback: (item.error.traceback || "").split("\n") });
    }
  }
  push(result.tail);
  return outs;
}

export { escapeHtml };
