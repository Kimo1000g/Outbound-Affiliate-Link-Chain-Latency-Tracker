"""Smoke tests — deterministic unit checks that never touch the network."""
from redirect_pathfinder.rule_engine import extract_params, compile_patterns
from redirect_pathfinder.models import ChainResult, Hop
from redirect_pathfinder.rule_engine import audit_param_survival
from redirect_pathfinder.latency import score_latency
from redirect_pathfinder.revenue import score_revenue_and_health
from redirect_pathfinder.compliance import detect_soft404


def test_extract_params():
    assert extract_params("https://x.com/?btag=1&subid=2") == {"btag": "1", "subid": "2"}


def test_param_strip_detection():
    c = ChainResult(source_url="https://a.com/?btag=1&subid=2",
                    hops=[Hop(index=0, url="https://a.com/?btag=1&subid=2", status=302),
                          Hop(index=1, url="https://b.com/?subid=2", status=200)])
    c = audit_param_survival(c, ["(btag|affid)", "(subid|s1)"])
    ev = {e.param: e.status for e in c.param_events}
    assert ev["btag"] == "stripped"
    assert ev["subid"] == "survived"
    assert c.params_intact is False


def test_latency_verdict():
    c = ChainResult(source_url="https://a.com", hops=[Hop(index=0, url="https://a.com", status=200, total_ms=2500)])
    c = score_latency(c)
    assert c.latency.verdict == "critical"
    assert c.latency.total_ms == 2500


def test_soft404():
    hit, phrases = detect_soft404("<title>Bet365 Bonus</title><p>This promotion has expired</p>", 200, ["promotion has expired"])
    assert hit is True


def test_revenue_math():
    c = ChainResult(source_url="https://a.com", clicks_30d=1000, epc=2.0, chain_ok=False, broken_reason="final-http-404")
    from redirect_pathfinder.models import LatencyScore, ComplianceResult
    c.latency = LatencyScore(total_ms=100, verdict="fast")
    c.compliance = ComplianceResult(compliant=True)
    c.params_intact = False
    c = score_revenue_and_health(c)
    assert c.revenue_at_risk == 2000.0
    assert c.revenue_basis.lower().startswith("estimated")


def test_revenue_basis_verified():
    c = ChainResult(source_url="https://a.com", clicks_30d=1000, epc=2.0,
                    chain_ok=True, revenue_verified=True)
    from redirect_pathfinder.models import LatencyScore, ComplianceResult
    c.latency = LatencyScore(total_ms=100, verdict="fast")
    c.compliance = ComplianceResult(compliant=True)
    c.params_intact = True
    c = score_revenue_and_health(c)
    assert c.revenue_basis.lower().startswith("verified")


def test_looks_like_sitemap_strict():
    from redirect_pathfinder.autofill import _looks_like_sitemap
    assert _looks_like_sitemap('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://x.com/</loc></url></urlset>') == "urlset"
    assert _looks_like_sitemap('<?xml version="1.0"?><sitemapindex><sitemap><loc>https://x.com/s1.xml</loc></sitemap></sitemapindex>') == "index"
    assert _looks_like_sitemap("<html><body>nope</body></html>") == ""
    assert _looks_like_sitemap("not xml at all") == ""
    assert _looks_like_sitemap("<urlset></urlset>") == ""  # no locs → not working


def test_clean_lines_skips_comments_and_notes():
    from redirect_pathfinder.api import _clean_lines
    assert _clean_lines(["https://a.com/sm.xml  # ✅ LIVE (3 urls)", "# ❌ DEAD [404] https://b.com", "",
                         "https://c.com/page"]) == ["https://a.com/sm.xml", "https://c.com/page"]


def test_page_forensics_counts():
    from redirect_pathfinder.orchestrator import page_forensics
    f = page_forensics("<html><head><title>T</title></head><body><h1>Hi</h1><p>one two three</p><a href='/x'>l</a></body></html>", "https://x.com")
    assert f["words"] >= 4 and f["outlinks"] == 1 and f["h1"] == "Hi"
