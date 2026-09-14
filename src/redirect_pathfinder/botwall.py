"""WAF / bot-wall scoring — coarse boolean replaced with evidence-graded score.

Signals: Cloudflare challenge cookies/headers (__cf_bm, cf_clearance, cf-mitigated),
Turnstile markers, Datadome (datadome, dd-*), Akamai (ak_bmsc, _abck), PerimeterX
(px3, _px3), Incapsula (incap_ses), generic captcha/verify-human strings.
Returns {score 0-100, vendor guess, signals[], needs_headless}.
"""
from __future__ import annotations

VENDOR_MARKERS = {
    "cloudflare": ["__cf_bm", "cf_clearance", "cf-mitigated", "cf-chl", "turnstile", "just a moment", "attention required"],
    "datadome": ["datadome", "dd-protection", "datadome-cid"],
    "akamai": ["ak_bmsc", "_abck", "akamai"],
    "perimeterx": ["_px3", "px-captcha", "perimeterx"],
    "incapsula": ["incap_ses", "visid_incap"],
    "generic": ["verify you are human", "captcha", "access denied", "unusual traffic"],
}


def score_botwall(status: int | None, headers: dict, html_head: str, url: str = "") -> dict:
    hdrs = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
    blob = " ".join(list(hdrs.keys()) + list(hdrs.values()) + [(html_head or "")[:8000].lower()])
    signals: list[str] = []
    vendors: list[str] = []
    for vendor, markers in VENDOR_MARKERS.items():
        hits = [m for m in markers if m in blob]
        if hits:
            vendors.append(vendor)
            signals.extend(f"{vendor}:{m}" for m in hits)
    score = 0
    if status in (403, 429, 503):
        score += 35
        signals.append(f"http:{status}")
    if "cf-mitigated" in hdrs.get("cf-mitigated", "") or "challenge" in hdrs.get("cf-mitigated", ""):
        score += 30
    score += min(50, 12 * len([s for s in signals if not s.startswith("http:")]))
    score = int(max(0, min(100, score)))
    return {"botwall_score": score,
            "vendor": vendors[0] if vendors else ("none" if score == 0 else "unknown"),
            "vendors": vendors, "signals": sorted(set(signals))[:12],
            "needs_headless": score >= 30}
