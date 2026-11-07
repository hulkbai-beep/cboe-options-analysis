#!/usr/bin/env python3
"""
Local web UI for the cboe-options-analysis skill. Standard library only; listens on 127.0.0.1.

  python ui/server.py                    # http://127.0.0.1:8987 and opens the browser
  python ui/server.py --port 9000 --no-browser

Routes
  GET /                       ui/index.html
  GET /<file>                 static files from ui/ only (.html .js .css .svg .png .ico)
  GET /api/analyze?symbol=NVDA[&refresh=1]
                              one ticker: runs analyze.run_analysis (the same code as scripts/analyze.py)
  GET /api/market[?refresh=1] the market thermometer: runs analyze.run_market
                              both return {"metrics": ..., "chart": ...}; errors return {"error": ...} and, for a
                              refused symbol (VIX, DJX), "error_id"

Status codes: 404 unknown symbol on CBOE, 502 CBOE unreachable or unusable, 400 invalid symbol or nothing to
analyse (VIX in ticker mode, no live contracts), 500 anything else (redacted traceback on stderr).

Connection settings come from the environment exactly as for the scripts. On Windows, FMP_*, MASSIVE_* and OA_*
values saved with `setx` are also read from the registry, so a terminal opened before `setx` still works. For the
third-party proxy at 54.236.92.205, FMP_TLS_VERIFY=0 is set for this process only, and only if FMP_TLS_VERIFY is
not set already. The API key is never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from winenv import load_user_env  # noqa: E402  (needs the path above)

DEFAULT_PORT = 8987
SKIP_TLS_HOSTS = {"54.236.92.205"}   # third-party FMP proxy with a self-signed certificate
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
}
NO_KEY_WARNING = ("FMP_API_KEY not set: earnings, realized volatility, dividends and the price cone will be "
                  "unavailable")
NO_MASSIVE_WARNING = ("MASSIVE_API_KEY not set (optional): short interest, news, the Treasury discount curve and the "
                      "holiday-calendar check will be unavailable")

_lock = threading.Lock()  # the CBOE/FMP/Massive caches, rate limits and the history store: one analysis at a time


# ---------------------------------------------------------------------------- environment
def apply_proxy_tls_rule() -> bool:
    """Set FMP_TLS_VERIFY=0 for this process when FMP_BASE_URL points at the known proxy host and
    FMP_TLS_VERIFY is unset. Returns True if it was set."""
    base = os.environ.get("FMP_BASE_URL", "").strip()
    host = urllib.parse.urlsplit(base if "://" in base else "https://" + base).hostname or ""
    if host in SKIP_TLS_HOSTS and not os.environ.get("FMP_TLS_VERIFY"):
        os.environ["FMP_TLS_VERIFY"] = "0"
        return True
    return False


def redact(text: str) -> str:
    """Remove the API keys (and any apikey=... query value) from a message."""
    for name in ("FMP_API_KEY", "MASSIVE_API_KEY"):
        key = os.environ.get(name, "").strip()
        if key:
            text = text.replace(key, "***")
    return re.sub(r"(?i)(apikey=)[^&\s\"']+", r"\1***", text)


# ---------------------------------------------------------------------------- requests
def static_file(url_path: str) -> Path | None:
    """The file under ui/ that a URL path names, or None (traversal, hidden files, other types)."""
    rel = urllib.parse.unquote(url_path).lstrip("/") or "index.html"
    if any(c in rel for c in ("\\", "\x00", ":")):
        return None
    if any(part in ("", ".", "..") or part.startswith(".") for part in rel.split("/")):
        return None
    path = (HERE / rel).resolve()
    if not path.is_relative_to(HERE) or not path.is_file() or path.suffix.lower() not in STATIC_TYPES:
        return None
    return path


def _error(exc: Exception) -> tuple[int, dict]:
    """(HTTP status, payload) for an exception, in the order of plan 9.1."""
    kinds = {c.__name__ for c in type(exc).__mro__}
    msg = redact(str(exc)) or type(exc).__name__
    if "NotFound" in kinds:
        return 404, {"error": f"No option chain on CBOE: {msg}"}
    if "CboeError" in kinds:                         # CboeUnreachable and any other CBOE failure
        return 502, {"error": f"CBOE error: {msg}"}
    if isinstance(exc, (ValueError, LookupError)):   # invalid symbol, VIX refused, no live contracts
        payload = {"error": msg}
        if getattr(exc, "error_id", None):
            payload["error_id"] = exc.error_id
        return 400, payload
    sys.stderr.write(redact(traceback.format_exc()))
    return 500, {"error": f"{type(exc).__name__}: {msg}"}


def _refresh(query: dict) -> bool:
    return query.get("refresh") == "1"


def _result(metrics: dict, chart_data: dict | None) -> tuple[int, dict]:
    if chart_data is None:
        errors = "; ".join((metrics.get("meta") or {}).get("errors") or []) or "unknown error"
        return 500, {"error": redact(f"chart could not be built: {errors}")}
    return 200, {"metrics": metrics, "chart": chart_data}


def analyze_request(query: dict) -> tuple[int, dict]:
    """Validate the symbol, run the ticker analysis and return (HTTP status, JSON payload)."""
    from symbols import normalize
    text = query.get("symbol")
    try:
        normalize(text)                      # VIX, DJX and anything that is not a US symbol stop here
    except ValueError as exc:
        status, payload = _error(exc)
        if "error_id" not in payload:
            payload["error"] = f"Invalid symbol: {payload['error']}."
        return status, payload
    with _lock:
        try:
            import analyze
            metrics, chart_data = analyze.run_analysis(text, refresh=_refresh(query))
        except Exception as exc:  # report it as JSON and keep the server alive
            return _error(exc)
    return _result(metrics, chart_data)


def market_request(query: dict) -> tuple[int, dict]:
    """Run the market view and return (HTTP status, JSON payload)."""
    with _lock:
        try:
            import analyze
            metrics, chart_data = analyze.run_market(refresh=_refresh(query))
        except Exception as exc:
            return _error(exc)
    return _result(metrics, chart_data)


ROUTES = {"/api/analyze": analyze_request, "/api/market": market_request}


def to_json(payload: dict) -> bytes:
    from chart import json_safe
    return json.dumps(json_safe(payload, compact=False), ensure_ascii=False, allow_nan=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "oa-ui"

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self) -> bool:
        """Only answer requests addressed to this machine (blocks DNS-rebinding pages)."""
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            host = host[1:].split("]", 1)[0]
        else:
            host = host.rsplit(":", 1)[0]
        return host in LOCAL_HOSTS

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if not self._host_ok():
            self._send(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        route = ROUTES.get(url.path)
        if route is not None:
            query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            try:
                code, payload = route(query)
                body = to_json(payload)
            except Exception as exc:  # e.g. a payload that cannot be encoded
                code, payload = _error(exc)
                body = json.dumps(payload).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return
        path = static_file(url.path)
        if path is None:
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        self._send(200, path.read_bytes(), STATIC_TYPES[path.suffix.lower()])

    def log_message(self, fmt, *args):
        sys.stderr.write("[ui] " + redact(fmt % args) + "\n")


def make_server(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Local UI for the cboe-options-analysis skill")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port on 127.0.0.1 (default {DEFAULT_PORT})")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    load_user_env()
    if apply_proxy_tls_rule():
        print("Note: HTTPS certificate verification is off for the FMP proxy 54.236.92.205 "
              "(this process only).", file=sys.stderr)
    if not os.environ.get("FMP_API_KEY"):
        print(f"Warning: {NO_KEY_WARNING}. See SKILL.md > Setup.", file=sys.stderr)
    if not os.environ.get("MASSIVE_API_KEY"):
        print(f"Note: {NO_MASSIVE_WARNING}. See SKILL.md > Setup.", file=sys.stderr)

    srv = make_server(args.port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"Options-analysis UI running at {url}  (Ctrl+C to stop)", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
