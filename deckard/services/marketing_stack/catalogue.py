"""Marketing stack vendor catalogue.

Single source of truth for what we look for. Each entry defines:
- The output key written into ``marketing_stack.vendors``.
- The category for downstream presentation grouping.
- Regex patterns matched against raw HTML to decide "detected".
- An optional ID pattern that pulls a public identifier from a match.
- An optional ``extras_extractor`` that derives vendor-specific extras
  (e.g. Meta advanced matching, HubSpot portal id) from the HTML.

Adding a new vendor in v2 is one entry here plus a fixture.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

VendorCategory = Literal[
    "analytics",
    "pixel",
    "product_analytics",
    "map_crm",
    "consent",
    "ab_testing",
    "reviews",
    "ecom_platform",
    "ads",
    "chat",
    "tag_manager",
]


@dataclass(frozen=True)
class VendorSignature:
    key: str
    category: VendorCategory
    script_patterns: tuple[re.Pattern[str], ...]
    id_pattern: re.Pattern[str] | None = None
    extras_extractor: Callable[[str], dict[str, Any]] | None = field(default=None)


def _re(pattern: str, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    return re.compile(pattern, flags)


# --- vendor-specific extras extractors ----------------------------------


def _meta_pixel_extras(html: str) -> dict[str, Any]:
    """Meta Pixel: advanced matching when init has a second arg, CAPI hint
    when fbq is loaded server-side via a known proxy path."""
    extras: dict[str, Any] = {}
    init_match = re.search(
        r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d+)['\"](\s*,\s*\{[^}]+\})?",
        html,
        re.IGNORECASE,
    )
    extras["advanced_matching"] = bool(init_match and init_match.group(2))
    # crude CAPI hint: site references a Meta Conversions API proxy endpoint
    extras["capi_hint"] = bool(re.search(r"/(?:meta-capi|fb-capi|capi/meta)", html, re.IGNORECASE))
    return extras


def _hubspot_extras(html: str) -> dict[str, Any]:
    match = re.search(r"js\.hs-scripts\.com/(\d+)\.js", html, re.IGNORECASE)
    return {"portal_id": match.group(1)} if match else {}


def _klaviyo_extras(html: str) -> dict[str, Any]:
    match = re.search(r"static\.klaviyo\.com/onsite/js/([A-Za-z0-9]+)/klaviyo\.js", html, re.IGNORECASE)
    if not match:
        match = re.search(r"klaviyo\.js\?company_id=([A-Za-z0-9]+)", html, re.IGNORECASE)
    return {"public_account_id": match.group(1)} if match else {}


# --- the catalogue ------------------------------------------------------

CATALOGUE: tuple[VendorSignature, ...] = (
    # Tag managers
    VendorSignature(
        key="gtm",
        category="tag_manager",
        script_patterns=(
            _re(r"googletagmanager\.com/gtm\.js\?id=GTM-[A-Z0-9]+"),
            _re(r"['\"]GTM-[A-Z0-9]+['\"]"),
        ),
        id_pattern=_re(r"GTM-[A-Z0-9]+"),
    ),
    # Analytics
    VendorSignature(
        key="ga4",
        category="analytics",
        script_patterns=(
            _re(r"googletagmanager\.com/gtag/js\?id=G-[A-Z0-9]+"),
            _re(r"gtag\(\s*['\"]config['\"]\s*,\s*['\"]G-[A-Z0-9]+['\"]"),
            _re(r"['\"]G-[A-Z0-9]{6,}['\"]"),
        ),
        id_pattern=_re(r"G-[A-Z0-9]{6,}"),
    ),
    VendorSignature(
        key="universal_analytics",
        category="analytics",
        script_patterns=(
            _re(r"google-analytics\.com/(?:analytics|ga)\.js"),
            _re(r"ga\(\s*['\"]create['\"]\s*,\s*['\"]UA-\d+-\d+['\"]"),
            _re(r"['\"]UA-\d+-\d+['\"]"),
        ),
        id_pattern=_re(r"UA-\d+-\d+"),
    ),
    # Ad pixels
    VendorSignature(
        key="meta_pixel",
        category="pixel",
        script_patterns=(
            _re(r"connect\.facebook\.net/[a-z_]+/fbevents\.js"),
            _re(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"]\d+['\"]"),
        ),
        id_pattern=_re(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d+)['\"]"),
        extras_extractor=_meta_pixel_extras,
    ),
    VendorSignature(
        key="linkedin_insight",
        category="pixel",
        script_patterns=(
            _re(r"snap\.licdn\.com/li\.lms-analytics/insight\.min\.js"),
            _re(r"_linkedin_partner_id\s*=\s*['\"]?\d+"),
        ),
        id_pattern=_re(r"_linkedin_partner_id\s*=\s*['\"]?(\d+)"),
    ),
    VendorSignature(
        key="tiktok_pixel",
        category="pixel",
        script_patterns=(
            _re(r"analytics\.tiktok\.com/i18n/pixel"),
            _re(r"ttq\.load\(\s*['\"][A-Z0-9]+['\"]"),
        ),
        id_pattern=_re(r"ttq\.load\(\s*['\"]([A-Z0-9]+)['\"]"),
    ),
    VendorSignature(
        key="pinterest_tag",
        category="pixel",
        script_patterns=(
            _re(r"s\.pinimg\.com/ct/core\.js"),
            _re(r"pintrk\(\s*['\"]load['\"]\s*,\s*['\"]\d+['\"]"),
        ),
        id_pattern=_re(r"pintrk\(\s*['\"]load['\"]\s*,\s*['\"](\d+)['\"]"),
    ),
    VendorSignature(
        key="reddit_pixel",
        category="pixel",
        script_patterns=(
            _re(r"www\.redditstatic\.com/ads/pixel\.js"),
            _re(r"rdt\(\s*['\"]init['\"]\s*,\s*['\"][a-z0-9_]+['\"]"),
        ),
        id_pattern=_re(r"rdt\(\s*['\"]init['\"]\s*,\s*['\"]([a-z0-9_]+)['\"]"),
    ),
    VendorSignature(
        key="snap_pixel",
        category="pixel",
        script_patterns=(
            _re(r"sc-static\.net/scevent\.min\.js"),
            _re(r"snaptr\(\s*['\"]init['\"]\s*,\s*['\"][a-f0-9-]+['\"]"),
        ),
        id_pattern=_re(r"snaptr\(\s*['\"]init['\"]\s*,\s*['\"]([a-f0-9-]+)['\"]"),
    ),
    # Ads conversion / paid
    VendorSignature(
        key="google_ads",
        category="ads",
        script_patterns=(
            _re(r"gtag\(\s*['\"]config['\"]\s*,\s*['\"]AW-\d+['\"]"),
            _re(r"['\"]AW-\d+(?:/[A-Za-z0-9_-]+)?['\"]"),
            _re(r"googleadservices\.com/pagead/conversion"),
        ),
        id_pattern=_re(r"AW-\d+"),
    ),
    VendorSignature(
        key="microsoft_uet",
        category="ads",
        script_patterns=(
            _re(r"bat\.bing\.com/bat\.js"),
            _re(r"uetq\s*=\s*window\.uetq"),
        ),
        id_pattern=_re(r"['\"]ti['\"]\s*:\s*['\"](\d+)['\"]"),
    ),
    # Product analytics
    VendorSignature(
        key="hotjar",
        category="product_analytics",
        script_patterns=(
            _re(r"static\.hotjar\.com/c/hotjar-\d+\.js"),
            _re(r"hjid\s*:\s*\d+"),
        ),
        id_pattern=_re(r"hjid\s*:\s*(\d+)"),
    ),
    VendorSignature(
        key="segment",
        category="product_analytics",
        script_patterns=(
            _re(r"cdn\.segment\.com/analytics\.js/v1/[A-Za-z0-9]+/analytics\.min\.js"),
            _re(r"analytics\.load\(\s*['\"][A-Za-z0-9]+['\"]"),
        ),
        id_pattern=_re(r"analytics\.load\(\s*['\"]([A-Za-z0-9]+)['\"]"),
    ),
    VendorSignature(
        key="mixpanel",
        category="product_analytics",
        script_patterns=(
            _re(r"cdn\.mxpnl\.com/libs/mixpanel-\d"),
            _re(r"mixpanel\.init\(\s*['\"][a-f0-9]+['\"]"),
        ),
        id_pattern=_re(r"mixpanel\.init\(\s*['\"]([a-f0-9]+)['\"]"),
    ),
    VendorSignature(
        key="amplitude",
        category="product_analytics",
        script_patterns=(
            _re(r"cdn\.amplitude\.com/libs/amplitude"),
            _re(r"amplitude\.(?:getInstance\(\)\.)?init\(\s*['\"][A-Za-z0-9]+['\"]"),
        ),
        id_pattern=_re(r"amplitude\.(?:getInstance\(\)\.)?init\(\s*['\"]([A-Za-z0-9]+)['\"]"),
    ),
    # MAP / CRM
    VendorSignature(
        key="hubspot",
        category="map_crm",
        script_patterns=(
            _re(r"js\.hs-scripts\.com/\d+\.js"),
            _re(r"js\.hubspot\.com/"),
        ),
        id_pattern=_re(r"js\.hs-scripts\.com/(\d+)\.js"),
        extras_extractor=_hubspot_extras,
    ),
    VendorSignature(
        key="klaviyo",
        category="map_crm",
        script_patterns=(
            _re(r"static\.klaviyo\.com/onsite/js/[A-Za-z0-9]+/klaviyo\.js"),
            _re(r"a\.klaviyo\.com/media/js/onsite/"),
            _re(r"klaviyo\.js\?company_id=[A-Za-z0-9]+"),
        ),
        id_pattern=_re(r"(?:static\.klaviyo\.com/onsite/js/|klaviyo\.js\?company_id=)([A-Za-z0-9]+)"),
        extras_extractor=_klaviyo_extras,
    ),
    VendorSignature(
        key="marketo",
        category="map_crm",
        script_patterns=(
            _re(r"//\d+-[a-z]+-\d+\.mktoresp\.com/"),
            _re(r"munchkin\.init\(\s*['\"][\dA-Z-]+['\"]"),
        ),
        id_pattern=_re(r"munchkin\.init\(\s*['\"]([\dA-Z-]+)['\"]"),
    ),
    VendorSignature(
        key="pardot",
        category="map_crm",
        script_patterns=(
            _re(r"pi\.pardot\.com/"),
            _re(r"piAId\s*=\s*['\"]?\d+"),
        ),
        id_pattern=_re(r"piAId\s*=\s*['\"]?(\d+)"),
    ),
    VendorSignature(
        key="braze",
        category="map_crm",
        script_patterns=(
            _re(r"js\.appboycdn\.com/web-sdk/"),
            _re(r"appboy\.initialize\(\s*['\"][a-f0-9-]+['\"]"),
            _re(r"braze\.initialize\(\s*['\"][a-f0-9-]+['\"]"),
        ),
        id_pattern=_re(r"(?:appboy|braze)\.initialize\(\s*['\"]([a-f0-9-]+)['\"]"),
    ),
    VendorSignature(
        key="customer_io",
        category="map_crm",
        script_patterns=(
            _re(r"assets\.customer\.io/assets/track\.js"),
            _re(r"_cio\.siteid\s*=\s*['\"][a-f0-9]+['\"]"),
        ),
        id_pattern=_re(r"_cio\.siteid\s*=\s*['\"]([a-f0-9]+)['\"]"),
    ),
    VendorSignature(
        key="intercom",
        category="chat",
        script_patterns=(
            _re(r"widget\.intercom\.io/widget/[a-z0-9]+"),
            _re(r"intercomSettings\s*=\s*\{[^}]*app_id"),
        ),
        id_pattern=_re(r"widget\.intercom\.io/widget/([a-z0-9]+)"),
    ),
    # Consent
    VendorSignature(
        key="onetrust",
        category="consent",
        script_patterns=(
            _re(r"cdn\.cookielaw\.org/(?:consent|scripttemplates)/"),
            _re(r"cdn-(?:apac|ukwest)\.onetrust\.com/"),
            _re(r"otSDKStub\.js"),
        ),
        id_pattern=_re(r"data-domain-script=['\"]([a-f0-9-]+)['\"]"),
    ),
    VendorSignature(
        key="cookiebot",
        category="consent",
        script_patterns=(
            _re(r"consent\.cookiebot\.com/uc\.js"),
            _re(r"data-cbid=['\"][a-f0-9-]+['\"]"),
        ),
        id_pattern=_re(r"data-cbid=['\"]([a-f0-9-]+)['\"]"),
    ),
    VendorSignature(
        key="didomi",
        category="consent",
        script_patterns=(
            _re(r"sdk\.privacy-center\.org/"),
            _re(r"didomi-host"),
        ),
    ),
    VendorSignature(
        key="usercentrics",
        category="consent",
        script_patterns=(
            _re(r"app\.usercentrics\.eu/"),
            _re(r"usercentrics-cmp"),
        ),
    ),
    VendorSignature(
        key="termly",
        category="consent",
        script_patterns=(
            _re(r"app\.termly\.io/embed\.min\.js"),
            _re(r"data-website-uuid=['\"][a-f0-9-]+['\"]"),
        ),
        id_pattern=_re(r"data-website-uuid=['\"]([a-f0-9-]+)['\"]"),
    ),
    # A/B testing
    VendorSignature(
        key="optimizely",
        category="ab_testing",
        script_patterns=(_re(r"cdn\.optimizely\.com/(?:js|public)/\d+\.js"),),
        id_pattern=_re(r"cdn\.optimizely\.com/(?:js|public)/(\d+)\.js"),
    ),
    VendorSignature(
        key="vwo",
        category="ab_testing",
        script_patterns=(
            _re(r"dev\.visualwebsiteoptimizer\.com/"),
            _re(r"window\._vwo_code"),
        ),
        id_pattern=_re(r"account_id\s*[:=]\s*['\"]?(\d+)"),
    ),
    # Reviews
    VendorSignature(
        key="yotpo",
        category="reviews",
        script_patterns=(_re(r"staticw2\.yotpo\.com/[A-Za-z0-9]+/widget\.js"),),
        id_pattern=_re(r"staticw2\.yotpo\.com/([A-Za-z0-9]+)/widget\.js"),
    ),
    VendorSignature(
        key="trustpilot",
        category="reviews",
        script_patterns=(
            _re(r"widget\.trustpilot\.com/bootstrap/"),
            _re(r"data-businessunit-id=['\"][a-f0-9]+['\"]"),
        ),
        id_pattern=_re(r"data-businessunit-id=['\"]([a-f0-9]+)['\"]"),
    ),
    VendorSignature(
        key="okendo",
        category="reviews",
        script_patterns=(
            _re(r"d3hw6dc1ow8pp2\.cloudfront\.net/"),
            _re(r"okendo-reviews-widget"),
            _re(r"cdn-static\.okendo\.io/"),
        ),
    ),
    # Ecom platforms
    VendorSignature(
        key="shopify",
        category="ecom_platform",
        script_patterns=(
            _re(r"cdn\.shopify\.com/"),
            _re(r"Shopify\.theme\s*="),
            _re(r"window\.Shopify\s*="),
        ),
    ),
    VendorSignature(
        key="magento",
        category="ecom_platform",
        script_patterns=(
            _re(r"/static/version\d+/frontend/"),
            _re(r"Mage\.Cookies"),
            _re(r"x-magento-"),
        ),
    ),
    VendorSignature(
        key="bigcommerce",
        category="ecom_platform",
        script_patterns=(
            _re(r"cdn\d*\.bigcommerce\.com/"),
            _re(r"window\.BCData\s*="),
        ),
    ),
    VendorSignature(
        key="woocommerce",
        category="ecom_platform",
        script_patterns=(
            _re(r"/wp-content/plugins/woocommerce/"),
            _re(r"woocommerce-no-js"),
            _re(r"wc_add_to_cart_params"),
        ),
    ),
)


CATALOGUE_BY_KEY: dict[str, VendorSignature] = {v.key: v for v in CATALOGUE}
