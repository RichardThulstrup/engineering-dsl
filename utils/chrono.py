"""
ISO 8601 time and date utilities.

A single polymorphic ``iso(...)`` function handles both directions —
parsing strings into Python ``date``/``datetime``/``time``/``timedelta``
objects, and formatting those objects back into ISO 8601 strings.

Examples
--------

Parsing strings::

    iso("2026-05-05")              → datetime.date(2026, 5, 5)
    iso("2026-05-05T14:30:00")     → datetime.datetime(...)
    iso("2026-05-05T14:30:00Z")    → datetime.datetime(..., tzinfo=UTC)
    iso("14:30:00")                 → datetime.time(14, 30)
    iso("P1Y6M")                    → timedelta(days=548.0, ...)
    iso("PT15M30S")                 → timedelta(minutes=15, seconds=30)

Special keyword strings::

    iso("now")                      → datetime.now()
    iso("today")                    → date.today()
    iso("utcnow")                   → datetime.now(timezone.utc)

Formatting objects::

    iso(date(2026, 5, 5))           → "2026-05-05"
    iso(datetime(2026, 5, 5, 14, 30)) → "2026-05-05T14:30:00"
    iso(timedelta(hours=2, minutes=15)) → "PT2H15M"

Arithmetic uses standard Python ``datetime`` semantics — there's no DSL
magic here, just the usual operators::

    end - start                     # timedelta
    iso("today") + iso("P1M")       # one month from today (approximate)
    start + iso("PT5M")             # 5 minutes after start

When mixing types, remember Python's rules: ``date - date`` and
``datetime - datetime`` both work, but ``date - datetime`` does not —
you can't subtract a moment-in-time from a whole day, because the date
has no time-of-day to anchor the subtraction.  Either promote the date
to a datetime (``iso("2026-05-06T00:00:00")``) or demote the datetime
to a date (``iso(dt.date())`` or ``iso("2026-05-05")``).

Approximate-vs-exact durations
------------------------------
ISO 8601 duration strings can include years and months (``P1Y6M``).
These are converted to ``timedelta`` using fixed averages — 365.25
days/year and 30.4375 days/month — because ``timedelta`` itself
doesn't track calendar units.  This is fine for "a year and a half"
arithmetic but loses calendar precision: ``iso("today") + iso("P1M")``
will add ~30.44 days, not "the same day next month."  For exact
calendar arithmetic (e.g., "1 month from Jan 31 = Feb 28"), use
``dateutil.relativedelta`` directly.
"""

from datetime import datetime, date, time, timedelta, timezone
import re

__all__ = [
    "datetime", "date", "time", "timedelta", "timezone",
    "iso",
]


# ISO 8601 duration: P[nY][nM][nW][nD][T[nH][nM][nS]].  The ``M`` token
# means months when it sits before ``T`` and minutes when it sits after,
# so we have separate named groups (``months`` vs ``mins``) to avoid
# the ambiguity at parse time.
_NUM = r'\d+(?:[.,]\d+)?'
_DUR_RE = re.compile(
    r'^(?P<sign>[-+])?P'
    rf'(?:(?P<years>{_NUM})Y)?'
    rf'(?:(?P<months>{_NUM})M)?'
    rf'(?:(?P<weeks>{_NUM})W)?'
    rf'(?:(?P<days>{_NUM})D)?'
    r'(?:T'
    rf'(?:(?P<hours>{_NUM})H)?'
    rf'(?:(?P<mins>{_NUM})M)?'
    rf'(?:(?P<secs>{_NUM})S)?'
    r')?$'
)

_DATE_PREFIX_RE = re.compile(r'^\d{4}-\d{2}-\d{2}')


def _parse_duration(s: str) -> timedelta:
    m = _DUR_RE.match(s)
    if not m:
        raise ValueError(f"not a valid ISO 8601 duration: {s!r}")
    g = m.groupdict()
    sign = -1 if g.pop('sign') == '-' else 1
    if not any(g.values()):
        # The regex matched, but every component is empty — i.e. the
        # string was just "P" (or "PT" with nothing after).  ISO 8601
        # requires at least one component.
        raise ValueError(f"empty ISO 8601 duration: {s!r}")

    def num(k):
        return float(g[k].replace(',', '.')) if g[k] else 0.0

    days = num('years') * 365.25 + num('months') * 30.4375 + num('weeks') * 7 + num('days')
    seconds = num('hours') * 3600 + num('mins') * 60 + num('secs')
    return sign * timedelta(days=days, seconds=seconds)


def _format_duration(td: timedelta) -> str:
    total = td.total_seconds()
    if total == 0:
        return "PT0S"

    sign = "-" if total < 0 else ""
    total = abs(total)

    # A duration that is an exact whole number of years, months or weeks
    # (as this module defines them) formats back in that unit, so
    # ``iso(iso("P1Y")) == "P1Y"`` round-trips.
    for unit, secs in (("Y", 365.25 * 86400), ("M", 30.4375 * 86400), ("W", 7 * 86400)):
        k = total / secs
        if k >= 1 and abs(k - round(k)) < 1e-9:
            return f"{sign}P{int(round(k))}{unit}"

    days = int(total // 86400)
    rem = total - days * 86400
    hours = int(rem // 3600)
    rem -= hours * 3600
    minutes = int(rem // 60)
    seconds = rem - minutes * 60

    parts = ["P"]
    if days:
        parts.append(f"{days}D")
    if hours or minutes or seconds:
        parts.append("T")
        if hours:
            parts.append(f"{hours}H")
        if minutes:
            parts.append(f"{minutes}M")
        if seconds:
            # Drop the trailing ".0" for whole-second values
            if seconds == int(seconds):
                parts.append(f"{int(seconds)}S")
            else:
                parts.append(f"{seconds}S")

    return sign + "".join(parts)


def iso(x):
    """Polymorphic ISO 8601 parser and formatter.

    Strings are parsed into the most specific type that fits:

    - ``"2026-05-05"`` → :class:`datetime.date`
    - ``"2026-05-05T14:30:00"`` → :class:`datetime.datetime`
    - ``"2026-05-05T14:30:00Z"`` → datetime with UTC tzinfo
    - ``"14:30:00"`` → :class:`datetime.time`
    - ``"P1Y6M"`` / ``"PT15M30S"`` → :class:`datetime.timedelta`

    The keyword strings ``"now"``, ``"today"``, ``"utcnow"`` produce the
    corresponding current values.

    Date/datetime/time/timedelta objects are formatted back to ISO 8601
    strings.  The mapping is bidirectional: ``iso(iso(s)) == s`` for
    well-formed inputs (modulo trivial rewrites like ``Z`` ↔ ``+00:00``).

    A space between the date and time of day is accepted as a separator
    on parse — common in log files — and normalised to ``T``.
    """
    # ---- Parse path ----
    if isinstance(x, str):
        s = x.strip()

        if s == "now":
            return datetime.now()
        if s == "today":
            return date.today()
        if s == "utcnow":
            return datetime.now(timezone.utc)

        # ISO 8601 durations always start with a literal P.
        if s.startswith("P") or s[:2] in ("-P", "+P"):
            return _parse_duration(s)

        # ISO 8601 instant.  Allow space as the date/time separator —
        # ``2026-05-05 14:30:00`` is common in log lines and not strict
        # ISO 8601, but we accept it as a courtesy.
        s_norm = s.replace(' ', 'T')
        # Z is the conventional UTC suffix.  Older Python's
        # ``datetime.fromisoformat`` (< 3.11) didn't accept it, and
        # rewriting to ``+00:00`` is harmless on newer versions too.
        s_norm = re.sub(r'Z$', '+00:00', s_norm)

        has_date = bool(_DATE_PREFIX_RE.match(s))
        has_time = ':' in s

        try:
            if has_date and ('T' in s_norm):
                return datetime.fromisoformat(s_norm)
            if has_date:
                return date.fromisoformat(s)
            if has_time:
                return time.fromisoformat(s)
        except ValueError:
            pass

        raise ValueError(f"could not parse {s!r} as ISO 8601")

    # ---- Format path ----
    # NB: ``isinstance(x, date)`` is True for datetime objects too,
    # because ``datetime`` subclasses ``date``.  Check the more
    # specific type first.
    if isinstance(x, datetime):
        return x.isoformat()
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, time):
        return x.isoformat()
    if isinstance(x, timedelta):
        return _format_duration(x)

    raise TypeError(
        f"cannot convert {type(x).__name__} to/from ISO 8601 — "
        f"expected str, date, datetime, time, or timedelta"
    )
