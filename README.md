# Outbound Affiliate Link Chain & Latency Tracker

**Enterprise revenue-guarding engine for iGaming affiliate redirect chains — live multi-hop tracing, param-loss fingerprinting, latency profiling, compliance guardianship and edge auto-healing.**

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-REST%20%2B%20Web%20App%20%2B%20MCP-green) ![Tests](https://img.shields.io/badge/pytest-22%20passing-brightgreen) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

**🚀 Live tool: https://outbound-affiliate-link-chain-latency.onrender.com/** — open it, paste URLs, run the audit. No install needed.

> **v3.2.0 enterprise-hardened (this commit):** TLS impersonation is now the PRIMARY per-hop transport (curl-cffi Chrome JA3/H2, pooled-httpx fallback, `per_hop_via` flag on every hop) · per-hop alt-svc capture feeds real `http3_advertised` (no more false negatives) · scoped API-key auth (`audit`/`mcp`/`export`/`admin` via `key:scopes:workspace`) + 60/min per-key rate limit + 2 MB body cap on all server-fetch endpoints · SSRF `assert_safe_url` on every source/sitemap/pasted URL · rule engine unknown-state (`params_intact=None`, mid-chain injection detection, duplicate-preserving params) · edge patches require explicit `fallback_url` (no `example.com` default, JS-escaped, subid/clickid passthrough) · TCF 2.3 parser (2.2 flagged deprecated) · canonical normalization + 9-geo hreflang map + source-vs-final `rel=sponsored` audit · SOV live on all 4 providers (OpenAI/Anthropic/Perplexity/Gemini) with cost caps + 7-day cache + `perplexity-only` partial label · GSC 25k pagination + quota backoff + GA4↔GSC join · HMAC-signed webhooks with correlation IDs · OTel spans on trace/audit fan-out · explicit Playwright fork rule recorded per chain · CrUX history + LCP-budget gate · workspace cost caps + Docker-secrets helper · idempotent runs + 90-run retention + `DELETE /monitor/runs/{id}`. Carried over from v3.1.0:SSRF guard on all server-side fetches (private/link-local/169.254 blocked + 5MB cap) · auth-required SSE with Last-Event-ID resume + persistent job store (Redis → SQLite, survives restart) · schedules in DB (no YAML race) · `tcp_tls` canonical single formula · UA rotated to Chrome 132 / Safari 18.4 · MCP server (`POST /mcp` + `/.well-known/mcp.json`: trace_chain/param_audit/sla_matrix/verify_inputs/ai_visibility/crux_lookup) · live affiliate APIs (Everflow/Cellxpert/IA/NetRefer/MyAff + S2S postback validator with sub1-10/adv1-10) · live GA4 Data API + GSC Search Analytics (CSV = fallback) · prompt-level Share-of-Voice (`POST /audit/sov`) · CrUX history + PSI INP attribution + web-vitals RUM snippet (CrUX/PSI on by default, honest unavailable without keys) · canonical/hreflang/rel=sponsored + HTTP/3 signal audits · Consent Mode v2 + TCF 2.3 parsing · E-E-A-T/dropoff as bands (no fake-precision scores) · F11 corrected per Google May 2026 (llms.txt = agent infra, NOT citation factor) · versioned compliance feed · `/metrics` (Prometheus) + OTel/Sentry hooks · workspaces + key scopes · deep-trace sampling on large bulks. Quickstart with Docker: `docker compose up --build` → http://localhost:8099/ (the tool lives at the domain root; `/app/` kept as an alias. Set `PATHFINDER_API_KEYS` to require API keys).

![Tool hero — inputs page with site auto-fill](docs/screenshots/01-hero-autofill.png)

> All 27 screenshots in this README are **light-mode captures from a live analysis of https://www.igamingontario.ca/en** — 18 targets auto-discovered, geo correctly inferred as CA-ON, 5 dead links caught, 28 chains audited.

In iGaming, an affiliate link is never a straight line. One click on an offer button routes through publisher cloaking plugins (`/out/bet365`), analytics handlers, affiliate ad servers (Income Access, Cellxpert, Everflow, NetRefer), geo/compliance routers and operator attribution — and when any hop silently breaks, strips an ID, slows down or lands on the wrong jurisdiction, the publisher's site stays up while the revenue pipeline quietly dies.

This tool continuously **crawls, executes, simulates and audits multi-hop affiliate redirect chains at scale under hyper-realistic browsing conditions** — catching broken links, dropped tracking parameters, latency bottlenecks and regulatory violations before they burn commission revenue. Every number it shows comes from **live HTTP measurements taken during your run**, with evidence counters to prove it. Anything it cannot verify remotely (private GA4 clicks, EPC deals) is **explicitly labelled ESTIMATED, never faked**.

---

## Table of contents

1. [3-page workflow](#1--3-page-workflow)
2. [Layer 0 — auto-fill any website](#2--layer-0--auto-fill-any-website-url)
3. [The 10 input layers in 3 steps](#3--the-10-input-layers-in-3-steps)
4. [Input verification engine](#4--input-verification-engine)
5. [Live log + timer](#5--live-log--timer)
6. [The 12 engines (F1–F11 + monitoring)](#6--the-12-engines-f1f11--monitoring)
7. [The 9 outputs (O1–O9)](#7--the-9-outputs-o1o9)
8. [Documentation pages (auto-updating)](#8--documentation-pages-auto-updating)
9. [REST API](#9--rest-api)
10. [Quickstart](#10--quickstart)
11. [Architecture & file map](#11--architecture--file-map)
12. [Configuration reference](#12--configuration-reference)
13. [Verification & honesty model](#13--verification--honesty-model)
14. [Test report](#14--test-report)
15. [Business impact](#15--business-impact)
16. [Roadmap](#16--roadmap)
17. [Contributing & license](#17--contributing--license)

---

## 1 · 3-page workflow

| Page | Purpose |
|------|---------|
| **Page 1 — Inputs** (`/`) | 3 steps (same 10 layers, Advanced collapsible) + site auto-fill + input verifier + Run with live SSE log |
| **Page 2 — Analysis** (`/analysis.html`) | Full F1–F11 feature analysis + severity scoring for every chain |
| **Page 3 — Outputs** (`/outputs.html`) | All 9 executive/engineering/legal outputs + real PDF/CSV/JSON downloads |

Plus 6 auto-updating documentation pages (`/info.html?p=about|why2026|features|inputs|outputs|impact`) and light/dark mode on every page:

![Light mode](docs/screenshots/12-light-mode.png)

---

## 2 · Layer 0 — auto-fill any website URL

Paste **any** business/publisher URL and the deep profiler researches it live, then fills layers 1–10:

![Auto-fill results](docs/screenshots/02-autofill-results.png)

What the profiler actually does over live HTTP (no mocks):

- **Business identity** — DNS → IP, RDAP (ARIN/RIPE) → hosting org, response headers + meta generator → tech stack / CDN, title/meta/lang/hreflang, JSON-LD brands
- **Strict sitemap verification** — robots.txt + conventional candidates, recursive sitemap-index expansion, review-page prioritisation. Only HTTP-200 + parseable XML with ≥1 URL counts as working; dead ones are labelled `# ❌ DEAD [HTTP 404]` right in the field
- **Concurrent crawl** — same-host BFS (up to 500 pages), full outbound link graph with anchors, bonus text and page-type classification
- **Wayback CDX discovery** — historical / deleted / hidden affiliate pages surfaced as clearly-marked archive targets
- **Live link checks** — top targets verified with real HEAD/GET status
- **Tracking-key mining** — observed query-param frequencies → regex suggestions appended to Layer 2; observed tracking domains → network mappings merged
- **Geo inference** — hreflang + currencies + RG-text signals + TLD voting
- **Compliance coverage** — which required strings the source site itself already carries per geo pack
- **Ranked baselines, platform hint, CDN log-filter recipe, exclusion suggestions**

---

## 3 · The 10 input layers in 3 steps

### Layer 1 — Site crawl & target discovery

![Targets table](docs/screenshots/03-layer1-targets.png)

Sitemaps (verified live, dead ones marked ❌ and skipped at runtime), pasted URL lists and bulk CSV (source, anchor, operator, network, geo, device, clicks, EPC, bonus) merge into one queue. Crawl constraints: desktop/mobile user-agents, concurrency, depth, path exclusions.

### Layer 2 — Affiliate tracking & parameter rule engine

![Tracking rules](docs/screenshots/04-layer2-rules.png)

Primary-ID / sub-ID / promo regexes that must survive every hop, plus operator → tracking-domain mappings (auto-mined from observed domains during auto-fill). Silent stripping and key mutation are fingerprinted per hop on Page 2.

### Layer 3 — Simulation & execution environment

![Execution environment](docs/screenshots/05-layer3-environment.png)

Licence-jurisdiction geo nodes (US-NJ/PA/MI/OH/MA, UK, CA-ON, DE, NL), device profiles (desktop Chrome 132/Edge, Safari 18.4, iOS 18.4, Android 15), HTTP-only → hybrid execution with Playwright escalation on bot-walls, per-geo `proxy_exit` for real exit IPs (Bright Data/Oxylabs; empty = direct + explicit unpinned warning).

### Layer 4 — Regulatory & compliance rules

![Compliance packs](docs/screenshots/06-layer4-compliance.png)

Per-geo required strings (21+, BeGambleAware.org, 1-800-GAMBLER…), licence badges (UKGC, NJ DGE, AGCO, GGL, KSA, PGCB, MGCB…), age-gate detection, soft-404 / expired-offer phrase lists — all scraped live from final landers.

### Layer 5 — Business data & revenue context

![Revenue context](docs/screenshots/07-layer5-revenue.png)

Blank EPC/clicks = **UNKNOWN $0** (no default $1.25). Per-target overrides or CSV/API imports flip rows to **ESTIMATED ±40%** (table hints) or **VERIFIED ±15%** (GA4 + affiliate exports via `/utils/traffic-import`, `/utils/affiliate-import`) — see [honesty model](#13--verification--honesty-model).

### Layer 6 — Affiliate program API credentials

![API credentials](docs/screenshots/08-layer6-credentials.png)

Session-only credential slots for Cellxpert, Income Access, NetRefer, MyAffiliates, Everflow — sent with the run for live campaign sync where supported, never written to disk.

### ③ Advanced (collapsible) — Layers 7–10: ad-block feeds, promo feeds, edge logs, baselines

![Feeds](docs/screenshots/09-layer8-feeds.png)
![Baselines](docs/screenshots/10-layer10-baselines.png)

EasyList fetched live weekly (cached, never a stale hardcoded list) + Safari-ITP param-strip simulation · operator promo feed URLs (RSS auto-discovered) · Cloudflare/Fastly log lines counted for `/out/` `/go/` click events (plus an auto-generated filter recipe) · per-operator click-to-register / click-to-deposit baselines ranked by observed frequency.

---

## 4 · Input verification engine

One click verifies **every** field live before the run — sitemap liveness, URL reachability, regex compilability, mapping parseability, feed health, baseline formats — and rewrites the sitemap field with ✅/❌ annotations:

![Verify results](docs/screenshots/11-verify-results.png)

---

## 5 · Live log + timer

Every run streams a timestamped engine log with a live timer, progress bar and per-chain OK/FAIL lines — including deep cross-device re-traces:

![Live log](docs/screenshots/13-live-log-timer.png)

---

## 6 · The 12 engines (F1–F11 + monitoring)

### F1 · Multi-hop tracing (HTTP + headless)

![F1 chain map](docs/screenshots/14-analysis-f1-tracing.png)

301/302/303/307/308/200/404/500 per hop with IPs, ASN/org (RDAP), servers, DoH-DNS/TTFB/download splits plus honestly-labelled `tcp_tls_est_ms` (subtraction formula published, never socket-faked), JS/meta-refresh detection, 0–100 bot-wall scoring (Cloudflare/Datadome/Akamai/PX), Playwright escalation + screenshot evidence — plus lander forensics with E-E-A-T signals, cross-device parity and evidence counters per chain. Transport: **curl-cffi TLS impersonation (Chrome JA3/H2) is the primary per-hop fetch** with pooled-httpx fallback; the actual transport is recorded per hop (`timing_meta.per_hop_via`). Every hop captures raw `alt-svc`/`server` headers, so `http3_advertised` reflects measured response headers instead of a hardcoded false negative.

### F2 · Parameter-loss fingerprinting

![F2 params](docs/screenshots/15-analysis-f2-params.png)

Survived / stripped / mutated verdicts per affiliate ID with the exact hop where it vanished, plus operator → network routing checks. Clean links with zero tracked keys report `params_intact: null` (**unknown**, never FAIL), mid-chain-injected IDs are flagged `injected-mid-chain` (informational), and duplicate query keys are preserved hop-to-hop (fragments included; cookies/S2S are out-of-band by design).

### F3 · Geo-fenced latency profiler

![F3 latency](docs/screenshots/16-analysis-f3-latency.png)

Per-hop timing splits, ESTIMATED drop-off curve (formula published on every score) vs the 1800 ms threshold, redirect-budget note. Redirect-ms is never labelled CWV — page LCP/INP/CLS come only from CrUX/PSI (`GET /utils/crux`, `POST /utils/psi`).

### F4 · Compliance & license guardian

![F4 compliance](docs/screenshots/17-analysis-f4-compliance.png)

Required-string/badges checklists per lander, soft-404 NLP hits, geo-mismatch routing verdicts.

### F5 · Bot-mitigation bypass · F6 · Edge auto-healing

Fingerprint-spoofed headers, proxy rotation and headless escalation status per chain (the explicit render-fork rule — `http_only` never escalates, `hybrid` escalates on bot-wall/consent-wall/empty-lander — is recorded in `evidence.render_fork`); every broken chain gets copy-paste **Cloudflare Worker, Vercel Edge and WordPress** reroute patches. Patches require an explicit `fallback_url` (no shipped default), are JS-escaped, and forward `subid`/`clickid`/`btag` passthrough:

![F6 edge patches](docs/screenshots/18-analysis-f6-edge.png)

### F7–F10 · Privacy, deep-links, brand AI, offer parity

Live-EasyList ad-block simulation per chain · mobile app-scheme/store/universal-link verdicts · expected-vs-rival brand alignment · on-site vs lander bonus comparison · thin-affiliate + merchant-copy similarity + site-reputation-abuse pre-flight (SpamBrain 2026) · Consent Mode v2 + **TCF 2.3** (2.2 strings flagged deprecated per the Mar 1 2026 mandate):

### F11 · GEO/AEO AI-visibility + scheduled monitoring

`POST /audit/ai-visibility` (robots AI-bot allow, JSON-LD extractability aid, fact-density **band**, readability chunking — all as infra diagnostics) · `POST /audit/sov` (live prompt tests on **all 4 providers** — OpenAI/Anthropic/Perplexity/Gemini — with per-provider caps, 7-day cache and `perplexity-only` partial labelling = the only real Share-of-Voice) · APScheduler + SQLite runs with new-broken/fixed/health-delta diffs (`GET /monitor/runs`, `GET /monitor/diff`, `DELETE /monitor/runs/{id}` + enforced 90-run retention) · HMAC-signed Slack/Teams webhooks with correlation IDs that actually POST · Jira/Linear push (`POST /export/jira`):

> **2026 correction (Google AI Search Guide May 15 2026):** llms.txt is agent-readable infra for Cursor/Claude Code/MCP, **NOT** a Google citation factor. Chunking is a readability diagnostic, not a ranking predictor. This tool reports bands + confidence intervals, never invented citation probabilities.

![F10 offers](docs/screenshots/19-analysis-f10-offers.png)

---

## 7 · The 9 outputs (O1–O9)

### O1 · Executive revenue risk dashboard

![Revenue dashboard](docs/screenshots/20-outputs-dashboard.png)

Dollars at risk as a **range (low–high)** with formula + per-chain basis (UNKNOWN $0 / ESTIMATED ±40% / VERIFIED ±15%), 0–100 health index plus critical/high/medium/low/info severity scoring.

### O2 · Engineering action center

![Action center](docs/screenshots/21-outputs-action-center.png)

Interactive hop maps, failure points, Jira/Linear push + markdown tickets, CSV/JSON downloads — and a **real server-generated PDF** (ReportLab bytes, not print-to-PDF) with revenue ranges and AI-visibility/CWV honesty sections.

### O3 · Latency (redirect-chain) + honest page CWV

![Latency report](docs/screenshots/22-outputs-latency-cwv.png)

Slowest-to-fastest network scorecard plus redirect-overhead note. Page CWV is **only** shown from Google (CrUX/PSI) — redirect-ms is never mislabelled LCP/INP/CLS.

### O4–O9 · Compliance, alerts, patches, scorecards, SLA, vendor tickets

![Compliance](docs/screenshots/23-outputs-compliance.png)
![Vendor tickets](docs/screenshots/24-outputs-vendor.png)

Violation + geo-mismatch logs · Slack/Teams payloads that actually POST (delivery receipts) + monitoring diffs · copy-paste edge rules · ad-block vulnerability % · per-network SLA matrix with revenue ranges · operator-ready escalation tickets with hop evidence + screenshot proof.

---

## 8 · Documentation pages (auto-updating)

![Docs](docs/screenshots/25-docs-features.png)
![Impact docs](docs/screenshots/26-docs-impact.png)

About · Why-2026 · Features · Inputs · Outputs · Impact render live from a single source (`src/redirect_pathfinder/site_content.py` via `GET /content`) — update the tool, the docs update themselves.

---

## 9 · REST API

![Swagger](docs/screenshots/27-api-swagger.png)

Interactive docs at `GET /docs`. Key endpoints:

| Endpoint | Purpose |
|----------|---------|
| `POST /audit/job` + `GET /audit/job/{id}` (auth) + `GET /audit/job/{id}/stream` (auth SSE, Last-Event-ID resume, keepalives, 60-min window) | Async run with persistent log (Redis → SQLite, survives restart) |
| Auth model | Scoped keys (`key:scopes:workspace`, scopes `audit`/`mcp`/`export`/`admin`), 60/min per-key rate limit (429), 2 MB body cap (413), open dev mode when no keys configured |
| `POST /mcp` + `GET /.well-known/mcp.json` | MCP server for Claude Code / Cursor (trace_chain, param_audit, sla_matrix, verify_inputs, ai_visibility, crux_lookup) |
| `POST /audit/sov` | Prompt-level Share-of-Voice across ChatGPT/Claude/Perplexity/Gemini (real GEO — HTML scores can't predict citations; capped + cached, partial-labelled) |
| `POST /utils/postback-validate` | S2S postback check (txid/clickid + payout, Everflow sub1-10/adv1-10) |
| `GET /utils/affiliate-live?platform=` + `GET /utils/traffic-live` | Live affiliate / GA4+GSC pulls (PRIMARY — CSV imports are fallback) |
| `GET /metrics` | Prometheus metrics + OTel/Sentry hooks |
| `POST /audit/schedule` + `GET /audit/schedule` (DB-backed) + `GET /monitor/runs` + `GET /monitor/diff` + `DELETE /monitor/runs/{id}` | Recurring monitoring + diffs + retention-managed runs |
| `POST /audit/full` | Synchronous full run (targets + all layer settings) |
| `POST /audit/bulk`, `POST /audit` | Row-list and single-URL audits |
| `POST /audit/ai-visibility` | F11 GEO/AEO check for one URL |
| `POST /utils/autofill` | Deep site profiler (Layer 0) |
| `POST /utils/verify-inputs` | Live verification of every input field |
| `GET /utils/sitemap?url=` | Sitemap expansion preview |
| `GET /utils/crux?url=` + `POST /utils/psi` | Honest page CWV (CrUX/PSI) |
| `POST /utils/affiliate-import` + `POST /utils/traffic-import` | VERIFIED revenue inputs |
| `POST /export/pdf` + `POST /export/jira` | Real PDF bytes + Jira/Linear push |
| `GET /content` + `GET /llms.txt` + `GET /openapi-example` | Docs, AI-crawler file, API example |
| `GET /config/defaults` (secrets redacted) | Enterprise defaults for form prefill |
| `GET /health` | Health check (Playwright + rate-limit status) |

---

## 10 · Quickstart

**Fastest: use the live deployment — https://outbound-affiliate-link-chain-latency.onrender.com/** (Page 1 inputs → Run → Page 2 analysis → Page 3 outputs).

Docker (recommended for self-hosting — Playwright Chromium pre-installed, hybrid mode real):

```powershell
docker compose up --build
# open http://localhost:8099/  (the tool lives at the domain root; /app/ still works as an alias.
#  On Render: https://<your-service>.onrender.com/ — set PATHFINDER_API_KEYS to require API keys)
```

Local:

```powershell
pip install -r requirements.txt
python -m playwright install --with-deps chromium   # enables headless escalation + screenshots
python cli.py audit --csv samples/urls.csv          # CLI audit → output/ (+ SQLite run + webhook POST)
python cli.py audit --url "https://publisher.com/out/bet365?btag=1" --operator bet365 --clicks 5000 --epc 1.85
python cli.py serve --port 8099                     # web app + API
pytest -q                                           # 22 tests
```

Open **http://localhost:8099** → Page 1 inputs (3 steps) → Run → Page 2 analysis → Page 3 outputs.

---

## 11 · Architecture & file map

```
cli.py                          CLI (audit / serve)
config/enterprise.yaml          tracking regex, networks, geo/compliance packs, thresholds, MCP/metrics/SSRF sections
config/compliance_feed.json     versioned helplines + badges (feed wins over YAML drift)
frontend/                       3-page web app (index/analysis/outputs/info + shared JS/CSS)
src/redirect_pathfinder/
  tracer.py                     TLS-impersonation PRIMARY per-hop fetch (curl-cffi, httpx fallback, per_hop_via) + per-hop alt-svc capture + SSRF guard (DoH DNS, canonical tcp_tls_est, bot-wall score, mounts-based proxy, task-safe throttle)
  tls_client.py                 curl-cffi impersonation (chrome131/safari18) + quarterly UA rotation (Chrome 132 / Safari 18.4)
  ssrf.py                       SSRF guard (private/link-local/169.254 blocked, DNS-rebinding check, 5MB cap) — enforced on every server-fetch path
  dns_rdap.py / botwall.py      DoH + RDAP attribution / WAF scoring (CF/Datadome/Akamai/PX)
  rule_engine.py                param survival (unknown-state, mid-chain injection, duplicate-preserving) + network-path audits
  latency.py / compliance.py    honest drop-off curve (+band) / 9-geo packs + age-gate + reputation-abuse (word-boundary soft-404)
  intel.py / easylist.py        adblock(ITP)/deep-link/brand/offer + Consent Mode v2/TCF 2.3 + live EasyList + consent/GPC (deterministic headers)
  content_quality.py / geo_ai.py SpamBrain bands + scaled-content + merchant-copy bands / F11 infra diagnostics, bands only (Google-May-2026-correct)
  sov.py                        prompt-level Share-of-Voice on 4 live providers (caps + cache + partial labels)
  crux_psi.py / screenshots.py  honest page CWV (CrUX+history/PSI+INP attribution/LCP-budget gate/RUM snippet) / screenshot evidence
  seo_audit.py                  canonical normalize + 9-geo hreflang map + source-vs-final rel=sponsored + HTTP/3 alt-svc/Early-Hints/bfcache
  proxy_routing.py              per-geo proxy abstraction + exit-IP probe (redacted logging)
  persistence.py / monitoring.py idempotent SQLite runs + diffs + 90-run retention (+schedules table) / APScheduler (fresh schedule each tick + overlap guard)
  job_store.py                  persistent jobs (Redis primary → SQLite → MEM, wall-clock, SSE resume)
  mcp.py                        MCP tools + manifest (trace_chain/param_audit/sla_matrix/verify/ai_visibility/crux) — SSRF-guarded
  observability.py / multitenant.py /metrics + OTel spans/Sentry / workspaces + key scopes + cost caps + Docker-secrets helper
  affiliate_sync.py / ga4_gsc.py live affiliate APIs + S2S validator + live GA4/GSC with 25k pagination + backoff + GA4↔GSC join (CSV fallback)
  compliance_feed.py            versioned regulator feed loader
  ticketing.py                  Jira/Linear/ClickUp real POST
  autofill.py                   deep site profiler (ROTATING_UAS, EXAMPLE-ONLY hints, crawl-delay + time budget, SSRF-guarded, robots-respecting, 5MB-capped, hreflang-aware)
  orchestrator.py               bulk engine + forensics + evidence + explicit render-fork rule + seeded sampled cross-device parity + OTel spans
  revenue.py / edge_healer.py   UNKNOWN/ESTIMATED/VERIFIED ranges + SLA + Worker/Vercel/WP patches (explicit fallback, JS-escaped, passthrough)
  alerting_export.py            alerts (HMAC-signed POST + correlation IDs), CSV/JSON/JIRA exports
  site_content.py               docs single source of truth
  export_pdf.py                 real PDF generator (ReportLab, ranges + F11/CWV honesty)
  api.py                        FastAPI: web app at / (+ /app/ alias), auth jobs+SSE, MCP, SOV, live imports, monitoring, SSRF guards, PDF
Dockerfile / docker-compose.yml Chromium image + api/redis/postgres stack · .github/workflows/ci.yml CI
samples/urls.csv                demo targets · samples/autofill-demo.html  profiler fixture
tests/test_pathfinder.py        9 deterministic unit tests
tests/test_enterprise.py        13 enterprise tests (mocked chains, sitemap index, API smoke, honesty)
docs/screenshots/               27 live screenshots (this README)
```

---

## 12 · Configuration reference

All defaults live in `config/enterprise.yaml`: `audit` (hops, timeout, pooling 50 conns, HTTP/2, retries, per-host RPS, `hybrid` mode, `deep_cross_device: auto`, screenshots, CrUX/PSI flags), `tracking_rules` (ID/sub-ID/promo regex + ITP strip list + operator→network map), `geo_profiles` (9 geos with `proxy_exit`), `compliance_packs` (US-NJ/PA/MI/OH/MA, UK, CA-ON, DE, NL), `latency` (900 warn / 1800 critical + published formulas), `revenue` (no default EPC — UNKNOWN until inputs/imports), `easylist` (live URL + TTL), `monitoring` (SQLite path + schedule), `security` (API keys, CORS, redaction), `crawl` constraints, `soft404_phrases`. Page 1 overrides any of them per run.

---

## 13 · Verification & honesty model

- **Measured live**: statuses, hop counts, IPs + ASN/org (DoH + RDAP), DNS/TTFB/download/total timings, param presence per hop, page text, headers, sitemap XML validity, URL reachability, EasyList ruleset version, PDF bytes — each with evidence counters.
- **Estimated and labelled**: `tcp_tls_est_ms` (subtraction formula published), drop-off % (industry bounce curve published), reach-scaled clicks, table EPCs — badged ESTIMATED with ranges (±40%) until GA4/affiliate imports flip rows to VERIFIED (±15%).
- **UNKNOWN, never faked**: no clicks/EPC → revenue UNKNOWN $0 (no $1.25 default); no CrUX/PSI key → page CWV unavailable (redirect-ms never mislabelled LCP/INP/CLS); no proxy → direct + explicit unpinned warning.
- **Never claimed**: private revenue without credentials, exit-IP execution without your proxy provider, LLM vision (brand checks are deterministic text/DOM analysis), invented AI-citation scores (F11 reports measured facts only).

---

## 14 · Test report

`pytest -q` → **22 passed** (9 unit + 13 enterprise): param extraction, strip/mutation detection, latency verdicts + honesty fields, soft-404 NLP, revenue UNKNOWN/ESTIMATED/VERIFIED math + ranges, strict-sitemap validator, sitemap-index recursion (mocked), respx-mocked 302→302→200 chain with param-strip audit, bot-wall scoring, live-EasyList parsing, ITP simulation, EEAT/thin-content, affiliate + traffic CSV mappers, API smoke (`/health`, `/content`, `/llms.txt`, imports). CI runs the same suite on every push (`.github/workflows/ci.yml`). Live E2E (headless Chromium): analysis renders F1–F11, outputs render O1–O9, zero page errors.

---

## 15 · Business impact

Zero lost commissions · higher click-to-register CR · 90%+ QA automation · regulatory fine shielding · edge rerouting through event spikes · 10–20% ad-block revenue recovery · hard SLA leverage at renewals. (Full breakdown: in-app docs → Impact.)

---

## 16 · Roadmap

~~Residential-proxy pool connectors~~ ✅ shipped (per-geo `proxy_exit` + exit-IP probe) · ~~affiliate API live sync~~ ✅ shipped (live pulls PRIMARY, CSV fallback) · ~~GA4/Search Console ingestion~~ ✅ shipped (live Data API + GSC PRIMARY, CSV fallback) · ~~scheduled monitoring + diff alerts~~ ✅ shipped (APScheduler + SQLite + webhooks, DB-backed schedules) · ~~MCP server~~ ✅ shipped (`POST /mcp`) · ~~SSRF guard + auth SSE + persistent jobs~~ ✅ shipped · ~~F11 Google-May-2026 correction + SOV~~ ✅ shipped · ~~TLS impersonation primary + alt-svc fix~~ ✅ shipped (v3.2.0) · ~~scoped auth + per-key rate limits~~ ✅ shipped (v3.2.0) · ~~rule-engine unknown-state + explicit-fallback edge patches~~ ✅ shipped (v3.2.0) · ~~TCF 2.3 + 9-geo hreflang + 4-provider SOV + HMAC webhooks + CrUX LCP-budget gate~~ ✅ shipped (v3.2.0) · multi-user workspaces (scopes + cost caps shipped; Postgres RLS next) · predictive regression + white-label PDFs (P2).

---

## 17 · Contributing & license

Issues and PRs welcome. MIT licensed — see `LICENSE` (to be added with your preferred terms).
