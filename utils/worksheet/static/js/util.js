// Small helpers shared by the worksheet modules.

export const $ = (selector, root = document) => root.querySelector(selector);

/** A zero-width space: gives an otherwise empty last line its height. */
export const ZWSP = String.fromCharCode(0x200b);

/** Create an element: el("div", {class: "x", onclick: f}, child, "text", …). */
export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

export const snap = (value, grid) => Math.round(value / grid) * grid;

export function uid() {
  const bytes = crypto.getRandomValues(new Uint8Array(6));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

export function debounce(fn, ms) {
  let timer = 0;
  const run = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
  run.cancel = () => clearTimeout(timer);
  return run;
}

let toastTimer = 0;

export function toast(message, kind = "") {
  const box = $("#toast");
  box.textContent = message;
  box.className = kind;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, kind === "error" ? 6000 : 2200);
}

/** fetch() for the JSON API; resolves to {ok, status, data}. */
export async function api(method, url, body) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    return { ok: false, status: 0, data: { error: "The worksheet server is not answering." } };
  }
  let data = {};
  try { data = await response.json(); } catch (error) { /* an empty body */ }
  return { ok: response.ok, status: response.status, data };
}
