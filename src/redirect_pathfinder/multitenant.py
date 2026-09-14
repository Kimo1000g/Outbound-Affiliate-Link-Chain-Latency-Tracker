"""Multi-tenant workspaces + API-key scopes — P0 ADD.

security.api_keys: [] = open dev mode (fine local, death in prod).
This module adds:
- PATHFINDER_API_KEYS entries as `key[:scope1+scope2][:workspace]` e.g.
  `sk-live-abc:audit+mcp:acme-casino`.
- workspaces: {id, name, allowed_geos, concurrency_cap} via env JSON or YAML.
- scope check: audit, bulk, mcp, admin(schedule/config), export.
- Postgres RLS note: when PATHFINDER_PG_DSN set, jobs/findings carry workspace_id.
- Per-workspace cost caps: PATHFINDER_WORKSPACE_CAPS JSON
  {ws: {max_audits_day, max_sov_calls}} enforced via check_cap() with in-memory
  daily counters -> {allowed, reason}.
- Secrets: get_secret(name) reads env first, then /run/secrets/<name> (Docker
  secrets), else "".

api.py wiring note (follow-up, api.py untouched — another change in flight):
  from .multitenant import check_cap
  cap = check_cap(workspace, "audits")   # or "sov" for SoV calls
  if not cap["allowed"]: raise HTTPException(429, cap["reason"])
  # and replace os.environ.get("X_KEY") reads with get_secret("X_KEY").
"""
from __future__ import annotations
import datetime
import json
import os


def parse_key_entry(entry: str) -> dict:
    parts = (entry or "").strip().split(":")
    key = parts[0] if parts else ""
    scopes = set((parts[1] if len(parts) > 1 else "audit+bulk+mcp+export").split("+"))
    ws = parts[2] if len(parts) > 2 else "default"
    return {"key": key, "scopes": scopes, "workspace": ws}


def configured_keys() -> list[dict]:
    out: list[dict] = []
    try:
        from .orchestrator import load_config
        cfg = load_config(os.environ.get("PATHFINDER_CFG", "config/enterprise.yaml"))
        for k in ((cfg.get("security", {}) or {}).get("api_keys", []) or []):
            out.append(parse_key_entry(str(k)))
    except Exception:
        pass
    for k in (os.environ.get("PATHFINDER_API_KEYS", "") or "").split(","):
        if k.strip():
            out.append(parse_key_entry(k.strip()))
    return out


def check_scope(provided_key: str, need: str) -> tuple[bool, str]:
    keys = configured_keys()
    if not keys:
        return True, "default"  # open dev mode
    for e in keys:
        if e["key"] and provided_key == e["key"]:
            if need in e["scopes"] or "admin" in e["scopes"]:
                return True, e["workspace"]
            return False, e["workspace"]
    return False, ""


def workspace_caps() -> dict:
    """Per-workspace cost caps from PATHFINDER_WORKSPACE_CAPS JSON.

    Shape: {ws: {max_audits_day: N, max_sov_calls: M}, "default": {...}}.
    Returns {} when unset/unparseable (unlimited) — never raises.
    """
    raw = os.environ.get("PATHFINDER_WORKSPACE_CAPS", "") or ""
    if not raw.strip():
        return {}
    try:
        caps = json.loads(raw)
        return caps if isinstance(caps, dict) else {}
    except Exception:
        return {}


_CAP_COUNTERS: dict[tuple[str, str, str], int] = {}


def _today_str() -> str:
    try:
        return datetime.date.today().isoformat()
    except Exception:
        return "unknown-day"


_CAP_KEY_BY_KIND = {"audits": "max_audits_day", "sov": "max_sov_calls",
                    "audit": "max_audits_day"}


def check_cap(workspace: str, kind: str, increment: bool = True) -> dict:
    """Check (+ by default, consume) a per-workspace daily cost cap.

    kind: "audits" (max_audits_day) or "sov" (max_sov_calls).
    Returns {allowed, reason, workspace, kind, used, limit, day}.
    Unlimited (allowed=True) when no cap is configured for the workspace/kind.
    In-memory daily counters reset on date rollover; process-local (single
    replica). For multi-replica enforcement back this with Redis/Postgres.
    """
    ws = (workspace or "default").strip() or "default"
    kd = (kind or "audits").strip().lower() or "audits"
    day = _today_str()
    caps = workspace_caps()
    ws_caps = caps.get(ws, {}) or {}
    if not isinstance(ws_caps, dict):
        ws_caps = {}
    def_caps = caps.get("default", {}) or {}
    if not isinstance(def_caps, dict):
        def_caps = {}
    cap_key = _CAP_KEY_BY_KIND.get(kd, f"max_{kd}_day")
    limit = ws_caps.get(cap_key, def_caps.get(cap_key))
    try:
        limit = int(limit) if limit is not None else None
    except Exception:
        limit = None
    if limit is None:
        return {"allowed": True, "reason": f"no cap configured for {ws}/{kd} — unlimited",
                "workspace": ws, "kind": kd, "used": 0, "limit": None, "day": day}
    if limit < 0:
        limit = 0
    ckey = (ws, kd, day)
    used = int(_CAP_COUNTERS.get(ckey, 0))
    if used >= limit:
        return {"allowed": False,
                "reason": f"workspace '{ws}' daily {kd} cap reached ({used}/{limit} for {day})",
                "workspace": ws, "kind": kd, "used": used, "limit": limit, "day": day}
    if increment:
        _CAP_COUNTERS[ckey] = used + 1
        used += 1
    return {"allowed": True, "reason": f"within cap ({used}/{limit} {kd} for {day})",
            "workspace": ws, "kind": kd, "used": used, "limit": limit, "day": day}


def reset_caps() -> None:
    """Clear in-memory daily counters (tests / admin reset)."""
    _CAP_COUNTERS.clear()


def get_secret(name: str) -> str:
    """Read a secret: env first, then Docker secret file /run/secrets/<name>, else "".

    Env lookup tries the exact name, then UPPER-CASED. The file fallback strips
    trailing whitespace/newlines. Never raises; "" means unconfigured (callers
    must degrade honestly, never invent a value).
    """
    n = (name or "").strip()
    if not n:
        return ""
    try:
        v = os.environ.get(n, "")
        if v:
            return v
        v = os.environ.get(n.upper(), "")
        if v:
            return v
    except Exception:
        pass
    try:
        base = n.split("/")[-1].split("\\")[-1]
        if not base or base in (".", ".."):
            return ""
        p = os.path.join("/run/secrets", base)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""
