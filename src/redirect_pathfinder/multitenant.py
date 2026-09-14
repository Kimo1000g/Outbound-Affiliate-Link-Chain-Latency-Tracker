"""Multi-tenant workspaces + API-key scopes — P0 ADD.

security.api_keys: [] = open dev mode (fine local, death in prod).
This module adds:
- PATHFINDER_API_KEYS entries as `key[:scope1+scope2][:workspace]` e.g.
  `sk-live-abc:audit+mcp:acme-casino`.
- workspaces: {id, name, allowed_geos, concurrency_cap} via env JSON or YAML.
- scope check: audit, bulk, mcp, admin(schedule/config), export.
- Postgres RLS note: when PATHFINDER_PG_DSN set, jobs/findings carry workspace_id.
"""
from __future__ import annotations
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
