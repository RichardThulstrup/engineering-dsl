// Aids for writing text regions: a table to start from, moving between
// its cells, the formula the caret is in, and how a block of markdown
// is set apart from the text around it.

const RULE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
const SEPARATORS = { left: "---", center: ":---:", right: "---:" };

/**
 * A markdown table of placeholders, as the symbol palette's table
 * builder makes them: a header row, the rule that also sets how the
 * columns are aligned, and `rows` rows to write over.
 */
export function tableMarkdown(columns, rows, align = "left") {
  const wide = Math.min(10, Math.max(1, Math.trunc(columns) || 1));
  const tall = Math.min(30, Math.max(1, Math.trunc(rows) || 1));
  const row = (cell) => "|" + Array.from({ length: wide }, (_, i) => ` ${cell(i)} |`).join("");
  return [row((i) => `Header ${i + 1}`), row(() => SEPARATORS[align] || SEPARATORS.left),
          ...Array.from({ length: tall }, () => row(() => "Cell"))].join("\n");
}

/**
 * The table around `caret`: its cells in reading order, [{from, to, start,
 * end}], where it ends in the text, and how many columns its header has.
 */
function tableAt(text, caret) {
  const lines = [];
  let at = 0;
  for (const line of text.split("\n")) {
    lines.push({ at, line });
    at += line.length + 1;
  }
  const here = lines.findIndex(({ at: start, line }) => caret >= start && caret <= start + line.length);
  if (here < 0 || !lines[here].line.includes("|")) return { cells: [], end: -1, columns: 0 };
  let first = here, last = here;
  while (first > 0 && lines[first - 1].line.includes("|")) first -= 1;
  while (last < lines.length - 1 && lines[last + 1].line.includes("|")) last += 1;
  const cells = [];
  let columns = 0;
  for (const { at: start, line } of lines.slice(first, last + 1)) {
    if (RULE.test(line)) continue;                       // the rule under the header is not written in
    const bars = [...line.matchAll(/(?<!\\)\|/g)].map((m) => m.index);
    const edges = [...(line.slice(0, bars[0]).trim() ? [-1] : []), ...bars,
                   ...(line.slice(bars[bars.length - 1] + 1).trim() ? [line.length] : [])];
    columns = columns || edges.length - 1;
    for (let i = 0; i + 1 < edges.length; i += 1) {
      const from = edges[i] + 1, to = edges[i + 1];
      const inner = line.slice(from, to);
      const lead = inner.length - inner.trimStart().length;
      const body = inner.trim().length;
      // An empty cell is entered after its first blank, so that one stays on each side.
      const begin = from + (body ? lead : Math.min(1, inner.length));
      cells.push({ from: start + from, to: start + to, start: start + begin, end: start + begin + body });
    }
  }
  return { cells, end: lines[last].at + lines[last].line.length, columns };
}

/**
 * Tab in the last cell of a table: {at, text, caret} — a row of empty
 * cells to put at offset `at`, and where the caret then goes.  Null
 * outside a table.
 */
export function tableRow(text, caret) {
  const { end, columns } = tableAt(text, caret);
  return columns ? { at: end, text: "\n|" + "  |".repeat(columns), caret: end + 3 } : null;
}

/**
 * In a table, where Tab (`step` 1) or Shift+Tab (-1) takes the caret: the
 * next or previous cell, its text selected — or null, at either end of
 * the table and outside one.
 */
export function tableCell(text, caret, step) {
  const { cells } = tableAt(text, caret);
  let index = cells.findIndex((cell) => caret >= cell.from && caret <= cell.to);
  if (index < 0) {                                       // on the rule: between header and first row
    index = step > 0 ? cells.findIndex((cell) => cell.from > caret) - 1
      : cells.length - [...cells].reverse().findIndex((cell) => cell.to < caret);
    if (step > 0 && index < -1) return null;
  }
  const next = cells[index + step];
  return next ? { start: next.start, end: next.end } : null;
}

/**
 * The formula `caret` is in: {start, end, latex, display} for a `$…$`
 * (or `$$…$$`) of markdown `text`, or null.  Dollars inside `code` are
 * not formulas.
 */
export function formulaAt(text, caret) {
  const pieces = /`[^`\n]+`|\$\$([^$]+?)\$\$|\$((?:\\.|[^$\\\n])+?)\$/g;
  for (const m of text.matchAll(pieces)) {
    const end = m.index + m[0].length;
    if (m[0][0] === "$" && m.index < caret && caret < end) {
      const display = m[1] !== undefined;
      return { start: m.index, end, latex: (display ? m[1] : m[2]).trim(), display };
    }
  }
  return null;
}

/**
 * Blank lines that set a block (a table, a displayed formula) apart from
 * the text `before` and `after` the place it goes: {lead, tail}.
 */
export function blockPadding(before, after) {
  const pad = (text, blank, one) => (!text.trim() ? "" : blank ? "" : one ? "\n" : "\n\n");
  return { lead: pad(before, /\n[ \t]*\n[ \t]*$/.test(before), /\n[ \t]*$/.test(before)),
           tail: pad(after, /^[ \t]*\n[ \t]*\n/.test(after), /^[ \t]*\n/.test(after)) };
}
