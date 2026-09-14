"""Live EasyList fetch (weekly cache) + Safari ITP / Firefox ETP simulation.

Old behaviour: hardcoded track.adnetwork.com list — stale blocklists are worse
than none. New: fetch EasyList over HTTP, parse ||domain^ rules, cache to
output/.easylist.cache (TTL 168h), fall back to embedded minimal list offline.
ITP: Safari strips known tracker query params (gclid/fbclid/...) on cross-site
bounce; Firefox ETP + Brave do similar. Simulate which tracked params would not
survive a Safari bounce so publishers see real-world attribution loss.
"""
from __future__ import annotations
import re
import time
from pathlib import Path
from urllib.parse import urlparse

EASYLIST_URL = "https://easylist.to/easylist/easylist.txt"
CACHE_TTL_S = 168 * 3600
FALLBACK_HOSTS = ["doubleclick.net", "googlesyndication.com", "googleadservices.com",
                  "amazon-adsystem.com", "track.", "/track", "cellxpert", "incomeaccess"]
ITP_DEFAULT_STRIPPED = ["gclid", "fbclid", "msclkid", "dclid", "ttclid", "twclid", "igshid", "mc_eid"]

_cache: dict = {"hosts": [], "ts": 0.0}


def _cache_path() -> Path:
    return Path("output/.easylist.cache")


def parse_easylist_hosts(text: str, cap: int = 4000) -> list[str]:
    hosts: list[str] = []
    for line in (text or "").splitlines()[:200000]:
        line = line.strip()
        if line.startswith("||") and "^" in line:
            dom = line[2:].split("^")[0].split("/")[0].strip()
            if dom and "*" not in dom and len(dom) < 80:
                hosts.append(dom.lower())
        if len(hosts) >= cap:
            break
    # keep ad/tracking-relevant subset + generic substrings
    return sorted(set(hosts))


async def load_easylist(client=None) -> list[str]:
    """Fetch + cache; never raise — returns fallback list offline."""
    global _cache
    if _cache["hosts"] and time.time() - _cache["ts"] < CACHE_TTL_S:
        return _cache["hosts"]
    cp = _cache_path()
    try:
        if cp.exists() and time.time() - cp.stat().st_mtime < CACHE_TTL_S:
            hosts = [l.strip() for l in cp.read_text(encoding="utf-8").splitlines() if l.strip()]
            if hosts:
                _cache = {"hosts": hosts, "ts": time.time()}
                return hosts
    except Exception:
        pass
    try:
        import httpx
        c = client or httpx.AsyncClient(timeout=15)
        own = client is None
        try:
            r = await c.get(EASYLIST_URL, headers={"User-Agent": "iGaming-Pathfinder/2.0"})
            if r.status_code == 200 and len(r.text) > 50000:
                hosts = parse_easylist_hosts(r.text)
                try:
                    cp.parent.mkdir(parents=True, exist_ok=True)
                    cp.write_text("\n".join(hosts[:4000]), encoding="utf-8")
                except Exception:
                    pass
                _cache = {"hosts": hosts, "ts": time.time()}
                return hosts
        finally:
            if own:
                try:
                    await c.aclose()
                except Exception:
                    pass
    except Exception:
        pass
    return list(FALLBACK_HOSTS)


def match_blocked(url: str, hosts: list[str]) -> str:
    """Return matched block rule or '' — substring/domain-suffix match."""
    try:
        host = (urlparse(url).hostname or "").lower()
        path = url.lower()
        for h in hosts:
            if not h:
                continue
            if h.startswith("/") or h.endswith("/"):
                if h.strip("/") in path:
                    return h
            elif h in host or host.endswith("." + h) or h in path:
                # only count tracking-ish contexts to avoid FP on publisher CDN
                if any(t in (host + path) for t in ("track", "ads", "doubleclick", "syndication", "affiliat", "pixel", "beacon", "cellxpert", "incomeaccess", "everflow", "netrefer")):
                    return h
        # structural fallback: dedicated tracking subdomains
        if host.startswith("track.") or "doubleclick" in host or "syndication" in host:
            return "structural:tracking-subdomain"
        return ""
    except Exception:
        return ""


def simulate_itp_strip(params: dict, itp_list: list[str] | None = None) -> list[str]:
    low = {str(k).lower() for k in (params or {})}
    lst = [x.lower() for x in (itp_list or ITP_DEFAULT_STRIPPED)]
    return sorted(k for k in low if k in lst)
