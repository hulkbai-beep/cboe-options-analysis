"""Calendar, session rules (plan 4.4), OCC parsing and the live-contract table (4.3)."""
from datetime import date, datetime, timedelta

import pytest

import chain as CH
import config as C
import synth


def payload(ltt="2026-11-05T16:00:00", ts="2026-11-05 21:30:00", options=None, **data):
    d = {"options": options or [], "current_price": 101.0, "close": 100.0, "price_change": -2.0,
         "prev_day_close": 102.0, "iv30": 25.0, "last_trade_time": ltt}
    d.update(data)
    return {"timestamp": ts, "symbol": "ACME", "data": d}


def opt(code, **kw):
    row = {"option": code, "bid": 1.0, "ask": 1.1, "iv": 0.3, "open_interest": 10, "volume": 1, "delta": 0.5}
    row.update(kw)
    return row


# ---------------------------------------------------------------------------- calendar
def test_trading_days_and_busdays():
    assert not CH.is_trading_day(date(2026, 11, 26))                 # Thanksgiving
    assert CH.next_trading_day(date(2026, 11, 25)) == date(2026, 11, 27)
    assert CH.prev_trading_day(date(2026, 11, 30)) == date(2026, 11, 27)
    assert CH.busdays(date(2026, 11, 23), date(2026, 11, 30)) == 4   # Mon-Fri minus Thursday
    assert CH.close_dt(date(2026, 11, 27)).hour == 13                 # early close


def test_stale_counts_sessions_open_for_15_minutes():
    fri = date(2026, 11, 6)
    assert CH.stale_info(fri, datetime(2026, 11, 9, 9, 44, tzinfo=CH.NY)) == {"is_stale": False, "business_days_behind": 0}
    assert CH.stale_info(fri, datetime(2026, 11, 9, 9, 45, tzinfo=CH.NY))["business_days_behind"] == 1
    assert CH.stale_info(fri, datetime(2026, 11, 7, 12, 0, tzinfo=CH.NY))["is_stale"] is False      # weekend
    assert CH.stale_info(date(2026, 11, 25), datetime(2026, 11, 27, 10, 0, tzinfo=CH.NY))["business_days_behind"] == 1


# ---------------------------------------------------------------------------- session
def test_closed_session_uses_the_close():
    s = CH.session_info(payload(), {"ACME"})
    assert s["closed"] and not s["overnight_quotes"]
    assert (s["as_of"], s["spot"], s["spot_basis"]) == (date(2026, 11, 5), 100.0, "close")
    assert s["prev_close"] == pytest.approx(102.0) and s["change_pct"] == pytest.approx(-2 / 102)
    assert s["data_dt"] == CH.close_dt(date(2026, 11, 5))
    assert s["iv30_cboe"] == pytest.approx(0.25)


def test_intraday_session_uses_the_current_price():
    s = CH.session_info(payload(ltt="2026-11-05T10:59:59", ts="2026-11-05 16:00:00"), {"ACME"})
    assert not s["closed"] and s["spot"] == 101.0 and s["spot_basis"] == "current_price"
    assert s["data_dt"] == s["feed_time"]


def test_last_second_trade_counts_as_closed():
    s = CH.session_info(payload(ltt="2026-11-05T15:59:59", ts="2026-11-05 21:00:30"), {"ACME"})
    assert s["closed"]


def test_early_close_day():
    s = CH.session_info(payload(ltt="2026-11-27T12:59:59", ts="2026-11-27 18:20:00"), {"ACME"})
    assert s["closed"] and s["as_of"] == date(2026, 11, 27)


def test_overnight_only_for_gth_roots():
    late = dict(ltt="2026-11-05T16:14:59", ts="2026-11-06 01:30:00")       # 20:30 New York
    assert CH.session_info(payload(**late), {"SPXW", "SPX"})["overnight_quotes"]
    assert not CH.session_info(payload(**late), {"NDXP"})["overnight_quotes"]
    early = dict(ltt="2026-11-05T16:14:59", ts="2026-11-06 01:00:00")      # 20:00 New York
    assert not CH.session_info(payload(**early), {"SPXW"})["overnight_quotes"]


def test_weekend_feed_is_closed():
    s = CH.session_info(payload(ltt="2026-11-06T16:00:00", ts="2026-11-07 15:00:00"), {"ACME"})
    assert s["closed"] and s["as_of"] == date(2026, 11, 6)


def test_rolled_previous_close():
    s = CH.session_info(payload(prev_day_close=100.0), {"ACME"})
    assert s["prev_close_rolled"]


# ---------------------------------------------------------------------------- contracts
def test_occ_parsing():
    f = CH.frame_from_options([opt("B1SPY261106C00740000"), opt("BRKB261120P00480000"),
                               opt("SPXW261106C07000000"), opt("SPX261120P06500000", iv=0.0)])
    assert list(f["root"]) == ["B1SPY", "BRKB", "SPXW", "SPX"]
    assert list(f["type"]) == ["C", "P", "C", "P"]
    assert list(f["strike"]) == [740.0, 480.0, 7000.0, 6500.0]
    assert list(f["expiration"]) == [date(2026, 11, 6), date(2026, 11, 20), date(2026, 11, 6), date(2026, 11, 20)]
    assert list(f["valid_iv"]) == [True, True, True, False]
    assert f["mid"].iloc[0] == pytest.approx(1.05)


def test_invalid_quotes():
    f = CH.frame_from_options([opt("ACME261120C00100000", bid=0.0), opt("ACME261120P00100000", bid=2.0, ask=1.0)])
    assert not f["valid_quote"].any() and f["mid"].isna().all()


def test_settlement_times():
    assert CH.settle_dt(date(2026, 11, 20), "SPX").time() == C.REGULAR_OPEN
    assert CH.settle_dt(date(2026, 11, 20), "SPXW").time() == C.REGULAR_CLOSE
    assert CH.settle_dt(date(2026, 11, 27), "SPY").hour == 13


def test_prepare_years_roots_and_exclusions():
    opts = [opt("SPXW261106C07000000"), opt("SPX261120C07000000"), opt("SPXW270319C07000000"),
            opt("SPXW261105C07000000"), opt("XYZW261106C07000000")]
    p = payload(options=opts, ltt="2026-11-05T16:14:59")
    s = CH.session_from_payload(p)
    x, info = CH.prepare("SPX", s, CH.frame_from_options(opts))
    by = {(r.root, r.expiration): r for r in x.itertuples()}
    assert (("SPXW", date(2026, 11, 5))) not in by                     # expired at today's close
    assert by[("SPXW", date(2026, 11, 6))].T == pytest.approx(1 / 365)
    am = by[("SPX", date(2026, 11, 20))]
    assert am.T == pytest.approx((15 * 86400 - 6.5 * 3600) / C.YEAR_SECONDS) and am.settlement == "AM"
    # 2027-03-19 is after the switch to daylight saving time: one hour less, measured in UTC
    assert by[("SPXW", date(2027, 3, 19))].T == pytest.approx(((date(2027, 3, 19) - date(2026, 11, 5)).days * 86400 - 3600)
                                                              / C.YEAR_SECONDS)
    assert info["excluded_roots"] == {"XYZW": 1}
    assert x["european"].all()


def test_synthetic_chain_round_trip():
    p = synth.xsp_payload()
    s = CH.session_from_payload(p)
    x, info = CH.prepare("XSP", s, CH.frame_from_options(p["data"]["options"]))
    assert s["closed"] and s["spot"] == 735.37 and info["contracts_live"] == len(x) > 0
    assert info["crossed_quotes"] == 0 and info["iv_outliers"] == 0
    e = sorted(set(x["expiration"]))[0]
    assert x.loc[x["expiration"] == e, "T"].iloc[0] == pytest.approx(synth.years_to(e, "XSP"))
    assert set(x["dte"]) == {(d - date(2026, 11, 5)).days for d in set(x["expiration"])}
    assert timedelta(0) < s["feed_time"] - CH.close_dt(s["as_of"]) < timedelta(hours=1)
