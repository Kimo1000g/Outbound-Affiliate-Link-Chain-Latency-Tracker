// Shared helpers: API base, theme, storage, downloads, badges, hop maps.
// Same-origin by default (works at / on Render AND at /app/ locally). Only when a
// developer opens the pages from a different local port/file do we point at the
// local backend on 8099.
const API = (() => {
  try {
    const h = location.hostname;
    const local = (h === "localhost" || h === "127.0.0.1" || h === "" || h === "0.0.0.0");
    if (local && location.port && location.port !== "8099") return "http://localhost:8099";
  } catch (e) {}
  return "";
})();
const store = {
  get(k, fb) {
    try { const v = sessionStorage.getItem(k); if (v) return JSON.parse(v); } catch (e) {}
    try { const w = localStorage.getItem("pf_" + k); if (w) return JSON.parse(w); } catch (e) {}
    return fb;
  },
  set(k, v) {
    try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) {}
    try { localStorage.setItem("pf_" + k, JSON.stringify(v)); } catch (e) {}
  },
  clear() {
    ["pf_result", "pf_input"].forEach(k => {
      try { sessionStorage.removeItem(k); } catch (e) {}
      try { localStorage.removeItem("pf_" + k); } catch (e) {}
    });
  }
};
// Never leave a page stuck on "Loading…": show the actual crash instead.
window.addEventListener("error", e => {
  const root = document.querySelector("#root");
  if (root && /Loading/.test(root.textContent || "")) {
    root.innerHTML = `<div class="card"><h2>Page failed to render</h2>
      <p class="d">Hard-refresh (Ctrl+Shift+R) to fetch the newest app files, then re-run from Page 1. Technical detail:</p>
      <pre>${String((e && (e.message || e.error)) || e).replace(/</g, "&lt;")}</pre>
      <div class="rowbtns"><button class="btn" onclick="location.href='index.html'">← Back to Page 1</button></div></div>`;
  }
});
function initTheme() {
  const t = localStorage.getItem("pf_theme") || "dark";
  document.documentElement.dataset.theme = t;
  const b = document.querySelector("#themebtn");
  if (b) b.textContent = t === "dark" ? "☀️ Light mode" : "🌙 Dark mode";
}
function toggleTheme() {
  const next = (document.documentElement.dataset.theme === "light") ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("pf_theme", next);
  initTheme();
}
function forensicsHTML(c) {
  const f = c.page_forensics || {};
  if (!f.words) return "";
  return `<div class="sub">🔬 Lander forensics — “${(f.title || "no title").slice(0, 90)}” · ${f.words} words · ${f.kb} KB · ${f.outlinks} outlinks · ${f.images} images · lang ${f.lang || "?"}</div>`;
}
function parityHTML(c) {
  const d = c.device_parity || {};
  if (!d.alt_device) return "";
  return `<div style="margin:4px 0">${badge("cross-device " + d.alt_device + " → " + (d.parity_ok ? "PARITY OK" : "DIVERGENT"), d.parity_ok ? "ok" : "fail")}</div>`;
}
function evidenceHTML(c) {
  const e = c.evidence || {};
  if (!e.http_requests) return "";
  return `<div class="sub">🧾 Evidence — ${e.http_requests} real HTTP requests · ${e.dns_lookups} DNS lookups · ${e.bytes_downloaded} bytes downloaded · mode ${e.execution_mode}${e.headless_used ? " · headless used" : ""}</div>`;
}
function revBasisBadge(c) {
  return badge(c.revenue_verified ? "VERIFIED $" : "ESTIMATED $", c.revenue_verified ? "ok" : "warn");
}
function fmtTime(s) {
  s = Math.max(0, Math.floor(s || 0));
  return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
}
async function getDefaults() {
  const r = await fetch(API + "/config/defaults");
  return r.json();
}
function badge(txt, cls) { return `<span class="badge ${cls}">${txt}</span>`; }
function statusBadge(s) { return badge(String(s ?? "?"), (s >= 200 && s < 300) ? "ok" : (s >= 300 && s < 400) ? "info" : "fail"); }
function verdictBadge(v) { return badge(v, v === "critical" ? "fail" : v === "warn" ? "warn" : "ok"); }
function download(name, text, type = "text/plain") {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click();
}
async function downloadPDF(result, input) {
  const r = await fetch(API + "/export/pdf", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ result, input: input || {} }) });
  if (!r.ok) throw new Error("PDF export failed: " + r.status);
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "affiliate-audit-report.pdf"; a.click();
}
function copyText(id) {
  const el = document.getElementById(id);
  navigator.clipboard.writeText(el.innerText).then(() => alert("Copied to clipboard"));
}
function toCSV(chains) {
  const h = ["source_url", "anchor", "operator", "network", "geo", "device", "final_url", "final_status", "hops", "total_ms", "verdict", "params_intact", "chain_ok", "broken_reason", "compliant", "revenue_at_risk", "health_score"];
  const q = v => `"${String(v ?? "").replace(/"/g, '""')}"`;
  return h.join(",") + "\n" + chains.map(c => [c.source_url, c.anchor_text, c.expected_operator, c.expected_network, c.geo, c.device, c.final_url, c.final_status, (c.hops || []).length, Math.round(c.latency?.total_ms || 0), c.latency?.verdict, c.params_intact, c.chain_ok, c.broken_reason, c.compliance?.compliant, c.revenue_at_risk, c.health_score].map(q).join(",")).join("\n");
}
function hopMapHTML(c) {
  return `<div class="hops">` + (c.hops || []).map((h, i) => {
    const bad = (h.status >= 400) || h.error ? " bad" : "";
    return `${i ? '<div class="arrow">→</div>' : ''}<div class="hop${bad}"><b>Hop ${h.index}</b> ${statusBadge(h.status)}<br/>
    <span style="word-break:break-all">${h.url}</span><br/>
    <span style="color:var(--mut)">type ${h.redirect_type} · ip ${h.ip || "?"} · ${h.server || ""}<br/>
    dns ${h.dns_ms}ms · tcp/tls ${h.tcp_tls_ms}ms · ttfb ${h.ttfb_ms}ms · dl ${h.download_ms}ms · <b>Σ ${h.total_ms}ms</b>
    ${h.error ? `<br/><span style="color:var(--red)">${h.error}</span>` : ""}${h.location_header ? `<br/>↳ ${h.location_header}` : ""}</span></div>`;
  }).join("") + `</div>`;
}
initTheme();
