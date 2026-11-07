"""The docs describe the code as it is (plan 11, 12): SKILL.md's frontmatter and length, the options tables against
argparse, every command line and every skill path the docs name, the folder layout of the README, the config
constants and environment variables the docs name, and every metrics.json key path the docs name (checked against
synthetic runs with and without Massive, two of them with a seeded history store so IV Rank and the open-interest
change are available)."""
import json
import re
import sys
from datetime import date

import pytest

import analyze
import chain as CH
import config as C
import fetch
import history as H
import massive_client as MC
import snapshot
import synth
import winenv
from conftest import NOW, SKILL, fakes_for, load_chain
from fakes import FakeMassive

sys.path.insert(0, str(SKILL / "ui"))
import server  # noqa: E402

DOCS = ("SKILL.md", "README.md", "references/report_template.md", "references/market_template.md",
        "references/methodology.md", "references/strategies.md")
TEXT = {name: (SKILL / name).read_text(encoding="utf-8") for name in DOCS}
TICKER_KEYS = ("mode", "ticker", "as_of", "quote_time", "data_source", "snapshot", "data_quality", "market_context",
               "expected_move", "term_structure", "skew", "volatility", "earnings", "positioning", "gamma", "flow",
               "news", "strategies", "history", "meta")
MARKET_KEYS = ("mode", "as_of", "quote_time", "data_source", "snapshot", "data_quality", "vix", "term_structure",
               "skew_board", "gamma_board", "expected_moves", "positioning", "realized", "thermometer", "history",
               "meta")
OPTION = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
COMMAND = re.compile(r"\b(?:python3?|py) (scripts|ui)/(\w+\.py)\b([^`\n]*)")
SKILL_PATH = re.compile(r"(?<![\w./~-])(?:scripts|references|ui|tests)/[\w./-]*\w")
KEY_PATH = re.compile(r"`([a-z_0-9]+(?:\.(?:[A-Za-z0-9_]+|<[a-z_]+>)|\[\*\])+)`")
ENV_VARS = {"OA_DATA_DIR", "OA_CACHE_DIR", "OA_TEST_DATA", "FMP_API_KEY", "FMP_BASE_URL", "FMP_TLS_VERIFY",
            "FMP_CACHE_DIR", "FMP_MAX_PER_MIN", "FMP_TIMEOUT", "FMP_PATH_PREFIX", "FMP_AUTH_STYLE", "MASSIVE_API_KEY",
            "MASSIVE_MAX_PER_MIN", "MASSIVE_MAX_WAIT", "MASSIVE_TIMEOUT", "MASSIVE_CACHE_DIR", "SSL_CERT_FILE"}
MASSIVE_VARS = {n for n in winenv.ENV_NAMES if n.startswith("MASSIVE_")}     # winenv reads them from the registry


def without_code(text: str) -> str:
    return re.sub(r"^```.*?^```[^\n]*$", "", text, flags=re.M | re.S)


def tables(text: str) -> list[list[list[str]]]:
    """Each markdown table outside code blocks as its rows of cells (separator rows left out)."""
    out, cur = [], None
    for line in without_code(text).splitlines():
        line = line.strip()
        if line.startswith("|"):
            if cur is None:
                cur = []
                out.append(cur)
            if not re.fullmatch(r"\|[\s|:-]+\|", line):
                cur.append([c.strip() for c in line.strip("|").split("|")])
        else:
            cur = None
    return out


def first_column_options(table) -> set[str]:
    return {o for row in table[1:] for o in OPTION.findall(row[0])}


def parser_options(parser) -> set[str]:
    return {s for a in parser._actions for s in a.option_strings if s.startswith("--")} - {"--help"}


SCRIPT_OPTIONS = {
    "analyze.py": parser_options(analyze.build_parser()),
    "snapshot.py": parser_options(snapshot.build_parser()),
    "server.py": parser_options(server.build_parser()),
    "cboe_client.py": set(),
    "fmp_client.py": set(),
    "massive_client.py": set(),
}


# ---------------------------------------------------------------------------- SKILL.md
def frontmatter() -> tuple[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n", TEXT["SKILL.md"], re.S)
    assert m, "SKILL.md has no frontmatter"
    return m.group(1), TEXT["SKILL.md"][m.end():]


def test_frontmatter():
    head, _ = frontmatter()
    keys = re.findall(r"^([a-z_-]+):", head, re.M)
    assert keys == ["name", "description"]
    name = re.search(r"^name: (.+)$", head, re.M).group(1)
    desc = re.search(r"^description: (.+)$", head, re.M).group(1)
    assert name == C.SKILL_NAME
    assert len(desc) <= 1024 and not set("<>") & set(desc)
    assert ": " not in desc and " #" not in desc                      # both would break the YAML scalar
    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load(head) == {"name": name, "description": desc}


def test_skill_md_is_short():
    assert len(TEXT["SKILL.md"].splitlines()) < 500


def test_skill_md_names_the_disclaimer_rule_and_every_reference():
    _, body = frontmatter()
    for ref in ("report_template.md", "market_template.md", "methodology.md", "strategies.md"):
        assert f"references/{ref}" in body, ref
    assert "meta.disclaimer" in body


# ---------------------------------------------------------------------------- options and command lines
def test_skill_options_table_matches_argparse():
    table = next(t for t in tables(TEXT["SKILL.md"]) if t[0][0] == "Option")
    assert first_column_options(table) == SCRIPT_OPTIONS["analyze.py"]
    env = {re.sub(r"`", "", row[0]) for row in table[1:] if not row[0].startswith("`--")}
    assert {"TICKER", "OA_DATA_DIR", "OA_CACHE_DIR", "FMP_API_KEY", "FMP_BASE_URL", "FMP_TLS_VERIFY",
            "FMP_CACHE_DIR"} | MASSIVE_VARS <= env


def test_massive_variables_are_documented():
    """Every MASSIVE_* variable (the ones winenv.load_user_env reads from the registry) is named in SKILL.md and
    README.md."""
    assert MASSIVE_VARS == {n for n in ENV_VARS if n.startswith("MASSIVE_")}
    for name in ("SKILL.md", "README.md"):
        missing = sorted(n for n in MASSIVE_VARS if f"`{n}`" not in TEXT[name])
        assert not missing, (name, missing)


def test_readme_options_tables_match_argparse():
    tabs = tables(TEXT["README.md"])
    an = next(t for t in tabs if t[0][0] == "Option" and "`--market`" in {r[0] for r in t})
    sn = next(t for t in tabs if t[0][0] == "Option" and "`--rebuild-summary`" in {r[0] for r in t})
    assert {o.split()[0] for o in first_column_options(an)} == SCRIPT_OPTIONS["analyze.py"]
    assert first_column_options(sn) == SCRIPT_OPTIONS["snapshot.py"] - {"--out"}   # -o / --out: the old alias


@pytest.mark.parametrize("name", DOCS)
def test_command_lines_use_real_options(name):
    for _, script, rest in COMMAND.findall(TEXT[name]):
        assert script in SCRIPT_OPTIONS, (name, script)
        used = set(OPTION.findall(rest.split(" python")[0]))
        assert used <= SCRIPT_OPTIONS[script] | {"--exclude"}, (name, script, used - SCRIPT_OPTIONS[script])


@pytest.mark.parametrize("name", DOCS)
def test_named_skill_files_exist(name):
    missing = sorted({p for p in SKILL_PATH.findall(TEXT[name]) if "<" not in p and not (SKILL / p).exists()})
    assert not missing, (name, missing)


def test_readme_folder_layout_lists_every_file():
    block = next(b for b in re.findall(r"^```\n(.*?)^```", TEXT["README.md"], re.M | re.S)
                 if b.startswith("cboe-options-analysis/"))
    for folder in ("scripts", "references", "ui"):
        for f in sorted((SKILL / folder).iterdir()):
            if f.is_file():
                assert re.search(rf"(?<![\w.]){re.escape(f.name)}(?![\w.])", block), f"{folder}/{f.name}"
    for f in ("SKILL.md", "README.md", "requirements.txt", "requirements-dev.txt"):
        assert f in block


def test_methodology_constants_exist():
    pattern = r"`([A-Z][A-Z0-9]*_[A-Z0-9_]+)`"                          # CONSTANT_NAMES (not `CODE`, `B1SPY`)
    names = set(re.findall(pattern, TEXT["references/methodology.md"]))
    names |= set(re.findall(pattern, TEXT["references/strategies.md"]))
    unknown = sorted(n for n in names if not hasattr(C, n) and n not in ENV_VARS)
    assert not unknown, unknown


def test_disclaimer_is_the_configured_one():
    for name in ("references/report_template.md", "references/market_template.md"):
        assert f"*{C.DISCLAIMER}*" in TEXT[name], name


# ---------------------------------------------------------------------------- report templates
def numbered(text: str, level: str) -> list[str]:
    return re.findall(rf"^{level} (\d+)\. ", text, re.M)


@pytest.mark.parametrize("name, n", [("references/report_template.md", 13), ("references/market_template.md", 9)])
def test_template_sections_and_example_agree(name, n):
    text = TEXT[name]
    rules, example = text.split("## Worked example", 1)
    assert numbered(rules, "###") == [str(i) for i in range(n + 1)]       # 0 is the title
    assert numbered(example, "##") == [str(i) for i in range(1, n + 1)]
    heads = re.findall(r"^### \d+\. ([^(\n]+?)(?: \(|$)", rules, re.M)[1:]
    shown = re.findall(r"^## \d+\. (.+)$", example, re.M)
    assert [h.strip() for h in heads] == shown


WORDS = re.compile(r"\b(recommend\w*|should|cheap\w*|expensive|undervalued|overvalued|attractive|opportunit\w*|"
                   r"bargain|worth it|sure thing|win rate|will)\b", re.I)


@pytest.mark.parametrize("name", ["references/report_template.md", "references/market_template.md"])
def test_examples_have_no_verdict_words(name):
    example = TEXT[name].split("## Worked example", 1)[1]
    found = sorted({m.group(0).lower() for m in WORDS.finditer(example)} - {"recommendation"})
    assert not found, found


# ---------------------------------------------------------------------------- metrics key paths
def calendar_mismatch() -> dict:
    """synth.massive_data() with an exchange closure that config.py does not list, so data_quality.calendar_check
    has a mismatch whose fields the docs can name."""
    data = synth.massive_data()
    data[(MC.HOLIDAYS, None)] = data[(MC.HOLIDAYS, None)] + [
        {"date": "2026-12-31", "exchange": "NYSE", "name": "Extra closure", "status": "closed"}]
    return data


@pytest.fixture(scope="module")
def runs(synth_data, tmp_path_factory):
    """Ticker runs: SPY, NDX, XSP without Massive; ACME (FakeMassive) and SPX (FakeMassive with a calendar mismatch)
    with a seeded history store. Market runs: without Massive, and with it (and the mismatch). Every run is given its
    Massive state, so none builds a client from a MASSIVE_API_KEY in the environment (module fixtures are set up
    before the function-scoped no_massive_key)."""
    def run(key: str, massive, store=None) -> dict:
        cboe, fmp = fakes_for(synth_data)
        store = store or tmp_path_factory.mktemp("history")
        if key == "MARKET":
            return analyze.run_market(client=cboe, fmp=fmp, now=NOW, data_dir=store, massive=massive)[0]
        return analyze.run_analysis(key, client=cboe, fmp=fmp, now=NOW, data_dir=store, massive=massive)[0]

    out = {"ticker": [run(t, fetch.MassiveState(None)) for t in ("SPY", "NDX", "XSP")],
           "market": [run("MARKET", fetch.MassiveState(None)), run("MARKET", FakeMassive(data=calendar_mismatch()))]}
    for key, massive in (("ACME", FakeMassive()), ("SPX", FakeMassive(data=calendar_mismatch()))):
        store = tmp_path_factory.mktemp(f"seeded_{key}")
        row = run(key, fetch.MassiveState(None))["history"]["summary_row"]
        days, d = [], date(2026, 11, 4)
        while len(days) < 70:
            if CH.is_trading_day(d):
                days.append(d)
            d = date.fromordinal(d.toordinal() - 1)
        rows = []
        for i, td in enumerate(sorted(days)):
            r = dict(row, trade_date=td)
            for col in ("cm_iv_30d", "cm_iv_30d_ex_event", "rr25_30d", "ratio_30_90", "net_gex_1pct"):
                if r.get(col) is not None:
                    r[col] = r[col] * (0.8 + 0.005 * i)
            rows.append(r)
        H.write_summary(store, key, rows)
        base = json.loads(json.dumps(load_chain(synth_data, key)))     # a base chain whose open interest differs
        for i, o in enumerate(base["data"]["options"]):
            o["open_interest"] = max(0, (o.get("open_interest") or 0) + (300 if i % 2 else -300))
        H.save_chain(store, key, base, dict(CH.session_from_payload(base), as_of=date(2026, 11, 4)))
        m = run(key, massive, store)
        oc = m["positioning"]["oi_change"]
        assert m["volatility"]["iv_rank"]["available"] and oc["top_increases"] and oc["top_decreases"], key
        assert m["snapshot"]["rates"]["source"] == "treasury" and m["data_quality"]["calendar_check"]["available"]
        out["ticker"].append(m)
    acme, spx = out["ticker"][-2:]
    assert acme["positioning"]["short"]["float"]["available"] and acme["news"]["rows"]
    for m in (spx, out["market"][1]):
        assert m["data_quality"]["calendar_check"]["mismatches"], m["mode"]
    return out


def resolve(obj, parts: list[str]) -> bool:
    """True when the key path exists in obj: [*] = some list element, <x> = some dict key; a final "reason" only
    needs its parent block (every block has one when it is unavailable)."""
    if not parts:
        return True
    head, rest = parts[0], parts[1:]
    if head == "reason" and not rest and isinstance(obj, dict):
        return True
    if head == "[*]":                                                    # a path ending in [*]: the list itself
        return isinstance(obj, list) and (not rest or any(resolve(v, rest) for v in obj))
    if not isinstance(obj, dict):
        return False
    if head.startswith("<"):
        return any(resolve(v, rest) for v in obj.values())
    return head in obj and resolve(obj[head], rest)


def split_path(path: str) -> list[str]:
    return [p for p in re.split(r"\.|(\[\*\])", path) if p]


DOC_MODES = {"SKILL.md": ("ticker",), "README.md": ("ticker",), "references/report_template.md": ("ticker",),
             "references/market_template.md": ("market",), "references/methodology.md": ("ticker", "market"),
             "references/strategies.md": ("ticker",)}


@pytest.mark.parametrize("name", DOCS)
def test_key_paths_exist(name, runs):
    tops = set(TICKER_KEYS) | set(MARKET_KEYS)
    bad = []
    for path in sorted(set(KEY_PATH.findall(TEXT[name]))):
        parts = split_path(path)
        if parts[0] not in tops or re.search(r"\.(py|md|json|html|js|css|txt|parquet)$", path):   # snapshot.py
            continue
        if not any(resolve(m, parts) for mode in DOC_MODES[name] for m in runs[mode]):
            bad.append(path)
    assert not bad, (name, bad)


def test_top_level_keys_in_methodology_match_the_output(runs):
    text = TEXT["references/methodology.md"]
    assert tuple(runs["ticker"][0]) == TICKER_KEYS and tuple(runs["market"][0]) == MARKET_KEYS
    items = text.split("\n- ")
    for start, keys in (("Ticker top-level keys", TICKER_KEYS), ("Market:", MARKET_KEYS)):
        line = next(ln for ln in items if ln.startswith(start))
        assert re.findall(r"`(\w+)`", line) == list(keys), start


def names_and_values(obj, keys: set, values: set) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            names_and_values(v, keys, values)
    elif isinstance(obj, list):
        for v in obj:
            names_and_values(v, keys, values)
    elif isinstance(obj, str):
        values.add(obj)


@pytest.mark.parametrize("name", ["references/report_template.md", "references/market_template.md",
                                  "references/strategies.md"])
def test_field_names_exist(name, runs):
    """Every bare `field_name` of a template is a key of metrics.json, an enumerated value or a string value."""
    keys, values = set(), set()
    for mode in DOC_MODES[name]:
        for m in runs[mode]:
            names_and_values(m, keys, values)
    enum_values = {v for vs in C.ENUMS.values() for v in vs}
    words = set(re.findall(r"`([a-z][a-z0-9_]*)`", TEXT[name]))
    unknown = sorted(w for w in words if w not in keys | values | enum_values | {"null"})
    assert not unknown, (name, unknown)
