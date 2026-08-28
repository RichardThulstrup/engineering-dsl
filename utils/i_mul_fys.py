"""
implicit_multiplication.py
---------------------------

Source transformer for the ``ideas`` import hook that makes algebra-like
expressions more natural to write in Python.

Features
========
- Implicit multiplication:
    2n         -> 2*n
    n 2        -> n*2
    2(a+b)     -> 2*(a+b)
    (a+b)2     -> (a+b)*2
    m n        -> m*n
    πr         -> π*r
    2πr        -> 2*π*r
    π(r+1)     -> π*(r+1)
    2π(r+1)    -> 2*π*(r+1)

- Unicode/operator normalization before tokenization:
    °C, ℃      -> degC
    ·, ⋅, ×    -> *
    −          -> -
    ÷          -> /

Important
=========
This module intentionally does *not* rewrite a general identifier followed by
``(`` into multiplication, because ``name(...)`` is normally a function call in
Python.  The only special case is ``π(``, which is treated as multiplication.
"""

from __future__ import annotations

import re

from ideas import import_hook
import token_utils


# Characters/operators that should be normalized before tokenization.
REPLACEMENTS = {
    "/°C": "/C",
    "/℃": "/C",
    "°C": "degC",
    "℃": "degC",
    "·": "*",
    "⋅": "*",
    "×": "*",
    "−": "-",
    "÷": "/",
}


def normalize_source(source: str) -> str:
    """Normalize selected Unicode characters before tokenization.

    In particular, this splits ``π`` into its own token only when it is glued to
    neighboring identifier/number text. This avoids turning a standalone line
    like ``π = pi`` into an indented line.
    """
    for old, new in REPLACEMENTS.items():
        source = source.replace(old, new)

    # Split π out only when it is attached to surrounding text.
    # Examples:
    #   2πr      -> 2 π r
    #   rπ2      -> r π 2
    #   πr       -> π r
    #   2π(r+1)  -> 2 π (r+1)
    # but:
    #   π = pi   stays unchanged
    source = re.sub(r"(?<=[\w\)])π(?=[\w\(])", " π ", source)
    source = re.sub(r"(?<=[\w\)])π", " π", source)
    source = re.sub(r"π(?=[\w\(])", "π ", source)

    return source


def transform_source(source: str, **_kwargs) -> str:
    """Insert ``*`` where multiplication is implicit in algebraic notation."""
    source = normalize_source(source)

    tokens = token_utils.tokenize(source)
    if not tokens:
        return source

    prev_token = tokens[0]
    new_tokens = [prev_token]

    for token in tokens[1:]:
        if (
            (
                prev_token.is_number()
                and (token.is_identifier() or token.is_number() or token == "(")
            )
            or (
                prev_token.is_identifier()
                and (token.is_identifier() or token.is_number())
            )
            or (prev_token == ")" and (token.is_identifier() or token.is_number()))
            or (prev_token == "π" and token == "(")
        ):
            new_tokens.append("*")

        new_tokens.append(token)
        prev_token = token

    return token_utils.untokenize(new_tokens)


def add_hook(**_kwargs):
    """Create and install the import hook."""
    hook = import_hook.create_hook(
        transform_source=transform_source,
        hook_name=__name__,
    )
    return hook
