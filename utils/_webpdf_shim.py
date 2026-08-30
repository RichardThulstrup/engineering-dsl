"""nbconvert launcher for ``--to webpdf`` on Windows.

``NbConvertApp.initialize`` forces asyncio's Selector event-loop policy
on Windows (its ZMQ kernel connections need ``add_reader``, which the
default Proactor loop lacks).  But the webpdf exporter runs Playwright,
which must *spawn Chromium as a subprocess* — and that is exactly what
Selector loops cannot do, so a plain ``python -m nbconvert --to webpdf``
dies with ``NotImplementedError`` on Windows.

The two requirements never coexist in one process in this toolkit:
``hardcopy()`` and ``hardcopy.py`` export webpdf from an already-saved
or already-executed notebook (no kernel involved), in a subprocess of
their own.  So this launcher lets nbconvert initialize normally, then
restores the platform default (Proactor) policy before the export runs.

Do not use this entry point with ``--execute``.
"""

import asyncio
import sys


def main():
    from nbconvert.nbconvertapp import NbConvertApp

    if sys.platform.startswith("win"):
        orig_initialize = NbConvertApp.initialize

        def initialize(self, argv=None):
            orig_initialize(self, argv)
            asyncio.set_event_loop_policy(None)   # back to the OS default

        NbConvertApp.initialize = initialize
    NbConvertApp.launch_instance()


if __name__ == "__main__":
    main()
