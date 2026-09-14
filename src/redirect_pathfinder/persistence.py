"""SQLite persistence — runs table + diffs. Replaces output/*.json-from-RAM + disk races.

One file (monitoring.db_path, default output/pathfinder.db). Tables:
  runs(id, started, finished, targets, broken, revenue_at_risk, health, payload_json)
  findings(run_id, source_url, verdict, reason, revenue_at_risk)
Diffs: compare latest two runs -> {new_broken, fixed, health_delta}.
Postgres-ready: swap _connect() for asyncpg DSN via PATHFINDER_PG_DSN (schema identical).
"""
from __future__ import annotations
import json
import sqlite3
import time
from pathlib import Path


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db_path)
    c.execute("""CREATE TABLE IF NOT EXISTS runs(
      id TEXT PRIMARY KEY, started REAL, finished REAL, targets INT, broken INT,
      revenue_at_risk REAL, health REAL, payload TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS findings(
      run_id TEXT, source_url TEXT, verdict TEXT, reason TEXT, revenue_at_risk REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started)")
    return c


def enforce_retention(db_path: str, keep_runs: int = 90) -> dict:
    """Delete oldest runs beyond keep_runs + their findings."""
    try:
        c = _connect(db_path)
        try:
            ids = [r[0] for r in c.execute(
                "SELECT id FROM runs ORDER BY started DESC LIMIT -1 OFFSET ?",
                (keep_runs,)).fetchall()]
            for rid in ids:
                c.execute("DELETE FROM findings WHERE run_id=?", (rid,))
                c.execute("DELETE FROM runs WHERE id=?", (rid,))
            c.commit()
            return {"pruned": len(ids), "keep_runs": keep_runs}
        finally:
            c.close()
    except Exception:
        return {"pruned": 0, "keep_runs": keep_runs}


def delete_run(db_path: str, run_id: str) -> bool:
    """Delete a run + its findings. Returns True if a run row was removed."""
    c = _connect(db_path)
    try:
        c.execute("DELETE FROM findings WHERE run_id=?", (run_id,))
        cur = c.execute("DELETE FROM runs WHERE id=?", (run_id,))
        c.commit()
        return (cur.rowcount or 0) > 0
    finally:
        c.close()


def save_run(db_path: str, run_id: str, started: float, chains: list[dict], bundle: dict) -> dict:
    c = _connect(db_path)
    try:
        broken = sum(1 for ch in chains if not ch.get("chain_ok") or not ch.get("params_intact"))
        c.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?,?,?)",
                  (run_id, started, time.time(), len(chains), broken,
                   bundle.get("revenue_at_risk", 0), bundle.get("health_index", 0),
                   json.dumps({"sla": bundle.get("sla"), "alerts": bundle.get("alerts")})[:200000]))
        # idempotent retry: clear prior findings for this run before inserting
        c.execute("DELETE FROM findings WHERE run_id=?", (run_id,))
        for ch in chains:
            c.execute("INSERT INTO findings VALUES(?,?,?,?,?)",
                      (run_id, ch.get("source_url", ""),
                       "ok" if (ch.get("chain_ok") and ch.get("params_intact")) else "broken",
                       ch.get("broken_reason", "")[:300], ch.get("revenue_at_risk", 0)))
        c.commit()
        try:
            enforce_retention(db_path)
        except Exception:
            pass
        return {"run_id": run_id, "targets": len(chains), "broken": broken}
    finally:
        c.close()


def list_runs(db_path: str, limit: int = 20) -> list[dict]:
    try:
        c = _connect(db_path)
        try:
            rows = c.execute("SELECT id,started,finished,targets,broken,revenue_at_risk,health FROM runs ORDER BY started DESC LIMIT ?",
                             (limit,)).fetchall()
            return [{"run_id": r[0], "started": r[1], "finished": r[2], "targets": r[3],
                     "broken": r[4], "revenue_at_risk": r[5], "health": r[6]} for r in rows]
        finally:
            c.close()
    except Exception:
        return []


def diff_last_two(db_path: str) -> dict:
    """Compare verdicts of the two most recent runs by source_url."""
    try:
        c = _connect(db_path)
        try:
            ids = [r[0] for r in c.execute("SELECT id FROM runs ORDER BY started DESC LIMIT 2").fetchall()]
            if len(ids) < 2:
                return {"comparable": False, "reason": "need 2+ runs — schedule recurring audits first"}
            def verdicts(rid: str) -> dict:
                return {r[0]: (r[1], r[2]) for r in
                        c.execute("SELECT source_url,verdict,reason FROM findings WHERE run_id=?", (rid,)).fetchall()}
            prev, latest = verdicts(ids[1]), verdicts(ids[0])
            new_broken = [u for u, (v, _) in latest.items() if v == "broken" and prev.get(u, ("ok", ""))[0] == "ok"]
            fixed = [u for u, (v, _) in latest.items() if v == "ok" and prev.get(u, ("ok", ""))[0] == "broken"]
            r_old = c.execute("SELECT health FROM runs WHERE id=?", (ids[1],)).fetchone()
            r_new = c.execute("SELECT health FROM runs WHERE id=?", (ids[0],)).fetchone()
            return {"comparable": True, "prev_run": ids[1], "latest_run": ids[0],
                    "new_broken": new_broken[:50], "fixed": fixed[:50],
                    "health_delta": round((r_new[0] if r_new else 0) - (r_old[0] if r_old else 0), 1)}
        finally:
            c.close()
    except Exception as e:
        return {"comparable": False, "reason": str(e)[:200]}
