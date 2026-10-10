"""Draw the worksheet's icon file from its two drawings.

    python -m utils.worksheet.make_icon

``static/icon.ico`` — what the page, the app window and a desktop
shortcut show — holds ``static/icon.svg`` at 40 px and more, and the
heavier ``static/icon-small.svg`` at 16 to 32 px, where thin strokes
would blur.  The drawings are rendered by Chromium through Playwright
(``pip install playwright``, then ``playwright install chromium``);
nothing but this script needs either.
"""
from __future__ import annotations

import struct
from pathlib import Path

__all__ = ["SIZES", "SMALL", "TARGET", "pack", "main"]

STATIC = Path(__file__).with_name("static")
TARGET = STATIC / "icon.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SMALL = 32                      # up to this size the heavier drawing is used


def pack(pictures: dict[int, bytes]) -> bytes:
    """An .ico file of PNG pictures, given by their size in pixels.

    The file is a short directory followed by the pictures themselves
    (Windows has read PNG pictures in icons since Vista).
    """
    directory, body = b"", b""
    offset = 6 + 16 * len(pictures)
    for size in sorted(pictures):
        png = pictures[size]
        directory += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
        body += png
        offset += len(png)
    return struct.pack("<HHH", 0, 1, len(pictures)) + directory + body


def render(size: int, browser) -> bytes:
    svg = (STATIC / ("icon-small.svg" if size <= SMALL else "icon.svg")).read_text(encoding="utf-8")
    page = browser.new_page(viewport={"width": size, "height": size}, device_scale_factor=1)
    try:
        page.set_content("<style>html, body { margin: 0; background: transparent }"
                         f" svg {{ display: block; width: {size}px; height: {size}px }}</style>" + svg)
        return page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})
    finally:
        page.close()


def _browser(playwright):
    """A Chromium to draw with: Playwright's own, or else an installed Chrome or Edge."""
    for options in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
        try:
            return playwright.chromium.launch(**options)
        except Exception:
            continue
    raise SystemExit("no Chromium to draw with: run  playwright install chromium")


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = _browser(playwright)
        try:
            pictures = {size: render(size, browser) for size in SIZES}
        finally:
            browser.close()
    TARGET.write_bytes(pack(pictures))
    print(f"{TARGET}: {TARGET.stat().st_size} bytes, {len(SIZES)} sizes from {SIZES[0]} to {SIZES[-1]} px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
