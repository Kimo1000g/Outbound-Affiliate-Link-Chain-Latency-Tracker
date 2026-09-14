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
import hashlib
import json
import os
import time
from pathlib import Path

DEFAULT_PROMPTS = [
    "Best {brand} bonus for {geo} players right now?",
    "Is {brand} legal in {geo} and what is the welcome offer?",
    "{brand} vs {rival}: which has faster payouts?",
    "What are the wagering requirements for {brand} welcome bonus?",
]

MAX_PROMPTS_PER_PROVIDER = 8
MAX_TOTAL_CALLS = 24
MAX_PROMPTS_ACCEPTED = 30
CACHE_TTL_S = 7 * 24 * 3600

# SSRF note: _ask posts ONLY to these allowlisted API hosts.
_ALLOWLIST = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "perplexity": "https://api.perplexity.ai/chat/completions",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
}


def render_prompts(brand: str, geo: str, rival: str = "bet365") -> list[str]:
    return [p.format(brand=brand or "this operator", geo=geo, rival=rival) for p in DEFAULT_PROMPTS]


def _cache_path(brand: str, geo: str, prompts: list[str]) -> Path:
    key = hashlib.sha256(json.dumps(
        {"b": (brand or "").lower(), "g": geo, "p": prompts}, sort_keys=True).encode()).hexdigest()[:32]
    for cand in (Path("output") / ".sov_cache", Path("/tmp") / ".sov_cache"):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            return cand / f"{key}.json"
        except Exception:
            continue
    import tempfile
    return Path(tempfile.gettempdir()) / ".sov_cache" / f"{key}.json"


def _cache_get(path: Path) -> dict | None:
    try:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > CACHE_TTL_S:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_set(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload)[:200000], encoding="utf-8")
    except Exception:
        pass


async def _ask(provider: str, prompt: str) -> tuple[str, list]:
    """Shared helper: POST prompt to allowlisted provider endpoint -> (text, citations)."""
    import httpx
    url = _ALLOWLIST[provider]
    if provider == "openai":
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(url,
                             headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                             json={"model": "gpt-4o-mini",
                                   "messages": [{"role": "user", "content": prompt}]})
            j = r.json() if r.status_code == 200 else {}
            txt = ((j.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "")
            return txt or "", []
    if provider == "anthropic":
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(url,
                             headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                                      "anthropic-version": "2023-06-01"},
                             json={"model": "claude-3-5-haiku-latest", "max_tokens": 1024,
                                   "messages": [{"role": "user", "content": prompt}]})
            j = r.json() if r.status_code == 200 else {}
            blocks = (j.get("content") or [])
            txt = " ".join(b.get("text", "") for b in blocks if isinstance(b, dict))
            cits = j.get("citations", []) or []
            return txt, cits
    if provider == "perplexity":
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(url,
                             headers={"Authorization": f"Bearer {os.environ['PERPLEXITY_API_KEY']}"},
                             json={"model": "sonar", "messages": [{"role": "user", "content": prompt}]})
            j = r.json() if r.status_code == 200 else {}
            txt = ((j.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "")
            cits = j.get("citations", []) or []
            return txt or "", cits
    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{url}?key={key}",
                             json={"contents": [{"parts": [{"text": prompt}]}]})
            j = r.json() if r.status_code == 200 else {}
            cands = j.get("candidates") or []
            txt = ""
            try:
                txt = (cands[0].get("content", {}).get("parts", [{}])[0] or {}).get("text", "")
            except Exception:
                pass
            return txt or "", []
    raise ValueError(f"unknown provider {provider}")


async def run_sov(brand: str, geo: str = "US-NJ", prompts: list[str] | None = None) -> dict:
    # SSRF/input validation: brand non-empty, prompts length cap 30
    brand = (brand or "").strip()[:120]
    if not brand:
        return {"configured": False, "brand": brand, "geo": geo, "error": "brand must be non-empty",
                "basis": "prompt testing requires a brand"}
    prompts = (prompts or render_prompts(brand, geo))[:MAX_PROMPTS_ACCEPTED]
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
    # file cache (TTL 7d) to avoid re-billing
    cpath = _cache_path(brand, geo, prompts)
    hit = _cache_get(cpath)
    if hit:
        hit = dict(hit)
        hit["cache_hit"] = True
        return hit
    active = [p for p, on in providers.items() if on]
    per = min(MAX_PROMPTS_PER_PROVIDER, max(1, MAX_TOTAL_CALLS // max(1, len(active))))
    results: list[dict] = []
    total = 0
    try:
        for provider in active:
            for p in prompts[:per]:
                if total >= MAX_TOTAL_CALLS:
                    break
                total += 1
                try:
                    txt, cits = await _ask(provider, p)
                    low = (brand or "").lower()
                    cited = low in (txt or "").lower() or any(low in str(x).lower() for x in (cits or []))
                    # recommendation: affirmative/CTA language mentioning brand
                    rec = bool(cited and ("recommend" in (txt or "").lower() or "best" in (txt or "").lower()
                                          or "sign up" in (txt or "").lower() or "bonus" in (txt or "").lower()))
                    results.append({"prompt": p, "provider": provider,
                                    "cited": cited, "recommended": rec,
                                    "citations": (cits or [])[:10],
                                    "text_excerpt": (txt or "")[:500]})
                except Exception as e:
                    results.append({"prompt": p, "provider": provider, "error": str(e)[:160]})
    except Exception as e:
        return {"configured": True, "brand": brand, "error": str(e)[:200], "providers": providers}
    tested = [r for r in results if "error" not in r]
    cited = sum(1 for r in tested if r.get("cited"))
    recd = sum(1 for r in tested if r.get("recommended"))
    with_cits = sum(1 for r in tested if r.get("citations"))
    n = max(1, len(tested))
    out: dict = {"configured": True, "brand": brand, "geo": geo, "providers": providers,
                 "tests": len(results), "cited": cited,
                 "sov_pct": round(cited / n * 100, 1),
                 "inclusion_rate_pct": round(cited / n * 100, 1),
                 "citation_rate_pct": round(with_cits / n * 100, 1),
                 "recommendation_rate_pct": round(recd / n * 100, 1),
                 "details": results,
                 "cost_guard": {"max_prompts_per_provider": MAX_PROMPTS_PER_PROVIDER,
                                "max_total_calls": MAX_TOTAL_CALLS, "calls_made": total},
                 "basis": "VERIFIED prompt-level citations (measured live, not estimated from HTML)"}
    if active == ["perplexity"]:
        out["partial"] = "perplexity-only"
    _cache_set(cpath, out)
    return out
