"""F11 GEO/AEO — AI-search visibility pre-flight (2026-correct).

GOOGLE MAY 15 2026 CORRECTION (applied here, was wrong before):
- Google said verbatim AEO/GEO is "still SEO" — IGNORE llms.txt, special AI markup,
  chunking rewrites as citation factors. Independent data: Limy 515M bot events =
  408 /llms.txt hits (zero), OtterlyAI 0.1%, ALLMO 0.00106%, SE Ranking 300k XGBoost = no effect.
- So: llms.txt kept ONLY as "agent-readable infra for Cursor/Claude Code/MCP, NOT a
  Google citation factor". Chunking kept ONLY as readability diagnostic. JSON-LD kept
  as extractability aid (Organization/Article/FAQPage help parsing, not ranking).
- Real GEO per Princeton paper + 2026 data: prompt-level Share of Voice across
  ChatGPT/Claude/Perplexity/Gemini/AI Overviews + fact density in crawlable HTML +
  schema + internal links + crawlability. Use sov.run_sov() — never score_est.
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
        # llms.txt — DEMOTED per Google May 2026: agent infra, NOT citation factor
        llms = {"present": False, "url": origin + "/llms.txt", "paths": [], "note": "",
                "citation_factor": False,
                "verdict": "Agent-readable infra for Cursor/Claude Code/MCP — NOT a Google citation factor (Google AI Search Guide May 15 2026: IGNORE llms.txt for Search; 0.1% cite-rate data)"}
        try:
            r = await c.get(origin + "/llms.txt")
            if r.status_code == 200 and len(r.text) > 20:
                llms["present"] = True
                llms["paths"] = [l.strip()[:160] for l in r.text.splitlines() if l.strip() and not l.strip().startswith("#")][:20]
            else:
                llms["note"] = f"HTTP {r.status_code} — llms.txt missing (AI coding agents fall back to HTML; Google unaffected)"
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
                "json_ld": {"valid": ld_valid, "types": ld_types[:10], "note": ld_note,
                            "citation_factor": False,
                            "verdict": "Extractability aid (Organization/Article/FAQPage help parsing) — NOT a ranking predictor per Google May 2026"},
                "fact_density": facts, "chunking": chunks,
                "ai_overviews_hint": _overviews_hint(html),
                "sov": {"measured": False,
                        "next": "POST /audit/sov {brand, geo} runs live prompt tests across ChatGPT/Claude/Perplexity/Gemini — the only real Share of Voice",
                        "warning": "Any score_est below is an HTML readability heuristic, NOT a citation probability. Do not report as GEO score."},
                "basis": "measured live HTML/robots/llms.txt as INFRA diagnostics — citation Share of Voice requires prompt testing, not HTML scoring"}
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
        raw = min(100, density * 25)
        band = "strong" if raw >= 75 else ("moderate" if raw >= 45 else "weak")
        facts["extractable_in_html"] = len(text) > 500
        # DEPRECATED: readability_hint kept ONLY for back-compat dashboards — it is an
        # HTML readability heuristic, NOT a citation probability. Use facts["band"].
        facts["readability_hint"] = raw
        facts["band"] = band
        facts["basis"] = ("ESTIMATED readability band (strong≥75/moderate 45-74/weak<45) — "
                          "fact density in crawlable HTML aids extraction; it does NOT predict citations (Google May 2026). "
                          "readability_hint is deprecated; never treat it as citation probability — use sov.run_sov().")
        return facts
    except Exception:
        return {"readability_hint": 0, "band": "weak",
                "basis": "parse error — readability_hint deprecated, not a citation probability"}


def _chunks(html: str) -> dict:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "lxml")
        for tag in soup(["script", "style", "nav", "footer", "noscript"]):
            tag.decompose()
        paras = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "li", "h2", "h3"]) if len(p.get_text(" ", strip=True)) > 60]
        chunks = [p for p in paras if 200 <= len(p) <= 1200]
        return {"chunks_200_1200": len(chunks), "pass": len(chunks) >= 5,
                "note": ("Readability diagnostic: 5+ self-contained 200-1200 char blocks help human readers "
                         "— NOT a Google citation factor (IGNORE chunking rewrites per Google May 2026)")}
    except Exception:
        return {"chunks_200_1200": 0, "pass": False, "note": "parse error"}


def _overviews_hint(html: str) -> dict:
    low = (html or "").lower()
    definitional = len(re.findall(r"(what is|how does|is .* legal|best .* (sites|apps|bonus))", low))
    return {"definitional_blocks": definitional,
            "hint": "ESTIMATED — definitional Q&A + comparison tables correlate with AI Overview citation; verify in GSC, not measured here"}
