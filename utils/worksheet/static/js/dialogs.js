// Modal dialogs: confirm, open, save as.  Each returns a promise.

import { $, api, el } from "./util.js";

function show(dialog, onCancel) {
  const overlay = $("#overlay");
  const close = () => {
    overlay.hidden = true;
    overlay.replaceChildren();
    document.removeEventListener("keydown", onKey, true);
    // Back to the sheet: typing should work again without a click.
    const sink = $("#sink");
    if (sink) sink.focus({ preventScroll: true });
  };
  const onKey = (event) => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); onCancel(); }
    else if (event.key === "Tab") {
      // Tab stays in the dialog: behind it lies the sheet, where typing writes.
      const stops = [...dialog.querySelectorAll("button, input")].filter((node) => !node.disabled);
      const at = stops.indexOf(document.activeElement);
      const next = event.shiftKey ? (at <= 0 ? stops.length - 1 : at - 1) : (at + 1) % (stops.length || 1);
      event.preventDefault();
      (stops[next] || dialog).focus();
    }
  };
  dialog.tabIndex = -1;
  overlay.replaceChildren(dialog);
  overlay.hidden = false;
  overlay.onpointerdown = (event) => { if (event.target === overlay) { close(); onCancel(); } };
  document.addEventListener("keydown", onKey, true);
  dialog.focus({ preventScroll: true });          // the keyboard is the dialog's from here on
  return close;
}

/** Ask a question; resolves to the chosen button's value (null if dismissed). */
export function ask(title, text, buttons) {
  return new Promise((resolve) => {
    const foot = el("div", { class: "foot" });
    const dialog = el("div", { class: "dialog", role: "dialog", "aria-label": title },
                      el("h3", { text: title }), el("div", { class: "body" }, el("p", { text })), foot);
    const close = show(dialog, () => resolve(null));
    for (const button of buttons) {
      const node = el("button", { class: "btn" + (button.primary ? " primary" : ""), type: "button",
                                  text: button.label });
      node.addEventListener("click", () => { close(); resolve(button.value); });
      foot.append(node);
      if (button.primary) setTimeout(() => node.focus(), 0);
    }
  });
}

/** Show `content` (a node) under a title, with a Close button. */
export function info(title, content) {
  return new Promise((resolve) => {
    const button = el("button", { class: "btn primary", type: "button", text: "Close" });
    const dialog = el("div", { class: "dialog wide", role: "dialog", "aria-label": title },
                      el("h3", { text: title }), el("div", { class: "body" }, content),
                      el("div", { class: "foot" }, button));
    const close = show(dialog, () => resolve());
    button.addEventListener("click", () => { close(); resolve(); });
    setTimeout(() => button.focus(), 0);
  });
}

function when(seconds) {
  const date = new Date(seconds * 1000);
  const today = new Date().toDateString() === date.toDateString();
  return today ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
}

/**
 * Browse the worksheet folder.
 * @param {object} opts {title, action, dir, name}  — `name` set means "save as":
 *        a file-name field is shown and a new name may be typed.
 * @returns {Promise<string|null>} path relative to the folder, or null
 */
export function browse({ title, action, dir = "", name = null }) {
  return new Promise((resolve) => {
    const crumbs = el("div", { class: "crumbs" });
    const files = el("div", { class: "files" });
    const input = name === null ? null : el("input", { type: "text", value: name, spellcheck: "false",
                                                       "aria-label": "File name" });
    const ok = el("button", { class: "btn primary", type: "button", text: action });
    const cancel = el("button", { class: "btn", type: "button", text: "Cancel" });
    const dialog = el("div", { class: "dialog", role: "dialog", "aria-label": title },
                      el("h3", { text: title }), crumbs,
                      el("div", { class: "body" }, files, input),
                      el("div", { class: "foot" }, cancel, ok));
    const close = show(dialog, () => resolve(null));
    let current = dir, entries = [], marked = -1;

    const finish = (path) => { close(); resolve(path); };
    const join = (file) => (current ? `${current}/${file}` : file);

    /** Highlight row `index` (a folder or a file); a file is thereby chosen. */
    const mark = (index) => {
      marked = index;
      [...files.children].forEach((row, i) => row.classList.toggle("on", i === index));
      files.children[index]?.scrollIntoView({ block: "nearest" });
      const entry = entries[index];
      if (input && entry && entry.type !== "dir") input.value = entry.name;
    };

    async function open(folder) {
      const { ok: fine, data } = await api("GET", `/api/list?dir=${encodeURIComponent(folder)}`);
      if (!fine) { files.replaceChildren(el("div", { class: "none", text: data.error || "Cannot read that folder." })); return; }
      current = data.dir;
      entries = data.entries;
      marked = -1;
      const parts = current ? current.split("/") : [];
      crumbs.replaceChildren(el("a", { text: data.root, onclick: () => open("") }));
      parts.forEach((part, i) => {
        crumbs.append(" / ", el("a", { text: part, onclick: () => open(parts.slice(0, i + 1).join("/")) }));
      });
      const rows = entries.map((entry, index) => {
        const row = el("div", { class: entry.type === "dir" ? "dir" : "" },
                       el("span", { class: "name", text: entry.name }),
                       el("span", { class: "when", text: entry.mtime ? when(entry.mtime) : "" }));
        row.addEventListener("click", () => {
          if (entry.type === "dir") open(join(entry.name)); else mark(index);
        });
        row.addEventListener("dblclick", () => {
          if (entry.type !== "dir") finish(join(entry.name));
        });
        return row;
      });
      files.replaceChildren(...(rows.length ? rows : [el("div", { class: "none", text: "No worksheets here yet." })]));
    }

    const accept = () => {
      const entry = entries[marked];
      if (input) {
        let file = input.value.trim().replace(/[\\/:*?"<>|]/g, "");
        if (!file) { input.focus(); return; }
        if (!file.toLowerCase().endsWith(".ipynb")) file += ".ipynb";
        finish(join(file));
      } else if (entry && entry.type !== "dir") finish(join(entry.name));
    };
    ok.addEventListener("click", accept);
    cancel.addEventListener("click", () => finish(null));
    dialog.addEventListener("keydown", (event) => {
      const key = event.key;
      const onButton = event.target instanceof HTMLButtonElement;      // Enter presses it
      if ((key === "ArrowDown" || key === "ArrowUp") && entries.length) {
        event.preventDefault();
        mark(key === "ArrowDown" ? Math.min(marked + 1, entries.length - 1) : Math.max(marked - 1, 0));
      } else if (key === "Enter" && !onButton) {
        // preventDefault: the dialog closes on this key-down, and the
        // key's own action must not land on whatever gets the focus next.
        event.preventDefault();
        const entry = entries[marked];
        const typing = event.target === input;
        if (entry && entry.type === "dir" && !typing) open(join(entry.name));
        else accept();
      }
    });
    if (input) setTimeout(() => { input.focus(); input.select(); }, 0);
    open(dir);
  });
}
