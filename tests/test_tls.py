"""HTTPS certificate checks (scripts/tls.py): the context always verifies; truststore, certifi and SSL_CERT_FILE are
used when present; the certificate a host presents is read for the error message (directly and through a CONNECT
proxy) without sending a request; and CboeClient reports a failed check with the reason, the presented certificate
and what was trusted, against a local HTTPS server whose certificate comes from an unknown authority."""
import gzip
import importlib.util
import json
import re
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import config as C
import synth
import tls
from cboe_client import CboeClient, CboeUnreachable
from fakes import FakeTransport

HAS = {name: importlib.util.find_spec(name) is not None for name in ("truststore", "certifi")}


@pytest.fixture
def no_cert_file(monkeypatch):
    monkeypatch.delenv(tls.CERT_FILE_ENV, raising=False)


# ---------------------------------------------------------------------------- the context
@pytest.mark.parametrize("hidden", [(), ("truststore",), ("certifi",), ("truststore", "certifi")])
def test_context_always_verifies(monkeypatch, no_cert_file, hidden):
    for name in hidden:
        monkeypatch.setitem(sys.modules, name, None)                 # import fails as if not installed
    t = tls.trust()
    assert t.context.verify_mode == ssl.CERT_REQUIRED and t.context.check_hostname
    missing = {n for n in ("truststore", "certifi") if n in hidden or not HAS[n]}
    assert set(t.missing) == missing
    for n in ("truststore", "certifi"):
        assert bool(re.search(rf"\b{n} \S", t.description)) == (n not in missing), n     # "certifi 2026.10.23"
    if "truststore" in missing:
        assert t.description.startswith("Python's default CA list")


def certifi_pem(label: str) -> str:
    certifi = pytest.importorskip("certifi")
    text = Path(certifi.where()).read_text(encoding="utf-8")
    start = text.index(f'# Label: "{label}"')
    begin = text.index("-----BEGIN CERTIFICATE-----", start)
    end = text.index("-----END CERTIFICATE-----", begin) + len("-----END CERTIFICATE-----")
    return text[begin:end] + "\n"


def test_ssl_cert_file_is_trusted(monkeypatch, tmp_path):
    pem = tmp_path / "company-root.pem"
    pem.write_text(certifi_pem("ISRG Root X2"), encoding="ascii")
    monkeypatch.setitem(sys.modules, "certifi", None)                # so the root can only come from the file
    monkeypatch.setenv(tls.CERT_FILE_ENV, str(pem))
    t = tls.trust()
    assert f"SSL_CERT_FILE {pem}" in t.description
    inner = getattr(t.context, "_ctx", t.context)       # truststore reads the extra roots from its inner context
    assert ssl.PEM_cert_to_DER_cert(pem.read_text()) in inner.get_ca_certs(binary_form=True)


def test_unreadable_ssl_cert_file_is_named_not_fatal(monkeypatch, tmp_path):
    missing = tmp_path / "nowhere.pem"
    monkeypatch.setenv(tls.CERT_FILE_ENV, str(missing))
    t = tls.trust()
    assert f"SSL_CERT_FILE {missing} (not loaded: FileNotFoundError)" in t.description
    assert t.context.verify_mode == ssl.CERT_REQUIRED


# ---------------------------------------------------------------------------- reading a certificate
def tlv(tag: int, body: bytes) -> bytes:
    n = len(body)
    if n < 0x80:
        return bytes([tag, n]) + body
    size = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(size)]) + size + body


def oid(*arcs: int) -> bytes:
    return tlv(0x06, bytes(arcs))


CN, O = oid(0x55, 4, 3), oid(0x55, 4, 10)


def name(*attrs) -> bytes:
    """attrs: (oid, string tag, text); 0x1E (BMPString) is encoded as UTF-16."""
    return tlv(0x30, b"".join(tlv(0x31, tlv(0x30, o + tlv(t, v.encode("utf-16-be" if t == 0x1E else "utf-8"))))
                              for o, t, v in attrs))


def der_cert(subject: bytes, issuer: bytes, not_before=(0x17, "261105000000Z"),
             not_after=(0x18, "20261231235959Z"), sans=()) -> bytes:
    ext = b""
    if sans:
        names = tlv(0x30, b"".join(tlv(0x82, s.encode()) for s in sans))
        ext = tlv(0xA3, tlv(0x30, tlv(0x30, oid(0x55, 0x1D, 0x11) + tlv(0x04, names))))
    algorithm = tlv(0x30, oid(0x2A, 0x86, 0x48, 0xCE, 0x3D))
    validity = tlv(0x30, tlv(not_before[0], not_before[1].encode()) + tlv(not_after[0], not_after[1].encode()))
    tbs = tlv(0x30, tlv(0xA0, tlv(0x02, b"\x02")) + tlv(0x02, b"\x01") + algorithm + issuer + validity + subject
              + tlv(0x30, b"") + ext)                                  # empty subjectPublicKeyInfo
    return tlv(0x30, tbs + algorithm + tlv(0x03, b"\x00"))


def test_describe_certificate():
    org = "Example Interception Proxy " + "x" * 120                      # a long-form DER length
    issuer = name((CN, 0x13, "Proxy Root"), (O, 0x0C, org))
    leaf = der_cert(name((CN, 0x0C, "cdn.cboe.com")), issuer, sans=("cdn.cboe.com", "cdn-api.cboe.com"))
    assert tls.describe_certificate(leaf) == {
        "subject": "cdn.cboe.com", "issuer": f"Proxy Root ({org})", "not_before": "2026-11-05",
        "not_after": "2026-12-31", "names": ["cdn.cboe.com", "cdn-api.cboe.com"], "self_signed": False}
    root = name((CN, 0x1E, "Wurzel"), (O, 0x13, "Wurzel"))                 # BMPString; O equal to CN
    d = tls.describe_certificate(der_cert(root, root, not_before=(0x17, "991231000000Z")))
    assert d["subject"] == d["issuer"] == "Wurzel" and d["self_signed"] and d["not_before"] == "1999-12-31"
    bare = tls.describe_certificate(der_cert(name(), issuer, sans=("a.example", "b.example")))
    assert bare["subject"] == "a.example, b.example"                          # no CN or O: the DNS names
    assert tls.describe_certificate(b"\x30\x82\xff\xff\x30") is None
    assert tls.describe_certificate(b"not a certificate") is None


def test_describe_a_real_root():
    der = ssl.PEM_cert_to_DER_cert(certifi_pem("ISRG Root X1"))
    assert tls.describe_certificate(der) == {
        "subject": "ISRG Root X1 (Internet Security Research Group)",
        "issuer": "ISRG Root X1 (Internet Security Research Group)",
        "not_before": "2015-06-04", "not_after": "2035-06-04", "names": [], "self_signed": True}


# ---------------------------------------------------------------------------- the error text
def verify_error(message: str) -> urllib.error.URLError:
    err = ssl.SSLCertVerificationError(1, f"[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: {message}")
    err.verify_message = message
    return urllib.error.URLError(err)


TRUSTED = tls.Trust(ssl.create_default_context(), "the Windows certificate store (truststore 0.10.5) + certifi")
PROXY_CERT = {"subject": "cdn.cboe.com", "issuer": "Proxy Root (Example Antivirus)", "not_before": "2026-11-01",
              "not_after": "2027-11-01", "names": ["cdn.cboe.com"], "self_signed": False}


def test_explain():
    text = tls.explain(verify_error("unable to get local issuer certificate"), TRUSTED, PROXY_CERT, date(2026, 11, 7))
    assert text.startswith("unable to get local issuer certificate. The certificate presented is for cdn.cboe.com, "
                           "issued by Proxy Root (Example Antivirus). Trusted: the Windows certificate store")
    assert "may be replacing the certificate" in text and "set SSL_CERT_FILE to its root certificate" in text
    old = dict(PROXY_CERT, not_after="2026-11-02")
    assert "but this computer's date is 2026-11-07" in tls.explain(verify_error("certificate has expired"), TRUSTED,
                                                                   old, date(2026, 11, 7))
    bare = tls.Trust(TRUSTED.context, "Python's default CA list", ("truststore", "certifi"))
    text = tls.explain(verify_error("unable to get local issuer certificate"), bare)
    assert "presented" not in text and "pip install -r requirements.txt adds truststore and certifi" in text


# ---------------------------------------------------------------------------- CboeClient on a failed check
def test_cert_failure_is_explained_once_and_fails_fast(tmp_path):
    t = FakeTransport({"cboe.com": [verify_error("self-signed certificate in certificate chain")]})
    seen = []
    c = CboeClient(cache_dir=tmp_path, urlopen=t, min_interval=0, sleep=lambda s: None,
                   read_certificate=lambda url, timeout: seen.append(url) or PROXY_CERT)
    with pytest.raises(CboeUnreachable) as e:
        c.get_chain("SPY")
    msg = str(e.value)
    assert msg.startswith("CBOE unreachable: TLS certificate verification failed for cdn.cboe.com: self-signed "
                          "certificate in certificate chain. The certificate presented is for cdn.cboe.com, issued by "
                          "Proxy Root (Example Antivirus).")
    assert "Trusted: " in msg and len(t.calls) == 1 and seen == [C.CBOE_CHAIN_URL.format(symbol="SPY")]
    with pytest.raises(CboeUnreachable, match="Proxy Root"):              # no retry, no new request
        c.get_index_history("VIX")
    assert len(t.calls) == 1 and len(seen) == 1


def test_injected_transport_reads_no_certificate(tmp_path):
    t = FakeTransport({"cboe.com": [verify_error("unable to get local issuer certificate")]})
    c = CboeClient(cache_dir=tmp_path, urlopen=t, min_interval=0, sleep=lambda s: None)
    with pytest.raises(CboeUnreachable) as e:
        c.get_chain("SPY")
    assert "presented" not in str(e.value) and "unable to get local issuer certificate." in str(e.value)


# ---------------------------------------------------------------------------- a local HTTPS server
def openssl(*args, cwd):
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True, timeout=60)


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    """ca.pem (an authority nobody trusts) and leaf.pem / leaf.key for localhost, signed by it."""
    if not shutil.which("openssl"):
        pytest.skip("the openssl command is not installed")
    d = tmp_path_factory.mktemp("certs")
    (d / "leaf.ext").write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                                "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"
                                "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n", encoding="ascii")
    ec = ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes"]
    try:
        openssl("req", "-x509", *ec, "-keyout", "ca.key", "-out", "ca.pem", "-days", "3",
                "-subj", "/CN=Test Proxy Root/O=Test Interception Proxy",
                "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-addext", "subjectKeyIdentifier=hash", cwd=d)
        openssl("req", *ec, "-keyout", "leaf.key", "-out", "leaf.csr", "-subj", "/CN=localhost", cwd=d)
        openssl("x509", "-req", "-in", "leaf.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
                "-out", "leaf.pem", "-days", "2", "-extfile", "leaf.ext", cwd=d)
    except (subprocess.SubprocessError, OSError) as e:
        pytest.skip(f"openssl could not make test certificates: {e}")
    return d


class QuietHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):                 # a client that leaves after the handshake
        pass


class QuietTCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


class ChainHandler(BaseHTTPRequestHandler):
    seen: list = []

    def do_GET(self):
        type(self).seen.append(self.path)
        data = gzip.compress(json.dumps(synth.oex_payload()).encode("utf-8"))
        self.send_response(200)
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def https_server(certs, monkeypatch):
    """https://localhost:PORT serving the OEX chain with a certificate from the untrusted test authority; no proxy."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certs / "leaf.pem", certs / "leaf.key")
    srv = QuietHTTPServer(("127.0.0.1", 0), ChainHandler)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ChainHandler.seen = []
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    monkeypatch.delenv(tls.CERT_FILE_ENV, raising=False)
    base = f"https://localhost:{srv.server_address[1]}"
    monkeypatch.setattr(C, "CBOE_CHAIN_URL", base + "/api/global/delayed_quotes/options/{symbol}.json")
    yield base
    srv.shutdown()
    srv.server_close()


class ConnectHandler(socketserver.BaseRequestHandler):
    """A CONNECT-only HTTP proxy: records the request line and relays bytes both ways."""

    def handle(self):
        f = self.request.makefile("rb")
        line = f.readline().decode("latin-1").strip()
        while f.readline() not in (b"\r\n", b"\n", b""):
            pass
        self.server.seen.append(line)
        host, port = line.split()[1].rsplit(":", 1)
        upstream = socket.create_connection((host, int(port)), timeout=10)
        self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")

        def pump(src, dst):
            try:
                while data := src.recv(65536):
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                for s in (src, dst):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        back = threading.Thread(target=pump, args=(upstream, self.request), daemon=True)
        back.start()
        pump(self.request, upstream)
        back.join(10)
        upstream.close()


@pytest.fixture
def connect_proxy(monkeypatch):
    srv = QuietTCPServer(("127.0.0.1", 0), ConnectHandler)
    srv.seen = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": f"http://127.0.0.1:{srv.server_address[1]}"})
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    yield srv
    srv.shutdown()
    srv.server_close()


def test_presented_certificate_sends_no_request(https_server):
    cert = tls.presented_certificate(https_server + "/api/global/delayed_quotes/options/OEX.json", timeout=10)
    assert cert["subject"] == "localhost" and cert["names"] == ["localhost"]
    assert cert["issuer"] == "Test Proxy Root (Test Interception Proxy)" and not cert["self_signed"]
    assert ChainHandler.seen == []


def test_presented_certificate_through_a_proxy(https_server, connect_proxy):
    cert = tls.presented_certificate(https_server + "/x.json", timeout=10)
    assert cert["issuer"] == "Test Proxy Root (Test Interception Proxy)"
    assert connect_proxy.seen == [f"CONNECT {https_server[len('https://'):]} HTTP/1.1"]
    assert ChainHandler.seen == []


def test_presented_certificate_unreachable_is_none(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]                                      # nothing listens there
    assert tls.presented_certificate(f"https://127.0.0.1:{port}/x.json", timeout=5) is None


def test_untrusted_server_fails_with_the_presented_issuer(https_server, tmp_path):
    c = CboeClient(cache_dir=tmp_path, min_interval=0, sleep=lambda s: None)
    with pytest.raises(CboeUnreachable) as e:
        c.get_chain("_OEX")
    msg = str(e.value)
    host = https_server[len("https://"):]
    assert msg.startswith(f"CBOE unreachable: TLS certificate verification failed for {host}: ")
    assert "The certificate presented is for localhost, issued by Test Proxy Root (Test Interception Proxy)." in msg
    assert f"Trusted: {c.trust.description}." in msg
    assert ChainHandler.seen == [] and c.stats["network_calls"] == 1


def test_untrusted_server_through_a_proxy(https_server, connect_proxy, tmp_path):
    c = CboeClient(cache_dir=tmp_path, min_interval=0, sleep=lambda s: None)
    with pytest.raises(CboeUnreachable, match=r"issued by Test Proxy Root \(Test Interception Proxy\)"):
        c.get_chain("_OEX")
    assert len(connect_proxy.seen) == 2 and ChainHandler.seen == []   # the request and the certificate read


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="test certificates meet OpenSSL's rules; the Windows and macOS verifiers have their own")
@pytest.mark.parametrize("hide_truststore", [False, True])
def test_ssl_cert_file_makes_the_server_trusted(https_server, certs, monkeypatch, tmp_path, hide_truststore):
    if hide_truststore:
        monkeypatch.setitem(sys.modules, "truststore", None)
    monkeypatch.setenv(tls.CERT_FILE_ENV, str(certs / "ca.pem"))
    c = CboeClient(cache_dir=tmp_path, min_interval=0, sleep=lambda s: None)
    payload = c.get_chain("_OEX")
    assert payload["symbol"] == "_OEX" and ChainHandler.seen == ["/api/global/delayed_quotes/options/_OEX.json"]
    assert f"SSL_CERT_FILE {certs / 'ca.pem'}" in c.trust.description


# ---------------------------------------------------------------------------- the FMP client
def test_fmp_uses_the_same_checks(monkeypatch, tmp_path, no_cert_file):
    import fmp_client
    monkeypatch.setenv("FMP_API_KEY", "test-key-123")
    for name in ("FMP_BASE_URL", "FMP_PATH_PREFIX", "FMP_AUTH_STYLE", "FMP_TLS_VERIFY"):
        monkeypatch.delenv(name, raising=False)
    official = fmp_client.FMPClient(cache_dir=tmp_path)
    assert official._ssl_ctx.verify_mode == ssl.CERT_REQUIRED and official._ssl_ctx.check_hostname
    assert official.describe()["tls_trust"] == official._trust.description
    msg = official._cert_hint(verify_error("unable to get local issuer certificate"))
    assert msg.startswith("HTTPS certificate verification failed for financialmodelingprep.com: unable to get local "
                          "issuer certificate. Trusted: ")
    assert "FMP_TLS_VERIFY" not in msg and "test-key-123" not in msg
    monkeypatch.setenv("FMP_BASE_URL", "https://192.0.2.1")
    monkeypatch.setenv("FMP_PATH_PREFIX", "stable")
    monkeypatch.setenv("FMP_AUTH_STYLE", "header")
    proxy = fmp_client.FMPClient(cache_dir=tmp_path)
    assert proxy._cert_hint(verify_error("self-signed certificate")).endswith(
        "To run anyway, set FMP_TLS_VERIFY=0 (traffic can then be intercepted; see SKILL.md).")
