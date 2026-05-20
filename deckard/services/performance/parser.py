"""Pure parser for Google PageSpeed Insights v5 responses.

PSI returns a 1–2 MB payload per request. We keep only the parts a marketing
audit actually consumes: the overall performance score, the canonical lab
metrics, real-user (CrUX) field data when available, and the top
opportunities + diagnostics ranked by potential savings.
"""

from __future__ import annotations

from typing import Any

MAX_OPPORTUNITIES = 5
MAX_DIAGNOSTICS = 5

# Lighthouse audit IDs → snake_case keys exposed to consumers.
_LAB_METRIC_AUDITS = {
    "first-contentful-paint": "first_contentful_paint_ms",
    "largest-contentful-paint": "largest_contentful_paint_ms",
    "speed-index": "speed_index_ms",
    "interactive": "time_to_interactive_ms",
    "total-blocking-time": "total_blocking_time_ms",
    "cumulative-layout-shift": "cumulative_layout_shift",
    "server-response-time": "server_response_time_ms",
}

# CLS is unitless; everything else above is milliseconds.
_UNITLESS_METRICS = {"cumulative_layout_shift"}


def parse_pagespeed_response(payload: dict[str, Any], *, strategy: str) -> dict[str, Any]:
    """Trim a raw PSI v5 response down to the shape we persist.

    The input is the JSON body returned by ``runPagespeed``. The output is
    safe to drop into ``request_metadata.performance`` as-is.
    """
    lighthouse = payload.get("lighthouseResult") or {}
    categories = lighthouse.get("categories") or {}
    audits = lighthouse.get("audits") or {}

    performance_category = categories.get("performance") or {}
    raw_score = performance_category.get("score")
    score = int(round(raw_score * 100)) if isinstance(raw_score, (int, float)) else None

    return {
        "strategy": strategy,
        "score": score,
        "metrics": _extract_metrics(audits),
        "field_data": _extract_field_data(payload),
        "opportunities": _extract_opportunities(audits),
        "diagnostics": _extract_diagnostics(audits),
        "lighthouse_version": lighthouse.get("lighthouseVersion"),
        "final_url": lighthouse.get("finalUrl") or lighthouse.get("requestedUrl"),
    }


def _extract_metrics(audits: dict[str, Any]) -> dict[str, float | int | None]:
    metrics: dict[str, float | int | None] = {}
    for audit_id, key in _LAB_METRIC_AUDITS.items():
        audit = audits.get(audit_id) or {}
        value = audit.get("numericValue")
        if not isinstance(value, (int, float)):
            metrics[key] = None
            continue
        metrics[key] = round(value, 3) if key in _UNITLESS_METRICS else int(round(value))
    return metrics


def _extract_field_data(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Prefer page-level CrUX (``loadingExperience``) over origin-level.

    PSI v5 uses snake_case for the CrUX block (``overall_category``,
    ``initial_url``) but camelCase elsewhere. The ``loadingExperience`` key
    can be present but empty/None when CrUX has no page-level data, in which
    case we fall back to ``originLoadingExperience`` and label the source
    accordingly — based on the value actually used, not key presence.
    """
    page_experience = payload.get("loadingExperience")
    origin_experience = payload.get("originLoadingExperience")
    if isinstance(page_experience, dict) and page_experience:
        experience = page_experience
        source = "page"
    elif isinstance(origin_experience, dict) and origin_experience:
        experience = origin_experience
        source = "origin"
    else:
        return None
    metrics = experience.get("metrics") or {}
    return {
        "overall_category": experience.get("overall_category"),
        "metrics": {
            metric_id: {
                "category": metric.get("category"),
                "percentile": metric.get("percentile"),
            }
            for metric_id, metric in metrics.items()
            if isinstance(metric, dict)
        },
        "source": source,
    }


def _extract_opportunities(audits: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for audit_id, audit in audits.items():
        details = audit.get("details") if isinstance(audit, dict) else None
        if not isinstance(details, dict) or details.get("type") != "opportunity":
            continue
        savings_ms = details.get("overallSavingsMs") or 0
        savings_bytes = details.get("overallSavingsBytes") or 0
        if not savings_ms and not savings_bytes:
            continue
        items.append(
            {
                "id": audit_id,
                "title": audit.get("title"),
                "description": audit.get("description"),
                "savings_ms": int(round(savings_ms)) if savings_ms else 0,
                "savings_bytes": int(round(savings_bytes)) if savings_bytes else 0,
                "display_value": audit.get("displayValue"),
            }
        )
    items.sort(key=lambda item: (-item["savings_ms"], -item["savings_bytes"], item["id"]))
    return items[:MAX_OPPORTUNITIES]


def _extract_diagnostics(audits: dict[str, Any]) -> list[dict[str, Any]]:
    """Surface failing diagnostics (score < 1) with a stable cap.

    PSI marks diagnostics with ``details.type == "diagnostic"`` or a
    ``scoreDisplayMode`` of ``"informative"``/``"manual"``. We keep failing
    audits regardless of details type, since the actionable signal is the
    score itself.
    """
    items: list[dict[str, Any]] = []
    for audit_id, audit in audits.items():
        if not isinstance(audit, dict):
            continue
        if audit_id in _LAB_METRIC_AUDITS:
            continue
        details_type = (audit.get("details") or {}).get("type") if isinstance(audit.get("details"), dict) else None
        if details_type == "opportunity":
            continue
        score = audit.get("score")
        if not isinstance(score, (int, float)) or score >= 0.9:
            continue
        items.append(
            {
                "id": audit_id,
                "title": audit.get("title"),
                "description": audit.get("description"),
                "score": round(score, 2),
                "display_value": audit.get("displayValue"),
            }
        )
    items.sort(key=lambda item: (item["score"], item["id"]))
    return items[:MAX_DIAGNOSTICS]
