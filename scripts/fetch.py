"""
Data loading for one analysis (plan 3.4 step 2).

load_all(text, cboe, fmp, now=None, market_context=True) -> dict with the symbol info (symbols.normalize, with the
"_" fallback for an index that is not in config.INDEX_SYMBOLS), the CBOE payload and its session, and the optional
pieces: FMP profile, earnings, dividends and daily closes, the CBOE volatility-index history, and the _SPX / _VIX
chains and VIX history for the market context.

FMP is optional: every FMP request goes through FmpState.get(), which turns a missing key, a rejected key, an
unreachable host or a plan restriction into a reason string (and meta.restricted / meta.notes), never into an
exception. CBOE errors on the main chain propagate (NotFound, CboeUnreachable, CboeError).
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pandas as pd

import config as C
import chain as CH
import fmp_client
from cboe_client import CboeError, NotFound
from symbols import kind_from_profile, normalize

FULL = "historical-price-eod/full"
FMP_NOT_CONFIGURED = "FMP not configured"


def make_fmp(refresh: bool = False):
    """(FMPClient or None, reason): a client only when FMP_API_KEY is set and the client starts."""
    if not os.environ.get("FMP_API_KEY", "").strip():
        return None, FMP_NOT_CONFIGURED
    try:
        return fmp_client.FMPClient(refresh=refresh), None
    except fmp_client.InvalidKey:
        return None, "FMP key rejected"
    except fmp_client.Unreachable as e:
        return None, f"FMP unreachable: {e}"
    except fmp_client.FMPError as e:
        return None, f"FMP error: {e}"


class FmpState:
    """Optional FMP requests with reasons instead of exceptions."""

    def __init__(self, client, reason: str | None = None):
        self.client = client
        self.dead = None if client is not None else (reason or FMP_NOT_CONFIGURED)
        self.restricted: list[str] = []
        self.notes: list[str] = []
        self._hinted = False

    def _hint(self):
        if not self._hinted:
            self._hinted = True
            print("[fmp] some FMP data is unavailable; run `python scripts/fmp_client.py check` to test the "
                  "connection and your plan", file=sys.stderr)

    def get(self, endpoint: str, **params):
        """(data, None) or (None, reason)."""
        if self.dead:
            return None, self.dead
        try:
            return self.client.get(endpoint, **params), None
        except fmp_client.InvalidKey:
            self.dead = "FMP key rejected"
            self.notes.append("FMP key rejected: FMP blocks are unavailable")
        except fmp_client.Unreachable as e:
            self.dead = f"FMP unreachable: {e}"
            self.notes.append(self.dead)
        except fmp_client.RestrictedEndpoint:
            label = f"{endpoint} ({params.get('symbol')})" if params.get("symbol") else endpoint
            if label not in self.restricted:
                self.restricted.append(label)
            self._hint()
            return None, f"restricted on the current FMP plan: {endpoint}"
        except fmp_client.FMPError as e:
            msg = f"FMP error on {endpoint}: {e}"
            self.notes.append(msg)
            self._hint()
            return None, msg
        self._hint()
        return None, self.dead

    def describe(self):
        return self.client.describe() if self.client is not None else None

    def calls(self) -> tuple[int, int]:
        if self.client is None:
            return 0, 0
        st = getattr(self.client, "stats", {}) or {}
        return int(st.get("network_calls", 0)), int(st.get("cache_hits", 0))


def closes_from_rows(rows, as_of: date, scale: float = 1.0) -> pd.Series:
    """Daily closes (FMP rows, any order) -> ascending Series up to as_of, weekdays only, one row per date."""
    data = {}
    for r in rows or []:
        try:
            d = pd.Timestamp(str(r["date"])[:10])
            v = float(r["close"])
        except (KeyError, TypeError, ValueError):
            continue
        data[d] = v * scale
    s = pd.Series(data, dtype="float64").sort_index()
    if s.empty:
        return s
    s = s[(s.index <= pd.Timestamp(as_of)) & (s.index.dayofweek < 5)]
    return s[~s.index.duplicated()]


def load_bars(fs: FmpState, symbol: str, as_of: date, scale: float = 1.0):
    """(closes or None, reason)."""
    frm = (as_of - timedelta(days=C.EOD_LOOKBACK_DAYS)).isoformat()
    rows, why = fs.get(FULL, symbol=symbol, **{"from": frm, "to": as_of.isoformat()})
    if rows is None:
        return None, why
    s = closes_from_rows(rows, as_of, scale)
    if s.empty:
        return None, f"no daily bars for {symbol}"
    return s, None


def get_chain_with_fallback(cboe, sym: dict) -> tuple[dict, dict]:
    """(payload, sym): the chain of sym["cboe"]; a plain-letter symbol that CBOE does not know is retried as an
    index ("_" + key) and sym is rewritten (kind index, kind_source cboe_fallback, no FMP symbol)."""
    try:
        return cboe.get_chain(sym["cboe"]), sym
    except NotFound:
        key = sym["key"]
        if sym["kind_source"] == "index_table" or sym["cboe"].startswith("_") or not key.isalpha():
            raise
        try:
            payload = cboe.get_chain("_" + key)
        except NotFound:
            raise NotFound(f"CBOE has no options for {key} (tried {key} and _{key})") from None
    sym = dict(sym, cboe="_" + key, kind="index", kind_source="cboe_fallback", fmp=None, fmp_scale=None,
               fallback=True)
    return payload, sym


def chain_or_reason(cboe, symbol: str):
    """(payload, None) or (None, reason) for an optional chain."""
    try:
        return cboe.get_chain(symbol), None
    except NotFound as e:
        return None, f"not found: {e}"
    except CboeError as e:
        return None, str(e)


def index_or_reason(cboe, index: str, memo: dict):
    if index in memo:
        return memo[index]
    try:
        memo[index] = (cboe.get_index_history(index), None)
    except CboeError as e:
        memo[index] = (None, f"{index} history unavailable: {e}")
    return memo[index]


def load_all(text: str, cboe, fs: FmpState, now=None, market_context: bool = True) -> dict:
    """Everything one ticker analysis needs (see the module docstring)."""
    sym = normalize(text)                                  # raises SymbolRejected / ValueError
    key = sym["key"]
    notes, note_ids = [], []
    profile, profile_reason = None, None
    if sym["kind"] != "index":
        rows, profile_reason = fs.get("profile", symbol=sym["fmp"])
        if rows:
            profile = rows[0] if isinstance(rows, list) else rows
            sym["kind"], sym["kind_source"] = kind_from_profile(key, profile)
        elif rows is not None:
            profile_reason = "FMP has no profile for this symbol"
    payload, sym = get_chain_with_fallback(cboe, sym)
    if sym.get("fallback"):
        notes.append(C.INDEX_FALLBACK_NOTE)
        note_ids.append({"id": "index_fallback", "args": []})
        profile = None
    session, frame, _x, _info = CH.load_payload(payload, key)
    as_of, kind = session["as_of"], sym["kind"]

    out = {"sym": sym, "key": key, "payload": payload, "session": session, "frame": frame, "profile": profile,
           "name": (profile or {}).get("companyName"), "notes": notes, "note_ids": note_ids, "skipped": {}}

    # earnings (stocks), dividends (stocks and ETFs), daily closes
    out["earnings_rows"], out["earnings_reason"] = None, None
    if kind == "stock":
        rows, why = fs.get("earnings", symbol=sym["fmp"])
        out["earnings_rows"], out["earnings_reason"] = (rows or None), why
        if rows is not None and not rows:
            out["earnings_reason"] = "FMP returned no earnings dates"
    out["dividends"], out["dividends_reason"] = [], None
    if kind in ("stock", "etf"):
        rows, why = fs.get("dividends", symbol=sym["fmp"])
        out["dividends_reason"] = why
        ahead = []
        for r in rows or []:
            try:
                d, a = date.fromisoformat(str(r["date"])[:10]), float(r["dividend"])
            except (KeyError, TypeError, ValueError):
                continue
            if d > as_of:
                ahead.append((d, a))
        out["dividends"] = sorted(ahead)
    out["bars"], out["bars_source"], out["bars_proxy"], out["bars_reason"] = None, None, None, None
    if sym["fmp"]:
        bars, why = load_bars(fs, sym["fmp"], as_of, sym["fmp_scale"] or 1.0)
        if bars is not None and sym["fmp"].startswith("^") and sym["fmp"] != C.SPX_FMP:
            ref, _ = load_bars(fs, C.SPX_FMP, as_of)        # drop index rows on non-trading days
            if ref is not None:
                bars = bars[bars.index.isin(ref.index)]
        out["bars"], out["bars_source"], out["bars_reason"] = bars, sym["fmp"], why
    elif key == "NDX":
        bars, why = load_bars(fs, C.NDX_PROXY, as_of)
        if bars is not None and session.get("spot"):
            bars = bars * (float(session["spot"]) / float(bars.iloc[-1]))
        out["bars"], out["bars_source"], out["bars_proxy"], out["bars_reason"] = bars, C.NDX_PROXY, C.NDX_PROXY, why
    else:
        out["bars_reason"] = "no FMP symbol for this index"

    # CBOE volatility index
    memo: dict = {}
    vol_sym = C.VOL_INDEX.get(key)
    out["iv_index_symbol"], out["iv_index"], out["iv_index_reason"] = vol_sym, None, None
    if vol_sym:
        out["iv_index"], out["iv_index_reason"] = index_or_reason(cboe, vol_sym, memo)

    # market context: _SPX and _VIX chains, VIX history
    out["market"] = None
    if market_context:
        mk = {"spx_payload": None, "spx_reason": None, "vix_payload": None, "vix_reason": None}
        if sym["cboe"] == "_SPX":
            mk["spx_payload"] = payload
        else:
            mk["spx_payload"], mk["spx_reason"] = chain_or_reason(cboe, "_SPX")
        mk["vix_payload"], mk["vix_reason"] = chain_or_reason(cboe, "_VIX")
        mk["vix_rows"], mk["vix_rows_reason"] = index_or_reason(cboe, "VIX", memo)
        out["market"] = mk
    out["fmp_profile_reason"] = profile_reason
    return out
