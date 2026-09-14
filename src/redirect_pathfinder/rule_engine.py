"""Regex rule engine — validates affiliate parameter survival + network routing."""
from __future__ import annotations
import re
from urllib.parse import urlparse, parse_qsl
from .models import ChainResult, ParamEvent


def extract_params_multi(url: str) -> dict[str, list[str]]:
    """Query (+ fragment-query) params preserving duplicates.

    Returns dict of key -> list of values in encounter order.
    Fragment (#...) params are accounted: if the fragment looks like a
    query string (contains '='), or contains '?k=v', it is merged after
    the real query string. Duplicate keys keep ALL values.
    """
    out: dict[str, list[str]] = {}
    try:
        parsed = urlparse(url or "")
        pairs = parse_qsl(parsed.query or "", keep_blank_values=True)
        frag = parsed.fragment or ""
        frag_qs = ""
        if frag:
            if "?" in frag:
                # e.g. https://x/#section?btag=1 — take part after '?'
                frag_qs = frag.split("?", 1)[-1]
            elif "=" in frag:
                # e.g. https://x/#btag=1&subid=2
                frag_qs = frag
        if frag_qs:
            try:
                pairs += parse_qsl(frag_qs, keep_blank_values=True)
            except Exception:
                pass
        for k, v in pairs:
            out.setdefault(k, []).append(v)
        return out
    except Exception:
        return {}


def extract_params(url: str) -> dict:
    """Back-compat: first value per key (use extract_params_multi for duplicates)."""
    try:
        multi = extract_params_multi(url)
        return {k: v[0] if v else "" for k, v in multi.items()}
    except Exception:
        return {}


def compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


_UNKNOWN_SUFFIX = ("params-unknown: no tracked affiliate params at hop 0 "
                   "(clean link?) — cookies/S2S postback are out-of-band, "
                   "server-side tracking still possible")


def _append_unknown_suffix(chain: ChainResult) -> None:
    base = chain.broken_reason or ""
    if "params-unknown" in base:
        return
    suffix = _UNKNOWN_SUFFIX
    chain.broken_reason = f"{base} | {suffix}" if base else suffix


def audit_param_survival(chain: ChainResult, required_patterns: list[str]) -> ChainResult:
    """Hop-by-hop mutation audit. Flags silent stripping + key mutation.

    Semantics (FAIL-on-clean-links fix):
    - params_intact=True  — every hop0-tracked param survived (injections are informational).
    - params_intact=False — ONLY when a hop0-tracked param was stripped/mutated.
    - params_intact=None  — unknown: hop 0 carried ZERO tracked keys matching
      required_patterns (clean/plain review link). param_events holds any
      mid-chain injections (or []), broken_reason gets a params-unknown suffix.
    - Mid-chain injection: a required-pattern key FIRST appearing at hop>0
      yields ParamEvent(status="injected-mid-chain"); it does not flip
      params_intact to False.
    - Value mutation is checked hop-to-hop (not just vs hop 0), including
      duplicate-value-list changes. Fragments are parsed (see
      extract_params_multi). Cookies are out-of-band — noted, never inferred.
    """
    compiled = compile_patterns(required_patterns or [])
    if not chain.hops:
        chain.param_events = []
        chain.params_intact = None
        if required_patterns:
            _append_unknown_suffix(chain)
        return chain

    hop_multi: list[dict[str, list[str]]] = [extract_params_multi(h.url) for h in chain.hops]
    # back-compat first-value view stored on each hop
    for i, h in enumerate(chain.hops):
        try:
            h.params = {k: (v[0] if v else "") for k, v in hop_multi[i].items()}
        except Exception:
            h.params = {}

    initial = hop_multi[0] if hop_multi else {}
    tracked: dict[str, ParamEvent] = {}
    prev_values: dict[str, list[str]] = {}
    for k, vals in initial.items():
        if any(rx.search(k) for rx in compiled):
            tracked[k.lower()] = ParamEvent(param=k, first_seen_hop=0, last_seen_hop=0, status="survived")
            prev_values[k.lower()] = list(vals)

    # mid-chain injection registry: low_key -> event
    injected: dict[str, ParamEvent] = {}

    for idx in range(1, len(hop_multi)):
        cur = hop_multi[idx]
        current_keys = {k.lower(): k for k in cur.keys()}
        # 1) advance tracked params hop-to-hop
        for low_key, ev in tracked.items():
            if ev.status != "survived":
                continue
            if low_key in current_keys:
                orig = current_keys[low_key]
                new_vals = list(cur.get(orig, []))
                old_vals = prev_values.get(low_key, [])
                ev.last_seen_hop = idx
                if old_vals != new_vals:
                    # value mutation hop-to-hop (covers duplicates + rewrites)
                    ev.mutated_to = f"{orig}={','.join(new_vals)} (was {','.join(old_vals)})"
                    ev.status = "mutated"
                prev_values[low_key] = new_vals
            else:
                ev.stripped_at_hop = idx
                ev.last_seen_hop = idx - 1
                ev.status = "stripped"
        # 2) injection detection: required-pattern keys first seen at hop>0
        for k, vals in cur.items():
            low = k.lower()
            if low in tracked or low in injected:
                if low in injected and injected[low].status == "injected-mid-chain":
                    injected[low].last_seen_hop = idx
                continue
            if any(rx.search(k) for rx in compiled):
                injected[low] = ParamEvent(
                    param=k,
                    first_seen_hop=idx,
                    last_seen_hop=idx,
                    status="injected-mid-chain",
                    note="first appeared mid-chain (hop>0); informational — not a hop0 strip",
                )

    if not tracked:
        # Unknown, NOT False: clean link carries no affiliate params at hop 0.
        # Cookies / S2S postback are out-of-band — noted in broken_reason suffix.
        chain.param_events = sorted(injected.values(), key=lambda e: e.first_seen_hop)
        chain.params_intact = None
        if required_patterns:
            _append_unknown_suffix(chain)
        return chain

    # tracked non-empty: False only on real strip/mutate; injections informational
    chain.param_events = sorted(
        list(tracked.values()) + list(injected.values()),
        key=lambda e: (e.first_seen_hop, e.param.lower()),
    )
    bad = any(e.status in ("stripped", "mutated") for e in tracked.values())
    chain.params_intact = (not bad)
    return chain


def audit_network_path(chain: ChainResult, network_mappings: dict) -> ChainResult:
    """Verify expected affiliate network appears somewhere in the chain."""
    op = (chain.expected_operator or "").lower()
    if not op or op not in {k.lower(): v for k, v in network_mappings.items()}:
        chain.network_path_ok = True
        return chain
    lookup = {k.lower(): v for k, v in network_mappings.items()}
    expected_domains = [d.lower() for d in lookup.get(op, [])]
    chain_urls = " ".join(h.url.lower() for h in chain.hops) + " " + chain.final_url.lower()
    chain.network_path_ok = any(d in chain_urls for d in expected_domains)
    return chain
