// A page of the worksheet that works out of sight for another one.
//
// To bring the sheets a sheet builds on up to date, each is opened, given
// what there is to take, calculated and saved: what a page of this
// worksheet does when someone does that by hand.  So that is what does
// it: a second page — in a frame beside the first, where nobody sees it —
// with a kernel of its own (main.js: a page opened with ?worker in its
// address).  It is laid out as any page is, so that what it saves is what
// a page in front would save; it cannot be clicked or typed into, and
// takes no key that is meant for the page in front.

import { el } from "./util.js";

/**
 * Start such a page.  Resolves to its handle {update, stop, close}:
 * update(path) opens the sheet at `path` and brings it up to date,
 * resolving to what came of it (main.js: bringUp); stop() has it leave
 * off what it is at; close() ends it, and its kernel with it.  Rejects
 * where the page does not come to be: the error says so in words.
 */
export async function openWorker() {
  const frame = el("iframe", { class: "worker", src: "/?worker=1", "aria-hidden": "true", inert: true });
  // (A frame is "loaded" whatever came of it: what it has then is looked at.)
  const loaded = new Promise((resolve) => frame.addEventListener("load", resolve, { once: true }));
  document.body.append(frame);
  try {
    await loaded;
    const work = frame.contentWindow.edslWorker;
    if (!work) throw new Error("the page that does it did not start");
    await work.ready;
    return {
      update: (path) => work.update(path),
      stop: () => work.stop(),
      close: () => { work.bye(); frame.remove(); },
    };
  } catch (error) {
    frame.remove();
    throw error;
  }
}
