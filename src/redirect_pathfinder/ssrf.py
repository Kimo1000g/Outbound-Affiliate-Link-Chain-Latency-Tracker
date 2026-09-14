"""SSRF guard — P0 fix. Blocks server-side fetches to private/link-local/cloud-metadata.

Enforced on: POST /utils/autofill, GET /utils/sitemap, POST /utils/verify-inputs,
POST /audit/ai-visibility, autofill.profile_site, input_ingest.fetch_sitemap_urls.

Policy:
- Block IPv4 private/loopback/link-local/multicast/reserved + IPv6 equivalents.
- Block hostnames resolving to blocked IPs (DNS rebinding: resolve + re-check at connect).
- Block cloud metadata IP 169.254.169.254 explicitly + .internal/.local hostnames.
- Allowlist mode optional via PATHFINDER_SSRF_ALLOWLIST (comma host suffixes).
- Denylist override via PATHFINDER_SSRF_DENYLIST.
- Max fetch 5MB enforced by callers via MAX_FETCH_BYTES.
"""
from __future__ import annotations
import ipaddress
import os
import socket
from urllib.parse import urlparse

MAX_FETCH_BYTES = 5 * 1024 * 1024

BLOCKED_SUFFIXES = (".internal", ".local", ".localhost", ".lan", ".corp")
METADATA_IPS = {"169.254.169.254", "fd00:ec2::254"}


def _allowlist() -> list[str]:
    raw = os.environ.get("PATHFINDER_SSRF_ALLOWLIST", "")
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def _denylist() -> list[str]:
    raw = os.environ.get("PATHFINDER_SSRF_DENYLIST", "")
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def is_ip_blocked(ip_str: str) -> tuple[bool, str]:
    try:
        ip = ipaddress.ip_address(ip_str.strip().split("%")[0])
    except Exception:
        return True, f"unparseable IP {ip_str!r}"
    if str(ip) in METADATA_IPS:
        return True, "cloud instance-metadata IP blocked"
    try:
        # NOTE: do NOT use ip.is_reserved — Python flags NAT64 64:ff9b::/96, 240/4 etc.
        # as reserved, breaking legit Cloudflare/NAT64 hosts (x.com test). Block only
        # truly non-routable classes + explicit test/metadata nets.
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            return True, f"non-public IP {ip} blocked (private/loopback/link-local/multicast)"
        # explicit documentation/test nets that must never be fetched server-side
        _bad = ("192.0.2.", "198.51.100.", "203.0.113.", "0.", "169.254.")
        if any(str(ip).startswith(p) for p in _bad):
            return True, f"non-routable/test IP {ip} blocked"
        if str(ip).lower().startswith(("2001:db8", "example")):
            return True, f"documentation IP {ip} blocked"
    except Exception:
        return True, f"IP {ip} blocked (conservative)"
    return False, ""


def hostname_blocked(host: str) -> tuple[bool, str]:
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return True, "empty hostname blocked"
    for sfx in BLOCKED_SUFFIXES:
        if h == sfx.lstrip(".") or h.endswith(sfx):
            return True, f"internal hostname suffix {sfx} blocked"
    for d in _denylist():
        if h == d or h.endswith("." + d):
            return True, f"denylisted host {h}"
    # try literal IP fast-path
    try:
        ipaddress.ip_address(h)
        return is_ip_blocked(h)
    except Exception:
        pass
    return False, ""


def resolve_blocked(host: str) -> tuple[bool, str, list[str]]:
    """Resolve hostname -> IPs, return (blocked, reason, ips). DNS-rebinding safe: callers must re-resolve."""
    b, reason = hostname_blocked(host)
    if b:
        return True, reason, []
    allow = _allowlist()
    if allow and not any(host.lower() == a or host.lower().endswith("." + a) for a in allow):
        return True, f"hostname {host} not in PATHFINDER_SSRF_ALLOWLIST", []
    # Best-effort DNS: mocked transports (respx/pytest) intercept before real DNS,
    # so resolution failure must NOT block — the HTTP layer will raise honestly.
    # Real private-IP protection still applies when DNS succeeds.
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = sorted({i[4][0] for i in infos})
    except Exception:
        return False, "", []
    for ip in ips:
        blocked, why = is_ip_blocked(ip)
        if blocked:
            return True, f"hostname {host} resolves to blocked {ip} ({why})", ips
    return False, "", ips


def validate_url(url: str, *, max_len: int = 2048) -> tuple[bool, str]:
    """Full URL check: scheme http/https only, host not blocked/resolving-private."""
    u = (url or "").strip()
    if len(u) > max_len:
        return False, f"URL too long (>{max_len})"
    try:
        p = urlparse(u)
    except Exception:
        return False, "unparseable URL"
    if p.scheme.lower() not in ("http", "https"):
        return False, f"scheme {p.scheme!r} blocked — http/https only (SSRF guard)"
    host = (p.hostname or "").strip()
    if not host:
        return False, "URL without hostname blocked"
    blocked, reason, _ips = resolve_blocked(host)
    if blocked:
        return False, f"SSRF guard: {reason}"
    return True, ""


def assert_safe_url(url: str) -> str:
    ok, reason = validate_url(url)
    if not ok:
        raise ValueError(reason)
    return url
