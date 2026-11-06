"""The history store (plan 3.5): chains and summaries on disk, IV Rank windows and open-interest changes."""
import gzip
import json
import math
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

import chain as CH
import config as C
import history as H
import synth
from conftest import make_ctx

AS_OF = synth.AS_OF


@pytest.fixture(scope="module")
def oex():
    p = synth.oex_payload()
    return p, CH.session_from_payload(p)


# ---------------------------------------------------------------------------- chains
def test_save_skip_force(tmp_path, oex):
    p, s = oex
    r = H.save_chain(tmp_path, "OEX", p, s)
    assert r["saved"] and r["contracts"] == len(p["data"]["options"])
    assert r["expirations"] == len({o["option"][3:9] for o in p["data"]["options"]})
    assert Path(r["path"]) == tmp_path / "chains" / "2026-11-05" / "OEX.parquet"
    again = H.save_chain(tmp_path, "OEX", p, s)
    assert not again["saved"] and again["reason"] == "2026-11-05 already saved"
    assert H.save_chain(tmp_path, "OEX", p, s, force=True, raw_bytes=json.dumps(p).encode())["saved"]
    raw = tmp_path / "raw" / "2026-11-05" / "OEX.json.gz"
    assert json.loads(gzip.decompress(raw.read_bytes())) == p
    assert H.chain_dates(tmp_path, "OEX") == [AS_OF] and H.stored_keys(tmp_path) == ["OEX"]
    assert not list(tmp_path.rglob("*.tmp"))                               # atomic writes leave nothing behind


def test_overnight_versions_are_never_stored(tmp_path, oex):
    p, s = oex
    r = H.save_chain(tmp_path, "OEX", p, dict(s, overnight_quotes=True))
    assert not r["saved"] and r["reason"] == "overnight (GTH) quotes are not stored"
    assert not (tmp_path / "chains").exists()


def test_chain_file_schema(tmp_path, oex):
    p, s = oex
    df = pd.read_parquet(H.save_chain(tmp_path, "OEX", p, s)["path"])
    assert list(df.columns[:len(C.CHAIN_COLUMNS)]) == list(C.CHAIN_COLUMNS)
    assert str(df["open_interest"].dtype) == "Int64" and str(df["snapshot_time"].dt.tz) == C.MARKET_TZ
    assert df["underlying_iv30"].iloc[0] == p["data"]["iv30"]              # percent, as in the prototype
    assert set(df["trade_date"]) == {AS_OF} and set(df["root"]) == {"OEX"}
    assert df["mid"].iloc[0] == pytest.approx((df["bid"].iloc[0] + df["ask"].iloc[0]) / 2)


def test_load_round_trip(tmp_path, oex):
    p, s = oex
    s2, f2 = H.load_chain(H.save_chain(tmp_path, "OEX", p, s)["path"])
    assert (s2["as_of"], s2["closed"], s2["spot"], s2["spot_basis"]) == (AS_OF, True, s["spot"], "close")
    assert s2["iv30_cboe"] == pytest.approx(s["iv30_cboe"]) and s2["feed_time"] == s["feed_time"]
    assert s2["data_dt"] == CH.close_dt(AS_OF) and not s2["overnight_quotes"]
    f1 = CH.frame_from_options(p["data"]["options"])
    cols = ["option", "root", "type", "strike", "expiration", "bid", "ask", "mid", "iv", "open_interest", "volume",
            "delta", "gamma", "valid_quote", "valid_iv"]
    pd.testing.assert_frame_equal(f1[cols].reset_index(drop=True), f2[cols].reset_index(drop=True), check_dtype=False)


# ---------------------------------------------------------------------------- summaries
def test_rebuilt_summary_equals_the_live_row(tmp_path, oex):
    p, s = oex
    live = H.summary_row(s, CH.frame_from_options(p["data"]["options"]), "index", key="OEX")
    s2, f2 = H.load_chain(H.save_chain(tmp_path, "OEX", p, s)["path"])
    rebuilt = H.summary_row(s2, f2, "index", key="OEX")
    assert list(live) == list(rebuilt) == list(C.SUMMARY_COLUMNS)
    for k in C.SUMMARY_COLUMNS:
        a, b = live[k], rebuilt[k]
        if isinstance(a, float):
            assert (math.isnan(a) and math.isnan(b)) or a == pytest.approx(b, rel=1e-9, abs=1e-9), k
        else:
            assert a == b, k
    assert live["closed"] and not live["late_feed"] and live["summary_version"] == C.SUMMARY_VERSION
    assert math.isnan(live["cm_iv_30d_ex_event"]) is False                 # an index: the 30-day IV itself


def test_summary_file_schema_and_append(tmp_path, oex):
    p, s = oex
    row = H.summary_row(s, CH.frame_from_options(p["data"]["options"]), "index", key="OEX")
    assert H.append_summary(tmp_path, "OEX", row) is True
    assert H.append_summary(tmp_path, "OEX", row) is False                 # one row per trading day
    assert H.append_summary(tmp_path, "OEX", dict(row, spot=1.0), force=True) is True
    df = H.read_summary(tmp_path, "OEX")
    assert len(df) == 1 and df["spot"].iloc[0] == 1.0 and df["trade_date"].iloc[0] == AS_OF
    types = {f.name: str(f.type) for f in pq.read_schema(H.summary_path(tmp_path, "OEX"))}
    assert list(types) == list(C.SUMMARY_COLUMNS)
    assert types["trade_date"] == "date32[day]" and types["feed_time"] == "string"
    assert all(types[c] == "int64" for c in H.SUMMARY_INT) and all(types[c] == "bool" for c in H.SUMMARY_BOOL)
    other = set(C.SUMMARY_COLUMNS) - set(H.SUMMARY_INT) - set(H.SUMMARY_BOOL) - {"trade_date", "feed_time"}
    assert {types[c] for c in other} == {"double"}


def test_a_damaged_summary_is_ignored(tmp_path):
    H.summary_path(tmp_path, "XYZ").parent.mkdir(parents=True)
    H.summary_path(tmp_path, "XYZ").write_bytes(b"not parquet")
    assert H.read_summary(tmp_path, "XYZ") is None and H.HistoryView(tmp_path, "XYZ", AS_OF).days == 0


# ---------------------------------------------------------------------------- IV Rank (5.5)
def hist(n, col="cm_iv_30d", vals=None, end=AS_OF):
    days = [end - timedelta(days=n - i) for i in range(n)]
    return pd.DataFrame({"trade_date": days, col: vals if vals is not None else [0.20 + 0.001 * i for i in range(n)]})


def test_rank_needs_sixty_values():
    b = H.rank_block(hist(58), "cm_iv_30d", AS_OF, 0.25)                  # 58 stored days + today = 59
    assert b["available"] is False and b["reason_id"] == "ivr_building" and (b["n_obs"], b["need"]) == (59, 60)
    assert b["reason"] == "history 59 days, need 60"
    assert H.rank_pctl(hist(58), "cm_iv_30d", AS_OF, 0.25) is None
    b = H.rank_block(hist(59), "cm_iv_30d", AS_OF, 0.25)
    assert b["available"] and b["n_obs"] == 60 and b["window_label"] == "recent"
    assert b["first_date"] == AS_OF - timedelta(days=59) and b["last_date"] == AS_OF


def test_rank_value_and_percentile():
    df = hist(59)                                                         # 0.200 ... 0.258
    top = H.rank_block(df, "cm_iv_30d", AS_OF, 0.30)
    assert top["value"] == 100.0 and top["pctl"] == pytest.approx(100 * 59 / 60)
    low = H.rank_block(df, "cm_iv_30d", AS_OF, 0.10)
    assert low["value"] == 0.0 and low["pctl"] == 0.0
    mid = H.rank_block(df, "cm_iv_30d", AS_OF, 0.229)
    assert mid["value"] == pytest.approx((0.229 - 0.200) / (0.258 - 0.200) * 100)
    assert mid["pctl"] == pytest.approx(100 * 29 / 60)                     # 0.200 ... 0.228 are strictly below
    flat = H.rank_block(hist(59, vals=[0.2] * 59), "cm_iv_30d", AS_OF, 0.2)
    assert flat["value"] is None and flat["pctl"] == 0.0


def test_rank_window_rules():
    df = hist(400)
    df.loc[10, "cm_iv_30d"] = float("nan")                               # missing values are skipped
    df = pd.concat([df, pd.DataFrame({"trade_date": [AS_OF], "cm_iv_30d": [9.9]})], ignore_index=True)
    b = H.rank_block(df, "cm_iv_30d", AS_OF, 0.5)
    assert b["n_obs"] == C.IVR_LOOKBACK and b["window_label"] == "1y"
    assert b["series"][-1] == [AS_OF, 0.5]                                # today's value, not the stored as_of row
    assert all(v < 1 for _, v in b["series"])
    assert b["first_date"] == df["trade_date"].iloc[399 - (C.IVR_LOOKBACK - 2)]   # the last 251 stored days
    assert H.rank_block(df, "cm_iv_30d", AS_OF, None)["reason"] == "no current value"
    assert H.rank_block(None, "cm_iv_30d", AS_OF, 0.2)["n_obs"] == 1


# ---------------------------------------------------------------------------- the view of one key
def summary_rows(n, end=AS_OF):
    rows = []
    for i in range(n):
        r = {c: float("nan") for c in C.SUMMARY_COLUMNS}
        r.update(trade_date=end - timedelta(days=n - i), cm_iv_30d=0.2 + 0.01 * i, total_oi=1, total_volume=1,
                 contracts_live=1, feed_time="2026-01-01T16:30:00-05:00", closed=True, late_feed=False,
                 summary_version=C.SUMMARY_VERSION)
        rows.append(r)
    return rows


def test_history_view(tmp_path):
    H.write_summary(tmp_path, "XYZ", summary_rows(3))
    v = H.HistoryView(tmp_path, "XYZ", AS_OF)
    assert v.days == 3 and v.first_last() == (AS_OF - timedelta(days=3), AS_OF - timedelta(days=1))
    assert v.previous("cm_iv_30d") == (AS_OF - timedelta(days=1), pytest.approx(0.22))
    assert v.previous("rr25_30d") is None                                 # no value stored
    assert v.iv_rank("cm_iv_30d", 0.3)["reason"] == "history 4 days, need 60" and v.pctl("cm_iv_30d", 0.3) is None


def test_history_block(tmp_path):
    b = H.history_block(None, "XYZ", None, None, None)
    assert b["available"] is False and b["reason"] == "history store not used"
    H.write_summary(tmp_path, "XYZ", summary_rows(2))
    v = H.HistoryView(tmp_path, "XYZ", AS_OF)
    b = H.history_block(tmp_path, "XYZ", v, {"saved": False, "reason": "2026-11-05 already saved"}, None)
    assert (b["days"], b["saved_this_run"]) == (2, False)
    assert b["notes"] == ["chain not saved: 2026-11-05 already saved"]


# ---------------------------------------------------------------------------- open-interest change (5.7)
def test_oi_change_against_the_previous_chain(tmp_path):
    p = synth.xsp_payload()
    ctx = make_ctx(p, "XSP", "index")
    opts = p["data"]["options"]
    a = next(o for o in opts if o["option"] == "XSP261120C00740000")      # base 500 higher: OI fell by 500
    b = next(o for o in opts if o["option"] == "XSP261120P00720000")      # base 300 lower: OI rose by 300
    base = json.loads(json.dumps(p))
    for o in base["data"]["options"]:
        if o["option"] == a["option"]:
            o["open_interest"] += 500
        if o["option"] == b["option"]:
            o["open_interest"] -= 300
    view = H.HistoryView(tmp_path, "XSP", AS_OF)
    assert view.oi_change(ctx)["reason"] == "no stored chain before 2026-11-05"
    s = CH.session_from_payload(base)
    H.save_chain(tmp_path, "XSP", base, dict(s, as_of=date(2026, 11, 4)))
    H.save_chain(tmp_path, "XSP", p, CH.session_from_payload(p))          # today's own chain is not the base
    oc = view.oi_change(ctx)
    assert oc["base_date"] == date(2026, 11, 4) and oc["net_change"] == -200
    x = ctx["x"]
    oi720 = int(x.loc[(x["type"] == "P") & (x["strike"] == 720.0), "open_interest"].sum())   # all expiries
    assert oc["top_increases"] == [{"type": "P", "strike": 720.0, "oi_change": 300, "oi": oi720, "base_oi": oi720 - 300}]
    assert oc["top_decreases"][0]["strike"] == 740.0 and oc["top_decreases"][0]["oi_change"] == -500
    assert not [n for n in oc["note_ids"] if n]                           # a one-day gap: no gap note
    H.save_chain(tmp_path, "XSP", base, dict(s, as_of=date(2026, 10, 30)))
    (tmp_path / "chains" / "2026-11-04" / "XSP.parquet").unlink()
    gap = view.oi_change(ctx)
    assert {"id": "oi_base_gap", "args": ["2026-10-30", 6]} in gap["note_ids"]


# ---------------------------------------------------------------------------- tickers.txt and the folder
def test_tickers_file_and_data_dir(tmp_path, monkeypatch):
    p = H.ensure_tickers_file(tmp_path)
    assert H.read_tickers(p) == list(C.SNAPSHOT_TICKERS)
    p.write_text("SPY, QQQ  # broad market\n# a comment line\nNVDA AAPL\n", encoding="utf-8")
    assert H.ensure_tickers_file(tmp_path) == p and H.read_tickers(p) == ["SPY", "QQQ", "NVDA", "AAPL"]
    monkeypatch.setenv(C.DATA_DIR_ENV, str(tmp_path / "env"))
    assert H.data_dir() == tmp_path / "env" and H.data_dir(tmp_path / "arg") == tmp_path / "arg"
    monkeypatch.delenv(C.DATA_DIR_ENV)
    assert H.data_dir() == Path.home() / C.DATA_DIR_DEFAULT
