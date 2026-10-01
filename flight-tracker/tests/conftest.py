"""Small hand-built fixtures. These are NOT real Google results; they only
exercise our own logic using the fast_flights model classes."""

import sys
from datetime import date
from pathlib import Path

import pytest
from fast_flights.model import Airport, CarbonEmission, Flights, SimpleDatetime, SingleFlight

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tracker.config import load_config  # noqa: E402
from tracker.filters import LegSpec  # noqa: E402

NAMES = {"SIN": "Singapore Changi", "BKK": "Suvarnabhumi", "DMK": "Don Mueang", "KUL": "Kuala Lumpur"}


def seg(frm, to, dep, arr, duration):
    """dep/arr as (y, m, d, hh, mm) local times."""
    return SingleFlight(
        from_airport=Airport(code=frm, name=NAMES[frm]),
        to_airport=Airport(code=to, name=NAMES[to]),
        departure=SimpleDatetime(date=dep[:3], time=dep[3:]),
        arrival=SimpleDatetime(date=arr[:3], time=arr[3:]),
        duration=duration,
        plane_type="A320",
    )


def itinerary(price, segments, airline="Scoot", code="TR"):
    return Flights(type=code, price=price, airlines=[airline], flights=segments,
                   carbon=CarbonEmission(typical_on_route=0, emission=0))


def sin_bkk(price, hh, mm, airline="Scoot", day=(2026, 10, 2), to="BKK", duration=145):
    """Fri SIN->BKK nonstop. SIN is UTC+8, BKK UTC+7, so local arrival = dep + duration - 1h."""
    from datetime import datetime, timedelta
    dep = datetime(*day, hh, mm)
    arr = dep + timedelta(minutes=duration) - timedelta(hours=1)
    return itinerary(price, [seg("SIN", to, (*day, hh, mm), (arr.year, arr.month, arr.day, arr.hour, arr.minute),
                                 duration)], airline)


def bkk_sin(price, hh, mm, airline="Thai AirAsia", day=(2026, 10, 4), duration=140):
    from datetime import datetime, timedelta
    dep = datetime(*day, hh, mm)
    arr = dep + timedelta(minutes=duration) + timedelta(hours=1)
    return itinerary(price, [seg("BKK", "SIN", (*day, hh, mm), (arr.year, arr.month, arr.day, arr.hour, arr.minute),
                                 duration)], airline)


@pytest.fixture
def cfg(tmp_path):
    text = (ROOT / "config.toml").read_text()
    p = tmp_path / "config.toml"
    p.write_text(text)
    return load_config(p)


@pytest.fixture
def out_spec(cfg):
    return LegSpec("outbound", date(2026, 10, 2), "SIN", "BKK", cfg.origin_tz, cfg.destination_tz,
                   cfg.outbound_earliest, True, cfg.min_leg_price, cfg.max_leg_price, cfg.duration_tolerance_minutes)


@pytest.fixture
def ret_spec(cfg):
    return LegSpec("return", date(2026, 10, 4), "BKK", "SIN", cfg.destination_tz, cfg.origin_tz,
                   cfg.return_earliest, True, cfg.min_leg_price, cfg.max_leg_price, cfg.duration_tolerance_minutes)
