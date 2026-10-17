// The dialog in which the sheets a sheet builds on are brought up to date
// (chain.js): what is to be done, in which order — and, once that is
// chosen, how it goes.

import { el, toast } from "./util.js";
import { show } from "./dialogs.js";
import { heldText, newsText, revisionText } from "./chain.js";

/**
 * Show what bringing a sheet's values up to date comes to, and have it
 * done when that is chosen.
 * @param {object} opts
 *   plan   what is to be done (chain.js: plan)
 *   cut    the server looked at as many sheets as it does: there may be more
 *   work   does it: work(tell) returns {done, stop} — it calls
 *          tell(path, text, how) for a sheet as it is begun and as it is
 *          done (path null: the sheet at hand; `how`: "work", "done" or
 *          "left"), `done` resolves to a line that says what came of it
 *          all, and stop() has it leave off
 * @returns {Promise<void>}  resolved when the dialog is closed
 */
export function chainDialog({ plan, cut = false, work }) {
  return new Promise((resolve) => {
    const title = "Bring values up to date";
    const states = new Map();           // a sheet's path (null: the sheet at hand) → where its row says how it goes
    const list = el("div", { class: "steps" });
    const row = (path, name, note, news) => {
      const state = el("span", { class: "state" });
      states.set(path, state);
      list.append(el("div", { class: "step", dataset: { sheet: path ?? "" } },
                     el("span", { class: "who" }, el("b", { text: name }), note ? el("span", { class: "note", text: ` · ${note}` }) : null),
                     el("span", { class: "what", text: `takes ${newsText(news)}` }), state));
    };
    for (const step of plan.steps) row(step.path, step.name, revisionText(step.revision), step.news);
    if (plan.here.length) row(null, "This sheet", "left for you to save", plan.here);

    const notes = [];
    if (plan.circle) {
      notes.push(`These sheets take values from each other in a circle: ${plan.circle.join(" → ")}. ` +
                 "None of them can be the first to be brought up to date.");
    }
    for (const one of plan.held) notes.push(heldText(one));
    if (cut) notes.push("There are more sheets than were looked at: bring the ones further up to date from one of them.");

    const todo = states.size > 0;
    const intro = plan.steps.length
      ? "These sheets hold values that have been saved anew since. Each is opened out of sight, given what there is to take, " +
        "calculated, and saved as a new revision — the first first. A sheet in which the new values lead to an error is left as it is."
      : todo ? "There are newer values for this sheet to take."
        : plan.circle || plan.held.length ? "Nothing can be brought up to date as things are."
          : "This sheet, and every sheet it builds on, has what there is to take.";

    const go = el("button", { class: "btn primary", type: "button", text: "Bring up to date" });
    const cancel = el("button", { class: "btn", type: "button", text: todo ? "Cancel" : "Close" });
    const dialog = el("div", { class: "dialog chain", role: "dialog", "aria-label": title },
                      el("h3", { text: title }),
                      el("div", { class: "body" }, el("p", { text: intro }), todo ? list : null,
                         notes.map((text) => el("p", { class: "note", text }))),
                      el("div", { class: "foot" }, cancel, todo ? go : null));
    let job = null, open = true;
    const close = show(dialog, () => { open = false; if (job) job.stop(); resolve(); });
    const finish = () => { open = false; close(); resolve(); };

    go.addEventListener("click", () => {
      go.disabled = true;
      cancel.textContent = "Stop";
      job = work((path, text, how) => {
        const state = states.get(path);
        if (!state) return;
        state.textContent = text;
        state.parentNode.className = `step ${how}`;
      });
      job.done.then((line) => {
        job = null;
        // (Closed meanwhile: what came of it is said where it can be seen.)
        if (!open) { toast(line); return; }
        go.remove();
        cancel.textContent = "Close";
        cancel.disabled = false;
        cancel.classList.add("primary");
        dialog.querySelector(".body").append(el("p", { class: "end", text: line }));
        cancel.focus();
      });
    });
    cancel.addEventListener("click", () => { if (job) { cancel.disabled = true; job.stop(); } else finish(); });
    setTimeout(() => (todo ? go : cancel).focus(), 0);
  });
}
