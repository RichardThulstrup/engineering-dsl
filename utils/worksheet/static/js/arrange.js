// Arranging a selection: where its regions go when they are aligned or
// spread out.  Geometry only — boxes in, places out — so that the rules
// can be read (and tested) without a page.
//
// A box is {x, y, w, h} in page pixels; a place is {x, y}.  Places come
// back in the order of the boxes.

/** The ways to arrange a selection, and how many regions each needs. */
export const NEEDED = { left: 1, centre: 1, right: 1, top: 2, middle: 2, bottom: 2, across: 3, down: 3 };

const ACROSS = { left: 0, centre: 0.5, right: 1 };
const DOWN = { top: 0, middle: 0.5, bottom: 1 };

/**
 * Align boxes by one side or by their middles.
 *
 * Left, centre and right put them on a vertical line: that of the
 * selection's own outline, or of the page (`pageWidth`) when a single box
 * is aligned.  Top, middle and bottom put them on a horizontal line; boxes
 * that would then lie on top of each other move right, in their order from
 * the left, until `gap` pixels clear of the one before.
 */
export function aligned(boxes, how, { pageWidth = 0, gap = 0 } = {}) {
  if (how in ACROSS) {
    const alone = boxes.length === 1;
    const left = alone ? 0 : Math.min(...boxes.map((b) => b.x));
    const right = alone ? pageWidth : Math.max(...boxes.map((b) => b.x + b.w));
    return boxes.map((b) => ({ x: Math.round(left + (right - left - b.w) * ACROSS[how]), y: b.y }));
  }
  const top = Math.min(...boxes.map((b) => b.y));
  const bottom = Math.max(...boxes.map((b) => b.y + b.h));
  const places = boxes.map((b) => ({ x: b.x, y: Math.round(top + (bottom - top - b.h) * DOWN[how]) }));
  let free = -Infinity;                         // the first x the next box may take
  for (const i of inOrder(boxes, "x")) {
    places[i].x = Math.max(places[i].x, free);
    free = Math.ceil(places[i].x + boxes[i].w + gap);
  }
  return places;
}

/**
 * Spread boxes out with equal space between them, across ("x") or down
 * ("y").  The first and the last stay where they are — unless the boxes
 * need more room than that at `gap` pixels apart, in which case they take it.
 */
export function distributed(boxes, axis, { gap = 0 } = {}) {
  const size = axis === "x" ? "w" : "h";
  const order = inOrder(boxes, axis);
  const first = boxes[order[0]], last = boxes[order[order.length - 1]];
  const room = last[axis] + last[size] - first[axis] - boxes.reduce((sum, b) => sum + b[size], 0);
  const between = Math.max(gap, room / (boxes.length - 1));
  const places = boxes.map((b) => ({ x: b.x, y: b.y }));
  let at = first[axis];
  for (const i of order) {
    places[i][axis] = Math.round(at);
    at += boxes[i][size] + between;
  }
  return places;
}

/** Indices of the boxes from the left ("x") or from the top ("y"). */
function inOrder(boxes, axis) {
  const other = axis === "x" ? "y" : "x";
  return boxes.map((_, i) => i).sort((a, b) => boxes[a][axis] - boxes[b][axis] || boxes[a][other] - boxes[b][other] || a - b);
}
