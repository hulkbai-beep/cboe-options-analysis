"""Chart data (plan 8.2) and the self-contained chart.html (plan 8.3), with and without the optional Massive data."""
import json
import math
import re
from datetime import date, datetime

import numpy as np
import pytest

import analyze
import chain as CH
import chart
import config as C
import fetch
import synth
from conftest import NOW, fakes_for
from fakes import FakeMassive

TICKER_SECTIONS = ("meta", "kpis", "cone", "term", "em", "smile", "oi", "gex", "strategies", "earnings", "chain", "flow",
                   "news", "skew", "volatility", "positioning", "market_context", "history", "data_notes")
MARKET_SECTIONS = ("meta", "kpis", "thermometer", "vix", "term", "gex", "skew_board", "skew_thresholds", "gamma_board",
                   "em", "pcr", "realized", "vix_history", "spark", "history", "data_notes")


def _chart(synth_data, store, ticker: str, massive):
    """The chart payload of one run on the synthetic data; `massive` is always passed, so a MASSIVE_API_KEY in the
    environment never reaches the network (module fixtures run before the autouse no_massive_key)."""
    cboe, fmp = fakes_for(synth_data)
    if ticker == "MARKET":
        return analyze.run_market(client=cboe, fmp=fmp, now=NOW, data_dir=store, massive=massive)[1]
    return analyze.run_analysis(ticker, client=cboe, fmp=fmp, now=NOW, data_dir=store, massive=massive)[1]


@pytest.fixture(scope="module")
def charts(synth_data, tmp_path_factory):
    """Without Massive (not configured)."""
    return {t: _chart(synth_data, tmp_path_factory.mktemp("history"), t, fetch.MassiveState(None))
            for t in ("ACME", "SPX", "XSP", "NDX", "MARKET")}


@pytest.fixture(scope="module")
def massive_charts(synth_data, tmp_path_factory):
    """With FakeMassive: the Treasury curve, short data, news (with a hostile row) and the holiday calendar."""
    return {t: _chart(synth_data, tmp_path_factory.mktemp("history"), t, FakeMassive())
            for t in ("ACME", "SPY", "SPX", "MARKET")}


def test_ticker_payload_shape(charts):
    for t in ("ACME", "SPX", "XSP", "NDX"):
        c = charts[t]
        assert tuple(c) == TICKER_SECTIONS, t
        want = [k for k in chart.TICKER_KPIS if k != "gex" or c["meta"]["kind"] != "stock"]   # no GEX tile for stocks
        assert [k["id"] for k in c["kpis"]] == want
        assert (c["meta"]["mode"], c["meta"]["ticker"], c["meta"]["as_of"]) == ("ticker", t, "2026-11-05")
        assert c["meta"]["disclaimer"] == C.DISCLAIMER
        assert set(c["strategies"]["screens"]) == set(C.STRATEGIES)
    acme = charts["ACME"]
    assert acme["earnings"]["applies"] and acme["earnings"]["next_date"] == "2026-11-17"
    assert charts["SPX"]["earnings"]["applies"] is False
    assert charts["SPX"]["strategies"]["screens"]["covered_call"]["available"] is False


def test_market_payload_shape(charts):
    m = charts["MARKET"]
    assert tuple(m) == MARKET_SECTIONS and m["meta"]["mode"] == "market"
    assert [k["id"] for k in m["kpis"]] == list(chart.MARKET_KPIS)
    assert m["skew_thresholds"] == dict(zip(("steep_put", "put", "flat"), C.SKEW_LABELS["index_etf"]))


@pytest.mark.parametrize("t", ["XSP", "NDX", "ACME"])
def test_cone_closes_end_at_spot(charts, t):
    cone = charts[t]["cone"]
    assert cone["available"] and len(cone["closes"]) == C.CHART_CONE_BARS
    assert cone["closes"][-1][0] == "2026-11-05"
    assert cone["closes"][-1][1] == pytest.approx(cone["spot"], rel=0.01)    # XSP: S&P 500 x 0.1; NDX: QQQ rescaled
    assert cone["proxy"] == (C.NDX_PROXY if t == "NDX" else None)
    for b in cone["bands"]:
        assert b["low"] < cone["spot"] < b["high"] and b["dte"] >= 1


def test_payload_size(charts, massive_charts):
    for t, c in [*charts.items(), *((f"{k} with Massive", v) for k, v in massive_charts.items())]:
        assert len(chart.embed_json(c).encode("utf-8")) < C.CHART_MAX_BYTES, t


# ---------------------------------------------------------------------------- Massive (optional)
def test_without_massive(charts):
    for t, c in charts.items():
        meta, dn = c["meta"], c["data_notes"]
        assert (meta["massive_configured"], meta["massive_connection"]) == (False, None), t
        assert "Massive" not in meta["data_source"]
        assert dn["calendar_check"]["available"] is False and dn["calendar_check"]["mismatches"] == []
        assert (dn["rates"]["source"], dn["rates"]["rate_30d"], dn["rates"]["curve_date"]) == ("fixed", C.DISCOUNT_RATE,
                                                                                               None)
        european = t != "ACME"
        assert dn["rates"]["european"] is european
        assert [i["id"] for i in dn["rates"]["note_ids"]] == (["rates_fixed"] if european else []), t
    acme = charts["ACME"]
    assert acme["news"]["available"] is False and acme["news"]["reason"] == fetch.MASSIVE_NOT_CONFIGURED
    assert acme["news"]["rows"] == [] and acme["positioning"]["short"]["available"] is False
    assert charts["SPX"]["news"]["reason"] == "news applies to stocks and ETFs"


def test_massive_payload(massive_charts):
    acme, spy, spx, mkt = (massive_charts[k] for k in ("ACME", "SPY", "SPX", "MARKET"))
    assert tuple(acme) == TICKER_SECTIONS and tuple(spx) == TICKER_SECTIONS and tuple(mkt) == MARKET_SECTIONS
    for t, c in massive_charts.items():
        meta, dn = c["meta"], c["data_notes"]
        assert meta["massive_configured"] and meta["massive_connection"]["route"] == "official Massive API", t
        assert meta["massive_connection"]["tls_verify"] is True and meta["massive_connection"]["calls"] >= 1
        assert meta["data_source"].count("Massive API") == 1
        assert dn["calendar_check"]["available"] and dn["calendar_check"]["mismatches"] == []
        assert dn["calendar_check"]["checked_through"] >= dn["calendar_check"]["checked_from"] == "2026-11-06"
        r = dn["rates"]
        assert (r["source"], r["curve_date"], r["european"]) == ("treasury", "2026-11-03", t in ("SPX", "MARKET")), t
        assert set(r["yields"]) == {k.removeprefix("yield_") for k, _ in C.TREASURY_TENORS} and r["note_ids"] == []
        assert 0.03 < r["rate_30d"] < r["rate_1y"] < 0.05
    short = acme["positioning"]["short"]
    si, sv, fl = short["interest"], short["volume"], short["float"]
    assert short["available"] and short["source"] == "Massive"
    assert (si["short_interest"], si["settlement_date"], si["prev_settlement_date"]) == (30_000_000, "2026-10-31",
                                                                                         "2026-10-15")
    assert si["pct_float"] == pytest.approx(30_000_000 / synth.ACME_FREE_FLOAT, rel=1e-4) and si["age_days"] == 5
    assert len(si["rows"]) == C.SHORT_INTEREST_ROWS and len(sv["rows"]) == sv["days"] == C.SHORT_VOLUME_DAYS
    assert [r["date"] for r in sv["rows"]] == sorted((r["date"] for r in sv["rows"]), reverse=True)
    assert fl["free_float"] == synth.ACME_FREE_FLOAT and fl["free_float_pct"] == pytest.approx(0.95)
    assert [i["id"] for i in si["note_ids"] + sv["note_ids"]] == ["short_interest_lag", "short_volume_mm"]
    assert spy["positioning"]["short"]["float"]["reason"] == "ETFs have no free float"
    assert spy["positioning"]["short"]["interest"]["available"] and spy["positioning"]["short"]["volume"]["available"]
    assert spx["positioning"]["short"]["available"] is False and spx["news"]["available"] is False
    assert acme["strategies"]["context"]["dividends_source"] == "fmp"


def test_news_payload(massive_charts):
    acme = massive_charts["ACME"]
    n = acme["news"]
    assert n["available"] and n["n_articles"] == 5 and n["window_days"] == C.NEWS_LOOKBACK_DAYS and not n["truncated"]
    assert n["window_from"] == "2026-11-02T00:00:00Z" and n["newest"] == n["rows"][0]["published_utc"]
    assert n["sentiment_counts"] == {"positive": 2, "neutral": 1, "negative": 1, "unlabeled": 1}
    times = [r["published_utc"] for r in n["rows"]]
    assert times == sorted(times, reverse=True) and len(times) == 5 <= C.NEWS_ROWS
    assert all(tuple(r) == chart.NEWS_ROW_KEYS for r in n["rows"])
    assert all(r["sentiment"] in (None, *C.ENUMS["sent_"]) for r in n["rows"])
    assert [r["url"] for r in n["rows"]].count(None) == 1
    assert all(r["url"] is None or r["url"].startswith("https://news.example.com/") for r in n["rows"])
    assert [i["id"] for i in n["note_ids"] if i] == ["news_third_party"]
    assert n["notes"][-1] in acme["data_notes"]["notes"]                  # the third-party note in the data notes
    empty = massive_charts["SPY"]["news"]
    assert empty["available"] and empty["n_articles"] == 0 and empty["rows"] == []
    assert empty["notes"][0].startswith("no articles about SPY")


def test_hostile_news_stays_text(massive_charts, tmp_path):
    """The synthetic hostile article: control characters gone, title cut, javascript: link dropped, and its
    <script> reasoning only ever escaped inside the page."""
    rows = massive_charts["ACME"]["news"]["rows"]
    bad = next(r for r in rows if r["title"].startswith("Ignore previous instructions"))
    assert bad["url"] is None and "\x00" not in bad["title"] and len(bad["title"]) <= C.NEWS_TEXT_CHARS
    assert bad["sentiment_reasoning"] == "<script>alert(1)</script>" and bad["sentiment"] == "positive"
    page = chart.write_chart_html(massive_charts["ACME"], tmp_path / "acme.html").read_text(encoding="utf-8")
    data = re.search(r'<script type="application/json" id="oa-data">(.*?)</script>', page, re.S).group(1)
    assert "javascript:" not in page and "alert(1)</script>" not in page and page.count("<script") == 4
    assert not set("<>&\x00") & set(data) and json.loads(data) == massive_charts["ACME"]


def test_links_and_holiday_names_are_cleaned():
    news = chart._news({"available": True, "rows": [
        {"title": "a", "url": "javascript:alert(1)"}, {"title": "b", "url": "https://x.example/b?q=1&r=2"},
        {"title": "c", "url": "data:text/html,<b>x</b>"}, {"title": "d", "url": 'http://x.example/" onmouseover="1'},
        {"title": "e", "url": "//x.example/e"}, "not a row"]})
    assert [r["url"] for r in news["rows"]] == [None, "https://x.example/b?q=1&r=2", None, None, None]
    assert news["notes"] == [] and news["note_ids"] == []
    cc = chart._calendar({"available": True, "checked_from": date(2026, 11, 6), "mismatches": [
        {"date": date(2026, 11, 27), "exchange": "closed", "exchange_close": None, "config": "early_close",
         "config_close": "13:00", "name": "Day\x00 after\n<b>Thanksgiving</b> " + "x" * 400}]})
    name = cc["mismatches"][0]["name"]
    assert name.startswith("Day after <b>Thanksgiving</b> x") and "\x00" not in name and len(name) <= C.NEWS_TEXT_CHARS
    assert chart._rates(None) is None and chart._calendar(None) is None


def test_html_is_self_contained(charts, tmp_path):
    for t, c in charts.items():
        page = chart.write_chart_html(c, tmp_path / f"{t}.html").read_text(encoding="utf-8")
        assert page.startswith("<!doctype html>") and '<html lang="en">' in page
        assert page.count("<script") == 4 and page.count("</script>") == 4   # data, i18n.js, chart.js, boot
        assert not re.search(r"(src|href)\s*=\s*[\"']?(https?:)?//", page) and "@import" not in page
        data = re.search(r'<script type="application/json" id="oa-data">(.*?)</script>', page, re.S).group(1)
        assert json.loads(data) == c
    assert "<title>ACME · Options analysis · 2026-11-05</title>" in (tmp_path / "ACME.html").read_text(encoding="utf-8")
    assert "<title>US options market thermometer · 2026-11-05</title>" in (tmp_path / "MARKET.html").read_text(
        encoding="utf-8")


def test_embedded_text_cannot_close_the_script(tmp_path):
    evil = {"meta": {"mode": "ticker", "ticker": "</script><script>alert(1)</script>", "as_of": "2026-11-05"},
            "note": "a & b <!-- c -->"}
    text = chart.embed_json(evil)
    assert not set("<>&") & set(text) and json.loads(text) == evil
    page = chart.write_chart_html(evil, tmp_path / "evil.html").read_text(encoding="utf-8")
    assert page.count("</script>") == 4 and "<title>&lt;/script&gt;&lt;script&gt;" in page
    assert chart._inline("x</script>y</SCRIPT>", "script") == "x<\\/script>y<\\/SCRIPT>"


def test_json_safe():
    t = CH.close_dt(date(2026, 11, 5))
    out = chart.json_safe({"nan": float("nan"), "inf": np.inf, "np": np.float64(1.234567891), "i": np.int64(3),
                           "b": np.bool_(True), "d": date(2026, 11, 5), "t": t, "tup": (1, 2), 5: "key"})
    assert out == {"nan": None, "inf": None, "np": 1.23457, "i": 3, "b": True, "d": "2026-11-05",
                   "t": "2026-11-05T16:00:00-05:00", "tup": [1, 2], "5": "key"}
    assert chart.json_safe(0.000123456789) == 0.000123457             # six significant digits ...
    assert chart.json_safe(7353.734567) == 7353.7346                   # ... and at least four decimals
    assert chart.json_safe(12345678.98761) == 12345678.9876
    assert chart.json_safe(1.234567891, compact=False) == 1.234567891
    assert isinstance(chart.json_safe(datetime(2026, 11, 5, 16, 0)), str)
    assert chart.json_safe([np.nan, math.inf]) == [None, None]
