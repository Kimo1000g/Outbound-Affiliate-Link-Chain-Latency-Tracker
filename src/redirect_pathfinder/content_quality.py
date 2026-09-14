"""Content quality 2026 — thin-affiliate, merchant-copy similarity, E-E-A-T bands, site-reputation abuse.

SpamBrain (Mar/Jun/Aug 2026, 19.5h rollouts) deindexes thin affiliate: merchant
copy, templated reviews, no testing evidence. This module gives publishers a
pre-flight check with honest BANDS (strong/moderate/weak + confidence interval),
never a fake-precision 0-100 Google verdict. E-E-A-T is not a score Google uses
(author bios ≠ ranking); we surface evidence signals instead.
"""
from __future__ import annotations
import re
from difflib import SequenceMatcher

THIN_MIN_WORDS = 400
TESTING_SIGNALS = ["we tested", "our test", "screenshot", "measured", "methodology",
                   "pros and cons", "withdrawal test", "deposit test", "kyc",
                   "comparison table", "<table", "payout speed", "verified"]
AUTHOR_SIGNALS = ["author", "reviewed by", "expert", "editor", "byline", "about the author",
                  "rel=\"author\"", "person", "sameas"]
DATE_SIGNALS = ["updated", "last updated", "published", "reviewed on", "2025", "2026"]
PRICE_RX = re.compile(r"(\$|€|£)\s?\d[\d,.]*")
BONUS_TERMS_RX = re.compile(r"(wagering|playthrough|t&cs|terms|18\+|21\+|gambleaware|gamstop|min deposit|expiry|expire)", re.I)
PARASITE_RX = re.compile(r"/(coupons?|promo-codes?|reviews?|best-.*|top-?10)/", re.I)


def _words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text or ""))


def merchant_copy_similarity(review_text: str, lander_text: str) -> float:
    """SequenceMatcher ratio on normalised alnum tokens — ESTIMATED similarity, not a plagiarism verdict.
    Threshold guidance: >55% suspect templated/merchant-copy (tune per vertical); 30-55% review; <30% original.
    """
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()[:6000]
    a, b = norm(review_text), norm(lander_text)
    if not a or not b:
        return 0.0
    try:
        return round(SequenceMatcher(None, a[:3000], b[:3000]).ratio() * 100, 1)
    except Exception:
        return 0.0


def merchant_copy_band(pct: float | None) -> dict:
    if pct is None:
        return {"band": "unknown", "detail": "no review-vs-lander comparison supplied"}
    p = float(pct)
    band = "suspect-templated" if p > 55 else ("review" if p >= 30 else "original")
    return {"band": band, "pct": p,
            "detail": ">55% suspect merchant-copy (tune threshold); 30-55% review; <30% original — ESTIMATED"}


def scaled_content_signals(html: str, url: str = "") -> dict:
    """Scaled/templated-content + outbound-to-editorial ratio (SpamBrain / SRA adjunct)."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text(" ", strip=True)) > 40]
        # near-duplicate paragraph ratio
        seen: dict[str, int] = {}
        for p in paras:
            key = p[:120].lower()
            seen[key] = seen.get(key, 0) + 1
        dup_ratio = (sum(v - 1 for v in seen.values()) / max(1, len(paras)))
        links = soup.find_all("a", href=True)
        aff = sum(1 for a in links if re.search(r"/(out|go|visit)|btag|affid|clickid", a.get("href", ""), re.I))
        ratio = (aff / max(1, len(links)))
        templated = dup_ratio > 0.35 or (len(paras) >= 6 and dup_ratio > 0.2)
        return {"paragraphs": len(paras), "near_dup_ratio": round(dup_ratio, 3),
                "outbound_affiliate_ratio": round(ratio, 3),
                "templated_suspect": bool(templated),
                "detail": ("templated/scaled pattern — review for SpamBrain scaled-content + SRA policy" if templated else "no scaled-content pattern"),
                "basis": "ESTIMATED structural heuristic, not a Google verdict"}
    except Exception:
        return {"templated_suspect": False, "basis": "parse error"}


def audit_content_quality(html: str, url: str = "") -> dict:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    low = text.lower()
    words = _words(text)
    testing_hits = [s for s in TESTING_SIGNALS if s in low or s in (html or "").lower()[:200000]]
    author_hits = [s for s in AUTHOR_SIGNALS if s in (html or "").lower()[:200000]]
    date_hits = [s for s in DATE_SIGNALS if s in low]
    has_prices = bool(PRICE_RX.search(text))
    has_bonus_terms = bool(BONUS_TERMS_RX.search(text))
    tables = len(soup.find_all("table"))
    h1 = soup.find("h1")
    title = (soup.title.string.strip() if soup.title and soup.title.string else "")
    thin = words < THIN_MIN_WORDS
    # E-E-A-T heuristic 0-100
    eeat = 20
    if words >= 1200:
        eeat += 20
    elif words >= 700:
        eeat += 12
    elif words >= 400:
        eeat += 6
    eeat += min(20, 5 * len(testing_hits))
    eeat += min(15, 7 * len(author_hits))
    eeat += 8 if date_hits else 0
    eeat += 7 if tables > 0 else 0
    eeat += 5 if has_bonus_terms else 0
    eeat += 5 if (h1 and title) else 0
    eeat = int(max(0, min(100, eeat)))
    band = "strong" if eeat >= 75 else ("moderate" if eeat >= 45 else "weak")
    parasite = bool(PARASITE_RX.search(url or "")) and thin
    scaled = scaled_content_signals(html, url)
    return {"words": words, "thin_content": thin,
            "thin_threshold_words": THIN_MIN_WORDS,
            "testing_evidence_hits": testing_hits[:10],
            "author_signals": author_hits[:8],
            "freshness_signals": date_hits[:6],
            "has_price_accuracy_signals": has_prices,
            "has_bonus_terms": has_bonus_terms,
            "comparison_tables": tables,
            "eeat_score_est": eeat,  # back-compat numeric; prefer eeat_band below
            "eeat_band": {"band": band, "score_est": eeat,
                          "confidence": "±15 pts heuristic",
                          "detail": "strong≥75/moderate 45-74/weak<45 — evidence signals only, NOT a Google E-E-A-T score"},
            "eeat_basis": "ESTIMATED heuristic band (words/testing/author/freshness/tables) — Google uses no author-bio score; do not report as E-E-A-T",
            "site_reputation_abuse_suspect": parasite,
            "site_reputation_detail": ("parasitized subfolder pattern + thin body — review for Site Reputation Abuse policy" if parasite else ""),
            "scaled_content": scaled,
            "merchant_copy_similarity_pct": None,
            "merchant_copy_basis": "fill by comparing review page vs operator lander text (SequenceMatcher, ESTIMATED; >55% suspect)"}
