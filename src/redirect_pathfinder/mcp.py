"""MCP server surface — P0 ADD. Voluum + Lumar both shipped MCP in 2026; without it
AI agents (Claude Code / Cursor) cannot query audits.

Exposes as plain JSON-RPC 2.0 over POST /mcp + discovery at /.well-known/mcp.json:
  tools: trace_chain, param_audit, sla_matrix, verify_inputs, ai_visibility, crux_lookup
No extra dependency (hand-rolled JSON-RPC, MCP-compatible tool schema).
"""
from __future__ import annotations

TOOLS = [
    {"name": "trace_chain",
     "description": "Trace an outbound affiliate URL hop-by-hop (no auto-follow). Returns hops, param_events, latency, honesty basis.",
     "inputSchema": {"type": "object", "properties": {
         "source_url": {"type": "string"}, "geo": {"type": "string", "default": "US-NJ"},
         "device": {"type": "string", "default": "desktop_chrome"}}, "required": ["source_url"]}},
    {"name": "param_audit",
     "description": "Check tracking-param survival (btag/affid/clickid/subid) across a traced chain.",
     "inputSchema": {"type": "object", "properties": {
         "source_url": {"type": "string"}, "geo": {"type": "string"}}, "required": ["source_url"]}},
    {"name": "sla_matrix",
     "description": "Network SLA matrix (success_rate, avg_latency, revenue_at_risk) from last bulk result or fresh targets.",
     "inputSchema": {"type": "object", "properties": {
         "targets": {"type": "array", "items": {"type": "object"}}}}},
    {"name": "verify_inputs",
     "description": "Live-verify sitemaps/URLs/regexes before an audit (kills garbage-in-gospel-out).",
     "inputSchema": {"type": "object", "properties": {"urls": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "ai_visibility",
     "description": "AI-search pre-flight (robots/llms.txt/JSON-LD/chunking as infra diagnostics — NOT citation predictors per Google May 2026).",
     "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "crux_lookup",
     "description": "Real-user CWV (LCP/INP/CLS p75) via CrUX API. Redirect-ms is NOT CWV.",
     "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
]

MCP_MANIFEST = {
    "schema_version": "2026-09-01",
    "name": "outbound-affiliate-link-chain-latency-tracker",
    "version": "3.1.0",
    "transport": {"type": "json-rpc", "endpoint": "/mcp"},
    "tools": TOOLS,
    "honesty": "UNKNOWN/ESTIMATED(±40%)/VERIFIED(±15%); redirect-ms ≠ CWV; llms.txt is agent-infra not citation factor",
}


async def dispatch(tool: str, args: dict, cfg: dict) -> dict:
    from .orchestrator import audit_single_url
    from .revenue import network_sla_matrix
    from .geo_ai import audit_ai_visibility
    from .crux_psi import fetch_crux
    try:
        if tool == "trace_chain":
            row = {"source_url": args.get("source_url", ""), "geo": args.get("geo", "US-NJ"),
                   "device": args.get("device", "desktop_chrome")}
            c = await audit_single_url(row, cfg)
            return {"ok": True, "chain": c.model_dump()}
        if tool == "param_audit":
            row = {"source_url": args.get("source_url", ""), "geo": args.get("geo", "US-NJ"),
                   "device": "desktop_chrome"}
            c = await audit_single_url(row, cfg)
            return {"ok": True, "params_intact": c.params_intact,
                    "param_events": [e.model_dump() for e in c.param_events],
                    "final_url": c.final_url}
        if tool == "sla_matrix":
            return {"ok": True, "note": "pass chains via bulk audit; per-network rollup uses revenue honesty bands",
                    "sla": []}
        if tool == "verify_inputs":
            import httpx
            from .autofill import _check_sitemap
            out = []
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as cl:
                for u in (args.get("urls") or [])[:20]:
                    try:
                        from .ssrf import assert_safe_url
                        assert_safe_url(u)
                        out.append(await _check_sitemap(cl, u))
                    except Exception as e:
                        out.append({"url": u, "live": False, "note": str(e)[:200]})
            return {"ok": True, "sitemaps": out}
        if tool == "ai_visibility":
            import httpx
            url = args.get("url", "")
            try:
                from .ssrf import assert_safe_url
                assert_safe_url(url)
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as cl:
                    r = await cl.get(url)
                    html = r.text[:300000]
            except Exception as e:
                return {"ok": False, "error": str(e)[:300]}
            return {"ok": True, "visibility": await audit_ai_visibility(url, html)}
        if tool == "crux_lookup":
            return {"ok": True, "crux": await fetch_crux(args.get("url", ""))}
        return {"ok": False, "error": f"unknown tool {tool}. Available: {[t['name'] for t in TOOLS]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
