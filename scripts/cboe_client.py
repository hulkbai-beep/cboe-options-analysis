#!/usr/bin/env python3
"""
Client for CBOE's free delayed-quotes feed (plan 4.2) with the cache rules of plan 3.6.

- get_chain("NVDA" | "_SPX" | "BRK.B") -> the parsed JSON of the whole option chain (every expiry and strike).
- get_index_history("VIX") -> [{"date": date, "close": float}, ...] oldest first, from CBOE's volatility-index CSVs.
- urllib, like fmp_client.py; no key is needed. HTTPS certificates are checked as tls.py describes: the operating
  system's certificate store through truststore, then certifi's root list and SSL_CERT_FILE. A failed check raises
  CboeUnreachable with the reason, the certificate the network presented and what was trusted (no retry).
- Every request sends Accept-Encoding: gzip and is decompressed by hand: urllib does not do it, and CBOE's
  403 "AccessDenied" body for an unknown symbol is gzip too. That 403 raises NotFound (no retry).
- A 403 without AccessDenied (a Cloudflare or proxy block), 429, 5xx and network errors are tried
  CBOE_ATTEMPTS times (waits CBOE_BACKOFF) and then raise CboeUnreachable. After CBOE_NO_RESPONSE_LIMIT
  consecutive tries without any HTTP response, every later request of the client fails at once.
- Requests of one process are at least CBOE_MIN_INTERVAL seconds apart.

Cache ($OA_CACHE_DIR, default ~/.cache/cboe-options; file names hold only the symbol):
- A chain whose session is still open, or one with overnight (GTH) quotes, lives CBOE_TTL_OPEN seconds, then
  a conditional request (ETag / Last-Modified) renews it on 304.
- A chain of a closed session lives until 09:30 New York on the next trading day.
- For SPX, XSP and VIX (GTH roots) a closed version generated before 20:15 New York wins over an overnight
  version of the same trading day, even with --refresh, so the close-consistent chain can be analysed and saved.
- An index CSV whose last row covers the latest closed session lives until the next session's close + 15 min;
  otherwise CBOE_TTL_OPEN.
- refresh=True skips reading the cache (except the GTH rule above) but still writes it.

CLI:
  python cboe_client.py check [SYMBOL]   certificates trusted, final URL and redirect, HTTP status, compression,
                                         sizes, timestamp, Last-Modified, cf-cache-status, contracts, expirations
                                         (default SPY)
  python cboe_client.py clear-cache      delete the cached responses
"""
from __future__ import annotations

import csv
import gzip
import http.client
import io
import json
import os
import re
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import config as C
import chain as CH
import tls

SOURCE = "CBOE delayed quotes"
_SYMBOL = re.compile(r"^[A-Z0-9_.]{1,12}$")
_INDEX = re.compile(r"^[A-Z0-9]{1,12}$")
_ACCESS_DENIED = b"<Code>AccessDenied</Code>"
_GZIP_MAGIC = b"\x1f\x8b"
_THROTTLE_LOCK = threading.Lock()
_LAST_REQUEST = [None]            # monotonic time of the latest request of this process


class CboeError(Exception):
    """Any failure getting usable data from CBOE."""


class NotFound(CboeError):
    """CBOE has no such symbol (HTTP 403 with an S3 AccessDenied body, or 404)."""


class CboeUnreachable(CboeError):
    """No usable HTTP response after the retries (network, proxy, TLS, 429, 5xx, a blocking 403)."""


@dataclass
class _Response:
    status: int
    url: str
    final_url: str
    headers: dict
    raw: bytes          # bytes as received
    body: bytes         # decompressed


def default_cache_dir() -> Path:
    env = os.environ.get(C.CACHE_DIR_ENV)
    return Path(env).expanduser() if env else Path.home().joinpath(*C.CACHE_DIR_DEFAULT)


def _decompress(raw: bytes, headers) -> bytes:
    enc = (headers.get("Content-Encoding") or "").lower() if headers is not None else ""
    if raw[:2] == _GZIP_MAGIC or "gzip" in enc:
        return gzip.decompress(raw)
    if "deflate" in enc:
        return zlib.decompress(raw)
    return raw


def _header_dict(headers) -> dict:
    if headers is None:
        return {}
    try:
        return {k: v for k, v in headers.items()}
    except AttributeError:
        return dict(headers)


def _origin(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc


def _is_cert_error(exc) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (ssl.SSLCertVerificationError, ssl.CertificateError)) or \
        "CERTIFICATE_VERIFY_FAILED" in str(reason)


def _net_reason(exc) -> str:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
        return "timed out"
    text = str(reason) or exc.__class__.__name__
    return text[:200]


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def parse_index_csv(text: str, index: str = "") -> list[dict]:
    """CBOE's DATE,OPEN,HIGH,LOW,CLOSE history (MM/DD/YYYY) -> [{date, close}] oldest first, one row per date.
    A two-column file (DATE,<NAME>) uses its second column as the close."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = [h.strip().upper() for h in next(reader)]
    except StopIteration:
        raise CboeError(f"{index} history CSV is empty") from None
    if "DATE" not in header:
        raise CboeError(f"{index} history CSV has no DATE column (header {header[:6]})")
    di = header.index("DATE")
    if "CLOSE" in header:
        ci = header.index("CLOSE")
    elif len(header) == 2:
        ci = 1 - di
    else:
        raise CboeError(f"{index} history CSV has no CLOSE column (header {header[:6]})")
    rows = {}
    for rec in reader:
        if len(rec) <= max(di, ci):
            continue
        d = _parse_date(rec[di])
        try:
            v = float(rec[ci])
        except ValueError:
            continue
        if d is None or not (v == v) or v <= 0:
            continue
        rows[d] = v
    if not rows:
        raise CboeError(f"{index} history CSV has no usable rows")
    return [{"date": d, "close": rows[d]} for d in sorted(rows)]


def _parse_date(text: str):
    text = text.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def chain_session_facts(payload: dict) -> dict:
    """{as_of, feed_time, closed, overnight, gth} of a chain payload (the 4.4 rules), for the cache."""
    s = CH.session_from_payload(payload)
    return {"as_of": s["as_of"].isoformat(), "feed_time": CH.iso(s["feed_time"]), "closed": s["closed"],
            "overnight": s["overnight_quotes"], "gth": s["gth"]}


class CboeClient:
    def __init__(self, cache_dir=None, use_cache=True, refresh=False, min_interval=C.CBOE_MIN_INTERVAL,
                 timeout=C.CBOE_TIMEOUT, urlopen=None, sleep=time.sleep, now=None, read_certificate=None):
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.use_cache = bool(use_cache)
        self.refresh = bool(refresh)
        self.min_interval = float(min_interval or 0.0)
        self.timeout = timeout
        self._urlopen = urlopen or self._open
        # what the network presents after a failed certificate check (for the message); off for injected transports
        self._read_certificate = read_certificate or (tls.presented_certificate if urlopen is None else None)
        self._trust = None
        self._opener = None
        self._sleep = sleep
        self._now_arg = now
        self.stats = {"network_calls": 0, "cache_hits": 0}
        self.redirected_to = None
        self.fetch_info: dict[str, dict] = {}     # per symbol / index: where the data came from and when
        self.keep_raw = False                       # snapshot.py --raw: keep the gzip JSON of the chain returned
        self.last_raw: bytes | None = None
        self._no_response = 0
        self._down = None

    # ------------------------------------------------------------------ public API
    def get_chain(self, cboe_symbol: str) -> dict:
        """The whole option chain of a CBOE symbol (NVDA, _SPX, BRK.B) as parsed JSON."""
        sym = str(cboe_symbol or "").strip().upper()
        if not _SYMBOL.match(sym):
            raise NotFound(f"{cboe_symbol!r} is not a CBOE symbol")
        url = C.CBOE_CHAIN_URL.format(symbol=sym)
        cached = self._read_cache("chains", sym, ".json.gz") if self.use_cache else None
        now = self._now()
        self.last_raw = None
        if cached and not self.refresh and now < _parse_iso(cached[0].get("expires")):
            payload = _load_json_gz(cached[1])
            if payload is not None:
                self._keep(cached[1])
                return self._from_cache(sym, cached[0], payload, "cache")
        cond = _conditional(cached[0]) if (cached and not self.refresh) else {}
        resp = self._fetch(url, cond)
        if resp is None and not cached:
            raise CboeError(f"CBOE answered 304 Not Modified to an unconditional request for {sym}")
        if resp is None:                                   # 304: the cached copy is current
            payload = _load_json_gz(cached[1])
            if payload is not None:
                meta = dict(cached[0], fetched_at=CH.iso(now), expires=CH.iso(self._chain_expiry(cached[0], now)))
                self._write_meta("chains", sym, meta)
                self._keep(cached[1])
                return self._from_cache(sym, meta, payload, "revalidated")
            resp = self._fetch(url, {})                    # unreadable cache: fetch it whole
        payload = self._parse_chain(resp.body, sym)
        facts = chain_session_facts(payload)
        if facts["overnight"] and cached:
            old = cached[0]
            if old.get("as_of") == facts["as_of"] and old.get("closed") and not old.get("overnight"):
                kept = _load_json_gz(cached[1])
                if kept is not None:                       # keep the pre-20:15 close version (3.6)
                    self._keep(cached[1])
                    return self._from_cache(sym, old, kept, "kept_close_version")
        meta = {"symbol": sym, "url": url, "final_url": resp.final_url, "etag": resp.headers.get("ETag"),
                "last_modified": resp.headers.get("Last-Modified"), "fetched_at": CH.iso(now),
                "cf_cache_status": resp.headers.get("cf-cache-status"),
                "bytes_compressed": len(resp.raw), "bytes_raw": len(resp.body), **facts}
        meta["expires"] = CH.iso(self._chain_expiry(meta, now))
        if self.use_cache or self.keep_raw:
            data = resp.raw if resp.raw[:2] == _GZIP_MAGIC else gzip.compress(resp.body, compresslevel=5)
            if self.use_cache:
                self._write_cache("chains", sym, ".json.gz", data, meta)
            self._keep(data)
        self.fetch_info[sym] = {"source": "network", **_public(meta)}
        return payload

    def get_index_history(self, index: str) -> list[dict]:
        """[{date, close}] oldest first for a CBOE index history CSV (VIX, VIX3M, VIX9D, VXN, RVX, VXAPL, ...)."""
        idx = str(index or "").strip().upper().lstrip("^_")
        if not _INDEX.match(idx):
            raise NotFound(f"{index!r} is not a CBOE index")
        url = C.CBOE_INDEX_URL.format(index=idx)
        key = f"{idx}_History"
        cached = self._read_cache("indices", key, ".csv") if self.use_cache else None
        now = self._now()
        if cached and not self.refresh and now < _parse_iso(cached[0].get("expires")):
            rows = _safe_csv(cached[1], idx)
            if rows:
                return self._from_cache(key, cached[0], rows, "cache")
        cond = _conditional(cached[0]) if (cached and not self.refresh) else {}
        resp = self._fetch(url, cond)
        if resp is None and not cached:
            raise CboeError(f"CBOE answered 304 Not Modified to an unconditional request for {idx}")
        if resp is None:
            rows = _safe_csv(cached[1], idx)
            if rows:
                meta = dict(cached[0], fetched_at=CH.iso(now),
                            expires=CH.iso(self._csv_expiry(rows[-1]["date"], now)))
                self._write_meta("indices", key, meta)
                return self._from_cache(key, meta, rows, "revalidated")
            resp = self._fetch(url, {})
        rows = parse_index_csv(resp.body.decode("utf-8", errors="replace"), idx)
        meta = {"symbol": idx, "url": url, "final_url": resp.final_url, "etag": resp.headers.get("ETag"),
                "last_modified": resp.headers.get("Last-Modified"), "fetched_at": CH.iso(now),
                "last_date": rows[-1]["date"].isoformat(), "rows": len(rows),
                "expires": CH.iso(self._csv_expiry(rows[-1]["date"], now))}
        if self.use_cache:
            self._write_cache("indices", key, ".csv", resp.body, meta)
        self.fetch_info[key] = {"source": "network", **_public(meta)}
        return rows

    def _keep(self, gz: bytes) -> None:
        if self.keep_raw:
            self.last_raw = gz

    def describe(self) -> dict:
        return {"source": SOURCE, "base": C.CBOE_BASE, "redirected_to": self.redirected_to,
                "calls": self.stats["network_calls"]}

    @property
    def trust(self) -> tls.Trust:
        """The HTTPS context of this client and what it trusts (built on first use)."""
        if self._trust is None:
            self._trust = tls.trust()
        return self._trust

    def clear_cache(self) -> list[str]:
        """Delete this client's cached responses; returns what was removed."""
        import shutil
        removed = []
        for sub in ("chains", "indices"):
            p = self.cache_dir / sub
            if p.is_dir():
                shutil.rmtree(p)
                removed.append(str(p))
        try:
            self.cache_dir.rmdir()                         # only when nothing else lives there
        except OSError:
            pass
        return removed

    # ------------------------------------------------------------------ cache rules
    def _now(self) -> datetime:
        return CH.to_ny(self._now_arg)

    @staticmethod
    def _chain_expiry(meta: dict, now: datetime) -> datetime:
        short = now + timedelta(seconds=C.CBOE_TTL_OPEN)
        if not meta.get("closed") or meta.get("overnight"):
            return short
        as_of = date.fromisoformat(meta["as_of"])
        return max(CH.open_dt(CH.next_trading_day(as_of)), short)

    @staticmethod
    def _csv_expiry(last_date: date, now: datetime) -> datetime:
        lctd = CH.last_closed_trading_day(now)
        if last_date >= lctd:
            nxt = CH.next_trading_day(lctd)
            return max(CH.close_dt(nxt) + timedelta(minutes=C.SESSION_BUFFER_MIN),
                       now + timedelta(seconds=C.CBOE_TTL_OPEN))
        return now + timedelta(seconds=C.CBOE_TTL_OPEN)

    def _from_cache(self, name: str, meta: dict, value, source: str):
        self.stats["cache_hits"] += 1
        if meta.get("final_url") and _origin(meta["final_url"]) != _origin(meta.get("url") or meta["final_url"]):
            self.redirected_to = self.redirected_to or _origin(meta["final_url"])
        self.fetch_info[name] = {"source": source, **_public(meta)}
        return value

    def _paths(self, sub: str, name: str, ext: str) -> tuple[Path, Path]:
        d = self.cache_dir / sub
        return d / f"{name}{ext}", d / f"{name}.meta.json"

    def _read_cache(self, sub: str, name: str, ext: str):
        data_p, meta_p = self._paths(sub, name, ext)
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            data = data_p.read_bytes()
        except (OSError, ValueError):
            return None
        return (meta, data) if isinstance(meta, dict) and data else None

    def _write_cache(self, sub: str, name: str, ext: str, data: bytes, meta: dict) -> None:
        data_p, meta_p = self._paths(sub, name, ext)
        try:
            _write_atomic(data_p, data)
            _write_atomic(meta_p, json.dumps(meta, indent=1).encode("utf-8"))
        except OSError as e:                               # a read-only or full disk must not stop the analysis
            print(f"note: could not write the CBOE cache ({e.__class__.__name__}: {e})", file=sys.stderr)

    def _write_meta(self, sub: str, name: str, meta: dict) -> None:
        _, meta_p = self._paths(sub, name, "")
        try:
            _write_atomic(meta_p, json.dumps(meta, indent=1).encode("utf-8"))
        except OSError:
            pass

    # ------------------------------------------------------------------ HTTP
    def _open(self, req, timeout=None):
        """urlopen with this client's certificate checks (and urllib's proxy settings)."""
        if self._opener is None:
            self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=self.trust.context))
        return self._opener.open(req, timeout=timeout)

    def _cert_failure(self, url: str, exc) -> str:
        cert = None
        if self._read_certificate is not None:
            try:
                cert = self._read_certificate(url, min(float(self.timeout or 15), 15.0))
            except Exception:                              # a diagnostic must never replace the real error
                cert = None
        return (f"CBOE unreachable: TLS certificate verification failed for {_host(url)}: "
                f"{tls.explain(exc, self.trust, cert)}")

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        with _THROTTLE_LOCK:
            last = _LAST_REQUEST[0]
            if last is not None:
                wait = self.min_interval - (time.monotonic() - last)
                if wait > 0:
                    self._sleep(wait)
            _LAST_REQUEST[0] = time.monotonic()

    def _fetch(self, url: str, extra_headers: dict) -> _Response | None:
        """One logical GET with retries. None = 304 Not Modified."""
        if self._down:
            raise CboeUnreachable(self._down)
        headers = {"User-Agent": C.USER_AGENT, "Accept-Encoding": "gzip", **extra_headers}
        last = "no attempt"
        for attempt in range(C.CBOE_ATTEMPTS):
            if attempt:
                self._sleep(C.CBOE_BACKOFF[min(attempt - 1, len(C.CBOE_BACKOFF) - 1)])
            self._throttle()
            self.stats["network_calls"] += 1
            req = urllib.request.Request(url, headers=headers)
            try:
                resp = self._urlopen(req, timeout=self.timeout)
            except urllib.error.HTTPError as e:
                self._no_response = 0
                final = e.geturl() or url
                self._note_redirect(url, final)
                if e.code == 304:
                    return None
                try:
                    raw = e.read() or b""
                except (OSError, http.client.HTTPException):
                    raw = b""
                try:
                    body = _decompress(raw, e.headers) if raw else b""
                except (OSError, EOFError, zlib.error):
                    body = raw
                if e.code == 404 or (e.code == 403 and _ACCESS_DENIED in body):
                    raise NotFound(f"CBOE has no data for {url.rsplit('/', 1)[-1]} (HTTP {e.code})") from None
                if e.code in (403, 429) or e.code >= 500:
                    last = f"HTTP {e.code}" + (" (blocked by a firewall or proxy?)" if e.code == 403 else "")
                    continue
                raise CboeError(f"HTTP {e.code} from {_host(final)}") from None
            except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
                if _is_cert_error(e):
                    self._down = self._cert_failure(url, e)
                    raise CboeUnreachable(self._down) from None
                self._no_response += 1
                last = _net_reason(e)
                if self._no_response >= C.CBOE_NO_RESPONSE_LIMIT:
                    self._down = f"CBOE unreachable: {last} ({_host(url)})"
                    raise CboeUnreachable(self._down) from None
                continue
            self._no_response = 0
            try:
                with resp:
                    raw = resp.read()
                    status = getattr(resp, "status", None) or resp.getcode()
                    final = resp.geturl() or url
                    hdrs = _header_dict(resp.headers)
            except (OSError, http.client.HTTPException) as e:
                last = f"transfer interrupted ({e.__class__.__name__})"
                continue
            self._note_redirect(url, final)
            try:
                body = _decompress(raw, resp.headers)
            except (OSError, EOFError, zlib.error):
                last = "corrupt compressed body"
                continue
            return _Response(int(status or 200), url, final, _Caseless(hdrs), raw, body)
        raise CboeUnreachable(f"CBOE unreachable after {C.CBOE_ATTEMPTS} tries: {last} ({_host(url)})")

    def _note_redirect(self, url: str, final: str) -> None:
        if final and _origin(final) != _origin(url):
            self.redirected_to = _origin(final)

    @staticmethod
    def _parse_chain(body: bytes, sym: str) -> dict:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise CboeError(f"CBOE returned something that is not JSON for {sym}") from None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("options"), list) or not payload.get("timestamp"):
            raise CboeError(f"CBOE response for {sym} has no data.options or timestamp")
        try:
            CH.feed_time(payload)
        except (TypeError, ValueError):
            raise CboeError(f"CBOE response for {sym} has an unreadable timestamp "
                            f"{str(payload.get('timestamp'))[:40]!r}") from None
        return payload


class _Caseless(dict):
    """Response headers with case-insensitive get()."""

    def get(self, key, default=None):
        k = key.lower()
        for name, value in self.items():
            if name.lower() == k:
                return value
        return default


def _conditional(meta: dict) -> dict:
    h = {}
    if meta.get("etag"):
        h["If-None-Match"] = meta["etag"]
    if meta.get("last_modified"):
        h["If-Modified-Since"] = meta["last_modified"]
    return h


def _parse_iso(text) -> datetime:
    try:
        return CH.to_ny(datetime.fromisoformat(str(text)))
    except (TypeError, ValueError):
        return CH.to_ny(datetime(1970, 1, 2))


def _load_json_gz(data: bytes):
    try:
        payload = json.loads(gzip.decompress(data).decode("utf-8"))
    except (OSError, EOFError, zlib.error, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance((payload.get("data") or {}).get("options"), list):
        return None
    return payload


def _safe_csv(data: bytes, idx: str):
    try:
        return parse_index_csv(data.decode("utf-8", errors="replace"), idx)
    except CboeError:
        return None


def _public(meta: dict) -> dict:
    keep = ("fetched_at", "feed_time", "as_of", "closed", "overnight", "last_modified", "final_url", "last_date")
    return {k: meta.get(k) for k in keep if k in meta}


# ---------------------------------------------------------------------- CLI
def _fmt_bytes(n) -> str:
    return f"{n:,}" if isinstance(n, int) else "?"


def check(symbol: str = "SPY") -> int:
    """Fetch one chain straight from CBOE (no cache) and print what came back. 0 = OK, 1 = problem."""
    from symbols import normalize, SymbolRejected
    try:
        info = normalize(symbol, allow_futures_settled=True)
    except SymbolRejected as e:
        print(f"{symbol}: {e}")
        return 1
    except ValueError as e:
        print(f"{symbol}: {e}")
        return 1
    client = CboeClient(use_cache=False)
    sym = info["cboe"]
    url = C.CBOE_CHAIN_URL.format(symbol=sym)
    print(f"CBOE delayed quotes check: {sym}")
    trust = client.trust
    print(f"  certificates    {trust.description}"
          + (f" ({' and '.join(trust.missing)} not installed: python -m pip install -r requirements.txt)"
             if trust.missing else ""))
    print(f"  request         {url}")
    try:
        try:
            resp = client._fetch(url, {})
        except NotFound:
            if sym.startswith("_") or not sym.isalpha():
                raise
            print(f"  {sym} not found; trying the index symbol _{sym}")
            sym = "_" + sym
            url = C.CBOE_CHAIN_URL.format(symbol=sym)
            resp = client._fetch(url, {})
        payload = client._parse_chain(resp.body, sym)
    except CboeError as e:
        print(f"  error           {e}")
        return 1
    redirected = _origin(resp.final_url) != _origin(url)
    print(f"  final URL       {resp.final_url}" + ("  (redirected)" if redirected else ""))
    print(f"  HTTP status     {resp.status}")
    print(f"  encoding        {resp.headers.get('Content-Encoding') or 'none'}")
    print(f"  bytes           {_fmt_bytes(len(resp.raw))} received / {_fmt_bytes(len(resp.body))} decompressed")
    feed = CH.feed_time(payload)
    print(f"  timestamp       {payload.get('timestamp')} UTC ({feed:%Y-%m-%d %H:%M:%S} New York)")
    print(f"  Last-Modified   {resp.headers.get('Last-Modified') or '-'}")
    print(f"  ETag            {resp.headers.get('ETag') or '-'}")
    print(f"  cf-cache-status {resp.headers.get('cf-cache-status') or '-'}")
    key = sym.lstrip("_")
    session, frame, live, q = CH.load_payload(payload, key)
    print(f"  contracts       {q['contracts_total']:,} in the response, {q['contracts_live']:,} live standard-root "
          f"contracts, {q['expirations']} expirations")
    if q["excluded_roots"]:
        print(f"  excluded roots  {q['excluded_roots']}")
    state = ("closed, overnight (GTH) option quotes" if session["overnight_quotes"]
             else "closed" if session["closed"] else "open (intraday)")
    print(f"  session         as_of {session['as_of']}, {state}; last trade {session['last_trade_time']}")
    print("OK")
    return 0


def _usage() -> int:
    print(__doc__.strip().split("CLI:")[-1].rstrip())
    return 2


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        from winenv import load_user_env
        load_user_env()
    except Exception:                                      # registry access is best effort
        pass
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        _usage()
        return 0
    if not args:
        return _usage()
    cmd, rest = args[0], args[1:]
    if cmd == "check":
        if len(rest) > 1:
            return _usage()
        return check(rest[0] if rest else "SPY")
    if cmd == "clear-cache":
        client = CboeClient()
        removed = client.clear_cache()
        print("removed " + ", ".join(removed) if removed else f"nothing cached in {client.cache_dir}")
        return 0
    return _usage()


if __name__ == "__main__":
    sys.exit(main())
