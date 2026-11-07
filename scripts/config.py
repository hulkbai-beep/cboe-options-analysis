"""
Every parameter, threshold and constant the cboe-options-analysis skill uses.

Change values here, never inline in the other scripts. references/methodology.md explains the
reasoning behind the defaults; each run records the key ones in meta.config_snapshot. Units are given
for every constant: IVs, volatilities, percentages and skews are decimals (0.25 = 25%) unless noted.
"""
from __future__ import annotations

import math
from datetime import date, time as dtime

# ---------------------------------------------------------------------------- identity and output
SKILL_NAME = "cboe-options-analysis"
USER_AGENT = "cboe-options-analysis-skill/1.0"   # sent to CBOE and FMP
DISCLAIMER = "This is options-market analysis for information only, not investment advice."
OUTPUT_DIR = "oa-output"          # ./oa-output/<KEY>_<as_of>/ (or ~/oa-output/... from inside the skill folder)
METRICS_SIG_DIGITS = 6            # metrics.json floats are rounded to this many significant digits
CHART_PRICE_DECIMALS = 4          # decimals kept for prices in chart data
CHART_VALUE_SIG_DIGITS = 6        # significant digits kept for other chart floats (at least CHART_PRICE_DECIMALS decimals)

# ---------------------------------------------------------------------------- folders (environment overrides)
DATA_DIR_ENV = "OA_DATA_DIR"              # history store (chains/, summary/, raw/, logs/, tickers.txt)
DATA_DIR_DEFAULT = "options-data"         # ~/options-data
CACHE_DIR_ENV = "OA_CACHE_DIR"            # CBOE response cache
CACHE_DIR_DEFAULT = (".cache", "cboe-options")   # ~/.cache/cboe-options
TEST_DATA_ENV = "OA_TEST_DATA"            # real fixtures for the offline tests (outside the skill folder)
CACHE_SUBDIR = "cboe-options-fmp"         # FMP cache: ~/.cache/<this> unless FMP_CACHE_DIR is set (fmp_client.py)

# ---------------------------------------------------------------------------- CBOE client (seconds)
CBOE_CHAIN_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
CBOE_INDEX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{index}_History.csv"
CBOE_BASE = "https://cdn.cboe.com/api/global"
CBOE_TTL_OPEN = 300               # cache lifetime of a chain whose session is still open (or overnight quotes)
CBOE_TIMEOUT = 60                 # per request
CBOE_MIN_INTERVAL = 1.0           # at least this long between two requests of one process
CBOE_ATTEMPTS = 4                 # tries per request on 429 / 5xx / a non-AccessDenied 403 / network errors
CBOE_BACKOFF = (2, 4, 8)          # waits between those tries
CBOE_NO_RESPONSE_LIMIT = 2        # tries without any HTTP response before the client fails fast

# ---------------------------------------------------------------------------- FMP (optional)
MARKET_TZ = "America/New_York"
MARKET_CLOSE_BUFFER = "16:15"     # fmp_client's same-session cache rule only (session logic uses SESSION_BUFFER_MIN)
VIX = "^VIX"
SPX_FMP = "^GSPC"                 # FMP daily bars for SPX (and XSP x 0.1)
EOD_LOOKBACK_DAYS = 1130          # calendar days of FMP daily bars (~3 years 1 month: 12 earnings, 1-year HV percentile)

# ---------------------------------------------------------------------------- Massive (optional, massive_client.py)
MASSIVE_BASE = "https://api.massive.com"          # formerly Polygon.io (api.polygon.io is being retired)
MASSIVE_CACHE_SUBDIR = "cboe-options-massive"     # ~/.cache/<this> unless MASSIVE_CACHE_DIR is set
MASSIVE_MAX_PER_MIN = 5           # requests per minute (the free plan); the MASSIVE_MAX_PER_MIN variable overrides
MASSIVE_MAX_WAIT = 65             # seconds a request may wait for a free slot (one 60 s window); longer -> skipped
MASSIVE_TIMEOUT = 30              # seconds per response
SHORT_INTEREST_ROWS = 6           # short-interest settlements read (twice a month: about three months)
SHORT_VOLUME_DAYS = 10            # trading days of short sale volume
NEWS_LOOKBACK_DAYS = 3            # calendar days of news before the data time
NEWS_FETCH = 50                   # articles read per run
NEWS_ROWS = 8                     # headlines kept in metrics.json (newest first)
NEWS_FOCUS_MAX_TICKERS = 3        # articles tagged with at most this many tickers are kept first (roundups after)
NEWS_TEXT_CHARS = 300             # titles and sentiment reasoning are cut to this many characters
RATES_LAG_DAYS = 2                # the curve that discounts a day is the latest one dated this many days earlier
                                  # or before, counting days when both the NYSE and the Federal Reserve Board are
                                  # open (always published by then; fetch.rates_cutoff)
RATES_MAX_AGE_DAYS = 7            # a Treasury curve dated more than this many calendar days before as_of is not used
TREASURY_TENORS = (               # Massive /fed/v1/treasury-yields field -> maturity in years (yields in percent)
    ("yield_1_month", 1 / 12), ("yield_3_month", 0.25), ("yield_1_year", 1.0), ("yield_2_year", 2.0),
    ("yield_5_year", 5.0), ("yield_10_year", 10.0), ("yield_30_year", 30.0),
)

# ---------------------------------------------------------------------------- calendar (maintain yearly; check NYSE)
MARKET_HOLIDAYS = (
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
    date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
)
EARLY_CLOSES = {date(2026, 11, 27): dtime(13, 0), date(2026, 12, 24): dtime(13, 0), date(2027, 11, 26): dtime(13, 0)}
REGULAR_OPEN = dtime(9, 30)
REGULAR_CLOSE = dtime(16, 0)
SESSION_BUFFER_MIN = 15           # CBOE quote delay (minutes): closed at close + 15, stale after 09:30 + 15
GTH_START = dtime(20, 15)         # Cboe global trading hours start for SPX/XSP/VIX options (New York time)
GTH_ROOTS = frozenset({"SPX", "SPXW", "XSP", "VIX", "VIXW"})

# ---------------------------------------------------------------------------- symbols
# key -> (CBOE symbol, FMP symbol, FMP price multiplier); FMP None = no FMP daily bars on the current plan
INDEX_SYMBOLS = {
    "SPX": ("_SPX", "^GSPC", 1.0),
    "XSP": ("_XSP", "^GSPC", 0.1),    # XSP = SPX / 10
    "RUT": ("_RUT", "^RUT", 1.0),
    "NDX": ("_NDX", None, None),      # ^NDX is HTTP 402 on the current FMP plan: QQQ stands in (NDX_PROXY)
    "VIX": ("_VIX", "^VIX", 1.0),     # snapshots and the market view only (FUTURES_SETTLED)
}
NDX_PROXY = "QQQ"                 # daily bars used for NDX realized volatility and the price cone
FUTURES_SETTLED = frozenset({"VIX", "VIXW"})   # refused in ticker mode: VIX options settle to VIX futures
UNSUPPORTED = {"DJX": "DJX options are not supported yet"}
KNOWN_ETFS = frozenset({"SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "HYG", "EEM", "EFA", "XLE", "XLF",
                        "XLK", "SMH", "KRE", "ARKK"})   # ETFs without an FMP profile (CBOE calls ETFs "stock")
VIX_FUTURES_MESSAGE = ("VIX options settle to VIX futures, not spot VIX: expected moves, probabilities and strategy "
                       "screens do not apply. Use --market for the VIX level and percentile.")
INDEX_FALLBACK_NOTE = "index not in INDEX_SYMBOLS: settlement and exercise style unverified"

# ---------------------------------------------------------------------------- roots and settlement
STANDARD_ROOTS = {                 # list order = priority for the per-expiry surface
    "SPX": ("SPXW", "SPX"), "NDX": ("NDXP", "NDX"), "RUT": ("RUTW", "RUT"), "XSP": ("XSP",),
    "VIX": ("VIX", "VIXW"),
}                                  # any other key: the key without "." and "_" (BRK.B -> BRKB)
AM_SETTLED_ROOTS = frozenset({"SPX", "NDX", "RUT", "VIX", "VIXW"})        # settle at the 09:30 open
EUROPEAN_ROOTS = frozenset({"SPX", "SPXW", "NDX", "NDXP", "RUT", "RUTW", "XSP", "VIX", "VIXW"})  # cash-settled

# ---------------------------------------------------------------------------- chain validity
IV_MIN, IV_MAX = 0.01, 5.0        # a usable per-contract IV lies strictly between these
MIN_T_SECONDS = 3600              # floor of time to settlement
YEAR_SECONDS = 365 * 86400        # T is in calendar years
MULTIPLIER = 100                  # shares per contract
PREV_CLOSE_ROLL_TOL = 0.005       # |prev_day_close - (px - price_change)| above this -> prev_close_rolled
IV30_CHECK_TOL = 0.01             # |iv30_cboe - cm_iv_30d| above this (1 vol point) -> warning

# ---------------------------------------------------------------------------- surface (plan 5.1)
FWD_STRIKE_RANGE = 0.20           # strikes within +-20% of spot for the forward
FWD_MEDIAN_N = 5                  # median of K + C - P over the 5 strikes with the smallest |C - P|
ATM_MAX_BRACKET = 0.10            # largest gap (relative to F) between the strikes that bracket the forward
DISCOUNT_RATE = 0.04              # discounting of European index straddles without a Treasury curve (Massive)
IV_SOLVER_BOUNDS = (1e-4, 10.0)   # bisection range for the straddle-implied volatility
IV_SOLVER_TOL = 1e-10
IV_SOLVER_MAX_ITER = 200
IV_PRICE_CHECK_DTE = 30           # ATM-IV self-check covers expiries up to this dte ...
IV_PRICE_CHECK_TOL = 0.03         # ... and flags |atm_iv / atm_iv_cboe - 1| above this

# ---------------------------------------------------------------------------- term structure and skew (5.3, 5.4)
CM_TENORS = (7, 30, 60, 90, 180)  # constant-maturity tenors (days)
CM_MIN_DTE = 2                    # expiries with dte below this are left out of the interpolation
CM_EXTRAP_7D_DAYS = 3             # 7d is flagged extrapolated when the first expiry is >= 3 days beyond 7
CM_EXTRAP_REL = 0.20              # other tenors: flagged when the gap is > 20% of the tenor
TERM_INVERTED, TERM_UPWARD = 1.02, 0.98          # IV30 / IV90 thresholds
KINK_RATIO, KINK_MIN_EXCESS = 1.5, 0.10          # kink: fwd vol >= 1.5 x median and >= median + 10 vol points
KINK_MIN_DTE, KINK_MIN_GAP = 7, 5                # forward vols use expiries with dte >= 7, at least 5 days apart
SKEW_DELTA = 0.25
SKEW_MIN_DTE = 7
SKEW_DELTA_BAND = (0.02, 0.50)    # |delta| range used for the 25-delta interpolation
SKEW_LABELS = {                   # lower bounds of steep_put, put (>=) and flat (>) for rr25 = put25 - call25
    "index_etf": (0.08, 0.03, -0.01),
    "stock": (0.06, 0.02, -0.01),
}

# ---------------------------------------------------------------------------- positioning, gamma, flow (5.7-5.9)
OI_WINDOW_DTE = 45                # key levels sum open interest over expiries up to this dte
KEY_LEVEL_RANGE = 0.10            # calls in [S, 1.1 S], puts in [0.9 S, S]
KEY_LEVEL_TOP = 3
PCR_SHORT_DTE = 30                # pcr.d30
OI_BASE_MAX_GAP_DAYS = 4          # OI change: note when the base snapshot is older than this (calendar days)
OI_CHANGE_TOP = 5
GEX_GRID_PCT = 0.15               # gamma profile from -15% to +15% of spot ...
GEX_GRID_POINTS = 601             # ... on this many points
GAMMA_CHECK_MIN_CORR = 0.95
GEX_CONVENTION = "calls +, puts -: assumes customers sell calls and buy puts (dealers long calls, short puts)"
UA_MIN_VOLUME = 500               # unusual activity: volume >= 500 ...
UA_VOL_OI = 2.0                   # ... and OI == 0 or volume / OI >= 2
UA_TOP = 10
FLOW_DIRECTION_NOTE = "volume does not show buyer or seller, or opening or closing: direction unknown"
DELAY_NOTE = "CBOE delayed quotes (~15 min)"
OI_NOTE = "open interest is as of the previous trading day's close"

# ---------------------------------------------------------------------------- earnings and volatility (5.5, 5.6)
EARNINGS_REACTIONS_MAX = 12
EARNINGS_REACTIONS_MIN = 8        # fewer -> small_sample
REACTION_WINDOW_DAYS = 2          # trading days in a historical reaction (two-day close to close)
DATE_CHECK_MAX_DAYS = 60          # date_check only for earnings within this many calendar days
NORMAL_BEYOND_1SD = 0.3173        # share of a normal distribution beyond 1 standard deviation
HV_WINDOWS = (10, 20, 60)
HV_ANNUAL = 252
PCTL_LOOKBACK = 252               # rows in every 1-year percentile window
IVR_LOOKBACK = 252                # IV Rank window (rows, including today's value)
IVR_MIN_OBS = 60                  # fewer -> "history n days, need 60"
VOL_INDEX = {"SPX": "VIX", "SPY": "VIX", "XSP": "VIX", "NDX": "VXN", "QQQ": "VXN", "RUT": "RVX", "IWM": "RVX",
             "AAPL": "VXAPL", "AMZN": "VXAZN", "GOOGL": "VXGOG", "GOOG": "VXGOG"}
VIX_BANDS = ((15.0, "low"), (20.0, "normal"), (30.0, "elevated"), (math.inf, "very_high"))   # upper bounds

# ---------------------------------------------------------------------------- strategy screens (6)
STRATEGIES = {   # option type, delta band (closed), dte window (closed), target delta
    "short_put": ("P", (-0.35, -0.10), (14, 60), -0.25),
    "covered_call": ("C", (0.10, 0.35), (14, 60), 0.25),
    "long_call": ("C", (0.30, 0.70), (30, 120), 0.50),
    "long_put": ("P", (-0.70, -0.30), (30, 120), -0.50),
}
STRAT_MIN_OI = 100
STRAT_MAX_SPREAD = 0.25           # (ask - bid) / mid
STRAT_MAX_ROWS = 8                # table rows per strategy
STRAT_CHART_ROWS = 60             # candidates per strategy in chart data
STRAT_EVENT_MAX_DTE = 60          # earnings straddle only when E2 is this close
LIQ_A_OI, LIQ_A_VOLUME, LIQ_B_OI = 1000, 100, 300
EXAMPLE_ACCOUNT = 100_000         # dollars; only for "one contract uses x% of an account"

# ---------------------------------------------------------------------------- market thermometer (7)
MARKET_UNIVERSE = (("_SPX", "index"), ("SPY", "etf"), ("QQQ", "etf"), ("IWM", "etf"))
MARKET_EM_KEYS = ("SPX", "SPY")   # members whose next_week and monthly expected moves are shown
MARKET_INDICES = ("VIX", "VIX3M", "VIX9D")
THERMO_VIX_LEVEL = 20.0           # index points
THERMO_FRONT_RATIO = 1.10         # SPX 7d / 30d IV
THERMO_LABELS = ((1, "calm"), (3, "cautious"), (6, "stressed"))   # most flags for each label
THERMO_MIN_AVAILABLE = 4
THERMO_FLAGS = ("vix_20_or_more", "term_inverted", "front_stress", "skew_steep", "negative_gamma",
                "realized_above_implied")
VIX_HISTORY_DAYS = 252            # chart: VIX and VIX3M over the last year

# ---------------------------------------------------------------------------- snapshots (3.5)
SNAPSHOT_TICKERS = ("SPY", "QQQ", "IWM", "SPX", "VIX", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
SUMMARY_VERSION = 1
CHAIN_COLUMNS = (   # the prototype's parquet schema; new columns may only be appended
    "trade_date", "snapshot_time", "underlying", "underlying_price", "underlying_close", "underlying_iv30",
    "option", "root", "expiration", "dte", "type", "strike", "bid", "ask", "mid", "bid_size", "ask_size",
    "last_trade_price", "last_trade_time", "volume", "open_interest", "iv", "delta", "gamma", "theta", "vega",
    "rho", "theo", "open", "high", "low", "prev_day_close", "change", "percent_change", "tick",
)
SUMMARY_COLUMNS = (
    "trade_date", "spot", "iv30_cboe", "cm_iv_7d", "cm_iv_30d", "cm_iv_60d", "cm_iv_90d", "cm_iv_180d",
    "cm_iv_30d_ex_event", "ratio_30_90", "rr25_30d", "bf25_30d", "straddle_30d_pct", "pcr_volume", "pcr_oi",
    "total_oi", "total_volume", "net_gex_1pct", "zero_gamma", "contracts_live", "feed_time", "closed", "late_feed",
    "summary_version",
)

# ---------------------------------------------------------------------------- chart data (8.2)
CHART_SMILE_TENORS = (7, 30, 60)  # expiries nearest these dte (plus E2) in the smile chart
CHART_SMILE_RANGE = 0.30          # |K/F - 1| shown in the smile
CHART_OI_RANGE = 0.25             # strikes within +-25% of spot in the open-interest chart
CHART_GEX_RANGE = 0.15            # strikes within +-15% in the GEX bars
CHART_CHAIN_RANGE = 0.20          # strikes within +-20% in the option-chain table
CHART_CONE_BARS = 60              # closes in the expected-move cone
CHART_FLOW_ROWS = 10
CHART_MAX_BYTES = 1_500_000       # target size of one chart payload

# ---------------------------------------------------------------------------- enumerations (UI labels: i18n.js)
# Every enumerated value written to metrics.json or chart data; ui/i18n.js has a label for each prefix + value.
ENUMS = {
    "shape_": ("inverted", "upward", "flat"),
    "skew_": ("steep_put", "put", "flat", "call"),
    "band_": ("low", "normal", "elevated", "very_high"),
    "thermo_": ("calm", "cautious", "stressed"),
    "flag_": THERMO_FLAGS,
    "strat_": ("short_put", "covered_call", "long_call", "long_put", "earnings_straddle"),
    "applic_": ("standard", "single_stock_low_confidence"),
    "liq_": ("A", "B", "C"),
    "regime_": ("positive", "negative", "near_zero"),
    "tag_": ("next", "this_week", "next_week", "monthly", "30d", "event"),
    "kind_": ("stock", "etf", "index"),
    "ksrc_": ("index_table", "fmp", "known_etfs", "default", "cboe_fallback"),
    "timing_": ("unknown",),
    "basis_": ("close", "current_price", "forward_first_expiry"),
    "method_": ("pre_post", "post_event_pair", "two_day_close_to_close"),
    "date_check_": ("kink_contains_date", "no_kink", "kink_elsewhere"),
    "window_": ("recent", "1y"),
    "settle_": ("AM", "PM"),
    "style_": ("american", "european"),
    "ivsrc_": ("straddle", "cboe_fallback"),
    "iv30src_": ("history", "cboe"),
    "evidence_": ("weak",),
    "sent_": ("positive", "neutral", "negative"),              # news sentiment toward the ticker (Massive)
    "ratesrc_": ("treasury", "fixed"),                          # snapshot.rates.source
    "divsrc_": ("fmp", "massive"),                              # where the dividends came from
    "cal_": ("closed", "early_close", "open"),                  # data_quality.calendar_check.mismatches
    # fixed sentences: notes[i] / warnings[i] / reason / error with an id (plan 8.1, 9.5)
    "note_": ("no_event_premium", "early_assignment", "ex_div_call", "closed_spread", "straddle_non_event",
              "earnings_today", "earnings_unknown", "intraday_rank", "ndx_proxy", "index_close_from",
              "ratios_from", "ex_event_unavailable", "index_fallback", "oi_base_gap", "short_interest_lag",
              "short_volume_mm", "news_third_party", "rates_fixed", "dividends_massive"),
    "warn_": ("overnight_quotes", "stale_quotes", "iv_price_check", "gamma_check", "iv30_check",
              "intraday_quotes", "no_live_quotes", "calendar_mismatch"),
    "reason_": ("ivr_building",),
    "err_": ("vix_futures", "djx"),
}

# Fixed sentences (English; the UI shows a label per id). {} placeholders take the args in order.
NOTES = {
    "no_event_premium": "no event premium detected (an inverted term structure can mask it)",
    "early_assignment": "American-style: may be assigned before expiry when deep in the money",
    "ex_div_call": ("before the ex-dividend date {}, an in-the-money call whose remaining time value is below the {} "
                    "dividend may be exercised early: the shares are called away and the dividend is lost"),
    "closed_spread": ("after-close spreads are usually wider than intraday: table prices are not executable; check "
                      "live broker quotes before trading"),
    "straddle_non_event": "most of the straddle cost comes from non-earnings days",
    "earnings_today": "earnings dated as_of; if released before the open, the move is already in the close",
    "earnings_unknown": "earnings date unknown: crossing not checked",
    "intraday_rank": "intraday value vs closing history",
    "ndx_proxy": "HV from QQQ as a proxy for NDX",
    "index_close_from": "index close from {}",
    "ratios_from": "ratios from {} close",
    "ex_event_unavailable": "ex-event IV unavailable: earnings date unknown or event move not isolated",
    "index_fallback": INDEX_FALLBACK_NOTE,
    "oi_base_gap": "open-interest base snapshot is from {}, {} calendar days before as_of",
    "short_interest_lag": "short interest is reported twice a month, about a week after the settlement date",
    "short_volume_mm": ("short sale volume includes market makers' short sales, most of them hedges: read the trend, "
                        "not one day"),
    "news_third_party": ("headlines and sentiment labels come from the news provider; they neither explain nor "
                         "forecast a price or IV move"),
    "rates_fixed": "European index options discounted at the fixed {} rate: {}",
    "dividends_massive": "dividends from Massive, not FMP ({})",
}
WARNINGS = {
    "overnight_quotes": "option quotes are from the overnight (GTH) session; the underlying price is still the {} close",
    "stale_quotes": "quotes are from {}, {} trading day(s) old",
    "iv_price_check": "ATM IV backed out of straddle prices differs from CBOE's IV by more than {} on {} expiries",
    "gamma_check": "CBOE gamma and recomputed Black-Scholes gamma disagree (correlation {})",
    "iv30_check": "CBOE iv30 {} differs from the 30-day constant-maturity IV {} by more than 1 vol point",
    "intraday_quotes": "intraday data: the session has not closed; values will change",
    "no_live_quotes": "no live two-sided quotes in the chain",
    "calendar_mismatch": ("the trading calendar in scripts/config.py differs from the exchange calendar on {} date(s) "
                          "({}): update MARKET_HOLIDAYS and EARLY_CLOSES"),
}
REASONS = {"ivr_building": "history {} days, need {}"}
