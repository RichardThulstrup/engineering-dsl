"""
Backwards-compatibility shim.

The setup module was renamed to ``Engineer``.  Old notebooks using
``from utils.dsl_setup import *`` continue to work — this file just
forwards to the new module.

For new code, import from ``utils.Engineer`` directly:

    from utils.Engineer import *
"""

from .Engineer import *           # noqa: F401, F403
from .Engineer import __all__     # noqa: F401
