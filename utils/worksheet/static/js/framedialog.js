// The dialog for a sheet's header and footer (File ▸ Header and footer…).
//
// What is set here is tried out on the spot: the header and the footer
// are drawn above and below the fields as the pages will have them.

import { el, toast } from "./util.js";
import { show } from "./dialogs.js";
import { band, BUILT_IN, DATES, dateText, FIELDS, LOGO, normal, sameFrame } from "./pageframe.js";
import { KINDS_SAID, megabytes, pictureFromFile } from "./pictures.js";

const NONE = "", OWN = "*";             // in the list of templates: no header or footer / one that is no template
const SIDES = ["left", "centre", "right"];
const RESERVED = ["None", "(as set below)"];

/** A revision as it is kept: a number is counted on from, anything else stays as typed, nothing starts from the beginning. */
function revisionOf(text) {
  const typed = text.trim().slice(0, 20);
  if (!typed) return undefined;
  return /^\d{1,9}$/.test(typed) ? Number(typed) : typed;
}

/**
 * Ask for the header and the footer of the sheet.
 * @param {object} given
 *   frame      what the sheet has now (a normal frame, or null)
 *   about      {title, author, revision}: what the sheet says of itself
 *   sample     what the fields say now (pageframe.js: `about`, with `pages`), for the preview
 *   templates  the user's own templates, [{name, frame}]
 *   usual      the name of the template a new sheet gets ("": none)
 *   keep(name, frame), drop(name)   keep a template of the user's own, or let go of one:
 *              each resolves to the user's templates as they are then — or to null
 * @returns {Promise<null | {frame, about, usual}>}  null: nothing is to change
 */
export function frameDialog(given) {
  return new Promise((resolve) => {
    const start = given.frame;
    const work = start
      ? { header: [...start.header], footer: [...start.footer], rule: { ...start.rule }, dates: start.dates, logo: start.logo }
      : { header: ["", "", ""], footer: ["", "", ""], rule: { header: true, footer: true }, dates: "iso", logo: null };
    let own = given.templates;
    let target = null;                  // the cell that had the keyboard last

    const frame = () => normal(work);
    const templates = () => [...BUILT_IN, ...own];
    const chosen = () => {
      const made = frame();
      if (!made) return NONE;
      const found = templates().find((template) => sameFrame(template.frame, made));
      return found ? found.name : OWN;
    };

    // ---- the parts ------------------------------------------------------------
    const list = el("select", { "aria-label": "Template" });
    const usual = el("select", { "aria-label": "Template of a new sheet" });
    const keepName = el("input", { type: "text", placeholder: "its name", "aria-label": "Name of the template", maxlength: "60" });
    const keepButton = el("button", { class: "btn", type: "button", text: "Save as template…" });
    const dropButton = el("button", { class: "btn", type: "button", text: "Delete" });
    const keepRow = el("div", { class: "row", hidden: true }, el("span", { class: "label", text: "Save as" }), keepName,
                       el("button", { class: "btn primary", type: "button", text: "Save", onclick: () => keep() }),
                       el("button", { class: "btn", type: "button", text: "Not now", onclick: () => { keepRow.hidden = true; } }));
    const shownHead = el("div", { class: "framepreview" });
    const shownFoot = el("div", { class: "framepreview" });
    const cell = (which, k) => {
      const area = el("textarea", { rows: "2", spellcheck: "false", placeholder: SIDES[k],
                                    "aria-label": `${which === "header" ? "Header" : "Footer"}, ${SIDES[k]}` });
      area.addEventListener("input", () => { work[which][k] = area.value; refresh(); });
      area.addEventListener("focus", () => { target = area; });
      return area;
    };
    const areas = { header: SIDES.map((side, k) => cell("header", k)), footer: SIDES.map((side, k) => cell("footer", k)) };
    const ruled = (which, text) => {
      const box = el("input", { type: "checkbox" });
      box.addEventListener("change", () => { work.rule[which] = box.checked; refresh(); });
      return { box, label: el("label", { class: "check" }, box, text) };
    };
    const rules = { header: ruled("header", "a line under it"), footer: ruled("footer", "a line over it") };
    const fields = el("div", { class: "fields" }, FIELDS.map(([name, what]) => el("button", {
      class: "field", type: "button", title: what, text: `{${name}}`, onclick: () => insert(`{${name}}`) })));
    const logoFile = el("input", { type: "file", hidden: true, accept: "image/png,image/jpeg,image/gif,image/webp,image/bmp,image/svg+xml,.svg" });
    const logoName = el("span", { class: "note" });
    const logoHeight = el("input", { type: "number", min: String(LOGO.least), max: String(LOGO.most), step: "1", "aria-label": "Height of the logo in mm" });
    const logoGone = el("button", { class: "btn", type: "button", text: "Remove" });
    const dates = el("select", { "aria-label": "How dates are written" },
                     Object.keys(DATES).map((style) => el("option", { value: style, text: dateText(given.sample.now, style) })));
    const about = Object.fromEntries(["title", "author", "revision"].map((name) => {
      const field = el("input", { type: "text", spellcheck: "false", maxlength: name === "revision" ? "20" : "120",
                                  "aria-label": name[0].toUpperCase() + name.slice(1) });
      field.value = String(given.about[name] ?? "");
      field.addEventListener("input", () => refresh());
      return [name, field];
    }));
    about.title.placeholder = given.sample.name;
    about.revision.placeholder = "0";

    // ---- what they do ----------------------------------------------------------
    function insert(text) {
      const area = target || areas.header[1];
      area.setRangeText(text, area.selectionStart, area.selectionEnd, "end");
      area.dispatchEvent(new Event("input"));
      area.focus();
    }

    /** Put a frame — a template's, say — into the cells; none empties them. */
    function take(made) {
      for (const which of ["header", "footer"]) work[which] = made ? [...made[which]] : ["", "", ""];
      if (made) {
        // (A template without a logo leaves the sheet the logo it has.)
        Object.assign(work, { rule: { ...made.rule }, dates: made.dates, logo: made.logo || work.logo });
      }
      showFields();
    }

    /** The fields as `work` has them. */
    function showFields() {
      for (const which of ["header", "footer"]) {
        areas[which].forEach((area, k) => { area.value = work[which][k]; });
        rules[which].box.checked = work.rule[which];
      }
      refresh();
    }

    function fillLists() {
      const option = (value, text) => el("option", { value, text });
      const named = () => [el("optgroup", { label: "Ready-made" }, BUILT_IN.map((template) => option(template.name, template.name))),
                           own.length ? el("optgroup", { label: "Your own" }, own.map((template) => option(template.name, template.name))) : null];
      list.replaceChildren(option(NONE, "None"), ...named().filter(Boolean), option(OWN, "(as set below)"));
      const wanted = usual.value || given.usual;
      usual.replaceChildren(option(NONE, "None"), ...named().filter(Boolean));
      usual.value = templates().some((template) => template.name === wanted) ? wanted : NONE;
    }

    function refresh() {
      const made = frame();
      const name = chosen();
      list.value = name;
      list.querySelector(`option[value="${OWN}"]`).hidden = name !== OWN;
      dropButton.disabled = !own.some((template) => template.name === name);
      keepButton.disabled = !made;
      logoName.textContent = work.logo ? (work.logo.name || "a picture") : "none: where {logo} stands, nothing is shown";
      logoGone.disabled = !work.logo;
      logoHeight.disabled = !work.logo;
      if (document.activeElement !== logoHeight) logoHeight.value = String(work.logo ? work.logo.height : LOGO.usual);
      dates.value = work.dates;
      const sample = { ...given.sample, title: about.title.value.trim() || given.sample.name, author: about.author.value.trim(),
                       revision: revisionOf(about.revision.value) ?? 0, page: 1, pages: given.sample.pages || 1,
                       noLogo: "[logo]" };
      for (const [box, which] of [[shownHead, "header"], [shownFoot, "footer"]]) {
        const drawn = made && band(made, which, sample);
        box.replaceChildren(drawn || el("div", { class: "nothing", text: which === "header" ? "no header" : "no footer" }));
      }
    }

    async function keep() {
      const name = keepName.value.trim();
      const made = frame();
      if (!name || !made) { keepName.focus(); return; }
      if (RESERVED.includes(name) || BUILT_IN.some((template) => template.name === name)) {
        toast(`"${name}" is a ready-made template: give yours another name.`, "error");
        return;
      }
      const kept = await given.keep(name, made);
      if (!kept) return;
      own = kept;
      keepRow.hidden = true;
      fillLists();
      refresh();
    }

    async function drop() {
      const kept = await given.drop(list.value);
      if (!kept) return;
      own = kept;
      fillLists();
      refresh();
    }

    list.addEventListener("change", () => {
      const found = templates().find((template) => template.name === list.value);
      take(found ? found.frame : null);
    });
    keepButton.addEventListener("click", () => {
      keepRow.hidden = false;
      keepName.value = own.some((template) => template.name === list.value) ? list.value : "";
      keepName.focus();
    });
    keepName.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); keep(); } });
    dropButton.addEventListener("click", drop);
    dates.addEventListener("change", () => { work.dates = dates.value; refresh(); });
    logoHeight.addEventListener("input", () => {
      const height = Number(logoHeight.value);
      if (work.logo && height >= LOGO.least && height <= LOGO.most) { work.logo = { ...work.logo, height }; refresh(); }
    });
    logoGone.addEventListener("click", () => { work.logo = null; refresh(); });
    logoFile.addEventListener("change", async () => {
      const file = logoFile.files[0];
      logoFile.value = "";                              // the same file may be chosen again
      if (!file) return;
      if (file.size > LOGO.bytes) {
        toast(`"${file.name}" is too large for a logo (${megabytes(file.size)} MB): one can be up to ${megabytes(LOGO.bytes)} MB.`, "error");
        return;
      }
      let picture;
      try {
        picture = await pictureFromFile(file);
      } catch (error) {
        toast(error.message || `A logo is a picture: ${KINDS_SAID}.`, "error");
        return;
      }
      work.logo = normal({ header: ["{logo}", "", ""], logo: { data: picture.bundle, name: file.name,
                                                            height: work.logo ? work.logo.height : LOGO.usual } }).logo;
      // A logo is there to be seen: where no cell names it yet, the header's left one does.
      if (![...work.header, ...work.footer].some((text) => text.includes("{logo}"))) {
        work.header[0] = work.header[0].trim() ? `{logo} ${work.header[0]}` : "{logo}";
      }
      showFields();
    });

    // ---- the dialog --------------------------------------------------------------
    const ok = el("button", { class: "btn primary", type: "button", text: "OK" });
    const cancel = el("button", { class: "btn", type: "button", text: "Cancel" });
    const row = (label, ...parts) => el("div", { class: "row" }, el("span", { class: "label", text: label }), ...parts);
    const note = (text) => el("span", { class: "note", text });
    const dialog = el("div", { class: "dialog wide frame", role: "dialog", "aria-label": "Header and footer" },
      el("h3", { text: "Header and footer" }),
      el("div", { class: "body" },
         row("Template", list, keepButton, dropButton),
         keepRow,
         shownHead,
         row("Header", el("div", { class: "three" }, areas.header), rules.header.label),
         row("Footer", el("div", { class: "three" }, areas.footer), rules.footer.label),
         shownFoot,
         row("Fields", fields),
         row("Logo", el("button", { class: "btn", type: "button", text: "Choose a picture…", onclick: () => logoFile.click() }),
             logoGone, el("label", { class: "check" }, "height", logoHeight, "mm"), logoName, logoFile),
         row("Dates", dates, note("A cell is written as text is — **bold**, *italic*, $x^2$ — and each of its lines is a line.")),
         el("h4", { text: "This sheet" }),
         row("Title", about.title, el("span", { class: "label short", text: "Author" }), about.author,
             el("span", { class: "label short", text: "Revision" }), about.revision),
         el("p", { class: "note", text: "The revision counts the saves that changed the sheet. A number typed here is counted on from; " +
                                        "anything else — B, 1.2 — stays as it is typed." }),
         el("h4", { text: "On this computer" }),
         row("New sheets", usual, note("the template a new sheet starts with"))),
      el("div", { class: "foot" }, cancel, ok));
    const close = show(dialog, () => resolve(null));
    cancel.addEventListener("click", () => { close(); resolve(null); });
    ok.addEventListener("click", () => {
      const made = frame();
      close();
      resolve({
        frame: made,
        about: { title: about.title.value.trim(), author: about.author.value.trim(), revision: revisionOf(about.revision.value) },
        usual: usual.value,
      });
    });
    fillLists();
    showFields();
    setTimeout(() => list.focus(), 0);
  });
}
