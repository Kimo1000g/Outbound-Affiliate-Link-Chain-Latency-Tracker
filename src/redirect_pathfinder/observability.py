"""Observability — P0 ADD. Prometheus /metrics + OpenTelemetry + Sentry hooks.

Enterprise won't buy without observability. Zero hard deps: prometheus_client,
opentelemetry, sentry_sdk are all optional; endpoints degrade to {enabled:false}.
"""
from __future__ import annotations
import os
import time
from contextlib import contextmanager

_START = time.perf_counter()
COUNTERS: dict[str, float] = {"audits_total": 0, "hops_total": 0, "errors_total": 0}


def bump(key: str, n: float = 1.0):
    COUNTERS[key] = COUNTERS.get(key, 0.0) + n
    try:
        _prom().bump(key, n)
    except Exception:
        pass


class _Prom:
    def __init__(self):
        self.ok = False
        try:
            from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST  # type: ignore
            self.Counter = Counter
            self.generate_latest = generate_latest
            self.CONTENT_TYPE_LATEST = CONTENT_TYPE_LATEST
            self.c_audits = Counter("pathfinder_audits_total", "Total chains audited")
            self.c_hops = Counter("pathfinder_hops_total", "Total hops traced")
            self.c_err = Counter("pathfinder_errors_total", "Total trace errors")
            self.ok = True
        except Exception:
            self.ok = False

    def bump(self, key: str, n: float):
        if not self.ok:
            return
        try:
            {"audits_total": self.c_audits, "hops_total": self.c_hops,
             "errors_total": self.c_err}.get(key, self.c_err).inc(n)
        except Exception:
            pass

    def render(self):
        if self.ok:
            return bytes(self.generate_latest()), self.CONTENT_TYPE_LATEST
        lines = [f"pathfinder_{k} {v}" for k, v in COUNTERS.items()]
        lines.append(f"pathfinder_uptime_s {round(time.perf_counter()-_START,1)}")
        return ("\n".join(lines) + "\n").encode(), "text/plain; version=0.0.4"


_PROM = _Prom()


def _prom() -> _Prom:
    return _PROM


def metrics_payload() -> tuple[bytes, str]:
    return _PROM.render()


def init_tracing():
    """Best-effort OTel + Sentry init from env. Never raises."""
    try:
        ep = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        if ep:
            try:
                from opentelemetry import trace  # type: ignore
                from opentelemetry.sdk.trace import TracerProvider  # type: ignore
                trace.set_tracer_provider(TracerProvider())
            except Exception:
                pass
    except Exception:
        pass
    try:
        dsn = os.environ.get("SENTRY_DSN", "")
        if dsn:
            try:
                import sentry_sdk  # type: ignore
                sentry_sdk.init(dsn=dsn, traces_sample_rate=0.1)
            except Exception:
                pass
    except Exception:
        pass


@contextmanager
def span(name: str, attributes: dict | None = None):
    """OTel span context manager — no-op (yields None) when OTel is missing.

    Usage: `with span("audit_single_url", {"source_url": url}): ...`
    Never raises, even if the exporter is misconfigured mid-request.
    """
    try:
        from opentelemetry import trace as _trace  # type: ignore
        tracer = _trace.get_tracer("redirect_pathfinder")
        try:
            with tracer.start_as_current_span(str(name)) as s:  # type: ignore[attr-defined]
                try:
                    if attributes and s is not None and hasattr(s, "set_attribute"):
                        for k, v in attributes.items():
                            try:
                                s.set_attribute(str(k), str(v)[:500])
                            except Exception:
                                pass
                except Exception:
                    pass
                yield s
        except Exception:
            yield None
    except Exception:
        yield None
