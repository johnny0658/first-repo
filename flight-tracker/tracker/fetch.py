"""Fetch one leg from Google Flights via fast-flights, politely.

We call the library's fetch_flights_html() and parse_js() ourselves instead of
get_flights(), because get_flights() crashes with an AttributeError on consent
or block pages. Looking at the HTML first lets us say what went wrong.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from fast_flights import FlightQuery, FlightsNotFound, Passengers, create_query
from fast_flights.fetcher import fetch_flights_html
from fast_flights.model import Flights
from fast_flights.parser import parse_js
from selectolax.lexbor import LexborHTMLParser

from .config import Config

log = logging.getLogger(__name__)

# Outcome kinds
OK = "ok"
CONSENT = "consent_page"
BLOCKED = "blocked"
GOOGLE_ERROR = "google_error"
UNPARSEABLE = "unparseable"
NETWORK = "network_error"

# These mean Google is refusing us; retrying or continuing would be impolite.
STOP_RUN = {CONSENT, BLOCKED}
RETRYABLE = {GOOGLE_ERROR, UNPARSEABLE, NETWORK}


@dataclass
class FetchOutcome:
    kind: str
    flights: list[Flights] = field(default_factory=list)
    detail: str = ""
    attempts: int = 0


def build_query(cfg: Config, date_iso: str, origin: str, destination: str):
    # The departure-time threshold is deliberately NOT sent to Google: its
    # hour filter's edge semantics are undocumented, and a filter we can't
    # verify could hide a qualifying 18:00 flight. filters.py applies it.
    flight = FlightQuery(
        date=date_iso,
        from_airport=origin,
        to_airport=destination,
        max_stops=0 if cfg.nonstop_only else None,
    )
    return create_query(
        flights=[flight],
        seat=cfg.seat,
        trip="one-way",
        passengers=Passengers(adults=cfg.adults),
        language=cfg.language,
        currency=cfg.currency,
    )


def classify_html(html: str) -> tuple[str, str]:
    """Return (kind, js_or_reason). kind is OK when the flights data script exists."""
    if not html or not html.strip():
        return UNPARSEABLE, "empty response"
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is not None:
        return OK, script.text()
    lower = html.lower()
    if "consent.google." in lower or "before you continue" in lower:
        return CONSENT, "Google returned a cookie-consent page instead of results"
    if "/sorry/" in lower or "unusual traffic" in lower or "captcha" in lower:
        return BLOCKED, "Google returned a block/CAPTCHA page (unusual traffic)"
    return UNPARSEABLE, "no flight data found in page (layout may have changed)"


def parse_flights(js: str) -> list[Flights]:
    """Parse with the library, also covering the 'best flights' section.

    fast-flights 3.1.0 only reads payload[3] of the page data. Google's page
    appears to split results into 'best' (payload[2]) and 'other' (payload[3])
    lists; if payload[2] has the same shape we parse it with the library too,
    by handing parse_js a copy whose [3] is swapped for [2]. If it doesn't
    parse, we keep the library's result and log it. Raises FlightsNotFound or
    parse errors from the library.
    """
    flights = list(parse_js(js))
    try:
        payload = json.loads(js.split("data:", 1)[1].rsplit(",", 1)[0])
        if isinstance(payload, list) and len(payload) > 3 and payload[2] and payload[2][0]:
            swapped = list(payload)
            swapped[3] = payload[2]
            flights.extend(parse_js("data:" + json.dumps(swapped) + ",}"))
    except Exception as exc:  # noqa: BLE001 - optional extra section
        log.warning("could not parse the 'best flights' section (%s); using library default only", exc)
    seen, unique = set(), []
    for f in flights:
        key = (
            f.price,
            tuple(f.airlines or ()),
            tuple((s.from_airport.code, s.to_airport.code, s.departure.date, s.departure.time) for s in f.flights),
        )
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def one_line(text: str, limit: int = 300) -> str:
    """Collapse whitespace so error text fits in one CSV/markdown cell."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fetch_once(query, fetch_html: Callable[[object], str] = fetch_flights_html) -> FetchOutcome:
    try:
        html = fetch_html(query)
    except Exception as exc:  # noqa: BLE001 - primp raises its own error types
        return FetchOutcome(NETWORK, detail=one_line(f"{type(exc).__name__}: {exc}"))
    outcome = _classify_and_parse(html)
    outcome.detail = one_line(outcome.detail)
    return outcome


def _classify_and_parse(html: str) -> FetchOutcome:
    kind, payload = classify_html(html)
    if kind != OK:
        return FetchOutcome(kind, detail=payload)
    try:
        return FetchOutcome(OK, flights=parse_flights(payload))
    except FlightsNotFound as exc:
        return FetchOutcome(GOOGLE_ERROR, detail=f"Google returned an error status: {exc}")
    except Exception as exc:  # noqa: BLE001
        return FetchOutcome(UNPARSEABLE, detail=f"could not parse flight data: {type(exc).__name__}: {exc}")


def fetch_with_retries(
    query,
    cfg: Config,
    fetch_html: Callable[[object], str] = fetch_flights_html,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchOutcome:
    attempt = 0
    while True:
        attempt += 1
        outcome = fetch_once(query, fetch_html)
        outcome.attempts = attempt
        if outcome.kind not in RETRYABLE or attempt > cfg.max_retries:
            return outcome
        wait = cfg.backoff_seconds * 2 ** (attempt - 1) + random.uniform(0, 5)
        log.warning("attempt %d failed (%s: %s); retrying in %.0fs", attempt, outcome.kind, outcome.detail, wait)
        sleep(wait)
