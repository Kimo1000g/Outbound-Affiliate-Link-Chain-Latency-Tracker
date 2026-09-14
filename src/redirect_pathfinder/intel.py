"""Bot-mitigation & anti-evasion helpers + adblock / deeplink / brand / offer auditors."""
from __future__ import annotations
import hashlib
import re
from urllib.parse import urlparse

ADBLOCK_TRACKING_HINTS = ["track.", "/track", "affiliate", "click", "pixel", "beacon", "syndication", "doubleclick"]


def spoof_headers(device: str = "desktop_chrome") -> dict:
    """2026-current fingerprint set: Chrome 132 / Safari 18.4, Sec-CH-UA-Platform, br encoding, GPC-aware."""
    from .tls_client import ROTATING_UAS
    mobile = "mobile" in device
    sec_ch = ('"Chromium";v="132", "Google Chrome";v="132", "Not-A.Brand";v="99"'
              if not mobile else '"Chromium";v="132", "Android WebView";v="132"')
    platform = '"Android"' if mobile else '"Windows"'
    # Deterministic rotation keyed by device hash (reproducible runs — no unseeded random)
    _langs = ["en-US,en;q=0.9", "en-GB,en;q=0.9", "en-CA,en;q=0.8", "de-DE,de;q=0.8,en;q=0.7"]
    _idx = int(hashlib.md5(device.encode("utf-8")).hexdigest(), 16) % len(_langs)
    lang = _langs[_idx]
    return {
        "User-Agent": ROTATING_UAS.get(device, ROTATING_UAS["desktop_chrome"]),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/png,*/*;q=0.8",
        "Accept-Language": lang,
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": sec_ch,
        "Sec-Ch-Ua-Mobile": "?1" if mobile else "?0",
        "Sec-Ch-Ua-Platform": platform,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-GPC": "1",  # Global Privacy Control — lets us detect consent-mode behaviour
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }


def tcf_and_consent_v2(html: str) -> dict:
    """Consent Mode v2 + TCF 2.3 string parse (TCF 2.3 mandatory Mar 1 2026; 2.2 strings dropped)."""
    blob = (html or "")[:120000]
    low = blob.lower()
    # TCF consent string (base64url-ish long token in cookie/localStorage/JS)
    import re as _re
    tcf_hit = bool(_re.search(r"euconsent-v2|addtlConsent|__tcfapi|tcfapi|consent-string", low))
    gtag_consent = "gtag('consent" in low or 'gtag("consent' in low
    ad_storage = bool(_re.search(r"ad_storage['\"]?\s*:\s*['\"]?(granted|denied)", low))
    analytics_storage = bool(_re.search(r"analytics_storage['\"]?\s*:\s*['\"]?(granted|denied)", low))
    s2s = bool(_re.search(r"server-side|server side|s2s|gtm.*server|stape|addingwell", low))
    # TCF version digit: look for explicit 2.2/2.3 markers near consent tokens
    ver = ""
    m = _re.search(r"tcf[\s_-]*v?(2\.[23])|tcf[\s_-]*2\.[23]|version[\s'\"]*[:=][\s'\"]*(2\.[23])", low)
    if m:
        ver = next((g for g in m.groups() if g), "")
    if not ver:
        # vendordisclosed / publisherCC + 2.3-only GVL signals imply 2.3; bare euconsent-v2 implies 2.2-era
        has_vendor_disclosed = bool(_re.search(r"vendordisclosed|vendor.?disclosed|publishercc|purposeone|gdprApplies", low))
        if _re.search(r"\b2\.3\b", blob):
            ver = "2.3"
        elif _re.search(r"\b2\.2\b", blob):
            ver = "2.2"
        elif has_vendor_disclosed:
            ver = "2.3"
        elif tcf_hit:
            ver = "2.2"
    vendor_disclosed = bool(_re.search(r"vendordisclosed|vendor.?disclosed", low))
    if ver == "2.3":
        tcf_version = "2.3"
    elif ver == "2.2":
        tcf_version = "2.2-deprecated"
    else:
        tcf_version = "unknown"
    note = ("Consent Mode v2 ad_storage/analytics_storage + TCF 2.3 required for CAPI/S2S postback "
            "attribution as 3P cookies die; affiliate _epc_map CSV will break without S2S validation. "
            "TCF 2.3 mandatory Mar 1 2026 — 2.2 strings are dropped by vendors.")
    if tcf_version == "2.2-deprecated":
        note += " This page signals TCF 2.2 — upgrade CMP to 2.3 before Mar 1 2026 cutoff."
    return {"tcf2_present": tcf_hit, "consent_mode_v2": gtag_consent,
            "ad_storage_signal": ad_storage, "analytics_storage_signal": analytics_storage,
            "s2s_gtm_hint": s2s,
            "tcf_version": tcf_version, "tcf_version_detected": ver or "unknown",
            "vendor_disclosed_present": vendor_disclosed,
            "note": note,
            "basis": "measured static HTML/JS markers — confirm in DevTools Application tab"}


def detect_consent_and_gpc(html: str, headers: dict | None = None) -> dict:
    """Consent-mode / CMP + GPC detection (missing in 2026 builds)."""
    blob = ((html or "")[:60000]).lower()
    cmp_hits = [k for k in ("onetrust", "cookiebot", "quantcast", "trustarc", "didomi", "sourcepoint",
                            "__tcfapi", "consentmanager", "osano", "gtag('consent", "dataLayer".lower())
                if k in blob]
    gpc_ack = False
    try:
        h = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
        gpc_ack = "gpc" in " ".join(h.keys()) or "1" in h.get("sec-gpc", "")
    except Exception:
        pass
    gtm = "googletagmanager" in blob or "gtag(" in blob
    return {"cmp_detected": bool(cmp_hits), "cmp_vendors": cmp_hits[:6],
            "gpc_signal_sent": True, "gpc_acknowledged": gpc_ack,
            "gtm_present": gtm,
            "note": "GPC=1 + DNT=1 sent on every trace; CMP presence gates consent-mode analytics claims"}


async def audit_adblock(chain, easylist_hosts: list | None = None, itp_list: list | None = None) -> object:
    """Live EasyList + structural + Safari-ITP simulation. Never hardcoded-only."""
    from .easylist import load_easylist, match_blocked, simulate_itp_strip
    hosts = easylist_hosts if easylist_hosts is not None else await load_easylist()
    blocked: list[str] = []
    for h in chain.hops:
        m = match_blocked(h.url, hosts)
        if m:
            blocked.append(f"{(urlparse(h.url).hostname or h.url)} [{m}]")
    chain.adblock_blocked_hosts = sorted(set(blocked))
    chain.adblock_vulnerable = len(chain.adblock_blocked_hosts) > 0
    chain.adblock_basis = (f"EasyList live ({len(hosts)} rules, weekly cache) + structural tracking-subdomain heuristic"
                           if hosts else "fallback list (offline) + structural heuristic")
    # ITP: simulate Safari stripping on final hop params
    try:
        final_params = chain.hops[-1].params if chain.hops and chain.hops[-1].params else {}
        if not final_params:
            from .rule_engine import extract_params
            final_params = extract_params(chain.final_url)
        chain.itp_stripped_simulated = simulate_itp_strip(final_params, itp_list)
    except Exception:
        chain.itp_stripped_simulated = []
    return chain


def inspect_deeplink(chain, html: str = "") -> object:
    """Detect app-store / deep-link routing quality on mobile chains."""
    blob = (" ".join(h.url for h in chain.hops) + " " + (html or "")[:20000]).lower()
    has_universal = "applinks" in blob or "apple-app-site-association" in blob or "intent://" in blob
    has_store = "apps.apple.com" in blob or "play.google.com" in blob
    has_scheme = bool(re.search(r"(sportsbook|casino|betmgm|draftkings|bet365)[a-z]*://", blob))
    if "mobile" not in chain.device:
        verdict = "n/a-desktop"
    elif has_scheme or has_universal:
        verdict = "deep-link-ok"
    elif has_store:
        verdict = "falls-back-to-store"
    else:
        verdict = "web-only-no-deeplink"
    chain.deeplink = {"verdict": verdict, "has_app_scheme": has_scheme,
                      "has_store_fallback": has_store, "has_universal_link": has_universal}
    return chain


def brand_alignment(chain, html: str = "") -> object:
    """Ensure review-page brand matches final lander brand (broken operator redirect guard)."""
    expected = (chain.expected_operator or "").lower()
    text = ((html or "")[:30000]).lower() + " " + chain.final_url.lower()
    found = bool(expected) and expected.replace(" ", "") in text.replace(" ", "")
    rivals = ["bet365", "draftkings", "fanduel", "betmgm", "caesars", "williamhill", "unibet", "888"]
    rivals_hit = [r for r in rivals if r in text.replace(" ", "") and r != expected.replace(" ", "")]
    chain.brand_alignment = {"expected_brand": chain.expected_operator, "brand_found_on_lander": found,
                             "rival_brands_detected": rivals_hit,
                             "aligned": found and not rivals_hit if expected else True}
    return chain


BONUS_RX = re.compile(r"(deposit|bet|wager)\s*\$?\s*(\d+)[^$]{0,30}?get\s*\$?\s*(\d+)", re.IGNORECASE)


def _parse_bonus(s: str) -> tuple:
    m = BONUS_RX.search(s or "")
    return (m.group(2), m.group(3)) if m else (None, None)


def offer_discrepancy(chain) -> object:
    """Compare on-site bonus pitch vs lander reality — trust + ASA compliance guard."""
    site = _parse_bonus(chain.bonus_text_on_site or "")
    lander_text = chain.offer_discrepancy.get("lander_bonus_text", "") if isinstance(chain.offer_discrepancy, dict) else ""
    lander = _parse_bonus(lander_text)
    if not site or not lander:
        chain.offer_discrepancy = {**(chain.offer_discrepancy or {}), "comparable": False,
                                   "mismatch": False, "detail": "insufficient bonus text to compare"}
    else:
        mismatch = site != lander
        chain.offer_discrepancy = {**(chain.offer_discrepancy or {}), "comparable": True, "mismatch": mismatch,
                                   "site_offer": f"{site[0]}→{site[1]}", "lander_offer": f"{lander[0]}→{lander[1]}",
                                   "detail": "MATCH" if not mismatch else f"Site {site} ≠ Lander {lander}"}
    return chain


def extract_lander_bonus(html: str) -> str:
    m = BONUS_RX.search(html or "")
    return m.group(0) if m else ""
