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
  against the NYSE calendar. Holidays move expiries: the Juneteenth holiday on Friday 2027-06-18 moves that
  monthly expiry to Thursday 2027-06-17.
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
- **Requests:** at least `CBOE_MIN_INTERVAL` = 1 second apart; `CBOE_TIMEOUT` = 60 seconds each; up to
  `CBOE_ATTEMPTS` = 4 tries on 429, 5xx, a 403 without `AccessDenied` or a network error (waits 2, 4 and 8
  seconds); after two tries with no HTTP response at all the client fails fast. FMP: at most
  `FMP_MAX_PER_MIN` = 280 requests per minute.

### 1.6 Optional or missing data

- FMP is optional. Without `FMP_API_KEY`, or with a rejected key, an unreachable server or an endpoint
  outside the plan, the affected blocks say `available: false` with a reason ("FMP not configured", "FMP
  key rejected", "FMP unreachable: ..."), `meta.notes` or `meta.restricted` records it, and the run still
  exits 0. Stock / ETF classification then falls back to `KNOWN_ETFS` (SPY, QQQ, IWM, DIA and others),
  otherwise "stock".
- `data_source` names the route: "CBOE delayed quotes (~15 min) + FMP stable API (official)", "... via
  third-party proxy {host}", or "...; FMP not configured". `meta.connection` gives each route's base URL,
  whether TLS verification is on, and the call counts, never the key.

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
   D = e^(−`DISCOUNT_RATE` · T) with `DISCOUNT_RATE` = 0.04 for European roots (`EUROPEAN_ROOTS`), D = 1
   otherwise. A straddle below intrinsic value or above the σ = 10 price has no solution: then `atm_iv =
   atm_iv_cboe` and `atm_iv_source = "cboe_fallback"`.
   - Why: CBOE's own short-dated SPX IVs do not match its quotes (a 1-day SPX straddle backs out about 0.92
     × CBOE's IV), so the straddle price, not the reported IV, sets the ATM level.
6. **Wings:** the 25-delta put and call IVs (`put25_iv`, `call25_iv`) are CBOE's per-contract IVs, averaged
   per delta value and interpolated linearly at Δ −0.25 (puts with −0.50 < Δ < −0.02) and +0.25 (calls with
   0.02 < Δ < 0.50); null when −0.25 / +0.25 is outside the sample.

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
- History comes from the local store (section 13): H = the last `IVR_LOOKBACK − 1` (251) non-null summary
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

## 13. History store and snapshots (`scripts/history.py`, `scripts/snapshot.py`)

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
  `--rebuild-summary` compute it with the same function, so the three always agree.
- VIX (futures-settled) is stored as a chain only, without a summary row.
- **Schedule:** once per trading day between 16:30 and 20:00 New York (SPX, XSP and VIX options trade
  overnight from 20:15). A missed day cannot be recovered. A cloud or sandbox session keeps no history.

---

## 14. Data-quality checks (`data_quality`)

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

The gamma check shows that CBOE's gamma is consistent with CBOE's IV; it does not prove the IV matches the
quotes, which is why the ATM IV comes from the straddle.

---

## 15. Approximations

| Item | Approximation | Why |
|---|---|---|
| Forward | median of K + C − P near the money (zero rates, no dividend model) | needs no rate or dividend inputs; accurate near the money; approximate for American options |
| ATM IV | Black-76 inversion of the ATM straddle; European roots discounted at 4%, American roots undiscounted | matches the traded prices, unlike CBOE's short-dated SPX IVs; early exercise is ignored |
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

## 16. Chart and local UI

- `chart.html` is self-contained (inline CSS, JavaScript and data; no external URLs) and opens offline.
  The local UI (`ui/server.py`, 127.0.0.1 only) serves the same page for any symbol and the market view.
- Every number is computed in Python (`scripts/chart.py`); the page only draws. Prices keep
  `CHART_PRICE_DECIMALS` (4) decimals, other floats `CHART_VALUE_SIG_DIGITS` (6) significant digits. Large
  chains are aggregated first (target payload < 1.5 MB).
- Ticker page: KPI tiles; the expected-move cone over the last 60 closes; the term structure with forward
  volatilities, kinks and the earnings markers; the smile of the expiries nearest 7, 30 and 60 days plus
  E2 (|K/F − 1| ≤ 0.30); open interest and volume by strike (±25%) per window; GEX by strike and the gamma
  profile; past earnings reactions; the strategy candidates (up to `STRAT_CHART_ROWS` = 60 per screen); an
  option-chain table (±20%); unusual activity.
- Market page: KPIs; the thermometer; SPX term structure and GEX; the skew board; expected moves; PCR;
  VIX and VIX3M over the last 252 days; history sparklines once history exists.
- The page text lives in `ui/i18n.js` (English in version 1; the structure takes another language).

---

## 17. Output conventions and limitations

**`metrics.json`** (an internal format; it may change between versions).
- Ticker top-level keys in order: `mode`, `ticker`, `as_of`, `quote_time`, `data_source`, `snapshot`,
  `data_quality`, `market_context`, `expected_move`, `term_structure`, `skew`, `volatility`, `earnings`,
  `positioning`, `gamma`, `flow`, `strategies`, `history`, `meta`.
- Market: `mode`, `as_of`, `quote_time`, `data_source`, `snapshot`, `data_quality`, `vix`,
  `term_structure`, `skew_board`, `gamma_board`, `expected_moves`, `positioning`, `realized`,
  `thermometer`, `history`, `meta`.
- **Units:** IVs, volatilities, percentages, skews and probabilities are fractions (0.25 = 25%).
  Exceptions: volatility index levels are index points (VIX 18.5); `*_pctl`, `pctl_1y` and IV Rank are
  0-100; prices are as quoted; dollar GEX is per 1% move.
- **Values:** floats rounded to `METRICS_SIG_DIGITS` (6) significant digits; NaN and infinity are `null`;
  dates `YYYY-MM-DD`; times New York ISO with offset.
- **Text:** `notes`, `warnings` and `reason` are English. Fixed sentences also carry ids in the parallel
  lists `note_ids` / `warning_ids` (and `reason_id`) for the UI.
- **`meta`:** network calls and cache hits (CBOE + FMP), restricted endpoints, errors, skipped members,
  `modules_unavailable` (every unavailable block with its reason), notes, the connection (never the key),
  timings, versions, `config_snapshot` and the disclaimer.

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
