"""Generate src/dslRules.js from utils/Engineer_Style.py.

The JupyterLab extension highlights the SAME vocabulary as the Pygments
lexer used for exports.  To keep the two from drifting, this script
reads ``EngineeringDSLLexer.EXTRA_TOKENS`` and emits the rules as a
JavaScript module.  Re-run it after editing Engineer_Style.py:

    python generate_rules.py

The Pygments patterns are written with only regex features JavaScript
also supports (alternation, character classes, lookarounds), so they
transfer verbatim; the ``u`` flag handles the non-BMP-free Unicode.

A few rules in EXTRA_TOKENS use ``bygroups`` (a Python callable that
cannot be introspected cleanly); those are recognised by their pattern
text and emitted with an explicit per-group class list.  One extra
JS-only rule is appended for trailing-dot decimals (``10.`` — DSL
sig-fig notation) so CodeMirror's member-access parse of ``10. kΩ``
doesn't leave the number half-coloured.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from pygments.token import Keyword, Number, Operator, String  # noqa: E402
from utils.Engineer_Style import (  # noqa: E402
    _ID_CHAR, _ID_CHAR_LEAD,
    EngineeringDSLLexer, NameCurrency, NameDSLHelper, NameGreek,
    NamePhysical, NameUnit,
)

TOKEN_CLASS = {
    Operator.Word:    'edsl-opword',
    Operator:         'edsl-op',
    Keyword.Constant: 'edsl-constkw',
    Keyword.Pseudo:   'edsl-decl',
    Number:           'edsl-number',
    Number.Integer:   'edsl-number',
    Number.Other:     'edsl-subsup',
    String.Escape:    'edsl-strescape',
    NamePhysical:     'edsl-physical',
    NameUnit:         'edsl-unit',
    NameCurrency:     'edsl-currency',
    NameGreek:        'edsl-greek',
    NameDSLHelper:    'edsl-helper',
}

rules = []
for pattern, action in EngineeringDSLLexer.EXTRA_TOKENS:
    if callable(action):
        # The bygroups rules — identified by pattern content.
        if pattern.startswith(r'(?<!\.)(\.\.)'):
            # number AFTER the range dots: dots, space, sign, number
            groups = ['edsl-op', None, 'edsl-op', 'edsl-number']
        elif r'\.\.' in pattern:
            # integer BEFORE the range dots
            groups = ['edsl-number', 'edsl-op']
        elif 'ᵀ' in pattern:
            groups = [None, 'edsl-op']
        elif '(h|min|in)' in pattern:
            # unit-position hour / minute / inch (whitespace, unit)
            groups = [None, 'edsl-unit']
        elif '0-9a-fA-F' in pattern or '[a-fA-F]+' in pattern:
            # base-suffixed integer literals (mantissa + base subscript)
            groups = ['edsl-number', 'edsl-subsup']
        else:
            groups = [None, 'edsl-subsup']
        rules.append({'pattern': pattern, 'groups': groups})
    else:
        rules.append({'pattern': pattern, 'cls': TOKEN_CLASS[action]})

# JS-only extra: a decimal literal with a trailing dot (``10.`` — the
# DSL's "trailing zeros are significant" notation).  CodeMirror's
# Python parser reads ``10. kΩ`` as member access, splitting the
# colouring; marking the whole literal here keeps it number-coloured.
rules.append({'pattern': r'(?<![\w.])\d+\.(?![\d.\w])', 'cls': 'edsl-number'})

# JS-only extra: a string literal directly after the ``..`` range
# operator (``['A'..'E']``, ``'a'..'f'``).  CodeMirror's error
# recovery around the double dot mis-tags the RIGHT endpoint (member
# access on a string), colouring it as a property while the left
# endpoint stays string-red.  Re-claim it with the theme's string
# colour.  Pygments tokenises these strings correctly, so exports
# need no counterpart.
# NB: no ``\"`` escapes — JS unicode-mode regexes reject them as
# invalid identity escapes, and one bad pattern kills the whole
# extension module at load.  (Raw strings keep the backslash that a
# Python string delimiter escape would need, hence the concatenation.)
rules.append({
    'pattern': (r"(\.\.\s*)('(?:[^'\\\n]|\\.)*'|"
                + r'"(?:[^"\\\n]|\\.)*")'),
    'groups': [None, 'edsl-string'],
})

# JS-only extra: IDENTIFIER endpoints of ``..`` ranges
# (``symbols: x..z``, ``R1..R4``) — same member-access misparse as the
# string endpoints above, colouring the right endpoint (sometimes the
# left too) as a property.  Reclaim both sides as plain names.  These
# sit after the vocabulary rules, so an endpoint that IS a known
# unit/constant keeps its vocabulary colour.
rules.append({'pattern': r'[A-Za-z_][A-Za-z0-9_]*(?=\s*\.\.(?!\.))',
              'cls': 'edsl-plain'})
rules.append({'pattern': r'(\.\.\s*)([A-Za-z_][A-Za-z0-9_]*)',
              'groups': [None, 'edsl-plain']})

# JS-only extra: numeric literals — claimed so numbers take the
# palette's green everywhere (the base theme colours them its own
# green).  Hex/bin/oct literals, floats, exponents, complex ``j``.
rules.append({
    'pattern': (r'(?<![\w.])(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|'
                r'0[oO][0-7_]+|\d[\d_]*\.?[\d_]*(?:[eE][+-]?\d+)?[jJ]?|'
                r'\.\d[\d_]*(?:[eE][+-]?\d+)?[jJ]?)'),
    'cls': 'edsl-number',
})

# JS-only extra: a bare ``=`` — DSL math equality at statement level
# (``if k % 2 = 1:``), keyword argument inside calls.  Pygments colours
# every ``=`` as an operator in the exports, and CodeMirror's parser
# leaves the DSL-equality case uncoloured (it sits in an error-recovery
# region), so mark it operator here for the same uniform look.  The
# lookarounds keep ``==``, ``<=``, ``!=``, ``:=``, ``+=`` and friends
# to their existing single-token colouring.
rules.append({'pattern': r'(?<![=<>!:+\-*/%&|^@])=(?!=)', 'cls': 'edsl-op'})

# JS-only extra: Python reserved words.  The jp theme renders keywords
# BOLD; the author prefers regular weight, so claim them under
# ``edsl-keyword`` (theme keyword colour, weight normal).  ``True``/
# ``False``/``None`` are left to the base theme — they are values, not
# control words, and claiming them here would shift their colour.
# Lookarounds use the lexer's identifier classes so a keyword glued to
# a Unicode identifier char (``forΩ``) stays an identifier.
_PY_KEYWORDS = ('and|as|assert|async|await|break|class|continue|def|del|'
                'elif|else|except|finally|for|from|global|if|import|in|is|'
                'lambda|nonlocal|not|or|pass|raise|return|try|while|with|'
                'yield')
rules.append({
    'pattern': ('(?<![' + _ID_CHAR_LEAD + '])(?:' + _PY_KEYWORDS
                + ')(?![' + _ID_CHAR + '])'),
    'cls': 'edsl-keyword',
})

# JS-only extra, lowest priority: ASCII operator runs.  The operator
# family is styled PLAIN (regular weight, default text colour) by the
# author's preference — the jp theme renders base operators bold
# lilac, so claiming them under ``edsl-op`` keeps ``<`` matching ``≤``
# and ``%`` matching ``‰`` under that preference.  ``@`` is left out
# so decorators keep their meta styling; ``:`` is punctuation and not
# claimed (``:=`` is caught earlier by the opword rule).
rules.append({'pattern': r'[+\-*/%<>=!&|^~]+', 'cls': 'edsl-op'})

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src', 'dslRules.js')
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, 'w', encoding='utf-8', newline='\n') as f:
    f.write('// GENERATED by generate_rules.py from utils/Engineer_Style.py — do not edit.\n')
    f.write('// Re-run `python generate_rules.py` after changing the Pygments lexer.\n')
    f.write('export const DSL_RULES = ')
    json.dump(rules, f, ensure_ascii=False, indent=2)
    f.write(';\n')
print(f'wrote {out} ({len(rules)} rules)')

# Compile-check every pattern as a JS unicode-mode regex.  One invalid
# pattern (e.g. a stray \" identity escape, which u-mode rejects)
# throws when the extension module loads and silently kills ALL
# highlighting — so fail generation loudly instead.
import subprocess  # noqa: E402
check = subprocess.run(
    ['node', '--input-type=module', '-e',
     "import { DSL_RULES } from " + json.dumps('file://' + out.replace(os.sep, '/')) + ";"
     "let bad = 0;"
     "for (const r of DSL_RULES) {"
     "  try { new RegExp(r.pattern, 'gu'); }"
     "  catch (e) { bad++; console.error('BAD PATTERN:', e.message, r.pattern); }"
     "}"
     "if (bad) process.exit(1);"
     "console.log('all patterns compile as JS /gu regexes');"],
    capture_output=True, text=True)
sys.stdout.write(check.stdout)
sys.stderr.write(check.stderr)
if check.returncode != 0:
    sys.exit('generated rules failed the JS compile check — dslRules.js NOT safe to ship')
