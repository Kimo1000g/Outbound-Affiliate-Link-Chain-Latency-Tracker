"""Latency profiler — redirect-chain budget ONLY. Page CWV comes from CrUX/PSI, never here.

Verdicts compare redirect total_ms vs warn/critical thresholds. Drop-off % is an
ESTIMATED industry bounce curve with its formula published on every score — not
measured conversions. Redirect overhead vs LCP budget: each 1s of redirect burn
is ~1s stolen from the 2.5s LCP budget; surfaced as lcp_budget_left_est.
"""
from __future__ import annotations
from .models import ChainResult, LatencyScore


def score_latency(chain: ChainResult, warn_ms: int = 900, critical_ms: int = 1800) -> ChainResult:
    per = [h.total_ms for h in chain.hops]
    total = sum(per)
    dns = sum(h.dns_ms for h in chain.hops)
    ttfb = sum(h.ttfb_ms for h in chain.hops)
    tcp_est = sum(getattr(h, "tcp_tls_est_ms", 0.0) or 0.0 for h in chain.hops) or \
        sum(getattr(h, "tcp_tls_ms", 0.0) or 0.0 for h in chain.hops)
    if total >= critical_ms:
        verdict = "critical"
    elif total >= warn_ms:
        verdict = "warn"
    else:
        verdict = "fast"
    # drop-off risk: logistic-ish curve anchored at industry data (every +1s ≈ +20-30% bounce on affiliates)
    if total <= 500:
        risk = round(total / 500 * 5, 1)
    elif total <= 1800:
        risk = round(5 + (total - 500) / 1300 * 30, 1)
    else:
        risk = round(min(95.0, 35 + (total - 1800) / 2000 * 40), 1)
    chain.latency = LatencyScore(total_ms=round(total, 1), per_hop_ms=[round(x, 1) for x in per],
                                 dns_total_ms=round(dns, 1), ttfb_total_ms=round(ttfb, 1),
                                 tcp_tls_est_total_ms=round(tcp_est, 1),
                                 verdict=verdict, dropoff_risk_pct=risk)
    return chain
