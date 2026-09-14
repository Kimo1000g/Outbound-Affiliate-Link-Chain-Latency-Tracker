"""Versioned compliance feed — REMOVE hardcoded drift. P0 FIX.

YAML compliance_packs drift (helplines/badges change). This module pins a
versioned feed with last_verified dates; YAML remains as offline fallback.
Feed file: config/compliance_feed.json {version, last_verified, packs}.
Regulators: UKGC/AGCO/GGL/KSA + US state helplines (NCPG 1-800-GAMBLER et al).
"""
from __future__ import annotations
import json
from pathlib import Path

FEED_DEFAULT = {
    "version": "2026-09-01",
    "last_verified": "2026-09-01",
    "source": "bundled feed — verify against regulator sites before legal reliance",
    "packs": {
        "US-NJ": {"required_strings": ["21+", "1-800-GAMBLER", "T&Cs Apply", "Gambling Problem"],
                  "license_badges": ["NJ DGE", "DGE"], "min_age": 21, "helpline": "1-800-GAMBLER"},
        "UK": {"required_strings": ["18+", "BeGambleAware.org", "T&Cs Apply", "GamStop"],
               "license_badges": ["UKGC", "GamStop"], "min_age": 18, "helpline": "GamStop / BeGambleAware.org"},
        "CA-ON": {"required_strings": ["19+", "ConnexOntario", "T&Cs Apply"],
                  "license_badges": ["AGCO", "iGaming Ontario"], "min_age": 19, "helpline": "ConnexOntario 1-866-531-2600"},
        "DE": {"required_strings": ["18+", "Glücksspiel kann süchtig machen", "T&Cs Apply", "GGL"],
               "license_badges": ["GGL"], "min_age": 18, "helpline": "BZgA 0800 1 37 27 00"},
        "NL": {"required_strings": ["18+", "Loket Kansspel", "T&Cs Apply", "KSA"],
               "license_badges": ["KSA"], "min_age": 18, "helpline": "Loket Kansspel"},
    },
}


def load_feed(feed_path: str = "config/compliance_feed.json", fallback: dict | None = None) -> dict:
    try:
        p = Path(feed_path)
        if p.exists():
            j = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(j, dict) and j.get("packs"):
                return j
    except Exception:
        pass
    if isinstance(fallback, dict) and fallback.get("packs"):
        out = dict(FEED_DEFAULT)
        out["packs"] = {**FEED_DEFAULT["packs"], **fallback.get("packs", {})}
        return out
    return dict(FEED_DEFAULT)


def merged_packs(yaml_packs: dict, feed_path: str = "config/compliance_feed.json") -> tuple[dict, dict]:
    """Feed wins on conflicts; returns (packs, meta{version,last_verified})."""
    feed = load_feed(feed_path, fallback={"packs": yaml_packs or {}})
    packs = dict(yaml_packs or {})
    packs.update(feed.get("packs", {}))
    meta = {"version": feed.get("version", "?"), "last_verified": feed.get("last_verified", "?"),
            "source": feed.get("source", "")}
    return packs, meta
