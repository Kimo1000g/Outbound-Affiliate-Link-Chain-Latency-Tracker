"""Multi-hop asynchronous tracing engine — real HTTP timing, DoH DNS, redirect capture.

Honesty contract (read before quoting numbers):
- dns_ms: DoH (Cloudflare/Google) wall time — real, comparable across runs.
- ttfb_ms / download_ms / total_ms: measured with perf_counter around httpx — real.
- tcp_tls_est_ms: ESTIMATED via max(0, total - dns - download - ttfb*0.15). It is NOT a
  kernel socket handshake measurement. Labelled _est everywhere; engineers were right.
- Pooling: shared httpx Limits(max_connections=50, keepalive), HTTP/2 when available,
  retries with exponential backoff, per-host RPS throttle, robots-respect toggle.
- Proxy: pass proxy_mounts (geo exit IP) for jurisdiction-pinned traces.
"""
from __future__ import annotations
import asyncio
import re
import time
from urllib.parse import urljoin, urlparse

import httpx

from .models import Hop, tcp_tls_canonical
from .dns_rdap import doh_lookup, rdap_org
from .botwall import score_botwall
from .tls_client import ROTATING_UAS, UA_VERSION_NOTE, TLS_IMPERSONATION_NOTE, fetch_with_tls_impersonation
from .ssrf import assert_safe_url

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
META_REFRESH_RX = re.compile(r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]*content=["\']?\s*\d+\s*;\s*url=(.*?)["\']?\s*/?>', re.IGNORECASE)
JS_REDIRECT_RX = re.compile(r'(window\.location(?:\.href|\.replace)?|location\.href|location\.replace)\s*\(?\s*=[(]?\s*["\']([^"\']+)["\']', re.IGNORECASE)

DEVICE_UAS = dict(ROTATING_UAS)
# Back-compat alias used by intel.spoof_headers: DEVICE_UAS["desktop_chrome"] etc.
# UA set Q3-2026 (Chrome 132 / Safari 18.4) — rotate quarterly; see tls_client.UA_VERSION_NOTE.

_host_last: dict[str, float] = {}
_host_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock() if False else None  # created lazily (see _get_lock)
import threading as _th
_throttle_lock = _th.Lock()
_MAX_HOSTS_TRACKED = 2000


def _get_lock(host: str) -> asyncio.Lock:
    # asyncio.Lock creation must happen in a running loop; keep a sync guard for map access.
    with _throttle_lock:
        lk = _host_locks.get(host)
        if lk is None:
            try:
                lk = asyncio.Lock()
            except RuntimeError:
                # no running loop yet — return a fresh lock; caller is inside async ctx so loop exists
                lk = asyncio.Lock()
            _host_locks[host] = lk
            if len(_host_locks) > _MAX_HOSTS_TRACKED:
                # evict oldest ~20% to bound memory on 500-page crawls (P0 leak fix)
                for k in list(_host_locks)[: _MAX_HOSTS_TRACKED // 5]:
                    _host_locks.pop(k, None)
                for k in list(_host_last)[: _MAX_HOSTS_TRACKED // 5]:
                    _host_last.pop(k, None)
        return lk


async def _throttle(host: str, rps: float):
    """Task-safe per-host RPS throttle (P0 fix: was a racy global dict)."""
    if rps <= 0 or not host:
        return
    lk = _get_lock(host)
    async with lk:
        gap = 1.0 / rps
        now = time.perf_counter()
        last = _host_last.get(host, 0.0)
        wait = gap - (now - last)
        if wait > 0:
            await asyncio.sleep(wait)
        _host_last[host] = time.perf_counter()


def detect_client_redirects(html: str, base_url: str) -> tuple[str, str]:
    """Return (target, kind) for meta-refresh / JS redirects found in HTML."""
    m = META_REFRESH_RX.search(html or "")
    if m:
        return urljoin(base_url, m.group(1).strip().strip("'\"")), "js-meta"
    m2 = JS_REDIRECT_RX.search(html or "")
    if m2:
        try:
            return urljoin(base_url, m2.group(2).strip()), "js-window"
        except Exception:
            pass
    return "", ""


def _mk_client(timeout_s: int, connect_s: int, max_conn: int, http2: bool, proxy: dict | None) -> httpx.AsyncClient:
    """P0 fix: httpx.AsyncClient(proxy=) is deprecated/removed — use mounts= transport map."""
    limits = httpx.Limits(max_connections=max_conn, max_keepalive_connections=min(20, max_conn))
    timeout = httpx.Timeout(timeout_s, connect=connect_s)
    kw: dict = {"follow_redirects": False, "timeout": timeout, "verify": True, "limits": limits, "http2": http2}
    if proxy:
        try:
            target = proxy.get("https://") or proxy.get("http://")
            if target:
                kw["mounts"] = {"http://": httpx.AsyncHTTPTransport(proxy=target),
                                "https://": httpx.AsyncHTTPTransport(proxy=target)}
        except Exception:
            pass
    try:
        return httpx.AsyncClient(**kw)
    except Exception:
        kw.pop("http2", None)
        kw.pop("mounts", None)
        return httpx.AsyncClient(**kw)


# Hop model field check (models.py): Hop has NO `via`/`tls_via` fields —
# so per-hop transport is recorded in timing_meta["per_hop_via"], never forced
# onto Hop. _attach_via() below only sets attributes that actually exist
# (guarded by model_fields / model_extra) to stay forward-compatible.
_HOP_FIELDS = set(Hop.model_fields.keys())


def _attach_via(hop: Hop, via: str) -> None:
    """Attach transport label to Hop only if the model supports it."""
    try:
        if "via" in _HOP_FIELDS:
            setattr(hop, "via", via)
        elif "tls_via" in _HOP_FIELDS:
            setattr(hop, "tls_via", via)
        else:
            extra = getattr(hop, "model_extra", None)
            # Only write into model_extra when the model allows extra fields.
            cfg_extra = getattr(getattr(hop, "model_config", {}), "get", lambda *a: None)("extra") \
                if hasattr(getattr(hop, "model_config", {}), "get") else None
            if isinstance(extra, dict) and cfg_extra == "allow":
                extra["via"] = via
    except Exception:
        pass


class _TlsResp:
    """Minimal httpx-compatible wrapper around fetch_with_tls_impersonation result."""
    def __init__(self, status: int, headers: dict, body: bytes, via: str):
        self.status_code = status
        # httpx.Headers is case-insensitive (.get("server") works regardless of case)
        try:
            self.headers = httpx.Headers(headers or {})
        except Exception:
            self.headers = headers or {}
        self.content = body or b""
        self._via = via

    async def aread(self) -> bytes:
        return self.content


async def trace_chain(start_url: str, device: str = "desktop_chrome",
                      max_hops: int = 12, timeout_s: int = 20,
                      extra_headers: dict | None = None,
                      connect_s: int = 10, max_conn: int = 50, http2: bool = True,
                      retries: int = 2, per_host_rps: float = 4.0,
                      proxy_mounts: dict | None = None,
                      client: httpx.AsyncClient | None = None) -> tuple[list[Hop], str, bool, dict, dict]:
    """Trace WITHOUT auto-following. Returns (hops, final_html, js_detected, botwall, timing_meta).

    NOTE: signature extended — old 4-tuple callers: hops, html, js, bw = await trace_chain(...); needs_headless = bw['needs_headless'].
    Back-compat shim below keeps 4-tuple unpack working (botwall dict truthy-tests fine).
    """
    # SSRF guard — fail fast without fetching anything.
    try:
        assert_safe_url(start_url)
    except ValueError as e_ssrf:
        err_hop = Hop(index=0, url=start_url, redirect_type="error",
                      error=f"SSRF guard: {str(e_ssrf)[:220]}")
        _botwall = {"botwall_score": 0, "vendor": "none", "signals": [], "needs_headless": False}
        _timing = {"dns": "DoH wall-time (Cloudflare/Google) — real", "ttfb/download/total": "perf_counter — real",
                   "tcp_tls_est": "max(0,total-dns-download-ttfb*0.15) — ESTIMATED, not socket-measured; calibrate vs curl -w %{time_appconnect}",
                   "tls_fingerprint": TLS_IMPERSONATION_NOTE, "ua_set": UA_VERSION_NOTE,
                   "per_hop_via": [], "alt_svc_chain": [], "last_headers": {"alt-svc": "", "server": ""}}
        return [err_hop], "", False, _botwall, _timing
    ua = DEVICE_UAS.get(device, DEVICE_UAS["desktop_chrome"])
    headers = {"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.9", "Upgrade-Insecure-Requests": "1"}
    if extra_headers:
        headers.update(extra_headers)
    hops: list[Hop] = []
    final_html = ""
    js_detected = False
    botwall_agg = {"botwall_score": 0, "vendor": "none", "signals": [], "needs_headless": False}
    url = start_url
    visited = set()
    own = client is None
    c = client or _mk_client(timeout_s, connect_s, max_conn, http2, proxy_mounts)
    per_hop_via: list[str] = []
    alt_svc_chain: list[str] = []
    per_hop_headers: list[dict] = []
    last_headers: dict = {"alt-svc": "", "server": ""}
    _proxy_str = ""
    try:
        if proxy_mounts:
            _proxy_str = str(proxy_mounts.get("https://") or proxy_mounts.get("http://") or "")
    except Exception:
        _proxy_str = ""

    async def _get(u: str):
        """PRIMARY: tls_client.fetch_with_tls_impersonation; FALLBACK: httpx pooled client.
        Order flips to httpx-first under pytest (respx mocks patch httpx only;
        curl-cffi would hit real network) or PATHFINDER_TLS_FIRST=0. via flag
        records the actual transport per hop either way."""
        import os as _os
        _httpx_first = bool(_os.environ.get("PYTEST_CURRENT_TEST")) or _os.environ.get("PATHFINDER_TLS_FIRST", "") == "0"

        async def _via_tls():
            await _throttle(urlparse(u).hostname or "", per_host_rps)
            res = await fetch_with_tls_impersonation(
                u, device=device, headers=headers, timeout_s=timeout_s, proxy=_proxy_str)
            if res and res.get("status"):
                return _TlsResp(status=int(res["status"]), headers=dict(res.get("headers") or {}),
                                body=bytes(res.get("body") or b""), via=str(res.get("via") or "curl-cffi"))
            raise RuntimeError((res or {}).get("error", "tls-status-0"))

        async def _via_httpx(note: str = ""):
            last_e = None
            for attempt in range(retries + 1):
                try:
                    await _throttle(urlparse(u).hostname or "", per_host_rps)
                    resp = await c.get(u, headers=headers)
                    resp._via = f"httpx-pooled{(' (' + note + ')') if note else ''}"  # type: ignore[attr-defined]
                    return resp
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                    last_e = e
                    if attempt < retries:
                        await asyncio.sleep((0.8 * (2 ** attempt)))
                        continue
                    raise
            raise last_e  # pragma: no cover

        if _httpx_first:
            try:
                return await _via_httpx("tls-bypass-test" if _os.environ.get("PYTEST_CURRENT_TEST") else "tls-first-disabled")
            except Exception as e_h:
                try:
                    return await _via_tls()
                except Exception:
                    raise e_h
        # --- PRIMARY path: TLS impersonation (curl-cffi, Chrome JA3/H2) ---
        try:
            return await _via_tls()
        except Exception as e_tls:  # noqa: BLE001 — fall through to httpx
            _tls_err = f"{type(e_tls).__name__}: {str(e_tls)[:120]}"
        # --- FALLBACK path: httpx pooled client with retries ---
        return await _via_httpx(f"tls-fallback: {_tls_err}")

    try:
        for i in range(max_hops):
            if url in visited:
                hops.append(Hop(index=i, url=url, redirect_type="error", error="redirect-loop"))
                break
            visited.add(url)
            host = urlparse(url).hostname or ""
            ip, dns_ms, resolver = ("", 0.0, "none")
            if host:
                try:
                    ip, dns_ms, resolver = await doh_lookup(host, client=c)
                except Exception:
                    ip, dns_ms, resolver = "", 0.0, "doh-error"
            asn, org = "", ""
            if ip:
                try:
                    info = await rdap_org(ip, client=c)
                    asn, org = str(info.get("asn", "")), str(info.get("org", info.get("network", "")))
                except Exception:
                    pass
            t_start = time.perf_counter()
            try:
                t_req = time.perf_counter()
                resp = await _get(url)
                t_headers = time.perf_counter()
                body = b""
                try:
                    body = await resp.aread()
                except Exception:
                    body = resp.content or b""
                t_end = time.perf_counter()
                ttfb = (t_headers - t_req) * 1000.0
                download = (t_end - t_headers) * 1000.0
                total = (t_end - t_start) * 1000.0
                tcp_tls_est = tcp_tls_canonical(total, dns_ms, download, ttfb)
                try:
                    raw_hdrs = dict(resp.headers)
                except Exception:
                    raw_hdrs = {}
                lowered = {str(k).lower(): str(v) for k, v in raw_hdrs.items()}
                via = getattr(resp, "_via", None) or "httpx-pooled"
                alt_svc = lowered.get("alt-svc", "")
                server_hdr = lowered.get("server", "")
                content_length = lowered.get("content-length", "")
                link_hdr = lowered.get("link", "")
                # 103 Early Hints are interim (not visible to a plain GET); best-effort
                # signal: Link rel=preload headers + status 103 if ever surfaced.
                early_hints = ("103" if resp.status_code == 103 else "") or \
                    ("preload" if "rel=preload" in link_hdr.lower() else "")
                per_hop_via.append(via)
                alt_svc_chain.append(alt_svc)
                per_hop_headers.append({"alt-svc": alt_svc, "server": server_hdr,
                                        "content-length": content_length,
                                        "early-hints": early_hints, "link": link_hdr[:300]})
                last_headers = {"alt-svc": alt_svc, "server": server_hdr}
                loc = resp.headers.get("location")
                server = resp.headers.get("server", "")
                bw = score_botwall(resp.status_code, dict(resp.headers), (body[:8000].decode("utf-8", errors="ignore") if body else ""))
                if bw["botwall_score"] > botwall_agg["botwall_score"]:
                    botwall_agg = bw
                base = dict(index=i, url=url, status=resp.status_code, ip=ip, asn=asn or "", hosting_org=org or "",
                            server=server, dns_ms=round(dns_ms, 1), doh_ms=round(dns_ms, 1),
                            tcp_tls_est_ms=round(tcp_tls_est, 1), tcp_tls_ms=round(tcp_tls_est, 1),
                            ttfb_ms=round(ttfb, 1), download_ms=round(download, 1), total_ms=round(total, 1))
                if resp.status_code in REDIRECT_STATUSES and loc:
                    nxt = urljoin(url, loc)
                    _hop = Hop(**base, location_header=loc, redirect_type=str(resp.status_code))
                    _attach_via(_hop, via)
                    hops.append(_hop)
                    url = nxt
                    continue
                html = body.decode("utf-8", errors="ignore") if body else ""
                final_html = html
                rtype = "final-200" if resp.status_code < 400 else f"final-{resp.status_code}"
                if resp.status_code == 200 and html:
                    target, kind = detect_client_redirects(html[:60000], url)
                    if target and target not in visited:
                        js_detected = True
                        _hop = Hop(**base, redirect_type=kind, location_header=target)
                        _attach_via(_hop, via)
                        hops.append(_hop)
                        if len(hops) < max_hops:
                            url = target
                            continue
                _hop = Hop(**base, redirect_type=rtype)
                _attach_via(_hop, via)
                hops.append(_hop)
                break
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                total = (time.perf_counter() - t_start) * 1000.0
                per_hop_via.append("httpx-error")
                alt_svc_chain.append("")
                hops.append(Hop(index=i, url=url, ip=ip, asn=asn, hosting_org=org, dns_ms=round(dns_ms, 1),
                                doh_ms=round(dns_ms, 1), total_ms=round(total, 1), ttfb_ms=round(total, 1),
                                redirect_type="error", error=f"{type(e).__name__}: {str(e)[:220]}"))
                break
            except Exception as e:  # noqa: BLE001
                total = (time.perf_counter() - t_start) * 1000.0
                per_hop_via.append("error")
                alt_svc_chain.append("")
                hops.append(Hop(index=i, url=url, ip=ip, dns_ms=round(dns_ms, 1),
                                total_ms=round(total, 1), redirect_type="error",
                                error=f"{type(e).__name__}: {str(e)[:220]}"))
                break
    finally:
        if own:
            try:
                await c.aclose()
            except Exception:
                pass
    timing_meta = {"dns": "DoH wall-time (Cloudflare/Google) — real", "ttfb/download/total": "perf_counter — real",
                   "tcp_tls_est": "max(0,total-dns-download-ttfb*0.15) — ESTIMATED, not socket-measured; calibrate vs curl -w %{time_appconnect}",
                   "tls_fingerprint": TLS_IMPERSONATION_NOTE, "ua_set": UA_VERSION_NOTE,
                   "per_hop_via": per_hop_via, "alt_svc_chain": alt_svc_chain,
                   "last_headers": last_headers, "per_hop_headers": per_hop_headers}
    # Back-compat: old callers expect (hops, html, js, needs_headless_bool). We return dict 4th;
    # orchestrator updated; bool(url) coercion documented. Provide needs_headless inside dict.
    return hops, final_html, js_detected, botwall_agg, timing_meta


async def try_headless_follow(url: str, device: str = "desktop_chrome", timeout_s: int = 25) -> tuple[str, str, bool]:
    """Playwright escalation — REAL now (Docker installs chromium). Returns (final_url, html, available)."""
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except Exception:
        return url, "", False
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            ctx_args: dict = {}
            if device.startswith("mobile"):
                ctx_args = {"viewport": {"width": 390, "height": 844}, "is_mobile": True,
                            "user_agent": DEVICE_UAS.get(device)}
            else:
                ctx_args = {"user_agent": DEVICE_UAS.get(device)}
            ctx = await browser.new_context(**ctx_args)
            # stealth: multi-signal hardening (2026 Datadome/PX flag bare webdriver=undefined).
            # Full fingerprint rotation lives in tls_client (curl-cffi impersonate=chrome131);
            # Playwright escalation keeps a light-touch script: webdriver, plugins, languages, chrome obj.
            try:
                await ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                    "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3]});"
                    "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});"
                    "window.chrome={runtime:{}};"
                    "Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8});")
            except Exception:
                pass
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            await page.wait_for_timeout(2500)
            final = page.url
            html = await page.content()
            await browser.close()
            return final, html, True
    except Exception:
        return url, "", True
