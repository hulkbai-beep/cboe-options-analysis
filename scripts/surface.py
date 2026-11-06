"""
The per-expiry volatility surface (plan 5.1) and the helpers built on it.

build(session, x) -> one row per surface expiry (the preferred root of each expiry):
  expiration, root, settlement, european, dte, T, forward, bracket_ok, atm_strike, straddle, straddle_pct,
  atm_iv (backed out of the ATM straddle with Black-76), atm_iv_source, atm_iv_cboe, move_1sd_pct,
  put25_iv, call25_iv, rr25 (= put25 - call25), bf25, drop_reason.
apply_overnight_spot(session, sf): overnight (GTH) quotes -> spot = forward of the first expiry with dte >= 1.
interp_in_t(): constant-maturity interpolation (total variance or linear in T, flat beyond the ends).
kink_expiries(), event_expiries() (E1, E2, E3 of plan 5.6), key_expiries() (the tags of plan 5.2).
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

import config as C

SURFACE_COLUMNS = ("expiration", "root", "settlement", "european", "dte", "T", "forward", "bracket_ok", "atm_strike",
                   "straddle", "straddle_pct", "atm_iv", "atm_iv_source", "atm_iv_cboe", "move_1sd_pct",
                   "put25_iv", "call25_iv", "rr25", "bf25", "drop_reason")
DROP_NO_FORWARD = "no strikes for the forward"
DROP_NO_BRACKET = "no strikes bracketing the forward (partial listing)"


def n_cdf(v: float) -> float:
    return 0.5 * (1.0 + math.erf(v / math.sqrt(2.0)))


# ---------------------------------------------------------------------------- one expiry
def pairs(e: pd.DataFrame) -> pd.DataFrame:
    """Strikes listed as both call and put: c_mid, p_mid (NaN without a valid quote), c_iv, p_iv (NaN unless valid)."""
    c = e[e["type"] == "C"].drop_duplicates("strike").set_index("strike")
    p = e[e["type"] == "P"].drop_duplicates("strike").set_index("strike")
    k = c.index.intersection(p.index)
    return pd.DataFrame({"c_mid": c.loc[k, "mid"], "p_mid": p.loc[k, "mid"],
                         "c_iv": c.loc[k, "iv"].where(c.loc[k, "valid_iv"]),
                         "p_iv": p.loc[k, "iv"].where(p.loc[k, "valid_iv"])}).sort_index()


def forward(pr: pd.DataFrame, s_ref: float) -> float | None:
    """Median of K + C - P over the FWD_MEDIAN_N strikes with the smallest |C - P| within +-FWD_STRIKE_RANGE."""
    q = pr.dropna(subset=["c_mid", "p_mid"])
    if q.empty or not s_ref:
        return None
    k = q.index.to_numpy(dtype=float)
    q = q[np.abs(k / s_ref - 1.0) <= C.FWD_STRIKE_RANGE]
    if q.empty:
        return None
    k = q.index.to_numpy(dtype=float)
    t = pd.DataFrame({"k": k, "d": (q["c_mid"] - q["p_mid"]).abs().to_numpy(),
                      "f": k + (q["c_mid"] - q["p_mid"]).to_numpy()})
    t = t.sort_values(["d", "k"], kind="mergesort").head(C.FWD_MEDIAN_N)
    return float(np.median(t["f"]))


def b76_straddle(f: float, k: float, sig: float, t: float, disc: float) -> float:
    """Discounted Black-76 call + put at strike k."""
    sd = sig * math.sqrt(t)
    d1 = (math.log(f / k) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    return disc * (f * (2.0 * n_cdf(d1) - 1.0) - k * (2.0 * n_cdf(d2) - 1.0))


def implied_from_straddle(price: float, f: float, k: float, t: float, disc: float) -> float | None:
    """sigma with b76_straddle(f, k, sigma, t, disc) == price by bisection; None when no solution exists."""
    lo, hi = C.IV_SOLVER_BOUNDS
    if not (price > 0 and f > 0 and k > 0 and t > 0):
        return None
    if price <= disc * abs(f - k) or price >= b76_straddle(f, k, hi, t, disc):
        return None
    for _ in range(C.IV_SOLVER_MAX_ITER):
        mid = 0.5 * (lo + hi)
        if b76_straddle(f, k, mid, t, disc) > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < C.IV_SOLVER_TOL:
            break
    return 0.5 * (lo + hi)


def wing(e: pd.DataFrame, kind: str) -> float | None:
    """CBOE IV interpolated at delta -0.25 (puts) or +0.25 (calls) over |delta| in SKEW_DELTA_BAND."""
    lo, hi = C.SKEW_DELTA_BAND
    base = e[(e["type"] == kind) & e["valid_quote"] & e["valid_iv"] & e["delta"].notna()]
    if kind == "P":
        q, target = base[(base["delta"] > -hi) & (base["delta"] < -lo)], -C.SKEW_DELTA
    else:
        q, target = base[(base["delta"] > lo) & (base["delta"] < hi)], C.SKEW_DELTA
    if q.empty:
        return None
    g = q.groupby("delta")["iv"].mean().sort_index()
    if not (g.index.min() <= target <= g.index.max()):
        return None
    return float(np.interp(target, g.index.to_numpy(dtype=float), g.to_numpy(dtype=float)))


def _expiry_row(e: pd.DataFrame, spot: float) -> dict:
    pr = pairs(e)
    T, root = float(e["T"].iloc[0]), str(e["root"].iloc[0])
    european = root in C.EUROPEAN_ROOTS
    disc = math.exp(-C.DISCOUNT_RATE * T) if european else 1.0
    f = forward(pr, spot)
    row = {"expiration": e["expiration"].iloc[0], "root": root,
           "settlement": "AM" if root in C.AM_SETTLED_ROOTS else "PM", "european": european,
           "dte": int(e["dte"].iloc[0]), "T": T, "forward": f, "bracket_ok": False, "atm_strike": None,
           "straddle": None, "straddle_pct": None, "atm_iv": None, "atm_iv_source": None, "atm_iv_cboe": None,
           "move_1sd_pct": None, "put25_iv": None, "call25_iv": None, "rr25": None, "bf25": None,
           "drop_reason": DROP_NO_FORWARD if f is None else DROP_NO_BRACKET}
    both = pr.dropna(subset=["c_mid", "p_mid", "c_iv", "p_iv"])
    if f is not None and not both.empty:
        ks = both.index.to_numpy(dtype=float)
        below, above = ks[ks <= f], ks[ks >= f]
        if len(below) and len(above) and (above.min() - below.max()) / f <= C.ATM_MAX_BRACKET:
            k_lo, k_hi = float(below.max()), float(above.min())
            iv = (both["c_iv"] + both["p_iv"]) / 2.0
            if k_lo == k_hi:
                iv_cboe = float(iv[k_lo])
            else:
                iv_cboe = float(np.interp(f, [k_lo, k_hi], [iv[k_lo], iv[k_hi]]))
            k_atm = k_lo if (f - k_lo) <= (k_hi - f) else k_hi
            strad = float(both.at[k_atm, "c_mid"] + both.at[k_atm, "p_mid"])
            sig = implied_from_straddle(strad, f, k_atm, T, disc)
            atm = sig if sig is not None else iv_cboe
            row.update(bracket_ok=True, atm_strike=k_atm, straddle=strad, straddle_pct=strad / spot,
                       atm_iv_cboe=iv_cboe, atm_iv=atm, atm_iv_source="straddle" if sig is not None else "cboe_fallback",
                       move_1sd_pct=atm * math.sqrt(T), drop_reason=None)
    if row["bracket_ok"]:
        p25, c25 = wing(e, "P"), wing(e, "C")
        row.update(put25_iv=p25, call25_iv=c25,
                   rr25=(p25 - c25) if (p25 is not None and c25 is not None) else None,
                   bf25=((p25 + c25) / 2.0 - row["atm_iv_cboe"])
                   if (p25 is not None and c25 is not None and row["atm_iv_cboe"] is not None) else None)
    return row


def build(session: dict, x: pd.DataFrame) -> pd.DataFrame:
    """The surface: one row per expiry of the surface contracts, sorted by expiration."""
    spot = session.get("spot")
    rows = []
    if spot and len(x):
        for _, e in x[x["surface"]].groupby("expiration", sort=True):
            rows.append(_expiry_row(e, float(spot)))
    sf = pd.DataFrame(rows, columns=list(SURFACE_COLUMNS))
    for c in ("forward", "atm_strike", "straddle", "straddle_pct", "atm_iv", "atm_iv_cboe", "move_1sd_pct",
              "put25_iv", "call25_iv", "rr25", "bf25", "T"):
        sf[c] = pd.to_numeric(sf[c], errors="coerce").astype("float64")
    sf["bracket_ok"] = sf["bracket_ok"].astype(bool)
    return sf.reset_index(drop=True)


def apply_overnight_spot(session: dict, sf: pd.DataFrame) -> bool:
    """Overnight (GTH) quotes: spot := forward of the first surface expiry with dte >= 1. True when applied."""
    q = sf[(sf["dte"] >= 1) & sf["forward"].notna()].sort_values("expiration")
    if q.empty:
        return False
    session["spot"], session["spot_basis"] = float(q["forward"].iloc[0]), "forward_first_expiry"
    return True


def usable(sf: pd.DataFrame) -> pd.DataFrame:
    """Expiries that passed the bracket rule (the others are term_structure.dropped_expiries)."""
    return sf[sf["bracket_ok"]]


def dropped(sf: pd.DataFrame) -> list[date]:
    return list(sf.loc[~sf["bracket_ok"], "expiration"])


# ---------------------------------------------------------------------------- interpolation in T (5.3, 5.4)
def interp_in_t(sf: pd.DataFrame, col: str, days: float, min_dte: int, variance: bool) -> tuple[float | None, bool]:
    """(value at `days`, extrapolated flag). variance=True interpolates total variance col^2 * T."""
    q = sf[(sf["dte"] >= min_dte) & sf[col].notna()].sort_values("T")
    if q.empty:
        return None, False
    t, v = q["T"].to_numpy(dtype=float), q[col].to_numpy(dtype=float)
    ts, d_first, d_last = days / 365.0, t[0] * 365.0, t[-1] * 365.0
    if ts <= t[0]:
        extrap = (d_first - days >= C.CM_EXTRAP_7D_DAYS) if days <= 7 else ((d_first - days) / days > C.CM_EXTRAP_REL)
        return float(v[0]), bool(extrap)
    if ts >= t[-1]:
        return float(v[-1]), bool((days - d_last) / days > C.CM_EXTRAP_REL)
    i = int(np.searchsorted(t, ts)) - 1
    if not variance:
        return float(v[i] + (v[i + 1] - v[i]) * (ts - t[i]) / (t[i + 1] - t[i])), False
    w0, w1 = v[i] ** 2 * t[i], v[i + 1] ** 2 * t[i + 1]
    w = w0 + (w1 - w0) * (ts - t[i]) / (t[i + 1] - t[i])
    return (math.sqrt(w / ts) if w > 0 else None), False


def kink_expiries(sf: pd.DataFrame) -> pd.DataFrame:
    """Expiries for forward vols and kinks: dte >= KINK_MIN_DTE with atm_iv, greedily thinned to >= KINK_MIN_GAP days."""
    q = sf[(sf["dte"] >= C.KINK_MIN_DTE) & sf["atm_iv"].notna()].sort_values("T")
    keep, last = [], None
    for i, d in enumerate(q["dte"]):
        if last is None or d - last >= C.KINK_MIN_GAP:
            keep.append(i)
            last = d
    return q.iloc[keep]


# ---------------------------------------------------------------------------- earnings expiries (5.6)
def event_expiries(sf: pd.DataFrame, d: date | None) -> dict:
    """{e1, e2, e3} rows (pd.Series or None) around the earnings date d: E1 the last expiry before d, E2 the first
    after d (an expiry on d itself is not used), E3 the first after E2 at least KINK_MIN_GAP days later (only
    looked for when there is no E1). All need dte >= 1 and an atm_iv."""
    out = {"e1": None, "e2": None, "e3": None}
    if d is None or sf.empty:
        return out
    q = sf[(sf["dte"] >= 1) & sf["atm_iv"].notna()].sort_values("expiration")
    before, after = q[q["expiration"] < d], q[q["expiration"] > d]
    if after.empty:
        return out
    out["e2"] = after.iloc[0]
    if not before.empty:
        out["e1"] = before.iloc[-1]
    else:
        e3s = after[after["dte"] - out["e2"]["dte"] >= C.KINK_MIN_GAP]
        if not e3s.empty:
            out["e3"] = e3s.iloc[0]
    return out


# ---------------------------------------------------------------------------- key expiries (5.2)
def is_monthly(e: date, expiries) -> bool:
    """Third Friday (15th-21st), or a Thursday 14th-20th whose Friday is not an expiry (holiday-shifted)."""
    if e.weekday() == 4 and 15 <= e.day <= 21:
        return True
    return e.weekday() == 3 and 14 <= e.day <= 20 and (e + timedelta(days=1)) not in expiries


def week_end(d: date) -> date:
    """The Friday of d's Monday-Friday week (a weekend day maps to the Friday before)."""
    return d + timedelta(days=4 - d.weekday())


def key_expiries(sf: pd.DataFrame, as_of: date, e2: date | None) -> dict:
    """{expiration: [tags]} sorted by date; tags next, this_week, next_week, monthly, 30d, event."""
    tags: dict = {}
    if sf.empty:
        return tags
    q = sf[(sf["dte"] >= 1) & sf["straddle"].notna()].sort_values("expiration")
    all_exp = set(sf["expiration"])

    def add(e, tag):
        if e is not None:
            tags.setdefault(e, []).append(tag)

    if q.empty:
        return tags
    add(q["expiration"].iloc[0], "next")
    this_w = q[q["expiration"] <= week_end(as_of)]
    add(this_w["expiration"].iloc[-1] if len(this_w) else None, "this_week")
    nw_end = week_end(as_of) + timedelta(days=7)
    next_w = q[(q["expiration"] > week_end(as_of)) & (q["expiration"] <= nw_end)]
    add(next_w["expiration"].iloc[-1] if len(next_w) else None, "next_week")
    monthly = q[[is_monthly(e, all_exp) for e in q["expiration"]]]
    add(monthly["expiration"].iloc[0] if len(monthly) else None, "monthly")
    add(q.loc[(q["dte"] - 30).abs().sort_values(kind="stable").index[0], "expiration"], "30d")
    if e2 is not None and e2 in set(q["expiration"]):
        add(e2, "event")
    return dict(sorted(tags.items()))
