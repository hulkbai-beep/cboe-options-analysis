# report.md template (one ticker)

Claude writes `report.md` next to `metrics.json` after running `scripts/analyze.py TICKER`. This file
defines the section order, which `metrics.json` fields feed each section (exact key paths), the table
layouts, the sentence templates and the wording rules. The market thermometer has its own template:
`references/market_template.md`.

## Rules (apply to every section)

**Numbers**
- Every number comes from `metrics.json`. Copy it; never estimate, recompute, interpolate or fill in a
  `null`.
- **Allowed changes:**
  - unit display: a fraction as a percent (0.0587 → 5.9%); an IV difference or a skew in vol points
    (0.046 → 4.6 vol points);
  - rounding: prices and strikes 2 decimals; percents 1 decimal (2 below 1%); vol points 1 decimal;
    ratios compared with a threshold (`ratio_30_90`, `ratio_7_30`) 3 decimals; other ratios 2 decimals;
    percentiles and IV Rank 0 decimals; dollars per contract 2 decimals, totals in whole dollars; GEX in
    $ millions or billions with 1 decimal;
  - copying a sign.
- **Units in `metrics.json`:**
  - fractions: every IV and volatility (`atm_iv`, `cm_iv.*`, `hv*`, `iv`, `fwd_vol`), `vrp`, `rr25*`,
    `bf25*`, every `*_pct`, `move_1sd`, `expected_abs_move`, `implied_window_*`, `mean_abs`,
    `median_abs`, `max_abs`, `items[*].move`, `event_variance_share`, every probability (`p_itm`,
    `p_touch`, `p_profit`, `p_beyond_be_rn`, `hist_share_beyond_be`, `share_above_*`), every yield and
    return, `be_up`, `be_down`, `theta_pct_per_day`, `em_units` (a multiple of the expiry's 1σ move);
  - already 0-100: `*_pctl`, `pctl_1y`, `volatility.iv_rank.value`, `volatility.iv_rank.pctl`;
  - index points, written as they are (no percent sign): `market_context.vix.level`,
    `volatility.iv_index.close`;
  - plain ratios: `ratio_30_90`, `ratio_7_30`, `iv_hv_ratio`, `pcr.*`, `ratio_implied_to_mean`,
    `vol_oi`;
  - dollars: `premium_per_contract`, `capital`, `max_loss`, `cost_per_contract`, `flow.rows[*].premium`,
    `gamma.net_gex_1pct`, `gamma.profile_at_spot` (dollar gamma per 1% move).
- `null` means "not computable". Say so and give the reason (`reason`, or the block's `notes`). Never fill
  it in.

**Wording**
- **Conditional and probabilistic.** Write "if", "historically", "about"; never "will" or "certainly".
- **Probabilities** are always "the risk-neutral probability implied by option prices": `p_itm` is the
  risk-neutral probability of finishing in the money at expiry, `p_profit` of finishing with a profit
  at expiry. These are not forecasts: they include the premium investors pay for protection and usually
  sit above historical frequencies, above all for large falls.
- **No advice and no verdicts.** Never write buy, sell, hold, add, trim, recommend, should, worth it,
  opportunity, bargain, cheap, expensive, undervalued, overvalued, attractive, safe, sure thing, income
  you can count on or win rate (in Chinese: 买入, 卖出, 持有, 加仓, 减仓, 建议, 推荐, 值得, 机会, 划算, 便宜, 贵,
  低估, 高估, 有吸引力, 安全, 稳赚, 收租, 躺赚, 胜率). Two exceptions only: the standard names of the
  screened strategies and contracts (sell a cash-secured put, covered call, buy a call, buy a put, buy a
  straddle, call option, put option), and the fixed sentences and disclaimer in `metrics.json`.
- **Never call options expensive or cheap.** Write the numbers and the comparison: "30-day IV 43.5%,
  34.1 vol points above 20-day realized volatility"; "25-delta puts trade at an IV 4.6 vol points above
  25-delta calls".
- **Labels** (term `shape`, skew `label`, VIX `band`, gamma `regime`, liquidity grade) are copied from
  `metrics.json` with their value and threshold, without adjectives such as high, low, dangerous or
  worrying. "Call" and "put" name options; they never express a view.
- **Unavailable blocks** (`available: false`) keep their section or row with one line and the
  `reason`. `meta.modules_unavailable` lists them all.

**Always state** (in the section named, and once in section 12)
- the delay and the data time: `data_quality.delay_note` and `quote_time` (section 2);
- overnight quotes when `data_quality.overnight_quotes` is true (sections 1 and 2);
- open interest is the previous trading day's (`data_quality.oi_note`, section 10);
- the GEX assumption and, for a stock, low confidence (section 11);
- the unusual-activity direction is unknown and max pain is weak evidence (section 10);
- stock and ETF options are American-style (early assignment possible, above all for in-the-money calls
  before an ex-dividend date and deep in-the-money puts); index options are European-style and
  cash-settled (section 9);
- strategy prices are mid prices of a delayed snapshot, wider after the close, not executable quotes
  (section 9);
- the basis of the IV percentile: IV Rank with its number of days, or the CBOE volatility index used, or
  "IV history building, n of 60 days" (sections 1 and 6);
- a third-party proxy when `data_source` contains "third-party proxy": the FMP data came through that
  proxy, not FMP's official server; when `meta.connection.fmp.tls_verify` is false, also that HTTPS
  certificate verification was off (section 2);
- this skill's `rr25` is put IV minus call IV, the opposite sign of the usual call-minus-put risk reversal
  (section 8).

**Language**
- Write in the user's language: Chinese if the user asked in Chinese, otherwise English. The headings
  below give the Chinese title in brackets.
- These terms stay in English in every language: IV, HV, IV Rank, ATM, OTM, ITM, delta (Δ), gamma, GEX,
  RR25, BF25, PCR, OI, DTE, VIX (and VXN, RVX ...), AM/PM settlement.
- Enumerated labels (`shape`, `label`, `band`, `regime`, `liquidity`, tags) may be translated, with the
  English original in parentheses the first time.
- `notes`, `warnings` and `reason` texts are English sentences. Translate them faithfully and keep every
  number, date and symbol exactly. (`note_ids` and `warning_ids` are for the UI; ignore them.)

**Reading metrics.json**
- The file is 30-60 KB. Skip the bulky parts unless a section below asks for them:
  `volatility.iv_rank.series`, `volatility.iv_rank_ex_event.series`, `term_structure.forward_vols` (the
  kinks are in `term_structure.kinks`), `skew.by_expiry` and `meta.config_snapshot`.
- The strategy tables are `strategies.<screen>.rows` (at most 8 rows). The chart's longer candidate lists
  are only in `chart.html`.
- Dates are `YYYY-MM-DD`; times are New York times with their UTC offset
  (`2026-11-05T16:30:00-05:00`).

---

## Section order and contents

The report starts with a title line, then exactly these 12 sections in this order.

### 0. Title
`# {ticker} options analysis (as of {as_of})` (Chinese: `# {ticker} 期权分析（截至 {as_of}）`). Add
"· overnight quotes" when `data_quality.overnight_quotes` is true and "· intraday" when
`data_quality.session_closed` is false.

### 1. Key points (要点)
Three to five bullets, one sentence each, repeating numbers from `metrics.json` only, in this order:
1. The 1σ expected move and price range of the `next_week` and `monthly` expiries, with dates
   (`expected_move.rows[*]` with those `tags`).
2. Stocks: the next earnings date, the event move and the implied/past ratio
   (`earnings.next_date`, `earnings.event_move.move_1sd`, `earnings.comparison.ratio_implied_to_mean`).
   ETFs and indices: skip this bullet.
3. The IV level and its percentile basis, plus IV − HV20: `volatility.cm_iv_30d`, then IV Rank
   (`volatility.iv_rank_ex_event` for a stock with `volatility.event_in_window` true, otherwise
   `volatility.iv_rank`), or the CBOE volatility index percentile (`volatility.iv_index.pctl_1y`), or "IV
   history building, {n_obs} of 60 days"; and `volatility.vrp` in vol points.
4. The skew label and value (`skew.label`, `skew.rr25_30d`).
5. The most important data warning, if any (`data_quality.warnings`, overnight or intraday data, a
   stale feed, `meta.errors`).

No conclusions, no advice, nothing that is not in `metrics.json`.

### 2. Snapshot (快照)
**Fields:**
- `snapshot.name`, `ticker`, `snapshot.kind` (`snapshot.kind_source`), `snapshot.cboe_symbol`,
  `snapshot.fmp_symbol`;
- `snapshot.spot` with `snapshot.spot_basis` (`close` after the close, `current_price` intraday,
  `forward_first_expiry` with overnight quotes), `snapshot.prev_close`, `snapshot.change_pct`;
- `quote_time` (the CBOE feed time), `snapshot.session.data_time`, `snapshot.session.closed`,
  `snapshot.session.overnight_quotes`, `data_quality.delay_note`;
- `data_source`; `meta.connection.fmp.tls_verify`;
- `snapshot.iv30_cboe` and `snapshot.iv30_change` (`snapshot.iv30_change_source`);
- chain: `snapshot.chain.contracts_live` of `snapshot.chain.contracts_total`, `snapshot.chain.expirations`,
  `snapshot.chain.excluded_roots`, `snapshot.chain.valid_quote_share`, `snapshot.chain.valid_iv_share`;
- history store: `history.days`, `history.first_date`, `history.saved_this_run`, `history.notes`;
- `meta.network_calls` and `meta.cache_hits`.

**Layout:** a two-column table (Item | Value). Then the warnings as a short bullet list:
- every entry of `data_quality.warnings`, translated faithfully;
- a stale feed (`data_quality.stale.is_stale`, `data_quality.stale.business_days_behind`) first of all;
- overnight quotes: option prices come from the overnight (GTH) session while the underlying is still the
  last close, and the spot used is the first expiry's forward;
- intraday data (`data_quality.session_closed` false): values change during the day and nothing was
  saved to the history store;
- the third-party proxy and TLS lines from the rules above;
- `snapshot.notes`.
If there is nothing to list, write "No data-quality warnings."

### 3. Market context (大盘背景)
**Fields:** `market_context.vix.level`, `market_context.vix.band`, `market_context.vix.pctl_1y`;
`market_context.spx.ratio_30_90`, `market_context.spx.shape`; `market_context.spx.rr25_30d`,
`market_context.spx.skew_label`; `market_context.spx.gamma_regime`, `market_context.spx.zero_gamma_dist_pct`;
`market_context.spx.as_of`; `market_context.notes`.

**Layout:** one table of at most 4 rows (Benchmark | Reading | Rule or detail):
- VIX | {level} ({band}) | 1-year percentile {pctl_1y}; bands: < 15 low, 15-20 normal, 20-30 elevated,
  ≥ 30 very high (index points);
- SPX term structure | IV30/IV90 {ratio_30_90} ({shape}) | inverted above 1.02, upward below 0.98;
- SPX skew | RR25 {rr25_30d} vol points ({skew_label}) | index thresholds: steep put ≥ 8.0, put ≥ 3.0;
- SPX dealer gamma | {gamma_regime} | zero-gamma level {zero_gamma_dist_pct} from spot.

Unavailable (`--no-market-context`, or SPX could not be loaded): one line with `market_context.reason`.
The full market view is `python scripts/analyze.py --market`.

### 4. Expected move (预期波动)
**Fields:** for each row of `expected_move.rows`: `expiration`, `tags` (`next`, `this_week`,
`next_week`, `monthly`, `30d`, `event`), `dte`, `root` and `settlement` (indices), `forward`,
`atm_strike`, `atm_iv`, `atm_iv_source`, `straddle`, `straddle_pct`, `move_1sd_pct`, `range_1sd`;
`expected_move.em_cm` (`7d`, `30d`); `expected_move.notes`.

**Layout:**
1. A table: Expiry | Tags | DTE | 1σ range | ±1σ | ATM straddle | ATM IV. For example
   "2026-11-20 | monthly, event | 15 | 90.08-109.92 | ±9.9% | 7.9% of spot | 48.9%".
2. One line: "Over the next 7 and 30 calendar days, the constant-maturity 1σ move is ±{em_cm.7d} and
   ±{em_cm.30d}."
3. One line of reading aid: the 1σ range is the range with about a 68% risk-neutral probability implied by
   option prices; the ATM straddle costs about 0.8 of the 1σ move.

**Notes:** an `event` tag marks the first expiry after the earnings date, so its move includes the
earnings event. For indices, name AM settlement (`settlement` "AM": the final value is set at the 09:30
open on the expiry date). If an `atm_iv_source` is `cboe_fallback`, say that expiry's ATM IV is CBOE's
because the straddle price could not be inverted.

### 5. Earnings (财报)
**Fields:**
- `earnings.next_date`, `earnings.trading_days_to_event`, `earnings.event_today`, `earnings.timing`
  ("unknown": FMP has no before/after-the-open flag), `earnings.date_check`, `earnings.notes`;
- `earnings.event_move`: `method`, `e1`, `e2`, `e3`, `sigma1`, `sigma2`, `sigma_base`, `move_1sd`,
  `expected_abs_move`, `implied_window_1sd`, `implied_window_abs`, `event_variance_share`,
  `straddle_e2_pct`, `notes`, `reason`;
- `earnings.reactions`: `method`, `n`, `mean_abs`, `median_abs`, `max_abs`, `small_sample`, `items[*]`
  (`date`, `move`);
- `earnings.comparison`: `ratio_implied_to_mean`, `share_above_implied_window_1sd`,
  `normal_benchmark_share`.

**Layout:** 3-6 sentences, then optionally the reactions in one line ("2024-02-23 +1.4%, ...").
- "The next earnings date from FMP is {next_date}, {trading_days_to_event} trading days away; FMP does not
  say whether it is before the open or after the close."
- Event move: "Option prices imply an earnings-event move of about ±{move_1sd} (1 standard deviation),
  net of ordinary daily volatility (between the {e1} and {e2} expiries). On the same two-day basis as past
  reactions that is about ±{implied_window_1sd} (expected absolute move {implied_window_abs}). Over the
  last {n} reports the mean absolute two-day move was {mean_abs} (median {median_abs}, largest
  {max_abs}): implied / past = {ratio_implied_to_mean}."
- "{share_above_implied_window_1sd} of those {n} reports moved more than ±{implied_window_1sd}; under a
  normal distribution the share beyond 1 standard deviation is 31.7%."
- "Earnings account for {event_variance_share} of the variance priced into the {e2} expiry, whose ATM
  straddle costs {straddle_e2_pct} of spot."
- `method` "post_event_pair" (no expiry before the event): the base volatility comes from the two expiries
  after the event; say that this assumes the same volatility before and after the report.
- `date_check`: `kink_contains_date` → the option-implied event window contains the FMP date;
  `kink_elsewhere` → "the event window implied by option prices is {kink from-to}, which does not match
  the FMP earnings date {next_date}: check the date"; `no_kink` → no forward-volatility kink was found;
  `null` → not checked (the date is too far away or too close).
- `small_sample` true: say the sample has fewer than 8 reports.
- The reactions are two-day close-to-close moves around the FMP date (split-adjusted price returns), not
  the exact reaction.
- `event_today` true: the date is today; if the report came before the open, the move is already in the
  close (`earnings.notes`).

**Unavailable:** ETFs and indices: "Not applicable: {reason}." A stock without FMP or without a future
date: the `reason` (for example "FMP not configured"), and say that every "crosses earnings" field below
is unknown. A null `move_1sd` with the note "no event premium detected (an inverted term structure can
mask it)": write exactly that.

### 6. Volatility (波动率水平)
**Fields:**
- implied: `volatility.cm_iv_30d` (the 30-day constant-maturity ATM IV, the main IV series),
  `volatility.cm_iv_30d_ex_event` and `volatility.event_in_window` (stocks), `volatility.iv30_cboe`
  (CBOE's own figure, shown only as a check);
- realized: `volatility.realized.hv10`, `.hv20`, `.hv60`, `.hv20_pctl_1y`, `.source`, `.notes`;
- comparison: `volatility.vrp` (IV30 − HV20), `volatility.iv_hv_ratio`;
- percentile basis, in this order of preference:
  - `volatility.iv_rank_ex_event` (stocks with `event_in_window` true) or `volatility.iv_rank`:
    `value`, `pctl`, `n_obs`, `first_date`, `last_date`, `window_label`, `reason`;
  - `volatility.iv_index`: `symbol`, `close`, `date`, `pctl_1y`, `notes`;
  - neither: `volatility.iv_rank.reason` ("history n days, need 60").

**Layout:** a table (Measure | Value | Note), then 2-3 sentences:
- "30-day IV {cm_iv_30d}, {vrp in vol points} vol points above (below) 20-day realized volatility
  {hv20}; IV / HV20 = {iv_hv_ratio}." HV20 sits at the {hv20_pctl_1y}th percentile of its past year.
- With earnings inside the next 30 days: "Excluding the earnings event, 30-day IV is {cm_iv_30d_ex_event}."
  Lead with the ex-event IV Rank when it is available.
- IV Rank: "IV Rank {value} (percentile {pctl}) over the last {n_obs} trading days ({first_date} to
  {last_date})". Call it "1-year IV Rank" only when `window_label` is `1y` (252 days); `recent` means a
  shorter window.
- Volatility index: "{symbol}, CBOE's 30-day implied volatility index computed with the VIX method, closed
  at {close} on {date}, at the {pctl_1y}th percentile of the past year." A date before `as_of` comes with
  the note "index close from {date}".
- Building: "IV history building: {n_obs} of 60 days. Run `scripts/snapshot.py` daily to build it." In a
  cloud or sandbox session, add that the history does not persist there.
- Intraday: an IV Rank compares an intraday value with closing history (note "intraday value vs closing
  history").
- NDX: realized volatility comes from QQQ as a proxy (note "HV from QQQ as a proxy for NDX").
- `iv30_cboe` differing from `cm_iv_30d` by more than 1 vol point is already in the warnings: CBOE's
  figure includes its own weighting and, for stocks, any earnings inside 30 days.

**Unavailable:** realized volatility without FMP: its `reason`; the comparison is then null.

### 7. Term structure (期限结构)
**Fields:** `term_structure.cm_iv` (`7d`, `30d`, `60d`, `90d`, `180d`), `term_structure.cm_extrapolated`,
`term_structure.ratio_30_90`, `term_structure.ratio_7_30`, `term_structure.shape`,
`term_structure.median_fwd_vol`, `term_structure.kinks[*]` (`from`, `to`, `fwd_vol`,
`negative_variance`, `contains_earnings`), `term_structure.dropped_expiries`, `term_structure.notes`.

**Layout:**
1. One row table: 7d | 30d | 60d | 90d | 180d constant-maturity ATM IV. Mark an extrapolated tenor
   (`cm_extrapolated` true) "flat-extrapolated: no contract at this tenor".
2. "IV30 / IV90 = {ratio_30_90} → {shape} (inverted above 1.02, upward below 0.98); IV7 / IV30 =
   {ratio_7_30}."
3. Kinks, one bullet each: "Forward volatility between {from} and {to} is {fwd_vol}, against a median of
   {median_fwd_vol} across expiries." Then by `contains_earnings`: true → "the window contains the
   earnings date"; false → "it does not contain an earnings date: possibly a macro event or data noise";
   null → "it cannot be told whether it relates to earnings (date unknown)". No kink: "No forward-volatility
   kink (≥ 1.5 × the median and ≥ 10 vol points above it)."
4. `dropped_expiries`: list them as "newly listed or partial expiries left out (no strikes on both sides of
   the forward)".

### 8. Skew (偏斜)
**Fields:** `skew.rr25_30d`, `skew.bf25_30d`, `skew.label`, `skew.thresholds` (`steep_put`, `put`,
`flat`), `skew.rr25_pctl`, `skew.notes`; `skew.by_expiry` only when the user asks about one expiry.

**Layout:** 2-3 sentences:
- "At 30 days, 25-delta puts trade at an IV {rr25_30d} vol points above (below) 25-delta calls: RR25
  {rr25_30d} → {label} (thresholds for {stocks | indices and ETFs}: steep put ≥ {steep_put}, put ≥
  {put}, flat above {flat}, otherwise call)."
- "BF25 {bf25_30d} vol points: the average 25-delta wing IV minus the ATM IV."
- With history: "RR25 is at the {rr25_pctl}th percentile of the stored history."
- Always: "This skill's RR25 is put IV minus call IV, the opposite sign of the usual call-minus-put risk
  reversal."

### 9. Strategy screens (策略筛选)
Open with: "This is arithmetic on the risk-neutral distribution implied by option prices, not advice.
Prices are mid prices of a delayed snapshot{, after the close, when spreads are usually wider}; they are
not executable quotes."

**Context** (`strategies.context`): `cm_iv_30d`, `hv20`, `vrp`, `iv_index_pctl`, `iv_rank`,
`iv_rank_ex_event`, `term_shape`, `skew_label`, `next_earnings`. One sentence placing the screens: "30-day
IV {cm_iv_30d} versus 20-day realized {hv20}; {percentile basis}; term structure {term_shape}; skew
{skew_label}; next earnings {next_earnings}." No verdict.

**Filters** (`strategies.filters`): open interest ≥ {min_oi}, spread ≤ {max_spread} of the mid; each
screen's delta band and DTE window (`strategies.<screen>.delta_band`, `.dte_window`, `.target_delta`);
one contract per expiry, the one closest to the target delta. Give the filters once, in one line.

**One table per screen** in this order: `short_put` (sell a cash-secured put; for an index "sell a put
(cash-settled)"), `covered_call`, `long_call` (buy a call), `long_put` (buy a put). Row fields
(`strategies.<screen>.rows[*]`): `expiration`, `dte`, `strike`, `delta`, `mid`, `bid`, `ask`, `p_itm`,
`p_profit`, `p_touch`, `breakeven`, `breakeven_ask`, `breakeven_move_pct`, `yield`, `yield_bid`,
`annualized_yield`, `annualized_yield_bid`, `upside_cap_pct`, `if_called_return`, `yield_with_div`,
`capital`, `max_loss`, `pct_of_example_account`, `liquidity`, `earnings_before_expiry`, `ex_div_date`,
`dividend`, `otm_pct`, `em_units`, `spread_pct`, `settlement`, `exercise_style`.

- `short_put`: Expiry (DTE) | Strike | Δ | Mid (bid / ask) | P(ITM) | P(profit) | Breakeven | Annualized
  (mid / bid) | Capital (% of $100k) | Liquidity | Earnings | Ex-div.
- `covered_call`: the same columns, with "Capital" the 100 shares at spot; add one line with the upside
  cap and the if-called return of the first row.
- `long_call`, `long_put`: Expiry (DTE) | Strike | Δ | Mid (bid / ask) | P(ITM) | P(profit) | Breakeven
  (mid / ask) | Cost = max loss (% of $100k) | Liquidity | Earnings | Ex-div.
- Cells: probabilities as "29.2%"; capital as "$9,400 (9.4%)"; Earnings: `earnings_before_expiry` true
  → "before expiry" ("today, timing unknown" when `earnings.event_today`), false → "—", null →
  "unknown"; Ex-div: "{ex_div_date} ({dividend})" or "—".
- Under the tables, one line: "Each row shows what one contract means in a hypothetical $100,000 account;
  it is not a position size or a recommendation." Then the screen's `notes` (early assignment, the
  ex-dividend rule) and `strategies.notes`, translated.
- An empty table: "No contract passed the filters ({candidates} candidates)" or the screen's `reason`
  (for example "index: no shares to cover").
- Probabilities: write `p_itm` as "the risk-neutral probability of finishing in the money" and `p_profit`
  as "of finishing with a profit at expiry". `p_touch` (about twice `p_itm`, without drift) may be added
  as "the approximate risk-neutral probability of trading through the strike before expiry".
- American style (`exercise_style` "american"): say once that a short option may be assigned early.
  European (`exercise_style` "european"): cash-settled at expiry, no early assignment.

**Earnings straddle** (`strategies.earnings_straddle`: `expiration`, `strike`, `cost`,
`cost_per_contract`, `be_up`, `be_down`, `event_variance_share`, `p_beyond_be_rn`, `k_pre`, `k_post`,
`hold_days`, `n`, `hist_share_beyond_be`, `notes`):
"Buying the {expiration} {strike} straddle costs {cost} ({cost_per_contract} per contract). At expiry it
finishes with a profit only beyond {be_up} or {be_down} from spot (the break-evens are asymmetric:
strike ± cost). The risk-neutral probability implied by option prices of finishing beyond a break-even is
about {p_beyond_be_rn}. Over the same holding window ({k_pre} trading days before the report and {k_post}
from it, {hold_days} in all), {hist_share_beyond_be} of the last {n} reports moved beyond the break-evens.
Earnings account for {event_variance_share} of the variance in this expiry." Add the notes (for example
"most of the straddle cost comes from non-earnings days"). Unavailable: the `reason`.

### 10. Positioning (持仓结构)
**Fields:**
- `positioning.pcr.all.volume`, `positioning.pcr.all.oi`, `positioning.pcr.d30.volume`,
  `positioning.pcr.d30.oi`; `positioning.total_oi`, `positioning.total_volume`;
- `positioning.key_levels.calls` and `.puts` (`[strike, open interest]`, expiries up to
  `positioning.key_levels.window_dte` days, strikes within `positioning.key_levels.range_pct` of spot);
- `positioning.far_otm[*]` (`type`, `strike`, `oi`);
- `positioning.max_pain.next` and `positioning.max_pain.monthly` (`expiration`, `strike`),
  `positioning.max_pain.evidence`;
- `positioning.oi_change`: `base_date`, `net_change`, `top_increases[*]` and `top_decreases[*]` (`type`,
  `strike`, `oi_change`, `oi`, `base_oi`), `notes`, `reason`;
- `flow.rows[*]` (`option`, `type`, `strike`, `expiration`, `dte`, `volume`, `open_interest`, `vol_oi`,
  `premium`, `iv`, `delta`), `flow.rules`, `flow.direction_note`; `data_quality.oi_note`.

**Layout:**
1. Open with: "Open interest is as of the previous trading day's close."
2. PCR table: Window | Put/call volume | Put/call open interest (all expiries; DTE ≤ 30).
3. One line each: "The largest call open interest between spot and +10% is at {strike} ({oi}), ...; the
   largest put open interest between −10% and spot at ...". Strikes with large open interest are not
   support or resistance: do not call them that.
4. `far_otm` (if any): "Large positions outside ±10% (often hedges, not support or resistance): ...".
5. Max pain, one line: "Max pain (weak evidence): {strike} for {expiration} (next) and {strike} for
   {expiration} (monthly)." Max pain never goes into the key points or the chat reply.
6. OI change (if available): "Between the {base_date} snapshot and this one, net open interest changed by
   {net_change} contracts", then the 3-5 largest increases and decreases by type and strike. Because open
   interest lags one trading day, this is the change over the sessions from {base_date} to the trading day
   before `as_of`. Repeat a gap note ("open-interest base snapshot is from {date}, {n} calendar days before
   as_of"). Unavailable: the reason (no stored chain yet).
7. **Unusual activity**, a small table of the top 5 `flow.rows`: Option | Type | Strike | Expiry | Volume
   | OI | Vol/OI | Premium ($) | IV. Then the direction note: "volume does not show buyer or seller, or
   opening or closing: direction unknown." Rules: volume ≥ {min_volume} and volume / OI ≥ {min_vol_oi} or
   OI 0. No rows: "No contract met the unusual-activity rules."

### 11. Dealer gamma (做市商 Gamma)
**Fields:** `gamma.convention`, `gamma.applicability`, `gamma.regime`, `gamma.profile_at_spot`,
`gamma.net_gex_1pct`, `gamma.zero_gamma`, `gamma.zero_gamma_dist_pct`, `gamma.call_wall`,
`gamma.put_wall`, `gamma.contracts`, `gamma.notes`; `data_quality.gamma_check` (`corr`, `median_ratio`,
`ok`).

**Layout:** 3-4 sentences:
- The assumption first: "Assuming customers sell calls and buy puts (dealers long calls, short puts), ..."
- "... the dealer gamma profile at spot is {regime}: about {profile_at_spot} per 1% move (CBOE-gamma net
  {net_gex_1pct}). The zero-gamma level is {zero_gamma}, {zero_gamma_dist_pct} from spot." Under this
  assumption, positive dealer gamma means hedging tends to lean against price moves and negative gamma
  that it tends to add to them; say it in that conditional form, nothing stronger.
- `regime` "near_zero": the two gamma measures have opposite signs; gamma is close to zero.
- "Largest call gamma at or above spot: {call_wall}; largest put gamma at or below spot: {put_wall}."
- Magnitudes depend on conventions: stress the sign and the zero-gamma level, not the dollar size.
- `applicability` "single_stock_low_confidence": "For a single stock this assumption is weak: low
  confidence."
- `data_quality.gamma_check.ok` false: the warning (CBOE gamma and recomputed gamma disagree).

### 12. Methodology & caveats (方法与说明)
**Content:**
- **Data:** `data_source`; `data_quality.delay_note`; data time `quote_time`; open interest from the
  previous trading day; CBOE keeps no history, so IV Rank, percentiles and OI changes come from the local
  history store (`history.days` days).
- **How the numbers are made:** the forward from put-call parity at zero rates; ATM IV backed out of the
  ATM straddle (Black-76); 25-delta wings from CBOE's per-contract IVs; constant-maturity IVs from total
  variance interpolation; dealer gamma under a sticky-strike assumption; earnings reactions as two-day
  close-to-close moves; probabilities risk-neutral, `p_touch` without drift.
- **Exercise style:** stock and ETF options are American-style; index options European-style and
  cash-settled.
- **Self-checks:** `data_quality.iv_price_check` (`max_abs_dev`, `flagged`) and
  `data_quality.gamma_check`, one line each.
- **Notes and errors:** any `meta.notes`, `meta.restricted`, `meta.skipped`, and `meta.errors` if non-empty
  (say which module failed; its numbers are absent).
- **Pointers:** `references/methodology.md` and `references/strategies.md` for definitions; `chart.html`
  for the interactive charts.

**Last line:** the disclaimer from `meta.disclaimer`, alone and in italics. English: *This is
options-market analysis for information only, not investment advice.* Chinese: *仅供信息参考的期权市场分析，
不构成投资建议。*

---

## Worked example (English; synthetic test data, values illustrative)

The strategy tables are shortened to one or two rows here; a real report lists every row of
`strategies.<screen>.rows`.

```markdown
# ACME options analysis (as of 2026-11-05)

## 1. Key points
- Option prices imply a 1σ move of ±5.8% (94.17-105.84) by the 2026-11-13 expiry and ±9.9%
  (90.08-109.92) by the monthly 2026-11-20 expiry.
- Earnings on 2026-11-17: the implied event move is about ±5.9% (1σ); on the two-day basis of past
  reactions, implied / past = 1.01.
- 30-day IV 43.5%, 34.1 vol points above 20-day realized volatility (9.4%); IV history building, 1 of 60
  days, and ACME has no CBOE volatility index.
- Skew: RR25 4.6 vol points (put skew).
- Data: CBOE's own iv30 (38.0%) differs from the 30-day constant-maturity IV (43.5%) by more than 1 vol
  point; the 30-day window contains earnings.

## 2. Snapshot
| Item | Value |
|---|---|
| Name / kind | Acme Synthetic Inc. · stock (from FMP) |
| Spot | 100.00 (close; −1.5% from 101.50) |
| Data time | 2026-11-05 16:00 New York, session closed; feed 16:30 (CBOE delayed quotes, ~15 min) |
| Data source | CBOE delayed quotes (~15 min) + FMP stable API (official) |
| CBOE iv30 | 38.0% |
| Chain | 1,924 live contracts in 13 expiries; 99.6% with a two-sided quote, 100% with an IV |
| History store | 1 day (2026-11-05, saved this run) |
| API calls | 8 network, 0 from cache |

- CBOE iv30 38.0% differs from the 30-day constant-maturity IV 43.5% by more than 1 vol point.

## 3. Market context
| Benchmark | Reading | Rule or detail |
|---|---|---|
| VIX | 18.5 (normal) | 1-year percentile 19; normal = 15-20 |
| SPX term structure | IV30/IV90 1.134 (inverted) | inverted above 1.02 |
| SPX skew | RR25 5.0 vol points (put) | index: put ≥ 3.0, steep put ≥ 8.0 |
| SPX dealer gamma | near zero | zero-gamma level +0.12% from spot |

## 4. Expected move
| Expiry | Tags | DTE | 1σ range | ±1σ | ATM straddle | ATM IV |
|---|---|---|---|---|---|---|
| 2026-11-06 | next, this week | 1 | 97.91-102.09 | ±2.1% | 1.7% | 39.9% |
| 2026-11-13 | next week | 8 | 94.17-105.84 | ±5.8% | 4.7% | 39.4% |
| 2026-11-20 | monthly, event | 15 | 90.08-109.92 | ±9.9% | 7.9% | 48.9% |
| 2026-12-04 | 30d | 29 | 87.68-112.32 | ±12.3% | 9.8% | 43.7% |

Over the next 7 and 30 calendar days the constant-maturity 1σ move is ±5.5% and ±12.5%. The 1σ range
carries about a 68% risk-neutral probability implied by option prices; the ATM straddle costs about 0.8
of the 1σ move. The 2026-11-20 expiry is the first after earnings.

## 5. Earnings
The next earnings date from FMP is 2026-11-17, 8 trading days away; FMP does not say whether it is before
the open or after the close. Option prices imply an earnings-event move of about ±5.9% (1 standard
deviation), net of ordinary daily volatility (between the 2026-11-13 and 2026-11-20 expiries). On the
same two-day basis as past reactions that is about ±6.8% (expected absolute move 5.5%). Over the last 11
reports the mean absolute two-day move was 5.4% (median 5.5%, largest 9.7%): implied / past = 1.01.
18.2% of those 11 reports moved more than ±6.8%; under a normal distribution the share beyond 1 standard
deviation is 31.7%. Earnings account for 35.1% of the variance priced into the 2026-11-20 expiry, whose
ATM straddle costs 7.9% of spot. The forward-volatility kink between 2026-11-13 and 2026-11-20 contains
the FMP date.

## 6. Volatility
| Measure | Value | Note |
|---|---|---|
| 30-day IV (constant maturity) | 43.5% | includes the 2026-11-17 earnings |
| 30-day IV excluding earnings | 38.3% | |
| Realized HV10 / HV20 / HV60 | 3.7% / 9.4% / 15.9% | HV20 at the 56th percentile of its year |
| IV − HV20 | 34.1 vol points | IV / HV20 = 4.64 |
| IV percentile | not yet | IV history building: 1 of 60 days; no CBOE volatility index for ACME |

30-day IV is 43.5%, 34.1 vol points above 20-day realized volatility (9.4%). Excluding the earnings
event, 30-day IV is 38.3%. IV Rank needs 60 stored trading days; run `scripts/snapshot.py` daily to build
the history.

## 7. Term structure
| 7d | 30d | 60d | 90d | 180d |
|---|---|---|---|---|
| 39.4% | 43.5% | 39.7% | 38.0% | 36.3% |

IV30 / IV90 = 1.143 → inverted (inverted above 1.02, upward below 0.98); IV7 / IV30 = 0.907.
- Forward volatility between 2026-11-13 and 2026-11-20 is 57.9%, against a median of 35.6% across
  expiries; the window contains the earnings date.

## 8. Skew
At 30 days, 25-delta puts trade at an IV 4.6 vol points above 25-delta calls: RR25 4.6 → put skew
(stock thresholds: steep put ≥ 6.0, put ≥ 2.0, flat above −1.0, otherwise call). BF25 0.5 vol points.
This skill's RR25 is put IV minus call IV, the opposite sign of the usual call-minus-put risk reversal.

## 9. Strategy screens
This is arithmetic on the risk-neutral distribution implied by option prices, not advice. Prices are mid
prices of a delayed snapshot taken after the close, when spreads are usually wider; they are not
executable quotes. Context: 30-day IV 43.5% versus 20-day realized 9.4%; no IV percentile yet; term
structure inverted; put skew; earnings 2026-11-17. Filters: open interest ≥ 100, spread ≤ 25% of the mid,
one contract per expiry closest to the target delta.

**Sell a cash-secured put** (Δ −0.35 to −0.10, 14-60 days, target −0.25)
| Expiry (DTE) | Strike | Δ | Mid (bid / ask) | P(ITM) | P(profit) | Breakeven | Annualized (mid / bid) | Capital (% of $100k) | Liquidity | Earnings | Ex-div |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-20 (15) | 94.00 | −0.26 | 1.72 (1.71 / 1.74) | 29.2% | 76.5% | 92.28 | 45.5% / 45.0% | $9,400 (9.4%) | A | before expiry | 2026-11-12 (0.25) |
| 2026-12-04 (29) | 93.00 | −0.26 | 2.21 (2.19 / 2.23) | 30.5% | 75.6% | 90.79 | 30.6% / 30.3% | $9,300 (9.3%) | B | before expiry | 2026-11-12 (0.25) |

**Covered call** (Δ 0.10 to 0.35, 14-60 days, target 0.25)
| Expiry (DTE) | Strike | Δ | Mid (bid / ask) | P(ITM) | P(profit) | Breakeven | Annualized (mid / bid) | Capital (% of $100k) | Liquidity | Earnings | Ex-div |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-20 (15) | 107.00 | 0.26 | 1.41 (1.40 / 1.42) | 22.8% | 54.6% | 98.59 | 34.3% / 33.9% | $10,000 (10.0%) | B | before expiry | 2026-11-12 (0.25) |

First row: upside capped at +7.0%; if called away at expiry, the return is 8.4%.

**Buy a call** (Δ 0.30 to 0.70, 30-120 days, target 0.50)
| Expiry (DTE) | Strike | Δ | Mid (bid / ask) | P(ITM) | P(profit) | Breakeven (mid / ask) | Cost = max loss (% of $100k) | Liquidity | Earnings | Ex-div |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-12-11 (36) | 101.00 | 0.51 | 4.96 (4.91 / 5.01) | 45.3% | 31.5% | 105.96 / 106.01 | $496.43 (0.50%) | A | before expiry | 2026-11-12 (0.25) |

**Buy a put** (Δ −0.70 to −0.30, 30-120 days, target −0.50)
| Expiry (DTE) | Strike | Δ | Mid (bid / ask) | P(ITM) | P(profit) | Breakeven (mid / ask) | Cost = max loss (% of $100k) | Liquidity | Earnings | Ex-div |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-12-11 (36) | 101.00 | −0.49 | 5.67 (5.61 / 5.72) | 54.7% | 37.5% | 95.33 / 95.28 | $566.79 (0.57%) | A | before expiry | 2026-11-12 (0.25) |

Each row shows what one contract means in a hypothetical $100,000 account; it is not a position size or a
recommendation. ACME options are American-style: a short put may be assigned before expiry when deep in
the money, and before the 2026-11-12 ex-dividend date an in-the-money call whose remaining time value is
below the 0.25 dividend may be exercised early.

**Earnings straddle:** buying the 2026-11-20 100 straddle costs 7.92 ($791.56 per contract). At expiry
it finishes with a profit only beyond +7.9% or −7.9% from spot. The risk-neutral probability implied by
option prices of finishing beyond a break-even is about 42.4%. Over the same holding window (7 trading
days before the report and 4 from it, 11 in all), 36.4% of the last 11 reports moved beyond the
break-evens. Earnings account for 35.1% of this expiry's variance: most of the straddle cost comes from
non-earnings days.

## 10. Positioning
Open interest is as of the previous trading day's close.
| Window | Put/call volume | Put/call open interest |
|---|---|---|
| All expiries | 0.98 | 1.00 |
| DTE ≤ 30 | 0.96 | 1.01 |

The largest call open interest between spot and +10% (expiries up to 45 days) is at 100 (62,608), 101
and 102; the largest put open interest between −10% and spot at 100, 99 and 98. Max pain (weak evidence):
100 for 2026-11-06 and 100 for 2026-11-20. Open-interest change: no stored chain before 2026-11-05.

| Option | Type | Strike | Expiry | Volume | OI | Vol/OI | Premium | IV |
|---|---|---|---|---|---|---|---|---|
| ACME261106C00105000 | call | 105.00 | 2026-11-06 | 2,400 | 120 | 20.00 | $1,418 | 39.0% |

Volume does not show buyer or seller, or opening or closing: direction unknown.

## 11. Dealer gamma
Assuming customers sell calls and buy puts (dealers long calls, short puts), the dealer gamma profile at
spot is positive: about $6.7 million per 1% move. The zero-gamma level is 99.55, −0.45% from spot. Under
this assumption, positive dealer gamma means hedging tends to lean against price moves. The largest call
and put gamma sit at 100. For a single stock this assumption is weak: low confidence. Magnitudes depend
on conventions; the sign and the zero-gamma level matter more than the dollar size.

## 12. Methodology & caveats
- Data: CBOE delayed quotes (~15 min) + FMP stable API (official), data time 2026-11-05 16:30 New York;
  open interest from the previous trading day. CBOE keeps no history: the local store has 1 day.
- Forward from put-call parity (zero rates); ATM IV backed out of the ATM straddle; RR25 from CBOE's
  per-contract IVs; dealer gamma with a sticky-strike assumption; earnings reactions are two-day
  close-to-close moves; all probabilities are risk-neutral.
- Self-checks: the straddle-implied ATM IV differs from CBOE's by at most 0.15% on the expiries up to 30
  days; CBOE gamma and recomputed gamma correlate at 1.00.
- Definitions: references/methodology.md and references/strategies.md. Interactive charts: chart.html.

*This is options-market analysis for information only, not investment advice.*
```
