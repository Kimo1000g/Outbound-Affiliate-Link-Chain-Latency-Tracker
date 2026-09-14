"""Input ingestion — strict sitemaps, CSV lists, crawl constraints, promo feeds, logs, baselines."""
from __future__ import annotations
import csv
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
import httpx


def _strict_urls(text: str) -> tuple[str, list[str]]:
    """Return (kind, urls) — kind ∈ urlset|index|'' ; strict: XML + <loc> + non-empty."""
    t = (text or "").strip()
    if not t.startswith("<") or "<loc>" not in t:
        return "", []
    try:
        root = ET.fromstring(t[:2000000])
    except Exception:
        return "", []
    locs = [e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.text and e.text.strip().startswith("http")]
    if not locs:
        return "", []
    tag = root.tag.lower()
    if "sitemapindex" in tag:
        return "index", locs
    return "urlset", locs


async def fetch_sitemap_urls(sitemap_url: str, limit: int = 500) -> list[str]:
    """Strict: HTTP 200 + XML + <loc>; sitemapindex recurses one level. Never trusts dead sitemaps."""
    try:
        from .ssrf import assert_safe_url, MAX_FETCH_BYTES
        try:
            assert_safe_url(sitemap_url)
        except ValueError:
            return []
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": "iGaming-Pathfinder/3.1"}) as c:
            r = await c.get(sitemap_url)
            if r.status_code != 200 or len(r.content or b"") > MAX_FETCH_BYTES:
                return []
            kind, locs = _strict_urls(r.text)
            if kind == "urlset":
                return locs[:limit]
            if kind == "index":
                out: list[str] = []
                for child in locs[:10]:
                    try:
                        try:
                            assert_safe_url(child)
                        except ValueError:
                            continue
                        rc = await c.get(child)
                        if rc.status_code != 200 or len(rc.content or b"") > MAX_FETCH_BYTES:
                            continue
                        k2, l2 = _strict_urls(rc.text)
                        if k2 == "urlset":
                            out.extend(l2)
                        if len(out) >= limit:
                            break
                    except Exception:
                        continue
                return out[:limit]
            return []
    except Exception:
        return []


def load_csv_targets(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append({k.strip(): (v or "").strip() for k, v in r.items()})
    return rows


def apply_crawl_constraints(urls: list[str], exclude_paths: list[str], include_hints: list[str] | None = None) -> list[str]:
    out = []
    for u in urls:
        if any(x in u for x in (exclude_paths or [])):
            continue
        out.append(u)
    return out


async def extract_outbound_links(page_url: str, html: str, hints: list[str]) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "lxml")
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        txt = a.get_text(" ", strip=True)[:120]
        if any(h.lower() in href.lower() for h in hints):
            found.append({"source_page": page_url, "href": href, "anchor": txt})
    return found


def parse_edge_logs(lines: list[str]) -> list[dict]:
    """Parse Cloudflare/Fastly-style edge log lines for outbound click events."""
    rx = re.compile(r'(GET|POST)\s+(\S*(?:/out/|/go/|/visit/)[^\s]*)')
    events = []
    for ln in lines:
        m = rx.search(ln)
        if m:
            events.append({"method": m.group(1), "path": m.group(2), "raw": ln[:300]})
    return events
