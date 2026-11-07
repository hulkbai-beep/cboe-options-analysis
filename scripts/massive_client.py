#!/usr/bin/env python3
"""
Minimal client for the Massive API (formerly Polygon.io), used by the cboe-options-analysis skill for optional
data the CBOE chain does not carry: short interest and short sale volume, free float, news with per-ticker
sentiment, Treasury yields (the discount curve of European index options), the exchange holiday calendar, and
dividends when FMP gives no answer. Massive is optional: without MASSIVE_API_KEY every analysis runs as
before.

- Standard library only; works on Windows, macOS and Linux.
- The key is read from MASSIVE_API_KEY and sent only in the Authorization header. It never appears in a URL, a
  cache file, a file name or printed output.
- HTTPS certificates are always verified (tls.py: the operating system's store, certifi, SSL_CERT_FILE).
- Responses are cached on disk for a time that suits each endpoint (TTL below), so a re-run makes no requests.
- The free plan allows 5 requests a minute. The client spaces its requests to MASSIVE_MAX_PER_MIN, counting the
  requests of every process that shares the cache folder (rate-window.json there: request times only), so two
  analyses in a row stay within the limit. A request waits for a free slot; when that is further away than
  MASSIVE_MAX_WAIT seconds (or than the `wait` of that request) it is skipped (RateLimited) and the block that
  needed it says why it is unavailable.
- An endpoint the plan does not include raises RestrictedEndpoint, a rejected key InvalidKey, and a host that
  sends no HTTP response twice, or fails the certificate check, Unreachable, after which every later request of
  the run fails at once. Likewise, when Massive still answers HTTP 429 after one wait (another program may be using
  the key), or SERVER_FAILURES requests in a row end on server errors, the client's later requests fail at once
  for a while (FAIL_FAST seconds) instead of each waiting again; cached answers are still served.
- Redirects are never followed (the key would go with them), and any 3xx answer is an error.

Environment variables:
  MASSIVE_API_KEY      required to use Massive (the free plan is enough)
  MASSIVE_MAX_PER_MIN  requests per minute (default 5, the free plan)
  MASSIVE_MAX_WAIT     longest wait for a free request slot, in seconds (default 65: one rate window)
  MASSIVE_TIMEOUT      seconds to wait for each response (default 30)
  MASSIVE_CACHE_DIR    cache folder (default ~/.cache/cboe-options-massive)

CLI:
  python massive_client.py check [SYMBOL]      connection, the endpoints this skill uses on your plan (default
                                               NVDA) and the holiday calendar against config.py
  python massive_client.py get PATH k=v ...    raw JSON, e.g. get /stocks/v1/short-interest ticker=NVDA limit=2
  python massive_client.py clear-cache         delete the local cache
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import config as C
import tls

OFFICIAL_BASE = C.MASSIVE_BASE
USER_AGENT = C.USER_AGENT

# the endpoints this skill uses
SHORT_INTEREST = "/stocks/v1/short-interest"
SHORT_VOLUME = "/stocks/v1/short-volume"
FLOAT = "/stocks/vX/float"
DIVIDENDS = "/stocks/v1/dividends"
NEWS = "/v2/reference/news"
TREASURY = "/fed/v1/treasury-yields"
HOLIDAYS = "/v1/marketstatus/upcoming"

HOUR, DAY = 3600, 86400
# cache lifetime in seconds (short sale volume of a day arrives in the evening; Treasury yields once a day)
TTL = {SHORT_INTEREST: 12 * HOUR, SHORT_VOLUME: 4 * HOUR, FLOAT: 7 * DAY, DIVIDENDS: 12 * HOUR, NEWS: 1800,
       TREASURY: 6 * HOUR, HOLIDAYS: DAY}
DEFAULT_TTL = HOUR
NETWORK_ATTEMPTS = 2   # tries per request when no HTTP response arrives (timeout, refused, DNS)
HTTP_ATTEMPTS = 3      # tries per request on HTTP 5xx (a retry only when a request slot is free at once)
SERVER_FAILURES = 2    # requests in a row that end on HTTP 5xx before the later ones fail at once
FAIL_FAST = {"throttled": 60.0, "server": 300.0}   # seconds later requests fail at once after a 429 / server errors
WINDOW = 60.0          # seconds of the rate window
RATE_FILE = "rate-window.json"   # request times (time.time()) of the last WINDOW seconds, shared by processes


class MassiveError(Exception):
    """Any failure talking to Massive."""


class RestrictedEndpoint(MassiveError):
    """The endpoint (or this request) is not included in the current plan."""


class InvalidKey(MassiveError):
    """Missing or rejected API key."""


class Unreachable(MassiveError):
    """The host sent no HTTP response; nothing else in this run can work either."""


class RateLimited(MassiveError):
    """No free request slot within MASSIVE_MAX_WAIT seconds (or Massive answered HTTP 429)."""


class ServerTrouble(MassiveError):
    """Massive answered server errors to SERVER_FAILURES requests in a row: later requests are skipped for a while."""


def default_cache_dir() -> Path:
    return Path(os.environ.get("MASSIVE_CACHE_DIR") or (Path.home() / ".cache" / C.MASSIVE_CACHE_SUBDIR))


def _env_number(name: str, default: float) -> float:
    """A positive, finite number from the environment, else the default (junk such as "inf" never stops a run)."""
    try:
        v = float(os.environ.get(name) or default)
    except ValueError:
        return float(default)
    return v if math.isfinite(v) and v > 0 else float(default)


def _is_cert_error(exc) -> bool:
    import ssl
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (ssl.SSLCertVerificationError, ssl.CertificateError)) or \
        "CERTIFICATE_VERIFY_FAILED" in str(reason)


def _message(body: str) -> str | None:
    """Massive's error text ({"status": "ERROR", "error": ...} or {"message": ...}), else None."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if isinstance(data, dict):
        for k in ("error", "message"):
            if data.get(k):
                return str(data[k])
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would send the Authorization header (the key) to the new address, whatever its
    scheme or host. The 3xx answer reaches the client as an error status instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_opener(context):
    """An HTTPS opener with the given verified context that does not follow redirects."""
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), _NoRedirect())


class MassiveClient:
    def __init__(self, api_key=None, cache_dir=None, use_cache=True, refresh=False, max_per_min=None,
                 max_wait=None, timeout=None, base=OFFICIAL_BASE, urlopen=None, sleep=time.sleep,
                 clock=time.time, read_certificate=None):
        self.api_key = (api_key or os.environ.get("MASSIVE_API_KEY", "")).strip()
        if not self.api_key:
            raise InvalidKey("MASSIVE_API_KEY is not set. Set it as an environment variable "
                             "(Windows: setx MASSIVE_API_KEY \"your_key\", then open a new terminal).")
        if not (self.api_key.isascii() and self.api_key.isprintable()):
            raise InvalidKey("MASSIVE_API_KEY contains characters that cannot be sent (typographic quotes or invisible "
                             "characters copied with it?): set it again with the key alone")
        self.base = base.rstrip("/")
        if not self.base.startswith("https://"):
            raise MassiveError("the Massive base URL must use https")
        self.host = urllib.parse.urlparse(self.base).hostname or self.base
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.use_cache, self.refresh = use_cache, refresh
        self.per_min = max(1, int(max_per_min or _env_number("MASSIVE_MAX_PER_MIN", C.MASSIVE_MAX_PER_MIN)))
        self.max_wait = float(_env_number("MASSIVE_MAX_WAIT", C.MASSIVE_MAX_WAIT) if max_wait is None else max_wait)
        self.timeout = float(timeout or _env_number("MASSIVE_TIMEOUT", C.MASSIVE_TIMEOUT))
        self._urlopen = urlopen or self._open
        self._read_certificate = read_certificate or (tls.presented_certificate if urlopen is None else None)
        self._sleep, self._clock = sleep, clock
        self._trust = None
        self._opener = None
        self._lock = threading.Lock()
        self._sent: list[float] = []         # clock() of this client's requests (when the shared file fails)
        self.stats = {"network_calls": 0, "cache_hits": 0}
        self.unreachable = None              # message once a request got no HTTP response; later requests fail fast
        self._halt = None                    # (exception class, message, clock() until which requests fail fast)
        self._server_failures = 0            # requests in a row that ended on HTTP 5xx

    # ------------------------------------------------------------------ connection
    @property
    def trust(self) -> tls.Trust:
        """The HTTPS context of this client and what it trusts (built on first use, one per client)."""
        if self._trust is None:
            self._trust = tls.trust()
        return self._trust

    def _open(self, req, timeout=None):
        if self._opener is None:
            self._opener = build_opener(self.trust.context)
        return self._opener.open(req, timeout=timeout)

    def describe(self) -> dict:
        return {"route": "official Massive API", "base": self.base, "tls_verify": True,
                "tls_trust": self._trust.description if self._trust else None,
                "max_per_min": self.per_min, "max_wait": self.max_wait}

    def _redact(self, text: str) -> str:
        return text.replace(self.api_key, "***") if self.api_key else text

    def _cert_failure(self, url: str, exc) -> str:
        cert = None
        if self._read_certificate is not None:
            try:
                cert = self._read_certificate(url, min(self.timeout, 15.0))
            except Exception:                      # a diagnostic must never replace the real error
                cert = None
        return self._redact(f"HTTPS certificate verification failed for {self.host}: "
                            f"{tls.explain(exc, self.trust, cert)}")

    # ------------------------------------------------------------------ rate limit
    def _read_window(self, now: float) -> list[float]:
        """Request times inside the current window: this client's and those in the shared file (sorted)."""
        times = set(self._sent)
        try:
            data = json.loads((self.cache_dir / RATE_FILE).read_text(encoding="utf-8"))
            if isinstance(data, list):
                times.update(float(t) for t in data if isinstance(t, (int, float)) and not isinstance(t, bool))
        except (OSError, ValueError):
            pass
        return sorted(t for t in times if now - WINDOW < t <= now + 1.0)

    def _write_window(self, times: list[float]) -> None:
        self._sent = list(times)
        file = self.cache_dir / RATE_FILE
        tmp = file.with_name(f"{file.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(times), encoding="utf-8")
            os.replace(tmp, file)
        except OSError:                            # read-only disk: the in-memory window still applies
            try:
                tmp.unlink()
            except OSError:
                pass

    def _slot(self, max_wait: float) -> None:
        """Wait for a free request slot (at most per_min requests in any WINDOW seconds, counted over every
        process that shares the cache folder); RateLimited when the wait would exceed max_wait."""
        while True:
            with self._lock:
                now = self._clock()
                times = self._read_window(now)
                if len(times) < self.per_min:
                    # a distinct time per request: the window is merged as a set, and two requests read at the same
                    # clock tick (a coarse clock) must still count twice
                    self._write_window(times + [max(now, times[-1] + 1e-6) if times else now])
                    return
                wait = WINDOW - (now - times[-self.per_min]) + 0.25
            if wait > max_wait:
                raise RateLimited(f"Massive allows {self.per_min} requests a minute on this plan; the next free slot "
                                  f"is {wait:.0f}s away (longest wait {max_wait:g}s): run again in a minute")
            print(f"[massive] waiting {wait:.0f}s for the rate limit ({self.per_min} requests a minute) ...",
                  file=sys.stderr, flush=True)
            self._sleep(wait)

    # ------------------------------------------------------------------ cache
    def _now_ny(self) -> datetime:
        return datetime.now(ZoneInfo(C.MARKET_TZ))

    def _cache_path(self, path: str, params: dict) -> Path:
        key = self.base + "|" + path + "?" + urllib.parse.urlencode(sorted(params.items()))
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        safe = path.strip("/").replace("/", "_")
        sym = "".join(c for c in str(params.get("ticker", "all")) if c.isalnum() or c in ".-_")
        return self.cache_dir / f"{safe}__{sym}__{digest}.json"

    def _is_fresh(self, path: str, file: Path, ttl) -> bool:
        ttl = TTL.get(path, DEFAULT_TTL) if ttl is None else ttl
        return time.time() - file.stat().st_mtime < ttl

    # ------------------------------------------------------------------ HTTP
    def _request(self, url: str):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.api_key}",
                                                   "Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with self._urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace"), resp.headers
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            return e.code, body, e.headers

    def _fail_fast(self, kind: str, exc_class, message: str) -> None:
        """Later requests of this client fail at once with `message` for FAIL_FAST[kind] seconds."""
        self._halt = (exc_class, message, self._clock() + FAIL_FAST[kind])

    def _fetch(self, path: str, params: dict, max_wait: float) -> dict:
        if self.unreachable:
            raise Unreachable(self.unreachable)
        if self._halt is not None:
            if self._clock() < self._halt[2]:
                raise self._halt[0](self._halt[1])
            self._halt = None
        url = self.base + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        no_response, server_errors, last, retry_if_free = 0, 0, None, False
        for attempt in range(HTTP_ATTEMPTS + 1):    # one more than HTTP_ATTEMPTS: a 429 or a lost response retries
            if retry_if_free:                        # a server-error retry never waits for a slot later requests need
                try:
                    self._slot(0)
                except RateLimited:
                    break
            else:
                self._slot(max_wait)
            retry_if_free = False
            try:
                status, body, headers = self._request(url)
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
                if _is_cert_error(e):                # nothing on this host can work: fail the rest of the run fast
                    self.unreachable = self._cert_failure(url, e)
                    raise Unreachable(self.unreachable) from None
                no_response += 1
                if no_response >= NETWORK_ATTEMPTS:
                    self.unreachable = self._redact(f"cannot reach {self.host}: {getattr(e, 'reason', e)}")
                    raise Unreachable(self.unreachable) from None
                print(self._redact(f"[massive] no response from {self.host} on {path} ({getattr(e, 'reason', e)}); "
                                   "retrying once ..."), file=sys.stderr, flush=True)
                self._sleep(1)
                continue
            except ValueError:                       # a header http.client cannot send: only the key is variable
                raise InvalidKey("MASSIVE_API_KEY contains characters that cannot be sent in a header") from None
            self.stats["network_calls"] += 1
            body = self._redact(body)
            msg = _plain(_message(body), 200)
            if status == 401:
                raise InvalidKey(f"key rejected on {path}: {msg or 'HTTP 401'}")
            if status == 429:                        # another program may be using the key
                wait = _retry_after(headers, self._clock())
                wait = WINDOW if wait is None else wait
                if attempt == 0 and wait <= max_wait:
                    print(f"[massive] HTTP 429 on {path}: waiting {wait:.0f}s ...", file=sys.stderr, flush=True)
                    self._sleep(wait)
                    continue
                text = f"Massive rate limit on {path} (HTTP 429): run again in a minute"
                self._fail_fast("throttled", RateLimited, text)
                raise RateLimited(text)
            if status in (402, 403):
                detail = (msg or f"HTTP {status}")[:160]
                raise RestrictedEndpoint(f"{path}: not included in your plan ({detail})")
            if status >= 500:
                last = MassiveError(f"HTTP {status} on {path} (server error)")
                server_errors += 1
                if server_errors >= HTTP_ATTEMPTS or attempt == HTTP_ATTEMPTS:   # no wait after the last try
                    break
                self._sleep(2 ** (server_errors - 1))
                retry_if_free = True
                continue
            if 300 <= status < 400:                  # redirects are not followed: the key must not go elsewhere
                raise MassiveError(f"HTTP {status} on {path} (redirect not followed)")
            if status >= 400:
                raise MassiveError(f"HTTP {status} on {path}: {msg or _plain(body, 200) or ''}")
            self._server_failures = 0
            try:
                data = json.loads(body) if body.strip() else {}
            except ValueError:
                raise MassiveError(f"{path}: response was not JSON: {body[:120]!r}") from None
            if isinstance(data, dict) and str(data.get("status", "")).upper() in ("ERROR", "NOT_AUTHORIZED"):
                raise MassiveError(f"{path}: {msg or _plain(str(data.get('status')), 40)}")
            return data
        if last is not None:                         # this request ended on server errors
            self._server_failures += 1
            if self._server_failures >= SERVER_FAILURES:
                self._fail_fast("server", ServerTrouble,
                                f"Massive answered server errors (HTTP 5xx) to {self._server_failures} requests in a "
                                "row: its other requests are skipped for a few minutes")
            raise last
        raise MassiveError(f"{path}: failed after retries")

    # ------------------------------------------------------------------ public
    def get(self, path: str, ttl=None, wait=None, **params):
        """GET one endpoint, e.g. get('/stocks/v1/short-interest', ticker='NVDA', limit=6): the decoded JSON (a dict,
        or a list for the holiday calendar). Cached; `ttl` (seconds) overrides the endpoint's lifetime and `wait`
        the longest wait for a request slot (0: only when a slot is free now)."""
        path = "/" + path.strip("/")
        params = {k: v for k, v in params.items() if v is not None}
        file = self._cache_path(path, params)
        if self.use_cache and not self.refresh and file.exists() and self._is_fresh(path, file, ttl):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                self.stats["cache_hits"] += 1
                return data
            except (OSError, ValueError):
                pass
        data = self._fetch(path, params, self.max_wait if wait is None else float(wait))
        if self.use_cache:
            tmp = file.with_name(f"{file.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                file.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(data), encoding="utf-8")
                os.replace(tmp, file)
            except OSError:
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return data

    def results(self, path: str, ttl=None, wait=None, **params) -> list:
        """The `results` list of an endpoint (a lone object becomes a one-item list; the holiday calendar is
        already a list)."""
        data = self.get(path, ttl=ttl, wait=wait, **params)
        if isinstance(data, list):
            return data
        if data is not None and not isinstance(data, dict):
            raise MassiveError(f"{path}: unexpected response ({type(data).__name__})")
        res = (data or {}).get("results")
        if isinstance(res, dict):
            return [res]
        if res is not None and not isinstance(res, list):
            raise MassiveError(f"{path}: unexpected results ({type(res).__name__})")
        return list(res or [])


def _retry_after(headers, now: float | None = None) -> float | None:
    """Seconds to wait from Retry-After or x-ratelimit-reset (None when neither is a finite number). A value above
    1e9 is a Unix time, not a number of seconds."""
    for name in ("Retry-After", "x-ratelimit-reset"):
        try:
            v = (headers or {}).get(name)
            if v is None:
                continue
            x = float(str(v).split(",")[0].strip())
            if not math.isfinite(x):
                continue
            if x > 1e9:
                x -= time.time() if now is None else now
            return max(0.0, x) + 0.5
        except (TypeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------- holiday calendar
def calendar_rows(rows) -> dict:
    """NYSE entries of /v1/marketstatus/upcoming -> {date: ("closed", None) | ("early_close", "HH:MM")} (New York
    time)."""
    out = {}
    ny = ZoneInfo(C.MARKET_TZ)
    for r in rows or []:
        if not isinstance(r, dict) or str(r.get("exchange", "")).upper() != "NYSE":
            continue
        try:
            d = date.fromisoformat(str(r["date"])[:10])
        except (KeyError, ValueError):
            continue
        status = str(r.get("status", "")).lower()
        if status == "closed":
            out[d] = ("closed", None)
        elif status in ("early-close", "early_close"):
            hhmm = None
            try:
                t = datetime.fromisoformat(str(r["close"]).replace("Z", "+00:00")).astimezone(ny)
                hhmm = t.strftime("%H:%M")
            except (KeyError, ValueError, OverflowError):
                pass
            out[d] = ("early_close", hhmm)
    return out


def compare_calendar(rows, today: date) -> dict:
    """The NYSE holiday calendar from Massive against config.MARKET_HOLIDAYS / EARLY_CLOSES, from `today` through the
    last date Massive lists: {checked_from, checked_through, n_dates, mismatches: [{date, exchange, exchange_close,
    config, config_close, name}]}; exchange and config are "closed", "early_close" or "open", the *_close fields
    the early close time "HH:MM" (New York) or None. `today` itself is compared only when Massive lists it (an
    "upcoming" list may leave out the current day)."""
    cal = calendar_rows(rows)
    names = {}
    for r in rows or []:
        if isinstance(r, dict) and str(r.get("exchange", "")).upper() == "NYSE":
            names[str(r.get("date"))[:10]] = _plain(r.get("name"))
    upcoming = sorted(d for d in cal if d >= today)
    if not upcoming:
        return {"checked_from": today, "checked_through": None, "n_dates": 0, "mismatches": []}
    last = upcoming[-1]
    mine = {d: ("closed", None) for d in C.MARKET_HOLIDAYS}
    mine.update({d: ("early_close", t.strftime("%H:%M")) for d, t in C.EARLY_CLOSES.items()})
    mismatches = []
    for d in sorted(set(upcoming) | {d for d in mine if today <= d <= last}):
        if d == today and d not in cal:
            continue
        theirs, ours = cal.get(d, ("open", None)), mine.get(d, ("open", None))
        same_time = theirs[1] == ours[1] or theirs[1] is None     # an unreadable close time is not a mismatch
        if theirs[0] != ours[0] or not same_time:
            mismatches.append({"date": d, "exchange": theirs[0], "exchange_close": theirs[1], "config": ours[0],
                               "config_close": ours[1], "name": names.get(d.isoformat())})
    return {"checked_from": today, "checked_through": last, "n_dates": len(upcoming), "mismatches": mismatches}


def _plain(v, limit: int = 80) -> str | None:
    """A holiday name from Massive for metrics.json and the terminal: printable characters only, whitespace collapsed,
    at most `limit` characters."""
    if not isinstance(v, str):
        return None
    return " ".join("".join(ch if ch.isprintable() else " " for ch in v).split())[:limit] or None


def calendar_label(kind: str, close: str | None) -> str:
    """"closed", "open" or "early close 13:00" (for messages)."""
    return f"early close {close}" if kind == "early_close" and close else kind.replace("_", " ")


# ---------------------------------------------------------------------- CLI
def _probes(symbol: str, today: date):
    return [
        (SHORT_INTEREST, {"ticker": symbol, "limit": 2, "sort": "settlement_date.desc"}, "settlement_date"),
        (SHORT_VOLUME, {"ticker": symbol, "limit": 2, "sort": "date.desc"}, "date"),
        (FLOAT, {"ticker": symbol}, "effective_date"),
        (NEWS, {"ticker": symbol, "limit": 5, "order": "desc", "sort": "published_utc"}, "published_utc"),
        (DIVIDENDS, {"ticker": symbol, "limit": 2, "sort": "ex_dividend_date.desc"}, "ex_dividend_date"),
        (TREASURY, {"date.lte": today.isoformat(), "sort": "date.desc", "limit": 1}, "date"),
        (HOLIDAYS, {}, "date"),
    ]


def _cmd_check(symbol: str) -> int:
    client = MassiveClient(refresh=True, max_wait=WINDOW + 5)
    print("Connection")
    print(f"  base URL    : {client.base}")
    print(f"  TLS verify  : on (certificate checked by {client.trust.description})")
    print(f"  cache       : {client.cache_dir}")
    print(f"  rate limit  : {client.per_min} requests a minute (MASSIVE_MAX_PER_MIN)")
    today = client._now_ny().date()
    print(f"\nProbing the endpoints this skill uses with {symbol} (spaced for the rate limit) ...\n")
    print(f"{'endpoint':<30} {'result':<11} detail")
    print("-" * 90)
    holidays = None
    for path, params, field in _probes(symbol, today):
        try:
            rows = client.results(path, **params)
        except InvalidKey as e:
            print(f"\nKey problem: {e}")
            return 2
        except Unreachable as e:
            print(f"\nUnreachable: {e}")
            return 1
        except RestrictedEndpoint as e:
            print(f"{path:<30} {'RESTRICTED':<11} {str(e).split('(', 1)[-1].rstrip(')')[:60]}")
            continue
        except MassiveError as e:
            print(f"{path:<30} {'ERROR':<11} {str(e)[:60]}")
            continue
        if path == HOLIDAYS:
            holidays = rows
        if not rows:
            print(f"{path:<30} {'EMPTY':<11} no rows returned")
            continue
        vals = sorted(str(r.get(field))[:10] for r in rows if isinstance(r, dict) and r.get(field))
        span = f" ({vals[0]} .. {vals[-1]})" if len(vals) > 1 else f" ({vals[0]})" if vals else ""
        print(f"{path:<30} {'OK':<11} {len(rows)} rows{span}")
    if holidays is not None:
        cmp = compare_calendar(holidays, today)
        print("\nHoliday calendar (NYSE) against scripts/config.py:")
        if not cmp["checked_through"]:
            print("  Massive listed no upcoming dates.")
        elif not cmp["mismatches"]:
            print(f"  matches through {cmp['checked_through']} ({cmp['n_dates']} dates)")
        for m in cmp["mismatches"]:
            print(f"  {m['date']}: exchange calendar {calendar_label(m['exchange'], m['exchange_close'])}, config.py "
                  f"{calendar_label(m['config'], m['config_close'])}{' (' + m['name'] + ')' if m.get('name') else ''}")
    print(f"\nNetwork calls used: {client.stats['network_calls']}")
    print("Massive is optional for this skill: without it, short interest, news, the Treasury discount curve and the "
          "calendar check are unavailable and everything from CBOE and FMP still works.")
    return 0


def _cmd_get(path: str, kvs) -> int:
    params = {}
    for kv in kvs:
        if "=" not in kv:
            print(f"Ignoring argument without '=': {kv}", file=sys.stderr)
            continue
        k, v = kv.split("=", 1)
        params[k] = v
    client = MassiveClient(max_wait=WINDOW + 5)
    print(json.dumps(client.get(path, **params), indent=2, ensure_ascii=False))
    return 0


def main(argv) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        from winenv import load_user_env
        load_user_env()
    except Exception:
        pass
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    try:
        if cmd == "check":
            from symbols import normalize
            sym = normalize(argv[1] if len(argv) > 1 else "NVDA")
            return _cmd_check(sym["key"])
        if cmd == "get" and len(argv) >= 2:
            return _cmd_get(argv[1], argv[2:])
        if cmd == "clear-cache":
            cache = default_cache_dir()
            shutil.rmtree(cache, ignore_errors=True)
            print(f"Cleared {cache}")
            return 0
    except (MassiveError, ValueError) as e:
        key = os.environ.get("MASSIVE_API_KEY", "").strip()
        text = str(e).replace(key, "***") if key else str(e)
        print(f"Error: {text}", file=sys.stderr)
        return 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
