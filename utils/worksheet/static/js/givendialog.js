// The dialog in which a sheet is given values of another sheet (given.js):
// which of that sheet's variables it takes.

import { el } from "./util.js";
import { show } from "./dialogs.js";
import { bundleNode, tex } from "./mathview.js";

/** When a sheet was saved, as it is said here: "11 Oct 2026, 14:03" ("" for what is no time). */
function when(saved) {
  const date = new Date(saved || NaN);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

/** A revision as it is said here — or, of a sheet that has none, when it was saved. */
function revisionSaid({ revision, saved }) {
  return revision === null || revision === undefined || revision === "" ? when(saved) || "an earlier save" : `rev. ${revision}`;
}

/**
 * Choose which values of another sheet this sheet takes.
 * @param {object} opts
 *   source   what the other sheet delivers, as the server says it
 *            (/api/values): {name, revision, saved, values} — `values`
 *            being null for a sheet that keeps none, or the record
 *            {revision, saved, values: [{name, tex, form, shown} | {name, tex, why}]};
 *            or {name, missing}: why nothing can be had of it
 *   taken    what the region whose values are chosen anew was given
 *            (given.js) — null where a new region is made
 * @returns {Promise<{names: string[]}|"other"|null>}  the names chosen;
 *            "other": another sheet is to be chosen; null: nothing is to change
 */
export function givenDialog({ source, taken = null }) {
  return new Promise((resolve) => {
    const record = source.missing ? null : source.values;
    const offered = record ? record.values : [];
    const had = new Map((taken ? taken.values : []).map((entry) => [entry.name, entry]));
    const boxes = new Map();          // name → its checkbox
    const rows = [];

    const title = "Values from another sheet";
    const other = el("button", { class: "btn", type: "button", text: "Other sheet…" });
    const ok = el("button", { class: "btn primary", type: "button" });
    const cancel = el("button", { class: "btn", type: "button", text: "Cancel" });
    const all = el("button", { class: "btn", type: "button", text: "All" });
    const none = el("button", { class: "btn", type: "button", text: "None" });
    const find = offered.length > 8
      ? el("input", { type: "text", placeholder: "Find a name", "aria-label": "Find a name", spellcheck: "false" }) : null;
    const list = el("div", { class: "values" });

    // The other sheet: its name, its revision, when it was saved.
    const facts = [];
    if (!source.missing) {
      if (source.revision !== null && source.revision !== undefined && source.revision !== "") facts.push(`rev. ${source.revision}`);
      if (when(source.saved)) facts.push(`saved ${when(source.saved)}`);
    }
    const head = el("div", { class: "source" }, el("b", { text: source.name }),
                    el("span", { class: "note", text: facts.join(" · ") }), other);

    // What is to be known before choosing.
    const notes = [];
    if (source.missing) notes.push(source.missing);
    else if (!record) {
      notes.push("This sheet keeps no values yet. Open it and save it once it is calculated: " +
                 "what its variables are is then kept with it, for other sheets to take.");
    } else {
      if (record.revision !== source.revision || record.saved !== source.saved) {
        notes.push(`These are its values as of ${revisionSaid(record)}: it has been saved since without being calculated.`);
      }
      if (taken && (taken.revision !== record.revision || taken.saved !== record.saved)) {
        notes.push(`This sheet has them as of ${revisionSaid(taken)}.`);
      }
    }

    const chosen = () => offered.filter((entry) => boxes.get(entry.name)?.checked).map((entry) => entry.name);
    const count = () => {
      const n = chosen().length;
      ok.textContent = n === 1 ? "Take 1 value" : n ? `Take ${n} values` : "Take values";
      ok.disabled = n === 0;
    };

    for (const entry of offered) {
      const before = had.get(entry.name);
      const there = typeof entry.form === "string";
      const box = there ? el("input", { type: "checkbox", "aria-label": entry.name }) : null;
      if (box) {
        box.checked = Boolean(before);
        box.addEventListener("change", count);
        boxes.set(entry.name, box);
      }
      const row = el("label", { class: "value" + (there ? "" : " none"), dataset: { name: entry.name } },
                     box || el("span"),
                     el("span", { class: "name" }, entry.tex ? tex(entry.tex) : el("code", { text: entry.name })),
                     there ? el("span", { class: "is" }, bundleNode(entry.shown, null, { inline: true }))
                       : el("span", { class: "is why", text: `${entry.why}: not handed on` }),
                     // What this sheet has of it is another value: say which.
                     before && before.form !== entry.form
                       ? el("span", { class: "was" }, "here: ", bundleNode(before.shown, null, { inline: true })) : null);
      rows.push({ row, name: entry.name, box });
      list.append(row);
    }
    // What this sheet has, and the other one no longer delivers.
    for (const before of record ? had.values() : []) {
      if (offered.some((entry) => entry.name === before.name)) continue;
      list.append(el("div", { class: "value none", dataset: { name: before.name } }, el("span"),
                     el("span", { class: "name" }, before.tex ? tex(before.tex) : el("code", { text: before.name })),
                     el("span", { class: "is why", text: "not there any more" }),
                     el("span", { class: "was" }, "here: ", bundleNode(before.shown, null, { inline: true }))));
    }
    if (!list.childNodes.length) list.hidden = true;

    const dialog = el("div", { class: "dialog given", role: "dialog", "aria-label": title },
                      el("h3", { text: title }),
                      el("div", { class: "body" }, head, notes.map((text) => el("p", { class: "note", text })), find, list),
                      el("div", { class: "foot" }, offered.length > 1 ? [all, none] : null, el("span", { class: "grow" }), cancel, ok));
    const close = show(dialog, () => resolve(null));
    const finish = (made) => { close(); resolve(made); };

    const shownRows = () => rows.filter((entry) => !entry.row.hidden && entry.box);
    all.addEventListener("click", () => { for (const entry of shownRows()) entry.box.checked = true; count(); });
    none.addEventListener("click", () => { for (const entry of shownRows()) entry.box.checked = false; count(); });
    if (find) {
      find.addEventListener("input", () => {
        const wanted = find.value.trim().toLowerCase();
        for (const entry of rows) entry.row.hidden = !entry.name.toLowerCase().includes(wanted);
      });
    }
    other.addEventListener("click", () => finish("other"));
    cancel.addEventListener("click", () => finish(null));
    ok.addEventListener("click", () => finish({ names: chosen() }));
    dialog.addEventListener("keydown", (event) => {
      // Enter takes what is chosen — but on a button it presses that button.
      if (event.key !== "Enter" || event.target instanceof HTMLButtonElement) return;
      event.preventDefault();
      if (!ok.disabled) ok.click();
    });
    count();
    setTimeout(() => (find || list.querySelector("input") || other).focus(), 0);
  });
}
