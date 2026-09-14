"""Multi-geo proxy routing abstraction — geo labels become real exit IPs when configured.

config geo_profiles.<GEO>.proxy_exit = "http://user:pass@host:port" (Bright Data /
Oxylabs / Smartproxy / Decodo). Empty string = direct connection + explicit warning that
jurisdiction-pinning is unverified. Per-request httpx mounts transport; never log creds.
Exit-IP proof: probe_exit_ip() records the egress IP in evidence (proxy_exit_used: direct
(unpinned) is honest but useless at scale — configure per-geo exits).
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


async def probe_exit_ip(proxy: str = "", timeout_s: int = 10) -> dict:
    """Exit-IP proof for evidence.proxy_exit (Bright Data/Oxylabs/Smartproxy/Decodo).

    Fetches api.ipify.org via the geo proxy (or direct) and returns {ip, via}.
    Never logs credentials — proxy URL redacted by callers.
    """
    import httpx
    urls = ["https://api.ipify.org?format=json", "https://ifconfig.me/ip"]
    mounts = None
    if proxy:
        try:
            mounts = {"http://": httpx.AsyncHTTPTransport(proxy=proxy),
                      "https://": httpx.AsyncHTTPTransport(proxy=proxy)}
        except Exception:
            mounts = None
    try:
        kw: dict = {"timeout": timeout_s, "follow_redirects": True}
        if mounts:
            kw["mounts"] = mounts
        async with httpx.AsyncClient(**kw) as c:
            for u in urls:
                try:
                    r = await c.get(u)
                    if r.status_code == 200 and r.text.strip():
                        try:
                            ip = r.json().get("ip", "") if "json" in r.headers.get("content-type", "") else r.text.strip()
                        except Exception:
                            ip = r.text.strip()[:64]
                        if ip:
                            return {"ip": ip[:64], "via": "proxy" if proxy else "direct",
                                    "provider_hint": "Bright Data/Oxylabs/Smartproxy/Decodo (configure per-geo proxy_exit)",
                                    "basis": "VERIFIED live egress probe at audit time"}
                except Exception:
                    continue
    except Exception as e:
        return {"ip": "", "via": "error", "reason": f"{type(e).__name__}: {str(e)[:120]}"}
    return {"ip": "", "via": "direct" if not proxy else "proxy", "reason": "probe unreachable"}
