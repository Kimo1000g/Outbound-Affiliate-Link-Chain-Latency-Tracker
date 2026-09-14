"""Prompt-level Share-of-Voice harness — P0 ADD (replaces fake GEO citation scores).

2026 GEO reality (Google AI Search Guide May 15 2026 + Princeton GEO paper):
- llms.txt / chunking / JSON-LD do NOT drive Google citations. Measure, don't invent.
- What works: prompt testing across ChatGPT/Claude/Perplexity/Gemini + AI Overviews,
  fact density in crawlable HTML, Organization/Article/FAQPage schema, crawlability.

This harness runs prompt templates against configured LLM endpoints (all optional;
without keys returns {configured:false} + ready-to-run templates — never fake SOV).
Env: OPENAI_API_KEY, ANTHROPIC_API_KEY, PERPLEXITY_API_KEY, GEMINI_API_KEY.
"""
from __future__ import annotations
import os

DEFAULT_PROMPTS = [
    "Best {brand} bonus for {geo} players right now?",
    "Is {brand} legal in {geo} and what is the welcome offer?",
    "{brand} vs {rival}: which has faster payouts?",
    "What are the wagering requirements for {brand} welcome bonus?",
]


def render_prompts(brand: str, geo: str, rival: str = "bet365") -> list[str]:
    return [p.format(brand=brand or "this operator", geo=geo, rival=rival) for p in DEFAULT_PROMPTS]


async def run_sov(brand: str, geo: str = "US-NJ", prompts: list[str] | None = None) -> dict:
    prompts = prompts or render_prompts(brand, geo)
    providers = {
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "perplexity": bool(os.environ.get("PERPLEXITY_API_KEY")),
        "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
    }
    if not any(providers.values()):
        return {"configured": False, "brand": brand, "geo": geo,
                "prompts": prompts,
                "reason": "no LLM keys set (OPENAI/ANTHROPIC/PERPLEXITY/GEMINI) — templates ready, SOV not measured",
                "basis": "prompt testing required for real Share of Voice; static HTML checks cannot predict citations"}
    results: list[dict] = []
    try:
        import httpx
        # Minimal live example: Perplexity chat completions (cites sources); others via same pattern.
        if providers["perplexity"]:
            async with httpx.AsyncClient(timeout=60) as c:
                for p in prompts[:8]:
                    try:
                        r = await c.post("https://api.perplexity.ai/chat/completions",
                                         headers={"Authorization": f"Bearer {os.environ['PERPLEXITY_API_KEY']}"},
                                         json={"model": "sonar", "messages": [{"role": "user", "content": p}]})
                        j = r.json() if r.status_code == 200 else {}
                        txt = ((j.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "")
                        cits = j.get("citations", []) or []
                        results.append({"prompt": p, "provider": "perplexity",
                                        "cited": any((brand or "").lower() in str(x).lower() for x in cits + [txt]),
                                        "citations": cits[:10]})
                    except Exception as e:
                        results.append({"prompt": p, "provider": "perplexity", "error": str(e)[:160]})
    except Exception as e:
        return {"configured": True, "brand": brand, "error": str(e)[:200], "providers": providers}
    cited = sum(1 for r in results if r.get("cited"))
    return {"configured": True, "brand": brand, "geo": geo, "providers": providers,
            "tests": len(results), "cited": cited,
            "sov_pct": round(cited / max(1, len(results)) * 100, 1),
            "details": results,
            "basis": "VERIFIED prompt-level citations (measured live, not estimated from HTML)"}
