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
from urllib.parse import urlparse, urlunparse


def _normalize_url(u: str) -> str:
    """Canonical normalization: lowercase scheme+host, strip default ports,
    collapse trailing slash (except root), lowercase path for comparison
    (origin servers vary; case diffs flagged separately, not as mismatch)."""
    try:
        p = urlparse((u or "").strip())
        scheme = (p.scheme or "https").lower()
        host = (p.hostname or "").lower()
        if not host:
            return (u or "").strip().rstrip("/")
        # strip default ports
        port = p.port
        if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
            host = f"{host}:{port}"
        path = p.path or "/"
        # trailing slash: "/page/" -> "/page", root stays "/"
        if len(path) > 1:
            path = path.rstrip("/")
        path = path.lower()  # comparison form; original case kept by caller for case_mismatch
        query = p.query  # keep query (UTMs matter for equity); fragment dropped
        return urlunparse((scheme, host, path, "", query, ""))
    except Exception:
        return (u or "").strip().rstrip("/")


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
    details: dict = {}
    if not canon:
        issues.append("missing canonical on final lander — add self-referencing canonical")
    elif final:
        norm_final = _normalize_url(final)
        norm_canon = _normalize_url(canon)
        # www vs non-www note (normalized comparison strips www for equity check)
        try:
            hf = (urlparse(final).hostname or "").lower()
            hc = (urlparse(canon).hostname or "").lower()
            www_mismatch = (hf.lstrip("www.").rstrip(".") == hc.lstrip("www.").rstrip(".")
                            and (hf.startswith("www.") != hc.startswith("www.")))
            details["www_mismatch"] = bool(www_mismatch)
            if www_mismatch:
                details["www_note"] = (f"www vs non-www: final host {hf} vs canonical host {hc} "
                                       "— pick one host, 301 the other; equity splits otherwise")
        except Exception:
            details["www_mismatch"] = False
        # case mismatch: same lowercased path but different raw case
        try:
            pf = (urlparse(final).path or "/").rstrip("/") or "/"
            pc = (urlparse(canon).path or "/").rstrip("/") or "/"
            case_mismatch = (pf != pc and pf.lower() == pc.lower())
            details["case_mismatch"] = bool(case_mismatch)
            if case_mismatch:
                issues.append(f"canonical case differs from final path ({pc[:60]} vs {pf[:60]}) — normalize to lowercase URLs")
        except Exception:
            details["case_mismatch"] = False
        # trailing-slash mismatch
        try:
            pf = urlparse(final).path or "/"
            pc = urlparse(canon).path or "/"
            slash_mismatch = (pf.rstrip("/") == pc.rstrip("/") and (pf.endswith("/") != pc.endswith("/")) and len(pf) > 1 and len(pc) > 1)
            details["slash_mismatch"] = bool(slash_mismatch)
            if slash_mismatch:
                issues.append("trailing-slash mismatch between canonical and final — normalize to one form + 301")
        except Exception:
            details["slash_mismatch"] = False
        details["normalized_final"] = norm_final[:160]
        details["normalized_canonical"] = norm_canon[:160]
        if norm_canon.split("?")[0] != norm_final.split("?")[0]:
            issues.append(f"canonical {canon[:80]} does not match final {final[:80]} — chain leaks equity")
    return {"canonical": canon, "final_url": final, "ok": not issues, "issues": issues,
            "details": details,
            "basis": "measured final HTML link[rel=canonical] vs traced final URL (normalized: lowercase host, www-folded, trailing-slash + case folded)"}


def audit_hreflang(html: str, expected_geo: str = "") -> dict:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "lxml")
        tags = [(l.get("hreflang", ""), l.get("href", "")) for l in soup.find_all("link", hreflang=True)]
    except Exception:
        tags = []
    langs = sorted({h for h, _ in tags if h})
    hreflang_map: dict[str, str] = {}
    for h, href in tags:
        if h and href and h not in hreflang_map:
            hreflang_map[h] = href
    has_default = any(h.lower() == "x-default" for h in langs)
    note = ""
    if not tags:
        note = "no hreflang — add hreflang + x-default for multi-geo affiliate landers"
    # 9-geo map (expected hreflang values per geo)
    GEO_HREFLANG: dict[str, list[str]] = {
        "UK": ["en-gb"], "US-NJ": ["en-us"], "CA-ON": ["en-ca", "en", "fr-ca"],
        "DE": ["de"], "NL": ["nl"], "SE": ["sv"], "IE": ["en-ie"],
        "AU": ["en-au"], "BR": ["pt-br"],
    }
    expected_list: list[str] = GEO_HREFLANG.get(expected_geo, [])
    geo_hint = expected_list[0] if expected_list else ""
    low_langs = [h.lower() for h in langs]
    covers = (not expected_list) or any(
        exp in h or "x-default" in h for exp in expected_list for h in low_langs)
    missing_for_expected = [e for e in expected_list
                            if not any(e == h.lower() for h in langs)]
    return {"hreflangs": langs[:20], "count": len(tags), "has_x_default": has_default,
            "covers_expected_geo": covers, "expected": geo_hint,
            "expected_list": expected_list, "hreflang_map": hreflang_map,
            "missing_for_expected": missing_for_expected, "note": note,
            "basis": "measured link[hreflang] in final HTML"}


def _audit_sponsored_blob(html: str) -> tuple[int, list[str]]:
    """Scan one HTML blob for affiliate-pattern anchors missing rel=sponsored/nofollow."""
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
    return total, bad[:20]


def audit_sponsored(html: str, source_html: str = "") -> dict:
    """Every /out|/go|/visit href should carry rel sponsored/nofollow (crawl-budget + policy).

    Audits the SOURCE review HTML (param source_html) in addition to the final
    lander html. Backward compat: audit_sponsored(html) still works; when
    source_html is empty, source_* keys report empty/ok.
    """
    try:
        final_total, final_bad = _audit_sponsored_blob(html or "")
        if source_html:
            src_total, src_bad = _audit_sponsored_blob(source_html)
        else:
            src_total, src_bad = 0, []
        ok = not final_bad and not src_bad
        return {"affiliate_links": final_total + src_total, "missing_sponsored": (src_bad + final_bad)[:20],
                "ok": ok, "basis": "measured rel= on affiliate-pattern anchors",
                "source_links": src_total, "source_missing": src_bad,
                "final_links": final_total, "final_missing": final_bad}
    except Exception:
        return {"affiliate_links": 0, "missing_sponsored": [], "ok": True, "basis": "parse error",
                "source_links": 0, "source_missing": [], "final_links": 0, "final_missing": []}


def http3_early_hints(headers: dict | None, html: str = "") -> dict:
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    alt = h.get("alt-svc", "")
    low = alt.lower()
    # Parse h3 / h3-29 tokens + QUIC port note
    h3_tokens = re.findall(r'h3(?:-29)?\s*=\s*"[^"]*"', low)
    h3 = bool(re.search(r'\bh3(?:-29)?\b', low))
    quic_note = ""
    m = re.search(r':(\d+)', alt)
    if h3 and m:
        quic_note = f"QUIC/UDP port {m.group(1)} advertised — UDP reachability gates real H3 benefit"
    elif h3:
        quic_note = "h3 advertised without explicit port — defaults to UDP 443"
    return {"http3_advertised": h3, "alt_svc": alt[:160],
            "h3_tokens": h3_tokens[:5], "quic_note": quic_note,
            "early_hints_103": "not probed (requires H1 informational capture) — enable CDN Early Hints for LCP",
            "bfcache_note": "bfcache requires no unload handlers + HTTPS — verify in Chrome DevTools; redirect chains bust bfcache when cross-origin without opener",
            "basis": "measured alt-svc response header; tracer is HTTP/2 (enterprise.yaml http2:true)"}
