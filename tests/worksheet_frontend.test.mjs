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

globalThis.document = { createElement: (tag) => new Element(tag), querySelector: () => null, compatMode: "CSS1Compat" };
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

// ---- markdown: the few tags that say what it cannot --------------------------
// Underlined, lowered and raised text has no mark in markdown: it is kept
// as HTML's own tag — those tags, bare, and no others.
assert.equal(md("H<sub>2</sub>O, x<sup>2</sup>, <u>under</u>, <b>bold</b>, <i>slanted</i>, <s>struck</s>"),
             "<p>H<sub>2</sub>O, x<sup>2</sup>, <u>under</u>, <strong>bold</strong>, <em>slanted</em>, <del>struck</del></p>");
assert.equal(md("<u>a **b** $x$</u>"), '<p><u>a <strong>b</strong> <span class="tex">[math:x]</span></u></p>');
const tagged = renderMarkdown('<u onclick="x">a</u> <font color="red">c</font> <U>d</U> <u>e <sub>f <span>g</span> <style>h</style>');
assert.equal(["u", "sub", "font", "span", "style"].map((tag) => tagged.find(tag).length).join(), "0,0,0,0,0");
assert.ok(tagged.html.includes("&lt;u onclick") && tagged.html.includes("&lt;style&gt;"));        // shown as text

// ---- markdown, read and written again -------------------------------------
// The formatted editor writes a text back as markdown (mdtree.js).  What
// is written must read as what was shown: for everything the reading
// knows — and for every text cell in the repo's notebooks.
const { address, parseMarkdown, sameReading, written, writeMarkdown } = await import(js("mdtree.js"));
const rewritten = (source) => writeMarkdown(parseMarkdown(source));
const KNOWN = [
  "# Title\n\n## Second ##\n\nplain *emph* **strong** `code` ~~gone~~ <u>u</u> H<sub>2</sub>O x<sup>2</sup>",
  "The **SOT-223 and *more*** package, __bold__ and _slanted_, a\\*b, 2\\*3\\*4, \\$5 and \\$6, \\<u>not\\</u>",
  "below $125\\,^\\circ\\mathrm{C}$ and $$\\frac{a}{b}$$ in the line\n\n$$\nx = 1\n$$\n\n$$y$$",
  "- a\n- b\n  - nested\n    1. deeper\n- c\n  wrapped\n\n1. one\n2. two\n\n* star\n+ plus",
  "| a | b | c |\n|:--|:-:|--:|\n| 1 | `x\\|y` | **3** |\n| only |",
  "```python\nx = 1\n\n  y = 2\n```\n\n~~~\ntilde\n~~~\n\n````\n```\n````",
  "> quoted **text**\n> - with a list\n>\n> and more\n\n---\n\nafter the rule",
  "[link](https://example.com/a_(b%29) and https://example.com/bare, ![pic](attachment:a%20b.png) ![](https://example.com/p.png)",
  "line one  \nline two\\\nline three<br>line four",
  "1\\. not a list\n\\# not a heading\n\\- not an item\n\\> not a quotation\n\\--- not a rule",
];
for (const source of [...KNOWN, ...sources]) {
  const again = rewritten(source);
  assert.ok(again.ok && sameReading(parseMarkdown(again.text), parseMarkdown(source)), `written anew, this reads differently:\n${source}`);
  assert.equal(rewritten(again.text).text, again.text, `written twice, this is spelled two ways:\n${source}`);
}
assert.ok(!sameReading(parseMarkdown("a **b** c"), parseMarkdown("a *b* c")));
assert.ok(!sameReading(parseMarkdown("- a\n- b"), parseMarkdown("1. a\n2. b")));
assert.ok(sameReading(parseMarkdown("a  **b**\nc"), parseMarkdown("a __b__ c")));          // however it is spelled and spaced

// How it is spelled.  Plainly, where nobody would misread it …
const one = (block) => written(block).text;
const p = (...c) => ({ t: "p", c });
const text = (v) => ({ t: "text", v });
assert.equal(one(p(text("V_out is 5 V, a_b_c, 100 % (about) [1]"))), "V_out is 5 V, a_b_c, 100 % (about) [1]");
// … a backslash doubled where it would be read as holding a mark back …
assert.equal(one(p(text("C:\\new\\*.txt and a_b_c, \\alpha"))), "C:\\new\\\\*.txt and a_b_c, \\alpha");
// … with a backslash before whatever would be read as a mark …
assert.equal(one(p(text("2*3*4 and $5 or $6, a <u>tag</u>"))), "2\\*3\\*4 and \\$5 or \\$6, a \\<u>tag\\</u>");
// … and with tags where marks touch (markdown's own would run together).
assert.equal(one(p({ t: "strong", c: [text("a")] }, { t: "em", c: [text("b")] })), "<b>a</b><i>b</i>");
// Blanks at the ends of what is marked go outside the mark.
assert.equal(one(p(text("a"), { t: "strong", c: [text(" b ")] }, text("c"))), "a **b** c");
// A line of a paragraph that would begin something else is held back.
for (const [line, held] of [["1. not a list", "1\\. not a list"], ["# not a heading", "\\# not a heading"],
                            ["- not an item", "\\- not an item"], ["> not a quotation", "\\> not a quotation"],
                            ["--- not a rule", "--- not a rule"], ["---", "\\---"]]) {
  assert.equal(one(p(text(line))), held);
}
// An address is as markdown can hold it between its brackets.
assert.equal(address(" https://example.org/a b(c) "), "https://example.org/a%20b(c%29");
assert.equal(one(p({ t: "link", href: "https://example.org/a%20b(c%29", c: [text("a ] b")] })),
             "[a \\] b](https://example.org/a%20b(c%29)");
assert.equal(one(p({ t: "img", src: "attachment:my%20plot.png", alt: "a [1]" })), "![a \\[1\\]](attachment:my%20plot.png)");
// A fence is longer than any in the code; a table holds its pipes back and
// sets its columns by its rule; a list numbers its items and indents the
// lists within them.
assert.equal(one({ t: "code", v: "```\nx\n```", info: "python" }), "````python\n```\nx\n```\n````");
assert.equal(one({ t: "table", align: [null, "right"], head: [[text("a|b")], [text("n")]],
                   rows: [[[text("x")], [text("1")]], [[text("y")]]] }),
             "| a\\|b | n |\n| --- | --: |\n| x | 1 |\n| y |  |");
assert.equal(one({ t: "list", ordered: true, items: [
  { c: [text("one"), { t: "list", ordered: false, items: [{ c: [text("in")] }] }] }, { c: [text("two")] }] }),
             "1. one\n   - in\n2. two");
// A new line within a paragraph; within a heading, which is one line; a
// heading that ends in a hash (hashes there are read as closing it).
assert.equal(one(p(text("a"), { t: "br" }, text("b"))), "a\\\nb");
assert.equal(one({ t: "h", n: 2, c: [text("a"), { t: "br" }, text("b")] }), "## a<br>b");
assert.equal(one({ t: "h", n: 2, c: [text("C#")] }), "## C# ##");
assert.equal(one(p(text("so "), { t: "math", v: "x^2", block: false }, text(" and "), { t: "math", v: "y", block: true })),
             "so $x^2$ and $$y$$");
// What cannot be written so that it reads the same is said to be so.
assert.equal(written(p({ t: "math", v: "a$b", block: false })).ok, false);

// A block nobody touched is written back as it stood, letter for letter,
// and blocks that stood together stay as far apart as they were.
const odd = "__bold__ and _it_\n* a\n* b\n\n\n\nLast   line";
const blocks = parseMarkdown(odd);
const stood = new Map(blocks.map((block) => [block, { from: block.from, to: block.to }]));
assert.equal(writeMarkdown(blocks, stood, odd).text, odd);
stood.delete(blocks[1]);                                       // the list was changed
assert.equal(writeMarkdown(blocks, stood, odd).text, "__bold__ and _it_\n\n- a\n- b\n\nLast   line");

// ---- pictures ---------------------------------------------------------------
// A text shows the pictures attached to its region — a drawing before the
// bitmap copy kept beside it for JupyterLab — whatever their names look like.
const { carried, lonePicture, looksLikeSvg, named, pictureUrl, reference } = await import(js("pictures.js"));
const BITMAP = "iVBORw0KGgo=", DRAWING = "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciLz4=";
const attached = { "a.png": { "image/png": BITMAP }, "b.svg": { "image/png": BITMAP, "image/svg+xml": DRAWING },
                   "my c.jpg": { "image/jpeg": BITMAP } };
const pictured = renderMarkdown("See ![a](attachment:a.png), **![b](attachment:b.svg)**\n\n- ![c](attachment:my%20c.jpg)", attached);
assert.deepEqual(pictured.find("img").map((img) => [img.attributes.alt, img.attributes.src]), [
  ["a", `data:image/png;base64,${BITMAP}`], ["b", `data:image/svg+xml;base64,${DRAWING}`],
  ["c", `data:image/jpeg;base64,${BITMAP}`]]);
assert.ok(pictured.find("img").every((img) => img.attributes.draggable === "false"));   // the sheet does the dragging
// … wherever in a text they are named: a heading, emphasis of every kind,
// a list (its wrapped lines and lists within it), a table, a quotation.
const everywhere = renderMarkdown([
  "# ![a](attachment:a.png)", "", "*![a](attachment:a.png)* ~~![a](attachment:a.png)~~ __![a](attachment:a.png)__", "",
  "- ![a](attachment:a.png)\n  ![a](attachment:a.png)\n  - ![a](attachment:a.png)", "",
  "| ![a](attachment:a.png) |\n|---|\n| ![a](attachment:a.png) |", "", "> ![a](attachment:a.png)"].join("\n"), attached);
assert.deepEqual(["h1", "em", "del", "strong", "li", "th", "td", "blockquote"].map((tag) => everywhere.find(tag)
  .filter((node) => node.find("img").length).length), [1, 1, 1, 1, 2, 1, 1, 1]);
assert.equal(everywhere.find("img").length, 10);
assert.equal(renderMarkdown("[![a](attachment:a.png)](https://example.com)", attached).find("a")[0].find("img").length, 1);

// What is no picture is not shown as one: a name with nothing attached, an
// attachment of another kind, data that is not base64, a name every object has.
const unpictured = renderMarkdown(
  "![a](attachment:gone.png) ![b](attachment:page.html) ![c](attachment:bad.png) ![d](attachment:constructor)",
  { "page.html": { "text/html": BITMAP }, "bad.png": { "image/png": '"><script>alert(1)</script>' } });
assert.equal(unpictured.find("img").length, 0);
assert.ok(unpictured.textContent.includes("![a](attachment:gone.png)"));       // left as it was written
assert.equal(renderMarkdown("![a](attachment:a.png)").find("img").length, 0);  // a text without attachments
assert.equal(pictureUrl({ "image/gif": BITMAP, "text/html": "x" }), `data:image/gif;base64,${BITMAP}`);
assert.equal(pictureUrl({ "image/svg+xml": "<svg/>" }), null);                 // a drawing, too, is kept as base64
assert.equal(pictureUrl(null), null);

// A region is a picture when its text is one attached picture and nothing else.
assert.equal(lonePicture("![plot](attachment:1a2b3c.png)"), "1a2b3c.png");
assert.equal(lonePicture("  ![](attachment:1a2b3c.svg)\n"), "1a2b3c.svg");
assert.equal(lonePicture("![plot](attachment:1a2b3c.png) and a caption"), null);
assert.equal(lonePicture("![plot](https://example.com/plot.png)"), null);
assert.equal(lonePicture("![a](attachment:a.png)![b](attachment:b.png)"), null);
assert.equal(reference({ alt: "plot", name: "1a2b3c.png" }), "![plot](attachment:1a2b3c.png)");

// A sheet is saved with the pictures its texts still name.
assert.deepEqual(Object.keys(named("![a](attachment:a.png) <img src=\"attachment:my%20c.jpg\">", attached)), ["a.png", "my c.jpg"]);
assert.deepEqual(named("no picture here", attached), {});
assert.deepEqual(named("![a](attachment:a.png)", undefined), {});

// Text that is an SVG drawing — the whole of it — is pasted as a picture.
assert.ok(looksLikeSvg('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect/></svg>'));
assert.ok(looksLikeSvg('\n<?xml version="1.0" encoding="UTF-8"?>\n<!-- drawn by hand -->\n<svg viewBox="0 0 1 1">\n</svg>\n'));
assert.ok(looksLikeSvg('<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "svg11.dtd"><svg></svg>'));
assert.ok(!looksLikeSvg("An <svg> element draws: <svg></svg>"));
assert.ok(!looksLikeSvg("<svg> is how a drawing starts"));
assert.ok(!looksLikeSvg("<p>about</p><svg></svg>"));
assert.ok(!looksLikeSvg("<!-- no drawing --><p></p></svg>"));
assert.ok(!looksLikeSvg("x := 5 V"));

// What a clipboard carries: picture files, a drawing as text — and a
// drawing the browser names in a paste but hands over only when asked.
const clipboard = (strings, files = []) => ({
  types: [...Object.keys(strings), ...(files.length ? ["Files"] : [])], files,
  getData: (type) => strings[type] || "" });
const file = (name, type) => ({ name, type, size: 10 });
assert.equal(carried(clipboard({ "text/plain": "x := 5 V" })), null);
assert.equal(carried(clipboard({}, [file("notes.txt", "text/plain"), file("report.pdf", "application/pdf")])), null);
assert.deepEqual(carried(clipboard({ "text/plain": "words" }, [file("image.png", "image/png"), file("notes.txt", "text/plain")])),
                 { files: [file("image.png", "image/png")], svg: null, ask: false });
assert.deepEqual(carried(clipboard({}, [file("drawing.SVG", ""), file("photo.jpeg", "")])).files.map((f) => f.name),
                 ["drawing.SVG", "photo.jpeg"]);                               // told by their names, where no kind is given
assert.deepEqual(carried(clipboard({ "image/svg+xml": "<svg/>" })), { files: [], svg: "<svg/>", ask: false });
assert.deepEqual(carried(clipboard({ "image/svg+xml": "" }, [file("image.png", "image/png")])),
                 { files: [file("image.png", "image/png")], svg: null, ask: true });

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

// ---- arranging a selection --------------------------------------------------
const { aligned, distributed, NEEDED } = await import(js("arrange.js"));
const BOXES = [{ x: 40, y: 10, w: 100, h: 20 }, { x: 200, y: 50, w: 60, h: 40 }, { x: 120, y: 120, w: 30, h: 10 }];
const xs = (places) => places.map((p) => p.x), ys = (places) => places.map((p) => p.y);
// By a side or by the middle, on the selection's own outline (40 to 260 across, 10 to 130 down).
assert.deepEqual(xs(aligned(BOXES, "left")), [40, 40, 40]);
assert.deepEqual(xs(aligned(BOXES, "centre")), [100, 120, 135]);
assert.deepEqual(xs(aligned(BOXES, "right")), [160, 200, 230]);
assert.deepEqual(ys(aligned(BOXES, "right")), [10, 50, 120]);                    // and nothing moves up or down
assert.deepEqual(ys(aligned(BOXES, "top")), [10, 10, 10]);
assert.deepEqual(ys(aligned(BOXES, "middle")), [60, 50, 65]);
assert.deepEqual(ys(aligned(BOXES, "bottom")), [110, 90, 120]);
// On one line, a region that would lie on its neighbour moves right, clear of it.
assert.deepEqual(xs(aligned(BOXES, "top", { gap: 8 })), [40, 200, 148]);
assert.deepEqual(xs(aligned([BOXES[0], { ...BOXES[1], x: 150 }, BOXES[2]], "top", { gap: 8 })), [40, 186, 148]);
// A region alone is aligned on the page.
const ALONE = [{ x: 50, y: 5, w: 100, h: 20 }];
assert.deepEqual(["left", "centre", "right"].map((how) => aligned(ALONE, how, { pageWidth: 680 })[0].x), [0, 290, 580]);
assert.equal(aligned(ALONE, "centre", { pageWidth: 680 })[0].y, 5);
// Spread out: the outer two stay, the space between neighbours is equal.
assert.deepEqual(ys(distributed(BOXES, "y")), [10, 55, 120]);                    // 25 between each
assert.deepEqual(xs(distributed(BOXES, "y")), [40, 200, 120]);
assert.deepEqual(xs(distributed(BOXES, "x")), [40, 200, 155]);                   // 15 between each, in order from the left
// Too little room for that: the regions take what they need, `gap` apart.
const STACK = [{ x: 0, y: 0, w: 10, h: 30 }, { x: 0, y: 10, w: 10, h: 30 }, { x: 0, y: 20, w: 10, h: 30 }];
assert.deepEqual(ys(distributed(STACK, "y", { gap: 9 })), [0, 39, 78]);
assert.deepEqual(NEEDED, { left: 1, centre: 1, right: 1, top: 2, middle: 2, bottom: 2, across: 3, down: 3 });

// ---- printing: KaTeX's long strokes, shortened ------------------------------
// The real KaTeX draws roots, arrows and braces 400em long; shortened to
// their box, each must be the same drawing at the end it is fastened by.
const { createRequire } = await import("node:module");
const katex = createRequire(import.meta.url)(path.join(here, "..", "utils", "worksheet", "static", "vendor", "katex", "katex.min.js"));
const { shortenStroke } = await import(js("strokes.js"));

/** The points a path visits, as [x, y] (control points included), from its `d`. */
function visited(d) {
  const points = [];
  let x = 0, y = 0, start = [0, 0];
  for (const [, command, rest] of d.matchAll(/([A-Za-z])([^A-Za-z]*)/g)) {
    const n = (rest.match(/[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?/gi) || []).map(Number);
    const relative = command === command.toLowerCase();
    const step = { m: 2, l: 2, t: 2, h: 1, v: 1, c: 6, s: 4, q: 4, z: 0 }[command.toLowerCase()];
    assert.ok(step !== undefined, `path command ${command}`);
    if (step === 0) { [x, y] = start; continue; }
    for (let i = 0; i + step <= n.length; i += step) {
      const group = n.slice(i, i + step);
      if (step === 1) {
        if (command.toLowerCase() === "h") x = relative ? x + group[0] : group[0];
        else y = relative ? y + group[0] : group[0];
        points.push([x, y]);
        continue;
      }
      for (let k = 0; k < step; k += 2) points.push(relative ? [x + group[k], y + group[k + 1]] : [group[k], group[k + 1]]);
      [x, y] = points[points.length - 1];
      if (command.toLowerCase() === "m" && i === 0) start = [x, y];
    }
  }
  return points;
}

const FORMULAS = ["\\sqrt{x}", "\\sqrt[3]{x^2+1}", "\\sqrt{\\frac{a}{b}}", "\\sqrt{1+\\left(\\frac{f}{f_c}\\right)^2}",
                  "\\sqrt{\\begin{matrix}a\\\\b\\\\c\\\\d\\end{matrix}}", "\\overrightarrow{AB}", "\\overleftarrow{AB}",
                  "\\overleftrightarrow{AB}", "\\xrightarrow{abc}", "\\xleftarrow{abc}", "\\xleftrightarrow{abc}",
                  "\\xRightarrow{abc}", "\\xhookrightarrow{abc}", "\\xtwoheadrightarrow{abc}", "\\xrightleftharpoons{abc}",
                  "\\overbrace{a+b+c}", "\\underbrace{a+b+c}", "\\overgroup{AB}", "\\undergroup{AB}", "\\xlongequal{abc}"];
const TO = 6000;            // units: a box six em wide
let drawings = 0;
for (const formula of FORMULAS) {
  const html = katex.renderToString(formula, { output: "html", throwOnError: true });
  for (const [, align, body] of html.matchAll(/<svg[^>]*width="400em"[^>]*preserveAspectRatio="(x\w+)YMin slice"[^>]*>([\s\S]*?)<\/svg>/g)) {
    for (const [, d] of body.matchAll(/ d="([^"]*)"/g)) {
      drawings += 1;
      const before = visited(d), after = visited(shortenStroke(d, TO));
      assert.equal(after.length, before.length, formula);
      const reach = Math.max(...after.map(([x]) => Math.abs(x)));
      assert.ok(reach <= TO + 1100, `${formula}: still reaches ${reach}`);     // (a bar is measured from its root sign, an em in)
      // Seen from where the drawing is fastened -- its left end, its middle
      // or its right end -- nothing within two em has moved.
      const from = { xMin: [0, 0], xMid: [200000, TO / 2], xMax: [400000, TO] }[align];
      const milli = (value) => Math.round(value * 1000) / 1000;
      const near = (points, origin) => points.map(([x, y]) => [milli(x - origin), milli(y)]).filter(([x]) => Math.abs(x) < 2000);
      assert.deepEqual(near(after, from[1]), near(before, from[0]), `${formula} (${align})`);
      assert.ok(near(before, from[0]).length > 2, formula);
    }
  }
}
assert.ok(drawings >= 30, `only ${drawings} long drawings were checked`);
assert.equal(shortenStroke("M95,702c-2.7,0,-7.17,-2.7,-13.5,-8H400000v40H845z", 400000), "M95,702c-2.7,0,-7.17,-2.7,-13.5,-8H400000v40H845z");

// ---- the frame of a page: header and footer ---------------------------------
{
  const frames = await import(js("pageframe.js"));
  assert.deepEqual(frames.BUILT_IN.map((template) => template.name), ["Page numbers", "File and date", "Title block"]);
  const BLOCK = frames.BUILT_IN[2].frame;
  // A frame is made of whatever a file holds — and of nothing but what a frame is.
  for (const none of [null, undefined, "x", 7, [], {}, { header: ["", " \n ", ""], footer: [] }, { header: "{file}" },
                      { logo: { data: { "image/png": BITMAP } } }]) {
    assert.equal(frames.normal(none), null, JSON.stringify(none));
  }
  const odd = frames.normal({ header: ["a\r\nb\rc\nd\ne", 5, "x".repeat(400), "a fourth"], footer: null, rule: { header: 1, footer: true },
                              dates: "tomorrow", template: 9, more: "of it" });
  assert.deepEqual(odd, { header: ["a\nb\nc\nd", "", "x".repeat(300)], footer: ["", "", ""],
                          rule: { header: false, footer: true }, dates: "iso", logo: null });
  assert.deepEqual(frames.normal(BLOCK), BLOCK);                                  // (made again, it is what it was)
  const logoOf = (logo) => frames.normal({ header: ["{logo}", "", ""], logo }).logo;
  assert.deepEqual(logoOf({ data: { "image/png": BITMAP }, name: "acme.png" }), { data: { "image/png": BITMAP }, name: "acme.png", height: 10 });
  assert.deepEqual(logoOf({ data: { "image/png": BITMAP, "image/svg+xml": DRAWING }, height: 15 }).data, { "image/svg+xml": DRAWING });
  assert.deepEqual([1, 4, 12.5, 40, 99, "x"].map((height) => logoOf({ data: { "image/png": BITMAP }, height }).height), [4, 4, 12.5, 40, 40, 10]);
  for (const data of [{ "text/html": BITMAP }, { "image/png": "<svg onload=alert(1)>" }, { "image/png": 5 }, "image/png", null,
                      { "image/png": "A".repeat(2.8e6 + 4) }]) {
    assert.equal(logoOf({ data }), null, JSON.stringify(data).slice(0, 60));
  }

  // How high they are is worked out, not measured: a step of the grid for
  // every line, the logo's height in whole steps, and one step for the rule.
  const high = (header, footer = ["", "", ""], logo = null) => frames.heights(frames.normal({ header, footer, logo }));
  assert.deepEqual(frames.heights(null), { head: 0, foot: 0 });
  assert.deepEqual(high(["{file}", "", ""]), { head: 32, foot: 0 });
  assert.deepEqual(high(["", "", ""], ["", "{page}", ""]), { head: 0, foot: 32 });
  assert.deepEqual(high(["one", "one\ntwo\nthree", "one\ntwo"], ["a\nb", "", ""]), { head: 64, foot: 48 });
  assert.deepEqual(frames.heights(BLOCK), { head: 48, foot: 32 });               // (a place for a logo, and none yet: a line)
  const PICTURE = { data: { "image/png": BITMAP }, height: 10 };                 // 10 mm are 37.8 px: three steps
  assert.deepEqual(high(["{logo}", "**{title}**\n{file}", ""], ["", "", ""], PICTURE), { head: 64, foot: 0 });
  assert.deepEqual(high(["{logo}\nACME", "", ""], ["", "", ""], PICTURE), { head: 80, foot: 0 });
  assert.deepEqual(high(["no logo here", "", ""], ["", "", ""], { ...PICTURE, height: 40 }), { head: 32, foot: 0 });
  assert.deepEqual(high(["{logo}", "", ""], ["", "", "{logo}"], { ...PICTURE, height: 40 }), { head: 176, foot: 176 });

  // The fields say what is known of the sheet and the page.  What they say
  // is text: it is not read again, as markdown or as anything else.
  const ABOUT = { file: "Torque_v2_.ipynb", name: "Torque_v2_", folder: "C:\\work", title: "**Torque** <img src=x onerror=alert(1)>",
                  author: "R. T.", revision: 7, saved: new Date(2026, 9, 11, 9, 5), now: new Date(2026, 11, 24, 18, 30), page: 2, pages: 5 };
  const drawn = (header, more = {}, about = ABOUT) => frames.band(frames.normal({ header, ...more }), "header", about);
  const texts = (node) => node.find("div").filter((div) => div.className === "words").map((line) => line.textContent);
  assert.deepEqual(texts(drawn(["{file} ({name}) in {folder}", "{title}", "Rev. {revision} by {author}\n{page} / {pages}"])),
                   ["Torque_v2_.ipynb (Torque_v2_) in C:\\work", "**Torque** <img src=x onerror=alert(1)>", "Rev. 7 by R. T.", "2 / 5"]);
  assert.deepEqual(drawn(["", "{title}", ""]).find("img"), []);
  assert.ok(drawn(["", "{title}", ""]).html.includes("**Torque** &lt;img src=x onerror=alert(1)&gt;"));
  assert.deepEqual(texts(drawn(["{saved} {saved-time}", "{date} {time}", "{nothing} {Page} { page } {pages"])),
                   ["2026-10-11 09:05", "2026-12-24 18:30", "{nothing} {Page} { page } {pages"]);
  assert.deepEqual(Object.keys(frames.DATES).map((dates) => texts(drawn(["{saved}", "", ""], { dates }))[0]),
                   ["2026-10-11", "11-10-2026", "11.10.2026", "11/10/2026", "10/11/2026", "11 October 2026"]);
  assert.equal(texts(drawn(["{saved}|{saved-time}|{revision}|{author}", "", ""], {}, { ...ABOUT, saved: null, revision: 0, author: undefined }))[0],
               "||0|");
  assert.equal(texts(drawn(["{saved}|{saved-time}|{date}", "", ""], {}, { ...ABOUT, saved: new Date("no date") }))[0], "||2026-12-24");
  // The text around them is read as a line of a text region is.
  const formatted = drawn(["**{name}** and *more*", "$x^2$ `{file}`", "one\ntwo"]);
  assert.equal(formatted.find("strong")[0].textContent, "Torque_v2_");
  assert.equal(formatted.find("em")[0].textContent, "more");
  assert.equal(formatted.find("code")[0].textContent, "{file}");                 // (code is as it is typed)
  assert.ok(formatted.html.includes("[math:x^2]"));
  assert.deepEqual(formatted.find("div").filter((div) => div.className.startsWith("cell ")).map((cell) => [cell.className, cell.childNodes.length]),
                   [["cell left", 1], ["cell centre", 1], ["cell right", 2]]);
  // The logo is a picture where it is named, at the height it is given; without one, its place is empty.
  const withLogo = drawn(["{logo} ACME", "", ""], { logo: { data: { "image/svg+xml": DRAWING }, height: 12 } });
  assert.deepEqual(withLogo.find("img").map((img) => [img.attributes.src, img.attributes.alt]), [[`data:image/svg+xml;base64,${DRAWING}`, "logo"]]);
  assert.equal(withLogo.attributes.style, "height: 64px; --logo: 45.35px;");
  assert.equal(texts(withLogo)[0], " ACME");
  assert.deepEqual([drawn(["{logo} ACME", "", ""]).find("img").length, texts(drawn(["{logo} ACME", "", ""]))[0]], [0, " ACME"]);
  assert.equal(texts(drawn(["{logo} ACME", "", ""], {}, { ...ABOUT, noLogo: "[logo]" }))[0], "[logo] ACME");
  // Header: its cells, then the rule; footer: the rule first.  A part that is empty is not there at all.
  const both = frames.normal({ header: ["h", "", ""], footer: ["", "f", ""], rule: { header: true, footer: false } });
  assert.deepEqual(frames.band(both, "header", ABOUT).childNodes.map((node) => node.className), ["cells", "rule drawn"]);
  assert.deepEqual(frames.band(both, "footer", ABOUT).childNodes.map((node) => node.className), ["rule", "cells"]);
  assert.deepEqual([frames.band(both, "header", ABOUT).className, frames.band(both, "footer", ABOUT).attributes.style], ["pageframe header", "height: 32px;"]);
  assert.equal(frames.band(frames.normal({ header: ["h", "", ""] }), "footer", ABOUT), null);
  assert.ok(frames.sameFrame({ ...BLOCK, template: "another name" }, BLOCK) && !frames.sameFrame({ ...BLOCK, dates: "dmy" }, BLOCK));
  assert.ok(frames.FIELDS.every(([name]) => texts(drawn([`{${name}}`, "", ""]))[0] !== `{${name}}`));     // every field offered says something

  // Header and footer take their room from the page.  Regions keep their
  // places on the sheet — one strip, top to bottom — and a page takes less
  // of it; on paper the frames stand between the pages' parts of the strip.
  const plain = frames.paged(0, 0, 1009);
  assert.deepEqual([plain.framed, plain.body, plain.toPaper(1234.5), plain.toSheet(1234.5), plain.toSheet(-20)], [false, 1009, 1234.5, 1234.5, -20]);
  const framed = frames.paged(48, 32, 1009);
  assert.deepEqual([framed.framed, framed.body], [true, 929]);
  assert.deepEqual([0, 100, 928, 929, 930, 1858, 2000].map(framed.toPaper), [48, 148, 976, 1057, 1058, 2066, 2208]);
  assert.deepEqual([48, 148, 976, 1057, 1058, 2066, 2208].map(framed.toSheet), [0, 100, 928, 929, 930, 1858, 2000]);
  // A place in a header or a footer is the nearest place of its page.
  assert.deepEqual([-30, 0, 47, 977, 1000, 1008, 1009, 1056].map(framed.toSheet), [0, 0, 0, 928, 928, 928, 929, 929]);
  assert.equal(framed.toPaper(-10), 38);                                         // (a region dragged above the sheet's top is above the first page's)
  for (let y = 0; y < 5000; y += 37) assert.equal(framed.toSheet(framed.toPaper(y)), y);
}

// ---- a text as the formatted editor holds it --------------------------------
// The editor's document (richdoc.js) is made from a region's markdown and
// written back as markdown.  It asks for ProseMirror by name, as the page
// does through its import map: every name there must be a file that is there.
const page = fs.readFileSync(path.join(here, "..", "utils", "worksheet", "static", "index.html"), "utf8");
const imports = JSON.parse(/<script type="importmap">([\s\S]*?)<\/script>/.exec(page)[1]).imports;
const vendored = (name) => path.join(here, "..", "utils", "worksheet", imports[name].replace(/^\//, ""));
assert.equal(Object.keys(imports).length, 13);
for (const name of Object.keys(imports)) {
  assert.ok(fs.existsSync(vendored(name)), `${name} is not where the page looks for it`);
  assert.ok(fs.existsSync(path.join(path.dirname(vendored(name)), `LICENSE-${name}.txt`)), `${name} has no licence beside it`);
}
const { registerHooks } = await import("node:module");
if (!registerHooks) console.log("(richdoc.js is not checked here: this node cannot be told where modules are)");
else {
  registerHooks({
    resolve: (specifier, context, next) => (specifier in imports
      ? { url: pathToFileURL(vendored(specifier)).href, shortCircuit: true, format: "module" } : next(specifier, context)),
  });
  // (ProseMirror asks the browser what kind it is when it is loaded: the
  // stand-in document above is none it would know.)
  const standIn = globalThis.document;
  delete globalThis.document;
  const rich = await import(js("richdoc.js"));
  const { Fragment, Slice } = await import("prosemirror-model");
  globalThis.document = standIn;
  const { schema } = rich;
  const N = schema.nodes;

  // Every text goes into the document — one its schema allows — and comes
  // back: untouched, as it stood; written anew, reading the same.
  for (const source of [...KNOWN, ...sources]) {
    const { doc, kept } = rich.toDoc(source);
    doc.check();
    const tidy = (written_) => written_.replace(/\r\n?/g, "\n").replace(/^\s*\n/, "").replace(/\s+$/, "");
    assert.equal(tidy(rich.toMarkdown(doc, kept, source).text), tidy(source));
    const anew = rich.toMarkdown(doc);
    assert.ok(anew.ok && sameReading(parseMarkdown(anew.text), parseMarkdown(source)), `through the editor, this reads differently:\n${source}`);
  }
  // A block changed and changed back — another node that says the same — is as it stood.
  const spelled = "__bold__ text\n\n* item";
  const first = rich.toDoc(spelled);
  const retyped = N.doc.create(null, [N.paragraph.create(null, first.doc.child(0).content), first.doc.child(1)]);
  assert.ok(retyped.child(0) !== first.doc.child(0));
  assert.equal(rich.toMarkdown(retyped, first.kept, spelled).text, spelled);

  // What only the editor can make is written as markdown can say it.
  const say = (...nodes) => {
    const made = rich.toMarkdown(N.doc.create(null, nodes));
    assert.ok(made.ok, `this does not read as what is shown: ${made.text}`);
    return made.text;
  };
  const para = (...content) => N.paragraph.create(null, content);
  const marked = (words, ...marks) => schema.text(words, marks.map((mark) => (typeof mark === "string" ? schema.marks[mark].create() : mark)));
  assert.equal(say(para(marked("a`b`c", "code"))), "a\\`b\\`c");                            // code cannot hold a backtick
  assert.equal(say(para(marked("x = 1", "code"))), "`x = 1`");
  assert.equal(say(para(marked("x", "strong", "em", "u"))), "***<u>x</u>***");
  assert.equal(say(para(schema.text("see https://example.org/x."))), "see https://example.org/x.");
  const there = schema.marks.link.create({ href: "https://example.org/x" });
  assert.equal(say(para(marked("https://example.org/x", there))), "https://example.org/x");  // a link that shows its own address
  assert.equal(say(para(marked("there", there))), "[there](https://example.org/x)");
  // A formula on a line of its own stands on a line of its own; what is empty is nothing.
  assert.equal(say(para(N.math.create({ tex: "x", big: true }), schema.text(" then"))), "$$x$$\n\nthen");
  assert.equal(say(para(), N.heading.create({ level: 2 }), para(schema.text("x")), N.math_block.create({ tex: " " }), para()), "x");
  assert.equal(say(N.blockquote.create(null, para()), N.bullet_list.create(null, N.list_item.create(null, para()))), "");

  // Text brought in line by line (pasted) keeps its lines.
  const brought = (source, options) => say(...rich.blockContent(source, options));
  assert.equal(brought("a\nb *c*\n\n- d\n  e"), "a b *c*\n\n- d e");
  assert.equal(brought("a\nb *c*\n\n- d\n  e\n\n> f\n> g", { lines: true }), "a\\\nb *c*\n\n- d e\n\n> f\\\n> g");

  // A piece cut out of a text (what is copied), as markdown: the bare text
  // from inside a block, code as it is, lists and tables as what they are.
  const { doc } = rich.toDoc("# Head **bold**\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\nx*y*z\n```");
  const find = (words) => {
    let at = -1;
    doc.descendants((node, pos) => { if (at < 0 && node.isText && node.text.includes(words)) at = pos + node.text.indexOf(words); });
    return at;
  };
  const cut = (from, to) => rich.sliceMarkdown(doc.slice(from, to, true));       // (cut as a selection is: with all around it)
  assert.equal(cut(find("ead"), find("bold") + 2), "ead **bo**");
  assert.equal(cut(find("ne"), find("two") + 2), "- ne\n- tw");
  assert.equal(rich.sliceMarkdown(doc.slice(find("ne"), find("two") + 2)), "- ne\n- tw");       // (or with no more than it needs)
  assert.equal(cut(find("x*y"), find("x*y") + 5), "x*y*z");
  assert.equal(cut(0, doc.content.size), "# Head **bold**\n\n- one\n- two\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n```\nx*y*z\n```");
  const row = doc.child(2).child(1);
  assert.equal(rich.sliceMarkdown(new Slice(Fragment.from(row), 1, 1)), "| 1 | 2 |\n| --- | --- |");      // cells picked in a table
  assert.equal(rich.sliceMarkdown(new Slice(Fragment.from(N.table_row.create(null, row.child(0))), 1, 1)), "1");
  assert.equal(rich.sliceMarkdown(Slice.empty), "");

  // A link goes where it is safe to go; a picture is shown from where one may be shown.
  assert.deepEqual(rich.linkTo(" https://example.org/a b(c) "), { href: "https://example.org/a%20b(c%29" });
  for (const bad of ["javascript:alert(1)", "data:text/html,x", "vbscript:x", "file:///C:/x", "", null]) assert.equal(rich.linkTo(bad), false);
  assert.ok(rich.pictureAllowed("attachment:a.png") && rich.pictureAllowed("https://example.org/a.png"));
  assert.ok(!rich.pictureAllowed("javascript:alert(1)") && !rich.pictureAllowed("file:///C:/a.png"));
}

console.log("FRONTEND_OK");
