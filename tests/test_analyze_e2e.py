"""run_analysis / run_market end to end on the synthetic data (plan 8, 12): the shape of metrics.json, the fixed-id
sentences, degraded FMP, symbol fallbacks and the command line."""
import json
import re

import pytest

import analyze
import config as C
import fetch
from cboe_client import CboeUnreachable
from conftest import NOW, fakes_for
from symbols import SymbolRejected

TICKERS = ("ACME", "SPY", "SPX", "NDX", "XSP")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$")
ENUM_FIELDS = {"shape": "shape_", "band": "band_", "applicability": "applic_", "regime": "regime_",
               "liquidity": "liq_", "kind_source": "ksrc_", "timing": "timing_", "spot_basis": "basis_",
               "method": "method_", "date_check": "date_check_", "window_label": "window_", "settlement": "settle_",
               "exercise_style": "style_", "atm_iv_source": "ivsrc_", "iv30_change_source": "iv30src_",
               "evidence": "evidence_", "reason_id": "reason_", "tags": "tag_"}


def walk(obj, path=""):
    """(path, dict) for every dict inside obj."""
    if isinstance(obj, dict):
        yield path, obj
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")


@pytest.fixture(scope="module", params=TICKERS)
def ticker_run(request, synth_run):
    return request.param, synth_run(request.param)[0]


@pytest.fixture(scope="module")
def all_runs(synth_run):
    return {t: synth_run(t)[0] for t in TICKERS + ("MARKET",)}


# ---------------------------------------------------------------------------- shape of metrics.json
def test_key_order_and_strict_json(ticker_run):
    t, m = ticker_run
    assert tuple(m) == analyze.TICKER_KEYS and m["mode"] == "ticker" and m["ticker"] == t
    assert json.loads(analyze.dumps(m)) == m                              # no NaN, no inf, plain types
    assert m["meta"]["errors"] == [] and m["as_of"] == "2026-11-05"
    assert m["meta"]["disclaimer"] == C.DISCLAIMER


def test_modules_say_why_they_are_unavailable(all_runs):
    for name, m in all_runs.items():
        keys = analyze.MARKET_KEYS[6:15] if name == "MARKET" else analyze.TICKER_KEYS[7:18]
        for k in keys:
            assert isinstance(m[k].get("available"), bool), (name, k)
        for path, d in walk(m):
            if d.get("available") is False:
                assert isinstance(d.get("reason"), str) and d["reason"], (name, path)
        for k, why in m["meta"]["modules_unavailable"].items():
            assert why, (name, k)


def test_times_are_iso_with_an_offset(all_runs):
    for name, m in all_runs.items():
        assert ISO.match(m["quote_time"]) and ISO.match(m["meta"]["generated_at"]) and ISO.match(m["meta"]["fetched_at"])
        if name != "MARKET":
            s = m["snapshot"]["session"]
            assert ISO.match(s["feed_time"]) and ISO.match(s["data_time"]) and s["as_of"] == "2026-11-05"
            assert s["data_time"] == "2026-11-05T16:00:00-05:00"                  # a closed session: the close
            for r in m["flow"].get("rows") or []:
                assert ISO.match(r["last_trade_time"])


def test_fixed_sentences_match_their_ids(all_runs):
    for name, m in all_runs.items():
        for path, d in walk(m):
            for text_key, id_key, table, prefix in (("notes", "note_ids", C.NOTES, "note_"),
                                                     ("warnings", "warning_ids", C.WARNINGS, "warn_")):
                if not isinstance(d.get(text_key), list):
                    continue
                assert len(d[text_key]) == len(d[id_key]), (name, path)
                for text, nid in zip(d[text_key], d[id_key]):
                    assert isinstance(text, str) and text
                    if nid is None:
                        continue
                    assert nid["id"] in C.ENUMS[prefix], (name, path, nid)
                    want = table[nid["id"]].format(*nid["args"])
                    assert text == (f"{nid['key']}: {want}" if nid.get("key") else want), (name, path)


def test_enumerated_values(all_runs):
    for name, m in all_runs.items():
        for path, d in walk(m):
            for field, prefix in ENUM_FIELDS.items():
                v = d.get(field)
                if isinstance(v, str):
                    assert v in C.ENUMS[prefix], (name, path, field, v)
                elif field == "tags" and isinstance(v, list):
                    assert set(v) <= set(C.ENUMS[prefix]), (name, path, v)
        if name == "MARKET":
            assert m["thermometer"]["label"] in C.ENUMS["thermo_"]
            assert tuple(m["thermometer"]["flags"]) == C.THERMO_FLAGS
            assert all(r["label"] in C.ENUMS["skew_"] for r in m["skew_board"]["rows"])
        else:
            assert m["skew"]["label"] in C.ENUMS["skew_"] and m["snapshot"]["kind"] in C.ENUMS["kind_"]


# ---------------------------------------------------------------------------- symbols and kinds
def test_snapshot_block(all_runs):
    acme, spx = all_runs["ACME"]["snapshot"], all_runs["SPX"]["snapshot"]
    assert (acme["ticker"], acme["cboe_symbol"], acme["fmp_symbol"], acme["kind"], acme["kind_source"]) == (
        "ACME", "ACME", "ACME", "stock", "fmp")
    assert acme["name"] == "Acme Synthetic Inc." and acme["spot"] == 100.0 and acme["spot_basis"] == "close"
    assert (spx["cboe_symbol"], spx["fmp_symbol"], spx["kind"], spx["kind_source"]) == ("_SPX", "^GSPC", "index",
                                                                                         "index_table")
    assert all_runs["SPY"]["snapshot"]["kind"] == "etf"
    assert all_runs["XSP"]["snapshot"]["fmp_symbol"] == "^GSPC"


def test_unknown_index_falls_back_to_the_underscore_symbol(synth_run):
    m = synth_run("OEX")[0]
    s = m["snapshot"]
    assert (s["cboe_symbol"], s["kind"], s["kind_source"], s["fmp_symbol"]) == ("_OEX", "index", "cboe_fallback", None)
    assert {"id": "index_fallback", "args": []} in s["note_ids"]
    assert m["meta"]["errors"] == [] and m["gamma"]["applicability"] == "standard"


def test_ndx_uses_qqq_bars(synth_run):
    v = synth_run("NDX")[0]["volatility"]
    assert v["realized"]["available"] and v["realized"]["source"] == C.NDX_PROXY
    assert {"id": "ndx_proxy", "args": []} in v["realized"]["note_ids"]


def test_vix_is_refused_before_any_request(synth_data):
    cboe, fmp = fakes_for(synth_data)
    with pytest.raises(SymbolRejected) as e:
        analyze.run_analysis("VIX", client=cboe, fmp=fmp, now=NOW, save=False)
    assert e.value.error_id == "vix_futures" and cboe.requests == [] and fmp.requests == []


def test_index_and_etf_blocks(all_runs):
    spx, spy = all_runs["SPX"], all_runs["SPY"]
    assert spx["earnings"]["reason"] == "ETF/index: no earnings" and spx["volatility"]["event_in_window"] is False
    assert spx["volatility"]["cm_iv_30d_ex_event"] == spx["volatility"]["cm_iv_30d"]
    assert spx["market_context"]["available"] and spx["market_context"]["spx"]["as_of"] == "2026-11-05"
    assert spy["earnings"]["available"] is False and spy["strategies"]["covered_call"]["available"]


# ---------------------------------------------------------------------------- FMP problems never stop a run
def test_fmp_unreachable(synth_run):
    m = synth_run("ACME", unreachable=True)[0]
    assert m["meta"]["errors"] == [] and m["expected_move"]["available"] and m["skew"]["available"]
    assert m["earnings"]["available"] is False and m["earnings"]["reason"].startswith("FMP unreachable")
    assert m["volatility"]["realized"]["available"] is False
    assert any(n.startswith("FMP unreachable") for n in m["meta"]["notes"])
    assert m["strategies"]["available"] and {"id": "earnings_unknown", "args": []} in m["strategies"]["note_ids"]


def test_fmp_key_rejected(synth_run):
    m = synth_run("ACME", key_rejected=True)[0]
    assert m["meta"]["errors"] == [] and m["earnings"]["reason"] == "FMP key rejected"
    assert "FMP key rejected: FMP blocks are unavailable" in m["meta"]["notes"]
    assert m["snapshot"]["kind"] == "stock" and m["snapshot"]["kind_source"] == "default"


def test_fmp_endpoint_restricted(synth_run):
    m = synth_run("ACME", blocked=("earnings",))[0]
    assert m["meta"]["errors"] == [] and m["meta"]["restricted"] == ["earnings (ACME)"]
    assert m["earnings"]["reason"] == "restricted on the current FMP plan: earnings"
    assert m["meta"]["skipped"]["earnings"] == "restricted on the current FMP plan: earnings"
    assert m["volatility"]["realized"]["available"]                       # the other FMP data still arrives


def test_without_fmp(synth_data, tmp_path, no_network):
    cboe, _ = fakes_for(synth_data)
    m, chart = analyze.run_analysis("SPY", client=cboe, fmp=fetch.FmpState(None), now=NOW, data_dir=tmp_path)
    assert m["data_source"] == "CBOE delayed quotes (~15 min); FMP not configured"
    assert m["meta"]["errors"] == [] and m["meta"]["connection"]["fmp"] is None
    assert m["snapshot"]["kind_source"] == "known_etfs" and m["volatility"]["realized"]["reason"] == "FMP not configured"
    assert chart is not None


# ---------------------------------------------------------------------------- history across runs
def test_history_saves_once(synth_data, tmp_path):
    cboe, fmp = fakes_for(synth_data)
    first, _ = analyze.run_analysis("SPY", client=cboe, fmp=fmp, now=NOW, data_dir=tmp_path)
    assert first["history"]["saved_this_run"] and first["history"]["days"] == 1
    again, _ = analyze.run_analysis("SPY", client=cboe, fmp=fmp, now=NOW, data_dir=tmp_path)
    assert not again["history"]["saved_this_run"]
    assert again["history"]["notes"] == ["chain not saved: 2026-11-05 already saved"]
    off, _ = analyze.run_analysis("SPY", client=cboe, fmp=fmp, now=NOW, data_dir=tmp_path, save=False)
    assert off["history"]["notes"] == ["chain not saved: --no-save"]
    assert again["volatility"]["iv_rank"]["reason_id"] == "ivr_building"


# ---------------------------------------------------------------------------- command line
@pytest.fixture
def cli(synth_data, monkeypatch, tmp_path):
    def clients(client, fmp, refresh, massive=None):
        cboe, f = fakes_for(synth_data)
        return cboe, fetch.FmpState(f), fetch.MassiveState(None)

    monkeypatch.setattr(analyze, "_clients", clients)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_cli_writes_metrics_and_chart(cli, capsys):
    assert analyze.main(["ACME", "--data-dir", str(cli / "h")]) == 0
    out = cli / "oa-output" / "ACME_2026-11-05"
    m = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert m["ticker"] == "ACME" and (out / "chart.html").is_file()
    text = capsys.readouterr().out
    assert text.startswith("ACME  as of 2026-11-05  spot 100.00") and "expected move" in text
    assert analyze.main(["--market", "--out", str(cli / "mk"), "--data-dir", str(cli / "h")]) == 0
    assert (cli / "mk" / "metrics.json").is_file() and "thermometer" in capsys.readouterr().out


def test_cli_errors(cli, capsys, monkeypatch):
    assert analyze.main(["VIX"]) == 1 and "Cannot analyse 'VIX'" in capsys.readouterr().err
    assert analyze.main(["ZZZZQ", "--data-dir", str(cli / "h")]) == 1
    assert "CBOE error" in capsys.readouterr().err
    for argv in ([], ["--market", "SPY"], ["--market", "--no-market-context"]):
        with pytest.raises(SystemExit) as e:
            analyze.main(argv)
        assert e.value.code == 2


def test_cli_never_prints_the_key(cli, capsys, monkeypatch):
    secret = "test-key-0123456789"
    monkeypatch.setenv("FMP_API_KEY", secret)

    class Down:
        stats = {"network_calls": 0, "cache_hits": 0}

        def get_chain(self, symbol):
            raise CboeUnreachable(f"cannot reach the feed (url had apikey={secret})")

    monkeypatch.setattr(analyze, "_clients", lambda c, f, r, m=None: (Down(), fetch.FmpState(None),
                                                                      fetch.MassiveState(None)))
    assert analyze.main(["SPY"]) == 1
    err = capsys.readouterr().err
    assert secret not in err and "apikey=***" in err
