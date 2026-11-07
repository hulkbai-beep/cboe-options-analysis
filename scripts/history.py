"""
The history store ($OA_DATA_DIR, default ~/options-data; plan 3.5):

  tickers.txt                      symbols snapshot.py saves (written from config.SNAPSHOT_TICKERS when missing)
  chains/YYYY-MM-DD/<key>.parquet  the whole chain of one trading day, in the prototype's schema
  summary/<key>.parquet            one row of derived numbers per trading day (summary_row)
  raw/YYYY-MM-DD/<key>.json.gz     optional raw feed (snapshot.py --raw)
  logs/snapshot.log                snapshot.py's log

CBOE has no history, so IV Rank, the skew percentile and open-interest changes build up from these files.
The date in a file name is the trading day the data belongs to (chain.session_info as_of), never the run date.
Overnight (GTH) versions are never stored.
"""
from __future__ import annotations

import gzip
import math
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import config as C
import chain as CH

FLOAT_FIELDS = ["bid", "ask", "last_trade_price", "iv", "delta", "gamma", "theta", "vega", "rho",
                "theo", "open", "high", "low", "prev_day_close", "change", "percent_change"]
INT_FIELDS = ["bid_size", "ask_size", "volume", "open_interest"]
SUMMARY_INT = ("total_oi", "total_volume", "contracts_live", "summary_version")
SUMMARY_BOOL = ("closed", "late_feed")
_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------- folders and files
def data_dir(path=None) -> Path:
    """--data-dir, else $OA_DATA_DIR, else ~/options-data."""
    if path:
        return Path(path).expanduser()
    env = os.environ.get(C.DATA_DIR_ENV)
    return Path(env).expanduser() if env else Path.home() / C.DATA_DIR_DEFAULT


def chain_path(root: Path, key: str, as_of: date) -> Path:
    return Path(root) / "chains" / as_of.isoformat() / f"{key}.parquet"


def summary_path(root: Path, key: str) -> Path:
    return Path(root) / "summary" / f"{key}.parquet"


def write_atomic(path: Path, write) -> None:
    """write(tmp_path) then os.replace onto path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        write(Path(tmp))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def chain_dates(root: Path, key: str) -> list[date]:
    """Trading days with a stored chain for key, ascending."""
    base = Path(root) / "chains"
    if not base.is_dir():
        return []
    out = []
    for d in base.iterdir():
        if d.is_dir() and _DATE_DIR.match(d.name) and (d / f"{key}.parquet").is_file():
            out.append(date.fromisoformat(d.name))
    return sorted(out)


def stored_keys(root: Path) -> list[str]:
    base = Path(root) / "chains"
    keys = set()
    if base.is_dir():
        for d in base.iterdir():
            if d.is_dir() and _DATE_DIR.match(d.name):
                keys.update(p.stem for p in d.glob("*.parquet"))
    return sorted(keys)


# ---------------------------------------------------------------------------- chains (prototype schema)
def chain_frame(key: str, payload: dict, session: dict) -> pd.DataFrame:
    """One row per contract in the prototype's column order (new CBOE fields are appended)."""
    d = payload["data"]
    raw = pd.DataFrame(d.get("options") or [])
    if raw.empty:
        raw = pd.DataFrame({"option": pd.Series(dtype=str)})

    def col(c):
        return raw[c] if c in raw else pd.Series(float("nan"), index=raw.index)

    def num(v):
        try:
            return float(v) if v is not None else float("nan")
        except (TypeError, ValueError):
            return float("nan")

    occ = raw["option"].astype(str).str.extract(CH.OCC)
    expiration = pd.to_datetime(occ["exp"], format="%y%m%d", errors="coerce")
    trade_date = session["as_of"]
    df = pd.DataFrame({
        "trade_date": [trade_date] * len(raw),
        "snapshot_time": pd.Series([pd.Timestamp(session["feed_time"])] * len(raw), index=raw.index,
                                   dtype=f"datetime64[ns, {C.MARKET_TZ}]"),
        "underlying": key,
        "underlying_price": num(d.get("current_price")),
        "underlying_close": num(d.get("close")),
        "underlying_iv30": num(d.get("iv30")),            # percent, as in the prototype
        "option": raw["option"],
        "root": occ["root"],
        "expiration": expiration.dt.date,
        "dte": (expiration - pd.Timestamp(trade_date)).dt.days.astype("Int64"),
        "type": occ["type"],
        "strike": pd.to_numeric(occ["strike"], errors="coerce") / 1000,
    }, index=raw.index)
    for c in FLOAT_FIELDS:
        df[c] = pd.to_numeric(col(c), errors="coerce").astype("float64")
    for c in INT_FIELDS:
        df[c] = pd.to_numeric(col(c), errors="coerce").round().astype("Int64")
    df["mid"] = ((df["bid"] + df["ask"]) / 2).where((df["bid"] > 0) & (df["ask"] > 0))
    ltt = pd.to_datetime(col("last_trade_time"), errors="coerce")
    if getattr(ltt.dt, "tz", None) is None:
        ltt = ltt.dt.tz_localize(C.MARKET_TZ, ambiguous="NaT", nonexistent="NaT")
    df["last_trade_time"] = ltt
    df["tick"] = col("tick").astype("string")
    extra = [c for c in raw.columns if c not in df.columns]
    for c in extra:
        df[c] = raw[c]
    return df[list(C.CHAIN_COLUMNS) + extra]


def save_chain(root: Path, key: str, payload: dict, session: dict, force: bool = False,
               raw_bytes: bytes | None = None) -> dict:
    """Store one chain (and raw/YYYY-MM-DD/<key>.json.gz when raw_bytes, the feed's JSON, plain or gzip, is
    given). Returns {saved, path, reason, contracts, expirations}; overnight (GTH) versions are never stored."""
    path = chain_path(root, key, session["as_of"])
    if session.get("overnight_quotes"):
        return {"saved": False, "path": str(path), "reason": "overnight (GTH) quotes are not stored"}
    if path.exists() and not force:
        return {"saved": False, "path": str(path), "reason": f"{session['as_of']} already saved"}
    df = chain_frame(key, payload, session)
    write_atomic(path, lambda p: df.to_parquet(p, index=False, compression="zstd"))
    if raw_bytes is not None:
        rp = Path(root) / "raw" / session["as_of"].isoformat() / f"{key}.json.gz"
        data = raw_bytes if raw_bytes[:2] == b"\x1f\x8b" else gzip.compress(raw_bytes)
        write_atomic(rp, lambda p: p.write_bytes(data))
    return {"saved": True, "path": str(path), "reason": None, "contracts": int(len(df)),
            "expirations": int(df["expiration"].nunique())}


def _as_date(v) -> date:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    return pd.Timestamp(v).date()


def load_chain(path) -> tuple[dict, pd.DataFrame]:
    """(session, contracts) rebuilt from a stored chain: as_of = trade_date, closed, data time = that day's close,
    spot = underlying_close, iv30 = underlying_iv30 / 100; valid quotes and mid recomputed from bid / ask."""
    df = pd.read_parquet(path)
    if df.empty:
        raise ValueError(f"{path}: empty chain file")
    as_of = _as_date(df["trade_date"].iloc[0])
    snap = pd.Timestamp(df["snapshot_time"].iloc[0])
    snap = (snap.tz_localize(C.MARKET_TZ) if snap.tzinfo is None else snap.tz_convert(C.MARKET_TZ)).to_pydatetime()
    close = float(df["underlying_close"].iloc[0])
    price = df["underlying_price"].iloc[0]
    iv30 = df["underlying_iv30"].iloc[0]
    frame = CH.frame_from_options(df)
    roots = set(frame["root"].dropna())
    session = {
        "as_of": as_of, "feed_time": snap, "last_trade_time": None, "closed": True,
        "gth": bool(roots & C.GTH_ROOTS), "overnight_quotes": False, "data_dt": CH.close_dt(as_of),
        "close": close, "current_price": float(price) if pd.notna(price) else None, "px": close,
        "spot": close, "spot_basis": "close", "price_change": None, "prev_day_close": None, "prev_close": None,
        "change_pct": None, "prev_close_rolled": False,
        "iv30_cboe": float(iv30) / 100 if pd.notna(iv30) else None, "iv30_change_cboe": None,
        "symbol": str(df["underlying"].iloc[0]), "security_type": None,
    }
    return session, frame


# ---------------------------------------------------------------------------- summary rows
def _nan(v):
    if v is None:
        return float("nan")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return f if math.isfinite(f) else float("nan")


def summary_from_ctx(ctx: dict) -> dict:
    """The summary row of an analysis whose chain modules (core.run_chain_modules) have run."""
    import modules as M
    s, mods, kind = ctx["session"], ctx["mods"], ctx["kind"]
    ts = mods.get("term_structure") or {}
    cm = ts.get("cm_iv") or {} if ts.get("available") else {}
    sk, pos, gm = mods.get("skew") or {}, mods.get("positioning") or {}, mods.get("gamma") or {}
    em = mods.get("expected_move") or {}
    row30 = next((r for r in em.get("rows") or [] if "30d" in r.get("tags", [])), None)
    eiw = M.event_in_window(kind, ctx.get("earnings_date"), ctx.get("earnings_known"), ctx["as_of"])
    move = None
    if eiw:
        move = M.event_move(ctx).get("move_1sd")
    exe = M.ex_event_iv(cm.get("30d"), eiw, move)
    x = ctx["x"]
    feed = s["feed_time"]
    pcr = (pos.get("pcr") or {}).get("all") or {}
    row = {
        "trade_date": ctx["as_of"], "spot": _nan(s.get("spot")), "iv30_cboe": _nan(s.get("iv30_cboe")),
        **{f"cm_iv_{d}d": _nan(cm.get(f"{d}d")) for d in C.CM_TENORS},
        "cm_iv_30d_ex_event": _nan(exe), "ratio_30_90": _nan(ts.get("ratio_30_90")),
        "rr25_30d": _nan(sk.get("rr25_30d")), "bf25_30d": _nan(sk.get("bf25_30d")),
        "straddle_30d_pct": _nan(row30.get("straddle_pct") if row30 else None),
        "pcr_volume": _nan(pcr.get("volume")), "pcr_oi": _nan(pcr.get("oi")),
        "total_oi": int(x["open_interest"].sum()) if len(x) else 0,
        "total_volume": int(x["volume"].sum()) if len(x) else 0,
        "net_gex_1pct": _nan(gm.get("net_gex_1pct")), "zero_gamma": _nan(gm.get("zero_gamma")),
        "contracts_live": int(ctx["info"]["contracts_live"]),
        "feed_time": CH.iso(feed), "closed": bool(s.get("closed")),
        "late_feed": bool(CH.to_ny(feed) >= datetime.combine(ctx["as_of"], C.GTH_START, CH.NY)),
        "summary_version": C.SUMMARY_VERSION,
    }
    return {k: row[k] for k in C.SUMMARY_COLUMNS}


def summary_row(session: dict, frame: pd.DataFrame, kind: str, earnings_date=None, key: str | None = None,
                rates=None) -> dict:
    """One summary row from a session and its contracts (the same call in snapshot.py, run_analysis and
    --rebuild-summary; `rates` discounts European straddles as in surface.build)."""
    import core
    key = key or str(session.get("key") or session.get("symbol") or "").lstrip("^_")
    ctx = core.chain_context(key, kind, session, frame, earnings_date, rates=rates)
    core.run_chain_modules(ctx)
    return summary_from_ctx(ctx)


def summary_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(C.SUMMARY_COLUMNS))
    df["trade_date"] = [_as_date(v) for v in df["trade_date"]]
    for c in C.SUMMARY_COLUMNS:
        if c in ("trade_date",):
            continue
        if c == "feed_time":
            df[c] = df[c].astype("string").astype(object)
        elif c in SUMMARY_INT:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
        elif c in SUMMARY_BOOL:
            df[c] = df[c].astype(bool)
        else:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    return df.sort_values("trade_date").reset_index(drop=True)


def _write_summary(path: Path, df: pd.DataFrame) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    fields = []
    for c in C.SUMMARY_COLUMNS:
        if c == "trade_date":
            fields.append(pa.field(c, pa.date32()))
        elif c == "feed_time":
            fields.append(pa.field(c, pa.string()))
        elif c in SUMMARY_INT:
            fields.append(pa.field(c, pa.int64()))
        elif c in SUMMARY_BOOL:
            fields.append(pa.field(c, pa.bool_()))
        else:
            fields.append(pa.field(c, pa.float64()))
    table = pa.Table.from_pandas(df[list(C.SUMMARY_COLUMNS)], schema=pa.schema(fields), preserve_index=False)
    write_atomic(path, lambda p: pq.write_table(table, p, compression="zstd"))


def read_summary(root: Path, key: str) -> pd.DataFrame | None:
    p = summary_path(root, key)
    if not p.is_file():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:                                      # a damaged file must not stop an analysis
        return None
    df["trade_date"] = [_as_date(v) for v in df["trade_date"]]
    return df.sort_values("trade_date").reset_index(drop=True)


def append_summary(root: Path, key: str, row: dict, force: bool = False) -> bool:
    """Add (or with force replace) the row of row['trade_date']; True when the file changed."""
    old = read_summary(root, key)
    rows = [] if old is None else old.to_dict("records")
    td = row["trade_date"]
    if any(_as_date(r["trade_date"]) == td for r in rows):
        if not force:
            return False
        rows = [r for r in rows if _as_date(r["trade_date"]) != td]
    rows.append(row)
    _write_summary(summary_path(root, key), summary_frame(rows))
    return True


def write_summary(root: Path, key: str, rows: list[dict]) -> None:
    _write_summary(summary_path(root, key), summary_frame(rows))


# ---------------------------------------------------------------------------- ranks (5.5)
def rank_window(df: pd.DataFrame | None, col: str, as_of: date, x):
    """(dates, values) of W = the last IVR_LOOKBACK - 1 non-null rows before as_of plus today's x."""
    if df is None or col not in getattr(df, "columns", ()):
        h = pd.DataFrame({"trade_date": [], col: []})
    else:
        h = df[[d < as_of for d in df["trade_date"]]]
        h = h[h[col].notna()].tail(C.IVR_LOOKBACK - 1)
    dates = list(h["trade_date"]) + [as_of]
    vals = np.append(h[col].to_numpy(dtype=float), float(x))
    return dates, vals


def rank_pctl(df, col: str, as_of: date, x) -> float | None:
    """Percentile (0-100, share of the window strictly below x) once the window has IVR_MIN_OBS values."""
    if x is None or not np.isfinite(x):
        return None
    _, w = rank_window(df, col, as_of, x)
    if len(w) < C.IVR_MIN_OBS:
        return None
    return float(100.0 * (w < x).sum() / len(w))


def rank_block(df, col: str, as_of: date, x) -> dict:
    """IV Rank and percentile of x over the history window (plan 5.5)."""
    from modules import block, unavailable
    if x is None or not np.isfinite(x):
        return unavailable("no current value")
    dates, w = rank_window(df, col, as_of, x)
    n = len(w)
    if n < C.IVR_MIN_OBS:
        return unavailable(C.REASONS["ivr_building"].format(n, C.IVR_MIN_OBS), "ivr_building", n_obs=n,
                           need=C.IVR_MIN_OBS)
    lo, hi = float(w.min()), float(w.max())
    return block(value=(x - lo) / (hi - lo) * 100.0 if hi > lo else None, pctl=float(100.0 * (w < x).sum() / n),
                 n_obs=n, first_date=dates[0], last_date=dates[-1],
                 series=[[d, float(v)] for d, v in zip(dates, w)],
                 window_label="1y" if n >= C.IVR_LOOKBACK else "recent")


# ---------------------------------------------------------------------------- the view an analysis reads
class HistoryView:
    """Read access to the history of one key for run_analysis / run_market."""

    def __init__(self, root: Path, key: str, as_of: date):
        self.root, self.key, self.as_of = Path(root), key, as_of
        self.summary = read_summary(self.root, key)

    @property
    def days(self) -> int:
        return 0 if self.summary is None else int(len(self.summary))

    def first_last(self):
        if self.summary is None or self.summary.empty:
            return None, None
        return self.summary["trade_date"].iloc[0], self.summary["trade_date"].iloc[-1]

    def previous(self, col: str):
        """(trade_date, value) of the latest row before as_of with a value."""
        if self.summary is None:
            return None
        h = self.summary[[d < self.as_of for d in self.summary["trade_date"]]]
        h = h[h[col].notna()] if col in h else h.iloc[0:0]
        if h.empty:
            return None
        return h["trade_date"].iloc[-1], float(h[col].iloc[-1])

    def iv_rank(self, col: str, x) -> dict:
        return rank_block(self.summary, col, self.as_of, x)

    def pctl(self, col: str, x):
        return rank_pctl(self.summary, col, self.as_of, x)

    def oi_change(self, ctx: dict) -> dict:
        """Open-interest change against the latest stored chain before as_of (plan 5.7)."""
        from modules import add_note, block, unavailable
        dates = [d for d in chain_dates(self.root, self.key) if d < self.as_of]
        if not dates:
            return unavailable(f"no stored chain before {self.as_of}")
        base_date = dates[-1]
        try:
            _, base = load_chain(chain_path(self.root, self.key, base_date))
        except Exception as e:                             # a damaged file: say so, keep the analysis
            return unavailable(f"base chain {base_date} unreadable ({type(e).__name__})")
        x = ctx["x"]
        b = base[base["root"].isin(set(x["root"]))].groupby("option")["open_interest"].sum()
        cur = x[["option", "type", "strike", "open_interest"]].copy()
        cur["base_oi"] = cur["option"].map(b).fillna(0).astype("int64")
        cur["oi_change"] = cur["open_interest"] - cur["base_oi"]
        agg = cur.groupby(["type", "strike"])[["oi_change", "open_interest", "base_oi"]].sum().reset_index()

        def items(df):
            return [{"type": r.type, "strike": float(r.strike), "oi_change": int(r.oi_change),
                     "oi": int(r.open_interest), "base_oi": int(r.base_oi)} for r in df.itertuples()]

        inc = agg[agg["oi_change"] > 0].sort_values(["oi_change", "strike"], ascending=[False, True]).head(C.OI_CHANGE_TOP)
        dec = agg[agg["oi_change"] < 0].sort_values(["oi_change", "strike"], ascending=[True, True]).head(C.OI_CHANGE_TOP)
        out = block(base_date=base_date, top_increases=items(inc), top_decreases=items(dec),
                    net_change=int(cur["oi_change"].sum()))
        gap = (self.as_of - base_date).days
        if gap > C.OI_BASE_MAX_GAP_DAYS:
            add_note(out, nid="oi_base_gap", args=(base_date, gap))
        add_note(out, "a snapshot's open interest is the previous trading day's close: the change between two "
                      "snapshots happened on the earlier trading day")
        return out


def history_block(root: Path | None, key: str, view: HistoryView | None, saved: dict | None, row: dict | None,
                  reason: str | None = None) -> dict:
    """metrics.history for the ticker mode."""
    from modules import block, unavailable
    if root is None:
        return unavailable(reason or "history store not used", data_dir=None, days=0, saved_this_run=False)
    view = view or HistoryView(root, key, date.today())
    first, last = view.first_last()
    out = block(data_dir=str(root), days=view.days, first_date=first, last_date=last,
                saved_this_run=bool(saved and saved.get("saved")), summary_row=row)
    if saved and not saved.get("saved") and saved.get("reason"):
        from modules import add_note
        add_note(out, f"chain not saved: {saved['reason']}")
    return out


# ---------------------------------------------------------------------------- tickers.txt
def read_tickers(path: Path) -> list[str]:
    tickers = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        tickers += line.split("#", 1)[0].replace(",", " ").split()
    return tickers


def ensure_tickers_file(root: Path) -> Path:
    p = Path(root) / "tickers.txt"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# one or more symbols per line; # starts a comment\n" + "\n".join(C.SNAPSHOT_TICKERS) + "\n",
                     encoding="utf-8")
    return p
