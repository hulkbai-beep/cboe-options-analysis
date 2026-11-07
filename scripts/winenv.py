"""
Environment variables saved with `setx` on Windows, for processes started before the `setx`.

load_user_env() fills the missing FMP_*, MASSIVE_* and OA_* variables and SSL_CERT_FILE from the registry
(HKCU\\Environment, then the machine environment written by `setx /M`). ui/server.py, scripts/analyze.py,
scripts/snapshot.py, scripts/cboe_client.py and scripts/massive_client.py call it at start-up, so a terminal or a
scheduled task that predates `setx` still sees the values. It does nothing on macOS and Linux.
"""
from __future__ import annotations

import os

ENV_NAMES = ("FMP_API_KEY", "FMP_BASE_URL", "FMP_PATH_PREFIX", "FMP_AUTH_STYLE", "FMP_TLS_VERIFY",
             "FMP_MAX_PER_MIN", "FMP_TIMEOUT", "FMP_CACHE_DIR", "MASSIVE_API_KEY", "MASSIVE_MAX_PER_MIN",
             "MASSIVE_MAX_WAIT", "MASSIVE_TIMEOUT", "MASSIVE_CACHE_DIR", "OA_DATA_DIR", "OA_CACHE_DIR",
             "SSL_CERT_FILE")


def load_user_env(names=ENV_NAMES) -> list[str]:
    """Fill missing variables from the Windows registry (values saved with setx). Returns the names loaded."""
    if os.name != "nt":
        return []
    import winreg
    roots = ((winreg.HKEY_CURRENT_USER, r"Environment"),
             (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"))
    loaded = []
    for name in names:
        if os.environ.get(name):
            continue
        for root, subkey in roots:
            try:
                with winreg.OpenKey(root, subkey) as key:
                    value, kind = winreg.QueryValueEx(key, name)
            except OSError:
                continue
            if kind == winreg.REG_EXPAND_SZ:
                value = winreg.ExpandEnvironmentStrings(value)
            os.environ[name] = str(value)
            loaded.append(name)
            break
    return loaded
