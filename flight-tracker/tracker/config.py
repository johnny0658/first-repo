"""Load and validate config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo


def parse_hhmm(value: str) -> time:
    """Parse a strict 24-hour "HH:MM" string."""
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() and len(p) == 2 for p in parts):
        raise ValueError(f"expected HH:MM, got {value!r}")
    return time(int(parts[0]), int(parts[1]))  # raises on 24:00, 18:60 etc.


@dataclass(frozen=True)
class Config:
    origin: str
    destination: str
    origin_tz: ZoneInfo
    destination_tz: ZoneInfo
    outbound_earliest: time
    return_earliest: time
    weekends: int

    adults: int
    seat: str
    nonstop_only: bool
    currency: str
    language: str

    min_leg_price: int
    max_leg_price: int
    duration_tolerance_minutes: int

    min_delay_seconds: float
    max_delay_seconds: float
    max_retries: int
    backoff_seconds: float

    alert_total_below: float
    alert_drop_percent: float

    history_csv: Path
    summary_md: Path


def load_config(path: Path) -> Config:
    raw = tomllib.loads(path.read_text())
    base = path.parent
    t, s, sa, sc, a, o = (raw[k] for k in ("trip", "search", "sanity", "scraping", "alerts", "output"))
    cfg = Config(
        origin=t["origin"].upper(),
        destination=t["destination"].upper(),
        origin_tz=ZoneInfo(t["origin_timezone"]),
        destination_tz=ZoneInfo(t["destination_timezone"]),
        outbound_earliest=parse_hhmm(t["outbound_earliest_departure"]),
        return_earliest=parse_hhmm(t["return_earliest_departure"]),
        weekends=int(t["weekends"]),
        adults=int(s["adults"]),
        seat=s["seat"],
        nonstop_only=bool(s["nonstop_only"]),
        currency=s["currency"].upper(),
        language=s["language"],
        min_leg_price=int(sa["min_leg_price"]),
        max_leg_price=int(sa["max_leg_price"]),
        duration_tolerance_minutes=int(sa["duration_tolerance_minutes"]),
        min_delay_seconds=float(sc["min_delay_seconds"]),
        max_delay_seconds=float(sc["max_delay_seconds"]),
        max_retries=int(sc["max_retries"]),
        backoff_seconds=float(sc["backoff_seconds"]),
        alert_total_below=float(a["total_below"]),
        alert_drop_percent=float(a["drop_percent"]),
        history_csv=base / o["history_csv"],
        summary_md=base / o["summary_md"],
    )
    if not 1 <= cfg.weekends <= 26:
        raise ValueError("weekends must be between 1 and 26")
    if cfg.min_delay_seconds > cfg.max_delay_seconds:
        raise ValueError("min_delay_seconds must not exceed max_delay_seconds")
    return cfg
