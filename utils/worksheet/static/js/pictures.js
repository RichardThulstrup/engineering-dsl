// Pictures on the page.
//
// A picture is kept the way a notebook keeps one: attached to a text
// region (a markdown cell) and named in its text, ![alt](attachment:name).
// An attachment is base64 whatever its kind.  A drawing (SVG) is stored
// with a bitmap copy beside it: JupyterLab shows bitmaps only and takes
// the copy; nbconvert, and this page, take the drawing.
//
// A picture is only ever shown through <img>, where a drawing can
// neither run a script nor fetch anything: its markup never enters the
// page.

import { uid } from "./util.js";

const SVG = "image/svg+xml";
const PNG = "image/png";
const KINDS = { png: PNG, jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif",
                webp: "image/webp", bmp: "image/bmp", svg: SVG };
const ENDING = { [PNG]: "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp",
                 "image/bmp": "bmp", [SVG]: "svg" };
// Which form of an attachment is shown: the drawing before its bitmap copy.
const SHOWN = [SVG, PNG, "image/jpeg", "image/gif", "image/webp", "image/bmp"];
const BASE64 = /^[A-Za-z0-9+/]+={0,2}$/;
const LONE = /^!\[[^\]]*\]\(attachment:([^)\s]+)\)$/;
const SVG_SPACE = "http://www.w3.org/2000/svg";
const PX = { px: 1, pt: 96 / 72, pc: 16, mm: 96 / 25.4, cm: 96 / 2.54, in: 96 };

// How much is taken: the bytes of one picture, and the characters of all
// the pictures a sheet is saved with (the server reads up to 128 MB).
export const LIMITS = { picture: 20e6, sheet: 100e6 };
export const KINDS_SAID = "PNG, JPEG, GIF, WebP, BMP or SVG";

// ---- what is stored ----------------------------------------------------------

/** An address for <img> from an attachment, {type: base64} — null if it holds no picture. */
export function pictureUrl(bundle) {
  for (const type of SHOWN) {
    const data = bundle ? bundle[type] : null;
    if (typeof data === "string" && BASE64.test(data)) return `data:${type};base64,${data}`;
  }
  return null;
}

/** How much the pictures of a sheet's regions (as they are saved) come to, in characters. */
export function weight(regions) {
  return regions.flatMap((region) => Object.values(region.attachments || {}))
    .flatMap((bundle) => Object.values(bundle)).reduce((sum, data) => sum + String(data).length, 0);
}

/** The attachment a text names, when the text is that one picture and nothing else. */
export function lonePicture(source) {
  const found = LONE.exec(source.trim());
  return found ? found[1] : null;
}

/** Those of a region's attachments that its text still names. */
export function named(source, attachments) {
  const kept = {};
  for (const [name, bundle] of Object.entries(attachments || {})) {
    if (source.includes(`attachment:${name}`) || source.includes(`attachment:${encodeURIComponent(name)}`)) {
      kept[name] = bundle;
    }
  }
  return kept;
}

/**
 * A picture that came as a data address (in text pasted from another
 * program): {name, bundle} to attach it by — null if it is none this page shows.
 */
export function attachment(address) {
  const m = /^data:(image\/[\w.+-]+);base64,([A-Za-z0-9+/]+={0,2})$/.exec(address);
  return m && ENDING[m[1]] ? { name: `${uid()}.${ENDING[m[1]]}`, bundle: { [m[1]]: m[2] } } : null;
}

/** How a picture is written into a text. */
export function reference(picture) {
  return `![${picture.alt}](attachment:${picture.name})`;
}

// ---- what is offered -----------------------------------------------------------

function kindOf(file) {
  if (Object.hasOwn(ENDING, file.type)) return file.type;
  const ending = /\.([a-z0-9]+)$/i.exec(file.name || "");
  return (ending && KINDS[ending[1].toLowerCase()]) || null;
}

/** Could this text be an SVG drawing — the whole of it, not text that mentions one? */
export function looksLikeSvg(text) {
  const body = text.trim();
  return /^<(svg[\s>]|\?xml\s|!--|!DOCTYPE\s+svg\b)/i.test(body) && /<svg[\s>]/i.test(body)
    && /<\/svg\s*>$/i.test(body);
}

/**
 * An SVG drawing read from text: {text, width, height} with the size it
 * asks for in CSS px — or null when the text is not a drawing.  A drawing
 * cut out of a web page comes without its namespaces; it is given them.
 */
export function readDrawing(text) {
  let body = text.trim();
  const opening = /<svg(?=[\s>])[^>]*>/i.exec(body);
  if (opening && !/\sxmlns\s*=/.test(opening[0])) {
    body = body.replace(/<svg(?=[\s>])/i, `<svg xmlns="${SVG_SPACE}" xmlns:xlink="http://www.w3.org/1999/xlink"`);
  }
  // Read as XML, never shown.  (Text that is not well-formed comes back
  // as a page saying so — which is no <svg> either.)
  const root = new DOMParser().parseFromString(body, SVG).documentElement;
  if (root.localName !== "svg" || root.namespaceURI !== SVG_SPACE) return null;
  const length = (value) => {
    const m = /^\s*(\d*\.?\d+)\s*(px|pt|pc|mm|cm|in)?\s*$/.exec(value || "");
    return m ? Number(m[1]) * PX[m[2] || "px"] : 0;
  };
  let width = length(root.getAttribute("width"));
  let height = length(root.getAttribute("height"));
  if (!(width && height)) {
    // Its box, then — or what a browser takes a drawing without a size to be.
    const box = (root.getAttribute("viewBox") || "").trim().split(/[\s,]+/).map(Number);
    [width, height] = box.length === 4 && box[2] > 0 && box[3] > 0 ? [box[2], box[3]] : [300, 150];
  }
  return { text: body, width, height };
}

/**
 * The pictures a clipboard or a drop carries: null, or {files, svg, ask}.
 * `svg` is a drawing as text — plain text that is one counts too.
 * `ask` says the clipboard holds a drawing that it gives to
 * navigator.clipboard only: Chromium names the kind in a paste, and
 * hands over nothing.
 */
export function carried(data) {
  const types = [...(data.types || [])];
  const files = [...(data.files || [])].filter((file) => kindOf(file));
  let svg = types.includes(SVG) ? data.getData(SVG) : "";
  if (!svg) {
    const plain = data.getData("text/plain");
    if (looksLikeSvg(plain) && readDrawing(plain)) svg = plain;
  }
  const ask = !svg && types.includes(SVG);
  return files.length || svg || ask ? { files, svg: svg || null, ask } : null;
}

/** Does a drag bring files along? */
export function bringsFiles(data) {
  return Boolean(data) && [...(data.types || [])].includes("Files");
}

// ---- making a picture ready ------------------------------------------------------

function loaded(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("unreadable"));
    img.src = url;
  });
}

function base64Of(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

export function megabytes(size) {
  return Math.round(size / 1e5) / 10;
}

/** Words for the picture's alt text: its file's name, without what markdown would trip over. */
function called(name, otherwise) {
  const words = String(name || "").replace(/\.[a-z0-9]+$/i, "").replace(/[\[\]()\s]+/g, " ").trim();
  return words || otherwise;
}

/** A bitmap of a drawing, at the size it asks for (within reason): where
 *  the copy is shown, it is shown pixel for pixel. */
async function bitmapCopy(url, width, height) {
  const scale = Math.min(1, 2400 / width, 2400 / height);
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  canvas.getContext("2d").drawImage(await loaded(url), 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL(PNG).split(",")[1];
}

/** A picture ready to be kept: {name, alt, bundle, width, height}. */
export async function pictureFromSvg(text, alt = "drawing") {
  const drawing = readDrawing(text);
  if (!drawing) throw new Error("That is not an SVG drawing this page can show.");
  const data = await base64Of(new Blob([drawing.text], { type: SVG }));
  const bundle = { [SVG]: data };
  try {
    bundle[PNG] = await bitmapCopy(`data:${SVG};base64,${data}`, drawing.width, drawing.height);
  } catch (error) { /* shown here as the drawing it is, all the same */ }
  return { name: `${uid()}.svg`, alt: called(alt, "drawing"), bundle, width: drawing.width, height: drawing.height };
}

export async function pictureFromFile(file) {
  const type = kindOf(file);
  const label = file.name ? `"${file.name}"` : "That picture";
  if (!type) throw new Error(`${label} is not a picture this page can show (${KINDS_SAID}).`);
  if (file.size > LIMITS.picture) {
    throw new Error(`${label} is too large (${megabytes(file.size)} MB): a picture can be up to ${megabytes(LIMITS.picture)} MB.`);
  }
  if (type === SVG) return pictureFromSvg(await file.text(), called(file.name, "drawing"));
  const data = await base64Of(file);
  let img;
  try {
    img = await loaded(`data:${type};base64,${data}`);
  } catch (error) {
    throw new Error(`${label} cannot be read as a picture.`);
  }
  // (A picture from the clipboard is called "image.png" by the browser.)
  const alt = /^image\.\w+$/.test(file.name || "") ? "picture" : called(file.name, "picture");
  return { name: `${uid()}.${ENDING[type]}`, alt, bundle: { [type]: data },
           width: img.naturalWidth, height: img.naturalHeight };
}

/** The drawing on the clipboard, when the browser lets the page read it; else "". */
async function clipboardDrawing() {
  try {
    for (const item of await navigator.clipboard.read()) {
      if (item.types.includes(SVG)) return await (await item.getType(SVG)).text();
    }
  } catch (error) { /* not allowed, or nothing there */ }
  return "";
}

/**
 * Make what was `carried` ready: {made, problems, asked}.  A drawing is
 * taken before the bitmap an application puts beside it; `asked` is true
 * when the drawing was there but the browser would not hand it over.
 */
export async function collect(offer) {
  const made = [], problems = [];
  let svg = offer.svg, asked = false;
  if (!svg && offer.ask) {
    svg = await clipboardDrawing();
    asked = !svg;
  }
  if (svg) {
    try { made.push(await pictureFromSvg(svg)); } catch (error) { problems.push(error.message); }
  }
  if (!made.length) {
    for (const file of offer.files || []) {
      try { made.push(await pictureFromFile(file)); } catch (error) { problems.push(error.message); }
    }
  }
  return { made, problems, asked };
}
