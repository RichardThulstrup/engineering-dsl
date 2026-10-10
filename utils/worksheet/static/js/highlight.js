// Syntax colouring for the math editor.
//
// The roles and colours are those of the JupyterLab highlighter; the
// vocabulary (which words are units, constants, toolkit functions) is
// sent by the kernel when it starts, so it is always the running
// toolkit's own.

import { escapeHtml, ZWSP } from "./util.js";

const KEYWORDS = new Set(("and as assert async await break class continue def del elif else except " +
  "finally for from global if import in is lambda nonlocal not or pass raise return try while with " +
  "yield True False None symbols real_symbols positive_symbols integer_symbols").split(" "));

const LETTER = "A-Za-z_\\u00B5\\u0370-\\u03FF\\u1F00-\\u1FFF\\u2126\\u212A\\u212B\\u210F";
const TOKEN = new RegExp([
  "(#.*)",                                                        // 1 comment
  "((?:[rbfRBF]{0,2})(?:\"\"\"[\\s\\S]*?(?:\"\"\"|$)|'''[\\s\\S]*?(?:'''|$)|\"(?:\\\\.|[^\"\\\\\\n])*\"?|'(?:\\\\.|[^'\\\\\\n])*'?))",  // 2 string
  "(0[xX][0-9a-fA-F_]+|0[bB][01_]+|\\d[\\d_]*\\.?\\d*(?:[eE][+-]?\\d+)?|\\.\\d+(?:[eE][+-]?\\d+)?)",   // 3 number
  "(°[CFR]|℃|℉)",                                                 // 4 temperature unit
  `([${LETTER}][${LETTER}0-9]*)`,                                 // 5 identifier
  // 6 sub/superscript run — the characters of the JupyterLab highlighter's
  //   two classes, the index separator (U+0375) and the raised capitals (ᵀ, ᴬ)
  "([₀₁₂₃₄₅₆₇₈₉₊₋₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓᵦᵧᵨᵩᵪ\\u0375" +
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾˙ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᴬ-ᵂ]+)",
  "(→)",                                                          // 7 arrow
].join("|"), "gu");

const GREEK_ONLY = /^[Ͱ-Ͽ]+$/u;

export function makeHighlighter(vocab) {
  const units = new Set(vocab?.units || []);
  const constants = new Set(vocab?.constants || []);
  const helpers = new Set(vocab?.functions || []);

  function classify(word) {
    if (KEYWORDS.has(word)) return "keyword";
    if (units.has(word)) return "unit";
    if (constants.has(word)) return "constant";
    if (helpers.has(word)) return "helper";
    if (GREEK_ONLY.test(word)) return "greek";
    return "";
  }

  /** HTML for `text`; `caret` (an offset) gets an empty marker span. */
  return function highlight(text, caret = -1) {
    let html = "", last = 0;
    const plain = (upto) => {
      if (upto > last) html += piece(text.slice(last, upto), last, "");
      last = upto;
    };
    const piece = (value, start, cls) => {
      let body;
      if (caret >= start && caret <= start + value.length && caret >= 0) {
        const cut = caret - start;
        body = escapeHtml(value.slice(0, cut)) + '<span class="caret"></span>' + escapeHtml(value.slice(cut));
        caret = -2;                                   // placed
      } else body = escapeHtml(value);
      return cls ? `<span class="tk-${cls}">${body}</span>` : body;
    };
    TOKEN.lastIndex = 0;
    for (let m = TOKEN.exec(text); m; m = TOKEN.exec(text)) {
      plain(m.index);
      const cls = m[1] ? "comment" : m[2] ? "string" : m[3] ? "number" : m[4] ? "unit"
        : m[5] ? classify(m[5]) : m[6] ? "subsup" : "opword";
      html += piece(m[0], m.index, cls);
      last = TOKEN.lastIndex;
    }
    plain(text.length);
    if (caret >= 0) html += '<span class="caret"></span>';
    // A trailing newline needs something after it to take up a line.
    return html + (text.endsWith("\n") || !text ? ZWSP : "");
  };
}
