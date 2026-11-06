"""
Strategy screens (plan 6): arithmetic on the delayed chain under the risk-neutral distribution the option prices
imply. No recommendations: the report says so.

build_strategies(ctx) -> {available, notes, context, filters, short_put, covered_call, long_call, long_put,
earnings_straddle}. Each screen keeps, per expiry, the contract whose delta is closest to the target, after the
filters (surface contracts, valid quote and IV, OI >= STRAT_MIN_OI, spread <= STRAT_MAX_SPREAD, closed delta and
dte windows, a forward for the expiry). ctx["strategy_candidates"] gets the first STRAT_CHART_ROWS candidates of
each screen for the chart.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

import config as C
import chain as CH
from modules import add_note, block, unavailable
from surface import n_cdf

ROW_KEYS = ("option", "expiration", "dte", "strike", "bid", "ask", "mid", "spread_pct", "iv", "delta", "p_itm",
            "p_touch", "p_profit", "otm_pct", "em_units", "breakeven", "breakeven_ask", "breakeven_move_pct",
            "earnings_before_expiry", "ex_div_date", "dividend", "theta_pct_per_day", "yield", "yield_bid",
            "annualized_yield", "annualized_yield_bid", "upside_cap_pct", "if_called_return", "yield_with_div",
            "premium_per_contract", "capital", "max_loss", "pct_of_example_account", "liquidity", "exercise_style",
            "settlement", "open_interest", "volume")


def keep_row_expiry(e: date, expiries) -> bool:
    """Fridays, and Thursdays whose Friday is not an expiry (a holiday Friday)."""
    return e.weekday() == 4 or (e.weekday() == 3 and (e + timedelta(days=1)) not in expiries)


def _screen(name: str, ctx: dict, earn, earn_known: bool) -> tuple[dict, list]:
    typ, band, win, target = C.STRATEGIES[name]
    s, x, sf, kind = ctx["session"], ctx["x"], ctx["sf"], ctx["kind"]
    S, as_of = float(s["spot"]), ctx["as_of"]
    fwd = {e: f for e, f in zip(sf["expiration"], sf["forward"]) if f is not None and np.isfinite(f)}
    m1 = {e: m for e, m in zip(sf["expiration"], sf["move_1sd_pct"]) if m is not None and np.isfinite(m)}
    all_exp = set(sf["expiration"])
    q = x[x["surface"] & x["valid_quote"] & x["valid_iv"] & (x["type"] == typ) & (x["open_interest"] >= C.STRAT_MIN_OI)
          & (x["delta"] >= band[0]) & (x["delta"] <= band[1]) & (x["dte"] >= win[0]) & (x["dte"] <= win[1])].copy()
    q["spread_pct"] = (q["ask"] - q["bid"]) / q["mid"]
    q = q[q["spread_pct"] <= C.STRAT_MAX_SPREAD].copy()
    q["F"] = [fwd.get(e) for e in q["expiration"]]
    q = q[q["F"].notna()].copy()
    q["F"] = q["F"].astype(float)
    sd = q["iv"] * np.sqrt(q["T"])

    def d2(kk):
        return (np.log(q["F"] / kk) - 0.5 * sd ** 2) / sd

    q["p_itm"] = [n_cdf(v) if typ == "C" else n_cdf(-v) for v in d2(q["strike"])]
    q["p_touch"] = (2 * q["p_itm"]).where(q["p_itm"] <= 0.5)
    q["otm_pct"] = q["strike"] / S - 1
    q["em_units"] = [(k / S - 1) / m1[e] if m1.get(e) else None for k, e in zip(q["strike"], q["expiration"])]
    if kind in ("etf", "index"):
        q["earnings_before_expiry"] = False
    elif not earn_known:
        q["earnings_before_expiry"] = None
    else:
        q["earnings_before_expiry"] = [bool(earn and as_of <= earn <= e) for e in q["expiration"]]
    divs = ctx.get("dividends") or []
    exd = [next(((d, a) for d, a in divs if as_of < d <= e), (None, None)) for e in q["expiration"]]
    q["ex_div_date"] = [d for d, _ in exd]
    q["dividend"] = pd.to_numeric(pd.Series([a for _, a in exd], index=q.index, dtype="object"), errors="coerce")
    if kind == "index":
        q["settlement"] = np.where(q["root"].isin(list(C.AM_SETTLED_ROOTS)), "AM", "PM")
    q["premium_per_contract"] = q["mid"] * C.MULTIPLIER
    if name == "short_put":
        q["breakeven"] = q["strike"] - q["mid"]
        q["yield"], q["yield_bid"] = q["mid"] / (q["strike"] - q["mid"]), q["bid"] / (q["strike"] - q["bid"])
        q["capital"] = q["strike"] * C.MULTIPLIER
        q["max_loss"] = (q["strike"] - q["mid"]) * C.MULTIPLIER
        q["p_profit"] = [n_cdf(v) for v in d2(q["breakeven"])]
    elif name == "covered_call":
        q["breakeven"] = S - q["mid"]
        q["yield"], q["yield_bid"] = q["mid"] / S, q["bid"] / S
        q["upside_cap_pct"] = q["strike"] / S - 1
        q["if_called_return"] = (q["strike"] - S + q["mid"]) / S
        q["yield_with_div"] = ((q["mid"] + q["dividend"]) / S).where(q["dividend"].notna())
        q["capital"] = S * C.MULTIPLIER
        q["max_loss"] = (S - q["mid"]) * C.MULTIPLIER
        q["p_profit"] = [n_cdf(v) for v in d2(q["breakeven"])]
    elif name == "long_call":
        q["breakeven"], q["breakeven_ask"] = q["strike"] + q["mid"], q["strike"] + q["ask"]
        q["capital"] = q["max_loss"] = q["mid"] * C.MULTIPLIER
        q["p_profit"] = [n_cdf(v) for v in d2(q["breakeven"])]
    else:
        q["breakeven"], q["breakeven_ask"] = q["strike"] - q["mid"], q["strike"] - q["ask"]
        q["capital"] = q["max_loss"] = q["mid"] * C.MULTIPLIER
        q["p_profit"] = [n_cdf(-v) for v in d2(q["breakeven"])]
    q["breakeven_move_pct"] = q["breakeven"] / S - 1
    if "yield" in q:
        q["annualized_yield"], q["annualized_yield_bid"] = q["yield"] * 365 / q["dte"], q["yield_bid"] * 365 / q["dte"]
    q["pct_of_example_account"] = q["capital"] / C.EXAMPLE_ACCOUNT
    q["theta_pct_per_day"] = q["theta"] / q["mid"]
    q["liquidity"] = np.where((q["open_interest"] >= C.LIQ_A_OI) & (q["volume"] >= C.LIQ_A_VOLUME), "A",
                              np.where(q["open_interest"] >= C.LIQ_B_OI, "B", "C"))
    q["exercise_style"] = np.where(q["european"], "european", "american")
    q["dist"] = (q["delta"] - target).abs()
    ordered = q.sort_values(["expiration", "dist", "strike"])
    rows = ordered.groupby("expiration").head(1)
    if len(rows) > C.STRAT_MAX_ROWS:
        rows = rows[[keep_row_expiry(e, all_exp) for e in rows["expiration"]]]
    rows = rows.head(C.STRAT_MAX_ROWS)

    def records(df):
        return [{k: v for k, v in r.items() if k in ROW_KEYS} for r in df.to_dict("records")]

    out = block(type=typ, delta_band=list(band), dte_window=list(win), target_delta=target, candidates=int(len(q)),
                rows=records(rows))
    return out, records(ordered.head(C.STRAT_CHART_ROWS))


def _earnings_straddle(ctx: dict) -> dict:
    earn = ctx["mods"].get("earnings") or {}
    ev = earn.get("event_move") or {}
    e2 = (ctx.get("ev") or {}).get("e2")
    if ctx["kind"] != "stock":
        return unavailable("ETF/index: no earnings")
    if not earn.get("available"):
        return unavailable(earn.get("reason") or "no earnings date")
    if not ev.get("available") or e2 is None:
        return unavailable(ev.get("reason") or "no expiry after the event")
    if int(e2["dte"]) > C.STRAT_EVENT_MAX_DTE:
        return unavailable(f"the first expiry after earnings is more than {C.STRAT_EVENT_MAX_DTE} days away")
    if e2["straddle"] is None or not np.isfinite(e2["straddle"]):
        return unavailable("no ATM straddle on the first expiry after earnings")
    S, as_of = float(ctx["session"]["spot"]), ctx["as_of"]
    K, st, F2, sg, T2 = (float(e2["atm_strike"]), float(e2["straddle"]), float(e2["forward"]), float(e2["atm_iv"]),
                         float(e2["T"]))
    sd2 = sg * math.sqrt(T2)

    def d2f(kk):
        return (math.log(F2 / kk) - 0.5 * sd2 ** 2) / sd2

    up, dn = (K + st) / S - 1, (K - st) / S - 1
    d = ctx["earnings_date"]
    k_pre = CH.busdays(as_of + timedelta(days=1), d)
    k_post = CH.busdays(d, e2["expiration"] + timedelta(days=1))
    out = block(expiration=e2["expiration"], strike=K, cost=st, cost_per_contract=st * C.MULTIPLIER, be_up=up,
                be_down=dn, event_variance_share=ev.get("event_variance_share"),
                p_beyond_be_rn=n_cdf(d2f(K + st)) + (n_cdf(-d2f(K - st)) if K > st else 0.0),
                k_pre=k_pre, k_post=k_post, hold_days=k_pre + k_post, n=None, hist_share_beyond_be=None)
    react, bars = earn.get("reactions") or {}, ctx.get("bars")
    if bars is not None and react.get("available") and react.get("n"):
        idx, moves = bars.index, []
        for it in react["items"]:
            i_d = int(np.searchsorted(idx, pd.Timestamp(it["date"])))
            a, b = i_d - k_pre - 1, i_d + k_post - 1
            if a >= 0 and b < len(idx):
                moves.append(float(bars.iloc[b] / bars.iloc[a] - 1))
        mv = np.array(moves)
        out.update(n=len(mv), hist_share_beyond_be=float(((mv >= up) | (mv <= dn)).mean()) if len(mv) else None)
    else:
        add_note(out, "no daily bars or past reactions: no historical comparison")
    share = ev.get("event_variance_share")
    if share is not None and share < 0.5:
        add_note(out, nid="straddle_non_event")
    add_note(out, "break-evens are asymmetric: strike +- straddle cost, relative to spot")
    return out


def build_strategies(ctx: dict) -> dict:
    kind, as_of = ctx["kind"], ctx["as_of"]
    earn, earn_known = ctx.get("earnings_date"), bool(ctx.get("earnings_known"))
    mods = ctx["mods"]
    vol, ts, sk = mods.get("volatility") or {}, mods.get("term_structure") or {}, mods.get("skew") or {}
    rv = vol.get("realized") or {}
    out = block(context={
        "cm_iv_30d": vol.get("cm_iv_30d"), "hv20": rv.get("hv20") if rv.get("available") else None,
        "vrp": vol.get("vrp"),
        "iv_index_pctl": (vol.get("iv_index") or {}).get("pctl_1y") if (vol.get("iv_index") or {}).get("available") else None,
        "iv_rank": (vol.get("iv_rank") or {}).get("value") if (vol.get("iv_rank") or {}).get("available") else None,
        "iv_rank_ex_event": ((vol.get("iv_rank_ex_event") or {}).get("value")
                             if (vol.get("iv_rank_ex_event") or {}).get("available") else None),
        "term_shape": ts.get("shape"), "skew_label": sk.get("label"),
        "next_earnings": earn if kind == "stock" else None},
        filters={"min_oi": C.STRAT_MIN_OI, "max_spread": C.STRAT_MAX_SPREAD,
                 "strategies": {k: {"type": v[0], "delta": list(v[1]), "dte": list(v[2]), "target_delta": v[3]}
                                for k, v in C.STRATEGIES.items()}})
    if kind == "stock" and not earn_known:
        add_note(out, nid="earnings_unknown")
    if ctx["session"].get("closed"):
        add_note(out, nid="closed_spread")
    add_note(out, "risk-neutral arithmetic from option prices, not advice")
    ctx["strategy_candidates"] = {}
    for name in C.STRATEGIES:
        if name == "covered_call" and kind == "index":
            out[name] = unavailable("index: no shares to cover")
            ctx["strategy_candidates"][name] = []
            continue
        out[name], ctx["strategy_candidates"][name] = _screen(name, ctx, earn, earn_known)
        rows = out[name]["rows"]
        if name == "short_put" and any(r.get("exercise_style") == "american" for r in rows):
            add_note(out[name], nid="early_assignment")
        if name == "covered_call":
            exd = sorted((r["ex_div_date"], r["dividend"]) for r in rows if r.get("ex_div_date"))
            if exd:
                add_note(out[name], nid="ex_div_call", args=(exd[0][0], f"{exd[0][1]:.2f}"))
    out["earnings_straddle"] = _earnings_straddle(ctx)
    return out
