// Text for the cboe-options-analysis pages: ui/chart.js, chart.html and the local app (ui/index.html).
// Version 1 ships English only. A language is one more block in STR with the same keys, plus texts for the
// fixed sentences that Python marks with ids (note_*, warn_*, reason_*, err_*; plan 9.5): English has none of
// those, because Python already writes them in English and the page shows them as written. The language button
// stays hidden until STR has two languages. Lookup order: current language, then English, then the key itself.
// Every value is plain text (chart.js escapes it); a function value takes the arguments of t(key, ...args).
(function (global) {
  "use strict";

  const KEY = "oa-lang";

  const STR = {
    en: {
      // language button (the label names the language it switches to) and the local app
      lang_button: "English", lang_hint: name => `Switch to ${name}`,
      app_title: "Options analysis", app_title_market: "US options market thermometer",
      f_symbol: "Symbol", f_mode: "View", mode_ticker: "Ticker", mode_market: "Market",
      c_refresh: "Ignore cache", go: "Analyze",
      loading: sym => `Analyzing ${sym}… (about 10-30 s on the first run, up to a minute more when Massive's rate limit applies; faster once cached)`,
      loading_market: "Reading SPX, SPY, QQQ, IWM and VIX… (about 30-60 s on the first run)",
      err_symbol: "Enter a valid symbol, e.g. NVDA, BRK.B, SPX.",
      // the page's wording of a refused symbol (error_id); the command-line text points at --market instead
      err_vix_futures: "VIX options settle to VIX futures, not spot VIX, so expected moves, probabilities and strategy screens do not apply. Switch to Market for the VIX level and its percentile.",
      etf: "ETF", none: "none", unavailable: "unavailable", unknown: "unknown", yes: "yes", no: "no",
      no_data: "No data to show.",

      // header and notices
      as_of: "as of", data_time: hm => `quotes at ${hm} ET`, source: "Data",
      badge_delayed: "Delayed ~15 min", badge_overnight: "Overnight (GTH) quotes", badge_intraday: "Intraday",
      badge_stale: "Stale quotes",
      notice_fmp_missing: "FMP_API_KEY is not set: earnings, realized volatility, dividends and the price cone are unavailable. Everything from the option chain is shown.",
      notice_fmp_missing_market: "FMP_API_KEY is not set: S&P 500 realized volatility (the realized-above-implied flag) is unavailable.",
      notice_proxy: base => `Data comes from a third-party FMP proxy (${base}), not FMP's official server. Verify key figures yourself.`,
      notice_tls: "HTTPS certificate verification was off for this run.",
      notice_error: e => `Part of the analysis failed and is left out: ${e}`,
      // Massive is optional: what a run without MASSIVE_API_KEY leaves out (an information line, not a warning)
      notice_massive_missing: "Optional: MASSIVE_API_KEY is not set, so short interest, short sale volume, news and the holiday-calendar check are left out.",
      notice_massive_missing_index: rate => `Optional: MASSIVE_API_KEY is not set, so European index options are discounted at a fixed ${rate} instead of the Treasury curve, and the holiday-calendar check is left out.`,
      notice_massive_missing_calendar: "Optional: MASSIVE_API_KEY is not set, so the holiday-calendar check is left out.",
      disclaimer: "This is options-market analysis for information only, not investment advice.",

      // units and short formats
      u_pts: "pts", dte_n: n => `${n} DTE`, days_n: n => `${n}d`, years_n: n => `${n}y`, cm_n: d => `${d}d`,
      ny_time: s => `${s} ET`, utc_time: s => `${s} UTC`, tenor_m: n => `${n}M`, tenor_y: n => `${n}Y`,
      shares_n: v => `${v} shares`,

      // KPI tiles, ticker page
      kpi_price: "Price", kpi_em: "Expected move (±1σ)", kpi_em_next_week: "Next week", kpi_em_monthly: "Monthly",
      em_tip: (straddle, lo, hi) => `ATM straddle ${straddle} of spot · 1σ range ${lo}–${hi}`,
      kpi_earnings: "Next earnings", no_earnings_kind: kind => `does not apply (${kind})`,
      earnings_today_short: "today",
      in_tdays: n => n == null ? "" : n === 1 ? "in 1 trading day" : `in ${n} trading days`,
      implied_vs_past: (a, b) => `implied ±${a} vs past ${b}`,
      kpi_iv30: "IV30", ivr_sub: (v, n) => `IV Rank ${v} (${n} days)`, ivx_sub: (sym, p) => `${sym} 1y pctl ${p}`,
      ivr_building_short: (n, need) => `IV history ${n}/${need} days`,
      kpi_iv_hv: "IV30 − HV20", hv20_sub: (hv, ratio) => `HV20 ${hv} · IV/HV ${ratio}`,
      kpi_rr25: "25Δ skew (RR)", pctl_sub: p => `pctl ${p}`,
      kpi_pcr: "Put/Call (OI)", pcr_vol_sub: v => `volume ${v}`,
      kpi_gex: "Net GEX per 1%", zero_gamma_sub: (k, dist) => `zero γ ${k} (${dist})`, no_zero_gamma: "no zero γ in range",

      // KPI tiles, market page
      kpi_vix: "VIX", pctl1y_sub: p => `1y pctl ${p}`, kpi_vix_vix3m: "VIX / VIX3M", kpi_spx_iv30: "SPX IV30",
      hv20_only: hv => `HV20 ${hv}`, kpi_ratio_30_90: "SPX IV30 / IV90", kpi_rr25_spx: "SPX 25Δ skew (RR)",
      kpi_spx_em: "SPX move next week", kpi_pcr_spx: "SPX Put/Call (OI)",

      // card titles and the notes under charts
      t_cone: "Expected-move cone", t_term: "Term structure", t_strategies: "Strategy screens", not_advice: "not advice",
      t_smile: "Volatility smile", t_oi: "Open interest and volume by strike", t_gex: "Dealer gamma (GEX)",
      low_confidence: "low confidence for a single stock", t_earnings: "Earnings: implied vs past moves",
      t_skew_term: "Skew and volatility", t_positioning: "Positioning", t_chain: "Option chain",
      t_flow: "Unusual activity", t_market_context: "Market context", t_notes: "Data notes",
      t_thermo: "Market thermometer", t_vix: "VIX and VIX3M, past year", t_term_spx: "SPX term structure",
      t_gex_spx: "SPX dealer gamma (GEX)", t_skew_board: "Skew board: 30-day RR25", t_em_board: "Expected moves",
      t_gamma_board: "Gamma board", t_pcr_board: "Put/Call board", t_spark: "History (stored daily summaries)",
      cone_note: "The cone joins the ±1σ range implied by each expiry's ATM straddle (risk-neutral, about 68%). It is the market's price of movement, not a forecast.",
      term_note: "ATM IV from each expiry's straddle. Diamonds are constant-maturity points (hollow = extrapolated); shaded segments are kinks, where forward volatility jumps (usually an event); triangles mark expiries left out.",
      smile_note: "Implied volatility against strike / forward: out-of-the-money puts on the left, calls on the right. A steeper left side means downside protection is dearer.",
      oi_note_chart: "Calls above the axis, puts below. Open interest is from the previous close; big strikes are where positions sit, not where price must go. Max pain is weak evidence.",
      gex_note: "Assumes dealers are long the calls and short the puts that customers trade, which is a convention, not a fact. Read the sign and the zero-gamma level, not the dollar size.",

      // legends and toggles
      lg_closes: "Closes", lg_band: "±1σ cone", lg_cm: "Constant maturity", lg_kinks: "Kinks", lg_cboe_iv: "CBOE IV",
      lg_gex_bars: "GEX by strike", lg_gex_profile: "Gamma profile",
      win_next: "Next expiry", win_d30: dte => `≤ ${dte} DTE`, win_d45: dte => `≤ ${dte} DTE`, win_all: "All",

      // chart 1: cone and expected-move table
      aria_cone: "Expected-move cone: recent closes and the ±1σ range to each key expiry",
      close: "Close", move_1sd: "±1σ", range_1sd: "1σ range", spot: "Spot", cone_proxy: sym => `closes from ${sym} (proxy)`,
      th_key: "Symbol", th_expiration: "Expiry", th_tags: "Tags", th_dte: "DTE", th_straddle_pct: "Straddle",
      th_move_1sd: "±1σ move", th_range_1sd: "1σ range", th_atm_iv: "ATM IV",

      // chart 2: term structure
      aria_term: "Term structure: ATM implied volatility by days to expiry",
      kink_title: (from, to, fv) => `Kink from ${from} to ${to}: forward volatility ${fv}`,
      earnings_marker: date => `earnings ${date}`, cm_title: (d, v) => `${d}-day constant-maturity IV ${v}`,
      extrapolated: "extrapolated", dropped_title: (exp, reason) => `${exp} left out: ${reason}`,
      atm_iv: "ATM IV", cboe_iv: "CBOE IV", fwd_vol_to: to => `forward vol to ${to}`,
      negative_variance: "negative variance", shape: "Shape",

      // chart 4: smile
      aria_smile: "Volatility smile: implied volatility by moneyness for the selected expiries",
      no_smile: "no expiry has enough valid quotes", moneyness: "Strike / forward",
      smile_hint: "Hover or use ← → to compare expiries at one moneyness (F = forward).",

      // chart 5: open interest
      aria_oi: "Open interest or volume by strike: calls above the axis, puts below",
      no_rows_window: "no strikes with open interest in this window",
      calls_oi: "Calls · OI", puts_oi: "Puts · OI", calls_vol: "Calls · volume", puts_vol: "Puts · volume",
      max_pain_title: (exp, k) => `Max pain for ${exp}: ${k} (weak evidence)`, max_pain_short: k => `max pain ${k}`,
      key_level_title: (side, k, oi, dte) => `${side} level ${k}: open interest ${oi} (expiries within ${dte} days)`,
      call: "Call", put: "Put", strike: "Strike", vol_short: "Vol", oi_short: "OI",
      oi_hint: "Hover or use ← → for each strike. C1-C3 and P1-P3 mark the largest call and put open interest.",

      // chart 6: dealer gamma
      aria_gex: "Dealer gamma exposure by strike and the gamma profile across prices",
      zero_gamma_short: k => `zero γ ${k}`, call_wall_short: k => `call wall ${k}`, put_wall_short: k => `put wall ${k}`,
      price: "Price", gex_profile: "Gamma profile", gex_strike: k => `GEX at ${k}`, net_gex: "Net GEX per 1%",
      regime: "Regime", zero_gamma: "Zero gamma",

      // earnings
      aria_earnings: "Past earnings-day moves against the implied event move", no_reactions: "no past earnings reactions",
      implied_line: v => `implied ±${v}`, past_mean_line: v => `past mean ±${v}`, reaction: "Move",
      reactions_n: (n, method) => `${n} past reactions (${method})`,
      next_earnings: "Next earnings", timing: "Timing", date_check: "Date check", event_move: "Event move (1σ)",
      abs_move: v => `expected absolute move ${v}`, implied_window: "Implied move to the first expiry after",
      past_reactions: "Past reactions", past_v: (mean, median, max, n) => `mean ${mean} · median ${median} · max ${max} (n = ${n})`,
      small_sample: "small sample", implied_to_past: "Implied ÷ past mean", share_above: "Past moves beyond the implied move",
      share_above_v: (a, b) => `${a} (a normal distribution gives ${b})`, event_share: "Event share of the variance",

      // strategy screens
      strat_short_put_index: "Short put (cash-settled)",
      screen_rule: (d0, d1, dte0, dte1, target) => `Δ ${d0} to ${d1}, ${dte0}-${dte1} DTE; per expiry the strike closest to Δ ${target}`,
      candidates_n: n => `${n} candidates`, show_per_expiry: "show one per expiry",
      show_candidates: n => `show the first ${n} candidates`, show_all_candidates: n => `show all ${n} candidates`,
      no_rows: "no contract passes the screen",
      th_strike: "Strike", th_bid_ask: "Bid / ask", th_mid: "Mid", th_spread: "Spread", th_iv: "IV", th_delta: "Δ",
      th_otm: "OTM", th_p_itm: "P(ITM)", th_p_touch: "P(touch)", th_p_profit: "P(profit)", th_breakeven: "Breakeven",
      th_breakeven_ask: "Breakeven at ask", th_yield: "Yield", ann_short: v => `${v} a year`, th_yield_bid: "Yield at bid",
      th_upside_cap: "Upside cap", th_if_called: "If called", th_yield_div: "Yield + dividend", th_premium: "Premium",
      th_capital: "Capital", of_account: v => `${v} of $100k`, th_max_loss: "Max loss", th_theta: "Theta / day",
      th_liquidity: "Liquidity", liq_title: (oi, vol) => `open interest ${oi} · volume ${vol}`,
      th_earnings: "Spans earnings", th_ex_div: "Ex-dividend", th_style: "Style",
      exdiv_source: src => `Ex-dividend dates from ${src}.`, exdiv_unknown: "No dividend data: ex-dividend dates are unknown.",
      straddle_cost: "Straddle cost", per_contract: v => `${v} per contract`, breakevens: "Breakevens",
      p_beyond_rn: "P(beyond a breakeven), risk-neutral", hold_days: "Holding period",
      hold_days_v: (h, pre, post) => `${h} trading days (${pre} before the event, ${post} after)`,
      hist_beyond: "Past events beyond a breakeven", hist_beyond_v: (v, n) => `${v} of ${n} events`,

      // skew, volatility and positioning summaries
      cm_iv: "Constant-maturity IV", iv30_ex_event: "IV30 without the earnings event", iv30_cboe: "CBOE IV30",
      iv_rank: "IV Rank", iv_rank_ex: "IV Rank without the event",
      // a fixed sentence from Python (reason_id) whose page wording differs from the metrics.json text
      reason_ivr_building: (n, need) => `IV history building: ${n ?? 0} of ${need ?? 60} days`,
      ivr_v: (v, p, n, win) => `${v} (percentile ${p}, ${n} days, ${win})`,
      iv_index: "Volatility index", ivx_v: (sym, close, date, p) => `${sym} ${close} on ${date} · 1y pctl ${p}`,
      realized: "Realized volatility", hv_v: (h10, h20, h60, p) => `HV10 ${h10} · HV20 ${h20} · HV60 ${h60} · HV20 1y pctl ${p}`,
      vrp: "Volatility premium (IV30 − HV20)", rr25: "RR25 (30 days)", bf25: "BF25 (30 days)",
      skew_by_expiry: "Skew by expiry", th_put25: "25Δ put IV", th_call25: "25Δ call IV",
      pcr_all: "Put/Call, all expiries", pcr_30d: "Put/Call, ≤ 30 DTE",
      call_levels: dte => `Call OI levels (≤ ${dte} DTE)`, put_levels: dte => `Put OI levels (≤ ${dte} DTE)`,
      far_otm: "Far out-of-the-money OI", max_pain: "Max pain", weak_evidence: "weak evidence",
      oi_change: "OI change", since: d => `since ${d}`, up_short: "up", down_short: "down",

      // short interest and news (Massive, optional; stocks and ETFs)
      t_short: "Short interest", t_news: "News",
      short_interest: "Short interest", of_float: v => `${v} of the free float`,
      days_to_cover: "Days to cover", adv_v: v => `average daily volume ${v}`,
      si_settled: "Settlement date", age_days: n => n === 1 ? "1 day old" : `${n} days old`,
      si_change: "Change", si_change_v: (pct, prev, cur) => `${pct} from ${prev} to ${cur}`,
      short_volume: "Short sale volume", sv_v: (total, date) => `of ${total} shares reported to FINRA on ${date}`,
      short_volume_avg: n => `${n}-day average`,
      free_float: "Free float", float_pct: v => `(${v} of shares outstanding)`, effective: d => `as of ${d}`,
      spark_si: "Short interest by settlement", spark_sv: "Short sale volume ratio by day",
      n_settlements: n => `${n} settlements`, avg_line: v => `average ${v} (dashed)`, range_v: (a, b) => `${a} → ${b}`,
      si_bar: (date, shares, dtc) => `${date}: ${shares} shares short, ${dtc} days to cover`,
      sv_bar: (date, ratio, sv, total) => `${date}: ${ratio} (${sv} of ${total} shares reported to FINRA)`,
      short_history: "Settlements and daily short sale volume",
      th_settlement: "Settlement", th_short_interest: "Short interest", th_adv: "Avg daily volume", th_dtc: "Days to cover",
      th_date: "Date", th_short_volume: "Short volume", th_total_volume: "FINRA volume", th_short_ratio: "Short %",
      news_window: (n, days, from) => `${n} ${n === 1 ? "article" : "articles"} in ${days} days (since ${from})`,
      news_unlabeled: "unlabeled", sent_title: sym => `The news provider's sentiment toward ${sym}`,
      news_tickers: n => `${n} tickers`,
      news_shown: (k, n, newest) => `Showing ${k} of ${n}: articles about few tickers first, then roundups; the newest article is from ${newest}.`,
      news_truncated: "The provider returned as many articles as are read, so the window may hold more: the counts cover the newest ones only.",

      // option chain and unusual activity
      no_chain: "no option chain to show", calls: "Calls", puts: "Puts",
      chain_note: (fwd, atm, root, settle, range) => `forward ${fwd} · ATM ${atm} · root ${root}, ${settle} · strikes within ±${range}`,
      th_bid: "Bid", th_ask: "Ask", th_oi: "OI", th_vol: "Vol",
      no_flow: (minv, minr) => `No contract traded at least ${minv} with volume / OI of at least ${minr}.`,
      flow_rule: (minv, minr) => `volume ≥ ${minv} and volume / OI ≥ ${minr}`,
      th_contract: "Contract", th_vol_oi: "Vol / OI", th_premium_total: "Premium", th_price: "Price", th_last: "Last trade",
      show_less: "show fewer", show_all_n: n => `show all ${n}`,

      // market context (ticker page)
      spx_term: "SPX term structure", spx_skew: "SPX skew", spx_gamma: "SPX gamma",
      zero_gamma_dist: v => `zero gamma ${v} from spot`,

      // data notes
      dn_skipped: "Left out", dn_restricted: "Limited by missing data", dn_notes: "Notes", dn_history: "History store",
      dn_approx: "Approximations", dn_rates: "Discount rate", dn_calendar: "Holiday calendar", dn_connections: "Connections",
      rates_treasury: (date, r30, r1y) => `${date}: ${r30} at 30 days, ${r1y} at 1 year (continuously compounded); European-style options are discounted with it.`,
      rates_fixed_v: (rate, why) => `European-style options are discounted at a fixed ${rate}${why ? ` (${why})` : ""}.`,
      rates_yields: list => `Treasury yields (bond-equivalent): ${list}`,
      rates_american: "Not used: American-style options are not discounted.",
      rates_context: (date, r30) => `The SPX market context uses the Treasury curve of ${date} (${r30} at 30 days).`,
      cal_ok: (through, n) => `scripts/config.py matches the exchange calendar through ${through} (${n} holidays and early closes).`,
      cal_mismatch_n: (n, through) => `${n} ${n === 1 ? "date differs" : "dates differ"} from the exchange calendar (checked through ${through}):`,
      cal_row: (date, name, exch, conf) => `${date}${name ? ` (${name})` : ""}: exchange ${exch}, scripts/config.py ${conf}`,
      conn_v: (name, route, base, calls) => `${name}: ${route}, ${base} · ${calls} network ${calls === 1 ? "call" : "calls"} this run`,
      conn_tls_off: "certificate verification off", conn_none: name => `${name}: not configured`,
      conn_none_optional: name => `${name}: not configured (optional)`,
      history_v: (days, first, last, saved) => `${days} trading days stored (${first} to ${last}); today's chain ${saved}`,
      saved_yes: "was saved", saved_no: "was not saved",
      member_days: (key, n) => `${key}: ${n} ${n === 1 ? "day" : "days"}`,
      approx_surface: "Implied volatility is backed out of mid prices with Black-76 at each expiry's forward (from put-call parity).",
      approx_rn: "Probabilities and the cone are risk-neutral: they come from option prices, not from a forecast.",
      approx_gex: "Dealer gamma assumes dealers are long calls and short puts; real positioning is unknown.",
      approx_oi: "Open interest is as of the previous close; volume is today's.",
      approx_delay: "CBOE quotes are delayed about 15 minutes; after the close they are the closing quotes.",

      // market thermometer
      not_computed: "not computed", fired: "fired", not_fired: "no", label_uncertain: "(missing flags could change it)",
      thermo_count: (n, a) => `${n} of ${a} flags fired`, thermo_missing: list => `not computed: ${list}`,
      th_flag: "Flag", th_value_threshold: "Value (fires at)", th_fired: "Fired",
      op_vix_20_or_more: "fires at ≥", op_term_inverted: "fires at >", op_front_stress: "fires at >",
      op_skew_steep: "fires at ≥", op_negative_gamma: "fires at <", op_realized_above_implied: "fires above IV30",
      thermo_note: "Calm = 0-1 flags fired, cautious = 2-3, stressed = 4 or more; a label needs at least 4 of the 6 flags. It is a transparent sum of rules, not a forecast.",

      // market charts and tables
      aria_vix: "VIX and VIX3M closes over the past year", no_vix_history: "no VIX history",
      vix_hint: "Dotted lines at 15, 20 and 30. Hover or use ← → for each day.",
      vix_note: (ratio, pctl) => `VIX9D / VIX ${ratio} (1y pctl ${pctl}). Above 1 means the next days are priced higher than the next month.`,
      aria_skew_board: "30-day 25-delta risk reversal of each index and ETF",
      skew_board_hint: "RR25 = 25Δ put IV − 25Δ call IV at 30 days; dashed lines are the label thresholds.",
      th_pctl: "1y pctl", th_pcr_oi: "Put/Call (OI)", th_pcr_vol: "Put/Call (volume)",
      spark_spx_iv30: "SPX IV30", spark_ratio_30_90: "SPX IV30 / IV90", spark_rr25: "SPX RR25", spark_gex: "SPX net GEX",
      spark_days: n => `${n} days`,

      // labels of values from metrics.json: tv(prefix, value); config.ENUMS lists every value
      shape_inverted: "inverted", shape_upward: "upward", shape_flat: "flat",
      skew_steep_put: "steep put skew", skew_put: "put skew", skew_flat: "flat", skew_call: "call skew",
      band_low: "low", band_normal: "normal", band_elevated: "elevated", band_very_high: "very high",
      thermo_calm: "Calm", thermo_cautious: "Cautious", thermo_stressed: "Stressed",
      flag_vix_20_or_more: "VIX at 20 or more", flag_term_inverted: "SPX term structure inverted (IV30 / IV90)",
      flag_front_stress: "Front-end stress (SPX IV7 / IV30)", flag_skew_steep: "Steep SPX put skew (RR25)",
      flag_negative_gamma: "Negative dealer gamma at spot", flag_realized_above_implied: "Realized above implied (HV20 vs IV30)",
      strat_short_put: "Cash-secured put", strat_covered_call: "Covered call", strat_long_call: "Long call",
      strat_long_put: "Long put", strat_earnings_straddle: "Earnings straddle",
      applic_standard: "standard", applic_single_stock_low_confidence: "single stock: low confidence",
      liq_A: "A", liq_B: "B", liq_C: "C",
      regime_positive: "positive gamma", regime_negative: "negative gamma", regime_near_zero: "near zero",
      tag_next: "next", tag_this_week: "this week", tag_next_week: "next week", tag_monthly: "monthly", tag_30d: "30 days",
      tag_event: "earnings",
      kind_stock: "stock", kind_etf: "ETF", kind_index: "index",
      ksrc_index_table: "index table", ksrc_fmp: "FMP profile", ksrc_known_etfs: "known ETF list", ksrc_default: "default",
      ksrc_cboe_fallback: "index not in the table",
      timing_unknown: "unknown (FMP does not say before or after the session)",
      basis_close: "close", basis_current_price: "current price", basis_forward_first_expiry: "first expiry's forward",
      method_pre_post: "close before to close after", method_post_event_pair: "the two closes after the event",
      method_two_day_close_to_close: "two-day close to close",
      date_check_kink_contains_date: "a term-structure kink brackets the date", date_check_no_kink: "no term-structure kink",
      date_check_kink_elsewhere: "the kink is at other expiries",
      window_recent: "recent history", window_1y: "1 year",
      settle_AM: "AM-settled", settle_PM: "PM-settled", style_american: "American", style_european: "European",
      ivsrc_straddle: "straddle", ivsrc_cboe_fallback: "CBOE IV", iv30src_history: "history store", iv30src_cboe: "CBOE",
      evidence_weak: "weak evidence",
      sent_positive: "positive", sent_neutral: "neutral", sent_negative: "negative",
      ratesrc_treasury: "Treasury curve", ratesrc_fixed: "Fixed rate",
      divsrc_fmp: "FMP", divsrc_massive: "Massive",
      cal_closed: "closed", cal_early_close: "early close", cal_open: "open",
    },
  };

  const LANGS = Object.keys(STR);
  const LOCALES = { en: "en-US" };
  let lang = "en";
  try {
    const saved = global.localStorage.getItem(KEY);
    if (saved && STR[saved]) lang = saved;
  } catch (e) { /* storage blocked */ }
  const listeners = [];

  function t(key, ...args) {
    const v = STR[lang][key] ?? STR.en[key];
    return typeof v === "function" ? v(...args) : (v ?? key);
  }

  // Whether the current language (or English) has a text for this key: chart.js shows Python's English sentence
  // when a note_/warn_/reason_/err_ id has no text.
  const has = key => key in STR[lang] || key in STR.en;

  // Label of a value from metrics.json; values without a label are shown as they are.
  function tv(prefix, value) {
    if (value == null || value === "") return "–";
    return has(prefix + value) ? t(prefix + value) : String(value);
  }

  function setLang(next) {
    if (!STR[next] || next === lang) return;
    lang = next;
    try { global.localStorage.setItem(KEY, lang); } catch (e) { /* storage blocked */ }
    applyStatic(global.document);
    listeners.forEach(fn => fn(lang));
  }

  // Fill elements marked data-i18n / data-i18n-ph / data-i18n-title / data-i18n-aria, and the page language.
  function applyStatic(doc) {
    if (!doc) return;
    doc.documentElement.lang = lang;
    doc.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
    doc.querySelectorAll("[data-i18n-ph]").forEach(el => { el.placeholder = t(el.dataset.i18nPh); });
    doc.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });
    doc.querySelectorAll("[data-i18n-aria]").forEach(el => { el.setAttribute("aria-label", t(el.dataset.i18nAria)); });
  }

  // The language button: hidden with one language; otherwise it names the next language and switches to it.
  function bindToggle(button) {
    if (!button) return;
    if (LANGS.length < 2) { button.hidden = true; return; }
    const next = () => LANGS[(LANGS.indexOf(lang) + 1) % LANGS.length];
    const paint = () => {
      const name = STR[next()].lang_button;
      button.hidden = false;
      button.textContent = name;
      button.title = t("lang_hint", name);
    };
    paint();
    listeners.push(paint);
    button.addEventListener("click", () => setLang(next()));
  }

  global.OA_I18N = {
    t, tv, has, setLang, applyStatic, bindToggle,
    lang: () => lang,
    locale: () => LOCALES[lang] || lang,
    languages: () => LANGS.slice(),
    onChange: fn => listeners.push(fn),
  };
})(window);
