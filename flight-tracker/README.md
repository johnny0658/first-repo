# SIN → BKK weekend fare tracker

A daily GitHub Actions job that checks Google Flights for Friday-evening
Singapore → Bangkok (Suvarnabhumi) and Sunday-night return flights over the
next 12 weekends, then writes a summary you can scan in a few seconds.
**Latest results: [SUMMARY.md](SUMMARY.md)**. Full history: [data/history.csv](data/history.csv).

It only tells you where to look. Always book, and check the real price, on the
airline's own site.

## How it works

1. Works out the next 12 Friday–Sunday pairs, using today's date in Singapore.
   If today is a Friday, this weekend is included.
2. For each weekend, it makes two one-way searches (outbound Friday, return
   Sunday). That is 24 requests, with a random 4–9 s pause between them.
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
| `❌ fetch failed: …` | No usable answer: network error, consent page, block page or unreadable response |

A total appears only when both legs were priced in this run. Old prices are
never carried forward. ★ marks the cheapest complete weekend.

## Changing the settings

Everything is in [`config.toml`](config.toml): airports and their time zones,
the departure-time thresholds, the number of weekends, passengers, cabin,
nonstop-only, currency, price sanity bounds, request pacing and alert
thresholds. Edit it on GitHub and commit. The next run uses the new values.

The schedule is set in `.github/workflows/flight-tracker.yml` (`cron`, in UTC;
the default `17 1 * * *` is 09:17 Singapore time).

To run it on demand, go to **Actions → Flight tracker (SIN-BKK weekends) →
Run workflow**. This button only appears once the workflow is on the
default branch, i.e. after this PR is merged.

To run it locally:

```bash
cd flight-tracker
pip install -r requirements-dev.txt
python -m pytest -q          # offline unit tests
python -m tracker            # live run; writes data/history.csv and SUMMARY.md
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
- **"Best flights" section.** fast-flights 3.1.0 parses only one of the two
  result lists in Google's page data. The tracker also tries to parse the
  other ("best flights") list, using the same library code. If that fails,
  it logs a warning and uses the library's list alone. This step has not
  been tested against a live Google response.
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
