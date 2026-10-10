// The library: what the Windows symbol palette offers, tab by tab —
// glyphs, units, functions and templates, each with a line that says
// what it is for.  The data (data/library.json) is generated from the
// palette's own list by `python -m utils.worksheet.make_library`; this
// module makes it searchable and knows how an entry is shown and where
// the caret goes once it has been inserted.

import { namesFor } from "./symbols.js";

let TABS = [];          // [{name, sections: [{heading, entries: [entry]}]}], as generated
let UNIQUE = [];        // one record per distinct text, for searching

export function tabs() { return TABS; }

/** Take the library's data (see `loadLibrary`; tests hand it in directly). */
export function setLibrary(data) {
  TABS = data.tabs;
  const byText = new Map();
  for (const tab of TABS) {
    for (const section of tab.sections) {
      for (const entry of section.entries) {
        let record = byText.get(entry.text);
        if (!record) {
          record = { entry, tab: tab.name, section: section.heading, lines: [], where: [] };
          byText.set(entry.text, record);
        }
        // The same glyph may sit on several tabs, saying something else on each.
        for (const line of [entry.detail, entry.desc, entry.label]) {
          if (line && !record.lines.includes(line)) record.lines.push(line);
        }
        record.where.push(`${tab.name} ${section.heading}`);
      }
    }
  }
  UNIQUE = [...byText.values()].map((record, order) => {
    const text = record.entry.text;
    const said = record.lines.join("  ").toLowerCase();
    return { ...record, order, lower: text.toLowerCase(), words: wordsOf(text),
             exactWords: text.split(NOT_WORD).filter(Boolean),
             names: namesFor(text).map((name) => name.slice(1).toLowerCase()),
             said, saidWords: wordsOf(said), whereLower: record.where.join("  ").toLowerCase() };
  });
}

export async function loadLibrary(url = "/static/data/library.json") {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`library: ${response.status}`);
  setLibrary(await response.json());
}

const NOT_WORD = /[^\p{L}\p{N}_]+/u;

function wordsOf(text) {
  return text.toLowerCase().split(NOT_WORD).filter(Boolean);
}

/** What the palette says about `text` (a glyph, say), or "". */
export function about(text) {
  const record = UNIQUE.find((r) => r.entry.text === text);
  return record ? (record.entry.detail || record.entry.desc || "") : "";
}

// ---- how an entry looks on a key ------------------------------------------
// Subscripts: U+2080-209C, U+1D62-1D6A, U+2C7C.  Superscripts: U+00B2, 00B3,
// 00B9, U+2070-207F, U+1D2C-1D61, U+1D9B-1DBF, U+02B0-02B8, U+02E1-02E3.
const LOWERED = /^[₀-ₜᵢ-ᵪⱼ]+$/u;
const RAISED = /^[²³¹⁰-ⁿᴬ-ᵡᶛ-ᶿʰ-ʸˡ-ˣ]+$/u;
const CODE = /[A-Za-z_]{2}|[()[\]{}="']/;

/**
 * The face of an entry's key: {text, shift, style}.  A run of sub- or
 * superscripts is shown as the plain characters set low or high (a bare
 * ₂ is too small to read), as the palette does; text of several lines is
 * shown on one.  `style` is "glyph" for symbols, "code" for program text
 * and "plain" for units and written-out values (▸ mV, 90 km/h).
 */
export function face(entry) {
  const text = entry.text;
  if (!entry.label) {
    if (text === "͵") return { text: ",", shift: "lowered", style: "glyph" };
    if (text.length <= 4 && (LOWERED.test(text) || RAISED.test(text))) {
      return { text: text.normalize("NFKC").replace(/-/g, "−"),
               shift: LOWERED.test(text) ? "lowered" : "raised", style: "glyph" };
    }
  }
  const lines = (entry.label || text).split("\n").map((line, i) => (i ? line.trimStart() : line));
  const shown = lines.join(" ↵ ").replace(/[ ↵]+$/, "");
  return { text: shown, shift: "", style: /^[▸\d]/.test(shown) ? "plain" : CODE.test(shown) ? "code" : "glyph" };
}

// ---- where the caret goes ----------------------------------------------------
// The first place an entry leaves open: an empty bracket or argument, an
// empty string, the condition before a colon, the gap between two bars.
const OPEN = /(?<=[([{])(?=[ ,)\]}:])|(?<=["'])(?=["'])|(?<= )(?=[:)\]}])|(?<=, )(?=[,)\]}])|(?<==)(?=[,)])|(?<=‖)(?=‖)/u;
// Calls that take nothing: the caret belongs after them.
const NO_ARGUMENTS = /^(\.\w+|datetime\.(now|today)|time\.time|list_themes|update_currency_rates|rates_status|clear_currency_cache)\(\)$/;

/** Offset in `text` where typing should continue once it is inserted. */
export function slot(text) {
  if (NO_ARGUMENTS.test(text)) return text.length;
  const open = OPEN.exec(text);
  return open ? open.index : text.length;
}

/** The next open place on the line after offset `from` — where Tab goes — or -1. */
export function nextSlot(text, from) {
  const end = text.indexOf("\n", from);
  const rest = new RegExp(OPEN.source, "gu");
  rest.lastIndex = from + 1;
  const open = rest.exec(end < 0 ? text : text.slice(0, end));
  return open ? open.index : -1;
}

// ---- searching ---------------------------------------------------------------
function worth(record, word, exact) {
  if (record.lower === word) return record.entry.text === exact ? 110 : 100;
  if (record.names.includes(word)) return 90;                           // \ohm
  if (record.lower.startsWith(word)) return record.entry.text.startsWith(exact) ? 84 : 80;
  if (record.exactWords.includes(exact)) return 78;                     // MW finds ▸ MW before ▸ mW
  if (record.words.includes(word)) return 76;
  if (record.exactWords.some((w) => w.startsWith(exact))) return 72;
  if (record.words.some((w) => w.startsWith(word))) return 70;          // theme= in plot(, , theme=…)
  if (record.names.some((name) => name.startsWith(word))) return 60;
  if (record.lower.includes(word)) return 50;
  if (record.saidWords.some((w) => w.startsWith(word))) return 30;
  if (record.said.includes(word)) return 20;
  if (record.whereLower.includes(word)) return 10;
  return 0;
}

/**
 * Entries matching every word of `query`, best first:
 * [{entry, tab, section, said}] — `said` is the line of explanation to show.
 */
export function search(query, limit = 80) {
  const exact = query.trim().split(/\s+/).filter(Boolean);
  if (!exact.length) return [];
  const words = exact.map((w) => w.toLowerCase());
  const hits = [];
  for (const record of UNIQUE) {
    let total = 0;
    for (let i = 0; i < words.length; i += 1) {
      const score = worth(record, words[i], exact[i]);
      if (!score) { total = 0; break; }
      total += score;
    }
    if (total) hits.push({ record, total });
  }
  hits.sort((a, b) => b.total - a.total || a.record.lower.length - b.record.lower.length
                      || a.record.order - b.record.order);
  return hits.slice(0, limit).map(({ record }) => ({
    entry: record.entry, tab: record.tab, section: record.section,
    said: record.lines.find((line) => words.some((w) => line.toLowerCase().includes(w))) || record.lines[0] || "",
  }));
}

/**
 * Entries for the editor's backslash list: those with a word that starts
 * with `prefix` (letters, as typed after the backslash) — functions,
 * templates, `▸ unit` targets.  Single glyphs are left to their names.
 */
export function snippets(prefix, limit = 8) {
  if (prefix.length < 2) return [];
  const lower = prefix.toLowerCase();
  const hits = [];
  for (const record of UNIQUE) {
    const text = record.entry.text;
    if (!/[A-Za-z]{2}/.test(text)) continue;
    const words = text.split(/[^A-Za-z_0-9]+/).filter(Boolean);
    let score = 0;
    if (text.startsWith(prefix)) score = 4;
    else if (words.some((w) => w.startsWith(prefix))) score = 3;
    else if (text.toLowerCase().startsWith(lower)) score = 2;
    else if (words.some((w) => w.toLowerCase().startsWith(lower))) score = 1;
    if (score) hits.push({ record, score });
  }
  hits.sort((a, b) => b.score - a.score || a.record.lower.length - b.record.lower.length
                      || a.record.order - b.record.order);
  return hits.slice(0, limit).map(({ record }) => ({
    kind: "snippet", name: prefix, glyph: record.entry.text, face: face(record.entry).text,
    said: record.lines[0] || "",
  }));
}
