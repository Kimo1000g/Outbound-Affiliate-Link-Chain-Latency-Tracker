"""Compliance & license guardian — RG text, badges, age-gate, soft-404 NLP, geo-mismatch.

2026 packs: US-NJ/PA/MI/OH/MA, UK (UKGC/GamStop/ASA), CA-ON (AGCO), DE (GGL), NL (KSA).
Adds: age-gate detection (verify-age interstitials), screenshot-evidence hook note,
and Site Reputation Abuse pattern surfacing (delegated from content_quality).
Empty/blocked pages -> cannot-verify -> non-compliant with explanation (never silent pass).
"""
from __future__ import annotations
import re
from urllib.parse import urlparse
from .models import ChainResult, ComplianceResult

TLD_GEO = {".co.uk": "UK", ".uk": "UK", ".us": "US", ".nj": "US-NJ", ".ca": "CA", ".com": "INTL", ".com.mx": "MX",
           ".de": "DE", ".nl": "NL", ".pa": "US-PA", ".mi": "US-MI", ".oh": "US-OH", ".ma": "US-MA"}

AGE_GATE_RX = re.compile(r"(are you (over )?1[89]|verify (your )?age|date of birth|enter your birth|i am (over )?1[89]|age (verification|gate|check))", re.I)


def _phrase_hit(low: str, phrase: str) -> bool:
    """Word-boundary match for bare numeric codes (fixes '404' matching '14040' etc)."""
    p = (phrase or "").strip().lower()
    if not p:
        return False
    if p == "404" or re.fullmatch(r"\d+", p):
        return bool(re.search(r"\b" + re.escape(p) + r"\b", low))
    return p in low


def detect_soft404(html: str, status: int | None, phrases: list[str]) -> tuple[bool, list[str]]:
    low = (html or "").lower()
    hits = [p for p in (phrases or []) if _phrase_hit(low, p)]
    m_title = re.search(r"<title[^>]*>(.*?)</title>", low, re.DOTALL)
    title = m_title.group(1) if m_title else ""
    if "not found" in title or re.search(r"\b404\b", title):
        if not any(h.lower() == "404" or h.startswith("title:") for h in hits):
            hits.append("title:404/not-found")
    return (len(hits) > 0 and (status == 200 or status is None)), hits


def geo_mismatch_check(final_url: str, expected_geo: str) -> tuple[bool, str]:
    host = (urlparse(final_url).hostname or "").lower()
    detail = ""
    mismatch = False
    eg = expected_geo.upper()
    if eg == "UK" and not (host.endswith(".co.uk") or host.endswith(".uk") or "uk" in host or "begambleaware" in (final_url.lower())):
        if host.endswith(".com") and "uk" not in host:
            mismatch = True
            detail = f"UK click landed on offshore host {host} — verify UKGC-licensed lander"
    if eg in ("US-NJ", "US-PA", "US-MI", "US-OH", "US-MA") and ("uk" in host or host.endswith(".co.uk")):
        mismatch = True
        detail = f"{eg} click landed on UK host {host}"
    if eg == "CA-ON" and host.endswith(".co.uk"):
        mismatch = True
        detail = f"Ontario click landed on UK host {host}"
    if eg == "DE" and host.endswith((".co.uk", ".nl")) and "ggl" not in final_url.lower():
        mismatch = True
        detail = f"DE click landed on non-DE host {host} — verify GGL-licensed lander"
    if eg == "NL" and host.endswith((".co.uk", ".de")) and "ksa" not in final_url.lower():
        mismatch = True
        detail = f"NL click landed on non-NL host {host} — verify KSA-licensed lander"
    return mismatch, detail


def detect_age_gate(html: str) -> tuple[bool, str]:
    blob = (html or "")[:40000]
    m = AGE_GATE_RX.search(blob)
    if m:
        return True, f"age-gate interstitial detected ('{m.group(0)[:60]}')"
    return False, ""


def audit_compliance(chain: ChainResult, html: str, compliance_packs: dict, soft404_phrases: list[str]) -> ChainResult:
    pack = compliance_packs.get(chain.geo, {}) or {}
    required = pack.get("required_strings", [])
    badges = pack.get("license_badges", [])
    low = (html or "").lower()
    req_map = {s: (s.lower() in low) for s in required}
    badge_map = {b: (b.lower() in low) for b in badges}
    soft, hits = detect_soft404(html, chain.final_status, soft404_phrases)
    mm, detail = geo_mismatch_check(chain.final_url, chain.geo)
    age_hit, age_detail = detect_age_gate(html)
    cq = getattr(chain, "content_quality", {}) or {}
    sra = bool(cq.get("site_reputation_abuse_suspect"))
    sra_detail = str(cq.get("site_reputation_detail", ""))
    compliant = all(req_map.values()) and not soft and not mm
    if not html:
        compliant = False
    chain.compliance = ComplianceResult(geo=chain.geo, required_strings=req_map,
                                        license_badges=badge_map, soft404_detected=soft,
                                        soft404_phrases_hit=hits, geo_mismatch=mm,
                                        geo_mismatch_detail=detail, compliant=compliant,
                                        age_gate_detected=age_hit, age_gate_detail=age_detail,
                                        site_reputation_abuse_suspect=sra,
                                        site_reputation_detail=sra_detail)
    if soft:
        chain.chain_ok = False
        chain.broken_reason = f"soft-404/expired-offer: {', '.join(hits[:3])}"
    return chain
