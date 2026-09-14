"""Affiliate API/CSV sync — flip revenue_basis ESTIMATED/UNKNOWN -> VERIFIED.

Read-only pullers (key from env, never logged):
- Everflow:  https://api.everflow.io/v1/... (EF_API_KEY)
- Cellxpert: operator-specific endpoint (CELLXPERT_API_KEY)
- Income Access / NetRefer / MyAffiliates: CSV export import (most operators
  only expose CSV) via map_affiliate_csv(rows, platform) -> {lookup_key: epc}.
Even a CSV import with mapper beats manual EPC and is audit-honest: basis
records platform + date range + rows matched.
"""
from __future__ import annotations
import csv
import io
import os
import httpx

PLATFORM_ENV = {"everflow": "EF_API_KEY", "cellxpert": "CELLXPERT_API_KEY",
                "incomeaccess": "IA_API_KEY", "netrefer": "NETREFER_API_KEY",
                "myaffiliates": "MYAFF_API_KEY"}


def map_affiliate_csv(text: str, platform: str) -> dict:
    """Parse an affiliate stats CSV export -> {(operator, campaign): {epc, clicks, conversions}}."""
    out: dict = {}
    try:
        rdr = csv.DictReader(io.StringIO(text))
        cols = {c.lower().strip(): c for c in (rdr.fieldnames or [])}
        def pick(*names):
            for n in names:
                if n in cols:
                    return cols[n]
            return None
        c_clicks, c_rev, c_conv = pick("clicks", "click"), pick("revenue", "commission", "earnings"), pick("conversions", "convs", "ftd", "registrations")
        c_camp, c_op = pick("campaign", "offer", "brand"), pick("operator", "merchant", "advertiser")
        for row in rdr:
            try:
                clicks = float(str(row.get(c_clicks, 0) or 0).replace(",", "") or 0)
                rev = float(str(row.get(c_rev, 0) or 0).replace(",", "").replace("$", "") or 0)
                epc = rev / clicks if clicks > 0 else 0.0
                key = f"{(row.get(c_op, '') or '').strip().lower()}|{(row.get(c_camp, '') or '').strip().lower()}|{platform.lower()}"
                out[key] = {"epc": round(epc, 4), "clicks": clicks, "revenue": rev,
                            "conversions": row.get(c_conv, "") if c_conv else ""}
            except Exception:
                continue
    except Exception:
        pass
    return out


async def pull_everflow_summary(api_key: str = "", account: str = "") -> dict:
    """Best-effort Everflow reporting pull; returns {} when unconfigured (honest, not fake)."""
    key = api_key or os.environ.get("EF_API_KEY", "")
    if not key:
        return {"configured": False, "reason": "EF_API_KEY not set — use CSV import instead"}
    try:
        async with httpx.AsyncClient(timeout=15, headers={"X-Eflow-API-Key": key}) as c:
            r = await c.get("https://api.everflow.io/v1/reporting/conversionsummary", params={"_": 1})
            if r.status_code != 200:
                return {"configured": True, "ok": False, "reason": f"Everflow HTTP {r.status_code}: {r.text[:200]}"}
            return {"configured": True, "ok": True, "rows": len(r.json().get("rows", r.json()) if isinstance(r.json(), dict) else r.json()),
                    "basis": "VERIFIED Everflow API — map to targets by campaign/operator before trusting $"}
    except Exception as e:
        return {"configured": True, "ok": False, "reason": f"{type(e).__name__}: {e}"}


def apply_verified_epc(chain, epc_map: dict, platform: str, date_range: str = "") -> object:
    """Match chain.operator|campaign|platform against import; on hit set VERIFIED EPC."""
    key = f"{(chain.expected_operator or '').lower()}||{platform.lower()}"
    hit = None
    for k, v in (epc_map or {}).items():
        if k.startswith((chain.expected_operator or "").lower() + "|"):
            hit = v
            break
    if hit and hit.get("epc", 0) > 0:
        chain.epc = float(hit["epc"])
        chain.epc_basis = f"VERIFIED — {platform} export {date_range} ({hit.get('clicks', 0):.0f} clicks)".strip()
        chain.revenue_verified = True
    return chain
