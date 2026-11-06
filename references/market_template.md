# report.md template (market thermometer)

Claude writes `report.md` next to `metrics.json` after running `scripts/analyze.py --market`. The
universe is SPX, SPY, QQQ and IWM, plus VIX (level only) and CBOE's VIX, VIX3M and VIX9D histories. This
file defines the section order, the key paths, the tables and the wording. The rules of
`references/report_template.md` (numbers, wording, language) apply here unchanged; only the differences
are repeated.

## Rules (in addition to report_template.md)

- **The thermometer is a transparent sum of six rules, not a forecast.** Report each flag with its value,
  threshold and whether it fired, then the label. Never describe the label as a prediction or a signal to
  act.
- **Two different scales:** the VIX `band` (low, normal, elevated, very high) describes the VIX level
  only; the thermometer `label` (calm, cautious, stressed) describes the count of fired flags. Do not mix
  the words.
- **Units:** `vix.level`, `vix.prev_close` and the `vix_20_or_more` flag's `value` and `threshold` are
  index points (write 18.5, not 18.5%); `vix.change_pct` is a fraction; `vix_vix3m` and `vix9d_vix` are
  ratios (3 decimals); every `*_pctl*` is 0-100. Dollar GEX is per 1% move, in $ billions or millions with
  1 decimal.
- **Percentile windows:** the VIX percentiles come from CBOE's own histories (the last 252 trading days).
  The `ratio_30_90_pctl`, `rr25_pctl` and `net_gex_pctl` come from the local history store: write "the
  {n}-day percentile" with `history.days.<key>`, and "1-year percentile" only from 252 days. They are null
  until 60 days are stored.
- **Members can be missing.** A member with `available: false` keeps its row in every board with its
  `reason`; `meta.skipped` lists them. The thermometer only needs SPX and the VIX history.
- **Overnight quotes:** after 20:15 New York, SPX option quotes come from the overnight (GTH) session
  (`snapshot.universe[*].overnight_quotes`); say so in sections 1 and 2.

---

## Section order and contents

### 0. Title
`# US options market thermometer (as of {as_of})` (Chinese: `# 美股期权市场温度计（截至 {as_of}）`).

### 1. Key points (要点)
Three to five bullets, one sentence each, numbers from `metrics.json` only:
1. The thermometer label and the flags that fired: `thermometer.label`, `thermometer.n_flags` of
   `thermometer.n_available`, the fired flag names; "uncertain" when `thermometer.label_uncertain`.
2. VIX and its percentile, VIX/VIX3M and its percentile: `vix.level` (`vix.band`), `vix.pctl_1y`,
   `vix.vix_vix3m`, `vix.vix_vix3m_pctl_1y`.
3. The SPX 1σ expected move for the `next_week` and `monthly` expiries (`expected_moves.rows[*]` with
   `key` "SPX"), with dates and ranges.
4. Optionally the SPX term-structure shape or the SPX gamma regime, whichever flag fired.
5. The most important data warning, if any.

### 2. Snapshot (快照)
**Fields:** `as_of`, `quote_time` (SPX's feed time), `data_quality.delay_note`, `data_source`,
`meta.connection.fmp.tls_verify`; `snapshot.universe[*]` (`key`, `kind`, `available`, `reason`, `spot`,
`spot_basis`, `feed_time`, `overnight_quotes`); `history.days`, `history.saved_this_run`;
`meta.network_calls`, `meta.cache_hits`.

**Layout:** a table (Member | Kind | Spot (basis) | Feed time | Overnight quotes | Status). Then one line
with the data source and calls, and the warnings as bullets: every `data_quality.warnings` entry
(prefixed with the member, translated faithfully), a stale feed (`data_quality.stale.is_stale`), the
third-party proxy and TLS lines of report_template.md, and `meta.skipped`. Nothing to list: "No
data-quality warnings."

### 3. Thermometer (温度计)
**Fields:** for each `<flag>` in `thermometer.flags` (`vix_20_or_more`, `term_inverted`,
`front_stress`, `skew_steep`, `negative_gamma`, `realized_above_implied`): `value`, `threshold`,
`fired`, `reason`; `thermometer.n_flags`, `thermometer.n_available`, `thermometer.label`,
`thermometer.label_uncertain`, `thermometer.missing`, `thermometer.notes`.

**Layout:** a table, one row per flag in this order:

| Flag | Rule | Value | Threshold | Fired |
|---|---|---|---|---|
| VIX at 20 or more | VIX ≥ 20 (index points) | `vix_20_or_more.value` | 20 | yes / no / n/a |
| SPX term structure inverted | IV30 / IV90 > 1.02 | `term_inverted.value` | 1.02 | |
| Front-end stress | SPX IV7 / IV30 > 1.10 | `front_stress.value` | 1.10 | |
| Steep SPX put skew | RR25 ≥ 8.0 vol points | `skew_steep.value` | 0.08 | |
| Negative dealer gamma | SPX gamma profile at spot < 0 | `negative_gamma.value` | 0 | |
| Realized above implied | SPX HV20 > SPX IV30 | `realized_above_implied.value` | today's IV30 | |

Write `fired` null as "n/a" with its `reason`. Under the table:
- "{n_flags} of {n_available} flags fired → **{label}** (calm 0-1, cautious 2-3, stressed 4 or more; a
  label needs at least 4 available flags)."
- `label_uncertain` true: "The label is uncertain: {missing} could not be computed and could change it."
- `label` null: "Too few flags were available for a label" with `thermometer.notes`.
- Always: "This is a transparent sum of rules, not a forecast."
- `negative_gamma` does not fire when the SPX regime is near zero (the two gamma measures disagree in
  sign); say so if that is the case.

### 4. VIX and term structure (VIX 与期限结构)
**Fields:**
- `vix.level`, `vix.band`, `vix.prev_close`, `vix.change_pct`, `vix.pctl_1y`, `vix.vix_vix3m`,
  `vix.vix_vix3m_pctl_1y`, `vix.vix9d_vix`, `vix.vix9d_vix_pctl_1y`, `vix.notes`;
- SPX: `term_structure.cm_iv` (`7d` ... `180d`), `term_structure.cm_extrapolated`,
  `term_structure.ratio_30_90`, `term_structure.ratio_30_90_pctl`, `term_structure.ratio_7_30`,
  `term_structure.shape`, `term_structure.kinks[*]`, `term_structure.dropped_expiries`;
- realized: `realized.spx_hv20`, `realized.spx_cm_iv_30d`, `realized.vrp`, `realized.source`,
  `realized.reason`.

**Layout:**
1. "VIX {level} ({band}: < 15 low, 15-20 normal, 20-30 elevated, ≥ 30 very high), {change_pct} from
   {prev_close}; 1-year percentile {pctl_1y}."
2. "VIX/VIX3M {vix_vix3m} (1-year percentile {vix_vix3m_pctl_1y}) and VIX9D/VIX {vix9d_vix}
   ({vix9d_vix_pctl_1y}): a ratio above 1 means near-term implied volatility is above longer-term." If
   `vix.notes` says "ratios from {date} close", repeat it.
3. SPX constant-maturity IV, one row table (7d | 30d | 60d | 90d | 180d), extrapolated tenors marked;
   "IV30 / IV90 = {ratio_30_90} → {shape}; IV7 / IV30 = {ratio_7_30}", with the percentile when available.
   Kinks as in report_template.md section 7 (SPX has no earnings: a kink is possibly a macro event or data
   noise).
4. "SPX 30-day IV {spx_cm_iv_30d} versus 20-day realized volatility of the S&P 500 ({source}) {spx_hv20}:
   {vrp} vol points." Unavailable without FMP: the reason.

### 5. Skew board (偏斜看板)
**Fields:** `skew_board.rows[*]`: `key`, `available`, `reason`, `rr25_30d`, `bf25_30d`, `label`,
`rr25_pctl`.

**Layout:** a table (Member | RR25 (vol points) | BF25 (vol points) | Label | Percentile). One line with
the index and ETF thresholds: steep put ≥ 8.0, put ≥ 3.0, flat above −1.0, otherwise call. Repeat that
this skill's RR25 is put IV minus call IV.

### 6. Gamma board (Gamma 看板)
**Fields:** `gamma_board.rows[*]`: `key`, `available`, `reason`, `net_gex_1pct`, `regime`,
`zero_gamma`, `zero_gamma_dist_pct`, `net_gex_pctl`.

**Layout:** open with the assumption: "Assuming customers sell calls and buy puts (dealers long calls,
short puts)". Then a table (Member | Regime | Zero-gamma level | Distance from spot | Net GEX per 1% |
Percentile). Under this assumption, positive dealer gamma means hedging tends to lean against price
moves and negative gamma that it tends to add to them; say it only in that conditional form. "near zero"
means the two gamma measures disagree in sign. Stress the sign and the zero-gamma level; the dollar size
depends on conventions.

### 7. Expected moves (预期波动)
**Fields:** `expected_moves.rows[*]`: `key`, `expiration`, `tags`, `dte`, `straddle_pct`,
`move_1sd_pct`, `range_1sd`.

**Layout:** a table (Member | Expiry | Tags | DTE | ±1σ | 1σ range | ATM straddle). The 1σ range carries
about a 68% risk-neutral probability implied by option prices; the ATM straddle costs about 0.8 of the 1σ
move.

### 8. Positioning (持仓)
**Fields:** `positioning.rows[*]`: `key`, `available`, `pcr_all_volume`, `pcr_all_oi`;
`data_quality.delay_note`.

**Layout:** a table (Member | Put/call volume | Put/call open interest), all expiries. One line: open
interest is as of the previous trading day's close; volume does not show buyer or seller.

### 9. Methodology & caveats (方法与说明)
- Data: `data_source`, the delay and the data time; open interest from the previous trading day; VIX,
  VIX3M and VIX9D histories from CBOE.
- The thermometer rules and their thresholds live in `scripts/config.py`; each run records them in
  `meta.config_snapshot`.
- Approximations: the forward from put-call parity at zero rates, ATM IV backed out of the straddle,
  sticky-strike gamma, risk-neutral probabilities.
- Self-checks: `data_quality.members.<key>.iv_price_check` and `.gamma_check` when one failed.
- `meta.notes`, `meta.skipped`, and `meta.errors` if non-empty.
- Pointers: `references/methodology.md` (section 10, the thermometer) and `chart.html`.

**Last line:** `meta.disclaimer`, alone and in italics.

---

## Worked example (English; synthetic test data, values illustrative)

```markdown
# US options market thermometer (as of 2026-11-05)

## 1. Key points
- Thermometer: 2 of 6 flags fired (SPX term structure inverted, front-end stress) → cautious.
- VIX 18.5 (normal), at the 19th percentile of the past year; VIX/VIX3M 0.944 (19th percentile).
- Option prices imply a 1σ SPX move of ±3.7% (7,081.44-7,626.02) by 2026-11-13 and ±4.8%
  (6,997.17-7,710.29) by the monthly 2026-11-20 expiry.
- SPX dealer gamma is near zero: the two gamma measures disagree in sign.
- Data: SPY's straddle-implied ATM IV differs from CBOE's IV by more than 3% on 1 expiry.

## 2. Snapshot
| Member | Kind | Spot (basis) | Feed time | Overnight quotes | Status |
|---|---|---|---|---|---|
| SPX | index | 7,353.73 (close) | 2026-11-05 16:30 | no | ok |
| SPY | ETF | 733.00 (close) | 2026-11-05 16:30 | no | ok |
| QQQ | ETF | 610.00 (close) | 2026-11-05 16:30 | no | ok |
| IWM | ETF | 240.00 (close) | 2026-11-05 16:30 | no | ok |

CBOE delayed quotes (~15 min) + FMP stable API (official); 9 network calls, 0 from cache.
- SPY: ATM IV backed out of straddle prices differs from CBOE's IV by more than 3% on 1 expiry.

## 3. Thermometer
| Flag | Rule | Value | Threshold | Fired |
|---|---|---|---|---|
| VIX at 20 or more | VIX ≥ 20 | 18.5 | 20 | no |
| SPX term structure inverted | IV30 / IV90 > 1.02 | 1.134 | 1.02 | yes |
| Front-end stress | IV7 / IV30 > 1.10 | 1.112 | 1.10 | yes |
| Steep SPX put skew | RR25 ≥ 8.0 vol points | 5.0 | 8.0 | no |
| Negative dealer gamma | gamma profile at spot < 0 | −$5.3 billion | 0 | no (regime near zero) |
| Realized above implied | HV20 > IV30 | 5.6% | 22.6% | no |

2 of 6 flags fired → **cautious** (calm 0-1, cautious 2-3, stressed 4 or more). This is a transparent sum
of rules, not a forecast. The gamma profile at spot is negative but CBOE's net gamma is positive, so the
regime is near zero and the flag does not fire.

## 4. VIX and term structure
VIX 18.5 (normal: 15-20), −1.0% from 18.69; 1-year percentile 19. VIX/VIX3M 0.944 (19th percentile) and
VIX9D/VIX 0.919 (19th percentile): a ratio above 1 would mean near-term implied volatility above
longer-term.

| 7d | 30d | 60d | 90d | 180d |
|---|---|---|---|---|
| 25.1% | 22.6% | 20.8% | 19.9% | 19.2% |

IV30 / IV90 = 1.134 → inverted; IV7 / IV30 = 1.112. No forward-volatility kink. SPX 30-day IV 22.6%
versus 20-day realized volatility of the S&P 500 (^GSPC) 5.6%: 17.0 vol points.

## 5. Skew board
| Member | RR25 | BF25 | Label | Percentile |
|---|---|---|---|---|
| SPX | 5.0 | 0.5 | put | n/a (1 day stored) |
| SPY | 5.0 | 0.5 | put | n/a |
| QQQ | 3.7 | 0.4 | put | n/a |
| IWM | 2.8 | 0.4 | flat | n/a |

Thresholds (indices and ETFs): steep put ≥ 8.0, put ≥ 3.0, flat above −1.0. RR25 here is put IV minus
call IV.

## 6. Gamma board
Assuming customers sell calls and buy puts (dealers long calls, short puts):
| Member | Regime | Zero-gamma level | Distance | Net GEX per 1% |
|---|---|---|---|---|
| SPX | near zero | 7,362.22 | +0.12% | $3.4 billion |
| SPY | positive | 730.58 | −0.33% | $208.9 million |
| QQQ | near zero | 610.13 | +0.02% | $35.7 million |
| IWM | positive | 239.21 | −0.33% | $35.3 million |

Under this assumption, positive dealer gamma means hedging tends to lean against price moves. Read the
sign and the zero-gamma level; the dollar size depends on conventions.

## 7. Expected moves
| Member | Expiry | Tags | DTE | ±1σ | 1σ range | ATM straddle |
|---|---|---|---|---|---|---|
| SPX | 2026-11-13 | next week | 8 | ±3.7% | 7,081.44-7,626.02 | 3.0% |
| SPX | 2026-11-20 | monthly | 15 | ±4.8% | 6,997.17-7,710.29 | 3.9% |
| SPY | 2026-11-13 | next week | 8 | ±3.7% | 706.24-759.76 | 2.9% |
| SPY | 2026-11-20 | monthly | 15 | ±4.9% | 697.44-768.56 | 3.9% |

## 8. Positioning
| Member | Put/call volume | Put/call open interest |
|---|---|---|
| SPX | 1.03 | 1.01 |
| SPY | 0.97 | 0.99 |
| QQQ | 1.03 | 1.01 |
| IWM | 0.99 | 0.99 |

Open interest is as of the previous trading day's close; volume does not show buyer or seller.

## 9. Methodology & caveats
- CBOE delayed quotes (~15 min), data time 2026-11-05 16:30 New York; VIX, VIX3M and VIX9D histories from
  CBOE; S&P 500 daily closes from FMP.
- Forward from put-call parity at zero rates; ATM IV backed out of the ATM straddle; gamma with a
  sticky-strike assumption; probabilities are risk-neutral.
- The history store has 1 day per member: the percentiles of the term ratio, RR25 and GEX need 60.
- Definitions: references/methodology.md. Interactive charts: chart.html.

*This is options-market analysis for information only, not investment advice.*
```
