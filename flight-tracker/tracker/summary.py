"""Render SUMMARY.md and work out alerts."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Config
from .results import FAILED, NO_FLIGHTS, OK, LegResult, WeekendResult


def money(cfg: Config, amount: float) -> str:
    return f"{cfg.currency} {amount:,.0f}"


FAILURE_LABELS = {
    "consent_page": "Google consent page",
    "blocked": "blocked by Google",
    "google_error": "Google returned an error",
    "unparseable": "unreadable response",
    "network_error": "network error",
}


def cell(text: str, limit: int = 90) -> str:
    """One line, no table-breaking pipes, bounded length (full text is in the CSV)."""
    text = " ".join(text.split()).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def leg_cell(cfg: Config, lr: LegResult) -> str:
    return cell(_leg_cell(cfg, lr), limit=140)


def _leg_cell(cfg: Config, lr: LegResult) -> str:
    if lr.status == OK:
        q = lr.quote
        arr = q.arrival.strftime("%H:%M")
        if q.arrival.date() > q.departure.date():
            arr += "+1"
        return f"{money(cfg, q.price)} · {q.airline} · {q.departure:%H:%M}→{arr}"
    if lr.status == NO_FLIGHTS:
        return f"— no qualifying flights ({lr.detail})" if lr.detail else "— no qualifying flights"
    kind, _, rest = lr.detail.partition(": ")
    if kind in FAILURE_LABELS:
        return f"❌ fetch failed: {FAILURE_LABELS[kind]} ({cell(rest, 60)})"
    return f"❌ fetch failed: {cell(lr.detail, 80)}"


def change_cell(cfg: Config, current: int | None, prev_run: str | None, prev: dict, friday: str) -> str:
    if current is None:
        return "—"
    if prev_run is None:
        return "first run"
    before = prev.get(friday)
    if before is None:
        return "new" if friday not in prev else "no total last run"
    diff = current - before
    if diff == 0:
        return "no change"
    arrow = "▼" if diff < 0 else "▲"
    return f"{arrow} {money(cfg, abs(diff))} ({diff / before:+.1%})"


def cheapest_weekend(results: list[WeekendResult]) -> WeekendResult | None:
    complete = [r for r in results if r.total is not None]
    return min(complete, key=lambda r: (r.total, r.weekend.friday)) if complete else None


def render(cfg: Config, run_ts: datetime, results: list[WeekendResult],
           prev_run: str | None, prev: dict, requests_made: int) -> str:
    local = run_ts.astimezone(cfg.origin_tz)
    tz_label = cfg.origin_tz.key.split("/")[-1].replace("_", " ") + " time"
    best = cheapest_weekend(results)
    failed = sum(1 for r in results for lr in (r.outbound, r.ret) if lr.status == FAILED)
    stops = "nonstop" if cfg.nonstop_only else "any stops"
    lines = [
        f"# {cfg.origin} → {cfg.destination} weekend fares",
        "",
        f"**Last run:** {local:%a %d %b %Y %H:%M} {tz_label} ({run_ts:%H:%M} UTC) · "
        f"{requests_made} requests · {failed} of {2 * len(results)} legs failed",
        "",
        f"Outbound: Friday {cfg.origin}→{cfg.destination}, departing {cfg.outbound_earliest:%H:%M} or later "
        f"({cfg.origin} local). Return: Sunday {cfg.destination}→{cfg.origin}, departing "
        f"{cfg.return_earliest:%H:%M} or later ({cfg.destination} local). {cfg.adults} adult, {cfg.seat}, {stops}. "
        f"Prices are the cheapest one-way fare per leg, summed (pricing type: 2 × one-way).",
        "",
        "Every price below comes from this run. If a leg failed or had no qualifying flights, "
        "no older price is shown in its place.",
        "",
        "| | Weekend | Outbound (Fri) | Return (Sun) | Total | vs last run |",
        "|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda r: r.weekend.friday):
        fri, sun = r.weekend.friday, r.weekend.sunday
        mark = "★" if best is r else ""
        total = f"**{money(cfg, r.total)}**" if r.total is not None else "—"
        lines.append(
            f"| {mark} | {fri:%a %d %b} – {sun:%a %d %b} | {leg_cell(cfg, r.outbound)} | "
            f"{leg_cell(cfg, r.ret)} | {total} | "
            f"{change_cell(cfg, r.total, prev_run, prev, fri.isoformat())} |"
        )
    lines += [
        "",
        f"★ = cheapest weekend with both legs priced. Times are local at each airport; "
        f"+1 = arrives next day. Previous run: {prev_run or 'none'}.",
        "",
        "Book on the airline's own site and check the price there; Google's fare can differ.",
        "",
    ]
    return "\n".join(lines)


def alerts(cfg: Config, results: list[WeekendResult], prev: dict) -> list[str]:
    msgs = []
    for r in sorted(results, key=lambda r: r.weekend.friday):
        if r.total is None:
            continue
        reasons = []
        if r.total < cfg.alert_total_below:
            reasons.append(f"below {money(cfg, cfg.alert_total_below)}")
        before = prev.get(r.weekend.friday.isoformat())
        if before and (before - r.total) / before * 100 > cfg.alert_drop_percent:
            reasons.append(f"down {(before - r.total) / before:.0%} from {money(cfg, before)}")
        if reasons:
            o, rt = r.outbound.quote, r.ret.quote
            msgs.append(
                f"{r.weekend.friday:%a %d %b} – {r.weekend.sunday:%a %d %b}: {money(cfg, r.total)} "
                f"({', '.join(reasons)})\n"
                f"  Out {o.departure:%H:%M} {o.airline} {money(cfg, o.price)} · "
                f"Back {rt.departure:%H:%M} {rt.airline} {money(cfg, rt.price)}"
            )
    return msgs
