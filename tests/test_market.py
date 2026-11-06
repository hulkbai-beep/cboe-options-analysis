"""The market view (plan 7): VIX bands, the thermometer's flags and labels, and run_market on the synthetic data."""
import pytest

import analyze
import config as C
import market as MK
from conftest import NOW, fakes_for

FLAGS = C.THERMO_FLAGS


def vix(level):
    return {"available": level is not None, "level": level}


def spx_mods(shape="flat", r30_90=1.0, r7=1.0, rr=0.0, label="flat", regime="positive", at_spot=1.0, cm30=0.20):
    return {"term_structure": {"available": True, "shape": shape, "ratio_30_90": r30_90, "ratio_7_30": r7,
                               "cm_iv": {"30d": cm30}},
            "skew": {"available": True, "label": label, "rr25_30d": rr},
            "gamma": {"available": True, "regime": regime, "profile_at_spot": at_spot}}


def fire(n: int):
    """(vix, mods, hv20) with the first n flags of THERMO_FLAGS fired."""
    on = set(FLAGS[:n])
    m = spx_mods(shape="inverted" if "term_inverted" in on else "flat", r30_90=1.05 if "term_inverted" in on else 1.0,
                 r7=1.2 if "front_stress" in on else 1.0, label="steep_put" if "skew_steep" in on else "flat",
                 rr=0.09 if "skew_steep" in on else 0.0, regime="negative" if "negative_gamma" in on else "positive",
                 at_spot=-1.0 if "negative_gamma" in on else 1.0)
    return vix(25.0 if "vix_20_or_more" in on else 15.0), m, 0.25 if "realized_above_implied" in on else 0.15


# ---------------------------------------------------------------------------- VIX bands
def test_vix_bands():
    levels = (None, 14.99, 15.0, 19.99, 20.0, 29.99, 30.0, 80.0)
    assert [MK.band(v) for v in levels] == [None, "low", "normal", "normal", "elevated", "elevated", "very_high",
                                            "very_high"]


# ---------------------------------------------------------------------------- thermometer
@pytest.mark.parametrize("n, label", [(0, "calm"), (1, "calm"), (2, "cautious"), (3, "cautious"), (4, "stressed"),
                                      (5, "stressed"), (6, "stressed")])
def test_labels_count_the_flags(n, label):
    t = MK.thermometer(*fire(n))
    assert [k for k in FLAGS if t["flags"][k]["fired"]] == list(FLAGS[:n])
    assert (t["n_flags"], t["n_available"], t["label"], t["label_uncertain"], t["missing"]) == (n, 6, label, False, [])


def test_flag_boundaries():
    def fired(flag, v, m, hv):
        return MK.thermometer(v, m, hv)["flags"][flag]["fired"]

    assert fired("vix_20_or_more", vix(20.0), spx_mods(), 0.1) is True              # 20 or more
    assert fired("vix_20_or_more", vix(19.99), spx_mods(), 0.1) is False
    assert fired("front_stress", vix(15), spx_mods(r7=C.THERMO_FRONT_RATIO), 0.1) is False   # strictly above 1.10
    assert fired("front_stress", vix(15), spx_mods(r7=1.1001), 0.1) is True
    assert fired("term_inverted", vix(15), spx_mods(shape="inverted", r30_90=1.03), 0.1) is True
    assert fired("term_inverted", vix(15), spx_mods(shape="flat", r30_90=1.02), 0.1) is False
    assert fired("skew_steep", vix(15), spx_mods(label="put", rr=0.079), 0.1) is False
    assert fired("negative_gamma", vix(15), spx_mods(regime="near_zero", at_spot=-1.0), 0.1) is False
    assert fired("realized_above_implied", vix(15), spx_mods(cm30=0.20), 0.20) is False     # strictly above IV30
    assert fired("realized_above_implied", vix(15), spx_mods(cm30=0.20), 0.2001) is True
    t = MK.thermometer(vix(21.0), spx_mods(r30_90=1.0, r7=1.05, rr=0.01, at_spot=2.0, cm30=0.18), 0.12)
    f = t["flags"]
    assert (f["vix_20_or_more"]["value"], f["vix_20_or_more"]["threshold"]) == (21.0, C.THERMO_VIX_LEVEL)
    assert (f["front_stress"]["value"], f["front_stress"]["threshold"]) == (1.05, C.THERMO_FRONT_RATIO)
    assert (f["skew_steep"]["value"], f["skew_steep"]["threshold"]) == (0.01, C.SKEW_LABELS["index_etf"][0])
    assert (f["realized_above_implied"]["value"], f["realized_above_implied"]["threshold"]) == (0.12, 0.18)


def test_missing_flags_make_the_label_uncertain():
    v, m, hv = fire(1)
    m["skew"] = {"available": False}
    t = MK.thermometer(v, m, None, "FMP key rejected")
    assert (t["n_flags"], t["n_available"], t["label"], t["label_uncertain"]) == (1, 4, "calm", True)
    assert t["missing"] == ["skew_steep", "realized_above_implied"]
    assert t["flags"]["skew_steep"]["reason"] == "SPX skew unavailable"
    assert t["flags"]["realized_above_implied"]["reason"] == "FMP key rejected"


def test_fewer_than_four_flags_give_no_label():
    v, m, hv = fire(4)
    m["term_structure"] = {"available": False}
    t = MK.thermometer(vix(None), m, hv)
    assert t["n_available"] == 2 and t["label"] is None and t["label_uncertain"]
    assert "only 2 of 6 flags available (need 4): no label" in t["notes"]
    assert t["flags"]["vix_20_or_more"]["reason"] == "VIX level unavailable"
    assert t["flags"]["front_stress"]["reason"] == "SPX 7-day IV unavailable or extrapolated"
    assert t["flags"]["realized_above_implied"]["reason"] == "SPX 30-day IV unavailable"


def test_daily_bars_reason_default():
    t = MK.thermometer(vix(15.0), spx_mods(), None)
    assert t["flags"]["realized_above_implied"]["reason"] == "S&P 500 daily bars unavailable"


# ---------------------------------------------------------------------------- run_market
@pytest.fixture(scope="module")
def market(synth_run):
    return synth_run("MARKET")[0]


def test_market_structure(market):
    assert tuple(market) == analyze.MARKET_KEYS and market["mode"] == "market"
    assert market["meta"]["errors"] == [] and market["as_of"] == "2026-11-05"
    assert [u["key"] for u in market["snapshot"]["universe"]] == ["SPX", "SPY", "QQQ", "IWM"]
    assert all(u["available"] for u in market["snapshot"]["universe"])


def test_market_vix(market):
    v = market["vix"]
    assert (v["level"], v["band"]) == (18.5, "normal")
    assert v["vix_vix3m"] == pytest.approx(18.5 / 19.6, rel=1e-5)        # the last closes of the CBOE CSVs
    assert v["vix9d_vix"] == pytest.approx(17.0 / 18.5, rel=1e-5)
    assert 0 <= v["pctl_1y"] <= 100 and v["notes"] == []


def test_thermometer_uses_the_ticker_computation(market, synth_run):
    spx = synth_run("SPX")[0]
    f, t = market["thermometer"]["flags"], spx["term_structure"]
    assert f["term_inverted"]["value"] == t["ratio_30_90"] and f["front_stress"]["value"] == t["ratio_7_30"]
    assert f["skew_steep"]["value"] == spx["skew"]["rr25_30d"]
    assert f["negative_gamma"]["value"] == spx["gamma"]["profile_at_spot"]
    assert f["realized_above_implied"]["value"] == market["realized"]["spx_hv20"]
    assert f["realized_above_implied"]["threshold"] == market["realized"]["spx_cm_iv_30d"] == t["cm_iv"]["30d"]
    # the synthetic SPX surface is inverted with a steep front: two flags, cautious
    th = market["thermometer"]
    assert [k for k in FLAGS if f[k]["fired"]] == ["term_inverted", "front_stress"]
    assert (th["n_flags"], th["label"], th["label_uncertain"]) == (2, "cautious", False)


def test_boards(market):
    assert [r["key"] for r in market["skew_board"]["rows"]] == ["SPX", "SPY", "QQQ", "IWM"]
    assert {r["key"] for r in market["expected_moves"]["rows"]} == set(C.MARKET_EM_KEYS)
    for r in market["expected_moves"]["rows"]:
        assert {"next_week", "monthly"} & set(r["tags"])
    assert market["realized"]["vrp"] == pytest.approx(market["realized"]["spx_cm_iv_30d"] - market["realized"]["spx_hv20"],
                                                      abs=2e-6)


def test_member_warnings_carry_their_key(market):
    dq = market["data_quality"]
    assert len(dq["warnings"]) == len(dq["warning_ids"])
    for text, wid in zip(dq["warnings"], dq["warning_ids"]):
        if wid and wid.get("key"):
            assert text.startswith(wid["key"] + ": ")
            assert text[len(wid["key"]) + 2:] == C.WARNINGS[wid["id"]].format(*wid["args"])


def test_a_missing_member_does_not_stop_the_market(synth_data, tmp_path):
    cboe, fmp = fakes_for(synth_data)
    cboe.missing = {"IWM", "_VIX"}
    m, _ = analyze.run_market(client=cboe, fmp=fmp, now=NOW, data_dir=tmp_path)
    assert m["meta"]["errors"] == []
    iwm = next(u for u in m["snapshot"]["universe"] if u["key"] == "IWM")
    assert iwm["available"] is False and "IWM" in iwm["reason"]
    assert m["thermometer"]["n_available"] == 6 and m["thermometer"]["label"] == "cautious"
    # no VIX chain: the level is the last close of CBOE's VIX history
    assert m["vix"]["level"] == pytest.approx(18.5) and m["vix"]["prev_close"] is None
    assert "VIX chain unavailable: level is the last close in CBOE's VIX history" in m["vix"]["notes"]
