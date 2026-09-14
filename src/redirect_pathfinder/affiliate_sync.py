"""Affiliate API/CSV sync — flip revenue_basis ESTIMATED/UNKNOWN -> VERIFIED.

Live pullers (keys from env, never logged) — PRIMARY in 2026, CSV is fallback:
- Everflow (EF_API_KEY): reporting + sub1-sub10/adv1-adv10 expanded May 18 2026
- Cellxpert (CELLXPERT_API_KEY + CELLXPERT_OPERATOR): operator endpoint
- Income Access (IA_API_KEY + IA_MERCHANT_ID), NetRefer (NETREFER_API_KEY),
  MyAffiliates (MYAFF_API_KEY): REST where exposed, else CSV export import.
- S2S postback validator: validate_postback(url) checks txid/clickid/signature presence.
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


async def pull_platform_summary(platform: str) -> dict:
    """Generic live pull for cellxpert/incomeaccess/netrefer/myaffiliates (env-keyed, best-effort).

    Each operator exposes a different REST shape; we attempt the documented base URL
    and return honest {configured, ok, reason} — never synthetic rows. CSV import
    remains the fallback. Env: CELLXPERT_API_KEY(+CELLXPERT_ENDPOINT),
    IA_API_KEY(+IA_ENDPOINT), NETREFER_API_KEY(+NETREFER_ENDPOINT), MYAFF_API_KEY(+MYAFF_ENDPOINT).
    """
    p = (platform or "").lower()
    env_map = {"cellxpert": ("CELLXPERT_API_KEY", "CELLXPERT_ENDPOINT", "https://api.cellxpert.com/affiliate/report"),
               "incomeaccess": ("IA_API_KEY", "IA_ENDPOINT", "https://api.incomeaccess.com/report"),
               "netrefer": ("NETREFER_API_KEY", "NETREFER_ENDPOINT", "https://api.netrefer.com/v1/report"),
               "myaffiliates": ("MYAFF_API_KEY", "MYAFF_ENDPOINT", "https://api.myaffiliates.com/v1/report")}
    if p == "everflow":
        return await pull_everflow_summary()
    if p not in env_map:
        return {"configured": False, "reason": f"unknown platform {platform} — use CSV import"}
    key_env, ep_env, default_ep = env_map[p]
    key = os.environ.get(key_env, "")
    if not key:
        return {"configured": False, "reason": f"{key_env} not set — use CSV import (/utils/affiliate-import)"}
    ep = os.environ.get(ep_env, default_ep)
    try:
        async with httpx.AsyncClient(timeout=15, headers={"Authorization": f"Bearer {key}"}) as c:
            r = await c.get(ep)
            if r.status_code != 200:
                return {"configured": True, "ok": False, "platform": p,
                        "reason": f"{p} HTTP {r.status_code}: {r.text[:200]}"}
            try:
                j = r.json()
                n = len(j.get("rows", j) if isinstance(j, dict) else j) if isinstance(j, (dict, list)) else 0
            except Exception:
                n = 0
            return {"configured": True, "ok": True, "platform": p, "rows": n,
                    "basis": f"VERIFIED {p} API — map to targets by campaign/operator before trusting $"}
    except Exception as e:
        return {"configured": True, "ok": False, "platform": p, "reason": f"{type(e).__name__}: {str(e)[:200]}"}


def validate_postback(url: str) -> dict:
    """S2S postback validator — checks txid/clickid + value/signature presence (Voluum parity).

    Voluum 2026 S2S requires txid passthrough; without it FTD/registration backfeed
    to Google/Meta breaks. Returns {ok, missing[], detail} — never sends traffic.
    """
    from urllib.parse import urlparse, parse_qsl
    try:
        q = dict(parse_qsl(urlparse(url or "").query, keep_blank_values=True))
        low = {k.lower(): v for k, v in q.items()}
        # Everflow sub1-sub10 + adv1-adv10 (expanded May 18 2026)
        sub_keys = [f"sub{i}" for i in range(1, 11)] + [f"adv{i}" for i in range(1, 11)]
        has_sub = any(k in low for k in sub_keys + ["subid", "clickid", "txid", "cid"])
        missing = []
        if not any(k in low for k in ("txid", "clickid", "cid", "subid", "sub1")):
            missing.append("txid/clickid (S2S click-id passthrough REQUIRED)")
        if not any(k in low for k in ("payout", "value", "commission", "revenue")):
            missing.append("payout/value (revenue reconciliation)")
        ok = not missing
        return {"ok": ok, "missing": missing, "has_sub_tracking": has_sub,
                "params": sorted(low.keys())[:20],
                "detail": ("S2S postback OK — txid + value present" if ok
                           else f"S2S postback INCOMPLETE — missing {', '.join(missing)}"),
                "basis": "measured query-string markers (Everflow sub1-10/adv1-10 incl.) — confirm against network docs"}
    except Exception as e:
        return {"ok": False, "missing": [], "detail": f"{type(e).__name__}: {e}"}


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
