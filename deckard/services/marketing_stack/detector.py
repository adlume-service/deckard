"""Static marketing-stack detector.

Pure function — no I/O, no DB, no network. Walks the catalogue against the
raw HTML and response headers from the seed page, produces a structured
report of detected vendors plus server-side tracking hints.

The GTM container is *not* fetched here; if a GTM container ID is found, its
``extras.container_fetch_status`` is set to ``"pending"`` and the orchestrator
(``enrich_with_gtm_container``) fills it in.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from deckard.services.marketing_stack.catalogue import CATALOGUE, VendorSignature
from deckard.services.marketing_stack.patterns import (
    extract_consent_mode_v2_from_html,
    extract_transport_url,
)

MARKETING_STACK_DETECTOR_VERSION = "1"

# sGTM vendor domains we recognise as a "high" confidence server-side signal.
SGTM_VENDOR_DOMAINS: tuple[str, ...] = ("stape.io", "addingwell.com")


def detect_marketing_stack(
    html: str,
    response_headers: dict[str, str],
    seed_url: str,
) -> dict[str, Any]:
    html = html or ""
    headers = _lower_headers(response_headers or {})
    seed_host = _host_of(seed_url)

    vendors: dict[str, dict[str, Any]] = {}
    for signature in CATALOGUE:
        vendor = _detect_vendor(signature, html)
        if vendor is not None:
            vendors[signature.key] = vendor

    # GTM-specific extras
    if "gtm" in vendors:
        gtm_extras = vendors["gtm"]["extras"]
        transport_url = extract_transport_url(html)
        consent_mode_v2 = extract_consent_mode_v2_from_html(html)
        if transport_url:
            gtm_extras["transport_url"] = transport_url
        if consent_mode_v2:
            gtm_extras["consent_mode_v2"] = consent_mode_v2
        gtm_extras["container_fetch_status"] = "pending"
        gtm_extras["container_tags"] = []

    # Cookies set via Set-Cookie header (crawl4ai doesn't surface client cookies)
    set_cookies = _parse_set_cookie(headers.get("set-cookie"))
    has_fbp = "_fbp" in set_cookies
    has_fbc = "_fbc" in set_cookies

    if "meta_pixel" in vendors:
        vendors["meta_pixel"]["extras"].setdefault("capi_hint", False)
        # If fbp/fbc are server-set but no script reference, that's a capi hint.
        # (script-present capi hint already handled by extras_extractor)
    elif has_fbp or has_fbc:
        # Cookies present without a Meta Pixel script — likely server-side / CAPI usage.
        # We *don't* fabricate a meta_pixel detection; we capture this as a server-side hint below.
        pass

    server_side_hints = _build_server_side_hints(
        html=html,
        vendors=vendors,
        seed_host=seed_host,
        has_fbp=has_fbp,
        has_fbc=has_fbc,
    )

    return {
        "detector_version": MARKETING_STACK_DETECTOR_VERSION,
        "detected_at": datetime.now(UTC).isoformat(),
        "vendors": vendors,
        "server_side_hints": server_side_hints,
    }


# --- vendor detection ---------------------------------------------------


def _detect_vendor(signature: VendorSignature, html: str) -> dict[str, Any] | None:
    matched_snippets: list[str] = []
    for pattern in signature.script_patterns:
        match = pattern.search(html)
        if match is not None:
            matched_snippets.append(_snippet_around(html, match))

    if not matched_snippets:
        return None

    ids: list[str] = []
    if signature.id_pattern is not None:
        for raw_match in signature.id_pattern.finditer(html):
            extracted = raw_match.group(1) if raw_match.groups() else raw_match.group(0)
            if extracted and extracted not in ids:
                ids.append(extracted)

    # `first_party_mode` is tri-state: True means positive evidence (e.g. transport_url is a
    # first-party subdomain), False means positive evidence to the contrary (e.g. transport_url
    # is on a known sGTM vendor domain), None means unknown / not enough signal. Downstream
    # consumers must not treat None as "no".
    # `source` records where the vendor entry was first observed: "html" for the seed page,
    # "gtm_container" for vendors only found inside the parsed GTM container body.
    extras: dict[str, Any] = {
        "id_count": len(ids),
        "load_context": "direct",
        "first_party_mode": None,
        "source": "html",
    }
    if signature.extras_extractor is not None:
        extras.update(signature.extras_extractor(html))

    evidence = [{"source": "html", "snippet": snippet} for snippet in matched_snippets[:3]]

    return {
        "detected": True,
        "ids": ids,
        "evidence": evidence,
        "extras": extras,
    }


def _snippet_around(html: str, match: re.Match[str], radius: int = 80) -> str:
    start = max(0, match.start() - radius)
    end = min(len(html), match.end() + radius)
    snippet = html[start:end].strip()
    snippet = re.sub(r"\s+", " ", snippet)
    return snippet[:240]


# --- server-side hints --------------------------------------------------


def _build_server_side_hints(
    *,
    html: str,
    vendors: dict[str, dict[str, Any]],
    seed_host: str | None,
    has_fbp: bool,
    has_fbc: bool,
) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []

    # GTM transport_url pointing to a non-Google subdomain of the requested host
    gtm = vendors.get("gtm")
    transport_url = gtm["extras"].get("transport_url") if gtm else None
    if transport_url:
        transport_host = _host_of(transport_url)
        if transport_host and seed_host and _is_first_party_subdomain(transport_host, seed_host):
            hints.append(
                {
                    "signal": "gtm_transport_url_first_party",
                    "confidence": "high",
                    "evidence": (
                        f"GTM transport_url={transport_url} — non-Google subdomain of requested host {seed_host}"
                    ),
                }
            )
            # first_party_mode flips on for GTM when transport_url is a first-party subdomain.
            gtm["extras"]["first_party_mode"] = True
        elif transport_host and any(transport_host.endswith(v) for v in SGTM_VENDOR_DOMAINS):
            hints.append(
                {
                    "signal": "gtm_transport_url_vendor_sgtm",
                    "confidence": "high",
                    "evidence": f"GTM transport_url={transport_url} — known sGTM vendor domain",
                }
            )
            gtm["extras"]["first_party_mode"] = False
        elif transport_host:
            hints.append(
                {
                    "signal": "gtm_transport_url_present",
                    "confidence": "medium",
                    "evidence": f"GTM transport_url={transport_url}",
                }
            )

    # Direct script ref to a known sGTM vendor domain
    for domain in SGTM_VENDOR_DOMAINS:
        match = re.search(rf"https?://[A-Za-z0-9.-]+\.{re.escape(domain)}/[^'\"\s]*", html)
        if match is not None:
            hints.append(
                {
                    "signal": "sgtm_vendor_script_reference",
                    "confidence": "high",
                    "evidence": f"Script reference to {domain}: {match.group(0)[:120]}",
                }
            )

    # Meta CAPI hint: fbp/fbc set server-side but no Meta Pixel script on page
    if (has_fbp or has_fbc) and "meta_pixel" not in vendors:
        cookies_seen = ", ".join(c for c in ("_fbp", "_fbc") if (c == "_fbp" and has_fbp) or (c == "_fbc" and has_fbc))
        hints.append(
            {
                "signal": "meta_capi_cookies_without_pixel",
                "confidence": "medium",
                "evidence": f"Set-Cookie includes {cookies_seen} but no Meta Pixel script detected on page",
            }
        )

    # GA4 detected via gtag but no GTM container — suggests direct sGTM proxying may be in use
    if "ga4" in vendors and "gtm" not in vendors:
        hints.append(
            {
                "signal": "ga4_without_gtm",
                "confidence": "low",
                "evidence": "GA4 measurement ID present without a GTM container on page",
            }
        )

    return hints


# --- helpers ------------------------------------------------------------


def _lower_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def _host_of(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        return (parsed.hostname or "").lower() or None
    except ValueError, AttributeError:
        return None


def _is_first_party_subdomain(candidate_host: str, seed_host: str) -> bool:
    """``candidate`` is a non-Google subdomain of ``seed_host``'s registered domain.

    We use a coarse approximation: candidate ends with the rightmost two labels of seed_host
    and is not on a Google-owned domain.
    """
    google_domains = ("google.com", "googletagmanager.com", "google-analytics.com", "googleapis.com")
    if any(candidate_host.endswith(g) for g in google_domains):
        return False
    seed_labels = seed_host.split(".")
    if len(seed_labels) < 2:
        return False
    registered = ".".join(seed_labels[-2:])
    return candidate_host == registered or candidate_host.endswith("." + registered)


def _parse_set_cookie(set_cookie_header: str | None) -> set[str]:
    """Extract cookie names from a Set-Cookie header value.

    The HTTP spec allows multiple Set-Cookie headers but httpx/crawl4ai collapse
    them with commas. We split on commas that precede a ``name=value`` pair while
    being careful about ``Expires=...`` values which legally contain a comma.
    """
    if not set_cookie_header:
        return set()
    names: set[str] = set()
    # Split on comma followed by whitespace and a likely cookie-name token.
    parts = re.split(r",\s*(?=[A-Za-z_][A-Za-z0-9_-]*=)", set_cookie_header)
    for part in parts:
        first = part.split(";", 1)[0]
        if "=" in first:
            name = first.split("=", 1)[0].strip()
            if name:
                names.add(name)
    return names
