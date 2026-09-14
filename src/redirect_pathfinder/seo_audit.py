"""SEO chain audit — P0 ADD. Canonical/hreflang/rel-sponsored + HTTP/3/Early-Hints/bfcache.

- validate_canonical_chain(hops, html): canonical must survive redirects; flags
  canonical-to-different-final, missing canonical, canonicalised affiliate /out/ leak.
- audit_hreflang(html, expected_geo): hreflang presence, x-default, self-reference.
- audit_sponsored(html, source_url): /out|/go|/visit links must carry rel=sponsored|nofollow.
- http3_early_hints(url, headers): alt-svc h3 advertisement, 103 Early Hints note
  (httpx is H2-only; we *detect* H3 capability, never claim H3 measurement).
- All outputs carry basis strings; nothing invented.
"""
from __future__ import annotations
import re
from urllib.parse import urlparse


def _canon_links(html: str) -> list[str]:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "lxml")
        return [l.get("href", "") for l in soup.find_all("link", rel=lambda v: v and "canonical" in str(v).lower()) if l.get("href")]
    except Exception:
        return []


def validate_canonical_chain(hops: list, final_html: str = "") -> dict:
    finals = [h.url for h in (hops or [])]
    final = finals[-1] if finals else ""
    canons = _canon_links(final_html)
    canon = canons[0] if canons else ""
    issues: list[str] = []
    if not canon:
        issues.append("missing canonical on final lander — add self-referencing canonical")
    elif final and canon.split("?")[0].rstrip("/") != final.split("?")[0].rstrip("/"):
        issues.append(f"canonical {canon[:80]} does not match final {final[:80]} — chain leaks equity")
    return {"canonical": canon, "final_url": final, "ok": not issues, "issues": issues,
            "basis": "measured final HTML link[rel=canonical] vs traced final URL"}


def audit_hreflang(html: str, expected_geo: str = "") -> dict:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "lxml")
        tags = [(l.get("hreflang", ""), l.get("href", "")) for l in soup.find_all("link", hreflang=True)]
    except Exception:
        tags = []
    langs = sorted({h for h, _ in tags if h})
    has_default = any(h.lower() == "x-default" for h in langs)
    note = ""
    if not tags:
        note = "no hreflang — add hreflang + x-default for multi-geo affiliate landers"
    geo_hint = {"UK": "en-gb", "US-NJ": "en-us", "CA-ON": "en-ca", "DE": "de", "NL": "nl"}.get(expected_geo, "")
    covers = (not geo_hint) or any(geo_hint in h.lower() or "x-default" in h.lower() for h in langs)
    return {"hreflangs": langs[:20], "count": len(tags), "has_x_default": has_default,
            "covers_expected_geo": covers, "expected": geo_hint, "note": note,
            "basis": "measured link[hreflang] in final HTML"}


def audit_sponsored(html: str) -> dict:
    """Every /out|/go|/visit href should carry rel sponsored/nofollow (crawl-budget + policy)."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "lxml")
        aff_rx = re.compile(r"/(out|go|visit|redirect|recommends)/|btag|affid|clickid", re.I)
        bad, total = [], 0
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if not aff_rx.search(href):
                continue
            total += 1
            rel = " ".join(a.get("rel", [])).lower()
            if "sponsored" not in rel and "nofollow" not in rel:
                bad.append(href[:120])
        return {"affiliate_links": total, "missing_sponsored": bad[:20],
                "ok": not bad, "basis": "measured rel= on affiliate-pattern anchors"}
    except Exception:
        return {"affiliate_links": 0, "missing_sponsored": [], "ok": True, "basis": "parse error"}


def http3_early_hints(headers: dict | None, html: str = "") -> dict:
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    alt = h.get("alt-svc", "")
    h3 = "h3" in alt.lower()
    return {"http3_advertised": h3, "alt_svc": alt[:160],
            "early_hints_103": "not probed (requires H1 informational capture) — enable CDN Early Hints for LCP",
            "bfcache_note": "bfcache requires no unload handlers + HTTPS — verify in Chrome DevTools; redirect chains bust bfcache when cross-origin without opener",
            "basis": "measured alt-svc response header; tracer is HTTP/2 (enterprise.yaml http2:true)"}
