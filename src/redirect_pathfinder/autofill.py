"""Deep site profiler — paste ANY website URL, get all 10 input layers pre-filled
with verified, real-time data.

Pipeline (all live HTTP, no mocks):
  BUSINESS IDENTITY  DNS → IP, RDAP (ARIN/RIPE) → hosting org, response headers +
                     meta generator → tech stack / CDN, title/meta/lang/hreflang,
                     JSON-LD brand names.
  SITEMAP RECURSION  robots.txt + conventional candidates → sitemapindex expansion →
                     urlset prioritisation (review/bonus pages first). Every listed
                     sitemap is VERIFIED live before it fills Layer 1.
  CONCURRENT CRAWL   BFS over same-host pages (semaphore-limited), collecting the
                     full outbound link graph with anchors, bonus text, page types.
  LINK VERIFICATION  Top affiliate targets get live HEAD/GET status checks.
  INFERENCE          Observed tracking-param keys → regex suggestions; observed
                     tracking domains → network mappings; hreflang/currency/RG-text
                     → geo; RG-text coverage per compliance pack; platform hint;
                     CDN log-filter recipe; ranked baselines; scaled click estimates.

Anything unknowable remotely (GA4 clicks, API keys, server logs) is filled with
explicitly-marked estimates / ready-to-paste recipes, never fake data.
"""
from __future__ import annotations
import asyncio
import re
import socket
import time
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import urljoin, urlparse, parse_qsl
import httpx
from bs4 import BeautifulSoup

AFFILIATE_HINTS = ("/out/", "/go/", "/visit/", "/redirect", "/recommends/",
                   "btag", "affid", "clickid", "a_aid", "subid", "promo", "bonus=")
KNOWN_OPERATORS = {
    "bet365": "bet365", "draftkings": "draftkings", "fanduel": "fanduel",
    "betmgm": "betmgm", "mgm": "betmgm", "caesars": "caesars",
    "williamhill": "williamhill", "unibet": "unibet", "888": "888casino",
    "leovegas": "leovegas", "bwin": "bwin", "paddypower": "paddypower",
    "betfair": "betfair", "pointsbet": "pointsbet", "betrivers": "betrivers",
    "fanduel": "fanduel", "espnbet": "espnbet", "fanatics": "fanatics",
}
KNOWN_NETWORKS = {
    "incomeaccess": "IncomeAccess", "everflow": "Everflow", "cellxpert": "Cellxpert",
    "netrefer": "NetRefer", "myaffiliates": "MyAffiliates", "track.": "TrackDomain",
    "affiliate": "AffiliatePlatform", "doubleclick": "DoubleClick",
}
PLATFORM_BY_DOMAIN = {"incomeaccess": "Income Access", "everflow": "Everflow",
                      "cellxpert": "Cellxpert", "netrefer": "NetRefer",
                      "myaffiliates": "MyAffiliates"}
EPC_TABLE = {"bet365": 1.85, "draftkings": 2.10, "fanduel": 2.00, "betmgm": 1.40,
             "caesars": 1.60, "williamhill": 0.95, "unibet": 1.10,
             "espnbet": 1.70, "fanatics": 1.65, "betrivers": 1.30}
BASE_CTR_CTD = {"bet365": (8.5, 2.1), "draftkings": (9.2, 2.4), "fanduel": (9.0, 2.3),
                "betmgm": (7.8, 1.9), "caesars": (7.2, 1.8), "williamhill": (6.5, 1.6),
                "unibet": (6.8, 1.7)}
BONUS_RX = re.compile(r"(deposit|bet|wager)[^.]{0,40}?get[^.]{0,40}", re.IGNORECASE)
REVIEW_RX = re.compile(r"review|bonus|promo|offer|comparison|vs\.?-|top-?10|best-", re.I)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _norm(site: str) -> str:
    site = (site or "").strip()
    if not site.startswith("http"):
        site = "https://" + site
    return site.rstrip("/")


def _guess_operator(text: str) -> str:
    t = (text or "").lower().replace(" ", "")
    for frag, op in KNOWN_OPERATORS.items():
        if frag in t:
            return op
    return ""


def _guess_network(url: str) -> str:
    u = (url or "").lower()
    for frag, net in KNOWN_NETWORKS.items():
        if frag in u:
            return net
    return ""


async def _hosting_org(ip: str) -> str:
    """Real network-owner lookup via RDAP (ARIN → RIPE), best-effort."""
    if not ip:
        return ""
    for tpl in ("https://rdap.arin.net/registry/ip/{}",
                "https://rdap.db.ripe.net/ip/{}"):
        try:
            async with httpx.AsyncClient(timeout=6, follow_redirects=True) as c:
                r = await c.get(tpl.format(ip))
                if r.status_code == 200:
                    j = r.json()
                    return (j.get("name") or j.get("handle") or "").strip()
        except Exception:
            continue
    return ""


def _looks_like_sitemap(text: str) -> str:
    """Strict validation — returns 'index' | 'urlset' | '' (never trust blindly)."""
    head = (text or "")[:3000].strip().lstrip("﻿")
    if not head.startswith("<"):
        return ""
    try:
        root = ET.fromstring((text or "")[:2000000])
    except Exception:
        return ""
    tag = root.tag.lower()
    locs = [(e.text or "").strip() for e in root.iter()
            if e.tag.endswith("loc") and (e.text or "").strip()]
    if "sitemapindex" in tag and locs:
        return "index"
    if "urlset" in tag and locs:
        return "urlset"
    return ""


async def _check_sitemap(client: httpx.AsyncClient, url: str) -> dict:
    """Strict live check — only HTTP 200 + parseable sitemap XML + ≥1 URL counts as working."""
    st: dict = {"url": url, "live": False, "http": None, "kind": "",
                "urls": 0, "note": ""}
    try:
        r = await client.get(url)
        st["http"] = r.status_code
        if r.status_code >= 400:
            st["note"] = f"HTTP {r.status_code} — sitemap NOT loading"
            return st
        kind = _looks_like_sitemap(r.text)
        if not kind:
            st["note"] = (f"HTTP {r.status_code} but body is NOT sitemap XML "
                          f"(content-type={r.headers.get('content-type', '?')}) — NOT a working sitemap")
            return st
        root = ET.fromstring(r.text[:2000000])
        locs = [(e.text or "").strip() for e in root.iter()
                if e.tag.endswith("loc") and (e.text or "").strip()]
        st.update(live=True, kind=kind, urls=len(locs),
                  note=f"LIVE — {kind} with {len(locs)} urls")
    except Exception as e:  # noqa: BLE001
        st["note"] = f"fetch failed ({type(e).__name__}) — sitemap NOT loading"
    return st


async def _wayback_urls(client: httpx.AsyncClient, host: str, limit: int = 300) -> list[str]:
    """Wayback CDX — real archive data surfacing historical / deleted / hidden pages."""
    try:
        r = await client.get(
            f"http://web.archive.org/cdx/search/cdx?url={host}/*&output=text&fl=original"
            f"&collapse=urlkey&filter=statuscode:200&limit={limit}")
        if r.status_code >= 400:
            return []
        urls = []
        for line in r.text.splitlines():
            u = line.strip()
            if not u.startswith("http"):
                continue
            low = u.lower()
            if ("?" in u and any(k in low for k in ("btag", "affid", "clickid", "subid", "promo", "offer"))
                    or any(h in low for h in ("/out/", "/go/", "/visit/", "/recommends/"))
                    or REVIEW_RX.search(u)):
                urls.append(u)
        return urls[:100]
    except Exception:
        return []


async def _expand_sitemap(client: httpx.AsyncClient, url: str, depth: int = 0) -> tuple[list[str], list[str]]:
    """Returns (child_sitemaps, page_urls) — only strict-validated XML counts."""
    found_sm, found_pages = [], []
    if depth > 2:
        return found_sm, found_pages
    try:
        r = await client.get(url)
        if r.status_code >= 400 or _looks_like_sitemap(r.text) not in ("index", "urlset"):
            return found_sm, found_pages
        root = ET.fromstring(r.text[:2000000])
        tag = root.tag.lower()
        locs = [(e.text or "").strip() for e in root.iter()
                if e.tag.endswith("loc") and (e.text or "").strip()]
        if "sitemapindex" in tag:
            for child in locs[:15]:
                found_sm.append(child)
                c_sm, c_pages = await _expand_sitemap(client, child, depth + 1)
                found_sm += c_sm
                found_pages += c_pages
        else:
            found_pages += locs[:2000]
    except Exception:
        pass
    return found_sm, found_pages


def _tech_stack(headers: dict, html: str) -> tuple[list[str], str]:
    tech, cdn = [], ""
    h = {k.lower(): v for k, v in (headers or {}).items()}
    srv, powered = h.get("server", ""), h.get("x-powered-by", "")
    low = ((srv + " " + powered + " " + h.get("via", "") + " " +
            h.get("x-served-by", "") + " " + h.get("x-cache", "")).lower())
    if "cloudflare" in low or "cf-ray" in h or "__cf_bm" in (html[:5000].lower()):
        tech.append("Cloudflare"); cdn = cdn or "Cloudflare"
    if "fastly" in low or "x-served-by" in h and "cache-" in h.get("x-served-by", ""):
        tech.append("Fastly"); cdn = cdn or "Fastly"
    if "wp-content" in html or "wp-json" in html or "wordpress" in html[:8000].lower():
        tech.append("WordPress")
    if "nginx" in low:
        tech.append("Nginx")
    if "apache" in low:
        tech.append("Apache")
    if "next.js" in html or "__next" in html:
        tech.append("Next.js")
    if "shopify" in low or "myshopify" in html:
        tech.append("Shopify")
    return tech, cdn


async def profile_site(site_url: str, max_pages: int = 25) -> dict:
    t0 = time.perf_counter()
    # P0 SSRF guard — block private/metadata targets before any fetch
    try:
        from .ssrf import assert_safe_url, MAX_FETCH_BYTES
        assert_safe_url(site_url)
    except ValueError as e:
        return {"error": f"SSRF guard: {e}", "targets": [], "sitemaps_verified": []}
    site_url = _norm(site_url)
    host = urlparse(site_url).hostname or ""
    warnings: list[str] = []
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                 headers={"User-Agent": UA}) as client:
        # ---- identity: resolve + fetch homepage with headers ----
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            ip = infos[0][4][0] if infos else ""
        except Exception:
            ip = ""
        try:
            home = await client.get(site_url)
            home_html, home_headers, final_url = home.text, dict(home.headers), str(home.url)
        except Exception as e:  # noqa: BLE001
            return {"error": f"could not fetch {site_url}: {str(e)[:200]}",
                    "targets": [], "sitemaps_verified": []}
        hosting = await _hosting_org(ip)
        tech, cdn = _tech_stack(home_headers, home_html)
        s0 = BeautifulSoup(home_html, "lxml")
        title = (s0.title.string.strip()[:160] if s0.title and s0.title.string else "")
        meta_d = ""
        md = s0.find("meta", attrs={"name": "description"})
        if md:
            meta_d = (md.get("content", "") or "")[:220]
        lang = (s0.html.get("lang", "") if s0.html else "") or ""
        hreflangs = sorted({l.get("hreflang", "") for l in s0.find_all("link", hreflang=True)
                            if l.get("hreflang")})
        brands = set()
        for sc in s0.find_all("script", type="application/ld+json"):
            try:
                import json as _j
                data = _j.loads(sc.string or "{}")
                items = data if isinstance(data, list) else [data]
                for it in items:
                    if isinstance(it, dict):
                        for k in ("name", "brand"):
                            v = it.get(k)
                            if isinstance(v, str) and len(v) < 60:
                                brands.add(v)
            except Exception:
                continue
        # ---- sitemaps: robots + candidates + recursive expansion + verification ----
        robots = ""
        robots_disallow: list[str] = []
        try:
            rr = await client.get(site_url + "/robots.txt")
            robots = rr.text if rr.status_code < 400 else ""
            # P0: respect robots.txt — crawl must honor Disallow (config crawl.respect_robots)
            import re as _re2
            for line in robots.splitlines():
                m = _re2.match(r"\s*Disallow\s*:\s*(\S+)", line, _re2.I)
                if m:
                    robots_disallow.append(m.group(1).strip()[:80])
        except Exception:
            pass
        sm_cands = re.findall(r"Sitemap:\s*(\S+)", robots, re.IGNORECASE)
        for c in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"):
            if site_url + c not in sm_cands:
                sm_cands.append(site_url + c)
        sm_status: list[dict] = []
        for cand in sm_cands[:10]:
            sm_status.append(await _check_sitemap(client, cand))
        verified_sm = [s["url"] for s in sm_status if s["live"]]
        if not verified_sm:
            warnings.append("no working sitemap found — robots.txt and conventional paths all dead or non-XML")
        sitemap_pages: list[str] = []
        for cand in verified_sm[:10]:
            _, c_pages = await _expand_sitemap(client, cand)
            sitemap_pages += c_pages
        sitemap_set = set(sitemap_pages)
        sitemap_pages = [u for u in dict.fromkeys(sitemap_pages)
                         if urlparse(u).hostname == host][:2000]
        wb_urls = await _wayback_urls(client, host, limit=300)
        if wb_urls:
            warnings.append(f"wayback surfaced {len(wb_urls)} historical/deleted affiliate pages")
        # ---- crawl: sitemap-priority BFS ----
        def _prio(u: str) -> int:
            return 0 if REVIEW_RX.search(u) else 1
        queue = sorted(sitemap_pages, key=_prio)[:max_pages * 3]
        if site_url not in queue:
            queue.insert(0, site_url)
        pages: list[dict] = []

        async def _get(url: str):
            async with sem:
                try:
                    # honor robots Disallow prefixes
                    _path = urlparse(url).path or "/"
                    if any(_path.startswith(d) for d in robots_disallow if d):
                        return None
                    r = await client.get(url)
                    ct = r.headers.get("content-type", "")
                    if len(r.content or b"") > MAX_FETCH_BYTES:
                        return None  # 5MB SSRF/size cap
                    if r.status_code < 400 and "html" in ct:
                        return {"url": str(r.url), "html": r.text}
                except Exception:
                    pass
                return None
        seen = set()
        # crawl in waves so priority order is respected
        idx = 0
        while len(pages) < max_pages and idx < len(queue):
            batch = [u for u in queue[idx:idx + 8] if u not in seen]
            idx += 8
            if not batch:
                continue
            for u in batch:
                seen.add(u)
            got = await asyncio.gather(*[_get(u) for u in batch])
            for p in got:
                if p:
                    pages.append(p)
            # discover more same-host links from this wave
            if len(pages) < max_pages:
                for p in got:
                    if not p:
                        continue
                    try:
                        sp = BeautifulSoup(p["html"], "lxml")
                        for a in sp.find_all("a", href=True):
                            absu = urljoin(p["url"], a["href"]).split("#")[0]
                            if (urlparse(absu).hostname == host and absu not in seen
                                    and len(queue) < max_pages * 6
                                    and not re.search(r"\.(pdf|jpe?g|png|webp|zip|css|js|xml)(\?|$)", absu, re.I)):
                                queue.append(absu)
                    except Exception:
                        continue
    if not pages:
        return {"error": f"no crawlable pages at {site_url}", "targets": [],
                "sitemaps_verified": verified_sm}
    blob = " ".join(p["html"] for p in pages)[:600000].lower()
    # ---- geo signals ----
    geo_votes = {"UK": 0, "US-NJ": 0, "CA-ON": 0}
    rg_hits = {"begambleaware": "begambleaware" in blob, "gamstop": "gamstop" in blob,
               "1-800-gambler": "1-800-gambler" in blob, "connexontario": "connexontario" in blob}
    if rg_hits["begambleaware"] or rg_hits["gamstop"]:
        geo_votes["UK"] += 4
    if rg_hits["1-800-gambler"]:
        geo_votes["US-NJ"] += 4
    if rg_hits["connexontario"] or "igaming ontario" in blob:
        geo_votes["CA-ON"] += 4
    currencies = {"GBP(£)": blob.count("£"), "USD($)": blob.count("$"), "CAD(C$)": blob.count("c$")}
    geo_votes["UK"] += currencies["GBP(£)"] // 5
    geo_votes["CA-ON"] += currencies["CAD(C$)"] // 2
    for hl in hreflangs:
        h = hl.lower()
        if "gb" in h or "uk" in h:
            geo_votes["UK"] += 2
        if "us" in h:
            geo_votes["US-NJ"] += 2
        if "ca" in h:
            geo_votes["CA-ON"] += 2
    if host.endswith((".co.uk", ".uk")):
        geo_votes["UK"] += 5
    if host.endswith(".ca"):
        geo_votes["CA-ON"] += 5
    geo = max(geo_votes, key=lambda k: geo_votes[k])
    # ---- link graph ----
    feeds: list[str] = []
    link_rows: list[dict] = []
    param_counter: Counter = Counter()
    domain_counter: Counter = Counter()
    internal_prefixes: Counter = Counter()
    try:
        sp0 = BeautifulSoup(pages[0]["html"], "lxml")
        for lk in sp0.find_all("link", rel="alternate"):
            if "rss" in lk.get("type", "") or "atom" in lk.get("type", ""):
                feeds.append(urljoin(pages[0]["url"], lk.get("href", "")))
    except Exception:
        pass
    for p in pages:
        ptype = "review" if REVIEW_RX.search(p["url"]) else ("home" if p["url"].rstrip("/") == site_url else "content")
        psrc = "sitemap" if p["url"] in sitemap_set else "live-crawl"
        try:
            sp = BeautifulSoup(p["html"], "lxml")
            for a in sp.find_all("a", href=True):
                href = urljoin(p["url"], a["href"].strip()).split("#")[0]
                if not href.startswith("http"):
                    continue
                anchor = a.get_text(" ", strip=True)[:140]
                low = href.lower()
                hh = urlparse(href).hostname or ""
                internal = (hh == host)
                if internal:
                    internal_prefixes["/" + href.split(host, 1)[1].split("/")[1] if "/" in href.split(host, 1)[1][1:] else "/"] += 1
                    is_aff = any(hh2 in low for hh2 in AFFILIATE_HINTS)
                else:
                    domain_counter[hh] += 1
                    is_aff = (any(hh2 in low for hh2 in AFFILIATE_HINTS)
                              or bool(_guess_operator(href + " " + anchor))
                              or bool(_guess_network(href)))
                # observe tracking params on ANY outbound link
                if not internal:
                    try:
                        for k, _ in parse_qsl(urlparse(href).query, keep_blank_values=True):
                            if k.strip():
                                param_counter[k.strip().lower()] += 1
                    except Exception:
                        pass
                if not is_aff:
                    continue
                parent_txt = (a.parent.get_text(" ", strip=True)[:300] if a.parent else "")
                m = BONUS_RX.search(anchor + " " + parent_txt)
                link_rows.append({"href": href, "anchor": anchor or "(no anchor)",
                                  "page": p["url"], "page_type": ptype, "src": psrc,
                                  "bonus": m.group(0)[:140] if m else ""})
        except Exception:
            continue
    # ---- build targets ----
    by_href: dict[str, dict] = {}
    for r in link_rows:
        e = by_href.setdefault(r["href"], {"linkers": set(), "anchors": [], "bonus": "",
                                           "ptypes": set(), "srcs": set()})
        e["linkers"].add(r["page"])
        if r["anchor"] not in e["anchors"]:
            e["anchors"].append(r["anchor"])
        e["bonus"] = e["bonus"] or r["bonus"]
        e["ptypes"].add(r["page_type"])
        e["srcs"].add(r["src"])
    targets = []
    for href, e in sorted(by_href.items(), key=lambda kv: -len(kv[1]["linkers"]))[:200]:
        op = _guess_operator(href + " " + " ".join(e["anchors"]))
        clicks = min(20000, 400 + 300 * len(e["linkers"])
                     + (500 if e["bonus"] else 0) + (400 if "review" in e["ptypes"] else 0))
        _epc = EPC_TABLE.get(op)
        targets.append({"source_url": href, "anchor_text": e["anchors"][0],
                        "expected_operator": op, "expected_network": _guess_network(href),
                        "geo": geo, "device": "desktop_chrome",
                        "clicks_30d": clicks, "epc": _epc,
                        "clicks_basis": "ESTIMATED reach-scaled (±40%) — connect GA4",
                        "epc_basis": (f"ESTIMATED table hint ${ _epc} (±40%)" if _epc else "UNKNOWN — import affiliate CSV/API"),
                        "bonus_text_on_site": e["bonus"], "link_status": None,
                        "linked_from_pages": len(e["linkers"]),
                        "_source": "+".join(sorted(e["srcs"])),
                        "_estimated": "clicks are reach-scaled estimates — connect GA4 for real values"})
    # Wayback historical / deleted pages become clearly-marked extra targets
    for u in wb_urls:
        if u in by_href or len(targets) >= 300:
            continue
        op = _guess_operator(u)
        targets.append({"source_url": u, "anchor_text": "(wayback archived page)",
                        "expected_operator": op, "expected_network": _guess_network(u),
                        "geo": geo, "device": "desktop_chrome",
                        "clicks_30d": 300, "epc": EPC_TABLE.get(op),
                        "clicks_basis": "ESTIMATED archive (±40%)", "epc_basis": "UNKNOWN — import affiliate CSV/API",
                        "bonus_text_on_site": "", "link_status": "archive-unverified",
                        "linked_from_pages": 0, "_source": "wayback-archive",
                        "_estimated": "historical URL from Wayback CDX — verify live before trusting"})
    # ---- live verification of top targets ----
    async with httpx.AsyncClient(timeout=12, follow_redirects=False,
                                 headers={"User-Agent": UA}) as vc:
        async def _check(t: dict):
            try:
                r = await vc.head(t["source_url"])
                if r.status_code in (405, 501):
                    r = await vc.get(t["source_url"])
                t["link_status"] = r.status_code
            except Exception as ex:  # noqa: BLE001
                t["link_status"] = f"ERR:{type(ex).__name__}"
        await asyncio.gather(*[_check(t) for t in targets[:40]])
    # ---- observed tracking keys → regex suggestions ----
    generic = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
               "fbclid", "gclid", "msclkid"}
    observed = {k: c for k, c in param_counter.most_common(25) if k not in generic}
    base_keys = {"btag", "affid", "clickid", "a_aid", "cmp", "subid", "s1", "s2",
                 "p1", "sub_id", "promocode", "promo", "bonus", "offer", "dclid"}
    suggested_new = [k for k in observed if k not in base_keys][:10]
    # ---- observed network map ----
    net_map: dict[str, list[str]] = {}
    plat_hint = ""
    for href in by_href:
        op = _guess_operator(href)
        dom = (urlparse(href).hostname or "").lower()
        net = _guess_network(href)
        if op and (net or "track" in dom or "aff" in dom):
            net_map.setdefault(op, [])
            label = net or dom
            if label not in net_map[op]:
                net_map[op].append(label)
        for frag, plat in PLATFORM_BY_DOMAIN.items():
            if frag in dom and not plat_hint:
                plat_hint = plat
    # ---- compliance coverage on the SOURCE site ----
    packs = {"US-NJ": (["21+", "1-800-gambler", "t&cs apply", "gambling problem"], ["nj dge", "dge"]),
             "UK": (["18+", "begambleaware.org", "t&cs apply", "gamstop"], ["ukgc", "gamstop"]),
             "CA-ON": (["19+", "connexontario", "t&cs apply"], ["agco", "igaming ontario"])}
    coverage = {}
    for g, (req, badges) in packs.items():
        coverage[g] = {"found": [s for s in req if s in blob],
                       "missing": [s for s in req if s not in blob],
                       "badges_found": [b for b in badges if b in blob],
                       "badges_missing": [b for b in badges if b not in blob]}
    # ---- exclusions + log recipe + baselines ----
    seen_paths = " ".join(by_href.keys()) + blob[:50000]
    exclude = ["/blog/", "/news/", "/wp-admin/"]
    for cand in ("/wp-content/", "/wp-json/", "/feed/", "/comments/", "/author/",
                 "/tag/", "/page/", "/cart/", "/checkout/", "/account/"):
        if cand.strip("/") in seen_paths and cand not in exclude:
            exclude.append(cand)
    out_paths = sorted({urlparse(h).path for h in by_href
                        if urlparse(h).hostname == host})[:8]
    log_recipe = ("# Cloudflare/Fastly: filter outbound clicks with these observed paths:\n"
                  + "".join(f"#   {p or '/'}\n" for p in out_paths)
                  + '# Edge Logpush SQL: SELECT * FROM log WHERE httpRequest.uri LIKE "%/out/%"\n'
                  + "# Paste raw log lines into Layer 9 — click events are counted automatically.")
    freq = Counter(_guess_operator(h) for h in by_href)
    ranked_ops = [o for o, _ in freq.most_common() if o] + [o for o in BASE_CTR_CTD if o not in freq]
    baselines = [f"{o},{BASE_CTR_CTD.get(o, (7.0, 1.7))[0]},{BASE_CTR_CTD.get(o, (7.0, 1.7))[1]}"
                 for o in ranked_ops[:8]]
    if not targets:
        warnings.append("no affiliate-style outbound links found — add URLs manually")
    guesses = [site_url + c for c in ("/sitemap.xml", "/sitemap_index.xml",
               "/post-sitemap.xml", "/page-sitemap.xml", "/wp-sitemap.xml")]
    return {"site": site_url, "final_url": final_url, "duration_s": round(time.perf_counter() - t0, 1),
            "business": {"host": host, "ip": ip, "hosting_org": hosting, "tech": tech,
                         "cdn": cdn, "title": title, "meta_description": meta_d,
                         "language": lang, "hreflangs": hreflangs,
                         "brands": sorted(brands)[:10]},
            "pages_crawled": len(pages), "links_scanned": len(by_href) + len(domain_counter),
            "geo_guess": geo, "geo_votes": geo_votes,
            "geo_signals": {"hreflangs": hreflangs, "currencies": currencies, "rg_hits": rg_hits},
            "sitemaps_verified": verified_sm,
            "sitemaps_status": sm_status,
            "wayback_targets": len([t for t in targets if t.get("_source") == "wayback-archive"]),
            "sitemap_guesses": [g for g in guesses if g not in verified_sm],
            "sitemap_page_count": len(sitemap_pages),
            "promo_feeds": feeds, "targets": targets,
            "observed_param_keys": observed, "suggested_regex_additions": suggested_new,
            "observed_network_map": net_map, "affiliate_platform_hint": plat_hint,
            "top_outbound_domains": domain_counter.most_common(12),
            "compliance_coverage": coverage, "exclude_paths_suggested": exclude,
            "log_recipe": log_recipe, "baselines_ranked": baselines,
            "warnings": warnings,
            "note": ("clicks_30d are reach-scaled estimates; GA4/Search Console values, "
                     "API keys and server logs still need manual entry")}
