// Markdown as the worksheet reads it: text → a tree → text again.
//
// One reading serves three uses.  The page draws the tree (markdown.js),
// the formatted editor edits it (richdoc.js, richtext.js), and what was
// edited is written back as markdown from the tree.  So what is edited
// is what was shown, and what is shown afterwards is what was edited.
// Nothing here touches the page: the tree is plain data.
//
// Blocks:  {t: "p", c}  {t: "h", n, c}  {t: "hr"}  {t: "quote", c: blocks}
//          {t: "code", v, info}  {t: "math", v}
//          {t: "list", ordered, items: [{c}]}       (an item's c: inline nodes, and lists within it)
//          {t: "table", align: [], head: [c], rows: [[c]]}
// Inline:  {t: "text", v}  {t: "code", v}  {t: "math", v, block}  {t: "br"}
//          {t: "img", src, alt, raw}  {t: "link", href, c}  {t: "auto", href}
//          {t: "strong" | "em" | "del" | "u" | "sub" | "sup", c}
// A block at the top of a text also says which lines it was read from
// (`from`, `to`), so that a block nobody touched can be kept as written.

export const SAFE_LINK = /^(https?:|mailto:|#|\.{0,2}\/|[\w.-]+(\/|$))/i;
export const SAFE_IMAGE = /^(https?:|data:image\/(png|jpeg|gif|webp);|\.{0,2}\/|[\w.-]+(\/|$))/i;
export const ATTACHED = "attachment:";

/**
 * An address as markdown can hold it between its brackets: without
 * blanks, without a closing bracket.  (It is made so where it comes into
 * a text — typed for a link, pasted — and written as it is.)
 */
export const address = (href) => href.trim().replace(/\s/g, "%20").replace(/\)/g, "%29");

// ---- reading -----------------------------------------------------------------

// Formatting markdown has no mark for is written as HTML's own tag — and
// read from it; every other tag in a text is shown as the text it is.
const TAGS = { u: "u", sub: "sub", sup: "sup", b: "strong", i: "em", s: "del" };

// One alternative per inline construct; the first match in the text wins.
const INLINE = new RegExp([
  /\\([\\`*_{}\[\]()#+\-.!$~|<>])/.source,                   // 1  escaped character
  /`([^`\n]+)`/.source,                                       // 2  code
  /\$\$([^$]+?)\$\$/.source,                                  // 3  display math
  /\$((?:\\.|[^$\\\n])+?)\$/.source,                          // 4  inline math
  /!\[((?:\\.|[^\]\\])*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/.source,       // 5,6 image
  /\[((?:\\.|!\[(?:\\.|[^\]\\])*\]\([^)\s]+(?:\s+"[^"]*")?\)|[^\]\\])+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/.source,   // 7,8 link (a picture may be its text)
  /\*\*(.+?)\*\*(?!\*)|__(.+?)__(?!_)/.source,                // 9,10 strong (may end in nested *em*)
  /\*([^*\s](?:[^*]*[^*\s])?)\*|(?<![\w])_([^_\s](?:[^_]*[^_\s])?)_(?![\w])/.source,  // 11,12 emphasis
  /~~(.+?)~~/.source,                                         // 13 strike
  /<(u|sub|sup|b|i|s)>(.+?)<\/\14>/.source,                   // 14,15 a tag of TAGS
  /<br\s*\/?>/.source,                                        // line break
  /(https?:\/\/[^\s<>)]+[^\s<>).,;:!?])/.source,              // 16 bare link
  /( {2,}|\\)\n/.source,                                      // 17 hard break
].join("|"), "g");

/** Add inline nodes to a list; text joins the text before it. */
function add(list, ...nodes) {
  for (const node of nodes) {
    const last = list[list.length - 1];
    if (node.t === "text" && last && last.t === "text") last.v += node.v;
    else list.push(node.t === "text" ? { t: "text", v: node.v } : node);
  }
  return list;
}

const unescaped = (text) => text.replace(/\\([\\\]\[])/g, "$1");

/** The inline nodes of a piece of markdown. */
export function parseInline(text) {
  const out = [];
  const say = (v) => add(out, { t: "text", v });
  let last = 0;
  // matchAll works on a private copy of the pattern: this function
  // calls itself for nested emphasis, and a shared `lastIndex` would
  // send the outer scan back to the start.
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) say(text.slice(last, m.index));
    last = m.index + m[0].length;
    if (m[1] !== undefined) say(m[1]);
    else if (m[2] !== undefined) out.push({ t: "code", v: m[2] });
    else if (m[3] !== undefined) out.push({ t: "math", v: m[3], block: true });
    else if (m[4] !== undefined) out.push({ t: "math", v: m[4], block: false });
    else if (m[6] !== undefined) {
      if (m[6].startsWith(ATTACHED) || SAFE_IMAGE.test(m[6])) out.push({ t: "img", src: m[6], alt: unescaped(m[5]), raw: m[0] });
      else say(m[0]);
    } else if (m[8] !== undefined) {
      if (SAFE_LINK.test(m[8])) out.push({ t: "link", href: m[8], c: parseInline(m[7]) });
      else say(m[0]);
    } else if (m[9] !== undefined || m[10] !== undefined) out.push({ t: "strong", c: parseInline(m[9] ?? m[10]) });
    else if (m[11] !== undefined || m[12] !== undefined) out.push({ t: "em", c: parseInline(m[11] ?? m[12]) });
    else if (m[13] !== undefined) out.push({ t: "del", c: parseInline(m[13]) });
    else if (m[14] !== undefined) out.push({ t: TAGS[m[14]], c: parseInline(m[15]) });
    else if (m[16] !== undefined) out.push({ t: "auto", href: m[16] });
    else out.push({ t: "br" });
  }
  if (last < text.length) say(text.slice(last));
  return out;
}

const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_RULE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const FENCE = /^(\s*)(```+|~~~+)\s*(\S*)\s*$/;
// What ends a paragraph without a blank line before it.
const INTERRUPTS = /^(#{1,6}\s|\s*>|\s*(```|~~~)|\s*\$\$)/;

function cells(line) {
  return line.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

function list(lines, start) {
  // Items of one list share the indentation of its first marker;
  // deeper-indented markers open a nested list inside the last item.
  const first = LIST_ITEM.exec(lines[start]);
  const indent = first[1].length;
  const node = { t: "list", ordered: /\d/.test(first[2]), items: [] };
  let i = start, item = null;
  while (i < lines.length) {
    const m = LIST_ITEM.exec(lines[i]);
    if (m && m[1].length === indent) {
      item = { c: parseInline(m[3]) };
      node.items.push(item);
      i += 1;
    } else if (m && m[1].length > indent && item) {
      let inner;
      [inner, i] = list(lines, i);
      item.c.push(inner);
    } else if (lines[i].trim() && /^\s+/.test(lines[i]) && item) {
      add(item.c, { t: "text", v: " " }, ...parseInline(lines[i].trim()));     // a wrapped line of the item
      i += 1;
    } else break;
  }
  return [node, i];
}

/** The blocks of a markdown text. */
export function parseMarkdown(source) {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    const from = i;
    let block;
    let m = FENCE.exec(line);
    if (m) {                                             // fenced code
      const body = [];
      for (i += 1; i < lines.length && !lines[i].trim().startsWith(m[2]); i += 1) body.push(lines[i]);
      i += 1;
      block = { t: "code", v: body.join("\n"), info: m[3] };
    } else if (line.trim().startsWith("$$")) {           // display math block
      const body = [line.trim().slice(2)];
      while (!/\$\$\s*$/.test(body[body.length - 1]) && i + 1 < lines.length) body.push(lines[++i]);
      i += 1;
      block = { t: "math", v: body.join("\n").replace(/\$\$\s*$/, "") };
    } else if ((m = HEADING.exec(line))) {
      block = { t: "h", n: m[1].length, c: parseInline(m[2]) };
      i += 1;
    } else if (RULE.test(line)) {
      block = { t: "hr" };
      i += 1;
    } else if (/^\s*>/.test(line)) {
      const body = [];
      for (; i < lines.length && /^\s*>/.test(lines[i]); i += 1) body.push(lines[i].replace(/^\s*>\s?/, ""));
      block = { t: "quote", c: parseMarkdown(body.join("\n")) };
    } else if (LIST_ITEM.test(line)) {
      [block, i] = list(lines, i);
    } else if (line.includes("|") && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])
               && cells(lines[i + 1]).length === cells(line).length) {
      // The rule under the header — a cell of dashes for each column —
      // also says how the column is set: :--- left, :---: centred, ---: right.
      const align = cells(lines[i + 1]).map((rule) => (
        rule.endsWith(":") ? (rule.startsWith(":") ? "center" : "right") : rule.startsWith(":") ? "left" : null));
      block = { t: "table", align, head: cells(line).map(parseInline), rows: [] };
      for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i += 1) {
        block.rows.push(cells(lines[i]).map(parseInline));
      }
    } else {
      const para = [lines[i++]];          // always consumes a line: the loop must advance
      for (; i < lines.length && lines[i].trim() && !INTERRUPTS.test(lines[i]) && !LIST_ITEM.test(lines[i]); i += 1) {
        para.push(lines[i]);
      }
      block = { t: "p", c: parseInline(para.join("\n")) };
    }
    block.from = from;
    block.to = Math.min(i, lines.length);
    blocks.push(block);
  }
  return blocks;
}

// ---- what a text says, whichever way it was written ----------------------------

// The marks a run of text can carry, outermost first when they are written.
export const MARKS = ["link", "strong", "em", "del", "u", "sub", "sup", "code"];

/**
 * Inline nodes as a flat list of runs — {text, marks} and, for what is
 * no text, {t, …, marks} — with `marks` like "strong em" (a link's is
 * "link:" and its address).  Blanks carry no marks and never come two
 * at a time, so two ways of writing the same thing give the same runs.
 */
export function runs(nodes, marks = [], whole = true) {
  const said = marks.slice().sort((a, b) => MARKS.indexOf(a.split(":")[0]) - MARKS.indexOf(b.split(":")[0])).join(" ");
  const pieces = [];
  for (const node of nodes) {
    if (node.t === "text" || node.t === "code" || node.t === "auto") {
      const mine = node.t === "code" ? [said, "code"].filter(Boolean).join(" ") : said;
      for (const piece of (node.t === "auto" ? node.href : node.v).split(/(\s+)/)) {
        if (piece) pieces.push(/^\s+$/.test(piece) ? { text: " ", marks: "" } : { text: piece, marks: mine });
      }
    } else if (node.t === "math") pieces.push({ t: "math", v: node.v.trim(), block: Boolean(node.block), marks: said });
    else if (node.t === "img") pieces.push({ t: "img", src: node.src, alt: node.alt || "", marks: said });
    else if (node.t === "br") pieces.push({ t: "br", marks: "" });
    else if (node.t === "list") continue;                                  // (an item's lists are told apart by the caller)
    else {
      const mark = node.t === "link" ? `link:${node.href}` : node.t;
      pieces.push(...runs(node.c, marks.includes(mark) ? marks : [...marks, mark], false));
    }
  }
  if (!whole) return pieces;
  // Blanks come one at a time and at neither end; text that touches is one run.
  const out = [];
  for (const piece of pieces) {
    const last = out[out.length - 1];
    const blank = piece.text === " ";
    if (blank && (!last || last.text === " ")) continue;
    if (!blank && piece.text !== undefined && last && last.text !== undefined && last.text !== " " && last.marks === piece.marks) {
      last.text += piece.text;
    } else out.push({ ...piece });
  }
  while (out.length && out[out.length - 1].text === " ") out.pop();
  return out;
}

/** A block as what it says: the same for every way of writing it. */
function said(block) {
  switch (block.t) {
    case "p": return { t: "p", c: runs(block.c) };
    case "h": return { t: "h", n: block.n, c: runs(block.c) };
    case "quote": return { t: "quote", c: reading(block.c) };
    case "code": return { t: "code", v: block.v, info: block.info || "" };
    case "math": return { t: "math", v: block.v.trim() };
    case "list": return { t: "list", ordered: Boolean(block.ordered),
                          items: block.items.map((item) => ({ c: runs(item.c),
                                                              lists: item.c.filter((n) => n.t === "list").map(said).filter(says) }))
                            .filter((item) => item.c.length || item.lists.length) };
    case "table": {
      const wide = Math.max(block.head.length, ...block.rows.map((row) => row.length));
      const row = (cells_) => Array.from({ length: wide }, (_, k) => runs(cells_[k] || []));
      return { t: "table", align: Array.from({ length: wide }, (_, k) => block.align[k] || null),
               head: row(block.head), rows: block.rows.map(row) };
    }
    default: return { t: block.t };
  }
}

/** Does a block (as `said` tells it) say anything?  A paragraph, a formula, a list with nothing in it does not. */
function says(block) {
  switch (block.t) {
    case "p": case "h": case "quote": return block.c.length > 0;
    case "math": return Boolean(block.v);
    case "list": return block.items.length > 0;
    default: return true;
  }
}

/** What a list of blocks says. */
export function reading(blocks) {
  return blocks.map(said).filter(says);
}

/** Do two lists of blocks say the same? */
export function sameReading(a, b) {
  return JSON.stringify(reading(a)) === JSON.stringify(reading(b));
}

/** Runs as nested inline nodes, each mark opened once for as long as it lasts. */
export function nested(list) {
  const marksOf = (run) => (run.marks ? run.marks.split(" ") : []);
  const build = (items, depth) => {
    const out = [];
    let i = 0;
    while (i < items.length) {
      const mark = marksOf(items[i])[depth];
      if (!mark) {
        const run = items[i];
        if (run.text !== undefined) add(out, { t: "text", v: run.text });
        else if (run.t === "math") out.push({ t: "math", v: run.v, block: run.block });
        else if (run.t === "img") out.push({ t: "img", src: run.src, alt: run.alt });
        else out.push({ t: "br" });
        i += 1;
        continue;
      }
      let j = i;
      while (j < items.length && marksOf(items[j])[depth] === mark) j += 1;
      const inner = build(items.slice(i, j), depth + 1);
      if (mark === "code") out.push({ t: "code", v: inner.map((n) => n.v || "").join("") });
      else if (mark.startsWith("link:")) out.push({ t: "link", href: mark.slice(5), c: inner });
      else out.push({ t: mark, c: inner });
      i = j;
    }
    return out;
  };
  return build(list, 0);
}

// ---- writing -------------------------------------------------------------------

// How carefully text is written.  Plainly first: a text nobody would
// misread is left as it is.  Where that reads differently, every
// character markdown could take for a mark gets a backslash; and where
// even that does (marks that touch, a star inside emphasis), bold,
// italic and struck text are written as the tags of TAGS.
const PLAIN = 0, ESCAPED = 1, TAGGED = 2;

function writeText(text, care) {
  if (care === PLAIN) return text.replace(/\\(?=[\\`*_{}\[\]()#+\-.!$~|<>]|$)/g, "\\\\");
  return text.replace(/[\\`*_$\[\]~<]/g, "\\$&");
}

/** Blanks at the ends of what a mark holds go outside it: "**bold **" is not bold everywhere. */
function around(inner, open, close) {
  const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(inner);
  return m[2] ? m[1] + open + m[2] + close + m[3] : inner;
}

/** Inline nodes as markdown.  `line`: everything must stay on one line (a heading, an item, a cell). */
function writeInline(nodes, care, line = false) {
  let out = "";
  for (const node of nodes) {
    switch (node.t) {
      case "text": out += writeText(line ? node.v.replace(/\s*\n\s*/g, " ") : node.v, care); break;
      case "code": out += "`" + node.v + "`"; break;
      case "math": out += node.block ? `$$${node.v}$$` : `$${node.v}$`; break;
      case "br": out += line ? "<br>" : "\\\n"; break;
      case "img": out += `![${node.alt.replace(/[\\\[\]]/g, "\\$&")}](${node.src})`; break;
      case "auto": out += node.href; break;
      case "link": out += `[${writeInline(node.c, care, true)}](${node.href})`; break;
      case "strong": out += care === TAGGED ? `<b>${writeInline(node.c, care, line)}</b>` : around(writeInline(node.c, care, line), "**", "**"); break;
      case "em": out += care === TAGGED ? `<i>${writeInline(node.c, care, line)}</i>` : around(writeInline(node.c, care, line), "*", "*"); break;
      case "del": out += care === TAGGED ? `<s>${writeInline(node.c, care, line)}</s>` : around(writeInline(node.c, care, line), "~~", "~~"); break;
      default: out += `<${node.t}>${writeInline(node.c, care, line)}</${node.t}>`;      // u, sub, sup
    }
  }
  return out;
}

/** A line of a paragraph that would be read as the start of something else gets a backslash. */
function guard(line) {
  const m = LIST_ITEM.exec(line);
  if (m) return m[1] + (/\d/.test(m[2]) ? m[2].slice(0, -1) + "\\" + m[2].slice(-1) : "\\" + m[2]) + line.slice(m[1].length + m[2].length);
  if (/^#{1,6}\s/.test(line) || /^\s*(>|```|~~~|\$\$)/.test(line) || RULE.test(line)) return line.replace(/\S/, "\\$&");
  return line;
}

function writeBlock(block, care) {
  switch (block.t) {
    case "p": return writeInline(block.c, care).trimStart().split("\n").map((line) => guard(line.replace(/[ \t]+$/, ""))).join("\n");
    case "h": {
      // (Hashes at the end of a heading are read as closing it: one that
      // ends in a hash is closed with hashes of its own.)
      const text = writeInline(block.c, care, true).trim();
      const marks = "#".repeat(block.n);
      return /#$/.test(text) ? `${marks} ${text} ${marks}` : `${marks} ${text}`;
    }
    case "hr": return "---";
    case "quote": return writeBlocks(block.c, care).split("\n").map((line) => (line ? "> " + line : ">")).join("\n");
    case "code": {
      const longest = Math.max(2, ...[...block.v.matchAll(/^\s*(`+)/gm)].map((m) => m[1].length));
      const fence = "`".repeat(longest + 1);
      return fence + (block.info || "") + "\n" + block.v + (block.v ? "\n" : "") + fence;
    }
    case "math": return `$$${block.v}$$`;
    case "list": return block.items.map((item, k) => {
      const marker = block.ordered ? `${k + 1}. ` : "- ";
      const text = writeInline(item.c.filter((n) => n.t !== "list"), care, true).trim();
      const inner = item.c.filter((n) => n.t === "list").map((n) => writeBlock(n, care).replace(/^/gm, " ".repeat(marker.length)));
      return [marker + text, ...inner].join("\n");
    }).join("\n");
    case "table": {
      const wide = Math.max(block.head.length, ...block.rows.map((row) => row.length));
      const row = (cells_) => "|" + Array.from({ length: wide }, (_, k) => (
        ` ${writeInline(cells_[k] || [], care, true).trim().replace(/\|/g, "\\|")} |`)).join("");
      const rule = "|" + Array.from({ length: wide }, (_, k) => (
        ` ${{ left: ":--", center: ":-:", right: "--:" }[block.align[k]] || "---"} |`)).join("");
      return [row(block.head), rule, ...block.rows.map(row)].join("\n");
    }
    default: return "";
  }
}

function writeBlocks(blocks, care) {
  return blocks.map((block) => writeBlock(block, care)).filter(Boolean).join("\n\n");
}

/**
 * One block as markdown that reads back as that block: {text, ok}.
 * `ok` is false when no way of writing it does — the text is then the
 * most careful try.
 */
export function written(block) {
  let text = "";
  for (const care of [PLAIN, ESCAPED, TAGGED]) {
    text = writeBlock(block, care);
    if (sameReading(parseMarkdown(text), [block])) return { text, ok: true };
  }
  return { text, ok: !reading([block]).length };
}

/**
 * Blocks as a markdown text: {text, ok}.  A block of `kept` — a map from
 * a block to the lines it was read from — is written as those lines,
 * and blocks that stood next to each other stay as they stood.
 */
export function writeMarkdown(blocks, kept = new Map(), source = "") {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  let text = "", ok = true, before = null;
  for (const block of blocks) {
    const origin = kept.get(block);
    const piece = origin ? { text: lines.slice(origin.from, origin.to).join("\n"), ok: true } : written(block);
    if (!piece.text) continue;
    ok = ok && piece.ok;
    // Two kept blocks with nothing but blank lines between them stay as far apart as they were.
    const together = origin && before && origin.from >= before.to
      && lines.slice(before.to, origin.from).every((line) => !line.trim());
    if (text) text += together ? "\n".repeat(origin.from - before.to + 1) : "\n\n";
    text += piece.text;
    before = origin || null;
  }
  return { text, ok };
}
