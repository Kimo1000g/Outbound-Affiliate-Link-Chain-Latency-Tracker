"""Alerting (Slack/Teams/webhook POST — actually firing) + CSV/JSON/JIRA exporters.

Webhook security: payloads are HMAC-SHA256 signed via PATHFINDER_WEBHOOK_SECRET
(header X-PF-Signature: sha256=<hex>). Each request carries a correlation ID
(uuid hex, header X-PF-Correlation-ID) propagated into logs/receipts. Without a
secret, payloads go unsigned with an explicit note — never silently unsigned.
"""
from __future__ import annotations
import csv
import hashlib
import hmac
import json
import os
import uuid

UNSIGNED_NOTE = "unsigned (set PATHFINDER_WEBHOOK_SECRET)"


def webhook_secret() -> str:
    """Shared webhook signing secret ('' when not configured)."""
    return os.environ.get("PATHFINDER_WEBHOOK_SECRET", "") or ""


def new_correlation_id() -> str:
    """Per-request correlation ID (uuid hex) for log/receipt tracing."""
    try:
        return uuid.uuid4().hex
    except Exception:
        import random
        return "%032x" % random.getrandbits(128)


def sign_payload(body: bytes, secret: str = "") -> str:
    """HMAC-SHA256 hex digest of raw request body ('' when no secret)."""
    sec = secret if secret else webhook_secret()
    if not sec:
        return ""
    try:
        return hmac.new(sec.encode("utf-8"), body, hashlib.sha256).hexdigest()
    except Exception:
        return ""


def build_signed_request(payload: dict, secret: str = "",
                         correlation_id: str = "") -> dict:
    """Serialize + sign a webhook payload -> {body, headers, correlation_id, signed, note}.

    Headers always include Content-Type + X-PF-Correlation-ID; X-PF-Signature
    (sha256=<hex>) only when a secret is configured.
    """
    cid = correlation_id or new_correlation_id()
    try:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except Exception:
        body = json.dumps(payload, default=str).encode("utf-8")
    sig = sign_payload(body, secret)
    headers = {"Content-Type": "application/json", "X-PF-Correlation-ID": cid}
    if sig:
        headers["X-PF-Signature"] = f"sha256={sig}"
        note = "signed sha256"
    else:
        note = UNSIGNED_NOTE
    return {"body": body, "headers": headers, "correlation_id": cid,
            "signed": bool(sig), "signature": f"sha256={sig}" if sig else "",
            "note": note}


def build_alerts(chain, high_value_clicks: int = 5000) -> list[dict]:
    alerts = []
    critical = (not chain.chain_ok) or (not chain.params_intact)
    if critical and float(chain.clicks_30d or 0) >= high_value_clicks:
        alerts.append({"channel": "slack+teams+email", "severity": "critical",
                       "text": f"CRITICAL: {chain.source_url} → {chain.final_url} broken ({chain.broken_reason or 'param-loss'}) | ${chain.revenue_at_risk:,.2f} at risk ({chain.revenue_basis})"})
    elif critical:
        alerts.append({"channel": "slack", "severity": "high",
                       "text": f"Broken affiliate chain: {chain.source_url} → {chain.final_url} | {chain.broken_reason or 'param-loss'}"})
    if chain.latency.verdict == "critical":
        alerts.append({"channel": "slack", "severity": "medium",
                       "text": f"Latency CRITICAL ({chain.latency.total_ms:.0f} ms redirect-chain, NOT page CWV) on {chain.source_url} — drop-off risk {chain.latency.dropoff_risk_pct}% (ESTIMATED curve)"})
    if not chain.compliance.compliant:
        alerts.append({"channel": "email+slack", "severity": "high",
                       "text": f"Compliance violation on {chain.final_url}: soft404={chain.compliance.soft404_detected} geo_mismatch={chain.compliance.geo_mismatch}"})
    try:
        bw = int((chain.botwall or {}).get("botwall_score", 0))
        if bw >= 60:
            alerts.append({"channel": "slack", "severity": "medium",
                           "text": f"Bot-wall {bw}/100 ({(chain.botwall or {}).get('vendor')}) on {chain.source_url} — headless escalation {'used' if chain.headless_used else 'needed'}"})
    except Exception:
        pass
    return alerts


async def post_alerts(alerts: list[dict], cfg: dict) -> dict:
    """POST to Slack/Teams webhooks from config (or env SLACK_WEBHOOK/TEAMS_WEBHOOK). Returns delivery receipt."""
    import httpx
    a = (cfg.get("alerting", {}) or {})
    slack = a.get("slack_webhook") or os.environ.get("SLACK_WEBHOOK", "")
    teams = a.get("teams_webhook") or os.environ.get("TEAMS_WEBHOOK", "")
    if not (a.get("post_alerts", True)):
        return {"posted": False, "reason": "alerting.post_alerts=false"}
    if not alerts:
        return {"posted": False, "reason": "no alerts"}
    cid = new_correlation_id()
    secret = webhook_secret()
    receipt: dict = {"slack": None, "teams": None, "correlation_id": cid,
                     "signed": bool(secret),
                     "signature_note": "signed sha256 (X-PF-Signature)" if secret else UNSIGNED_NOTE}
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            if slack:
                texts = [x["text"] for x in alerts if "slack" in x.get("channel", "")]
                if texts:
                    payload = {"text": "🛰 Affiliate chain alerts\n" + "\n".join(f"• [{x['severity']}] {x['text'][:400]}" for x in alerts if 'slack' in x.get('channel',''))[:20]}
                    signed = build_signed_request(payload, secret=secret, correlation_id=cid)
                    r = await c.post(slack, content=signed["body"], headers=signed["headers"])
                    receipt["slack"] = f"HTTP {r.status_code} cid={cid} {signed['note']}"
            if teams:
                texts = [x["text"] for x in alerts if "teams" in x.get("channel", "")]
                if texts:
                    payload = {"text": "Affiliate chain alerts\n" + "\n".join(texts[:20])}
                    signed = build_signed_request(payload, secret=secret, correlation_id=cid)
                    r = await c.post(teams, content=signed["body"], headers=signed["headers"])
                    receipt["teams"] = f"HTTP {r.status_code} cid={cid} {signed['note']}"
    except Exception as e:
        receipt["error"] = f"{type(e).__name__}: {e} cid={cid}"
    receipt["posted"] = bool(receipt.get("slack") or receipt.get("teams"))
    if not receipt["posted"] and "error" not in receipt:
        receipt["reason"] = "no webhooks configured — alerts built but not delivered (set alerting.slack_webhook)"
    return receipt


def export_csv(chains: list, path: str) -> str:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source_url", "anchor", "operator", "network", "geo", "device", "final_url", "final_status",
                    "hops", "total_ms", "verdict", "params_intact", "chain_ok", "broken_reason",
                    "compliant", "revenue_at_risk", "revenue_low", "revenue_high", "revenue_basis", "severity", "health_score"])
        for c in chains:
            w.writerow([c.source_url, c.anchor_text, c.expected_operator, c.expected_network, c.geo, c.device,
                        c.final_url, c.final_status, len(c.hops), round(c.latency.total_ms, 1), c.latency.verdict,
                        c.params_intact, c.chain_ok, c.broken_reason, c.compliance.compliant,
                        c.revenue_at_risk, getattr(c, "revenue_at_risk_low", 0), getattr(c, "revenue_at_risk_high", 0),
                        getattr(c, "revenue_basis", ""), getattr(c, "severity", ""), c.health_score])
    return path


def export_json(chains: list, path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([c.model_dump() for c in chains], f, indent=2)
    return path


def jira_tickets(chains: list) -> list[dict]:
    out = []
    secret = webhook_secret()
    for c in chains:
        if c.chain_ok and c.params_intact and c.compliance.compliant:
            continue
        cid = new_correlation_id()
        body = (f"Source: {c.source_url}\nFinal: {c.final_url} [{c.final_status}]\n"
                f"Hops: {len(c.hops)} total {c.latency.total_ms:.0f}ms (redirect-chain, not CWV)\n"
                f"Params: {[(e.param, e.status) for e in c.param_events]}\n"
                f"Revenue at risk: ${c.revenue_at_risk} (range ${getattr(c,'revenue_at_risk_low',0)}-${getattr(c,'revenue_at_risk_high',0)}) basis: {getattr(c,'revenue_basis','')}\nVendor ticket:\n{c.vendor_ticket}")
        sig = sign_payload(body.encode("utf-8"), secret)
        out.append({"project": "AFF", "issuetype": "Bug",
                    "summary": f"[Affiliate] Broken chain: {c.source_url} ({c.broken_reason or 'param-loss'})",
                    "description": body,
                    "priority": "Highest" if c.revenue_at_risk > 1000 else "High",
                    "correlation_id": cid,
                    "webhook_signature": f"sha256={sig}" if sig else "",
                    "signature_note": "signed sha256 (X-PF-Signature)" if sig else UNSIGNED_NOTE})
    return out
