"""Shared test setup: scripts/ on sys.path, the real fixtures (outside the skill folder), no network, and the
synthetic chains (written to a temporary folder at session start, never into the skill folder)."""
from __future__ import annotations

import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SKILL = TESTS.parent
sys.path[:0] = [str(SKILL / "scripts"), str(TESTS)]

import config as C  # noqa: E402
import chain as CH  # noqa: E402

DEFAULT_TEST_DATA = SKILL.parent / "test-data"
# a fixed "now" after the 2026-11-05 close data and before the next session's 09:45 (not stale)
NOW = datetime(2026, 11, 6, 9, 0, tzinfo=CH.NY)


def test_data_dir() -> Path | None:
    p = Path(os.environ.get(C.TEST_DATA_ENV) or DEFAULT_TEST_DATA)
    return p if (p / "cboe" / "2026-11-05").is_dir() else None


@pytest.fixture(scope="session")
def real_data() -> Path:
    p = test_data_dir()
    if p is None:
        pytest.skip(f"real fixtures not found (set {C.TEST_DATA_ENV} to the test-data folder)")
    return p


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network access in a test")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)      # CboeClient's own opener
    return refuse


@pytest.fixture(scope="session")
def synth_dir(tmp_path_factory) -> Path:
    """NDX.json.gz, XSP.json.gz and OEX.json.gz built by synth.py (deterministic)."""
    import synth
    d = tmp_path_factory.mktemp("synthetic_chains")
    synth.write_standard_chains(d)
    return d


@pytest.fixture(scope="session")
def synth_data(tmp_path_factory) -> Path:
    """A whole synthetic test-data tree (synth.write_dataset: SPX, SPY, QQQ, IWM, VIX, ACME and their FMP files)
    with NDX, XSP and OEX in extra/; FakeCboe(synth_data, extra_dir=synth_data / "extra") serves all of them."""
    import synth
    root = tmp_path_factory.mktemp("synthetic_data")
    synth.write_dataset(root)
    synth.write_standard_chains(root / "extra")
    return root


@pytest.fixture(scope="session")
def synth_run(synth_data, tmp_path_factory):
    """run(ticker or "MARKET", **FakeFMP options) -> (metrics, chart_data) on the synthetic data, computed once per
    combination; every run gets its own empty history store."""
    import analyze
    done = {}

    def run(ticker: str, **fmp_kwargs):
        key = (ticker, repr(sorted(fmp_kwargs.items())))
        if key not in done:
            cboe, fmp = fakes_for(synth_data, **fmp_kwargs)
            store = tmp_path_factory.mktemp("history")
            if ticker == "MARKET":
                done[key] = analyze.run_market(client=cboe, fmp=fmp, now=NOW, data_dir=store)
            else:
                done[key] = analyze.run_analysis(ticker, client=cboe, fmp=fmp, now=NOW, data_dir=store)
        return done[key]
    return run


def fakes_for(test_data: Path, **fmp_kwargs):
    """(FakeCboe, FakeFMP) over a test-data tree; extra/ (synthetic chains) is used when present."""
    from fakes import FakeCboe, FakeFMP
    extra = test_data / "extra"
    return FakeCboe(test_data, extra_dir=extra if extra.is_dir() else None), FakeFMP(test_data, **fmp_kwargs)


def load_chain(test_data: Path, code: str, day: str = "2026-11-05") -> dict:
    """One chain payload of a test-data tree (cboe/<day>/<code>.json.gz)."""
    import gzip
    import json
    return json.loads(gzip.decompress((test_data / "cboe" / day / f"{code}.json.gz").read_bytes()))


def make_ctx(payload: dict, key: str, kind: str = "index", earnings_date=None) -> dict:
    """The chain context of one payload, as run_analysis builds it (no FMP data)."""
    import core
    frame = CH.frame_from_options(payload["data"]["options"])
    session = CH.session_from_payload(payload)
    session["key"] = key
    return core.chain_context(key, kind, session, frame, earnings_date)


@pytest.fixture
def now():
    return NOW
