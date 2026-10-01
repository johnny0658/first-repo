"""Page classification, parsing wrapper and retries, without the network.

The HTML/JS below is SYNTHETIC: tiny strings shaped after what
fast_flights/parser.py (v3.1.0) reads, not captured Google responses.
"""

import json

from tracker.fetch import (BLOCKED, CONSENT, DEBUG_DIR_ENV, GOOGLE_ERROR, NETWORK, OK, PARSE_ERROR, UNPARSEABLE,
                           classify_html, fetch_once, fetch_with_retries, parse_flights)


def leg(dep_time, price):
    seg = [None] * 22
    seg[3], seg[4], seg[5], seg[6] = "SIN", "Changi", "Suvarnabhumi", "BKK"
    seg[8], seg[10], seg[11], seg[17] = dep_time, [21, 25], 145, "A320"
    seg[20], seg[21] = [2026, 10, 2], [2026, 10, 2]
    flight = [None] * 23
    flight[0], flight[1], flight[2] = "TR", ["Scoot"], [seg]
    flight[22] = [None] * 9
    return [flight, [[None, price]]]


def page(best, other):
    payload = [None] * 8
    payload[2] = [best] if best is not None else None
    payload[3] = [other]
    payload[7] = [None, [[], [["TR", "Scoot"]]]]
    js = "AF_initDataCallback({key: 'ds:1', hash: '1', data:" + json.dumps(payload) + ", sideChannel: {}});"
    return f'<html><script class="ds:1">{js}</script></html>', js


def test_classify_ok():
    html, js = page(None, [leg([19], 120)])
    assert classify_html(html) == (OK, js)


def test_classify_failures():
    assert classify_html("")[0] == UNPARSEABLE
    assert classify_html('<form action="https://consent.google.com/save">Before you continue</form>')[0] == CONSENT
    assert classify_html("<p>Our systems have detected unusual traffic</p>")[0] == BLOCKED
    assert classify_html("<html><body>new layout</body></html>")[0] == UNPARSEABLE


def test_parse_merges_best_and_other_sections_and_dedupes():
    _, js = page([leg([19], 120), leg([20, 30], 150)], [leg([19], 120), leg([22], 99)])
    flights, stats = parse_flights(js)
    assert sorted((f.price, f.flights[0].departure.time) for f in flights) == [(99, (22, 0)), (120, (19, 0)),
                                                                                (150, (20, 30))]
    assert stats.per_section == {"other": 2, "best": 2} and stats.duplicates == 1 and stats.skipped == 0


def test_one_bad_result_is_skipped_not_fatal():
    bad = leg([21], 80)
    bad[1] = []  # e.g. a result without a price: the library's k[1][0][1] raises IndexError
    _, js = page(None, [leg([19], 120), bad, leg([22], 99)])
    flights, stats = parse_flights(js)
    assert sorted(f.price for f in flights) == [99, 120] and stats.skipped == 1


def test_unparseable_best_section_items_are_skipped():
    _, js = page([["garbage"]], [leg([22], 99)])
    flights, stats = parse_flights(js)
    assert [f.price for f in flights] == [99] and stats.skipped == 1


def test_empty_other_section_with_best_results():
    # payload[3] == [] made fast-flights 3.1.0 raise IndexError on payload[3][0]
    _, js = page([leg([19], 120)], None)
    payload = json.loads(js.split("data:", 1)[1].rsplit(",", 1)[0])
    payload[3] = []
    js = "x({data:" + json.dumps(payload) + ", y})"
    flights, stats = parse_flights(js)
    assert [f.price for f in flights] == [120] and stats.per_section == {"other": 0, "best": 1}


def test_broken_airline_table_does_not_matter():
    _, js = page(None, [leg([19], 120)])
    payload = json.loads(js.split("data:", 1)[1].rsplit(",", 1)[0])
    payload[7] = None
    flights, _ = parse_flights("x({data:" + json.dumps(payload) + ", y})")
    assert [f.price for f in flights] == [120]


def test_no_results_at_all_is_not_an_error():
    _, js = page(None, None)
    assert parse_flights(js)[0] == []


def test_all_results_unreadable_is_a_parse_error(cfg):
    bad = leg([21], 80)
    bad[1] = []
    html, _ = page(None, [bad])
    calls = []

    def f(q):
        calls.append(q)
        return html
    out = fetch_with_retries("q", cfg, fetch_html=f, sleep=lambda s: None)
    assert out.kind == PARSE_ERROR and len(calls) == 1  # deterministic: not retried


def test_debug_page_saved_on_failure_and_skips(tmp_path, monkeypatch):
    monkeypatch.setenv(DEBUG_DIR_ENV, str(tmp_path))
    bad = leg([21], 80)
    bad[1] = []
    partial, _ = page(None, [leg([19], 120), bad])
    out = fetch_once(None, lambda q: partial, debug_name="2026-10-02_outbound")
    assert out.kind == OK and out.skipped == 1
    assert (tmp_path / "2026-10-02_outbound.html").read_text() == partial
    fetch_once(None, lambda q: "<p>unusual traffic</p>", debug_name="x/../y")
    assert (tmp_path / "x_.._y.html").exists()
    good, _ = page(None, [leg([19], 120)])
    fetch_once(None, lambda q: good, debug_name="ok")
    assert not (tmp_path / "ok.html").exists()


def test_fetch_once_outcomes():
    html, _ = page(None, [leg([19], 120)])
    assert fetch_once(None, lambda q: html).kind == OK
    assert fetch_once(None, lambda q: "<html>data:errorHasStatus</html>").kind == UNPARSEABLE
    err = '<script class="ds:1">x({data: errorHasStatus: true, y})</script>'
    assert fetch_once(None, lambda q: err).kind == GOOGLE_ERROR

    def down(q):
        raise ConnectionError("reset")
    assert fetch_once(None, down).kind == NETWORK


def test_retries_then_gives_up(cfg):
    calls, waits = [], []

    def flaky(q):
        calls.append(q)
        raise ConnectionError("reset")
    out = fetch_with_retries("q", cfg, fetch_html=flaky, sleep=waits.append)
    assert out.kind == NETWORK and out.attempts == cfg.max_retries + 1 == len(calls)
    assert len(waits) == cfg.max_retries and waits[1] > waits[0] >= cfg.backoff_seconds


def test_no_retry_when_blocked(cfg):
    calls = []

    def blocked(q):
        calls.append(q)
        return "<p>unusual traffic</p>"
    assert fetch_with_retries("q", cfg, fetch_html=blocked, sleep=lambda s: None).kind == BLOCKED
    assert len(calls) == 1


def test_retry_recovers(cfg):
    html, _ = page(None, [leg([19], 120)])
    seq = iter([ConnectionError("x"), html])

    def f(q):
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v
    out = fetch_with_retries("q", cfg, fetch_html=f, sleep=lambda s: None)
    assert out.kind == OK and out.attempts == 2 and out.flights[0].price == 120
