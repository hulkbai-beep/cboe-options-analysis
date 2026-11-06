# Strategy screens

`scripts/strategies.py` turns the delayed chain into five small tables: sell a cash-secured put, covered
call, buy a call, buy a put, and buy an earnings straddle. Each row is arithmetic on one contract: what it
costs or pays, where it breaks even, how much capital it ties up in a hypothetical $100,000 account, and
the risk-neutral probabilities implied by option prices. **The screens rank nothing and recommend
nothing.** Every report section that uses them opens with: "This is arithmetic on the risk-neutral
distribution implied by option prices, not advice."

Parameters are in `scripts/config.py` (`STRATEGIES`, `STRAT_*`, `LIQ_*`, `EXAMPLE_ACCOUNT`); the output is
`strategies` in `metrics.json` and the strategy tab of `chart.html`.

---

## 1. Common filters

A contract enters a screen only if all of these hold:
- it is a surface contract (one root per expiry, section 1.2 of `methodology.md`) with a valid two-sided
  quote and a valid IV;
- open interest ≥ `STRAT_MIN_OI` (100);
- `spread_pct = (ask − bid) / mid` ≤ `STRAT_MAX_SPREAD` (0.25). This only removes clearly broken quotes:
  spreads after the close are much wider than intraday, and a 10% limit would drop most of a large stock's
  chain;
- its expiry has a forward (section 2 of `methodology.md`);
- its delta and DTE are inside the screen's band and window (both closed intervals).

`candidates` counts the contracts that passed. The chart lists up to `STRAT_CHART_ROWS` (60) of them per
screen (by expiry, then by distance to the target delta).

## 2. The screens

| Screen | Name in the report | Type | Delta band | DTE | Target delta |
|---|---|---|---|---|---|
| `short_put` | sell a cash-secured put (index: sell a put, cash-settled) | put | −0.35 to −0.10 | 14-60 | −0.25 |
| `covered_call` | covered call (own 100 shares, sell one call) | call | 0.10 to 0.35 | 14-60 | 0.25 |
| `long_call` | buy a call | call | 0.30 to 0.70 | 30-120 | 0.50 |
| `long_put` | buy a put | put | −0.70 to −0.30 | 30-120 | −0.50 |
| `earnings_straddle` | buy an earnings straddle | call + put at the ATM strike | E2, the first expiry after earnings | E2 ≤ 60 | ATM |

- An index has no covered call (`available: false`, reason "index: no shares to cover").
- ETFs and indices have no earnings straddle ("ETF/index: no earnings").

## 3. Which rows are shown

- Per expiry, the one contract whose delta is closest to the target (ties: the lower strike), sorted by
  expiry.
- With more than `STRAT_MAX_ROWS` (8) expiries, only Fridays are kept, plus a Thursday whose Friday is not
  an expiry (a holiday Friday, for example 2026-12-31); then the first 8.

## 4. Fields of every row

| Field | Definition | Wording |
|---|---|---|
| `expiration`, `dte`, `strike`, `option` | the contract | |
| `bid`, `ask`, `mid` | the delayed quote; `mid` = (bid + ask) / 2 | "mid price of a delayed snapshot" |
| `spread_pct` | (ask − bid) / mid | |
| `iv`, `delta` | CBOE's per-contract IV and delta | |
| `p_itm` | Black-76 on the expiry's forward F: d₂ = (ln(F/K) − σ²T/2) / (σ√T), calls N(d₂), puts N(−d₂), with the contract's own IV | "the risk-neutral probability of finishing in the money at expiry" |
| `p_touch` | 2 × `p_itm` when `p_itm` ≤ 0.5, else null (reflection principle, no drift) | "the approximate risk-neutral probability of trading through the strike before expiry" |
| `p_profit` | the same formula with K replaced by the break-even | "the risk-neutral probability of finishing with a profit at expiry" |
| `otm_pct` | K / spot − 1 (negative for strikes below spot) | "the strike is x% below/above spot" |
| `em_units` | `otm_pct` divided by the expiry's 1σ move (`move_1sd_pct`) | "about n expected moves (1σ) away" |
| `breakeven`, `breakeven_move_pct` | see section 5; `breakeven_move_pct` = break-even / spot − 1 | |
| `earnings_before_expiry` | stocks: next earnings date ≤ expiry (true also when the date is today); null when the date is unknown; false for ETFs and indices | "earnings before expiry" / "unknown" / "—" |
| `ex_div_date`, `dividend` | the first announced ex-dividend date with as_of < date ≤ expiry, and its amount per share (FMP) | |
| `theta_pct_per_day` | CBOE theta (per calendar day) / mid | "the option loses about x% of its price per day if nothing else changes" |
| `premium_per_contract` | mid × 100 | |
| `capital` | see section 5 | "capital tied up" |
| `max_loss` | see section 5 | "maximum loss at expiry" |
| `pct_of_example_account` | capital / `EXAMPLE_ACCOUNT` (100,000) | "x% of a hypothetical $100,000 account" |
| `liquidity` | A: OI ≥ 1,000 and volume ≥ 100; B: OI ≥ 300; else C (the spread is a separate column) | "liquidity grade A" |
| `exercise_style` | `european` for European roots (SPX, SPXW, NDX, NDXP, RUT, RUTW, XSP), else `american` | |
| `settlement` | AM or PM (09:30 open or the close of the expiry day) | |

## 5. Screen-specific fields

**Sell a cash-secured put** (`short_put`)
- `breakeven = K − mid`; `yield = mid / (K − mid)`; `yield_bid = bid / (K − bid)`;
  `annualized_yield = yield × 365 / dte` (and `annualized_yield_bid`).
- `capital = K × 100` (the cash that secures the put); `max_loss = (K − mid) × 100` (the stock going to
  zero); `p_profit = P(S_T > breakeven)`.
- American-style puts add the note "American-style: may be assigned before expiry when deep in the money".
- Index puts are cash-settled: no shares change hands; the payoff is the strike minus the settlement value.

**Covered call** (`covered_call`)
- `breakeven = spot − mid`; `yield = mid / spot`; `yield_bid = bid / spot`; `annualized_yield` (and
  `_bid`).
- `upside_cap_pct = K / spot − 1`: the gain on the shares stops at the strike.
- `if_called_return = (K − spot + mid) / spot`: the return if the shares are called away at expiry.
- `yield_with_div = (mid + dividend) / spot`, only when an ex-dividend date falls before expiry.
- `capital = spot × 100` (the shares); `max_loss = (spot − mid) × 100`; `p_profit = P(S_T > spot − mid)`.
- With an ex-dividend date before expiry the screen adds the note "before the ex-dividend date {date}, an
  in-the-money call whose remaining time value is below the {dividend} dividend may be exercised early: the
  shares are called away and the dividend is lost".

**Buy a call** (`long_call`)
- `breakeven = K + mid`; `breakeven_ask = K + ask` (paying the ask);
- `capital = max_loss = mid × 100`; `p_profit = P(S_T > breakeven)`.

**Buy a put** (`long_put`)
- `breakeven = K − mid`; `breakeven_ask = K − ask`;
- `capital = max_loss = mid × 100`; `p_profit = P(S_T < breakeven)`.

`p_profit` uses the contract's own IV at a different strike, so it is an approximation; for a long option
it sits below `p_itm` (the price has to be recovered first), for a short option above it.

## 6. Earnings straddle (`earnings_straddle`)

Computed for a stock with a known next earnings date when E2 (the first expiry after it, `methodology.md`
section 7.2) is at most `STRAT_EVENT_MAX_DTE` (60) days away and has an ATM straddle.
- `strike` = E2's ATM strike, `cost` = its straddle (call mid + put mid), `cost_per_contract` = cost × 100.
- **Break-evens are asymmetric:** `be_up = (K + cost) / spot − 1`, `be_down = (K − cost) / spot − 1`.
- `p_beyond_be_rn = N(d₂(K + cost)) + N(−d₂(K − cost))` with E2's forward and ATM IV: the risk-neutral
  probability of finishing beyond either break-even.
- `event_variance_share`: the share of E2's variance that comes from the earnings event. Below 0.5 the note
  "most of the straddle cost comes from non-earnings days" is added.
- **History on the same holding window:** `k_pre = busdays(as_of + 1, earnings date)` trading days before
  the report and `k_post = busdays(earnings date, E2 + 1)` from it, `hold_days = k_pre + k_post`. For each
  of the same past reports as the earnings module (up to 12), the move from the close k_pre + 1 bars before
  the report bar to the close k_post − 1 bars after it; `hist_share_beyond_be` = the share at or beyond a
  break-even, over `n` reports. Without daily bars it is null (note).

## 7. Context (`strategies.context`)

`cm_iv_30d`, `hv20`, `vrp`, `iv_index_pctl`, `iv_rank`, `iv_rank_ex_event`, `term_shape`, `skew_label`,
`next_earnings`: the readings that place the screens (IV versus realized volatility and versus its own
history, the term structure, the skew, the next report). They describe where IV stands; they do not
judge whether selling or buying options is a good idea.

## 8. Notes and fixed sentences

| Note | When |
|---|---|
| "after-close spreads are usually wider than intraday: table prices are not executable; check live broker quotes before trading" | the session has closed |
| "earnings date unknown: crossing not checked" | a stock without a known next earnings date |
| "risk-neutral arithmetic from option prices, not advice" | always |
| "American-style: may be assigned before expiry when deep in the money" | `short_put` with American rows |
| "before the ex-dividend date ..." | `covered_call` with an ex-dividend date before expiry |
| "most of the straddle cost comes from non-earnings days" | `earnings_straddle` with `event_variance_share` < 0.5 |
| "break-evens are asymmetric: strike +- straddle cost, relative to spot" | `earnings_straddle` |

## 9. Wording

- **Allowed:** the standard names (sell a cash-secured put, covered call, buy a call, buy a put, buy a
  straddle), the numbers, the definitions above, and conditional sentences: "If ACME closes above 92.28
  on 2026-11-20, the put finishes with a profit at expiry."
- **Not allowed:** a preferred row, a "best" strike or expiry, "income", "yield you can collect", "safe",
  "high probability trade", "worth it", "cheap" or "expensive" options, a win rate, any instruction to
  trade. Annualized yields are arithmetic on today's mid, not returns anyone earns: say "annualized at the
  mid price" and never compare them with interest rates or other investments as if they were alternatives.
- **Probabilities** are risk-neutral (they include a risk premium) and are not forecasts.
- **Prices** are mid prices of a delayed snapshot, wider after the close, not executable quotes. The bid
  columns show the same arithmetic at the bid.
- **Size:** one contract is 100 shares. `pct_of_example_account` shows what one contract would tie up in a
  hypothetical $100,000 account; it is not a position size.
- **Exercise:** stock and ETF options are American-style (a short put can be assigned early when deep in
  the money; a short call can be exercised early before an ex-dividend date when its time value is below
  the dividend). Index options are European-style and cash-settled.
- **Earnings:** a row whose `earnings_before_expiry` is true spans a report; say so next to the row. When
  it is null, say the earnings date is unknown.

## 10. Questions about selling puts or covered calls

When the user asks "should I sell a put on X" or "which covered call is best":
1. Run the analysis and show the screen's table with its filters and the context line.
2. Explain what each column means for one contract, using the first row as the worked example: the cash
   tied up, the premium, the break-even, the risk-neutral probabilities of finishing in the money and with
   a profit, the earnings and ex-dividend dates before expiry, and the assignment rule.
3. State the risks in plain arithmetic: the maximum loss (`max_loss`), the capped upside of a covered call
   (`upside_cap_pct`), the move to the break-even in 1σ units (`em_units`).
4. Do not choose a row, do not say whether to trade, and end with the disclaimer.
