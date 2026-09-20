# Weekly Financial Market Monitor

A config-driven weekly market calendar. Every Sunday at 10:00 it collects
the macro releases scheduled for the coming week and the earnings dates
for a watchlist, and puts both on one dashboard.

The split it is built around:

- **Official sources** decide *when an event happens.*
- **`config/config.yaml`** decides *whether you care and how much.*
- **The dashboard** decides *how it looks.*

It is a monitoring and planning tool. It does not place trades.

## Where the data comes from

All free, all official, no scraping of commercial sites:

| Source | Feed | Covers |
| --- | --- | --- |
| BLS | official iCalendar feed | CPI, PPI, Employment Situation, JOLTS, ECI |
| BEA | official iCalendar feed | GDP, Personal Income and Outlays (PCE) |
| Federal Reserve | FOMC calendar page | Rate decisions, statements, minutes |
| Census | economic indicator calendar | Retail Sales, Durable Goods, Housing Starts |
| EarningsAPI.com | `/v1/earnings` REST API | Earnings dates for your watchlist |

BLS and BEA publish real calendar feeds, so those two need no HTML
parsing at all. The Fed and Census pages are parsed, but against stable
anchors — the Fed's `fomc-meeting` CSS classes and the Census table's
`sorttable_customkey` timestamps — rather than positional selectors.

TradingView is never scraped. Its official Economic Calendar widget can
be embedded in the dashboard as a visual cross-check, and that is all it
is: your table is generated from the agencies.

## Setup

Python 3.9 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .

cp .env.example .env
```

Then edit `.env`:

```bash
# Required in practice: BLS returns 403 to clients that do not identify
# a contact address.
CONTACT_EMAIL=you@example.com

# Required for earnings. Get a key at https://www.earningsapi.com/
EARNINGS_API_KEY=your_key_here
```

`.env` holds every secret; no API key belongs in the YAML. EarningsAPI
takes its key as a query parameter, so it necessarily appears in the
request URL — every URL is passed through a redactor before it reaches a
log line or a dashboard warning.

## Use

```bash
python -m market_monitor validate-config    # check config.yaml, print a summary
python -m market_monitor refresh            # run the full pipeline once
python -m market_monitor fetch-macro        # macro calendar only
python -m market_monitor fetch-earnings     # earnings watchlist only
python -m market_monitor summary            # text summary of the last run
python -m market_monitor dashboard          # launch Streamlit
python -m market_monitor schedule           # run the weekly scheduler
```

If the virtualenv is not activated, prefix with `.venv/bin/python`
instead of `python`.

`refresh` exits non-zero when the report carries warnings, so cron and CI
can notice a degraded run.

### Dashboard

```bash
python -m market_monitor dashboard                  # opens a browser at :8501
python -m market_monitor dashboard --port 9000      # different port
python -m market_monitor dashboard --no-browser     # headless, e.g. on a Pi
```

It reads the stored report, so it loads instantly and works offline.
There is a **Refresh now** button in the sidebar — that alone is enough
to use the app, no scheduler required. Stop it with Ctrl+C.

### Scheduler

```bash
python -m market_monitor schedule             # wait for the next slot
python -m market_monitor schedule --run-now   # refresh now, then wait
```

This blocks in the foreground: it sits there until the scheduled time,
runs a refresh, and goes back to waiting. **Closing the terminal stops
it** — there is no background daemon. If the machine is asleep at the
scheduled time, a one-hour grace window means it still fires on wake;
miss it by longer and that run is skipped.

For something that survives reboots and sleep, use the OS scheduler
(`launchd` on macOS, `cron` on Linux) to run
`python -m market_monitor refresh` directly, rather than keeping a Python
process alive all week.

### Changing when it runs

Edit `config/config.yaml` — no Python changes, and nothing to restart
except the scheduler process itself:

```yaml
app:
  timezone: "America/Toronto"   # DST is handled for you

schedule:
  enabled: true
  day_of_week: "sunday"         # monday .. sunday
  time: "10:00"                 # 24-hour HH:MM
```

| Setting | Next run (from Sun Sep 20, 19:56) |
| --- | --- |
| `sunday` / `10:00` | Sun Sep 27, 10:00 EDT |
| `sunday` / `07:30` | Sun Sep 27, 07:30 EDT |
| `monday` / `06:00` | Mon Sep 21, 06:00 EDT |
| `friday` / `16:30` (New York) | Fri Sep 25, 16:30 EDT |

`validate-config` checks the values before you rely on them: `"10am"`,
`"25:00"` and `"someday"` are all rejected with an explanation rather
than failing silently at 10am on a Sunday.

Setting `enabled: false` makes `schedule` exit immediately instead of
blocking, which is what you want if you drive refreshes from `launchd`
or the dashboard button.

## Configuration

`config/config.yaml` owns all of it. Changing any of the following needs
no Python edit:

```yaml
schedule:
  day_of_week: "sunday"     # when it runs
  time: "10:00"

app:
  timezone: "America/Toronto"

macro:
  days_ahead: 7             # 7 = the Mon..Sun week ahead
  minimum_importance: 3     # hide anything below this
  providers:                # turn individual sources off
    census: true
  events:
    CPI:
      enabled: true
      importance: 5         # your rating, not anyone else's
      aliases:
        - "Consumer Price Index"

earnings:
  symbols: ["NVDA", "MU", "AMD", "MSFT"]
  lookahead_days: 30
```

Importance is 5 (Critical) down to 1 (Low), and it is *your* number. The
shipped values are a starting point, not a claim about the market.

Tickers are normalized for you, so `nvda`, ` MU `, and `Msft` all work.

### How an event gets matched

Agencies do not use your labels. BEA publishes
`"Gross Domestic Product, 2nd Quarter 2026 (Second Estimate)"`, and you
want to call it `GDP`. Matching is exact, retried against progressively
simplified forms of the title:

```
"Gross Domestic Product, 2nd Quarter 2026 (Second Estimate)"
  -> strip the parenthetical   -> no match
  -> cut at the first comma    -> "Gross Domestic Product" -> GDP
```

Exact matching is deliberate. BEA also publishes *"Gross Domestic Product
by State and Personal Income by State"*; a substring rule would quietly
file that regional release as the headline GDP print. Anything no alias
claims is simply left out.

To add a release, add its published title as an alias. To see what is
being skipped, the `MacroService.unmatched_names()` helper lists the
titles nothing claimed.

### Configured events with no provider yet

A few entries in the shipped config will never produce a row, and that is
expected:

- **Core CPI, Core PPI, Core PCE, Unemployment Rate** — no agency
  publishes these separately. They ship inside the CPI, PPI, Personal
  Income and Outlays, and Employment Situation releases, which do appear.
- **ISM Manufacturing / Services PMI, Consumer Confidence, University of
  Michigan Sentiment** — these are commercial products with no free
  machine-readable calendar.
- **Initial Jobless Claims** — published by the Department of Labor,
  which has no provider here yet.

They stay in the config so that adding a provider later is a config
change rather than a code change.

## Behaviour when something breaks

The Sunday run is unattended, so failure handling is part of the design,
not an afterthought.

- **Retries.** Each HTTP source gets 3 attempts with exponential backoff
  (2s, 4s), all configurable under `network:`.
- **Isolation.** One source failing never fails the report. If BLS is
  down, BEA, the Fed, Census and earnings still populate the dashboard.
- **Last known good.** A failed fetch falls back to that provider's most
  recent successful snapshot, and the dashboard says so, with the time
  the data was captured. A failure never overwrites the cache.
- **Cache reach.** Snapshots are stored for ~120 days past the reported
  window, so next week's fallback has rows next week's window can see.
- **Conflicts.** Two sources giving the same release different times are
  both kept, and a warning is raised. Nothing is silently dropped.

Provider health, last success time and any warnings are all on the
dashboard, and `refresh_runs` in SQLite keeps a log of every run.

## Earnings dates are projections

EarningsAPI publishes no confirmation flag, so this app never marks a
date **Confirmed**. A row whose reporting time is known is **Expected**;
one reported as `time-not-supplied` is **Estimated**. The dashboard
counts how many are unconfirmed. Treat them as dates that can move.

The session column comes from the `time` field (`time-pre-market`,
`time-after-hours`), with `BMO` / `AMC` handled as well.

### Request cost

`/v1/earnings` is queried **once per symbol**, and it returns that
company's history *and* its upcoming dates in one response. A four-name
watchlist therefore costs four requests per refresh.

The alternative — the date-based `/v1/calendar/earnings` endpoint —
takes a single date per call, so a 30-day lookahead would cost 30
requests. On the free plan (60/min, 100/day, 1,000/month) the per-symbol
route is the one that fits. A long watchlist still costs one request per
name, and the provider logs a warning if yours gets large enough to
matter.

### Switching earnings provider

The provider sits behind an interface, so swapping it is a config change:

```yaml
earnings:
  provider: "earningsapi"    # or "alphavantage"
```

The Alpha Vantage adapter is still included and still works. It takes the
opposite approach — one request that downloads the entire market's
calendar, filtered locally — which makes it a useful fallback when you
are out of EarningsAPI quota. It needs `ALPHA_VANTAGE_API_KEY`, though
the literal value `demo` works for the full-calendar download.

## Output

- **SQLite** (`data/market_monitor.db`) — snapshots, reports, run log.
- **JSON** (`output/current_report.json`) — the full `WeeklyReport`,
  ready for a React frontend, a Discord bot, Grafana or anything else.
- **Dashboard** — Streamlit, reading the stored report.

## Layout

```
src/market_monitor/
  config.py            YAML schema + validation
  main.py              wiring and the refresh pipeline
  cli.py               command line
  scheduler.py         APScheduler cron job
  models/              MacroEvent, EarningsEvent, WeeklyReport, ProviderStatus
  providers/macro/     bls, bea, federal_reserve, census, composite
  providers/earnings/  earningsapi (default), alphavantage
  services/            normalizer, macro, earnings, report
  repository/          SQLite persistence
  dashboard/           Streamlit app and sections
  notifications/       discord, email
  utils/               dates, ics, http, retry, logging
```

The dashboard consumes `WeeklyReport` and nothing else — it has no idea
which agency a row came from beyond the `source` field it prints. A
provider parses its own source and nothing more; it never decides whether
an event matters.

## Tests

```bash
.venv/bin/pytest            # offline; uses saved fixtures
.venv/bin/pytest -m live    # also hits the real endpoints
```

The fixtures in `tests/fixtures/` are real captured responses from each
agency, so a change to a published format shows up as a failing parse
here instead of an empty dashboard on Sunday.

## Notifications

Off by default. Enable in YAML, put the credentials in `.env`:

```yaml
notifications:
  enabled: true
  provider: "discord"   # or "email"
```

A notification failure is logged and never fails the refresh.

## Not in scope

No LLM-generated dates, no trading, no sentiment, no recommendations.
An AI layer may summarize the collected report, but every factual field —
dates, values, sources — comes from structured provider data.
