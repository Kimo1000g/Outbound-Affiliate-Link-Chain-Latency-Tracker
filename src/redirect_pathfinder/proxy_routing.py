"""Multi-geo proxy routing abstraction — geo labels become real exit IPs when configured.

config geo_profiles.<GEO>.proxy_exit = "http://user:pass@host:port" (Bright Data /
Oxylabs / Smartproxy). Empty string = direct connection + explicit warning that
jurisdiction-pinning is unverified. Per-request httpx proxy mount; never log creds.
"""
from __future__ import annotations
import re


def proxy_for_geo(cfg: dict, geo: str) -> str:
    try:
        return ((cfg.get("geo_profiles", {}) or {}).get(geo, {}) or {}).get("proxy_exit", "") or ""
    except Exception:
        return ""


def redacted(proxy: str) -> str:
    if not proxy:
        return ""
    # http://user:pass@host:port -> http://***@host:port
    return re.sub(r"://[^@]*@", "://***@", proxy)


def proxy_mounts(proxy: str) -> dict | None:
    if not proxy:
        return None
    return {"http://": proxy, "https://": proxy}
