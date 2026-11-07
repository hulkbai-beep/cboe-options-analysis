#!/usr/bin/env python3
"""
Options analysis of one US stock, ETF or index from CBOE's delayed option chain (plus optional FMP and Massive
data), or the market risk thermometer: writes metrics.json (every computed number) and a self-contained chart.html.

  python scripts/analyze.py TICKER [--out DIR] [--data-dir DIR] [--refresh] [--no-save] [--no-market-context]
  python scripts/analyze.py --market [--out DIR] [--data-dir DIR] [--refresh] [--no-save]

  --market             the market view (SPX, SPY, QQQ, IWM, VIX) instead of one ticker
  --out                output folder (default ./oa-output/<TICKER>_<as_of>/ or ./oa-output/MARKET_<as_of>/)
  --data-dir           history store (default $OA_DATA_DIR, else ~/options-data)
  --refresh            ignore the CBOE, FMP and Massive caches and fetch again
  --no-save            do not store today's chain and summary row in the history store
  --no-market-context  skip the SPX / VIX market context (two fewer CBOE requests)

Exit codes: 0 done (also when FMP or Massive is unavailable); 1 the analysis could not run (unknown symbol, CBOE
unreachable, no live contracts, VIX refused in ticker mode); 2 usage problem (bad arguments).
The report (report.md) is written by Claude from metrics.json with references/report_template.md or
references/market_template.md. No API key is ever printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import platform
import sys
import time
import traceback
import urllib.parse
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import config as C
import chain as CH
import core
import fetch
import fmp_client
import history as H
import market as MK
import modules as M
import quality as Q
import strategies as ST
from cboe_client import CboeClient, CboeError
from symbols import SymbolRejected, normalize

TICKER_KEYS = ("mode", "ticker", "as_of", "quote_time", "data_source", "snapshot", "data_quality", "market_context",
               "expected_move", "term_structure", "skew", "volatility", "earnings", "positioning", "gamma", "flow",
               "news", "strategies", "history", "meta")
MARKET_KEYS = ("mode", "as_of", "quote_time", "data_source", "snapshot", "data_quality", "vix", "term_structure",
               "skew_board", "gamma_board", "expected_moves", "positioning", "realized", "thermometer", "history",
               "meta")
# the module blocks of each mode (between data_quality and meta): meta.modules_unavailable reads these
TICKER_MODULES = TICKER_KEYS[TICKER_KEYS.index("market_context"):TICKER_KEYS.index("meta")]
MARKET_MODULES = MARKET_KEYS[MARKET_KEYS.index("vix"):MARKET_KEYS.index("meta")]
# ticker-mode builders after the earnings date is known, in the fixed order of plan 3.4 (market_context separate)
MODULE_ORDER = (
    ("term_structure", M.build_term_structure),
    ("expected_move", M.build_expected_move),
    ("skew", M.build_skew),
    ("earnings", M.build_earnings),
    ("volatility", M.build_volatility),
    ("positioning", M.build_positioning),
    ("gamma", M.build_gamma),
    ("flow", M.build_flow),
    ("news", M.build_news),
)
SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent
CBOE_SOURCE = "CBOE delayed quotes (~15 min)"
MASSIVE_SOURCE = "Massive API"
# ctx key -> load_all key: the Massive rows the module builders read
MASSIVE_CTX = {"short_interest_rows": "short_interest", "short_interest_reason": "short_interest_reason",
               "short_volume_rows": "short_volume", "short_volume_reason": "short_volume_reason",
               "float_rows": "float", "float_reason": "float_reason", "news_rows": "news",
               "news_reason": "news_reason", "news_from": "news_from"}


# ---------------------------------------------------------------------------- JSON
def json_clean(obj, sig: int = C.METRICS_SIG_DIGITS):
    """Plain-JSON copy: NaN/inf -> None, numpy -> Python, dates -> "YYYY-MM-DD", tuples/sets -> lists,
    dict keys -> str, floats rounded to `sig` significant digits. Times must already be ISO strings."""
    if isinstance(obj, dict):
        return {str(k): json_clean(v, sig) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [json_clean(v, sig) for v in obj]
    if isinstance(obj, np.ndarray):
        return json_clean(obj.tolist(), sig)
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        v = float(obj)
        return float(f"{v:.{sig}g}") if math.isfinite(v) else None
    if obj is pd.NaT or obj is pd.NA or (isinstance(obj, np.datetime64) and np.isnat(obj)):
        return None
    if isinstance(obj, (pd.Timestamp, np.datetime64, dt.date)):
        return pd.Timestamp(obj).strftime("%Y-%m-%d")
    return str(obj)


def dumps(metrics: dict) -> str:
    return json.dumps(metrics, ensure_ascii=False, indent=1, allow_nan=False)


def pair_ids(obj):
    """Every dict with notes / warnings gets note_ids / warning_ids of the same length (None = no fixed id)."""
    if isinstance(obj, dict):
        for text_key, id_key in (("notes", "note_ids"), ("warnings", "warning_ids")):
            if isinstance(obj.get(text_key), list):
                ids = obj.get(id_key)
                ids = list(ids) if isinstance(ids, list) else []
                ids = (ids + [None] * len(obj[text_key]))[:len(obj[text_key])]
                obj[id_key] = ids
        for v in obj.values():
            pair_ids(v)
    elif isinstance(obj, list):
        for v in obj:
            pair_ids(v)
    return obj


# ---------------------------------------------------------------------------- pieces
def fmp_data_source(connection: dict | None) -> str | None:
    """"FMP stable API (official)" or "FMP stable API via third-party proxy <host>" (None without a client)."""
    if not connection:
        return None
    base = str(connection.get("base") or "")
    host = urllib.parse.urlparse(base).hostname or base or "unknown host"
    if host.lower() in fmp_client.OFFICIAL_HOSTS:
        return "FMP stable API (official)"
    return f"FMP stable API via third-party proxy {host}"


def data_source(fmp_connection: dict | None, massive_connection: dict | None = None) -> str:
    """CBOE, then FMP's route (or "; FMP not configured"); " + Massive API" only when Massive is configured."""
    src = fmp_data_source(fmp_connection)
    extra = f" + {MASSIVE_SOURCE}" if massive_connection else ""
    return f"{CBOE_SOURCE} + {src}{extra}" if src else f"{CBOE_SOURCE}{extra}; FMP not configured"


def config_snapshot() -> dict:
    return {
        "surface": {"fwd_strike_range": C.FWD_STRIKE_RANGE, "fwd_median_n": C.FWD_MEDIAN_N,
                    "atm_max_bracket": C.ATM_MAX_BRACKET, "discount_rate": C.DISCOUNT_RATE,
                    "iv_price_check": [C.IV_PRICE_CHECK_DTE, C.IV_PRICE_CHECK_TOL]},
        "term": {"cm_tenors": list(C.CM_TENORS), "cm_min_dte": C.CM_MIN_DTE, "inverted": C.TERM_INVERTED,
                 "upward": C.TERM_UPWARD, "kink": [C.KINK_RATIO, C.KINK_MIN_EXCESS, C.KINK_MIN_DTE, C.KINK_MIN_GAP]},
        "skew": {"delta": C.SKEW_DELTA, "min_dte": C.SKEW_MIN_DTE, "labels": C.SKEW_LABELS},
        "gamma": {"grid_pct": C.GEX_GRID_PCT, "grid_points": C.GEX_GRID_POINTS, "check_min_corr": C.GAMMA_CHECK_MIN_CORR},
        "positioning": {"oi_window_dte": C.OI_WINDOW_DTE, "key_level_range": C.KEY_LEVEL_RANGE,
                        "unusual": [C.UA_MIN_VOLUME, C.UA_VOL_OI, C.UA_TOP]},
        "earnings": {"reactions_max": C.EARNINGS_REACTIONS_MAX, "reactions_min": C.EARNINGS_REACTIONS_MIN,
                     "window_days": C.REACTION_WINDOW_DAYS, "date_check_max_days": C.DATE_CHECK_MAX_DAYS},
        "volatility": {"hv_windows": list(C.HV_WINDOWS), "pctl_lookback": C.PCTL_LOOKBACK,
                       "ivr": [C.IVR_LOOKBACK, C.IVR_MIN_OBS]},
        "strategies": {"min_oi": C.STRAT_MIN_OI, "max_spread": C.STRAT_MAX_SPREAD, "max_rows": C.STRAT_MAX_ROWS,
                       "example_account": C.EXAMPLE_ACCOUNT},
        "thermometer": {"vix_level": C.THERMO_VIX_LEVEL, "front_ratio": C.THERMO_FRONT_RATIO,
                        "min_available": C.THERMO_MIN_AVAILABLE},
        "session": {"buffer_min": C.SESSION_BUFFER_MIN, "gth_start": C.GTH_START.strftime("%H:%M")},
        "cboe": {"ttl_open": C.CBOE_TTL_OPEN, "min_interval": C.CBOE_MIN_INTERVAL, "attempts": C.CBOE_ATTEMPTS},
    }


def _safe(name: str, fn, errors: list, *args):
    """fn(*args); an exception becomes {"available": False, "reason": "internal error: ..."} and an entry in
    `errors`, so one failing module does not stop the others."""
    try:
        return fn(*args)
    except Exception as exc:  # reported in meta.errors and the module's reason
        frames = traceback.extract_tb(exc.__traceback__)
        ours = [f for f in frames if Path(f.filename).resolve().parent == SCRIPTS_DIR]
        frame = (ours or frames)[-1]
        msg = f"{type(exc).__name__}: {exc} ({Path(frame.filename).name}:{frame.lineno})"
        errors.append(f"{name}: {msg}")
        print(f"[analyze] {name} failed: {msg}", file=sys.stderr)
        return {"available": False, "reason": f"internal error: {msg}", "notes": [], "note_ids": []}


def _unavailable(modules: dict) -> dict:
    """{module or module.block: reason} for everything reported as unavailable."""
    out = {}
    for name, m in modules.items():
        if not isinstance(m, dict):
            continue
        if m.get("available") is False:
            out[name] = m.get("reason") or "unavailable"
            continue
        for key, b in m.items():
            if isinstance(b, dict) and b.get("available") is False and b.get("reason"):
                out[f"{name}.{key}"] = b["reason"]
    return out


def _iso_now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _versions() -> dict:
    return {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__}


def _clients(client, fmp, refresh: bool, massive=None):
    """(CBOE client, FmpState, MassiveState): the given clients, else ones made from the environment."""
    cboe = client if client is not None else CboeClient(refresh=refresh)
    if isinstance(fmp, fetch.FmpState):
        fs = fmp
    elif fmp is not None:
        fs = fetch.FmpState(fmp)
    else:
        fs = fetch.FmpState(*fetch.make_fmp(refresh))
    if isinstance(massive, fetch.MassiveState):
        ms = massive
    elif massive is not None:
        ms = fetch.MassiveState(massive)
    else:
        ms = fetch.MassiveState(*fetch.make_massive(refresh))
    return cboe, fs, ms


def _meta(cboe, fs, ms, calls0, started, errors, mods, notes, skipped, fetched_at) -> dict:
    cb_calls = int(cboe.stats["network_calls"]) - calls0[0]
    cb_hits = int(cboe.stats["cache_hits"]) - calls0[1]
    f_calls, f_hits = fs.calls()
    f_calls, f_hits = f_calls - calls0[2], f_hits - calls0[3]
    m_calls, m_hits = ms.calls()
    m_calls, m_hits = m_calls - calls0[4], m_hits - calls0[5]
    cdesc = cboe.describe() if hasattr(cboe, "describe") else {"source": "CBOE delayed quotes"}
    fdesc, mdesc = fs.describe(), ms.describe()
    return {
        "network_calls": cb_calls + f_calls + m_calls, "cache_hits": cb_hits + f_hits + m_hits,
        "restricted": list(fs.restricted) + list(ms.restricted), "errors": list(errors), "skipped": dict(skipped),
        "modules_unavailable": _unavailable(mods), "notes": list(notes) + list(fs.notes) + list(ms.notes),
        "connection": {"cboe": {**{k: cdesc.get(k) for k in ("source", "base", "redirected_to")}, "calls": cb_calls},
                       "fmp": ({"route": fdesc.get("route"), "base": fdesc.get("base"),
                                "tls_verify": fdesc.get("tls_verify"), "calls": f_calls} if fdesc else None),
                       "massive": ({"route": mdesc.get("route"), "base": mdesc.get("base"),
                                    "tls_verify": mdesc.get("tls_verify"), "calls": m_calls} if mdesc else None)},
        "fetched_at": fetched_at, "generated_at": _iso_now(), "runtime_seconds": round(time.time() - started, 2),
        "versions": _versions(), "config_snapshot": config_snapshot(), "disclaimer": C.DISCLAIMER,
    }


def _calls0(cboe, fs, ms) -> tuple:
    return (int(cboe.stats["network_calls"]), int(cboe.stats["cache_hits"])) + fs.calls() + ms.calls()


def _today_ny(now) -> dt.date:
    return CH.to_ny(now).date()


def _massive_skipped(data: dict, ms, discounted: bool) -> dict:
    """meta.skipped entries for the Massive pieces a configured Massive did not deliver (`discounted`: European
    straddles in this run, of the ticker or of the market context's SPX)."""
    if not ms.configured:
        return {}
    out = {}
    if data["sym"]["kind"] != "index":
        for name in ("short_interest", "short_volume", "news") + (("float",) if data["sym"]["kind"] == "stock" else ()):
            if data.get(name) is None and data.get(f"{name}_reason"):
                out[name] = data[f"{name}_reason"]
    if data.get("rates") is None and discounted and data.get("rates_reason"):
        out["treasury_curve"] = data["rates_reason"]
    if data.get("holidays") is None and data.get("holidays_reason"):
        out["calendar_check"] = data["holidays_reason"]
    return out


def _fetched_at(cboe, symbol: str) -> str | None:
    info = (getattr(cboe, "fetch_info", None) or {}).get(symbol.upper())
    return (info or {}).get("fetched_at") or _iso_now()


# ---------------------------------------------------------------------------- ticker mode
def _snapshot(ctx: dict, data: dict, view) -> dict:
    s, sym, info = ctx["session"], data["sym"], ctx["info"]
    prev_close, change_pct = s.get("prev_close"), s.get("change_pct")
    bars = data.get("bars")
    if prev_close is None and bars is not None and len(bars):
        before = bars[bars.index < pd.Timestamp(ctx["as_of"])]
        if len(before) and s.get("px"):
            prev_close = float(before.iloc[-1])
            change_pct = float(s["px"]) / prev_close - 1
    iv30_change, src = None, None
    prev = view.previous("iv30_cboe") if view is not None else None
    if prev and prev[0] == CH.prev_trading_day(ctx["as_of"]) and s.get("iv30_cboe") is not None:
        iv30_change, src = s["iv30_cboe"] - prev[1], "history"
    elif s.get("iv30_change_cboe") is not None:
        iv30_change, src = s["iv30_change_cboe"], "cboe"
    return {
        "ticker": ctx["key"], "cboe_symbol": sym["cboe"], "fmp_symbol": sym["fmp"], "name": data.get("name"),
        "kind": ctx["kind"], "kind_source": sym["kind_source"], "spot": s["spot"], "spot_basis": s["spot_basis"],
        "close": s.get("close"), "current_price": s.get("current_price"), "prev_close": prev_close,
        "change_pct": change_pct, "iv30_cboe": s.get("iv30_cboe"), "iv30_change": iv30_change,
        "iv30_change_source": src,
        "session": {"closed": s["closed"], "overnight_quotes": s["overnight_quotes"], "feed_time": CH.iso(s["feed_time"]),
                    "data_time": CH.iso(s["data_dt"]), "as_of": s["as_of"]},
        "chain": {k: info[k] for k in ("contracts_total", "contracts_live", "expirations", "excluded_roots",
                                       "valid_quote_share", "valid_iv_share")},
    }


def _market_context(data: dict, ctx: dict, errors: list) -> tuple[dict, dict | None]:
    mk = data.get("market")
    if mk is None:
        return M.unavailable("market context switched off (--no-market-context)"), None
    spx_ctx, spx_reason = None, mk.get("spx_reason")
    if ctx["key"] == "SPX":
        spx_ctx = ctx
    elif mk.get("spx_payload") is not None:
        try:
            spx_ctx = MK.member("SPX", "index", mk["spx_payload"], rates=data.get("rates"))
        except Exception as e:  # the market context is optional
            spx_reason = f"SPX computation failed: {type(e).__name__}: {e}"
            errors.append(f"market_context: {spx_reason}")
    vix_session = CH.session_from_payload(mk["vix_payload"]) if mk.get("vix_payload") is not None else None
    out = MK.market_context(spx_ctx, spx_reason, vix_session, mk.get("vix_rows"), ctx["as_of"])
    return out, spx_ctx


def run_analysis(ticker: str, client=None, fmp=None, now=None, refresh: bool = False, save: bool = True,
                 market_context: bool = True, data_dir=None, massive=None) -> tuple[dict, dict | None]:
    """Fetch, compute every module and return (metrics, chart_data) for one ticker (plan 3.4). `client`, `fmp` and
    `massive` default to clients made from the environment (FMP and Massive are optional).
    Raises SymbolRejected / ValueError (bad symbol, VIX, no live contracts) and CboeError (NotFound, CboeUnreachable)."""
    normalize(ticker)                                     # refuse VIX / DJX / foreign listings before any request
    started = time.time()
    cboe, fs, ms = _clients(client, fmp, refresh, massive)
    calls0 = _calls0(cboe, fs, ms)
    data = fetch.load_all(ticker, cboe, fs, now=now, market_context=market_context, ms=ms)
    sym, session, frame = data["sym"], data["session"], data["frame"]
    key, kind = data["key"], sym["kind"]
    as_of = session["as_of"]
    earn_date = M.next_earnings(data["earnings_rows"], as_of, session["closed"]) if kind == "stock" else None
    ctx = core.chain_context(key, kind, session, frame, earn_date, rates=data["rates"])
    if ctx["info"]["contracts_live"] == 0:
        raise ValueError(f"no live contracts in the {sym['cboe']} chain (as of {as_of})")
    root = H.data_dir(data_dir)
    view = H.HistoryView(root, key, as_of)
    ctx.update({k: data[k] for k in ("earnings_rows", "dividends", "dividends_source", "dividends_reason", "bars",
                                     "bars_source", "bars_proxy", "iv_index", "iv_index_symbol", "iv_index_reason")})
    ctx.update({k: data[v] for k, v in MASSIVE_CTX.items()})
    ctx["earnings_reason"] = data.get("earnings_reason") if kind == "stock" else None
    if kind == "stock" and earn_date is None and data.get("earnings_rows"):
        ctx["earnings_reason"] = "no upcoming earnings date from FMP"
    ctx["bars_reason"] = data.get("bars_reason")
    ctx["history"] = view
    errors: list[str] = []
    mods = ctx["mods"]
    for name, fn in MODULE_ORDER:
        mods[name] = _safe(name, fn, errors, ctx)
    if isinstance(mods.get("positioning"), dict):
        mods["positioning"]["short"] = _safe("positioning.short", M.build_short, errors, ctx)
    res = _safe("market_context", _market_context, errors, data, ctx, errors)
    mods["market_context"], ctx["spx_ctx"] = res if isinstance(res, tuple) else (res, None)

    # history: store today's closed chain and summary row, then the ranks already read (H excludes as_of)
    saved, row = None, None
    if save and session["closed"] and not session["overnight_quotes"]:
        try:
            saved = H.save_chain(root, key, data["payload"], session)
            row = H.summary_from_ctx(ctx)
            H.append_summary(root, key, row)
        except Exception as e:  # a read-only disk must not stop the analysis
            errors.append(f"history: {type(e).__name__}: {e}")
    elif session["overnight_quotes"]:
        saved = {"saved": False, "reason": "overnight (GTH) quotes are not stored"}
    elif not session["closed"]:
        saved = {"saved": False, "reason": "intraday data is not stored"}
    elif not save:
        saved = {"saved": False, "reason": "--no-save"}
    mods["strategies"] = _safe("strategies", ST.build_strategies, errors, ctx)
    view2 = H.HistoryView(root, key, as_of)
    hist = H.history_block(root, key, view2, saved, row)

    dq = Q.data_quality(ctx, now)
    Q.calendar_check(dq, data.get("holidays"), data.get("holidays_reason"), _today_ny(now))
    european = bool(ctx["sf"]["european"].any()) if len(ctx["sf"]) else False
    metrics = {
        "mode": "ticker", "ticker": key, "as_of": as_of, "quote_time": CH.iso(session["feed_time"]),
        "data_source": data_source(fs.describe(), ms.describe()), "snapshot": _snapshot(ctx, data, view),
        "data_quality": dq, "market_context": mods["market_context"],
        **{k: mods[k] for k in ("expected_move", "term_structure", "skew", "volatility", "earnings", "positioning",
                                "gamma", "flow", "news", "strategies")},
        "history": hist,
    }
    snap = metrics["snapshot"]
    discounted = european or ctx.get("spx_ctx") is not None      # this chain, or the SPX of the market context
    curve, why = (data["rates"], data["rates_reason"]) if discounted else (None, fetch.RATES_NOT_NEEDED)
    snap["rates"] = fetch.rates_block(curve, why, european)
    snap["notes"], snap["note_ids"] = [], []
    for n, nid in zip(data["notes"], data["note_ids"]):
        if nid:
            M.add_note(snap, nid=nid["id"], args=tuple(nid["args"]))
        else:
            M.add_note(snap, n)
    skipped = {}
    if data.get("fmp_profile_reason") and kind != "index":
        skipped["profile"] = data["fmp_profile_reason"]
    if kind in ("stock", "etf") and data.get("dividends_reason") and data.get("dividends_source") != "massive":
        skipped["dividends"] = data["dividends_reason"]
    if data.get("bars_reason"):
        skipped["daily_bars"] = data["bars_reason"]
    if kind == "stock" and data.get("earnings_reason") and not data.get("earnings_rows"):
        skipped["earnings"] = data["earnings_reason"]
    skipped.update(_massive_skipped(data, ms, discounted))
    chart_data = None
    metrics["meta"] = _meta(cboe, fs, ms, calls0, started, errors, {k: metrics[k] for k in TICKER_MODULES},
                            [], skipped, _fetched_at(cboe, sym["cboe"]))
    metrics = pair_ids(metrics)
    try:
        import chart
        chart_data = chart.build_chart_data(ctx, metrics)
    except Exception as exc:  # the metrics stand without the chart
        msg = f"chart: {type(exc).__name__}: {exc}"
        metrics["meta"]["errors"].append(msg)
        print(f"[analyze] {msg}", file=sys.stderr)
    return json_clean({k: metrics[k] for k in TICKER_KEYS}), (json_clean(chart_data, C.CHART_VALUE_SIG_DIGITS)
                                                              if chart_data is not None else None)


# ---------------------------------------------------------------------------- market mode
def run_market(client=None, fmp=None, now=None, refresh: bool = False, save: bool = True, universe=None,
               data_dir=None, massive=None) -> tuple[dict, dict | None]:
    """The market view (plan 7). Raises the CboeError of _SPX when SPX is unavailable."""
    started = time.time()
    cboe, fs, ms = _clients(client, fmp, refresh, massive)
    calls0 = _calls0(cboe, fs, ms)
    universe = tuple(universe or C.MARKET_UNIVERSE)
    errors: list[str] = []
    skipped: dict = {}
    members: dict = {}
    payloads: dict = {}
    spx_payload = cboe.get_chain("_SPX")                  # SPX is required: its CboeError ends the run (exit 1)
    rates, rates_reason = fetch.load_rates(ms, CH.session_from_payload(spx_payload)["as_of"])
    if rates is None and ms.configured:
        skipped["treasury_curve"] = rates_reason
    for cboe_sym, kind in universe:
        key = cboe_sym.lstrip("_")
        try:
            payload = spx_payload if cboe_sym == "_SPX" else cboe.get_chain(cboe_sym)
        except CboeError as e:
            members[key] = {"available": False, "reason": str(e), "kind": kind}
            skipped[key] = str(e)
            continue
        payloads[key] = payload
        try:
            members[key] = MK.member(key, kind, payload, rates=rates)
        except Exception as e:  # one member must not stop the others
            msg = f"{key}: {type(e).__name__}: {e}"
            errors.append(msg)
            members[key] = {"available": False, "reason": f"internal error: {msg}", "kind": kind}
    spx = members.get("SPX")
    if not isinstance(spx, dict) or "mods" not in spx:
        raise ValueError(f"SPX analysis failed: {(spx or {}).get('reason')}")
    as_of, s_spx = spx["as_of"], spx["session"]
    vix_payload, vix_reason = fetch.chain_or_reason(cboe, "_VIX")
    if vix_payload is None:
        skipped["VIX"] = vix_reason
    memo: dict = {}
    rows = {ix: fetch.index_or_reason(cboe, ix, memo) for ix in C.MARKET_INDICES}
    for ix, (r, why) in rows.items():
        if r is None:
            skipped[f"{ix}_History"] = why
    vix_session = CH.session_from_payload(vix_payload) if vix_payload is not None else None
    vix = MK.vix_block(vix_session, rows["VIX"][0], rows["VIX3M"][0], rows["VIX9D"][0], as_of)
    gspc, gspc_reason = fetch.load_bars(fs, C.SPX_FMP, as_of)
    hv20 = None
    if gspc is not None and len(gspc) > max(C.HV_WINDOWS):
        hv20 = M.realized(gspc)["hv20"]
    elif gspc is not None:
        gspc_reason = f"only {len(gspc)} daily closes of {C.SPX_FMP}"
    if gspc_reason:
        skipped["daily_bars"] = gspc_reason
    root = H.data_dir(data_dir)
    views = {k: H.HistoryView(root, k, m["as_of"]) for k, m in members.items() if "mods" in m}

    # boards
    def avail(m):
        return isinstance(m, dict) and "mods" in m

    ts = dict(spx["mods"]["term_structure"])
    ts["ratio_30_90_pctl"] = views["SPX"].pctl("ratio_30_90", ts.get("ratio_30_90")) if ts.get("available") else None
    skew_rows, gamma_rows, pcr_rows, em_rows = [], [], [], []
    for k, m in members.items():
        if not avail(m):
            skew_rows.append({"key": k, "available": False, "reason": m.get("reason")})
            gamma_rows.append({"key": k, "available": False, "reason": m.get("reason")})
            pcr_rows.append({"key": k, "available": False, "reason": m.get("reason")})
            continue
        sk, gm, pos = m["mods"]["skew"], m["mods"]["gamma"], m["mods"]["positioning"]
        skew_rows.append({"key": k, "available": bool(sk.get("available")), "reason": sk.get("reason"),
                          "rr25_30d": sk.get("rr25_30d"), "bf25_30d": sk.get("bf25_30d"), "label": sk.get("label"),
                          "rr25_pctl": views[k].pctl("rr25_30d", sk.get("rr25_30d"))})
        gamma_rows.append({"key": k, "available": bool(gm.get("available")), "reason": gm.get("reason"),
                           "net_gex_1pct": gm.get("net_gex_1pct"), "regime": gm.get("regime"),
                           "zero_gamma": gm.get("zero_gamma"), "zero_gamma_dist_pct": gm.get("zero_gamma_dist_pct"),
                           "applicability": gm.get("applicability"),
                           "net_gex_pctl": views[k].pctl("net_gex_1pct", gm.get("net_gex_1pct"))})
        pcr = ((pos.get("pcr") or {}).get("all")) or {}
        pcr_rows.append({"key": k, "available": bool(pos.get("available")), "pcr_all_volume": pcr.get("volume"),
                         "pcr_all_oi": pcr.get("oi")})
        if k in C.MARKET_EM_KEYS:
            for r in (m["mods"]["expected_move"].get("rows") or []):
                if {"next_week", "monthly"} & set(r["tags"]):
                    em_rows.append({"key": k, "expiration": r["expiration"], "tags": r["tags"], "dte": r["dte"],
                                    "straddle_pct": r["straddle_pct"], "move_1sd_pct": r["move_1sd_pct"],
                                    "range_1sd": r["range_1sd"]})
    cm30 = (ts.get("cm_iv") or {}).get("30d") if ts.get("available") else None
    realized = (M.block(spx_hv20=hv20, spx_cm_iv_30d=cm30, vrp=(cm30 - hv20) if (cm30 is not None and hv20 is not None)
                        else None, source=C.SPX_FMP)
                if hv20 is not None else M.unavailable(gspc_reason or "no S&P 500 daily bars", source=C.SPX_FMP,
                                                       spx_cm_iv_30d=cm30))
    thermo = MK.thermometer(vix, spx["mods"], hv20, gspc_reason)

    # data quality per member
    dq = {"delay_note": C.DELAY_NOTE, "stale": CH.stale_info(as_of, now), "warnings": [], "warning_ids": [],
          "members": {}}
    for k, m in members.items():
        if not avail(m):
            continue
        q = Q.data_quality(m, now)
        dq["members"][k] = {kk: q[kk] for kk in ("overnight_quotes", "prev_close_rolled", "negative_forward_variance",
                                                 "dropped_expiries", "iv_price_check", "gamma_check", "warnings",
                                                 "warning_ids")}
        for w, wid in zip(q["warnings"], q["warning_ids"]):
            dq["warnings"].append(f"{k}: {w}")
            dq["warning_ids"].append({**wid, "key": k} if wid else None)
    holidays, holidays_reason = ms.results(fetch.MC.HOLIDAYS, wait=0)     # last: only when a slot is free at once
    Q.calendar_check(dq, holidays, holidays_reason, _today_ny(now))
    if holidays is None and ms.configured:
        skipped["calendar_check"] = holidays_reason

    # history: chains for every available member (VIX chain only), summary rows except VIX
    saved_keys, hist_days = [], {}
    if save:
        for k, m in members.items():
            if not avail(m):
                continue
            s = m["session"]
            if not s["closed"] or s["overnight_quotes"]:
                continue
            try:
                res = H.save_chain(root, k, payloads[k], s)
                H.append_summary(root, k, H.summary_from_ctx(m))
                if res.get("saved"):
                    saved_keys.append(k)
            except Exception as e:
                errors.append(f"history {k}: {type(e).__name__}: {e}")
        if vix_session is not None and vix_session["closed"] and not vix_session["overnight_quotes"]:
            try:
                if H.save_chain(root, "VIX", vix_payload, vix_session).get("saved"):
                    saved_keys.append("VIX")
            except Exception as e:
                errors.append(f"history VIX: {type(e).__name__}: {e}")
    for k in members:
        hist_days[k] = H.HistoryView(root, k, as_of).days
    hist = M.block(data_dir=str(root), saved_this_run=saved_keys, days=hist_days)

    universe_rows = []
    for k, m in members.items():
        if avail(m):
            s = m["session"]
            universe_rows.append({"key": k, "kind": m["kind"], "available": True, "reason": None, "spot": s["spot"],
                                  "spot_basis": s["spot_basis"], "feed_time": CH.iso(s["feed_time"]),
                                  "overnight_quotes": s["overnight_quotes"], "as_of": m["as_of"]})
        else:
            universe_rows.append({"key": k, "kind": m.get("kind"), "available": False, "reason": m.get("reason"),
                                  "spot": None, "spot_basis": None, "feed_time": None, "overnight_quotes": None})
    metrics = {
        "mode": "market", "as_of": as_of, "quote_time": CH.iso(s_spx["feed_time"]),
        "data_source": data_source(fs.describe(), ms.describe()),
        "snapshot": {"as_of": as_of, "feed_time": CH.iso(s_spx["feed_time"]), "universe": universe_rows,
                     "rates": fetch.rates_block(rates, rates_reason, True)},
        "data_quality": dq, "vix": vix, "term_structure": ts,
        "skew_board": M.block(rows=skew_rows), "gamma_board": M.block(rows=gamma_rows),
        "expected_moves": M.block(rows=em_rows), "positioning": M.block(rows=pcr_rows), "realized": realized,
        "thermometer": thermo, "history": hist,
    }
    mods = {k: metrics[k] for k in MARKET_MODULES}
    metrics["meta"] = _meta(cboe, fs, ms, calls0, started, errors, mods, [], skipped, _fetched_at(cboe, "_SPX"))
    metrics = pair_ids(metrics)
    chart_data = None
    try:
        import chart
        chart_data = chart.build_market_chart_data(members, metrics, rows)
    except Exception as exc:
        msg = f"chart: {type(exc).__name__}: {exc}"
        metrics["meta"]["errors"].append(msg)
        print(f"[analyze] {msg}", file=sys.stderr)
    return json_clean({k: metrics[k] for k in MARKET_KEYS}), (json_clean(chart_data, C.CHART_VALUE_SIG_DIGITS)
                                                              if chart_data is not None else None)


# ---------------------------------------------------------------------------- output
def _inside_skill(path: Path) -> bool:
    try:
        path.resolve().relative_to(SKILL_DIR)
        return True
    except ValueError:
        return False


def default_out_dir(name: str, as_of: str, cwd=None) -> Path:
    """./oa-output/<NAME>_<as_of>/, or ~/oa-output/... when the current folder is inside the skill folder."""
    safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in name)
    base = Path(cwd or Path.cwd())
    if _inside_skill(base):
        base = Path.home()
    return base / C.OUTPUT_DIR / f"{safe}_{as_of}"


def write_outputs(metrics: dict, chart_data: dict | None, out_dir) -> dict:
    """Write metrics.json (and chart.html when there is chart data); returns their paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"metrics": out / "metrics.json", "chart": None}
    paths["metrics"].write_text(dumps(metrics) + "\n", encoding="utf-8")
    if chart_data is not None:
        try:
            import chart
            paths["chart"] = chart.write_chart_html(chart_data, out / "chart.html")
        except Exception as exc:
            print(f"[analyze] chart.html not written: {type(exc).__name__}: {exc}", file=sys.stderr)
    return paths


def _pct(x, digits=1, sign=False) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"


def _num(x, digits: int) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _line(label: str, text: str) -> str:
    return f"  {label:<13}: {text}"


def _massive_lines(m: dict) -> list[str]:
    """Summary lines for the Massive blocks of a ticker run that arrived (short data, news)."""
    lines = []
    short = (m.get("positioning") or {}).get("short") or {}
    si, sv = short.get("interest") or {}, short.get("volume") or {}
    if si.get("available") and si.get("short_interest") is not None:
        pf = f", {_pct(si['pct_float'])} of the free float" if si.get("pct_float") is not None else ""
        lines.append(_line("short", f"{si['short_interest']:,.0f} shares{pf}, {_num(si.get('days_to_cover'), 1)} "
                                    f"days to cover ({si.get('settlement_date')})"))
    if sv.get("available"):
        lines.append(_line("short volume", f"{_pct(sv.get('ratio'))} of FINRA volume on {sv.get('date')}, "
                                           f"{sv.get('days')}-day average {_pct(sv.get('avg_ratio'))}"))
    news = m.get("news") or {}
    if news.get("available"):
        c = news.get("sentiment_counts") or {}
        lines.append(_line("news", f"{news.get('n_articles')} articles in {news.get('window_days')} days; provider "
                                   f"labels: {c.get('positive', 0)} positive, {c.get('neutral', 0)} neutral, "
                                   f"{c.get('negative', 0)} negative, {c.get('unlabeled', 0)} unlabeled"))
    return lines


def summary(metrics: dict, paths: dict) -> str:
    """Short plain-text summary of a run for the terminal (English only)."""
    m, meta = metrics, metrics["meta"]
    if m["mode"] == "market":
        th, vx, ts = m["thermometer"], m["vix"], m["term_structure"]
        lines = [f"MARKET  as of {m['as_of']}  [{m['data_source']}]",
                 _line("metrics.json", str(paths["metrics"])),
                 _line("chart.html", str(paths["chart"] or "not written (see errors)"))]
        fired = [k for k, f in (th.get("flags") or {}).items() if f.get("fired")]
        lines.append(_line("thermometer", f"{th.get('label') or 'n/a'}"
                           + (" (uncertain)" if th.get("label_uncertain") else "")
                           + f", {th.get('n_flags')}/{th.get('n_available')} flags: {', '.join(fired) or 'none'}"))
        if vx.get("available"):
            lines.append(_line("VIX", f"{vx['level']:.2f} ({vx['band']}), 1y percentile "
                               f"{_num(vx['pctl_1y'], 0)}, VIX/VIX3M {_num(vx['vix_vix3m'], 3)}"))
        em = next((r for r in m["expected_moves"].get("rows") or [] if r["key"] == "SPX" and "next_week" in r["tags"]),
                  None)
        gm = next((r for r in m["gamma_board"].get("rows") or [] if r["key"] == "SPX"), {})
        sk = next((r for r in m["skew_board"].get("rows") or [] if r["key"] == "SPX"), {})
        lines.append(_line("SPX", f"next week 1sd {_pct(em['move_1sd_pct']) if em else 'n/a'}, 30/90 "
                           f"{_num(ts.get('ratio_30_90'), 3)}, rr25 {_pct(sk.get('rr25_30d'))}, "
                           f"gamma {gm.get('regime') or 'n/a'}"))
    else:
        snap = m["snapshot"]
        lines = [f"{m['ticker']}  as of {m['as_of']}  spot {snap['spot']:.2f} ({_pct(snap['change_pct'], 2, True)})  "
                 f"[{m['data_source']}]",
                 _line("metrics.json", str(paths["metrics"])),
                 _line("chart.html", str(paths["chart"] or "not written (see errors)"))]
        rows = m["expected_move"].get("rows") or []
        parts = []
        for tag, label in (("next_week", "next week"), ("monthly", "monthly")):
            r = next((r for r in rows if tag in r["tags"]), None)
            if r:
                parts.append(f"{label} {r['expiration']} +-{_pct(r['move_1sd_pct'])}")
        lines.append(_line("expected move", "; ".join(parts) or "n/a"))
        ea = m["earnings"]
        if ea.get("available"):
            ev = ea.get("event_move") or {}
            ratio = (ea.get("comparison") or {}).get("ratio_implied_to_mean")
            lines.append(_line("earnings", f"{ea['next_date']}, event 1sd {_pct(ev.get('move_1sd'))}, "
                               f"implied/past {ratio:.2f}" if ratio else
                               f"{ea['next_date']}, event 1sd {_pct(ev.get('move_1sd'))}"))
        else:
            lines.append(_line("earnings", ea.get("reason") or "n/a"))
        v = m["volatility"]
        rank = v.get("iv_rank") or {}
        ivx = v.get("iv_index") or {}
        extra = (f"IV Rank {rank['value']:.0f} ({rank['n_obs']} days)" if rank.get("available") and rank.get("value")
                 is not None else f"{ivx['symbol']} 1y percentile {ivx['pctl_1y']:.0f}" if ivx.get("available")
                 else f"IV Rank {rank['reason']}" if rank.get("reason") else "no percentile")
        lines.append(_line("IV", f"30d {_pct(v.get('cm_iv_30d'))}, {extra}"))
        sk = m["skew"]
        lines.append(_line("skew", f"rr25 {_pct(sk.get('rr25_30d'))} ({sk.get('label') or 'n/a'})"))
        gm = m["gamma"]
        lines.append(_line("gamma", f"{gm.get('regime') or 'n/a'}, zero gamma "
                           f"{gm['zero_gamma']:.2f}" if gm.get("zero_gamma") else f"{gm.get('regime') or 'n/a'}"))
        lines += _massive_lines(m)
    lines.append(_line("API calls", f"{meta['network_calls']} network, {meta['cache_hits']} from cache"))
    for w in m["data_quality"].get("warnings") or []:
        lines.append(_line("WARNING", w))
    for e in meta["errors"]:
        lines.append(_line("error", e))
    return "\n".join(lines)


def _redact(text: str) -> str:
    for name in ("FMP_API_KEY", "MASSIVE_API_KEY"):
        key = os.environ.get(name, "").strip()
        if key:
            text = text.replace(key, "***")
    return text


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Options analysis from CBOE's delayed option chains (FMP and Massive "
                                             "optional).")
    ap.add_argument("ticker", nargs="?", help="e.g. NVDA, SPY, SPX, BRK.B (not VIX: use --market)")
    ap.add_argument("--market", action="store_true", help="the market view (SPX, SPY, QQQ, IWM, VIX)")
    ap.add_argument("--out", help="output folder (default ./oa-output/<TICKER>_<as_of>/)")
    ap.add_argument("--data-dir", help="history store (default $OA_DATA_DIR, else ~/options-data)")
    ap.add_argument("--refresh", action="store_true", help="ignore the CBOE, FMP and Massive caches and fetch again")
    ap.add_argument("--no-save", action="store_true", help="do not store today's chain and summary row")
    ap.add_argument("--no-market-context", action="store_true", help="skip the SPX / VIX market context")
    return ap


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        from winenv import load_user_env
        load_user_env()
    except Exception:
        pass
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.market == bool(args.ticker):
        ap.error("give a TICKER or --market (not both)")
    if args.market and args.no_market_context:
        ap.error("--no-market-context only applies to a ticker")
    try:
        if args.market:
            metrics, chart_data = run_market(refresh=args.refresh, save=not args.no_save, data_dir=args.data_dir)
            name = "MARKET"
        else:
            normalize(args.ticker)
            metrics, chart_data = run_analysis(args.ticker, refresh=args.refresh, save=not args.no_save,
                                               market_context=not args.no_market_context, data_dir=args.data_dir)
            name = metrics["ticker"]
    except SymbolRejected as e:
        print(f"Cannot analyse {args.ticker!r}: {e}", file=sys.stderr)
        return 1
    except CboeError as e:
        print(f"CBOE error: {_redact(str(e))}\nRun `python scripts/cboe_client.py check {args.ticker or 'SPY'}` to "
              "test the connection; an unknown symbol answers HTTP 403.", file=sys.stderr)
        return 1
    except (ValueError, LookupError) as e:
        print(f"Cannot analyse {args.ticker or 'the market'}: {_redact(str(e))}", file=sys.stderr)
        return 1
    except Exception as e:  # unexpected: show where, never the key
        print(_redact(traceback.format_exc()), file=sys.stderr)
        print(f"Unexpected error: {_redact(f'{type(e).__name__}: {e}')}", file=sys.stderr)
        return 1
    paths = write_outputs(metrics, chart_data, args.out or default_out_dir(name, metrics["as_of"]))
    print(summary(metrics, paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
