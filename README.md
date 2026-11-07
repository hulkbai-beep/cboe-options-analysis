# CBOE Options Analysis (cboe-options-analysis)

Options analysis of US stocks, ETFs and indices from CBOE's free delayed option chains, with optional
Financial Modeling Prep (FMP) and Massive data. It works as a Claude skill, and it can also be run from the
command line or as a local web page.

What it covers:
- Expected moves: the 1σ move and price range of every key expiry (next, this week, next week, monthly,
  30 days, the first expiry after earnings), from the ATM straddle
- Earnings: the move option prices imply for the report itself, against the last 12 two-day reactions
  on the same basis, and a check of the earnings date against the term structure
- Implied volatility: the 30-day constant-maturity IV, its percentile (a CBOE volatility index such as VIX
  or VXN from day one, IV Rank once 60 days are stored), IV excluding earnings, and IV against 10/20/60-day
  realized volatility
- Term structure (7 to 180 days), forward volatilities and kinks
- Skew: 25-delta risk reversal and butterfly, the smile by expiry
- Positioning: put/call ratios, the strikes with the most open interest, large far-out-of-the-money
  positions, max pain (weak evidence), day-to-day open-interest changes, unusual activity
- Dealer gamma (GEX): the gamma profile, the zero-gamma level, call and put walls (with the assumption it
  rests on)
- Strategy screens with per-contract money arithmetic: sell a cash-secured put, covered call, buy a call,
  buy a put, earnings straddle
- A market thermometer: VIX level and percentile, VIX/VIX3M, SPX term structure, skew and gamma, realized
  versus implied volatility, as six transparent rules
- With Massive (optional): short interest, days to cover, the share of the free float and daily short sale
  volume; the latest headlines with the news provider's sentiment label; the latest Treasury curve for
  discounting European index options (SPX, NDX, RUT, XSP) instead of a fixed 4%; a check of the built-in
  holiday calendar against the exchange's
- A self-contained interactive `chart.html` for every run, and a local web UI

All calculations are done by the scripts; Claude explains the results. Probabilities are risk-neutral,
implied by option prices. The skill says nothing about whether to trade.

Data sources:

| Source | Key | What it provides |
|---|---|---|
| CBOE delayed quotes | none | option chains (about 15 minutes delayed), volatility index histories |
| FMP (optional) | `FMP_API_KEY` | company profile, earnings dates, dividends, daily closes |
| Massive (optional, formerly Polygon.io) | `MASSIVE_API_KEY` | short interest, short sale volume and free float; headlines with sentiment labels; Treasury yields; the exchange holiday calendar; dividends when FMP gives no answer |

Without the optional keys everything from CBOE still works; the blocks that need them say why they are
unavailable.

> This is options-market analysis for information only, not investment advice. CBOE's data is about 15
> minutes delayed and unofficial; verify important figures with your broker.

Version 1 is in English: the skill, this guide, the report templates and the web page. Claude writes each
report in the language of the request.

---

## Folder layout

```
cboe-options-analysis/
├── SKILL.md                  # Instructions for Claude: setup, workflow, interpretation rules, options
├── README.md                 # This file: the guide for people
├── requirements.txt          # pandas, numpy, pyarrow, truststore, certifi (tzdata on Windows)
├── requirements-dev.txt      # + pytest
├── references/
│   ├── report_template.md    # The ticker report: sections, key paths, tables, wording, a worked example
│   ├── market_template.md    # The market thermometer report
│   ├── methodology.md        # How every number is computed, the approximations and the limits
│   └── strategies.md         # The strategy screens: filters, columns, formulas and wording
├── scripts/
│   ├── analyze.py            # Main script: one ticker or --market -> metrics.json + chart.html
│   ├── snapshot.py           # End-of-day snapshots into the history store (for a scheduled task)
│   ├── cboe_client.py        # CBOE client: redirect, gzip, cache, retries; check / clear-cache commands
│   ├── fmp_client.py         # FMP client (optional): cache, rate limit, proxy rules; check / get / clear-cache
│   ├── massive_client.py     # Massive client (optional): cache, shared rate limit; check / get / clear-cache
│   ├── symbols.py            # Symbol normalisation: $SPX, ^SPX, _SPX, BRK.B / BRK-B / brk b; foreign suffixes refused
│   ├── chain.py              # Chain parsing, the exchange calendar, sessions (closed, intraday, overnight)
│   ├── surface.py            # Forward, ATM IV from the straddle, 25-delta wings, key expiries
│   ├── modules.py            # Expected move, term structure, skew, earnings, volatility, positioning, gamma, flow
│   ├── strategies.py         # The strategy screens
│   ├── market.py             # The market view and the thermometer
│   ├── history.py            # The history store: chains, summaries, IV Rank, open-interest changes
│   ├── chart.py              # Chart data and the self-contained chart.html
│   ├── tls.py                # HTTPS certificate checks: the OS certificate store, certifi, SSL_CERT_FILE
│   ├── quality.py, core.py, fetch.py, config.py, winenv.py
├── ui/
│   ├── server.py             # Local web server (127.0.0.1:8987)
│   ├── index.html, chart.js, chart.css
│   └── i18n.js               # All page text (English in version 1)
└── tests/                    # Offline tests on synthetic chains: python -m pytest -q -p no:cacheprovider
```

## Requirements

- Python 3.11 or later (built and tested with Python 3.13, pandas 3.0, numpy 2.5, pyarrow 26).
- `pip install -r requirements.txt` (pandas, numpy, pyarrow, truststore and certifi; `tzdata` is added on Windows).
- On Windows the command is usually `python` (or `py`); on macOS and Linux `python3`.

---

## 1. First-time setup

### 1. Install the requirements

```bash
python -m pip install -r requirements.txt
```

### 2. Network

CBOE needs no key. The machine must reach `cdn.cboe.com` and `cdn-api.cboe.com` (CBOE redirects every
request to `cdn-api`), plus `financialmodelingprep.com` when FMP is used and `api.massive.com` when
Massive is used. In a sandbox or a corporate network with an allowlist, all of these hosts must be on it.

HTTPS certificates are always checked, the way a browser checks them: `truststore` hands the check to the
operating system (on Windows it also downloads a root certificate that Windows does not have yet), and
`certifi` adds the standard Mozilla list of root certificates. A company proxy or an antivirus product that
inspects HTTPS usually puts its root certificate in the Windows certificate store, and then nothing else is
needed. If it does not, save that root certificate as a PEM file and point `SSL_CERT_FILE` at it:

```powershell
setx SSL_CERT_FILE "C:\certs\company-root.pem"
```

Then open a new terminal and restart Claude (macOS / Linux: `export SSL_CERT_FILE=...` in the shell profile).

### 3. The FMP key (optional)

Without FMP the chain-based analysis works fully. FMP adds earnings dates and past reactions, realized
volatility, dividends, the stock / ETF classification and the price cone. The key is read only from the
environment variable `FMP_API_KEY`; never write it into a file in this folder.

Windows (PowerShell or CMD):

```bash
setx FMP_API_KEY "your-key"
```

`setx` only applies to terminals opened afterwards: open a new terminal and restart Claude.

macOS / Linux: add `export FMP_API_KEY="your-key"` to `~/.zshrc` or `~/.bashrc` (edit it with
`nano ~/.zshrc`, not TextEdit), then open a new terminal. Check it without printing the key:

```bash
[ -n "$FMP_API_KEY" ] && echo "set" || echo "not set"
```

The Claude desktop app on a Mac may not read `~/.zshrc`. If Claude says the key is not set, add it to
the `env` block of `~/.claude/settings.json` and restart the app:

```json
{
  "env": {
    "FMP_API_KEY": "your-key"
  }
}
```

### 4. A third-party FMP proxy (optional)

With an official FMP key, leave `FMP_BASE_URL` and `FMP_TLS_VERIFY` unset: requests go to
`https://financialmodelingprep.com/stable` with certificate verification on, and the client ignores
`FMP_TLS_VERIFY=0` for the official host. Only with a proxy:

| Environment variable | Value / notes |
|---|---|
| `FMP_API_KEY` | the token the proxy provider gave you |
| `FMP_BASE_URL` | the proxy address, e.g. `https://1.2.3.4` |
| `FMP_TLS_VERIFY` | `0` turns off HTTPS certificate verification; only for a proxy whose certificate does not verify, and only for one process |
| `FMP_PATH_PREFIX`, `FMP_AUTH_STYLE` | usually not needed: detected on the first connection and saved |

With certificate verification off the connection, and the key on it, can be intercepted. Set
`FMP_TLS_VERIFY=0` per command, never with `setx` or in a profile:

```powershell
$env:FMP_TLS_VERIFY="0"; python scripts/analyze.py NVDA
```

```bash
FMP_TLS_VERIFY=0 python3 scripts/analyze.py NVDA
```

If a run names a proxy host although you have an official key (for example `HTTPS certificate
verification failed for 54.236.92.205`), `FMP_BASE_URL` is still set. Remove it (PowerShell:
`[Environment]::SetEnvironmentVariable("FMP_BASE_URL", $null, "User")`), then restart Claude and any
running `ui/server.py`.

### 5. The Massive key (optional)

Massive (massive.com, formerly Polygon.io) adds what the CBOE chain does not carry: short interest, days to
cover, the share of the free float and daily short sale volume (stocks and ETFs); the latest headlines with
the news provider's sentiment label; the latest Treasury curve for discounting European index options
(otherwise a fixed 4%); a check of the holiday calendar in `scripts/config.py` against the exchange's; and
dividends when FMP gives no answer. It is optional: without it these blocks say "Massive not configured" and
everything else works. A free account is enough. The key is read only from the environment variable
`MASSIVE_API_KEY` and sent only in the `Authorization` header, never in a URL, a cache file or the output;
never write it into a file in this folder.

Windows (PowerShell or CMD):

```bash
setx MASSIVE_API_KEY "your_key"
```

Then open a new terminal and restart Claude and any running `ui/server.py`: programs that are already
running keep their old environment.

macOS / Linux: add `export MASSIVE_API_KEY="your_key"` to `~/.zshrc` or `~/.bashrc`, then open a new
terminal. For the Claude desktop app on a Mac, add it to the `env` block of `~/.claude/settings.json` as
for FMP (step 3).

Then check it (step 7):

```bash
python scripts/massive_client.py check NVDA
```

The free plan allows 5 requests a minute. The scripts keep to that over all runs that share the cache
folder: a request waits for a free slot up to `MASSIVE_MAX_WAIT` seconds, and a piece that would wait
longer is skipped with that reason; the next run a minute later fetches it.

### 6. Other optional environment variables

| Environment variable | Default | Notes |
|---|---|---|
| `OA_DATA_DIR` | `~/options-data` | the history store (chains, summaries, snapshot log, `tickers.txt`) |
| `OA_CACHE_DIR` | `~/.cache/cboe-options` | the CBOE response cache |
| `FMP_CACHE_DIR` | `~/.cache/cboe-options-fmp` | the FMP response cache (never contains the key) |
| `FMP_MAX_PER_MIN` | `280` | FMP requests per minute |
| `FMP_TIMEOUT` | `30` | seconds per FMP request |
| `MASSIVE_CACHE_DIR` | `~/.cache/cboe-options-massive` | the Massive response cache and `rate-window.json` (never contains the key) |
| `MASSIVE_MAX_PER_MIN` | `5` | Massive requests per minute (the free plan); raise it only for a paid plan |
| `MASSIVE_MAX_WAIT` | `65` | seconds a Massive request may wait for a free slot; a longer wait skips that piece |
| `MASSIVE_TIMEOUT` | `30` | seconds per Massive request |

After `setx OA_DATA_DIR "D:\options-data"`, restart Claude and any running `ui/server.py`.

### 7. Check the connections

```bash
python scripts/cboe_client.py check SPY
python scripts/fmp_client.py check
python scripts/massive_client.py check NVDA
```

The first prints which certificates are trusted, the final URL after the redirect, the HTTP status, the
sizes before and after gzip, the feed time, `Last-Modified`, the contract and expiry counts. The second
(with a key) prints the FMP route and whether each endpoint the skill uses (profile, earnings, dividends,
daily prices) is in your plan. The third (with a Massive key) prints the base URL, the certificate trust,
the cache folder and the rate limit, each endpoint the skill uses as OK, RESTRICTED (not in your plan),
EMPTY or ERROR, and the holiday calendar against `scripts/config.py`; it takes about a minute on the free
plan. None of them prints a key.

### 8. Install it as a Claude skill

Claude reads the copy in `~/.claude/skills/cboe-options-analysis/`. Copy this folder there, and again
after every update. Windows:

```powershell
robocopy C:\Users\<you>\Documents\claude\cboe-options-analysis C:\Users\<you>\.claude\skills\cboe-options-analysis /E /XD .git oa-output __pycache__
```

macOS / Linux:

```bash
mkdir -p ~/.claude/skills
rsync -a --exclude .git --exclude oa-output --exclude __pycache__ ~/Documents/claude/cboe-options-analysis/ ~/.claude/skills/cboe-options-analysis/
```

To update: run `git pull` in this folder, copy it to the skills folder again with the same `robocopy` or
`rsync` command, then restart Claude and any running `ui/server.py`. The keys, the caches and the history
store live outside the folder and are kept.

---

## 2. Three ways to use it

### Option 1: ask Claude (recommended)

Ask in English or Chinese; there is no need to mention CBOE. For example:
- "What move are NVDA's options pricing for earnings?"
- "Show me the expected move for SPY this week and this month."
- "Is TSLA's implied volatility high compared with its history?"
- "What does the put skew on QQQ look like?"
- "I'm thinking of selling a put on AAPL. What do the numbers look like?"
- "How nervous is the options market right now?" (the market thermometer)
- "NVDA 期权隐含的财报波动是多少？", "大盘情绪怎么样？"

Claude runs `scripts/analyze.py`, reads `metrics.json`, writes `report.md` next to it following
`references/report_template.md` (or `market_template.md`) and replies with the key points, the paths to
`report.md` and `chart.html`, and the disclaimer. It never says whether to trade: asked "should I sell
this put?", it shows the arithmetic of the contract and leaves the decision to you.

### Option 2: command line

From this folder:

```bash
python scripts/analyze.py NVDA
python scripts/analyze.py --market
```

The terminal shows a short summary:

```
ACME  as of 2026-11-05  spot 100.00 (-1.48%)  [CBOE delayed quotes (~15 min) + FMP stable API (official)]
  metrics.json : ./oa-output/ACME_2026-11-05/metrics.json
  chart.html   : ./oa-output/ACME_2026-11-05/chart.html
  expected move: next week 2026-11-13 +-5.8%; monthly 2026-11-20 +-9.9%
  earnings     : 2026-11-17, event 1sd 5.9%, implied/past 1.01
  IV           : 30d 43.5%, IV Rank history 1 days, need 60
  skew         : rr25 4.6% (put)
  gamma        : positive, zero gamma 99.55
  API calls    : 8 network, 0 from cache
```

(ACME is the made-up company of the test data.) The output folder holds `metrics.json` (every number,
an internal format) and `chart.html` (open it in any browser, works offline). Run from inside the skill
folder, the output goes to `~/oa-output/` instead.

| Option | Default | What it does |
|---|---|---|
| `TICKER` | required unless `--market` | a US stock, ETF or index: `NVDA`, `SPY`, `SPX` (or `^SPX`, `$SPX`, `_SPX`), `BRK.B` (or `BRK-B`, `brk b`) |
| `--market` | off | the market thermometer (SPX, SPY, QQQ, IWM, VIX) |
| `--out DIR` | `./oa-output/<TICKER>_<as_of>/` | the output folder |
| `--data-dir DIR` | `$OA_DATA_DIR`, else `~/options-data` | the history store |
| `--refresh` | off | ignore the CBOE, FMP and Massive caches and fetch again |
| `--no-save` | off | do not store today's chain and summary row |
| `--no-market-context` | off | skip the SPX / VIX context of a ticker run |

- An uncached stock run makes about 8 requests (4 to CBOE, 4 to FMP); the market view about 9. With
  Massive, a stock run with the market context adds 6, an ETF 5 (one more for either when FMP gave no answer
  on dividends: not configured, rejected, unreachable or restricted; one fewer without the market context),
  an index or the market view 2. The free plan's 5 requests a minute count over all runs, so a run, or a
  second run within a minute, that needs more waits for a slot (the terminal says so; about a minute), and
  the holiday-calendar check, which runs only when a request slot is free at once, is often skipped on the
  first run of the day; it is then cached for a day. When Massive answers "too many requests" (HTTP 429)
  after one wait, or server errors to two requests in a row, the rest of the run skips Massive instead of
  waiting again.
- Exit codes: 0 ok (also when FMP or Massive is unavailable: those blocks say why); 1 a symbol, data or
  CBOE problem (the message says which); 2 a usage error.
- VIX in the ticker view is refused (VIX options settle to VIX futures): use `--market`. DJX is not
  supported yet. Foreign listings (`SHOP.TO`, `0700.HK`) are refused: use the US listing or ADR.

### Option 3: local web UI

```bash
python ui/server.py
```

Then open **http://127.0.0.1:8987** (it opens by itself unless you pass `--no-browser`; another port:
`--port 9000`). Type a symbol, or switch to Market, and get the same analysis and charts as `chart.html`.
Links work directly: `http://127.0.0.1:8987/?symbol=NVDA`.
- It listens on 127.0.0.1 only and refuses requests addressed to any other host name.
- On Windows it reads the `FMP_*`, `MASSIVE_*` and `OA_*` values saved with `setx`, even in a terminal
  opened before. At start it notes which optional key is not set.
- With the old proxy `54.236.92.205` as `FMP_BASE_URL`, it sets `FMP_TLS_VERIFY=0` for its own process
  only; nothing changes for the official API.
- After an update, stop it with Ctrl+C and start it again.

---

## 3. History and scheduled snapshots

CBOE keeps no history: IV Rank, the skew and GEX percentiles and day-to-day open-interest changes need
the chains you save yourself. Every ticker run of a closed session saves its chain; a daily scheduled
snapshot builds the history for a list of symbols.

```bash
python scripts/snapshot.py                     # the symbols in <data-dir>/tickers.txt
python scripts/snapshot.py SPY QQQ NVDA        # just these
python scripts/snapshot.py --rebuild-summary   # rebuild summary/<key>.parquet from the stored chains
```

| Option | What it does |
|---|---|
| `TICKER ...` | symbols to save (default: `<data-dir>/tickers.txt`, written the first time from SPY, QQQ, IWM, SPX, VIX, AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA) |
| `-f FILE` (`--tickers-file`) | read the symbols from a file (one or more per line, `#` starts a comment) |
| `--data-dir DIR` | the history store (`-o` / `--out` also work) |
| `--force` | overwrite saved days, and save while the market is open (never overnight quotes) |
| `--raw` | also keep the gzipped raw feed under `raw/` |
| `--sleep SECONDS` | pause between CBOE requests (default 1) |
| `--rebuild-summary` | rebuild the summaries from the stored chains (all symbols, or those given) |

The store:

```
options-data/
├── tickers.txt
├── chains/YYYY-MM-DD/<SYMBOL>.parquet   # the whole chain of one trading day
├── summary/<SYMBOL>.parquet             # one row of derived values per trading day
├── raw/YYYY-MM-DD/<SYMBOL>.json.gz      # with --raw
└── logs/snapshot.log
```

**When to run it:** once per trading day between 16:30 and 20:00 New York time. SPX, XSP and VIX options
trade overnight from 20:15, and an overnight version is never stored. Files are named after the trading
day the data belongs to, so extra runs (weekends, holidays, retries) skip what is already saved. A missed
day cannot be recovered later. Exit code 1 means at least one symbol failed; the log says which.

**Windows Task Scheduler** (a PC on Beijing time: Tuesday to Saturday at 06:00 is 17:00 New York in winter
and 18:00 in summer, so no daylight-saving change is needed). Use the `python.exe` that has the
requirements, and put paths with spaces in `\"...\"` inside `/TR`:

```
schtasks /Create /TN cboe-options-snapshot /SC WEEKLY /D TUE,WED,THU,FRI,SAT /ST 06:00 /F /TR "C:\Python313\python.exe C:\Users\<you>\.claude\skills\cboe-options-analysis\scripts\snapshot.py --data-dir C:\options-data -f C:\options-data\tickers.txt"
```

On a PC on New York time, use `/SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 17:00`. A scheduled task may not see
variables set later with `setx`, so the command names the data folder and the symbol file. In the task's
properties, tick "Run task as soon as possible after a scheduled start is missed". The computer must be on.

**cron** (a machine on UTC; 22:00 UTC is 17:00 New York in winter and 18:00 in summer):

```
0 22 * * 1-5 /path/to/python /path/to/cboe-options-analysis/scripts/snapshot.py --data-dir /path/to/options-data
```

**Discounting:** with `MASSIVE_API_KEY` set, the summary rows of European indices (SPX, NDX, RUT, XSP) are
discounted with the latest Treasury curve dated two trading days or more before that day (the Federal
Reserve posts a day's yields on the next business day, so this curve is always out by the snapshot), in a
snapshot and in `--rebuild-summary` alike, so both store the same row (the rebuild fetches the curves with one
request per calendar year; a year it cannot read falls back to the fixed rate); without it at the fixed 4%,
as in an analysis. The log says which.

**Continuing an existing history:** chains saved by the earlier prototype script use the same parquet
schema. Point `OA_DATA_DIR` (or `--data-dir`) at that folder and run `python scripts/snapshot.py
--rebuild-summary` once.

**Cloud sessions** (Claude Code on the web, sandboxes) have a temporary home folder: the history and the
caches do not survive the session, so IV Rank and open-interest changes are usually unavailable there.
Do not schedule snapshots in the cloud.

---

## 4. Cache

- **CBOE:** `~/.cache/cboe-options` (`OA_CACHE_DIR`). A chain of a closed session stays valid until 09:30
  New York the next trading day; an open session's chain for 5 minutes, then a conditional request
  renews it. Clear it with `python scripts/cboe_client.py clear-cache`.
- **FMP:** `~/.cache/cboe-options-fmp` (`FMP_CACHE_DIR`), reused for the rest of the New York day. Clear it
  with `python scripts/fmp_client.py clear-cache` (this also forgets a detected proxy setup).
- **Massive:** `~/.cache/cboe-options-massive` (`MASSIVE_CACHE_DIR`). Each answer is kept for a time that
  suits it: news 30 minutes, short sale volume 4 hours, Treasury yields 6 hours, short interest and dividends
  12 hours, the holiday calendar 1 day, the free float 7 days. `rate-window.json` in the same folder holds the
  times of the last minute's requests (nothing else), so every run that uses this folder shares the rate
  limit. Clear it with `python scripts/massive_client.py clear-cache`.
- `--refresh` (or "Ignore cache" on the web page) skips reading all three caches.

---

## 5. Tests

The tests run offline on synthetic option chains generated at test time (no network, no key):

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q -p no:cacheprovider
```

On Windows, keep pytest's temporary files out of the skill folder:

```
python -m pytest -q -p no:cacheprovider --basetemp=%TEMP%\oa-pytest
```

The HTTPS tests run a local server with test certificates made by the `openssl` command, and are skipped
where that command is missing. A few checks compare results with values measured on real CBOE and FMP files
from 2026-11-05. Those files are private test data and are not part of this repository or the packaged skill;
point `OA_TEST_DATA` at that folder to run the checks, otherwise they are skipped.

---

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No option chain on CBOE: XYZ` | CBOE has no options for that symbol (HTTP 403 `AccessDenied`). Check the ticker; indices are found with or without `^`, `$` or `_` |
| `CBOE error: cannot reach cdn.cboe.com` or timeouts | A network block. Allow `cdn.cboe.com` and `cdn-api.cboe.com`, then run `python scripts/cboe_client.py check SPY` |
| `TLS certificate verification failed for cdn.cboe.com` | Run `python -m pip install -r requirements.txt` (truststore, certifi), copy the skill folder again, then `python scripts/cboe_client.py check SPY`. The message gives the reason, who issued the certificate your network presented, and what was trusted. Issued by a public authority such as Let's Encrypt: check the computer's date and time. Issued by your company, a proxy, an antivirus or a VPN app: it inspects HTTPS; set `SSL_CERT_FILE` to its root certificate (section 1, step 2) or exclude `cdn.cboe.com` and `cdn-api.cboe.com` from HTTPS scanning. Never turn verification off |
| `VIX options settle to VIX futures ...` | Expected: use `python scripts/analyze.py --market` for VIX |
| `FMP not configured` in the report | No `FMP_API_KEY`: earnings, realized volatility, dividends and the cone are unavailable; everything else works |
| `FMP key rejected` / `FMP unreachable` | Wrong key, or the FMP host is blocked. Run `python scripts/fmp_client.py check` |
| `HTTPS certificate verification failed for 54.236.92.205` | `FMP_BASE_URL` still points to the old proxy. Remove it and restart Claude and `ui/server.py` |
| `HTTPS certificate verification failed for financialmodelingprep.com` | As for `cdn.cboe.com` above. Do not turn verification off |
| `Massive not configured` in the report | No `MASSIVE_API_KEY`: short interest, news and the calendar check are unavailable and index options are discounted at the fixed 4%; everything else works |
| `Massive key rejected` / `Massive unreachable: ...` | Wrong or expired key, or `api.massive.com` is blocked. After `setx`, open a new terminal and restart Claude and `ui/server.py`; then run `python scripts/massive_client.py check NVDA` |
| `restricted on the current Massive plan: /stocks/vX/float` | That endpoint is not in your plan (`meta.restricted` names it); only that piece is missing |
| `[massive] waiting 52s for the rate limit`, or "skipped for Massive's rate limit" | The free plan allows 5 requests a minute, counted over every run that uses the Massive cache folder. A run a minute later fetches the skipped piece and reads the rest from the cache. "HTTP 429" in the reason means Massive itself refused: another program or computer may be using the same key. Raise `MASSIVE_MAX_PER_MIN` only with a paid plan |
| `Massive answered server errors (HTTP 5xx) to 2 requests in a row ...` | Massive had a problem; the rest of that run went without it. Run again later |
| `MASSIVE_API_KEY contains characters that cannot be sent ...` | The key was saved with quotes or invisible characters (often from copying). Run `setx MASSIVE_API_KEY "your_key"` again with straight quotes, then open a new terminal |
| `the trading calendar in scripts/config.py differs from the exchange calendar ...` | `MARKET_HOLIDAYS` and `EARLY_CLOSES` in `scripts/config.py` need an update for the dates named; until then the sessions of those dates may be wrong |
| `HTTPS certificate verification failed for api.massive.com` | As for `cdn.cboe.com` above. Do not turn verification off |
| IV Rank says "history n days, need 60" | Not enough stored days yet. Schedule `snapshot.py` (section 3); until then the report uses a CBOE volatility index where one exists |
| "intraday data: the session has not closed" | Run during market hours: values change, nothing is saved. Run after 16:15 New York for closing data |
| "option quotes are from the overnight (GTH) session" | SPX, XSP and VIX after 20:15 New York: option quotes are overnight quotes while the index is the 16:00 close. Expected; nothing is saved |
| "quotes are from ..., n trading day(s) old" | The feed has not been updated since an earlier session. Try `--refresh`; check `cboe_client.py check` |
| Wide spreads, empty strategy tables | After the close spreads widen; the screens drop quotes wider than 25% of the mid and contracts with open interest below 100 |
| `ModuleNotFoundError: pandas` / `pyarrow` | Run `python -m pip install -r requirements.txt` with the same Python |
| Windows says `python` is not found | Use `py`, e.g. `py scripts/analyze.py NVDA` |
| The web page does not open | Check that `ui/server.py` is running, or use another port: `--port 9000` |

Definitions and formulas: [references/methodology.md](references/methodology.md) and
[references/strategies.md](references/strategies.md).
