"""
The market view (plan 7) and the market context of the ticker mode (plan 5.10).

- member(key, kind, payload, rates=None) -> ctx with the chain modules (term structure, expected move, skew,
  positioning, gamma) of one MARKET_UNIVERSE member, the same computation as the ticker mode.
- vix_block(vix_session, vix_rows, vix3m_rows, vix9d_rows, as_of): level, band, change, 1-year percentile and
  the VIX/VIX3M and VIX9D/VIX ratios with their percentiles (CBOE index CSVs aligned on common dates).
- thermometer(vix, spx_mods, hv20): the six flags with value, threshold and fired, and the summary label.
- market_context(spx_ctx, vix_session, vix_rows): the small table of the ticker mode.
"""
from __future__ import annotations

from datetime import date

import numpy as np

import config as C
import chain as CH
import core
from modules import add_note, block, pctl, unavailable


def band(level) -> str | None:
    if level is None:
        return None
    return next(lbl for hi, lbl in C.VIX_BANDS if level < hi)


def member(key: str, kind: str, payload: dict, rates=None) -> dict:
    """ctx of one market member with its chain modules (exceptions propagate)."""
    session, frame, _x, _info = CH.load_payload(payload, key)
    ctx = core.chain_context(key, kind, session, frame, rates=rates)
    core.run_chain_modules(ctx)
    return ctx


def _series(rows, as_of: date) -> dict:
    return {r["date"]: r["close"] for r in rows or [] if r["date"] <= as_of}


def _ratio(num: dict, den: dict):
    """(dates, values) of num/den on the dates both have, ascending, last PCTL_LOOKBACK."""
    common = sorted(set(num) & set(den))
    vals = [(d, num[d] / den[d]) for d in common if den[d]]
    vals = vals[-C.PCTL_LOOKBACK:]
    return [d for d, _ in vals], np.array([v for _, v in vals], dtype=float)


def vix_block(vix_session: dict | None, vix_rows, vix3m_rows, vix9d_rows, as_of: date) -> dict:
    if vix_session is None and not vix_rows:
        return unavailable("no VIX chain and no VIX history")
    vix = _series(vix_rows, as_of)
    if vix_session is not None:
        level = vix_session.get("px")
        prev = vix_session.get("prev_close")
        chg = vix_session.get("change_pct")
    else:
        d_last = max(vix)
        level, prev, chg = vix[d_last], None, None
    window = np.array([vix[d] for d in sorted(vix)][-C.PCTL_LOOKBACK:], dtype=float)
    out = block(level=level, band=band(level), prev_close=prev, change_pct=chg,
                pctl_1y=pctl(window, level) if len(window) else None,
                vix_vix3m=None, vix_vix3m_pctl_1y=None, vix9d_vix=None, vix9d_vix_pctl_1y=None)
    if vix_session is None:
        add_note(out, "VIX chain unavailable: level is the last close in CBOE's VIX history")
    last_dates = []
    if vix and vix3m_rows:
        dts, r = _ratio(vix, _series(vix3m_rows, as_of))
        if len(r):
            out["vix_vix3m"], out["vix_vix3m_pctl_1y"] = float(r[-1]), pctl(r, r[-1])
            last_dates.append(dts[-1])
    if vix and vix9d_rows:
        dts, r = _ratio(_series(vix9d_rows, as_of), vix)
        if len(r):
            out["vix9d_vix"], out["vix9d_vix_pctl_1y"] = float(r[-1]), pctl(r, r[-1])
            last_dates.append(dts[-1])
    if last_dates and min(last_dates) < as_of:
        add_note(out, nid="ratios_from", args=(min(last_dates),))
    if not vix_rows:
        add_note(out, "VIX history unavailable: no percentile")
    return out


def _flag(value, threshold, fired, reason=None) -> dict:
    return {"value": value, "threshold": threshold, "fired": fired, "reason": reason}


def thermometer(vix: dict, spx_mods: dict | None, hv20, hv20_reason: str | None = None) -> dict:
    spx_mods = spx_mods or {}
    ts, sk, gm = (spx_mods.get(k) or {} for k in ("term_structure", "skew", "gamma"))
    level = vix.get("level") if vix.get("available") else None
    cm30 = (ts.get("cm_iv") or {}).get("30d") if ts.get("available") else None
    flags = {}
    flags["vix_20_or_more"] = (_flag(level, C.THERMO_VIX_LEVEL, bool(level >= C.THERMO_VIX_LEVEL)) if level is not None
                               else _flag(None, C.THERMO_VIX_LEVEL, None, "VIX level unavailable"))
    shape = ts.get("shape") if ts.get("available") else None
    flags["term_inverted"] = (_flag(ts.get("ratio_30_90"), C.TERM_INVERTED, shape == "inverted") if shape
                              else _flag(None, C.TERM_INVERTED, None, "SPX term structure unavailable"))
    r7 = ts.get("ratio_7_30") if ts.get("available") else None
    flags["front_stress"] = (_flag(r7, C.THERMO_FRONT_RATIO, bool(r7 > C.THERMO_FRONT_RATIO)) if r7 is not None
                             else _flag(None, C.THERMO_FRONT_RATIO, None,
                                        "SPX 7-day IV unavailable or extrapolated"))
    steep = C.SKEW_LABELS["index_etf"][0]
    lab = sk.get("label") if sk.get("available") else None
    flags["skew_steep"] = (_flag(sk.get("rr25_30d"), steep, lab == "steep_put") if lab
                           else _flag(None, steep, None, "SPX skew unavailable"))
    reg = gm.get("regime") if gm.get("available") else None
    flags["negative_gamma"] = (_flag(gm.get("profile_at_spot"), 0.0, reg == "negative") if reg
                               else _flag(None, 0.0, None, "SPX gamma unavailable"))
    if hv20 is not None and cm30 is not None:
        flags["realized_above_implied"] = _flag(hv20, cm30, bool(hv20 > cm30))
    else:
        why = hv20_reason or "S&P 500 daily bars unavailable" if hv20 is None else "SPX 30-day IV unavailable"
        flags["realized_above_implied"] = _flag(hv20, cm30, None, why)
    n_true = sum(f["fired"] is True for f in flags.values())
    n_avail = sum(f["fired"] is not None for f in flags.values())

    def lab_of(n):
        return next(lbl for mx, lbl in C.THERMO_LABELS if n <= mx)

    lo, hi = lab_of(n_true), lab_of(n_true + (len(flags) - n_avail))
    out = block(flags=flags, n_flags=n_true, n_available=n_avail,
                label=lo if n_avail >= C.THERMO_MIN_AVAILABLE else None, label_uncertain=lo != hi,
                missing=[k for k, f in flags.items() if f["fired"] is None])
    if n_avail < C.THERMO_MIN_AVAILABLE:
        add_note(out, f"only {n_avail} of {len(flags)} flags available (need {C.THERMO_MIN_AVAILABLE}): no label")
    add_note(out, "a transparent sum of rules, not a forecast")
    return out


def market_context(spx_ctx: dict | None, spx_reason: str | None, vix_session: dict | None, vix_rows,
                   as_of: date) -> dict:
    """VIX level, band and percentile; SPX 30/90 ratio, shape, rr25 and label, gamma regime and zero-gamma distance."""
    if spx_ctx is None and vix_session is None and not vix_rows:
        return unavailable(spx_reason or "market data unavailable")
    vb = vix_block(vix_session, vix_rows, None, None, as_of) if (vix_session is not None or vix_rows) else None
    out = block(vix={"level": vb.get("level"), "band": vb.get("band"), "pctl_1y": vb.get("pctl_1y")} if vb else None,
                spx=None)
    if spx_ctx is not None:
        m = spx_ctx["mods"]
        ts, sk, gm = m.get("term_structure") or {}, m.get("skew") or {}, m.get("gamma") or {}
        out["spx"] = {"ratio_30_90": ts.get("ratio_30_90"), "shape": ts.get("shape"), "rr25_30d": sk.get("rr25_30d"),
                      "skew_label": sk.get("label"), "gamma_regime": gm.get("regime"),
                      "zero_gamma_dist_pct": gm.get("zero_gamma_dist_pct"),
                      "as_of": spx_ctx["as_of"]}
        if spx_ctx["as_of"] != as_of:
            add_note(out, f"SPX data is from {spx_ctx['as_of']}")
    else:
        add_note(out, f"SPX unavailable: {spx_reason}")
    if vb is None:
        add_note(out, "VIX unavailable")
    return out
