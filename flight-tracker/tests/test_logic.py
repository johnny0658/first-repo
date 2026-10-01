from datetime import date, datetime, time

import pytest
from conftest import bkk_sin, itinerary, seg, sin_bkk
from fast_flights.model import SimpleDatetime

from tracker.config import parse_hhmm
from tracker.filters import Quote, cheapest, evaluate, qualifying, to_local_datetime
from tracker.weekends import upcoming_weekends


# --- Friday-Sunday pairs ------------------------------------------------------

@pytest.mark.parametrize("today, first_friday", [
    (date(2026, 9, 28), date(2026, 10, 2)),  # Monday
    (date(2026, 10, 1), date(2026, 10, 2)),  # Thursday
    (date(2026, 10, 2), date(2026, 10, 2)),  # Friday: this weekend included
    (date(2026, 10, 3), date(2026, 10, 9)),  # Saturday: skip to next
    (date(2026, 10, 4), date(2026, 10, 9)),  # Sunday
])
def test_first_weekend(today, first_friday):
    assert upcoming_weekends(today, 1)[0].friday == first_friday


def test_twelve_consecutive_weekends_across_month_and_year():
    ws = upcoming_weekends(date(2026, 12, 1), 12)
    assert len(ws) == 12
    assert all(w.friday.weekday() == 4 and w.sunday.weekday() == 6 for w in ws)
    assert all((b.friday - a.friday).days == 7 for a, b in zip(ws, ws[1:]))
    assert ws[4].friday == date(2027, 1, 1) and ws[4].sunday == date(2027, 1, 3)


# --- time parsing -------------------------------------------------------------

def test_parse_hhmm():
    assert parse_hhmm("18:00") == time(18, 0)
    assert parse_hhmm(" 20:05 ") == time(20, 5)
    for bad in ["18", "18:0", "6pm", "24:00", "18:60", "-1:00", ""]:
        with pytest.raises(ValueError):
            parse_hhmm(bad)


def test_to_local_datetime():
    assert to_local_datetime(SimpleDatetime(date=(2026, 10, 2), time=(18, 5))) == datetime(2026, 10, 2, 18, 5)
    # fast-flights pads omitted components to zero, e.g. [None, 31] -> (0, 31)
    assert to_local_datetime(SimpleDatetime(date=(2026, 10, 3), time=(0, 31))) == datetime(2026, 10, 3, 0, 31)
    with pytest.raises(ValueError):
        to_local_datetime(SimpleDatetime(date=(2026, 2, 30), time=(10, 0)))


# --- threshold edge cases -----------------------------------------------------

@pytest.mark.parametrize("hh, mm, ok", [(17, 59, False), (18, 0, True), (18, 1, True), (23, 55, True)])
def test_outbound_threshold_is_inclusive(out_spec, hh, mm, ok):
    assert isinstance(evaluate(sin_bkk(120, hh, mm), out_spec), Quote) is ok


@pytest.mark.parametrize("hh, mm, ok", [(19, 59, False), (20, 0, True), (20, 1, True), (23, 59, True)])
def test_return_threshold_is_inclusive(ret_spec, hh, mm, ok):
    assert isinstance(evaluate(bkk_sin(120, hh, mm), ret_spec), Quote) is ok


def test_return_late_flight_arriving_next_day(ret_spec):
    q = evaluate(bkk_sin(150, 23, 30), ret_spec)
    assert q.departure == datetime(2026, 10, 4, 23, 30)
    assert q.arrival == datetime(2026, 10, 5, 2, 50)


# --- airport / stops / date / sanity checks -----------------------------------

def test_rejects_don_mueang(out_spec):
    assert evaluate(sin_bkk(80, 19, 0, to="DMK"), out_spec) == "arrives DMK, not BKK"


def test_rejects_wrong_origin(ret_spec):
    f = itinerary(100, [seg("DMK", "SIN", (2026, 10, 4, 21, 0), (2026, 10, 5, 0, 20), 140)])
    assert evaluate(f, ret_spec) == "departs DMK, not BKK"


def test_rejects_connection(out_spec):
    f = itinerary(90, [seg("SIN", "KUL", (2026, 10, 2, 19, 0), (2026, 10, 2, 20, 0), 60),
                       seg("KUL", "BKK", (2026, 10, 2, 21, 0), (2026, 10, 2, 22, 0), 120)])
    assert evaluate(f, out_spec) == "not nonstop"


def test_rejects_wrong_date(out_spec):
    assert evaluate(sin_bkk(100, 19, 0, day=(2026, 10, 3)), out_spec) == "wrong departure date"


def test_rejects_times_inconsistent_with_duration(out_spec):
    # Arrival shown as if both times were in the same zone: off by an hour.
    f = itinerary(100, [seg("SIN", "BKK", (2026, 10, 2, 19, 0), (2026, 10, 2, 21, 25), 145)])
    assert evaluate(f, out_spec) == "times inconsistent with duration"


@pytest.mark.parametrize("price, reason", [(None, "missing price"), (5, "implausible price"),
                                           (4000, "implausible price")])
def test_rejects_bad_prices(out_spec, price, reason):
    assert evaluate(sin_bkk(price, 19, 0), out_spec) == reason


# --- picking the cheapest -----------------------------------------------------

def test_cheapest_qualifying_ignores_cheaper_non_qualifying(out_spec):
    flights = [
        sin_bkk(60, 17, 30),  # cheapest overall, but too early
        sin_bkk(70, 19, 0, to="DMK"),  # wrong airport
        sin_bkk(140, 21, 0, airline="Singapore Airlines"),
        sin_bkk(110, 18, 0, airline="Scoot"),
    ]
    quotes, rejected = qualifying(flights, out_spec)
    best = cheapest(quotes)
    assert (best.price, best.airline, best.departure.hour) == (110, "Scoot", 18)
    assert rejected == {"departs before 18:00": 1, "arrives DMK, not BKK": 1}


def test_cheapest_tie_goes_to_earlier_flight(out_spec):
    quotes, _ = qualifying([sin_bkk(110, 21, 0, "B"), sin_bkk(110, 19, 0, "A")], out_spec)
    assert cheapest(quotes).departure.hour == 19


def test_cheapest_of_nothing():
    assert cheapest([]) is None


# --- scans / date ranges ------------------------------------------------------

def test_weekends_in_range():
    from tracker.weekends import weekends_in_range
    ws = weekends_in_range(date(2026, 10, 1), date(2027, 1, 1), date(2027, 12, 31))
    assert len(ws) == 53 and ws[0].friday == date(2027, 1, 1) and ws[-1].friday == date(2027, 12, 31)
    # Same "today" rule as upcoming_weekends: Friday included, Saturday not.
    assert weekends_in_range(date(2027, 3, 5), date(2027, 1, 1), date(2027, 12, 31))[0].friday == date(2027, 3, 5)
    assert weekends_in_range(date(2027, 3, 6), date(2027, 1, 1), date(2027, 12, 31))[0].friday == date(2027, 3, 12)
    assert weekends_in_range(date(2028, 1, 1), date(2027, 1, 1), date(2027, 12, 31)) == []


@pytest.mark.parametrize("scan_toml, error", [
    ('weekends = 4\nfirst_friday = 2027-01-01\nlast_friday = 2027-02-01', "either weekends or"),
    ('title = "x"', "either weekends or"),
    ('first_friday = 2027-01-01', "must both be dates"),
    ('first_friday = 2027-03-01\nlast_friday = 2027-02-01', "after last_friday"),
    ('weekends = 40', "between 1 and 26"),
])
def test_bad_scan_config(tmp_path, scan_toml, error):
    from conftest import ROOT
    from tracker.config import load_config
    text = (ROOT / "config.toml").read_text().split("[scans.upcoming]")[0]
    p = tmp_path / "config.toml"
    p.write_text(text + f'[scans.bad]\nhistory_csv = "h.csv"\nsummary_md = "s.md"\n{scan_toml}\n')
    with pytest.raises(ValueError, match=error):
        load_config(p)
