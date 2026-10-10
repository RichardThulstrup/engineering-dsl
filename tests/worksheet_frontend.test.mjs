// Pure-logic parts of the worksheet front end, run under node with a
// stand-in DOM (see test_worksheet_frontend.py):
//
//     node tests/worksheet_frontend.test.mjs
//
// Text regions come from notebooks that may be someone else's, so the
// markdown renderer is checked for what it must never do as much as for
// what it renders.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.childNodes = [];
    this.attributes = {};
    this.className = "";
    this.dataset = {};
    this.nodeType = 1;
  }
  append(...items) {
    for (const item of items) {
      this.childNodes.push(typeof item === "string" ? { nodeType: 3, textContent: item } : item);
    }
  }
  appendChild(node) { this.childNodes.push(node); return node; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener() {}
  set textContent(value) { this.childNodes = [{ nodeType: 3, textContent: String(value) }]; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  /** Serialised the way a browser would: text is text, never markup. */
  get html() {
    const escape = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const inner = this.childNodes.map((c) => (c.nodeType === 3 ? escape(c.textContent) : c.html)).join("");
    const attrs = Object.entries(this.attributes).map(([k, v]) => ` ${k}="${v}"`).join("");
    const tag = this.tagName.toLowerCase();
    return `<${tag}${this.className ? ` class="${this.className}"` : ""}${attrs}>${inner}</${tag}>`;
  }
  find(tag, out = []) {
    for (const child of this.childNodes) {
      if (child.nodeType !== 1) continue;
      if (child.tagName === tag.toUpperCase()) out.push(child);
      child.find(tag, out);
    }
    return out;
  }
}

globalThis.document = { createElement: (tag) => new Element(tag), querySelector: () => null };
globalThis.window = { katex: { render: (source, node) => { node.textContent = `[math:${source}]`; } } };

const here = path.dirname(fileURLToPath(import.meta.url));
const js = (name) => pathToFileURL(path.join(here, "..", "utils", "worksheet", "static", "js", name)).href;
const { renderMarkdown } = await import(js("markdown.js"));
const { makeHighlighter } = await import(js("highlight.js"));
const { incomplete } = await import(js("editor.js"));
const { complete, GLYPH, namesFor, noteFor, PALETTE } = await import(js("symbols.js"));
const library = await import(js("library.js"));
library.setLibrary(JSON.parse(fs.readFileSync(
  path.join(here, "..", "utils", "worksheet", "static", "data", "library.json"), "utf8")));

const md = (source) => renderMarkdown(source).html.replace(/^<div class="md">|<\/div>$/g, "");

// ---- markdown: what it renders -------------------------------------------
assert.equal(md("# Title"), "<h1>Title</h1>");
assert.equal(md("plain *emph* **strong** `code` ~~gone~~"),
             "<p>plain <em>emph</em> <strong>strong</strong> <code>code</code> <del>gone</del></p>");
// Nested emphasis once sent the renderer round in circles.
assert.equal(md("The **SOT-223 and *more*** package"),
             "<p>The <strong>SOT-223 and <em>more</em></strong> package</p>");
assert.equal(md("below $125\\,^\\circ\\mathrm{C}$."),
             '<p>below <span class="tex">[math:125\\,^\\circ\\mathrm{C}]</span>.</p>');
assert.equal(md("- a\n- b\n  - nested\n- c"), "<ul><li>a</li><li>b<ul><li>nested</li></ul></li><li>c</li></ul>");
assert.equal(md("1. one\n2. two"), "<ol><li>one</li><li>two</li></ol>");
assert.equal(md("| a | b |\n|---|---|\n| 1 | 2 |"),
             "<table><thead><tr><th>a</th><th>b</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>");
assert.equal(md("```\nx = 1\n```"), "<pre><code>x = 1</code></pre>");
assert.equal(md("a\n\n\nb"), "<p>a</p><p>b</p>");
// Underscores inside identifiers are not emphasis: V_out and R_1 stay as typed.
assert.equal(md("V_out and R_1 are _not_ emphasised mid_word_here"),
             "<p>V_out and R_1 are <em>not</em> emphasised mid_word_here</p>");

// The rule under a table's header also sets its columns left, centred or right.
assert.equal(md("| a | b | c |\n|:--|:-:|--:|\n| 1 | 2 | 3 |"),
             '<table><thead><tr><th class="al-left">a</th><th class="al-center">b</th><th class="al-right">c</th></tr></thead>' +
             '<tbody><tr><td class="al-left">1</td><td class="al-center">2</td><td class="al-right">3</td></tr></tbody></table>');

assert.ok(!md("a | b\n---").includes("<table"));              // a rule of one cell is not a rule for two columns

// ---- markdown: what it must never do -------------------------------------
const hostile = renderMarkdown(
  '<script>alert(1)</script> <img src=x onerror=alert(1)> [x](javascript:alert(1)) ' +
  '![i](javascript:alert(1)) [ok](https://example.com) <a href="javascript:1">y</a>');
assert.equal(hostile.find("script").length, 0);
assert.equal(hostile.find("img").length, 0);
const links = hostile.find("a");
assert.deepEqual(links.map((a) => a.attributes.href), ["https://example.com"]);
assert.equal(links[0].attributes.rel, "noopener noreferrer");
assert.ok(hostile.html.includes("&lt;script&gt;"));          // shown as text

// Fragments that are not quite markdown, and every text cell in the repo's
// notebooks, must render — and in good time (an endless loop shows here).
const sources = ["**unclosed *nested* things", "$$", "$", "****", "__", "| a |\n|", "- ", ">", "#", "```", "\\", "![]()"];
for (const file of fs.readdirSync(path.join(here, "..")).filter((f) => f.endsWith(".ipynb"))) {
  const notebook = JSON.parse(fs.readFileSync(path.join(here, "..", file), "utf8"));
  for (const cell of notebook.cells) {
    if (cell.cell_type === "markdown") sources.push([].concat(cell.source).join(""));
  }
}
const started = Date.now();
for (const source of sources) renderMarkdown(source);
assert.ok(Date.now() - started < 5000, "markdown rendering is too slow");

// ---- syntax colouring -----------------------------------------------------
const highlight = makeHighlighter({ units: ["kΩ", "V"], constants: ["π"], functions: ["sqrt"] });
assert.equal(highlight("R_1 := 10. kΩ  # load"),
             'R_1 := <span class="tk-number">10.</span> <span class="tk-unit">kΩ</span>  ' +
             '<span class="tk-comment"># load</span>');
assert.equal(highlight("x = '<b>'"), 'x = <span class="tk-string">\'&lt;b&gt;\'</span>');   // escaped
assert.ok(highlight("for k in xs:").startsWith('<span class="tk-keyword">for</span>'));
assert.ok(highlight("sqrt(2π)").includes('<span class="tk-helper">sqrt</span>'));
assert.ok(highlight("ab", 1).includes('a<span class="caret"></span>b'));                   // caret marker
// Raised and lowered letters are exponents, indices and literal marks, not parts of a name.
assert.ok(highlight("xʸ + vₓ").includes('x<span class="tk-subsup">ʸ</span> + v<span class="tk-subsup">ₓ</span>'));
assert.ok(highlight('"PT90M"ₜᵢₘₑ').endsWith('<span class="tk-subsup">ₜᵢₘₑ</span>'));

// ---- when does Enter finish a region? ------------------------------------
const open = (text) => incomplete(text, text.length);
assert.equal(open("x := 5 V"), false);
assert.equal(open("x := (1 +"), true);                       // an open bracket
assert.equal(open("for 1 ≤ k ≤ 5:"), true);                  // a block is starting
assert.equal(open("for k in xs:\n    total += k"), true);    // inside the block
assert.equal(open("for k in xs:\n    total += k\n"), false); // a blank line ends it
assert.equal(open("s := 'a (b'"), false);                    // brackets in strings do not count
assert.equal(open("x := 5  # see (1"), false);               // nor in comments
assert.equal(open('t := """one'), true);                     // an open triple-quoted string
// With the caret inside brackets that are closed further on, Enter finishes
// the region: an argument was changed, not a line begun.
assert.equal(incomplete("y := atan2(3, 4)", "y := atan2(3, 4".length), false);
assert.equal(incomplete("xs := [1, 2,\n      3]", "xs := [1, 2,".length), false);
assert.equal(incomplete("y := f(g(3, 4)", "y := f(g(3".length), true);            // still one open

// ---- backslash names ------------------------------------------------------
assert.equal(GLYPH.get("ohm"), "Ω");
assert.equal(GLYPH.get("^2"), "²");
assert.equal(complete("oh")[0].glyph, "Ω");
assert.equal(complete("pi")[0].name, "pi");                  // an exact name comes first
assert.deepEqual(complete("zzz"), []);
// Sets, logic and calculus have names, and so have the letters set high or low.
assert.deepEqual(["subseteq", "and", "partial", "_e", "^x"].map((name) => GLYPH.get(name)), ["⊆", "∧", "∂", "ₑ", "ˣ"]);
assert.equal(GLYPH.get("sum"), "∑");                         // the summation sign;
assert.equal(GLYPH.get("Sigma"), "Σ");                       // the letter keeps its own name
assert.equal(GLYPH.get("time"), "ₜᵢₘₑ");                     // "PT90M"\time
assert.ok(complete("sub").some((c) => c.name === "subseteq"));

// ---- the palette ----------------------------------------------------------
for (const group of PALETTE) {
  assert.ok(!group.faces || group.faces.length === group.keys.length, group.title);
}
assert.deepEqual(namesFor("∧"), ["\\land", "\\and", "\\wedge"]);     // a key can tell its names,
assert.deepEqual(namesFor("kΩ"), []);                        // if it has any,
assert.ok(noteFor("ₜᵢₘₑ").startsWith("after a string"));     // and what it is for

// ---- the library (the Windows palette's tabs) -----------------------------
const { about, face, nextSlot, search, slot, snippets, tabs } = library;
assert.deepEqual(tabs().slice(0, 3).map((tab) => tab.name), ["Math", "Greek", "Sub/Sup"]);
assert.ok(about("‖").startsWith("parallel"));

// Where typing goes on after an entry is inserted: its first open place.
const caret = (text) => text.slice(0, slot(text)) + "|" + text.slice(slot(text));
assert.deepEqual(
  ["sin()", "atan2(, )", "∫(, (, , ))", 'save=""', '""ᵣₒₘₑ', "if :", "[ for  in ]", "timedelta(days=)", "‖‖"].map(caret),
  ["sin(|)", "atan2(|, )", "∫(|, (, , ))", 'save="|"', '"|"ᵣₒₘₑ', "if |:", "[| for  in ]", "timedelta(days=|)", "‖|‖"]);
assert.deepEqual(["▸ mV", ".det()", "datetime.now()", '"PT90M"ₜᵢₘₑ', "for j in 1..10:\n    ", "π"].map(caret),
                 ["▸ mV|", ".det()|", "datetime.now()|", '"PT90M"ₜᵢₘₑ|', "for j in 1..10:\n    |", "π|"]);
// Tab moves on to the next one, and stays on its line.
assert.equal(nextSlot("atan2(3, )", "atan2(3".length), "atan2(3, ".length);
assert.equal(nextSlot("x := 5", 2), -1);
assert.equal(nextSlot("a := f(1)\nb := g()", 3), -1);

// How an entry looks on its key.
assert.deepEqual(face({ text: "₂" }), { text: "2", shift: "lowered", style: "glyph" });
assert.deepEqual(face({ text: "⁻¹" }), { text: "−1", shift: "raised", style: "glyph" });
assert.deepEqual(face({ text: "for j in 1..10:\n    " }), { text: "for j in 1..10:", shift: "", style: "code" });
assert.deepEqual(face({ text: "▸ gal_us", label: "▸ gal (US)" }), { text: "▸ gal (US)", shift: "", style: "plain" });
assert.deepEqual([face({ text: "π" }).style, face({ text: "sin()" }).style, face({ text: "90 km/h" }).style],
                 ["glyph", "code", "plain"]);

// Searching: by what an entry is, what it says, and the name that types it.
const found = (query) => search(query).map((hit) => hit.entry.text);
assert.equal(found("ohm")[0], "Ω");                          // \ohm
assert.deepEqual(found("setminus"), ["∖"]);                  // only its backslash name says so
assert.ok(found("subs").includes("⊆"));                      // … and the beginning of a name will do
// Ω sits on two tabs that say different things about it; both find it, once.
assert.ok(found("uppercase omega").includes("Ω") && found("electrical resistance").includes("Ω"));
assert.deepEqual(found("bode"), ["bode(, , (, ))", "bode(, , (, ), s=False)"]);
assert.equal(found("MW")[0], "▸ MW");                        // the case typed comes first,
assert.equal(found("mW")[0], "▸ mW");
assert.ok(found("mW").includes("▸ MW"));                     // the other is not hidden
assert.ok(found("resistance").includes("‖"));                // from the explanation
assert.ok(found("plot log").every((text) => text.includes("log") || text.includes("grid")));
assert.equal(search("bode")[0].tab, "Plotting");
assert.deepEqual(found("zzzz"), []);
assert.deepEqual(found("  "), []);
assert.equal(new Set(found("pi")).size, found("pi").length); // π sits on four tabs, and is found once

// After a backslash: functions, templates and units by their words.
assert.deepEqual(snippets("bod").map((s) => s.glyph), ["bode(, , (, ))", "bode(, , (, ), s=False)"]);
assert.equal(snippets("kWh")[0].glyph, "▸ kWh");
assert.ok(snippets("sin").every((s) => s.kind === "snippet" && s.said));
assert.deepEqual(snippets("p"), []);                         // one letter is a glyph's business
assert.deepEqual(snippets("omega"), []);                     // and so is a glyph

// ---- aids for text regions -------------------------------------------------
const { blockPadding, formulaAt, tableCell, tableMarkdown, tableRow } = await import(js("textaids.js"));

// A table to write over, as the palette's builder makes it.
assert.equal(tableMarkdown(2, 1, "center"), "| Header 1 | Header 2 |\n| :---: | :---: |\n| Cell | Cell |");
assert.equal(tableMarkdown(3, 2).split("\n")[1], "| --- | --- | --- |");
assert.equal(tableMarkdown(99, 99).split("\n").length, 2 + 30);              // at most 30 rows
assert.equal(tableMarkdown(99, 1).split("\n")[0].match(/Header/g).length, 10);     // and 10 columns
assert.equal(md(tableMarkdown(1, 1, "right")),
             '<table><thead><tr><th class="al-right">Header 1</th></tr></thead>' +
             '<tbody><tr><td class="al-right">Cell</td></tr></tbody></table>');

// Tab in a table: ^ is the caret, [ ] what is selected next.
const tab = (marked, step) => {
  const at = marked.indexOf("^");
  const text = marked.replace("^", "");
  const cell = tableCell(text, at, step);
  return cell && text.slice(0, cell.start) + "[" + text.slice(cell.start, cell.end) + "]" + text.slice(cell.end);
};
assert.equal(tab("| Pa^rt | Value |\n| --- | --- |\n| R1 |  |", 1), "| Part | [Value] |\n| --- | --- |\n| R1 |  |");
assert.equal(tab("| Part | Val^ue |\n| --- | --- |\n| R1 |  |", 1), "| Part | Value |\n| --- | --- |\n| [R1] |  |");   // over the rule
assert.equal(tab("| Part | Value |\n| --- | --- |\n| R1^ |  |", 1), "| Part | Value |\n| --- | --- |\n| R1 | [] |");    // an empty cell
assert.equal(tab("| Part | Value |\n| --- | --- |\n| R1 | ^ |", 1), null);          // the end of the table
assert.equal(tab("| Part | Value |\n| --- | --- |\n| ^R1 |  |", -1), "| Part | [Value] |\n| --- | --- |\n| R1 |  |");
assert.equal(tab("| ^Part | Value |\n| --- | --- |\n| R1 |  |", -1), null);         // and its beginning
assert.equal(tab("| Part | Value |\n| -^-- | --- |\n| R1 |  |", 1), "| Part | Value |\n| --- | --- |\n| [R1] |  |");
assert.equal(tab("| Part | Value |\n| -^-- | --- |\n| R1 |  |", -1), "| Part | [Value] |\n| --- | --- |\n| R1 |  |");
assert.equal(tab("a ^line of text\n\n| x | y |", 1), null);                         // not in a table
assert.equal(tab("x ^| y\n--|--\n1 | 2", 1), "x | [y]\n--|--\n1 | 2");              // bars at the ends are optional
// Past the last cell, Tab adds a row of as many cells as the header has.
const grown = "| a | b |\n| --- | --- |\n| 1 | 2 |";
assert.deepEqual(tableRow(grown + "\n\nand text", grown.length - 2),
                 { at: grown.length, text: "\n|  |  |", caret: grown.length + 3 });
assert.equal(tableRow("no table here", 4), null);

// The formula the caret is in.
assert.deepEqual(formulaAt("a $x+1$ b", 4), { start: 2, end: 7, latex: "x+1", display: false });
assert.equal(formulaAt("a $x+1$ b", 2), null);                                      // before its first dollar
assert.equal(formulaAt("a $x+1$ b", 7), null);                                      // after its last
assert.deepEqual(formulaAt("see $$ E=mc^2 $$", 9), { start: 4, end: 16, latex: "E=mc^2", display: true });
assert.equal(formulaAt("`$x$` and $y$", 2), null);                                  // dollars in code are not math
assert.equal(formulaAt("`$x$` and $y$", 11).latex, "y");

// A block is set apart from the text around it by blank lines.
assert.deepEqual(blockPadding("", ""), { lead: "", tail: "" });
assert.deepEqual(blockPadding("Some text", ""), { lead: "\n\n", tail: "" });
assert.deepEqual(blockPadding("Some text\n", "more"), { lead: "\n", tail: "\n\n" });
assert.deepEqual(blockPadding("Some text\n\n", "\n\nmore"), { lead: "", tail: "" });

console.log("FRONTEND_OK");
