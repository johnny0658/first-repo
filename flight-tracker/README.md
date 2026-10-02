# SIN → BKK weekend fare tracker

A GitHub Actions job that checks Google Flights for Friday-evening
Singapore → Bangkok (Suvarnabhumi) and Sunday-night return flights, then
writes summaries you can scan in a few seconds. It runs two scans:

| Scan | When | Weekends | Results | History |
|---|---|---|---|---|
| `upcoming` | daily | next 12 | **[SUMMARY.md](SUMMARY.md)** | [data/history.csv](data/history.csv) |
| `year2027` | weekly (Wed) | every weekend with its Friday in 2027 (53) | **[SUMMARY-2027.md](SUMMARY-2027.md)** | [data/history-2027.csv](data/history-2027.csv) |

Each page shows only its own latest run. The weekly page is up to a week
old, and its header says when it ran.

It only tells you where to look. Always book, and check the real price, on the
airline's own site.

## How it works

1. Works out the scan's Friday–Sunday pairs, using today's date in Singapore.
   If today is a Friday, this weekend is included; past weekends are dropped.
2. For each weekend, it makes two one-way searches (outbound Friday, return
   Sunday), with a random 4–9 s pause between them. That's 24 requests for
   `upcoming`. For `year2027`, any leg more than 330 days ahead
   (`max_days_ahead`) shows `⏳ not on sale yet` and isn't searched, because
   airlines haven't released those seats. In October 2026 that means about
   69 requests, rising to 106 as the rest of 2027 comes on sale.
   The searches use [fast-flights](https://github.com/AWeirdDev/flights)
   (pinned to 3.1.0), which scrapes Google Flights without an API key.
3. It re-checks every result in its own code and does not rely on Google's
   filters. A flight counts only if it:
   - is nonstop;
   - departs SIN and arrives BKK (or the reverse). DMK and anything else is rejected;
   - departs on the right date, at or after 18:00 Singapore time on Friday,
     or at or after 20:00 Bangkok time on Sunday. These times are inclusive,
     so exactly 18:00 or 20:00 counts;
   - has local departure and arrival times consistent with the flight
     duration Google reports, given the two airports' time zones (within 10 min).
     This checks that the times really are local airport times;
   - has a price within a plausible range (default 20–1500 per leg).
4. It keeps the cheapest qualifying flight per leg and adds the two together.
5. It appends one row per leg to `data/history.csv`, including failures. It
   then regenerates `SUMMARY.md`, comparing each weekend's total with the
   previous run, and the workflow commits both files.

Each leg in the summary shows one of three states:

| Shown | Meaning |
|---|---|
| `SGD 128 · Scoot · 19:05→20:30` | Cheapest qualifying flight found **in this run** |
| `— no qualifying flights (…)` | Google answered, but nothing met the rules. The reason is given, e.g. "3 departs before 18:00" |
| `⏳ not on sale yet` | More than `max_days_ahead` days away, so not searched. It gets searched automatically once it's in range |
| `❌ fetch failed: …` | No usable answer: network error, consent page, block page or unreadable response |
| `… ⚠ 2 unreadable` | Priced, but Google also sent results that couldn't be read and were skipped. One of those could have been cheaper |

Flights that Google lists without a fare yet (common far ahead) can't be the
cheapest, so they don't get a ⚠. They're counted in the history CSV's
`detail` column ("7 flight(s) with no price shown"), and each one is named
in the run log.

A total appears only when both legs were priced in this run. Old prices are
never carried forward. ★ marks the cheapest complete weekend.

## Changing the settings

Everything is in [`config.toml`](config.toml): airports and their time zones,
the departure-time thresholds, passengers, cabin, nonstop-only, currency,
price sanity bounds, booking window, request pacing, alert thresholds and the
scans (`[scans.upcoming]` with `weekends = 12`, and `[scans.year2027]` with a
`first_friday`/`last_friday` range). Edit it on GitHub and commit. The next
run uses the new values. To track 2028 later, add a `[scans.year2028]` section,
then add it to the workflow's `scan` options and schedule.

The schedules are in `.github/workflows/flight-tracker.yml` (`cron`, in UTC):
`17 1 * * *` runs `upcoming` daily at 09:17 Singapore time, and `47 2 * * 3`
runs `year2027` on Wednesdays at 10:47.

To run it on demand, go to **Actions → Flight tracker (SIN-BKK weekends) →
Run workflow** and pick the scan.

To run it locally:

```bash
cd flight-tracker
pip install -r requirements-dev.txt
python -m pytest -q          # offline unit tests
python -m tracker                     # live run of the daily scan
python -m tracker --scan year2027     # live run of the 2027 scan
```

## Telegram alerts (optional)

The tracker sends one message when any weekend's total is **below
`alerts.total_below`** (default SGD 250), or **has dropped by more than
`alerts.drop_percent`** (default 10%) since the previous run. A weekend that
stays below the threshold triggers an alert again on each daily run.

1. In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`,
   and copy the token it gives you.
2. Send your new bot any message. Then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy
   `result[0].message.chat.id`.
3. In the GitHub repo, go to **Settings → Secrets and variables → Actions →
   New repository secret**. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

If either secret is missing, alerts are skipped silently. A failed alert
logs a warning but doesn't fail the run.

## Known limitations

- **Request volume.** On Wednesdays the two scans together make up to ~130
  searches, compared with ~24 on other days. If Google starts blocking
  runs, move the 2027 scan to a less frequent schedule first.
- **Booking window.** 330 days is an estimate. Airlines open sales between
  roughly 330 and 355 days ahead. Legs just past the cut-off may already be
  on sale but won't be searched until they fall inside it.
- **Unofficial scraping.** Google can change its page at any time, or show a
  consent or CAPTCHA page to GitHub's servers. When that happens the affected
  legs show `❌ fetch failed`. After a consent or block page, the run stops
  making requests. If every leg fails, the workflow run is marked failed so
  GitHub emails you. A string of failures probably means fast-flights needs
  updating. Bump the pin in `requirements.txt` deliberately, and re-read
  `fast_flights/parser.py` when you do.
- **No round-trip pricing.** Full-service carriers sometimes price a round trip
  below two one-ways, but this tracker can't check that reliably.
  fast-flights reads only the first results page of a round-trip search,
  which lists outbound flights with a "from" round-trip total. It doesn't
  say which return flight that total assumes, so the return can't be
  checked against the Sunday 20:00 rule or the airport rule. Every price
  here is therefore **2 × one-way** (`pricing_type = one-way` in the CSV).
  For SQ or Thai, it can be worth also checking the round-trip price on the airline's site.
- **Unreadable results.** fast-flights 3.1.0 gives up on a whole page if one
  result has an unexpected shape (seen live: two legs failed this way on the
  first run). The tracker reads each result separately, skips any it can't
  read, and marks that leg with ⚠. A result whose only problem is a missing
  fare is counted as "no price shown" instead, with no ⚠ (the plain library
  crashes on these too). If none can be read, the leg shows
  `❌ fetch failed`. Such a page isn't retried, since it would fail the same
  way. Any page that failed or had skipped results is attached to the
  workflow run as the `debug-pages` artifact for 7 days, so the cause can be
  checked.
- **"Best flights" section.** fast-flights 3.1.0 parses only one of the two
  result lists in Google's page data. The tracker also tries to parse the
  other ("best flights") list, using the same library code. If that fails,
  it skips those results as above. Each request's log line shows how many
  results came from each list.
- **Where the search comes from.** The search runs from a GitHub server
  (usually in the US). Google may show slightly different fares or flights
  than you would see in Singapore. The currency is requested as SGD, but
  the response doesn't confirm it. The per-leg price bounds are a backstop
  against a wrong currency.
- **Flight numbers** aren't exposed by fast-flights, so the tracker records
  airline and times only.
- **GitHub cron** can start runs late at busy times, and GitHub disables
  scheduled workflows after 60 days without repository activity. The daily
  commits normally prevent that.
