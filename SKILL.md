---
name: cboe-options-analysis
description: US stock, ETF and index options analysis from CBOE's free delayed option chains, with optional FMP data and a local interactive UI - expected moves by expiry, earnings implied move versus past earnings reactions, implied volatility level, term structure and percentile, IV versus realized volatility, put-call skew and smile, open interest by strike, put/call ratios, dealer gamma exposure (GEX) and the zero-gamma level, screens for cash-secured puts, covered calls, long calls and puts and earnings straddles with per-contract money arithmetic, and a market risk thermometer (VIX, SPX term structure, skew, gamma). Use it whenever the user asks about options, an option chain, implied volatility, expected move, earnings move, skew, gamma, open interest, max pain, put/call ratio or selling puts, including Chinese requests such as 期权, 期权链, 隐含波动率, 预期波动, 财报波动, 波动率偏斜, 期限结构, 持仓量, 未平仓, 最大痛点, 卖put, 备兑, 期权策略, 大盘情绪, 恐慌指数 - even if they do not mention CBOE.
---

# Options analysis (CBOE)

`scripts/analyze.py` downloads the whole option chain of one symbol from CBOE's free delayed feed (about 15
minutes behind, no key), adds optional FMP data (earnings dates, dividends, daily closes), computes every number
and writes `metrics.json` plus a self-contained `chart.html`. Claude then writes `report.md` from `metrics.json`
only, following `references/report_template.md` (one ticker) or `references/market_template.md` (the market
thermometer). Python does the arithmetic; Claude does the explaining. Probabilities are risk-neutral, read from
option prices. The output describes the options market. It is not advice.

## Setup (once)

1. `pip install -r requirements.txt` (pandas, numpy and pyarrow; Python 3.11+, built with Python 3.13, pandas 3.0,
   numpy 2.5 and pyarrow 26). Windows also gets `tzdata`.
2. **CBOE needs no key.** The machine must reach `cdn.cboe.com` and `cdn-api.cboe.com` (CBOE answers every request
   with a 307 redirect to `cdn-api`). Add `financialmodelingprep.com` when FMP is used. In a sandbox with a network
   allowlist, all three hosts must be on it.
3. **FMP is optional.** Without it the chain-based modules all work; earnings, realized volatility, dividends and
   the price cone show as unavailable. To use it, set the key as an environment variable, never in a file or a
   command you show:
   - Windows: `setx FMP_API_KEY "<key>"`, then open a **new** terminal.
   - macOS / Linux: `export FMP_API_KEY=<key>` in the shell profile.
   - Never print, echo or log the key, and never paste it into a reply. The scripts redact it.
4. **Route.** With an official FMP key, leave `FMP_BASE_URL` and `FMP_TLS_VERIFY` unset: requests go to
   `https://financialmodelingprep.com/stable` with certificate verification on. The client ignores
   `FMP_TLS_VERIFY=0` for the official host.
   - If `fmp_client.py check` or a run names another host (for example `route : third-party proxy at ...` or
     `HTTPS certificate verification failed for 54.236.92.205`) although the user has an official key,
     `FMP_BASE_URL` is still set. Ask the user to remove it (PowerShell:
     `[Environment]::SetEnvironmentVariable("FMP_BASE_URL", $null, "User")`), then to restart Claude and any
     running `ui/server.py`; processes already running keep the old value. Never turn certificate verification
     off to get past an error.
   - Only with a third-party proxy that has a self-signed certificate (`FMP_BASE_URL` set to it), set
     `FMP_TLS_VERIFY=0` **for that one process only**, never with `setx` or in a profile:
     - PowerShell: `$env:FMP_TLS_VERIFY="0"; python scripts/analyze.py NVDA`
     - cmd: `set FMP_TLS_VERIFY=0&& python scripts\analyze.py NVDA`
     - bash: `FMP_TLS_VERIFY=0 python scripts/analyze.py NVDA`
     - `ui/server.py` does this by itself only when `FMP_BASE_URL` is the old proxy host `54.236.92.205`.
5. **History store.** IV Rank, the skew percentile and open-interest changes need saved chains, because CBOE keeps
   no history. They go to `$OA_DATA_DIR` (default `~/options-data`). To move it: `setx OA_DATA_DIR "D:\options-data"`
   on Windows, then restart Claude and any running `ui/server.py`. See "History and snapshots".
6. **Check the connections:** `python scripts/cboe_client.py check SPY` (redirect, status, sizes, feed time,
   contracts) and, with a key, `python scripts/fmp_client.py check` (route and which endpoints the plan includes,
   without the key).

## Workflow

1. **Pick the symbol and the view.**
   - One ticker: a US stock, ETF or index. Indices may be typed `SPX`, `^SPX`, `$SPX` or `_SPX`; share classes
     `BRK.B`, `BRK/B`, `BRK-B` or `brk b`. A foreign suffix (`SHOP.TO`, `0700.HK`, `VOD.L`) is refused: suggest the
     US listing or ADR.
   - The market thermometer: `--market` (SPX, SPY, QQQ, IWM and VIX). Use it for VIX questions too: VIX options
     settle to VIX futures, so the ticker view refuses `VIX`. `DJX` is not supported yet.
   - Chinese requests: 期权, 期权链, 隐含波动率, 预期波动, 财报波动, 波动率偏斜, 期限结构, 持仓量, 未平仓, 最大痛点, 卖put,
     备兑, 期权策略 → the ticker view; 大盘情绪, 恐慌指数, 市场温度, VIX → `--market`.
2. **Run** from the skill folder:
   `python scripts/analyze.py TICKER [--out DIR] [--data-dir DIR] [--refresh] [--no-save] [--no-market-context]`
   or `python scripts/analyze.py --market [--out DIR] [--data-dir DIR] [--refresh] [--no-save]`.
   - Output goes to `./oa-output/<TICKER>_<as_of>/` (`MARKET_<as_of>` for the market). From inside the skill
     folder it goes to `~/oa-output/` instead, so results never land in the skill.
   - An uncached stock run makes about 8 requests (4 to CBOE, 4 to FMP); the market view about 9. A chain of a
     closed session stays cached until 09:30 New York the next trading day; an open session's chain for 5 minutes.
   - A closed session's chain and summary row are saved to the history store (not with `--no-save`, not intraday,
     never overnight quotes).
   - Exit codes: 0 OK; 1 a symbol, data or CBOE problem (the message on stderr says which); 2 bad arguments. FMP
     problems never fail a run: the affected blocks say why they are unavailable.
3. **Read `metrics.json`** (30-60 KB). Skip the bulky parts: `volatility.iv_rank.series`,
   `term_structure.forward_vols`, `skew.by_expiry` and `meta.config_snapshot` (the templates say when one is
   needed). The strategy tables are `strategies.<screen>.rows`; the chart's longer candidate lists live only in
   `chart.html`.
4. **Write `report.md`** next to `metrics.json`, following `references/report_template.md` (ticker) or
   `references/market_template.md` (market) exactly: the sections in fixed order, the key paths and the wording
   rules. Write it in the user's language.
5. **Reply** with the report's 3-5 key points, any data warning, the paths to `report.md` and `chart.html`, and
   the disclaimer last.

## Local UI

`python ui/server.py [--port 8987] [--no-browser]` serves http://127.0.0.1:8987.
- Type a symbol, or switch to Market, and get the same analysis and chart in the browser.
- The interface is English in version 1 (the text lives in `ui/i18n.js`, ready for another language).
- It listens on localhost only and refuses requests addressed to any other host name. On Windows it reads
  `FMP_*` and `OA_*` values saved with `setx` even in an older terminal.
- It is optional; the command line is the main path.

## Interpretation rules

- **Numbers come only from `metrics.json`.** Copy them; never estimate, recompute or fill a `null`. Allowed:
  fractions as percents, IV differences as vol points, rounding. A `null` or `available: false` is reported with
  its `reason`.
- **Conditional, probabilistic wording.** Never "will". Probabilities are "the risk-neutral probability implied
  by option prices": `p_itm` is the risk-neutral probability of finishing in the money, `p_profit` of finishing
  with a profit at expiry.
- **No advice and no verdicts.** No buy / sell / hold, recommend, worth it, opportunity, cheap, expensive,
  undervalued, overvalued, attractive, safe, sure thing or win rate (in Chinese: 买入, 卖出, 持有, 建议, 推荐, 值得,
  机会, 便宜, 贵, 低估, 高估, 安全, 稳赚, 胜率 …). Exceptions: the standard names of the screened strategies (sell a
  cash-secured put, covered call, buy a call, buy a put, buy a straddle) and the fixed sentences in `metrics.json`.
- **Never call options expensive or cheap.** Write the numbers and the comparison: "30-day IV 63.1%, 17.4 vol
  points above 20-day realized volatility"; "25-delta puts trade at an IV 12.1 vol points above 25-delta calls".
- **Labels** (term shape, skew label, VIX band, gamma regime, thermometer) are copied with their value and
  threshold, without adjectives such as high, low or dangerous. "Call" and "put" are option names, never views.
- **Always state:**
  - the delay and the data time (`quote_time`), and overnight (GTH) quotes when `data_quality.overnight_quotes`;
  - open interest is the previous trading day's;
  - the GEX assumption (customers sell calls and buy puts), and that single-stock GEX is low confidence;
  - unusual-activity direction is unknown; max pain is weak evidence;
  - stock and ETF options are American (early assignment, above all for in-the-money calls before an
    ex-dividend date); index options are European and cash-settled;
  - strategy prices are mid prices of a delayed snapshot, wider after the close, not executable quotes;
  - the IV percentile's basis: IV Rank with n days of history, or a CBOE volatility index (VIX, VXN, RVX,
    VXAPL ...), or "history building, n of 60 days";
  - `data_source` naming a third-party proxy, and `meta.connection.fmp.tls_verify` false;
  - this skill's `rr25` is put IV minus call IV, the opposite sign of the usual risk reversal.
- **Unavailable modules keep their section** with one line and the reason.
- End every report and summary with `meta.disclaimer`.

## Options

| Option | Values | Default | Effect |
|---|---|---|---|
| `TICKER` | e.g. `NVDA`, `SPY`, `SPX`, `BRK.B` | required unless `--market` | US stock, ETF or index (not VIX, not DJX) |
| `--market` | flag | off | the market thermometer (SPX, SPY, QQQ, IWM, VIX) instead of one ticker |
| `--out` | folder | `./oa-output/<TICKER>_<as_of>/` | where `metrics.json` and `chart.html` go |
| `--data-dir` | folder | `$OA_DATA_DIR`, else `~/options-data` | the history store |
| `--refresh` | flag | off | ignore the CBOE and FMP caches and fetch again |
| `--no-save` | flag | off | do not store today's chain and summary row |
| `--no-market-context` | flag | off | skip the SPX / VIX market context of a ticker run |
| `OA_DATA_DIR` | folder | `~/options-data` | history store (chains, summaries, snapshot log, tickers.txt) |
| `OA_CACHE_DIR` | folder | `~/.cache/cboe-options` | CBOE response cache |
| `FMP_API_KEY` | key | unset | enables the FMP blocks |
| `FMP_BASE_URL` | URL | unset (official FMP) | only for a third-party proxy |
| `FMP_TLS_VERIFY` | `0` / `1` | `1` | `0` only per process, only for a proxy; ignored for the official host |
| `FMP_CACHE_DIR` | folder | `~/.cache/cboe-options-fmp` | FMP response cache (never contains the key) |

All analysis thresholds live in `scripts/config.py`, and each run records them in `meta.config_snapshot`.
`metrics.json` is an internal format and may change between versions.

## History and snapshots

- `python scripts/snapshot.py [TICKER ...] [-f FILE] [--data-dir DIR] [--force] [--raw] [--sleep SECONDS]` saves
  the whole chain of each symbol (parquet) and one summary row per trading day. Without symbols it reads
  `<data-dir>/tickers.txt`, written from a default list (SPY, QQQ, IWM, SPX, VIX and seven large stocks) the first
  time. `python scripts/snapshot.py --rebuild-summary [TICKER ...]` rebuilds the summaries from the stored chains.
- Run it once per trading day between 16:30 and 20:00 New York time: SPX, XSP and VIX options trade overnight from
  20:15 and an overnight version is never stored. Files are named after the trading day the data belongs to, so
  extra runs (weekends, holidays, retries) skip what is saved. Exit code 1 means at least one symbol failed.
- The schedule is the user's to create (Windows Task Scheduler or cron); `README.md` has the exact commands. Every
  ticker run of a closed session also saves its chain.
- IV Rank needs 60 stored days. Until then the report uses the 1-year percentile of a CBOE volatility index where
  one exists (VIX for SPX, SPY and XSP; VXN for NDX and QQQ; RVX for RUT and IWM; VXAPL, VXAZN, VXGOG for AAPL,
  AMZN, GOOGL) and otherwise says "history building, n of 60 days".
- A cloud or sandbox session keeps no history between sessions: say so when IV Rank is unavailable there.

## Edge cases

- **ETFs and indices** have no earnings, earnings straddle or ex-event IV. An index has no covered call. These
  show as unavailable with a reason; that is expected.
- **VIX** in the ticker view exits 1 with a message: use `--market`. `DJX` exits 1 as unsupported.
- **An index not in the built-in table** (e.g. `OEX`): CBOE answers 403 for `OEX` and the run retries `_OEX`, adds
  the note "index not in INDEX_SYMBOLS: settlement and exercise style unverified" and has no FMP data. Mention it.
- **Overnight quotes** (SPX, XSP and VIX after 20:15 New York): option prices come from the GTH session, the
  underlying is still the 16:00 close, and the spot used is the first expiry's forward
  (`snapshot.spot_basis = forward_first_expiry`). Say so; nothing is saved.
- **Intraday data** (`data_quality.session_closed` false): values change during the day; ranks compare an
  intraday value with closing history; nothing is saved; contracts expiring today are left out of the expected
  move, the gamma profile and the IV self-check.
- **Stale data** (`data_quality.stale.is_stale`): the feed is older than the last trading day; report it first.
- **Early-close days** (13:00 New York) and holidays come from the calendar in `scripts/config.py`, which needs a
  yearly update.
- **Newly listed expiries** with strikes on one side of the forward only are dropped
  (`term_structure.dropped_expiries`); a kink that disappears with them was not real.
- **Thin chains**: the strategy screens need open interest ≥ 100 and a spread ≤ 25% of the mid; an empty table
  says so. Wide spreads after the close are normal.
- **NDX** uses QQQ daily closes, rescaled to NDX, for realized volatility and the price cone (note `ndx_proxy`):
  FMP's `^NDX` is not on the plan.
- **No FMP:** earnings, realized volatility, dividends and the cone are unavailable; ETFs are told from stocks by a
  built-in list. **A rejected key or an unreachable FMP** is listed in `meta.notes` and never stops the run.
- **Module failure:** `available: false`, `reason: "internal error: ..."` and an entry in `meta.errors`. The rest
  of the report stands; name the gap.

## Troubleshooting

- `python scripts/cboe_client.py check [SYMBOL]` shows the final URL after the redirect, the HTTP status, sizes,
  the feed time and the contract count. A 403 with `AccessDenied` means CBOE has no such symbol (indices need the
  `_` form, which the scripts add); a 403 without it, or timeouts, mean a network block: check the allowlist
  (`cdn.cboe.com` and `cdn-api.cboe.com`).
- `python scripts/cboe_client.py clear-cache` deletes cached chains and index files.
- `python scripts/fmp_client.py check [SYMBOL]` shows the FMP route and which endpoints work on the plan;
  `python scripts/fmp_client.py clear-cache` clears the FMP cache and the saved proxy detection.
- "certificate verify failed" on the official FMP API: something on the network is intercepting HTTPS. Do not turn
  verification off. With a third-party proxy, see Setup 4.
- `python -m pytest -q -p no:cacheprovider` runs the offline tests (no network, no key) on synthetic chains.

## References

- `references/report_template.md`: the ticker report's sections, key paths, tables and wording rules. Read it
  before writing a ticker `report.md`.
- `references/market_template.md`: the same for the market thermometer report.
- `references/methodology.md`: data, sessions and time, the forward and ATM IV, every module's formulas, the
  approximations and the limits. Read it when the user asks how a number is computed.
- `references/strategies.md`: the five strategy screens, their filters, columns and the wording for them. Read it
  before writing the strategy section or answering a question about selling puts or covered calls.
