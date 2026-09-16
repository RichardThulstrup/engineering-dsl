"""JSON-lines syntax checker. Transforms and compiles cells; never runs them."""
import ast
from contextlib import redirect_stdout
from difflib import SequenceMatcher
import io
import json
import os
from pathlib import Path
import sys


def _position(source, offset):
    prefix = source[:max(0, min(len(source), offset))]
    line = prefix.count("\n")
    # VS Code uses UTF-16 columns, unlike Python's Unicode character offsets.
    column = len(prefix.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2
    return {"line": line, "character": column}


def error_range(source, generated, error):
    """Locate a compiler error after width/line-changing rewrites.

    Line alignment first bounds the character diff. Inserted helper calls map
    to the original expression; a transformer error without a location is
    deliberately shown on the cell's first nonempty line.
    """
    original_lines = source.splitlines(keepends=True) or [""]
    generated_lines = generated.splitlines(keepends=True) or [""]
    if not getattr(error, "lineno", None):
        line = next((i for i, text in enumerate(original_lines) if text.strip()), 0)
        start = sum(map(len, original_lines[:line]))
        return {"start": _position(source, start),
                "end": _position(source, start + len(original_lines[line].rstrip()))}
    line = max(0, min(error.lineno - 1, len(generated_lines) - 1))
    original_line = min(line, len(original_lines) - 1)
    for tag, i, j, a, b in SequenceMatcher(
        None, original_lines, generated_lines, autojunk=False
    ).get_opcodes():
        if a <= line < b:
            original_line = min(i + line - a, max(i, j - 1), len(original_lines) - 1)
            break
    old = original_lines[original_line].rstrip("\r\n")
    new = generated_lines[line].rstrip("\r\n")
    col = max(0, min(len(new), (getattr(error, "offset", None) or 1) - 1))
    start_col, end_col = min(col, len(old)), min(col + 1, len(old))
    for tag, i, j, a, b in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if a <= col < b or col == b == len(new):
            if tag == "equal":
                start_col = min(j, i + col - a)
                end_col = min(len(old), start_col + 1)
            else:
                start_col, end_col = i, max(i + 1, j)
            break
    offset = sum(map(len, original_lines[:original_line]))
    return {"start": _position(source, offset + start_col),
            "end": _position(source, offset + min(len(old), end_col))}


def check_notebook(cells, mode="python"):
    from utils import circuit_dsl as dsl
    from IPython.core.inputtransformer2 import TransformerManager

    dsl.validate_syntax_mode(mode)
    dsl._DECLARED_SUBSCRIPT_SYMBOLS.clear()
    # Protection is a runtime policy that cells can change. Static checking
    # cannot know the executed state and does not pretend to validate it.
    dsl.PROTECTED_NAMES.clear()
    ipython = TransformerManager()
    results = []
    for source in cells:
        generated = source
        declared = dict(dsl._DECLARED_SUBSCRIPT_SYMBOLS)
        try:
            first, separator, rest = source.partition("\n")
            header = first.strip()
            if header == "%%python":
                generated = "\n" + rest
            elif header == "%%dsl" or header.startswith("%%dsl "):
                cell_mode = header[5:].strip() or mode
                generated = "\n" + dsl.transform_source(rest, syntax_mode=cell_mode)
            elif header.startswith("%%") or any(
                line.lstrip().startswith(("%", "!", "?")) for line in source.splitlines()
            ):
                # Match runtime._input_transformer: IPython owns these cells.
                generated = ipython.transform_cell(source)
            else:
                generated = dsl.transform_source(source, syntax_mode=mode)
                generated = ipython.transform_cell(generated)
            tree = compile(generated, "<DSL cell>", "exec",
                           flags=ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
            compile(generated, "<DSL cell>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
            results.append({"diagnostics": [], "python": generated})
            # Only a literal, top-level mode selection is predictable without
            # executing code. Function bodies and branches do not change it.
            for statement in tree.body:
                call = statement.value if isinstance(statement, ast.Expr) else None
                if not isinstance(call, ast.Call):
                    continue
                name = call.func.id if isinstance(call.func, ast.Name) else (
                    call.func.attr if isinstance(call.func, ast.Attribute) else "")
                if name == "set_syntax_mode" and len(call.args) == 1:
                    arg = call.args[0]
                    if isinstance(arg, ast.Constant) and arg.value in ("python", "legacy"):
                        mode = arg.value
        except (SyntaxError, ValueError, TypeError, IndexError, OverflowError) as error:
            dsl._DECLARED_SUBSCRIPT_SYMBOLS.clear()
            dsl._DECLARED_SUBSCRIPT_SYMBOLS.update(declared)
            message = getattr(error, "msg", None) or str(error)
            results.append({"diagnostics": [{"message": message,
                            "range": error_range(source, generated, error)}],
                            "python": generated})
    return results


def main():
    # The extension passes an explicitly configured or discovered project root.
    runtime = os.environ.get("ENGINEERING_DSL_ROOT")
    if runtime:
        sys.path.insert(0, str(Path(runtime).resolve()))
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            with redirect_stdout(io.StringIO()):
                result = check_notebook(request["cells"], request.get("mode", "python"))
            response = {"id": request["id"], "cells": result}
        except Exception as error:
            response = {"id": request.get("id"), "error": f"{type(error).__name__}: {error}"}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
