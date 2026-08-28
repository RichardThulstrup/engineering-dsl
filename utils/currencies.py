"""Currency conversion with the ``▶`` operator.

Currencies are not physical units — they don't have a dimension forallpeople
understands, and their conversion factors change daily.  So they get their
own type, ``Currency``, that participates in the toolkit just enough to be
convertible via ``▶`` and to support arithmetic that makes sense
(scaling by numbers, adding amounts in the same currency).

Usage::

    salary := 50_000 USD
    in_dkk := salary ▶ DKK            # converted at current rate
    in_eur := salary ▶ EUR

    update_currency_rates()            # refresh rates from Nationalbanken

By default the rate table is populated with reasonable static rates as a
fallback, then refreshed on first use from Danmarks Nationalbank's public
XML API.  Rates are cached on disk for 24 hours.

The data source — Danmarks Nationalbank — publishes rates as DKK per 100
units of foreign currency.  We normalise to "DKK per 1 unit" internally
to keep the arithmetic readable.

Limitations of currency-as-unit:

  - **Time-dependent.**  ``100 USD ▶ DKK`` uses the rate at the time of the
    call.  If you ran the same line yesterday you'd get a different number.
  - **No mixing with physical dimensions.**  ``100 USD * (5 m)`` raises —
    money isn't a length, even if Python's duck typing would let it slide.
  - **Currency arithmetic is restricted.**  You can scale by a number
    (``3 * salary``), add same-currency amounts (``salary + bonus``), or
    add cross-currency amounts (``income_usd + income_dkk`` — converted to
    the LHS's currency at the current rate).  You cannot multiply two
    currency amounts (``USD * EUR`` is meaningless).

The currency rates are public reference rates published once per banking
day; they're not bid/ask quotes you'd use for actual trading.  For
engineering estimates, budget planning, and unit-converting exercise
problems, that's what you want.  For settling actual transactions, use a
broker.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_RATES_URL = "https://www.nationalbanken.dk/api/currencyratesxml?lang=en"
_HTTP_TIMEOUT_SEC = 10
_CACHE_TTL_SEC = 24 * 60 * 60   # 24 hours

# Cache version — bump when the parser semantics change in a way that would
# make older cached files wrong.  Older caches are silently ignored on load
# (treated as if absent), so a parser fix automatically invalidates rates
# fetched by the buggy version on the next module use.
#
# Version history:
#   1 — initial release; parser only checked ``refamount`` on the root
#       element.  Caches written by v1 had every rate 100× too large
#       because Nationalbanken puts ``refamount`` on ``<dailyrates>``.
#   2 — refamount detection extended to ancestors of <currency>; sanity
#       heuristic added that catches the 100× condition even if the
#       attribute can't be located.  Old v1 caches are discarded.
_CACHE_VERSION = 2

# Cache lives in a user-cache directory so multiple notebooks share refreshes.
# On Windows, ``Path.home() / 'AppData' / 'Local'`` is conventional; on Linux,
# ``~/.cache``; on macOS, ``~/Library/Caches``.  Python doesn't have a
# stdlib XDG-cache helper, so we use a single user-home location that works
# everywhere.
_CACHE_DIR = Path.home() / ".cache" / "engineer_dsl"
_CACHE_FILE = _CACHE_DIR / "currency_rates.json"


# ---------------------------------------------------------------------------
# Static fallback rates
# ---------------------------------------------------------------------------
# Used when the API is unreachable AND no cached rates exist.  These get
# stale — the whole point of the live fetch is to replace them — but they
# let ``import currencies`` succeed offline and give *some* approximate
# answer rather than crashing.  Values are illustrative; expect to be off
# by a few percent against today's actual rate.
#
# Stored as DKK-per-unit (i.e. ``_FALLBACK_RATES["USD"] == 6.94`` means
# 1 USD ≈ 6.94 DKK).  DKK is always exactly 1.
_FALLBACK_RATES: dict[str, float] = {
    "DKK": 1.0,
    "EUR": 7.46,        # roughly fixed by Danish ERM-II commitment
    "USD": 6.94,
    "GBP": 8.65,
    "JPY": 0.0463,      # rates per 1 yen, not per 100 yen
    "SEK": 0.654,
    "NOK": 0.621,
    "CHF": 7.85,
    "CAD": 5.05,
    "AUD": 4.55,
    "CNY": 0.961,
    "HKD": 0.890,
    "INR": 0.0826,
    "PLN": 1.69,
    "CZK": 0.298,
    "HUF": 0.0186,
    "TRY": 0.187,
    "BRL": 1.20,
    "ZAR": 0.378,
    "MXN": 0.341,
    "SGD": 5.20,
    "KRW": 0.00500,
    "NZD": 4.16,
    "ILS": 1.92,
    "RUB": 0.0689,
    "THB": 0.198,
    "IDR": 0.000425,
}


# Module-level state.  ``_rates`` is the current best estimate of rates
# (live, cached, or fallback).  ``_rates_timestamp`` is when ``_rates`` was
# populated, used to decide whether a refresh is needed.
_rates: dict[str, float] = dict(_FALLBACK_RATES)
_rates_timestamp: float | None = None
_rates_source: str = "fallback"   # "live" | "cache" | "fallback"


# ---------------------------------------------------------------------------
# Currency type
# ---------------------------------------------------------------------------

class Currency:
    """A money amount with a currency code.

    Created by multiplying a number by a unit-marker like ``USD`` or ``DKK``.
    The unit-marker itself is just a ``Currency`` instance with ``value=1``
    so the same class covers both "the unit" and "an amount" — analogous to
    how forallpeople's ``m`` is a Physical with magnitude 1.
    """

    __slots__ = ("value", "code")

    def __init__(self, value: float, code: str):
        self.value = float(value)
        self.code = code

    # ------------------------------------------------------------------
    # Multiplication: number * Currency or Currency * number — scale.
    # ------------------------------------------------------------------
    def __mul__(self, other):
        if isinstance(other, Currency):
            return NotImplemented   # USD * EUR is meaningless
        # Refuse Physical (forallpeople) — money×length isn't a thing.
        # Detect by duck-typing on ``.dimensions`` rather than importing
        # forallpeople so this module stays a soft-dependency.
        if hasattr(other, "dimensions"):
            raise TypeError(
                f"cannot multiply currency ({self.code}) by a physical "
                f"quantity ({other!r}) — money has no SI dimension"
            )
        try:
            f = float(other)
        except (TypeError, ValueError):
            return NotImplemented
        return Currency(self.value * f, self.code)

    def __rmul__(self, other):
        # Reached when the LHS doesn't know how to multiply by us — e.g.
        # ``5 * USD``, ``np.float64(5) * USD``, or ``Physical * USD``
        # (where Physical's ``__mul__`` stepped aside).  We re-check for
        # Physical here for the same reason as ``__mul__``.
        if hasattr(other, "dimensions"):
            raise TypeError(
                f"cannot multiply currency ({self.code}) by a physical "
                f"quantity ({other!r}) — money has no SI dimension"
            )
        return self.__mul__(other)

    # ------------------------------------------------------------------
    # Division
    #
    # Currency / Currency: dimensionless ratio in target-currency terms.
    # This is what makes ``▶`` work — ``100*USD / DKK`` gives the
    # number-of-DKK that 100 USD is worth.
    #
    # Currency / number: scale.
    # ------------------------------------------------------------------
    def __truediv__(self, other):
        if isinstance(other, Currency):
            self_to_dkk = get_currency_rate(self.code)
            other_to_dkk = get_currency_rate(other.code)
            # self.value (in self.code) → self.value * self_to_dkk  (in DKK)
            # then /= other_to_dkk to express as count of `other.code`.
            #
            # Bookkeeping: ``other.value`` is normally 1 (DKK as a marker)
            # but if the user wrote ``▶ (10 EUR)`` we honour that literally.
            if other.value == 0:
                raise ZeroDivisionError(
                    f"cannot express {self!r} in zero {other.code}")
            return (self.value * self_to_dkk) / (other.value * other_to_dkk)
        # Refuse Physical — same rationale as __mul__.
        if hasattr(other, "dimensions"):
            raise TypeError(
                f"cannot divide currency ({self.code}) by a physical "
                f"quantity ({other!r}) — currencies are dimensionless"
            )
        try:
            f = float(other)
        except (TypeError, ValueError):
            return NotImplemented
        if f == 0:
            raise ZeroDivisionError("Currency divided by zero")
        return Currency(self.value / f, self.code)

    def __rtruediv__(self, other):
        # number / Currency — return the inverse-currency interpretation
        # only when ``other`` is dimensionless.  ``500 / USD`` doesn't have
        # a sensible meaning in this model, so we refuse rather than
        # silently doing the wrong thing.
        return NotImplemented

    # ------------------------------------------------------------------
    # Addition / subtraction: same currency, or convert RHS to LHS's code.
    # ------------------------------------------------------------------
    def __add__(self, other):
        if not isinstance(other, Currency):
            return NotImplemented
        if other.code == self.code:
            return Currency(self.value + other.value, self.code)
        # Convert other to self.code at the current rate.
        rhs_in_self = float(other / Currency(1.0, self.code))
        return Currency(self.value + rhs_in_self, self.code)

    def __radd__(self, other):
        return NotImplemented

    def __sub__(self, other):
        if not isinstance(other, Currency):
            return NotImplemented
        if other.code == self.code:
            return Currency(self.value - other.value, self.code)
        rhs_in_self = float(other / Currency(1.0, self.code))
        return Currency(self.value - rhs_in_self, self.code)

    # ------------------------------------------------------------------
    # Comparison: only meaningful between currencies (always normalises
    # to DKK first so cross-currency comparison is exchange-rate-aware).
    # ------------------------------------------------------------------
    def __eq__(self, other):
        if isinstance(other, Currency):
            self_dkk = self.value * get_currency_rate(self.code)
            other_dkk = other.value * get_currency_rate(other.code)
            return abs(self_dkk - other_dkk) < 1e-9
        return NotImplemented

    def __lt__(self, other):
        if isinstance(other, Currency):
            self_dkk = self.value * get_currency_rate(self.code)
            other_dkk = other.value * get_currency_rate(other.code)
            return self_dkk < other_dkk
        return NotImplemented

    def __hash__(self):
        return hash((round(self.value * get_currency_rate(self.code), 6),))

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        # Two decimal places is the standard for most currencies.  Yen
        # and a few others traditionally show whole units, but the
        # extra decimals don't hurt and keep formatting simple.  Users
        # who want different formatting use ``▶`` or ``f"{x:.0f} {x.code}"``.
        return f"{self.value:,.2f} {self.code}"

    def __str__(self) -> str:
        return self.__repr__()

    def __float__(self) -> float:
        return self.value

    def __format__(self, spec: str) -> str:
        if spec:
            return f"{self.value:{spec}} {self.code}"
        return self.__repr__()


# ---------------------------------------------------------------------------
# Rate management
# ---------------------------------------------------------------------------

def get_currency_rate(code: str) -> float:
    """Return DKK-per-unit-of-``code`` at the current time.

    Triggers a refresh from the live API if the cached rates are older
    than ``_CACHE_TTL_SEC``.  Failures during refresh fall back to the
    last-known rates (cached or static).

    Raises ``KeyError`` if ``code`` isn't a known currency.  The known
    set is whatever the most recent successful fetch returned, plus
    DKK and the static fallback list (so the call works offline for
    common currencies even on first use).
    """
    _ensure_rates_fresh()
    try:
        return _rates[code]
    except KeyError:
        raise KeyError(
            f"Unknown currency code: {code!r}. Known: {sorted(_rates.keys())}"
        ) from None


def _ensure_rates_fresh() -> None:
    """Refresh ``_rates`` if stale.  Silent on failure — the caller still
    gets a usable rate table (just an older one)."""
    global _rates_timestamp, _rates_source

    now = _now_seconds()

    # First call: try cache, then live fetch.  Subsequent calls only
    # fetch live if the in-memory copy is stale.
    if _rates_timestamp is None:
        if _try_load_cache():
            return
        try:
            update_currency_rates()
        except Exception:
            # Stay on fallback rates — already populated at module load.
            _rates_timestamp = now
            _rates_source = "fallback"
        return

    if now - _rates_timestamp > _CACHE_TTL_SEC:
        try:
            update_currency_rates()
        except Exception:
            # Refresh failed; keep using what we have but mark the time so
            # we don't retry on every single call.
            _rates_timestamp = now


def update_currency_rates() -> dict[str, float]:
    """Force a refresh from the Nationalbanken API.  Returns the new rate
    table (dict of ``code -> DKK-per-unit``).

    Saves the result to the on-disk cache so other notebook sessions don't
    re-fetch unnecessarily.  Raises ``urllib.error.URLError`` (or similar)
    if the network is unreachable; the caller can decide whether to fall
    back or surface the error.
    """
    global _rates, _rates_timestamp, _rates_source

    req = urllib.request.Request(
        _RATES_URL,
        headers={
            # Nationalbanken's CDN rejects clients with non-browser UAs
            # (HTTP 403).  A standard Mozilla string passes; this is the
            # same approach used by widely-deployed accounting tools that
            # consume this feed (e.g. C5/Business Central integrations).
            "User-Agent": (
                "Mozilla/5.0 (compatible; engineering-dsl currency helper)"
            ),
            "Accept": "application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SEC) as resp:
        xml_bytes = resp.read()

    parsed = _parse_nationalbanken_xml(xml_bytes)
    # Always include DKK = 1 (the base currency, not in the feed itself).
    parsed["DKK"] = 1.0

    _rates = parsed
    _rates_timestamp = _now_seconds()
    _rates_source = "live"
    _save_cache()
    return dict(_rates)


def _parse_nationalbanken_xml(xml_bytes: bytes) -> dict[str, float]:
    """Parse Nationalbanken's currency-rates XML into a code→rate dict.

    The published format (stable for many years) is::

        <exchangerates type="..." refamount="100">
          <dailyrates id="2024-...">
            <currency code="USD" desc="..." rate="694.32"/>
            <currency code="EUR" desc="..." rate="745.81"/>
            ...
          </dailyrates>
        </exchangerates>

    ``refamount="100"`` means each rate is "DKK per 100 foreign currency
    units", so we divide by 100 to get DKK-per-unit, which is what the
    rest of this module uses internally.

    Defensive details:

      - ``refamount`` may live on the outer ``<exchangerates>`` element
        OR on the inner ``<dailyrates>`` element, depending on which
        flavour of the feed you hit.  We search the root, then the first
        ``<dailyrates>`` child, then any ancestor of the first
        ``<currency>`` element — whichever has it wins.

      - A few legacy variants put ``refamount`` directly on each
        ``<currency>`` element instead of as a containing-element
        default.  We honour that per-currency value when it's present
        (overrides the document-level ref).

      - Comma-as-decimal separator has been observed in older Danish
        locale exports; we accept both ``"6.94"`` and ``"6,94"``.
    """
    root = ET.fromstring(xml_bytes)

    def _read_refamount(el) -> float | None:
        """Return the refamount declared on ``el``, or None if absent."""
        if el is None:
            return None
        for attr_name in ("refamount", "RefAmount", "refAmount", "REFAMOUNT"):
            v = el.attrib.get(attr_name)
            if v is None:
                continue
            try:
                return float(v.replace(",", "."))
            except (ValueError, AttributeError):
                continue
        return None

    # Default refamount when nowhere declared — interpret rates verbatim.
    document_refamount = _read_refamount(root)
    if document_refamount is None:
        # Try the first <dailyrates> child — that's where it lives in
        # the current Nationalbanken feed (the bug fixed in this turn).
        first_daily = next(iter(root.iter("dailyrates")), None)
        document_refamount = _read_refamount(first_daily)
    if document_refamount is None:
        # Final fallback: walk every ancestor of the first <currency>
        # node looking for the attribute.  Catches feeds that use
        # different containing-element names.
        first_currency = next(iter(root.iter("currency")), None)
        if first_currency is not None:
            for ancestor in root.iter():
                if first_currency in list(ancestor):
                    document_refamount = _read_refamount(ancestor)
                    if document_refamount is not None:
                        break
    if document_refamount is None or document_refamount <= 0:
        document_refamount = 1.0

    rates: dict[str, float] = {}
    # Each <currency> entry has code, desc, rate.  We take all of them;
    # if the feed includes multiple <dailyrates> blocks (unusual but
    # possible for historical data), the last one wins per code, which
    # is what you want for "the most recent rate".
    for currency in root.iter("currency"):
        code = (currency.attrib.get("code") or
                currency.attrib.get("Code") or "").strip().upper()
        rate_str = (currency.attrib.get("rate") or
                    currency.attrib.get("Rate") or "").strip()
        if not code or not rate_str:
            continue
        try:
            rate = float(rate_str.replace(",", "."))
        except ValueError:
            continue

        # Per-element refamount overrides the document default.  Most
        # currencies don't carry one, so the document_refamount applies.
        per_element_ref = _read_refamount(currency)
        ref = per_element_ref if per_element_ref else document_refamount

        # Stored normalised: DKK per 1 unit of `code`.
        rates[code] = rate / ref

    if not rates:
        raise ValueError(
            "Nationalbanken response had no parseable currency entries")

    # Sanity heuristic — catches the case where the XML uses an attribute
    # name or container element we don't recognise, leaving us with rates
    # that are off by the (almost always) factor of 100.
    #
    # We use EUR and USD as canaries because they're the most-watched
    # rates against DKK and their plausible ranges are well known:
    #   - EUR-DKK is held within ~7.45 ± 2% by Danish ERM-II commitment.
    #     A value > 100 is impossible in any realistic scenario.
    #   - USD-DKK has historically ranged 5–9; > 100 means we're 100×
    #     too large.
    # The threshold of 50 leaves a wide margin and won't false-positive
    # on any real currency the bank publishes (the highest plausible
    # DKK-per-unit rate is GBP at maybe 11–12).
    canary = max(rates.get("EUR", 0.0), rates.get("USD", 0.0))
    if canary > 50.0:
        # Off by 100× — almost certainly an undetected refamount=100.
        # Renormalise the entire table; if the heuristic misfired
        # (genuinely impossible given the threshold) the rates would
        # still be wrong but in a different direction.
        rates = {k: v / 100.0 for k, v in rates.items()}

    return rates


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def _save_cache() -> None:
    """Persist ``_rates`` to the on-disk cache.  Best-effort; cache
    write failures are swallowed (we're offline-tolerant)."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _CACHE_VERSION,
            "timestamp": _rates_timestamp,
            "rates": _rates,
        }
        with _CACHE_FILE.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
    except OSError:
        pass


def _try_load_cache() -> bool:
    """Populate ``_rates`` from the on-disk cache if it exists, is
    fresh, AND was written by a compatible parser version.  Returns
    True on success.

    A version mismatch means the cache was written by an older parser
    that may have produced wrong values; we ignore it as if absent so
    the module re-fetches with the current parser on next use.
    """
    global _rates, _rates_timestamp, _rates_source

    if not _CACHE_FILE.exists():
        return False
    try:
        with _CACHE_FILE.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False

    # Reject caches from an older parser version — they may contain
    # systematically-wrong rates (the 100× bug fixed in v2 is the
    # canonical example).  Treating them as absent triggers a fresh
    # fetch on next use.
    if payload.get("version") != _CACHE_VERSION:
        return False

    ts = payload.get("timestamp")
    rates = payload.get("rates")
    if not isinstance(ts, (int, float)) or not isinstance(rates, dict):
        return False
    if _now_seconds() - ts > _CACHE_TTL_SEC:
        return False

    # Validate types — guard against a corrupted cache file shaped like
    # a dict but with non-numeric values.
    cleaned: dict[str, float] = {}
    for k, v in rates.items():
        if isinstance(k, str) and isinstance(v, (int, float)):
            cleaned[k] = float(v)
    if not cleaned:
        return False

    _rates = cleaned
    _rates_timestamp = float(ts)
    _rates_source = "cache"
    return True


def clear_currency_cache() -> bool:
    """Delete the on-disk currency-rates cache.  Returns True if the
    cache existed and was removed, False if it was already absent.

    Useful when you want to force a full refresh on the next call —
    e.g. after a parser update, or to force-pull rates from a different
    business day than the cached one.  After calling this, the next
    rate lookup will hit the live API.
    """
    global _rates_timestamp
    try:
        _CACHE_FILE.unlink()
        # Reset the in-memory state too so the next call refreshes.
        _rates_timestamp = None
        return True
    except FileNotFoundError:
        return False


def _now_seconds() -> float:
    return datetime.now(tz=timezone.utc).timestamp()


def rates_status() -> dict[str, Any]:
    """Return a summary of the current rates state.  Useful for debugging
    and for showing the user when their rates last refreshed."""
    return {
        "source": _rates_source,
        "timestamp": _rates_timestamp,
        "age_seconds": (_now_seconds() - _rates_timestamp
                        if _rates_timestamp else None),
        "currency_count": len(_rates),
        "codes": sorted(_rates.keys()),
    }


# ---------------------------------------------------------------------------
# Pre-built currency markers
# ---------------------------------------------------------------------------
# Each is a ``Currency`` with ``value=1``, so ``100 * USD`` works the way
# ``5 * m`` works for length — number times unit-marker gives a measurement.

DKK = Currency(1.0, "DKK")
USD = Currency(1.0, "USD")
EUR = Currency(1.0, "EUR")
GBP = Currency(1.0, "GBP")
JPY = Currency(1.0, "JPY")
SEK = Currency(1.0, "SEK")
NOK = Currency(1.0, "NOK")
CHF = Currency(1.0, "CHF")
CAD = Currency(1.0, "CAD")
AUD = Currency(1.0, "AUD")
CNY = Currency(1.0, "CNY")
HKD = Currency(1.0, "HKD")
INR = Currency(1.0, "INR")
PLN = Currency(1.0, "PLN")
CZK = Currency(1.0, "CZK")


__all__ = [
    "Currency",
    "get_currency_rate",
    "update_currency_rates",
    "clear_currency_cache",
    "rates_status",
    # Pre-built markers
    "DKK", "USD", "EUR", "GBP", "JPY", "SEK", "NOK",
    "CHF", "CAD", "AUD", "CNY", "HKD", "INR", "PLN", "CZK",
]
