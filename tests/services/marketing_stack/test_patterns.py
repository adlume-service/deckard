"""Unit tests for the shared GTM regex helpers."""

from deckard.services.marketing_stack.patterns import (
    extract_consent_mode_v2_from_container,
    extract_consent_mode_v2_from_html,
    extract_transport_url,
)


class TestExtractTransportUrl:
    def test_finds_quoted_https_value(self) -> None:
        body = 'something("transport_url": "https://collect.example.com/g/collect")'
        assert extract_transport_url(body) == "https://collect.example.com/g/collect"

    def test_finds_assignment_form(self) -> None:
        body = 'var x = { transport_url = "http://t.example.com/x" }'
        assert extract_transport_url(body) == "http://t.example.com/x"

    def test_returns_none_when_missing(self) -> None:
        assert extract_transport_url("nothing here") is None
        assert extract_transport_url("") is None


class TestExtractConsentModeV2FromHtml:
    def test_granted(self) -> None:
        html = "gtag('consent','default',{ad_storage:'granted',analytics_storage:'denied'})"
        assert extract_consent_mode_v2_from_html(html) == "granted"

    def test_denied(self) -> None:
        html = "gtag('consent', 'update', { ad_storage: 'denied' })"
        assert extract_consent_mode_v2_from_html(html) == "denied"

    def test_configured_without_explicit_ad_storage(self) -> None:
        html = "gtag('consent', 'default', { analytics_storage: 'granted' })"
        assert extract_consent_mode_v2_from_html(html) == "configured"

    def test_no_gtag_call(self) -> None:
        assert extract_consent_mode_v2_from_html("no consent here") is None


class TestExtractConsentModeV2FromContainer:
    def test_granted(self) -> None:
        body = '...{"ad_storage":"granted","analytics_storage":"denied"}...'
        assert extract_consent_mode_v2_from_container(body) == "granted"

    def test_denied(self) -> None:
        body = '..."ad_storage":"denied"...'
        assert extract_consent_mode_v2_from_container(body) == "denied"

    def test_missing(self) -> None:
        assert extract_consent_mode_v2_from_container("nothing") is None
        # Container form never returns the "configured" fallback.
        assert extract_consent_mode_v2_from_container("ad_storage:'???'") is None
