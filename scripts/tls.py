"""
HTTPS certificate checks for the CBOE and FMP clients. Verification of the certificate chain and the host name is
always on; nothing in this module turns it off for a request.

trust() -> Trust(context, description, missing): a new SSLContext and what it trusts.
- With truststore (requirements.txt) the operating system verifies, as a browser does: the Windows certificate
  store, which downloads a missing root on demand, the macOS keychain, or the system CA file on Linux. Without it
  Python uses its default list, which on Windows holds only the roots Windows has already downloaded, so a site whose
  root is not among them fails with "unable to get local issuer certificate".
- certifi's copy of the Mozilla root list is added when it is installed (on Windows and macOS truststore uses it
  when the system check fails; on Linux the two are merged).
- SSL_CERT_FILE, when set, names one more PEM file to trust: the root certificate of a company proxy or an antivirus
  product that inspects HTTPS.
Each client builds its own context and uses it from one thread (truststore before 0.10.5 is not thread-safe).

presented_certificate(url, timeout) -> {subject, issuer, not_before, not_after, names, self_signed} or None: the
certificate that the host presents on this network, through the proxy settings urllib uses. It is read from a TLS
handshake whose connection is closed before any HTTP request is sent; nothing from it is used except in the error
message, which then shows who issued the certificate (a public authority, or a proxy that replaces it).

explain(exc, trust, cert=None) -> the text after "certificate verification failed for HOST: ": the verifier's
reason, the certificate presented, what was trusted and the next step.
"""
from __future__ import annotations

import http.client
import os
import platform
import ssl
import urllib.request
from dataclasses import dataclass
from datetime import date

CERT_FILE_ENV = "SSL_CERT_FILE"
_STORES = {"Windows": "the Windows certificate store", "Darwin": "the macOS keychain"}


@dataclass(frozen=True)
class Trust:
    context: ssl.SSLContext
    description: str                  # e.g. "the Windows certificate store (truststore 0.10.5) + certifi 2026.10.23"
    missing: tuple[str, ...] = ()     # packages of requirements.txt that are not installed


def trust() -> Trust:
    """A new SSLContext that verifies the certificate chain and the host name (see the module docstring)."""
    parts, missing = [], []
    try:
        import truststore
    except ImportError:
        truststore = None
    if truststore is not None:
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        store = _STORES.get(platform.system(), "the system CA certificates")
        parts.append(f"{store} (truststore {getattr(truststore, '__version__', '?')})")
    else:
        ctx = ssl.create_default_context()
        parts.append("Python's default CA list")
        missing.append("truststore")
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
        parts.append(f"certifi {getattr(certifi, '__version__', '?')}")
    except ImportError:
        missing.append("certifi")
    except (OSError, ssl.SSLError) as e:
        parts.append(f"certifi (unreadable: {e.__class__.__name__})")
    extra = os.environ.get(CERT_FILE_ENV, "").strip()
    if extra:
        try:
            ctx.load_verify_locations(cafile=extra)
            parts.append(f"{CERT_FILE_ENV} {extra}")
        except (OSError, ssl.SSLError, ValueError) as e:
            parts.append(f"{CERT_FILE_ENV} {extra} (not loaded: {e.__class__.__name__})")
    if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:
        raise RuntimeError("an HTTPS context without certificate verification")
    return Trust(ctx, " + ".join(parts), tuple(missing))


# ---------------------------------------------------------------------- the certificate a host presents
class _Presented(Exception):
    def __init__(self, der):
        super().__init__("certificate read")
        self.der = der


class _ReadCertificate(http.client.HTTPSConnection):
    """Connects (TCP, CONNECT through a proxy, TLS handshake), keeps the server's certificate and closes the
    connection before http.client sends the request."""

    def connect(self):
        super().connect()
        der = self.sock.getpeercert(binary_form=True)
        self.close()
        raise _Presented(der)


class _ReadCertificateHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_ReadCertificate, req, context=self._context)


def presented_certificate(url: str, timeout: float = 15.0) -> dict | None:
    """The certificate `url`'s host presents on this network (see the module docstring); None if unreadable."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE                # read only: no request is sent over this connection
    opener = urllib.request.build_opener(_ReadCertificateHandler(context=ctx))
    try:
        opener.open(urllib.request.Request(url, method="HEAD"), timeout=timeout)
    except _Presented as p:
        return describe_certificate(p.der) if p.der else None
    except (OSError, http.client.HTTPException, ValueError):
        return None
    return None


# ---------------------------------------------------------------------- reading a DER certificate
_CN, _O, _SAN = b"\x55\x04\x03", b"\x55\x04\x0a", b"\x55\x1d\x11"


def _tlv(buf: bytes, i: int, end: int) -> tuple[int, int, int]:
    """(tag, start, end) of the value of the DER element at i."""
    if i + 2 > end:
        raise ValueError("truncated DER")
    tag, n = buf[i], buf[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        if not 1 <= k <= 4 or i + k > end:
            raise ValueError("bad DER length")
        n = int.from_bytes(buf[i:i + k], "big")
        i += k
    if i + n > end:
        raise ValueError("truncated DER")
    return tag, i, i + n


def _items(buf: bytes, start: int, end: int) -> list[tuple[int, int, int]]:
    out, i = [], start
    while i < end:
        tag, s, e = _tlv(buf, i, end)
        out.append((tag, s, e))
        i = e
    return out


def _text(buf: bytes, tag: int, s: int, e: int) -> str:
    if tag == 0x1E:                                                   # BMPString
        return buf[s:e].decode("utf-16-be", "replace")
    if tag == 0x1C:                                                   # UniversalString
        return buf[s:e].decode("utf-32-be", "replace")
    return buf[s:e].decode("utf-8", "replace")                        # UTF8String, PrintableString, IA5String


def _name(buf: bytes, s: int, e: int) -> str:
    """'CN (O)' of an X.501 name, or whichever of the two it has; '' with neither."""
    found = {}
    for _, s1, e1 in _items(buf, s, e):                               # RelativeDistinguishedName
        for _, s2, e2 in _items(buf, s1, e1):                         # AttributeTypeAndValue
            parts = _items(buf, s2, e2)
            if len(parts) == 2 and parts[0][0] == 0x06:
                oid = buf[parts[0][1]:parts[0][2]]
                if oid in (_CN, _O) and oid not in found:
                    found[oid] = _text(buf, *parts[1])
    cn, org = found.get(_CN), found.get(_O)
    if cn and org and org != cn:
        return f"{cn} ({org})"
    return cn or org or ""


def _date(buf: bytes, tag: int, s: int, e: int) -> str:
    t = buf[s:e].decode("ascii", "replace")
    if tag == 0x17:                                                   # UTCTime YYMMDDhhmm[ss]Z
        yy = int(t[:2])
        return f"{(1900 if yy >= 50 else 2000) + yy}-{t[2:4]}-{t[4:6]}"
    return f"{t[:4]}-{t[4:6]}-{t[6:8]}"                               # GeneralizedTime YYYYMMDDhhmm[ss]Z


def _dns_names(buf: bytes, s: int, e: int) -> list[str]:
    """dNSName entries of the subjectAltName extension, from the content of the [3] extensions field."""
    names = []
    for _, s1, e1 in _items(buf, s, e):                               # Extensions (SEQUENCE OF Extension)
        for _, s2, e2 in _items(buf, s1, e1):                         # Extension: OID, critical?, OCTET STRING
            parts = _items(buf, s2, e2)
            if parts and parts[0][0] == 0x06 and buf[parts[0][1]:parts[0][2]] == _SAN and parts[-1][0] == 0x04:
                _, gs, ge = _tlv(buf, parts[-1][1], parts[-1][2])    # GeneralNames
                names += [buf[a:b].decode("ascii", "replace") for t, a, b in _items(buf, gs, ge) if t == 0x82]
    return names


def describe_certificate(der: bytes) -> dict | None:
    """{subject, issuer, not_before, not_after, names, self_signed} of a DER X.509 certificate; None if unreadable.
    subject falls back to the DNS names when the certificate has no common name or organisation."""
    try:
        _, cs, ce = _tlv(der, 0, len(der))
        _, ts, te = _tlv(der, cs, ce)                                 # TBSCertificate
        fields = _items(der, ts, te)
        if fields and fields[0][0] == 0xA0:                           # [0] version
            fields = fields[1:]
        _serial, _algorithm, issuer, validity, subject = fields[:5]
        not_before, not_after = _items(der, validity[1], validity[2])[:2]
        names = []
        for tag, s, e in fields[5:]:
            if tag == 0xA3:
                names = _dns_names(der, s, e)
        return {"subject": _name(der, subject[1], subject[2]) or ", ".join(names[:3]) or "(no name)",
                "issuer": _name(der, issuer[1], issuer[2]) or "(no name)",
                "not_before": _date(der, *not_before), "not_after": _date(der, *not_after), "names": names[:5],
                "self_signed": der[subject[1]:subject[2]] == der[issuer[1]:issuer[2]]}
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------- the error text
def reason(exc) -> str:
    """The verifier's reason ("unable to get local issuer certificate", "Hostname mismatch, ...")."""
    r = getattr(exc, "reason", exc)
    text = getattr(r, "verify_message", None) or str(r) or r.__class__.__name__
    return text.strip().rstrip(".")


def explain(exc, trusted: Trust, cert: dict | None = None, today: date | None = None) -> str:
    """The reason, the certificate presented (when read), what was trusted and the next step."""
    out = [reason(exc) + "."]
    out_of_date = False
    if cert:
        line = f"The certificate presented is for {cert['subject']}, issued by {cert['issuer']}"
        if cert.get("self_signed"):
            line += " (self-signed)"
        day = (today or date.today()).isoformat()
        out_of_date = not cert["not_before"] <= day <= cert["not_after"]
        if out_of_date:
            line += f", valid {cert['not_before']} to {cert['not_after']}, but this computer's date is {day}"
        out.append(line + ".")
    out.append(f"Trusted: {trusted.description}.")
    if trusted.missing:
        out.append(f"Install the requirements (python -m pip install -r requirements.txt adds "
                   f"{' and '.join(trusted.missing)}), then try again.")
    elif out_of_date:
        out.append("Correct this computer's date and time, then try again.")
    else:
        out.append("Something between this computer and the server (a company proxy, antivirus HTTPS scanning, "
                   "a VPN or a network accelerator) may be replacing the certificate. If you trust it, set "
                   f"{CERT_FILE_ENV} to its root certificate (a PEM file); see README, Troubleshooting.")
    return " ".join(out)
