"""Decide which flights qualify, and pick the cheapest.

Everything here works on fast_flights model objects (fast_flights.model.Flights),
so it can be tested without the network. The library's own query filters are
also sent to Google, but nothing returned is trusted until it passes these checks.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fast_flights.model import Flights, SimpleDatetime


@dataclass(frozen=True)
class LegSpec:
    """What one leg of the weekend must look like."""

    leg: str  # "outbound" or "return"
    travel_date: date
    origin: str
    destination: str
    origin_tz: ZoneInfo
    destination_tz: ZoneInfo
    earliest_departure: time  # inclusive, origin local time
    nonstop_only: bool
    min_price: int
    max_price: int
    duration_tolerance_minutes: int


@dataclass(frozen=True)
class Quote:
    airline: str
    departure: datetime  # naive, origin local time
    arrival: datetime  # naive, destination local time
    price: int


def to_local_datetime(value: SimpleDatetime) -> datetime:
    """Turn the library's (y, m, d) + (h, m) tuples into a naive local datetime.

    fast-flights reports Google's wall-clock time at the airport itself, with no
    time zone attached. Raises ValueError/TypeError on malformed tuples.
    """
    y, m, d = value.date
    hh, mm = value.time
    return datetime(int(y), int(m), int(d), int(hh), int(mm))


def evaluate(flight: Flights, spec: LegSpec) -> Quote | str:
    """Return a Quote if the itinerary qualifies, else a short rejection reason."""
    segments = flight.flights
    if not segments:
        return "no segments"
    if spec.nonstop_only and len(segments) != 1:
        return "not nonstop"
    first, last = segments[0], segments[-1]
    if first.from_airport.code != spec.origin:
        return f"departs {first.from_airport.code}, not {spec.origin}"
    if last.to_airport.code != spec.destination:
        return f"arrives {last.to_airport.code}, not {spec.destination}"

    try:
        dep = to_local_datetime(first.departure)
        arr = to_local_datetime(last.arrival)
    except (TypeError, ValueError):
        return "unparseable time"
    if dep.date() != spec.travel_date:
        return "wrong departure date"
    if dep.time() < spec.earliest_departure:
        return f"departs before {spec.earliest_departure:%H:%M}"

    # Cross-check the local times against the reported duration. If the
    # library ever returned times in a different zone, this catches it.
    # Only possible when both ends are airports whose zone we know.
    if len(segments) == 1:
        if not isinstance(first.duration, int) or first.duration <= 0:
            return "cannot verify times (no duration)"
        elapsed = (arr.replace(tzinfo=spec.destination_tz) - dep.replace(tzinfo=spec.origin_tz))
        if abs(elapsed - timedelta(minutes=first.duration)) > timedelta(minutes=spec.duration_tolerance_minutes):
            return "times inconsistent with duration"

    price = flight.price
    if not isinstance(price, int) or isinstance(price, bool):
        return "missing price"
    if not spec.min_price <= price <= spec.max_price:
        return "implausible price"

    airline = " + ".join(flight.airlines) if flight.airlines else (flight.type or "unknown")
    return Quote(airline=airline, departure=dep, arrival=arr, price=price)


def qualifying(flights: list[Flights], spec: LegSpec) -> tuple[list[Quote], Counter[str]]:
    quotes: list[Quote] = []
    rejected: Counter[str] = Counter()
    for f in flights:
        result = evaluate(f, spec)
        if isinstance(result, Quote):
            quotes.append(result)
        else:
            rejected[result] += 1
    return quotes, rejected


def cheapest(quotes: list[Quote]) -> Quote | None:
    """Lowest price; ties go to the earlier departure, then airline name."""
    if not quotes:
        return None
    return min(quotes, key=lambda q: (q.price, q.departure, q.airline))
