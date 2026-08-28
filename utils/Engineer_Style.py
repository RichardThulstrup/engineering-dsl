"""
Pygments lexer and style for the engineering DSL.

This module extends Pygments so that nbconvert (HTML, LaTeX, PDF) and any
other Pygments-driven renderer can colour the DSL's extra syntax
distinctly:

  * Math-assignment glyphs           — ``:=``, ``←``, ``≔``     (Operator.Word)
  * Engineering binary operators     — ``‖``, ``±``, ``∠``, ``≈``  (Operator)
  * Math glyphs / postfix operators  — ``°``, ``²``, ``√``, ``⌊⌋⌈⌉``, ``%``, ``‰``, ``!``  (Operator)
  * Subscript / superscript digits   — ``₀``..``₉``, ``⁰``..``⁹``     (Number.Other)
  * Resistor / EE notation           — ``4k7``, ``2R2``, ``100n``, ``1M5`` (Number.Engineering)
  * Physical constants               — ``c``, ``h``, ``ℏ``, ``k_B``, ``ε_0`` … (Name.Constant.Physical)
  * SI base + prefixed units         — ``V``, ``Ω``, ``mV``, ``kΩ``, ``nF`` …  (Name.Builtin.Unit)
  * Greek identifier letters         — ``π``, ``α``, ``β``, ``θ``, ``ω`` …    (Name.Builtin.Greek)
  * DSL helpers                      — ``parallel``, ``Γ``, ``Π``, ``phasor``, ``to_dB_v``, ``approx`` …  (Name.Function.Magic)

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

NumberEngineering = Number.Engineering         # 4k7, 2R2, 100n
NameUnit          = Name.Builtin.Unit          # V, Ω, mV, kΩ
NameGreek         = Name.Builtin.Greek         # π, α, ω
NamePhysical      = Name.Constant.Physical     # c, h, ε_0
NameDSLHelper     = Name.Function.Magic        # parallel, Γ, phasor


# ---------------------------------------------------------------------------
# Vocabulary lists — kept in sync with calc_symbols.py and circuit_dsl.py
# ---------------------------------------------------------------------------

_PHYSICAL_CONSTANTS = (
    # Defined-exact (post-2019 SI)
    'c', 'h', 'ℏ', 'ħ', 'k_B', 'N_A', 'q_e', 'R_gas', 'g_n', 'T_0',
    # CODATA-measured
    'ε_0', 'μ_0', 'm_e', 'm_p',
    # Math
    'π', 'pi', 'inf',
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
)

# Identifier-character set used in trailing lookarounds (cannot CONTINUE
# an identifier with these chars).  Includes ASCII word chars plus Greek
# letters and special glyphs that appear inside DSL identifiers like
# ``μF``, ``mΩ``, ``ε_0``, ``ℏ``.
_ID_CHAR = r'A-Za-z0-9_πμΩεℏΓΠΣσαβγδζηθικλνξορςτυφχψωΦΘΛΨ'

# Leading lookbehind: only letters and underscore disqualify a constant
# from matching.  This way ``2π`` recognises ``π`` (digit before is fine)
# but ``xπ`` keeps ``π`` as part of identifier ``xπ``.  Greek letters in
# the lookbehind so that ``aπ`` doesn't accidentally split inside
# multi-Greek identifiers like ``θπ``.
_ID_CHAR_LEAD = r'A-Za-z_πμΩεℏΓΠΣσαβγδζηθικλνξορςτυφχψωΦΘΛΨ'


def _alt(words_seq):
    """Build a regex alternation that prefers longest matches."""
    return '|'.join(sorted(set(words_seq), key=len, reverse=True))


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
    'sqrt', 'log10', 'log2', 'ln', 'floor', 'ceil',
    'phasor', 'to_dB_v', 'to_dB_p', 'from_dB_v', 'from_dB_p',
    'approx', 'exact', 'measured', 'sigfigs_of',
    'σ', 'Σ', 'Γ', 'Π',
    'protect', 'unprotect',
    'protect_si_units', 'protect_constants', 'protect_all',
    'list_protected', 'clear_protections',
    'launch_palette',
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
      1. Resistor/EE notation (``4k7``) must come before plain numbers.
      2. Math-assignment glyphs must come before single ``=`` / ``:=``.
      3. Subscript/superscript digits must come before identifier rules.
      4. Constants & helper names are matched as bare words (``\\b...\\b``)
         so they don't fire inside other identifiers.
    """

    name = 'EngineeringDSL'
    aliases = ['engineering-dsl', 'edsl', 'eedsl']
    filenames = []          # opt-in only; don't auto-claim .py files
    mimetypes = []

    EXTRA_TOKENS = [
        # ---- resistor / EE notation: 4k7, 2R2, 100n, 1M5, 4u7 ----
        # Must precede Pygments' generic Number rule.
        (r'(?<!\w)\d+[RkMGTmµunp]\d*(?!\w)', NumberEngineering),

        # ---- math-assignment glyphs ----
        (r':=|≔|←', Operator.Word),

        # ---- engineering binary operators ----
        (r'‖|±|∠|≈', Operator),

        # ---- math glyphs / unary operators ----
        (r'°|²|³|√|⌊|⌋|⌈|⌉|‰|×|÷|·|⋅|−|≤|≥|≠|∞', Operator),

        # ---- subscript / superscript digits & signs ----
        (r'[₀₁₂₃₄₅₆₇₈₉₊₋₍₎]+', Number.Other),
        (r'[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾]+', Number.Other),

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

        # ---- DSL helper functions / values ----
        (r'(?<![' + _ID_CHAR_LEAD + r'])(?:' + _alt(_DSL_HELPERS) +
         r')(?![' + _ID_CHAR + r'])',
         NameDSLHelper),
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

      * Constants (immutable, defined values) — deep blue
      * Units (also immutable but functionally distinct) — teal
      * Greek — deep purple
      * Resistor/EE numbers — orange (engineering-y)
      * Math operators (‖, ±, ∠, ≈) — bold dark grey
      * Math-assignment (≔, :=, ←) — bold green
      * Helper functions — soft brown
    """

    name = 'engineering-dsl'
    background_color = '#fdfdfd'
    default_style = ''

    styles = {
        # Inherit from Python defaults for unrelated tokens
        Comment:                    'italic #888',
        Comment.Single:             'italic #888',
        Keyword:                    'bold #007020',
        Keyword.Constant:           'bold #0086b3',
        Operator:                   '#666',
        String:                     '#4070a0',
        String.Doc:                 'italic #4070a0',
        Number:                     '#40a070',
        Name.Function:              '#06287e',
        Name.Class:                 'bold #0e84b5',
        Name.Builtin:               '#007020',
        Name.Exception:             'bold #d2413a',

        # ---- DSL-specific overrides ----
        NumberEngineering:          'bold #c8651b',     # 4k7, 100n
        Operator.Word:              'bold #2b8a3e',     # := ≔ ←
        NamePhysical:               'bold #1f3a93',     # c, h, ε_0
        NameUnit:                   '#0a7d7d',          # V, Ω, mV
        NameGreek:                  '#5a3e8c',          # π, α, ω
        NameDSLHelper:              '#a05a2c',          # parallel, Γ
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
        Comment:                    'italic #6a9955',
        Comment.Single:             'italic #6a9955',
        Keyword:                    'bold #569cd6',
        Keyword.Constant:           'bold #4fc1ff',
        Operator:                   '#d4d4d4',
        String:                     '#ce9178',
        String.Doc:                 'italic #ce9178',
        Number:                     '#b5cea8',
        Name.Function:              '#dcdcaa',
        Name.Class:                 'bold #4ec9b0',
        Name.Builtin:               '#4ec9b0',
        Name.Exception:             'bold #f48771',

        # ---- DSL-specific overrides ----
        NumberEngineering:          'bold #ffa657',     # 4k7, 100n
        Operator.Word:              'bold #4ec9b0',     # := ≔ ←
        NamePhysical:               'bold #79c0ff',     # c, h, ε_0
        NameUnit:                   '#76d7c4',          # V, Ω, mV
        NameGreek:                  '#c586c0',          # π, α, ω
        NameDSLHelper:              '#d2a06c',          # parallel, Γ
        Number.Other:               '#bb9af7',          # subscripts/superscripts
    }
