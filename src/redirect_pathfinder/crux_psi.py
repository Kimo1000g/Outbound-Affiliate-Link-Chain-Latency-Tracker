"""CrUX + PSI — honest Core Web Vitals. Redirect-chain ms is NOT page CWV.

- CrUX API (needs CRUX_API_KEY env): real-user LCP/INP/CLS p75 by origin+form factor,
  plus queryHistoryRecord (28d trend) via fetch_crux_history.
- PSI (needs PSI key or keyless quota): lab + field + INP attribution for a URL.
- RUM snippet: rum_snippet() returns a web-vitals.js attribution snippet for publisher pages.
Without keys both return {available:false, reason} — never fake numbers.
Endpoint: GET /utils/crux?url=… ; POST /utils/psi {url, strategy}.
"""
from __future__ import annotations
import os
from urllib.parse import urlparse
import httpx


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}".rstrip("/")


async def fetch_crux(url: str, form_factor: str = "PHONE") -> dict:
    key = os.environ.get("CRUX_API_KEY", "")
    if not key:
        return {"available": False, "reason": "CRUX_API_KEY not set — redirect total_ms is server hops only, not LCP/INP/CLS",
                "url": url, "origin": _origin(url)}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"https://chromeuxreport.googleapis.com/v1/records:queryRecord?key={key}",
                             json={"origin": _origin(url), "formFactor": form_factor,
                                   "metrics": ["largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift"]})
            if r.status_code != 200:
                return {"available": False, "reason": f"CrUX HTTP {r.status_code}: {r.text[:200]}", "url": url}
            j = r.json()
            out = {"available": True, "url": url, "origin": _origin(url), "form_factor": form_factor, "metrics": {}}
            for k in ("largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift"):
                m = (j.get("record", {}).get("metrics", {}) or {}).get(k, {})
                pct = (m.get("percentiles") or {})
                out["metrics"][k] = {"p75": pct.get("p75"), "good": (m.get("histogram") or [{}])[0].get("density"),
                                     "basis": "CrUX real-user p75 — VERIFIED Google field data"}
            return out
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}", "url": url}


async def fetch_psi(url: str, strategy: str = "mobile") -> dict:
    key = os.environ.get("PSI_API_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            params = {"url": url, "strategy": strategy, "category": "performance"}
            if key:
                params["key"] = key
            r = await c.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed", params=params)
            if r.status_code != 200:
                return {"available": False, "reason": f"PSI HTTP {r.status_code}: {r.text[:200]}", "url": url}
            j = r.json()
            audits = ((j.get("lighthouseResult") or {}).get("audits") or {})
            def num(*ids):
                for i in ids:
                    a = audits.get(i, {})
                    if a.get("numericValue") is not None:
                        return a["numericValue"]
                return None
            return {"available": True, "url": url, "strategy": strategy,
                    "lcp_ms": num("largest-contentful-paint"), "inp_ms": num("interaction-to-next-paint"),
                    "cls": num("cumulative-layout-shift"), "tbt_ms": num("total-blocking-time"),
                    "ttfb_ms": num("server-response-time"),
                    "inp_attribution": (audits.get("interaction-to-next-paint", {}) or {}).get("details", {}),
                    "performance_score": ((j.get("lighthouseResult") or {}).get("categories") or {}).get("performance", {}).get("score"),
                    "inp_budget_note": "INP ≤200ms good / >500ms poor (2026 killer: 43% mobile fail). Gate deploys on LCP ≤2.5s + INP budget.",
                    "basis": "PageSpeed Insights lab+field — VERIFIED Google data, page-level (not redirect hops)"}
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}", "url": url}


async def fetch_crux_history(url: str, form_factor: str = "PHONE") -> dict:
    """CrUX 28d history (queryHistoryRecord) — trend for LCP/INP/CLS p75. Honest unavailable without key."""
    key = os.environ.get("CRUX_API_KEY", "")
    if not key:
        return {"available": False, "reason": "CRUX_API_KEY not set — history unavailable", "url": url}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"https://chromeuxreport.googleapis.com/v1/records:queryHistoryRecord?key={key}",
                             json={"origin": _origin(url), "formFactor": form_factor,
                                   "metrics": ["largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift"]})
            if r.status_code != 200:
                return {"available": False, "reason": f"CrUX history HTTP {r.status_code}: {r.text[:200]}", "url": url}
            return {"available": True, "url": url, "history": r.json(),
                    "basis": "VERIFIED CrUX 28d history — use to gate deploys on LCP/INP regression"}
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}", "url": url}


def rum_snippet(endpoint: str = "/api/rum") -> str:
    """web-vitals.js RUM snippet (web-vitals/attribution) for publisher pages — INP attribution."""
    return (f"<!-- RUM: INP/LCP/CLS attribution — paste before </body>. POSTs to {endpoint} -->\n"
            "<script type='module'>\n"
            "import {onLCP,onINP,onCLS} from 'https://unpkg.com/web-vitals@4/dist/web-vitals.attribution.js';\n"
            f"const ep='{endpoint}';\n"
            "function send(m){fetch(ep,{method:'POST',headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify({name:m.name,value:m.value,rating:m.rating,attribution:m.attribution,url:location.href})}).catch(()=>{});}\n"
            "onLCP(send);onINP(send);onCLS(send);\n</script>")
