"""Fetch one leg from Google Flights via fast-flights, politely.

We call the library's fetch_flights_html() and parse_js() ourselves instead of
get_flights(), because get_flights() crashes with an AttributeError on consent
or block pages, and its parser gives up on the whole page when one result
doesn't have the expected shape. Doing these steps ourselves lets us say what
went wrong and skip only the bad result.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
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
UNPARSEABLE = "unparseable"  # no flight data in the page at all
PARSE_ERROR = "parse_error"  # flight data present, but we couldn't read it
NETWORK = "network_error"

# These mean Google is refusing us; retrying or continuing would be impolite.
STOP_RUN = {CONSENT, BLOCKED}
# PARSE_ERROR is deliberately absent: the same page would fail the same way.
RETRYABLE = {GOOGLE_ERROR, UNPARSEABLE, NETWORK}

# Google's page data keeps results in two lists: payload[3] is the one
# fast-flights 3.1.0 reads; payload[2] appears to hold the "best flights".
SECTIONS = {"other": 3, "best": 2}

# Pages that failed or had skipped results are saved here when set (the
# workflow uploads them as a run artifact for diagnosis).
DEBUG_DIR_ENV = "TRACKER_DEBUG_DIR"


@dataclass
class ParseStats:
    per_section: dict[str, int] = field(default_factory=dict)
    skipped: int = 0  # results we couldn't read and left out
    duplicates: int = 0

    def describe(self) -> str:
        parts = [f"{n} from '{name}'" for name, n in self.per_section.items()]
        return ", ".join(parts) + f", {self.duplicates} duplicates, {self.skipped} unreadable"


@dataclass
class FetchOutcome:
    kind: str
    flights: list[Flights] = field(default_factory=list)
    detail: str = ""
    attempts: int = 0
    skipped: int = 0


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


def _section_items(payload: list, index: int) -> list:
    """The result list at payload[index][0], or [] if that section is absent/empty."""
    if len(payload) <= index:
        return []
    section = payload[index]
    if not isinstance(section, list) or not section or not isinstance(section[0], list):
        return []
    return section[0]


def _parse_item(payload: list, item) -> Flights:
    """Parse one result with the library's own parse_js.

    We hand parse_js a copy of the page data holding just this result (in the
    slot it reads) and an empty airline/alliance table (it parses that table
    but we never use it), so a problem elsewhere in the page can't fail it.
    """
    single = list(payload) + [None] * max(0, 8 - len(payload))
    single[3] = [[item]]
    single[7] = [None, [[], []]]
    parsed = parse_js("data:" + json.dumps(single) + ",}")
    if len(parsed) != 1:
        raise ValueError(f"expected 1 flight, got {len(parsed)}")
    return parsed[0]


def parse_flights(js: str) -> tuple[list[Flights], ParseStats]:
    """Parse every result in both sections, skipping (and counting) unreadable ones.

    Raises FlightsNotFound when Google flags an error, ValueError when the
    page data can't be read at all or no result in it could be read.
    """
    try:
        data = js.split("data:", 1)[1].rsplit(",", 1)[0]
    except IndexError:
        raise ValueError("no data block in flight script") from None
    if data.endswith("errorHasStatus: true"):  # same check as fast_flights.parser
        raise FlightsNotFound("no flights found; received error")
    payload = json.loads(data)
    if not isinstance(payload, list):
        raise ValueError("flight data is not a list")

    stats, flights, seen = ParseStats(), [], set()
    total_items = 0
    for name, index in SECTIONS.items():
        items = _section_items(payload, index)
        total_items += len(items)
        count = 0
        for item in items:
            try:
                f = _parse_item(payload, item)
            except Exception as exc:  # noqa: BLE001 - library raises Index/Type/KeyError
                stats.skipped += 1
                log.warning("skipped an unreadable result in '%s' (%s: %s)", name, type(exc).__name__, exc)
                continue
            count += 1
            key = (
                f.price,
                tuple(f.airlines or ()),
                tuple((s.from_airport.code, s.to_airport.code, tuple(s.departure.date), tuple(s.departure.time))
                      for s in f.flights),
            )
            if key in seen:
                stats.duplicates += 1
                continue
            seen.add(key)
            flights.append(f)
        stats.per_section[name] = count

    if total_items and not flights:
        raise ValueError(f"none of the {total_items} results could be read")
    return flights, stats


def one_line(text: str, limit: int = 300) -> str:
    """Collapse whitespace so error text fits in one CSV/markdown cell."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def save_debug_page(name: str, html: str) -> None:
    folder = os.environ.get(DEBUG_DIR_ENV)
    if not folder or not name:
        return
    try:
        path = Path(folder) / (re.sub(r"[^A-Za-z0-9_.-]+", "_", name) + ".html")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html)
        log.info("saved page for diagnosis: %s", path)
    except OSError as exc:
        log.warning("could not save debug page (%s)", exc)


def fetch_once(query, fetch_html: Callable[[object], str] = fetch_flights_html,
               debug_name: str = "") -> FetchOutcome:
    try:
        html = fetch_html(query)
    except Exception as exc:  # noqa: BLE001 - primp raises its own error types
        return FetchOutcome(NETWORK, detail=one_line(f"{type(exc).__name__}: {exc}"))
    outcome = _classify_and_parse(html)
    outcome.detail = one_line(outcome.detail)
    if outcome.kind != OK or outcome.skipped:
        save_debug_page(debug_name, html)
    return outcome


def _classify_and_parse(html: str) -> FetchOutcome:
    kind, payload = classify_html(html)
    if kind != OK:
        return FetchOutcome(kind, detail=payload)
    try:
        flights, stats = parse_flights(payload)
    except FlightsNotFound as exc:
        return FetchOutcome(GOOGLE_ERROR, detail=f"Google returned an error status: {exc}")
    except Exception as exc:  # noqa: BLE001
        return FetchOutcome(PARSE_ERROR, detail=f"could not read flight data: {type(exc).__name__}: {exc}")
    log.info("parsed %d results (%s)", len(flights), stats.describe())
    return FetchOutcome(OK, flights=flights, skipped=stats.skipped)


def fetch_with_retries(
    query,
    cfg: Config,
    fetch_html: Callable[[object], str] = fetch_flights_html,
    sleep: Callable[[float], None] = time.sleep,
    debug_name: str = "",
) -> FetchOutcome:
    attempt = 0
    while True:
        attempt += 1
        outcome = fetch_once(query, fetch_html, debug_name)
        outcome.attempts = attempt
        if outcome.kind not in RETRYABLE or attempt > cfg.max_retries:
            return outcome
        wait = cfg.backoff_seconds * 2 ** (attempt - 1) + random.uniform(0, 5)
        log.warning("attempt %d failed (%s: %s); retrying in %.0fs", attempt, outcome.kind, outcome.detail, wait)
        sleep(wait)
