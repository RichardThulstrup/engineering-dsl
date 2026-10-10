// Printing: KaTeX's long strokes, cut to what the page shows.
//
// KaTeX draws the bar of a root sign (and the shaft of a stretchy arrow
// or brace) 400em long and leaves it to the box around it to hide the
// rest.  A screen and a PDF do exactly that.  On paper the bar has been
// seen running to the left of the root sign instead: at 600 dpi the
// stroke is some 37000 dots long, more than a printer driver that counts
// in 16 bits (up to 32767) can hold, which fits a far end that wraps
// round.  So while a page is being printed every such drawing ends just
// past its box, and no coordinate leaves the page.

const FULL = 400000;        // KaTeX's length in path units (1000 to the em)
const SPARE = 500;          // units a stroke runs on past its box

/**
 * The `d` of one of KaTeX's long drawings, `to` units long instead of 400000.
 * The far end lies near 400000 and comes in by the whole difference; the
 * middle of a brace lies near 200000 and comes in by half as much.  KaTeX
 * also writes the far end as 40000 (in two of its bars) and as 40000040
 * (in its tall root sign): those come in to `to` as well.
 */
export function shortenStroke(d, to) {
  const cut = FULL - to;
  return d.replace(/-?\d+(?:\.\d+)?/g, (number) => {
    const size = Math.abs(number);
    let less;
    if (size === FULL / 10) less = Math.min(size, to);
    else if (size < FULL / 4) return number;
    else less = size < FULL * 3 / 4 ? size - cut / 2 : Math.min(size - cut, to);
    return String(number < 0 ? -less : less);
  });
}

const drawn = new Map();    // svg -> what KaTeX made of it, while a page is printed

/** Before printing: every long drawing as long as its box, and no longer. */
export function fitStrokes(root = document) {
  for (const svg of root.querySelectorAll(`.katex svg[width="${FULL / 1000}em"]`)) {
    const box = svg.parentElement.getBoundingClientRect();
    const height = Number(svg.getAttribute("viewBox").trim().split(/\s+/)[3]);
    if (!box.height || !height) continue;                       // not on the page
    // The drawing is scaled to the height of its box, which gives the
    // path units to the pixel whatever the zoom.
    const to = Math.min(FULL, Math.ceil(box.width * height / box.height) + SPARE);
    const paths = [...svg.querySelectorAll("path")];
    drawn.set(svg, { width: svg.getAttribute("width"), viewBox: svg.getAttribute("viewBox"),
                     paths: paths.map((path) => [path, path.getAttribute("d")]) });
    svg.setAttribute("width", `${to / 1000}em`);
    svg.setAttribute("viewBox", `0 0 ${to} ${height}`);
    for (const path of paths) path.setAttribute("d", shortenStroke(path.getAttribute("d"), to));
  }
}

/** After printing: KaTeX's own drawings again. */
export function restoreStrokes() {
  for (const [svg, was] of drawn) {
    svg.setAttribute("width", was.width);
    svg.setAttribute("viewBox", was.viewBox);
    for (const [path, d] of was.paths) path.setAttribute("d", d);
  }
  drawn.clear();
}
