"""GTM container fetch + parse.

Two-pass split:

- ``parse_gtm_container`` is a pure function over a container body string.
- ``fetch_gtm_container`` is the I/O entry: single retry, timeout-bounded,
  returns ``None`` on any failure rather than raising.
- ``enrich_with_gtm_container`` is the orchestrator-side entrypoint that
  merges parsed container data into a detector output dict.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from deckard.config import get_settings
from deckard.services.marketing_stack.catalogue import CATALOGUE_BY_KEY

logger = logging.getLogger(__name__)


CONTAINER_URL_TEMPLATE = "https://www.googletagmanager.com/gtm.js?id={container_id}"


async def fetch_gtm_container(container_id: str, *, timeout_s: float | None = None) -> str | None:
    """Fetch the GTM container body for ``container_id``.

    Performs at most one retry. Returns the body on success, ``None`` on any
    failure (timeout, non-2xx status, transport error). Never raises.
    """
    if timeout_s is None:
        timeout_s = get_settings().marketing_stack_gtm_fetch_timeout_seconds

    url = CONTAINER_URL_TEMPLATE.format(container_id=container_id)

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for attempt in (1, 2):
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                logger.info(
                    "GTM container fetch attempt %d for %s failed: %s",
                    attempt,
                    container_id,
                    exc,
                )
                continue
            if response.status_code == 200 and response.text:
                return response.text
            logger.info(
                "GTM container fetch attempt %d for %s returned status %d",
                attempt,
                container_id,
                response.status_code,
            )
    return None


def parse_gtm_container(body: str) -> dict[str, Any]:
    """Pure parse of a GTM container body. Returns ``{tags, transport_url, consent_mode_v2}``.

    GTM container bodies are minified JS. We use coarse regex matching against
    the well-known tag-template substrings; this is intentionally a
    static-pattern approach matching the rest of v1.
    """
    body = body or ""
    tags: list[dict[str, str]] = []

    # GA4
    for match in re.finditer(r"['\"](G-[A-Z0-9]{6,})['\"]", body):
        _add_tag(tags, type_="ga4", id_=match.group(1))
    # Universal Analytics
    for match in re.finditer(r"['\"](UA-\d+-\d+)['\"]", body):
        _add_tag(tags, type_="universal_analytics", id_=match.group(1))
    # Meta Pixel — pattern is fbq init or fbevents reference; ID is a 13-16 digit number
    if re.search(r"connect\.facebook\.net/[a-z_]+/fbevents\.js", body, re.IGNORECASE) or re.search(
        r"fbq\(['\"]init['\"]", body, re.IGNORECASE
    ):
        id_match = re.search(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d+)['\"]", body, re.IGNORECASE)
        _add_tag(tags, type_="meta_pixel", id_=id_match.group(1) if id_match else "")
    # Google Ads
    for match in re.finditer(r"['\"](AW-\d+)['\"]", body):
        _add_tag(tags, type_="google_ads", id_=match.group(1))
    # Floodlight
    for match in re.finditer(r"['\"](DC-\d+)['\"]", body):
        _add_tag(tags, type_="floodlight", id_=match.group(1))
    # LinkedIn
    for match in re.finditer(r"_linkedin_partner_id\s*=\s*['\"]?(\d+)", body):
        _add_tag(tags, type_="linkedin_insight", id_=match.group(1))
    # TikTok
    for match in re.finditer(r"ttq\.load\(\s*['\"]([A-Z0-9]+)['\"]", body):
        _add_tag(tags, type_="tiktok_pixel", id_=match.group(1))
    # Pinterest
    for match in re.finditer(r"pintrk\(\s*['\"]load['\"]\s*,\s*['\"](\d+)['\"]", body):
        _add_tag(tags, type_="pinterest_tag", id_=match.group(1))

    transport_url: str | None = None
    transport_match = re.search(
        r"['\"]?transport_url['\"]?\s*[:=]\s*['\"](https?://[^'\"]+)['\"]",
        body,
        re.IGNORECASE,
    )
    if transport_match is not None:
        transport_url = transport_match.group(1)

    consent_mode_v2: str | None = None
    consent_match = re.search(
        r"['\"]ad_storage['\"]\s*:\s*['\"](granted|denied)['\"]",
        body,
        re.IGNORECASE,
    )
    if consent_match is not None:
        consent_mode_v2 = consent_match.group(1).lower()

    return {
        "tags": tags,
        "transport_url": transport_url,
        "consent_mode_v2": consent_mode_v2,
    }


def _add_tag(tags: list[dict[str, str]], *, type_: str, id_: str) -> None:
    entry = {"type": type_, "id": id_}
    if entry not in tags:
        tags.append(entry)


async def enrich_with_gtm_container(stack: dict[str, Any]) -> dict[str, Any]:
    """Merge GTM container parse output into a detector report.

    No-op when GTM was not detected. On any fetch/parse failure, sets
    ``gtm.extras.container_fetch_status = "failed"`` and leaves the rest of
    the report alone.
    """
    vendors = stack.get("vendors") or {}
    gtm = vendors.get("gtm")
    if not gtm or not gtm.get("ids"):
        return stack

    container_id = gtm["ids"][0]
    body = await fetch_gtm_container(container_id)
    if body is None:
        gtm["extras"]["container_fetch_status"] = "failed"
        return stack

    parsed = parse_gtm_container(body)
    gtm["extras"]["container_fetch_status"] = "ok"
    gtm["extras"]["container_tags"] = parsed["tags"]

    # Prefer container values when the seed HTML didn't surface them.
    if parsed["transport_url"] and not gtm["extras"].get("transport_url"):
        gtm["extras"]["transport_url"] = parsed["transport_url"]
    if parsed["consent_mode_v2"] and not gtm["extras"].get("consent_mode_v2"):
        gtm["extras"]["consent_mode_v2"] = parsed["consent_mode_v2"]

    # Refine load_context: any vendor whose tag appeared in the container body
    # gets bumped from "direct" -> "both" (or set to "gtm" if not in HTML).
    container_types = {tag["type"] for tag in parsed["tags"]}
    for vendor_key, vendor in vendors.items():
        if vendor_key == "gtm":
            continue
        if vendor_key not in container_types:
            continue
        current = vendor["extras"].get("load_context")
        if current == "direct":
            vendor["extras"]["load_context"] = "both"
        elif current is None:
            vendor["extras"]["load_context"] = "gtm"

    # Vendors that appear only inside the container (not in vendors map yet)
    # — synthesise minimal entries from container_tags.
    for tag in parsed["tags"]:
        vtype = tag["type"]
        if vtype in vendors or vtype == "gtm":
            continue
        # Only synthesise for vendors we know the shape of (key matches catalogue).
        if vtype not in CATALOGUE_BY_KEY:
            continue
        ids = [tag["id"]] if tag.get("id") else []
        vendors[vtype] = {
            "detected": True,
            "ids": ids,
            "evidence": [{"source": "gtm_container", "snippet": f"Tag found inside GTM container {container_id}"}],
            "extras": {
                "id_count": len(ids),
                "load_context": "gtm",
                "first_party_mode": None,
                "source": "gtm_container",
            },
        }

    return stack
