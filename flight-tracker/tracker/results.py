"""Per-run results, the CSV history, and comparison with the previous run."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .filters import Quote
from .weekends import Weekend

OK, NO_FLIGHTS, FAILED = "ok", "no_qualifying_flights", "fetch_failed"
PRICING_ONE_WAY = "one-way"

FIELDS = [
    "run_timestamp_utc", "weekend_friday", "weekend_sunday", "leg", "travel_date",
    "status", "airline", "departure_local", "arrival_local", "price", "currency",
    "pricing_type", "detail",
]


@dataclass
class LegResult:
    leg: str  # "outbound" / "return"
    travel_date: date
    status: str  # OK / NO_FLIGHTS / FAILED
    quote: Quote | None = None
    detail: str = ""
    skipped: int = 0  # results Google sent that we couldn't read


@dataclass
class WeekendResult:
    weekend: Weekend
    outbound: LegResult
    ret: LegResult

    @property
    def total(self) -> int | None:
        if self.outbound.status == OK and self.ret.status == OK:
            return self.outbound.quote.price + self.ret.quote.price
        return None


def history_rows(run_ts: datetime, results: list[WeekendResult], currency: str) -> list[dict]:
    rows = []
    for wr in results:
        for lr in (wr.outbound, wr.ret):
            q = lr.quote
            rows.append({
                "run_timestamp_utc": run_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "weekend_friday": wr.weekend.friday.isoformat(),
                "weekend_sunday": wr.weekend.sunday.isoformat(),
                "leg": lr.leg,
                "travel_date": lr.travel_date.isoformat(),
                "status": lr.status,
                "airline": q.airline if q else "",
                "departure_local": q.departure.strftime("%Y-%m-%d %H:%M") if q else "",
                "arrival_local": q.arrival.strftime("%Y-%m-%d %H:%M") if q else "",
                "price": q.price if q else "",
                "currency": currency,
                "pricing_type": PRICING_ONE_WAY if q else "",
                "detail": lr.detail,
            })
    return rows


def append_history(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def previous_totals(path: Path, before: datetime) -> tuple[str | None, dict[str, int | None]]:
    """Weekend totals from the latest run before `before`.

    Returns (run timestamp or None, {friday_iso: total or None}). A weekend's
    total is None unless both legs were OK in that run.
    """
    if not path.exists():
        return None, {}
    cutoff = before.strftime("%Y-%m-%dT%H:%M:%SZ")
    with path.open(newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["run_timestamp_utc"] < cutoff]
    if not rows:
        return None, {}
    last = max(r["run_timestamp_utc"] for r in rows)
    legs: dict[str, dict[str, int | None]] = {}
    for r in rows:
        if r["run_timestamp_utc"] != last:
            continue
        price = int(r["price"]) if r["status"] == OK and r["price"] else None
        legs.setdefault(r["weekend_friday"], {})[r["leg"]] = price
    totals = {}
    for fri, by_leg in legs.items():
        o, rt = by_leg.get("outbound"), by_leg.get("return")
        totals[fri] = o + rt if o is not None and rt is not None else None
    return last, totals
