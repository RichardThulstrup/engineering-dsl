// Sheets that build on each other.
//
// A sheet takes values from other sheets (given.js), and those may take
// values from others in turn.  When one of them is saved with other
// values, the sheets below it are behind: each holds what the sheet above
// it delivered before.  Here it is worked out — from what the server
// reads in the sheets' files (/api/chain) — which sheets are behind, and
// in which order they are brought up to date: a sheet takes what the
// sheets above it deliver now, is calculated, and is saved as a new
// revision, which the sheets below it take in turn.
//
// A sheet is one of
//
//     { name, revision, saved, values, takes: [take], trusted }
//
// — `values` being {revision, saved, count} of the values it delivers
// (null: it keeps none), `trusted` whether it may be calculated here
// without asking (said only where that was asked), and a take
//
//     { sheet, revision, saved }
//
// what one of its regions was given by the sheet `sheet` (a path from
// the top folder; null: a sheet above that folder): the values of that
// sheet's `revision`, as it was `saved` then.
//
// (No DOM in here: the tests run this under node as well.)

const HERE = Symbol("the sheet at hand");

/**
 * What bringing a sheet's values up to date comes to.
 * @param {object} opts
 *   own      the takes of the sheet at hand (as its page has them)
 *   self     its path — null for a sheet that has no file yet
 *   sheets   the sheets it builds on, by path, as the server says them
 *            (null for one that is not there)
 * @returns {object}
 *   steps    the sheets to bring up to date, the first first: each
 *            {path, name, revision, news} — `news` being what it is given
 *            anew, each {sheet, name, has, there, anew}: of which sheet,
 *            which revision it has, which one is there to take (null:
 *            none yet), and whether that sheet is itself saved anew
 *            before (`anew`)
 *   here     the news of the sheet at hand
 *   behind   the paths of the sheets that are behind (a Set)
 *   held     the sheets that are behind and stay so: {path, name, why}
 *            — "untrusted", or "waits" (for the sheet named `for`)
 *   circle   the names of sheets that take from each other in a circle
 *            — null where there is none; with one, nothing is to be done
 */
export function plan({ own, self = null, sheets }) {
  const node = (path) => (path === self ? HERE : path);
  const name = (at) => (at === HERE ? "this sheet" : sheets[at].name);
  const known = new Map();              // a sheet → what was found for it ("open": it is being looked at)
  const steps = [], held = [];
  let circle = null;

  const look = (at, trail) => {
    const had = known.get(at);
    if (had === "open") {
      circle = circle || [...trail.slice(trail.indexOf(at)), at].map(name);
      return { behind: false, renews: false, news: [] };
    }
    if (had) return had;
    known.set(at, "open");
    const news = [];
    let behind = false, waits = null;
    for (const take of at === HERE ? own : sheets[at].takes) {
      // (A sheet above the top folder — it has no path — or one that is not there: nothing is to be had of it.)
      if (!sheets[take.sheet]) continue;
      const above = node(take.sheet);
      const found = look(above, [...trail, at]);
      const record = above === HERE ? null : sheets[above].values;
      const moved = Boolean(record) && !(record.revision === take.revision && record.saved === take.saved);
      if (moved || found.renews) {
        news.push({ sheet: take.sheet, name: name(above), has: { revision: take.revision, saved: take.saved },
                    there: moved ? { revision: record.revision, saved: record.saved } : null, anew: found.renews });
      } else if (found.behind) waits = name(above);
      behind = behind || moved || found.behind;
    }
    const untrusted = at !== HERE && sheets[at].trusted === false;
    const found = { behind, renews: news.length > 0 && !untrusted, news };
    known.set(at, found);
    if (at !== HERE && found.renews) steps.push({ path: at, name: name(at), revision: sheets[at].revision, news });
    else if (at !== HERE && behind) {
      held.push(news.length ? { path: at, name: name(at), why: "untrusted" } : { path: at, name: name(at), why: "waits", for: waits });
    }
    return found;
  };

  const here = look(HERE, []);
  const behind = new Set([...known].filter(([at, found]) => at !== HERE && found.behind).map(([at]) => at));
  if (circle) return { steps: [], here: [], behind, held: [], circle };
  return { steps, here: here.news, behind, held, circle: null };
}

/** A revision as it is said: "rev. 4" — or "", of a sheet that says none (one from elsewhere may not). */
export const revisionText = (revision) => (revision === null || revision === undefined || revision === "" ? "" : `rev. ${revision}`);

/**
 * What a sheet is given anew (plan: news), in words: "rev. 7 of Limits
 * (it has rev. 5), Supply as it is saved then".  A revision that was
 * typed stays what it is when its sheet is saved anew: "rev. B of
 * Limits, saved anew".  What several regions take alike is said once.
 */
export function newsText(news) {
  return [...new Set(news.map((one) => {
    if (!one.there) return `${one.name} as it is saved then`;
    const had = revisionText(one.has.revision);
    const has = !had ? "" : one.has.revision === one.there.revision ? ", saved anew" : ` (it has ${had})`;
    return `${revisionText(one.there.revision) || "a later save"} of ${one.name}${has}` + (one.anew ? ", or what is saved then" : "");
  }))].join(", ");
}

/** Why a sheet that is behind stays so (plan: held), as a sentence. */
export function heldText(one) {
  if (one.why === "untrusted") {
    return `${one.name} has not been calculated on this computer yet: open it and choose Calculate. It is left as it is.`;
  }
  return `${one.name} waits for ${one.for}, and is left as it is.`;
}

/** What came of bringing a sheet up to date (the word of the page that did it — see main.js: bringUp), in a few words. */
export function outcomeText(said) {
  if (said.saved) return revisionText(said.revision) ? `saved as ${revisionText(said.revision)}` : "saved";
  return said.same ? "has what there is" : `left as it is: ${said.left}`;
}
