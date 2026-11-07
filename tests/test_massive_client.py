"""massive_client.py offline: the key only in the Authorization header, HTTPS only, the cache, the error mapping,
retries and fail-fast, the rate window shared through rate-window.json, the holiday calendar against config.py, the
command line, and the key redaction of analyze.py and ui/server.py. Every request goes to fakes.FakeTransport (or a
patched opener); time and sleeps come from a fake clock, so no test waits or reaches the network."""
from __future__ import annotations

import http.client
import json
import os
import ssl
import time
import traceback
import urllib.error
import urllib.request
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import config as C
import massive_client as MC
import synth
import tls
from conftest import SKILL
from fakes import FakeResponse, FakeTransport

KEY = "mk-test-SECRET-7f3a9c41"          # made up: it must never leave the Authorization header
BASE = C.MASSIVE_BASE
NY = ZoneInfo(C.MARKET_TZ)
SETTINGS = ("MASSIVE_MAX_PER_MIN", "MASSIVE_MAX_WAIT", "MASSIVE_TIMEOUT", "MASSIVE_CACHE_DIR")


@pytest.fixture(autouse=True)
def massive_settings(monkeypatch):
    """No MASSIVE_* setting of this machine applies (conftest.no_massive_key already removed the key)."""
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)


class Clock:
    """clock() and sleep() of a MassiveClient: sleeping moves the clock, and every sleep is recorded."""

    def __init__(self, t: float = 1_000_000.0):
        self.t, self.sleeps = float(t), []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def tick(self, seconds: float) -> None:
        self.t += seconds


def ok(obj, status: int = 200, headers: dict | None = None) -> dict:
    """A FakeTransport step: obj as a JSON body."""
    return {"status": status, "body": json.dumps(obj).encode("utf-8"),
            "headers": {"Content-Type": "application/json", **(headers or {})}}


def make(tmp_path, transport, clock: Clock | None = None, **kw) -> MC.MassiveClient:
    """A client on the fake transport and clock; 100 requests a minute unless the test is about the rate limit."""
    clock = clock or Clock()
    kw.setdefault("max_per_min", 100)
    return MC.MassiveClient(api_key=KEY, cache_dir=tmp_path / "cache", urlopen=transport, sleep=clock.sleep,
                            clock=clock, **kw)


def files(folder) -> list:
    return sorted(p for p in folder.rglob("*") if p.is_file()) if folder.exists() else []


def verify_error(message: str) -> urllib.error.URLError:
    err = ssl.SSLCertVerificationError(1, f"[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: {message}")
    err.verify_message = message
    return urllib.error.URLError(err)


PROXY_CERT = {"subject": "api.massive.com", "issuer": "Proxy Root (Example Antivirus)", "not_before": "2026-11-01",
              "not_after": "2027-11-01", "names": ["api.massive.com"], "self_signed": False}


# ---------------------------------------------------------------------------- the key
def test_key_only_in_the_authorization_header(tmp_path):
    t = FakeTransport({MC.SHORT_INTEREST: [ok({"results": [{"settlement_date": "2026-10-31"}], "status": "OK"})]})
    c = make(tmp_path, t)
    rows = c.results(MC.SHORT_INTEREST, ticker="ACME", limit=2, sort="settlement_date.desc", unused=None)
    assert rows == [{"settlement_date": "2026-10-31"}]
    (url, headers), = t.calls
    assert url == f"{BASE}/stocks/v1/short-interest?ticker=ACME&limit=2&sort=settlement_date.desc"
    assert headers["authorization"] == f"Bearer {KEY}" and KEY not in url
    assert headers["accept"] == "application/json" and headers["user-agent"] == C.USER_AGENT
    stored = files(tmp_path / "cache")
    names = [p.name for p in stored]
    assert MC.RATE_FILE in names and not [n for n in names if n.endswith(".tmp")]
    (cached,) = [p for p in stored if p.name != MC.RATE_FILE]
    assert cached.name.startswith("stocks_v1_short-interest__ACME__") and cached.suffix == ".json"
    for p in stored:
        assert KEY not in p.name and KEY not in p.read_text(encoding="utf-8")
    assert KEY not in json.dumps(c.describe()) and KEY not in repr(c.stats)


ECHO = f"bad request for apiKey={KEY}"


@pytest.mark.parametrize("step, exc, start", [
    (ok({"status": "ERROR", "error": ECHO}, status=400), MC.MassiveError,
     "HTTP 400 on /v2/reference/news: bad request"),
    (ok({"status": "ERROR", "error": ECHO}, status=401), MC.InvalidKey, "key rejected on /v2/reference/news: bad"),
    (ok({"status": "NOT_AUTHORIZED", "message": ECHO}, status=403), MC.RestrictedEndpoint,
     "/v2/reference/news: not included in your plan (bad request"),
    (ok({"status": "ERROR", "error": ECHO}), MC.MassiveError, "/v2/reference/news: bad request"),
    ({"status": 200, "body": f"<html>{ECHO}</html>".encode()}, MC.MassiveError,
     "/v2/reference/news: response was not JSON: '<html>bad request"),
    ({"status": 404, "body": ECHO.encode()}, MC.MassiveError, "HTTP 404 on /v2/reference/news: bad request"),
    (urllib.error.URLError(f"proxy refused {ECHO}"), MC.Unreachable, "cannot reach api.massive.com: proxy refused"),
    (verify_error(f"self-signed certificate ({ECHO})"), MC.Unreachable,
     "HTTPS certificate verification failed for api.massive.com: self-signed certificate (bad request"),
], ids=["400", "401", "403", "200-error", "not-json", "404-text", "no-response", "certificate"])
def test_the_key_never_appears_in_an_error_even_when_echoed(tmp_path, capsys, step, exc, start):
    c = make(tmp_path, FakeTransport({"massive.com": [step]}))
    with pytest.raises(MC.MassiveError) as e:
        c.results(MC.NEWS, ticker="ACME")
    text = str(e.value)
    assert type(e.value) is exc and text.startswith(start), text
    assert KEY not in text and "apiKey=***" in text
    assert KEY not in "".join(traceback.format_exception(e.value))    # nor in a chained exception
    assert all(KEY not in p.read_text(encoding="utf-8") for p in files(tmp_path / "cache"))
    out, err = capsys.readouterr()
    assert KEY not in out + err                                        # nor in a progress line


@pytest.mark.parametrize("key", ["\u201cmk-abc123\u201d", "mk-abc\u200b123", "mk-abc\n123", "mk-ab\u00e9c", "\ufeffmk-abc"])
def test_a_key_with_characters_that_cannot_be_sent_is_rejected_at_start(tmp_path, monkeypatch, key):
    import fetch
    with pytest.raises(MC.InvalidKey, match="contains characters that cannot be sent") as e:
        MC.MassiveClient(api_key=key, cache_dir=tmp_path, urlopen=FakeTransport())
    assert "abc" not in str(e.value)
    monkeypatch.setenv("MASSIVE_API_KEY", key)
    assert fetch.make_massive() == (None, "Massive key rejected")       # the run goes on without Massive


def test_a_header_that_cannot_be_sent_is_a_key_problem_without_the_key(tmp_path):
    t = FakeTransport({"massive.com": [UnicodeEncodeError("latin-1", f"Bearer {KEY}", 7, 8, "ordinal not in range")]})
    with pytest.raises(MC.InvalidKey, match="cannot be sent in a header") as e:
        make(tmp_path, t).results(MC.NEWS, ticker="ACME")
    assert KEY not in "".join(traceback.format_exception(e.value))


def test_vendor_messages_are_plain_text(tmp_path):
    body = json.dumps({"status": "ERROR", "error": "bad\x1b[31m request\n\x00" + "x" * 500}).encode()
    c = make(tmp_path, FakeTransport({"massive.com": [{"status": 400, "body": body}]}))
    with pytest.raises(MC.MassiveError) as e:
        c.results(MC.NEWS, ticker="ACME")
    text = str(e.value)
    assert text.startswith("HTTP 400 on /v2/reference/news: bad [31m request xxx") and text.isprintable()
    assert len(text) == len("HTTP 400 on /v2/reference/news: ") + 200


def test_https_only_and_a_key_is_required(tmp_path):
    t = FakeTransport()
    with pytest.raises(MC.MassiveError, match="must use https"):
        MC.MassiveClient(api_key=KEY, cache_dir=tmp_path, base="http://api.massive.com", urlopen=t)
    with pytest.raises(MC.InvalidKey, match="MASSIVE_API_KEY is not set"):
        MC.MassiveClient(cache_dir=tmp_path, urlopen=t)                  # conftest removed the variable
    with pytest.raises(MC.InvalidKey):
        MC.MassiveClient(api_key="   ", cache_dir=tmp_path, urlopen=t)
    c = MC.MassiveClient(api_key=f"  {KEY}\n", cache_dir=tmp_path, base=BASE + "/", urlopen=t)
    assert (c.api_key, c.base, c.host, c.cache_dir) == (KEY, BASE, "api.massive.com", tmp_path)
    assert c.describe() == {"route": "official Massive API", "base": BASE, "tls_verify": True, "tls_trust": None,
                            "max_per_min": C.MASSIVE_MAX_PER_MIN, "max_wait": float(C.MASSIVE_MAX_WAIT)}
    assert t.calls == []                                                  # nothing is sent at start-up


def test_default_cache_dir(monkeypatch, tmp_path):
    assert MC.default_cache_dir() == MC.Path.home() / ".cache" / C.MASSIVE_CACHE_SUBDIR
    monkeypatch.setenv("MASSIVE_CACHE_DIR", str(tmp_path / "elsewhere"))
    assert MC.default_cache_dir() == tmp_path / "elsewhere"
    c = MC.MassiveClient(api_key=KEY, urlopen=FakeTransport())
    assert c.cache_dir == tmp_path / "elsewhere"


# ---------------------------------------------------------------------------- the cache
def age(path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def cached(tmp_path, prefix: str):
    (p,) = [f for f in files(tmp_path / "cache") if f.name.startswith(prefix)]
    return p


def test_cache_hit_then_expiry_by_endpoint(tmp_path):
    t = FakeTransport({MC.TREASURY: [ok({"results": [{"date": "2026-11-05"}]})],
                       MC.NEWS: [ok({"results": [{"id": "n1"}]})]})
    c = make(tmp_path, t)
    first = c.results(MC.TREASURY, sort="date.desc", limit=1)
    assert c.results(MC.TREASURY, sort="date.desc", limit=1) == first == [{"date": "2026-11-05"}]
    assert len(t.calls) == 1 and c.stats == {"network_calls": 1, "cache_hits": 1}
    f = cached(tmp_path, "fed_v1_treasury-yields__all__")
    age(f, MC.TTL[MC.TREASURY] - 60)                                    # still inside its 6 hours
    c.results(MC.TREASURY, sort="date.desc", limit=1)
    assert len(t.calls) == 1 and c.stats["cache_hits"] == 2
    age(f, MC.TTL[MC.TREASURY] + 60)                                    # expired
    c.results(MC.TREASURY, sort="date.desc", limit=1)
    assert len(t.calls) == 2 and c.stats == {"network_calls": 2, "cache_hits": 2}
    c.results(MC.TREASURY, sort="date.desc", limit=2)                   # another query, another file
    assert len(t.calls) == 3

    c.results(MC.NEWS, ticker="ACME")
    news = cached(tmp_path, "v2_reference_news__ACME__")
    age(news, MC.TTL[MC.TREASURY] - 60)                                 # fresh for Treasury, stale for news
    c.results(MC.NEWS, ticker="ACME")
    assert len(t.calls) == 5
    c.results(MC.NEWS, ticker="ACME", ttl=0)                            # ttl overrides the endpoint's lifetime
    c.results(MC.NEWS, ticker="ACME", ttl=3600)
    assert len(t.calls) == 6
    assert MC.TTL[MC.NEWS] < MC.TTL[MC.TREASURY] < MC.TTL[MC.HOLIDAYS]


def test_unknown_endpoint_uses_the_default_ttl_and_a_broken_cache_file_is_refetched(tmp_path):
    t = FakeTransport({"/v3/other": [ok({"results": [1]}), ok({"results": [2]})]})
    c = make(tmp_path, t)
    assert c.results("v3/other/", ticker="X") == [1]                    # the path is normalised
    f = cached(tmp_path, "v3_other__X__")
    age(f, MC.DEFAULT_TTL - 60)
    assert c.results("/v3/other", ticker="X") == [1] and len(t.calls) == 1
    f.write_text("{broken", encoding="utf-8")
    assert c.results("/v3/other", ticker="X") == [2] and len(t.calls) == 2
    assert json.loads(f.read_text(encoding="utf-8")) == {"results": [2]}


def test_refresh_bypasses_the_cache_but_refills_it(tmp_path):
    t = FakeTransport({MC.NEWS: [ok({"results": [{"id": "a"}]}), ok({"results": [{"id": "b"}]})]})
    fresh = make(tmp_path, t, refresh=True)
    assert fresh.results(MC.NEWS, ticker="ACME") == [{"id": "a"}]
    assert fresh.results(MC.NEWS, ticker="ACME") == [{"id": "b"}] and len(t.calls) == 2
    plain = make(tmp_path, t)
    assert plain.results(MC.NEWS, ticker="ACME") == [{"id": "b"}] and len(t.calls) == 2
    assert plain.stats == {"network_calls": 0, "cache_hits": 1}
    nocache = make(tmp_path / "other", t, use_cache=False)
    nocache.results(MC.NEWS, ticker="ACME")
    nocache.results(MC.NEWS, ticker="ACME")
    assert len(t.calls) == 4 and [p.name for p in files(tmp_path / "other" / "cache")] == [MC.RATE_FILE]


# ---------------------------------------------------------------------------- response shapes
def test_results_of_each_response_shape(tmp_path):
    holidays = [{"date": "2026-11-26", "exchange": "NYSE", "name": "Thanksgiving", "status": "closed"}]
    t = FakeTransport({
        MC.SHORT_INTEREST: [ok({"results": [{"a": 1}, {"a": 2}], "status": "OK", "next_url": "https://x"})],
        MC.FLOAT: [ok({"results": {"ticker": "ACME", "free_float": 1}, "status": "OK"})],
        MC.HOLIDAYS: [ok(holidays)],
        MC.DIVIDENDS: [ok({"status": "OK"})],
        MC.SHORT_VOLUME: [ok({"results": None, "status": "OK"})],
        MC.NEWS: [{"status": 200, "body": b"  "}],
        MC.TREASURY: [ok(None)],
    })
    c = make(tmp_path, t)
    assert c.results(MC.SHORT_INTEREST, ticker="ACME") == [{"a": 1}, {"a": 2}]
    assert c.results(MC.FLOAT, ticker="ACME") == [{"ticker": "ACME", "free_float": 1}]
    assert c.results(MC.HOLIDAYS, wait=0) == holidays and c.get(MC.HOLIDAYS) == holidays
    assert c.results(MC.DIVIDENDS, ticker="ACME") == []
    assert c.results(MC.SHORT_VOLUME, ticker="ACME") == []
    assert c.results(MC.NEWS, ticker="ACME") == []                      # an empty body
    assert c.results(MC.TREASURY) == []
    assert c.get(MC.FLOAT, ticker="ACME") == {"results": {"ticker": "ACME", "free_float": 1}, "status": "OK"}


@pytest.mark.parametrize("body, text", [
    ('"OK"', "/v2/reference/news: unexpected response (str)"),
    ("42", "/v2/reference/news: unexpected response (int)"),
    ('{"results": 5}', "/v2/reference/news: unexpected results (int)"),
    ('{"results": "abc"}', "/v2/reference/news: unexpected results (str)"),
])
def test_an_unexpected_shape_is_a_massive_error(tmp_path, body, text):
    c = make(tmp_path, FakeTransport({MC.NEWS: [{"status": 200, "body": body.encode()}]}))
    with pytest.raises(MC.MassiveError) as e:
        c.results(MC.NEWS, ticker="ACME")
    assert type(e.value) is MC.MassiveError and str(e.value) == text


# ---------------------------------------------------------------------------- HTTP errors
@pytest.mark.parametrize("step, exc, text", [
    (ok({"status": "ERROR", "error": "Unknown API Key"}, status=401), MC.InvalidKey,
     "key rejected on /stocks/v1/short-volume: Unknown API Key"),
    ({"status": 401, "body": b""}, MC.InvalidKey, "key rejected on /stocks/v1/short-volume: HTTP 401"),
    (ok({"status": "NOT_AUTHORIZED", "message": "You are not entitled to this data.\nUpgrade."}, status=403),
     MC.RestrictedEndpoint, "/stocks/v1/short-volume: not included in your plan (You are not entitled to this data. "
                            "Upgrade.)"),
    ({"status": 402, "body": b""}, MC.RestrictedEndpoint, "/stocks/v1/short-volume: not included in your plan "
                                                         "(HTTP 402)"),
    (ok({"message": "x" * 400}, status=403), MC.RestrictedEndpoint,
     "/stocks/v1/short-volume: not included in your plan (" + "x" * 160 + ")"),
    (ok({"status": "ERROR", "error": "Not found"}, status=404), MC.MassiveError,
     "HTTP 404 on /stocks/v1/short-volume: Not found"),
    (ok({"status": "ERROR", "error": "Invalid date"}), MC.MassiveError, "/stocks/v1/short-volume: Invalid date"),
    (ok({"status": "NOT_AUTHORIZED"}), MC.MassiveError, "/stocks/v1/short-volume: NOT_AUTHORIZED"),
], ids=["401", "401-empty", "403", "402", "403-long", "404", "200-error", "200-not-authorized"])
def test_http_errors_map_to_exceptions(tmp_path, step, exc, text):
    t = FakeTransport({MC.SHORT_VOLUME: [step]})
    clock = Clock()
    c = make(tmp_path, t, clock)
    with pytest.raises(MC.MassiveError) as e:
        c.results(MC.SHORT_VOLUME, ticker="ACME")
    assert type(e.value) is exc and str(e.value) == text
    assert len(t.calls) == 1 and clock.sleeps == [] and c.stats["network_calls"] == 1    # no retry
    assert [p.name for p in files(tmp_path / "cache")] == [MC.RATE_FILE]                  # an error is not cached


def test_server_errors_are_retried_with_backoff(tmp_path):
    t = FakeTransport({MC.NEWS: [{"status": 503, "body": b"busy"}]})
    clock = Clock()
    c = make(tmp_path, t, clock)
    with pytest.raises(MC.MassiveError) as e:
        c.results(MC.NEWS, ticker="ACME")
    assert type(e.value) is MC.MassiveError and str(e.value) == "HTTP 503 on /v2/reference/news (server error)"
    assert len(t.calls) == MC.HTTP_ATTEMPTS == c.stats["network_calls"]
    assert clock.sleeps == [2 ** i for i in range(MC.HTTP_ATTEMPTS - 1)]    # 1, 2: no wait after the last try
    t2 = FakeTransport({MC.NEWS: [{"status": 502, "body": b""}, ok({"results": [{"id": "x"}]})]})
    clock2 = Clock()
    assert make(tmp_path / "b", t2, clock2).results(MC.NEWS, ticker="ACME") == [{"id": "x"}]
    assert len(t2.calls) == 2 and clock2.sleeps == [1]


def test_a_429_then_server_errors_still_tries_each_at_most_three_times(tmp_path):
    t = FakeTransport({MC.NEWS: [ok({}, status=429, headers={"Retry-After": "1"}), {"status": 500, "body": b""}]})
    clock = Clock()
    with pytest.raises(MC.MassiveError, match=r"HTTP 500 on /v2/reference/news \(server error\)"):
        make(tmp_path, t, clock).results(MC.NEWS, ticker="ACME")
    assert len(t.calls) == 1 + MC.HTTP_ATTEMPTS and clock.sleeps == [1.5, 1, 2]


def test_server_error_retries_only_take_a_free_slot(tmp_path):
    t = FakeTransport({MC.NEWS: [{"status": 503, "body": b"busy"}]})
    clock = Clock()
    with pytest.raises(MC.MassiveError, match=r"HTTP 503 on /v2/reference/news \(server error\)"):
        make(tmp_path, t, clock, max_per_min=2).results(MC.NEWS, ticker="ACME")
    assert len(t.calls) == 2 and clock.sleeps == [1, 2]       # a third try would have waited for the window


def test_server_errors_on_requests_in_a_row_skip_the_rest_for_a_while(tmp_path):
    t = FakeTransport({MC.SHORT_INTEREST: [ok({"results": [{"a": 1}]})], "massive.com": [{"status": 500, "body": b""}]})
    clock = Clock()
    c = make(tmp_path, t, clock)
    assert c.results(MC.SHORT_INTEREST, ticker="ACME") == [{"a": 1}]
    for path in (MC.NEWS, MC.FLOAT):
        with pytest.raises(MC.MassiveError, match="server error") as e:
            c.results(path, ticker="ACME")
        assert type(e.value) is MC.MassiveError
    n = len(t.calls)
    assert n == 1 + MC.SERVER_FAILURES * MC.HTTP_ATTEMPTS
    for path in (MC.DIVIDENDS, MC.SHORT_VOLUME):
        with pytest.raises(MC.ServerTrouble, match=r"^Massive answered server errors \(HTTP 5xx\) to 2 requests in a"):
            c.results(path, ticker="ACME")
    assert len(t.calls) == n                                          # no request
    assert c.results(MC.SHORT_INTEREST, ticker="ACME") == [{"a": 1}]   # the cache still answers
    clock.tick(MC.FAIL_FAST["server"])
    with pytest.raises(MC.MassiveError, match="server error"):
        c.results(MC.DIVIDENDS, ticker="ACME")
    assert len(t.calls) == n + MC.HTTP_ATTEMPTS                       # asked again after the pause


def test_a_success_between_server_errors_resets_the_count(tmp_path):
    t = FakeTransport({MC.NEWS: [{"status": 500, "body": b""}], MC.FLOAT: [{"status": 502, "body": b""}],
                       MC.SHORT_INTEREST: [ok({"results": []})]})
    c = make(tmp_path, t, Clock(), use_cache=False)
    for path in (MC.NEWS, MC.SHORT_INTEREST, MC.FLOAT, MC.SHORT_INTEREST, MC.NEWS):
        if path == MC.SHORT_INTEREST:
            assert c.results(path, ticker="ACME") == []
            continue
        with pytest.raises(MC.MassiveError) as e:
            c.results(path, ticker="ACME")
        assert type(e.value) is MC.MassiveError                     # never ServerTrouble: not in a row


@pytest.mark.parametrize("status", [301, 302, 303, 304, 307, 308])
def test_a_redirect_is_an_error_and_nothing_is_cached(tmp_path, status):
    t = FakeTransport({"massive.com": [{"status": status, "body": b'{"results": [{"short_interest": 1}]}',
                                        "headers": {"Location": "http://elsewhere.example/x"}}]})
    c = make(tmp_path, t)
    with pytest.raises(MC.MassiveError, match=rf"^HTTP {status} on /stocks/v1/short-interest \(redirect not followed\)"):
        c.results(MC.SHORT_INTEREST, ticker="ACME")
    assert len(t.calls) == 1 and [p.name for p in files(tmp_path / "cache")] == [MC.RATE_FILE]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("location", ["http://127.0.0.1:{port}/elsewhere", "http://[gateway]/blocked",
                                      "http://[broken/x"])
def test_the_opener_never_follows_a_redirect(monkeypatch, status, location):
    """A local plain-HTTP server answers a redirect to another path: the client's opener stops there, so the
    Authorization header (the key) never reaches the new address; a malformed Location is never parsed."""
    import http.server
    import threading
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.path, self.headers.get("Authorization")))
            if self.path == "/start":
                self.send_response(status)
                self.send_header("Location", location.format(port=self.server.server_port))
            else:
                self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.setenv(name, "*")
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        opener = MC.build_opener(ssl.create_default_context())
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/start",
                                     headers={"Authorization": f"Bearer {KEY}"})
        with pytest.raises(urllib.error.HTTPError) as e:
            opener.open(req, timeout=10)
        assert e.value.code == status
    finally:
        server.shutdown()
        server.server_close()
    assert hits == [("/start", f"Bearer {KEY}")]                      # /elsewhere was never asked
    assert any(isinstance(h, MC._NoRedirect) for h in opener.handlers)
    assert not any(type(h) is urllib.request.HTTPRedirectHandler for h in opener.handlers)


def test_only_a_header_error_is_a_key_problem(tmp_path):
    """A ValueError while sending is a key problem only when http.client refused a header (the key is the only
    header that varies); any other ValueError fails that request alone, and the next request still goes out."""
    secret = "proxy URL with no authority: 'http:/user:pa55@proxy:3128'"
    t = FakeTransport({MC.SHORT_INTEREST: [ValueError(secret)],
                       MC.FLOAT: [UnicodeEncodeError("ascii", "/v2/ré", 4, 5, "ordinal not in range(128)")],
                       MC.NEWS: [ok({"results": [{"id": "n1"}]})]})
    c = make(tmp_path, t)
    with pytest.raises(MC.MassiveError) as e:
        c.results(MC.SHORT_INTEREST, ticker="ACME")
    assert type(e.value) is MC.MassiveError and str(e.value) == "/stocks/v1/short-interest: request failed (ValueError)"
    with pytest.raises(MC.MassiveError) as e:                                   # a request line that is not ASCII
        c.results(MC.FLOAT, ticker="ACME")
    assert type(e.value) is MC.MassiveError and "request failed (UnicodeEncodeError: 'ascii' codec" in str(e.value)
    assert c.results(MC.NEWS, ticker="ACME") == [{"id": "n1"}]
    bad = make(tmp_path / "b", FakeTransport({MC.NEWS: [ValueError(f"Invalid header value b'Bearer {KEY}'")]}))
    with pytest.raises(MC.InvalidKey) as e:
        bad.results(MC.NEWS, ticker="ACME")
    assert KEY not in str(e.value) and "cannot be sent in a header" in str(e.value)
    enc = make(tmp_path / "c", FakeTransport({MC.NEWS: [UnicodeEncodeError("latin-1", "“", 0, 1, "x")]}))
    with pytest.raises(MC.InvalidKey):
        enc.results(MC.NEWS, ticker="ACME")


# ---------------------------------------------------------------------------- no response, certificates
@pytest.mark.parametrize("failure", [urllib.error.URLError("timed out"), TimeoutError("timed out"),
                                     ConnectionResetError("timed out"), http.client.IncompleteRead(b"timed out")],
                         ids=["URLError", "TimeoutError", "OSError", "HTTPException"])
def test_no_response_twice_is_unreachable_and_later_requests_fail_fast(tmp_path, failure):
    t = FakeTransport({MC.SHORT_INTEREST: [ok({"results": [{"a": 1}]})], "massive.com": [failure]})
    clock = Clock()
    c = make(tmp_path, t, clock)
    assert c.results(MC.SHORT_INTEREST, ticker="ACME") == [{"a": 1}]   # cached before the host went away
    with pytest.raises(MC.Unreachable) as e:
        c.results(MC.NEWS, ticker="ACME")
    assert str(e.value) == f"cannot reach api.massive.com: {getattr(failure, 'reason', failure)}"
    assert len(t.calls) == 1 + MC.NETWORK_ATTEMPTS and clock.sleeps == [1] and c.stats["network_calls"] == 1
    assert c.unreachable == str(e.value)
    with pytest.raises(MC.Unreachable, match="cannot reach api.massive.com"):
        c.results(MC.TREASURY, sort="date.desc", limit=1)
    with pytest.raises(MC.Unreachable):
        c.results(MC.HOLIDAYS, wait=0)
    assert len(t.calls) == 1 + MC.NETWORK_ATTEMPTS                       # no new request
    assert c.results(MC.SHORT_INTEREST, ticker="ACME") == [{"a": 1}]   # the cache still answers


def test_one_lost_response_is_retried_once(tmp_path):
    t = FakeTransport({MC.NEWS: [urllib.error.URLError("connection reset"), ok({"results": [{"id": 1}]})]})
    clock = Clock()
    c = make(tmp_path, t, clock)
    assert c.results(MC.NEWS, ticker="ACME") == [{"id": 1}]
    assert len(t.calls) == 2 and clock.sleeps == [1] and c.unreachable is None


class CutOff(FakeResponse):
    """A response whose body breaks off (http.client raises IncompleteRead while reading)."""

    def read(self):
        raise http.client.IncompleteRead(b'{"results": [', 120)


def test_a_body_that_breaks_off_is_a_lost_response_not_a_crash(tmp_path):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        return CutOff(req.full_url, 200)

    clock = Clock()
    c = make(tmp_path, urlopen, clock)
    with pytest.raises(MC.Unreachable, match=r"^cannot reach api.massive.com: IncompleteRead\(13 bytes read, 120 more"):
        c.results(MC.NEWS, ticker="ACME")
    assert len(calls) == MC.NETWORK_ATTEMPTS and clock.sleeps == [1]


def test_a_certificate_failure_is_unreachable_at_once(tmp_path):
    t = FakeTransport({"massive.com": [verify_error("self-signed certificate in certificate chain")]})
    seen, clock = [], Clock()
    c = MC.MassiveClient(api_key=KEY, cache_dir=tmp_path, urlopen=t, sleep=clock.sleep, clock=clock, max_per_min=100,
                         read_certificate=lambda url, timeout: seen.append((url, timeout)) or PROXY_CERT)
    with pytest.raises(MC.Unreachable) as e:
        c.results(MC.TREASURY, sort="date.desc", limit=1)
    msg = str(e.value)
    assert msg.startswith("HTTPS certificate verification failed for api.massive.com: self-signed certificate in "
                          "certificate chain. The certificate presented is for api.massive.com, issued by Proxy Root "
                          "(Example Antivirus).")
    assert f"Trusted: {c.trust.description}." in msg
    assert len(t.calls) == 1 and clock.sleeps == [] and c.stats["network_calls"] == 0     # no retry
    assert seen == [(f"{BASE}/fed/v1/treasury-yields?sort=date.desc&limit=1", 15.0)]
    with pytest.raises(MC.Unreachable, match=r"Proxy Root \(Example Antivirus\)"):
        c.results(MC.HOLIDAYS, wait=0)
    assert len(t.calls) == 1 and len(seen) == 1


def test_an_injected_transport_reads_no_certificate_and_a_failed_read_keeps_the_error(tmp_path):
    t = FakeTransport({"massive.com": [verify_error("unable to get local issuer certificate")]})
    c = make(tmp_path, t)
    with pytest.raises(MC.Unreachable) as e:
        c.results(MC.NEWS, ticker="ACME")
    assert "presented" not in str(e.value) and "unable to get local issuer certificate." in str(e.value)

    def broken(url, timeout):
        raise OSError("no route")

    c2 = MC.MassiveClient(api_key=KEY, cache_dir=tmp_path / "b", urlopen=t, sleep=Clock().sleep, clock=Clock(),
                          read_certificate=broken)
    with pytest.raises(MC.Unreachable, match="unable to get local issuer certificate"):
        c2.results(MC.NEWS, ticker="ACME")


def test_one_tls_trust_per_client_and_the_opener_uses_it(tmp_path, monkeypatch, no_network):
    made, opened = [], []
    real = tls.trust
    monkeypatch.setattr(tls, "trust", lambda: made.append(1) or real())

    def fake_open(self, req, data=None, timeout=None):             # the client's own opener, never the network
        https = [h for h in self.handlers if isinstance(h, urllib.request.HTTPSHandler)]
        assert any(isinstance(h, MC._NoRedirect) for h in self.handlers)
        opened.append((req.full_url, https[0]._context, timeout))
        return FakeResponse(req.full_url, 200, {"Content-Type": "application/json"}, b'{"results": [{"x": 1}]}')

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)
    clock = Clock()
    c = MC.MassiveClient(api_key=KEY, cache_dir=tmp_path, use_cache=False, sleep=clock.sleep, clock=clock, timeout=7)
    assert c.results(MC.TREASURY, limit=1) == [{"x": 1}] and c.results(MC.HOLIDAYS, wait=0) == [{"x": 1}]
    assert len(made) == 1 and len(opened) == 2
    ctx = c.trust.context
    assert all(o[1] is ctx and o[2] == 7.0 for o in opened)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert c.describe()["tls_trust"] == c.trust.description and len(made) == 1
    other = MC.MassiveClient(api_key=KEY, cache_dir=tmp_path, use_cache=False, sleep=clock.sleep, clock=clock)
    other.results(MC.NEWS, ticker="ACME")
    assert len(made) == 2 and opened[-1][1] is other.trust.context is not ctx


# ---------------------------------------------------------------------------- HTTP 429
def test_retry_after_parsing():
    assert MC._retry_after({"Retry-After": "7"}) == 7.5
    assert MC._retry_after({"x-ratelimit-reset": "4, 9"}) == 4.5
    assert MC._retry_after({"Retry-After": "soon", "x-ratelimit-reset": "2"}) == 2.5
    assert MC._retry_after({"Retry-After": "-3"}) == 0.5
    assert MC._retry_after({"Retry-After": "soon"}) is None
    assert MC._retry_after({}) is None and MC._retry_after(None) is None
    assert MC._retry_after({"x-ratelimit-reset": "1762480030"}, 1762480000.0) == 30.5     # a Unix time
    assert MC._retry_after({"x-ratelimit-reset": "1762479000"}, 1762480000.0) == 0.5      # already past
    assert MC._retry_after({"Retry-After": "inf", "x-ratelimit-reset": "nan"}) is None


def test_429_with_retry_after_is_retried_once(tmp_path):
    t = FakeTransport({MC.NEWS: [ok({}, status=429, headers={"Retry-After": "3"}), ok({"results": [{"id": "n"}]})]})
    clock = Clock()
    assert make(tmp_path, t, clock).results(MC.NEWS, ticker="ACME") == [{"id": "n"}]
    assert clock.sleeps == [3.5] and len(t.calls) == 2


def test_429_twice_is_rate_limited(tmp_path):
    t = FakeTransport({MC.NEWS: [ok({}, status=429, headers={"Retry-After": "1"})]})
    clock = Clock()
    with pytest.raises(MC.RateLimited, match=r"^Massive rate limit on /v2/reference/news \(HTTP 429\): run again"):
        make(tmp_path, t, clock).results(MC.NEWS, ticker="ACME")
    assert clock.sleeps == [1.5] and len(t.calls) == 2


def test_429_retry_after_beyond_the_longest_wait_is_not_retried(tmp_path):
    t = FakeTransport({MC.NEWS: [ok({}, status=429, headers={"Retry-After": "30"})]})
    clock = Clock()
    with pytest.raises(MC.RateLimited):
        make(tmp_path, t, clock, max_wait=10).results(MC.NEWS, ticker="ACME")
    assert clock.sleeps == [] and len(t.calls) == 1


def test_a_429_that_persists_makes_later_requests_fail_fast(tmp_path):
    t = FakeTransport({MC.SHORT_INTEREST: [ok({"results": [{"a": 1}]})], "massive.com": [ok({}, status=429)]})
    clock = Clock()
    c = make(tmp_path, t, clock)
    assert c.results(MC.SHORT_INTEREST, ticker="ACME") == [{"a": 1}]
    with pytest.raises(MC.RateLimited, match="HTTP 429"):
        c.results(MC.NEWS, ticker="ACME")
    assert clock.sleeps == [MC.WINDOW] and len(t.calls) == 3          # one full-window wait, one retry
    for path in (MC.FLOAT, MC.SHORT_VOLUME, MC.DIVIDENDS):
        with pytest.raises(MC.RateLimited, match=r"^Massive rate limit on /v2/reference/news \(HTTP 429\)"):
            c.results(path, ticker="ACME")
    assert clock.sleeps == [MC.WINDOW] and len(t.calls) == 3          # no other wait, no other request
    assert c.results(MC.SHORT_INTEREST, ticker="ACME") == [{"a": 1}]   # the cache still answers
    clock.tick(MC.FAIL_FAST["throttled"])
    with pytest.raises(MC.RateLimited):
        c.results(MC.FLOAT, ticker="ACME")
    assert len(t.calls) == 5                                          # a minute later Massive is asked again


@pytest.mark.parametrize("max_wait, wait, retried", [(65, None, True), (59, None, False), (65, 0, False)])
def test_429_without_headers_waits_a_full_window_only_when_that_fits(tmp_path, max_wait, wait, retried):
    t = FakeTransport({MC.HOLIDAYS: [{"status": 429, "body": b""}, ok([])]})
    clock = Clock()
    c = make(tmp_path, t, clock, max_wait=max_wait)
    if retried:
        assert c.results(MC.HOLIDAYS, wait=wait) == []
        assert clock.sleeps == [MC.WINDOW] and len(t.calls) == 2
    else:
        with pytest.raises(MC.RateLimited):
            c.results(MC.HOLIDAYS, wait=wait)
        assert clock.sleeps == [] and len(t.calls) == 1


# ---------------------------------------------------------------------------- the rate window
def window(tmp_path) -> list:
    return json.loads((tmp_path / "cache" / MC.RATE_FILE).read_text(encoding="utf-8"))


def test_requests_are_spaced_to_the_rate_limit(tmp_path):
    t = FakeTransport({"massive.com": [ok({"results": []})]})
    clock = Clock()
    c = make(tmp_path, t, clock, max_per_min=5, use_cache=False)
    t0 = clock.t
    for i in range(5):                                                  # five requests go at once
        c.results(MC.NEWS, ticker=f"T{i}")
        clock.tick(2)
    assert clock.sleeps == [] and len(t.calls) == 5
    c.results(MC.NEWS, ticker="T5")                                     # the sixth waits for the oldest to leave
    assert clock.sleeps == [pytest.approx(MC.WINDOW - 10 + 0.25)] and len(t.calls) == 6
    assert window(tmp_path) == [t0 + 2, t0 + 4, t0 + 6, t0 + 8, pytest.approx(t0 + MC.WINDOW + 0.25)]
    clock.tick(MC.WINDOW + 1)
    c.results(MC.NEWS, ticker="T6")                                     # a quiet minute later: at once
    assert len(clock.sleeps) == 1 and len(window(tmp_path)) == 1


def test_a_wait_beyond_the_longest_wait_is_skipped_without_sleeping(tmp_path):
    t = FakeTransport({"massive.com": [ok({"results": []})]})
    clock = Clock()
    c = make(tmp_path, t, clock, max_per_min=2, max_wait=30)
    c.results(MC.NEWS, ticker="A")
    c.results(MC.NEWS, ticker="B")
    with pytest.raises(MC.RateLimited) as e:
        c.results(MC.NEWS, ticker="C")
    assert str(e.value) == ("Massive allows 2 requests a minute on this plan; the next free slot is 60s away "
                            "(longest wait 30s): run again in a minute")
    with pytest.raises(MC.RateLimited, match=r"\(longest wait 0s\)"):
        c.results(MC.HOLIDAYS, wait=0)                                  # wait=0: only when a slot is free now
    assert clock.sleeps == [] and len(t.calls) == 2                     # a skipped request is never sent
    clock.tick(MC.WINDOW + 1)
    assert c.results(MC.HOLIDAYS, wait=0) == [] and len(t.calls) == 3
    assert c.results(MC.NEWS, ticker="D", wait=MC.WINDOW + 5) == [] and clock.sleeps == []
    c.results(MC.NEWS, ticker="E", wait=MC.WINDOW + 5)                  # a longer wait of its own is allowed
    assert clock.sleeps == [pytest.approx(MC.WINDOW + 0.25)] and len(t.calls) == 5


def test_requests_at_the_same_clock_reading_count_separately(tmp_path):
    t = FakeTransport({"massive.com": [ok({"results": []})]})
    clock = Clock()                                                     # never moves between the requests
    c = make(tmp_path, t, clock, max_per_min=3, max_wait=0)
    for ticker in ("A", "B", "C"):
        c.results(MC.NEWS, ticker=ticker)
    with pytest.raises(MC.RateLimited):
        c.results(MC.NEWS, ticker="D")
    assert len(t.calls) == 3 and len(set(window(tmp_path))) == 3
    other = make(tmp_path, t, clock, max_per_min=3, max_wait=0)         # another process sees all three
    with pytest.raises(MC.RateLimited):
        other.results(MC.NEWS, ticker="E")


def test_two_clients_share_the_window_through_the_cache_folder(tmp_path):
    t = FakeTransport({"massive.com": [ok({"results": []})]})
    clock = Clock()
    a = make(tmp_path, t, clock, max_per_min=2)
    b = make(tmp_path, t, clock, max_per_min=2)
    t0 = clock.t
    a.results(MC.NEWS, ticker="A")
    clock.tick(1)
    a.results(MC.NEWS, ticker="B")
    clock.tick(1)
    b.results(MC.NEWS, ticker="C")                                      # b counts a's requests
    assert clock.sleeps == [pytest.approx(MC.WINDOW - 2 + 0.25)]
    assert window(tmp_path) == [t0 + 1, pytest.approx(t0 + MC.WINDOW + 0.25)]
    text = (tmp_path / "cache" / MC.RATE_FILE).read_text(encoding="utf-8")
    assert KEY not in text and all(isinstance(x, float) for x in json.loads(text))     # request times only


def test_junk_in_the_window_file_is_ignored(tmp_path):
    clock = Clock()
    (tmp_path / "cache").mkdir()
    now = clock.t + 10
    (tmp_path / "cache" / MC.RATE_FILE).write_text(json.dumps(
        [clock.t, "x", True, None, {"a": 1}, clock.t + 1, now + 3600, clock.t - 3600]), encoding="utf-8")
    clock.tick(10)
    t = FakeTransport({"massive.com": [ok({"results": []})]})
    c = make(tmp_path, t, clock, max_per_min=3, max_wait=30)
    c.results(MC.NEWS, ticker="A")                                      # two times in the window: the third slot
    with pytest.raises(MC.RateLimited, match="next free slot is 50s away"):
        c.results(MC.NEWS, ticker="B")
    assert window(tmp_path) == [clock.t - 10, clock.t - 9, clock.t]


@pytest.mark.parametrize("content", ["{broken", '{"a": 1}', "", "\x00\xff"])
def test_a_broken_window_file_falls_back_to_the_clients_own_window(tmp_path, content):
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / MC.RATE_FILE).write_text(content, encoding="utf-8", errors="ignore")
    t = FakeTransport({"massive.com": [ok({"results": []})]})
    clock = Clock()
    c = make(tmp_path, t, clock, max_per_min=2, max_wait=30)
    c.results(MC.NEWS, ticker="A")
    c.results(MC.NEWS, ticker="B")
    with pytest.raises(MC.RateLimited):
        c.results(MC.NEWS, ticker="C")
    assert len(t.calls) == 2 and len(window(tmp_path)) == 2


def test_an_unwritable_cache_folder_keeps_the_window_in_memory(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the cache folder should be", encoding="utf-8")
    t = FakeTransport({"massive.com": [ok({"results": [{"id": 1}]})]})
    clock = Clock()
    c = MC.MassiveClient(api_key=KEY, cache_dir=blocker / "cache", urlopen=t, sleep=clock.sleep, clock=clock,
                         max_per_min=2, max_wait=30)
    assert c.results(MC.NEWS, ticker="A") == [{"id": 1}]
    assert c.results(MC.NEWS, ticker="A") == [{"id": 1}]                # not cached: asked again
    with pytest.raises(MC.RateLimited):
        c.results(MC.NEWS, ticker="A")
    assert len(t.calls) == 2 and blocker.is_file() and [p.name for p in tmp_path.iterdir()] == ["blocker"]


@pytest.mark.parametrize("value", ["0", "-3", "abc", "", "nan", "inf", "-inf", "1e400"])
def test_junk_settings_fall_back_to_the_defaults(monkeypatch, tmp_path, value):
    # MASSIVE_MAX_WAIT=0 is left out: "never wait" or junk is an open question (it falls back to the default now)
    for name in ("MASSIVE_MAX_PER_MIN", "MASSIVE_TIMEOUT") + (() if value == "0" else ("MASSIVE_MAX_WAIT",)):
        monkeypatch.setenv(name, value)
    c = MC.MassiveClient(api_key=KEY, cache_dir=tmp_path, urlopen=FakeTransport())
    assert (c.per_min, c.max_wait, c.timeout) == (C.MASSIVE_MAX_PER_MIN, float(C.MASSIVE_MAX_WAIT),
                                                  float(C.MASSIVE_TIMEOUT))


def test_settings_from_the_environment_and_the_arguments(monkeypatch, tmp_path):
    monkeypatch.setenv("MASSIVE_MAX_PER_MIN", "2.9")
    monkeypatch.setenv("MASSIVE_MAX_WAIT", "10")
    monkeypatch.setenv("MASSIVE_TIMEOUT", "7.5")
    c = MC.MassiveClient(api_key=KEY, cache_dir=tmp_path, urlopen=FakeTransport())
    assert (c.per_min, c.max_wait, c.timeout) == (2, 10.0, 7.5)
    monkeypatch.setenv("MASSIVE_MAX_PER_MIN", "0.5")
    assert MC.MassiveClient(api_key=KEY, cache_dir=tmp_path, urlopen=FakeTransport()).per_min == 1
    c = MC.MassiveClient(api_key=KEY, cache_dir=tmp_path, urlopen=FakeTransport(), max_per_min=3, max_wait=0,
                         timeout=5)
    assert (c.per_min, c.max_wait, c.timeout) == (3, 0.0, 5.0)


# ---------------------------------------------------------------------------- holiday calendar
TODAY = date(2026, 11, 6)


def test_calendar_rows_keep_nyse_closures_and_early_closes_in_new_york_time():
    rows = [
        {"date": "2026-11-26", "exchange": "NYSE", "name": "Thanksgiving", "status": "closed"},
        {"date": "2026-11-26", "exchange": "NASDAQ", "name": "Thanksgiving", "status": "closed"},
        {"date": "2026-11-27", "exchange": "NYSE", "name": "Thanksgiving", "status": "early-close",
         "open": "2026-11-27T14:30:00.000Z", "close": "2026-11-27T18:00:00.000Z"},          # EST: 13:00
        {"date": "2027-07-02", "exchange": "nyse", "status": "early_close", "close": "2027-07-02T17:00:00Z"},   # EDT
        {"date": "2026-12-24", "exchange": "NYSE", "status": "early-close", "close": "not a time"},
        {"date": "2027-11-26", "exchange": "NYSE", "status": "early-close", "close": "0001-01-01T00:00:00Z"},
        {"date": "2026-12-31", "exchange": "NASDAQ", "status": "closed"},
        {"date": "2027-01-04", "exchange": "NYSE", "status": "open"},
        {"date": "junk", "exchange": "NYSE", "status": "closed"},
        {"exchange": "NYSE", "status": "closed"},
        "not a row", None,
    ]
    assert MC.calendar_rows(rows) == {date(2026, 11, 26): ("closed", None),
                                      date(2026, 11, 27): ("early_close", "13:00"),
                                      date(2027, 7, 2): ("early_close", "13:00"),
                                      date(2026, 12, 24): ("early_close", None),
                                      date(2027, 11, 26): ("early_close", None)}            # out of range: no time
    assert MC.calendar_rows(None) == {} == MC.calendar_rows([])


def test_the_synthetic_calendar_matches_config():
    rows = synth.holiday_rows()
    cmp = MC.compare_calendar(rows, TODAY)
    nyse = {r["date"] for r in rows if r["exchange"] == "NYSE"}
    assert cmp == {"checked_from": TODAY, "checked_through": date(2027, 9, 6), "n_dates": len(nyse), "mismatches": []}
    assert len(nyse) == 12


def test_calendar_mismatches():
    rows = [r for r in synth.holiday_rows() if r["date"] != "2027-01-18"]         # a holiday the exchange lacks
    for r in rows:
        if r["exchange"] == "NYSE" and r["date"] == "2026-12-24":
            r["close"] = "2026-12-24T19:00:00.000Z"                              # 14:00 New York, config 13:00
        if r["exchange"] == "NYSE" and r["date"] == "2026-11-27":
            r["close"] = "garbage"                                               # unreadable: not a mismatch
        if r["exchange"] == "NYSE" and r["date"] == "2027-05-31":
            r.update(status="early-close", close="2027-05-31T17:00:00Z")        # config: closed
    rows += [{"date": "2026-12-31", "exchange": "NYSE", "name": "Extra closure", "status": "closed"},
             {"date": "2026-12-30", "exchange": "NASDAQ", "name": "Not NYSE", "status": "closed"},
             {"date": "2026-10-01", "exchange": "NYSE", "name": "Before today", "status": "closed"}]
    cmp = MC.compare_calendar(rows, TODAY)
    assert cmp["checked_from"] == TODAY and cmp["checked_through"] == date(2027, 9, 6) and cmp["n_dates"] == 12
    assert cmp["mismatches"] == [
        {"date": date(2026, 12, 24), "exchange": "early_close", "exchange_close": "14:00", "config": "early_close",
         "config_close": "13:00", "name": "Early close"},
        {"date": date(2026, 12, 31), "exchange": "closed", "exchange_close": None, "config": "open",
         "config_close": None, "name": "Extra closure"},
        {"date": date(2027, 1, 18), "exchange": "open", "exchange_close": None, "config": "closed",
         "config_close": None, "name": None},
        {"date": date(2027, 5, 31), "exchange": "early_close", "exchange_close": "13:00", "config": "closed",
         "config_close": None, "name": "Memorial Day"},
    ]
    for m in cmp["mismatches"]:
        assert m["exchange"] in C.ENUMS["cal_"] and m["config"] in C.ENUMS["cal_"]


def test_holiday_names_are_plain_text():
    """A holiday name is third-party text: printable characters only (no terminal escapes), at most 80 characters."""
    rows = synth.holiday_rows() + [
        {"date": "2026-12-30", "exchange": "NYSE", "name": "Odd\x1b[2J\x00  closure\n", "status": "closed"},
        {"date": "2026-12-31", "exchange": "NYSE", "name": "x" * 200, "status": "closed"},
        {"date": "2027-01-04", "exchange": "NYSE", "name": ["not", "text"], "status": "closed"},
        {"date": "2027-01-05", "exchange": "NYSE", "name": " \t ", "status": "closed"}]
    names = {m["date"].isoformat(): m["name"] for m in MC.compare_calendar(rows, TODAY)["mismatches"]}
    assert names == {"2026-12-30": "Odd [2J closure", "2026-12-31": "x" * 80, "2027-01-04": None,
                     "2027-01-05": None}


def test_today_is_compared_only_when_massive_lists_it():
    thanksgiving = date(2026, 11, 26)
    later = synth.holiday_rows(today=date(2026, 11, 27))                 # an "upcoming" list without today
    cmp = MC.compare_calendar(later, thanksgiving)
    assert cmp["checked_from"] == thanksgiving and cmp["mismatches"] == [] and cmp["n_dates"] == 11
    listed = synth.holiday_rows(today=thanksgiving)
    assert MC.compare_calendar(listed, thanksgiving)["mismatches"] == []
    wrong = [r for r in listed if r["date"] != "2026-11-26"] + [
        {"date": "2026-11-26", "exchange": "NYSE", "status": "early-close", "close": "2026-11-26T18:00:00Z"}]
    assert [m["date"] for m in MC.compare_calendar(wrong, thanksgiving)["mismatches"]] == [thanksgiving]


def test_dates_before_today_are_ignored_and_an_empty_calendar_checks_nothing():
    past = [{"date": "2026-10-01", "exchange": "NYSE", "status": "closed"}]
    nasdaq = [{"date": "2026-12-31", "exchange": "NASDAQ", "status": "closed"}]
    for rows in ([], None, past, nasdaq):
        assert MC.compare_calendar(rows, TODAY) == {"checked_from": TODAY, "checked_through": None, "n_dates": 0,
                                                    "mismatches": []}
    late = MC.compare_calendar(synth.holiday_rows(), date(2027, 6, 1))
    assert late["mismatches"] == [] and late["n_dates"] == 3                       # 06-18, 07-05, 09-06


def test_calendar_label():
    assert MC.calendar_label("closed", None) == "closed" and MC.calendar_label("open", None) == "open"
    assert MC.calendar_label("early_close", "13:00") == "early close 13:00"
    assert MC.calendar_label("early_close", None) == "early close"


# ---------------------------------------------------------------------------- command line
@pytest.fixture
def cli(monkeypatch, tmp_path, no_network):
    """massive_client.main() with a made-up key and every client on a FakeTransport and a fake clock."""
    monkeypatch.setenv("MASSIVE_API_KEY", KEY)
    monkeypatch.setenv("MASSIVE_CACHE_DIR", str(tmp_path / "cache"))
    state = SimpleNamespace(transport=FakeTransport(), clock=Clock(), made=[])
    real = MC.MassiveClient

    def client(**kw):
        state.made.append(kw)
        return real(urlopen=state.transport, sleep=state.clock.sleep, clock=state.clock, **kw)

    monkeypatch.setattr(MC, "MassiveClient", client)
    monkeypatch.setattr(real, "_now_ny", lambda self: datetime(2026, 11, 6, 9, 0, tzinfo=NY))
    return state


def test_cli_get(cli, capsys):
    body = {"results": [{"ticker": "ACME", "short_interest": 5}], "status": "OK"}
    cli.transport = FakeTransport({MC.SHORT_INTEREST: [ok(body)]})
    assert MC.main(["get", "/stocks/v1/short-interest", "ticker=ACME", "limit=2", "junk"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out) == body and "Ignoring argument without '=': junk" in err
    (url, headers), = cli.transport.calls
    assert url == f"{BASE}/stocks/v1/short-interest?ticker=ACME&limit=2" and headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in out + err


def test_cli_errors_never_print_the_key(cli, capsys):
    cli.transport = FakeTransport({"massive.com": [ok({"status": "ERROR", "error": f"Unknown API Key {KEY}"},
                                                      status=401)]})
    assert MC.main(["get", "/v2/reference/news", "ticker=ACME"]) == 1
    out, err = capsys.readouterr()
    assert err == "Error: key rejected on /v2/reference/news: Unknown API Key ***\n" and KEY not in out
    cli.transport = FakeTransport({"massive.com": [urllib.error.URLError(f"tunnel failed for {KEY}")]})
    assert MC.main(["get", "/v2/reference/news"]) == 1
    out, err = capsys.readouterr()
    assert err.splitlines() == ["[massive] no response from api.massive.com on /v2/reference/news (tunnel failed for "
                                "***); retrying once ...", "Error: cannot reach api.massive.com: tunnel failed for ***"]
    assert KEY not in out + err


def test_cli_check(cli, capsys):
    holidays = synth.holiday_rows() + [{"date": "2026-12-31", "exchange": "NYSE", "name": "Extra closure",
                                        "status": "closed"}]
    cli.transport = FakeTransport({
        MC.SHORT_INTEREST: [ok({"results": [{"settlement_date": "2026-10-31"}, {"settlement_date": "2026-10-15"}]})],
        MC.SHORT_VOLUME: [ok({"status": "NOT_AUTHORIZED", "message": "You are not entitled to this data."},
                             status=403)],
        MC.FLOAT: [ok({"results": [], "status": "OK"})],
        MC.NEWS: [ok({"results": [{"published_utc": "2026-11-05T21:05:00Z"}], "status": "OK"})],
        MC.DIVIDENDS: [{"status": 500, "body": b""}],
        MC.TREASURY: [ok({"results": [{"date": "2026-11-05", "yield_1_month": 4.03}], "status": "OK"})],
        MC.HOLIDAYS: [ok(holidays)],
    })
    assert MC.main(["check", "acme"]) == 0
    out, err = capsys.readouterr()
    lines = out.splitlines()

    def row(path):
        return next(ln for ln in lines if ln.startswith(path + " "))

    assert row(MC.SHORT_INTEREST).split()[1:] == ["OK", "2", "rows", "(2026-10-15", "..", "2026-10-31)"]
    assert row(MC.SHORT_VOLUME).split()[1] == "RESTRICTED"
    assert "You are not entitled to this data." in row(MC.SHORT_VOLUME)
    assert row(MC.FLOAT).split()[1:] == ["EMPTY", "no", "rows", "returned"]
    assert row(MC.NEWS).split()[1:] == ["OK", "1", "rows", "(2026-11-05)"]
    assert row(MC.DIVIDENDS).split()[1] == "ERROR" and "HTTP 500" in row(MC.DIVIDENDS)
    assert row(MC.TREASURY).split()[1:] == ["OK", "1", "rows", "(2026-11-05)"]
    assert row(MC.HOLIDAYS).split()[1] == "OK"
    assert "  2026-12-31: exchange calendar closed, config.py open (Extra closure)" in lines
    # the HTTP 500 is not retried: the probes before it used every request slot of the minute
    assert f"Network calls used: {len(cli.transport.calls)}" in out and len(cli.transport.calls) == 7
    assert cli.made == [{"refresh": True, "max_wait": MC.WINDOW + 5}]
    urls = cli.transport.urls()
    assert f"{BASE}{MC.SHORT_INTEREST}?ticker=ACME&limit=2&sort=settlement_date.desc" in urls
    assert f"{BASE}{MC.TREASURY}?date.lte=2026-11-06&sort=date.desc&limit=1" in urls     # today in New York
    assert cli.clock.sleeps and all(s <= MC.WINDOW + 5 for s in cli.clock.sleeps)   # spaced, never really waited
    assert KEY not in out + err


@pytest.mark.parametrize("step, code, text", [
    (ok({"status": "ERROR", "error": "Unknown API Key"}, status=401), 2,
     "Key problem: key rejected on /stocks/v1/short-interest: Unknown API Key"),
    (urllib.error.URLError("timed out"), 1, "Unreachable: cannot reach api.massive.com: timed out"),
])
def test_cli_check_stops_on_a_key_or_connection_problem(cli, capsys, step, code, text):
    cli.transport = FakeTransport({"massive.com": [step]})
    assert MC.main(["check"]) == code
    out = capsys.readouterr().out
    assert text in out and "Holiday calendar" not in out
    assert "ticker=NVDA" in cli.transport.urls()[0]                     # the default symbol


def test_cli_clear_cache_and_usage(monkeypatch, tmp_path, capsys):
    cache = tmp_path / "massive-cache"
    (cache / "sub").mkdir(parents=True)
    (cache / "x.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MASSIVE_CACHE_DIR", str(cache))
    assert MC.main(["clear-cache"]) == 0 and not cache.exists()
    assert f"Cleared {cache}" in capsys.readouterr().out
    assert MC.main([]) == 0 and "MASSIVE_API_KEY" in capsys.readouterr().out
    assert MC.main(["--help"]) == 0
    assert MC.main(["bogus"]) == 1 and MC.main(["get"]) == 1


# ---------------------------------------------------------------------------- the key elsewhere
def test_analyze_and_the_ui_server_redact_the_massive_key(monkeypatch):
    import analyze
    monkeypatch.syspath_prepend(str(SKILL / "ui"))
    import server
    monkeypatch.setenv("MASSIVE_API_KEY", f" {KEY} ")
    monkeypatch.setenv("FMP_API_KEY", "fmp-test-key-42")
    text = f"failed: Bearer {KEY} apikey=fmp-test-key-42&x=1 again {KEY}"
    assert analyze._redact(text) == "failed: Bearer *** apikey=***&x=1 again ***"
    assert server.redact(text) == "failed: Bearer *** apikey=***&x=1 again ***"
    monkeypatch.delenv("MASSIVE_API_KEY")
    monkeypatch.delenv("FMP_API_KEY")
    assert analyze._redact(text) == text


def test_winenv_reads_the_massive_settings():
    import winenv
    assert {"MASSIVE_API_KEY", *SETTINGS} <= set(winenv.ENV_NAMES)
