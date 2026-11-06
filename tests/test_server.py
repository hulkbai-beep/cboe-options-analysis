"""The local UI server (plan 9): routes, status codes, the Host check, static files and key redaction. The analysis
functions are wrapped to run on the synthetic data; requests go over real HTTP to 127.0.0.1."""
import json
import sys
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

import analyze
from cboe_client import CboeUnreachable
from conftest import NOW, SKILL, fakes_for

sys.path.insert(0, str(SKILL / "ui"))
import server  # noqa: E402


@pytest.fixture
def ui(synth_data, tmp_path, monkeypatch):
    calls = []
    real_run, real_market = analyze.run_analysis, analyze.run_market

    def run(text, refresh=False, **kw):
        calls.append(text)
        cboe, fmp = fakes_for(synth_data)
        return real_run(text, client=cboe, fmp=fmp, now=NOW, data_dir=tmp_path)

    def market(refresh=False, **kw):
        calls.append("MARKET")
        cboe, fmp = fakes_for(synth_data)
        return real_market(client=cboe, fmp=fmp, now=NOW, data_dir=tmp_path)

    monkeypatch.setattr(analyze, "run_analysis", run)
    monkeypatch.setattr(analyze, "run_market", market)
    srv = server.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(path, host=None):
        req = urllib.request.Request(base + path, headers={"Host": host} if host else {})
        try:
            with opener.open(req, timeout=120) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    yield SimpleNamespace(get=get, calls=calls, port=srv.server_address[1], monkeypatch=monkeypatch)
    srv.shutdown()
    srv.server_close()


# ---------------------------------------------------------------------------- static files and the Host check
def test_index_and_static_files(ui):
    code, h, body = ui.get("/")
    assert code == 200 and h["Content-Type"].startswith("text/html") and b"i18n.js" in body
    assert h["Cache-Control"] == "no-store" and h["X-Content-Type-Options"] == "nosniff"
    assert h["Server"].startswith("oa-ui")
    code, h, _ = ui.get("/chart.js")
    assert code == 200 and h["Content-Type"].startswith("text/javascript")
    for path in ("/server.py", "/../scripts/config.py", "/%2e%2e/scripts/config.py", "/.hidden.js", "/nope.css",
                 "/..%2fscripts%2fconfig.py", "/C:/x.js"):
        assert ui.get(path)[0] == 404, path


def test_host_check(ui):
    assert ui.get("/", host="evil.example.com")[0] == 403
    assert ui.get("/api/market", host="attacker.test:8987")[0] == 403 and ui.calls == []
    assert ui.get("/", host=f"localhost:{ui.port}")[0] == 200
    assert ui.get("/", host=f"[::1]:{ui.port}")[0] == 200


# ---------------------------------------------------------------------------- the API
def test_analyze_and_market(ui):
    code, h, body = ui.get("/api/analyze?symbol=acme")
    assert code == 200 and h["Content-Type"].startswith("application/json")
    data = json.loads(body)
    assert data["metrics"]["ticker"] == "ACME" and data["chart"]["meta"]["ticker"] == "ACME"
    code, _, body = ui.get("/api/market?refresh=1")
    assert code == 200 and json.loads(body)["metrics"]["mode"] == "market"
    assert ui.calls == ["acme", "MARKET"]


@pytest.mark.parametrize("symbol, error_id", [("VIX", "vix_futures"), ("%5EVIX", "vix_futures"), ("DJX", "djx")])
def test_refused_symbols(ui, symbol, error_id):
    code, _, body = ui.get(f"/api/analyze?symbol={symbol}")
    data = json.loads(body)
    assert code == 400 and data["error_id"] == error_id and ui.calls == []


@pytest.mark.parametrize("symbol", ["SHOP.TO", "", "NOT%20A%20TICKER!"])
def test_invalid_symbols_never_reach_the_analysis(ui, symbol):
    code, _, body = ui.get(f"/api/analyze?symbol={symbol}")
    data = json.loads(body)
    assert code == 400 and data["error"].startswith("Invalid symbol: ") and "error_id" not in data
    assert ui.calls == []


def test_unknown_symbol_is_404(ui):
    code, _, body = ui.get("/api/analyze?symbol=ZZZZQ")
    assert code == 404 and json.loads(body)["error"].startswith("No option chain on CBOE: ")


def test_cboe_unreachable_is_502(ui):
    def down(text, refresh=False, **kw):
        raise CboeUnreachable("cannot reach cdn.cboe.com: timed out")

    ui.monkeypatch.setattr(analyze, "run_analysis", down)
    code, _, body = ui.get("/api/analyze?symbol=SPY")
    assert code == 502 and json.loads(body) == {"error": "CBOE error: cannot reach cdn.cboe.com: timed out"}


def test_errors_never_show_the_key(ui, capsys):
    secret = "test-key-0123456789"
    ui.monkeypatch.setenv("FMP_API_KEY", secret)

    def broken(text, refresh=False, **kw):
        raise RuntimeError(f"boom with {secret} and https://x.test/q?apikey=other-value&a=1")

    ui.monkeypatch.setattr(analyze, "run_analysis", broken)
    code, _, body = ui.get("/api/analyze?symbol=SPY")
    data = json.loads(body)
    assert code == 500 and secret not in data["error"] and "other-value" not in data["error"]
    assert data["error"] == "RuntimeError: boom with *** and https://x.test/q?apikey=***&a=1"
    err = capsys.readouterr().err
    assert secret not in err and "other-value" not in err and "Traceback" in err


def test_no_chart_is_an_error():
    code, payload = server._result({"meta": {"errors": ["chart: KeyError: 'x'"]}}, None)
    assert code == 500 and payload == {"error": "chart could not be built: chart: KeyError: 'x'"}


# ---------------------------------------------------------------------------- environment rules
def test_proxy_tls_rule(monkeypatch):
    monkeypatch.delenv("FMP_TLS_VERIFY", raising=False)
    monkeypatch.setenv("FMP_BASE_URL", "https://54.236.92.205/api")
    assert server.apply_proxy_tls_rule() is True and server.os.environ["FMP_TLS_VERIFY"] == "0"
    monkeypatch.setenv("FMP_TLS_VERIFY", "1")
    assert server.apply_proxy_tls_rule() is False and server.os.environ["FMP_TLS_VERIFY"] == "1"
    monkeypatch.delenv("FMP_TLS_VERIFY")
    monkeypatch.setenv("FMP_BASE_URL", "https://financialmodelingprep.com")
    assert server.apply_proxy_tls_rule() is False and "FMP_TLS_VERIFY" not in server.os.environ
    monkeypatch.setenv("FMP_BASE_URL", "54.236.92.205")                   # no scheme
    assert server.apply_proxy_tls_rule() is True


def test_redact(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "abc123secret")
    assert server.redact("key abc123secret in ?apikey=zzz&x=1") == "key *** in ?apikey=***&x=1"
    monkeypatch.delenv("FMP_API_KEY")
    assert server.redact("APIKEY=q1 stays redacted") == "APIKEY=*** stays redacted"
