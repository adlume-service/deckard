"""Pure-function tests for the marketing-stack detector."""

from __future__ import annotations

from pathlib import Path

import pytest

from deckard.services.marketing_stack import (
    MARKETING_STACK_DETECTOR_VERSION,
    detect_marketing_stack,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "marketing_stack"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _detect(name: str, *, headers: dict[str, str] | None = None, url: str = "https://www.example.com/") -> dict:
    return detect_marketing_stack(html=_load(name), response_headers=headers or {}, seed_url=url)


# --- nothing-on-page ---------------------------------------------------


def test_empty_html_detects_nothing() -> None:
    result = _detect("empty.html")
    assert result["detector_version"] == MARKETING_STACK_DETECTOR_VERSION
    assert isinstance(result["detected_at"], str)
    assert result["vendors"] == {}
    assert result["server_side_hints"] == []


# --- per-vendor presence ----------------------------------------------


def test_gtm_detected_with_id() -> None:
    result = _detect("gtm.html")
    gtm = result["vendors"]["gtm"]
    assert "GTM-ABC123" in gtm["ids"]
    assert gtm["extras"]["container_fetch_status"] == "pending"
    assert gtm["extras"]["container_tags"] == []


def test_ga4_detected() -> None:
    result = _detect("ga4.html")
    ga4 = result["vendors"]["ga4"]
    assert "G-XYZ12345" in ga4["ids"]
    assert ga4["extras"]["load_context"] == "direct"


def test_universal_analytics_detected() -> None:
    result = _detect("universal_analytics.html")
    ua = result["vendors"]["universal_analytics"]
    assert "UA-12345-6" in ua["ids"]


def test_meta_pixel_detected_with_advanced_matching() -> None:
    result = _detect("meta_pixel.html")
    meta = result["vendors"]["meta_pixel"]
    assert "1234567890123456" in meta["ids"]
    assert meta["extras"]["advanced_matching"] is True
    assert meta["extras"]["capi_hint"] is False


def test_multi_vendor_fixture_covers_every_catalogue_key() -> None:
    """The combined fixture should detect every vendor in the catalogue
    *plus* the magento/bigcommerce/woocommerce variants from their own files."""
    from deckard.services.marketing_stack.catalogue import CATALOGUE

    combined_vendors: set[str] = set()
    for fixture in (
        "multi_vendor.html",
        "magento.html",
        "bigcommerce.html",
        "woocommerce.html",
        "universal_analytics.html",
    ):
        result = _detect(fixture)
        combined_vendors.update(result["vendors"].keys())

    expected = {v.key for v in CATALOGUE}
    missing = expected - combined_vendors
    assert not missing, f"Catalogue keys with no detection in fixtures: {missing}"


def test_multi_vendor_id_extraction() -> None:
    result = _detect("multi_vendor.html")
    vendors = result["vendors"]
    assert "GTM-PRIMARY" in vendors["gtm"]["ids"]
    assert "G-AAAAA1" in vendors["ga4"]["ids"]
    assert "9876543210" in vendors["meta_pixel"]["ids"]
    assert "111222" in vendors["linkedin_insight"]["ids"]
    assert "ABCDEF123456" in vendors["tiktok_pixel"]["ids"]
    assert "2612345678" in vendors["pinterest_tag"]["ids"]
    assert "t2_abc123" in vendors["reddit_pixel"]["ids"]
    assert "12345678" in vendors["hubspot"]["ids"]
    assert vendors["hubspot"]["extras"]["portal_id"] == "12345678"
    assert "KLAVCO" in vendors["klaviyo"]["ids"]
    assert vendors["klaviyo"]["extras"]["public_account_id"] == "KLAVCO"
    assert "AW-9876543210" in vendors["google_ads"]["ids"]
    assert "9876543" in vendors["optimizely"]["ids"]


# --- multiple IDs ------------------------------------------------------


def test_multiple_gtm_ids() -> None:
    html = """
    <script src="https://www.googletagmanager.com/gtm.js?id=GTM-AAA111"></script>
    <script src="https://www.googletagmanager.com/gtm.js?id=GTM-BBB222"></script>
    """
    result = detect_marketing_stack(html=html, response_headers={}, seed_url="https://example.com/")
    gtm = result["vendors"]["gtm"]
    assert "GTM-AAA111" in gtm["ids"]
    assert "GTM-BBB222" in gtm["ids"]
    assert gtm["extras"]["id_count"] == 2


# --- server-side hint calibration --------------------------------------


def test_gtm_first_party_transport_url_high_confidence() -> None:
    result = _detect("gtm_first_party.html", url="https://www.brand.com/")
    gtm = result["vendors"]["gtm"]
    assert gtm["extras"]["transport_url"] == "https://sgtm.brand.com"
    assert gtm["extras"]["consent_mode_v2"] == "denied"
    assert gtm["extras"]["first_party_mode"] is True
    hint = next(h for h in result["server_side_hints"] if h["signal"] == "gtm_transport_url_first_party")
    assert hint["confidence"] == "high"


def test_sgtm_vendor_script_reference_high_confidence() -> None:
    html = '<script src="https://gtm.stape.io/proxy.js"></script>'
    result = detect_marketing_stack(html=html, response_headers={}, seed_url="https://example.com/")
    hint = next(h for h in result["server_side_hints"] if h["signal"] == "sgtm_vendor_script_reference")
    assert hint["confidence"] == "high"
    assert "stape.io" in hint["evidence"]


def test_meta_capi_cookies_without_pixel_medium_confidence() -> None:
    headers = {"set-cookie": "_fbp=fb.1.123.456; Path=/, _fbc=fb.1.789.abc; Path=/"}
    result = detect_marketing_stack(
        html="<html><body></body></html>", response_headers=headers, seed_url="https://x.com/"
    )
    hint = next(h for h in result["server_side_hints"] if h["signal"] == "meta_capi_cookies_without_pixel")
    assert hint["confidence"] == "medium"
    assert "_fbp" in hint["evidence"]


def test_ga4_without_gtm_low_confidence() -> None:
    result = _detect("ga4.html")
    hint = next(h for h in result["server_side_hints"] if h["signal"] == "ga4_without_gtm")
    assert hint["confidence"] == "low"


def test_transport_url_to_unrelated_host_only_medium() -> None:
    html = """
    <script src="https://www.googletagmanager.com/gtm.js?id=GTM-XYZ"></script>
    <script>gtag('config','G-Z', {transport_url: 'https://collect.unrelated.io'});</script>
    """
    result = detect_marketing_stack(html=html, response_headers={}, seed_url="https://www.brand.com/")
    hint = next(h for h in result["server_side_hints"] if h["signal"].startswith("gtm_transport_url"))
    assert hint["confidence"] == "medium"


# --- load_context semantics --------------------------------------------


def test_direct_load_context_when_script_on_page() -> None:
    result = _detect("meta_pixel.html")
    assert result["vendors"]["meta_pixel"]["extras"]["load_context"] == "direct"


@pytest.mark.parametrize("vendor_key", ["gtm", "ga4", "meta_pixel"])
def test_evidence_present_for_detected_vendor(vendor_key: str) -> None:
    result = _detect("multi_vendor.html")
    if vendor_key not in result["vendors"]:
        pytest.skip(f"{vendor_key} not in fixture")
    evidence = result["vendors"][vendor_key]["evidence"]
    assert evidence
    assert evidence[0]["source"] == "html"
    assert evidence[0]["snippet"]


# --- detected_at is an ISO string, not a datetime ----------------------


def test_detected_at_is_iso_string() -> None:
    result = _detect("empty.html")
    # parseable as ISO 8601
    from datetime import datetime

    parsed = datetime.fromisoformat(result["detected_at"])
    assert parsed.tzinfo is not None
