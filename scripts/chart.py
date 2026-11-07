"""
Chart data (plan 8.2) and the self-contained chart.html (plan 8.3).

build_chart_data(ctx, metrics) turns one ticker analysis (the ctx of analyze.run_analysis and its metrics) into one
JSON-safe dict for ui/chart.js: KPIs, the expected-move cone, the term structure, the smile, open interest and
volume by strike, dealer gamma, the strategy screens, the earnings comparison, the option chain around the key
expiries, unusual activity, short interest and news (Massive, optional) and the data notes (with the discount rate
and the holiday-calendar check). build_market_chart_data(members, metrics, rows) does the same for the market view.
Every number is computed here or in the modules; ui/chart.js only draws. Big chains (SPX) are cut to strike windows
first so one payload stays under CHART_MAX_BYTES. Third-party text (news, holiday names) stays text: ui/chart.js
escapes it, and a news link is kept only when it is http(s).

write_chart_html(chart_data, path) writes one HTML file with ui/chart.css, ui/i18n.js and ui/chart.js inlined and
the data embedded as a JSON block: no server, no CDN, opens offline.
"""
from __future__ import annotations

import html
import json
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

import config as C
import modules as M
import surface as SF

UI_DIR = Path(__file__).resolve().parents[1] / "ui"
KEY_TAGS = ("next", "this_week", "next_week", "monthly", "30d", "event")
TICKER_KPIS = ("price", "em_next_week", "em_monthly", "earnings", "iv30", "iv_hv", "rr25", "pcr", "gex")
MARKET_KPIS = ("vix", "vix_vix3m", "spx_iv30", "ratio_30_90", "rr25", "gex", "em_next_week", "pcr")
OI_WINDOWS = ("next", "d30", "d45", "all")
SPARK_COLUMNS = (("cm_iv_30d", "spx_iv30"), ("ratio_30_90", "ratio_30_90"), ("rr25_30d", "rr25"),
                 ("net_gex_1pct", "gex"))
NEWS_KEYS = ("available", "reason", "source", "window_from", "window_days", "n_articles", "truncated", "newest",
             "oldest", "sentiment_counts")
NEWS_ROW_KEYS = ("published_utc", "publisher", "title", "url", "sentiment", "sentiment_reasoning", "n_tickers")
RATES_KEYS = ("available", "reason", "source", "european", "curve_date", "yields", "rate_30d", "rate_1y",
              "fixed_rate")
CALENDAR_KEYS = ("available", "reason", "source", "checked_from", "checked_through", "n_dates")
MISMATCH_KEYS = ("date", "exchange", "exchange_close", "config", "config_close")


# ---------------------------------------------------------------------------- value helpers
def _num(x) -> float | None:
    """Plain float, or None for missing / non-finite values."""
    if x is None or isinstance(x, (str, bytes, bool)):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _int(x) -> int | None:
    v = _num(x)
    return None if v is None else int(round(v))


def _compact(v: float) -> float:
    """v with C.CHART_VALUE_SIG_DIGITS significant digits but at least C.CHART_PRICE_DECIMALS decimals."""
    if v == 0.0:
        return 0.0
    digits = C.CHART_VALUE_SIG_DIGITS - 1 - math.floor(math.log10(abs(v)))
    return round(v, max(C.CHART_PRICE_DECIMALS, digits))


def json_safe(obj, compact: bool = True):
    """Recursively convert to JSON-safe Python: NaN/inf -> None, numpy -> python, dates -> "YYYY-MM-DD", times ->
    ISO with offset, tuples and sets -> lists, dict keys -> str; floats go through _compact unless compact=False."""
    if isinstance(obj, dict):
        return {str(k): json_safe(v, compact) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(v, compact) for v in obj]
    if isinstance(obj, np.ndarray):
        return [json_safe(v, compact) for v in obj.tolist()]
    if obj is None or obj is pd.NaT:
        return None
    if isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        v = float(obj)
        if not math.isfinite(v):
            return None
        return _compact(v) if compact else v
    if isinstance(obj, pd.Timestamp):
        if pd.isna(obj):
            return None
        if obj.tzinfo is None and obj == obj.normalize():
            return obj.strftime("%Y-%m-%d")
        return obj.isoformat()
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, np.datetime64):
        return None if pd.isna(obj) else pd.Timestamp(obj).strftime("%Y-%m-%d")
    return str(obj)


def _get(d, *path):
    """d[path[0]][path[1]]... with None for any missing step."""
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def _avail(block) -> bool:
    return isinstance(block, dict) and bool(block.get("available"))


def _kpi(kid: str, value, unit: str, **sub) -> dict:
    return {"id": kid, "value": value, "unit": unit, "sub": sub}


def _tag_row(rows, tag):
    return next((r for r in rows or [] if tag in (r.get("tags") or [])), None)


# ---------------------------------------------------------------------------- shared blocks (ticker and market)
def _term(ctx: dict, ts: dict) -> dict:
    """Term structure: every expiry's ATM IV by dte, constant-maturity points, forward vols, kinks, events."""
    sf = ctx["sf"]
    dte_of = {e: int(d) for e, d in zip(sf["expiration"], sf["dte"])}
    use = SF.usable(sf)
    pts = [[int(r.dte), _num(r.atm_iv), r.expiration, _num(r.atm_iv_cboe)] for r in use.itertuples()
           if _num(r.atm_iv) is not None]
    out = {"available": _avail(ts), "reason": ts.get("reason"), "points": pts, "cm": [], "forward_vols": [],
           "kinks": [], "dropped": [], "event": None, "shape": ts.get("shape"), "ratio_30_90": ts.get("ratio_30_90"),
           "ratio_7_30": ts.get("ratio_7_30")}
    out["dropped"] = [{"expiration": e, "dte": dte_of.get(e), "reason": rsn if isinstance(rsn, str) else None}
                      for e, rsn, ok in zip(sf["expiration"], sf["drop_reason"], sf["bracket_ok"]) if not ok]
    if not out["available"]:
        return out
    cm, ex = ts.get("cm_iv") or {}, ts.get("cm_extrapolated") or {}
    out["cm"] = [[int(k[:-1]), cm[k], bool(ex.get(k))] for k in (f"{d}d" for d in C.CM_TENORS) if cm.get(k) is not None]

    def fw(f):
        return {"from": f["from"], "to": f["to"], "from_dte": dte_of.get(f["from"]), "to_dte": dte_of.get(f["to"]),
                "fwd_vol": f["fwd_vol"], "negative_variance": f["negative_variance"],
                "contains_earnings": f["contains_earnings"]}

    out["forward_vols"] = [fw(f) for f in ts.get("forward_vols") or []]
    out["kinks"] = [fw(f) for f in ts.get("kinks") or []]
    out["median_fwd_vol"] = ts.get("median_fwd_vol")
    d = ctx.get("earnings_date")
    if d is not None:
        ev = ctx.get("ev") or {}
        out["event"] = {"date": d, "dte": (d - ctx["as_of"]).days,
                        **{k: ({"expiration": ev[k]["expiration"], "dte": int(ev[k]["dte"])} if ev.get(k) is not None
                               else None) for k in ("e1", "e2")}}
    return out


def _gex(ctx: dict, gm: dict) -> dict:
    """Dealer gamma: net GEX by strike (within CHART_GEX_RANGE of spot) and the gamma profile."""
    out = {"available": _avail(gm), "reason": gm.get("reason"), "applicability": gm.get("applicability"),
           "regime": gm.get("regime"), "net_gex_1pct": gm.get("net_gex_1pct"), "zero_gamma": gm.get("zero_gamma"),
           "zero_gamma_dist_pct": gm.get("zero_gamma_dist_pct"), "call_wall": gm.get("call_wall"),
           "put_wall": gm.get("put_wall"), "profile_at_spot": gm.get("profile_at_spot"),
           "spot": _num(ctx["session"]["spot"]), "by_strike": [], "profile": []}
    arr = ctx.get("gamma_arrays")
    if not out["available"] or not arr:
        return out
    g, S = arr["g"], float(ctx["session"]["spot"])
    sign = np.where(g["type"] == "C", 1.0, -1.0)
    net = pd.Series(sign * g["gamma"].to_numpy(dtype=float) * g["open_interest"].to_numpy(dtype=float)
                    * C.MULTIPLIER * S * S * 0.01, index=g["strike"].to_numpy(dtype=float)).groupby(level=0).sum()
    net = net[(net.index >= S * (1 - C.CHART_GEX_RANGE)) & (net.index <= S * (1 + C.CHART_GEX_RANGE))]
    out["by_strike"] = [[float(k), float(v)] for k, v in net.items() if v != 0]
    out["profile"] = [[float(p), float(v)] for p, v in zip(arr["grid"], arr["profile"])]
    return out


# ---------------------------------------------------------------------------- ticker blocks
def _expected_move(ctx: dict, em: dict) -> dict:
    use = SF.usable(ctx["sf"])
    rows = em.get("rows") or [] if _avail(em) else []
    every = [[int(r.dte), _num(r.straddle_pct), _num(r.move_1sd_pct), r.expiration] for r in use.itertuples()
             if r.dte >= 1 and _num(r.move_1sd_pct) is not None]
    return {"available": _avail(em), "reason": em.get("reason"), "rows": rows, "all": every, "em_cm": em.get("em_cm")}


def _cone(ctx: dict, em: dict) -> dict:
    """Last CHART_CONE_BARS closes and the 1-sd range of every expiry up to the farthest key expiry."""
    S = float(ctx["session"]["spot"])
    bars = ctx.get("bars")
    closes = []
    if bars is not None and len(bars):
        b = bars[bars.index <= pd.Timestamp(ctx["as_of"])].iloc[-C.CHART_CONE_BARS:]
        closes = [[d.strftime("%Y-%m-%d"), float(v)] for d, v in b.items()]
    rows = em.get("rows") or [] if _avail(em) else []
    key = {r["expiration"]: r["tags"] for r in rows}
    horizon = max([r["dte"] for r in rows if {"monthly", "30d", "next_week"} & set(r["tags"])] or [45])
    use = SF.usable(ctx["sf"])
    bands = []
    for r in use.itertuples():
        m = _num(r.move_1sd_pct)
        if r.dte < 1 or m is None or r.dte > horizon:
            continue
        bands.append({"expiration": r.expiration, "dte": int(r.dte), "low": S * (1 - m), "high": S * (1 + m),
                      "move_1sd_pct": m, "tags": key.get(r.expiration, [])})
    return {"available": bool(bands), "reason": None if bands else (em.get("reason") or "no expected move"),
            "closes": closes, "closes_reason": None if closes else (ctx.get("bars_reason") or "no daily bars"),
            "proxy": ctx.get("bars_proxy"), "spot": S, "as_of": ctx["as_of"], "bands": bands}


def _smile_expiries(use: pd.DataFrame, e2) -> list:
    """Expiries with dte >= 2 nearest CHART_SMILE_TENORS (ties: the earlier one), plus E2, without repeats."""
    q = use[(use["dte"] >= 2) & use["forward"].notna()].sort_values("expiration")
    out, labels = [], {}
    for d in C.CHART_SMILE_TENORS:
        if q.empty:
            break
        i = (q["dte"] - d).abs().to_numpy().argmin()
        e = q["expiration"].iloc[i]
        labels.setdefault(e, []).append(f"{d}d")
        if e not in out:
            out.append(e)
    if e2 is not None and e2["expiration"] in set(q["expiration"]):
        labels.setdefault(e2["expiration"], []).append("event")
        if e2["expiration"] not in out:
            out.append(e2["expiration"])
    return [(e, labels[e]) for e in sorted(out)]


def _smile(ctx: dict) -> dict:
    use, x = SF.usable(ctx["sf"]), ctx["x"]
    by = use.set_index("expiration")
    out = []
    for e, labels in _smile_expiries(use, (ctx.get("ev") or {}).get("e2")):
        F = float(by.loc[e, "forward"])
        q = x[(x["expiration"] == e) & x["surface"] & x["valid_iv"]]
        q = q[(q["strike"] / F - 1).abs() <= C.CHART_SMILE_RANGE]
        piv = q.pivot_table(index="strike", columns="type", values="iv", aggfunc="first")
        pts = [[float(k) / F, _num(r.get("C")), _num(r.get("P"))] for k, r in piv.iterrows()]
        out.append({"expiration": e, "dte": int(by.loc[e, "dte"]), "forward": F, "labels": labels, "points": pts,
                    "atm_iv": _num(by.loc[e, "atm_iv"])})
    return {"available": bool(out), "expiries": out}


def _oi(ctx: dict, pos: dict, em: dict) -> dict:
    """Open interest and volume by strike within CHART_OI_RANGE of spot, per window (next expiry, <= 30 dte,
    <= OI_WINDOW_DTE, all)."""
    x, S = ctx["x"], float(ctx["session"]["spot"])
    x = x[(x["strike"] >= S * (1 - C.CHART_OI_RANGE)) & (x["strike"] <= S * (1 + C.CHART_OI_RANGE))]
    nxt = _tag_row(em.get("rows"), "next")
    windows = {"next": x[x["expiration"] == nxt["expiration"]] if nxt else x.iloc[0:0],
               "d30": x[x["dte"] <= C.PCR_SHORT_DTE], "d45": x[x["dte"] <= C.OI_WINDOW_DTE], "all": x}
    out = {"available": _avail(pos) and len(x) > 0, "reason": pos.get("reason"), "spot": S,
           "next_expiration": nxt["expiration"] if nxt else None, "windows": {},
           "window_dte": {"d30": C.PCR_SHORT_DTE, "d45": C.OI_WINDOW_DTE},
           "key_levels": pos.get("key_levels"), "max_pain": pos.get("max_pain"), "far_otm": pos.get("far_otm")}
    for name in OI_WINDOWS:
        w = windows[name]
        if w.empty:
            out["windows"][name] = []
            continue
        agg = w.groupby(["strike", "type"])[["open_interest", "volume"]].sum().unstack("type", fill_value=0)
        rows = []
        for k, r in agg.iterrows():
            co, po = int(r.get(("open_interest", "C"), 0)), int(r.get(("open_interest", "P"), 0))
            cv, pv = int(r.get(("volume", "C"), 0)), int(r.get(("volume", "P"), 0))
            if co or po or cv or pv:
                rows.append([float(k), co, po, cv, pv])
        out["windows"][name] = rows
    return out


def _chain(ctx: dict, em: dict) -> dict:
    """T-quotes (surface root) of the key expiries for strikes within CHART_CHAIN_RANGE of spot."""
    x, S = ctx["x"], float(ctx["session"]["spot"])
    rows = em.get("rows") or [] if _avail(em) else []
    out = []
    for r in sorted(rows, key=lambda r: r["expiration"]):
        e = r["expiration"]
        q = x[(x["expiration"] == e) & x["surface"]
              & (x["strike"] >= S * (1 - C.CHART_CHAIN_RANGE)) & (x["strike"] <= S * (1 + C.CHART_CHAIN_RANGE))]
        if q.empty:
            continue
        side = {}
        for t in ("C", "P"):
            s = q[q["type"] == t].set_index("strike")
            side[t] = s[~s.index.duplicated()]
        strikes = sorted(set(side["C"].index) | set(side["P"].index))

        def cells(t, k):
            s = side[t]
            if k not in s.index:
                return [None] * 6
            c = s.loc[k]
            iv = _num(c["iv"]) if bool(c["valid_iv"]) else None
            return [_num(c["bid"]), _num(c["ask"]), iv, _num(c["delta"]), _int(c["open_interest"]), _int(c["volume"])]

        out.append({"expiration": e, "dte": r["dte"], "tags": r["tags"], "root": r["root"],
                    "settlement": r["settlement"], "forward": r["forward"], "atm_strike": r["atm_strike"],
                    "rows": [[float(k)] + cells("C", k) + cells("P", k) for k in strikes]})
    return {"available": bool(out), "spot": S, "range_pct": C.CHART_CHAIN_RANGE, "expiries": out}


def _strategies(ctx: dict, st: dict) -> dict:
    cand = ctx.get("strategy_candidates") or {}
    out = {"available": _avail(st), "reason": st.get("reason"), "notes": st.get("notes") or [],
           "note_ids": st.get("note_ids") or [], "context": st.get("context"), "kind": ctx["kind"], "screens": {}}
    if not out["available"]:
        return out
    for name in C.STRATEGIES:
        b = st.get(name) or {}
        out["screens"][name] = {"available": _avail(b), "reason": b.get("reason"), "candidates": b.get("candidates"),
                                "target_delta": b.get("target_delta"), "delta_band": b.get("delta_band"),
                                "dte_window": b.get("dte_window"), "rows": b.get("rows") or [],
                                "all": (cand.get(name) or [])[:C.STRAT_CHART_ROWS], "notes": b.get("notes") or [],
                                "note_ids": b.get("note_ids") or []}
    out["earnings_straddle"] = st.get("earnings_straddle")
    return out


def _earnings(ea: dict, kind: str) -> dict:
    if kind != "stock":
        return {"available": False, "reason": ea.get("reason") or "ETF/index: no earnings", "applies": False}
    ev, re_, cmp_ = ea.get("event_move") or {}, ea.get("reactions") or {}, ea.get("comparison") or {}
    return {"available": _avail(ea), "applies": True, "reason": ea.get("reason"), "next_date": ea.get("next_date"),
            "event_today": ea.get("event_today"), "trading_days_to_event": ea.get("trading_days_to_event"),
            "timing": ea.get("timing"), "date_check": ea.get("date_check"),
            "event_move": {k: ev.get(k) for k in ("available", "reason", "method", "event_date", "e1", "e2", "e3",
                                                   "move_1sd", "expected_abs_move", "implied_window_1sd",
                                                   "implied_window_abs", "event_variance_share", "straddle_e2_pct")},
            "reactions": {"available": _avail(re_), "reason": re_.get("reason"), "method": re_.get("method"),
                          "n": re_.get("n"), "mean_abs": re_.get("mean_abs"), "median_abs": re_.get("median_abs"),
                          "max_abs": re_.get("max_abs"), "small_sample": re_.get("small_sample"),
                          "items": [[i["date"], i["move"]] for i in re_.get("items") or []]},
            "comparison": cmp_, "notes": ea.get("notes") or [], "note_ids": ea.get("note_ids") or []}


def _news(nw: dict) -> dict:
    """The headlines of modules.build_news (third-party text, already cleaned and cut); a link is kept only when
    modules.safe_url accepts it (ui/chart.js checks the scheme again)."""
    out = {k: nw.get(k) for k in NEWS_KEYS}
    out["rows"] = [{**{k: r.get(k) for k in NEWS_ROW_KEYS}, "url": M.safe_url(r.get("url"))}
                   for r in (nw.get("rows") or [])[:C.NEWS_ROWS] if isinstance(r, dict)]
    out["notes"], out["note_ids"] = nw.get("notes") or [], nw.get("note_ids") or []
    return out


def _rates(r) -> dict | None:
    """snapshot.rates: the discount rate of European-style options (Treasury curve or the fixed rate)."""
    if not isinstance(r, dict):
        return None
    return {**{k: r.get(k) for k in RATES_KEYS}, "notes": r.get("notes") or [], "note_ids": r.get("note_ids") or []}


def _calendar(cc) -> dict | None:
    """data_quality.calendar_check; the holiday names come from Massive, so they are cleaned like news text."""
    if not isinstance(cc, dict):
        return None
    return {**{k: cc.get(k) for k in CALENDAR_KEYS},
            "mismatches": [{**{k: m.get(k) for k in MISMATCH_KEYS}, "name": M.clean_text(m.get("name"))}
                           for m in cc.get("mismatches") or [] if isinstance(m, dict)]}


def _connection(metrics: dict, source: str = "fmp") -> dict | None:
    return _get(metrics, "meta", "connection", source)


def _data_notes(metrics: dict, section_keys) -> dict:
    meta, dq = metrics.get("meta") or {}, metrics.get("data_quality") or {}
    skipped = [{"module": str(k), "reason": str(v)} for k, v in (meta.get("skipped") or {}).items()]
    seen = {s["module"] for s in skipped}
    for name, reason in (meta.get("modules_unavailable") or {}).items():
        if name not in seen:
            skipped.append({"module": name, "reason": str(reason)})
    notes, note_ids = [], []

    def add(block, prefix=""):
        for i, n in enumerate((block or {}).get("notes") or []):
            ids = (block or {}).get("note_ids") or []
            nid = ids[i] if i < len(ids) else None
            if prefix + n not in notes:
                notes.append(prefix + n)
                note_ids.append(nid)

    add(metrics.get("snapshot"))
    for k in section_keys:
        b = metrics.get(k)
        if isinstance(b, dict):
            add(b)
    for n in meta.get("notes") or []:
        if n not in notes:
            notes.append(n)
            note_ids.append(None)
    return {"skipped": skipped, "notes": notes, "note_ids": note_ids, "warnings": dq.get("warnings") or [],
            "warning_ids": dq.get("warning_ids") or [], "restricted": meta.get("restricted") or [],
            "errors": meta.get("errors") or [], "stale": dq.get("stale"), "oi_note": dq.get("oi_note"),
            "delay_note": dq.get("delay_note"), "rates": _rates(_get(metrics, "snapshot", "rates")),
            "calendar_check": _calendar(dq.get("calendar_check"))}


def _ticker_kpis(metrics: dict) -> list:
    snap, em, ea = metrics["snapshot"], metrics["expected_move"], metrics["earnings"]
    vol, sk, pos, gm = metrics["volatility"], metrics["skew"], metrics["positioning"], metrics["gamma"]
    rows = em.get("rows") or [] if _avail(em) else []
    out = [_kpi("price", snap["spot"], "price", change_pct=snap.get("change_pct"), prev_close=snap.get("prev_close"),
                basis=snap.get("spot_basis"))]
    for kid, tag in (("em_next_week", "next_week"), ("em_monthly", "monthly")):
        r = _tag_row(rows, tag)
        out.append(_kpi(kid, r["move_1sd_pct"] if r else None, "pct",
                        **({"expiration": r["expiration"], "dte": r["dte"], "straddle_pct": r["straddle_pct"],
                            "range_1sd": r["range_1sd"], "straddle": r["straddle"]} if r else {})))
    ev, cmp_, re_ = ea.get("event_move") or {}, ea.get("comparison") or {}, ea.get("reactions") or {}
    out.append(_kpi("earnings", ea.get("next_date") if _avail(ea) else None, "date",
                    applies=snap["kind"] == "stock", reason=ea.get("reason"),
                    trading_days=ea.get("trading_days_to_event"), event_today=ea.get("event_today"),
                    implied_window=ev.get("implied_window_1sd"), move_1sd=ev.get("move_1sd"),
                    hist_mean_abs=re_.get("mean_abs") if _avail(re_) else None,
                    ratio=cmp_.get("ratio_implied_to_mean")))
    ivx, rank = vol.get("iv_index") or {}, vol.get("iv_rank") or {}
    out.append(_kpi("iv30", vol.get("cm_iv_30d"), "pct", iv30_cboe=vol.get("iv30_cboe"),
                    iv_index={"symbol": ivx.get("symbol"), "pctl_1y": ivx.get("pctl_1y")} if _avail(ivx) else None,
                    iv_rank={"value": rank.get("value"), "pctl": rank.get("pctl"), "n_obs": rank.get("n_obs"),
                             "window_label": rank.get("window_label")} if _avail(rank) else None,
                    building={"n": rank.get("n_obs"), "need": rank.get("need")}
                    if rank.get("reason_id") == "ivr_building" else None))
    rv = vol.get("realized") or {}
    out.append(_kpi("iv_hv", vol.get("vrp"), "vol_pts", hv20=rv.get("hv20") if _avail(rv) else None,
                    cm_iv_30d=vol.get("cm_iv_30d"), ratio=vol.get("iv_hv_ratio"),
                    reason=None if _avail(rv) else rv.get("reason")))
    out.append(_kpi("rr25", sk.get("rr25_30d"), "vol_pts", label=sk.get("label"), bf25=sk.get("bf25_30d"),
                    pctl=sk.get("rr25_pctl")))
    pcr = _get(pos, "pcr", "all") or {}
    out.append(_kpi("pcr", pcr.get("oi"), "ratio", volume=pcr.get("volume")))
    if snap["kind"] in ("index", "etf"):
        out.append(_kpi("gex", gm.get("net_gex_1pct"), "usd", zero_gamma=gm.get("zero_gamma"),
                        regime=gm.get("regime"), zero_gamma_dist_pct=gm.get("zero_gamma_dist_pct")))
    return out


def _meta(metrics: dict, mode: str, **extra) -> dict:
    dq = metrics.get("data_quality") or {}
    fmp, massive = _connection(metrics), _connection(metrics, "massive")
    return {"mode": mode, "as_of": metrics["as_of"], "quote_time": metrics["quote_time"],
            "data_source": metrics["data_source"], "disclaimer": C.DISCLAIMER, "connection": fmp,
            "fmp_configured": fmp is not None, "massive_connection": massive, "massive_configured": massive is not None,
            "stale": dq.get("stale"), "delay_note": dq.get("delay_note"), **extra}


# ---------------------------------------------------------------------------- public
def build_chart_data(ctx: dict, metrics: dict) -> dict:
    """JSON-safe chart payload of one ticker analysis (see the module docstring)."""
    snap, s = metrics["snapshot"], ctx["session"]
    em = metrics["expected_move"]
    data = {
        "meta": _meta(metrics, "ticker", ticker=metrics["ticker"], name=snap.get("name"), kind=snap["kind"],
                      kind_source=snap.get("kind_source"), cboe_symbol=snap.get("cboe_symbol"),
                      fmp_symbol=snap.get("fmp_symbol"), spot=snap["spot"], spot_basis=snap["spot_basis"],
                      closed=s["closed"], overnight_quotes=s["overnight_quotes"],
                      feed_time=_get(snap, "session", "feed_time"), chain=snap.get("chain"),
                      iv30_change=snap.get("iv30_change"), iv30_change_source=snap.get("iv30_change_source")),
        "kpis": _ticker_kpis(metrics),
        "cone": _cone(ctx, em),
        "term": _term(ctx, metrics["term_structure"]),
        "em": _expected_move(ctx, em),
        "smile": _smile(ctx),
        "oi": _oi(ctx, metrics["positioning"], em),
        "gex": _gex(ctx, metrics["gamma"]),
        "strategies": _strategies(ctx, metrics["strategies"]),
        "earnings": _earnings(metrics["earnings"], snap["kind"]),
        "chain": _chain(ctx, em),
        "flow": {"available": _avail(metrics["flow"]), "reason": metrics["flow"].get("reason"),
                 "rules": metrics["flow"].get("rules"), "direction_note": metrics["flow"].get("direction_note"),
                 "rows": (metrics["flow"].get("rows") or [])[:C.CHART_FLOW_ROWS]},
        "news": _news(metrics.get("news") or {}),
        "skew": {k: metrics["skew"].get(k) for k in ("available", "reason", "rr25_30d", "bf25_30d", "label",
                                                     "rr25_pctl", "thresholds", "by_expiry")},
        "volatility": {k: metrics["volatility"].get(k) for k in ("available", "reason", "iv30_cboe", "cm_iv_30d",
                                                                 "cm_iv_30d_ex_event", "event_in_window",
                                                                 "iv_index", "realized", "vrp", "iv_hv_ratio",
                                                                 "iv_rank", "iv_rank_ex_event")},
        "positioning": {k: metrics["positioning"].get(k) for k in ("available", "reason", "pcr", "key_levels",
                                                                   "far_otm", "max_pain", "oi_change", "short")},
        "market_context": metrics["market_context"],
        "history": {k: metrics["history"].get(k) for k in ("available", "reason", "days", "first_date",
                                                           "last_date", "saved_this_run", "data_dir")},
        "data_notes": _data_notes(metrics, ("market_context", "expected_move", "term_structure", "skew", "volatility",
                                            "earnings", "positioning", "gamma", "flow", "news", "strategies",
                                            "history")),
    }
    return json_safe(data)


def build_market_chart_data(members: dict, metrics: dict, rows: dict) -> dict:
    """JSON-safe chart payload of the market view: members = {key: ctx or {available: False, ...}}, rows = the CBOE
    index histories {index: (rows or None, reason)}."""
    spx = members["SPX"]
    vix, ts, th = metrics["vix"], metrics["term_structure"], metrics["thermometer"]
    spx_mods = spx["mods"]
    gm = spx_mods.get("gamma") or {}
    em_rows = _get(metrics, "expected_moves", "rows") or []
    spx_next = next((r for r in em_rows if r["key"] == "SPX" and "next_week" in r["tags"]), None)
    pcr = next((r for r in _get(metrics, "positioning", "rows") or [] if r["key"] == "SPX"), {})
    sk = next((r for r in _get(metrics, "skew_board", "rows") or [] if r["key"] == "SPX"), {})
    kpis = [
        _kpi("vix", vix.get("level"), "points", band=vix.get("band"), pctl_1y=vix.get("pctl_1y"),
             change_pct=vix.get("change_pct"), prev_close=vix.get("prev_close")),
        _kpi("vix_vix3m", vix.get("vix_vix3m"), "ratio", pctl_1y=vix.get("vix_vix3m_pctl_1y"),
             vix9d_vix=vix.get("vix9d_vix")),
        _kpi("spx_iv30", (ts.get("cm_iv") or {}).get("30d") if _avail(ts) else None, "pct",
             hv20=_get(metrics, "realized", "spx_hv20")),
        _kpi("ratio_30_90", ts.get("ratio_30_90"), "ratio", shape=ts.get("shape"), pctl=ts.get("ratio_30_90_pctl")),
        _kpi("rr25", sk.get("rr25_30d"), "vol_pts", label=sk.get("label"), pctl=sk.get("rr25_pctl")),
        _kpi("gex", gm.get("net_gex_1pct"), "usd", zero_gamma=gm.get("zero_gamma"), regime=gm.get("regime"),
             zero_gamma_dist_pct=gm.get("zero_gamma_dist_pct")),
        _kpi("em_next_week", spx_next["move_1sd_pct"] if spx_next else None, "pct",
             **({"expiration": spx_next["expiration"], "dte": spx_next["dte"],
                 "straddle_pct": spx_next["straddle_pct"], "range_1sd": spx_next["range_1sd"]} if spx_next else {})),
        _kpi("pcr", pcr.get("pcr_all_oi"), "ratio", volume=pcr.get("pcr_all_volume")),
    ]
    as_of = metrics["as_of"]

    def series(ix):
        r, _why = rows.get(ix) or (None, None)
        return {x["date"]: x["close"] for x in r or [] if x["date"] <= as_of}

    v, v3 = series("VIX"), series("VIX3M")
    dates = sorted(v)[-C.VIX_HISTORY_DAYS:]
    vix_hist = [[d, v[d], v3.get(d)] for d in dates]
    spark = {}
    root = _get(metrics, "history", "data_dir")
    if root:
        import history as H
        df = H.read_summary(Path(root), "SPX")
        if df is not None and len(df) >= 2:
            df = df[[d <= as_of for d in df["trade_date"]]].tail(C.IVR_LOOKBACK)
            for col, name in SPARK_COLUMNS:
                pts = [[d, float(x)] for d, x in zip(df["trade_date"], df[col]) if _num(x) is not None]
                if len(pts) >= 2:
                    spark[name] = pts
    spx_ctx = dict(spx, earnings_date=None)
    data = {
        "meta": _meta(metrics, "market", spot=spx["session"]["spot"], spot_basis=spx["session"]["spot_basis"],
                      overnight_quotes=spx["session"]["overnight_quotes"], closed=spx["session"]["closed"],
                      universe=_get(metrics, "snapshot", "universe")),
        "kpis": kpis,
        "thermometer": th,
        "vix": {k: vix.get(k) for k in ("available", "reason", "level", "band", "prev_close", "change_pct", "pctl_1y",
                                        "vix_vix3m", "vix_vix3m_pctl_1y", "vix9d_vix", "vix9d_vix_pctl_1y")},
        "term": _term(spx_ctx, ts),
        "gex": _gex(spx_ctx, gm),
        "skew_board": [[r["key"], r.get("rr25_30d"), r.get("bf25_30d"), r.get("label"), r.get("rr25_pctl")]
                       for r in _get(metrics, "skew_board", "rows") or [] if r.get("available")],
        "skew_thresholds": dict(zip(("steep_put", "put", "flat"), C.SKEW_LABELS["index_etf"])),
        "gamma_board": _get(metrics, "gamma_board", "rows") or [],
        "em": em_rows,
        "pcr": [[r["key"], r.get("pcr_all_volume"), r.get("pcr_all_oi")]
                for r in _get(metrics, "positioning", "rows") or [] if r.get("available")],
        "realized": metrics.get("realized"),
        "vix_history": vix_hist,
        "spark": spark,
        "history": metrics.get("history"),
        "data_notes": _data_notes(metrics, ("vix", "term_structure", "thermometer", "realized", "history")),
    }
    return json_safe(data)


# ---------------------------------------------------------------------------- chart.html
def _inline(text: str, tag: str) -> str:
    """Text safe inside <tag>...</tag>: an embedded closing tag would end the element early."""
    return text.replace(f"</{tag}", f"<\\/{tag}").replace(f"</{tag.upper()}", f"<\\/{tag.upper()}")


def embed_json(obj) -> str:
    """JSON for a <script type="application/json"> block: <, > and & become \\u escapes, so the text can never
    close the element or open a comment, and JSON.parse returns the same data."""
    text = json.dumps(json_safe(obj), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def page_title(chart_data: dict) -> str:
    meta = chart_data.get("meta") or {}
    if meta.get("mode") == "market":
        return f"US options market thermometer · {meta.get('as_of') or ''}"
    return f"{meta.get('ticker') or ''} · Options analysis · {meta.get('as_of') or ''}"


def write_chart_html(chart_data: dict, path) -> Path:
    """Write the self-contained chart page (UTF-8) and return its path."""
    css = (UI_DIR / "chart.css").read_text(encoding="utf-8")
    scripts = [(UI_DIR / name).read_text(encoding="utf-8") for name in ("i18n.js", "chart.js")]
    title = html.escape(page_title(chart_data))
    page = "\n".join([
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{title}</title>",
        '<link rel="icon" href="data:,">',
        f"<style>\n{_inline(css, 'style')}\n</style>",
        "</head>",
        "<body>",
        '<div class="oa-wrap">',
        '  <div class="oa-top"><span class="oa-app" data-i18n="app_title"></span>'
        '<button type="button" class="oa-lang" id="oa-lang" hidden></button></div>',
        '  <div id="oa-root"></div>',
        "</div>",
        f'<script type="application/json" id="oa-data">{embed_json(chart_data)}</script>',
        *(f"<script>\n{_inline(js, 'script')}\n</script>" for js in scripts),
        "<script>OAChart.boot(document.getElementById(\"oa-root\"), "
        "JSON.parse(document.getElementById(\"oa-data\").textContent));</script>",
        "</body>",
        "</html>",
        "",
    ])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path
