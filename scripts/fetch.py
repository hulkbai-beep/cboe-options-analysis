"""
Data loading for one analysis (plan 3.4 step 2).

load_all(text, cboe, fmp, now=None, market_context=True, ms=None) -> dict with the symbol info (symbols.normalize,
with the "_" fallback for an index that is not in config.INDEX_SYMBOLS), the CBOE payload and its session, and the
optional pieces: FMP profile, earnings, dividends and daily closes, the CBOE volatility-index history, the _SPX /
_VIX chains and VIX history for the market context, and from Massive the Treasury curve that discounts European
index options, short interest, short sale volume, free float, news, dividends when FMP gives no answer, and the
exchange holiday calendar.

FMP and Massive are optional: every request goes through FmpState.get() or MassiveState.results(), which turn a
missing key, a rejected key, an unreachable host, a plan restriction or (Massive) the rate limit into a reason
string (and meta.restricted / meta.notes), never into an exception. CBOE errors on the main chain propagate
(NotFound, CboeUnreachable, CboeError). Massive requests go in order of importance (the discount curve first, the
holiday calendar last and only when a request slot is free at once), because the free plan allows 5 a minute.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pandas as pd

import config as C
import chain as CH
import fmp_client
import massive_client as MC
import surface as SF
from cboe_client import CboeError, NotFound
from symbols import kind_from_profile, normalize

FULL = "historical-price-eod/full"
FMP_NOT_CONFIGURED = "FMP not configured"
MASSIVE_NOT_CONFIGURED = "Massive not configured"
STOCK_DATA_ONLY = "applies to stocks and ETFs"
RATES_NOT_NEEDED = "not needed: no European-style options in this run"


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


def make_massive(refresh: bool = False):
    """(MassiveClient or None, reason): a client only when MASSIVE_API_KEY is set and the client starts."""
    if not os.environ.get("MASSIVE_API_KEY", "").strip():
        return None, MASSIVE_NOT_CONFIGURED
    try:
        return MC.MassiveClient(refresh=refresh), None
    except MC.InvalidKey:
        return None, "Massive key rejected"
    except MC.MassiveError as e:
        return None, f"Massive error: {e}"


class MassiveState:
    """Optional Massive requests with reasons instead of exceptions (the same contract as FmpState)."""

    def __init__(self, client=None, reason: str | None = None):
        self.client = client
        self.dead = None if client is not None else (reason or MASSIVE_NOT_CONFIGURED)
        self.restricted: list[str] = []
        self.notes: list[str] = []
        self._hinted = False

    @property
    def configured(self) -> bool:
        return self.client is not None

    def _hint(self):
        if not self._hinted:
            self._hinted = True
            print("[massive] some Massive data is unavailable; run `python scripts/massive_client.py check` to test "
                  "the connection and your plan", file=sys.stderr)

    def results(self, path: str, wait=None, **params):
        """(rows, None) or (None, reason)."""
        if self.dead:
            return None, self.dead
        try:
            return self.client.results(path, wait=wait, **params), None
        except MC.InvalidKey:
            self.dead = "Massive key rejected"
            self.notes.append("Massive key rejected: Massive data is unavailable")
        except MC.Unreachable as e:
            self.dead = f"Massive unreachable: {e}"
            self.notes.append(self.dead)
        except MC.RestrictedEndpoint:
            label = f"Massive {path} ({params.get('ticker')})" if params.get("ticker") else f"Massive {path}"
            if label not in self.restricted:
                self.restricted.append(label)
            self._hint()
            return None, f"restricted on the current Massive plan: {path}"
        except MC.RateLimited as e:
            return None, f"skipped for Massive's rate limit: {e}"
        except MC.ServerTrouble as e:
            if str(e) not in self.notes:
                self.notes.append(str(e))
            return None, str(e)
        except MC.MassiveError as e:
            msg = f"Massive error on {path}: {e}"
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


# ---------------------------------------------------------------------------- Treasury discount curve (Massive)
def rates_cutoff(as_of: date) -> date:
    """The latest curve date that may discount as_of: RATES_LAG_DAYS trading days before it. The Federal Reserve's
    H.15 posts a day's Treasury yields on the next business day (about 16:15 New York), so the curve of this date
    is published before any snapshot or analysis of as_of runs, and a live run and --rebuild-summary pick the same
    curve for the same day."""
    d = as_of
    for _ in range(C.RATES_LAG_DAYS):
        d = CH.prev_trading_day(d)
    return d


def curve_from_rows(rows, as_of: date):
    """(RateCurve, None) from the latest Treasury row dated on or before rates_cutoff(as_of) and at most
    RATES_MAX_AGE_DAYS before as_of, else (None, reason)."""
    cutoff, best = rates_cutoff(as_of), None
    for r in rows or []:
        try:
            d = date.fromisoformat(str(r.get("date"))[:10])
        except (AttributeError, ValueError):
            continue
        if d <= cutoff and (best is None or d > best[0]):
            best = (d, r)
    if best is None:
        return None, f"no Treasury curve on or before {cutoff}"
    if (as_of - best[0]).days > C.RATES_MAX_AGE_DAYS:
        return None, (f"the latest Treasury curve is from {best[0]}, more than {C.RATES_MAX_AGE_DAYS} days before "
                      f"{as_of}")
    curve = SF.RateCurve.from_treasury(best[1])
    return (curve, None) if curve is not None else (None, f"the Treasury curve of {best[0]} has no yields")


def load_rates(ms: MassiveState, as_of: date):
    """(RateCurve or None, reason): the Treasury curve for as_of (the latest one up to rates_cutoff(as_of))."""
    rows, why = ms.results(MC.TREASURY, **{"date.lte": rates_cutoff(as_of).isoformat()}, sort="date.desc", limit=1)
    if rows is None:
        return None, why
    return curve_from_rows(rows, as_of)


def load_rate_history(ms: MassiveState, start: date, end: date):
    """(rows, None), (rows, reason) when some years failed, or (None, reason) when all did: the Treasury rows from
    RATES_MAX_AGE_DAYS before `start` through `end`, one request per calendar year (for --rebuild-summary). Days whose
    year failed are discounted at the fixed rate; the others keep their curve."""
    lo, rows, failed = start - timedelta(days=C.RATES_MAX_AGE_DAYS), [], []
    for year in range(lo.year, end.year + 1):
        a, b = max(lo, date(year, 1, 1)), min(end, date(year, 12, 31))
        got, why = ms.results(MC.TREASURY, **{"date.gte": a.isoformat(), "date.lte": b.isoformat()}, sort="date.asc",
                              limit=400)
        if got is None:
            failed.append(f"{year}: {why}")
            continue
        rows += got
    why = "; ".join(failed) or None
    return (None, why) if failed and not rows else (rows, why)


def rates_block(curve, reason: str | None, european: bool) -> dict:
    """snapshot.rates: where the discount rates of European straddles came from (also read when the ticker has
    none: the SPX of the market context is European)."""
    from modules import add_note, block
    if curve is not None:
        out = block(source="treasury", european=european, curve_date=curve.curve_date,
                    yields={k.replace("yield_", ""): v for k, v in curve.yields.items()},
                    rate_30d=curve.rate(30 / 365), rate_1y=curve.rate(1.0), fixed_rate=C.DISCOUNT_RATE)
    else:
        out = block(source="fixed", european=european, curve_date=None, yields=None, rate_30d=C.DISCOUNT_RATE,
                    rate_1y=C.DISCOUNT_RATE, fixed_rate=C.DISCOUNT_RATE, reason=reason)
        if european:
            add_note(out, nid="rates_fixed", args=(f"{C.DISCOUNT_RATE:.0%}", reason or MASSIVE_NOT_CONFIGURED))
    return out


def massive_dividends(ms: MassiveState, key: str, as_of: date):
    """(rows [(ex_date, amount)] after as_of, None) or (None, reason): cash dividends from Massive."""
    rows, why = ms.results(MC.DIVIDENDS, ticker=key, **{"ex_dividend_date.gt": as_of.isoformat()},
                           sort="ex_dividend_date.asc", limit=10)
    if rows is None:
        return None, why
    ahead = []
    for r in rows:
        try:
            d, a = date.fromisoformat(str(r["ex_dividend_date"])[:10]), float(r["cash_amount"])
        except (KeyError, TypeError, ValueError):
            continue
        if d > as_of and a > 0 and str(r.get("currency") or "USD").upper() == "USD":
            ahead.append((d, a))
    return sorted(ahead), None


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


def load_all(text: str, cboe, fs: FmpState, now=None, market_context: bool = True, ms: MassiveState | None = None) -> dict:
    """Everything one ticker analysis needs (see the module docstring)."""
    ms = ms if ms is not None else MassiveState(None)
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
    out["dividends"], out["dividends_reason"], out["dividends_source"] = [], None, None
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
        out["dividends_source"] = "fmp" if rows is not None else None
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
    load_massive(out, ms, market_context, european=bool(len(_x)) and bool(_x["european"].any()))
    return out


def load_massive(out: dict, ms: MassiveState, market_context: bool, european: bool = False) -> None:
    """The Massive pieces of one ticker analysis into `out` (each as rows or None with a reason), in order of
    importance: the discount curve (when the chain has European-style options, or the market context has an SPX
    chain), short interest, short sale volume, free float (stocks), news, dividends when FMP gave no answer, and
    the holiday calendar (only when a request slot is free at once; it is cached for a day)."""
    key, kind, as_of = out["key"], out["sym"]["kind"], out["session"]["as_of"]
    out["rates"], out["rates_reason"] = None, RATES_NOT_NEEDED
    if european or (market_context and (out.get("market") or {}).get("spx_payload") is not None):
        out["rates"], out["rates_reason"] = load_rates(ms, as_of)
    stock_data = kind in ("stock", "etf")
    for name in ("short_interest", "short_volume", "float", "news"):
        out[name], out[f"{name}_reason"] = None, (STOCK_DATA_ONLY if not stock_data else None)
    out["news_from"] = None
    if stock_data:
        out["short_interest"], out["short_interest_reason"] = ms.results(
            MC.SHORT_INTEREST, ticker=key, sort="settlement_date.desc", limit=C.SHORT_INTEREST_ROWS)
        out["short_volume"], out["short_volume_reason"] = ms.results(
            MC.SHORT_VOLUME, ticker=key, sort="date.desc", limit=C.SHORT_VOLUME_DAYS)
        if kind == "stock":
            out["float"], out["float_reason"] = ms.results(MC.FLOAT, ticker=key)
        else:
            out["float_reason"] = "ETFs have no free float"
        frm = f"{(as_of - timedelta(days=C.NEWS_LOOKBACK_DAYS)).isoformat()}T00:00:00Z"
        out["news_from"] = frm
        out["news"], out["news_reason"] = ms.results(MC.NEWS, ticker=key, **{"published_utc.gte": frm}, order="desc",
                                                     sort="published_utc", limit=C.NEWS_FETCH)
        if out["dividends_source"] is None and out.get("dividends_reason") and ms.configured:
            divs, why = massive_dividends(ms, key, as_of)
            if divs is not None:
                out["dividends"], out["dividends_source"] = divs, "massive"
            else:
                out["dividends_reason"] = f"{out['dividends_reason']}; Massive: {why}"
    out["holidays"], out["holidays_reason"] = ms.results(MC.HOLIDAYS, wait=0)
