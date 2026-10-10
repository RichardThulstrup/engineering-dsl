// Symbols of the DSL's notation: the palette's keys, and the names that
// complete to a glyph after a backslash in the editor (\ohm → Ω).
//
// Each of the two reaches every character the Windows symbol palette
// (SymbolPaletteWinUI) pastes; tests/test_worksheet_frontend.py holds
// them against the palette's own list.

// `raised` / `lowered` groups show each key as a full-size character set
// high or low (a bare ² is too small to read on a key) while typing the
// real sub/superscript glyph.  The everyday groups come first.
export const PALETTE = [
  { title: "Operators",
    keys: "· × ÷ ± − √ ∛ ∜ ↑ ‖ ∥ ∠ ° ≈ ≠ ≤ ≥ ≟ ＝ ≡ ≔ → ← ▸ ‥ ∞ π ℏ ! ‰ ⌊ ⌋ ⌈ ⌉ ∑ ∏ ∂ ∫".split(" ") },
  { title: "Powers", raised: true, keys: "² ³ ⁻¹ ⁰ ¹ ⁴ ⁵ ⁶ ⁷ ⁸ ⁹ ⁻ ⁺ ˙ ⁿ ᵀ ⁽ ⁾".split(" "),
    faces: "2 3 −1 0 1 4 5 6 7 8 9 − + . n T ( )".split(" ") },
  { title: "Indices", lowered: true, keys: "₀ ₁ ₂ ₃ ₄ ₅ ₆ ₇ ₈ ₉ ͵ ₙ ᵢ ⱼ ₖ ₊ ₋ ₍ ₎".split(" "),
    faces: "0 1 2 3 4 5 6 7 8 9 , n i j k + − ( )".split(" ") },
  { title: "Greek",
    keys: "α β γ δ ε ζ η θ ι κ λ μ ν ξ ο π ρ σ τ υ φ χ ψ ω Γ Δ Θ Λ Ξ Π Σ Φ Ψ Ω".split(" ") },
  { title: "Units", wide: true,
    keys: "Ω kΩ MΩ mΩ μF nF pF mH μH nH mV μV kV mA μA nA kHz MHz GHz ms μs ns mm μm cm °C ΔC K mW kW kWh N·m kN MPa bar".split(" ") },
  { title: "Logic and sets", keys: "∧ ∨ ¬ ∈ ∉ ∋ ∌ ∩ ∪ ∖ △ ⊕ ⊆ ⊇ ⊂ ⊃ ∅".split(" ") },
  { title: "Fractions", keys: "½ ⅓ ⅔ ¼ ¾ ⅛ ⅜".split(" ") },
  { title: "Raised letters", raised: true,
    keys: "ᵃ ᵇ ᶜ ᵈ ᵉ ᶠ ᵍ ʰ ⁱ ʲ ᵏ ˡ ᵐ ⁿ ᵒ ᵖ ʳ ˢ ᵗ ᵘ ᵛ ʷ ˣ ʸ ᶻ ᴬ".split(" "),
    faces: "a b c d e f g h i j k l m n o p r s t u v w x y z A".split(" ") },
  { title: "Lowered letters", lowered: true,
    keys: "ₐ ₑ ₕ ᵢ ⱼ ₖ ₗ ₘ ₙ ₒ ₚ ᵣ ₛ ₜ ᵤ ᵥ ₓ ᵦ".split(" "),
    faces: "a e h i j k l m n o p r s t u v x β".split(" ") },
  { title: "Tab and newline", keys: "⇥ ↵".split(" ") },
  { title: "Date and Roman marks", wide: true, lowered: true, keys: "ₜᵢₘₑ ᵣₒₘₑ".split(" "),
    faces: "time rome".split(" ") },
];

const GREEK = {
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", zeta: "ζ", eta: "η", theta: "θ",
  iota: "ι", kappa: "κ", lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", omicron: "ο", rho: "ρ", sigma: "σ",
  tau: "τ", upsilon: "υ", phi: "φ", chi: "χ", psi: "ψ", omega: "ω",
  Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ", Pi: "Π", Sigma: "Σ", Phi: "Φ",
  Psi: "Ψ", Omega: "Ω",
};

// Where LaTeX has a name for the glyph, that name is here (first), so
// habits carry over; the shorter everyday names come beside it.
const NAMED = {
  ohm: "Ω", micro: "μ", pi: "π", inf: "∞", infinity: "∞", hbar: "ℏ",
  cdot: "·", dot: "·", times: "×", div: "÷", pm: "±", plusminus: "±", minus: "−",
  sqrt: "√", cbrt: "∛", fourthroot: "∜", root: "√", uparrow: "↑", up: "↑",
  par: "‖", parallel: "‖", parallelto: "∥", angle: "∠", deg: "°", degC: "°C", degF: "°F",
  approx: "≈", ne: "≠", neq: "≠", le: "≤", leq: "≤", ge: "≥", geq: "≥", eq: "≟", eqeq: "＝", equiv: "≡",
  to: "→", arrow: "→", mapsto: "→", gets: "←", leftarrow: "←",
  show: "▸", as: "▸", unit: "▸", convert: "▸", interval: "‥", permille: "‰",
  land: "∧", and: "∧", wedge: "∧", lor: "∨", or: "∨", vee: "∨", lnot: "¬", not: "¬", neg: "¬",
  in: "∈", notin: "∉", ni: "∋", contains: "∋", notni: "∌", cap: "∩", cup: "∪", setminus: "∖",
  symdiff: "△", triangle: "△", oplus: "⊕", subseteq: "⊆", supseteq: "⊇", subset: "⊂", supset: "⊃",
  empty: "∅",
  sum: "∑", prod: "∏", partial: "∂", int: "∫", integral: "∫",
  half: "½", third: "⅓", twothirds: "⅔", quarter: "¼", threequarters: "¾", eighth: "⅛", threeeighths: "⅜",
  floor: "⌊⌋", ceil: "⌈⌉", transpose: "ᵀ", inverse: "⁻¹", squared: "²", cubed: "³",
  assign: "≔", deltaC: "ΔC", subbeta: "ᵦ",
  tab: "⇥", newline: "↵", nl: "↵", time: "ₜᵢₘₑ", rome: "ᵣₒₘₑ", roman: "ᵣₒₘₑ",
};

const SUP = { 0: "⁰", 1: "¹", 2: "²", 3: "³", 4: "⁴", 5: "⁵", 6: "⁶", 7: "⁷", 8: "⁸", 9: "⁹",
              "-": "⁻", "+": "⁺", ".": "˙", "(": "⁽", ")": "⁾", T: "ᵀ", A: "ᴬ",
              a: "ᵃ", b: "ᵇ", c: "ᶜ", d: "ᵈ", e: "ᵉ", f: "ᶠ", g: "ᵍ", h: "ʰ", i: "ⁱ", j: "ʲ", k: "ᵏ",
              l: "ˡ", m: "ᵐ", n: "ⁿ", o: "ᵒ", p: "ᵖ", r: "ʳ", s: "ˢ", t: "ᵗ", u: "ᵘ", v: "ᵛ", w: "ʷ",
              x: "ˣ", y: "ʸ", z: "ᶻ" };
const SUB = { 0: "₀", 1: "₁", 2: "₂", 3: "₃", 4: "₄", 5: "₅", 6: "₆", 7: "₇", 8: "₈", 9: "₉",
              ",": "͵", "-": "₋", "+": "₊", "(": "₍", ")": "₎",
              a: "ₐ", e: "ₑ", h: "ₕ", i: "ᵢ", j: "ⱼ", k: "ₖ", l: "ₗ", m: "ₘ", n: "ₙ", o: "ₒ", p: "ₚ",
              r: "ᵣ", s: "ₛ", t: "ₜ", u: "ᵤ", v: "ᵥ", x: "ₓ" };

// What a key is for, where neither its face nor the library says (shown
// in the panel's status line when the pointer rests on it).
const NOTES = {
  "⇥": "a tab, inside a string",
  "↵": "a new line, inside a string",
  "ₜᵢₘₑ": "after a string: a date, time or duration — \"2026-05-10\"ₜᵢₘₑ, \"PT90M\"ₜᵢₘₑ",
  "ᵣₒₘₑ": "after a string: a Roman numeral — \"MMXXVI\"ᵣₒₘₑ",
  "͵": "between two indices: M₁͵₂",
  "ᵦ": "as in kᵦ, the Boltzmann constant",
};

export const SUPERSCRIPT = SUP;
export const SUBSCRIPT = SUB;
export const SUPERSCRIPT_CHARS = new Set(Object.values(SUP));

/** [{name, glyph}] for everything a backslash name can produce. */
export const COMPLETIONS = [
  ...Object.entries(NAMED).map(([name, glyph]) => ({ name, glyph })),
  ...Object.entries(GREEK).map(([name, glyph]) => ({ name, glyph })),
  ...Object.entries(SUP).map(([key, glyph]) => ({ name: "^" + key, glyph })),
  ...Object.entries(SUB).map(([key, glyph]) => ({ name: "_" + key, glyph })),
];

/** name → glyph, for names typed out in full. */
export const GLYPH = new Map(COMPLETIONS.map((c) => [c.name, c.glyph]));

const NAMES = new Map();
for (const { name, glyph } of COMPLETIONS) NAMES.set(glyph, [...(NAMES.get(glyph) || []), "\\" + name]);

/** The backslash names that type `glyph`, each with its backslash. */
export function namesFor(glyph) {
  return NAMES.get(glyph) || [];
}

/** What a key is for, where the palette's library has nothing to say about it. */
export function noteFor(glyph) {
  return NOTES[glyph] || "";
}

/** Completions whose name starts with `prefix`; exact matches first. */
export function complete(prefix) {
  if (!prefix) return COMPLETIONS.slice(0, 12);
  const lower = prefix.toLowerCase();
  const starts = COMPLETIONS.filter((c) => c.name.startsWith(prefix));
  const loose = COMPLETIONS.filter((c) => !c.name.startsWith(prefix) && c.name.toLowerCase().startsWith(lower));
  const out = [...starts, ...loose];
  out.sort((a, b) => (a.name === prefix ? -1 : 0) - (b.name === prefix ? -1 : 0) || a.name.length - b.name.length);
  return out.slice(0, 12);
}
