"""Enterprise tests — deterministic, respx-mocked HTTP (no real network)."""
import httpx
import respx
from redirect_pathfinder.rule_engine import audit_param_survival
from redirect_pathfinder.models import ChainResult, Hop, LatencyScore, ComplianceResult
from redirect_pathfinder.latency import score_latency
from redirect_pathfinder.revenue import score_revenue_and_health
from redirect_pathfinder.botwall import score_botwall
from redirect_pathfinder.easylist import parse_easylist_hosts, match_blocked, simulate_itp_strip
from redirect_pathfinder.content_quality import audit_content_quality, merchant_copy_similarity
from redirect_pathfinder.affiliate_sync import map_affiliate_csv
from redirect_pathfinder.ga4_gsc import map_traffic_csv
from redirect_pathfinder.input_ingest import fetch_sitemap_urls, _strict_urls


def _ok_chain(**kw):
    c = ChainResult(source_url="https://a.com", clicks_30d=kw.pop("clicks_30d", 1000),
                    epc=kw.pop("epc", 2.0), **kw)
    c.latency = LatencyScore(total_ms=100, verdict="fast")
    c.compliance = ComplianceResult(compliant=True)
    c.params_intact = True
    c.chain_ok = True
    return c


def test_revenue_unknown_when_no_inputs():
    c = _ok_chain(clicks_30d=0, epc=0)
    c = score_revenue_and_health(c)
    assert c.revenue_basis.startswith("UNKNOWN")
    assert c.revenue_at_risk == 0.0


def test_revenue_estimated_has_range_and_formula():
    c = _ok_chain(chain_ok=False)
    c.params_intact = False
    c = score_revenue_and_health(c)
    assert c.revenue_basis.startswith("ESTIMATED")
    assert c.revenue_at_risk == 2000.0
    assert c.revenue_at_risk_low < c.revenue_at_risk <= c.revenue_at_risk_high
    assert "ESTIMATED" in c.revenue_formula


def test_latency_honesty_fields():
    c = ChainResult(source_url="https://a.com", hops=[Hop(index=0, url="https://a.com", status=200, total_ms=2500)])
    c = score_latency(c)
    assert c.latency.verdict == "critical"
    assert "ESTIMATED" in c.latency.tcp_tls_formula
    assert "ESTIMATED" in c.latency.dropoff_formula
    assert "CrUX" in c.latency.redirect_budget_note or "CWV" in c.latency.redirect_budget_note


def test_botwall_scores_cloudflare():
    bw = score_botwall(403, {"cf-mitigated": "challenge", "set-cookie": "__cf_bm=xyz"},
                       "<html>Just a moment... verify you are human</html>")
    assert bw["botwall_score"] >= 30
    assert bw["needs_headless"] is True
    assert "cloudflare" in bw["vendors"]
    clean = score_botwall(200, {"server": "nginx"}, "<html><p>hello bonus 21+</p></html>")
    assert clean["botwall_score"] == 0


def test_easylist_parse_and_match():
    txt = "||doubleclick.net^\n||tracking.example.com^\n@@||allow.com^\n! comment\n"
    hosts = parse_easylist_hosts(txt)
    assert "doubleclick.net" in hosts
    m = match_blocked("https://track.doubleclick.net/click?btag=1", hosts)
    assert m != ""
    assert match_blocked("https://publisher.com/review-bet365", hosts) == ""


def test_itp_simulation():
    assert "gclid" in simulate_itp_strip({"gclid": "1", "btag": "2"})
    assert "btag" not in simulate_itp_strip({"gclid": "1", "btag": "2"})


def test_content_quality_thin_and_eeat():
    thin = audit_content_quality("<html><head><title>x</title></head><body><p>Bet now bonus</p></body></html>", "https://x.com/coupons/bet365/")
    assert thin["thin_content"] is True
    assert thin["eeat_score_est"] < 60
    rich = audit_content_quality("<html><head><title>Review</title></head><body><h1>Test</h1>" + "<p>We tested withdrawals and measured payout speed. Comparison table methodology.</p>" * 60 +
                                 "<table><tr><td>x</td></tr></table><p>Author: expert, updated 2026. Wagering T&Cs apply 18+.</p></body></html>", "https://x.com/review/")
    assert rich["eeat_score_est"] > thin["eeat_score_est"]
    assert merchant_copy_similarity("bet now get bonus bet now", "bet now get bonus bet now") > 80


def test_affiliate_csv_maps_epc():
    csv_text = "operator,campaign,clicks,revenue\nbet365,welcome,1000,1850\n"
    m = map_affiliate_csv(csv_text, "everflow")
    assert m["bet365|welcome|everflow"]["epc"] == 1.85


def test_traffic_csv_maps_clicks():
    m = map_traffic_csv("page,clicks\n/out/bet365,4200\n")
    assert m["/out/bet365"] == 4200


def test_strict_sitemap_rejects_dead():
    assert _strict_urls("<html>nope</html>") == ("", [])
    assert _strict_urls("not xml")[1] == []
    kind, urls = _strict_urls('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://x.com/</loc></url></urlset>')
    assert kind == "urlset" and urls == ["https://x.com/"]


@respx.mock
async def test_mocked_chain_302_302_200_with_param_strip():
    from redirect_pathfinder.tracer import trace_chain
    respx.get("https://cloudflare-dns.com/dns-query").respond(json={"Answer": [{"type": 1, "data": "1.2.3.4"}]})
    respx.get("https://dns.google/resolve").respond(json={"Answer": []})
    respx.get("https://rdap.arin.net/registry/ip/1.2.3.4").respond(404)
    respx.get("https://rdap.db.ripe.net/ip/1.2.3.4").respond(404)
    r0 = respx.get("https://pub.com/out?btag=1&subid=2").respond(302, headers={"location": "https://trk.net/c?btag=1&subid=2"})
    r1 = respx.get("https://trk.net/c?btag=1&subid=2").respond(302, headers={"location": "https://op.com/land?subid=2"})
    r2 = respx.get("https://op.com/land?subid=2").respond(200, text="<html><head><title>Bet</title></head><body>21+ bonus</body></html>")
    hops, html, js, bw, meta = await trace_chain("https://pub.com/out?btag=1&subid=2", per_host_rps=0)
    assert len(hops) == 3
    assert hops[0].status == 302 and hops[2].status == 200
    assert "ESTIMATED" in meta["tcp_tls_est"]
    assert hops[0].doh_ms >= 0
    c = ChainResult(source_url="https://pub.com/out?btag=1&subid=2", hops=hops)
    c = audit_param_survival(c, ["(btag|affid)", "(subid|s1)"])
    ev = {e.param: e.status for e in c.param_events}
    assert ev["btag"] == "stripped" and ev["subid"] == "survived"
    assert r0.called and r1.called and r2.called


@respx.mock
async def test_sitemap_index_recursion():
    idx = '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://x.com/s1.xml</loc></sitemap></sitemapindex>'
    urlset = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://x.com/a</loc></url><url><loc>https://x.com/b</loc></url></urlset>'
    respx.get("https://x.com/sitemap.xml").respond(200, text=idx)
    respx.get("https://x.com/s1.xml").respond(200, text=urlset)
    urls = await fetch_sitemap_urls("https://x.com/sitemap.xml")
    assert urls == ["https://x.com/a", "https://x.com/b"]


def test_api_smoke():
    from fastapi.testclient import TestClient
    from redirect_pathfinder.api import app
    t = TestClient(app)
    assert t.get("/health").json()["status"] == "ok"
    assert "sections" in t.get("/content").json()
    assert "Allow" in t.get("/llms.txt").text
    r = t.post("/utils/traffic-import", json={"csv_text": "page,clicks\n/out/x,10\n"})
    assert r.json()["pages"] == 1
