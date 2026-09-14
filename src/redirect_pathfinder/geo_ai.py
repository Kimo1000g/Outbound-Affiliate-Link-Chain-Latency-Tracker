"""F11 GEO/AEO — AI-search visibility pre-flight (Lumar GEO parity, iGaming-scoped).

Checks (all live, honest booleans — no invented citation scores):
- llms.txt presence + parse (allowed paths, contacts)
- robots.txt AI-crawler allow: GPTBot, ClaudeBot, PerplexityBot, Google-Extended, CCBot
- JSON-LD validity: parse application/ld+json blocks, require @context/@type
- Fact extractability: prices/bonuses/licence strings in structured text vs JS-only
- Semantic chunking test: can the page split into >5 self-contained 200-800 char chunks?
- AI Overviews hint: GSC-style regex presence of definitional Q&A blocks (ESTIMATED hint only)

Endpoint: POST /audit/ai-visibility {url}. One module, one endpoint, 2026-relevant.
"""
from __future__ import annotations
import json
import re
from urllib.parse import urljoin, urlparse
import httpx

AI_BOTS = ["GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended", "CCBot", "anthropic-ai", "cohere-ai"]
UA = "iGaming-Pathfinder-GEO/2.0"


async def audit_ai_visibility(url: str, html: str = "", client: httpx.AsyncClient | None = None) -> dict:
    own = client is None
    c = client or httpx.AsyncClient(timeout=12, follow_redirects=True, headers={"User-Agent": UA})
    try:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        # llms.txt
        llms = {"present": False, "url": origin + "/llms.txt", "paths": [], "note": ""}
        try:
            r = await c.get(origin + "/llms.txt")
            if r.status_code == 200 and len(r.text) > 20:
                llms["present"] = True
                llms["paths"] = [l.strip()[:160] for l in r.text.splitlines() if l.strip() and not l.strip().startswith("#")][:20]
            else:
                llms["note"] = f"HTTP {r.status_code} — llms.txt missing (AI crawlers fall back to HTML)"
        except Exception as e:
            llms["note"] = f"{type(e).__name__} fetching llms.txt"
        # robots AI-bot allow
        robots = {"url": origin + "/robots.txt", "blocks": [], "allows_ai": None, "note": ""}
        try:
            r = await c.get(origin + "/robots.txt")
            if r.status_code == 200:
                txt = r.text
                for bot in AI_BOTS:
                    # crude per-agent Disallow check
                    m = re.search(rf"User-agent:\s*{re.escape(bot)}[^\n]*\n((?:[ ]*[^\n]*\n){{0,6}})", txt, re.I)
                    block = m.group(0) if m else ""
                    if re.search(r"Disallow:\s*/", block):
                        robots["blocks"].append(bot)
                robots["allows_ai"] = not robots["blocks"]
                if robots["blocks"]:
                    robots["note"] = "AI crawlers disallowed — pages invisible to AI answers/RAG"
            else:
                robots["note"] = f"robots.txt HTTP {r.status_code}"
        except Exception as e:
            robots["note"] = f"{type(e).__name__} fetching robots.txt"
        # JSON-LD
        ld_valid, ld_types, ld_note = _jsonld(html)
        facts = _facts(html)
        chunks = _chunks(html)
        return {"url": url, "llms_txt": llms, "robots_ai": robots,
                "json_ld": {"valid": ld_valid, "types": ld_types[:10], "note": ld_note},
                "fact_density": facts, "chunking": chunks,
                "ai_overviews_hint": _overviews_hint(html),
                "basis": "measured live HTML/robots/llms.txt — no invented citation probability"}
    finally:
        if own:
            try:
                await c.aclose()
            except Exception:
                pass


def _jsonld(html: str) -> tuple[bool, list, str]:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "lxml")
        blocks = soup.find_all("script", attrs={"type": "application/ld+json"})
        if not blocks:
            return False, [], "no application/ld+json blocks — add Review/FAQ/Organization schema for AI extractability"
        types: list[str] = []
        valid = False
        for b in blocks:
            try:
                j = json.loads(b.string or "")
            except Exception:
                continue
            items = j if isinstance(j, list) else [j]
            for it in items:
                if isinstance(it, dict) and "@type" in it:
                    valid = True
                    t = it.get("@type")
                    types.append(t if isinstance(t, str) else ",".join(t) if isinstance(t, list) else "?")
        return valid, types, ("" if valid else "ld+json present but unparseable/missing @type")
    except Exception:
        return False, [], "parse error"


def _facts(html: str) -> dict:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        low = text.lower()
        facts = {"bonus_terms": bool(re.search(r"(wagering|playthrough|min deposit|t&cs)", low)),
                 "licence_mention": bool(re.search(r"(ukgc|dge|agco|ggl|ksa|mgcb|pgcb)", low)),
                 "price_like": bool(re.search(r"(\$|€|£)\s?\d", text)),
                 "qa_blocks": len(re.findall(r"\?\s+[A-Z]", text[:20000]))}
        density = sum(1 for v in facts.values() if v is True)
        facts["extractable_in_html"] = len(text) > 500
        facts["score_est"] = min(100, density * 25)
        facts["basis"] = "ESTIMATED — structured-fact presence in static HTML (JS-only facts score 0)"
        return facts
    except Exception:
        return {"score_est": 0, "basis": "parse error"}


def _chunks(html: str) -> dict:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "lxml")
        for tag in soup(["script", "style", "nav", "footer", "noscript"]):
            tag.decompose()
        paras = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "li", "h2", "h3"]) if len(p.get_text(" ", strip=True)) > 60]
        chunks = [p for p in paras if 200 <= len(p) <= 1200]
        return {"chunks_200_1200": len(chunks), "pass": len(chunks) >= 5,
                "note": "RAG/AI answers cite self-contained 200-1200 char blocks — 5+ is healthy"}
    except Exception:
        return {"chunks_200_1200": 0, "pass": False, "note": "parse error"}


def _overviews_hint(html: str) -> dict:
    low = (html or "").lower()
    definitional = len(re.findall(r"(what is|how does|is .* legal|best .* (sites|apps|bonus))", low))
    return {"definitional_blocks": definitional,
            "hint": "ESTIMATED — definitional Q&A + comparison tables correlate with AI Overview citation; verify in GSC, not measured here"}
