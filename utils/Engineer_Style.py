"""
Pygments lexer and style for the engineering DSL.

This module extends Pygments so that nbconvert (HTML, LaTeX, PDF) and any
other Pygments-driven renderer can colour the DSL's extra syntax
distinctly:

  * Math-assignment glyphs           — ``:=``, ``←``, ``≔``     (Operator — plain, by preference)
  * DSL structural operators         — ``→`` (lambda/def arrow), ``ₜᵢₘₑ``/``ᵣₒₘₑ``
                                       (postfix literal tags)  (Operator.Word);  ``▶``/``▸``
                                       (target unit / display tag), ``..``/``‥`` (range)  (Operator — plain)
  * Engineering binary operators     — ``‖``, ``±``, ``∠``, ``≈``  (Operator)
  * Math glyphs / postfix operators  — ``°``, ``²``, ``√``, ``⌊⌋⌈⌉``, ``%``, ``‰``, ``!``,
                                       ``↑`` (power), ``ᵀ`` (transpose)  (Operator)
  * Set-theory operators             — ``∈``, ``∉``, ``∋``, ``∌``, ``∪``, ``∩``, ``∖``, ``△``,
                                       ``⊕``, ``⊆``, ``⊇``, ``⊂``, ``⊃``  (Operator); ``∅``  (Keyword.Constant)
  * Vulgar fractions                 — ``½``, ``⅓``, ``¾`` …            (Number)
  * Subscript / superscript runs     — ``₀``..``₉``, ``⁰``..``⁹``, ``ₙ``, ``ⁿ`` …  (Number.Other)
  * Physical constants               — ``c``, ``h``, ``ℏ``, ``k_B``, ``ε_0``, ``M☉``, ``R⊕`` … (Name.Constant.Physical)
  * SI + extra units                 — ``V``, ``Ω``, ``mV``, ``kΩ``, ``nF``, ``psi``, ``Nm``,
                                       ``ly``, ``eV``, ``hp`` …  (Name.Builtin.Unit)
  * Currency markers                 — ``DKK``, ``USD``, ``EUR`` …  (Name.Builtin.Currency)
  * Greek identifier letters         — ``π``, ``α``, ``β``, ``θ``, ``ω`` …    (Name.Builtin.Greek)
  * DSL helpers                      — ``parallel``, ``Γ``, ``Π``, ``∑``, ``∏``, ``phasor``, ``iso``,
                                       ``to_roman``, ``fit``, ``plot``, ``approx`` …  (Name.Function.Magic)
  * Symbol-declaration keywords      — ``symbols``, ``real_symbols``, ``positive_symbols`` …  (Keyword.Pseudo)

To use with nbconvert, register the lexer and style as Pygments plugins
via setup.py / pyproject entry points, OR pass them explicitly:

    jupyter nbconvert --to html notebook.ipynb \\
        --HTMLExporter.template_name=lab \\
        --HTMLExporter.theme=light

    (then in your nbconvert config: c.HighlightMagicsPreprocessor.enabled = True
     and c.HTMLExporter.pygments_lexer = 'engineering-dsl')

For ad-hoc use:

    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from utils.Engineer_Style import EngineeringDSLLexer, EngineeringDSLStyle

    html = highlight(source,
                     EngineeringDSLLexer(),
                     HtmlFormatter(style=EngineeringDSLStyle))
"""

from pygments.lexer import bygroups, words
from pygments.lexers.python import PythonLexer
from pygments.style import Style
from pygments.token import (
    Token, Comment, Keyword, Name, Number, Operator, String, Text,
)


# ---------------------------------------------------------------------------
# Custom token subtypes
# ---------------------------------------------------------------------------
# Pygments lets us introduce custom token kinds simply by indexing into
# Token.  Styles can then target these new kinds while still inheriting
# everything else from a standard style.

NameUnit          = Name.Builtin.Unit          # V, Ω, mV, kΩ
NameGreek         = Name.Builtin.Greek         # π, α, ω
NamePhysical      = Name.Constant.Physical     # c, h, ε_0
NameDSLHelper     = Name.Function.Magic        # parallel, Γ, phasor
NameCurrency      = Name.Builtin.Currency      # DKK, USD, EUR


# ---------------------------------------------------------------------------
# Vocabulary lists — kept in sync with the modules aggregated by
# utils.Engineer: calc_symbols.py, circuit_dsl.py, extra_units.py,
# currencies.py, chrono.py, symbolic.py, iso286.py, radix_formats.py,
# plotting.py, hardcopy_helpers.py, and Engineer.py itself.
# ---------------------------------------------------------------------------

_PHYSICAL_CONSTANTS = (
    # Defined-exact (post-2019 SI)
    'c', 'h', 'ℏ', 'ħ', 'k_B', 'N_A', 'q_e', 'R_gas', 'g_n', 'g_0', 'T_0',
    # CODATA-measured
    'ε_0', 'μ_0', 'm_e', 'm_p',
    # Math  (``i`` and ``E`` are deliberately absent: both are far too
    # common as plain variables — loop index, energy — to claim as
    # constants; ``oo`` is sympy's infinity.)
    'π', 'pi', 'inf', 'oo',
    # Astronomical reference values from extra_units.py — both the
    # underscore spellings and the journal-style glyph composites the
    # DSL accepts in source (``M☉``, ``M⊙``, ``R⊕``, ``M♃`` …).
    'M_sun', 'M_earth', 'M_jupiter', 'M_moon',
    'R_sun', 'R_earth', 'R_jupiter', 'L_sun', 'T_sun',
    'M☉', 'R☉', 'L☉', 'T☉', 'M⊙', 'R⊙', 'L⊙',
    'M⊕', 'R⊕', 'M♃', 'R♃',
    # Sub/superscript source spellings of the constants above —
    # accepted by the DSL (see ``_NON_CALLABLE_NAMES`` in circuit_dsl;
    # Python NFKC-normalises them to the calc_symbols aliases at parse
    # time), so they deserve the same constant colouring as ``k_B``.
    # The post-NFKC ASCII aliases (``me``, ``go``, ``NA`` …) are
    # deliberately NOT here: as bare words they collide with ordinary
    # variables far too often.
    'kᵦ', 'qₑ', 'Nᴬ', 'Rᵍᵃˢ', 'gₒ', 'gₙ', 'Tₒ', 'εₒ', 'μₒ',
    'm_n', 'mₑ', 'mₚ', 'mₙ',
)

_SI_UNITS = (
    # SI base
    'm', 'kg', 's', 'A', 'K', 'mol', 'cd',
    # SI derived
    'V', 'W', 'F', 'H', 'J', 'N', 'Pa', 'C', 'T', 'S', 'Wb',
    'Hz', 'rad', 'sr',
    'Ω', 'Ohm',
    # Prefix multipliers
    'prefix_p', 'prefix_n', 'prefix_μ', 'prefix_d', 'prefix_c',
    'prefix_m', 'prefix_k', 'prefix_M', 'prefix_G', 'prefix_T',
    # Common prefixed conveniences
    'pA', 'nA', 'μA', 'mA',
    'mΩ', 'kΩ', 'MΩ', 'GΩ',
    'nW', 'mW',
    'pF', 'nF', 'μF', 'mF',
    'pH', 'nH', 'μH', 'mH',
    'kHz', 'MHz', 'GHz', 'THz',
    'pV', 'nV', 'μV', 'mV',
    'ps', 'ns', 'μs', 'ms',
    'pm', 'nm', 'μm', 'mm', 'cm',
    'degC',
    # ---- extra_units.py catalogue ----
    # (``u`` — the atomic mass unit — is deliberately absent: a bare
    # ``u`` is too common as a plain variable to claim as a unit.)
    # Length — imperial / astro / metric
    'inch', 'ft', 'yard', 'mile', 'nautical_mile', 'thou', 'mil',
    'parsec', 'pc', 'ly', 'au', 'kpc', 'Mpc', 'Gpc',
    'km', 'hm', 'dam', 'dm', 'fm', 'Å',
    # Time
    'fs', 'ks', 'minute', 'hour', 'day', 'week', 'month', 'year',
    'year_julian', 'year_tropical', 'yr', 'kyr', 'Myr', 'Gyr',
    'HMS', 'hms',
    # Mass
    'g', 'mg', 'μg', 'ng', 'lb', 'lbm', 'oz', 'grain', 'slug', 'stone',
    'ton_us', 'ton_uk', 'tonne', 'ozt', 'Da', 'amu',
    # Voltage / current — large prefixes
    'kV', 'MV', 'kA',
    # Force
    'newton', 'μN', 'mN', 'kN', 'MN', 'GN', 'lbf', 'ozf', 'kgf', 'dyne',
    # Charge
    'coulomb', 'μC', 'mC', 'nC', 'pC', 'kC',
    # Pressure
    'hPa', 'kPa', 'MPa', 'GPa', 'bar', 'mbar', 'atm',
    'torr', 'mmHg', 'psi', 'ksi', 'inH2O',
    # Energy / particle physics
    'kJ', 'MJ', 'GJ', 'cal', 'Cal', 'kcal', 'BTU', 'kWh', 'Wh',
    'eV', 'keV', 'MeV', 'GeV', 'TeV', 'PeV', 'EeV', 'erg',
    'eV_per_c2', 'keV_per_c2', 'MeV_per_c2', 'GeV_per_c2', 'TeV_per_c2',
    'eV_per_c', 'keV_per_c', 'MeV_per_c', 'GeV_per_c', 'TeV_per_c',
    # Torque (display-preserving) & cross sections
    'Nm', 'mNm', 'kNm', 'MNm', 'Nmm', 'lbf_ft', 'lbf_inch', 'ozf_inch',
    'barn', 'mbarn', 'μbarn', 'ubarn', 'nbarn', 'pbarn', 'fbarn', 'abarn',
    # Astronomy — radio flux density
    'Jy', 'mJy', 'μJy', 'uJy',
    # Power
    'μW', 'kW', 'MW', 'GW', 'hp', 'hp_metric', 'hp_electrical',
    # Volume
    'liter', 'litre', 'mL', 'dL', 'cc',
    'gal_us', 'gal_uk', 'qt_us', 'pt_us', 'fl_oz_us',
    'qt_uk', 'pt_uk', 'fl_oz_uk', 'barrel',
    # Speed
    'mph', 'kph', 'knot',
    # Temperature (delta-by-default convention) & ratios
    'ΔK', 'ΔC', 'ΔF', 'deltaK', 'deltaC', 'deltaF', 'degF', 'degR',
    'ptm', 'ptc', 'ppm',
)

# Currency markers from currencies.py — a soft dependency of the
# toolkit, but the markers are part of the visible syntax
# (``salary := 50000 DKK``), so the highlighter always knows them.
_CURRENCIES = (
    'DKK', 'USD', 'EUR', 'GBP', 'JPY', 'SEK', 'NOK',
    'CHF', 'CAD', 'AUD', 'CNY', 'HKD', 'INR', 'PLN', 'CZK',
)

# NFKC lookalike codepoints.  Python normalises identifiers via NFKC
# (PEP 3131), so source may legally spell a name with either form and
# mean the same thing — ``kΩ`` typed with U+2126 OHM SIGN runs exactly
# like the U+03A9 GREEK CAPITAL OMEGA spelling, and ``µF`` with U+00B5
# MICRO SIGN like U+03BC GREEK SMALL MU.  Keyboards, IMEs, and symbol
# palettes produce both, so the vocabulary must match both or two
# visually identical lines highlight differently.
_NFKC_VARIANTS = {
    'Ω': ('Ω', 'Ω'),   # GREEK CAPITAL OMEGA / OHM SIGN
    'μ': ('μ', 'µ'),   # GREEK SMALL MU / MICRO SIGN
    'Å': ('Å', 'Å'),   # A WITH RING ABOVE / ANGSTROM SIGN
    'K': ('K', 'K'),   # LATIN CAPITAL K / KELVIN SIGN
}

# The variant codepoints, for splicing into the identifier-char classes.
_NFKC_VARIANT_CHARS = 'ΩµÅK'


# Identifier-character set used in trailing lookarounds (cannot CONTINUE
# an identifier with these chars).  Includes ASCII word chars plus Greek
# letters and special glyphs that appear inside DSL identifiers like
# ``μF``, ``mΩ``, ``ε_0``, ``ℏ``, ``ΔC``, ``Å`` — and the NFKC
# lookalikes (ohm/micro/angstrom/kelvin signs), which Python treats as
# the same identifier characters.
_ID_CHAR = r'A-Za-z0-9_πμΩεℏΓΠΣσαβγδζηθικλνξορςτυφχψωΦΘΛΨΔÅ' + _NFKC_VARIANT_CHARS

# Leading lookbehind: only letters and underscore disqualify a constant
# from matching.  This way ``2π`` recognises ``π`` (digit before is fine)
# but ``xπ`` keeps ``π`` as part of identifier ``xπ``.  Greek letters in
# the lookbehind so that ``aπ`` doesn't accidentally split inside
# multi-Greek identifiers like ``θπ``.
_ID_CHAR_LEAD = r'A-Za-z_πμΩεℏΓΠΣσαβγδζηθικλνξορςτυφχψωΦΘΛΨΔÅ' + _NFKC_VARIANT_CHARS




def _expand_variants(word):
    """All spellings of ``word`` over the NFKC lookalike codepoints."""
    forms = ['']
    for ch in word:
        forms = [f + v for f in forms for v in _NFKC_VARIANTS.get(ch, (ch,))]
    return forms


def _alt(words_seq):
    """Build a regex alternation that prefers longest matches.

    Each word is expanded over the NFKC lookalikes, so one vocabulary
    entry covers every spelling Python treats as the same identifier.
    """
    expanded = {form for w in words_seq for form in _expand_variants(w)}
    return '|'.join(sorted(expanded, key=len, reverse=True))


_GREEK_LETTERS = (
    # Lowercase
    'α', 'β', 'γ', 'δ', 'ε', 'ζ', 'η', 'θ', 'ι', 'κ',
    'λ', 'μ', 'ν', 'ξ', 'ο', 'ρ', 'ς', 'σ', 'τ', 'υ',
    'φ', 'χ', 'ψ', 'ω',
    # Uppercase  (Γ Π Σ Ω also serve as DSL helpers / units, so the rules
    # below take priority for those four; Δ Θ Λ Ξ Φ Ψ remain pure Greek.)
    'Δ', 'Θ', 'Λ', 'Ξ', 'Φ', 'Ψ',
)

_DSL_HELPERS = (
    'parallel', 'percent', 'permille', 'fact', 'mod', 'plusminus',
    'pp', 'pv', 'pn', 'plot', 'display',
    'sqrt', 'log10', 'log2', 'ln', 'floor', 'ceil',
    'phasor', 'to_dB_v', 'to_dB_p', 'from_dB_v', 'from_dB_p',
    'approx', 'exact', 'measured', 'sigfigs_of',
    'σ', 'Σ', 'Γ', 'Π',
    'protect', 'unprotect',
    'protect_si_units', 'protect_constants', 'protect_all',
    'list_protected', 'clear_protections',
    'launch_palette',
    # sigfig / display machinery re-exported by circuit_dsl
    'Sig', 'mean', 'radix', 'in_units', 'register_radix',
    'set_decimal_literals', 'get_decimal_literals',
    # chrono.py — ISO 8601 date/time/duration parsing
    'iso',
    # symbolic.py — the sympy bridge
    'sym', 'Symbol', 'Eq', 'Rational',
    'solve', 'expand', 'factor', 'simplify',
    'diff', 'integrate', 'limit', 'series',
    'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'atan2',
    'sinh', 'cosh', 'tanh', 'asinh', 'acosh', 'atanh',
    'exp', 'log',
    # iso286.py — limits & fits
    'hole', 'shaft', 'fit', 'it_grade', 'tolerance_unit',
    # radix_formats.py + the ``▸`` display-tag names.  ``hex``/``bin``/
    # ``oct`` are Python builtins, but as radix tags they must colour
    # like ``dec``/``roman`` — and claiming them here keeps the family
    # uniform in the live editor too, where a bare builtin name gets no
    # colour at all.
    'to_roman', 'from_roman', 'roman', 'dec', 'hex', 'bin', 'oct',
    'base2', 'base8', 'base10', 'base16',
    # plotting.py
    'linefit', 'polyfit', 'list_themes',
    # hardcopy_helpers.py
    'print_view', 'hardcopy',
    # currencies.py — rate management
    'get_currency_rate', 'update_currency_rates',
    'clear_currency_cache', 'rates_status',
    # extra_units.py — temperature conversions
    'to_fahrenheit', 'to_celsius', 'to_kelvin',
    'to_celsius_from_F', 'to_kelvin_from_F', 'to_fahrenheit_from_K',
    'from_degC', 'from_degF', 'from_degR',
    # Engineer.py itself
    'fmt', 'refresh_display',
)

# Symbol-declaration keywords — used both postfix (``x, y := symbols``)
# and prefix (``symbols: x, y``) by the sympy-declaration rewriters in
# circuit_dsl.  Highlighted as pseudo-keywords so declarations read as
# structure, not as function calls.
_SYMBOL_DECL_KEYWORDS = (
    'symbols', 'positive_symbols', 'real_symbols',
    'integer_symbols', 'complex_symbols',
)


# ---------------------------------------------------------------------------
# The lexer
# ---------------------------------------------------------------------------

class EngineeringDSLLexer(PythonLexer):
    """Python with the engineering-DSL extras highlighted distinctly.

    The base ``PythonLexer`` already understands every legal Python
    construct; this subclass merely *prepends* a small set of high-priority
    rules to ``'root'`` so that DSL-specific glyphs and identifiers get
    recognised before Python's own rules see them.

    Order matters in Pygments:
      1. Math-assignment glyphs must come before single ``=`` / ``:=``.
      2. Subscript/superscript digits must come before identifier rules.
      3. Constants & helper names are matched as bare words (``\\b...\\b``)
         so they don't fire inside other identifiers.
    """

    name = 'EngineeringDSL'
    aliases = ['engineering-dsl', 'edsl', 'eedsl']
    filenames = []          # opt-in only; don't auto-claim .py files
    mimetypes = []

    EXTRA_TOKENS = [
        # ---- math-assignment glyphs ----
        # Plain Operator (black regular, author's preference) — the
        # glyphs stand out by shape, like the arithmetic operators.
        (r':=|≔|←', Operator),

        # ---- inclusive-range dots ----
        # Also plain Operator by preference.  The lookarounds keep
        # Python's ``...`` ellipsis intact.  An integer directly
        # before ``..`` needs its own rule: without it, Python's float
        # rule would consume ``1.`` out of ``[1..10]`` before the
        # scanner ever reaches the range operator.
        (r'(\d+)(\.\.)(?!\.)', bygroups(Number.Integer, Operator)),
        (r'‥|(?<!\.)\.\.(?!\.)', Operator),

        # ---- DSL structural operators ----
        # ``▶``/``▸`` (target-unit / display tag) are plain Operator
        # too, by preference; only ``→`` (lambda / def return arrow)
        # keeps the distinct Operator.Word colour.
        (r'▶|▸', Operator),
        (r'→', Operator.Word),

        # ---- postfix literal tags ----
        # ``"2026-05-05"ₜᵢₘₑ`` → iso(...), ``"MCMIX"ᵣₒₘₑ`` → from_roman(...).
        # Must precede the generic subscript-run rule below, which would
        # otherwise consume these letters as an index run.
        (r'ₜᵢₘₑ|ᵣₒₘₑ', Operator.Word),

        # ---- engineering binary operators ----
        (r'‖|±|∠|≈', Operator),

        # ---- set-theory glyphs ----
        # ``∅`` is a value (the empty set), the rest are operators.  A
        # standalone ``⊕`` is symmetric difference; in the astro
        # composites (``M⊕``, ``R⊕``) the lexer reaches the ``M``/``R``
        # first and the constants rule consumes the whole composite, so
        # this ``⊕`` only fires when the glyph stands alone.
        (r'∅', Keyword.Constant),
        (r'∈|∉|∋|∌|∪|∩|∖|△|⊕|⊆|⊇|⊂|⊃', Operator),

        # ---- n-ary sum / product glyphs ----
        # ``∑`` and ``∏`` are source-level aliases of the ``Σ`` / ``Π``
        # helpers (normalize_source rewrites them), so colour them the
        # same way.  They are not Python identifier characters, so
        # without this rule they'd fall through as errors.
        (r'∑|∏', NameDSLHelper),

        # ---- temperature-scale units ----
        # ``°C``/``°F``/``°R`` lex as ONE unit token (the DSL rewrites
        # ``27 °C`` into ``from_degC(27)``), matching the single-glyph
        # ``℃``.  Must precede the math-glyph rule, which would
        # otherwise claim the bare ``°`` (the angle postfix, e.g.
        # ``45°`` — still an operator when not followed by C/F/R).
        (r'°[CFR](?![' + _ID_CHAR + r'])', NameUnit),
        (r'℃|℉', NameUnit),

        # ---- math glyphs / unary operators ----
        # ``↑`` is Knuth's power arrow, ``ᵀ`` the matrix transpose,
        # ``!`` the postfix factorial (bare only — the lookahead keeps
        # Python's ``!=`` a single operator token).
        (r'°|²|³|√|⌊|⌋|⌈|⌉|‰|×|÷|·|⋅|−|≤|≥|≠|∞|↑|ᵀ|!(?!=)', Operator),

        # ---- tab / newline argument glyphs ----
        # ``print("a" ⇥ "b" ↵)`` — each glyph stands for a ``"\t"`` /
        # ``"\n"`` string argument, so colour them like string escapes.
        (r'[⇥⭾↵⏎↩⮐]', String.Escape),

        # ---- vulgar fractions ----
        (r'[½⅓¼¾⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]', Number),

        # ---- subscript / superscript runs (digits, signs, letters) ----
        # Letters included because the DSL uses them as indices
        # (``xₙ`` → ``x[n]``) and root indices (``ⁿ√x``).  ``͵`` (U+0375)
        # is the matrix dimension separator (``M₀͵₁`` → ``M[0][1]``) and
        # ``˙`` (U+02D9) the superscript decimal point (``k⁰˙⁵⁵``), so
        # both belong inside a run.
        (r'[₀₁₂₃₄₅₆₇₈₉₊₋₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓᵦᵧᵨᵩᵪ͵]+', Number.Other),
        (r'[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾˙ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ]+', Number.Other),

        # ---- base-suffixed integer literals ----
        # Mirrors ``_BASE_LITERAL_RES`` in circuit_dsl: a digit-led,
        # hex-permissive run before any base subscript (``0715₈``,
        # ``11111111₂``, ``fed…`` forms led by a digit), and a
        # hex-letter-led run before ``₁₆`` specifically (``fed₁₆``,
        # ``DEADBEEF₁₆`` — restricted to base 16 exactly as the
        # rewriter is, so ``samples₃`` stays an indexed identifier).
        # The mantissa colours as a number so every base looks alike;
        # the base subscript keeps the subscript colouring.  Must sit
        # before the vocabulary rules — ``c₁₆`` is 0xc, not the speed
        # of light, and ``A₁₆`` is 0xA, not the ampere.
        (r'(?<![A-Za-z_0-9])([0-9][0-9a-fA-F]*)([₀₁₂₃₄₅₆₇₈₉]+)',
         bygroups(Number.Integer, Number.Other)),
        (r'(?<![A-Za-z_0-9])([a-fA-F]+)(₁₆)',
         bygroups(Number.Hex, Number.Other)),

        # ---- physical constants ----
        # Lookarounds (rather than \b) accept numeric / Greek prefix and
        # block alphanumeric continuation, so ``2π`` recognises the constant
        # while ``q_eat`` does not.  Longest-first so ``k_B`` wins over ``k``.
        (r'(?<![' + _ID_CHAR_LEAD + r'])(?:' + _alt(_PHYSICAL_CONSTANTS) +
         r')(?![' + _ID_CHAR + r'])',
         NamePhysical),

        # ---- SI units ----
        (r'(?<![' + _ID_CHAR_LEAD + r'])(?:' + _alt(_SI_UNITS) +
         r')(?![' + _ID_CHAR + r'])',
         NameUnit),

        # ---- single Greek letter as identifier ----
        (r'(?<![' + _ID_CHAR_LEAD + r'])(?:' + _alt(_GREEK_LETTERS) +
         r')(?![' + _ID_CHAR + r'])',
         NameGreek),

        # ---- currency markers ----
        (r'(?<![' + _ID_CHAR_LEAD + r'])(?:' + _alt(_CURRENCIES) +
         r')(?![' + _ID_CHAR + r'])',
         NameCurrency),

        # ---- symbol-declaration keywords ----
        # Before the helper rule not for precedence (the vocabularies
        # don't overlap) but to keep the keyword-ish rules together.
        (r'(?<![' + _ID_CHAR_LEAD + r'])(?:' + _alt(_SYMBOL_DECL_KEYWORDS) +
         r')(?![' + _ID_CHAR + r'])',
         Keyword.Pseudo),

        # ---- DSL helper functions / values ----
        (r'(?<![' + _ID_CHAR_LEAD + r'])(?:' + _alt(_DSL_HELPERS) +
         r')(?![' + _ID_CHAR + r'])',
         NameDSLHelper),

        # ---- identifiers with glued modifier-letter suffixes ----
        # Subscript/superscript LETTERS (``ₙ``, ``ᵀ``, ``ₜᵢₘₑ`` …) are
        # Unicode Lm modifier letters, which Python's identifier rule
        # happily swallows — ``Mᵀ`` and ``xₙ`` would each lex as ONE
        # plain Name.  These two rules split the identifier from its
        # suffix so the transpose reads as an operator and letter
        # subscripts get the same index colouring as digit subscripts
        # (``xₙ`` means ``x[n]``, just like ``x₁`` means ``x[1]``).
        # Placed after the vocabulary rules so a known unit/constant
        # prefix (``sᵀ``) still wins the leading-word match.
        (r'([' + _ID_CHAR_LEAD + r'][' + _ID_CHAR + r']*)(ᵀ)'
         r'(?![' + _ID_CHAR + r'])',
         bygroups(Name, Operator)),
        # Only the Lm letters here — sub/superscript DIGITS are Unicode
        # No and never glue into a Name, so the standalone run rules
        # above already handle them (and ``²``/``³`` keep their
        # established Operator colouring).
        (r'([' + _ID_CHAR_LEAD + r'][' + _ID_CHAR + r']*)'
         r'([ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓᵦᵧᵨᵩᵪᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ]+)',
         bygroups(Name, Number.Other)),
    ]

    # Splice EXTRA_TOKENS into a copy of the base 'root' state, *at the top*.
    tokens = dict(PythonLexer.tokens)
    tokens['root'] = EXTRA_TOKENS + list(PythonLexer.tokens['root'])


# ---------------------------------------------------------------------------
# The style
# ---------------------------------------------------------------------------

class EngineeringDSLStyle(Style):
    """A light-background style tuned for engineering notebooks.

    Inherits the readable defaults from Pygments' built-in ``default``
    style and overrides only the DSL-specific token kinds.  The colour
    choices follow a simple scheme:

      * Constants and units (immutable quantities) — navy blue,
        regular weight (author's preference)
      * Greek — deep purple
      * Resistor/EE numbers — orange (engineering-y)
      * Math operators (‖, ±, ∠, ≈) — plain text colour (the glyphs
        are distinctive enough on their own — author's preference)
      * Math-assignment (≔, :=, ←) — plain text, regular weight
      * Helper functions — soft brown
    """

    name = 'engineering-dsl'
    background_color = '#fdfdfd'
    default_style = ''

    styles = {
        # Inherit from Python defaults for unrelated tokens
        Comment:                    'italic #888',
        Comment.Single:             'italic #888',
        Keyword:                    '#19a86d',
        Keyword.Constant:           '#218bc0',
        Operator:                   '#000000',
        String:                     '#cc1e3b',
        String.Doc:                 'italic #cc1e3b',
        Number:                     '#19a86d',
        Name.Function:              '#06287e',
        Name.Class:                 'bold #0e84b5',
        Name.Builtin:               '#007020',
        Name.Exception:             'bold #d2413a',

        # ---- DSL-specific overrides ----
        Operator.Word:              '#19a86d',          # := ≔ ← → ▶ ..
        # Units and constants share navy regular by the author's
        # preference — one quiet colour for every immutable quantity.
        NamePhysical:               '#218bc0',          # c, h, ε_0, M☉
        NameUnit:                   '#218bc0',          # V, Ω, mV, psi
        NameCurrency:               '#a08600',          # DKK, USD, EUR
        NameGreek:                  '#5a3e8c',          # π, α, ω
        NameDSLHelper:              '#a08600',          # parallel, Γ, iso
        Keyword.Pseudo:             '#a08600',          # symbols, real_symbols
        Number.Other:               '#7a4ea0',          # subscripts/superscripts
    }


class EngineeringDSLStyleDark(Style):
    """Dark-background variant of EngineeringDSLStyle.

    Same colour-coding logic, but tones picked for legibility on dark
    backgrounds (Jupyter "dark" / "JupyterLab Dark" themes, or any
    terminal with a dark colour scheme).  Hex values were chosen to
    sit comfortably on backgrounds in the ``#1e1e1e`` – ``#272822``
    range and to retain enough chroma to be distinguished at a glance.
    """

    name = 'engineering-dsl-dark'
    background_color = '#1e1e1e'
    default_style = ''

    styles = {
        Comment:                    'italic #9d9d9d',
        Comment.Single:             'italic #9d9d9d',
        Keyword:                    '#19a86d',
        Keyword.Constant:           '#218bc0',
        Operator:                   '#d4d4d4',
        String:                     '#e2506d',
        String.Doc:                 'italic #e2506d',
        Number:                     '#19a86d',
        Name.Function:              '#dcdcaa',
        Name.Class:                 'bold #4ec9b0',
        Name.Builtin:               '#4ec9b0',
        Name.Exception:             'bold #f48771',

        # ---- DSL-specific overrides ----
        Operator.Word:              '#19a86d',          # := ≔ ← → ▶ ..
        # Units and constants share one regular blue (the dark-theme
        # stand-in for the light style's navy).
        NamePhysical:               '#218bc0',          # c, h, ε_0, M☉
        NameUnit:                   '#218bc0',          # V, Ω, mV, psi
        NameCurrency:               '#fbd924',          # DKK, USD, EUR
        NameGreek:                  '#c586c0',          # π, α, ω
        NameDSLHelper:              '#fbd924',          # parallel, Γ, iso
        Keyword.Pseudo:             '#fbd924',          # symbols, real_symbols
        Number.Other:               '#bb9af7',          # subscripts/superscripts
    }
