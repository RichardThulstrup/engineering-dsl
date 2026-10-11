// Markdown for text regions, drawn straight to DOM nodes.
//
// Text regions are notebook markdown cells, so a sheet may have been
// written by someone else.  Nothing here ever assigns markup: the text
// is read into a tree (mdtree.js), and every piece of the tree ends up
// in a text node, in an element made here, or in KaTeX (which is given
// `trust: false`).  HTML in the source is shown as text, but for the
// few tags that say what markdown cannot (<u>, <sub>, <sup>, …).  Link
// and image addresses are restricted to harmless schemes; a picture
// attached to the region (`attachment:name`) is shown from what is attached.

import { el } from "./util.js";
import { ATTACHED, parseMarkdown, SAFE_IMAGE } from "./mdtree.js";
import { pictureUrl } from "./pictures.js";

/** The address of the attached picture a text names — null if there is none of that name. */
export function attached(name, pictures) {
  const all = pictures || {};
  let plain = name;
  try { plain = decodeURIComponent(name); } catch (error) { /* not an encoded name */ }
  return pictureUrl(all[name]) || pictureUrl(all[plain]);       // (what is no picture gets no address)
}

/** The address a picture is shown from, or null where it must not be shown. */
export function pictureSource(src, pictures) {
  if (src.startsWith(ATTACHED)) return attached(src.slice(ATTACHED.length), pictures);
  return SAFE_IMAGE.test(src) ? src : null;
}

export function math(source, display) {
  const span = el("span", { class: display ? "tex block" : "tex" });
  try {
    window.katex.render(source, span, { displayMode: display, throwOnError: false,
                                        strict: "ignore", trust: false });
  } catch (error) {
    span.textContent = source;
  }
  return span;
}

const ELEMENT = { strong: "strong", em: "em", del: "del", u: "u", sub: "sub", sup: "sup" };

function inline(nodes, parent, pictures) {
  for (const node of nodes) {
    if (node.t === "text") parent.append(node.v);
    else if (node.t === "code") parent.append(el("code", { text: node.v }));
    else if (node.t === "math") parent.append(math(node.v, node.block));
    else if (node.t === "br") parent.append(el("br"));
    else if (node.t === "img") {
      const src = pictureSource(node.src, pictures);
      if (src) parent.append(el("img", { src, alt: node.alt, draggable: "false" }));
      else parent.append(node.raw);
    } else if (node.t === "link" || node.t === "auto") {
      // (The reading only makes a link of an address it found harmless.)
      const a = parent.appendChild(el("a", { href: node.href, target: "_blank", rel: "noopener noreferrer" }));
      if (node.t === "auto") a.textContent = node.href; else inline(node.c, a, pictures);
    } else if (node.t === "list") list(node, parent, pictures);
    else if (ELEMENT[node.t]) inline(node.c, parent.appendChild(el(ELEMENT[node.t])), pictures);
  }
  return parent;
}

function list(node, parent, pictures) {
  const made = parent.appendChild(el(node.ordered ? "ol" : "ul"));
  for (const item of node.items) inline(item.c, made.appendChild(el("li")), pictures);
}

function blocks(tree, root, pictures) {
  for (const block of tree) {
    if (block.t === "code") root.append(el("pre", {}, el("code", { text: block.v })));
    else if (block.t === "math") root.append(el("div", { class: "katex-display" }, math(block.v, true)));
    else if (block.t === "h") inline(block.c, root.appendChild(el("h" + block.n)), pictures);
    else if (block.t === "hr") root.append(el("hr"));
    else if (block.t === "quote") blocks(block.c, root.appendChild(el("blockquote")), pictures);
    else if (block.t === "list") list(block, root, pictures);
    else if (block.t === "table") {
      const set = block.align.map((side) => (side ? `al-${side}` : null));
      const table = root.appendChild(el("table"));
      const head = table.appendChild(el("thead")).appendChild(el("tr"));
      block.head.forEach((c, k) => inline(c, head.appendChild(el("th", { class: set[k] })), pictures));
      const body = table.appendChild(el("tbody"));
      for (const cells of block.rows) {
        const row = body.appendChild(el("tr"));
        cells.forEach((c, k) => inline(c, row.appendChild(el("td", { class: set[k] })), pictures));
      }
    } else inline(block.c, root.appendChild(el("p")), pictures);
  }
  if (!root.childNodes.length) root.append(el("p", { class: "empty", text: "empty text region" }));
  return root;
}

/** Draw the nodes of one line of text, as mdtree reads a line, into `parent` (for a page's header and footer). */
export function renderInline(nodes, parent, pictures = null) {
  return inline(nodes, parent, pictures);
}

/**
 * Render markdown `source` into a new <div class="md">.  `pictures` are
 * the region's attachments, {name: {type: base64}}, for the pictures its
 * text names.
 */
export function renderMarkdown(source, pictures = null) {
  return blocks(parseMarkdown(source), el("div", { class: "md" }), pictures);
}
