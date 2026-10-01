"""End-to-end logic with a fake fetcher (no network)."""

import csv
from datetime import date, datetime, timezone

from conftest import bkk_sin, sin_bkk

from tracker.__main__ import run
from tracker.fetch import BLOCKED, OK, UNPARSEABLE, FetchOutcome
from tracker.results import FAILED, NO_FLIGHTS, append_history, history_rows, previous_totals
from tracker.summary import alerts, render
from tracker import telegram

RUN1 = datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)  # Thu 09:00 SGT
RUN2 = datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc)


def fake_fetch(responses):
    """responses: {(date_iso, origin): FetchOutcome}; default = unparseable."""
    calls = []

    def fetch(query, cfg, sleep, debug_name=""):
        fd = query.flight_data[0]
        key = (fd.date, fd.from_airport.airport)
        calls.append(key)
        out = responses.get(key, FetchOutcome(UNPARSEABLE, detail="no fixture"))
        return FetchOutcome(out.kind, out.flights, out.detail, attempts=1, skipped=out.skipped)

    fetch.calls = calls
    return fetch


def no_sleep(_):
    pass


def ok(*flights):
    return FetchOutcome(OK, flights=list(flights))


def test_run_builds_weekends_and_statuses(cfg):
    cfg = cfg.__class__(**{**cfg.__dict__, "weekends": 3})
    fetch = fake_fetch({
        ("2026-10-02", "SIN"): ok(sin_bkk(130, 19, 0), sin_bkk(99, 12, 0)),
        ("2026-10-04", "BKK"): ok(bkk_sin(140, 21, 0)),
        ("2026-10-09", "SIN"): ok(sin_bkk(100, 17, 0, day=(2026, 10, 9))),  # only an early flight
        ("2026-10-11", "BKK"): ok(bkk_sin(90, 22, 0, day=(2026, 10, 11))),
        # weekend 3 outbound: unparseable (default)
        ("2026-10-18", "BKK"): ok(bkk_sin(95, 20, 0, day=(2026, 10, 18))),
    })
    results, n = run(cfg, RUN1, fetch=fetch, sleep=no_sleep)
    assert n == 6 and len(fetch.calls) == 6
    assert [r.weekend.friday for r in results] == [date(2026, 10, 2), date(2026, 10, 9), date(2026, 10, 16)]
    assert results[0].total == 270
    assert results[1].outbound.status == NO_FLIGHTS and results[1].total is None
    assert "1 departs before 18:00" in results[1].outbound.detail
    assert results[2].outbound.status == FAILED and results[2].total is None

    md = render(cfg, RUN1, results, None, {}, n)
    assert md.index("Fri 02 Oct") < md.index("Fri 09 Oct") < md.index("Fri 16 Oct")
    row1 = next(line for line in md.splitlines() if line.startswith("|") and "Fri 02 Oct" in line)
    assert row1.startswith("| ★ |") and "**SGD 270**" in row1 and "first run" in row1
    assert "— no qualifying flights" in md
    assert "❌ fetch failed: unreadable response (no fixture)" in md
    assert md.count("★") == 2  # the row mark + the legend


def test_block_stops_further_requests(cfg):
    cfg = cfg.__class__(**{**cfg.__dict__, "weekends": 3})
    fetch = fake_fetch({
        ("2026-10-02", "SIN"): ok(sin_bkk(130, 19, 0)),
        ("2026-10-04", "BKK"): FetchOutcome(BLOCKED, detail="captcha"),
    })
    results, n = run(cfg, RUN1, fetch=fetch, sleep=no_sleep)
    assert n == 2
    later = [lr for r in results[1:] for lr in (r.outbound, r.ret)]
    assert all(lr.status == FAILED and "not attempted" in lr.detail for lr in later)
    assert results[0].total is None  # outbound priced, return blocked -> no total


def test_history_and_change_vs_previous_run(cfg, tmp_path):
    cfg = cfg.__class__(**{**cfg.__dict__, "weekends": 2})
    path = tmp_path / "history.csv"

    r1, _ = run(cfg, RUN1, sleep=no_sleep, fetch=fake_fetch({
        ("2026-10-02", "SIN"): ok(sin_bkk(150, 19, 0)),
        ("2026-10-04", "BKK"): ok(bkk_sin(150, 21, 0)),
        ("2026-10-09", "SIN"): ok(sin_bkk(150, 19, 0, day=(2026, 10, 9))),
        ("2026-10-11", "BKK"): ok(bkk_sin(150, 21, 0, day=(2026, 10, 11))),
    }))
    append_history(path, history_rows(RUN1, r1, "SGD"))

    r2, _ = run(cfg, RUN2, sleep=no_sleep, fetch=fake_fetch({
        ("2026-10-02", "SIN"): ok(sin_bkk(120, 19, 0)),
        ("2026-10-04", "BKK"): ok(bkk_sin(140, 21, 0)),
        # weekend 2 outbound fails this time
        ("2026-10-11", "BKK"): ok(bkk_sin(150, 21, 0, day=(2026, 10, 11))),
    }))
    prev_run, prev = previous_totals(path, RUN2)
    assert prev_run == "2026-10-01T01:00:00Z" and prev == {"2026-10-02": 300, "2026-10-09": 300}

    md = render(cfg, RUN2, r2, prev_run, prev, 4)
    row1 = next(line for line in md.splitlines() if line.startswith("|") and "Fri 02 Oct" in line)
    row2 = next(line for line in md.splitlines() if line.startswith("|") and "Fri 09 Oct" in line)
    assert "▼ SGD 40 (-13.3%)" in row1
    assert "SGD 300" not in row2  # the old price is never carried forward
    assert "❌ fetch failed" in row2 and row2.rstrip().endswith("| — | — |")

    append_history(path, history_rows(RUN2, r2, "SGD"))
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 8
    failed = [r for r in rows if r["status"] == FAILED]
    assert len(failed) == 1 and failed[0]["price"] == "" and failed[0]["leg"] == "outbound"
    assert rows[0]["pricing_type"] == "one-way" and rows[0]["departure_local"] == "2026-10-02 19:00"


def test_alerts(cfg):
    cfg = cfg.__class__(**{**cfg.__dict__, "weekends": 3, "alert_total_below": 200.0})
    results, _ = run(cfg, RUN1, sleep=no_sleep, fetch=fake_fetch({
        ("2026-10-02", "SIN"): ok(sin_bkk(90, 19, 0)),   # total 190: below threshold
        ("2026-10-04", "BKK"): ok(bkk_sin(100, 21, 0)),
        ("2026-10-09", "SIN"): ok(sin_bkk(135, 19, 0, day=(2026, 10, 9))),  # 270 vs 300: exactly 10% -> no
        ("2026-10-11", "BKK"): ok(bkk_sin(135, 21, 0, day=(2026, 10, 11))),
        ("2026-10-16", "SIN"): ok(sin_bkk(130, 19, 0, day=(2026, 10, 16))),  # 260 vs 300: 13% -> yes
        ("2026-10-18", "BKK"): ok(bkk_sin(130, 21, 0, day=(2026, 10, 18))),
    }))
    msgs = alerts(cfg, results, {"2026-10-02": 190, "2026-10-09": 300, "2026-10-16": 300})
    assert len(msgs) == 2
    assert "below SGD 200" in msgs[0] and "down" not in msgs[0]
    assert msgs[1].startswith("Fri 16 Oct") and "down 13% from SGD 300" in msgs[1]


def test_telegram_skips_without_secrets():
    def boom(*a, **k):
        raise AssertionError("must not call network")
    assert telegram.send("x", env={}, opener=boom) is False
    assert telegram.send("x", env={"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": ""}, opener=boom) is False


def test_failure_text_cannot_break_the_table(cfg):
    cfg = cfg.__class__(**{**cfg.__dict__, "weekends": 1})
    from tracker.fetch import NETWORK
    nasty = "RequestError: proxy CONNECT failed | HTTP/1.1 403\nContent-Type: text/plain\n" + "x" * 500
    results, n = run(cfg, RUN1, sleep=no_sleep, fetch=fake_fetch({
        ("2026-10-02", "SIN"): FetchOutcome(NETWORK, detail=nasty),
    }))
    md = render(cfg, RUN1, results, None, {}, n)
    rows = [line for line in md.splitlines() if line.startswith("|") and "Oct" in line]
    assert len(rows) == 1 and rows[0].count("|") == 7
    assert "network error" in rows[0]


def test_skipped_results_are_flagged(cfg):
    cfg = cfg.__class__(**{**cfg.__dict__, "weekends": 1})
    partial = FetchOutcome(OK, flights=[sin_bkk(130, 19, 0)], skipped=2)
    results, n = run(cfg, RUN1, sleep=no_sleep, fetch=fake_fetch({
        ("2026-10-02", "SIN"): partial,
        ("2026-10-04", "BKK"): ok(bkk_sin(140, 21, 0)),
    }))
    lr = results[0].outbound
    assert lr.skipped == 2 and "2 unreadable result(s) skipped" in lr.detail
    assert results[0].total == 270  # still priced, but flagged
    md = render(cfg, RUN1, results, None, {}, n)
    row = next(line for line in md.splitlines() if line.startswith("|") and "Oct" in line)
    assert "SGD 130 · Scoot · 19:00→20:25 ⚠ 2 unreadable" in row
    assert "⚠ 2 unreadable" not in row.split("|")[4]  # return leg not flagged
