"""End-to-end logic with a fake fetcher (no network)."""

import csv
from datetime import date, datetime, timezone

from conftest import bkk_sin, sin_bkk

from tracker.__main__ import run
from tracker.fetch import BLOCKED, OK, UNPARSEABLE, FetchOutcome
from tracker.results import FAILED, NO_FLIGHTS, append_history, history_rows, previous_totals
from tracker.summary import alerts, render
from tracker import telegram
from tracker.config import Scan
from tracker.results import NOT_ON_SALE


def upcoming(cfg, n):
    return Scan("upcoming", "next weekends", cfg.scans["upcoming"].history_csv, cfg.scans["upcoming"].summary_md,
                weekends=n)

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
        return FetchOutcome(out.kind, out.flights, out.detail, attempts=1, skipped=out.skipped,
                            unpriced=out.unpriced)

    fetch.calls = calls
    return fetch


def no_sleep(_):
    pass


def ok(*flights):
    return FetchOutcome(OK, flights=list(flights))


def test_run_builds_weekends_and_statuses(cfg):
    scan = upcoming(cfg, 3)
    fetch = fake_fetch({
        ("2026-10-02", "SIN"): ok(sin_bkk(130, 19, 0), sin_bkk(99, 12, 0)),
        ("2026-10-04", "BKK"): ok(bkk_sin(140, 21, 0)),
        ("2026-10-09", "SIN"): ok(sin_bkk(100, 17, 0, day=(2026, 10, 9))),  # only an early flight
        ("2026-10-11", "BKK"): ok(bkk_sin(90, 22, 0, day=(2026, 10, 11))),
        # weekend 3 outbound: unparseable (default)
        ("2026-10-18", "BKK"): ok(bkk_sin(95, 20, 0, day=(2026, 10, 18))),
    })
    results, n = run(cfg, scan, RUN1, fetch=fetch, sleep=no_sleep)
    assert n == 6 and len(fetch.calls) == 6
    assert [r.weekend.friday for r in results] == [date(2026, 10, 2), date(2026, 10, 9), date(2026, 10, 16)]
    assert results[0].total == 270
    assert results[1].outbound.status == NO_FLIGHTS and results[1].total is None
    assert "1 departs before 18:00" in results[1].outbound.detail
    assert results[2].outbound.status == FAILED and results[2].total is None

    md = render(cfg, scan, RUN1, results, None, {}, n)
    assert md.index("Fri 02 Oct") < md.index("Fri 09 Oct") < md.index("Fri 16 Oct")
    row1 = next(line for line in md.splitlines() if line.startswith("|") and "Fri 02 Oct" in line)
    assert row1.startswith("| ★ |") and "**SGD 270**" in row1 and "first run" in row1
    assert "— no qualifying flights" in md
    assert "❌ fetch failed: unreadable response (no fixture)" in md
    assert md.count("★") == 2  # the row mark + the legend


def test_block_stops_further_requests(cfg):
    scan = upcoming(cfg, 3)
    fetch = fake_fetch({
        ("2026-10-02", "SIN"): ok(sin_bkk(130, 19, 0)),
        ("2026-10-04", "BKK"): FetchOutcome(BLOCKED, detail="captcha"),
    })
    results, n = run(cfg, scan, RUN1, fetch=fetch, sleep=no_sleep)
    assert n == 2
    later = [lr for r in results[1:] for lr in (r.outbound, r.ret)]
    assert all(lr.status == FAILED and "not attempted" in lr.detail for lr in later)
    assert results[0].total is None  # outbound priced, return blocked -> no total


def test_history_and_change_vs_previous_run(cfg, tmp_path):
    scan = upcoming(cfg, 2)
    path = tmp_path / "history.csv"

    r1, _ = run(cfg, scan, RUN1, sleep=no_sleep, fetch=fake_fetch({
        ("2026-10-02", "SIN"): ok(sin_bkk(150, 19, 0)),
        ("2026-10-04", "BKK"): ok(bkk_sin(150, 21, 0)),
        ("2026-10-09", "SIN"): ok(sin_bkk(150, 19, 0, day=(2026, 10, 9))),
        ("2026-10-11", "BKK"): ok(bkk_sin(150, 21, 0, day=(2026, 10, 11))),
    }))
    append_history(path, history_rows(RUN1, r1, "SGD"))

    r2, _ = run(cfg, scan, RUN2, sleep=no_sleep, fetch=fake_fetch({
        ("2026-10-02", "SIN"): ok(sin_bkk(120, 19, 0)),
        ("2026-10-04", "BKK"): ok(bkk_sin(140, 21, 0)),
        # weekend 2 outbound fails this time
        ("2026-10-11", "BKK"): ok(bkk_sin(150, 21, 0, day=(2026, 10, 11))),
    }))
    prev_run, prev = previous_totals(path, RUN2)
    assert prev_run == "2026-10-01T01:00:00Z" and prev == {"2026-10-02": 300, "2026-10-09": 300}

    md = render(cfg, scan, RUN2, r2, prev_run, prev, 4)
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
    cfg, scan = cfg.__class__(**{**cfg.__dict__, "alert_total_below": 200.0}), upcoming(cfg, 3)
    results, _ = run(cfg, scan, RUN1, sleep=no_sleep, fetch=fake_fetch({
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
    scan = upcoming(cfg, 1)
    from tracker.fetch import NETWORK
    nasty = "RequestError: proxy CONNECT failed | HTTP/1.1 403\nContent-Type: text/plain\n" + "x" * 500
    results, n = run(cfg, scan, RUN1, sleep=no_sleep, fetch=fake_fetch({
        ("2026-10-02", "SIN"): FetchOutcome(NETWORK, detail=nasty),
    }))
    md = render(cfg, scan, RUN1, results, None, {}, n)
    rows = [line for line in md.splitlines() if line.startswith("|") and "Oct" in line]
    assert len(rows) == 1 and rows[0].count("|") == 7
    assert "network error" in rows[0]


def test_skipped_results_are_flagged(cfg):
    scan = upcoming(cfg, 1)
    partial = FetchOutcome(OK, flights=[sin_bkk(130, 19, 0)], skipped=2)
    results, n = run(cfg, scan, RUN1, sleep=no_sleep, fetch=fake_fetch({
        ("2026-10-02", "SIN"): partial,
        ("2026-10-04", "BKK"): ok(bkk_sin(140, 21, 0)),
    }))
    lr = results[0].outbound
    assert lr.skipped == 2 and "2 unreadable result(s) skipped" in lr.detail
    assert results[0].total == 270  # still priced, but flagged
    md = render(cfg, scan, RUN1, results, None, {}, n)
    row = next(line for line in md.splitlines() if line.startswith("|") and "Oct" in line)
    assert "SGD 130 · Scoot · 19:00→20:25 ⚠ 2 unreadable" in row
    assert "⚠ 2 unreadable" not in row.split("|")[4]  # return leg not flagged


def test_year_scan_skips_past_and_not_on_sale_weekends(cfg):
    from datetime import date as d
    scan = cfg.scans["year2027"]
    now = datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)  # booking window ends 2027-08-27
    fetch = fake_fetch({
        ("2027-01-01", "SIN"): ok(sin_bkk(150, 19, 0, day=(2027, 1, 1))),
        ("2027-01-03", "BKK"): ok(bkk_sin(160, 21, 0, day=(2027, 1, 3))),
    })
    results, n = run(cfg, scan, now, fetch=fetch, sleep=no_sleep)
    assert len(results) == 53  # 2027 has 53 Fridays (1 Jan and 31 Dec)
    assert results[0].weekend.friday == d(2027, 1, 1) and results[-1].weekend.sunday == d(2028, 1, 2)
    # Fri 27 Aug 2027 is exactly 330 days out: searched. Its Sunday is not.
    aug27 = next(r for r in results if r.weekend.friday == d(2027, 8, 27))
    assert aug27.outbound.status != NOT_ON_SALE and aug27.ret.status == NOT_ON_SALE
    assert n == len(fetch.calls) == 35 + 34  # only on-sale legs were requested
    assert all(r.outbound.status == r.ret.status == NOT_ON_SALE for r in results if r.weekend.friday > d(2027, 8, 27))

    md = render(cfg, scan, now, results, None, {}, n)
    assert md.startswith("# SIN → BKK weekend fares: 2027")
    assert "69 requests · 67 of 69 searched legs failed · 37 legs not on sale yet" in md
    dec = next(line for line in md.splitlines() if line.startswith("|") and "Fri 31 Dec" in line)
    assert dec.count("⏳ not on sale yet") == 2 and dec.rstrip().endswith("| — | — |")

    # Run again in mid-2027: past weekends are gone, later ones come on sale.
    later = datetime(2027, 6, 30, 1, 0, tzinfo=timezone.utc)
    results2, _ = run(cfg, scan, later, fetch=fake_fetch({}), sleep=no_sleep)
    assert results2[0].weekend.friday == d(2027, 7, 2)
    assert not any(lr.status == NOT_ON_SALE for r in results2 for lr in (r.outbound, r.ret))


def test_main_exit_code_ignores_not_on_sale_legs(cfg, tmp_path, monkeypatch):
    import tracker.__main__ as m
    monkeypatch.setattr(m, "run", lambda cfg, scan, now: ([], 0))
    cfg_path = tmp_path / "config.toml"
    assert m.main(["x", str(cfg_path), "--scan", "year2027"]) == 0  # nothing searched is not a failure
    assert (tmp_path / "SUMMARY-2027.md").exists()


def test_unpriced_flights_are_noted_without_a_warning(cfg):
    scan = upcoming(cfg, 1)
    results, n = run(cfg, scan, RUN1, sleep=no_sleep, fetch=fake_fetch({
        ("2026-10-02", "SIN"): FetchOutcome(OK, flights=[sin_bkk(130, 19, 0)], unpriced=7),
        ("2026-10-04", "BKK"): FetchOutcome(OK, flights=[], unpriced=3),
    }))
    out, ret = results[0].outbound, results[0].ret
    assert out.skipped == 0 and "7 flight(s) with no price shown" in out.detail
    assert ret.status == "no_qualifying_flights" and "3 flight(s) with no price shown" in ret.detail
    md = render(cfg, scan, RUN1, results, None, {}, n)
    row = next(line for line in md.splitlines() if line.startswith("|") and "Oct" in line)
    assert "⚠" not in row and "SGD 130 · Scoot · 19:00→20:25 |" in row
