// A text region as the formatted editor holds it: a ProseMirror document,
// made from the region's markdown and written back as markdown.
//
// What the document can hold is what the markdown reading (mdtree.js)
// knows: nothing can be put into it — typed, pasted or dropped — that
// could not be written back.  A block of the text that nobody touched is
// written back exactly as it stood in the file.

import { Schema } from "prosemirror-model";
import { tableNodes } from "prosemirror-tables";
import { address, ATTACHED, MARKS, nested, parseInline, parseMarkdown, SAFE_IMAGE, SAFE_LINK, writeMarkdown } from "./mdtree.js";

const BARE_LINK = /^https?:\/\/[^\s<>)]+[^\s<>).,;:!?]$/;
const SIDES = ["left", "center", "right"];

/** May a picture with this address be put into a text?  (One pasted as data is, once it has been attached.) */
export const pictureAllowed = (src) => src.startsWith(ATTACHED) || SAFE_IMAGE.test(src);

// How a column is set travels on each of its cells.
const align = {
  default: null,
  getFromDOM: (dom) => SIDES.find((side) => dom.classList.contains(`al-${side}`) || dom.style.textAlign === side
                                            || dom.getAttribute("align") === side) || null,
  setDOMAttr: (value, attrs) => { if (value) attrs.class = `al-${value}`; },
};

// A formula brought in from elsewhere, as it can be written between dollars.
const tex = (dom) => {
  const found = (dom.getAttribute("data-tex") || "").replace(/(?<!\\)\$/g, "").replace(/\s+/g, " ").trim();
  return found ? { tex: found, big: dom.hasAttribute("data-big") } : false;
};

/** The link a text may carry to this address — or none (false). */
export function linkTo(href) {
  const to = address(href || "");
  return to && SAFE_LINK.test(to) ? { href: to } : false;
}

const tag = (name, more = []) => ({ parseDOM: [{ tag: name }, ...more], toDOM: () => [name, 0] });
const styled = (property, pattern) => ({ style: property, getAttrs: (value) => pattern.test(value) && null });

export const schema = new Schema({
  nodes: {
    doc: { content: "block+" },
    paragraph: { group: "block", content: "inline*", ...tag("p") },
    heading: { group: "block", content: "inline*", defining: true, attrs: { level: { default: 1 } },
               parseDOM: [1, 2, 3, 4, 5, 6].map((level) => ({ tag: `h${level}`, attrs: { level } })),
               toDOM: (node) => [`h${node.attrs.level}`, 0] },
    blockquote: { group: "block", content: "block+", defining: true, ...tag("blockquote") },
    code_block: { group: "block", content: "text*", marks: "", code: true, defining: true, attrs: { info: { default: "" } },
                  parseDOM: [{ tag: "pre", preserveWhitespace: "full" }], toDOM: () => ["pre", ["code", 0]] },
    horizontal_rule: { group: "block", parseDOM: [{ tag: "hr" }], toDOM: () => ["hr"] },
    math_block: { group: "block", atom: true, attrs: { tex: { default: "" } },
                  parseDOM: [{ tag: "div[data-tex]", getAttrs: (dom) => { const found = tex(dom); return found && { tex: found.tex }; } }],
                  toDOM: (node) => ["div", { "data-tex": node.attrs.tex }, `$$${node.attrs.tex}$$`] },
    bullet_list: { group: "block", content: "list_item+", ...tag("ul") },
    ordered_list: { group: "block", content: "list_item+", ...tag("ol") },
    list_item: { content: "paragraph (bullet_list | ordered_list)*", defining: true, ...tag("li") },
    ...tableNodes({ tableGroup: "block", cellContent: "inline*", cellAttributes: { align } }),
    text: { group: "inline" },
    math: { group: "inline", inline: true, atom: true, attrs: { tex: { default: "" }, big: { default: false } },
            parseDOM: [{ tag: "span[data-tex]", getAttrs: tex }],
            toDOM: (node) => ["span", { "data-tex": node.attrs.tex, ...(node.attrs.big ? { "data-big": "" } : {}) },
                              node.attrs.big ? `$$${node.attrs.tex}$$` : `$${node.attrs.tex}$`] },
    image: { group: "inline", inline: true, atom: true, attrs: { src: {}, alt: { default: "" } },
             parseDOM: [{ tag: "img[src]", getAttrs: (dom) => {
               const given = dom.getAttribute("src"), alt = dom.getAttribute("alt") || "";
               // One that comes as data is attached to the region before it is put in (RichEditor.adopt).
               if (/^data:image\//.test(given)) return { src: given, alt };
               const src = address(given);
               return pictureAllowed(src) ? { src, alt } : false;
             } }],
             toDOM: (node) => ["img", { src: node.attrs.src, alt: node.attrs.alt }] },
    hard_break: { group: "inline", inline: true, selectable: false, parseDOM: [{ tag: "br" }], toDOM: () => ["br"] },
  },
  // (In the order they are written around a text, outermost first: MARKS.)
  marks: {
    link: { attrs: { href: {} }, inclusive: false, excludes: "link u",
            parseDOM: [{ tag: "a[href]", getAttrs: (dom) => linkTo(dom.getAttribute("href")) }],
            toDOM: (mark) => ["a", { href: mark.attrs.href, target: "_blank", rel: "noopener noreferrer" }, 0] },
    strong: tag("strong", [{ tag: "b", getAttrs: (dom) => dom.style.fontWeight !== "normal" && null },
                           styled("font-weight", /^(bold(er)?|[5-9]\d{2,})$/)]),
    em: tag("em", [{ tag: "i", getAttrs: (dom) => dom.style.fontStyle !== "normal" && null }, styled("font-style", /^italic$/)]),
    del: tag("del", [{ tag: "s" }, { tag: "strike" }, styled("text-decoration", /line-through/)]),
    u: tag("u", [{ tag: "ins" }, styled("text-decoration", /underline/)]),
    sub: { excludes: "sub sup", ...tag("sub", [styled("vertical-align", /^sub$/)]) },
    sup: { excludes: "sub sup", ...tag("sup", [styled("vertical-align", /^super$/)]) },
    code: { code: true, ...tag("code") },
  },
});

const { nodes: N, marks: M } = schema;

// ---- markdown → document ------------------------------------------------------

function inlineNodes(tree, marks = [], lines = false) {
  const out = [];
  for (const node of tree) {
    if (node.t === "text") {
      // Where a line of the source ends, the text goes on after a blank —
      // or, in text brought in line by line (`lines`), on a new line.
      const parts = lines ? node.v.split(/[ \t]*\n[ \t]*/) : [node.v.replace(/\s*\n\s*/g, " ")];
      parts.forEach((part, k) => {
        if (k) out.push(N.hard_break.create());
        if (part) out.push(schema.text(part, marks));
      });
    } else if (node.t === "code") out.push(schema.text(node.v, M.code.create().addToSet(marks)));
    else if (node.t === "math") out.push(N.math.create({ tex: node.v.trim(), big: Boolean(node.block) }, null, marks));
    else if (node.t === "img") out.push(N.image.create({ src: node.src, alt: node.alt || "" }, null, marks));
    else if (node.t === "br") out.push(N.hard_break.create());
    else if (node.t === "auto") out.push(schema.text(node.href, M.link.create({ href: node.href }).addToSet(marks)));
    else if (node.t !== "list") {
      const mark = node.t === "link" ? M.link.create({ href: node.href }) : M[node.t].create();
      out.push(...inlineNodes(node.c, mark.addToSet(marks), lines));
    }
  }
  return out;
}

function listNode(block) {
  return (block.ordered ? N.ordered_list : N.bullet_list).create(null, block.items.map((item) => N.list_item.create(null, [
    N.paragraph.create(null, inlineNodes(item.c)), ...item.c.filter((n) => n.t === "list").map(listNode)])));
}

function blockNode(block, lines = false) {
  switch (block.t) {
    case "h": return N.heading.create({ level: block.n }, inlineNodes(block.c));
    case "hr": return N.horizontal_rule.create();
    case "quote": return N.blockquote.create(null, block.c.length ? block.c.map((inner) => blockNode(inner, lines))
                                                                    : [N.paragraph.create()]);
    case "code": return N.code_block.create({ info: block.info || "" }, block.v ? schema.text(block.v) : null);
    case "math": return N.math_block.create({ tex: block.v.trim() });
    case "list": return listNode(block);
    case "table": {
      const wide = Math.max(block.head.length, ...block.rows.map((row) => row.length));
      const row = (cells, type) => N.table_row.create(null, Array.from({ length: wide }, (_, k) => (
        type.create({ align: block.align[k] || null }, inlineNodes(cells[k] || [])))));
      return N.table.create(null, [row(block.head, N.table_header), ...block.rows.map((cells) => row(cells, N.table_cell))]);
    }
    default: return N.paragraph.create(null, inlineNodes(block.c, [], lines));
  }
}

/** Markdown as inline content, to put into a text at the caret. */
export function inlineContent(markdown) {
  return inlineNodes(parseInline(markdown));
}

/**
 * Markdown as blocks, to put into a document.  With `lines`, the lines
 * of a paragraph stay lines: text typed elsewhere line by line is not
 * run together.
 */
export function blockContent(markdown, { lines = false } = {}) {
  return parseMarkdown(markdown).map((block) => blockNode(block, lines));
}

/**
 * The document for a region's markdown: {doc, kept}.  `kept` knows, for
 * every block as it was read, the lines it stood on.
 */
export function toDoc(source) {
  const kept = new Map();
  const blocks = parseMarkdown(source).map((block) => {
    const node = blockNode(block);
    kept.set(node, { from: block.from, to: block.to });
    return node;
  });
  return { doc: N.doc.create(null, blocks.length ? blocks : [N.paragraph.create()]), kept };
}

// ---- document → markdown ------------------------------------------------------

function inlineTree(parent) {
  const list = [];
  parent.forEach((node) => {
    let marks = node.marks.map((mark) => (mark.type === M.link ? `link:${mark.attrs.href}` : mark.type.name));
    if (node.isText) {
      // What markdown cannot say inside code, it says as plain text.
      if (marks.includes("code") && /[`\n]/.test(node.text)) marks = marks.filter((mark) => mark !== "code");
      list.push({ text: node.text, marks });
    } else if (node.type === N.math) list.push({ t: "math", v: node.attrs.tex, block: node.attrs.big, marks });
    else if (node.type === N.image) list.push({ t: "img", src: node.attrs.src, alt: node.attrs.alt, marks });
    else list.push({ t: "br", marks: [] });
  });
  const order = (mark) => MARKS.indexOf(mark.split(":")[0]);
  const tree = nested(list.map((run) => ({ ...run, marks: run.marks.slice().sort((a, b) => order(a) - order(b)).join(" ") })));
  // A link that shows its own address is written as the address alone
  // (an address written out is a link wherever it is read).
  const plain = (nodes) => nodes.map((node) => (
    node.t === "link" && node.c.length === 1 && node.c[0].t === "text" && node.c[0].v === node.href && BARE_LINK.test(node.href)
      ? { t: "auto", href: node.href } : node.c ? { ...node, c: plain(node.c) } : node));
  return plain(tree);
}

function listBlock(node) {
  const items = [];
  node.forEach((item) => {
    const c = [...inlineTree(item.firstChild)];
    item.forEach((child, _, index) => { if (index) c.push(listBlock(child)); });
    if (c.length) items.push({ c });
  });
  return items.length ? { t: "list", ordered: node.type === N.ordered_list, items } : null;
}

/** The blocks a node of the document is written as (none, for one with nothing in it). */
function blocksOf(node) {
  switch (node.type) {
    case N.paragraph: {
      const c = inlineTree(node);
      // A displayed formula at the head of a paragraph stands on a line of its own.
      if (c.length && c[0].t === "math" && c[0].block) return [{ t: "math", v: c[0].v }, ...(c.length > 1 ? [{ t: "p", c: c.slice(1) }] : [])];
      return c.length ? [{ t: "p", c }] : [];
    }
    case N.heading: return node.content.size ? [{ t: "h", n: node.attrs.level, c: inlineTree(node) }] : [];
    case N.horizontal_rule: return [{ t: "hr" }];
    case N.blockquote: {
      const c = [];
      node.forEach((child) => c.push(...blocksOf(child)));
      return c.length ? [{ t: "quote", c }] : [];
    }
    case N.code_block: return [{ t: "code", v: node.textContent, info: node.attrs.info.split(/\s/)[0] }];
    case N.math_block: return node.attrs.tex.trim() ? [{ t: "math", v: node.attrs.tex.trim() }] : [];
    case N.table: {
      const rows = [];
      node.forEach((row) => { const cells = []; row.forEach((cell) => cells.push(cell)); rows.push(cells); });
      return [{ t: "table", align: rows[0].map((cell) => cell.attrs.align), head: rows[0].map(inlineTree),
                rows: rows.slice(1).map((cells) => cells.map(inlineTree)) }];
    }
    default: { const block = listBlock(node); return block ? [block] : []; }
  }
}

/**
 * A piece cut from a document (a ProseMirror slice) as markdown: what is
 * copied out of a text as plain text.  A piece from inside a paragraph, a
 * heading or an item is the bare text; code is the code as it is.
 */
export function sliceMarkdown(slice) {
  let { content, openStart, openEnd } = slice;
  while (openStart > 0 && openEnd > 0 && content.childCount === 1) {
    const only = content.firstChild;
    if (only.type.spec.code) return only.textContent;
    if (!only.isTextblock && only.childCount !== 1) break;         // two items of a list: still a list
    content = only.content;
    openStart -= 1;
    openEnd -= 1;
    if (only.isTextblock) break;
  }
  if (!content.childCount) return "";
  if (content.firstChild.isInline) return toMarkdown(N.doc.create(null, N.paragraph.create(null, content))).text;
  // Rows cut out of a table are written as a table again, items as a list.
  const blocks = [];
  let run = [];
  const close = () => {
    if (run.length) blocks.push((run[0].type === N.table_row ? N.table : N.bullet_list).create(null, run));
    run = [];
  };
  content.forEach((node) => {
    const together = node.type === N.table_row || node.type === N.list_item;
    if (!together || (run.length && run[0].type !== node.type)) close();
    if (together) run.push(node);
    else if (node.type === N.table_cell || node.type === N.table_header) blocks.push(N.paragraph.create(null, node.content));
    else blocks.push(node);
  });
  close();
  return toMarkdown(N.doc.create(null, blocks)).text;
}

/**
 * The document as markdown: {text, ok}.  Blocks of `kept` are written as
 * they stood in `source`; `ok` is false when a block cannot be written
 * so that it reads back as what the document shows.
 */
export function toMarkdown(doc, kept = new Map(), source = "") {
  const blocks = [], origin = new Map();
  const stood = [...kept];
  doc.forEach((node) => {
    // As it stood: a block nobody touched — or one changed and changed back.
    const lines = kept.get(node) || (stood.find(([was]) => was.eq(node)) || [])[1];
    const made = lines ? [{ t: "kept" }] : blocksOf(node);
    blocks.push(...made);
    if (lines) origin.set(made[0], lines);
  });
  return writeMarkdown(blocks, origin, source);
}
