"""The page code (plan 9.5): every text key chart.js and index.html use exists in ui/i18n.js, every enumerated value
has a label, and the symbol check of the local app accepts what scripts/symbols.py accepts."""
import re

import pytest

import chart
import config as C
from conftest import SKILL
from symbols import normalize

UI = SKILL / "ui"
CHART_JS = (UI / "chart.js").read_text(encoding="utf-8")
INDEX = (UI / "index.html").read_text(encoding="utf-8")
I18N = (UI / "i18n.js").read_text(encoding="utf-8")
SENTENCE_PREFIXES = ("note_", "warn_", "reason_", "err_")


def object_keys(src: str, opener: str) -> set[str]:
    """Top-level keys of the JS object literal that starts right after `opener` (strings, template literals and
    comments skipped; nested braces, brackets and parentheses ignored)."""
    i = src.index(opener) + len(opener)
    keys, depth, last = set(), 0, "{"
    while i < len(src):
        c = src[i]
        if src.startswith("//", i):
            i = src.index("\n", i)
            continue
        if c in "\"'`":
            j = i + 1
            while src[j] != c:
                j += 2 if src[j] == "\\" else 1
            i, last = j + 1, c
            continue
        if c in "{([":
            depth += 1
        elif c in "})]":
            if depth == 0:
                return keys
            depth -= 1
        elif depth == 0 and last in "{," and (m := re.match(r"([A-Za-z_]\w*)\s*:", src[i:])):
            keys.add(m.group(1))
            i, last = i + m.end(), ":"
            continue
        if not c.isspace():
            last = c
        i += 1
    raise AssertionError(f"unterminated object after {opener!r}")


EN = object_keys(I18N, "en: {")


def static_keys(src: str) -> set[str]:
    keys = set(re.findall(r"\bt\(\s*\"([a-z0-9_]+)\"\s*[,)]", src))            # t("key", ...), not t("op_" + k)
    keys |= set(re.findall(r"\bth:\s*\"([a-z0-9_]+)\"", src))                    # table columns {th: "key"}
    for arr in re.findall(r"\[((?:\s*\"[a-z0-9_]+\",?)+)\s*\]\.map\(k => [^\n]*?\bt\(k\)", src):
        keys |= set(re.findall(r"\"([a-z0-9_]+)\"", arr))                       # ["a", "b"].map(k => t(k))
    keys |= set(re.findall(r"data-i18n(?:-ph|-title|-aria)?=\"([a-z0-9_]+)\"", src))
    return keys


def test_the_dictionary_parses():
    assert len(EN) > 200 and {"lang_button", "app_title", "disclaimer", "kpi_price"} <= EN
    assert re.search(r"const STR = \{\s*en: \{", I18N)


def test_every_static_key_exists():
    missing = sorted((static_keys(CHART_JS) | static_keys(INDEX)) - EN)
    assert not missing, missing


def test_keys_built_from_prefixes():
    assert "t(\"op_\" + k)" in CHART_JS and "t(\"spark_\" + k)" in CHART_JS and "t(\"win_\" + k" in CHART_JS
    need = {f"op_{k}" for k in C.THERMO_FLAGS} | {f"spark_{k}" for _, k in chart.SPARK_COLUMNS}
    need |= {f"win_{k}" for k in chart.OI_WINDOWS}
    assert not sorted(need - EN)


def test_orders_match_python():
    flags = re.search(r"const FLAG_ORDER = \[([^\]]*)\]", CHART_JS).group(1)
    strats = re.search(r"const STRAT_ORDER = \[([^\]]*)\]", CHART_JS).group(1)
    assert tuple(re.findall(r"\"(\w+)\"", flags)) == C.THERMO_FLAGS
    assert tuple(re.findall(r"\"(\w+)\"", strats)) == C.ENUMS["strat_"]


def test_every_enumerated_value_has_a_label():
    used = set(re.findall(r"\btv\(\s*\"([a-z0-9_]+_)\"", CHART_JS))
    assert used <= set(C.ENUMS), sorted(used - set(C.ENUMS))
    missing = [p + v for p, values in C.ENUMS.items() if p not in SENTENCE_PREFIXES for v in values if p + v not in EN]
    assert not missing, missing


def test_fixed_sentences_come_from_python_in_english():
    # English shows Python's own sentence for note_/warn_ ids; only these two page wordings differ
    ids = {p + v for p in SENTENCE_PREFIXES for v in C.ENUMS[p]}
    assert EN & ids == {"reason_ivr_building", "err_vix_futures"}
    assert "has(prefix + id.id)" in CHART_JS or "I18N.has(prefix + id.id)" in CHART_JS
    assert 'has("err_" + ' in INDEX


def test_third_party_links():
    # news titles are the only links: chart.js accepts http(s) again (after scripts/chart.py) and opens a new tab
    # without opener or referrer; the title itself goes through esc() like every string
    assert re.search(r"const httpUrl = u => [^\n]*\^https\?:", CHART_JS) and "const href = httpUrl(r.url)" in CHART_JS
    assert CHART_JS.count("<a href=") == 1
    assert '<a href="${esc(href)}" target="_blank" rel="noopener noreferrer nofollow">${esc(r.title)}</a>' in CHART_JS


def test_one_language_hides_the_switch():
    assert re.findall(r"^    (\w+): \{", I18N, re.M) == ["en"]
    assert "if (LANGS.length < 2) { button.hidden = true; return; }" in I18N


def test_disclaimer_matches_python():
    assert re.search(r'disclaimer: "([^"]+)"', I18N).group(1) == C.DISCLAIMER


# ---------------------------------------------------------------------------- the symbol box of the local app
SYMBOL_RE = re.compile(re.search(r"const SYMBOL_RE = /(.+)/;", INDEX).group(1))


def clean(s: str) -> str:
    """index.html's cleanSymbol."""
    return re.sub(r"^\$ ", "$", re.sub(r"\s+", " ", str(s or "").strip().upper()))


@pytest.mark.parametrize("text", ["nvda", "$NVDA", " NVDA ", "BRK.B", "BRK-B", "BRK/B", "brk b", "SPX", "^SPX", "$SPX",
                                  "_SPX", "NDX", "SPY", "^OEX", "XSP", "$ nvda"])
def test_the_page_accepts_what_python_accepts(text):
    normalize(text)                                                          # Python accepts it ...
    assert SYMBOL_RE.match(clean(text)), text                               # ... and so does the page


@pytest.mark.parametrize("text", ["", "  ", "NOT A TICKER!", "TOOLONGSYMBOL", "A.BCDEF", "<script>"])
def test_the_page_refuses_garbage(text):
    assert not SYMBOL_RE.match(clean(text))
