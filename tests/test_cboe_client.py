"""CboeClient over real urllib against a local server (307 redirect, gzip, 403 AccessDenied) and over FakeTransport
for the cache rules (plan 3.6): conditional requests, lifetimes and the overnight exception."""
import gzip
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import chain as CH
import config as C
import synth
from cboe_client import CboeClient, CboeError, CboeUnreachable, NotFound
from fakes import ACCESS_DENIED, FakeTransport, gz

AFTER_CLOSE = datetime(2026, 11, 5, 17, 30, tzinfo=CH.NY)


def chain_bytes(payload=None) -> bytes:
    return json.dumps(payload or synth.oex_payload()).encode("utf-8")


# ---------------------------------------------------------------------------- real urllib, local server
class Handler(BaseHTTPRequestHandler):
    body = b""

    def do_GET(self):
        if self.path.startswith("/old/"):                       # the 2026-11 move to cdn-api: 307 elsewhere
            self.send_response(307)
            self.send_header("Location", f"http://localhost:{self.server.server_address[1]}/new/" + self.path[5:])
            self.end_headers()
            return
        if self.path.endswith("/OEX.json"):
            data = gzip.compress(self.body)
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Type", "application/json")
            self.send_header("ETag", 'W/"abc"')
        else:                                                    # unknown symbol: S3 AccessDenied, gzip-encoded
            data = gzip.compress(ACCESS_DENIED)
            self.send_response(403)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def local_cboe(monkeypatch):
    Handler.body = chain_bytes()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setattr(C, "CBOE_CHAIN_URL", base + "/old/api/global/delayed_quotes/options/{symbol}.json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # no proxy for 127.0.0.1
    yield base, opener.open
    srv.shutdown()
    srv.server_close()


def test_redirect_and_gzip_over_urllib(local_cboe, tmp_path):
    base, urlopen = local_cboe
    c = CboeClient(cache_dir=tmp_path, urlopen=urlopen, min_interval=0, now=AFTER_CLOSE, sleep=lambda s: None)
    p = c.get_chain("oex")                                       # upper-cased
    assert p["symbol"] == "_OEX" and len(p["data"]["options"]) > 100
    assert c.redirected_to == base.replace("127.0.0.1", "localhost")
    assert c.fetch_info["OEX"]["source"] == "network"
    with pytest.raises(NotFound):
        c.get_chain("ZZZZQ")
    assert c.stats["network_calls"] >= 2


# ---------------------------------------------------------------------------- FakeTransport: cache rules
def ok(body, etag='W/"1"'):
    return {"status": 200, "body": gz(body), "headers": {"Content-Encoding": "gzip", "ETag": etag,
                                                          "Last-Modified": "Fri, 06 Nov 2026 01:05:00 GMT"}}


def client(tmp_path, transport, now=AFTER_CLOSE, **kw):
    return CboeClient(cache_dir=tmp_path, urlopen=transport, min_interval=0, now=now, sleep=lambda s: None, **kw)


def test_closed_chain_cached_until_next_open(tmp_path):
    t = FakeTransport({"OEX.json": [ok(chain_bytes())]})
    client(tmp_path, t).get_chain("_OEX")
    c2 = client(tmp_path, t, now=datetime(2026, 11, 6, 9, 29, tzinfo=CH.NY))
    c2.get_chain("_OEX")
    assert len(t.calls) == 1 and c2.stats == {"network_calls": 0, "cache_hits": 1}
    meta = json.loads((tmp_path / "chains" / "_OEX.meta.json").read_text())
    assert meta["expires"].startswith("2026-11-06T09:30")


def test_revalidation_with_304(tmp_path):
    t = FakeTransport({"OEX.json": [ok(chain_bytes()), {"status": 304}]})
    client(tmp_path, t).get_chain("_OEX")
    later = client(tmp_path, t, now=datetime(2026, 11, 6, 10, 0, tzinfo=CH.NY))
    p = later.get_chain("_OEX")
    assert p["symbol"] == "_OEX" and later.fetch_info["_OEX"]["source"] == "revalidated"
    assert t.calls[1][1].get("if-none-match") == 'W/"1"'


def test_refresh_skips_the_cache(tmp_path):
    t = FakeTransport({"OEX.json": [ok(chain_bytes())]})
    client(tmp_path, t).get_chain("_OEX")
    client(tmp_path, t, refresh=True).get_chain("_OEX")
    assert len(t.calls) == 2 and "if-none-match" not in t.calls[1][1]


def test_intraday_chain_lives_five_minutes(tmp_path):
    p = synth.oex_payload()
    p["timestamp"], p["data"]["last_trade_time"] = "2026-11-05 16:00:00", "2026-11-05T10:59:59"
    noon = datetime(2026, 11, 5, 11, 0, tzinfo=CH.NY)
    t = FakeTransport({"OEX.json": [ok(chain_bytes(p))]})
    client(tmp_path, t, now=noon).get_chain("_OEX")
    meta = json.loads((tmp_path / "chains" / "_OEX.meta.json").read_text())
    assert meta["closed"] is False and meta["expires"].startswith("2026-11-05T11:05")


def test_overnight_version_does_not_replace_the_close_version(tmp_path):
    close = synth.xsp_payload()
    night = json.loads(json.dumps(close))
    night["timestamp"] = "2026-11-06 01:30:00"                  # 20:30 New York: GTH quotes
    night["data"]["options"][0]["bid"] = 0.0
    t = FakeTransport({"XSP.json": [ok(chain_bytes(close)), ok(chain_bytes(night), 'W/"2"')]})
    client(tmp_path, t).get_chain("_XSP")
    c2 = client(tmp_path, t, now=datetime(2026, 11, 5, 20, 40, tzinfo=CH.NY), refresh=True)
    p = c2.get_chain("_XSP")
    assert p["timestamp"] == close["timestamp"] and c2.fetch_info["_XSP"]["source"] == "kept_close_version"


def test_retries_with_backoff(tmp_path):
    waits = []
    t = FakeTransport({"OEX.json": [{"status": 503}]})
    c = CboeClient(cache_dir=tmp_path, urlopen=t, min_interval=0, now=AFTER_CLOSE, sleep=waits.append)
    with pytest.raises(CboeUnreachable):
        c.get_chain("_OEX")
    assert len(t.calls) == C.CBOE_ATTEMPTS and waits[:len(C.CBOE_BACKOFF)] == list(C.CBOE_BACKOFF)


def test_fail_fast_without_any_response(tmp_path):
    t = FakeTransport({"cboe.com": [urllib.error.URLError("timed out")]})
    c = CboeClient(cache_dir=tmp_path, urlopen=t, min_interval=0, now=AFTER_CLOSE, sleep=lambda s: None)
    with pytest.raises(CboeUnreachable):
        c.get_chain("_OEX")
    n = len(t.calls)
    assert n == C.CBOE_NO_RESPONSE_LIMIT
    with pytest.raises(CboeUnreachable):                          # no new request once CBOE is down
        c.get_chain("_SPX")
    assert len(t.calls) == n


def test_access_denied_is_not_retried(tmp_path):
    t = FakeTransport({"ZZZZQ.json": [{"status": 403, "body": gz(ACCESS_DENIED),
                                       "headers": {"Content-Encoding": "gzip"}}]})
    with pytest.raises(NotFound):
        client(tmp_path, t).get_chain("ZZZZQ")
    assert len(t.calls) == 1


def test_304_without_a_cache_is_an_error(tmp_path):
    t = FakeTransport({"OEX.json": [{"status": 304}]})
    with pytest.raises(CboeError):
        client(tmp_path, t, use_cache=False).get_chain("_OEX")


def test_index_history_csv(tmp_path):
    csv = "DATE,OPEN,HIGH,LOW,CLOSE\n11/04/2026,1,1,1,17.5\n11/05/2026,1,1,1,18.5\n".encode()
    t = FakeTransport({"VIX_History.csv": [{"status": 200, "body": csv}]})
    rows = client(tmp_path, t).get_index_history("^vix")
    assert [r["close"] for r in rows] == [17.5, 18.5]
    client(tmp_path, t).get_index_history("VIX")                  # has the last closed day: cached
    assert len(t.calls) == 1
