"""The analysis modules (plan 5.2-5.9) on synthetic chains with known answers, and on small hand-built examples."""
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

import config as C
import core
import modules as M
import synth
from conftest import load_chain, make_ctx

AS_OF = synth.AS_OF
EXP = date(2026, 11, 20)


def stock_ctx(surface, earnings_date=None, run=True):
    exps = synth.standard_expiries()
    p = synth.chain_payload("ACME", 100.0, roots_by_expiry={e: ("ACME",) for e in exps}, surface=surface,
                            security_type="stock", step=1.0)
    ctx = make_ctx(p, "ACME", "stock", earnings_date=earnings_date)
    if run:
        core.run_chain_modules(ctx)
    return ctx


def index_ctx(surface):
    exps = synth.standard_expiries(dailies=True)
    p = synth.chain_payload("_XSP", 735.37, roots_by_expiry={e: ("XSP",) for e in exps}, surface=surface, step=1.0)
    ctx = make_ctx(p, "XSP", "index")
    core.run_chain_modules(ctx)
    return ctx


def synth_jumps() -> dict:
    """{earnings date: the jump synth.py put on the next trading day} (most recent past report first)."""
    vals = (0.07, -0.05, 0.04, -0.09, 0.03, 0.06)
    return {r["date"]: vals[i % 6] for i, r in enumerate(synth.acme_earnings()[1:])}


@pytest.fixture(scope="module")
def acme(synth_run):
    return synth_run("ACME")[0]


@pytest.fixture(scope="module")
def acme_ctx(synth_data):
    ctx = make_ctx(load_chain(synth_data, "ACME"), "ACME", "stock", earnings_date=synth.EARNINGS_NEXT)
    core.run_chain_modules(ctx)
    return ctx


@pytest.fixture(scope="module")
def xsp_ctx():
    ctx = make_ctx(synth.xsp_payload(), "XSP", "index")
    core.run_chain_modules(ctx)
    return ctx


# ---------------------------------------------------------------------------- 5.6 earnings
def test_event_move_recovers_the_built_in_move(acme):
    ev = acme["earnings"]["event_move"]
    assert ev["method"] == "pre_post" and (ev["e1"], ev["e2"]) == ("2026-11-13", "2026-11-20")
    assert ev["move_1sd"] == pytest.approx(synth.EVENT_MOVE, abs=0.004)
    assert ev["expected_abs_move"] == pytest.approx(ev["move_1sd"] * math.sqrt(2 / math.pi), rel=1e-5)
    assert ev["event_variance_share"] == pytest.approx(ev["move_1sd"] ** 2 / (ev["sigma2"] ** 2 * ev["T2"]), rel=1e-4)
    assert ev["implied_window_1sd"] > ev["move_1sd"]                      # adds two days of base variance


def test_flat_base_isolates_the_event_exactly():
    flat = synth.Surface(0.30, 0.30, skew=0.0, curv=0.0, event=date(2026, 11, 17), event_move=0.05)
    ev = M.event_move(stock_ctx(flat, date(2026, 11, 17), run=False))
    assert ev["method"] == "pre_post"
    assert ev["sigma_base"] == pytest.approx(0.30, abs=2e-4) and ev["move_1sd"] == pytest.approx(0.05, abs=5e-4)


def test_post_event_pair_when_no_expiry_comes_before_the_event():
    d = date(2026, 11, 6)                                                 # the first expiry: not used
    flat = synth.Surface(0.30, 0.30, skew=0.0, curv=0.0, event=d, event_move=0.05)
    ev = M.event_move(stock_ctx(flat, d, run=False))
    assert ev["method"] == "post_event_pair" and (ev["e2"], ev["e3"]) == (date(2026, 11, 13), date(2026, 11, 20))
    assert ev["move_1sd"] == pytest.approx(0.05, abs=5e-4)
    assert any("same base vol" in n for n in ev["notes"])


def test_no_event_premium_on_a_falling_term_structure():
    ev = M.event_move(stock_ctx(synth.Surface(0.45, 0.30, decay_days=20), date(2026, 11, 17), run=False))
    assert ev["available"] and ev["move_1sd"] is None
    assert [n["id"] for n in ev["note_ids"] if n] == ["no_event_premium"]


def test_event_move_unavailable():
    ctx = stock_ctx(synth.Surface(0.30, 0.30), None, run=False)
    assert M.event_move(ctx)["reason"] == "no earnings date"
    late = stock_ctx(synth.Surface(0.30, 0.30), date(2028, 1, 5), run=False)
    assert M.event_move(late)["reason"] == "no expiry after the event"


def test_next_earnings_rules():
    d = AS_OF
    rows = [{"date": "2026-11-05", "epsActual": 1.2}, {"date": "2026-11-20", "epsActual": None},
            {"date": "2026-08-01", "epsActual": 1.0}, {"date": "not a date"}]
    assert M.next_earnings(rows, d, closed=True) == d                     # after the close, today's report counts
    assert M.next_earnings(rows, d, closed=False) == date(2026, 11, 20)   # intraday: already reported
    rows[0]["epsActual"] = None
    assert M.next_earnings(rows, d, closed=False) == d
    assert M.next_earnings([], d, True) is None and M.next_earnings(None, d, True) is None


def test_reactions_are_two_day_moves_around_past_reports(acme):
    r = acme["earnings"]["reactions"]
    # the 2023-11-17 report is older than the first daily bar (2023-12-04): 11 of the 12 past reports
    assert r["method"] == "two_day_close_to_close" and r["n"] == len(r["items"]) == 11 and not r["small_sample"]
    jumps = synth_jumps()
    for it in r["items"]:
        assert it["move"] == pytest.approx(jumps[it["date"]], abs=0.025)
        assert np.sign(it["move"]) == np.sign(jumps[it["date"]])
    a = [abs(i["move"]) for i in r["items"]]
    assert r["mean_abs"] == pytest.approx(np.mean(a), rel=1e-4) and r["max_abs"] == pytest.approx(max(a), rel=1e-5)


def test_earnings_block(acme):
    e = acme["earnings"]
    assert (e["next_date"], e["trading_days_to_event"], e["event_today"]) == ("2026-11-17", 8, False)
    assert e["timing"] == "unknown" and e["date_check"] == "kink_contains_date"
    c = e["comparison"]
    assert c["normal_benchmark_share"] == C.NORMAL_BEYOND_1SD
    assert c["ratio_implied_to_mean"] == pytest.approx(
        e["event_move"]["implied_window_abs"] / e["reactions"]["mean_abs"], rel=1e-4)
    beyond = [abs(i["move"]) > e["event_move"]["implied_window_1sd"] for i in e["reactions"]["items"]]
    assert c["share_above_implied_window_1sd"] == pytest.approx(sum(beyond) / len(beyond), rel=1e-5)


def test_earnings_today(acme_ctx):
    ctx = dict(acme_ctx, earnings_date=AS_OF, mods=dict(acme_ctx["mods"]))
    ctx["ev"] = {"e1": None, "e2": None, "e3": None}
    e = M.build_earnings(ctx)
    assert e["event_today"] and e["trading_days_to_event"] == 0
    assert "earnings_today" in [n["id"] for n in e["note_ids"] if n]
    assert e["reactions"]["available"] is False                            # no daily bars in this ctx


def test_index_has_no_earnings(synth_run):
    e = synth_run("SPX")[0]["earnings"]
    assert e["available"] is False and e["reason"] == "ETF/index: no earnings"


# ---------------------------------------------------------------------------- 5.3 term structure
def test_kink_marks_the_earnings_week(acme):
    ts = acme["term_structure"]
    assert [(k["from"], k["to"], k["contains_earnings"]) for k in ts["kinks"]] == [("2026-11-13", "2026-11-20", True)]
    assert ts["kinks"][0]["fwd_vol"] >= C.KINK_RATIO * ts["median_fwd_vol"]


def test_term_shapes(synth_run):
    spx = synth_run("SPX")[0]["term_structure"]
    assert spx["shape"] == "inverted" and spx["ratio_30_90"] > C.TERM_INVERTED
    up = M.build_term_structure(index_ctx(synth.Surface(0.16, 0.22)))
    assert up["shape"] == "upward" and up["ratio_30_90"] < C.TERM_UPWARD
    base = synth.Surface(0.16, 0.22).base
    for d in (30, 90):
        assert up["cm_iv"][f"{d}d"] == pytest.approx(base(d / 365), abs=0.004)
    flat = M.build_term_structure(index_ctx(synth.Surface(0.20, 0.20)))
    assert flat["shape"] == "flat" and not flat["kinks"]


def test_unknown_earnings_leaves_kinks_unexplained():
    ctx = stock_ctx(synth.Surface(0.40, 0.35, event=synth.EARNINGS_NEXT, event_move=0.06), None)
    ts = ctx["mods"]["term_structure"]
    assert ts["kinks"] and all(f["contains_earnings"] is None for f in ts["forward_vols"])
    assert "earnings date unknown: cannot tell whether a kink is earnings" in ts["notes"]


def test_index_forward_vols_never_contain_earnings(xsp_ctx):
    assert all(f["contains_earnings"] is False for f in xsp_ctx["mods"]["term_structure"]["forward_vols"])


# ---------------------------------------------------------------------------- 5.2 expected move
def test_expected_move_rows(acme):
    em, S = acme["expected_move"], synth.ACME_SPOT
    tags = {r["expiration"]: set(r["tags"]) for r in em["rows"]}
    assert {"next", "this_week"} <= tags["2026-11-06"] and "event" in tags["2026-11-20"]
    for r in em["rows"]:
        assert r["move_1sd_pct"] == pytest.approx(r["atm_iv"] * math.sqrt(r["T"]), rel=1e-4)
        assert r["range_1sd"] == pytest.approx([S * (1 - r["move_1sd_pct"]), S * (1 + r["move_1sd_pct"])], rel=1e-5)
        assert r["straddle_pct"] == pytest.approx(r["straddle"] / S, rel=1e-5)
    cm = acme["term_structure"]["cm_iv"]
    assert em["em_cm"]["30d"] == pytest.approx(cm["30d"] * math.sqrt(30 / 365), rel=1e-4)


# ---------------------------------------------------------------------------- 5.4 skew
def test_skew_sign_and_label(acme):
    sk, (steep, put, _flat) = acme["skew"], C.SKEW_LABELS["stock"]
    assert sk["label"] == "put" and put <= sk["rr25_30d"] < steep
    assert sk["thresholds"] == dict(zip(("steep_put", "put", "flat"), C.SKEW_LABELS["stock"]))
    for r in sk["by_expiry"]:
        assert r["rr25"] == pytest.approx(r["put25_iv"] - r["call25_iv"], abs=2e-6)


def test_call_skew_on_a_positive_skew_surface():
    sk = M.build_skew(index_ctx(synth.Surface(0.25, 0.25, skew=0.10)))
    assert sk["rr25_30d"] < C.SKEW_LABELS["index_etf"][2] and sk["label"] == "call"


def label_for(rr, thresholds):
    steep, put, flat = thresholds
    return "steep_put" if rr >= steep else "put" if rr >= put else "flat" if rr > flat else "call"


def test_index_thresholds(synth_run):
    sk, th = synth_run("SPX")[0]["skew"], C.SKEW_LABELS["index_etf"]
    assert sk["thresholds"] == dict(zip(("steep_put", "put", "flat"), th))
    assert sk["label"] == label_for(sk["rr25_30d"], th) == "put"


# ---------------------------------------------------------------------------- 5.5 volatility
def test_ex_event_iv_by_hand():
    t = 30 / 365
    assert M.ex_event_iv(0.40, True, 0.06) == pytest.approx(math.sqrt((0.16 * t - 0.0036) / t))
    assert M.ex_event_iv(0.40, False, 0.06) == 0.40
    assert M.ex_event_iv(0.40, None, 0.06) is None and M.ex_event_iv(0.40, True, None) is None
    assert M.ex_event_iv(0.10, True, 0.06) is None                        # the event alone exceeds 30 days of variance
    assert M.ex_event_iv(None, False, None) is None


def test_event_in_window():
    assert M.event_in_window("etf", None, True, AS_OF) is False
    assert M.event_in_window("stock", None, False, AS_OF) is None
    assert M.event_in_window("stock", AS_OF + timedelta(days=30), True, AS_OF) is True
    assert M.event_in_window("stock", AS_OF + timedelta(days=31), True, AS_OF) is False


def test_acme_ex_event_iv(acme):
    v, move = acme["volatility"], acme["earnings"]["event_move"]["move_1sd"]
    assert v["event_in_window"] is True and v["cm_iv_30d_ex_event"] < v["cm_iv_30d"]
    assert v["cm_iv_30d_ex_event"] == pytest.approx(M.ex_event_iv(v["cm_iv_30d"], True, move), rel=1e-4)
    base30 = synth.Surface(0.40, 0.35).base(30 / 365)                     # the surface without its event
    assert v["cm_iv_30d_ex_event"] == pytest.approx(base30, abs=0.01)
    assert v["vrp"] == pytest.approx(v["cm_iv_30d"] - v["realized"]["hv20"], abs=2e-6)


def test_realized_volatility_by_hand():
    a = 0.01
    lr = np.array([a if i % 2 else -a for i in range(300)])
    bars = pd.Series(100 * np.exp(np.concatenate([[0.0], np.cumsum(lr)])),
                     index=pd.bdate_range("2025-01-01", periods=301))
    rv = M.realized(bars)
    for n in C.HV_WINDOWS:                                               # even windows: mean 0, n a^2 / (n - 1)
        assert rv[f"hv{n}"] == pytest.approx(a * math.sqrt(n / (n - 1)) * math.sqrt(C.HV_ANNUAL))
    assert 0.0 <= rv["hv20_pctl_1y"] <= 100.0


def test_percentile_counts_strictly_below():
    assert M.pctl([1, 2, 3, 4], 3) == 50.0
    assert M.pctl([1, 2, 3, 4], 5) == 100.0 and M.pctl([1, 2, 3, 4], 1) == 0.0
    assert M.pctl([], 1) is None and M.pctl([1], None) is None and M.pctl([1], float("nan")) is None


def test_iv_index_block():
    rows = [{"date": AS_OF - timedelta(days=4 - i), "close": 10.0 + i} for i in range(5)]
    b = M.iv_index_block(rows, "VXN", AS_OF)
    assert (b["close"], b["pctl_1y"], b["n_obs"], b["notes"]) == (14.0, 80.0, 5, [])
    late = M.iv_index_block(rows, "VXN", AS_OF + timedelta(days=1))
    assert late["note_ids"] == [{"id": "index_close_from", "args": [AS_OF.isoformat()]}]
    assert M.iv_index_block(rows, None, AS_OF)["available"] is False
    assert M.iv_index_block([], "VXN", AS_OF)["reason"] == "VXN history unavailable"


# ---------------------------------------------------------------------------- 5.7 positioning
def frame(rows, exp=EXP):
    return pd.DataFrame([{"expiration": exp, "type": t, "strike": float(k), "open_interest": oi, "volume": v}
                         for t, k, oi, v in rows])


def test_max_pain_by_hand():
    x = frame([(t, k, 100, 0) for t in "CP" for k in (90, 100, 110)])
    assert M.max_pain(x, EXP) == 100.0              # writers pay 3000 at 90, 2000 at 100 and 3000 at 110
    x.loc[(x["type"] == "P") & (x["strike"] == 110), "open_interest"] = 1000
    assert M.max_pain(x, EXP) == 110.0              # heavy puts at 110: 21000, 11000, 3000
    assert M.max_pain(x, date(2027, 1, 15)) is None


def test_put_call_ratios_by_hand():
    q = frame([("C", 100, 1000, 200), ("C", 110, 0, 0), ("P", 90, 500, 300)])
    assert M._pcr(q) == {"volume": 1.5, "oi": 0.5}
    assert M._pcr(q[q["type"] == "P"]) == {"volume": None, "oi": None}


def test_key_levels(acme_ctx):
    kl, S = acme_ctx["mods"]["positioning"]["key_levels"], acme_ctx["session"]["spot"]
    w = acme_ctx["x"][acme_ctx["x"]["dte"] <= C.OI_WINDOW_DTE]
    for side, lo, hi in (("calls", S, S * 1.1), ("puts", S * 0.9, S)):
        rows = kl[side]
        assert 0 < len(rows) <= C.KEY_LEVEL_TOP
        assert [oi for _, oi in rows] == sorted((oi for _, oi in rows), reverse=True)
        for k, oi in rows:
            assert lo <= k <= hi
            assert oi == w.loc[(w["type"] == side[0].upper()) & (w["strike"] == k), "open_interest"].sum()
    assert kl["calls"][0][0] == kl["puts"][0][0] == 100.0                # open interest peaks at the money


def test_max_pain_on_the_synthetic_chain(acme):
    mp = acme["positioning"]["max_pain"]
    assert mp["evidence"] == "weak" and mp["monthly"] == {"expiration": "2026-11-20", "strike": 100.0}


# ---------------------------------------------------------------------------- 5.8 dealer gamma
@pytest.mark.parametrize("side, regime", [("P", "positive"), ("C", "negative")])
def test_gamma_sign_follows_the_convention(xsp_ctx, side, regime):
    x = xsp_ctx["x"].copy()
    x.loc[x["type"] == side, "open_interest"] = 0
    ctx = dict(xsp_ctx, x=x)
    g = M.build_gamma(ctx)
    assert g["regime"] == regime and np.sign(g["net_gex_1pct"]) == np.sign(g["profile_at_spot"])
    assert g["zero_gamma"] is None                                          # one-sided: the profile never crosses 0
    assert g["applicability"] == "standard" and g["convention"] == C.GEX_CONVENTION


def test_gamma_check_agrees_with_black_scholes(xsp_ctx):
    ctx = dict(xsp_ctx)
    g = M.build_gamma(ctx)
    assert ctx["gamma_check"]["ok"] and ctx["gamma_check"]["corr"] > 0.99
    assert g["call_wall"] >= ctx["session"]["spot"] >= g["put_wall"]


def test_single_stock_gamma_is_low_confidence(acme):
    g = acme["gamma"]
    assert g["applicability"] == "single_stock_low_confidence" and g["regime"] in ("positive", "negative", "near_zero")


# ---------------------------------------------------------------------------- 5.9 unusual activity
def test_flow_flags_the_hot_contract(acme):
    f = acme["flow"]
    assert f["rules"] == {"min_volume": C.UA_MIN_VOLUME, "min_vol_oi": C.UA_VOL_OI}
    hot = next(r for r in f["rows"] if r["option"] == "ACME261106C00105000")
    assert (hot["volume"], hot["open_interest"], hot["vol_oi"]) == (2400, 120, 20.0)
    for r in f["rows"]:
        assert r["volume"] >= C.UA_MIN_VOLUME and (r["vol_oi"] is None or r["vol_oi"] >= C.UA_VOL_OI)
        assert r["premium"] == pytest.approx(r["volume"] * r["price"] * C.MULTIPLIER, rel=1e-4)
    assert [r["premium"] for r in f["rows"]] == sorted((r["premium"] for r in f["rows"]), reverse=True)
