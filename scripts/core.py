"""
The chain-only part of an analysis, shared by analyze.run_analysis, analyze.run_market and history.summary_row
(so a summary row written by snapshot.py, by an analysis and by --rebuild-summary is the same computation).

chain_context(key, kind, session, frame, earnings_date=None, earnings_known=None) prepares the live contracts,
builds the surface (with the overnight spot rule of plan 4.4), picks E1/E2/E3 and returns the ctx dict that the
module builders read. run_chain_modules(ctx, names) runs builders in the fixed order and fills ctx["mods"].
"""
from __future__ import annotations

import chain as CH
import modules as M
import surface as SF

CHAIN_MODULES = (
    ("term_structure", M.build_term_structure),
    ("expected_move", M.build_expected_move),
    ("skew", M.build_skew),
    ("positioning", M.build_positioning),
    ("gamma", M.build_gamma),
)


def chain_context(key: str, kind: str, session: dict, frame, earnings_date=None, earnings_known=None) -> dict:
    """ctx with session, x, info, sf, ev, earnings_date, earnings_known (kind != stock -> known, no date)."""
    session = dict(session)
    x, info = CH.prepare(key, session, frame)
    sf = SF.build(session, x)
    if session.get("overnight_quotes") and SF.apply_overnight_spot(session, sf):
        sf = SF.build(session, x)
    if kind != "stock":
        earnings_date, earnings_known = None, True
    elif earnings_known is None:
        earnings_known = earnings_date is not None
    ctx = {"key": key, "kind": kind, "session": session, "as_of": session["as_of"], "frame": frame, "x": x,
           "info": info, "sf": sf, "earnings_date": earnings_date, "earnings_known": bool(earnings_known),
           "ev": SF.event_expiries(SF.usable(sf), earnings_date), "mods": {}, "history": None}
    return ctx


def run_chain_modules(ctx: dict, names=None) -> dict:
    """Run the chain-only builders (all, or `names`) in order; exceptions propagate (callers wrap them)."""
    for name, fn in CHAIN_MODULES:
        if names is None or name in names:
            ctx["mods"][name] = fn(ctx)
    return ctx["mods"]
