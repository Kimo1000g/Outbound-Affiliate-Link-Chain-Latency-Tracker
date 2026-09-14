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
import csv
import io
import os


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
        resp = client.run_report(req)
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


async def gsc_live_clicks(site_url: str = "", days: int = 28) -> dict:
    """Live GSC Search Analytics pull + AI-Overview visibility hint.

    Requires GSC_SITE_URL (or GSC OAuth) + google-api-python-client creds.
    Query regex flags definitional/AI-Overview-eligible queries (ESTIMATED hint only —
    confirm Share of Voice in GSC UI + prompt tests, not here).
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
        req = svc.searchanalytics().query(siteUrl=site, body={
            "startDate": start.isoformat(), "endDate": end.isoformat(),
            "dimensions": ["page", "query"], "rowLimit": 5000})
        res = req.execute() if hasattr(req, "execute") else {}
        rows = res.get("rows", []) if isinstance(res, dict) else []
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
                "basis": "VERIFIED GSC Search Analytics clicks per page"}
    except Exception as e:
        return {"configured": bool(site), "site": site, "ok": False,
                "reason": f"{type(e).__name__}: {str(e)[:250]}"}
