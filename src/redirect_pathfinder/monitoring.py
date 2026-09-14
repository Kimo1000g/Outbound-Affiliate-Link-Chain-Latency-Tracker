"""APScheduler monitoring — always-on diffs (ContentKing parity for the one-shot engine).

Schedules _run_scheduled_audit rows (from a saved target list) on cron/interval,
persists to SQLite via persistence.save_run, POSTs alerts, and records diffs.
Wire in api lifespan when monitoring.schedule.enabled=true.
"""
from __future__ import annotations
import asyncio
import time
import uuid

_lock = asyncio.Lock()
_running = False


def schedule_cfg(cfg: dict) -> dict:
    # P0: DB-backed schedules win over YAML (YAML rewrite race removed). YAML remains as seed.
    try:
        from .job_store import schedule_load
        db = schedule_load()
        if db.get("enabled") and db.get("targets"):
            merged = dict(((cfg.get("monitoring", {}) or {}).get("schedule", {}) or {}))
            merged.update({"enabled": True, "interval_minutes": db.get("interval_minutes", 60),
                           "targets": db.get("targets", [])})
            return merged
    except Exception:
        pass
    return ((cfg.get("monitoring", {}) or {}).get("schedule", {}) or {})


def start_scheduler(app, cfg: dict):
    sc = schedule_cfg(cfg)
    if not sc.get("enabled"):
        return None
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
    except Exception:
        return None
    sched = AsyncIOScheduler()
    interval_min = int(sc.get("interval_minutes", 60))

    async def _tick():
        global _running
        # overlap guard: skip tick if previous still running
        if _running or _lock.locked():
            return
        _running = True
        async with _lock:
            from .orchestrator import load_config, run_bulk_audit
            from .alerting_export import build_alerts, post_alerts
            from .persistence import save_run
            import os
            try:
                live_cfg = load_config(os.environ.get("PATHFINDER_CFG", "config/enterprise.yaml"))
                # fresh schedule + targets each tick (no stale closure over sc)
                try:
                    fresh = schedule_cfg(live_cfg)
                except Exception:
                    fresh = {}
                try:
                    from .job_store import schedule_load
                    _db_sched = schedule_load()
                except Exception:
                    _db_sched = {}
                rows = (fresh.get("targets", []) or []) or (_db_sched.get("targets", []) or [])
                if not rows:
                    return
                chains = await run_bulk_audit(rows, live_cfg)
                bundle = {"revenue_at_risk": sum(c.revenue_at_risk for c in chains),
                          "health_index": round(sum(c.health_score for c in chains) / max(1, len(chains)), 1),
                          "sla": [], "alerts": []}
                for c in chains:
                    bundle["alerts"].extend(build_alerts(c))
                save_run(live_cfg.get("monitoring", {}).get("db_path", "output/pathfinder.db"),
                         f"sched-{uuid.uuid4().hex[:8]}-{int(time.time())}", time.time(),
                         [c.model_dump() for c in chains], bundle)
                await post_alerts(bundle["alerts"], live_cfg)
            except Exception:
                pass
            finally:
                _running = False

    sched.add_job(_tick, "interval", minutes=interval_min, id="pathfinder-monitor")
    sched.start()
    app.state.scheduler = sched
    return sched
