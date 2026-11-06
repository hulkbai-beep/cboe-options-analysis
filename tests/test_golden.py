"""run_analysis / run_market on the real 2026-11-05 fixtures against golden_values.json (plan appendix B)."""
from __future__ import annotations

import json
import math

import pytest

import analyze
from conftest import NOW
from fakes import FakeCboe, FakeFMP

TICKERS = ("SPY", "SPX", "NVDA", "AAPL", "QQQ", "TSLA", "IWM")


@pytest.fixture(scope="module")
def golden(real_data):
    return json.loads((real_data / "golden_values.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def runs(real_data, tmp_path_factory):
    out = {}
    for t in TICKERS:
        d = tmp_path_factory.mktemp(f"hist_{t}")
        out[t], _ = analyze.run_analysis(t, client=FakeCboe(real_data), fmp=FakeFMP(real_data), now=NOW, data_dir=d)
    d = tmp_path_factory.mktemp("hist_overnight")
    out["SPX_overnight"], _ = analyze.run_analysis("SPX", client=FakeCboe(real_data, date="2026-11-05-overnight"),
                                                   fmp=FakeFMP(real_data), now=NOW, data_dir=d)
    d = tmp_path_factory.mktemp("hist_market")
    out["market"], _ = analyze.run_market(client=FakeCboe(real_data), fmp=FakeFMP(real_data), now=NOW, data_dir=d)
    return out


# ---------------------------------------------------------------------------- comparison helpers
class Mismatch(list):
    def check(self, path, ok, got, want):
        if not ok:
            self.append(f"{path}: got {got!r}, want {want!r}")


def close_abs(got, want, tol):
    if want is None:
        return got is None
    return got is not None and abs(got - want) <= tol


def close_rel(got, want, tol):
    if want is None:
        return got is None
    return got is not None and abs(got - want) <= tol * max(abs(want), 1e-12)


def cmp_value(mm, path, got, want, tol=None, rel=None):
    if isinstance(want, bool) or isinstance(want, str) or want is None and tol is None and rel is None:
        mm.check(path, got == want, got, want)
    elif isinstance(want, (int, float)) and not isinstance(want, bool):
        if rel is not None:
            mm.check(path, close_rel(got, want, rel), got, want)
        elif tol is not None:
            mm.check(path, close_abs(got, want, tol), got, want)
        else:
            mm.check(path, got == want, got, want)
    elif want is None:
        mm.check(path, got is None, got, want)
    else:
        mm.check(path, got == want, got, want)


def cmp_keys(mm, prefix, got: dict, want: dict, tol=None, rel=None, exact=(), skip=()):
    for k, w in want.items():
        if k in skip:
            continue
        g = got.get(k) if isinstance(got, dict) else None
        if k in exact:
            mm.check(f"{prefix}.{k}", g == w, g, w)
        else:
            cmp_value(mm, f"{prefix}.{k}", g, w, tol, rel)


def compare_ticker(t: str, g: dict, m: dict) -> list:
    mm = Mismatch()
    snap, dq = m["snapshot"], m["data_quality"]
    mm.check("ticker", m["ticker"] == g["key"], m["ticker"], g["key"])
    mm.check("as_of", m["as_of"] == g["as_of"], m["as_of"], g["as_of"])
    for k in ("kind", "kind_source", "cboe_symbol", "fmp_symbol", "spot_basis"):
        mm.check(f"snapshot.{k}", snap[k] == g[k], snap[k], g[k])
    for k in ("spot", "prev_close", "change_pct"):
        cmp_value(mm, f"snapshot.{k}", snap[k], g[k], rel=1e-5)
    for k in ("closed", "overnight_quotes", "feed_time"):
        mm.check(f"session.{k}", snap["session"][k] == g[k], snap["session"][k], g[k])
    mm.check("prev_close_rolled", dq["prev_close_rolled"] == g["prev_close_rolled"], dq["prev_close_rolled"],
             g["prev_close_rolled"])
    cmp_value(mm, "iv30_cboe", snap["iv30_cboe"], g["iv30_cboe"], tol=1e-6)
    if snap["iv30_change_source"] == "cboe" or g["iv30_change_cboe"] is None:
        cmp_value(mm, "iv30_change", snap["iv30_change"] if snap["iv30_change_source"] == "cboe" else None,
                  g["iv30_change_cboe"], tol=1e-6)
    ch = snap["chain"]
    for gk, mk in (("live_contracts", "contracts_live"), ("excluded_roots", "excluded_roots"),
                   ("contracts_total", "contracts_total"), ("expirations", "expirations")):
        mm.check(f"chain.{mk}", ch[mk] == g[gk], ch[mk], g[gk])
    for k in ("crossed_quotes", "iv_outliers"):
        mm.check(f"data_quality.{k}", dq[k] == g[k], dq[k], g[k])
    for k in ("valid_quote_share", "valid_iv_share"):
        cmp_value(mm, f"chain.{k}", ch[k], g[k], tol=1e-5)
    # expected move
    rows = {r["expiration"]: r for r in m["expected_move"]["rows"]}
    mm.check("expected_move.expirations", sorted(rows) == sorted(r["expiration"] for r in g["expected_move"]),
             sorted(rows), [r["expiration"] for r in g["expected_move"]])
    for w in g["expected_move"]:
        r = rows.get(w["expiration"], {})
        p = f"expected_move[{w['expiration']}]"
        cmp_keys(mm, p, r, w, exact=("tags", "root", "settlement", "dte", "expiration"),
                 skip=("forward", "straddle", "atm_strike", "atm_iv", "atm_iv_cboe", "straddle_pct", "move_1sd_pct"))
        cmp_keys(mm, p, r, {k: w[k] for k in ("forward", "straddle", "atm_strike")}, rel=1e-5)
        cmp_keys(mm, p, r, {k: w[k] for k in ("atm_iv", "atm_iv_cboe", "straddle_pct", "move_1sd_pct")}, tol=5e-4)
    cmp_keys(mm, "em_cm", m["expected_move"]["em_cm"], g["em_cm"], tol=5e-4)
    # term structure
    ts, gts = m["term_structure"], g["term_structure"]
    cmp_keys(mm, "cm_iv", ts.get("cm_iv") or {}, gts["cm_iv"], tol=5e-4)
    mm.check("cm_extrapolated", ts.get("cm_extrapolated") == gts["cm_extrapolated"], ts.get("cm_extrapolated"),
             gts["cm_extrapolated"])
    for k in ("ratio_30_90", "ratio_7_30"):
        cmp_value(mm, f"term.{k}", ts.get(k), gts[k], tol=2e-3)
    mm.check("term.shape", ts.get("shape") == gts["shape"], ts.get("shape"), gts["shape"])
    cmp_value(mm, "term.median_fwd_vol", ts.get("median_fwd_vol"), gts["median_fwd_vol"], tol=5e-4)
    gk, mk = gts["kinks"], ts.get("kinks") or []
    mm.check("term.kinks.n", len(gk) == len(mk), len(mk), len(gk))
    for i, (a, b) in enumerate(zip(mk, gk)):
        cmp_keys(mm, f"kinks[{i}]", a, b, tol=5e-4, exact=("from", "to", "negative_variance", "contains_earnings"))
    mm.check("dropped_expiries", ts.get("dropped_expiries") == gts["dropped_expiries"], ts.get("dropped_expiries"),
             gts["dropped_expiries"])
    mm.check("negative_forward_variance", dq["negative_forward_variance"] == gts["negative_forward_variance"],
             dq["negative_forward_variance"], gts["negative_forward_variance"])
    # skew
    sk = m["skew"]
    cmp_keys(mm, "skew", sk, g["skew"], tol=5e-4, exact=("label",))
    # iv price check
    cmp_value(mm, "iv_price_check.max_abs_dev", dq["iv_price_check"]["max_abs_dev"],
              g["iv_price_check"]["max_abs_dev"], tol=1e-3)
    mm.check("iv_price_check.flagged", dq["iv_price_check"]["flagged"] == g["iv_price_check"]["flagged"],
             dq["iv_price_check"]["flagged"], g["iv_price_check"]["flagged"])
    # positioning
    pos, gp = m["positioning"], g["positioning"]
    cmp_keys(mm, "pcr.all", pos["pcr"]["all"], gp["pcr_all"], tol=1e-4)
    cmp_keys(mm, "pcr.d30", pos["pcr"]["d30"], gp["pcr_30d"], tol=1e-4)
    for gk_, mk_ in (("call_levels", "calls"), ("put_levels", "puts")):
        mm.check(f"key_levels.{mk_}", pos["key_levels"][mk_] == gp[gk_], pos["key_levels"][mk_], gp[gk_])
    mm.check("far_otm", pos["far_otm"] == gp["far_otm"], pos["far_otm"], gp["far_otm"])
    for gk_, mk_ in (("max_pain_next", "next"), ("max_pain_monthly", "monthly")):
        mm.check(f"max_pain.{mk_}", pos["max_pain"][mk_] == gp[gk_], pos["max_pain"][mk_], gp[gk_])
    # gamma
    gm, gg = m["gamma"], g["gamma"]
    for k in ("net_gex_1pct", "profile_at_spot"):
        cmp_value(mm, f"gamma.{k}", gm.get(k), gg[k], rel=5e-3)
    for k in ("regime", "call_wall", "put_wall", "applicability", "contracts"):
        mm.check(f"gamma.{k}", gm.get(k) == gg[k], gm.get(k), gg[k])
    cmp_value(mm, "gamma.zero_gamma", gm.get("zero_gamma"), gg["zero_gamma"], tol=1e-3 * snap["spot"])
    cmp_value(mm, "gamma.zero_gamma_dist_pct", gm.get("zero_gamma_dist_pct"), gg["zero_gamma_dist_pct"], tol=1e-3)
    gc = dq["gamma_check"]
    cmp_value(mm, "gamma_check.corr", gc["corr"], gg["gamma_check"]["corr"], tol=1e-3)
    cmp_value(mm, "gamma_check.median_ratio", gc["median_ratio"], gg["gamma_check"]["median_ratio"], tol=1e-3)
    mm.check("gamma_check.ok", gc["ok"] == gg["gamma_check"]["ok"], gc["ok"], gg["gamma_check"]["ok"])
    # flow
    fr = m["flow"]["rows"]
    mm.check("flow.n", len(fr) == len(g["unusual"]), len(fr), len(g["unusual"]))
    for i, (a, b) in enumerate(zip(fr, g["unusual"])):
        cmp_keys(mm, f"flow[{i}]", a, b, exact=("option", "volume", "open_interest"), skip=("premium",))
        cmp_value(mm, f"flow[{i}].premium", a["premium"], b["premium"], rel=1e-5)
    # volatility
    vol = m["volatility"]
    if g.get("realized"):
        rv = vol["realized"]
        cmp_keys(mm, "realized", rv, {k: g["realized"][k] for k in ("hv10", "hv20", "hv60")}, tol=1e-4)
        cmp_value(mm, "realized.hv20_pctl_1y", rv.get("hv20_pctl_1y"), g["realized"]["hv20_pctl_1y"], tol=0.5)
        for k in ("vrp", "iv_hv_ratio"):
            cmp_value(mm, f"volatility.{k}", vol.get(k), g["realized"][k], tol=5e-4)
    else:
        mm.check("realized.available", vol["realized"]["available"] is False, vol["realized"]["available"], False)
    if g.get("iv_index"):
        ix = vol["iv_index"]
        for k in ("symbol", "date", "close"):
            mm.check(f"iv_index.{k}", ix.get(k) == g["iv_index"][k], ix.get(k), g["iv_index"][k])
        cmp_value(mm, "iv_index.pctl_1y", ix.get("pctl_1y"), g["iv_index"]["pctl_1y"], tol=0.5)
    else:
        mm.check("iv_index.available", vol["iv_index"]["available"] is False, vol["iv_index"]["available"], False)
    # earnings
    ea = m["earnings"]
    if g.get("earnings"):
        ge = g["earnings"]
        for k in ("next_date", "event_today", "trading_days_to_event"):
            mm.check(f"earnings.{k}", ea.get(k) == ge[k], ea.get(k), ge[k])
        mm.check("earnings.date_check", ea.get("date_check") == ge.get("date_check"), ea.get("date_check"),
                 ge.get("date_check"))
        ev = ea.get("event_move") or {}
        for k in ("available", "event_date", "method", "e1", "e2", "e3"):
            if k in ge:
                mm.check(f"event_move.{k}", ev.get(k) == ge[k], ev.get(k), ge[k])
        for k in ("sigma1", "sigma2", "sigma_base", "T2", "move_1sd", "expected_abs_move", "implied_window_1sd",
                  "implied_window_abs", "event_variance_share", "straddle_e2_pct"):
            if k in ge:
                cmp_value(mm, f"event_move.{k}", ev.get(k), ge[k], tol=5e-4)
        if "reactions" in ge:
            re_, gr = ea.get("reactions") or {}, ge["reactions"]
            for k in ("n", "small_sample"):
                mm.check(f"reactions.{k}", re_.get(k) == gr[k], re_.get(k), gr[k])
            for k in ("mean_abs", "median_abs", "max_abs"):
                cmp_value(mm, f"reactions.{k}", re_.get(k), gr[k], tol=1e-4)
            mm.check("reactions.dates", [i["date"] for i in re_.get("items", [])] == [i["date"] for i in gr["items"]],
                     [i["date"] for i in re_.get("items", [])], [i["date"] for i in gr["items"]])
            for i, (a, b) in enumerate(zip(re_.get("items", []), gr["items"])):
                cmp_value(mm, f"reactions.items[{i}].move", a["move"], b["move"], tol=1e-4)
        for k in ("ratio_implied_to_mean", "share_above_implied_window_1sd"):
            if k in ge:
                cmp_value(mm, f"comparison.{k}", (ea.get("comparison") or {}).get(k), ge[k], tol=2e-3)
    else:
        mm.check("earnings.available", ea["available"] is False, ea["available"], False)
    # strategies
    st, gs = m["strategies"], g["strategies"]
    for name, want in gs.items():
        got = st.get(name) or {}
        if name == "earnings_straddle":
            for k, w in want.items():
                if isinstance(w, str) or w is None or isinstance(w, bool):
                    mm.check(f"earnings_straddle.{k}", got.get(k) == w, got.get(k), w)
                elif k in ("cost", "cost_per_contract", "strike"):
                    cmp_value(mm, f"earnings_straddle.{k}", got.get(k), w, rel=1e-5)
                elif isinstance(w, int):
                    mm.check(f"earnings_straddle.{k}", got.get(k) == w, got.get(k), w)
                else:
                    cmp_value(mm, f"earnings_straddle.{k}", got.get(k), w, tol=1e-4)
            continue
        mm.check(f"{name}.available", got.get("available") == want["available"], got.get("available"),
                 want["available"])
        if not want["available"]:
            continue
        mm.check(f"{name}.candidates", got.get("candidates") == want["candidates"], got.get("candidates"),
                 want["candidates"])
        grows = {r["option"]: r for r in got.get("rows", [])}
        mm.check(f"{name}.options", list(grows) == [r["option"] for r in want["rows"]], list(grows),
                 [r["option"] for r in want["rows"]])
        for w in want["rows"]:
            r = grows.get(w["option"], {})
            for k, v in w.items():
                p = f"{name}[{w['option']}].{k}"
                if isinstance(v, (str, bool)) or v is None:
                    mm.check(p, r.get(k) == v, r.get(k), v)
                elif k in ("premium_per_contract", "capital", "max_loss", "breakeven", "breakeven_ask", "strike",
                           "bid", "ask", "mid"):
                    cmp_value(mm, p, r.get(k), v, rel=1e-5)
                elif isinstance(v, int):
                    mm.check(p, r.get(k) == v, r.get(k), v)
                else:
                    cmp_value(mm, p, r.get(k), v, tol=1e-4)
    if "earnings_straddle" not in gs:
        mm.check("earnings_straddle.available", (st.get("earnings_straddle") or {}).get("available") is False,
                 (st.get("earnings_straddle") or {}).get("available"), False)
    return mm


@pytest.mark.parametrize("t", TICKERS + ("SPX_overnight",))
def test_ticker_golden(t, golden, runs):
    mm = compare_ticker(t, golden[t], runs[t])
    assert not mm, f"{t}: {len(mm)} mismatches:\n" + "\n".join(mm[:60])


def test_market_golden(golden, runs):
    m, g = runs["market"], golden["market"]
    mm = Mismatch()
    v, gv = m["vix"], g["vix"]
    for k in ("level", "band"):
        mm.check(f"vix.{k}", v[k] == gv[k], v[k], gv[k])
    cmp_value(mm, "vix.prev_close", v["prev_close"], gv["prev_close"], rel=1e-5)
    for k in ("change_pct", "vix_vix3m", "vix9d_vix"):
        cmp_value(mm, f"vix.{k}", v[k], gv[k], tol=1e-4)
    for k in ("pctl_1y", "vix_vix3m_pctl_1y", "vix9d_vix_pctl_1y"):
        cmp_value(mm, f"vix.{k}", v[k], gv[k], tol=0.5)
    th, gt = m["thermometer"], g["thermometer"]
    for k, fired in gt["flags"].items():
        mm.check(f"flags.{k}", th["flags"][k]["fired"] == fired, th["flags"][k]["fired"], fired)
    for k in ("n_flags", "n_available", "label", "label_uncertain"):
        mm.check(f"thermometer.{k}", th[k] == gt[k], th[k], gt[k])
    # boards: the same computation as the ticker mode
    for row in m["skew_board"]["rows"]:
        gk = golden[row["key"]]["skew"]
        cmp_keys(mm, f"skew_board[{row['key']}]", row, gk, tol=5e-4, exact=("label",))
    for row in m["gamma_board"]["rows"]:
        gk = golden[row["key"]]["gamma"]
        cmp_value(mm, f"gamma_board[{row['key']}].net_gex_1pct", row["net_gex_1pct"], gk["net_gex_1pct"], rel=5e-3)
        mm.check(f"gamma_board[{row['key']}].regime", row["regime"] == gk["regime"], row["regime"], gk["regime"])
        spot = golden[row["key"]]["spot"]
        cmp_value(mm, f"gamma_board[{row['key']}].zero_gamma", row["zero_gamma"], gk["zero_gamma"], tol=1e-3 * spot)
        cmp_value(mm, f"gamma_board[{row['key']}].zero_gamma_dist_pct", row["zero_gamma_dist_pct"],
                  gk["zero_gamma_dist_pct"], tol=1e-3)
    for row in m["positioning"]["rows"]:
        gk = golden[row["key"]]["positioning"]["pcr_all"]
        cmp_value(mm, f"pcr[{row['key']}].volume", row["pcr_all_volume"], gk["volume"], tol=1e-4)
        cmp_value(mm, f"pcr[{row['key']}].oi", row["pcr_all_oi"], gk["oi"], tol=1e-4)
    want_em = [(k, r) for k in ("SPX", "SPY") for r in golden[k]["expected_move"]
               if {"next_week", "monthly"} & set(r["tags"])]
    got_em = {(r["key"], r["expiration"]): r for r in m["expected_moves"]["rows"]}
    mm.check("expected_moves.n", len(got_em) == len(want_em), len(got_em), len(want_em))
    for k, w in want_em:
        r = got_em.get((k, w["expiration"]), {})
        mm.check(f"em[{k},{w['expiration']}].tags", r.get("tags") == w["tags"], r.get("tags"), w["tags"])
        for f in ("straddle_pct", "move_1sd_pct"):
            cmp_value(mm, f"em[{k},{w['expiration']}].{f}", r.get(f), w[f], tol=5e-4)
    assert not mm, f"market: {len(mm)} mismatches:\n" + "\n".join(mm[:60])


def test_no_errors_on_real_data(runs):
    bad = {k: m["meta"]["errors"] for k, m in runs.items() if m["meta"]["errors"]}
    assert not bad, bad
