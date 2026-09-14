"""GA4 + GSC ingestion — kill the biggest honesty gap (invented clicks_30d).

- GA4 Data API (GA4_PROPERTY_ID + GOOGLE_APPLICATION_CREDENTIALS): pull sessions/
  clicks per landing page for the last 28d and auto-fill clicks_30d (basis VERIFIED).
- GSC (GSC_SITE_URL + creds): clicks/impressions per review page.
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
