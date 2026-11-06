"""
Option-chain parsing, the exchange calendar and the session rules (plan 4.3-4.4).

- Calendar: trading days (weekends and config.MARKET_HOLIDAYS skipped), 13:00 early closes, busdays().
- session_info(payload): which trading day the data belongs to (as_of), whether that session has closed,
  whether the option quotes come from the overnight (GTH) session, the data time used for T, the spot and
  the previous close (prev_day_close and price_change_percent are unreliable in the feed).
- frame_from_options(rows): one row per contract with the OCC symbol parsed (root, expiration, type,
  strike), numbers coerced, valid-quote / valid-IV flags and the mid.
- prepare(key, session, frame): only the standard roots of the key and only live contracts (settlement
  after the data time), with T (calendar years, measured in UTC), dte, the settlement style and the
  per-expiry surface root; plus the data-quality counts.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config as C
from symbols import roots_for

NY = ZoneInfo(C.MARKET_TZ)
UTC = timezone.utc
OCC = re.compile(r"^(?P<root>.+?)(?P<exp>\d{6})(?P<type>[CP])(?P<strike>\d{8})$")
NUMERIC = ("bid", "ask", "bid_size", "ask_size", "iv", "delta", "gamma", "theta", "vega", "rho", "theo",
           "open_interest", "volume", "last_trade_price", "open", "high", "low", "prev_day_close", "change",
           "percent_change")
_HOLIDAYS = frozenset(C.MARKET_HOLIDAYS)
_HOLIDAYS_NP = np.array(sorted(C.MARKET_HOLIDAYS), dtype="datetime64[D]")


# ---------------------------------------------------------------------------- calendar
def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _HOLIDAYS


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def close_time(d: date) -> dtime:
    return C.EARLY_CLOSES.get(d, C.REGULAR_CLOSE)


def close_dt(d: date) -> datetime:
    """The session close of day d in New York (13:00 on early-close days)."""
    return datetime.combine(d, close_time(d), NY)


def open_dt(d: date) -> datetime:
    return datetime.combine(d, C.REGULAR_OPEN, NY)


def busdays(a: date, b: date) -> int:
    """Trading days d with a <= d < b (like numpy.busday_count with MARKET_HOLIDAYS)."""
    if b <= a:
        return 0
    return int(np.busday_count(np.datetime64(a, "D"), np.datetime64(b, "D"), holidays=_HOLIDAYS_NP))


def to_ny(now=None) -> datetime:
    """`now` as an aware New York datetime: None = the current time; a naive value is taken as New York time."""
    if now is None:
        return datetime.now(NY)
    if callable(now):
        now = now()
    if isinstance(now, pd.Timestamp):
        now = now.to_pydatetime()
    if isinstance(now, date) and not isinstance(now, datetime):
        now = datetime.combine(now, dtime(12, 0))
    return now.replace(tzinfo=NY) if now.tzinfo is None else now.astimezone(NY)


def last_closed_trading_day(now) -> date:
    """The most recent trading day whose close plus SESSION_BUFFER_MIN has passed at `now`."""
    now = to_ny(now)
    d = now.date()
    if is_trading_day(d) and now >= close_dt(d) + timedelta(minutes=C.SESSION_BUFFER_MIN):
        return d
    return prev_trading_day(d)


def stale_info(as_of: date, now) -> dict:
    """{is_stale, business_days_behind}: trading days after as_of whose session had been open for
    SESSION_BUFFER_MIN minutes (09:45 New York) by `now`."""
    now = to_ny(now)
    n, d = 0, as_of
    while True:
        d = next_trading_day(d)
        if d > now.date() or now < open_dt(d) + timedelta(minutes=C.SESSION_BUFFER_MIN):
            break
        n += 1
    return {"is_stale": n >= 1, "business_days_behind": n}


def iso(ts) -> str | None:
    """New York ISO string with offset (2026-11-05T20:40:28-05:00) for an aware datetime."""
    if ts is None:
        return None
    return to_ny(ts).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------- session (4.4)
def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def feed_time(payload: dict) -> datetime:
    """The feed's `timestamp` (UTC, "YYYY-MM-DD HH:MM:SS") in New York time."""
    ts = datetime.fromisoformat(str(payload["timestamp"]).strip().replace("Z", ""))
    ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts
    return ts.astimezone(NY)


def _ny_naive(text) -> datetime | None:
    if not text:
        return None
    try:
        t = datetime.fromisoformat(str(text).strip())
    except ValueError:
        return None
    return t.replace(tzinfo=NY) if t.tzinfo is None else t.astimezone(NY)


def session_info(payload: dict, roots_present) -> dict:
    """The session facts of one CBOE payload (plan 4.4). roots_present: option roots in the chain (for GTH)."""
    d = payload.get("data") or {}
    feed = feed_time(payload)
    ltt = _ny_naive(d.get("last_trade_time"))
    as_of = ltt.date() if ltt else feed.date()
    cdt = close_dt(as_of)
    closed = bool(feed.date() > as_of
                  or feed >= cdt + timedelta(minutes=C.SESSION_BUFFER_MIN)
                  or (ltt is not None and ltt >= cdt - timedelta(seconds=1)))
    gth = bool(set(roots_present or ()) & C.GTH_ROOTS)
    overnight = bool(closed and gth and feed >= datetime.combine(as_of, C.GTH_START, NY))
    close, current = _num(d.get("close")), _num(d.get("current_price"))
    px = close if closed else current
    if px is None:                                   # a feed without one of the two prices
        px = current if closed else close
    change = _num(d.get("price_change"))
    prev_close = px - change if (px is not None and change not in (None, 0.0)) else None
    pdc = _num(d.get("prev_day_close"))
    rolled = bool(prev_close is not None and pdc is not None and abs(pdc - prev_close) > C.PREV_CLOSE_ROLL_TOL)
    iv30 = _num(d.get("iv30"))
    iv30_chg = _num(d.get("iv30_change"))
    return {
        "as_of": as_of, "feed_time": feed, "last_trade_time": ltt, "closed": closed, "gth": gth,
        "overnight_quotes": overnight, "data_dt": feed if (overnight or not closed) else cdt,
        "close": close, "current_price": current, "px": px,
        "spot": px, "spot_basis": "close" if closed else "current_price",
        "price_change": change, "prev_day_close": pdc, "prev_close": prev_close,
        "change_pct": (change / prev_close) if prev_close else None, "prev_close_rolled": rolled,
        "iv30_cboe": iv30 / 100 if iv30 is not None else None,
        "iv30_change_cboe": iv30_chg / 100 if iv30_chg not in (None, 0.0) else None,
        "symbol": d.get("symbol"), "security_type": d.get("security_type"),
    }


def session_from_payload(payload: dict) -> dict:
    """session_info() with the roots read from the option symbols."""
    roots = set()
    for o in (payload.get("data") or {}).get("options") or []:
        m = OCC.match(str(o.get("option", "")))
        if m:
            roots.add(m.group("root"))
    return session_info(payload, roots)


# ---------------------------------------------------------------------------- contracts (4.3)
def frame_from_options(rows) -> pd.DataFrame:
    """One row per contract: OCC fields parsed, numbers coerced, valid_quote / mid / valid_iv / ltt_date.
    `rows` is the feed's data.options list or a DataFrame with the same field names (a stored chain)."""
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    if df.empty:
        df = pd.DataFrame({"option": pd.Series(dtype=str)})
    df = df.reset_index(drop=True)
    occ = df["option"].astype(str).str.extract(OCC)
    df["root"] = occ["root"]
    df["type"] = occ["type"]
    df["strike"] = pd.to_numeric(occ["strike"], errors="coerce") / 1000.0
    df["expiration"] = pd.to_datetime(occ["exp"], format="%y%m%d", errors="coerce").dt.date
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64") if c in df else np.nan
    for c in ("open_interest", "volume"):
        df[c] = df[c].fillna(0.0).round().astype("int64")
    df["valid_quote"] = (df["bid"] > 0) & (df["ask"] > 0) & (df["ask"] >= df["bid"])
    df["mid"] = ((df["bid"] + df["ask"]) / 2.0).where(df["valid_quote"])
    df["valid_iv"] = (df["iv"] > C.IV_MIN) & (df["iv"] < C.IV_MAX)
    ltt = df["last_trade_time"] if "last_trade_time" in df else pd.Series(None, index=df.index, dtype=object)
    df["ltt_date"] = [_ltt_date(v) for v in ltt]
    df["last_trade_time"] = [_ltt_iso(v) for v in ltt]
    return df


def _ltt_date(v):
    if v is None or (isinstance(v, float) and not np.isfinite(v)) or v is pd.NaT:
        return None
    try:
        ts = pd.Timestamp(v)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert(C.MARKET_TZ)
    return ts.date()


def _ltt_iso(v):
    """A contract's last trade time as a New York ISO string with offset, or None."""
    if v is None or v is pd.NaT or (isinstance(v, float) and not np.isfinite(v)):
        return None
    try:
        ts = pd.Timestamp(v)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    ts = ts.tz_localize(C.MARKET_TZ) if ts.tzinfo is None else ts.tz_convert(C.MARKET_TZ)
    return ts.isoformat(timespec="seconds")


def settle_dt(exp: date, root: str) -> datetime:
    """Settlement time: 09:30 New York for AM-settled roots, else the close of the expiry day."""
    if root in C.AM_SETTLED_ROOTS:
        return datetime.combine(exp, C.REGULAR_OPEN, NY)
    return close_dt(exp)


def prepare(key: str, session: dict, frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """(live standard-root contracts with T, dte, settlement, european and surface columns, data-quality info)."""
    roots = roots_for(key)
    pref = {r: i for i, r in enumerate(roots)}
    other = frame.loc[frame["root"].notna() & ~frame["root"].isin(roots), "root"]
    excluded = {str(r): int(n) for r, n in other.value_counts().sort_index().items()}
    x = frame[frame["root"].isin(roots) & frame["expiration"].notna()].copy()
    data_utc = session["data_dt"].astimezone(UTC)
    pairs = x[["expiration", "root"]].drop_duplicates()
    secs = {(e, r): (settle_dt(e, r).astimezone(UTC) - data_utc).total_seconds()
            for e, r in zip(pairs["expiration"], pairs["root"])}
    s = np.array([secs[(e, r)] for e, r in zip(x["expiration"], x["root"])], dtype=float)
    x = x[s > 0].copy()
    s = s[s > 0]
    x["T"] = np.maximum(s, C.MIN_T_SECONDS) / C.YEAR_SECONDS
    as_of = session["as_of"]
    x["dte"] = [(e - as_of).days for e in x["expiration"]]
    x["settlement"] = np.where(x["root"].isin(list(C.AM_SETTLED_ROOTS)), "AM", "PM")
    x["european"] = x["root"].isin(list(C.EUROPEAN_ROOTS))
    if len(x):
        best = x.groupby("expiration")["root"].agg(lambda rs: min(set(rs), key=pref.get))
        x["surface"] = x["root"].to_numpy() == best.reindex(x["expiration"]).to_numpy()
    else:
        x["surface"] = pd.Series(dtype=bool)
    x = x.reset_index(drop=True)
    n = len(x)
    info = {
        "contracts_total": int(len(frame)),
        "contracts_live": int(n),
        "expirations": int(x["expiration"].nunique()) if n else 0,
        "excluded_roots": excluded,
        "valid_quote_share": float(x["valid_quote"].mean()) if n else None,
        "valid_iv_share": float(x["valid_iv"].mean()) if n else None,
        "crossed_quotes": int(((x["bid"] > 0) & (x["ask"] > 0) & (x["ask"] < x["bid"])).sum()),
        "iv_outliers": int(((x["iv"] > 0) & ~x["valid_iv"]).sum()),   # iv == 0 means "no IV", not an outlier
    }
    return x, info


def load_payload(payload: dict, key: str) -> tuple[dict, pd.DataFrame, pd.DataFrame, dict]:
    """(session, all contracts, live standard-root contracts, info) for one CBOE payload."""
    frame = frame_from_options((payload.get("data") or {}).get("options") or [])
    session = session_info(payload, set(frame["root"].dropna()))
    session["key"] = key
    x, info = prepare(key, session, frame)
    return session, frame, x, info
