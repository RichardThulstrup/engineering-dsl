"""Build TextMate injection rules from the shared JupyterLab vocabulary.

Run the JupyterLab generate_rules.py first after editing Engineer_Style.py.
No Python grammar is vendored: VS Code supplies source.python.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
rules_file = ROOT.parent / "jupyterlab-edsl-highlight/src/dslRules.js"
text = rules_file.read_text(encoding="utf-8")
rules = json.loads(text.split("export const DSL_RULES = ", 1)[1].rstrip(";\n"))
scopes = {
    "edsl-plain": "variable.other.engineering-dsl",
    "edsl-op": "keyword.operator.engineering-dsl",
    "edsl-opword": "keyword.operator.word.engineering-dsl",
    "edsl-constkw": "constant.language.engineering-dsl",
    "edsl-decl": "keyword.declaration.engineering-dsl",
    "edsl-number": "constant.numeric.engineering-dsl",
    "edsl-subsup": "constant.numeric.index.engineering-dsl",
    "edsl-string": "string.quoted.engineering-dsl",
    "edsl-strescape": "constant.character.escape.engineering-dsl",
    "edsl-physical": "constant.other.engineering-dsl",
    "edsl-unit": "support.constant.unit.engineering-dsl",
    "edsl-currency": "support.constant.unit.engineering-dsl",
    "edsl-greek": "variable.other.engineering-dsl",
    "edsl-helper": "support.function.engineering-dsl",
    "edsl-keyword": "keyword.control.engineering-dsl",
}
patterns = []
for rule in rules:
    pattern = {"match": rule["pattern"]}
    if "groups" in rule:
        pattern["captures"] = {str(i): {"name": scopes[scope]}
                               for i, scope in enumerate(rule["groups"], 1) if scope}
    else:
        pattern["name"] = scopes[rule["cls"]]
    patterns.append(pattern)
grammar = {"scopeName": "source.engineering-dsl", "patterns": [{"include": "source.python"}]}
injection = {"scopeName": "engineering-dsl.injection",
             "injectionSelector": "L:source.engineering-dsl -comment -string",
             "patterns": patterns}
(ROOT / "syntaxes").mkdir(exist_ok=True)
for name, content in [("engineering-dsl", grammar), ("injection", injection)]:
    (ROOT / f"syntaxes/{name}.tmLanguage.json").write_text(
        json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Generated {len(patterns)} DSL highlighting rules")
