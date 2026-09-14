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
                    "psi_inp_attribution": {
                        "note": "INP attribution requires field data + JS interaction profiling — lab TBT is NOT INP; enforce LCP budget gate",
                        "lab_inp_ms": num("interaction-to-next-paint"),
                        "lab_tbt_ms": num("total-blocking-time"),
                        "attribution": (audits.get("interaction-to-next-paint", {}) or {}).get("details", {}),
                    },
                    "performance_score": ((j.get("lighthouseResult") or {}).get("categories") or {}).get("performance", {}).get("score"),
                    "inp_budget_note": "INP ≤200ms good / >500ms poor (2026 killer: 43% mobile fail). Gate deploys on LCP ≤2.5s + INP budget.",
                    "basis": "PageSpeed Insights lab+field — VERIFIED Google data, page-level (not redirect hops)"}
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}", "url": url}


async def fetch_crux_history(url: str, form_factor: str = "PHONE") -> dict:
    """CrUX 28d history (queryHistoryRecord) -> {available, history:[{period,lcp,inp,cls}]}.

    Real CrUX history API when CRUX_API_KEY is set (normalized per collection
    period from percentilesTimeseries p75); honest {available:false, reason}
    without a key or on HTTP/error — never synthetic. Raw API payload is kept
    under `raw_history` for auditability.
    """
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
            j = r.json()
            history: list[dict] = []
            try:
                rec = j.get("record", {}) or {}
                periods = rec.get("collectionPeriods", []) or []
                metrics = rec.get("metrics", {}) or {}
                series: dict[str, list] = {}
                for mkey in ("largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift"):
                    series[mkey] = ((metrics.get(mkey, {}) or {}).get("percentilesTimeseries", {}) or {}).get("p75s", []) or []
                n = max([len(periods)] + [len(v) for v in series.values()] + [0])
                short = {"largest_contentful_paint": "lcp", "interaction_to_next_paint": "inp",
                         "cumulative_layout_shift": "cls"}
                for i in range(n):
                    entry: dict = {}
                    try:
                        p = periods[i] if i < len(periods) else {}
                        entry["period"] = f"{p.get('firstDate', {}).get('year')}-{p.get('firstDate', {}).get('month')}-{p.get('firstDate', {}).get('day')}_to_{p.get('lastDate', {}).get('year')}-{p.get('lastDate', {}).get('month')}-{p.get('lastDate', {}).get('day')}"
                    except Exception:
                        entry["period"] = f"period_{i}"
                    for mkey, sname in short.items():
                        try:
                            entry[sname] = (series[mkey][i] if i < len(series[mkey]) else None)
                        except Exception:
                            entry[sname] = None
                    history.append(entry)
            except Exception:
                history = []
            if not history:
                return {"available": True, "url": url, "origin": _origin(url),
                        "form_factor": form_factor, "history": [],
                        "raw_history": j,
                        "note": "History response parsed with no usable periods — see raw_history",
                        "basis": "VERIFIED CrUX 28d history — use to gate deploys on LCP/INP regression"}
            return {"available": True, "url": url, "origin": _origin(url),
                    "form_factor": form_factor, "history": history,
                    "raw_history": j,
                    "basis": "VERIFIED CrUX 28d history — use to gate deploys on LCP/INP regression"}
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}", "url": url}


def crux_lcp_budget_ok(crux_data: dict, redirect_ms: float, budget_ms: float = 2500) -> dict:
    """Gate a redirect chain against the CrUX LCP p75 budget.

    Redirect-chain ms burns LCP budget ~1:1 (redirect TTFB steals LCP headroom),
    so a chain already over budget fails even before page load starts.
    Returns {ok, reason, lcp_p75_ms, redirect_ms, budget_ms} — ok=False gates
    the deploy; ok=True with a no-field-data reason passes open (lab-only).
    """
    try:
        redirect_ms = float(redirect_ms or 0)
    except Exception:
        redirect_ms = 0.0
    try:
        budget_ms = float(budget_ms or 2500)
    except Exception:
        budget_ms = 2500.0
    lcp_p75 = None
    try:
        m = (crux_data or {}).get("metrics", {}) or {}
        lcp_p75 = (m.get("largest_contentful_paint", {}) or {}).get("p75")
        if lcp_p75 is None:
            hist = (crux_data or {}).get("history") or []
            if isinstance(hist, list) and hist:
                lcp_p75 = hist[-1].get("lcp")
        lcp_p75 = float(lcp_p75) if lcp_p75 is not None else None
    except Exception:
        lcp_p75 = None
    if lcp_p75 is None:
        ok = redirect_ms <= budget_ms
        return {"ok": bool(ok), "reason": ("no CrUX LCP p75 field data — lab-only gate: "
                                           f"redirect {redirect_ms:.0f}ms vs LCP budget {budget_ms:.0f}ms"),
                "lcp_p75_ms": None, "redirect_ms": redirect_ms, "budget_ms": budget_ms}
    if redirect_ms > budget_ms:
        return {"ok": False,
                "reason": (f"redirect chain {redirect_ms:.0f}ms already exceeds LCP budget "
                           f"{budget_ms:.0f}ms (CrUX LCP p75={lcp_p75:.0f}ms) — burns LCP 1:1, gate deploy"),
                "lcp_p75_ms": lcp_p75, "redirect_ms": redirect_ms, "budget_ms": budget_ms}
    return {"ok": True,
            "reason": (f"redirect {redirect_ms:.0f}ms within LCP budget {budget_ms:.0f}ms "
                       f"(CrUX LCP p75={lcp_p75:.0f}ms) — redirect burn leaves "
                       f"{max(0.0, budget_ms - redirect_ms):.0f}ms headroom"),
            "lcp_p75_ms": lcp_p75, "redirect_ms": redirect_ms, "budget_ms": budget_ms}


def rum_snippet(endpoint: str = "/api/rum") -> str:
    """web-vitals.js RUM snippet (web-vitals/attribution) for publisher pages — INP attribution."""
    return (f"<!-- RUM: INP/LCP/CLS attribution — paste before </body>. POSTs to {endpoint} -->\n"
            "<script type='module'>\n"
            "import {onLCP,onINP,onCLS} from 'https://unpkg.com/web-vitals@4/dist/web-vitals.attribution.js';\n"
            f"const ep='{endpoint}';\n"
            "function send(m){fetch(ep,{method:'POST',headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify({name:m.name,value:m.value,rating:m.rating,attribution:m.attribution,url:location.href})}).catch(()=>{});}\n"
            "onLCP(send);onINP(send);onCLS(send);\n</script>")
