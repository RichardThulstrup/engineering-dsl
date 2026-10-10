// Markdown for text regions, rendered straight to DOM nodes.
//
// Text regions are notebook markdown cells, so a sheet may have been
// written by someone else.  Nothing here ever assigns markup: every
// piece of the source ends up in a text node (or in KaTeX, which is
// given `trust: false`), and raw HTML in the source is shown as text.
// Link and image addresses are restricted to harmless schemes.

import { el } from "./util.js";

const SAFE_LINK = /^(https?:|mailto:|#|\.{0,2}\/|[\w.-]+(\/|$))/i;
const SAFE_IMAGE = /^(https?:|data:image\/(png|jpeg|gif|webp);|\.{0,2}\/|[\w.-]+(\/|$))/i;

function math(source, display) {
  const span = el("span", { class: display ? "tex block" : "tex" });
  try {
    window.katex.render(source, span, { displayMode: display, throwOnError: false,
                                        strict: "ignore", trust: false });
  } catch (error) {
    span.textContent = source;
  }
  return span;
}

// One alternative per inline construct; the first match in the text wins.
const INLINE = new RegExp([
  /\\([\\`*_{}\[\]()#+\-.!$~|<>])/.source,                   // 1  escaped character
  /`([^`\n]+)`/.source,                                       // 2  code
  /\$\$([^$]+?)\$\$/.source,                                  // 3  display math
  /\$((?:\\.|[^$\\\n])+?)\$/.source,                          // 4  inline math
  /!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/.source,         // 5,6 image
  /\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/.source,          // 7,8 link
  /\*\*(.+?)\*\*(?!\*)|__(.+?)__(?!_)/.source,                // 9,10 strong (may end in nested *em*)
  /\*([^*\s](?:[^*]*[^*\s])?)\*|(?<![\w])_([^_\s](?:[^_]*[^_\s])?)_(?![\w])/.source,  // 11,12 emphasis
  /~~(.+?)~~/.source,                                         // 13 strike
  /<br\s*\/?>/.source,                                        // line break
  /(https?:\/\/[^\s<>)]+[^\s<>).,;:!?])/.source,              // 14 bare link
  /( {2,}|\\)\n/.source,                                      // 15 hard break
].join("|"), "g");

function inline(text, parent) {
  let last = 0;
  // matchAll works on a private copy of the pattern: this function
  // calls itself for nested emphasis, and a shared `lastIndex` would
  // send the outer scan back to the start.
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) parent.append(text.slice(last, m.index));
    last = m.index + m[0].length;
    if (m[1] !== undefined) parent.append(m[1]);
    else if (m[2] !== undefined) parent.append(el("code", { text: m[2] }));
    else if (m[3] !== undefined) parent.append(math(m[3], true));
    else if (m[4] !== undefined) parent.append(math(m[4], false));
    else if (m[6] !== undefined) {
      if (SAFE_IMAGE.test(m[6])) parent.append(el("img", { src: m[6], alt: m[5] || "" }));
      else parent.append(m[0]);
    } else if (m[8] !== undefined) {
      if (SAFE_LINK.test(m[8])) {
        const a = el("a", { href: m[8], target: "_blank", rel: "noopener noreferrer" });
        inline(m[7], a);
        parent.append(a);
      } else parent.append(m[0]);
    } else if (m[9] !== undefined || m[10] !== undefined) {
      inline(m[9] ?? m[10], parent.appendChild(el("strong")));
    } else if (m[11] !== undefined || m[12] !== undefined) {
      inline(m[11] ?? m[12], parent.appendChild(el("em")));
    } else if (m[13] !== undefined) {
      inline(m[13], parent.appendChild(el("del")));
    } else if (m[14] !== undefined) {
      parent.append(el("a", { href: m[14], target: "_blank", rel: "noopener noreferrer", text: m[14] }));
    } else parent.append(el("br"));
  }
  if (last < text.length) parent.append(text.slice(last));
  return parent;
}

const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_RULE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

function cells(line) {
  return line.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

function list(lines, start, parent) {
  // Items of one list share the indentation of its first marker;
  // deeper-indented markers open a nested list inside the last item.
  const first = LIST_ITEM.exec(lines[start]);
  const indent = first[1].length;
  const node = el(/\d/.test(first[2]) ? "ol" : "ul");
  let i = start, item = null;
  while (i < lines.length) {
    const m = LIST_ITEM.exec(lines[i]);
    if (m && m[1].length === indent) {
      item = node.appendChild(el("li"));
      inline(m[3], item);
      i += 1;
    } else if (m && m[1].length > indent && item) {
      i = list(lines, i, item);
    } else if (lines[i].trim() && /^\s+/.test(lines[i]) && item) {
      item.append(" ");
      inline(lines[i].trim(), item);          // a wrapped line of the item
      i += 1;
    } else break;
  }
  parent.append(node);
  return i;
}

/** Render markdown `source` into a new <div class="md">. */
export function renderMarkdown(source) {
  const root = el("div", { class: "md" });
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }

    let m = /^(\s*)(```+|~~~+)\s*(\S*)\s*$/.exec(line);
    if (m) {                                             // fenced code
      const body = [];
      for (i += 1; i < lines.length && !lines[i].trim().startsWith(m[2]); i += 1) body.push(lines[i]);
      i += 1;
      root.append(el("pre", {}, el("code", { text: body.join("\n") })));
      continue;
    }
    if (line.trim().startsWith("$$")) {                  // display math block
      const body = [line.trim().slice(2)];
      while (!/\$\$\s*$/.test(body[body.length - 1]) && i + 1 < lines.length) body.push(lines[++i]);
      i += 1;
      root.append(el("div", { class: "katex-display" }, math(body.join("\n").replace(/\$\$\s*$/, ""), true)));
      continue;
    }
    m = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
    if (m) {
      inline(m[2], root.appendChild(el("h" + m[1].length)));
      i += 1;
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { root.append(el("hr")); i += 1; continue; }
    if (/^\s*>/.test(line)) {
      const body = [];
      for (; i < lines.length && /^\s*>/.test(lines[i]); i += 1) body.push(lines[i].replace(/^\s*>\s?/, ""));
      const quote = el("blockquote");
      quote.append(...renderMarkdown(body.join("\n")).childNodes);
      root.append(quote);
      continue;
    }
    if (LIST_ITEM.test(line)) { i = list(lines, i, root); continue; }
    if (line.includes("|") && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])
        && cells(lines[i + 1]).length === cells(line).length) {
      // The rule under the header — a cell of dashes for each column —
      // also says how the column is set: :--- left, :---: centred, ---: right.
      const set = cells(lines[i + 1]).map((rule) => (
        rule.endsWith(":") ? (rule.startsWith(":") ? "al-center" : "al-right") : rule.startsWith(":") ? "al-left" : null));
      const table = el("table");
      const head = table.appendChild(el("thead")).appendChild(el("tr"));
      cells(line).forEach((c, k) => inline(c, head.appendChild(el("th", { class: set[k] }))));
      const body = table.appendChild(el("tbody"));
      for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i += 1) {
        const row = body.appendChild(el("tr"));
        cells(lines[i]).forEach((c, k) => inline(c, row.appendChild(el("td", { class: set[k] }))));
      }
      root.append(table);
      continue;
    }
    const para = [lines[i++]];          // always consumes a line: the loop must advance
    for (; i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*>|\s*(```|~~~)|\s*\$\$)/.test(lines[i])
           && !LIST_ITEM.test(lines[i]); i += 1) para.push(lines[i]);
    inline(para.join("\n"), root.appendChild(el("p")));
  }
  if (!root.childNodes.length) root.append(el("p", { class: "empty", text: "empty text region" }));
  return root;
}
