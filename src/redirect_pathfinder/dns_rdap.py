"""Authoritative DNS-over-HTTPS + RDAP hosting attribution (shared by tracer + autofill).

Why: asyncio loop.getaddrinfo measures the LOCAL resolver, not the authoritative
answer. DoH to Cloudflare/Google gives a real, comparable DNS number, and RDAP
gives ASN/org so per-hop IP attribution is verifiable instead of blank.
All network calls are best-effort with short timeouts — never fail a trace.
"""
from __future__ import annotations
import ipaddress
import time
import httpx

DOH_ENDPOINTS = [
    "https://cloudflare-dns.com/dns-query",
    "https://dns.google/resolve",
]
_RDAP_CACHE: dict[str, dict] = {}


async def doh_lookup(host: str, client: httpx.AsyncClient | None = None, timeout_s: float = 5.0) -> tuple[str, float, str]:
    """Return (ip, ms, resolver). Tries Cloudflare DoH JSON then Google DoH JSON."""
    if not host:
        return "", 0.0, "none"
    own = client is None
    c = client or httpx.AsyncClient(timeout=timeout_s)
    try:
        t0 = time.perf_counter()
        try:
            r = await c.get("https://cloudflare-dns.com/dns-query",
                            params={"name": host, "type": "A"},
                            headers={"Accept": "application/dns-json"})
            if r.status_code == 200:
                ans = (r.json().get("Answer") or [])
                for a in ans:
                    if a.get("type") == 1 and a.get("data"):
                        return a["data"], (time.perf_counter() - t0) * 1000.0, "doh-cloudflare"
        except Exception:
            pass
        t1 = time.perf_counter()
        try:
            r = await c.get("https://dns.google/resolve", params={"name": host, "type": "A"})
            if r.status_code == 200:
                ans = (r.json().get("Answer") or [])
                for a in ans:
                    if a.get("type") == 1 and a.get("data"):
                        return a["data"], (time.perf_counter() - t1) * 1000.0, "doh-google"
        except Exception:
            pass
        return "", (time.perf_counter() - t0) * 1000.0, "doh-failed"
    finally:
        if own:
            try:
                await c.aclose()
            except Exception:
                pass


def _rdap_url_for_ip(ip: str) -> str:
    try:
        v = ipaddress.ip_address(ip)
        if v.is_private or v.is_loopback:
            return ""
    except Exception:
        return ""
    # ARIN RDAP works for all RIRs via referral; RIPE direct as fallback handled by caller cache
    return f"https://rdap.arin.net/registry/ip/{ip}"


async def rdap_org(ip: str, client: httpx.AsyncClient | None = None, timeout_s: float = 6.0) -> dict:
    """Return {asn, org, network} — cached in-process. Real RDAP, graceful fallback."""
    if not ip or ip in _RDAP_CACHE:
        return _RDAP_CACHE.get(ip, {})
    url = _rdap_url_for_ip(ip)
    if not url:
        return {}
    own = client is None
    c = client or httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)
    try:
        out: dict = {}
        try:
            r = await c.get(url, headers={"Accept": "application/rdap+json"})
            if r.status_code == 200:
                j = r.json()
                out["network"] = (j.get("name") or "")
                entities = j.get("entities") or []
                for ent in entities:
                    vcard = ent.get("vcardArray", [])
                    if isinstance(vcard, list) and len(vcard) > 1:
                        for row in vcard[1]:
                            if isinstance(row, list) and row and row[0] == "fn":
                                out["org"] = str(row[3]) if len(row) > 3 else ""
                                break
                    if out.get("org"):
                        break
                # ASN sometimes in remarks / links — best effort
                for remark in (j.get("remarks") or []):
                    desc = " ".join(remark.get("description") or [])
                    if "AS" in desc:
                        out["asn"] = desc[:40]
                        break
        except Exception:
            pass
        if not out:
            # RIPE fallback for EU ranges
            try:
                r = await c.get(f"https://rdap.db.ripe.net/ip/{ip}", headers={"Accept": "application/rdap+json"})
                if r.status_code == 200:
                    j = r.json()
                    out["network"] = j.get("name", "")
                    out["org"] = j.get("handle", "")
            except Exception:
                pass
        _RDAP_CACHE[ip] = out
        return out
    finally:
        if own:
            try:
                await c.aclose()
            except Exception:
                pass
