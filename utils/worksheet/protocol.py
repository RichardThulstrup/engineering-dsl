"""Names shared by the server process and the kernel-side engine.

Kept apart from both so that the server can describe the protocol
without importing the toolkit (sympy, forallpeople …) into itself.
"""

# MIME type of the ``display_data`` messages that carry engine replies.
MIME = "application/vnd.edsl.worksheet+json"

# The one-line setup every Engineering DSL notebook starts with.
PREAMBLE = "from utils.Engineer import *"

# Run once in a fresh kernel: sets up the toolkit and the engine.
BOOTSTRAP = '__import__("utils.worksheet.engine", fromlist=["install"]).install()\nNone'


def request_code(encoded: str) -> str:
    """Kernel code that hands one base64-encoded request to the engine.

    ``%%python`` keeps the DSL transformer off the line, and base64 keeps
    the payload free of anything it or IPython might interpret.
    """
    return f'%%python\n__edsl_ws__.handle("{encoded}")'
