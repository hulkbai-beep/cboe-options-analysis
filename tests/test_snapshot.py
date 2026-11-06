"""snapshot.py (plan 3.5) on the synthetic data: what is saved, what is skipped, --rebuild-summary and exit codes."""
import math

import pandas as pd
import pytest

import config as C
import fetch
import history as H
import snapshot
from conftest import fakes_for

DAY = "2026-11-05"


def run(synth_data, root, *args, transform=None, fmp=None, **fmp_kwargs):
    cboe, fake_fmp = fakes_for(synth_data, **fmp_kwargs)
    cboe.transform = transform
    code = snapshot.main([*args, "--data-dir", str(root), "--sleep", "0"], cboe=cboe,
                         fmp=fake_fmp if fmp is None else fmp)
    return code, cboe


def log_text(root):
    return (root / "logs" / "snapshot.log").read_text(encoding="utf-8")


def test_saves_chains_and_summaries(synth_data, tmp_path, no_network):
    code, cboe = run(synth_data, tmp_path, "SPY", "acme", "SPX", "VIX", "--raw")
    assert code == 0
    assert sorted(p.stem for p in (tmp_path / "chains" / DAY).glob("*.parquet")) == ["ACME", "SPX", "SPY", "VIX"]
    assert sorted(p.stem for p in (tmp_path / "summary").glob("*.parquet")) == ["ACME", "SPX", "SPY"]   # VIX: chain only
    assert (tmp_path / "raw" / DAY / "SPX.json.gz").is_file()
    assert cboe.requests == ["SPY", "ACME", "_SPX", "_VIX"]
    acme = H.read_summary(tmp_path, "ACME").iloc[0]
    assert acme["cm_iv_30d_ex_event"] < acme["cm_iv_30d"]                   # earnings known: the event is removed
    spy = H.read_summary(tmp_path, "SPY").iloc[0]
    assert spy["cm_iv_30d_ex_event"] == spy["cm_iv_30d"]                    # an ETF has no event
    text = log_text(tmp_path)
    assert "VIX: 2026-11-05 saved" in text and "chain only (futures-settled), no summary" in text


def test_a_rerun_skips_what_is_saved_and_force_rewrites(synth_data, tmp_path):
    run(synth_data, tmp_path, "SPY", "VIX")
    path = H.chain_path(tmp_path, "SPY", pd.Timestamp(DAY).date())
    before = path.stat().st_mtime_ns
    code, _ = run(synth_data, tmp_path, "SPY", "VIX")
    assert code == 0 and path.stat().st_mtime_ns == before
    assert log_text(tmp_path).count("already saved, skipped") == 2
    code, _ = run(synth_data, tmp_path, "SPY", "--force")
    assert code == 0 and path.stat().st_mtime_ns != before
    assert len(H.read_summary(tmp_path, "SPY")) == 1


def test_overnight_quotes_are_never_saved(synth_data, tmp_path):
    def overnight(p):
        p["timestamp"] = "2026-11-06 01:30:00"                             # 20:30 New York: GTH session
        return p

    code, _ = run(synth_data, tmp_path, "SPX", "--force", transform=overnight)
    assert code == 0 and not (tmp_path / "chains").exists()
    assert "overnight (GTH) quotes" in log_text(tmp_path)


def test_intraday_needs_force(synth_data, tmp_path):
    def intraday(p):
        p["timestamp"], p["data"]["last_trade_time"] = "2026-11-05 16:00:00", "2026-11-05T10:59:59"
        return p

    code, _ = run(synth_data, tmp_path, "SPY", transform=intraday)
    assert code == 0 and not (tmp_path / "chains").exists() and "market still open" in log_text(tmp_path)
    code, _ = run(synth_data, tmp_path, "SPY", "--force", transform=intraday)
    assert code == 0 and H.read_summary(tmp_path, "SPY")["closed"].tolist() == [False]
    assert "intraday data (--force)" in log_text(tmp_path)


def test_without_fmp(synth_data, tmp_path):
    code, _ = run(synth_data, tmp_path, "ACME", fmp=fetch.FmpState(None))
    assert code == 0
    row = H.read_summary(tmp_path, "ACME").iloc[0]
    assert math.isnan(row["cm_iv_30d_ex_event"]) and not math.isnan(row["cm_iv_30d"])
    assert "FMP not configured, ex-event IV not stored" in log_text(tmp_path)


def test_rebuild_summary_matches_the_saved_rows(synth_data, tmp_path):
    run(synth_data, tmp_path, "SPY", "ACME", "VIX")
    before = {k: H.read_summary(tmp_path, k) for k in ("SPY", "ACME")}
    H.summary_path(tmp_path, "SPY").unlink()
    code, _ = run(synth_data, tmp_path, "--rebuild-summary")
    assert code == 0
    for k, old in before.items():
        new = H.read_summary(tmp_path, k)
        assert list(new.columns) == list(C.SUMMARY_COLUMNS) and len(new) == 1
        for c in C.SUMMARY_COLUMNS:
            a, b = old[c].iloc[0], new[c].iloc[0]
            if isinstance(a, float):
                assert (math.isnan(a) and math.isnan(b)) or a == pytest.approx(b, rel=1e-9, abs=1e-9), (k, c)
            else:
                assert a == b, (k, c)
    assert "VIX: chain only (futures-settled), no summary" in log_text(tmp_path)


def test_exit_codes(synth_data, tmp_path):
    code, _ = run(synth_data, tmp_path, "SPY", "ZZZZQ")
    assert code == 1 and H.chain_path(tmp_path, "SPY", pd.Timestamp(DAY).date()).exists()
    assert "ZZZZQ: no option chain on CBOE (check the symbol)" in log_text(tmp_path)
    code, _ = run(synth_data, tmp_path, "SHOP.TO")
    assert code == 1
    code, _ = run(synth_data, tmp_path, "--rebuild-summary", "QQQ")                # nothing stored for QQQ
    assert code == 1
    with pytest.raises(SystemExit) as e:
        snapshot.main(["SPY", "--sleep", "-1", "--data-dir", str(tmp_path)], cboe=object(), fmp=fetch.FmpState(None))
    assert e.value.code == 2


def test_tickers_file(synth_data, tmp_path):
    f = tmp_path / "list.txt"
    f.write_text("SPY, acme  # two symbols\n# a comment\nSPY\n", encoding="utf-8")
    code, cboe = run(synth_data, tmp_path / "store", "-f", str(f))
    assert code == 0 and cboe.requests == ["SPY", "ACME"]
    assert not (tmp_path / "store" / "tickers.txt").exists()               # only written when no symbol is given
