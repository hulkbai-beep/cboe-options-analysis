"""The per-expiry surface (plan 5.1) on synthetic chains whose forwards and volatilities are known."""
import math
from datetime import date

import pytest

import config as C
import quality as Q
import surface as SF
import synth
from conftest import make_ctx


@pytest.fixture(scope="module")
def xsp():
    return make_ctx(synth.xsp_payload(), "XSP", "index")


@pytest.fixture(scope="module")
def american():
    surf = synth.Surface(0.30, 0.25, skew=-0.10)
    exps = synth.standard_expiries()
    p = synth.chain_payload("ACME", 100.0, roots_by_expiry={e: ("ACME",) for e in exps}, surface=surf,
                            security_type="stock", div_yield=0.01, step=1.0)
    return make_ctx(p, "ACME", "stock"), surf


def test_forward_recovered_european(xsp):
    for r in SF.usable(xsp["sf"]).itertuples():
        f = 735.37 * math.exp(synth.RATE * r.T)
        assert r.forward == pytest.approx(f, rel=1e-3)
        assert r.european and r.settlement == "PM"


def test_forward_exact_american(american):
    ctx, _ = american
    for r in SF.usable(ctx["sf"]).itertuples():
        assert r.forward == pytest.approx(100.0 * math.exp((synth.RATE - 0.01) * r.T), rel=1e-6)
        assert not r.european


def test_straddle_iv_recovers_sigma(american, xsp):
    ctx, surf = american
    for r in SF.usable(ctx["sf"]).itertuples():                    # American: undiscounted, exact forward
        assert r.atm_iv_source == "straddle"
        assert r.atm_iv == pytest.approx(surf.vol(r.atm_strike, r.forward, r.T, r.expiration), abs=2e-4)
        assert r.move_1sd_pct == pytest.approx(r.atm_iv * math.sqrt(r.T))
    xs = synth.Surface(0.20, 0.17)
    for r in SF.usable(xsp["sf"]).itertuples():                    # European: discounted at DISCOUNT_RATE
        assert r.atm_iv == pytest.approx(xs.vol(r.atm_strike, r.forward, r.T, r.expiration), abs=2e-3)


def test_wings_and_risk_reversal(american):
    ctx, surf = american
    r = SF.usable(ctx["sf"]).iloc[6]
    assert r["put25_iv"] > r["atm_iv_cboe"] > r["call25_iv"]           # negative skew parameter
    assert r["rr25"] == pytest.approx(r["put25_iv"] - r["call25_iv"])
    assert r["bf25"] == pytest.approx((r["put25_iv"] + r["call25_iv"]) / 2 - r["atm_iv_cboe"])


def test_one_sided_expiry_is_dropped():
    p = synth.xsp_payload()
    bad = date(2026, 12, 4)
    opts = p["data"]["options"]
    p["data"]["options"] = [o for o in opts if not (o["option"].startswith(f"XSP{bad:%y%m%d}")
                                                    and int(o["option"][-8:]) / 1000 < 760)]
    ctx = make_ctx(p, "XSP", "index")
    assert bad in SF.dropped(ctx["sf"])
    row = ctx["sf"][ctx["sf"]["expiration"] == bad].iloc[0]
    assert not row["bracket_ok"] and row["drop_reason"]
    assert bad not in set(SF.usable(ctx["sf"])["expiration"])


def test_constant_maturity_interpolation(xsp):
    use = SF.usable(xsp["sf"])
    v30, ex30 = SF.interp_in_t(use, "atm_iv", 30, C.CM_MIN_DTE, True)
    lo = use[use["dte"] <= 30].iloc[-1]
    hi = use[use["dte"] >= 30].iloc[0]
    assert not ex30 and min(lo["atm_iv"], hi["atm_iv"]) - 1e-9 <= v30 <= max(lo["atm_iv"], hi["atm_iv"]) + 1e-9
    w = lambda r: r["atm_iv"] ** 2 * r["T"]                              # noqa: E731
    t30 = 30 / 365
    if lo["dte"] != hi["dte"]:                                           # linear in total variance
        frac = (t30 - lo["T"]) / (hi["T"] - lo["T"])
        assert v30 == pytest.approx(math.sqrt((w(lo) + frac * (w(hi) - w(lo))) / t30), rel=0.02)
    v_long, ex_long = SF.interp_in_t(use, "atm_iv", 5000, C.CM_MIN_DTE, True)
    assert ex_long                                                       # beyond the last expiry: flagged


def test_monthly_rule_with_a_holiday_friday():
    exps = synth.standard_expiries()
    assert date(2027, 6, 17) in exps                                     # 2027-06-18 is a holiday
    assert SF.is_monthly(date(2027, 6, 17), exps)
    assert SF.is_monthly(date(2026, 11, 20), exps)
    assert not SF.is_monthly(date(2026, 11, 13), exps)


def test_iv_self_check_skips_the_expiry_settling_today():
    import pandas as pd
    sf = pd.DataFrame({"expiration": [date(2026, 11, 6), date(2026, 11, 13), date(2026, 12, 18)], "dte": [0, 7, 42],
                       "atm_iv": [0.50, 0.21, 0.30], "atm_iv_cboe": [0.30, 0.20, 0.10]})
    chk = Q.iv_price_check(sf)
    assert chk["flagged"] == [date(2026, 11, 13)] and chk["max_abs_dev"] == pytest.approx(0.05)
    assert chk["tolerance"] == C.IV_PRICE_CHECK_TOL
