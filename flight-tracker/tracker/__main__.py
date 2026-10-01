"""Run the tracker: python -m tracker [path/to/config.toml]"""

from __future__ import annotations

import logging
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import telegram
from .config import Config, load_config
from .fetch import STOP_RUN, build_query, fetch_with_retries
from .filters import LegSpec, cheapest, qualifying
from .results import FAILED, NO_FLIGHTS, OK, LegResult, WeekendResult, append_history, history_rows, previous_totals
from .summary import alerts, render
from .weekends import upcoming_weekends

log = logging.getLogger("tracker")


def leg_specs(cfg: Config, weekend) -> list[LegSpec]:
    common = dict(nonstop_only=cfg.nonstop_only, min_price=cfg.min_leg_price, max_price=cfg.max_leg_price,
                  duration_tolerance_minutes=cfg.duration_tolerance_minutes)
    return [
        LegSpec("outbound", weekend.friday, cfg.origin, cfg.destination, cfg.origin_tz, cfg.destination_tz,
                cfg.outbound_earliest, **common),
        LegSpec("return", weekend.sunday, cfg.destination, cfg.origin, cfg.destination_tz, cfg.origin_tz,
                cfg.return_earliest, **common),
    ]


def describe_rejections(n_results: int, rejected) -> str:
    if not rejected:
        return f"{n_results} results"
    parts = ", ".join(f"{n} {reason}" for reason, n in rejected.most_common())
    return f"{n_results} results, none qualifying: {parts}"


def run(cfg: Config, now: datetime, fetch: Callable = fetch_with_retries,
        sleep: Callable[[float], None] = time.sleep) -> tuple[list[WeekendResult], int]:
    today = now.astimezone(cfg.origin_tz).date()
    results: list[WeekendResult] = []
    requests_made = 0
    stop_reason = None

    for weekend in upcoming_weekends(today, cfg.weekends):
        legs = []
        for spec in leg_specs(cfg, weekend):
            if stop_reason:
                legs.append(LegResult(spec.leg, spec.travel_date, FAILED, detail=f"not attempted ({stop_reason})"))
                continue
            if requests_made:
                sleep(random.uniform(cfg.min_delay_seconds, cfg.max_delay_seconds))
            query = build_query(cfg, spec.travel_date.isoformat(), spec.origin, spec.destination)
            label = f"{spec.leg} {spec.origin}->{spec.destination} {spec.travel_date}"
            outcome = fetch(query, cfg, sleep=sleep, debug_name=f"{spec.travel_date}_{spec.leg}")
            requests_made += outcome.attempts
            if outcome.kind != OK:
                log.error("FAILED %s: %s: %s (after %d attempt(s))", label, outcome.kind, outcome.detail,
                          outcome.attempts)
                legs.append(LegResult(spec.leg, spec.travel_date, FAILED, detail=f"{outcome.kind}: {outcome.detail}"))
                if outcome.kind in STOP_RUN:
                    stop_reason = f"stopped after {outcome.kind} on an earlier request"
                    log.error("Stopping this run: Google is refusing requests (%s).", outcome.kind)
                continue
            quotes, rejected = qualifying(outcome.flights, spec)
            best = cheapest(quotes)
            summary = describe_rejections(len(outcome.flights), rejected)
            if outcome.skipped:
                summary += f"; {outcome.skipped} unreadable result(s) skipped"
            if best is None:
                log.info("%s: no qualifying flight (%s)", label, summary)
                legs.append(LegResult(spec.leg, spec.travel_date, NO_FLIGHTS, detail=summary,
                                      skipped=outcome.skipped))
            else:
                log.info("%s: %s %s %d (%d qualifying of %d)", label, best.airline, f"{best.departure:%H:%M}",
                         best.price, len(quotes), len(outcome.flights))
                detail = f"{len(quotes)} qualifying of {len(outcome.flights)} results"
                if outcome.skipped:
                    detail += f"; {outcome.skipped} unreadable result(s) skipped"
                legs.append(LegResult(spec.leg, spec.travel_date, OK, quote=best, detail=detail,
                                      skipped=outcome.skipped))
        results.append(WeekendResult(weekend, legs[0], legs[1]))
    return results, requests_made


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent / "config.toml")
    now = datetime.now(timezone.utc).replace(microsecond=0)

    prev_run, prev = previous_totals(cfg.history_csv, now)
    results, requests_made = run(cfg, now)

    append_history(cfg.history_csv, history_rows(now, results, cfg.currency))
    cfg.summary_md.write_text(render(cfg, now, results, prev_run, prev, requests_made))

    messages = alerts(cfg, results, prev)
    if messages:
        text = f"✈️ {cfg.origin}→{cfg.destination} weekend fare alert\n\n" + "\n\n".join(messages)
        if telegram.send(text):
            log.info("Telegram alert sent (%d weekend(s))", len(messages))

    legs = [lr for r in results for lr in (r.outbound, r.ret)]
    failed = sum(lr.status == FAILED for lr in legs)
    log.info("Done: %d requests, %d/%d legs failed", requests_made, failed, len(legs))
    # Non-zero exit when every leg failed, so the workflow run shows red.
    return 1 if failed == len(legs) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
