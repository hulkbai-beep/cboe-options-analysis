# Methodology

How every number in `metrics.json` is produced. Parameter names in `CODE` refer to `scripts/config.py`,
the only place parameters live; the values quoted here are the defaults, and each run records the key ones
in `meta.config_snapshot`.

**Principles**
- **Python computes, Claude explains.** Every figure comes from `scripts/`; the report copies it. No
  figure is estimated in prose.
- **One snapshot, one time.** Everything is read from one CBOE delayed snapshot of the whole chain. The
  data time, the session state and the spot used are decided once (section 1.4) and drive every module.
- **Risk-neutral, not predictive.** Probabilities and expected moves are implied by option prices. They
  include the premium investors pay for protection and are not forecasts.
- **Nothing is filled in.** A missing input gives `null` and a reason. A missing module gives
  `available: false` with its `reason`. A module that fails becomes `available: false` with
  `reason: "internal error: ..."` and an entry in `meta.errors`; the other modules still run.
- **No recommendations.** Labels are fixed rules with their thresholds; strategy screens are arithmetic.

---

## 1. Data, time and sessions

### 1.1 Sources

| Data | Source | Used for |
|---|---|---|
| Option chain (all expiries and strikes, quotes, IV, Greeks, OI, volume) | CBOE delayed quotes, `cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json`, about 15 minutes behind, no key | everything except the items below |
| Volatility index histories (VIX, VIX3M, VIX9D, VXN, RVX, VXAPL, VXAZN, VXGOG) | CBOE, `cdn.cboe.com/api/global/us_indices/daily_prices/{INDEX}_History.csv` | the IV percentile from day one; the thermometer's VIX percentiles |
| Name, ETF or fund flag | FMP `profile` (optional) | stock / ETF classification |
| Earnings dates | FMP `earnings` (stocks only) | earnings module, event expiries, "crosses earnings" fields |
| Ex-dividend dates and amounts | FMP `dividends` (stocks and ETFs) | the strategy screens' ex-dividend fields |
| Daily closes, about 3 years (`EOD_LOOKBACK_DAYS` = 1130 calendar days) | FMP `historical-price-eod/full` (split-adjusted, not dividend-adjusted) | realized volatility, past earnings reactions, the price cone |
| Treasury par yields of one day, 1 month to 30 years | Massive `/fed/v1/treasury-yields` (optional) | the discount rates of European index straddles (section 2.1) |
| Short interest, daily short sale volume, free float | Massive `/stocks/v1/short-interest`, `/stocks/v1/short-volume`, `/stocks/vX/float` (stocks and ETFs, optional) | `positioning.short` (section 8.1) |
| Headlines with the provider's sentiment label per ticker | Massive `/v2/reference/news` (stocks and ETFs, optional) | `news` (section 13) |
| Exchange holidays and early closes | Massive `/v1/marketstatus/upcoming` (optional) | `data_quality.calendar_check` (section 15) |
| Ex-dividend dates and amounts when FMP gives no answer | Massive `/stocks/v1/dividends` (optional) | the strategy screens' ex-dividend fields |

- Massive (formerly Polygon.io) is optional: without `MASSIVE_API_KEY` nothing is asked and every block it feeds
  says "Massive not configured" (section 1.7).
- CBOE answers every request with a 307 redirect to `cdn-api.cboe.com`; both hosts must be reachable.
- Symbols are case-sensitive at CBOE; indices use an underscore (`_SPX`), share classes a dot (`BRK.B`).
  `scripts/symbols.py` normalizes `$SPX`, `^SPX`, `brk b`, `BRK/B` and similar spellings. A symbol CBOE
  does not know returns HTTP 403 with an `AccessDenied` body (gzip-compressed), reported as "No option
  chain on CBOE". A plain-letter symbol that is not found is retried once with the underscore (an index
  outside `INDEX_SYMBOLS`, such as OEX); it is then treated as a PM-settled, American-style index with the
  note "index not in INDEX_SYMBOLS: settlement and exercise style unverified" and no FMP data.
- Index daily bars on FMP: SPX and XSP use `^GSPC` (XSP × 0.1), RUT `^RUT`. NDX has no FMP bars on the
  current plan: QQQ's daily log returns stand in for realized volatility, and QQQ closes rescaled to NDX
  (close × NDX spot / last QQQ close) draw the price cone (note "HV from QQQ as a proxy for NDX").
- **Not used from the CBOE feed** because they are unreliable after the close: the underlying's
  `prev_day_close` (rewritten to the day's close by later regenerations), `price_change_percent`, and an
  `iv30_change` of 0.0 (treated as missing).
- VIX options settle to VIX futures, so the ticker view refuses VIX (and VIXW); DJX is not supported.

### 1.2 The chain

- **Parsing.** OCC symbols are split with `^(root)(YYMMDD)(C|P)(strike × 1000, 8 digits)$`. Numbers are
  coerced; `open_interest` and `volume` become integers.
- **Valid quote:** `bid > 0`, `ask > 0` and `ask ≥ bid`; then `mid = (bid + ask) / 2`, otherwise the mid
  is missing. **Valid IV:** `IV_MIN` < iv < `IV_MAX` (0.01 to 5.0). CBOE writes iv = 0 for "no IV"; that is
  not counted as an outlier.
- **Standard roots** (`STANDARD_ROOTS`): SPX → SPXW, SPX; NDX → NDXP, NDX; RUT → RUTW, RUT; XSP → XSP;
  VIX → VIX, VIXW; any other symbol → the symbol without "." and "_" (BRK.B → BRKB). Other roots (for
  example the binary-like `B1SPY`) are dropped and counted in `snapshot.chain.excluded_roots`.
- **Surface contracts:** for each expiry only one root is used, the first in the list above (SPXW on the
  third Friday). The forward, ATM IV, straddles, wings, term structure and strategy screens use the
  surface contracts. Positioning, PCR, GEX, max pain and unusual activity use every standard root.
- **Live contracts:** settlement time after the data time. After the close, contracts expiring that day
  are dropped.

### 1.3 Time

- **Settlement time:** 09:30 New York on the expiry date for AM-settled roots (`AM_SETTLED_ROOTS`: SPX,
  NDX, RUT, VIX, VIXW); otherwise the close of the expiry day (16:00, or 13:00 on `EARLY_CLOSES`).
- **Time to settlement** `T = max(settlement − data time, MIN_T_SECONDS = 1 hour) / 365 days`, in
  calendar years. Both times are converted to UTC before subtracting, so daylight-saving changes count
  correctly.
- `dte = (expiration − as_of)` in calendar days, for display and filters.
- **Exchange calendar:** `MARKET_HOLIDAYS` and `EARLY_CLOSES` (2026-2027). They need a yearly update
  against the NYSE calendar; with Massive, each run compares them with the exchange's calendar (section 15).
  Holidays move expiries: the Juneteenth holiday on Friday 2027-06-18 moves that monthly expiry to Thursday
  2027-06-17.
- `busdays(a, b)` counts trading days d with a ≤ d < b (weekends and `MARKET_HOLIDAYS` skipped), like
  `numpy.busday_count`.

### 1.4 Sessions

- `feed_time` (`quote_time` in `metrics.json`) is the feed's `timestamp` (UTC) in New York time.
- `as_of`, the trading day the data belongs to, is the date of the underlying's `last_trade_time`
  (New York time), or the feed date when that is missing. Output folders and history files carry `as_of`,
  never the run date.
- **Closed** when any of: the feed date is after `as_of`; `feed_time ≥ close + SESSION_BUFFER_MIN`
  (15 minutes, the CBOE delay); the last trade is within 1 second of the close (stocks often show
  15:59:59).
- **Overnight quotes** (`overnight_quotes`): closed, the chain has `GTH_ROOTS` (SPX, SPXW, XSP, VIX,
  VIXW), and `feed_time ≥ 20:15` New York (`GTH_START`). Option quotes then come from Cboe's global trading
  hours while the underlying is still the 16:00 close. The data time becomes `feed_time`, the spot becomes
  the forward of the first surface expiry with dte ≥ 1 (`spot_basis = forward_first_expiry`; the surface is
  computed twice), a warning is added, and nothing is stored in the history.
- **Spot and data time otherwise:** closed → the underlying's `close` at the session close
  (`spot_basis = close`); intraday → `current_price` at `feed_time` (`spot_basis = current_price`).
- **Previous close and change:** `prev_close = price − price_change` (price = `close` after the close,
  `current_price` intraday), `change_pct = price_change / prev_close`. When the feed's `prev_day_close`
  differs from that by more than `PREV_CLOSE_ROLL_TOL` (0.005), `data_quality.prev_close_rolled` records it.
  A missing or zero `price_change` falls back to FMP's previous close, else null.
- **Intraday data** (`session_closed` false): values change during the day; a warning says so; nothing is
  stored; IV ranks compare an intraday value with closing history (note); contracts expiring today are
  left out of the expected move, the gamma profile and the IV self-check.
- **Stale data:** `stale.business_days_behind` counts trading days after `as_of` whose session had been
  open for 15 minutes (past 09:45 New York) at run time. One or more → `stale.is_stale` and a warning
  ("quotes are from {as_of}, {n} trading day(s) old"). Data from Friday's close is not stale until Monday
  09:45.

### 1.5 Caching

- CBOE responses are cached per symbol under `OA_CACHE_DIR` (default `~/.cache/cboe-options`) with their
  `ETag` and `Last-Modified`.
  - Open session (or overnight quotes): valid `CBOE_TTL_OPEN` = 300 seconds, then a conditional request; a
    304 renews the entry.
  - Closed session: valid until 09:30 New York on the next trading day.
  - SPX, XSP and VIX: a version with `feed_time` before 20:15 is kept and preferred for that `as_of`; an
    overnight version never replaces it, even with `--refresh`.
- Volatility index CSVs: cached until the next session's close (plus 15 minutes) once they contain the
  last closed trading day; otherwise re-fetched after 300 seconds.
- `--refresh` skips reading the caches but still writes them. Writes are atomic.
- FMP responses use `fmp_client.py`'s own session-based cache (`FMP_CACHE_DIR`, default
  `~/.cache/cboe-options-fmp`; the key is never stored).
- Massive responses use `massive_client.py`'s own cache with one lifetime per endpoint (`MASSIVE_CACHE_DIR`,
  default `~/.cache/cboe-options-massive`; the key is never stored; section 1.7).
- **Requests:** at least `CBOE_MIN_INTERVAL` = 1 second apart; `CBOE_TIMEOUT` = 60 seconds each; up to
  `CBOE_ATTEMPTS` = 4 tries on 429, 5xx, a 403 without `AccessDenied` or a network error (waits 2, 4 and 8
  seconds); after two tries with no HTTP response at all the client fails fast. FMP: at most
  `FMP_MAX_PER_MIN` = 280 requests per minute. Massive: at most `MASSIVE_MAX_PER_MIN` = 5 in any 60 seconds,
  across processes (section 1.7).

### 1.6 Optional or missing data

- FMP is optional. Without `FMP_API_KEY`, or with a rejected key, an unreachable server or an endpoint
  outside the plan, the affected blocks say `available: false` with a reason ("FMP not configured", "FMP
  key rejected", "FMP unreachable: ..."), `meta.notes` or `meta.restricted` records it, and the run still
  exits 0. Stock / ETF classification then falls back to `KNOWN_ETFS` (SPY, QQQ, IWM, DIA and others),
  otherwise "stock".
- Massive is optional on the same terms: without `MASSIVE_API_KEY`, or with a rejected key, an unreachable
  host, an endpoint outside the plan or the rate limit, the blocks it feeds say `available: false` with a
  reason, European index options are discounted at the fixed `DISCOUNT_RATE` (section 2.1), and the run still
  exits 0 (section 1.7).
- `data_source` names the route: "CBOE delayed quotes (~15 min) + FMP stable API (official)", "... via
  third-party proxy {host}", or "...; FMP not configured". With Massive configured (a key is set, whether or
  not each piece arrived: `meta.notes`, `meta.restricted` and `meta.skipped` say what did not), " + Massive API" follows
  the FMP part ("CBOE delayed quotes (~15 min) + FMP stable API (official) + Massive API"), or the CBOE part
  when FMP is not configured ("CBOE delayed quotes (~15 min) + Massive API; FMP not configured").
  `meta.connection` gives each route's base URL, whether TLS verification is on, and the call counts, never the
  key.

### 1.7 Massive (optional, `scripts/massive_client.py`)

- **Connection:** `MASSIVE_BASE` (https://api.massive.com); the free plan is enough. The key comes from
  `MASSIVE_API_KEY` and goes only into the `Authorization: Bearer` header: never into a URL, a cache file, a file
  name or the output. Certificates are always verified (`scripts/tls.py`: the operating system's store, certifi,
  `SSL_CERT_FILE`; one trust context per client). Redirects are never followed (the header would go with them):
  a 3xx answer is an error ("HTTP {status} on {endpoint} (redirect not followed)") and nothing is cached. A key
  with characters a header cannot carry (typographic quotes or invisible characters copied with it) counts as
  rejected.
- **Requests of a ticker run**, in order of importance:

| # | Request | When | Feeds |
|---|---|---|---|
| 1 | Treasury curve: the latest row up to `rates_cutoff(as_of)` | a chain with European roots, or a market context with an SPX chain | section 2.1 |
| 2 | Short interest: the newest `SHORT_INTEREST_ROWS` (6) settlements | stocks and ETFs | `positioning.short.interest` |
| 3 | Short sale volume: the newest `SHORT_VOLUME_DAYS` (10) days | stocks and ETFs | `positioning.short.volume` |
| 4 | Free float | stocks | `positioning.short.float` |
| 5 | News since `as_of` − `NEWS_LOOKBACK_DAYS`, newest first, at most `NEWS_FETCH` (50) | stocks and ETFs | `news` |
| 6 | Dividends with an ex-date after `as_of` | stocks and ETFs whose FMP dividends gave no answer | the strategy screens |
| 7 | Holiday calendar | every run, only when a request slot is free at once | `data_quality.calendar_check` |

  The market view asks for the Treasury curve (SPX's `as_of`) and then the holiday calendar. Uncached, a stock
  run with the market context makes 6 Massive requests and an ETF 5 (one more when FMP gave no answer on
  dividends; one fewer without the market context), a European index and the market view 2 each, an
  American-style index (OEX) 2 with the market context and 1 without. On the free plan a run that needs more than
  5 waits about a minute for the sixth (the dividends request of a stock without FMP), or skips it (the
  calendar).
- **Rate limit:** at most `MASSIVE_MAX_PER_MIN` (5, the free plan) requests in any 60 seconds, counted over every
  process that shares the cache folder through `rate-window.json` there (request times only), so consecutive
  runs, `ui/server.py` and `snapshot.py` share one budget. A request waits for a free slot ("[massive] waiting
  {n}s for the rate limit" on the console); when the slot is further away than `MASSIVE_MAX_WAIT` (65 seconds,
  one window), or than the request's own limit (0 for the holiday calendar), the request is not sent and its
  block says "skipped for Massive's rate limit: ...". An HTTP 429 is waited out once (`Retry-After` or
  `x-ratelimit-reset`, else 60 seconds) within the same limit; when Massive still answers 429, another program
  may be using the key, and the client's later requests fail at once for 60 seconds with the same reason
  instead of each waiting again (cached answers are still served). On the free plan an uncached stock
  run fills the window with its first five requests, so its calendar check is usually skipped; a later run
  fetches it and the calendar is then cached for a day.
- **Retries:** `MASSIVE_TIMEOUT` (30) seconds per response; HTTP 5xx up to 3 tries, 1 and 2 seconds apart, a
  retry only when a request slot is free at once (a retry never makes a later request wait); when two requests
  in a row end on server errors, the later requests fail at once for 5 minutes
  ("Massive answered server errors (HTTP 5xx) to 2 requests in a row: its other requests are skipped for a few
  minutes", one note in `meta.notes`). No HTTP response twice, or a failed certificate check, makes Massive
  unreachable for the rest of the run (every later request fails at once).
- **Cache:** one JSON file per request under `MASSIVE_CACHE_DIR` (the endpoint, the ticker and a hash of the
  parameters in its name, never the key). Lifetimes: news 30 minutes, short sale volume 4 hours, the Treasury
  curve 6 hours, short interest and dividends 12 hours, the holiday calendar 1 day, the free float 7 days.
  `--refresh` skips reading it; the rate limit still applies.
- **Failures become reasons** (`fetch.MassiveState`, as for FMP), never exceptions:

| Failure | Reason in the blocks | Also |
|---|---|---|
| no key | "Massive not configured" | nothing is asked; `meta.connection.massive` is null |
| key rejected (HTTP 401) | "Massive key rejected", for every later piece | `meta.notes`: "Massive key rejected: Massive data is unavailable"; one request spent |
| no response, certificate check failed | "Massive unreachable: ..." | `meta.notes` |
| endpoint outside the plan (HTTP 402, 403) | "restricted on the current Massive plan: {endpoint}" | `meta.restricted`: "Massive {endpoint} ({ticker})" |
| rate limit (also HTTP 429) | "skipped for Massive's rate limit: ..." | |
| server errors on 2 requests in a row | "Massive answered server errors (HTTP 5xx) to 2 requests in a row: ..." | `meta.notes`, once |
| any other error | "Massive error on {endpoint}: ..." | `meta.notes` |

- **`meta`:** `meta.connection.massive` = {route, base, tls_verify, calls} (null without Massive);
  `meta.network_calls` and `meta.cache_hits` include Massive. When Massive is configured, `meta.skipped` names
  each piece that did not arrive, with its reason: `short_interest`, `short_volume`, `float` (stocks), `news`,
  `treasury_curve` (runs that discount European options: the chain's or the market context's SPX),
  `calendar_check`. `dividends` there
  means neither FMP nor Massive answered ("FMP not configured; Massive: ...").
- **Command line:** `python scripts/massive_client.py check [SYMBOL]` probes each endpoint on the plan (default
  NVDA) and compares the holiday calendar with `scripts/config.py`; `get PATH key=value ...` prints the raw JSON;
  `clear-cache` deletes the cache.

---

## 2. Forward and ATM implied volatility (`scripts/surface.py`)

Per expiry, on the surface contracts:

1. **Forward.** Among strikes with valid call and put quotes within `FWD_STRIKE_RANGE` (±20%) of spot,
   take the `FWD_MEDIAN_N` (5) with the smallest |C − P| and set F = median(K + C − P). This is put-call
   parity at zero rates: accurate near the money, approximate for American options. No usable strike → the
   expiry has no forward.
2. **Bracket.** K_lo = the highest strike ≤ F and K_hi = the lowest strike ≥ F among strikes whose call and
   put both have a valid quote and a valid IV. Only interpolation is allowed: both must exist and
   (K_hi − K_lo) / F ≤ `ATM_MAX_BRACKET` (0.10). Otherwise the expiry is dropped from the expected move, the
   term structure and the skew, and listed in `term_structure.dropped_expiries` (newly listed expiries with
   strikes on one side only would otherwise create false kinks and negative forward variance).
3. **CBOE's ATM IV** `atm_iv_cboe`: the call/put average IV at K_lo and K_hi, interpolated linearly at F.
4. **ATM straddle:** K_atm = whichever of K_lo, K_hi is closer to F (ties: the lower). `straddle = C_mid +
   P_mid`, `straddle_pct = straddle / spot`.
5. **ATM IV** `atm_iv` (used everywhere downstream): solve D · [B76_call(F, K_atm, σ, T) + B76_put(F, K_atm,
   σ, T)] = straddle by bisection on σ in [0.0001, 10] to a width of 1e-10 (at most 200 steps).
   B76 straddle = D · [F(2N(d₁) − 1) − K(2N(d₂) − 1)], d₁ = (ln(F/K) + σ²T/2)/(σ√T), d₂ = d₁ − σ√T.
   D = e^(−r(T) · T) for European roots (`EUROPEAN_ROOTS`), with r(T) from the Treasury curve of section 2.1
   (two trading days before the data date), or the fixed `DISCOUNT_RATE` = 0.04 without one; D = 1 otherwise. A straddle below intrinsic value or
   above the σ = 10 price has no solution: then `atm_iv = atm_iv_cboe` and `atm_iv_source = "cboe_fallback"`.
   - Why: CBOE's own short-dated SPX IVs do not match its quotes (a 1-day SPX straddle backs out about 0.92
     × CBOE's IV), so the straddle price, not the reported IV, sets the ATM level.
6. **Wings:** the 25-delta put and call IVs (`put25_iv`, `call25_iv`) are CBOE's per-contract IVs, averaged
   per delta value and interpolated linearly at Δ −0.25 (puts with −0.50 < Δ < −0.02) and +0.25 (calls with
   0.02 < Δ < 0.50); null when −0.25 / +0.25 is outside the sample.

### 2.1 Discount rates (`snapshot.rates`)

- **Where a rate enters:** only the straddle inversion of European roots (step 5): SPX, SPXW, NDX, NDXP, RUT,
  RUTW, XSP (and VIX, VIXW, which the ticker view refuses). Their `atm_iv` and everything built on it (expected
  moves, constant-maturity IVs, the term structure, the event move) depend on it. American roots (stocks and
  ETFs) are undiscounted; the forward, the wings, gamma and the strategy probabilities use no rate.
- **The Treasury curve (Massive):** the par yields of one day from `/fed/v1/treasury-yields`
  (`TREASURY_TENORS`: 1 and 3 months, 1, 2, 5, 10 and 30 years; bond-equivalent, in percent) become continuously
  compounded rates r = 2 ln(1 + y/2), y as a decimal. r(T) is linear in T between the tenors and flat before 1
  month and beyond 30 years; D = e^(−r(T) · T). A yield that is not a number or lies outside −5% to 30% is
  left out.
- **Which day:** the latest curve dated on or before `rates_cutoff(as_of)`, `RATES_LAG_DAYS` (2) trading days
  before `as_of`. The Federal Reserve's H.15 posts a day's yields on the next business day (about 16:15 New
  York), so that curve is always published before the day's snapshot (16:30-20:00) or any analysis of `as_of`,
  and a live run and `--rebuild-summary` months later pick the same curve for the same day. A curve more than
  `RATES_MAX_AGE_DAYS` (7) calendar days older than `as_of` is not used.
- **Fixed fallback:** without a usable curve (no key, a failure, a curve too old, a row without yields) European
  straddles are discounted at `DISCOUNT_RATE` (0.04), as before Massive. `snapshot.rates.reason` says why, and
  when the chain has European expiries the note "European index options discounted at the fixed 4% rate:
  {reason}" is added.
- **When it is asked for:** when the chain has European roots, or the market context has an SPX chain (SPX is
  European). A stock, ETF or American-style index (OEX) run with `--no-market-context`, or one whose market
  context has no SPX chain, asks for none: `source` `fixed` with the reason "not needed: no European-style
  options in this run", and no note.
- **`snapshot.rates`:** `source` (`treasury` or `fixed`), `european` (this chain has European expiries; false
  for a stock, ETF or American-style index, whose block then describes the curve the market context's SPX
  used), `curve_date`, `yields` (by tenor,
  decimals, bond-equivalent), `rate_30d` and `rate_1y` (the continuous rates r(30/365) and r(1) used for
  discounting), `fixed_rate` (`DISCOUNT_RATE`), `reason` (fixed only), `notes`. The market view has the same
  block, for SPX.
- **One computation everywhere:** the same rule picks the curve for the market context's SPX, the market view's
  members, `snapshot.py` (each trading day with the curve of its own cutoff) and `--rebuild-summary` (the
  Treasury rows of the whole period in one request per calendar year, each day with the latest curve on or before
  its cutoff within `RATES_MAX_AGE_DAYS`), so a summary row from an analysis, from `snapshot.py` and from
  `--rebuild-summary` is the same computation on the same curve (section 14). A day without a usable curve falls
  back to `DISCOUNT_RATE` (also the days of a calendar year whose request failed in a rebuild; the other years
  keep their curves), and `snapshot.py` logs which rate it used. A row saved while Massive was unavailable
  differs from a rebuild that can read the curve, by the size below.
- **Size of the effect:** a rate difference Δr scales a European ATM IV by about e^(Δr · T): for 1 percentage
  point, about 0.1% of the IV at 30 days and 1% at one year (0.2 vol points on an IV of 20%). Summary rows of a
  European index saved with and without the curve differ by that much.

---

## 3. Expected move (`expected_move`)

- Per expiry: `straddle_pct` (the market's expected absolute move, about 0.8σ); `move_1sd_pct = atm_iv ×
  √T` (one standard deviation, about a 68% risk-neutral range); `range_1sd = spot × (1 ∓ move_1sd_pct)`.
- **Key expiries** (dte ≥ 1 with a straddle), tagged and deduplicated:
  - `next`: the first; `this_week` / `next_week`: the last expiry of this / next Monday-Friday week;
  - `monthly`: the next monthly expiry: a Friday on the 15th-21st, or a Thursday on the 14th-20th whose
    Friday is not an expiry (a holiday);
  - `30d`: dte closest to 30 (ties: the earlier); `event`: E2, the first expiry after the earnings date
    (section 7).
- `em_cm = {7d: cm_iv_7d × √(7/365), 30d: cm_iv_30d × √(30/365)}` from the constant-maturity IVs
  (section 4).

---

## 4. Term structure (`term_structure`)

- **Constant-maturity IVs** for `CM_TENORS` (7, 30, 60, 90, 180 days): from expiries with dte ≥
  `CM_MIN_DTE` (2) and an `atm_iv`, interpolate the total variance w = atm_iv² × T linearly in T and return
  √(w/T). Outside the listed range the nearest IV is carried flat and `cm_extrapolated` says whether the
  gap is material: 7d when the first expiry is ≥ `CM_EXTRAP_7D_DAYS` (3) days beyond 7; other tenors when
  the gap is more than `CM_EXTRAP_REL` (20%) of the tenor.
- **Shape:** `ratio_30_90 = cm_iv_30d / cm_iv_90d`; above `TERM_INVERTED` (1.02) → `inverted`, below
  `TERM_UPWARD` (0.98) → `upward`, otherwise `flat`. `ratio_7_30 = cm_iv_7d / cm_iv_30d` (null when 7d is
  extrapolated).
- **Forward volatilities:** expiries with dte ≥ `KINK_MIN_DTE` (7), thinned greedily so consecutive ones are
  ≥ `KINK_MIN_GAP` (5) days apart; between neighbours fv = (w₂ − w₁) / (T₂ − T₁) and `fwd_vol` = √fv
  (null with `negative_variance` when fv ≤ 0; counted in `data_quality.negative_forward_variance`).
- **Kinks:** `fwd_vol ≥ KINK_RATIO` (1.5) × the median forward vol **and** ≥ the median + `KINK_MIN_EXCESS`
  (10 vol points). `contains_earnings`: true when from ≤ earnings date ≤ to (both ends count: the timing
  of the report is unknown); null for a stock whose date is unknown; false for ETFs and indices.

---

## 5. Skew (`skew`)

- Per expiry (dte ≥ 1, bracket passed): `rr25 = put25_iv − call25_iv` and `bf25 = (put25_iv + call25_iv) /
  2 − atm_iv_cboe` (wings and centre from the same CBOE source).
- **Sign:** positive `rr25` means puts are priced at a higher IV than calls. This is the opposite sign of
  the usual call-minus-put risk reversal.
- **30-day values:** `rr25_30d` and `bf25_30d` are interpolated linearly in T across expiries with dte ≥
  `SKEW_MIN_DTE` (7), flat beyond the ends.
- **Label** (`SKEW_LABELS`): indices and ETFs: ≥ 0.08 `steep_put`, ≥ 0.03 `put`, > −0.01 `flat`, else
  `call`; stocks: 0.06, 0.02, −0.01.
- `rr25_pctl` (0-100): the percentile of today's `rr25_30d` in the stored history once 60 days exist
  (section 6.4).

---

## 6. Volatility level (`volatility`)

### 6.1 The implied series
`cm_iv_30d` (section 4) is the implied series for every comparison: IV versus realized, the KPI, IV Rank
and the thermometer. CBOE's own `iv30` (`iv30_cboe`) is shown as a check; a difference above
`IV30_CHECK_TOL` (1 vol point) adds a warning. For stocks it usually comes from the earnings event or from
CBOE's own weighting.

### 6.2 Volatility index percentile (`iv_index`)
For symbols in `VOL_INDEX` (SPX, SPY, XSP → VIX; NDX, QQQ → VXN; RUT, IWM → RVX; AAPL → VXAPL; AMZN →
VXAZN; GOOGL, GOOG → VXGOG): the last `PCTL_LOOKBACK` (252) rows of CBOE's CSV up to `as_of`; `close` is
the last row (index points), `pctl_1y` = 100 × the share of the window strictly below it. When the last
row is older than `as_of` (intraday, or before CBOE appends the day), the note "index close from {date}"
says so. Available from the first run, without history or FMP.

### 6.3 Realized volatility (`realized`, needs FMP)
Daily log returns of the FMP closes (split-adjusted price returns, not dividend-adjusted):
`hv_n = std(last n returns, ddof=1) × √252` for n = 10, 20, 60. `hv20_pctl_1y` is the percentile of today's
hv20 among the last 252 rolling hv20 values. `vrp = cm_iv_30d − hv20` (positive: implied above realized)
and `iv_hv_ratio = cm_iv_30d / hv20`.

### 6.4 IV Rank and history percentiles (`iv_rank`, `iv_rank_ex_event`)
- History comes from the local store (section 14): H = the last `IVR_LOOKBACK − 1` (251) non-null summary
  rows before `as_of`, W = H plus today's value x (today's stored row is never counted twice).
- `n_obs = len(W)`. Below `IVR_MIN_OBS` (60): `available: false`, reason "history {n} days, need 60".
- `value = (x − min W) / (max W − min W) × 100` (null when max = min); `pctl = 100 × count(W < x) / len(W)`.
- `window_label`: `1y` from 252 observations, `recent` from 60 to 251 ("IV Rank over the last n days").
- The same `rank_pctl` gives `skew.rr25_pctl` and, in the market view, the percentiles of SPX's 30/90
  ratio, each member's RR25 and net GEX.
- Intraday values get the note "intraday value vs closing history".

### 6.5 IV excluding earnings (`cm_iv_30d_ex_event`)
- `event_in_window`: for a stock, the next earnings date (including today) is no later than `as_of` + 30
  days; null when the date is unknown; false for ETFs and indices.
- false → equal to `cm_iv_30d`; true with an event move → √((cm_iv_30d² × T₃₀ − move_1sd²) / T₃₀) with
  T₃₀ = 30/365 (null when the term under the root is ≤ 0); otherwise null with the note "ex-event IV
  unavailable: earnings date unknown or event move not isolated".
- The history stores both series. An IV that contains earnings is never written into the ex-event series:
  missing values stay NaN and IV Rank skips them. With earnings inside 30 days, the report leads with the
  ex-event rank.

---

## 7. Earnings (`earnings`, stocks only)

### 7.1 The next date
- After the close: the earliest FMP earnings date ≥ `as_of` (a report dated `as_of` counts even if FMP
  already shows actual EPS: the chain's prices stopped at the close). Dated `as_of` → `event_today` and the
  note "earnings dated as_of; if released before the open, the move is already in the close".
- Intraday: dates after `as_of`, or `as_of` itself without actual EPS.
- `timing` is always "unknown" (FMP has no before/after-the-open flag and no confirmation flag).
- `trading_days_to_event = busdays(as_of + 1, next_date + 1)`.

### 7.2 The implied event move (`event_move`)
- E1 = the last surface expiry before the earnings date, E2 = the first after it; both need dte ≥ 1 and an
  `atm_iv`. An expiry on the earnings date itself is skipped (timing unknown).
- **`pre_post`** (E1 exists): the base variance σ_b² = σ₁².
- **`post_event_pair`** (no E1): E3 = the first expiry ≥ 5 days after E2 with an IV; σ_b² = (σ₃²T₃ −
  σ₂²T₂) / (T₃ − T₂), with the note "assumes the same base vol before and after the event; overstates the
  event when the term structure is inverted". A negative σ_b² leaves the move null ("negative forward
  variance between E2 and E3: event not isolated").
- `move_1sd = √(T₂ × (σ₂² − σ_b²))`: the event's own 1σ move, net of ordinary days. A term ≤ 0 gives null
  with the note "no event premium detected (an inverted term structure can mask it)".
- `expected_abs_move = move_1sd × √(2/π)`.
- **Same basis as history:** `implied_window_1sd = √(move_1sd² + REACTION_WINDOW_DAYS × σ_b² / 252)` (the
  event plus two ordinary trading days, variance accumulated per trading day);
  `implied_window_abs = implied_window_1sd × √(2/π)`.
- `event_variance_share = move_1sd² / (σ₂² T₂)`: the event's share of E2's variance.
  `straddle_e2_pct` = E2's ATM straddle / spot (it also contains the ordinary days).
- Using σ₁ as the base understates the event when the term structure is inverted and overstates it when
  it slopes upward.

### 7.3 Past reactions (`reactions`)
For FMP earnings dates d before `as_of`: move = close(first bar after d) / close(last bar before d) − 1, a
two-day close-to-close move that contains the reaction whether the report came before the open or after
the close. The last `EARNINGS_REACTIONS_MAX` (12) reports; `small_sample` below `EARNINGS_REACTIONS_MIN`
(8). Output: `n`, `mean_abs`, `median_abs`, `max_abs` and `items` [{date, move}].

### 7.4 Comparison and date check
- `ratio_implied_to_mean = implied_window_abs / mean_abs`.
- `share_above_implied_window_1sd`: the share of past |move| above `implied_window_1sd`;
  `normal_benchmark_share` = 0.3173 (the share beyond 1σ under a normal distribution) as a reference.
- `date_check` (earnings within `DATE_CHECK_MAX_DAYS` = 60 days and inside the span of the forward
  volatilities): `kink_contains_date`, `no_kink`, or `kink_elsewhere` (the option-implied event window
  does not contain the FMP date: check the date); otherwise null.

---

## 8. Positioning (`positioning`, every standard root)

- **PCR:** put volume / call volume and put OI / call OI, over all expiries (`pcr.all`) and dte ≤ 30
  (`pcr.d30`).
- **Key levels:** OI summed by (type, strike) over expiries with dte ≤ `OI_WINDOW_DTE` (45); the top
  `KEY_LEVEL_TOP` (3) call strikes in [S, 1.10 S] and put strikes in [0.90 S, S].
- **Large out-of-range positions** (`far_otm`): calls above 1.10 S and puts below 0.90 S whose summed OI is
  strictly above the largest key level of the same type; up to 3 per type. Often hedges; never support or
  resistance.
- **Max pain** (weak evidence): for the `next` and `monthly` expiries, the settlement price P among strikes
  with OI that minimizes Σ_calls OI × max(P − K, 0) + Σ_puts OI × max(K − P, 0) (ties: the lower).
- **OI change** (needs a stored chain): the base is the latest stored chain before `as_of`
  (`base_date`); ΔOI per contract, summed by (type, strike); the top `OI_CHANGE_TOP` (5) increases and
  decreases, and `net_change`. A base older than `OI_BASE_MAX_GAP_DAYS` (4 calendar days) adds a note.
  Open interest lags one trading day: the difference between the snapshots of T and T−1 is the change
  during trading day T−1.
- Open interest in the CBOE feed is the previous trading day's close (OCC updates it once each morning).

### 8.1 Short interest and short sale volume (`positioning.short`, Massive)

Stocks and ETFs only (an index: unavailable, "short data applies to stocks and ETFs"). FINRA's figures as
Massive serves them; rows dated after `as_of` are ignored, and each date counts once.
- **Short interest** (`interest`): the shares sold short and not yet covered at a settlement date. Brokers
  report it twice a month (mid-month and month-end settlement dates) and FINRA publishes it about a week later,
  so the newest figure is one to three weeks old: `age_days` = `as_of` − `settlement_date` in calendar days,
  with the note "short interest is reported twice a month, about a week after the settlement date". The newest
  `SHORT_INTEREST_ROWS` (6) settlements are read, about three months (`rows`, newest first).
  - `short_interest` and `avg_daily_volume` in shares; `days_to_cover` = short interest / average daily volume
    (Massive's value, computed when missing);
  - `pct_float` = short interest / free float (null without a free float);
  - `change_pct` = short interest / the previous settlement's − 1 (`prev_settlement_date`).
- **Free float** (`float`, stocks): `free_float`, the shares available for public trading, `free_float_pct`, the
  free float as a fraction of the shares outstanding, and `effective_date` (the latest Massive lists). An ETF
  has none ("ETFs have no free float"), so its `pct_float` is null.
- **Short sale volume** (`volume`): for each of the newest `SHORT_VOLUME_DAYS` (10) trading days, `ratio =
  short_volume / total_volume`, where both count only the trades reported to FINRA's facilities (its trade
  reporting facilities and the ADF: off-exchange trades), not the consolidated tape. `avg_ratio` is the mean
  ratio over the `days` rows. Every sale marked short counts, including market makers' short sales that fill
  buy orders, most of them hedged, so a day's ratio is not short interest and does not show who holds which
  position: note "short sale volume includes market makers' short sales, most of them hedges: read the trend,
  not one day".
- `positioning.short.available` is false only when neither the short interest nor the short sale volume is
  available; each part keeps its own `reason`. No module reads these figures: they are context.

---

## 9. Dealer gamma (`gamma`, every standard root)

- Contracts with OI > 0, a valid IV and a CBOE gamma. Dollar gamma per 1% move: gamma × OI × 100 × S² ×
  0.01, with calls + and puts −: **this assumes customers sell calls and buy puts (dealers long calls,
  short puts).**
- `net_gex_1pct`: the sum at spot with CBOE's gamma.
- **Profile:** on a grid P = S × (1 + g), g from −15% to +15% (`GEX_GRID_PCT`, `GEX_GRID_POINTS` = 601),
  Σ sign × BS_gamma(P) × OI × 100 × P² × 0.01, with Black-Scholes at r = q = 0 and each contract's own IV
  and T held fixed (sticky strike). Intraday, contracts expiring today are left out of the profile.
- **Zero gamma:** the profile's sign changes, by linear interpolation between grid points; the root
  nearest spot (null, with a note, when there is none).
- **Regime:** the sign of the profile at spot (`profile_at_spot`): `positive` or `negative`; `near_zero`
  when it disagrees in sign with `net_gex_1pct` (with a note).
- **Walls:** gamma × OI by strike; `call_wall` = the largest call strike ≥ S, `put_wall` = the largest put
  strike ≤ S.
- `applicability`: `standard` for indices and ETFs; `single_stock_low_confidence` for stocks, where the
  dealer-positioning assumption is weaker.
- Magnitudes vary with conventions (CBOE's 4-decimal gamma alone moves them by about ±8%): read the sign
  and the zero-gamma level.

---

## 10. Market context and the market thermometer (`scripts/market.py`)

- **Universe** (`MARKET_UNIVERSE`): `_SPX` (index), SPY, QQQ, IWM (ETFs); each runs the same chain modules
  as a ticker (sections 2-5, 8, 9). Plus the VIX option chain (its level and change only) and CBOE's VIX,
  VIX3M and VIX9D histories. `as_of` and `quote_time` are SPX's. A missing member keeps its row with a
  reason; SPX and the VIX history are required.
- **VIX block:** `level` (index points, the chain's close after the close, current price intraday),
  `band` (`VIX_BANDS`: < 15 low, [15, 20) normal, [20, 30) elevated, ≥ 30 very high), `prev_close`,
  `change_pct`, `pctl_1y` (the last 252 CSV rows up to `as_of`, today's value = `level`); `vix_vix3m` and
  `vix9d_vix` with their percentiles, computed on the dates both CSVs share (the last value may be the
  previous close: note "ratios from {date} close").
- **Thermometer flags** (`THERMO_FLAGS`), each {value, threshold, fired, reason}:

| Flag | Fires when | Value / threshold |
|---|---|---|
| `vix_20_or_more` | VIX ≥ `THERMO_VIX_LEVEL` (20) | VIX level / 20 (index points) |
| `term_inverted` | SPX `ratio_30_90` > 1.02 (shape inverted) | ratio / 1.02 |
| `front_stress` | SPX `ratio_7_30` > `THERMO_FRONT_RATIO` (1.10) | ratio / 1.10 |
| `skew_steep` | SPX skew label `steep_put` (RR25 ≥ 0.08) | rr25_30d / 0.08 |
| `negative_gamma` | SPX gamma regime `negative` (not `near_zero`) | profile at spot / 0 |
| `realized_above_implied` | S&P 500 hv20 (`^GSPC`) > SPX `cm_iv_30d` | hv20 / today's IV30 |

- A flag whose input is missing is null with a reason. `n_flags` counts true flags, `n_available` non-null
  ones. `label` (`THERMO_LABELS`): 0-1 `calm`, 2-3 `cautious`, 4-6 `stressed`, with nulls counted as false;
  if counting them as true would change the label, `label_uncertain` is true and `missing` lists them.
  Fewer than `THERMO_MIN_AVAILABLE` (4) available flags → no label.
- **Boards:** `skew_board` (rr25_30d, bf25_30d, label), `gamma_board` (net GEX, regime, zero gamma),
  `expected_moves` (SPX and SPY `next_week` and `monthly`), `positioning` (PCR), `realized` (^GSPC hv20,
  SPX IV30, vrp). With 60+ stored days they add history percentiles (section 6.4).
- **Market context of a ticker run** (`market_context`): the VIX level, band and percentile and SPX's
  30/90 ratio, shape, RR25, label, gamma regime and zero-gamma distance, from the same code (usually from
  the cache). `--no-market-context` skips it.

---

## 11. Strategy screens (`strategies`)

Arithmetic on the delayed chain under the risk-neutral distribution, not advice: the screens, filters,
columns and formulas are in `references/strategies.md`. In short: surface contracts with valid quotes and
IV, OI ≥ 100 and spread ≤ 25% of the mid; one contract per expiry closest to the target delta; Black-76
probabilities on each expiry's forward; per-contract dollars on a hypothetical $100,000 account.

---

## 12. Unusual activity (`flow`)

Live standard-root contracts with volume ≥ `UA_MIN_VOLUME` (500) and OI = 0 or volume / OI ≥ `UA_VOL_OI`
(2). `premium = volume × price × 100`, price = the mid, else the last trade price when it is positive and
from `as_of`. The top `UA_TOP` (10) by premium. Volume does not show buyer or seller, or opening or
closing: the direction is unknown.

---

## 13. News (`news`, Massive)

Stocks and ETFs only (an index: unavailable, "news applies to stocks and ETFs").
- **Window:** articles about the ticker published from `window_from` = `as_of` − `NEWS_LOOKBACK_DAYS` (3)
  calendar days, 00:00 UTC (`window_days`), newest first, at most `NEWS_FETCH` (50) read.
- **Each article** (`rows[*]`): `published_utc` (UTC, ending in `Z`), `publisher`, `title`, `url`,
  `sentiment`, `sentiment_reasoning`, `n_tickers` (how many tickers the article is tagged with).
- **Sentiment:** the provider labels an article per ticker. Only the label for this ticker is read:
  `sentiment` is `positive`, `neutral` or `negative`, null when the article has no label for it, and
  `sentiment_reasoning` is the provider's one-line reason. Labels for other tickers in the same article are
  ignored. The label is the provider's model output, not a computation of this skill.
- **Cleaning:** titles, publishers and reasons are third-party text. Only printable characters are kept
  (control characters such as NUL become spaces), whitespace is collapsed, and the text is cut to
  `NEWS_TEXT_CHARS` (300) characters with "…" (publishers to 80). A link is kept only when it is an http(s)
  URL with a host, at most 500 characters, without spaces, quotes, angle brackets, backslashes or backticks;
  otherwise `url` is null. An article without a title or a readable time is skipped, and one listed twice
  (the same id, or the same title and time) counts once. Nothing in the text is interpreted or acted on.
- **Output:** `n_articles` (the articles kept); `sentiment_counts` over all of them (`positive`, `neutral`,
  `negative`, and `unlabeled` without a label for this ticker); `newest` and `oldest` (publication times);
  `rows`, `NEWS_ROWS` (8) articles shown newest first, picked first from the newest articles tagged with at
  most `NEWS_FOCUS_MAX_TICKERS` (3) tickers and only then from roundups tagged with more (a list of many
  stocks says little about this one). `truncated` is true when the provider returned `NEWS_FETCH` articles:
  the window may hold more than were read, and the counts then cover only the newest 50.
- **Notes:** always "headlines and sentiment labels come from the news provider; they neither explain nor
  forecast a price or IV move"; with no article, "no articles about {ticker} since {window_from}".
- No module reads the headlines or the labels: they are context, never an input.

---

## 14. History store and snapshots (`scripts/history.py`, `scripts/snapshot.py`)

```
$OA_DATA_DIR/                       default ~/options-data
├── tickers.txt                     the snapshot list (written from SNAPSHOT_TICKERS the first time)
├── chains/YYYY-MM-DD/<key>.parquet the whole chain of one trading day
├── summary/<key>.parquet           one row of derived values per trading day
├── raw/YYYY-MM-DD/<key>.json.gz    optional (--raw)
└── logs/snapshot.log
```

- CBOE keeps no history, so IV Rank, the history percentiles and OI changes need this store.
- **Write rules:** files are named after `as_of`, the trading day the data belongs to; an existing file is
  skipped (`--force` overwrites); writes are atomic. Only closed sessions are stored; overnight versions
  never (not even with `--force`); intraday data only with `snapshot.py --force`.
- **Chain schema** (`CHAIN_COLUMNS`): the prototype's parquet columns (`trade_date, snapshot_time,
  underlying, underlying_price, underlying_close, underlying_iv30, option, root, expiration, dte, type,
  strike, bid, ask, mid, ... tick`); new columns may only be appended. `underlying_iv30` is in percent, as
  in the prototype. Pointing `OA_DATA_DIR` at a prototype data folder and running `snapshot.py
  --rebuild-summary` continues an existing history.
- **Summary row** (`SUMMARY_COLUMNS`): `trade_date`, `spot`, `iv30_cboe`, `cm_iv_7d` ... `cm_iv_180d`,
  `cm_iv_30d_ex_event`, `ratio_30_90`, `rr25_30d`, `bf25_30d`, `straddle_30d_pct`, `pcr_volume`, `pcr_oi`,
  `total_oi`, `total_volume`, `net_gex_1pct`, `zero_gamma`, `contracts_live`, `feed_time`, `closed`,
  `late_feed` (feed at or after 20:15), `summary_version`. `analyze.py`, `snapshot.py` and
  `--rebuild-summary` compute it with the same function, so the three agree whenever they read the same
  inputs. A European index row is discounted with the Treasury curve of its cutoff date, two trading days
  earlier (section 2.1), when Massive is configured, else at `DISCOUNT_RATE`.
- VIX (futures-settled) is stored as a chain only, without a summary row.
- **Schedule:** once per trading day between 16:30 and 20:00 New York (SPX, XSP and VIX options trade
  overnight from 20:15). A missed day cannot be recovered. A cloud or sandbox session keeps no history.

---

## 15. Data-quality checks (`data_quality`)

| Check | Rule | Effect |
|---|---|---|
| Quote coverage | `valid_quote_share`, `valid_iv_share`, `missing_quote_share`, `crossed_quotes`, `iv_outliers` | reported |
| ATM IV self-check `iv_price_check` | max \|atm_iv / atm_iv_cboe − 1\| over expiries with 1 ≤ dte ≤ `IV_PRICE_CHECK_DTE` (30); flagged above `IV_PRICE_CHECK_TOL` (3%). An expiry settling today (intraday, hours left) is left out, as in the expected move | warning |
| Gamma self-check `gamma_check` | on contracts with CBOE gamma > 0: Pearson correlation of CBOE's gamma with Black-Scholes gamma recomputed at spot (r = q = 0); `median_ratio` = median(BS / CBOE); ok when corr ≥ 0.95 | warning when not ok |
| iv30 check | \|iv30_cboe − cm_iv_30d\| > 1 vol point | warning |
| Forward variance | negative forward variances counted | reported |
| Dropped expiries | no forward or no bracketing strikes | listed |
| Previous close | the feed's `prev_day_close` rewritten after the close | `prev_close_rolled` |
| Stale, intraday, overnight, no live quotes | section 1.4 | warning |
| Exchange calendar `calendar_check` (Massive, both modes) | the exchange's holidays and early closes against `MARKET_HOLIDAYS` and `EARLY_CLOSES` | warning on any difference |

The gamma check shows that CBOE's gamma is consistent with CBOE's IV; it does not prove the IV matches the
quotes, which is why the ATM IV comes from the straddle.

**Calendar check** (`data_quality.calendar_check`): Massive's `/v1/marketstatus/upcoming` lists the coming
exchange holidays and early closes; the NYSE rows are read, early-close times converted to New York time. From
the New York date of the run (`checked_from`) through the last date listed (`checked_through`; `n_dates` dates
listed), every date whose status differs (`closed`, `early_close` or `open`), or whose early-close time differs,
is a mismatch (the run's own date only when Massive lists it, since an "upcoming" list may leave out the current
day; holiday names are cut to 80 printable characters): `data_quality.calendar_check.mismatches[*]` = {date, exchange, exchange_close, config,
config_close, name}, the close times "HH:MM" New York or null (an unreadable close time is not a mismatch). Any
mismatch adds the warning "the trading calendar in scripts/config.py differs from the exchange calendar on {n}
date(s) ({dates}): update MARKET_HOLIDAYS and EARLY_CLOSES" (at most five dates named), because settlement
times, sessions, staleness and trading-day counts read that calendar. The request is the last of a run and is
sent only when a request slot is free at once (section 1.7); the calendar is then cached for a day. Without it
the block is unavailable with the reason. `python scripts/massive_client.py check` prints the same comparison.

---

## 16. Approximations

| Item | Approximation | Why |
|---|---|---|
| Forward | median of K + C − P near the money (zero rates, no dividend model) | needs no rate or dividend inputs; accurate near the money; approximate for American options |
| ATM IV | Black-76 inversion of the ATM straddle; European roots discounted with the Treasury curve of two trading days earlier (Massive) or at 4%, American roots undiscounted | matches the traded prices, unlike CBOE's short-dated SPX IVs; early exercise is ignored |
| Discount curve | Treasury par yields (bond-equivalent) as continuous rates 2 ln(1 + y/2), linear in T, flat beyond 1 month and 30 years; the curve of two trading days before the data date | a recent risk-free curve, the same in every run of that day; a rate error Δr moves a European ATM IV by a factor of about e^(Δr · T) |
| Wings and BF25 | CBOE's per-contract IVs, interpolated in delta | CBOE's model for American options and dividends is not published |
| Constant-maturity IV | linear in total variance, calendar time; flat beyond the listed range | standard; weekends and holidays count as calendar days |
| Dealer gamma | sign convention (customers sell calls, buy puts); sticky-strike profile; r = q = 0 | the feed shows no customer or dealer side |
| Earnings reactions | two-day close-to-close move around the FMP date | no before/after-the-open flag |
| Event move | base vol from E1 (or E2-E3); event variance = σ₂²T₂ − σ_b²T₂; two ordinary days added per trading day | isolates the event with two or three expiries |
| `p_itm`, `p_profit` | Black-76 N(d₂) on the expiry's forward with the contract's own IV | risk-neutral and approximate for American options |
| `p_touch` | 2 × `p_itm`, only when `p_itm` ≤ 0.5 | the reflection principle without drift |
| Probabilities in general | risk-neutral, from option prices | not a forecast; usually above historical frequencies for large falls |
| Realized volatility | log returns of split-adjusted closes (price only) | FMP `/full` is not dividend-adjusted |
| NDX realized volatility and cone | QQQ returns; QQQ closes rescaled to NDX | `^NDX` is not on the FMP plan |

---

## 17. Chart and local UI

- `chart.html` is self-contained (inline CSS, JavaScript and data; no external URLs) and opens offline.
  The local UI (`ui/server.py`, 127.0.0.1 only) serves the same page for any symbol and the market view.
- Every number is computed in Python (`scripts/chart.py`); the page only draws. Prices keep
  `CHART_PRICE_DECIMALS` (4) decimals, other floats `CHART_VALUE_SIG_DIGITS` (6) significant digits. Large
  chains are aggregated first (target payload < 1.5 MB).
- Ticker page: KPI tiles; the expected-move cone over the last 60 closes; the term structure with forward
  volatilities, kinks and the earnings markers; the smile of the expiries nearest 7, 30 and 60 days plus
  E2 (|K/F − 1| ≤ 0.30); open interest and volume by strike (±25%) per window; GEX by strike and the gamma
  profile; past earnings reactions; the strategy candidates (up to `STRAT_CHART_ROWS` = 60 per screen); an
  option-chain table (±20%); unusual activity; with Massive, the short data and the headlines (third-party
  text is shown as text, and a link only when it is http(s)). The data notes include the discount rate and the
  calendar check.
- Market page: KPIs; the thermometer; SPX term structure and GEX; the skew board; expected moves; PCR;
  VIX and VIX3M over the last 252 days; history sparklines once history exists.
- The page text lives in `ui/i18n.js` (English in version 1; the structure takes another language).

---

## 18. Output conventions and limitations

**`metrics.json`** (an internal format; it may change between versions).
- Ticker top-level keys in order: `mode`, `ticker`, `as_of`, `quote_time`, `data_source`, `snapshot`,
  `data_quality`, `market_context`, `expected_move`, `term_structure`, `skew`, `volatility`, `earnings`,
  `positioning`, `gamma`, `flow`, `news`, `strategies`, `history`, `meta`.
- Market: `mode`, `as_of`, `quote_time`, `data_source`, `snapshot`, `data_quality`, `vix`,
  `term_structure`, `skew_board`, `gamma_board`, `expected_moves`, `positioning`, `realized`,
  `thermometer`, `history`, `meta`.
- **Units:** IVs, volatilities, percentages, skews, probabilities, yields and rates are fractions (0.25 =
  25%). Exceptions: volatility index levels are index points (VIX 18.5); `*_pctl`, `pctl_1y` and IV Rank are
  0-100; prices are as quoted; dollar GEX is per 1% move; short interest, short sale volumes and the free float
  (`positioning.short`) are shares, while option volume and open interest (`positioning.total_volume`,
  `positioning.total_oi`, `flow.rows[*].volume`) are contracts; `days_to_cover` is days.
- **Values:** floats rounded to `METRICS_SIG_DIGITS` (6) significant digits; NaN and infinity are `null`;
  dates `YYYY-MM-DD`; times New York ISO with offset, except the news times, which are UTC (`Z`).
- **Text:** `notes`, `warnings` and `reason` are English. Fixed sentences also carry ids in the parallel
  lists `note_ids` / `warning_ids` (and `reason_id`) for the UI. News titles, publishers and reasons are
  third-party text in the provider's words (section 13).
- **`meta`:** network calls and cache hits (CBOE + FMP + Massive), restricted endpoints, errors, skipped
  members and data pieces, `modules_unavailable` (every unavailable block with its reason), notes, the
  connection (never the key), timings, versions, `config_snapshot` and the disclaimer.

**Limitations.**
1. About 15 minutes delayed, and only the current snapshot: history exists only where snapshots were
   saved.
2. Open interest is the previous trading day's; today's change shows tomorrow.
3. Volume does not tell buyer from seller or opening from closing.
4. GEX rests on an assumption about who holds which side.
5. Per-contract IVs and Greeks are CBOE's model values; its treatment of American exercise and dividends is
   not published.
6. Option-implied probabilities are risk-neutral: they include a risk premium and are not forecasts.
7. Earnings dates come from FMP without confirmation or timing; the event expiries and every "crosses
   earnings" field depend on them.
8. Stock and ETF options are American-style and can be assigned early (above all in-the-money calls before
   an ex-dividend date and deep in-the-money puts); index options are European-style and cash-settled.
9. Spreads are wider after the close; table prices are not executable quotes.
10. The CBOE endpoint is unofficial: its address and format can change (the 2026-11 redirect is an
    example). Keep the request rate low; no commercial use or resale.
11. Short interest is reported twice a month and published about a week after the settlement date; short
    sale volume covers only trades reported to FINRA's facilities and includes market makers' hedges.
12. Headlines and sentiment labels are the news provider's; this skill neither verifies nor uses them.
13. Massive's free plan allows 5 requests a minute: a piece whose request would wait longer than
    `MASSIVE_MAX_WAIT` is skipped (`meta.skipped`); a later run fetches it.
