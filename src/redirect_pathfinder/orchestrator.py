"""Orchestrator — async bulk audit engine wiring F1-F11 + honesty model."""
from __future__ import annotations
import asyncio
import yaml
from .models import ChainResult
from .tracer import trace_chain, try_headless_follow
from .rule_engine import audit_param_survival, audit_network_path
from .latency import score_latency
from .compliance import audit_compliance
from .intel import spoof_headers, audit_adblock, inspect_deeplink, brand_alignment, offer_discrepancy, extract_lander_bonus, detect_consent_and_gpc
from .revenue import score_revenue_and_health
from .edge_healer import build_edge_patch, build_vendor_ticket
from .content_quality import audit_content_quality, merchant_copy_similarity
from .proxy_routing import proxy_for_geo, redacted, proxy_mounts
from .affiliate_sync import apply_verified_epc
from .ga4_gsc import apply_traffic


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _to_float(v, default=0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(str(v).replace(",", "").replace("$", "") or default)
    except Exception:
        return default


def page_forensics(html: str, url: str) -> dict:
    """Deep final-lander forensics — real parsed measurements + E-E-A-T/quality signals."""
    if not html:
        return {"words": 0, "kb": 0.0, "title": "", "h1": "", "lang": "",
                "outlinks": 0, "images": 0, "note": "empty body (blocked or error)"}
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        h1 = soup.find("h1")
        base = {"words": len(text.split()), "kb": round(len(html) / 1024, 1),
                "title": (soup.title.string.strip()[:160] if soup.title and soup.title.string else ""),
                "h1": (h1.get_text(" ", strip=True)[:140] if h1 else ""),
                "lang": (soup.html.get("lang", "") if soup.html else "") or "",
                "outlinks": len(soup.find_all("a", href=True)),
                "images": len(soup.find_all("img"))}
        try:
            cq = audit_content_quality(html, url)
            base["eeat_score_est"] = cq.get("eeat_score_est")
            base["thin_content"] = cq.get("thin_content")
            base["testing_evidence_hits"] = cq.get("testing_evidence_hits")
            base["comparison_tables"] = cq.get("comparison_tables")
        except Exception:
            pass
        return base
    except Exception:
        return {"words": 0, "kb": round(len(html or "") / 1024, 1), "title": "",
                "h1": "", "lang": "", "outlinks": 0, "images": 0}


async def audit_single_url(row: dict, cfg: dict, _shared_client=None, _easylist_hosts: list | None = None) -> ChainResult:
    audit_cfg = cfg.get("audit", {})
    max_hops = int(audit_cfg.get("max_hops", 12))
    timeout_s = int(audit_cfg.get("timeout_seconds", 20))
    connect_s = int(audit_cfg.get("connect_timeout_seconds", 10))
    max_conn = int(audit_cfg.get("max_connections", 50))
    http2 = bool(audit_cfg.get("http2", True))
    retries = int(audit_cfg.get("retries", 2))
    per_host_rps = float(audit_cfg.get("per_host_rps", 4) or 4)
    mode = audit_cfg.get("execution_mode", "hybrid")
    src = row.get("source_url") or row.get("url") or ""
    device = row.get("device", "desktop_chrome") or "desktop_chrome"
    geo = row.get("geo", "US-NJ") or "US-NJ"
    # EPC/clicks: None/"" -> 0/UNKNOWN (revenue.py decides). Never default $1.25 silently.
    rev_cfg = cfg.get("revenue", {}) or {}
    epc_raw = row.get("epc", None)
    clicks_raw = row.get("clicks_30d", None)
    epc = _to_float(epc_raw, 0.0) if epc_raw not in (None, "") else 0.0
    clicks = _to_float(clicks_raw, 0.0) if clicks_raw not in (None, "") else 0.0
    chain = ChainResult(source_url=src, anchor_text=row.get("anchor_text", ""),
                        expected_operator=row.get("expected_operator", ""),
                        expected_network=row.get("expected_network", ""),
                        geo=geo, device=device,
                        clicks_30d=clicks, epc=epc,
                        bonus_text_on_site=row.get("bonus_text_on_site", ""))
    # traffic/epc imports (VERIFIED path) — row may carry _traffic_map/_epc_map from bulk pre-pass
    try:
        if row.get("_traffic_map"):
            apply_traffic(chain, row["_traffic_map"], row.get("_traffic_source", "GA4 export"))
        elif clicks > 0 and row.get("clicks_basis"):
            chain.clicks_basis = str(row.get("clicks_basis"))
        if row.get("_epc_map"):
            apply_verified_epc(chain, row["_epc_map"], row.get("_epc_platform", "affiliate"), row.get("_epc_range", ""))
        elif epc > 0 and row.get("epc_basis"):
            chain.epc_basis = str(row.get("epc_basis"))
    except Exception:
        pass
    headers = spoof_headers(device)
    proxy = proxy_for_geo(cfg, geo)
    chain.proxy_exit_used = redacted(proxy) if proxy else "direct (unpinned — jurisdiction not exit-verified)"
    mounts = proxy_mounts(proxy)
    try:
        out = await trace_chain(src, device=device, max_hops=max_hops,
                                timeout_s=timeout_s, extra_headers=headers,
                                connect_s=connect_s, max_conn=max_conn, http2=http2,
                                retries=retries, per_host_rps=per_host_rps,
                                proxy_mounts=mounts, client=_shared_client)
        if len(out) == 5:
            hops, html, js_hit, botwall, timing_meta = out
        else:  # pragma: no cover — defensive
            hops, html, js_hit, botwall = out[0], out[1], out[2], out[3]
            timing_meta = {}
        needs_headless = bool((botwall or {}).get("needs_headless"))
    except Exception as e:  # noqa: BLE001 — never let one URL kill a bulk run
        from .models import Hop
        hops, html, js_hit, botwall, timing_meta = ([Hop(index=0, url=src, redirect_type="error", error=str(e)[:250])], "", False, {"botwall_score": 0, "needs_headless": False}, {})
        needs_headless = False
    chain.hops = hops
    chain.js_redirect_detected = js_hit
    chain.botwall = botwall or {"botwall_score": 0, "needs_headless": False}
    chain.needs_headless = bool(chain.botwall.get("needs_headless"))
    try:
        from playwright.async_api import async_playwright  # type: ignore
        chain.playwright_available = True
    except Exception:
        chain.playwright_available = False
    # headless escalation (hybrid only; http_only never launches a browser)
    if mode == "hybrid" and chain.needs_headless:
        try:
            furl, hhtml, avail = await try_headless_follow(hops[-1].url if hops else src, device=device)
            chain.playwright_available = avail and chain.playwright_available is not False or avail
            if hhtml:
                chain.headless_used = True
                html = hhtml
                if furl and furl != (hops[-1].url if hops else src):
                    from .models import Hop as H
                    hops.append(H(index=len(hops), url=furl, status=200, redirect_type="headless-final"))
                    chain.hops = hops
        except Exception:
            pass
    chain.final_url = chain.hops[-1].url if chain.hops else src
    chain.final_status = chain.hops[-1].status if chain.hops else None
    chain.revenue_verified = bool(row.get("revenue_verified", False) or chain.revenue_verified)
    chain.page_forensics = page_forensics(html, chain.final_url)
    try:
        cq = audit_content_quality(html or "", chain.final_url)
        # merchant-copy similarity needs the SOURCE review page; row may carry _review_html
        if row.get("_review_html"):
            cq["merchant_copy_similarity_pct"] = merchant_copy_similarity(row["_review_html"], html or "")
        chain.content_quality = cq
        chain.eeat = {"eeat_score_est": cq.get("eeat_score_est"), "testing_evidence": cq.get("testing_evidence_hits"),
                      "author_signals": cq.get("author_signals"), "basis": cq.get("eeat_basis")}
    except Exception:
        pass
    chain.consent_mode = detect_consent_and_gpc(html or "", None)
    try:
        chain.gpc_detected = bool(chain.consent_mode.get("gpc_acknowledged"))
    except Exception:
        pass
    chain.evidence = {"http_requests": len(chain.hops),
                      "dns_lookups": sum(1 for h in chain.hops if h.ip),
                      "dns_method": "DoH (Cloudflare/Google) wall-time — real; authoritative delta possible",
                      "bytes_downloaded": len(html or ""),
                      "execution_mode": mode,
                      "headless_used": chain.headless_used,
                      "playwright_available": chain.playwright_available,
                      "timing_honesty": (timing_meta or {"tcp_tls_est": "ESTIMATED via subtraction"}),
                      "proxy_exit": chain.proxy_exit_used}
    last = chain.hops[-1] if chain.hops else None
    if not chain.hops:
        chain.chain_ok, chain.broken_reason = False, "no-hops"
    elif last and last.error and "loop" in last.error:
        chain.chain_ok, chain.broken_reason = False, "redirect-loop"
    elif last and last.error:
        chain.chain_ok, chain.broken_reason = False, last.error[:200]
    elif last and last.status and last.status >= 400:
        chain.chain_ok, chain.broken_reason = False, f"final-http-{last.status}"
    else:
        chain.chain_ok, chain.broken_reason = True, ""
    tr = cfg.get("tracking_rules", {})
    chain = audit_param_survival(chain, tr.get("required_param_patterns", []))
    chain = audit_network_path(chain, tr.get("network_mappings", {}))
    chain = score_latency(chain, int(cfg.get("latency", {}).get("warn_threshold_ms", 900)),
                          int(cfg.get("latency", {}).get("drop_off_threshold_ms", 1800)))
    chain.offer_discrepancy = {"lander_bonus_text": extract_lander_bonus(html or "")}
    chain = offer_discrepancy(chain)
    chain = audit_compliance(chain, html or "", cfg.get("compliance_packs", {}), cfg.get("soft404_phrases", []))
    chain = await audit_adblock(chain, easylist_hosts=_easylist_hosts, itp_list=tr.get("itp_stripped_params", []))
    chain = inspect_deeplink(chain, html or "")
    chain = brand_alignment(chain, html or "")
    chain = score_revenue_and_health(chain)
    build_edge_patch(chain)
    build_vendor_ticket(chain)
    if cfg.get("audit", {}).get("_deep_xdevice"):
        alt = "mobile_ios" if device.startswith("desktop") else "desktop_chrome"
        try:
            out2 = await trace_chain(src, device=alt, max_hops=max_hops,
                                     timeout_s=timeout_s, extra_headers=spoof_headers(alt),
                                     connect_s=connect_s, max_conn=max_conn, http2=http2,
                                     retries=retries, per_host_rps=per_host_rps,
                                     proxy_mounts=mounts, client=_shared_client)
            hops2 = out2[0]
            fin2 = hops2[-1].url if hops2 else ""
            from urllib.parse import urlparse as _up, parse_qsl as _pqs
            q1 = dict(_pqs(_up(chain.final_url).query))
            q2 = dict(_pqs(_up(fin2).query))
            tracked = [e.param for e in chain.param_events]
            kept1 = [k for k in q1 if any(k.lower() == t.lower() for t in tracked)]
            kept2 = [k for k in q2 if any(k.lower() == t.lower() for t in tracked)]
            chain.device_parity = {"primary_device": device, "alt_device": alt,
                                   "alt_final_url": fin2,
                                   "alt_status": hops2[-1].status if hops2 else None,
                                   "same_final": fin2.split("?")[0] == chain.final_url.split("?")[0],
                                   "params_survive_on_alt": kept2,
                                   "parity_ok": (fin2.split("?")[0] == chain.final_url.split("?")[0]
                                                 and set(k.lower() for k in kept1) == set(k.lower() for k in kept2))}
            chain.evidence["http_requests"] += len(hops2)
        except Exception as e:  # noqa: BLE001
            chain.device_parity = {"parity_ok": False, "note": f"alt-device trace failed: {str(e)[:150]}"}
    return chain


async def run_bulk_audit(rows: list[dict], cfg: dict, progress=None) -> list[ChainResult]:
    """Shared pooled client + one EasyList load; per-host throttle + robots toggle respected by callers."""
    import httpx
    from .easylist import load_easylist
    audit_cfg = cfg.get("audit", {}) or {}
    sem = asyncio.Semaphore(int(audit_cfg.get("concurrency", 8)))
    max_conn = int(audit_cfg.get("max_connections", 50))
    http2 = bool(audit_cfg.get("http2", True))
    timeout_s = int(audit_cfg.get("timeout_seconds", 20))
    state = {"done": 0}
    try:
        easylist_hosts = await load_easylist()
    except Exception:
        easylist_hosts = []
    limits = httpx.Limits(max_connections=max_conn, max_keepalive_connections=min(20, max_conn))
    try:
        shared = httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(timeout_s, connect=10.0),
                                   limits=limits, http2=http2)
    except Exception:
        shared = httpx.AsyncClient(follow_redirects=False, timeout=timeout_s)

    async def _one(r: dict) -> ChainResult:
        async with sem:
            if progress:
                progress({"event": "start", "url": r.get("source_url", "")})
            c = await audit_single_url(r, cfg, _shared_client=shared, _easylist_hosts=easylist_hosts)
            state["done"] += 1
            if progress:
                progress({"event": "done", "url": c.source_url, "index": state["done"],
                          "healthy": bool(c.chain_ok and c.params_intact),
                          "ms": round(c.latency.total_ms, 1),
                          "reason": c.broken_reason or "ok"})
            return c

    try:
        return list(await asyncio.gather(*[_one(r) for r in rows]))
    finally:
        try:
            await shared.aclose()
        except Exception:
            pass
