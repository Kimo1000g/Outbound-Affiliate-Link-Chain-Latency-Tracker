"""Pydantic data models — strict contracts for every hop, chain and finding."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


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
    tcp_tls_est_ms: float = 0.0  # ESTIMATED via subtraction — see tcp_tls_formula; not a socket measurement
    tcp_tls_ms: float = 0.0  # back-compat alias of tcp_tls_est_ms
    ttfb_ms: float = 0.0
    download_ms: float = 0.0
    total_ms: float = 0.0
    redirect_type: str = "unknown"   # 301/302/303/307/308/js-meta/js-window/final-200/error
    params: dict = Field(default_factory=dict)
    error: Optional[str] = None


class ParamEvent(BaseModel):
    param: str
    first_seen_hop: int
    last_seen_hop: Optional[int] = None
    stripped_at_hop: Optional[int] = None
    mutated_to: Optional[str] = None
    status: str  # survived | stripped | mutated


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
    tcp_tls_formula: str = "tcp_tls_est = max(0, total - dns - download - ttfb*0.15) — ESTIMATED, not a socket handshake measurement"
    verdict: str = "fast"  # fast | warn | critical (redirect-chain budget only, NOT page CWV)
    dropoff_risk_pct: float = 0.0
    dropoff_formula: str = "logistic anchored <=500ms ~0-5%, 500-1800ms 5-35%, >1800ms 35-95% — ESTIMATED industry bounce curve"
    redirect_budget_note: str = "Redirect-chain total_ms is server+network hops only. LCP/INP/CLS come only from CrUX/PSI."


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
    params_intact: bool = True
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
    evidence: dict = Field(default_factory=dict)
    edge_patch: dict = Field(default_factory=dict)
    vendor_ticket: str = ""


class AuditFinding(BaseModel):
    severity: str  # critical | high | medium | low | info
    category: str
    message: str
    source_url: str = ""
