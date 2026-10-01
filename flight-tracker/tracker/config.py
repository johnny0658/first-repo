"""Load and validate config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo


def parse_hhmm(value: str) -> time:
    """Parse a strict 24-hour "HH:MM" string."""
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() and len(p) == 2 for p in parts):
        raise ValueError(f"expected HH:MM, got {value!r}")
    return time(int(parts[0]), int(parts[1]))  # raises on 24:00, 18:60 etc.


@dataclass(frozen=True)
class Scan:
    """One set of weekends with its own summary page and history file.

    Either `weekends` (the next N from today) or `first_friday`/`last_friday`
    (a fixed range of Friday dates) is set.
    """

    name: str
    title: str
    history_csv: Path
    summary_md: Path
    weekends: int | None = None
    first_friday: date | None = None
    last_friday: date | None = None


def _load_scan(name: str, raw: dict, base: Path) -> Scan:
    scan = Scan(
        name=name,
        title=raw.get("title", name),
        history_csv=base / raw["history_csv"],
        summary_md=base / raw["summary_md"],
        weekends=int(raw["weekends"]) if "weekends" in raw else None,
        first_friday=raw.get("first_friday"),
        last_friday=raw.get("last_friday"),
    )
    is_range = scan.first_friday is not None or scan.last_friday is not None
    if (scan.weekends is None) == (not is_range):
        raise ValueError(f"scan {name!r}: set either weekends or first_friday/last_friday")
    if is_range:
        if not (isinstance(scan.first_friday, date) and isinstance(scan.last_friday, date)):
            raise ValueError(f"scan {name!r}: first_friday and last_friday must both be dates (YYYY-MM-DD)")
        if scan.first_friday > scan.last_friday:
            raise ValueError(f"scan {name!r}: first_friday is after last_friday")
    elif not 1 <= scan.weekends <= 26:
        raise ValueError(f"scan {name!r}: weekends must be between 1 and 26")
    return scan


@dataclass(frozen=True)
class Config:
    origin: str
    destination: str
    origin_tz: ZoneInfo
    destination_tz: ZoneInfo
    outbound_earliest: time
    return_earliest: time

    adults: int
    seat: str
    nonstop_only: bool
    currency: str
    language: str

    min_leg_price: int
    max_leg_price: int
    duration_tolerance_minutes: int
    max_days_ahead: int

    min_delay_seconds: float
    max_delay_seconds: float
    max_retries: int
    backoff_seconds: float

    alert_total_below: float
    alert_drop_percent: float

    scans: dict[str, Scan]


def load_config(path: Path) -> Config:
    raw = tomllib.loads(path.read_text())
    base = path.parent
    t, s, sa, sc, a = (raw[k] for k in ("trip", "search", "sanity", "scraping", "alerts"))
    cfg = Config(
        origin=t["origin"].upper(),
        destination=t["destination"].upper(),
        origin_tz=ZoneInfo(t["origin_timezone"]),
        destination_tz=ZoneInfo(t["destination_timezone"]),
        outbound_earliest=parse_hhmm(t["outbound_earliest_departure"]),
        return_earliest=parse_hhmm(t["return_earliest_departure"]),
        adults=int(s["adults"]),
        seat=s["seat"],
        nonstop_only=bool(s["nonstop_only"]),
        currency=s["currency"].upper(),
        language=s["language"],
        min_leg_price=int(sa["min_leg_price"]),
        max_leg_price=int(sa["max_leg_price"]),
        duration_tolerance_minutes=int(sa["duration_tolerance_minutes"]),
        max_days_ahead=int(sa["max_days_ahead"]),
        min_delay_seconds=float(sc["min_delay_seconds"]),
        max_delay_seconds=float(sc["max_delay_seconds"]),
        max_retries=int(sc["max_retries"]),
        backoff_seconds=float(sc["backoff_seconds"]),
        alert_total_below=float(a["total_below"]),
        alert_drop_percent=float(a["drop_percent"]),
        scans={name: _load_scan(name, v, base) for name, v in raw["scans"].items()},
    )
    if not cfg.scans:
        raise ValueError("config needs at least one [scans.<name>] section")
    files = [p for sc_ in cfg.scans.values() for p in (sc_.history_csv, sc_.summary_md)]
    if len(set(files)) != len(files):
        raise ValueError("each scan needs its own history_csv and summary_md")
    if cfg.min_delay_seconds > cfg.max_delay_seconds:
        raise ValueError("min_delay_seconds must not exceed max_delay_seconds")
    return cfg
