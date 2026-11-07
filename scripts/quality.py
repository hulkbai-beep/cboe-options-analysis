"""
data_quality blocks (plan 4.4, 5.1, 5.3, 5.8): counts, self-checks and the warnings with their i18n ids.
Shared by the ticker mode and every member of the market mode. calendar_check() compares the exchange holiday
calendar (Massive, optional) with config.MARKET_HOLIDAYS / EARLY_CLOSES in both modes.
"""
from __future__ import annotations

import config as C
import chain as CH
import massive_client as MC
from modules import add_warning, block, unavailable

CALENDAR_DATES_SHOWN = 5          # dates named in the calendar_mismatch warning


def iv_price_check(sf) -> dict:
    """max |atm_iv / atm_iv_cboe - 1| over expiries with 1 <= dte <= IV_PRICE_CHECK_DTE, and the expiries above the
    tolerance. An expiry settling today (intraday, hours left) is left out, as in the expected move."""
    dev = sf[(sf["dte"] >= 1) & (sf["dte"] <= C.IV_PRICE_CHECK_DTE) & sf["atm_iv"].notna() & sf["atm_iv_cboe"].notna()]
    devs = dev["atm_iv"] / dev["atm_iv_cboe"] - 1
    return {"max_abs_dev": float(devs.abs().max()) if len(devs) else None,
            "flagged": [e for e, v in zip(dev["expiration"], devs) if abs(v) > C.IV_PRICE_CHECK_TOL],
            "tolerance": C.IV_PRICE_CHECK_TOL}


def data_quality(ctx: dict, now=None) -> dict:
    s, info, sf, mods = ctx["session"], ctx["info"], ctx["sf"], ctx.get("mods") or {}
    ts = mods.get("term_structure") or {}
    fwd = ts.get("forward_vols") or []
    vq = info.get("valid_quote_share")
    stale = CH.stale_info(s["as_of"], now)
    gc = ctx.get("gamma_check") or {"corr": None, "median_ratio": None, "ok": None}
    ipc = iv_price_check(sf)
    dq = {
        "delay_note": C.DELAY_NOTE, "session_closed": bool(s["closed"]), "overnight_quotes": bool(s["overnight_quotes"]),
        "stale": stale, "prev_close_rolled": bool(s.get("prev_close_rolled")),
        "missing_quote_share": (1.0 - vq) if vq is not None else None,
        "crossed_quotes": info.get("crossed_quotes"), "iv_outliers": info.get("iv_outliers"),
        "negative_forward_variance": int(sum(1 for f in fwd if f["negative_variance"])),
        "dropped_expiries": [e for e in sf.loc[~sf["bracket_ok"], "expiration"]] if len(sf) else [],
        "iv_price_check": ipc, "gamma_check": gc, "oi_note": C.OI_NOTE, "warnings": [], "warning_ids": [],
    }
    if s["overnight_quotes"]:
        add_warning(dq, "overnight_quotes", (s["as_of"],))
    if stale["is_stale"]:
        add_warning(dq, "stale_quotes", (s["as_of"], stale["business_days_behind"]))
    if not s["closed"]:
        add_warning(dq, "intraday_quotes")
    if info.get("contracts_live") and not vq:
        add_warning(dq, "no_live_quotes")
    if ipc["flagged"]:
        add_warning(dq, "iv_price_check", (f"{C.IV_PRICE_CHECK_TOL:.0%}", len(ipc["flagged"])))
    if gc.get("ok") is False:
        add_warning(dq, "gamma_check", (f"{gc['corr']:.3f}",))
    cm30 = ((ts.get("cm_iv") or {}).get("30d")) if ts.get("available") else None
    iv30 = s.get("iv30_cboe")
    if cm30 is not None and iv30 is not None and abs(iv30 - cm30) > C.IV30_CHECK_TOL:
        add_warning(dq, "iv30_check", (f"{iv30:.1%}", f"{cm30:.1%}"))
    return dq


def calendar_check(dq: dict, rows, reason: str | None, today) -> dict:
    """dq["calendar_check"]: the NYSE holiday calendar from Massive (rows of /v1/marketstatus/upcoming, or None with
    a reason) against MARKET_HOLIDAYS and EARLY_CLOSES from `today` (New York date) on; a mismatch adds the
    calendar_mismatch warning, because the session, staleness and business-day logic read config.py."""
    if rows is None:
        dq["calendar_check"] = unavailable(reason or "no exchange calendar", source="Massive")
        return dq["calendar_check"]
    cmp = MC.compare_calendar(rows, today)
    if not cmp["checked_through"]:
        dq["calendar_check"] = unavailable("Massive listed no upcoming exchange holidays", source="Massive")
        return dq["calendar_check"]
    out = block(source="Massive", checked_from=cmp["checked_from"], checked_through=cmp["checked_through"],
                n_dates=cmp["n_dates"], mismatches=cmp["mismatches"])
    dq["calendar_check"] = out
    mm = cmp["mismatches"]
    if mm:
        shown = ", ".join(m["date"].isoformat() for m in mm[:CALENDAR_DATES_SHOWN])
        add_warning(dq, "calendar_mismatch", (len(mm), shown + (", ..." if len(mm) > CALENDAR_DATES_SHOWN else "")))
    return out
