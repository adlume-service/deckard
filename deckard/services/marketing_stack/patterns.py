"""Shared regex helpers for GTM signals.

The same two patterns — ``transport_url`` and consent-mode-v2 ``ad_storage`` —
appear in both the on-page HTML (detector.py) and the fetched GTM container
body (gtm.py). Live in one place.
"""

import re

_TRANSPORT_URL_RE = re.compile(
    r"['\"]?transport_url['\"]?\s*[:=]\s*['\"](https?://[^'\"]+)['\"]",
    re.IGNORECASE,
)

# On-page form: gtag('consent', 'default'|'update', { ad_storage: 'granted'|'denied', ... })
_GTAG_CONSENT_RE = re.compile(
    r"gtag\(\s*['\"]consent['\"]\s*,\s*['\"](?:default|update)['\"]\s*,\s*\{([^}]+)\}",
    re.IGNORECASE,
)
_AD_STORAGE_GRANTED_RE = re.compile(r"ad_storage['\"]?\s*:\s*['\"]granted['\"]", re.IGNORECASE)
_AD_STORAGE_DENIED_RE = re.compile(r"ad_storage['\"]?\s*:\s*['\"]denied['\"]", re.IGNORECASE)

# Container body form: bare "ad_storage":"granted"|"denied" outside any gtag call
_AD_STORAGE_LITERAL_RE = re.compile(
    r"['\"]ad_storage['\"]\s*:\s*['\"](granted|denied)['\"]",
    re.IGNORECASE,
)


def extract_transport_url(text: str) -> str | None:
    match = _TRANSPORT_URL_RE.search(text)
    return match.group(1) if match else None


def extract_consent_mode_v2_from_html(html: str) -> str | None:
    """On-page consent state: ``granted`` | ``denied`` | ``configured`` | ``None``.

    Heuristic: locate a ``gtag('consent', 'default'|'update', {...})`` call and
    inspect its ``ad_storage`` value. If we find the gtag call but neither
    granted/denied within it, the page is at least configured for consent v2.
    """
    match = _GTAG_CONSENT_RE.search(html)
    if match is None:
        return None
    body = match.group(1)
    if _AD_STORAGE_GRANTED_RE.search(body):
        return "granted"
    if _AD_STORAGE_DENIED_RE.search(body):
        return "denied"
    return "configured"


def extract_consent_mode_v2_from_container(body: str) -> str | None:
    """Container-body consent state: ``granted`` | ``denied`` | ``None``.

    Container bodies don't carry a gtag call — they carry the literal
    ``"ad_storage":"granted"|"denied"`` mapping. No ``configured`` fallback
    because absence of a value is just absence.
    """
    match = _AD_STORAGE_LITERAL_RE.search(body)
    return match.group(1).lower() if match else None
