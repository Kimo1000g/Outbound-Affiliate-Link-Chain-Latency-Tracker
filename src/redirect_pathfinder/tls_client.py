"""TLS-impersonation HTTP client — P0 fix. Wires the installed-but-unused curl-cffi.

Strategy (honest fallback chain):
1. Try curl_cffi AsyncSession with impersonate=chrome131 (or chrome124/safari18 fallbacks).
   Real Chrome TLS/JA3 + HTTP/2 fingerprint — beats naive httpx vs Datadome/PX/Cloudflare.
2. On any failure (or curl-cffi missing), fall back to httpx with rotated 2026 UAs.
3. Callers get (status, headers_dict, body_bytes, elapsed_ms, fingerprint_label).

Also exposes ROTATING_UAS (quarterly rotation — Chrome 132 / Safari 18.4 / Edge 132)
so tracer/intel/autofill stop sending stale Chrome 131 as a bot signal.
"""
from __future__ import annotations
import time

# Quarterly rotation — Q3 2026 set. Bump each quarter; stale UA = bot signal.
ROTATING_UAS: dict[str, str] = {
    "desktop_chrome": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "desktop_edge": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0",
    "desktop_safari": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15",
    "mobile_ios": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Mobile/15E148 Safari/604.1",
    "mobile_android": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36",
}
IMPERSONATE_MAP = {
    "desktop_chrome": "chrome131",
    "desktop_edge": "chrome131",
    "desktop_safari": "safari18_0",
    "mobile_ios": "safari18_0",
    "mobile_android": "chrome131",
}
UA_VERSION_NOTE = "UA set Q3-2026 (Chrome 132 / Safari 18.4). Rotate quarterly."
TLS_IMPERSONATION_NOTE = "curl-cffi impersonate=chrome131 (Chrome TLS/JA3+H2). Fallback httpx when unavailable."


def impersonate_for(device: str) -> str:
    return IMPERSONATE_MAP.get(device, "chrome131")


def ua_for(device: str) -> str:
    return ROTATING_UAS.get(device, ROTATING_UAS["desktop_chrome"])


async def fetch_with_tls_impersonation(url: str, *, device: str = "desktop_chrome",
                                       headers: dict | None = None,
                                       timeout_s: int = 20,
                                       proxy: str = "",
                                       max_bytes: int = 5 * 1024 * 1024) -> dict:
    """Single GET with TLS impersonation. Returns dict(status, headers, body, ms, via)."""
    hdrs = dict(headers or {})
    hdrs.setdefault("User-Agent", ua_for(device))
    hdrs.setdefault("Accept", "text/html,application/xhtml+xml,*/*;q=0.8")
    hdrs.setdefault("Accept-Language", "en-US,en;q=0.9")
    t0 = time.perf_counter()
    # 1) curl-cffi path
    try:
        from curl_cffi.requests import AsyncSession  # type: ignore
        async with AsyncSession(impersonate=impersonate_for(device), timeout=timeout_s) as s:
            kw: dict = {"headers": hdrs}
            if proxy:
                kw["proxies"] = {"http": proxy, "https": proxy}
            r = await s.get(url, **kw)
            body = r.content or b""
            if len(body) > max_bytes:
                body = body[:max_bytes]
            ms = (time.perf_counter() - t0) * 1000.0
            return {"status": r.status_code, "headers": dict(r.headers),
                    "body": body, "ms": round(ms, 1),
                    "via": f"curl-cffi/{impersonate_for(device)}"}
    except Exception as e_cffi:
        # 2) httpx fallback (rotated UA)
        try:
            import httpx
            timeout = httpx.Timeout(timeout_s)
            async with httpx.AsyncClient(follow_redirects=False, timeout=timeout,
                                         headers=hdrs, http2=True) as c:
                r = await c.get(url)
                try:
                    body = await r.aread()
                except Exception:
                    body = r.content or b""
                if len(body) > max_bytes:
                    body = body[:max_bytes]
                ms = (time.perf_counter() - t0) * 1000.0
                return {"status": r.status_code, "headers": dict(r.headers),
                        "body": bytes(body), "ms": round(ms, 1),
                        "via": f"httpx-fallback ({type(e_cffi).__name__})"}
        except Exception as e2:
            ms = (time.perf_counter() - t0) * 1000.0
            return {"status": 0, "headers": {}, "body": b"", "ms": round(ms, 1),
                    "via": "error", "error": f"{type(e2).__name__}: {str(e2)[:200]}"}
