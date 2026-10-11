// The formatted editor of a text region.
//
// A text region is markdown in the file and formatted text on the page.
// Here it is edited as it looks: bold is bold, a list is a list, a
// formula is drawn — with ProseMirror (vendored, MIT) holding the text
// as a document (richdoc.js) that can only hold what markdown can say.
// What is written back is markdown again; a paragraph nobody touched is
// written back as it stood.
//
// Formatting is done from the bar above the region and from the
// keyboard; text pasted from another program keeps the formatting the
// document can hold, and loses the rest.

import { EditorState, NodeSelection, Plugin, Selection, TextSelection } from "prosemirror-state";
import { EditorView } from "prosemirror-view";
import { DOMParser as Reader, DOMSerializer, Fragment, Slice } from "prosemirror-model";
import { baseKeymap, chainCommands, exitCode, lift, setBlockType, toggleMark, wrapIn } from "prosemirror-commands";
import { keymap } from "prosemirror-keymap";
import { history, redo, undo } from "prosemirror-history";
import { InputRule, inputRules, textblockTypeInputRule, undoInputRule, wrappingInputRule } from "prosemirror-inputrules";
import { liftListItem, sinkListItem, splitListItem, wrapInList } from "prosemirror-schema-list";
import { addRowAfter, goToNextCell, isInTable, nextCell, selectionCell, tableEditing } from "prosemirror-tables";
import { el } from "./util.js";
import { math, pictureSource } from "./markdown.js";
import { attachment, bringsFiles, carried } from "./pictures.js";
import { blockContent, inlineContent, linkTo, schema, sliceMarkdown, toDoc, toMarkdown } from "./richdoc.js";

const { nodes: N, marks: M } = schema;

// ---- what the keys and the bar do ---------------------------------------------

/** A list of this kind around the selection — or, where it is one already, no list. */
function toggleList(type) {
  return (state, dispatch) => {
    const { $from } = state.selection;
    for (let depth = $from.depth; depth > 0; depth -= 1) {
      if ($from.node(depth).type === type) return liftListItem(N.list_item)(state, dispatch);
      if ($from.node(depth).type === N.bullet_list || $from.node(depth).type === N.ordered_list) {
        if (dispatch) dispatch(state.tr.setNodeMarkup($from.before(depth), type));      // the other kind of list
        return true;
      }
    }
    return wrapInList(type)(state, dispatch);
  };
}

/** A quotation around the selection — or, inside one, out of it. */
function toggleQuote(state, dispatch) {
  const { $from } = state.selection;
  for (let depth = $from.depth; depth > 0; depth -= 1) {
    if ($from.node(depth).type === N.blockquote) return lift(state, dispatch);
  }
  return wrapIn(N.blockquote)(state, dispatch);
}

/**
 * The writing goes on after `end`: right there in a line of text, and
 * otherwise in the paragraph below (made, where there is none).
 */
function onward(tr, end) {
  if (tr.doc.resolve(end).parent.inlineContent) return tr.setSelection(TextSelection.create(tr.doc, end));
  const next = tr.doc.resolve(end).nodeAfter;
  if (!next || !next.isTextblock) tr.insert(end, N.paragraph.create());
  return tr.setSelection(TextSelection.create(tr.doc, end + 1));
}

/** Backspace at the very start of a heading makes it ordinary text again. */
function unhead(state, dispatch) {
  const { $cursor } = state.selection;
  if (!$cursor || $cursor.parentOffset || $cursor.parent.type !== N.heading) return false;
  return setBlockType(N.paragraph)(state, dispatch);
}

/** Tab in a table: the next cell; after the last one, a new row. */
function cellOnward(state, dispatch) {
  if (!isInTable(state)) return false;
  if (goToNextCell(1)(state, dispatch)) return true;
  if (dispatch) {
    addRowAfter(state, (tr) => {
      const next = Selection.findFrom(tr.doc.resolve(tr.mapping.map(state.selection.$from.end(-1))), 1, true);
      dispatch(next ? tr.setSelection(next) : tr);
    });
  }
  return true;
}

/**
 * In a table ↑ and ↓ go to the cell above or below — a browser left to
 * itself goes to the cell before or after — and from its first row or
 * its last out of the table.
 */
function rowwise(dir) {
  return (state, dispatch, view) => {
    if (!state.selection.empty || !isInTable(state) || !view.endOfTextblock(dir < 0 ? "up" : "down")) return false;
    const $cell = selectionCell(state);
    const $next = nextCell($cell, "vert", dir);
    // (To the end of that cell's text: what is typed goes on with it.)
    const to = $next ? TextSelection.create(state.doc, $next.pos + $next.nodeAfter.nodeSize - 1)
      : Selection.findFrom(state.doc.resolve(dir < 0 ? $cell.before(-1) : $cell.after(-1)), dir);
    if (!to) return false;
    dispatch(state.tr.setSelection(to).scrollIntoView());
    return true;
  };
}

/**
 * Where a text begins or ends with something one cannot write beside — a
 * table, a formula, a list, code — the arrow key that would leave it
 * makes an empty paragraph there.  (One that stays empty is not kept.)
 */
function room(dir) {
  return (state, dispatch, view) => {
    const { doc, selection } = state;
    const { $from, $to } = selection;
    const picked = selection instanceof NodeSelection;
    const edge = dir < 0 ? doc.firstChild : doc.lastChild;
    if (edge.type === N.paragraph || edge.type === N.heading || !(selection.empty || picked)) return false;
    if (dir < 0 ? $from.index(0) > 0 : $to.indexAfter(0) < doc.childCount) return false;
    if (!picked && !view.endOfTextblock(dir < 0 ? "up" : "down")) return false;
    if (edge.type === N.table) {
      // In a table the key goes from row to row: out of it from the first, or the last.
      if (dir < 0 ? $from.index(1) > 0 : $to.index(1) < edge.childCount - 1) return false;
    } else if (dir < 0 ? Selection.findFrom(doc.resolve(picked ? selection.from : $from.before()), -1)
      : Selection.findFrom(doc.resolve(picked ? selection.to : $to.after()), 1)) return false;
    const at = dir < 0 ? 0 : doc.content.size;
    const tr = state.tr.insert(at, N.paragraph.create());
    dispatch(tr.setSelection(TextSelection.create(tr.doc, at + 1)).scrollIntoView());
    return true;
  };
}

/** To the first place of the text, or the last, that the caret can be in (a browser left to itself
 *  does not always get there: not past a table, not past a formula). */
function toEnd(dir) {
  return (state, dispatch) => {
    const end = dir < 0 ? Selection.atStart(state.doc) : Selection.atEnd(state.doc);
    if (dispatch) dispatch(state.tr.setSelection(end).scrollIntoView());
    return true;
  };
}

function hardBreak(state, dispatch) {
  if (state.selection.$from.parent.type.spec.code) return false;
  if (dispatch) dispatch(state.tr.replaceSelectionWith(N.hard_break.create()).scrollIntoView());
  return true;
}

const style = (type, attrs) => setBlockType(type, attrs);

// What the bar offers, and the keys that do the same.
const MARK_TOOLS = [
  ["strong", "B", "Bold (Ctrl+B)", "Mod-b"], ["em", "I", "Italic (Ctrl+I)", "Mod-i"], ["u", "U", "Underline (Ctrl+U)", "Mod-u"],
  ["del", "S", "Strikethrough (Ctrl+Shift+X)", "Shift-Mod-x"], ["sub", "x₂", "Subscript (Ctrl+,)", "Mod-,"],
  ["sup", "x²", "Superscript (Ctrl+.)", "Mod-."], ["code", "<>", "Code (Ctrl+<)", "Mod-<"],
];
const STYLES = [["Text", N.paragraph, null], ["Heading 1", N.heading, { level: 1 }], ["Heading 2", N.heading, { level: 2 }],
                ["Heading 3", N.heading, { level: 3 }], ["Code", N.code_block, null]];

function markRule(pattern, type) {
  return new InputRule(pattern, (state, match, start, end) => {
    const tr = state.tr;
    const from = start + match[0].indexOf(match[1]);
    const to = from + match[1].length;
    if (to < end) tr.delete(to, end);
    if (from > start) tr.delete(start, from);
    return tr.addMark(start, start + match[1].length, type.create()).removeStoredMark(type);
  }, { inCodeMark: false });
}

/** $$…$$ typed: a formula on a line of its own — a block of its own, where it is all its paragraph holds. */
function displayed(state, match, start, end) {
  const $start = state.doc.resolve(start);
  const alone = $start.parent.type === N.paragraph && start === $start.start() && end === $start.end()
    && $start.node(-1).canReplaceWith($start.index(-1), $start.indexAfter(-1), N.math_block);
  if (!alone) return state.tr.replaceWith(start, end, N.math.create({ tex: match[1], big: true }));
  const tr = state.tr.replaceWith($start.before(), $start.after(), N.math_block.create({ tex: match[1] }));
  return onward(tr, $start.before() + 1);
}

// Markdown's own marks still work while typing: "# " starts a heading,
// "- " a list, **bold** is bold, and $x^2$ is a formula.
const typing = inputRules({ rules: [
  textblockTypeInputRule(/^(#{1,6})\s$/, N.heading, (match) => ({ level: match[1].length })),
  wrappingInputRule(/^\s*([-+*])\s$/, N.bullet_list),
  wrappingInputRule(/^(\d+)[.)]\s$/, N.ordered_list),
  wrappingInputRule(/^\s*>\s$/, N.blockquote),
  textblockTypeInputRule(/^```$/, N.code_block),
  new InputRule(/(?<!\$)\$([^$\s](?:[^$]*[^$\s])?)\$$/, (state, match, start, end) => (
    state.tr.replaceWith(start, end, N.math.create({ tex: match[1] }))), { inCodeMark: false }),
  new InputRule(/\$\$([^$\s](?:[^$]*[^$\s])?)\$\$$/, displayed, { inCodeMark: false }),
  markRule(/\*\*([^*\s](?:[^*]*[^*\s])?)\*\*$/, M.strong),
  markRule(/(?<![*\w])\*([^*\s](?:[^*]*[^*\s])?)\*$/, M.em),
  markRule(/~~([^~\s](?:[^~]*[^~\s])?)~~$/, M.del),
  markRule(/`([^`]+)`$/, M.code),
] });

// A table is kept as markdown can say it, wherever it came from: its
// first row is the header, no cell spans two, and a column is set the
// way its header is.
const plainTables = new Plugin({
  appendTransaction(transactions, before, state) {
    if (!transactions.some((tr) => tr.docChanged)) return null;
    const tr = state.tr;
    state.doc.descendants((table, at) => {
      if (table.type !== N.table) return true;
      const head = table.firstChild;
      table.forEach((row, rowAt, r) => row.forEach((cell, cellAt, k) => {
        const type = r ? N.table_cell : N.table_header;
        const align = r && k < head.childCount ? head.child(k).attrs.align : cell.attrs.align;
        const { colspan, rowspan, colwidth } = cell.attrs;
        if (cell.type !== type || cell.attrs.align !== align || colspan !== 1 || rowspan !== 1 || colwidth) {
          tr.setNodeMarkup(at + 1 + rowAt + 1 + cellAt, type, { align, colspan: 1, rowspan: 1, colwidth: null });
        }
      }));
      return false;
    });
    return tr.docChanged ? tr : null;
  },
});

// ---- what other programs put on the clipboard ---------------------------------

const BLOCKS = "p, div, h1, h2, h3, h4, h5, h6, li, ul, ol, blockquote, pre";

/**
 * Word writes a list as paragraphs that begin with their bullet or number.
 * It marks them itself — mso-list and the level in the paragraph's style,
 * mso-list:Ignore around the bullet — and by those marks they are made
 * the items of a list, a list within a list for each level.
 */
function wordLists(page) {
  let lists = [];                       // the list being made, and the lists within it down to the last item's
  for (const paragraph of page.querySelectorAll("p")) {
    const level = /mso-list:\s*\w+\s+level(\d+)/i.exec(paragraph.getAttribute("style") || "");
    if (!level) continue;
    const bullet = paragraph.querySelector('[style*="mso-list:Ignore" i]');
    const ordered = /^\s*[0-9a-z]{1,4}[.)]/i.test(bullet ? bullet.textContent : "");
    if (bullet) bullet.remove();
    const made = () => page.createElement(ordered ? "ol" : "ul");
    if (!lists.length || paragraph.previousElementSibling !== lists[0]) {         // not the next paragraph of that list
      lists = [made()];
      paragraph.before(lists[0]);
    }
    lists.length = Math.min(lists.length, Number(level[1]));
    while (lists.length < Number(level[1])) {
      const within = lists[lists.length - 1].lastElementChild || lists[lists.length - 1];
      lists.push(within.appendChild(made()));
    }
    const item = page.createElement("li");
    item.append(...paragraph.childNodes);
    lists[lists.length - 1].append(item);
    paragraph.remove();
  }
}

/**
 * Pasted markup, made fit for a text before it is read.  A cell of a
 * table holds one piece of text here, so what is a paragraph in a cell —
 * a word processor writes every cell as one — becomes a line of it
 * (left as a paragraph, it would end the table).  So for an item of a
 * list that holds several paragraphs.
 */
function fittedPage(html) {
  const page = new DOMParser().parseFromString(html, "text/html");       // (read, not run)
  wordLists(page);
  const lines = (blocks) => {
    for (const block of blocks.reverse()) block.replaceWith(...block.childNodes, page.createElement("br"));
  };
  for (const cell of page.querySelectorAll("td, th")) lines([...cell.querySelectorAll(BLOCKS)]);
  for (const item of page.querySelectorAll("li")) {
    const paragraphs = [...item.children].filter((child) => child.nodeName === "P");
    if (paragraphs.length > 1) lines(paragraphs);
  }
  // (A line break that ends a cell or an item is no line of it.)
  for (const holder of page.querySelectorAll("td, th, li")) {
    const ends = (node) => node && (node.nodeName === "BR" || (node.nodeType === 3 && !node.data.trim()));
    while (ends(holder.lastChild)) holder.lastChild.remove();
  }
  return page;
}

const fitted = (html) => fittedPage(html).body.innerHTML;

/**
 * Is all of it code?  What is copied in an editor of programs is
 * formatted, too — coloured, in a <pre> or with its line ends kept by its
 * style — and is not prose for that.
 */
function isCode(page) {
  const kept = (node) => {
    for (let at = node.parentElement; at && at !== page.body; at = at.parentElement) {
      if (/^(PRE|CODE|TT|SAMP)$/.test(at.nodeName) || /white-space:\s*pre\s*(;|$)/i.test(at.getAttribute("style") || "")) return true;
    }
    return false;
  };
  const texts = page.createTreeWalker(page.body, NodeFilter.SHOW_TEXT);
  let any = false;
  for (let node = texts.nextNode(); node; node = texts.nextNode()) {
    if (!node.data.trim()) continue;
    if (!kept(node)) return false;
    any = true;
  }
  return any;
}

/** Pictures that came along as data are attached (put among `pictures`), as every picture of a text is. */
function adopted(fragment, pictures) {
  const out = [];
  fragment.forEach((node) => {
    if (node.type === N.image && node.attrs.src.startsWith("data:")) {
      const made = attachment(node.attrs.src);
      if (!made) return;                              // nothing this page could show: left out
      pictures[made.name] = made.bundle;
      out.push(node.type.create({ ...node.attrs, src: `attachment:${made.name}` }, null, node.marks));
    } else out.push(node.copy(adopted(node.content, pictures)));
  });
  return Fragment.fromArray(out);
}

/**
 * Formatted text from another program as a text region keeps it:
 * {text, pictures} — its markdown, and the pictures that came with it as
 * data.  Or null, where it is nothing of the kind: no words in it, or
 * code, which is taken as it is typed.
 */
export function pastedText(html) {
  const page = fittedPage(html);
  if (isCode(page)) return null;
  const pictures = {};
  const doc = Reader.fromSchema(schema).parse(page.body);
  const { text } = toMarkdown(doc.copy(adopted(doc.content, pictures)));
  return text.trim() ? { text, pictures } : null;
}

// ---- formulas and pictures in the text ----------------------------------------

/** A formula: drawn; a double click (or Enter on it) opens its LaTeX. */
class FormulaView {
  constructor(node, view, getPos, editor) {
    this.node = node;
    this.view = view;
    this.getPos = getPos;
    this.editor = editor;
    this.block = node.type === N.math_block;
    this.dom = el(this.block ? "div" : "span", { class: this.block ? "formula katex-display" : "formula" });
    this.dom.formula = this;
    this.field = null;
    this.dom.addEventListener("dblclick", (event) => { event.preventDefault(); this.open(); });
    this.draw();
  }

  draw() {
    this.dom.replaceChildren(math(this.node.attrs.tex, this.block || this.node.attrs.big));
  }

  open() {
    if (this.field) return;
    const field = el("input", { type: "text", class: "tex-source", spellcheck: "false", "aria-label": "LaTeX of the formula" });
    field.value = this.node.attrs.tex;
    field.size = Math.max(8, field.value.length + 2);
    this.field = field;
    const close = (keep) => {
      if (this.field !== field) return;
      this.field = null;
      this.editor.opened = null;
      const tex = field.value.replace(/(?<!\\)\$/g, "").replace(/\s+/g, " ").trim();
      const at = this.getPos();
      this.draw();
      if (keep && tex !== this.node.attrs.tex) {
        // (Emptied, it is gone — one on its own line for a paragraph: a text is never without one.)
        const tr = this.view.state.tr;
        this.view.dispatch(tex ? tr.setNodeMarkup(at, null, { ...this.node.attrs, tex })
          : this.block ? tr.replaceWith(at, at + 1, N.paragraph.create()) : tr.delete(at, at + 1));
      }
    };
    // (What is being written into it counts when the region is finished meanwhile.)
    this.editor.opened = { field, close: () => close(true) };
    field.addEventListener("input", () => { field.size = Math.max(8, field.value.length + 2); });
    field.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === "Escape") {
        event.preventDefault();
        close(event.key === "Enter");
        this.editor.take();
      }
    });
    field.addEventListener("blur", () => { close(true); this.editor.left(); });
    this.dom.replaceChildren(field);
    field.focus();
  }

  update(node) {
    if (node.type !== this.node.type) return false;
    this.node = node;
    if (!this.field) this.draw();
    return true;
  }

  selectNode() { this.dom.classList.add("ProseMirror-selectednode"); }
  deselectNode() { this.dom.classList.remove("ProseMirror-selectednode"); }
  stopEvent() { return Boolean(this.field); }
  ignoreMutation() { return true; }                    // what is drawn in it is not text of the document
}

/**
 * How a text leaves for another program: as the document says it, a
 * formula as its LaTeX between dollars — and a picture as what it shows,
 * not as the name it has in its region.
 */
function outward(pictures) {
  return new DOMSerializer({
    ...DOMSerializer.nodesFromSchema(schema),
    image: (node) => ["img", { src: pictureSource(node.attrs.src, pictures) || "", alt: node.attrs.alt }],
  }, DOMSerializer.marksFromSchema(schema));
}

/** A region's text (markdown) as formatted text for another program. */
export function formatted(source, pictures) {
  return el("div", {}, outward(pictures).serializeFragment(toDoc(source).doc.content)).innerHTML;
}

/** A picture: shown from what is attached to the region. */
function pictureView(pictures) {
  return (node) => {
    const dom = el("img", { alt: node.attrs.alt, draggable: "false", src: pictureSource(node.attrs.src, pictures) });
    return { dom };
  };
}

// ---- the editor ------------------------------------------------------------------

export class RichEditor {
  /**
   * @param {object} opts
   * @param {string} opts.value                        the region's markdown
   * @param {object} opts.pictures                     its attachments; pictures pasted in are added to them
   * @param {(value: string, how: string) => void} opts.onCommit   how: enter | blur | escape
   * @param {() => void} opts.onInput                  the text has changed
   * @param {(value: string, why?: string) => void} opts.onSource  go on with the markdown text instead
   */
  constructor({ value, pictures, onCommit, onInput, onSource }) {
    this.kind = "text";
    this.source = value;
    this.pictures = pictures;
    this.onCommit = onCommit;
    this.onInput = onInput || (() => {});
    this.onSource = onSource;
    this.done = false;
    this.touched = false;               // has anything been changed?
    this.opened = null;                 // the formula whose LaTeX is being written, if one is
    const { doc, kept } = toDoc(value);
    this.kept = kept;
    const mount = el("div", { class: "md" });
    this.bar = this.buildBar();
    this.el = el("div", { class: "rich" }, this.bar, mount);

    const keys = {
      "Mod-z": undo, "Mod-y": redo, "Shift-Mod-z": redo,
      Backspace: chainCommands(undoInputRule, unhead),
      Enter: (state, dispatch, view) => this.openFormula(state, view) || splitListItem(N.list_item)(state, dispatch),
      "Shift-Enter": chainCommands(exitCode, hardBreak),
      "Mod-Enter": () => { this.commit("enter"); return true; },
      Escape: () => { this.commit("escape"); return true; },
      Tab: chainCommands(cellOnward, sinkListItem(N.list_item), () => true),
      "Shift-Tab": chainCommands(goToNextCell(-1), liftListItem(N.list_item), () => true),
      "Shift-Mod-0": style(N.paragraph), "Shift-Mod-8": toggleList(N.bullet_list), "Shift-Mod-9": toggleList(N.ordered_list),
      "Mod-'": toggleQuote, "Mod-k": () => { this.askLink(); return true; },
      ArrowUp: chainCommands(room(-1), rowwise(-1)), ArrowDown: chainCommands(room(1), rowwise(1)),
      "Mod-Home": toEnd(-1), "Mod-End": toEnd(1),
    };
    for (const [mark, , , key] of MARK_TOOLS) keys[key] = toggleMark(M[mark]);
    // (With Shift, not Alt: on many keyboards Ctrl+Alt and a digit is AltGr
    // and that digit — a character of its own: @, £, $, { …)
    for (let level = 1; level <= 6; level += 1) keys[`Shift-Mod-${level}`] = style(N.heading, { level });

    this.view = new EditorView({ mount }, {
      state: EditorState.create({ doc, plugins: [
        typing, keymap(keys), keymap(baseKeymap), history(), tableEditing(), plainTables,
        new Plugin({ view: () => ({ update: () => this.showState() }) }),
      ] }),
      nodeViews: {
        math: (node, view, getPos) => new FormulaView(node, view, getPos, this),
        math_block: (node, view, getPos) => new FormulaView(node, view, getPos, this),
        image: pictureView(pictures),
      },
      clipboardSerializer: outward(pictures),
      // As plain text, what is copied is its markdown — and plain text that
      // is pasted is read as markdown, each of its lines a line.  (Pasted
      // with Shift held as well, it is put in just as it is.)
      clipboardTextSerializer: sliceMarkdown,
      clipboardTextParser: (text, $context, plain) => {
        const blocks = plain ? [] : blockContent(text, { lines: true });
        return blocks.length ? new Slice(Fragment.from(blocks), 0, 0) : null;
      },
      transformPastedHTML: fitted,
      transformPasted: (slice) => new Slice(adopted(slice.content, this.pictures), slice.openStart, slice.openEnd),
      handleDOMEvents: {
        // Pictures on their own — pasted or dropped — are the sheet's to place.
        paste: (view, event) => Boolean(carried(event.clipboardData)) && !event.clipboardData.getData("text/plain"),
        drop: (view, event) => bringsFiles(event.dataTransfer),
        blur: () => { this.left(); return false; },
      },
      attributes: { role: "textbox", "aria-multiline": "true", "aria-label": "Text of the region" },
      dispatchTransaction: (tr) => {
        this.view.updateState(this.view.state.apply(tr));
        if (!tr.docChanged) return;
        this.touched = true;
        this.onInput();
      },
    });
    this.showState();
  }

  /** The markdown of what is shown: {text, ok} — `ok` is false when it cannot be written so that it reads back the same. */
  written() {
    if (this.opened) this.opened.close();
    if (!this.touched) return { text: this.source, ok: true };       // nothing changed: the text it was
    return toMarkdown(this.view.state.doc, this.kept, this.source);
  }

  get value() { return this.written().text; }

  /**
   * Take the keyboard.  With a point of the window the caret goes there
   * (on a formula or a picture: that is selected); without one, to the
   * end of the text.
   */
  focus(point = null) {
    const { doc } = this.view.state;
    const found = point ? this.view.posAtCoords({ left: point.x, top: point.y }) : null;
    const hit = found && found.inside >= 0 ? doc.nodeAt(found.inside) : null;
    const selection = hit && hit.isAtom && NodeSelection.isSelectable(hit) ? NodeSelection.create(doc, found.inside)
      : found ? Selection.near(doc.resolve(found.pos))
      // (Not on a formula the text ends with: what is typed next would take its place.)
      : Selection.findFrom(doc.resolve(doc.content.size), -1, true) || Selection.atEnd(doc);
    this.view.dispatch(this.view.state.tr.setSelection(selection));
    this.take();
  }

  /**
   * The bar stays on the paper: over a text that stands far to the right,
   * it reaches to the left of it.  (Asked for by the sheet, once the
   * region is in its place on the page.)
   */
  place() {
    const content = this.el.closest("#content");
    if (!content) return;
    let x = 0;                          // where the text begins, across the page
    for (let at = this.el; at && at !== content; at = at.offsetParent) x += at.offsetLeft;
    const margin = (content.parentElement.clientWidth - content.clientWidth) / 2 - 8;       // the paper's own, but for a little
    this.bar.style.left = `${Math.min(-6, content.clientWidth + margin - x - this.bar.offsetWidth)}px`;
  }

  /** Give the text the keyboard, the caret where the editor has it. */
  take() {
    this.view.focus();
    // ProseMirror takes a caret that turns up at the very start of a text
    // within 200 ms of the text getting the keyboard for a browser's
    // mistake, and puts it back where it was.  Here the caret is in its
    // place when the keyboard comes: Home pressed right after is meant.
    if (this.view.input) this.view.input.lastFocus = 0;
  }

  /** Text at the caret, as it is (from the symbol panel); `caret` characters into it, typing goes on. */
  insert(text, caret = text.length) {
    if (this.opened) {                                    // into the formula whose LaTeX is being written
      const { field } = this.opened;
      const at = field.selectionStart;
      field.setRangeText(text, at, field.selectionEnd, "end");
      field.setSelectionRange(at + caret, at + caret);
      field.dispatchEvent(new Event("input"));
      return;
    }
    const { from } = this.view.state.selection;
    const tr = this.view.state.tr.insertText(text);
    this.view.dispatch(tr.setSelection(TextSelection.create(tr.doc, from + caret)).scrollIntoView());
  }

  /** The keyboard, back to where the caret is. */
  refocus() {
    if (this.opened) this.opened.field.focus();
    else this.take();
  }

  /**
   * Markdown at the caret.  A `block` (a table, a displayed formula) goes
   * after the block the caret is in; `select` — [offset, length] in the
   * markdown — leaves that many characters of its first text selected.
   */
  insertMarkdown(markdown, { block = false, select = null } = {}) {
    if (this.opened) this.opened.close();
    const { state } = this.view;
    let tr = state.tr;
    if (block) {
      const { $from, $to } = state.selection;
      const empty = $from.depth === 1 && $from.parent.type === N.paragraph && !$from.parent.content.size;
      const at = empty ? $from.before(1) : $to.after(1);
      const content = Fragment.from(blockContent(markdown));
      tr = empty ? tr.replaceWith(at, $from.after(1), content) : tr.insert(at, content);
      const end = at + content.size;
      const start = Selection.findFrom(tr.doc.resolve(at), 1, true);
      // (Where nothing to write in was put — a formula — the writing goes on below it.)
      if (!start || start.from > end) onward(tr, end);
      else tr.setSelection(select ? TextSelection.create(tr.doc, start.from, start.from + select[1]) : start);
    } else {
      tr = tr.replaceSelection(new Slice(Fragment.from(inlineContent(markdown)), 0, 0));
    }
    this.view.dispatch(tr.scrollIntoView());
    this.take();
  }

  /** The formula that is selected: {start, end, latex, display} — or null. */
  formula() {
    const { selection } = this.view.state;
    const node = selection instanceof NodeSelection ? selection.node : null;
    if (!node || (node.type !== N.math && node.type !== N.math_block)) return null;
    return { start: selection.from, end: selection.to, latex: node.attrs.tex, display: node.type === N.math_block || node.attrs.big };
  }

  /** Put a formula in place of the one `found` (when `replace`), beside it, or at the caret. */
  putFormula(latex, display, found, replace) {
    if (this.opened) this.opened.close();
    const { state } = this.view;
    const old = found ? state.doc.nodeAt(found.start) : null;
    if (old) {
      // Where the new one goes: after the old one, or where the old one
      // was — for one on its own line, a paragraph in its place.  (A text
      // is never without a paragraph to write in.)
      let tr = state.tr;
      if (!replace) onward(tr, found.end);
      else if (old.type === N.math_block) {
        tr = tr.replaceWith(found.start, found.end, N.paragraph.create());
        tr.setSelection(TextSelection.create(tr.doc, found.start + 1));
      } else tr = tr.delete(found.start, found.end);
      this.view.dispatch(tr);
    }
    this.insertMarkdown(display ? `$$${latex}$$` : `$${latex}$`, { block: display });
  }

  /** Enter on a selected formula opens it. */
  openFormula(state, view) {
    const found = this.formula();
    const dom = found ? view.nodeDOM(found.start) : null;
    if (!dom || !dom.formula) return false;
    dom.formula.open();
    return true;
  }

  // ---- the bar ---------------------------------------------------------------------
  buildBar() {
    // A key of the bar is pressed with the pointer's button going down, so
    // that the text never loses the keyboard to it.
    const key = (face, title, data, down, look = "") => el("button", {
      type: "button", class: `fmt ${look}`.trim(), title, dataset: data,
      onmousedown: (event) => { event.preventDefault(); down(); } }, face);
    // What a key does to the text; the keyboard is the text's again after it.
    const does = (run) => () => { run(this.view.state, this.view.dispatch, this.view); this.take(); };
    this.buttons = MARK_TOOLS.map(([mark, face, title]) => key(face, title, { mark }, does(toggleMark(M[mark])), mark));
    this.styles = el("select", { title: "What kind of paragraph this is", "aria-label": "Paragraph style" },
                     STYLES.map(([name], k) => el("option", { value: k, text: name })));
    this.styles.addEventListener("change", () => {
      const [, type, attrs] = STYLES[Number(this.styles.value)];
      does(style(type, attrs))();
    });
    this.tools = {
      bullets: key("•", "Bulleted list (Ctrl+Shift+8)", { tool: "bullets" }, does(toggleList(N.bullet_list))),
      numbers: key("1.", "Numbered list (Ctrl+Shift+9)", { tool: "numbers" }, does(toggleList(N.ordered_list))),
      quote: key("”", "Quotation (Ctrl+')", { tool: "quote" }, does(toggleQuote)),
      link: key("🔗", "Link (Ctrl+K) — on the selected text", { tool: "link" }, () => this.askLink()),
      markdown: key("Markdown", "Go on with the markdown text of this region", { tool: "markdown" }, () => this.toSource(), "wide"),
    };
    this.link = el("input", { type: "text", class: "link", placeholder: "https://…  (Enter puts it on the selected text)",
                              "aria-label": "Address of the link", hidden: true });
    this.link.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); this.setLink(this.link.value.trim()); }
      if (event.key === "Escape") { event.preventDefault(); this.take(); }
    });
    this.link.addEventListener("input", () => this.link.classList.remove("bad"));
    this.link.addEventListener("blur", () => { this.link.hidden = true; this.left(); });
    const gap = () => el("span", { class: "gap" });
    return el("div", { class: "formatbar", dataset: { keepsEdit: "" } },
              ...this.buttons, gap(), this.styles, gap(),
              this.tools.bullets, this.tools.numbers, this.tools.quote, this.tools.link, gap(), this.tools.markdown, this.link);
  }

  /** The bar shows what the text at the caret is. */
  showState() {
    if (!this.view) return;
    const { state } = this.view;
    const { $from, from, to, empty } = state.selection;
    const marks = empty ? (state.storedMarks || $from.marks()) : null;
    for (const button of this.buttons) {
      const type = M[button.dataset.mark];
      button.classList.toggle("on", marks ? Boolean(type.isInSet(marks)) : state.doc.rangeHasMark(from, to, type));
    }
    const parent = $from.parent;
    const k = STYLES.findIndex(([, type, attrs]) => parent.type === type && (!attrs || attrs.level === parent.attrs.level));
    this.styles.value = String(Math.max(0, k));
    const around = [];
    for (let depth = $from.depth; depth > 0; depth -= 1) around.push($from.node(depth).type);
    const list = around.find((type) => type === N.bullet_list || type === N.ordered_list);       // the innermost
    this.tools.bullets.classList.toggle("on", list === N.bullet_list);
    this.tools.numbers.classList.toggle("on", list === N.ordered_list);
    this.tools.quote.classList.toggle("on", around.includes(N.blockquote));
    this.tools.link.classList.toggle("on", Boolean(M.link.isInSet(marks || $from.marks()))
                                           || (!empty && state.doc.rangeHasMark(from, to, M.link)));
  }

  askLink() {
    const { state } = this.view;
    const mark = state.selection.$from.marks().find((m) => m.type === M.link)
      || (state.selection.$from.nodeAfter || { marks: [] }).marks.find((m) => m.type === M.link);
    this.link.hidden = false;
    this.link.classList.remove("bad");
    this.link.value = mark ? mark.attrs.href : "";
    this.link.focus();
    this.link.select();
  }

  /**
   * The address goes on the selected text; an empty address takes the
   * link off.  One a link cannot go to (javascript:…) is not taken: the
   * field stays, marked, for another.
   */
  setLink(href) {
    const link = linkTo(/^www\./i.test(href) ? `https://${href}` : href);
    if (href && !link) {
      this.link.classList.add("bad");
      this.link.select();
      return;
    }
    const { state } = this.view;
    let { from, to } = state.selection;
    const $pos = state.selection.$from;
    const mark = [...$pos.marks(), ...($pos.nodeAfter ? $pos.nodeAfter.marks : [])].find((m) => m.type === M.link);
    if (from === to && mark) {                            // the caret is in a link: all of that link
      $pos.parent.forEach((child, at) => {
        const start = $pos.start() + at;
        if (mark.isInSet(child.marks) && start <= $pos.pos && $pos.pos <= start + child.nodeSize) {
          from = Math.min(from, start);
          to = Math.max(to, start + child.nodeSize);
        }
      });
    }
    let tr = state.tr.removeMark(from, to, M.link);
    if (link) {
      tr = from === to ? tr.insertText(link.href, from).addMark(from, from + link.href.length, M.link.create(link))
        : tr.addMark(from, to, M.link.create(link));
    }
    this.view.dispatch(tr);
    this.take();
  }

  // ---- ending ------------------------------------------------------------------------
  /** The keyboard has left the text: unless it went to the bar or the panel, the edit ends. */
  left() {
    setTimeout(() => {
      const now = document.activeElement;
      if (this.done || this.el.contains(now) || now?.closest?.("[data-keeps-edit]")) return;
      this.commit("blur");
    }, 0);
  }

  /** Go on with the markdown text of the region instead. */
  toSource(why) {
    if (this.done) return;
    const { text } = this.written();
    this.done = true;
    this.view.destroy();
    this.onSource(text, why);
  }

  commit(how) {
    if (this.done) return;
    const { text, ok } = this.written();
    if (!ok) {
      // (A formula with a dollar in it, say.)  Rather than save what would
      // read differently, show what would be saved.
      this.toSource("This text could not be written as markdown the way it is shown: here is what would be saved.");
      return;
    }
    this.done = true;
    this.view.destroy();
    this.onCommit(text, how);
  }

  cancel() { this.commit("escape"); }
}
