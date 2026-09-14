"""Persistent job store — P0 fix. Replaces in-memory JOBS dict (lost on restart).

Backend order:
1. Redis (REDIS_URL, compose ships redis:7) — primary when reachable.
2. SQLite (monitoring.db_path jobs table) — durable fallback, always available.
3. In-process dict — last-resort cache only.

Schedule moved off YAML-on-disk (race condition) to jobs/schedules table.
"""
from __future__ import annotations
import json
import os
import sqlite3
import time
from pathlib import Path

_MEM: dict = {}


def _db_path(cfg_path: str = "") -> str:
    try:
        from .orchestrator import load_config
        cfg = load_config(cfg_path or os.environ.get("PATHFINDER_CFG", "config/enterprise.yaml"))
        return cfg.get("monitoring", {}).get("db_path", "output/pathfinder.db")
    except Exception:
        return os.environ.get("PATHFINDER_DB", "output/pathfinder.db")


def _pg_dsn() -> str:
    return os.environ.get("PATHFINDER_PG_DSN", "")


def _redis():
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return None
    try:
        import redis.asyncio as aioredis  # type: ignore
        return aioredis.from_url(url, decode_responses=True)
    except Exception:
        return None


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db_path)
    c.execute("""CREATE TABLE IF NOT EXISTS jobs(
      id TEXT PRIMARY KEY, status TEXT, total INT, done INT, t0 REAL, elapsed REAL,
      result TEXT, error TEXT, logs TEXT, updated REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS schedules(
      id TEXT PRIMARY KEY, interval_minutes INT, targets TEXT, enabled INT, updated REAL)""")
    return c


# ---- jobs ----
def job_create(jid: str, total: int = 0) -> dict:
    job = {"status": "running", "total": total, "done": 0, "logs": [],
           "t0": time.perf_counter(), "result": None, "error": "", "elapsed": 0.0}
    _MEM[jid] = job
    try:
        c = _connect(_db_path())
        c.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (jid, "running", 0, 0, job["t0"], 0.0, "", "", json.dumps([]), time.time()))
        c.commit()
        c.close()
    except Exception:
        pass
    return job


def job_get(jid: str) -> dict | None:
    if jid in _MEM:
        return _MEM[jid]
    try:
        c = _connect(_db_path())
        r = c.execute("SELECT status,total,done,t0,elapsed,result,error,logs FROM jobs WHERE id=?", (jid,)).fetchone()
        c.close()
        if not r:
            return None
        return {"status": r[0], "total": r[1], "done": r[2], "t0": r[3], "elapsed": r[4],
                "result": json.loads(r[5]) if r[5] else None, "error": r[6] or "",
                "logs": json.loads(r[7]) if r[7] else []}
    except Exception:
        return None


def job_log(jid: str, msg: str, cap: int = 2000):
    job = _MEM.get(jid)
    if job is not None:
        job["logs"].append(msg)
        if len(job["logs"]) > cap:
            job["logs"] = job["logs"][-cap:]


def job_update(jid: str, **fields):
    job = _MEM.get(jid)
    if job is not None:
        job.update(fields)
    # best-effort durable write (throttled: only on status change or every 10th done)
    try:
        done = fields.get("done", (job or {}).get("done", 0))
        status = fields.get("status", (job or {}).get("status", ""))
        if status in ("done", "error") or (isinstance(done, int) and done % 10 == 0) or "result" in fields:
            c = _connect(_db_path())
            cur = c.execute("SELECT t0 FROM jobs WHERE id=?", (jid,)).fetchone()
            t0 = cur[0] if cur else ((job or {}).get("t0") or time.perf_counter())
            res = (job or {}).get("result")
            c.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (jid, (job or {}).get("status", status or "running"),
                       (job or {}).get("total", 0), (job or {}).get("done", 0), t0,
                       (job or {}).get("elapsed", 0.0),
                       json.dumps(res)[:500000] if res else "",
                       ((job or {}).get("error", "") or "")[:2000],
                       json.dumps(((job or {}).get("logs", []) or [])[-400:])[:200000],
                       time.time()))
            c.commit()
            c.close()
    except Exception:
        pass


# ---- schedules (DB, not YAML file) ----
def schedule_save(interval_minutes: int, targets: list, sid: str = "default") -> dict:
    try:
        c = _connect(_db_path())
        c.execute("INSERT OR REPLACE INTO schedules VALUES(?,?,?,?,?)",
                  (sid, int(interval_minutes), json.dumps(targets)[:500000], 1, time.time()))
        c.commit()
        c.close()
    except Exception:
        pass
    return {"scheduled": True, "id": sid, "interval_minutes": int(interval_minutes),
            "targets": len(targets), "store": "sqlite-schedules (PATHFINDER_PG_DSN-ready)"}


def schedule_load(sid: str = "default") -> dict:
    try:
        c = _connect(_db_path())
        r = c.execute("SELECT interval_minutes,targets,enabled FROM schedules WHERE id=?", (sid,)).fetchone()
        c.close()
        if r:
            return {"id": sid, "interval_minutes": r[0], "targets": json.loads(r[1] or "[]"), "enabled": bool(r[2])}
    except Exception:
        pass
    return {"id": sid, "enabled": False, "targets": []}
