"""FastAPI — enterprise REST surface + 3-layer web app host + live jobs + SSE + monitoring + docs + PDF."""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import time as _time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from fastapi import FastAPI, Query, Request, Depends, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .orchestrator import load_config, audit_single_url, run_bulk_audit
from .revenue import network_sla_matrix
from .alerting_export import build_alerts, jira_tickets, post_alerts
from .input_ingest import fetch_sitemap_urls
from .autofill import profile_site, _check_sitemap
from .site_content import SECTIONS, NAV
from .export_pdf import build_pdf
from .ssrf import assert_safe_url, MAX_FETCH_BYTES
from .job_store import job_create, job_get, job_log, job_update, schedule_save, schedule_load
from .multitenant import check_scope, configured_keys
from .observability import metrics_payload, init_tracing
from .mcp import MCP_MANIFEST, dispatch as mcp_dispatch

init_tracing()

logger = logging.getLogger("pathfinder")
logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}')
CFG_PATH = os.environ.get("PATHFINDER_CFG", "config/enterprise.yaml")

# ---- optional rate limiting (slowapi) ----
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])
    _RATE_OK = True
except Exception:
    limiter = None
    _RATE_OK = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        cfg = load_config(CFG_PATH)
        from .monitoring import start_scheduler
        start_scheduler(app, cfg)
    except Exception as e:
        logger.warning("scheduler not started: %s", e)
    yield
    try:
        sched = getattr(app.state, "scheduler", None)
        if sched:
            sched.shutdown()
    except Exception:
        pass

app = FastAPI(title="Outbound Affiliate Link Chain & Latency Tracker", version="3.0.0", lifespan=lifespan)
if _RATE_OK:
    app.state.limiter = limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    app.add_middleware(SlowAPIMiddleware)
    @app.exception_handler(RateLimitExceeded)
    async def _rl(req, exc):
        return JSONResponse({"error": "rate-limited — slow down"}, status_code=429)

def _cors_origins() -> list[str]:
    try:
        cfg = load_config(CFG_PATH)
        o = ((cfg.get("security", {}) or {}).get("cors_allow_origins", [])) or []
        if o:
            return o
    except Exception:
        pass
    return ["http://localhost:8099", "http://127.0.0.1:8099"]
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins(), allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "HEAD"], allow_headers=["*"])

_ROOT = Path(__file__).resolve().parent.parent.parent
_FRONTEND = _ROOT / "frontend"
if not _FRONTEND.exists():
    _FRONTEND = Path("frontend").resolve()
if _FRONTEND.exists():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND), html=True), name="app")
_SHOTS = _ROOT / "output" / "shots"
try:
    _SHOTS.mkdir(parents=True, exist_ok=True)
    app.mount("/shots", StaticFiles(directory=str(_SHOTS)), name="shots")
except Exception:
    pass


def _api_keys() -> list[str]:
    try:
        cfg = load_config(CFG_PATH)
        keys = ((cfg.get("security", {}) or {}).get("api_keys", []) or [])
    except Exception:
        keys = []
    env = os.environ.get("PATHFINDER_API_KEYS", "")
    if env:
        keys = keys + [k.strip() for k in env.split(",") if k.strip()]
    return keys


async def _auth(request: Request, x_api_key: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    """Default auth (audit scope) — back-compat wrapper around _auth_scope."""
    return await _auth_scope("audit")(
        request, x_api_key=x_api_key, authorization=authorization)


# ---- per-key rate limit (in-memory token bucket, 60/min) + request size cap ----
_KEY_BUCKETS: dict[str, list[float]] = {}
_KEY_RATE_LIMIT = 60
_KEY_WINDOW_S = 60.0
_MAX_BODY_BYTES = 2 * 1024 * 1024


def _extract_token(x_api_key: str | None, authorization: str | None) -> str:
    tok = (x_api_key or "").strip()
    if not tok and authorization and authorization.lower().startswith("bearer "):
        tok = authorization[7:].strip()
    return tok


def _enforce_per_key_rate(tok: str):
    key = tok or "anon"
    now = _time.monotonic()
    hits = _KEY_BUCKETS.get(key, [])
    hits = [t for t in hits if now - t < _KEY_WINDOW_S]
    if len(hits) >= _KEY_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="per-key rate limit exceeded (60/min)")
    hits.append(now)
    _KEY_BUCKETS[key] = hits


def _enforce_size(request: Request | None):
    try:
        cl = request.headers.get("content-length") if request is not None else None
        if cl and int(cl) > _MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="payload too large (2MB cap)")
    except HTTPException:
        raise
    except Exception:
        pass


def _auth_scope(need: str):
    """Dependency factory: enforce per-key rate limit + size cap + scope check.

    Returns the workspace string. Open-dev-mode (no keys configured) allows all.
    """
    async def _dep(request: Request,
                   x_api_key: str | None = Header(default=None),
                   authorization: str | None = Header(default=None)):
        tok = _extract_token(x_api_key, authorization)
        _enforce_per_key_rate(tok)
        _enforce_size(request)
        ok, ws = check_scope(tok or "", need)
        if ok:
            return ws or "default"
        if not tok:
            raise HTTPException(status_code=401, detail="invalid or missing API key (X-API-Key)")
        # distinguish unknown key (401) from known key lacking scope (403)
        try:
            known = any(e.get("key") == tok for e in configured_keys())
        except Exception:
            known = False
        if known:
            raise HTTPException(status_code=403, detail=f"key lacks '{need}' scope")
        raise HTTPException(status_code=401, detail="invalid or missing API key (X-API-Key)")
    return _dep


def _redact(obj, params: list[str] | None = None):
    """Redact credential-ish query params from logged/returned URLs."""
    try:
        cfg = load_config(CFG_PATH)
        plist = ((cfg.get("security", {}) or {}).get("redact_query_params", []) or []) if params is None else params
    except Exception:
        plist = ["btag", "affid", "clickid", "subid", "token", "key"]
    if isinstance(obj, str):
        for p in plist:
            obj = re.sub(rf"([?&]{p}=)[^& ]+", r"\1***", obj, flags=re.I)
        return obj
    return obj


class SingleAuditIn(BaseModel):
    source_url: str
    anchor_text: str = ""
    expected_operator: str = ""
    expected_network: str = ""
    geo: str = "US-NJ"
    device: str = "desktop_chrome"
    clicks_30d: float | None = None
    epc: float | None = None
    clicks_basis: str = ""
    epc_basis: str = ""
    bonus_text_on_site: str = ""
    model_config = {"extra": "allow"}


class FullAuditIn(BaseModel):
    targets: list[SingleAuditIn] = []
    pasted_urls: str = ""
    sitemap_urls: list[str] = []
    sitemap_limit: int = 50
    global_geo: str = "US-NJ"
    global_device: str = "desktop_chrome"
    settings: dict = {}
    model_config = {"extra": "allow"}


class AutofillIn(BaseModel):
    site_url: str
    max_pages: int = 25


def _deep_merge(base: dict, override: dict) -> dict:
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        elif v is not None:
            base[k] = v
    return base


def _clean_lines(text) -> list[str]:
    """Field lines minus blanks, #-comment lines and trailing '  # status' notes."""
    lines = text if isinstance(text, list) else (text or "").splitlines()
    out = []
    for ln in lines:
        s = (ln or "").strip()
        if not s or s.startswith("#"):
            continue
        s = re.sub(r"\s{2,}#.*$", "", s).strip().strip(",")
        if s:
            out.append(s)
    return out


async def _expand_rows(body: FullAuditIn, cfg: dict, log=None) -> list[dict]:
    rows = [r.model_dump() for r in body.targets]
    if log:
        log(f"seeded {len(rows)} table targets")
    n_paste = 0
    for u in _clean_lines(body.pasted_urls):
        if u.startswith("http"):
            try:
                assert_safe_url(u)
            except ValueError as e:
                if log:
                    log(f"blocked pasted URL (SSRF guard): {u[:110]} — {e}")
                else:
                    logger.warning("blocked pasted URL (SSRF guard): %s — %s", u[:110], e)
                continue
            rows.append({"source_url": u, "geo": body.global_geo, "device": body.global_device,
                         "clicks_30d": None, "epc": None,
                         "clicks_basis": "UNKNOWN", "epc_basis": "UNKNOWN"})
            n_paste += 1
    if log:
        log(f"added {n_paste} pasted URLs")
    for sm in _clean_lines(body.sitemap_urls):
        try:
            assert_safe_url(sm)
        except ValueError as e:
            if log:
                log(f"blocked sitemap URL (SSRF guard): {sm[:110]} — {e}")
            else:
                logger.warning("blocked sitemap URL (SSRF guard): %s — %s", sm[:110], e)
            continue
        if log:
            log(f"expanding sitemap {sm} …")
        urls = await fetch_sitemap_urls(sm, limit=body.sitemap_limit)
        if log:
            log(f"sitemap yielded {len(urls)} URLs")
        for u in urls:
            rows.append({"source_url": u, "geo": body.global_geo, "device": body.global_device,
                         "clicks_30d": None, "epc": None})
    return rows


def _bundle(chains, cfg) -> dict:
    sla = network_sla_matrix(chains)
    alerts = []
    for c in chains:
        alerts.extend(build_alerts(c, int(cfg.get("alerting", {}).get("break_on_high_value_clicks", 5000))))
    unverified = sum(1 for c in chains if (c.revenue_basis or "").startswith("UNKNOWN"))
    return {"chains": [c.model_dump() for c in chains], "sla": sla, "alerts": alerts,
            "jira": jira_tickets(chains),
            "revenue_at_risk": round(sum(c.revenue_at_risk for c in chains), 2),
            "revenue_range": [round(sum(c.revenue_at_risk_low for c in chains), 2),
                              round(sum(c.revenue_at_risk_high for c in chains), 2)],
            "revenue_honesty": (f"{unverified}/{len(chains)} chains UNKNOWN — supply EPC/clicks or import affiliate/GA4 data" if unverified
                                else "all chains carry ESTIMATED (±40%) or VERIFIED (±15%) basis — see per-chain revenue_basis"),
            "health_index": round(sum(c.health_score for c in chains) / max(1, len(chains)), 1)}


# ---------------- live jobs (SSE stream + persistent store: Redis/SQLite, survives restart) ----------------
# P0 fix: was in-memory JOBS dict (lost on restart) + unauthenticated SSE stream.
# JOBS kept as read-through alias for back-compat; authoritative store is job_store.
JOBS: dict = {}
from . import job_store as _JS

def _job(jid: str) -> dict | None:
    j = _JS.job_get(jid)
    if j is not None and jid not in JOBS:
        JOBS[jid] = j
    return JOBS.get(jid) or j


async def _run_job(jid: str, payload: dict):
    job = _job(jid) or JOBS.get(jid)
    t0 = job["t0"]

    def log(msg: str):
        m = f"[{_time.perf_counter() - t0:7.1f}s] {_redact(msg)}"
        job["logs"].append(m)
        if len(job["logs"]) > 2000:
            job["logs"] = job["logs"][-2000:]
        job_update(jid, logs=job["logs"])

    try:
        body = FullAuditIn(**payload)
        cfg = _deep_merge(load_config(CFG_PATH), body.settings or {})
        log(f"engine start — mode={cfg.get('audit', {}).get('execution_mode')} "
            f"max_hops={cfg.get('audit', {}).get('max_hops')} "
            f"concurrency={cfg.get('audit', {}).get('concurrency')}")
        rows = await _expand_rows(body, cfg, log=log)
        if not rows:
            job.update(status="error", error="no targets — add URLs, paste a list, upload CSV or give a sitemap")
            return
        job["total"] = len(rows)
        deep = cfg.get("audit", {}).get("deep_cross_device", "auto")
        cfg["audit"]["_deep_xdevice"] = (len(rows) <= 12) if deep == "auto" else bool(deep)
        if cfg["audit"]["_deep_xdevice"]:
            log("deep mode ON — every chain re-traced on the opposite device (2× evidence)")
        log(f"auditing {len(rows)} target(s) …")

        def prog(e: dict):
            job["done"] = e.get("index", job["done"])
            if e["event"] == "start":
                job["logs"].append(f"[{_time.perf_counter() - t0:7.1f}s] ▶ tracing {_redact(e['url'][:110])}")
            else:
                mark = "OK " if e.get("healthy") else "FAIL"
                job["logs"].append(f"[{_time.perf_counter() - t0:7.1f}s] [{mark} {e['index']}/{job['total']}] "
                                   f"{_redact(e['url'][:90])} · {e.get('ms')} ms · {e.get('reason')}")

        chains = await run_bulk_audit(rows, cfg, progress=prog)
        # screenshot evidence (best-effort, never fails the job)
        if cfg.get("audit", {}).get("screenshot_evidence"):
            try:
                from .screenshots import capture_shot
                for i, c in enumerate(chains):
                    if c.final_url and c.final_status and c.final_status < 400:
                        out = str(_SHOTS / jid / f"{i}_{c.device}.png")
                        shot = await capture_shot(c.final_url, c.device, out)
                        c.screenshots = {"job_id": jid, "png": f"/shots/{jid}/{i}_{c.device}.png" if shot.get("taken") else "",
                                         "taken": shot.get("taken"), "reason": shot.get("reason", "")}
            except Exception as e:
                log(f"screenshots skipped: {type(e).__name__}")
        out = _bundle(chains, cfg)
        out["effective_settings"] = {"execution_mode": cfg.get("audit", {}).get("execution_mode"),
                                     "max_hops": cfg.get("audit", {}).get("max_hops"),
                                     "concurrency": cfg.get("audit", {}).get("concurrency")}
        # persist to sqlite (single persistence layer — API never writes output/*.json per job)
        try:
            from .persistence import save_run, enforce_retention
            db_path = cfg.get("monitoring", {}).get("db_path", "output/pathfinder.db")
            save_run(db_path, jid, t0,
                     [c.model_dump() for c in chains], out)
            try:
                enforce_retention(db_path)
            except Exception:
                pass
        except Exception as e:
            log(f"persistence skipped: {e}")
        # fire webhooks
        try:
            receipt = await post_alerts(out["alerts"], cfg)
            out["alert_delivery"] = receipt
            log(f"alert delivery: {receipt}")
        except Exception:
            pass
        job["result"] = out
        job["status"] = "done"
        job_update(jid, status="done", result=out, done=job.get("done", job.get("total", 0)),
                   elapsed=round(_time.perf_counter() - t0, 1))
        log(f"complete — revenue at risk ${out['revenue_at_risk']:,.2f} (range ${out['revenue_range'][0]:,.2f}-${out['revenue_range'][1]:,.2f}) · health {out['health_index']}/100")
    except Exception as e:  # noqa: BLE001
        job.update(status="error", error=str(e)[:500])
        job_update(jid, status="error", error=str(e)[:500])
        log(f"ERROR {e}")
    job["elapsed"] = round(_time.perf_counter() - t0, 1)


@app.post("/audit/job")
async def start_job(body: FullAuditIn, _=Depends(_auth_scope("audit"))):
    jid = uuid.uuid4().hex[:12]
    job = job_create(jid)
    job["t0"] = _time.perf_counter()
    JOBS[jid] = job
    asyncio.create_task(_run_job(jid, body.model_dump()))
    return {"job_id": jid}


@app.get("/audit/job/{jid}")
async def job_status(jid: str, _=Depends(_auth)):
    job = _job(jid)
    if not job:
        return {"status": "unknown"}
    return {"status": job["status"], "total": job["total"], "done": job["done"],
            "elapsed": round(_time.perf_counter() - job["t0"], 1),
            "logs": job["logs"][-400:], "error": job["error"],
            "result": job["result"] if job["status"] == "done" else None}


@app.get("/audit/job/{jid}/stream")
async def job_stream(jid: str, request: Request, _=Depends(_auth)):
    """SSE live log — auth-required (P0 fix), Last-Event-ID resume, persistent backend."""
    try:
        last_id = int(request.headers.get("last-event-id", "0") or 0)
    except Exception:
        last_id = 0
    async def gen():
        last = last_id
        eid = last_id
        for i in range(3600):
            if i > 0 and i % 15 == 0:
                yield ": keepalive\n\n"
            job = _job(jid)
            if not job:
                eid += 1
                yield f"id: {eid}\ndata: {{\"status\":\"unknown\"}}\n\n"
                return
            logs = job["logs"][last:]
            last = len(job["logs"])
            payload = json.dumps({"status": job["status"], "done": job["done"], "total": job["total"],
                                  "logs": logs[-50],
                                  "result": job["result"] if job["status"] == "done" else None})
            eid += 1
            yield f"id: {eid}\ndata: {payload}\n\n"
            if job["status"] in ("done", "error"):
                return
            await asyncio.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------- docs (auto-updating) ----------------
@app.get("/content")
def content():
    return {"tool": "Outbound Affiliate Link Chain & Latency Tracker",
            "nav": NAV, "sections": SECTIONS}


@app.get("/llms.txt")
def llms_txt():
    return Response("User-agent: *\nAllow: /\n\n# Canonical docs\n- /content — all tool docs (JSON)\n- /docs — OpenAPI\n- /app/ — web UI (3-layer inputs: Essentials, Verification, Advanced)\nContact: publisher affiliate engineering\n",
                    media_type="text/plain")


@app.get("/openapi-example")
def openapi_example():
    return {"single": {"source_url": "https://publisher.com/out/bet365?btag=123&a_aid=1&subid=test",
                       "expected_operator": "bet365", "geo": "US-NJ", "device": "desktop_chrome"},
            "full": {"pasted_urls": "https://publisher.com/out/bet365?btag=123\n", "global_geo": "US-NJ",
                     "global_device": "desktop_chrome", "sitemap_limit": 50, "settings": {}}}


# ---------------- real PDF ----------------
@app.post("/export/pdf")
async def export_pdf(body: dict, _=Depends(_auth_scope("export"))):
    pdf = build_pdf(body.get("result", {}) or {}, body.get("input") or {})
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": "attachment; filename=affiliate-audit-report.pdf"})


@app.post("/export/jira")
async def export_jira(body: dict, _=Depends(_auth_scope("export"))):
    """Push the first ticket via Jira API (rest markdown in payload)."""
    from .ticketing import push_jira
    tickets = (body.get("result", {}) or {}).get("jira", []) or []
    if not tickets:
        return {"pushed": False, "reason": "no tickets — all chains healthy"}
    t = tickets[0]
    r = await push_jira(t["summary"], t["description"], t.get("priority", "High"))
    return {"pushed": r.get("pushed"), "detail": r, "remaining": len(tickets) - 1}


class VerifyIn(BaseModel):
    sitemaps: list[str] = []
    urls: list[str] = []
    regex_ids: str = ""
    regex_sub: str = ""
    regex_promo: str = ""
    netmap: list[str] = []
    feeds: list[str] = []
    baselines: list[str] = []
    default_epc: float | None = None


def _rx_ok(pattern: str) -> dict:
    try:
        re.compile(pattern or "")
        return {"ok": True, "error": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:160]}


@app.post("/utils/verify-inputs")
async def verify_inputs(body: VerifyIn):
    """Verify every input field live — sitemaps, URLs, regexes, mappings, feeds, baselines."""
    out: dict = {}
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=12, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0"}) as client:
        out["sitemaps"] = []
        for sm in _clean_lines(body.sitemaps)[:20]:
            try:
                assert_safe_url(sm)
                st = await _check_sitemap(client, sm)
            except ValueError as e:
                st = {"url": sm, "live": False, "http": None, "note": f"SSRF guard: {e}"}
            # cap body reads already handled in _check_sitemap path; note cap here
            out["sitemaps"].append(st)

        async def _url(u: str) -> dict:
            async with sem:
                d = {"url": u, "live": False, "http": None, "final": "", "note": ""}
                try:
                    assert_safe_url(u)
                    r = await client.head(u)
                    if r.status_code in (405, 501):
                        r = await client.get(u)
                    try:
                        cl = int(r.headers.get("content-length", "0") or 0)
                        if cl > MAX_FETCH_BYTES:
                            d.update(http=r.status_code, final=str(r.url), live=False,
                                     note=f"body {cl} bytes exceeds 5MB cap — NOT fetched")
                            return d
                    except Exception:
                        pass
                    d.update(http=r.status_code, final=str(r.url),
                             live=r.status_code < 400,
                             note="LIVE" if r.status_code < 400 else f"HTTP {r.status_code} — NOT reachable")
                except ValueError as e:
                    d["note"] = f"SSRF guard: {e}"
                except Exception as e:  # noqa: BLE001
                    d["note"] = f"{type(e).__name__} — NOT reachable"
                return d
        out["urls"] = await asyncio.gather(*[_url(u) for u in _clean_lines(body.urls)[:80]])

        async def _feed(f: str) -> dict:
            async with sem:
                try:
                    assert_safe_url(f)
                    r = await client.get(f)
                    if len(r.content or b"") > MAX_FETCH_BYTES:
                        return {"url": f, "live": False, "http": r.status_code, "note": "exceeds 5MB cap"}
                    return {"url": f, "live": r.status_code < 400, "http": r.status_code}
                except ValueError as e:
                    return {"url": f, "live": False, "http": None, "note": f"SSRF guard: {e}"}
                except Exception:
                    return {"url": f, "live": False, "http": None}
        out["feeds"] = await asyncio.gather(*[_feed(f) for f in _clean_lines(body.feeds)[:20]])
    out["regex"] = {"ids": _rx_ok(body.regex_ids), "sub": _rx_ok(body.regex_sub),
                    "promo": _rx_ok(body.regex_promo)}
    parsed, bad = 0, []
    for ln in _clean_lines(body.netmap):
        if "=" in ln and ln.split("=")[1].strip():
            parsed += 1
        else:
            bad.append(ln[:80])
    out["netmap"] = {"ok": not bad, "parsed": parsed, "bad": bad[:10]}
    prow, bbad = 0, []
    for ln in _clean_lines(body.baselines):
        try:
            _, ctr, ctd = [x.strip() for x in ln.split(",")]
            float(ctr)
            float(ctd)
            prow += 1
        except Exception:
            bbad.append(ln[:80])
    out["baselines"] = {"ok": not bbad, "parsed": prow, "bad": bbad[:10]}
    out["epc_ok"] = (body.default_epc or 0) > 0
    out["epc_note"] = ("explicit EPC supplied" if out["epc_ok"]
                       else "no EPC — revenue will be UNKNOWN until affiliate/GA4 import (no $1.25 default)")
    return out


# ---------------- F11 + CWV + imports + monitoring ----------------
@app.post("/audit/ai-visibility")
async def ai_visibility(body: dict, _=Depends(_auth)):
    from .geo_ai import audit_ai_visibility
    url = body.get("url", "")
    try:
        assert_safe_url(url)
    except ValueError as e:
        return {"url": url, "error": f"SSRF guard: {e}"}
    html = ""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get(url)
            raw = r.content or b""
            if len(raw) > MAX_FETCH_BYTES:
                return {"url": url, "error": "page exceeds 5MB fetch cap — NOT fetched (SSRF/size guard)"}
            html = raw.decode("utf-8", errors="ignore")[:300000]
    except Exception as e:
        return {"url": url, "error": f"{type(e).__name__}: {e}"}
    return await audit_ai_visibility(url, html)


@app.post("/audit/sov")
async def sov_endpoint(body: dict, _=Depends(_auth)):
    """Prompt-level Share of Voice (real GEO). Body: {brand, geo, prompts?}."""
    from .sov import run_sov
    return await run_sov(body.get("brand", ""), body.get("geo", "US-NJ"), body.get("prompts"))


@app.post("/utils/postback-validate")
async def postback_validate(body: dict, _=Depends(_auth)):
    """S2S postback validator (Voluum parity): checks txid/clickid + payout presence."""
    from .affiliate_sync import validate_postback
    return validate_postback(body.get("url", ""))


@app.get("/utils/affiliate-live")
async def affiliate_live(platform: str = Query("everflow"), _=Depends(_auth)):
    """Live affiliate API status/pull (PRIMARY) — CSV import is fallback."""
    from .affiliate_sync import pull_platform_summary
    return await pull_platform_summary(platform)


@app.get("/utils/traffic-live")
async def traffic_live(site: str = Query(""), days: int = 28, _=Depends(_auth)):
    """Live GA4 + GSC pulls (PRIMARY) — CSV import is fallback."""
    from .ga4_gsc import ga4_live_clicks, gsc_live_clicks
    return {"ga4": await ga4_live_clicks([], days), "gsc": await gsc_live_clicks(site, days)}


@app.get("/metrics")
async def metrics():
    body, ctype = metrics_payload()
    return Response(content=body, media_type=ctype)


@app.get("/.well-known/mcp.json")
async def mcp_manifest():
    return MCP_MANIFEST


@app.post("/mcp")
async def mcp_endpoint(body: dict, _=Depends(_auth_scope("mcp"))):
    """MCP JSON-RPC 2.0: {jsonrpc, id, method: tools/list|tools/call, params}."""
    method = body.get("method", "")
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": body.get("id"), "result": {"tools": MCP_MANIFEST["tools"]}}
    if method == "tools/call":
        p = body.get("params", {}) or {}
        cfg = load_config(CFG_PATH)
        res = await mcp_dispatch(p.get("name", ""), p.get("arguments", {}) or {}, cfg)
        return {"jsonrpc": "2.0", "id": body.get("id"), "result": res}
    return {"jsonrpc": "2.0", "id": body.get("id"),
            "error": {"code": -32601, "message": f"unknown method {method}"}}


@app.get("/utils/crux")
async def crux(url: str = Query(...), form: str = "PHONE"):
    from .crux_psi import fetch_crux
    return await fetch_crux(url, form)


@app.post("/utils/psi")
async def psi(body: dict, _=Depends(_auth)):
    from .crux_psi import fetch_psi
    return await fetch_psi(body.get("url", ""), body.get("strategy", "mobile"))


@app.post("/utils/affiliate-import")
async def affiliate_import(body: dict, _=Depends(_auth)):
    """Import affiliate stats CSV -> EPC map. Body: {platform, csv_text, date_range}."""
    from .affiliate_sync import map_affiliate_csv
    m = map_affiliate_csv(body.get("csv_text", ""), body.get("platform", "everflow"))
    return {"platform": body.get("platform"), "campaigns": len(m), "map": dict(list(m.items())[:50]),
            "note": "pass this map as _epc_map per target (or via schedule targets) to flip revenue UNKNOWN->VERIFIED"}


@app.post("/utils/traffic-import")
async def traffic_import(body: dict, _=Depends(_auth)):
    """Import GA4/GSC CSV (page,clicks) -> traffic map for clicks_30d VERIFIED fill."""
    from .ga4_gsc import map_traffic_csv
    m = map_traffic_csv(body.get("csv_text", ""))
    return {"pages": len(m), "map": dict(list(m.items())[:50]),
            "note": "pass as _traffic_map per target to flip clicks UNKNOWN->VERIFIED"}


@app.get("/utils/ga4-status")
async def ga4_status_ep():
    from .ga4_gsc import ga4_status
    return ga4_status()


@app.post("/audit/schedule")
async def audit_schedule(body: dict, _=Depends(_auth_scope("admin"))):
    """Save/update the recurring monitor target list (DB-backed — P0 fix, was YAML rewrite race)."""
    return schedule_save(int(body.get("interval_minutes", 60)), body.get("targets", []))


@app.get("/audit/schedule")
async def audit_schedule_get(_=Depends(_auth_scope("admin"))):
    return schedule_load()


@app.get("/monitor/runs")
async def monitor_runs(limit: int = 20):
    from .persistence import list_runs
    cfg = load_config(CFG_PATH)
    return {"runs": list_runs(cfg.get("monitoring", {}).get("db_path", "output/pathfinder.db"), limit)}


@app.get("/monitor/diff")
async def monitor_diff():
    from .persistence import diff_last_two
    cfg = load_config(CFG_PATH)
    return diff_last_two(cfg.get("monitoring", {}).get("db_path", "output/pathfinder.db"))


@app.delete("/monitor/runs/{run_id}")
async def monitor_run_delete(run_id: str, _=Depends(_auth)):
    from .persistence import delete_run
    cfg = load_config(CFG_PATH)
    ok = delete_run(cfg.get("monitoring", {}).get("db_path", "output/pathfinder.db"), run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="run not found")
    return {"deleted": True, "run_id": run_id}


# ---------------- web app at domain root ----------------
# The 3-page frontend lives at "/" (so https://<host>/ IS the tool — no /app suffix).
# /app/* is kept as a backward-compatible alias. The "/" static mount is registered
# LAST in this file so every API route above (/audit/*, /mcp, /utils/*, /metrics,
# /docs, /shots, /content …) matches first and is never swallowed by static files.
@app.get("/dashboard", include_in_schema=False)
def dashboard_deprecated():
    """Legacy dashboard/dashboard.html duplicate — canonical is frontend/ 3-page app (P0 dedupe)."""
    return RedirectResponse("/", status_code=301)


@app.get("/health")
def health():
    try:
        from playwright.async_api import async_playwright  # type: ignore
        pw = True
    except Exception:
        pw = False
    try:
        import curl_cffi  # type: ignore
        tls = True
    except Exception:
        tls = False
    return {"status": "ok", "service": "outbound-affiliate-link-chain-latency-tracker", "version": "3.1.0",
            "playwright": pw, "tls_impersonation": tls, "rate_limit": _RATE_OK,
            "pg_dsn_configured": bool(os.environ.get("PATHFINDER_PG_DSN")),
            "redis_configured": bool(os.environ.get("REDIS_URL"))}


@app.get("/config/defaults")
def config_defaults(_=Depends(_auth)):
    cfg = load_config(CFG_PATH)
    # never leak webhooks/keys/proxy creds
    for section in ("alerting", "security"):
        for k in ("slack_webhook", "teams_webhook", "api_keys",):
            if section in cfg and k in cfg[section]:
                cfg[section][k] = "***" if cfg[section][k] else ""
    for g, p in (cfg.get("geo_profiles", {}) or {}).items():
        if p.get("proxy_exit"):
            p["proxy_exit"] = "***configured***"
    return cfg


@app.get("/utils/sitemap")
async def expand_sitemap(url: str = Query(...), limit: int = 100):
    try:
        assert_safe_url(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"SSRF guard: {e}")
    urls = await fetch_sitemap_urls(url, limit=limit)
    return {"sitemap": url, "count": len(urls), "urls": urls}


@app.post("/utils/autofill")
async def autofill_from_site(body: AutofillIn, _=Depends(_auth)):
    try:
        assert_safe_url(body.site_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"SSRF guard: {e}")
    return await profile_site(body.site_url, max_pages=max(1, min(body.max_pages, 500)))


@app.post("/audit")
async def audit_one(body: SingleAuditIn, _=Depends(_auth_scope("audit"))):
    try:
        assert_safe_url(body.source_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"SSRF guard: {e}")
    cfg = load_config(CFG_PATH)
    chain = await audit_single_url(body.model_dump(), cfg)
    return chain.model_dump()


@app.post("/audit/bulk")
async def audit_bulk(rows: list[SingleAuditIn], _=Depends(_auth_scope("audit"))):
    for r in rows:
        try:
            assert_safe_url(r.source_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"SSRF guard: {e}")
    cfg = load_config(CFG_PATH)
    chains = await run_bulk_audit([r.model_dump() for r in rows], cfg)
    return _bundle(chains, cfg)


@app.post("/audit/full")
async def audit_full(body: FullAuditIn, _=Depends(_auth_scope("audit"))):
    for r in body.targets:
        try:
            assert_safe_url(r.source_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"SSRF guard: {e}")
    cfg = _deep_merge(load_config(CFG_PATH), body.settings or {})
    rows = await _expand_rows(body, cfg)
    if not rows:
        return {"error": "no targets — add URLs, paste a list, upload CSV or give a sitemap"}
    deep = cfg.get("audit", {}).get("deep_cross_device", "auto")
    cfg["audit"]["_deep_xdevice"] = (len(rows) <= 12) if deep == "auto" else bool(deep)
    chains = await run_bulk_audit(rows, cfg)
    out = _bundle(chains, cfg)
    out["effective_settings"] = {"execution_mode": cfg.get("audit", {}).get("execution_mode"),
                                  "max_hops": cfg.get("audit", {}).get("max_hops"),
                                  "concurrency": cfg.get("audit", {}).get("concurrency")}
    return out


# NOTE: keep this mount LAST — Starlette matches routes in registration order, so the
# frontend directory served at "/" must come after every API route above. html=True
# serves index.html at exactly "/", and /analysis.html, /outputs.html, /info.html,
# /styles.css, /common.js resolve as plain files. All page links and API calls are
# relative/same-origin, so the tool works identically at / and at the /app/ alias.
if _FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND), html=True), name="root-app")
