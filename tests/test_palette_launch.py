"""When importing the toolkit starts the symbol palette, and when it does not.

Every case imports ``utils.Engineer`` in a process of its own, with the
launch recorded instead of carried out: these tests start nothing.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAND_IN = sys.executable       # a file that is there; it is recorded, never run

# What the child process runs.  Only a start of the palette is recorded
# (the program the environment names, or one called "…palette…");
# anything else the import may start runs as usual.
CHILD = '''
import json, os, subprocess, sys

started, really = [], subprocess.Popen


class Recorded:
    def poll(self):
        return None


def popen(args, *more, **named):
    program = str(args[0] if isinstance(args, (list, tuple)) else args)
    if "palette" in os.path.basename(program).lower() or program == os.environ.get("SYMBOL_PALETTE_EXE"):
        started.append(program)
        return Recorded()
    return really(args, *more, **named)


subprocess.Popen = popen
import utils.Engineer as engineer

if "call" in sys.argv:
    engineer.launch_palette()
counts = {}
for value in sys.argv[sys.argv.index("values") + 1:] if "values" in sys.argv else ():
    os.environ["ENGINEER_HEADLESS"] = value
    counts[value] = engineer._headless()
print("REPORT", json.dumps({"started": len(started), "counts": counts}))
'''


def imported(*arguments, **settings):
    """Import the toolkit with ``settings`` in the environment.

    Returns the child's report and what the toolkit said on stderr.
    """
    env = {name: value for name, value in os.environ.items()
           if name not in ("SYMBOL_PALETTE_EXE", "ENGINEER_HEADLESS")}
    env.update(settings, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-c", CHILD, *arguments], cwd=str(ROOT), env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert result.returncode == 0, result.stderr
    report = next(line for line in result.stdout.splitlines() if line.startswith("REPORT"))
    return json.loads(report[len("REPORT"):]), [line for line in result.stderr.splitlines()
                                                if "[Engineer]" in line]


def started(*arguments, **settings):
    report, said = imported(*arguments, **settings)
    return report["started"], said


def test_the_palette_the_environment_names_is_started_on_import():
    assert started(SYMBOL_PALETTE_EXE=STAND_IN) == (1, [])


def test_headless_the_import_starts_no_palette_and_says_nothing():
    assert started(SYMBOL_PALETTE_EXE=STAND_IN, ENGINEER_HEADLESS="1") == (0, [])


def test_headless_the_palette_still_starts_when_it_is_called_for():
    assert started("call", SYMBOL_PALETTE_EXE=STAND_IN, ENGINEER_HEADLESS="1") == (1, [])


def test_a_switch_that_says_no_is_not_headless():
    assert started(SYMBOL_PALETTE_EXE=STAND_IN, ENGINEER_HEADLESS="0") == (1, [])


def test_which_values_of_the_switch_count():
    on, off = ["1", "yes", "true", "On"], ["", "0", "false", "No", " off "]
    report, _ = imported("values", *on, *off, ENGINEER_HEADLESS="1")
    assert report["counts"] == dict({value: True for value in on}, **{value: False for value in off})


def test_the_palette_beside_the_toolkit_is_left_alone_too():
    found, _ = started()                        # nothing set, as in a notebook: found by itself
    if not found:
        pytest.skip("no palette binary beside the toolkit (utils/bin)")
    assert started(ENGINEER_HEADLESS="1") == (0, [])
