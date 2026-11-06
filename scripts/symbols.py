"""
Symbol normalisation for every entry point.

normalize_symbol(text) -> the symbol FMP's stable API expects (unchanged from the fmp-* skills: BRK.B -> BRK-B,
foreign listings refused). fmp_client.py check, the UI server and the tests import it.

normalize(text, fmp_profile=None, allow_futures_settled=False) -> what this skill needs for one symbol:
    {key, cboe, fmp, fmp_scale, kind, kind_source}
  key        NVDA, BRK.B, SPX: names outputs, history files and the `underlying` column
  cboe       NVDA, BRK.B, _SPX: the CBOE delayed-quotes symbol (indices carry a leading underscore)
  fmp        NVDA, BRK-B, ^GSPC, or None when FMP has no daily bars for it
  fmp_scale  multiplier that puts FMP prices on the CBOE scale (XSP = ^GSPC x 0.1)
  kind       stock | etf | index
  kind_source index_table | fmp | known_etfs | default | cboe_fallback

People type nvda, $NVDA, BRK.B, BRK-B, BRK/B, "brk b", SPX, ^SPX, $SPX or _SPX. VIX and VIXW are refused
(SymbolRejected, error_id "vix_futures") unless allow_futures_settled=True (snapshots); DJX is refused
(error_id "djx"). normalize() makes no network request: the "_" fallback for an index that is not in
config.INDEX_SYMBOLS happens in fetch.load_all().
"""
from __future__ import annotations

import re

import config as C

_PLAIN = re.compile(r"^\^?[A-Z]{1,8}$")                           # AAPL, GOOGL, ^VIX
_SHARE_CLASS = re.compile(r"^([A-Z]{1,6})[.\-/ ]([A-Z])$")      # BRK.B, BRK-B, BRK/B, BRK B
_FOREIGN = re.compile(r"^[A-Z0-9]{1,10}\.([A-Z]{1,4})$")         # SHOP.TO, 0700.HK, SAP.DE, 7203.T
# One-letter exchange suffixes that collide with the share-class form: refused rather than turned into X-L.
_ONE_LETTER_EXCHANGES = {"L": "London", "T": "Tokyo"}
EXAMPLES = "e.g. NVDA, BRK.B, SPX"
_INDEX_PREFIXED = re.compile(r"^[\^_]([A-Z]{1,8})$")            # ^SPX, _SPX, ^OEX


class SymbolRejected(ValueError):
    """A symbol this skill deliberately does not analyse in ticker mode (VIX, DJX). `error_id` names the
    fixed sentence (i18n key err_<error_id>)."""

    def __init__(self, message: str, error_id: str):
        super().__init__(message)
        self.error_id = error_id


def normalize_symbol(text) -> str:
    """The FMP symbol for a typed ticker; ValueError with a hint when it cannot be a US listing."""
    raw = "" if text is None else str(text)
    s = re.sub(r"\s+", " ", raw.strip().upper())
    if s.startswith("$"):
        s = s[1:].strip()
    if not s:
        raise ValueError("no ticker given")
    if _PLAIN.match(s):
        return s
    m = _SHARE_CLASS.match(s)
    if m:
        root, cls = m.groups()
        if "." in s and cls in _ONE_LETTER_EXCHANGES:
            raise ValueError(f"{raw.strip()!r} looks like a {_ONE_LETTER_EXCHANGES[cls]} listing; this skill covers US "
                             f"listings only (use the US ticker or ADR; a US share class is written {root}-{cls})")
        return f"{root}-{cls}"
    m = _FOREIGN.match(s)
    if m:
        raise ValueError(f"{raw.strip()!r} looks like a non-US listing (exchange suffix .{m.group(1)}); this skill "
                         "covers US listings only: use the US ticker or ADR")
    raise ValueError(f"{raw.strip()!r} is not a US ticker: letters, optionally a share class ({EXAMPLES})")


def _check_refused(base: str, allow_futures_settled: bool) -> None:
    if base in C.FUTURES_SETTLED and not allow_futures_settled:
        raise SymbolRejected(C.VIX_FUTURES_MESSAGE, "vix_futures")
    if base in C.UNSUPPORTED:
        raise SymbolRejected(C.UNSUPPORTED[base], base.lower())


def _index(key: str, kind_source: str = "index_table") -> dict:
    if key in C.INDEX_SYMBOLS:
        cboe, fmp, scale = C.INDEX_SYMBOLS[key]
        return {"key": key, "cboe": cboe, "fmp": fmp, "fmp_scale": scale, "kind": "index", "kind_source": kind_source}
    return {"key": key, "cboe": "_" + key, "fmp": None, "fmp_scale": None, "kind": "index",
            "kind_source": "cboe_fallback"}


def kind_from_profile(key: str, fmp_profile: dict | None) -> tuple[str, str]:
    """(kind, kind_source) of a non-index symbol: the FMP profile when there is one, else the known-ETF list."""
    if fmp_profile:
        return ("etf" if (fmp_profile.get("isEtf") or fmp_profile.get("isFund")) else "stock"), "fmp"
    if key in C.KNOWN_ETFS:
        return "etf", "known_etfs"
    return "stock", "default"


def normalize(text, fmp_profile: dict | None = None, allow_futures_settled: bool = False) -> dict:
    """{key, cboe, fmp, fmp_scale, kind, kind_source} for a typed symbol (see the module docstring).
    Raises SymbolRejected (a ValueError) for VIX / VIXW / DJX and ValueError for anything else unusable."""
    raw = "" if text is None else str(text)
    s = re.sub(r"\s+", " ", raw.strip().upper())
    if s.startswith("$"):
        s = s[1:].strip()
    m = _INDEX_PREFIXED.match(s)
    if m:                                         # ^SPX, _SPX: an index, in the table or not
        base = m.group(1)
        _check_refused(base, allow_futures_settled)
        return _index(base)
    if s in C.INDEX_SYMBOLS or s in C.FUTURES_SETTLED or s in C.UNSUPPORTED:
        _check_refused(s, allow_futures_settled)
        return _index(s)
    fmp = normalize_symbol(s)                     # BRK.B -> BRK-B; refuses foreign listings
    if fmp.startswith("^"):                       # ^OEX passed the FMP rule; handled above, kept for safety
        return _index(fmp[1:])
    key = fmp.replace("-", ".")
    kind, source = kind_from_profile(key, fmp_profile)
    return {"key": key, "cboe": key, "fmp": fmp, "fmp_scale": 1.0, "kind": kind, "kind_source": source}


def roots_for(key: str) -> tuple[str, ...]:
    """Standard option roots of a key, in surface priority order (BRK.B -> BRKB)."""
    return tuple(C.STANDARD_ROOTS.get(key, (key.replace(".", "").replace("_", ""),)))
