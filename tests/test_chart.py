"""Chart data (plan 8.2) and the self-contained chart.html (plan 8.3)."""
import json
import math
import re
from datetime import date, datetime

import numpy as np
import pytest

import chain as CH
import chart
import config as C

TICKER_SECTIONS = ("meta", "kpis", "cone", "term", "em", "smile", "oi", "gex", "strategies", "earnings", "chain", "flow",
                   "skew", "volatility", "positioning", "market_context", "history", "data_notes")
MARKET_SECTIONS = ("meta", "kpis", "thermometer", "vix", "term", "gex", "skew_board", "skew_thresholds", "gamma_board",
                   "em", "pcr", "realized", "vix_history", "spark", "history", "data_notes")


@pytest.fixture(scope="module")
def charts(synth_run):
    return {t: synth_run(t)[1] for t in ("ACME", "SPX", "XSP", "NDX", "MARKET")}


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


def test_payload_size(charts):
    for t, c in charts.items():
        assert len(chart.embed_json(c).encode("utf-8")) < C.CHART_MAX_BYTES, t


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
