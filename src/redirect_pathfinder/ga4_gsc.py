"""GA4 + GSC ingestion — kill the biggest honesty gap (invented clicks_30d).

Priority (2026): LIVE APIs primary, CSV fallback:
- GA4 Data API (GA4_PROPERTY_ID + GOOGLE_APPLICATION_CREDENTIALS): pull sessions/
  clicks per landing page for the last 28d and auto-fill clicks_30d (VERIFIED).
- GSC Search Analytics (GSC_SITE_URL + creds): clicks/impressions per review page +
  AI Overview visibility hint via regex on query (ESTIMATED hint, verify in GSC UI).
- CSV fallback: paste a GA4/GSC export (page,clicks) and map by URL substring.
Without creds everything returns {configured:false} — never synthetic traffic.
"""
from __future__ import annotations
import asyncio
import csv
import inspect
import io
import os
import random
from urllib.parse import urlparse


def map_traffic_csv(text: str) -> dict:
    """Parse 'page,clicks[,impressions]' export -> {url_substring: clicks}."""
    out: dict = {}
    try:
        rdr = csv.DictReader(io.StringIO(text))
        cols = {c.lower().strip(): c for c in (rdr.fieldnames or [])}
        cp = cols.get("page") or cols.get("url") or cols.get("landing page")
        cc = cols.get("clicks") or cols.get("sessions") or cols.get("users")
        if not cp or not cc:
            return {}
        for row in rdr:
            try:
                out[str(row.get(cp, "")).strip()[:200]] = float(str(row.get(cc, 0) or 0).replace(",", "") or 0)
            except Exception:
                continue
    except Exception:
        pass
    return out


def apply_traffic(chain, traffic_map: dict, source: str = "GA4 export") -> object:
    best, best_v = "", 0.0
    for page, clicks in (traffic_map or {}).items():
        if page and page in chain.source_url and clicks > best_v:
            best, best_v = page, clicks
    if best:
        chain.clicks_30d = float(best_v)
        chain.clicks_basis = f"VERIFIED — {source} matched '{best[:80]}'"
    return chain


def ga4_status() -> dict:
    prop = os.environ.get("GA4_PROPERTY_ID", "")
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not prop or not creds:
        return {"configured": False,
                "reason": "GA4_PROPERTY_ID + GOOGLE_APPLICATION_CREDENTIALS not set — use CSV import (/utils/traffic-import)"}
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient  # type: ignore
        return {"configured": True, "property": prop, "client": "google-analytics-data available"}
    except Exception:
        return {"configured": True, "property": prop,
                "reason": "google-analytics-data not installed — pip install google-analytics-data"}


def _is_quota_error(exc: BaseException) -> bool:
    """True for retryable quota/rate-limit/transient backend errors (429/503)."""
    try:
        code = getattr(exc, "code", None) or getattr(getattr(exc, "resp", None), "status", None)
        if code in (429, 503):
            return True
    except Exception:
        pass
    msg = f"{type(exc).__name__}: {exc}"
    for token in ("429", "503", "rateLimitExceeded", "userRateLimitExceeded",
                  "quotaExceeded", "Quota exceeded", "Rate Limit Exceeded",
                  "ResourceExhausted", "ServiceUnavailable", "backendError",
                  "rate limit", "Rate limit"):
        if token in msg:
            return True
    return False


async def _with_backoff(coro_fn, retries: int = 4, base_s: float = 1.0):
    """Shared quota backoff: retry async work on 429/503 with exponential sleep.

    coro_fn: zero-arg callable returning an awaitable (re-invoked per attempt),
        or a sync callable, or an already-created awaitable (single attempt only).
    retries: number of retries after the first attempt (default 4 -> up to 5 tries).
    Never swallows the final error — re-raises for the caller to report honestly.
    """
    last_exc: BaseException | None = None
    attempts = max(1, int(retries) + 1)
    if not callable(coro_fn):
        # Already-created awaitable: can only be awaited once — no retry possible.
        if inspect.isawaitable(coro_fn):
            return await coro_fn
        return coro_fn
    for attempt in range(attempts):
        try:
            res = coro_fn()
            if inspect.isawaitable(res):
                res = await res
            return res
        except Exception as e:
            last_exc = e
            if not _is_quota_error(e) or attempt >= attempts - 1:
                raise
            sleep_s = float(base_s) * (2 ** attempt) + random.uniform(0, 0.5)
            try:
                await asyncio.sleep(sleep_s)
            except Exception:
                pass
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("_with_backoff: no attempts made")


def _norm_traffic_key(u: str) -> str:
    """Normalize a GA4 pagePath or GSC page URL to a joinable path key."""
    try:
        s = (u or "").strip()
        if not s:
            return "/"
        if "://" in s:
            p = urlparse(s).path or "/"
        else:
            p = s.split("?")[0].split("#")[0] or "/"
        if not p.startswith("/"):
            p = "/" + p
        if len(p) > 1 and p.endswith("/"):
            p = p.rstrip("/") or "/"
        return p[:200] or "/"
    except Exception:
        return "/"


def join_traffic(ga4_map: dict, gsc_map: dict) -> dict:
    """Join GA4↔GSC traffic on normalized path -> {path: {ga4_clicks, gsc_clicks, delta, basis}}.

    Both maps may be keyed by full URLs or pagePaths; keys are normalized via
    _norm_traffic_key (path only, trailing-slash stripped) and duplicate keys
    are summed before the join.
    Date note: GA4 sessions and GSC clicks use different attribution and date
    windows (each caller's `days` param) — delta = ga4_clicks - gsc_clicks is a
    directional sanity signal, not an exact reconciliation.
    """
    def _collapse(m: dict) -> dict[str, float]:
        out: dict[str, float] = {}
        for k, v in (m or {}).items():
            try:
                out[_norm_traffic_key(str(k))] = out.get(_norm_traffic_key(str(k)), 0.0) + float(v or 0)
            except Exception:
                continue
        return out
    ga = _collapse(ga4_map)
    gs = _collapse(gsc_map)
    basis = ("Joined on normalized path (no query/fragment, trailing-slash stripped). "
             "GA4 sessions vs GSC clicks differ in attribution + date window — "
             "delta is directional only; verify in GA4/GSC UI.")
    out: dict = {}
    for path in sorted(set(ga) | set(gs)):
        g, s = float(ga.get(path, 0.0)), float(gs.get(path, 0.0))
        out[path] = {"ga4_clicks": g, "gsc_clicks": s, "delta": g - s, "basis": basis}
    n_paths = len(out)
    out["_note"] = {"basis": basis, "paths": n_paths}
    return out


async def ga4_live_clicks(pages: list[str], days: int = 28) -> dict:
    """Live GA4 Data API pull: sessions per pagePath for last N days -> {page: clicks}.

    Requires GA4_PROPERTY_ID + GOOGLE_APPLICATION_CREDENTIALS + google-analytics-data.
    Honest {configured:false} when missing — never synthetic.
    """
    prop = os.environ.get("GA4_PROPERTY_ID", "")
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not prop or not creds:
        return {"configured": False, "reason": "GA4_PROPERTY_ID + GOOGLE_APPLICATION_CREDENTIALS not set"}
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient  # type: ignore
        from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest  # type: ignore
        client = BetaAnalyticsDataClient()
        req = RunReportRequest(property=f"properties/{prop}",
                               dimensions=[Dimension(name="pagePath")],
                               metrics=[Metric(name="sessions")],
                               date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")])

        async def _call():
            return await asyncio.to_thread(client.run_report, req)

        resp = await _with_backoff(_call, retries=4)
        out: dict[str, float] = {}
        for row in resp.rows or []:
            try:
                out[row.dimension_values[0].value] = float(row.metric_values[0].value)
            except Exception:
                continue
        return {"configured": True, "property": prop, "days": days, "pages": len(out),
                "map": dict(list(out.items())[:200]),
                "basis": "VERIFIED GA4 Data API sessions — use as clicks_30d via apply_traffic"}
    except Exception as e:
        return {"configured": True, "property": prop, "ok": False,
                "reason": f"{type(e).__name__}: {str(e)[:250]}"}


async def ga4_funnel(pages: list[str], days: int = 28) -> dict:
    """GA4 funnel helper: pivot-like per-page session counts for the given pages.

    Honest {configured:false} when no credentials — never synthetic.
    With credentials, pulls live pagePath sessions (same source as ga4_live_clicks)
    and returns them in the requested page order (best-effort match on normalized
    path, falling back to substring match), i.e. a one-step funnel table.
    """
    prop = os.environ.get("GA4_PROPERTY_ID", "")
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not prop or not creds:
        return {"configured": False, "reason": "GA4_PROPERTY_ID + GOOGLE_APPLICATION_CREDENTIALS not set"}
    try:
        live = await ga4_live_clicks(list(pages or []), days=days)
        if not live.get("configured") or live.get("ok") is False:
            return {"configured": True, "property": prop, "ok": False,
                    "reason": live.get("reason", "GA4 funnel pull failed"), "days": days}
        full_map = live.get("map", {}) or {}
        norm_map: dict[str, float] = {}
        for k, v in full_map.items():
            try:
                nk = _norm_traffic_key(str(k))
                norm_map[nk] = norm_map.get(nk, 0.0) + float(v or 0)
            except Exception:
                continue
        funnel = []
        for p in (pages or []):
            try:
                nk = _norm_traffic_key(str(p))
                sessions = norm_map.get(nk, 0.0)
                if sessions == 0.0:
                    # substring fallback (pagePath vs full URL)
                    for k, v in full_map.items():
                        if str(p) and (str(p) in str(k) or str(k) in str(p)):
                            sessions = float(v or 0)
                            break
                funnel.append({"page": str(p)[:200], "sessions": float(sessions)})
            except Exception:
                funnel.append({"page": str(p)[:200], "sessions": 0.0})
        return {"configured": True, "property": prop, "days": days, "funnel": funnel,
                "basis": "Pivot-like per-page GA4 sessions (pagePath, last N days) — VERIFIED Google data"}
    except Exception as e:
        return {"configured": True, "property": prop, "ok": False,
                "reason": f"{type(e).__name__}: {str(e)[:250]}"}


_GSC_PAGE_SIZE = 25000
_GSC_TOTAL_CAP = 25000


async def gsc_live_clicks(site_url: str = "", days: int = 28) -> dict:
    """Live GSC Search Analytics pull + AI-Overview visibility hint.

    Requires GSC_SITE_URL (or GSC OAuth) + google-api-python-client creds.
    Paginates with rowLimit up to 25000 + startRow looping until a short page
    or the 25k-row cap. Query regex flags definitional/AI-Overview-eligible
    queries (ESTIMATED hint only — confirm Share of Voice in GSC UI + prompt
    tests, not here).
    """
    site = site_url or os.environ.get("GSC_SITE_URL", "")
    if not site:
        return {"configured": False, "reason": "GSC_SITE_URL not set — use CSV import"}
    try:
        from google.oauth2 import service_account  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
        import datetime
        creds = service_account.Credentials.from_service_account_file(
            os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
            scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
        svc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
        end = datetime.date.today()
        start = end - datetime.timedelta(days=days)

        async def _fetch_page(start_row: int, limit: int) -> dict:
            def _sync():
                q = svc.searchanalytics().query(siteUrl=site, body={
                    "startDate": start.isoformat(), "endDate": end.isoformat(),
                    "dimensions": ["page", "query"], "rowLimit": limit, "startRow": start_row})
                return q.execute() if hasattr(q, "execute") else {}
            return await asyncio.to_thread(_sync)

        all_rows: list = []
        start_row = 0
        pages_fetched = 0
        while True:
            limit = min(_GSC_PAGE_SIZE, _GSC_TOTAL_CAP - len(all_rows))
            if limit <= 0:
                break
            res = await _with_backoff(lambda sr=start_row, lim=limit: _fetch_page(sr, lim), retries=4)
            batch = res.get("rows", []) if isinstance(res, dict) else []
            pages_fetched += 1
            if not batch:
                break
            all_rows.extend(batch)
            if len(batch) < limit or len(all_rows) >= _GSC_TOTAL_CAP:
                break
            start_row += len(batch)
        rows = all_rows[:_GSC_TOTAL_CAP]
        by_page: dict[str, float] = {}
        aio_hits = 0
        import re as _re
        for r in rows:
            try:
                page = (r.get("keys") or [""])[0]
                q = (r.get("keys") or ["", ""])[1] if len(r.get("keys", [])) > 1 else ""
                by_page[page] = by_page.get(page, 0) + float(r.get("clicks", 0))
                if _re.search(r"what is|how (does|to)|best|vs\.?|review", q, _re.I):
                    aio_hits += 1
            except Exception:
                continue
        return {"configured": True, "site": site, "days": days, "pages": len(by_page),
                "map": dict(list(by_page.items())[:200]),
                "ai_overview_eligible_queries_est": aio_hits,
                "ai_note": "ESTIMATED hint from definitional-query regex — verify AI Overview visibility in GSC UI",
                "basis": "VERIFIED GSC Search Analytics clicks per page",
                "rows_total": len(rows), "pages_fetched": pages_fetched,
                "pagination": f"rowLimit {_GSC_PAGE_SIZE} + startRow loop, cap {_GSC_TOTAL_CAP} rows"}
    except Exception as e:
        return {"configured": bool(site), "site": site, "ok": False,
                "reason": f"{type(e).__name__}: {str(e)[:250]}"}
