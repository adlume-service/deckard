"""Parser-level tests for PageSpeed Insights response trimming."""

from __future__ import annotations

import json
from pathlib import Path

from deckard.services.performance.parser import parse_pagespeed_response

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "performance" / "pagespeed_response.json"


def _load_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parser_extracts_score_and_metrics() -> None:
    report = parse_pagespeed_response(_load_payload(), strategy="mobile")

    assert report["strategy"] == "mobile"
    assert report["score"] == 62
    metrics = report["metrics"]
    assert metrics["first_contentful_paint_ms"] == 1820
    assert metrics["largest_contentful_paint_ms"] == 3140
    assert metrics["speed_index_ms"] == 4100
    assert metrics["time_to_interactive_ms"] == 5200
    assert metrics["total_blocking_time_ms"] == 290
    assert metrics["server_response_time_ms"] == 410
    assert metrics["cumulative_layout_shift"] == 0.082


def test_parser_ranks_opportunities_by_savings() -> None:
    report = parse_pagespeed_response(_load_payload(), strategy="mobile")

    opportunity_ids = [item["id"] for item in report["opportunities"]]
    # `unused-javascript` (1200ms savings) beats `render-blocking-resources` (450ms);
    # `uses-text-compression` has zero savings and must be filtered out.
    assert opportunity_ids == ["unused-javascript", "render-blocking-resources"]
    top = report["opportunities"][0]
    assert top["savings_ms"] == 1200
    assert top["savings_bytes"] == 84000
    assert top["display_value"] == "Potential savings of 84 KiB"


def test_parser_surfaces_failing_diagnostics_only() -> None:
    report = parse_pagespeed_response(_load_payload(), strategy="mobile")

    diagnostic_ids = [item["id"] for item in report["diagnostics"]]
    # `uses-long-cache-ttl` (score 0.5) and `uses-passive-event-listeners` (0.8) fail.
    # `viewport` (score 1.0) passes. Opportunity audits and lab-metric audits are excluded.
    assert "uses-long-cache-ttl" in diagnostic_ids
    assert "uses-passive-event-listeners" in diagnostic_ids
    assert "viewport" not in diagnostic_ids
    assert "unused-javascript" not in diagnostic_ids
    assert "first-contentful-paint" not in diagnostic_ids
    # Sorted ascending by score: 0.5 first.
    assert report["diagnostics"][0]["id"] == "uses-long-cache-ttl"


def test_parser_extracts_page_level_field_data() -> None:
    report = parse_pagespeed_response(_load_payload(), strategy="mobile")

    field = report["field_data"]
    assert field is not None
    assert field["source"] == "page"
    assert field["overall_category"] == "AVERAGE"
    assert field["metrics"]["LARGEST_CONTENTFUL_PAINT_MS"]["category"] == "AVERAGE"
    assert field["metrics"]["CUMULATIVE_LAYOUT_SHIFT_SCORE"]["percentile"] == 5


def test_parser_handles_missing_field_data() -> None:
    payload = _load_payload()
    del payload["loadingExperience"]
    report = parse_pagespeed_response(payload, strategy="mobile")
    assert report["field_data"] is None


def test_parser_falls_back_to_origin_when_page_level_absent() -> None:
    payload = _load_payload()
    page_experience = payload.pop("loadingExperience")
    payload["originLoadingExperience"] = {
        **page_experience,
        "overall_category": "SLOW",
    }
    report = parse_pagespeed_response(payload, strategy="mobile")

    field = report["field_data"]
    assert field is not None
    assert field["source"] == "origin"
    assert field["overall_category"] == "SLOW"


def test_parser_falls_back_to_origin_when_page_level_empty() -> None:
    # PSI sometimes returns ``loadingExperience`` as an empty dict (or None)
    # when there is no page-level CrUX. Source must reflect the value used.
    for empty_value in ({}, None):
        payload = _load_payload()
        page_experience = payload["loadingExperience"]
        payload["loadingExperience"] = empty_value
        payload["originLoadingExperience"] = {
            **page_experience,
            "overall_category": "AVERAGE",
        }
        report = parse_pagespeed_response(payload, strategy="mobile")

        field = report["field_data"]
        assert field is not None, f"expected origin fallback for {empty_value!r}"
        assert field["source"] == "origin"
        assert field["overall_category"] == "AVERAGE"


def test_parser_prefers_page_level_when_both_present() -> None:
    payload = _load_payload()
    payload["originLoadingExperience"] = {
        "id": "https://www.brand.com",
        "overall_category": "SLOW",
        "metrics": {
            "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 9000, "category": "SLOW"},
        },
    }
    report = parse_pagespeed_response(payload, strategy="mobile")

    field = report["field_data"]
    assert field is not None
    assert field["source"] == "page"
    # Page-level overall_category from the fixture wins over the origin's SLOW.
    assert field["overall_category"] == "AVERAGE"


def test_parser_handles_missing_score() -> None:
    payload = _load_payload()
    payload["lighthouseResult"]["categories"]["performance"]["score"] = None
    report = parse_pagespeed_response(payload, strategy="mobile")
    assert report["score"] is None


def test_parser_handles_empty_audits() -> None:
    report = parse_pagespeed_response({"lighthouseResult": {}}, strategy="desktop")
    assert report["strategy"] == "desktop"
    assert report["score"] is None
    assert all(value is None for value in report["metrics"].values())
    assert report["opportunities"] == []
    assert report["diagnostics"] == []
