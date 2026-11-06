"""
The analysis modules of the ticker mode (plan 5.2-5.9). Each build_<module>(ctx) returns a dict with
`available` and `notes` (plus `note_ids`, `reason` when unavailable) and reads only ctx and ctx["mods"]
(the modules computed before it, in analyze.MODULE_ORDER).

ctx keys used here: key, kind, as_of, session (spot, closed, ...), x (live standard-root contracts),
sf (surface.build), earnings_date, earnings_known, ev (surface.event_expiries), earnings_rows,
earnings_reason, bars (pd.Series of daily closes or None), bars_source, bars_reason, bars_proxy,
iv_index (CBOE index rows or None), iv_index_symbol, iv_index_reason, history (history.HistoryView or None).
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

import config as C
import chain as CH
import surface as SF


# ---------------------------------------------------------------------------- blocks and fixed sentences
def block(available: bool = True, **fields) -> dict:
    return {"available": available, **fields, "notes": [], "note_ids": []}


def unavailable(reason: str, reason_id: str | None = None, **fields) -> dict:
    b = {"available": False, "reason": reason, **fields, "notes": [], "note_ids": []}
    if reason_id:
        b["reason_id"] = reason_id
    return b


def add_note(b: dict, text: str | None = None, nid: str | None = None, args=()) -> None:
    """Append a note; with `nid` the English text is config.NOTES[nid] filled with args (an i18n id for the UI)."""
    if nid:
        text = C.NOTES[nid].format(*args)
    b.setdefault("notes", []).append(text)
    b.setdefault("note_ids", []).append({"id": nid, "args": [_arg(a) for a in args]} if nid else None)


def add_warning(dq: dict, wid: str | None = None, args=(), text: str | None = None) -> None:
    if wid:
        text = C.WARNINGS[wid].format(*args)
    dq.setdefault("warnings", []).append(text)
    dq.setdefault("warning_ids", []).append({"id": wid, "args": [_arg(a) for a in args]} if wid else None)


def _arg(a):
    if isinstance(a, date):
        return a.isoformat()
    if isinstance(a, (np.floating, np.integer)):
        return a.item()
    return a


def pctl(window, x) -> float | None:
    """100 x share of the window strictly below x."""
    w = np.asarray(window, dtype=float)
    if not len(w) or x is None or not np.isfinite(x):
        return None
    return float((w < x).mean() * 100.0)


def _f(v):
    """float or None (NaN -> None)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------------------- earnings date (before the modules)
def next_earnings(rows, as_of: date, closed: bool) -> date | None:
    """The next earnings date (plan 5.6): after the close, the earliest date >= as_of (today's date counts even
    with epsActual, the chain stopped at the close); intraday, date > as_of or date == as_of without epsActual."""
    out = []
    for r in rows or []:
        try:
            d = date.fromisoformat(str(r.get("date"))[:10])
        except ValueError:
            continue
        if d > as_of or (d == as_of and (closed or r.get("epsActual") is None)):
            out.append(d)
    return min(out) if out else None


# ---------------------------------------------------------------------------- 5.3 term structure
def build_term_structure(ctx: dict) -> dict:
    sf = ctx["sf"]
    use = SF.usable(sf)
    dropped = [str(e) for e in SF.dropped(sf)]
    if use.empty or use["atm_iv"].notna().sum() == 0:
        return unavailable("no expiry with an ATM IV (no strikes bracketing the forward)", dropped_expiries=dropped)
    cm, ex = {}, {}
    for d in C.CM_TENORS:
        cm[f"{d}d"], ex[f"{d}d"] = SF.interp_in_t(use, "atm_iv", d, C.CM_MIN_DTE, True)
    r = cm["30d"] / cm["90d"] if cm["30d"] and cm["90d"] else None
    shape = None if r is None else "inverted" if r > C.TERM_INVERTED else "upward" if r < C.TERM_UPWARD else "flat"
    r7 = cm["7d"] / cm["30d"] if cm["7d"] and cm["30d"] and not ex["7d"] else None
    event, known = ctx.get("earnings_date"), (ctx.get("earnings_known") or ctx["kind"] != "stock")
    q = SF.kink_expiries(use)
    w, t = (q["atm_iv"] ** 2 * q["T"]).to_numpy(dtype=float), q["T"].to_numpy(dtype=float)
    fwd = []
    for i in range(len(q) - 1):
        a, b = q["expiration"].iloc[i], q["expiration"].iloc[i + 1]
        fv = (w[i + 1] - w[i]) / (t[i + 1] - t[i])
        ce = (bool(a <= event <= b) if event else False) if known else None
        fwd.append({"from": a, "to": b, "fwd_vol": math.sqrt(fv) if fv > 0 else None,
                    "negative_variance": bool(fv <= 0), "contains_earnings": ce})
    vols = [f["fwd_vol"] for f in fwd if f["fwd_vol"] is not None]
    med = float(np.median(vols)) if vols else None
    kinks = [f for f in fwd if med and f["fwd_vol"] is not None and f["fwd_vol"] >= C.KINK_RATIO * med
             and f["fwd_vol"] - med >= C.KINK_MIN_EXCESS]
    out = block(cm_iv=cm, cm_extrapolated=ex, ratio_30_90=r, ratio_7_30=r7, shape=shape, median_fwd_vol=med,
                forward_vols=fwd, kinks=kinks, dropped_expiries=dropped)
    for k, flag in ex.items():
        if flag and cm[k] is not None:
            add_note(out, f"{k} extrapolated flat: no expiry close to {k[:-1]} days")
    if any(f["negative_variance"] for f in fwd):
        add_note(out, "negative forward variance between some expiries (IV falls faster than time grows)")
    if kinks and known and not any(k["contains_earnings"] for k in kinks):
        add_note(out, "kinks without earnings may be macro events or data noise")
    if ctx["kind"] == "stock" and not ctx.get("earnings_known"):
        add_note(out, "earnings date unknown: cannot tell whether a kink is earnings")
    return out


# ---------------------------------------------------------------------------- 5.2 expected move
def build_expected_move(ctx: dict) -> dict:
    sf, s = ctx["sf"], ctx["session"]
    use = SF.usable(sf)
    e2 = ctx.get("ev", {}).get("e2")
    tags = SF.key_expiries(use, ctx["as_of"], e2["expiration"] if e2 is not None else None)
    ctx["key_tags"] = tags
    if not tags:
        return unavailable("no expiry with an ATM straddle (dte >= 1)", rows=[], em_cm={"7d": None, "30d": None})
    S = float(s["spot"])
    by = use.set_index("expiration")
    rows = []
    for e, tg in tags.items():
        r = by.loc[e]
        m = _f(r["move_1sd_pct"])
        rows.append({"expiration": e, "tags": list(tg), "root": r["root"], "settlement": r["settlement"],
                     "dte": int(r["dte"]), "T": _f(r["T"]), "forward": _f(r["forward"]),
                     "atm_strike": _f(r["atm_strike"]), "atm_iv": _f(r["atm_iv"]),
                     "atm_iv_source": r["atm_iv_source"], "atm_iv_cboe": _f(r["atm_iv_cboe"]),
                     "straddle": _f(r["straddle"]), "straddle_pct": _f(r["straddle_pct"]), "move_1sd_pct": m,
                     "range_1sd": [S * (1 - m), S * (1 + m)] if m is not None else None})
    cm = ((ctx["mods"].get("term_structure") or {}).get("cm_iv")) or {}
    em_cm = {k: (cm[k] * math.sqrt(int(k[:-1]) / 365.0) if cm.get(k) else None) for k in ("7d", "30d")}
    out = block(rows=rows, em_cm=em_cm)
    if not cm:
        add_note(out, "em_cm unavailable: term structure unavailable")
    add_note(out, "straddle_pct ~ 0.8 x the 1-sd move; probabilities are risk-neutral (from option prices)")
    return out


# ---------------------------------------------------------------------------- 5.4 skew
def build_skew(ctx: dict) -> dict:
    sf = SF.usable(ctx["sf"])
    if sf.empty:
        return unavailable("no expiry with an ATM IV")
    rr, _ = SF.interp_in_t(sf, "rr25", 30, C.SKEW_MIN_DTE, False)
    bf, _ = SF.interp_in_t(sf, "bf25", 30, C.SKEW_MIN_DTE, False)
    steep, put, flat = C.SKEW_LABELS["stock" if ctx["kind"] == "stock" else "index_etf"]
    label = None if rr is None else "steep_put" if rr >= steep else "put" if rr >= put else "flat" if rr > flat else "call"
    by = [{"expiration": r.expiration, "dte": int(r.dte), "put25_iv": _f(r.put25_iv), "call25_iv": _f(r.call25_iv),
           "atm_iv_cboe": _f(r.atm_iv_cboe), "rr25": _f(r.rr25), "bf25": _f(r.bf25)}
          for r in sf.itertuples() if r.dte >= 1]
    out = block(rr25_30d=rr, bf25_30d=bf, label=label, rr25_pctl=None, thresholds=dict(
        zip(("steep_put", "put", "flat"), (steep, put, flat))), by_expiry=by)
    h = ctx.get("history")
    if h is not None and rr is not None:
        out["rr25_pctl"] = h.pctl("rr25_30d", rr)
    if rr is None:
        add_note(out, "no 25-delta wings around 30 days")
    add_note(out, "rr25 = put25 IV - call25 IV: positive means puts are priced at a higher IV "
                  "(the opposite sign of the usual call-minus-put risk reversal)")
    return out


# ---------------------------------------------------------------------------- 5.6 earnings
def event_move(ctx: dict) -> dict:
    """The earnings move implied by the surface around ctx["earnings_date"] (5.6)."""
    d, ev = ctx.get("earnings_date"), ctx.get("ev") or {}
    e1, e2, e3 = ev.get("e1"), ev.get("e2"), ev.get("e3")
    if d is None:
        return unavailable("no earnings date")
    if e2 is None:
        return unavailable("no expiry after the event")
    out = block(event_date=d, method=None, e1=None, e2=e2["expiration"], e3=None, sigma1=None,
                sigma2=float(e2["atm_iv"]), sigma_base=None, T2=float(e2["T"]), move_1sd=None, expected_abs_move=None,
                implied_window_1sd=None, implied_window_abs=None, event_variance_share=None,
                straddle_e2_pct=_f(e2["straddle_pct"]))
    s2, t2 = float(e2["atm_iv"]), float(e2["T"])
    if e1 is not None:
        base2, out["method"] = float(e1["atm_iv"]) ** 2, "pre_post"
        out.update(e1=e1["expiration"], sigma1=float(e1["atm_iv"]))
    elif e3 is not None:
        s3, t3 = float(e3["atm_iv"]), float(e3["T"])
        base2 = (s3 ** 2 * t3 - s2 ** 2 * t2) / (t3 - t2)
        out.update(method="post_event_pair", e3=e3["expiration"])
        add_note(out, "assumes the same base vol before and after the event; overstates the event when the term "
                      "structure is inverted")
    else:
        add_note(out, "no expiry pair to isolate the event")
        return out
    if base2 <= 0:
        add_note(out, "negative forward variance between E2 and E3: event not isolated")
        return out
    out["sigma_base"] = math.sqrt(base2)
    m2 = t2 * (s2 ** 2 - base2)
    if m2 <= 0:
        add_note(out, nid="no_event_premium")
        return out
    m = math.sqrt(m2)
    iw = math.sqrt(m2 + C.REACTION_WINDOW_DAYS * base2 / C.HV_ANNUAL)
    out.update(move_1sd=m, expected_abs_move=m * math.sqrt(2 / math.pi), implied_window_1sd=iw,
               implied_window_abs=iw * math.sqrt(2 / math.pi), event_variance_share=m2 / (s2 ** 2 * t2))
    return out


def reactions(rows, bars: pd.Series, as_of: date) -> dict:
    """Two-day close-to-close moves around the last EARNINGS_REACTIONS_MAX earnings dates before as_of."""
    past = set()
    for r in rows or []:
        try:
            d = date.fromisoformat(str(r.get("date"))[:10])
        except ValueError:
            continue
        if d < as_of:
            past.add(d)
    idx, items = bars.index, []
    for d in sorted(past):
        ts = pd.Timestamp(d)
        pre, post = idx[idx < ts], idx[idx > ts]
        if len(pre) and len(post) and post[0] <= pd.Timestamp(as_of):
            items.append({"date": d, "move": float(bars[post[0]] / bars[pre[-1]] - 1.0)})
    items = items[-C.EARNINGS_REACTIONS_MAX:]
    a = np.abs([i["move"] for i in items])
    return block(method="two_day_close_to_close", n=len(items),
                 mean_abs=float(a.mean()) if len(a) else None, median_abs=float(np.median(a)) if len(a) else None,
                 max_abs=float(a.max()) if len(a) else None, small_sample=len(items) < C.EARNINGS_REACTIONS_MIN,
                 items=items)


def build_earnings(ctx: dict) -> dict:
    if ctx["kind"] != "stock":
        return unavailable("ETF/index: no earnings")
    d = ctx.get("earnings_date")
    if d is None:
        out = unavailable(ctx.get("earnings_reason") or "no upcoming earnings date from FMP")
        return out
    as_of = ctx["as_of"]
    out = block(next_date=d, event_today=d == as_of,
                trading_days_to_event=CH.busdays(as_of + timedelta(days=1), d + timedelta(days=1)),
                timing="unknown", date_check=None)
    if d == as_of:
        add_note(out, nid="earnings_today")
    add_note(out, "FMP has no before/after-the-open flag and no confirmation flag for earnings dates")
    ev = event_move(ctx)
    out["event_move"] = ev
    bars = ctx.get("bars")
    if bars is not None and ctx.get("earnings_rows"):
        out["reactions"] = reactions(ctx["earnings_rows"], bars, as_of)
        if out["reactions"]["n"] == 0:
            out["reactions"] = unavailable("no past earnings inside the daily-bar window", n=0, items=[])
    else:
        out["reactions"] = unavailable(ctx.get("bars_reason") or "no daily bars", items=[])
    re = out["reactions"]
    comp = {"ratio_implied_to_mean": None, "share_above_implied_window_1sd": None,
            "normal_benchmark_share": C.NORMAL_BEYOND_1SD}
    if ev.get("available") and re.get("available") and ev.get("implied_window_abs") and re.get("mean_abs"):
        comp["ratio_implied_to_mean"] = ev["implied_window_abs"] / re["mean_abs"]
        comp["share_above_implied_window_1sd"] = float(
            (np.abs([i["move"] for i in re["items"]]) > ev["implied_window_1sd"]).mean())
    out["comparison"] = comp
    ts = ctx["mods"].get("term_structure") or {}
    if ev.get("available") and ts.get("available"):
        fv = ts.get("forward_vols") or []
        covered = len(fv) >= 1 and fv[0]["from"] <= d <= fv[-1]["to"]
        if covered and (d - as_of).days <= C.DATE_CHECK_MAX_DAYS:
            ks = [k for k in ts["kinks"] if k["contains_earnings"]]
            out["date_check"] = "kink_contains_date" if ks else "no_kink" if not ts["kinks"] else "kink_elsewhere"
    return out


# ---------------------------------------------------------------------------- 5.5 volatility
def realized(bars: pd.Series) -> dict:
    r = np.log(bars / bars.shift(1)).dropna()
    out = {f"hv{n}": float(r.iloc[-n:].std(ddof=1) * math.sqrt(C.HV_ANNUAL)) for n in C.HV_WINDOWS}
    hv20 = (r.rolling(20, min_periods=20).std(ddof=1) * math.sqrt(C.HV_ANNUAL)).dropna().iloc[-C.PCTL_LOOKBACK:]
    out["hv20_pctl_1y"] = pctl(hv20.to_numpy(), float(hv20.iloc[-1])) if len(hv20) else None
    return out


def iv_index_block(rows, symbol: str | None, as_of: date) -> dict:
    """{symbol, close, date, pctl_1y} from a CBOE index history (rows [{date, close}])."""
    if not symbol:
        return unavailable("no CBOE volatility index for this symbol")
    if not rows:
        return unavailable(f"{symbol} history unavailable")
    w = [r for r in rows if r["date"] <= as_of][-C.PCTL_LOOKBACK:]
    if not w:
        return unavailable(f"{symbol} history has no rows up to {as_of}")
    vals = np.array([r["close"] for r in w], dtype=float)
    out = block(symbol=symbol, close=float(vals[-1]), date=w[-1]["date"], pctl_1y=pctl(vals, vals[-1]),
                n_obs=len(w))
    if w[-1]["date"] < as_of:
        add_note(out, nid="index_close_from", args=(w[-1]["date"],))
    return out


def ex_event_iv(cm30, event_in_window, move_1sd):
    """cm_iv_30d without the earnings event (5.5); None when it cannot be isolated."""
    if cm30 is None:
        return None
    if event_in_window is False:
        return cm30
    if event_in_window is None or move_1sd is None:
        return None
    t30 = 30 / 365.0
    v = (cm30 ** 2 * t30 - move_1sd ** 2) / t30
    return math.sqrt(v) if v > 0 else None


def event_in_window(kind: str, earnings_date, earnings_known: bool, as_of: date):
    if kind != "stock":
        return False
    if not earnings_known or earnings_date is None:
        return None
    return bool(earnings_date <= as_of + timedelta(days=30))


def build_volatility(ctx: dict) -> dict:
    s, as_of, kind = ctx["session"], ctx["as_of"], ctx["kind"]
    ts = ctx["mods"].get("term_structure") or {}
    cm30 = (ts.get("cm_iv") or {}).get("30d") if ts.get("available") else None
    eiw = event_in_window(kind, ctx.get("earnings_date"), ctx.get("earnings_known"), as_of)
    move = None
    if eiw:
        em = ((ctx["mods"].get("earnings") or {}).get("event_move")) or {}
        move = em.get("move_1sd")
    exe = ex_event_iv(cm30, eiw, move)
    out = block(iv30_cboe=s.get("iv30_cboe"), cm_iv_30d=cm30, cm_iv_30d_ex_event=exe, event_in_window=eiw)
    if cm30 is not None and exe is None:
        add_note(out, nid="ex_event_unavailable")
    out["iv_index"] = iv_index_block(ctx.get("iv_index"), ctx.get("iv_index_symbol"), as_of)
    if ctx.get("iv_index_symbol") and not ctx.get("iv_index") and ctx.get("iv_index_reason"):
        out["iv_index"]["reason"] = ctx["iv_index_reason"]
    bars = ctx.get("bars")
    if bars is None:
        out["realized"] = unavailable(ctx.get("bars_reason") or "no daily bars")
    elif len(bars) <= max(C.HV_WINDOWS):
        out["realized"] = unavailable(f"only {len(bars)} daily closes, need {max(C.HV_WINDOWS) + 1}")
    else:
        rv = realized(bars)
        out["realized"] = block(source=ctx.get("bars_source"), **rv)
        if ctx.get("bars_proxy"):
            add_note(out["realized"], nid="ndx_proxy")
    hv20 = out["realized"].get("hv20") if out["realized"].get("available") else None
    out["vrp"] = cm30 - hv20 if (cm30 is not None and hv20 is not None) else None
    out["iv_hv_ratio"] = cm30 / hv20 if (cm30 is not None and hv20) else None
    h = ctx.get("history")
    out["iv_rank"] = h.iv_rank("cm_iv_30d", cm30) if h is not None else unavailable("no history store")
    if kind == "stock":
        out["iv_rank_ex_event"] = (h.iv_rank("cm_iv_30d_ex_event", exe) if h is not None
                                   else unavailable("no history store"))
    else:
        out["iv_rank_ex_event"] = unavailable("ETF/index: no earnings")
    if not s.get("closed"):
        for k in ("iv_rank", "iv_rank_ex_event"):
            if out[k].get("available"):
                add_note(out[k], nid="intraday_rank")
    return out


# ---------------------------------------------------------------------------- 5.7 positioning
def max_pain(x: pd.DataFrame, exp: date) -> float | None:
    e = x[(x["expiration"] == exp) & (x["open_interest"] > 0)]
    if e.empty:
        return None
    ks = np.sort(e["strike"].unique())
    c, p = e[e["type"] == "C"], e[e["type"] == "P"]
    ck, co = c["strike"].to_numpy(dtype=float), c["open_interest"].to_numpy(dtype=float)
    pk, po = p["strike"].to_numpy(dtype=float), p["open_interest"].to_numpy(dtype=float)
    pay = [(co * np.maximum(k - ck, 0)).sum() + (po * np.maximum(pk - k, 0)).sum() for k in ks]
    return float(ks[int(np.argmin(pay))])


def _pcr(q: pd.DataFrame) -> dict:
    cv, pv = q.loc[q["type"] == "C", "volume"].sum(), q.loc[q["type"] == "P", "volume"].sum()
    co, po = q.loc[q["type"] == "C", "open_interest"].sum(), q.loc[q["type"] == "P", "open_interest"].sum()
    return {"volume": float(pv / cv) if cv else None, "oi": float(po / co) if co else None}


def build_positioning(ctx: dict) -> dict:
    x, S = ctx["x"], float(ctx["session"]["spot"])
    if x.empty:
        return unavailable("no live contracts")
    tags = ctx.get("key_tags") or {}
    w = x[x["dte"] <= C.OI_WINDOW_DTE]
    oi = w.groupby(["type", "strike"])["open_interest"].sum()
    empty = pd.Series(dtype="int64")
    oc = oi["C"] if "C" in oi.index.get_level_values(0) else empty
    op = oi["P"] if "P" in oi.index.get_level_values(0) else empty
    calls = oc[(oc.index >= S) & (oc.index <= S * (1 + C.KEY_LEVEL_RANGE))].nlargest(C.KEY_LEVEL_TOP)
    puts = op[(op.index <= S) & (op.index >= S * (1 - C.KEY_LEVEL_RANGE))].nlargest(C.KEY_LEVEL_TOP)
    far = []
    for t, inside, ser in (("C", calls, oc), ("P", puts, op)):
        side = ser[ser.index > S * (1 + C.KEY_LEVEL_RANGE)] if t == "C" else ser[ser.index < S * (1 - C.KEY_LEVEL_RANGE)]
        bar = inside.max() if len(inside) else 0
        far += [{"type": t, "strike": float(k), "oi": int(v)} for k, v in side[side > bar].nlargest(C.KEY_LEVEL_TOP).items()]
    nxt = next((e for e, tg in tags.items() if "next" in tg), None)
    mon = next((e for e, tg in tags.items() if "monthly" in tg), None)
    out = block(pcr={"all": _pcr(x), "d30": _pcr(x[x["dte"] <= C.PCR_SHORT_DTE])},
                key_levels={"window_dte": C.OI_WINDOW_DTE, "range_pct": C.KEY_LEVEL_RANGE,
                            "calls": [[float(k), int(v)] for k, v in calls.items()],
                            "puts": [[float(k), int(v)] for k, v in puts.items()]},
                far_otm=far,
                max_pain={"evidence": "weak",
                          "next": {"expiration": nxt, "strike": max_pain(x, nxt)} if nxt else None,
                          "monthly": {"expiration": mon, "strike": max_pain(x, mon)} if mon else None},
                total_oi=int(x["open_interest"].sum()), total_volume=int(x["volume"].sum()))
    add_note(out, C.OI_NOTE)
    add_note(out, "far_otm: large out-of-range positions (often hedges), not support or resistance")
    add_note(out, "max pain is weak evidence")
    h = ctx.get("history")
    out["oi_change"] = h.oi_change(ctx) if h is not None else unavailable("no history store")
    return out


# ---------------------------------------------------------------------------- 5.8 dealer gamma
def bs_gamma(spot, k, sig, t):
    d1 = (np.log(spot / k) + 0.5 * sig ** 2 * t) / (sig * np.sqrt(t))
    return np.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi) / (spot * sig * np.sqrt(t))


def gamma_arrays(session: dict, x: pd.DataFrame) -> dict:
    """The contracts in the GEX (OI > 0, valid IV, CBOE gamma present) and the gamma profile on the price grid."""
    g = x[(x["open_interest"] > 0) & x["valid_iv"] & x["gamma"].notna()]
    S = float(session["spot"])
    prof_g = g if session["closed"] else g[g["dte"] >= 1]
    psign = np.where(prof_g["type"] == "C", 1.0, -1.0)
    k, sig = prof_g["strike"].to_numpy(dtype=float), prof_g["iv"].to_numpy(dtype=float)
    t, oi = prof_g["T"].to_numpy(dtype=float), prof_g["open_interest"].to_numpy(dtype=float)
    grid = S * (1.0 + np.linspace(-C.GEX_GRID_PCT, C.GEX_GRID_PCT, C.GEX_GRID_POINTS))
    w = psign * oi
    prof = np.array([(w * bs_gamma(p, k, sig, t)).sum() * C.MULTIPLIER * p * p * 0.01 for p in grid])
    return {"g": g, "grid": grid, "profile": prof}


def build_gamma(ctx: dict) -> dict:
    s, x, kind = ctx["session"], ctx["x"], ctx["kind"]
    arr = gamma_arrays(s, x)
    ctx["gamma_arrays"] = arr
    g, grid, prof = arr["g"], arr["grid"], arr["profile"]
    if g.empty:
        return unavailable("no contracts with open interest, IV and gamma")
    S = float(s["spot"])
    sign = np.where(g["type"] == "C", 1.0, -1.0)
    net = float((sign * g["gamma"] * g["open_interest"]).sum() * C.MULTIPLIER * S * S * 0.01)
    roots = []
    for i in range(len(grid) - 1):
        if prof[i] == 0:
            roots.append(grid[i])
        elif np.sign(prof[i]) != np.sign(prof[i + 1]) and prof[i + 1] != 0:
            roots.append(grid[i] - prof[i] * (grid[i + 1] - grid[i]) / (prof[i + 1] - prof[i]))
    zero = float(min(roots, key=lambda r: abs(r - S))) if roots else None
    at_s = float(prof[C.GEX_GRID_POINTS // 2])
    regime = "positive" if at_s > 0 else "negative"
    if np.sign(at_s) != np.sign(net):
        regime = "near_zero"
    by = (g["gamma"] * g["open_interest"] * C.MULTIPLIER * S * S * 0.01).groupby([g["type"], g["strike"]]).sum()
    types = set(by.index.get_level_values(0))
    cw = by["C"][by["C"].index >= S] if "C" in types else pd.Series(dtype=float)
    pw = by["P"][by["P"].index <= S] if "P" in types else pd.Series(dtype=float)
    out = block(convention=C.GEX_CONVENTION,
                applicability="single_stock_low_confidence" if kind == "stock" else "standard",
                net_gex_1pct=net, profile_at_spot=at_s, regime=regime, zero_gamma=zero,
                zero_gamma_dist_pct=(zero / S - 1) if zero else None,
                call_wall=float(cw.idxmax()) if len(cw) else None, put_wall=float(pw.idxmax()) if len(pw) else None,
                contracts=int(len(g)), grid={"pct": C.GEX_GRID_PCT, "points": C.GEX_GRID_POINTS})
    if zero is None:
        add_note(out, f"no zero-gamma level within +-{C.GEX_GRID_PCT:.0%} of spot")
    if regime == "near_zero":
        add_note(out, "the gamma profile at spot and CBOE's net gamma have opposite signs: gamma is near zero")
    if not s["closed"]:
        add_note(out, "intraday: the profile leaves out contracts expiring today")
    add_note(out, "dollar gamma per 1% move; magnitudes depend on conventions, read the sign and the zero-gamma level")
    pos = (g["gamma"] > 0).to_numpy()
    if pos.sum() >= 2:
        bsg = bs_gamma(S, g["strike"].to_numpy(dtype=float), g["iv"].to_numpy(dtype=float), g["T"].to_numpy(dtype=float))
        cg = g["gamma"].to_numpy(dtype=float)
        corr = float(np.corrcoef(bsg[pos], cg[pos])[0, 1])
        ctx["gamma_check"] = {"corr": corr, "median_ratio": float(np.median(bsg[pos] / cg[pos])),
                              "ok": bool(corr >= C.GAMMA_CHECK_MIN_CORR)}
    else:
        ctx["gamma_check"] = {"corr": None, "median_ratio": None, "ok": None}
    return out


# ---------------------------------------------------------------------------- 5.9 unusual activity
def build_flow(ctx: dict) -> dict:
    x, as_of = ctx["x"], ctx["as_of"]
    q = x[(x["volume"] >= C.UA_MIN_VOLUME)
          & ((x["open_interest"] == 0) | (x["volume"] / x["open_interest"].where(x["open_interest"] > 0) >= C.UA_VOL_OI))].copy()
    last_ok = (q["last_trade_price"] > 0) & (q["ltt_date"] == as_of)
    q["px"] = q["mid"].where(q["mid"].notna(), q["last_trade_price"].where(last_ok))
    q["premium"] = q["volume"] * q["px"] * C.MULTIPLIER
    q = q[q["px"].notna()].sort_values("premium", ascending=False).head(C.UA_TOP)
    rows = [{"option": r.option, "type": r.type, "strike": float(r.strike), "expiration": r.expiration,
             "dte": int(r.dte), "volume": int(r.volume), "open_interest": int(r.open_interest),
             "vol_oi": (r.volume / r.open_interest) if r.open_interest else None, "premium": float(r.premium),
             "price": float(r.px), "iv": _f(r.iv), "delta": _f(r.delta), "last_trade_time": r.last_trade_time}
            for r in q.itertuples()]
    out = block(rules={"min_volume": C.UA_MIN_VOLUME, "min_vol_oi": C.UA_VOL_OI}, direction_note=C.FLOW_DIRECTION_NOTE,
                rows=rows)
    add_note(out, C.FLOW_DIRECTION_NOTE)
    return out
