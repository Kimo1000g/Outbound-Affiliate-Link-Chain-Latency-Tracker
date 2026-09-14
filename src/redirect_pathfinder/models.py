"""Pydantic data models — strict contracts for every hop, chain and finding."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, model_validator


TCP_TLS_FORMULA = "tcp_tls_est = max(0, total - dns - download - ttfb*0.15) — ESTIMATED, not a socket handshake measurement; calibrate vs curl -w %{time_appconnect}"


def tcp_tls_canonical(total_ms: float, dns_ms: float, download_ms: float, ttfb_ms: float) -> float:
    try:
        return max(0.0, float(total_ms) - float(dns_ms) - float(download_ms) - (float(ttfb_ms) * 0.15))
    except Exception:
        return 0.0


def band_0_100(score: float) -> dict:
    s = max(0.0, min(100.0, float(score or 0)))
    band = "strong" if s >= 75 else ("moderate" if s >= 45 else "weak")
    return {"score_est": round(s, 1), "band": band,
            "basis": "ESTIMATED heuristic band (strong ≥75 / moderate 45-74 / weak <45) — not a Google score"}


class Hop(BaseModel):
    index: int
    url: str
    status: Optional[int] = None
    location_header: Optional[str] = None
    ip: Optional[str] = None
    asn: Optional[str] = None
    hosting_org: Optional[str] = None
    server: Optional[str] = None
    dns_ms: float = 0.0
    doh_ms: float = 0.0
    tcp_tls_est_ms: float = 0.0  # CANONICAL — ESTIMATED via subtraction; see TCP_TLS_FORMULA
    tcp_tls_ms: float = 0.0  # DEPRECATED alias, always synced to tcp_tls_est_ms (kept for back-compat dashboards)
    ttfb_ms: float = 0.0
    download_ms: float = 0.0
    total_ms: float = 0.0
    redirect_type: str = "unknown"   # 301/302/303/307/308/js-meta/js-window/final-200/error
    params: dict = Field(default_factory=dict)
    error: Optional[str] = None

    @model_validator(mode="after")
    def _sync_tcp_tls(self):
        # Single source of truth: tcp_tls_est_ms. Alias synced both directions.
        try:
            if self.tcp_tls_est_ms and not self.tcp_tls_ms:
                self.tcp_tls_ms = self.tcp_tls_est_ms
            elif self.tcp_tls_ms and not self.tcp_tls_est_ms:
                self.tcp_tls_est_ms = self.tcp_tls_ms
            elif self.tcp_tls_est_ms != self.tcp_tls_ms:
                self.tcp_tls_ms = self.tcp_tls_est_ms
        except Exception:
            pass
        return self


class ParamEvent(BaseModel):
    param: str
    first_seen_hop: int
    last_seen_hop: Optional[int] = None
    stripped_at_hop: Optional[int] = None
    mutated_to: Optional[str] = None
    status: str  # survived | stripped | mutated | injected-mid-chain
    note: Optional[str] = None  # informational only, e.g. cookies out-of-band / fragment handling


class ComplianceResult(BaseModel):
    geo: str = "unknown"
    required_strings: dict = Field(default_factory=dict)  # string -> found bool
    license_badges: dict = Field(default_factory=dict)
    soft404_detected: bool = False
    soft404_phrases_hit: list = Field(default_factory=list)
    geo_mismatch: bool = False
    geo_mismatch_detail: str = ""
    age_gate_detected: bool = False
    age_gate_detail: str = ""
    site_reputation_abuse_suspect: bool = False
    site_reputation_detail: str = ""
    compliant: bool = True


class LatencyScore(BaseModel):
    total_ms: float = 0.0
    per_hop_ms: list = Field(default_factory=list)
    dns_total_ms: float = 0.0
    ttfb_total_ms: float = 0.0
    tcp_tls_est_total_ms: float = 0.0
    tcp_tls_formula: str = TCP_TLS_FORMULA
    verdict: str = "fast"  # fast | warn | critical (redirect-chain budget only, NOT page CWV)
    dropoff_risk_pct: float = 0.0
    dropoff_band: str = "low"  # low | elevated | high — band replaces fake-precision single number
    dropoff_formula: str = "logistic anchored <=500ms ~0-5%, 500-1800ms 5-35%, >1800ms 35-95% — ESTIMATED industry bounce curve"
    redirect_budget_note: str = "Redirect-chain total_ms is server+network hops only. LCP/INP/CLS come only from CrUX/PSI."
    inp_breakdown: dict = Field(default_factory=dict)  # PSI/web-vitals attribution when available
    ttfb_budget_note: str = "TTFB ≤800ms good (Google). Redirect TTFB burn steals LCP budget 1:1."


class ChainResult(BaseModel):
    source_url: str
    anchor_text: str = ""
    expected_operator: str = ""
    expected_network: str = ""
    geo: str = "US-NJ"
    device: str = "desktop_chrome"
    clicks_30d: float = 0.0
    clicks_basis: str = "UNKNOWN"
    epc: float = 0.0
    epc_basis: str = "UNKNOWN"
    bonus_text_on_site: str = ""
    hops: list[Hop] = Field(default_factory=list)
    final_url: str = ""
    final_status: Optional[int] = None
    chain_ok: bool = True
    broken_reason: str = ""
    param_events: list[ParamEvent] = Field(default_factory=list)
    params_intact: Optional[bool] = True  # True intact | False stripped/mutated | None unknown (clean link, zero tracked params at hop 0)
    itp_stripped_simulated: list = Field(default_factory=list)
    compliance: ComplianceResult = Field(default_factory=ComplianceResult)
    latency: LatencyScore = Field(default_factory=LatencyScore)
    crux: dict = Field(default_factory=dict)
    psi: dict = Field(default_factory=dict)
    ai_visibility: dict = Field(default_factory=dict)
    content_quality: dict = Field(default_factory=dict)
    eeat: dict = Field(default_factory=dict)
    botwall: dict = Field(default_factory=dict)
    js_redirect_detected: bool = False
    needs_headless: bool = False
    headless_used: bool = False
    playwright_available: bool = False
    adblock_vulnerable: bool = False
    adblock_blocked_hosts: list = Field(default_factory=list)
    adblock_basis: str = "EasyList live fetch (weekly cache) + ITP simulation"
    consent_mode: dict = Field(default_factory=dict)
    gpc_detected: bool = False
    deeplink: dict = Field(default_factory=dict)
    brand_alignment: dict = Field(default_factory=dict)
    offer_discrepancy: dict = Field(default_factory=dict)
    network_path_ok: bool = True
    revenue_at_risk: float = 0.0
    revenue_at_risk_low: float = 0.0
    revenue_at_risk_high: float = 0.0
    revenue_formula: str = ""
    health_score: float = 100.0
    severity: str = "info"
    revenue_verified: bool = False
    revenue_basis: str = "UNKNOWN"
    page_forensics: dict = Field(default_factory=dict)
    device_parity: dict = Field(default_factory=dict)
    screenshots: dict = Field(default_factory=dict)
    proxy_exit_used: str = ""
    proxy_exit_ip: str = ""  # exit-IP proof when jurisdiction-pinned (else "")
    evidence: dict = Field(default_factory=dict)
    edge_patch: dict = Field(default_factory=dict)
    vendor_ticket: str = ""
    canonical: dict = Field(default_factory=dict)  # seo_audit.validate_canonical_chain
    hreflang: dict = Field(default_factory=dict)
    sponsored: dict = Field(default_factory=dict)
    http3: dict = Field(default_factory=dict)
    sov: dict = Field(default_factory=dict)  # prompt-level Share of Voice (measured, never invented)
    consent_v2: dict = Field(default_factory=dict)  # Consent Mode v2 + TCF 2.2
    workspace: str = "default"


class AuditFinding(BaseModel):
    severity: str  # critical | high | medium | low | info
    category: str
    message: str
    source_url: str = ""
