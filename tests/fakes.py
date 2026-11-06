"""
Offline stand-ins for CBOE and FMP (no network in any test).

- FakeTransport: the urlopen of a real CboeClient; scripted responses per URL, or from_test_data() which serves
  the real fixtures (chains as gzip JSON, index CSVs, gzip 403 AccessDenied for anything else).
- FakeCboe: the CboeClient interface over the fixtures (and a folder of synthetic chains); no cache, every call
  counts as one network call.
- FakeFMP: the FMPClient interface over test-data/fmp, filtering daily bars by from/to like the real endpoint.
"""
from __future__ import annotations

import gzip
import io
import json
import re
import urllib.error
import urllib.parse
from email.message import Message
from pathlib import Path

import pandas as pd

import fmp_client
from cboe_client import NotFound, parse_index_csv

ACCESS_DENIED = (b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code>'
                 b'<Message>Access Denied</Message></Error>')
CHAIN_PATH = re.compile(r"/delayed_quotes/options/(?P<code>[^/]+)\.json$")
INDEX_PATH = re.compile(r"/us_indices/daily_prices/(?P<index>[^/]+)_History\.csv$")


# ---------------------------------------------------------------------------- urllib level
def _headers(d: dict) -> Message:
    m = Message()
    for k, v in (d or {}).items():
        m[k] = v
    return m


class FakeResponse:
    def __init__(self, url: str, status: int = 200, headers: dict | None = None, body: bytes = b"", final_url=None):
        self.url, self.status, self._body = url, status, body
        self.headers = _headers(headers)
        self._final = final_url or url

    def read(self):
        return self._body

    def geturl(self):
        return self._final

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(url: str, code: int, body: bytes = b"", headers: dict | None = None, final_url=None):
    e = urllib.error.HTTPError(final_url or url, code, f"HTTP {code}", _headers(headers), io.BytesIO(body))
    return e


def gz(data: bytes) -> bytes:
    return gzip.compress(data)


class FakeTransport:
    """urlopen replacement. `script` maps a URL substring to a list of steps (used in order, the last one repeats);
    a step is a dict {status, headers, body, final_url} or an exception instance to raise. `fallback(url, req)`
    answers URLs the script does not cover. Every call is recorded in .calls as (url, headers)."""

    def __init__(self, script: dict | None = None, fallback=None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.fallback = fallback
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        headers = {k.lower(): v for k, v in req.header_items()}
        self.calls.append((url, headers))
        for pat, steps in self.script.items():
            if pat in url:
                step = steps.pop(0) if len(steps) > 1 else steps[0]
                return self._answer(url, step)
        if self.fallback is not None:
            return self._answer(url, self.fallback(url, headers))
        raise urllib.error.URLError("no scripted response")

    @staticmethod
    def _answer(url, step):
        if isinstance(step, BaseException):
            raise step
        status = step.get("status", 200)
        final = step.get("final_url")
        if status >= 300:
            raise http_error(url, status, step.get("body", b""), step.get("headers"), final)
        return FakeResponse(url, status, step.get("headers"), step.get("body", b""), final)

    def urls(self) -> list[str]:
        return [u for u, _ in self.calls]

    @classmethod
    def from_test_data(cls, test_data, date: str = "2026-11-05", extra_dir=None, script=None):
        """Serve the real fixtures by URL path (host ignored): chains from cboe/<date>/<CODE without _>.json.gz
        (then extra_dir), index CSVs from cboe_indices/; anything else is a gzip 403 AccessDenied."""
        test_data = Path(test_data)

        def answer(url, headers):
            path = urllib.parse.urlsplit(url).path
            m = CHAIN_PATH.search(path)
            if m:
                code = m.group("code")
                for base in (test_data / "cboe" / date, Path(extra_dir) if extra_dir else None):
                    if base is not None and code == code.upper() and (base / f"{code.lstrip('_')}.json.gz").is_file():
                        if code.lstrip("_") in INDEX_LIKE and not code.startswith("_"):
                            break                              # SPX.json is a 403 on CBOE: indices need _SPX
                        body = (base / f"{code.lstrip('_')}.json.gz").read_bytes()
                        return {"status": 200, "body": body,
                                "headers": {"Content-Encoding": "gzip", "Content-Type": "application/json",
                                            "ETag": f'W/"{len(body)}"', "Last-Modified": "Fri, 06 Nov 2026 01:05:00 GMT"}}
            m = INDEX_PATH.search(path)
            if m:
                p = test_data / "cboe_indices" / f"{m.group('index')}_History.csv"
                if p.is_file():
                    return {"status": 200, "body": p.read_bytes(), "headers": {"Content-Type": "text/csv"}}
            return {"status": 403, "body": gz(ACCESS_DENIED),
                    "headers": {"Content-Encoding": "gzip", "Content-Type": "application/xml"}}

        return cls(script or {}, fallback=answer)


INDEX_LIKE = {"SPX", "VIX", "NDX", "RUT", "XSP", "OEX", "DJX"}


# ---------------------------------------------------------------------------- CboeClient stand-in
class FakeCboe:
    """get_chain / get_index_history over the fixtures; transform(payload) edits each chain (a deep copy)."""

    def __init__(self, test_data, date: str = "2026-11-05", transform=None, extra_dir=None, missing=()):
        self.test_data, self.date = Path(test_data), date
        self.transform, self.extra_dir = transform, (Path(extra_dir) if extra_dir else None)
        self.missing = set(missing)
        self.stats = {"network_calls": 0, "cache_hits": 0}
        self.requests: list[str] = []
        self.fetch_info: dict = {}
        self.keep_raw, self.last_raw = False, None

    def _chain_file(self, symbol: str):
        name = f"{symbol.lstrip('_')}.json.gz"
        for base in (self.test_data / "cboe" / self.date, self.extra_dir):
            if base is not None and (base / name).is_file():
                return base / name
        return None

    def get_chain(self, symbol: str) -> dict:
        self.stats["network_calls"] += 1
        self.requests.append(symbol)
        sym = str(symbol).upper()
        f = self._chain_file(sym)
        bare = sym.lstrip("_")
        if f is None or sym in self.missing or (bare in INDEX_LIKE and not sym.startswith("_")):
            raise NotFound(f"CBOE has no data for {sym}.json (HTTP 403)")
        data = f.read_bytes()
        self.last_raw = data if self.keep_raw else None
        payload = json.loads(gzip.decompress(data))
        return self.transform(payload) if self.transform else payload

    def get_index_history(self, index: str) -> list:
        self.stats["network_calls"] += 1
        self.requests.append(f"{index}_History.csv")
        p = self.test_data / "cboe_indices" / f"{index}_History.csv"
        if not p.is_file() or index in self.missing:
            raise NotFound(f"CBOE has no data for {index}_History.csv (HTTP 403)")
        return parse_index_csv(p.read_text(encoding="utf-8"), index)

    def describe(self) -> dict:
        return {"source": "CBOE delayed quotes", "base": "https://cdn.cboe.com/api/global",
                "redirected_to": "https://cdn-api.cboe.com", "calls": self.stats["network_calls"]}


# ---------------------------------------------------------------------------- FMPClient stand-in
class FakeFMP:
    """FMP over test-data/fmp: profile_<SYM>.json, earnings_<SYM>.json, dividends_<SYM>.json, eod_<SYM>.json
    (eod_idx_<NAME>.json for ^NAME). Missing files answer []; daily bars are filtered by from/to."""

    def __init__(self, test_data, blocked=(), key_rejected=False, unreachable=False, base=fmp_client.OFFICIAL_BASE,
                 earnings=None):
        self.dir = Path(test_data) / "fmp"
        self.blocked = set(blocked)
        self.key_rejected, self.unreachable = key_rejected, unreachable
        self.base = base
        self.earnings_override = earnings or {}
        self.stats = {"network_calls": 0, "cache_hits": 0}
        self.requests: list[tuple[str, dict]] = []
        self.restricted, self.errors = [], []

    def _load(self, name: str):
        p = self.dir / name
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []

    def get(self, endpoint, ttl=None, **params):
        self.stats["network_calls"] += 1
        self.requests.append((endpoint, dict(params)))
        if self.unreachable:
            raise fmp_client.Unreachable("cannot reach financialmodelingprep.com: timed out")
        if self.key_rejected:
            raise fmp_client.InvalidKey("HTTP 401: Invalid API KEY")
        sym = params.get("symbol")
        if endpoint in self.blocked or (endpoint, sym) in self.blocked:
            raise fmp_client.RestrictedEndpoint(f"{endpoint}: not included in your plan (HTTP 402)")
        if endpoint == "profile":
            return self._load(f"profile_{sym}.json")
        if endpoint == "earnings":
            if sym in self.earnings_override:
                return self.earnings_override[sym]
            return self._load(f"earnings_{sym}.json")
        if endpoint == "dividends":
            return self._load(f"dividends_{sym}.json")
        if endpoint == "historical-price-eod/full":
            name = f"eod_idx_{sym[1:]}.json" if sym.startswith("^") else f"eod_{sym}.json"
            rows = self._load(name)
            lo, hi = pd.Timestamp(params["from"]), pd.Timestamp(params["to"])
            return [r for r in rows if lo <= pd.Timestamp(r["date"]) <= hi]
        raise fmp_client.FMPError(f"HTTP 404 on {endpoint}")

    def describe(self):
        host = urllib.parse.urlsplit(self.base).hostname or self.base
        official = host in fmp_client.OFFICIAL_HOSTS
        return {"route": "official FMP API" if official else f"third-party proxy at {host}", "base": self.base,
                "prefix": "stable", "auth_style": "header", "source": "test", "tls_verify": official}

    def calls_to(self, endpoint: str, symbol: str | None = None):
        return [p for e, p in self.requests if e == endpoint and (symbol is None or p.get("symbol") == symbol)]
