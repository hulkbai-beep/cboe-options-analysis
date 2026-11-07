#!/usr/bin/env python3
"""
Daily snapshot of option chains into the history store (plan 3.5): the whole chain of each symbol as parquet (the
prototype's schema) plus one summary row per trading day, so IV Rank, the skew percentile and open-interest
changes can build up. CBOE keeps no history: a trading day that is not saved cannot be filled in later.

  python scripts/snapshot.py [TICKER ...] [-f FILE] [--data-dir DIR] [--force] [--raw] [--sleep SECONDS]
  python scripts/snapshot.py --rebuild-summary [TICKER ...] [--data-dir DIR]

  TICKER             symbols to save, e.g. SPY QQQ SPX VIX BRK.B (default: <data-dir>/tickers.txt, written from
                     the built-in list the first time)
  -f, --tickers-file one or more symbols per line; # starts a comment
  --data-dir DIR     history store (default $OA_DATA_DIR, else ~/options-data); -o / --out are aliases
  --force            overwrite a saved day and save even while the market is open (overnight quotes never)
  --raw              also keep the gzipped raw feed under raw/YYYY-MM-DD/ (roughly doubles disk use)
  --sleep SECONDS    at least this long between two CBOE requests (default 1)
  --rebuild-summary  rebuild summary/<key>.parquet from the stored chains (every key, or the ones given)

Run it once per trading day between 16:30 and 20:00 New York time: SPX, XSP and VIX options trade overnight from
20:15, and an overnight version is never stored. Files are named after the trading day the data belongs to, so
extra runs (weekends, holidays, retries) skip what is already saved. VIX is stored as a chain only (its options
settle to VIX futures). With FMP_API_KEY set, the FMP profile decides stock or ETF and the next earnings date
gives the ex-event 30-day IV of stocks; without it that column stays empty. With MASSIVE_API_KEY set, European
index options (SPX, NDX, RUT, XSP) are discounted with the Treasury curve of the second business day before the data
date, counting days when both the NYSE and the Federal Reserve Board are open (fetch.rates_cutoff: always published
by then), as in an analysis and in --rebuild-summary; without it at the fixed DISCOUNT_RATE.

Every run is logged to <data-dir>/logs/snapshot.log as well as the console.
Exit codes: 0 done; 1 at least one symbol failed (the others are still saved); 2 bad arguments.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import config as C
import chain as CH
import fetch
import history as H
import modules as M
from cboe_client import CboeClient, CboeError, NotFound
from symbols import kind_from_profile, normalize

log = logging.getLogger("oa.snapshot")
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"


# ---------------------------------------------------------------------------- kind and earnings (FMP optional)
class SymbolFacts:
    """kind (4.1) and the FMP earnings rows of each symbol, asked once per run, and the Treasury discount curve of
    each trading day (Massive, optional)."""

    def __init__(self, fs: fetch.FmpState, ms: fetch.MassiveState | None = None):
        self.fs = fs
        self.ms = ms if ms is not None else fetch.MassiveState(None)
        self._earnings: dict[str, tuple] = {}
        self._curves: dict = {}                  # as_of -> (RateCurve or None, reason)
        self._history = None                     # (start, end, rows or None, {failed year: reason}) after preload_rates
        self._logged: set = set()

    def preload_rates(self, start, end) -> None:
        """Read the Treasury rows of [start, end] in a few requests (for --rebuild-summary)."""
        if self._history is not None and self._history[0] <= start and end <= self._history[1]:
            return
        if self._history is not None:
            start, end = min(start, self._history[0]), max(end, self._history[1])
        rows, failed = fetch.load_rate_history(self.ms, start, end)
        self._history = (start, end, rows, failed)
        self._curves.clear()

    def rates(self, as_of):
        """The RateCurve that discounts European straddles on as_of, or None (the fixed DISCOUNT_RATE)."""
        if as_of not in self._curves:
            h = self._history
            if h is not None and h[0] <= as_of <= h[1]:
                if h[2] is None:                                         # no year of the history could be read
                    self._curves[as_of] = (None, "; ".join(f"{y}: {why}" for y, why in h[3].items()))
                else:
                    self._curves[as_of] = fetch.curve_from_rows(h[2], as_of, h[3])
            else:
                self._curves[as_of] = fetch.load_rates(self.ms, as_of)
            curve, why = self._curves[as_of]
            label = f"Treasury curve of {curve.curve_date}" if curve is not None else f"fixed rate ({why})"
            if label not in self._logged:
                self._logged.add(label)
                if curve is not None:
                    log.info("European index options discounted with the %s", label)
                else:
                    log.info("European index options discounted at the fixed %.0f%% rate: %s", C.DISCOUNT_RATE * 100,
                             why)
        return self._curves[as_of][0]

    def kind(self, sym: dict) -> str:
        if sym["kind"] == "index" or not sym.get("fmp"):
            return sym["kind"]
        rows, _why = self.fs.get("profile", symbol=sym["fmp"])
        profile = rows[0] if isinstance(rows, list) and rows else rows if isinstance(rows, dict) else None
        return kind_from_profile(sym["key"], profile)[0]

    def earnings(self, sym: dict) -> tuple[list | None, str | None]:
        """(rows, None) or (None, reason)."""
        key = sym["key"]
        if key not in self._earnings:
            rows, why = self.fs.get("earnings", symbol=sym["fmp"])
            if rows is not None and not rows:
                rows, why = None, "FMP returned no earnings dates"
            self._earnings[key] = (rows, why)
        return self._earnings[key]


def _feed_label(session: dict) -> str:
    return f"{CH.to_ny(session['feed_time']):%Y-%m-%d %H:%M} New York"


# ---------------------------------------------------------------------------- one symbol
def snapshot_one(cboe, facts: SymbolFacts, text: str, root: Path, force: bool = False,
                 keep_raw: bool = False) -> tuple[int, str]:
    """Store one symbol's chain and summary row. Returns (log level, message); raises on a CBOE or symbol error."""
    sym = normalize(text, allow_futures_settled=True)
    cboe.keep_raw = keep_raw
    try:
        payload, sym = fetch.get_chain_with_fallback(cboe, sym)
        raw_gz = cboe.last_raw if keep_raw else None
    finally:
        cboe.keep_raw, cboe.last_raw = False, None
    key = sym["key"]
    session = CH.session_from_payload(payload)
    as_of, feed = session["as_of"], _feed_label(session)
    if session["overnight_quotes"]:
        return logging.WARNING, (f"{key}: {as_of} overnight (GTH) quotes, feed time {feed}: not stored (also with "
                                 "--force); run between 16:30 and 20:00 New York time")
    if not session["closed"] and not force:
        return logging.WARNING, (f"{key}: market still open (feed time {feed}), skipped; run after 16:30 New York "
                                 "time, or use --force to save it anyway")
    futures = key in C.FUTURES_SETTLED
    have_chain = H.chain_path(root, key, as_of).exists()
    old = None if futures else H.read_summary(root, key)
    have_row = old is not None and any(d == as_of for d in old["trade_date"])
    if have_chain and not force and (futures or have_row):
        return logging.INFO, f"{key}: {as_of} already saved, skipped"

    parts = []
    if not have_chain or force:
        res = H.save_chain(root, key, payload, session, force=force, raw_bytes=raw_gz)
        parts.append(f"saved {res.get('contracts', 0)} contracts, {res.get('expirations', 0)} expirations")
        if keep_raw and raw_gz is None:
            parts.append("raw feed not kept (not available from the client)")
    if futures:
        parts.append("chain only (futures-settled), no summary")
    else:
        kind = facts.kind(sym)
        earnings_date = None
        if kind == "stock":
            rows, why = facts.earnings(sym)
            if rows is None:
                parts.append(f"{why}, ex-event IV not stored")
            else:
                earnings_date = M.next_earnings(rows, as_of, session["closed"])
        frame = CH.frame_from_options(payload["data"].get("options") or [])
        rates = facts.rates(as_of) if kind == "index" else None      # only European (index) straddles are discounted
        row = H.summary_row(session, frame, kind, earnings_date, key=key, rates=rates)
        H.append_summary(root, key, row, force=force)
        parts.append("summary row written" if (not have_row or force) else "summary row kept")
        if not session["closed"]:
            parts.append("intraday data (--force)")
    return logging.INFO, f"{key}: {as_of} " + "; ".join(parts) + f" (feed time {feed})"


# ---------------------------------------------------------------------------- --rebuild-summary
def rebuild_summary(root: Path, keys: list[str], facts: SymbolFacts) -> list[str]:
    """Rebuild summary/<key>.parquet from chains/ for each key (all stored keys when none are given). Returns the
    keys that failed."""
    failed = []
    wanted = keys or H.stored_keys(root)
    if not wanted:
        log.warning("no stored chains under %s", Path(root) / "chains")
    for text in wanted:
        try:
            sym = normalize(text, allow_futures_settled=True)
        except ValueError as e:
            log.error("%s: %s", text, e)
            failed.append(text)
            continue
        key = sym["key"]
        if key in C.FUTURES_SETTLED:
            log.info("%s: chain only (futures-settled), no summary", key)
            continue
        dates = H.chain_dates(root, key)
        if not dates:
            log.error("%s: no stored chains", key)
            failed.append(key)
            continue
        kind = facts.kind(sym)
        rows_e, why = facts.earnings(sym) if kind == "stock" else (None, None)
        if kind == "stock" and rows_e is None:
            log.info("%s: %s, ex-event IV not stored", key, why)
        if kind == "index":
            facts.preload_rates(dates[0], dates[-1])
        rows, bad = [], 0
        for d in dates:
            try:
                session, frame = H.load_chain(H.chain_path(root, key, d))
                ed = M.next_earnings(rows_e, d, True) if rows_e else None   # stored chains are after the close
                rates = facts.rates(session["as_of"]) if kind == "index" else None
                rows.append(H.summary_row(session, frame, kind, ed, key=key, rates=rates))
            except Exception as e:  # one damaged file must not stop the others
                bad += 1
                log.error("%s %s: %s: %s", key, d, type(e).__name__, e)
        if rows:
            H.write_summary(root, key, rows)
        log.info("%s: summary rebuilt from %d chains (%s to %s)%s", key, len(rows), dates[0], dates[-1],
                 f", {bad} unreadable" if bad else "")
        if bad:
            failed.append(key)
    return failed


# ---------------------------------------------------------------------------- CLI
def _ticker_list(args, root: Path) -> list[str]:
    tickers = list(args.tickers)
    path = args.tickers_file
    if path is None and not tickers:
        path = H.ensure_tickers_file(root)
    if path is not None:
        tickers += H.read_tickers(Path(path))
    return list(dict.fromkeys(t.strip().upper() for t in tickers if t.strip()))


def _setup_logging(root: Path) -> list[logging.Handler]:
    fmt = logging.Formatter(LOG_FORMAT, "%Y-%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    log.propagate = False
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        p = Path(root) / "logs" / "snapshot.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(p, encoding="utf-8"))
    except OSError as e:
        print(f"note: cannot write {Path(root) / 'logs' / 'snapshot.log'} ({e})", file=sys.stderr)
    for h in handlers:
        h.setFormatter(fmt)
        log.addHandler(h)
    return handlers


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="snapshot.py",
                                 description="Save end-of-day US option chains from CBOE's free delayed feed into "
                                             "the history store.")
    ap.add_argument("tickers", nargs="*", metavar="TICKER",
                    help="e.g. SPY QQQ SPX VIX BRK.B (default: <data-dir>/tickers.txt)")
    ap.add_argument("-f", "--tickers-file", type=Path, help="one or more symbols per line, # starts a comment")
    ap.add_argument("--data-dir", "-o", "--out", dest="data_dir", type=Path,
                    help="history store (default $OA_DATA_DIR, else ~/options-data)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite saved days and save even while the market is open (never overnight quotes)")
    ap.add_argument("--raw", action="store_true", help="also keep the gzipped raw feed under raw/")
    ap.add_argument("--sleep", type=float, default=C.CBOE_MIN_INTERVAL,
                    help="seconds between CBOE requests (default 1)")
    ap.add_argument("--rebuild-summary", action="store_true",
                    help="rebuild summary/<key>.parquet from the stored chains (all keys, or the TICKERs given)")
    return ap


def main(argv=None, cboe=None, fmp=None, massive=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        from winenv import load_user_env
        load_user_env()
    except Exception:
        pass
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.sleep < 0:
        ap.error("--sleep must be 0 or more")
    root = H.data_dir(args.data_dir)
    handlers = _setup_logging(root)
    try:
        fs = fmp if isinstance(fmp, fetch.FmpState) else fetch.FmpState(*fetch.make_fmp()) if fmp is None \
            else fetch.FmpState(fmp)
        ms = massive if isinstance(massive, fetch.MassiveState) else \
            fetch.MassiveState(*fetch.make_massive()) if massive is None else fetch.MassiveState(massive)
        facts = SymbolFacts(fs, ms)
        if fs.client is None:
            log.info("FMP not configured: stocks and ETFs are told apart by the built-in list, ex-event IV not "
                     "stored")
        if args.rebuild_summary:
            log.info("rebuilding summaries in %s", root)
            failed = rebuild_summary(root, list(args.tickers), facts)
        else:
            tickers = _ticker_list(args, root)
            if not tickers:
                ap.error("no tickers given (and the tickers file is empty)")
            client = cboe if cboe is not None else CboeClient(refresh=True, min_interval=args.sleep)
            log.info("snapshot of %d symbols into %s", len(tickers), root)
            failed = []
            for t in tickers:
                try:
                    level, msg = snapshot_one(client, facts, t, root, args.force, args.raw)
                    log.log(level, msg)
                except NotFound:
                    log.error("%s: no option chain on CBOE (check the symbol)", t)
                    failed.append(t)
                except CboeError as e:
                    log.error("%s: %s", t, e)
                    failed.append(t)
                except Exception as e:  # keep going with the other symbols
                    log.error("%s: %s: %s", t, type(e).__name__, e)
                    failed.append(t)
            for note in fs.notes:
                log.warning("FMP: %s", note)
        for note in ms.notes:
            log.warning("Massive: %s", note)
        if failed:
            log.error("failed: %s", " ".join(failed))
        return 1 if failed else 0
    finally:
        for h in handlers:
            log.removeHandler(h)
            h.close()


if __name__ == "__main__":
    sys.exit(main())
