// What a sheet is given by another sheet.
//
// A sheet at a revision delivers the values its variables had when it
// was saved (utils/worksheet/values.py writes a value as text).  A math
// region of another sheet that takes some of them says where they are
// from:
//
//     given = { sheet, name, revision, saved, values: [{name, tex, form, shown}] }
//
// — `sheet` being the other sheet's file as it is reached from this
// sheet's folder, `revision` and `saved` the revision the values are of,
// `form` a value as text, `tex` its name typeset and `shown` how the
// other sheet displayed it.
//
// The region holds the values as code, so that the sheet is still a
// notebook any Jupyter runs: its source, which is made here from the
// names and the forms and from nothing else.  A region whose source is
// not exactly that code was given nothing: it is ordinary math, and
// shows what it runs.
//
// (No DOM in here: the tests run this under node as well.)

const KEYWORDS = new Set(("False None True and as assert async await break class continue def del elif else except " +
  "finally for from global if import in is lambda nonlocal not or pass raise return try while with yield").split(" "));

/** Is this a name a sheet hands on: one a sheet can define, and does not keep to itself (`_x`)? */
export function isName(name) {
  return typeof name === "string" && /^\p{XID_Start}\p{XID_Continue}*$/u.test(name)
    && name.normalize("NFKC") === name && !KEYWORDS.has(name);
}

/**
 * JSON as a Python string literal, in plain ASCII: a character that is
 * none is written by its number (as JSON writes it), so that nothing
 * which reads the code — Python, or the toolkit before it — sees more in
 * the text than letters.
 */
function literal(json) {
  const plain = json.replace(/[\u007f-\uffff]/g, (unit) => "\\u" + unit.charCodeAt(0).toString(16).padStart(4, "0"));
  return "'" + plain.replace(/\\/g, "\\\\").replace(/'/g, "\\'") + "'";
}

const HEAD = "# Values given by another sheet: this cell's output says which sheet, and what they are.\n" +
             "from utils.worksheet.values import given as _given\n";

/** The code that holds what a region was given — or null, for what is no such thing. */
export function sourceOf(given) {
  const values = given && Array.isArray(given.values) ? given.values : [];
  if (!values.length || !values.every((entry) => entry && isName(entry.name) && typeof entry.form === "string")) return null;
  const names = values.map((entry) => entry.name);
  if (new Set(names).size !== names.length) return null;
  const forms = JSON.stringify(Object.fromEntries(values.map((entry) => [entry.name, entry.form])));
  return `${HEAD}(${names.join(", ")},) = _given(${literal(forms)})`;
}

/** How a value is displayed, as far as that is said as it should be: its LaTeX and its plain text. */
function shownOf(shown) {
  const kept = {};
  for (const kind of ["text/latex", "text/plain"]) {
    if (shown && typeof shown[kind] === "string") kept[kind] = shown[kind];
  }
  return kept;
}

/** What a revision is called: a number that was counted, or text that was typed — else null. */
const revisionOf = (revision) => (typeof revision === "string" || Number.isSafeInteger(revision) ? revision : null);

/**
 * What a region says it was given, as far as that is of the kind it
 * should be — or null.  (A file says it, or the clipboard: either may be
 * anyone's.)
 */
export function normal(given) {
  if (!given || typeof given !== "object" || typeof given.sheet !== "string" || !given.sheet) return null;
  const values = [], seen = new Set();
  for (const entry of Array.isArray(given.values) ? given.values : []) {
    if (!entry || !isName(entry.name) || typeof entry.form !== "string" || seen.has(entry.name)) continue;
    seen.add(entry.name);
    values.push({ name: entry.name, ...(typeof entry.tex === "string" ? { tex: entry.tex } : {}),
                  form: entry.form, shown: shownOf(entry.shown) });
  }
  if (!values.length) return null;
  return { sheet: given.sheet, name: typeof given.name === "string" ? given.name : "", revision: revisionOf(given.revision),
           saved: typeof given.saved === "string" ? given.saved : "", values };
}

/**
 * What a region is given when it takes the values `names` of a sheet:
 * `said` is what that sheet delivers (the server's /api/values) and
 * `sheet` its file as it is reached from the folder of the sheet that
 * takes them.  Null where it delivers none of them.
 */
export function taken(said, sheet, names) {
  const record = said && said.values;
  const wanted = new Set(names);
  return normal({ sheet, name: said && said.name, revision: record && record.revision, saved: record && record.saved,
                  values: (record && Array.isArray(record.values) ? record.values : []).filter((entry) => entry && wanted.has(entry.name)) });
}

/** Where what a region was given is from, in a few words: "From Limits · rev. 3". */
export function caption(given) {
  const revision = given.revision === null || given.revision === "" ? "" : ` · rev. ${given.revision}`;
  return `From ${given.name || given.sheet}${revision}`;
}

/**
 * How what a region was given stands to what its sheet delivers now.
 * `said` is the server's word on that sheet (/api/values; `brief` will
 * do) — null for a sheet that is not there.  Null: the region has what
 * the sheet delivers.  Otherwise {lost: why} — nothing is delivered — or
 * {revision, saved}: the revision whose values are there to take.
 */
export function standing(given, said) {
  if (!said) return { lost: "not found" };
  const record = said.values;
  if (!record) return { lost: "keeps no values" };
  if (record.revision === given.revision && record.saved === given.saved) return null;
  return { revision: revisionOf(record.revision), saved: typeof record.saved === "string" ? record.saved : "" };
}

/** What a mark on a region says of `standing` that is not null. */
export function markText(mark) {
  if (mark.lost) return mark.lost;
  return mark.revision === null || mark.revision === "" ? "newer values are there" : `rev. ${mark.revision} is there`;
}

// ---- where the other sheet is ----------------------------------------------
// Paths as the server has them: from the folder it was started in, with
// "/" between the parts.  A region names the other sheet as it is reached
// from its own sheet's folder, so that sheets which are moved together
// still find each other.

/** `path` as it is reached from `folder` ("" is the top one): "../common/Limits.ipynb". */
export function relative(folder, path) {
  const from = folder ? folder.split("/") : [];
  const to = path.split("/");
  let same = 0;
  while (same < from.length && same < to.length - 1 && from[same] === to[same]) same += 1;
  return [...from.slice(same).map(() => ".."), ...to.slice(same)].join("/");
}

/** The path of what `reference` names as seen from `folder` — or null, where that is outside the top folder. */
export function resolve(folder, reference) {
  const parts = folder ? folder.split("/") : [];
  for (const part of String(reference).split("/")) {
    if (part === "" || part === ".") continue;
    if (part !== "..") parts.push(part);
    else if (!parts.length) return null;
    else parts.pop();
  }
  return parts.join("/");
}
