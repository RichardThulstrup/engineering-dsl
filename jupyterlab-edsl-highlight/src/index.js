// JupyterLab extension: live CodeMirror highlighting for the
// engineering DSL, mirroring the Pygments lexer in
// utils/Engineer_Style.py (which colours nbconvert exports).
//
// Implementation: a ViewPlugin that scans the visible ranges of every
// Python editor with the rule set generated from the Pygments lexer
// (src/dslRules.js) and lays mark decorations over the base Python
// highlighting.  String and comment interiors are skipped by
// consulting the syntax tree, matching how the Pygments lexer nests
// its DSL rules inside Python's root state.

import { RangeSetBuilder } from '@codemirror/state';
import { Decoration, ViewPlugin } from '@codemirror/view';
import { syntaxTree } from '@codemirror/language';
import {
  IEditorExtensionRegistry,
  EditorExtensionRegistry
} from '@jupyterlab/codemirror';

import { DSL_RULES } from './dslRules';

// Compile once at load.  'g' for scanning, 'u' so the astronomical
// glyph composites and modifier letters match as written.
const COMPILED = DSL_RULES.map(rule => ({
  re: new RegExp(rule.pattern, 'gu'),
  cls: rule.cls || null,
  groups: rule.groups || null
}));

const DECOS = new Map(); // class name -> Decoration (cached)
function deco(cls) {
  let d = DECOS.get(cls);
  if (!d) {
    d = Decoration.mark({ class: cls });
    DECOS.set(cls, d);
  }
  return d;
}

// A real Python string always begins with an optional prefix (r, b, u,
// f, up to two letters) and a quote.  The parser's error recovery can
// fabricate "String" nodes over plain junk — e.g. in
// `pp("x=" ⇥ 255 ▸ hex)` the glyph run `⇥ 255 ▸ hex)` becomes a String
// child of a ContinuedString — and trusting those would both suppress
// our decorations and leave the base string-red on ordinary code.
function isGenuineString(text) {
  return /^[rRbBuUfF]{0,2}['"]/.test(text);
}

// True when `pos` sits inside a REAL string or comment token, where DSL
// glyphs must keep their literal meaning (same rule as the Pygments
// lexer, whose DSL patterns only apply in the 'root' state).
// ContinuedString is only a container — its child String nodes decide.
function inStringOrComment(view, tree, pos) {
  let node = tree.resolveInner(pos, 1);
  for (; node; node = node.parent) {
    const name = node.name;
    if (name.indexOf('Comment') !== -1) {
      return true;
    }
    if (name !== 'ContinuedString' && name.indexOf('String') !== -1) {
      const head = view.state.doc.sliceString(node.from, Math.min(node.to, node.from + 3));
      if (isGenuineString(head)) {
        return true;
      }
      // Recovery junk mislabelled as String — keep walking; an outer
      // genuine string/comment can still claim the position.
    }
  }
  return false;
}

// Mini-tokenizer for junk regions: the sub-ranges that are REALLY
// strings or comments (so DSL rules stay suppressed there and the
// fillers can restore their proper colours), run over the raw text
// since the tree is unreliable inside these regions.
const MINI_STRING = /'(?:[^'\\\n]|\\.)*'?|"(?:[^"\\\n]|\\.)*"?/g;
const MINI_COMMENT = /#[^\n]*/g;
const MINI_NUMBER = /\d+(?:\.\d*)?|\.\d+/g;

function miniRanges(text, base) {
  const claims = []; // [from, to, cls], strings before comments
  for (const [re, cls] of [[MINI_STRING, 'edsl-string'], [MINI_COMMENT, 'edsl-comment']]) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      if (!m[0].length) { re.lastIndex++; continue; }
      claims.push([base + m.index, base + m.index + m[0].length, cls]);
    }
  }
  claims.sort((a, b) => a[0] - b[0]);
  const out = [];
  let cursor = -1;
  for (const c of claims) {
    if (c[0] < cursor) { continue; } // a string containing '#', etc.
    out.push(c);
    cursor = c[1];
  }
  return out;
}

function buildDecorations(view) {
  const tree = syntaxTree(view.state);
  // Only decorate Python documents (the notebook's code cells and .py
  // files).  Markdown cells, raw cells, and other file types parse to
  // a different top node.
  if (tree.type.name !== 'Script') {
    return Decoration.none;
  }

  // Junk regions: parser-recovery "String" nodes that aren't strings
  // at all (see isGenuineString).  The base highlighter paints them
  // string-red wholesale, so after the DSL rules run, fillers below
  // restore their real strings, comments, and numbers and bleach the
  // rest back to plain text.
  const junk = []; // [from, to]
  const mini = []; // [from, to, cls] — real strings/comments inside junk
  // Real Comment and genuine String nodes, claimed from the tree so
  // they take the palette's grey-italic / red instead of the theme's
  // colours.  The regex rules can't do this — they are suppressed
  // inside strings and comments by design.
  const claims = [];
  for (const { from, to } of view.visibleRanges) {
    tree.iterate({
      from, to,
      enter: n => {
        if (n.name !== 'ContinuedString' && n.name.indexOf('String') !== -1) {
          const head = view.state.doc.sliceString(n.from, Math.min(n.to, n.from + 3));
          if (isGenuineString(head)) {
            claims.push({ from: n.from, to: n.to, cls: 'edsl-string' });
          } else {
            junk.push([n.from, n.to]);
            mini.push(...miniRanges(view.state.doc.sliceString(n.from, n.to), n.from));
          }
        } else if (n.name.indexOf('Comment') !== -1) {
          claims.push({ from: n.from, to: n.to, cls: 'edsl-comment' });
        }
      }
    });
  }
  const inMini = pos => mini.some(r => pos >= r[0] && pos < r[1]);

  const found = []; // {from, to, cls, order}
  let order = 0;
  for (const { from, to } of view.visibleRanges) {
    const text = view.state.doc.sliceString(from, to);
    for (let r = 0; r < COMPILED.length; r++) {
      const rule = COMPILED[r];
      rule.re.lastIndex = 0;
      let m;
      while ((m = rule.re.exec(text)) !== null) {
        if (m[0].length === 0) {
          rule.re.lastIndex++;
          continue;
        }
        const start = from + m.index;
        if (inStringOrComment(view, tree, start) || inMini(start)) {
          continue;
        }
        if (rule.groups) {
          // Contiguous capture groups; a null class skips its group.
          let pos = start;
          for (let g = 0; g < rule.groups.length; g++) {
            const len = (m[g + 1] || '').length;
            if (rule.groups[g] && len > 0) {
              found.push({
                from: pos, to: pos + len,
                cls: rule.groups[g], rank: r, order: order++
              });
            }
            pos += len;
          }
        } else {
          found.push({
            from: start, to: start + m[0].length,
            cls: rule.cls, rank: r, order: order++
          });
        }
      }
    }
  }

  // Rule order is priority order (as in the Pygments lexer): sort by
  // position, break ties by rank, and drop later matches overlapping
  // an accepted one.
  found.sort((a, b) => a.from - b.from || a.rank - b.rank || a.order - b.order);
  const accepted = [];
  let lastEnd = -1;
  for (const f of found) {
    if (f.from < lastEnd) {
      continue;
    }
    accepted.push(f);
    lastEnd = f.to;
  }

  // Fillers for the junk regions: the mini strings/comments keep their
  // proper colours, remaining digit runs become numbers, and whatever
  // is left is bleached back to plain text (the base string-red on
  // these ranges is always wrong — they are not strings).
  for (const [jf, jt] of junk) {
    const covered = [
      ...accepted.filter(f => f.from < jt && f.to > jf).map(f => [f.from, f.to]),
      ...mini.filter(r => r[0] < jt && r[1] > jf).map(r => [r[0], r[1]])
    ].sort((a, b) => a[0] - b[0]);
    let cursor = jf;
    const gaps = [];
    for (const [cf, ct] of covered) {
      if (cf > cursor) { gaps.push([cursor, cf]); }
      cursor = Math.max(cursor, ct);
    }
    if (cursor < jt) { gaps.push([cursor, jt]); }
    accepted.push(...mini
      .filter(r => r[0] >= jf && r[1] <= jt)
      .map(r => ({ from: r[0], to: r[1], cls: r[2] })));
    for (const [gf, gt] of gaps) {
      const text = view.state.doc.sliceString(gf, gt);
      MINI_NUMBER.lastIndex = 0;
      let cursor2 = gf;
      let m;
      while ((m = MINI_NUMBER.exec(text)) !== null) {
        if (!m[0].length) { MINI_NUMBER.lastIndex++; continue; }
        const s = gf + m.index;
        if (s > cursor2) { accepted.push({ from: cursor2, to: s, cls: 'edsl-plain' }); }
        accepted.push({ from: s, to: s + m[0].length, cls: 'edsl-number' });
        cursor2 = s + m[0].length;
      }
      if (cursor2 < gt) { accepted.push({ from: cursor2, to: gt, cls: 'edsl-plain' }); }
    }
  }

  accepted.push(...claims);
  accepted.sort((a, b) => a.from - b.from || a.to - b.to);
  const builder = new RangeSetBuilder();
  let end = -1;
  for (const f of accepted) {
    if (f.from < end) {
      continue;
    }
    builder.add(f.from, f.to, deco(f.cls));
    end = f.to;
  }
  return builder.finish();
}

const edslHighlighter = ViewPlugin.fromClass(
  class {
    constructor(view) {
      this.decorations = buildDecorations(view);
    }
    update(update) {
      // The language (and thus the syntax tree) attaches asynchronously
      // after the editor is built, so a tree change must trigger a
      // rebuild too — without it, editors created before the Python
      // parser loads would never get their DSL colours.
      if (
        update.docChanged ||
        update.viewportChanged ||
        syntaxTree(update.startState) !== syntaxTree(update.state)
      ) {
        this.decorations = buildDecorations(update.view);
      }
    }
  },
  { decorations: v => v.decorations }
);

const plugin = {
  id: 'jupyterlab-edsl-highlight:plugin',
  description: 'Engineering-DSL syntax highlighting in the live editor',
  autoStart: true,
  requires: [IEditorExtensionRegistry],
  activate: (app, registry) => {
    registry.addExtension(
      Object.freeze({
        name: 'jupyterlab-edsl-highlight:highlighter',
        factory: () =>
          EditorExtensionRegistry.createImmutableExtension(edslHighlighter)
      })
    );
  }
};

export default plugin;
