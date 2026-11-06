#!/usr/bin/env python3
"""
Minimal client for the Financial Modeling Prep (FMP) *stable* API, used by the
cboe-options-analysis skill (a copy of the fmp-technical-analysis client with this skill's constants).
FMP is optional here: without FMP_API_KEY the analysis runs on CBOE data alone.

- Standard library only; works on Windows, macOS and Linux.
- The API key is read from the FMP_API_KEY environment variable. It never appears
  in cache files, file names or printed output.
- Responses are cached on disk. Daily price data, earnings and dividends are
  reused for the rest of the New York day (a same-day re-run makes no requests), except that
  a response cached before the close buffer (config.MARKET_CLOSE_BUFFER) is refetched once
  the close has passed, so an intraday run cannot freeze a partial bar.
- Endpoints that your plan does not include raise RestrictedEndpoint
  (or return None via try_get) instead of crashing an analysis.
- A host that sends no HTTP response (timeout, refused, DNS) is tried twice; after that the
  client raises Unreachable at once for every later request, so a down proxy fails a run in
  about one timeout instead of retrying each endpoint.

Connection settings (environment variables):
  FMP_API_KEY       required. Official FMP key, or the token a proxy service gave you.
  FMP_BASE_URL      default https://financialmodelingprep.com . Set it to use a
                    third-party proxy, e.g. https://1.2.3.4
  FMP_PATH_PREFIX   path before the endpoint name (default "stable"; auto-detected for proxies)
  FMP_AUTH_STYLE    how the key is sent: header | bearer | x-api-key | query
                    (default "header" = FMP's apikey header; auto-detected for proxies)
  FMP_TLS_VERIFY    "0" turns off HTTPS certificate verification (needed for many
                    bare-IP proxies; less secure). Default "1". Ignored for the official
                    FMP host, whose certificate is always verified.
  FMP_MAX_PER_MIN   request rate cap shared by all threads (default 280).
  FMP_TIMEOUT       seconds to wait for each response (default 30).
  FMP_CACHE_DIR     cache folder (default ~/.cache/cboe-options-fmp).

CLI:
  python fmp_client.py check [SYMBOL]          connection info + which endpoints this skill
                                               uses work on your plan (default NVDA)
  python fmp_client.py get ENDPOINT k=v ...    raw JSON from any stable endpoint
  python fmp_client.py clear-cache             delete the local cache and saved detection
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import config as C

OFFICIAL_BASE = "https://financialmodelingprep.com"
OFFICIAL_HOSTS = {"financialmodelingprep.com", "www.financialmodelingprep.com"}
AUTH_STYLES = ("header", "bearer", "x-api-key", "query")
PREFIX_CANDIDATES = ("stable", "")
USER_AGENT = C.USER_AGENT          # "cboe-options-analysis-skill/1.0"

DAY = 86400
TTL_BY_ENDPOINT = {"profile": DAY}
DEFAULT_TTL = DAY
# Daily-bar endpoints, earnings and dividends: cached until the New York session they belong to
# is over (see _same_session); earnings actuals and dividend declarations posted after the close are
# picked up by the post-close refetch.
SESSION_ENDPOINTS = ("historical-price-eod/", "quote", "earnings", "dividends")
NETWORK_ATTEMPTS = 2   # tries per request when no HTTP response arrives (timeout, refused, DNS)
HTTP_ATTEMPTS = 4      # tries per request on HTTP 429 / 5xx

# Phrases FMP uses when a plan does not cover an endpoint, symbol or parameter.
RESTRICTED_MARKERS = (
    "restricted",
    "subscription",
    "upgrade",
    "premium",
    "not available under your current",
    "exclusive",
)


class FMPError(Exception):
    """Any failure talking to FMP."""


class RestrictedEndpoint(FMPError):
    """The endpoint (or this symbol / parameter) is not included in the current plan."""


class InvalidKey(FMPError):
    """Missing or rejected API key."""


class Unreachable(FMPError):
    """The host sent no HTTP response; nothing else in this run can work either."""


def default_cache_dir() -> Path:
    return Path(os.environ.get("FMP_CACHE_DIR") or (Path.home() / ".cache" / C.CACHE_SUBDIR))


def _error_message(body: str):
    """Return FMP's error text if the body is an error payload, else None."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if isinstance(data, dict):
        for k in ("Error Message", "error", "Error"):
            if k in data and data[k]:
                return str(data[k])
        if "message" in data and len(data) <= 2:
            return str(data["message"])
    return None


def _env_flag(name, default=True):
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


def _is_timeout(exc):
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, TimeoutError) or "timed out" in str(reason).lower()


def _is_cert_error(exc):
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (ssl.SSLCertVerificationError, ssl.CertificateError)) or \
        "CERTIFICATE_VERIFY_FAILED" in str(reason)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _same_session(written: datetime, now: datetime) -> bool:
    """True if a daily-bar response written at `written` is still current at `now` (both NY time):
    same calendar date, and not written before the close buffer when the buffer has since passed.
    Weekends have no session, so any same-date copy stays valid."""
    if written.date() != now.date():
        return False
    if now.weekday() >= 5:
        return True
    close = _minutes(C.MARKET_CLOSE_BUFFER)
    return written.hour * 60 + written.minute >= close or now.hour * 60 + now.minute < close


class FMPClient:
    def __init__(self, api_key=None, cache_dir=None, use_cache=True, refresh=False,
                 max_per_min=None, timeout=30):
        self.api_key = (api_key or os.environ.get("FMP_API_KEY", "")).strip()
        if not self.api_key:
            raise InvalidKey(
                "FMP_API_KEY is not set. Set it as an environment variable "
                "(Windows: setx FMP_API_KEY \"your_key\", then open a new terminal)."
            )
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.use_cache = use_cache
        self.refresh = refresh
        per_min = max_per_min or int(os.environ.get("FMP_MAX_PER_MIN", "280"))
        self.rate = max(per_min, 1) / 60.0  # requests per second
        self.burst = min(10.0, float(max(per_min, 1)))
        try:
            self.timeout = float(os.environ.get("FMP_TIMEOUT") or timeout)
        except ValueError:
            self.timeout = timeout
        self._tokens, self._refilled = self.burst, time.time()
        self._lock = threading.Lock()  # guards the rate limiter, stats and the lists below
        self.stats = {"network_calls": 0, "cache_hits": 0}
        self.restricted = []  # endpoints skipped because the plan lacks them
        self.errors = []      # optional endpoints that failed for other reasons
        self.unreachable = None  # message once a request got no HTTP response; later requests fail fast

        base = (os.environ.get("FMP_BASE_URL") or OFFICIAL_BASE).strip().rstrip("/")
        if "://" not in base:
            base = "https://" + base
        self.base = base
        self.host = urllib.parse.urlparse(base).hostname or base
        self.is_official = self.host.lower() in OFFICIAL_HOSTS
        self.tls_verify = _env_flag("FMP_TLS_VERIFY", True)
        if not self.tls_verify and self.is_official:
            print(f"[fmp] FMP_TLS_VERIFY=0 ignored for {self.host}: certificate verification stays on "
                  f"for the official API.", file=sys.stderr)
            self.tls_verify = True
        self._ssl_ctx = None if self.tls_verify else ssl._create_unverified_context()
        if not self.tls_verify:
            print(f"[fmp] WARNING: HTTPS certificate verification is OFF for {self.host} "
                  f"(FMP_TLS_VERIFY=0).", file=sys.stderr)

        env_prefix = os.environ.get("FMP_PATH_PREFIX")
        env_style = (os.environ.get("FMP_AUTH_STYLE") or "").strip().lower() or None
        if env_style and env_style not in AUTH_STYLES:
            raise FMPError(f"FMP_AUTH_STYLE must be one of {', '.join(AUTH_STYLES)}")
        self.prefix = env_prefix.strip("/") if env_prefix is not None else None
        self.auth_style = env_style
        self.connection_source = "environment"
        if self.is_official:
            self.prefix = "stable" if self.prefix is None else self.prefix
            self.auth_style = self.auth_style or "header"
            self.connection_source = "official defaults" if not (env_prefix or env_style) else "environment"
        elif self.prefix is None or self.auth_style is None:
            saved = self._load_saved()
            if saved and not refresh:
                self.prefix = saved["prefix"] if self.prefix is None else self.prefix
                self.auth_style = self.auth_style or saved["style"]
                self.connection_source = "saved detection"
            else:
                self.detect()

    # ------------------------------------------------------------------ connection
    def _url(self, endpoint, params, prefix=None, style=None):
        prefix = self.prefix if prefix is None else prefix
        style = style or self.auth_style
        params = dict(params)
        if style == "query":
            params["apikey"] = self.api_key
        path = "/".join(p for p in (prefix, endpoint) if p)
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        return f"{self.base}/{path}{query}"

    def _headers(self, style):
        h = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if style == "header":
            h["apikey"] = self.api_key
        elif style == "bearer":
            h["Authorization"] = f"Bearer {self.api_key}"
        elif style == "x-api-key":
            h["X-API-Key"] = self.api_key
        return h

    def _saved_path(self):
        return self.cache_dir / "connection.json"

    def _load_saved(self):
        try:
            data = json.loads(self._saved_path().read_text(encoding="utf-8"))
            entry = data.get(self.base)
            if entry and entry.get("style") in AUTH_STYLES and "prefix" in entry:
                return entry
        except (OSError, ValueError, AttributeError):
            pass
        return None

    def _save(self):
        try:
            path = self._saved_path()
            data = {}
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
            data[self.base] = {"prefix": self.prefix, "style": self.auth_style}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass

    def detect(self, symbol="AAPL"):
        """Find the path prefix and auth style a proxy accepts, by probing `profile`."""
        print(f"[fmp] Detecting request format for {self.host} (waits up to {self.timeout:g}s "
              f"per try) ...", file=sys.stderr, flush=True)
        prefixes = [self.prefix] if self.prefix is not None else list(PREFIX_CANDIDATES)
        styles = [self.auth_style] if self.auth_style else list(AUTH_STYLES)
        seen = []
        for prefix in prefixes:
            for style in styles:
                url = self._url("profile", {"symbol": symbol}, prefix, style)
                for attempt in range(2):  # one retry if the server is momentarily slow
                    try:
                        status, body = self._http_get(url, style)
                        break
                    except (urllib.error.URLError, OSError) as e:  # OSError covers read timeouts
                        if _is_cert_error(e):
                            raise FMPError(self._cert_hint())
                        if attempt == 0 and _is_timeout(e):
                            continue
                        raise Unreachable(self._unreachable_hint(e))
                self._count("network_calls")
                ok = status == 402 or (status == 200 and _error_message(body) is None
                                       and body.strip()[:1] in ("[", "{"))
                seen.append(f"/{prefix or ''} + {style}: HTTP {status}")
                if ok:
                    self.prefix, self.auth_style = prefix, style
                    self.connection_source = "auto-detected"
                    self._save()
                    return
        raise FMPError(
            f"Could not find a working request format for {self.host}. Tried: "
            + "; ".join(seen)
            + ". Check FMP_API_KEY / FMP_BASE_URL, or set FMP_PATH_PREFIX and FMP_AUTH_STYLE "
              "to match the proxy's documentation."
        )

    def _unreachable_hint(self, exc):
        msg = f"cannot reach {self.host}: {getattr(exc, 'reason', exc)}."
        if _is_timeout(exc):
            msg += (f" The server sent no response within {self.timeout:g}s (tried twice). It may be "
                    f"down or overloaded: try again in a few minutes, or set FMP_TIMEOUT=60.")
        else:
            msg += " Check FMP_BASE_URL and the network connection."
        return msg

    def _cert_hint(self):
        return (f"HTTPS certificate verification failed for {self.host}. This is common for "
                f"proxies addressed by a bare IP. To run anyway, set FMP_TLS_VERIFY=0 "
                f"(traffic can then be intercepted; see SKILL.md).")

    def describe(self):
        route = "official FMP API" if self.is_official else f"third-party proxy at {self.host}"
        return {
            "route": route, "base": self.base, "prefix": self.prefix, "auth_style": self.auth_style,
            "source": self.connection_source, "tls_verify": self.tls_verify,
        }

    # ------------------------------------------------------------------ low level
    def _http_get(self, url, style=None):
        req = urllib.request.Request(url, headers=self._headers(style or self.auth_style))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            return e.code, body

    def _redact(self, text: str) -> str:
        return text.replace(self.api_key, "***")

    def _now_ny(self) -> datetime:
        return datetime.now(ZoneInfo(C.MARKET_TZ))

    def _cache_path(self, endpoint, params):
        key = self.base + "|" + endpoint + "?" + urllib.parse.urlencode(sorted(params.items()))
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        safe_ep = endpoint.replace("/", "_")
        sym = "".join(c for c in str(params.get("symbol", "all")) if c.isalnum() or c in ".-_")
        return self.cache_dir / f"{safe_ep}__{sym}__{digest}.json"

    def _is_fresh(self, endpoint, path, ttl):
        """Freshness policy: an explicit ttl or the endpoint's TTL (seconds since written),
        except daily-bar endpoints and the quote, which follow the New York session rule."""
        now = self._now_ny()
        mtime = path.stat().st_mtime
        if ttl is None and endpoint.startswith(SESSION_ENDPOINTS):
            return _same_session(datetime.fromtimestamp(mtime, now.tzinfo), now)
        ttl = TTL_BY_ENDPOINT.get(endpoint, DEFAULT_TTL) if ttl is None else ttl
        return now.timestamp() - mtime < ttl

    def _count(self, key):
        with self._lock:
            self.stats[key] += 1

    def _throttle(self):
        """Token bucket shared by all threads: short bursts of up to 10 requests, and
        about FMP_MAX_PER_MIN per minute over time. A caller that finds the bucket empty
        reserves the next token and sleeps until it is due."""
        with self._lock:
            now = time.time()
            self._tokens = min(self.burst, self._tokens + (now - self._refilled) * self.rate)
            self._refilled = now
            self._tokens -= 1
            wait = -self._tokens / self.rate if self._tokens < 0 else 0.0
        if wait > 0:
            time.sleep(wait)

    def _fetch(self, endpoint, params):
        if self.unreachable:
            raise Unreachable(self.unreachable)
        url = self._url(endpoint, params)
        last_err, no_response = None, 0
        for attempt in range(HTTP_ATTEMPTS):
            self._throttle()
            try:
                status, body = self._http_get(url)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if _is_cert_error(e):
                    raise FMPError(self._cert_hint())
                no_response += 1
                if no_response >= NETWORK_ATTEMPTS:
                    self.unreachable = self._redact(self._unreachable_hint(e))
                    raise Unreachable(self.unreachable)
                print(f"[fmp] no response from {self.host} on {endpoint} ({getattr(e, 'reason', e)}); "
                      f"retrying once ...", file=sys.stderr, flush=True)
                time.sleep(1)
                continue
            self._count("network_calls")
            if status == 429 or status >= 500:
                last_err = FMPError(f"HTTP {status} on {endpoint} (rate limit or server error)")
                time.sleep(2 ** attempt + 1)
                continue
            body = self._redact(body)  # a proxy could echo the request (and a query-string key)
            msg = _error_message(body)
            low = (msg or "").lower()
            if status == 401 or "invalid api key" in low:
                hint = "" if self.is_official else " (proxy: run `fmp_client.py check` to re-detect the request format)"
                raise InvalidKey(f"key rejected on {endpoint}: {msg or status}{hint}")
            if status in (402, 403) or any(m in low for m in RESTRICTED_MARKERS):
                detail = (msg or body or f"HTTP {status}").strip().replace("\n", " ")[:160]
                raise RestrictedEndpoint(f"{endpoint}: not included in your plan ({detail})")
            if status >= 400:
                raise FMPError(f"HTTP {status} on {endpoint}: {(msg or body)[:200]}")
            if msg:
                raise FMPError(f"{endpoint}: {msg[:200]}")
            if not body.strip():
                return []
            try:
                return json.loads(body)
            except ValueError:
                raise FMPError(f"{endpoint}: response was not JSON: {body[:120]!r}")
        raise last_err or FMPError(f"{endpoint}: failed after retries")

    # ------------------------------------------------------------------ public
    def get(self, endpoint, ttl=None, **params):
        """GET a stable endpoint, e.g. get('historical-price-eod/full', symbol='AAPL', **{'from': ..., 'to': ...})."""
        endpoint = endpoint.strip("/")
        params = {k: v for k, v in params.items() if v is not None}
        path = self._cache_path(endpoint, params)
        if self.use_cache and not self.refresh and path.exists() and self._is_fresh(endpoint, path, ttl):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._count("cache_hits")
                return data
            except (OSError, ValueError):
                pass
        data = self._fetch(endpoint, params)
        if self.use_cache:
            # write-then-rename so a parallel reader never sees a half-written file
            tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(data), encoding="utf-8")
                os.replace(tmp, path)
            except OSError:
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return data

    def record_failure(self, endpoint, exc, params):
        """Note an optional endpoint that failed: plan restrictions go to `restricted`, the rest to `errors`."""
        with self._lock:
            if isinstance(exc, RestrictedEndpoint):
                extra = ", ".join(f"{k}={v}" for k, v in params.items()
                                  if k not in ("symbol", "page", "from", "to"))
                label = f"{endpoint} ({extra})" if extra else endpoint
                if label not in self.restricted:
                    self.restricted.append(label)
            else:
                self.errors.append(str(exc))

    def try_get(self, endpoint, **params):
        """Like get(), but returns None (and records why) for optional data."""
        try:
            return self.get(endpoint, **params)
        except (InvalidKey, Unreachable):
            raise
        except FMPError as e:
            self.record_failure(endpoint, e, params)
        return None


def get_with_reason(client, endpoint, **params):
    """client.get() for optional data: (data, None) on success (data may be empty), or
    (None, "restricted: ..." | "error: ...") after recording the failure on the client.
    A rejected key (InvalidKey) or a host that does not answer (Unreachable) still raises,
    since nothing else can work either."""
    try:
        return client.get(endpoint, **params), None
    except (InvalidKey, Unreachable):
        raise
    except FMPError as e:
        client.record_failure(endpoint, e, params)
        kind = "restricted" if isinstance(e, RestrictedEndpoint) else "error"
        return None, f"{kind}: {e}"


# ---------------------------------------------------------------------- CLI
def _probes(symbol, start, end):
    window = {"from": start, "to": end}
    return [
        ("profile", {"symbol": symbol}, ""),
        ("earnings", {"symbol": symbol}, ""),
        ("dividends", {"symbol": symbol}, ""),
        ("historical-price-eod/full", {"symbol": symbol, **window}, "last 30 days"),
        ("historical-price-eod/full", {"symbol": C.SPX_FMP, **window}, f"{C.SPX_FMP}, last 30 days"),
        ("historical-price-eod/full", {"symbol": C.VIX, **window}, f"{C.VIX}, last 30 days"),
    ]


def _probe(client, ep, params):
    try:
        data = client.get(ep, **params)
    except RestrictedEndpoint as e:
        return "RESTRICTED", str(e).split("(", 1)[-1].rstrip(")")[:60]
    except InvalidKey:
        raise
    except FMPError as e:
        return "ERROR", str(e)[:60]
    rows = data if isinstance(data, list) else ([data] if data else [])
    if not rows:
        return "EMPTY", "no rows returned"
    dates = sorted(str(r["date"])[:10] for r in rows if isinstance(r, dict) and r.get("date"))
    span = f" ({dates[0]} .. {dates[-1]})" if len(dates) > 1 else ""
    return "OK", f"{len(rows)} rows{span}"


def _cmd_check(symbol):
    client = FMPClient(refresh=True)
    c = client.describe()
    print("Connection")
    print(f"  route       : {c['route']}")
    print(f"  base URL    : {c['base']}")
    print(f"  path prefix : /{c['prefix'] or ''}")
    print(f"  auth style  : {c['auth_style']}  ({c['source']})")
    print(f"  TLS verify  : {'on' if c['tls_verify'] else 'OFF'}")
    print(f"  cache       : {client.cache_dir}")
    today = client._now_ny().date()
    start = (today - timedelta(days=30)).isoformat()
    print(f"\nProbing the endpoints this skill uses with {symbol} ...\n")
    print(f"{'endpoint':<40} {'params':<22} {'result':<11} detail")
    print("-" * 100)
    try:
        for ep, params, label in _probes(symbol, start, today.isoformat()):
            result, detail = _probe(client, ep, params)
            print(f"{ep:<40} {label:<22} {result:<11} {detail}")
    except InvalidKey as e:
        print(f"\nKey problem: {e}")
        return 2
    print(f"\nNetwork calls used: {client.stats['network_calls']}")
    print("FMP is optional for this skill: without it, earnings, realized volatility, dividends and the price cone "
          "are unavailable and everything from CBOE still works.")
    return 0


def _cmd_get(endpoint, kvs):
    params = {}
    for kv in kvs:
        if "=" not in kv:
            print(f"Ignoring argument without '=': {kv}", file=sys.stderr)
            continue
        k, v = kv.split("=", 1)
        params[k] = v
    client = FMPClient()
    print(json.dumps(client.get(endpoint, **params), indent=2, ensure_ascii=False))
    return 0


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    try:
        if cmd == "check":
            from symbols import normalize_symbol
            return _cmd_check(normalize_symbol(argv[1] if len(argv) > 1 else "NVDA"))
        if cmd == "get" and len(argv) >= 2:
            return _cmd_get(argv[1], argv[2:])
        if cmd == "clear-cache":
            cache = default_cache_dir()
            shutil.rmtree(cache, ignore_errors=True)
            print(f"Cleared {cache}")
            return 0
    except FMPError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
