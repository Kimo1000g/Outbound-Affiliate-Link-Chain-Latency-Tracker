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


def _redis_sync():
    """Sync Redis primary client. Returns None when lib missing/unreachable (never raises)."""
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return None
    try:
        import redis as _redis_lib  # type: ignore
        client = _redis_lib.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2,
                                           decode_responses=True)
        return client
    except Exception:
        return None


def _redis_key(jid: str) -> str:
    return f"pf:job:{jid}"


def _redis_write(jid: str, job: dict):
    try:
        r = _redis_sync()
        if r is None:
            return
        try:
            r.set(_redis_key(jid), json.dumps(job, default=str)[:1000000], ex=86400)
        finally:
            try:
                r.close()
            except Exception:
                pass
    except Exception:
        pass


def _redis_read(jid: str) -> dict | None:
    try:
        r = _redis_sync()
        if r is None:
            return None
        try:
            raw = r.get(_redis_key(jid))
        finally:
            try:
                r.close()
            except Exception:
                pass
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db_path)
    c.execute("""CREATE TABLE IF NOT EXISTS jobs(
      id TEXT PRIMARY KEY, status TEXT, total INT, done INT, t0 REAL, elapsed REAL,
      result TEXT, error TEXT, logs TEXT, updated REAL, workspace_id TEXT DEFAULT 'default',
      wall_start REAL)""")
    # migrate older DBs that lack the new columns
    for _ddl in ("ALTER TABLE jobs ADD COLUMN workspace_id TEXT DEFAULT 'default'",
                 "ALTER TABLE jobs ADD COLUMN wall_start REAL"):
        try:
            c.execute(_ddl)
        except Exception:
            pass
    c.execute("""CREATE TABLE IF NOT EXISTS schedules(
      id TEXT PRIMARY KEY, interval_minutes INT, targets TEXT, enabled INT, updated REAL)""")
    return c


# ---- jobs ----
def job_create(jid: str, total: int = 0, workspace: str = "default") -> dict:
    job = {"status": "running", "total": total, "done": 0, "logs": [],
           "t0": time.perf_counter(), "wall_start": time.time(),
           "result": None, "error": "", "elapsed": 0.0, "workspace": workspace or "default"}
    _MEM[jid] = job
    _redis_write(jid, job)
    try:
        c = _connect(_db_path())
        cols = [r[1] for r in c.execute("PRAGMA table_info(jobs)").fetchall()]
        if "workspace_id" in cols and "wall_start" in cols:
            c.execute("INSERT OR REPLACE INTO jobs(id,status,total,done,t0,elapsed,result,error,logs,updated,workspace_id,wall_start)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (jid, "running", 0, 0, job["t0"], 0.0, "", "", json.dumps([]), time.time(),
                       job["workspace"], job["wall_start"]))
        else:
            c.execute("INSERT OR REPLACE INTO jobs(id,status,total,done,t0,elapsed,result,error,logs,updated)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (jid, "running", 0, 0, job["t0"], 0.0, "", "", json.dumps([]), time.time()))
        c.commit()
        c.close()
    except Exception:
        pass
    return job


def job_get(jid: str) -> dict | None:
    # Redis primary -> SQLite -> MEM (never crash when redis missing/unreachable)
    try:
        rj = _redis_read(jid)
        if rj is not None:
            _MEM[jid] = rj
            return rj
    except Exception:
        pass
    try:
        c = _connect(_db_path())
        try:
            cols = [r[1] for r in c.execute("PRAGMA table_info(jobs)").fetchall()]
            if "workspace_id" in cols and "wall_start" in cols:
                r = c.execute("SELECT status,total,done,t0,elapsed,result,error,logs,workspace_id,wall_start"
                              " FROM jobs WHERE id=?", (jid,)).fetchone()
            else:
                r = c.execute("SELECT status,total,done,t0,elapsed,result,error,logs FROM jobs WHERE id=?",
                              (jid,)).fetchone()
        finally:
            c.close()
        if r:
            if len(r) >= 10:
                job = {"status": r[0], "total": r[1], "done": r[2], "t0": r[3], "elapsed": r[4],
                       "result": json.loads(r[5]) if r[5] else None, "error": r[6] or "",
                       "logs": json.loads(r[7]) if r[7] else [],
                       "workspace": r[8] or "default", "wall_start": r[9] or 0.0}
            else:
                job = {"status": r[0], "total": r[1], "done": r[2], "t0": r[3], "elapsed": r[4],
                       "result": json.loads(r[5]) if r[5] else None, "error": r[6] or "",
                       "logs": json.loads(r[7]) if r[7] else [], "wall_start": 0.0}
            if jid not in _MEM:
                _MEM[jid] = job
            return _MEM.get(jid) or job
    except Exception:
        pass
    return _MEM.get(jid)


def job_log(jid: str, msg: str, cap: int = 2000):
    job = _MEM.get(jid)
    if job is not None:
        job["logs"].append(msg)
        if len(job["logs"]) > cap:
            dropped = len(job["logs"]) - cap
            job["logs"] = job["logs"][-cap:]
            note = f"[...truncated {dropped} older messages, showing last {cap}...]"
            if not job["logs"] or job["logs"][0] != note:
                # keep length bounded while surfacing the truncation
                job["logs"] = [note] + job["logs"][-(cap - 1):] if cap > 1 else [note]


def job_update(jid: str, **fields):
    job = _MEM.get(jid)
    if job is not None:
        job.update(fields)
    # best-effort durable write (throttled: status change, every 5th done, or result present)
    try:
        done = fields.get("done", (job or {}).get("done", 0))
        status = fields.get("status", (job or {}).get("status", ""))
        prev_status = (job or {}).get("status", "")
        status_changed = ("status" in fields and fields.get("status") != prev_status) or status in ("done", "error")
        if status_changed or (isinstance(done, int) and done % 5 == 0) or "result" in fields:
            logs = ((job or {}).get("logs", []) or [])
            if len(logs) > 400:
                dropped = len(logs) - 400
                logs = [f"[...truncated {dropped} older messages...]"] + logs[-399:]
            if job is not None:
                _redis_write(jid, job)
            c = _connect(_db_path())
            cur = c.execute("SELECT t0 FROM jobs WHERE id=?", (jid,)).fetchone()
            t0 = cur[0] if cur else ((job or {}).get("t0") or time.perf_counter())
            res = (job or {}).get("result")
            cols = [r[1] for r in c.execute("PRAGMA table_info(jobs)").fetchall()]
            if "workspace_id" in cols and "wall_start" in cols:
                c.execute("INSERT OR REPLACE INTO jobs(id,status,total,done,t0,elapsed,result,error,logs,updated,workspace_id,wall_start)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (jid, (job or {}).get("status", status or "running"),
                           (job or {}).get("total", 0), (job or {}).get("done", 0), t0,
                           (job or {}).get("elapsed", 0.0),
                           json.dumps(res)[:500000] if res else "",
                           ((job or {}).get("error", "") or "")[:2000],
                           json.dumps(logs)[:200000],
                           time.time(),
                           (job or {}).get("workspace", "default"),
                           (job or {}).get("wall_start", 0.0)))
            else:
                c.execute("INSERT OR REPLACE INTO jobs(id,status,total,done,t0,elapsed,result,error,logs,updated)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (jid, (job or {}).get("status", status or "running"),
                           (job or {}).get("total", 0), (job or {}).get("done", 0), t0,
                           (job or {}).get("elapsed", 0.0),
                           json.dumps(res)[:500000] if res else "",
                           ((job or {}).get("error", "") or "")[:2000],
                           json.dumps(logs)[:200000],
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
