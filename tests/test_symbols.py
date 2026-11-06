"""Symbol normalisation (plan 4.1): every spelling people type, the refusals and the index table."""
import pytest

from symbols import SymbolRejected, kind_from_profile, normalize, normalize_symbol, roots_for


@pytest.mark.parametrize("text, key, cboe, fmp, kind, source", [
    ("nvda", "NVDA", "NVDA", "NVDA", "stock", "default"),
    ("$NVDA", "NVDA", "NVDA", "NVDA", "stock", "default"),
    (" NVDA ", "NVDA", "NVDA", "NVDA", "stock", "default"),
    ("BRK.B", "BRK.B", "BRK.B", "BRK-B", "stock", "default"),
    ("BRK-B", "BRK.B", "BRK.B", "BRK-B", "stock", "default"),
    ("BRK/B", "BRK.B", "BRK.B", "BRK-B", "stock", "default"),
    ("brk b", "BRK.B", "BRK.B", "BRK-B", "stock", "default"),
    ("SPX", "SPX", "_SPX", "^GSPC", "index", "index_table"),
    ("^SPX", "SPX", "_SPX", "^GSPC", "index", "index_table"),
    ("$SPX", "SPX", "_SPX", "^GSPC", "index", "index_table"),
    ("_SPX", "SPX", "_SPX", "^GSPC", "index", "index_table"),
    ("NDX", "NDX", "_NDX", None, "index", "index_table"),
    ("SPY", "SPY", "SPY", "SPY", "etf", "known_etfs"),
    ("^OEX", "OEX", "_OEX", None, "index", "cboe_fallback"),
])
def test_spellings(text, key, cboe, fmp, kind, source):
    s = normalize(text)
    assert (s["key"], s["cboe"], s["fmp"], s["kind"], s["kind_source"]) == (key, cboe, fmp, kind, source)


def test_xsp_price_multiplier():
    s = normalize("XSP")
    assert (s["cboe"], s["fmp"], s["fmp_scale"]) == ("_XSP", "^GSPC", 0.1)


def test_profile_decides_the_kind():
    assert normalize("ACME", fmp_profile={"isEtf": True})["kind_source"] == "fmp"
    assert kind_from_profile("ACME", {"isEtf": False, "isFund": False}) == ("stock", "fmp")
    assert kind_from_profile("ACME", {"isFund": True}) == ("etf", "fmp")
    assert kind_from_profile("SPY", None) == ("etf", "known_etfs")


@pytest.mark.parametrize("text, error_id", [("VIX", "vix_futures"), ("^VIX", "vix_futures"), ("_VIX", "vix_futures"),
                                            ("VIXW", "vix_futures"), ("DJX", "djx")])
def test_refused(text, error_id):
    with pytest.raises(SymbolRejected) as e:
        normalize(text)
    assert e.value.error_id == error_id
    assert isinstance(e.value, ValueError)


def test_vix_allowed_for_snapshots():
    s = normalize("VIX", allow_futures_settled=True)
    assert (s["key"], s["cboe"], s["kind"]) == ("VIX", "_VIX", "index")


@pytest.mark.parametrize("text", ["SHOP.TO", "0700.HK", "VOD.L", "7203.T", "", "  ", "NOT A TICKER!", None])
def test_not_us_symbols(text):
    with pytest.raises(ValueError) as e:
        normalize(text)
    assert not isinstance(e.value, SymbolRejected)


def test_fmp_symbol_unchanged_from_the_fmp_skills():
    assert normalize_symbol("brk.b") == "BRK-B"
    assert normalize_symbol("^vix") == "^VIX"


def test_roots():
    assert roots_for("SPX") == ("SPXW", "SPX")
    assert roots_for("NDX") == ("NDXP", "NDX")
    assert roots_for("BRK.B") == ("BRKB",)
    assert roots_for("NVDA") == ("NVDA",)
