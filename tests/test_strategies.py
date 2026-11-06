"""Strategy screens (plan 6): the arithmetic of every row, the filters and windows, and the earnings straddle."""
import math
from datetime import date

import pytest

import chain as CH
import config as C
import core
import strategies as ST
import synth
from conftest import load_chain, make_ctx

AS_OF = synth.AS_OF
S = synth.ACME_SPOT
REL = 1e-4                         # metrics keep 6 significant digits


@pytest.fixture(scope="module")
def acme(synth_run):
    return synth_run("ACME")[0]["strategies"]


@pytest.fixture(scope="module")
def acme_ctx(synth_data):
    ctx = make_ctx(load_chain(synth_data, "ACME"), "ACME", "stock", earnings_date=synth.EARNINGS_NEXT)
    core.run_chain_modules(ctx)
    ctx["dividends"] = [(date(2026, 11, 12), 0.25)]
    return ctx


def rows(st, name):
    return st[name]["rows"]


def expiry(r):
    return date.fromisoformat(r["expiration"]) if isinstance(r["expiration"], str) else r["expiration"]


# ---------------------------------------------------------------------------- shared row rules
@pytest.mark.parametrize("name", list(C.STRATEGIES))
def test_rows_respect_the_screen(acme, name):
    typ, band, win, target = C.STRATEGIES[name]
    b = acme[name]
    assert (b["type"], b["delta_band"], b["dte_window"], b["target_delta"]) == (typ, list(band), list(win), target)
    rs = rows(acme, name)
    assert 0 < len(rs) <= C.STRAT_MAX_ROWS
    assert [r["expiration"] for r in rs] == sorted({r["expiration"] for r in rs})          # one row per expiry
    for r in rs:
        assert band[0] <= r["delta"] <= band[1] and win[0] <= r["dte"] <= win[1]
        assert r["open_interest"] >= C.STRAT_MIN_OI and r["spread_pct"] <= C.STRAT_MAX_SPREAD
        assert r["spread_pct"] == pytest.approx((r["ask"] - r["bid"]) / r["mid"], abs=2e-4)   # a difference of rounded prices
        assert r["premium_per_contract"] == pytest.approx(r["mid"] * C.MULTIPLIER, rel=REL)
        assert r["otm_pct"] == pytest.approx(r["strike"] / S - 1, rel=REL, abs=1e-9)
        assert r["breakeven_move_pct"] == pytest.approx(r["breakeven"] / S - 1, rel=REL, abs=1e-9)
        assert r["pct_of_example_account"] == pytest.approx(r["capital"] / C.EXAMPLE_ACCOUNT, rel=REL)
        assert 0 < r["p_itm"] < 1 and 0 < r["p_profit"] < 1
        if r["p_itm"] <= 0.5:
            assert r["p_touch"] == pytest.approx(2 * r["p_itm"], rel=REL)
        else:
            assert r["p_touch"] is None
        grade = ("A" if r["open_interest"] >= C.LIQ_A_OI and r["volume"] >= C.LIQ_A_VOLUME
                 else "B" if r["open_interest"] >= C.LIQ_B_OI else "C")
        assert r["liquidity"] == grade and r["exercise_style"] == "american"
        assert r["earnings_before_expiry"] is (AS_OF <= synth.EARNINGS_NEXT <= expiry(r))


@pytest.mark.parametrize("name", list(C.STRATEGIES))
def test_each_row_is_closest_to_the_target_delta(acme_ctx, name):
    typ, band, win, target = C.STRATEGIES[name]
    out, _ = ST._screen(name, acme_ctx, synth.EARNINGS_NEXT, True)
    x = acme_ctx["x"]
    q = x[x["surface"] & x["valid_quote"] & x["valid_iv"] & (x["type"] == typ) & (x["open_interest"] >= C.STRAT_MIN_OI)
          & x["delta"].between(*band) & x["dte"].between(*win)]
    q = q[(q["ask"] - q["bid"]) / q["mid"] <= C.STRAT_MAX_SPREAD]
    assert out["candidates"] == len(q)
    for r in out["rows"]:
        same = q[q["expiration"] == r["expiration"]]
        assert abs(r["delta"] - target) == pytest.approx((same["delta"] - target).abs().min())


@pytest.mark.parametrize("edge", [0, 1])
def test_delta_and_dte_windows_are_closed(acme_ctx, edge):
    _typ, band, win, _target = C.STRATEGIES["short_put"]
    x = acme_ctx["x"].copy()
    e = x["expiration"] == date(2026, 11, 20)
    x.loc[e, "dte"] = win[edge]                                       # exactly on the dte bound
    x.loc[e & (x["type"] == "P"), "delta"] = -0.5                     # every put of the expiry outside the band ...
    pick = e & (x["type"] == "P") & (x["strike"] == 95.0)
    x.loc[pick, "delta"] = band[edge]                                 # ... but one exactly on the delta bound
    out, _ = ST._screen("short_put", dict(acme_ctx, x=x), synth.EARNINGS_NEXT, True)
    r = next(r for r in out["rows"] if r["expiration"] == date(2026, 11, 20))
    assert (r["strike"], r["delta"], r["dte"]) == (95.0, band[edge], win[edge])


def test_row_expiries_when_the_table_is_full():
    exps = {date(2027, 6, 17), date(2027, 6, 16), date(2027, 6, 11), date(2027, 6, 10)}
    assert ST.keep_row_expiry(date(2027, 6, 11), exps)                # a Friday
    assert ST.keep_row_expiry(date(2027, 6, 17), exps)                # Thursday before the 2027-06-18 holiday
    assert not ST.keep_row_expiry(date(2027, 6, 10), exps)            # Thursday whose Friday is an expiry
    assert not ST.keep_row_expiry(date(2027, 6, 16), exps)            # a Wednesday


# ---------------------------------------------------------------------------- per-strategy arithmetic
def test_short_put_arithmetic(acme):
    for r in rows(acme, "short_put"):
        k, m, b = r["strike"], r["mid"], r["bid"]
        assert r["breakeven"] == pytest.approx(k - m, rel=REL)
        assert r["yield"] == pytest.approx(m / (k - m), rel=REL) and r["yield_bid"] == pytest.approx(b / (k - b), rel=REL)
        assert r["annualized_yield"] == pytest.approx(r["yield"] * 365 / r["dte"], rel=REL)
        assert r["capital"] == pytest.approx(k * C.MULTIPLIER, rel=REL)
        assert r["max_loss"] == pytest.approx((k - m) * C.MULTIPLIER, rel=REL)
        assert r["p_profit"] >= 1 - r["p_itm"]                         # the premium moves the break-even down
    assert "early_assignment" in [n["id"] for n in acme["short_put"]["note_ids"] if n]


def test_covered_call_arithmetic_and_dividend(acme):
    rs = rows(acme, "covered_call")
    for r in rs:
        k, m = r["strike"], r["mid"]
        assert r["breakeven"] == pytest.approx(S - m, rel=REL)
        assert r["yield"] == pytest.approx(m / S, rel=REL) and r["yield_bid"] == pytest.approx(r["bid"] / S, rel=REL)
        assert r["upside_cap_pct"] == pytest.approx(k / S - 1, rel=REL)
        assert r["if_called_return"] == pytest.approx((k - S + m) / S, rel=REL)
        assert r["capital"] == pytest.approx(S * C.MULTIPLIER) and r["max_loss"] == pytest.approx((S - m) * C.MULTIPLIER, rel=REL)
        if expiry(r) >= date(2026, 11, 12):                           # ACME goes ex-dividend 0.25 on 2026-11-12
            assert (r["ex_div_date"], r["dividend"]) == ("2026-11-12", 0.25)
            assert r["yield_with_div"] == pytest.approx((m + 0.25) / S, rel=REL)
        else:
            assert r["ex_div_date"] is None and r["yield_with_div"] is None
    assert {"id": "ex_div_call", "args": ["2026-11-12", "0.25"]} in acme["covered_call"]["note_ids"]


@pytest.mark.parametrize("name, sign", [("long_call", 1), ("long_put", -1)])
def test_long_option_arithmetic(acme, name, sign):
    for r in rows(acme, name):
        k, m = r["strike"], r["mid"]
        assert r["breakeven"] == pytest.approx(k + sign * m, rel=REL)
        assert r["breakeven_ask"] == pytest.approx(k + sign * r["ask"], rel=REL)
        assert r["capital"] == r["max_loss"] == pytest.approx(m * C.MULTIPLIER, rel=REL)
        assert r["p_profit"] < r["p_itm"]                              # it must finish beyond strike + premium


def test_context_and_filters(acme, synth_run):
    ctx, f = acme["context"], acme["filters"]
    assert ctx["next_earnings"] == "2026-11-17" and ctx["term_shape"] == "inverted" and ctx["skew_label"] == "put"
    assert (f["min_oi"], f["max_spread"]) == (C.STRAT_MIN_OI, C.STRAT_MAX_SPREAD)
    assert f["strategies"]["short_put"] == {"type": "P", "delta": [-0.35, -0.10], "dte": [14, 60], "target_delta": -0.25}
    assert "closed_spread" in [n["id"] for n in acme["note_ids"] if n]
    assert synth_run("SPX")[0]["strategies"]["context"]["next_earnings"] is None


# ---------------------------------------------------------------------------- kinds
def test_index_has_no_covered_call(synth_run):
    st = synth_run("SPX")[0]["strategies"]
    assert (st["covered_call"]["available"], st["covered_call"]["reason"]) == (False, "index: no shares to cover")
    for name in ("short_put", "long_call", "long_put"):
        for r in rows(st, name):
            assert r["exercise_style"] == "european" and r["earnings_before_expiry"] is False
            assert r["settlement"] == ("AM" if r["option"].startswith("SPX2") else "PM")
    assert st["earnings_straddle"]["reason"] == "ETF/index: no earnings"


def test_etf_screens(synth_run):
    st = synth_run("SPY")[0]["strategies"]
    assert st["covered_call"]["available"] and all(r["earnings_before_expiry"] is False for r in rows(st, "covered_call"))
    assert st["earnings_straddle"]["available"] is False


def test_unknown_earnings_date(synth_run):
    past = [r for r in synth.acme_earnings() if r["date"] < AS_OF.isoformat()]
    m = synth_run("ACME", earnings={"ACME": past})[0]
    st = m["strategies"]
    assert {"id": "earnings_unknown", "args": []} in st["note_ids"]
    assert "earnings date unknown: crossing not checked" in st["notes"]
    for name in C.STRATEGIES:
        assert all(r["earnings_before_expiry"] is None for r in rows(st, name))
    assert st["earnings_straddle"]["available"] is False
    assert st["earnings_straddle"]["reason"] == "no upcoming earnings date from FMP"


# ---------------------------------------------------------------------------- earnings straddle
def test_earnings_straddle_arithmetic(synth_run):
    m = synth_run("ACME")[0]
    es = m["strategies"]["earnings_straddle"]
    e2 = next(r for r in m["expected_move"]["rows"] if r["expiration"] == "2026-11-20")
    K, cost = e2["atm_strike"], e2["straddle"]
    assert (es["expiration"], es["strike"], es["cost"]) == ("2026-11-20", K, cost)
    assert es["cost_per_contract"] == pytest.approx(cost * C.MULTIPLIER, rel=REL)
    assert es["be_up"] == pytest.approx((K + cost) / S - 1, rel=REL)
    assert es["be_down"] == pytest.approx((K - cost) / S - 1, rel=REL)
    assert es["k_pre"] == CH.busdays(date(2026, 11, 6), synth.EARNINGS_NEXT) == 7
    assert es["k_post"] == CH.busdays(synth.EARNINGS_NEXT, date(2026, 11, 21)) == 4
    assert es["hold_days"] == 11
    sd = e2["atm_iv"] * math.sqrt(e2["T"])

    def d2(k):
        return (math.log(e2["forward"] / k) - 0.5 * sd * sd) / sd

    p = synth.n_cdf(d2(K + cost)) + synth.n_cdf(-d2(K - cost))
    assert es["p_beyond_be_rn"] == pytest.approx(p, rel=1e-3)
    assert es["n"] == m["earnings"]["reactions"]["n"]
    assert es["hist_share_beyond_be"] * es["n"] == pytest.approx(round(es["hist_share_beyond_be"] * es["n"]), abs=1e-4)
    assert es["event_variance_share"] < 0.5 and "straddle_non_event" in [n["id"] for n in es["note_ids"] if n]
