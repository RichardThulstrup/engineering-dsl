// The frame of a page: a header above, and a footer below, what is on it.
//
// A frame is part of a sheet (settings.frame in its file): three cells
// of text for the header and three for the footer — left, centre, right
// — a logo, and how dates are written.  A cell is read as a line of a
// text region is (**bold**, *italic*, $x^2$), line by line, and may
// hold fields in braces, which say something about the sheet or the
// page: {file}, {saved}, {page}.  A template is a frame with a name.
//
// Header and footer take their room from the page: what is left of its
// height is for the regions.  They are whole grid steps high, worked out
// from the number of lines and the height of the logo — never measured
// — so that a sheet has the same pages on every computer.
//
// A sheet may be someone else's.  `normal()` makes a frame of whatever a
// file holds, and nothing here assigns markup: text goes through the
// reading of text regions (markdown.js), a logo is shown through <img>.

import { clamp, el } from "./util.js";
import { parseInline } from "./mdtree.js";
import { renderInline } from "./markdown.js";
import { pictureUrl } from "./pictures.js";

export const STEP = 16;                 // header and footer are so many of these high (px): the grid's step
const MM = 96 / 25.4;                   // px in a millimetre
const SIDES = ["left", "centre", "right"];
const LINES = 4;                        // of a cell, at most
const LETTERS = 300;
export const LOGO = { least: 4, most: 40, usual: 10, bytes: 2e6 };     // its height in mm; the size of its file

const two = (number) => String(number).padStart(2, "0");
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                "October", "November", "December"];
/** The ways a date is written, by name. */
export const DATES = {
  iso: (y, m, d) => `${y}-${two(m)}-${two(d)}`,
  dmy: (y, m, d) => `${two(d)}-${two(m)}-${y}`,
  dots: (y, m, d) => `${two(d)}.${two(m)}.${y}`,
  slashes: (y, m, d) => `${two(d)}/${two(m)}/${y}`,
  us: (y, m, d) => `${two(m)}/${two(d)}/${y}`,
  words: (y, m, d) => `${d} ${MONTHS[m - 1]} ${y}`,
};

const real = (date) => date instanceof Date && !Number.isNaN(date.getTime());

export function dateText(date, style = "iso") {
  return real(date) ? (DATES[style] || DATES.iso)(date.getFullYear(), date.getMonth() + 1, date.getDate()) : "";
}

export function timeText(date) {
  return real(date) ? `${two(date.getHours())}:${two(date.getMinutes())}` : "";
}

/** The fields, and what each says — in the order they are offered. */
export const FIELDS = [
  ["file", "the sheet's file name"],
  ["name", "the file name without .ipynb"],
  ["folder", "the folder the file is in"],
  ["title", "the sheet's title — its name, until it is given one"],
  ["author", "who wrote the sheet"],
  ["revision", "counts the saves that changed the sheet"],
  ["saved", "the date the sheet was last saved"],
  ["saved-time", "the time of day it was last saved"],
  ["date", "today's date: the day of printing"],
  ["time", "the time of day of printing"],
  ["page", "the number of the page"],
  ["pages", "the number of pages"],
  ["logo", "the logo"],
];
const FIELD = /\{([a-z]+(?:-[a-z]+)?)\}/g;

/**
 * What a field says — a string, the picture of the logo, or null for a
 * name that is no field.  `about` is what is known of the sheet and the
 * page: {file, name, folder, title, author, revision, saved, now, page,
 * pages}, the two times as Dates; `noLogo` is said where there is no logo.
 */
function said(name, frame, about) {
  switch (name) {
    case "file": case "name": case "folder": case "title": case "author": case "revision": case "page": case "pages":
      return String(about[name] ?? "");
    case "saved": return dateText(about.saved, frame.dates);
    case "saved-time": return timeText(about.saved);
    case "date": return dateText(about.now, frame.dates);
    case "time": return timeText(about.now);
    case "logo": return frame.logo ? { t: "img", src: "attachment:logo", alt: "logo", raw: "" } : String(about.noLogo ?? "");
    default: return null;
  }
}

/** The nodes of a line of text, with every field in them replaced by what it says. */
function filled(nodes, say) {
  const out = [];
  for (const node of nodes) {
    if (node.t === "text") {
      let from = 0;
      for (const found of node.v.matchAll(FIELD)) {
        const made = say(found[1]);
        if (made === null) continue;                    // no field: it stays as it was typed
        if (found.index > from) out.push({ t: "text", v: node.v.slice(from, found.index) });
        if (made !== "") out.push(typeof made === "string" ? { t: "text", v: made } : made);
        from = found.index + found[0].length;
      }
      if (from < node.v.length) out.push({ t: "text", v: node.v.slice(from) });
    } else if (Array.isArray(node.c)) out.push({ ...node, c: filled(node.c, say) });
    else out.push(node);
  }
  return out;
}

const written = (text) => text.trim() !== "";

/** The logo a file holds, as {data: {type: base64}, height, name} — null if it is no picture. */
function logoOf(logo) {
  if (!logo || typeof logo !== "object") return null;
  const address = /^data:([^;]+);base64,(.+)$/.exec(pictureUrl(logo.data && typeof logo.data === "object" ? logo.data : null) || "");
  if (!address || address[2].length > LOGO.bytes * 1.4) return null;
  return { data: { [address[1]]: address[2] }, name: typeof logo.name === "string" ? logo.name.slice(0, 80) : "",
           height: clamp(Number(logo.height) || LOGO.usual, LOGO.least, LOGO.most) };
}

/**
 * A frame as this page understands it, made of whatever a file (or a
 * template) holds: {header, footer, rule, dates, logo} — or null when
 * it has neither header nor footer.
 */
export function normal(frame) {
  if (!frame || typeof frame !== "object") return null;
  const cells = (list) => SIDES.map((side, k) => (Array.isArray(list) && typeof list[k] === "string"
    ? list[k].replace(/\r\n?/g, "\n").split("\n").slice(0, LINES).join("\n").slice(0, LETTERS) : ""));
  const rule = frame.rule && typeof frame.rule === "object" ? frame.rule : {};
  const made = {
    header: cells(frame.header), footer: cells(frame.footer),
    rule: { header: rule.header === true, footer: rule.footer === true },
    dates: Object.hasOwn(DATES, frame.dates) ? frame.dates : "iso",
    logo: logoOf(frame.logo),
  };
  return made.header.some(written) || made.footer.some(written) ? made : null;
}

/** Are two frames the same header and footer? */
export function sameFrame(a, b) {
  return JSON.stringify(normal(a)) === JSON.stringify(normal(b));
}

/** How high a (normal) frame's header and footer are, in px: {head, foot} — 0 for one that is empty. */
export function heights(frame) {
  if (!frame) return { head: 0, foot: 0 };
  const logoRows = frame.logo ? Math.ceil(frame.logo.height * MM / STEP) : 1;
  const rows = (cells) => Math.max(0, ...cells.filter(written).map((text) => text.split("\n")
    .reduce((sum, line) => sum + (line.includes("{logo}") ? logoRows : 1), 0)));
  // (One step more: the line between it and the page's regions, and the room around that line.)
  const band = (cells) => (rows(cells) ? (rows(cells) + 1) * STEP : 0);
  return { head: band(frame.header), foot: band(frame.footer) };
}

/**
 * The header ("header") or the footer ("footer") of a page, drawn — null
 * when the frame has none.  `about` as for the fields.
 */
export function band(frame, which, about) {
  const height = heights(frame)[which === "header" ? "head" : "foot"];
  if (!height) return null;
  const pictures = frame.logo ? { logo: frame.logo.data } : null;
  const say = (name) => said(name, frame, about);
  const cells = el("div", { class: "cells" }, frame[which].map((text, k) => el("div", { class: `cell ${SIDES[k]}` },
    text.split("\n").map((line) => renderInline(filled(parseInline(line), say), el("div", { class: "words" }), pictures)))));
  const rule = el("div", { class: frame.rule[which] ? "rule drawn" : "rule" });
  const logo = frame.logo ? ` --logo: ${Math.round(frame.logo.height * MM * 100) / 100}px;` : "";
  return el("div", { class: `pageframe ${which}`, style: `height: ${height}px;${logo}`,
                     title: `${which === "header" ? "Header" : "Footer"} — double-click to change it` },
            ...(which === "header" ? [cells, rule] : [rule, cells]));
}

/**
 * Where things are on paper when every page has a header `head` px and a
 * footer `foot` px high.  Regions are placed on the sheet as one long
 * strip, y from its top, which a page takes `body` px of; on paper (and
 * on screen) header and footer stand between the pages' parts of it.
 */
export function paged(head, foot, height) {
  const body = height - head - foot;
  if (!(head + foot)) return { head: 0, foot: 0, body, framed: false, toPaper: (y) => y, toSheet: (at) => at };
  return {
    head, foot, body, framed: true,
    toPaper: (y) => y + Math.max(0, Math.floor(y / body)) * (head + foot) + head,
    // (A place in a header or a footer is the nearest place of that page that regions can have.)
    toSheet: (at) => {
      const page = Math.max(0, Math.floor(at / height));
      return page * body + clamp(at - page * height - head, 0, body - 1);
    },
  };
}

/** Templates that are always there. */
export const BUILT_IN = [
  { name: "Page numbers",
    frame: { header: ["", "", ""], footer: ["", "{page} / {pages}", ""], rule: { header: false, footer: false } } },
  { name: "File and date",
    frame: { header: ["{file}", "", "{saved}"], footer: ["", "Page {page} of {pages}", ""], rule: { header: true, footer: true } } },
  { name: "Title block",
    frame: { header: ["{logo}", "**{title}**\n{file}", "Rev. {revision}\n{saved}"],
             footer: ["{author}", "", "Page {page} of {pages}"], rule: { header: true, footer: true } } },
  { name: "Technical note",
    frame: { header: ["{logo}", "**Technical note**\n{title}", "Rev. {revision}\n{saved}"],
             footer: ["{author}", "{file}", "Page {page} of {pages}"], rule: { header: true, footer: true } } },
  { name: "Power requirements",
    frame: { header: ["{logo}", "**Power requirements**\n{title}", "Rev. {revision}\n{saved}"],
             footer: ["{author}", "{file}", "Page {page} of {pages}"], rule: { header: true, footer: true } } },
].map(({ name, frame }) => ({ name, frame: normal(frame) }));
