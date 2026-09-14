"""Real server-side PDF export — genuine PDF bytes via ReportLab (not print-to-PDF).

Layout rules (strict, to never overlap):
- One idea per block: KPI grid, then one stacked label/value card per chain.
- Hop URLs truncated to 80 chars; full detail preserved in JSON/CSV exports.
- Every table's column widths sum to exactly the printable width (182 mm on A4).
- Header rows repeat; rows may split across pages but never overlap (no KeepTogether
  on growing tables, generous leading, footer space reserved via bottomMargin).
"""
from __future__ import annotations
import datetime as _dt
from io import BytesIO
from xml.sax.saxutils import escape as _esc
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

PAGE_W = 182 * mm
ACCENT = colors.HexColor("#0e7490")
INK = colors.HexColor("#0b1220")
MUT = colors.HexColor("#475569")
ALT = colors.HexColor("#f1f5f9")
GRID = colors.HexColor("#94a3b8")


def _p(text, style, maxlen: int = 0) -> Paragraph:
    t = str(text if text is not None else "—")
    if maxlen and len(t) > maxlen:
        t = t[:maxlen] + "…"
    return Paragraph(_esc(t), style)


def _grid(rows: list[list], s_body, widths: list, header: bool = False) -> Table:
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [("BOX", (0, 0), (-1, -1), 0.6, INK),
             ("INNERGRID", (0, 0), (-1, -1), 0.35, GRID),
             ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 4),
             ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), INK),
                  ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
    else:
        style += [("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, ALT])]
    t.setStyle(TableStyle(style))
    return t


def build_pdf(result: dict, extras: dict | None = None) -> bytes:
    chains = result.get("chains", []) or []
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=14 * mm, bottomMargin=18 * mm,
                            title="Outbound Affiliate Link Chain & Latency Tracker — Audit Report")
    ss = getSampleStyleSheet()
    s_title = ParagraphStyle("t", parent=ss["Title"], fontSize=18, leading=22, textColor=INK)
    s_sub = ParagraphStyle("st", parent=ss["Normal"], fontSize=9, leading=12.5, textColor=MUT)
    s_h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=13, leading=16,
                          textColor=ACCENT, spaceBefore=16, spaceAfter=6, keepWithNext=True)
    s_h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=10.5, leading=14,
                          textColor=INK, spaceBefore=10, spaceAfter=4, keepWithNext=True)
    s_b = ParagraphStyle("b", parent=ss["Normal"], fontSize=8.6, leading=12.5)
    s_c = ParagraphStyle("c", parent=ss["Normal"], fontSize=7.4, leading=10)
    s_ch = ParagraphStyle("ch", parent=s_c, textColor=colors.white, fontName="Helvetica-Bold")
    s_code = ParagraphStyle("code", parent=ss["Normal"], fontName="Courier", fontSize=6.8,
                            leading=9, textColor=colors.HexColor("#1e293b"))

    story: list = [
        Paragraph("Outbound Affiliate Link Chain &amp; Latency Tracker", s_title),
        Paragraph("Enterprise affiliate redirect-chain audit — full feature analysis (F1–F10) "
                  f"+ outputs (O1–O9). Generated {_dt.datetime.now():%Y-%m-%d %H:%M} · "
                  f"{len(chains)} chains.", s_sub),
        HRFlowable(width="100%", color=ACCENT, thickness=1.2),
        Paragraph("O1 · Executive revenue risk dashboard", s_h1),
    ]
    broken = sum(1 for c in chains if not c.get("chain_ok") or not c.get("params_intact"))
    avg_lat = round(sum((c.get("latency") or {}).get("total_ms", 0) for c in chains) / max(1, len(chains)))
    verified_n = sum(1 for c in chains if c.get("revenue_verified"))
    rr = result.get("revenue_range") or [result.get("revenue_at_risk") or 0, result.get("revenue_at_risk") or 0]
    kpi = [[_p("Revenue at risk (30d)", s_b), _p(f"${(result.get('revenue_at_risk') or 0):,.2f} (range ${rr[0]:,.2f}–${rr[1]:,.2f})", s_b)],
           [_p("Revenue honesty", s_b), _p(result.get("revenue_honesty") or
                                          (f"{verified_n}/{len(chains)} VERIFIED; rest ESTIMATED ±40% or UNKNOWN — see per-chain basis"), s_b)],
           [_p("Link Health Index", s_b), _p(f"{result.get('health_index')}/100", s_b)],
           [_p("Chains audited", s_b), _p(str(len(chains)), s_b)],
           [_p("Broken / param-loss", s_b), _p(str(broken), s_b)],
           [_p("Compliance violations", s_b),
            _p(str(sum(1 for c in chains if not (c.get("compliance") or {}).get("compliant"))), s_b)],
           [_p("Average chain latency", s_b), _p(f"{avg_lat} ms", s_b)]]
    story.append(_grid(kpi, s_b, [70 * mm, PAGE_W - 70 * mm]))

    story.append(Paragraph("O2 / F1 · Chain cards with hop maps", s_h1))
    for i, c in enumerate(chains, 1):
        lat = c.get("latency") or {}
        story.append(Paragraph(f"Chain #{i} — health {c.get('health_score')}/100", s_h2))
        card = [
            [_p("<b>Source</b>", s_c), _p(c.get("source_url"), s_c, 90)],
            [_p("<b>Final lander</b>", s_c), _p(c.get("final_url"), s_c, 90)],
            [_p("<b>Status</b>", s_c),
             _p(f"HTTP {c.get('final_status')} · {len(c.get('hops') or [])} hops · "
                f"{round(lat.get('total_ms', 0))} ms ({lat.get('verdict')}) · "
                f"drop-off {lat.get('dropoff_risk_pct')}%", s_c)],
            [_p("<b>Params</b>", s_c),
             _p("INTACT" if c.get("params_intact") else
                ("LOSS — " + (c.get("broken_reason") or "stripped")), s_c)],
            [_p("<b>Compliance</b>", s_c),
             _p("COMPLIANT" if (c.get("compliance") or {}).get("compliant") else "VIOLATION", s_c)],
            [_p("<b>Revenue</b>", s_c),
             _p(f"${c.get('revenue_at_risk', 0):,.2f} at risk · basis: "
                f"{'VERIFIED' if c.get('revenue_verified') else 'ESTIMATED'}", s_c)],
        ]
        story.append(_grid(card, s_c, [32 * mm, PAGE_W - 32 * mm]))
        hops = [[_p("Hop", s_ch), _p("HTTP", s_ch), _p("URL", s_ch), _p("Timing / detail", s_ch)]]
        for h in (c.get("hops") or []):
            detail = f"{h.get('redirect_type')} · {h.get('total_ms')} ms"
            if h.get("error"):
                detail += f" · {h['error'][:80]}"
            hops.append([_p(h.get("index"), s_c), _p(h.get("status"), s_c),
                         _p(h.get("url"), s_c, 80), _p(detail, s_c)])
        story.append(Spacer(1, 2 * mm))
        story.append(_grid(hops, s_c, [12 * mm, 14 * mm, 96 * mm, PAGE_W - 122 * mm], header=True))
        pf = c.get("page_forensics") or {}
        if pf.get("words"):
            story.append(Spacer(1, 2 * mm))
            story.append(Paragraph(
                f"Lander forensics — title: {_esc(str(pf.get('title', ''))[:90])} · "
                f"{pf.get('words')} words · {pf.get('kb')} KB · {pf.get('outlinks')} outlinks · "
                f"{pf.get('images')} images · lang {pf.get('lang') or '?'}", s_sub))
        dp = c.get("device_parity") or {}
        if dp.get("alt_device"):
            story.append(Paragraph(
                f"Cross-device re-trace ({dp.get('primary_device')} → {dp.get('alt_device')}): "
                f"{'PARITY OK' if dp.get('parity_ok') else 'DIVERGENT'} · "
                f"alt final HTTP {dp.get('alt_status')}", s_sub))
        ev = c.get("evidence") or {}
        if ev:
            story.append(Paragraph(
                f"Evidence — {ev.get('http_requests')} HTTP requests · "
                f"{ev.get('dns_lookups')} DNS lookups · {ev.get('bytes_downloaded')} bytes · "
                f"mode {ev.get('execution_mode')}", s_sub))

    story.append(Paragraph("F2 · Parameter survival ledger", s_h1))
    rows = [[_p("Chain", s_ch), _p("Param", s_ch), _p("Verdict", s_ch), _p("Detail", s_ch)]]
    for c in chains:
        for e in (c.get("param_events") or []):
            det = ("intact" if e.get("status") == "survived"
                   else (f"lost at hop {e.get('stripped_at_hop')}" if e.get("stripped_at_hop") is not None
                         else str(e.get("mutated_to") or e.get("status"))))
            rows.append([_p(c.get("source_url"), s_c, 60), _p(e.get("param"), s_c),
                         _p(e.get("status"), s_c), _p(det, s_c, 60)])
    if len(rows) == 1:
        rows.append([_p("—", s_c)] * 4)
    story.append(_grid(rows, s_c, [62 * mm, 30 * mm, 28 * mm, PAGE_W - 120 * mm], header=True))

    story.append(Paragraph("F3 / O3 · Latency ledger + network SLA", s_h1))
    story.append(_grid(
        [[_p("Chain", s_ch), _p("Total", s_ch), _p("Verdict", s_ch), _p("Drop-off", s_ch)]] +
        [[_p(c.get("source_url"), s_c, 70),
          _p(f"{round((c.get('latency') or {}).get('total_ms', 0))} ms", s_c),
          _p((c.get("latency") or {}).get("verdict"), s_c),
          _p(f"{(c.get('latency') or {}).get('dropoff_risk_pct')}%", s_c)] for c in chains],
        s_c, [86 * mm, 28 * mm, 28 * mm, PAGE_W - 142 * mm], header=True))
    story.append(Spacer(1, 3 * mm))
    story.append(_grid(
        [[_p("Network", s_ch), _p("Checks", s_ch), _p("Success %", s_ch),
          _p("Avg ms", s_ch), _p("Rev at risk", s_ch)]] +
        [[_p(s.get("network"), s_c), _p(s.get("checks"), s_c), _p(s.get("success_rate_pct"), s_c),
          _p(s.get("avg_latency_ms"), s_c), _p(f"${s.get('revenue_at_risk', 0):,.2f}", s_c)]
         for s in (result.get("sla") or [])],
        s_c, [52 * mm, 22 * mm, 28 * mm, 28 * mm, PAGE_W - 130 * mm], header=True))

    story.append(Paragraph("F4 / O4 · Compliance & geo audit", s_h1))
    crows = [[_p("Lander", s_ch), _p("Missing strings", s_ch), _p("Soft-404", s_ch),
              _p("Geo", s_ch), _p("Verdict", s_ch)]]
    for c in chains:
        k = c.get("compliance") or {}
        miss = ", ".join(s for s, f in (k.get("required_strings") or {}).items() if not f) or "none"
        crows.append([_p(c.get("final_url"), s_c, 60), _p(miss, s_c, 50),
                      _p(("EXPIRED: " + ",".join(k.get("soft404_phrases_hit") or []))
                         if k.get("soft404_detected") else "clean", s_c),
                      _p(k.get("geo_mismatch_detail") or "routed OK", s_c, 40),
                      _p("PASS" if k.get("compliant") else "VIOLATION", s_c)])
    story.append(_grid(crows, s_c, [58 * mm, 42 * mm, 30 * mm, 32 * mm, PAGE_W - 162 * mm], header=True))

    story.append(Paragraph("F6–F10 · Healing, privacy, deep-link, brand, offers", s_h1))
    story.append(_grid(
        [[_p("Chain", s_ch), _p("Patch?", s_ch), _p("Ad-block", s_ch),
          _p("Deep-link", s_ch), _p("Brand", s_ch), _p("Offer", s_ch)]] +
        [[_p(c.get("source_url"), s_c, 55),
          _p("YES" if (c.get("edge_patch") or {}).get("needed") else "no", s_c),
          _p(",".join(c.get("adblock_blocked_hosts") or []) or "resilient", s_c, 30),
          _p((c.get("deeplink") or {}).get("verdict"), s_c),
          _p("ALIGNED" if (c.get("brand_alignment") or {}).get("aligned") else "MISALIGNED", s_c),
          _p("MISMATCH" if (c.get("offer_discrepancy") or {}).get("mismatch") else "ok", s_c)]
         for c in chains],
        s_c, [56 * mm, 18 * mm, 34 * mm, 30 * mm, 26 * mm, PAGE_W - 164 * mm], header=True))

    story.append(Paragraph("F11 / O3b · AI visibility (GEO) + page CWV honesty", s_h1))
    for c in chains:
        ai = c.get("ai_visibility") or {}
        cx = c.get("crux") or {}
        story.append(Paragraph(
            f"{_esc(str(c.get('source_url', ''))[:70])} — llms.txt: "
            f"{(ai.get('llms_txt') or {}).get('present', '?')} · robots-AI-allow: "
            f"{(ai.get('robots_ai') or {}).get('allows_ai', '?')} · JSON-LD: "
            f"{(ai.get('json_ld') or {}).get('valid', '?')} · "
            f"CrUX: {'available' if cx.get('available') else 'not configured — redirect ms is NOT page CWV'}", s_b))
    story.append(Paragraph("O5 · Alerts", s_h1))
    alerts = result.get("alerts") or [{"channel": "—", "severity": "info",
                                       "text": "No alerts — all quiet"}]
    for a in alerts:
        sev = _esc(str(a.get("severity", "")))
        ch = _esc(str(a.get("channel", "")))
        tx = _esc(str(a.get("text", "")))
        story.append(Paragraph(f"<b>[{sev}]</b> ({ch}) {tx}", s_b))

    brokens = [x for x in chains if not x.get("chain_ok") or not x.get("params_intact")]
    if brokens:
        story.append(Paragraph("O9 · Vendor tickets (escalation-ready)", s_h1))
        for c in brokens:
            story.append(Paragraph(f"To: {_esc(str(c.get('expected_operator', 'operator')))} "
                                   f"affiliate team — {c.get('geo')} / {c.get('device')}", s_h2))
            story.append(Paragraph(_esc(c.get("vendor_ticket", ""))[:1500].replace("\n", "<br/>"), s_code))
            story.append(Spacer(1, 3 * mm))
        story.append(PageBreak())
        story.append(Paragraph("O6 · Edge patch appendix (Cloudflare Workers)", s_h1))
        for c in brokens:
            if not (c.get("edge_patch") or {}).get("needed"):
                continue
            story.append(Paragraph(_esc(str(c.get("source_url", ""))), s_h2))
            story.append(Paragraph(_esc((c.get("edge_patch") or {}).get(
                "cloudflare_worker_js", ""))[:2000].replace("\n", "<br/>"), s_code))
            story.append(Spacer(1, 4 * mm))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawString(14 * mm, 11 * mm,
                      "Outbound Affiliate Link Chain & Latency Tracker — confidential audit report")
    canvas.drawRightString(A4[0] - 14 * mm, 11 * mm, f"Page {doc.page}")
    canvas.restoreState()
