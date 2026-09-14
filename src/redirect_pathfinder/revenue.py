"""Revenue protection math + network SLA matrix.

Honesty-first 2026 policy:
- clicks_30d / epc default to 0/UNKNOWN. NOTHING is worth $1.25 by default.
- revenue_at_risk is a RANGE (low/high) with the formula published; point value kept for dashboards.
- revenue_basis ∈ {UNKNOWN, ESTIMATED, VERIFIED} with provenance:
    UNKNOWN   — no clicks and no EPC supplied, no import. $ at risk = $0 and flagged.
    ESTIMATED — reach-scaled clicks × table/EPC hint. Range ±40%, labelled everywhere.
    VERIFIED  — GA4/traffic import (clicks_basis) + affiliate API/CSV (epc_basis).
"""
from __future__ import annotations
from collections import defaultdict


def score_revenue_and_health(chain) -> object:
    clicks = float(chain.clicks_30d or 0)
    epc = float(chain.epc or 0)
    has_clicks = clicks > 0
    has_epc = epc > 0
    fatal = (not chain.chain_ok) or (not chain.params_intact) or (chain.final_status and chain.final_status >= 400)
    degraded = (chain.latency.verdict == "critical") or (not chain.compliance.compliant) or (not chain.network_path_ok)

    if not has_clicks or not has_epc:
        chain.revenue_basis = "UNKNOWN — supply EPC + clicks_30d, import affiliate CSV/API, or connect GA4 (see /utils/traffic-import, /utils/affiliate-import)"
        chain.revenue_formula = "no $ computed without inputs (clicks_30d=%s, epc=%s)" % (clicks, epc)
        chain.revenue_at_risk = 0.0
        chain.revenue_at_risk_low = 0.0
        chain.revenue_at_risk_high = 0.0
    elif getattr(chain, "revenue_verified", False):
        base = clicks * epc
        factor = 1.0 if fatal else (0.35 if degraded else 0.0)
        chain.revenue_at_risk = round(base * factor, 2)
        chain.revenue_at_risk_low = round(base * factor * 0.85, 2)
        chain.revenue_at_risk_high = round(base * factor * 1.15, 2)
        chain.revenue_basis = f"VERIFIED — {getattr(chain, 'clicks_basis', '')} | {getattr(chain, 'epc_basis', '')}".strip(" |")
        chain.revenue_formula = f"VERIFIED: clicks({clicks:.0f}) × epc({epc}) × loss_factor({factor}) ±15% confidence"
    else:
        base = clicks * epc
        factor = 1.0 if fatal else (0.35 if degraded else 0.0)
        chain.revenue_at_risk = round(base * factor, 2)
        chain.revenue_at_risk_low = round(base * factor * 0.6, 2)
        chain.revenue_at_risk_high = round(base * factor * 1.4, 2)
        chain.revenue_basis = ("ESTIMATED — reach-scaled clicks × table/EPC hint ±40% "
                               "(connect GA4 / affiliate API for VERIFIED $)")
        chain.revenue_formula = f"ESTIMATED: clicks({clicks:.0f}) × epc({epc}) × loss_factor({factor}) range ±40%"
    # health 0-100 + severity scoring (Sitebulb-style prioritisation)
    score = 100.0
    if not chain.chain_ok:
        score -= 45
    if not chain.params_intact:
        score -= 30
    if chain.final_status and chain.final_status >= 400:
        score -= 20
    if chain.latency.verdict == "critical":
        score -= 15
    elif chain.latency.verdict == "warn":
        score -= 7
    if not chain.compliance.compliant:
        score -= 12
    if not chain.network_path_ok:
        score -= 10
    if chain.adblock_vulnerable:
        score -= 5
    bw = 0
    try:
        bw = int((chain.botwall or {}).get("botwall_score", 0))
    except Exception:
        bw = 0
    if bw >= 60:
        score -= 8
    chain.health_score = round(max(0.0, min(100.0, score)), 1)
    chain.severity = ("critical" if (not chain.chain_ok or not chain.params_intact) else
                      "high" if (not chain.compliance.compliant or chain.latency.verdict == "critical") else
                      "medium" if (not chain.network_path_ok or chain.adblock_vulnerable or bw >= 30) else
                      "low" if chain.health_score < 95 else "info")
    return chain


def network_sla_matrix(chains: list) -> list[dict]:
    by_net: dict[str, list] = defaultdict(list)
    for c in chains:
        by_net[c.expected_network or "unknown"].append(c)
    rows = []
    for net, items in by_net.items():
        lat = [c.latency.total_ms for c in items if c.latency.total_ms]
        ok = sum(1 for c in items if c.chain_ok and c.params_intact)
        rows.append({"network": net, "checks": len(items),
                     "success_rate_pct": round(ok / max(1, len(items)) * 100, 1),
                     "avg_latency_ms": round(sum(lat) / max(1, len(lat)), 1) if lat else 0,
                     "revenue_at_risk": round(sum(c.revenue_at_risk for c in items), 2),
                     "revenue_range": [round(sum(c.revenue_at_risk_low for c in items), 2),
                                       round(sum(c.revenue_at_risk_high for c in items), 2)],
                     "revenue_basis": "MIXED — see per-chain basis" if any(getattr(c, "revenue_basis", "").startswith("UNKNOWN") for c in items) else "ESTIMATED/VERIFIED mix"})
    return sorted(rows, key=lambda r: r["avg_latency_ms"])
