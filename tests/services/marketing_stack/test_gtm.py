"""Tests for the GTM container fetch + parse module."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from deckard.services.marketing_stack.gtm import (
    enrich_with_gtm_container,
    fetch_gtm_container,
    parse_gtm_container,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "marketing_stack"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_gtm_container_extracts_tags() -> None:
    body = _load("gtm_container_body.js")
    parsed = parse_gtm_container(body)
    types = {tag["type"] for tag in parsed["tags"]}
    assert "ga4" in types
    assert "meta_pixel" in types
    assert "google_ads" in types
    assert "linkedin_insight" in types
    assert "tiktok_pixel" in types
    assert parsed["transport_url"] == "https://sgtm.example.com"
    assert parsed["consent_mode_v2"] == "granted"


def test_parse_gtm_container_empty_body() -> None:
    parsed = parse_gtm_container("")
    assert parsed == {"tags": [], "transport_url": None, "consent_mode_v2": None}


@pytest.mark.asyncio
async def test_fetch_gtm_container_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.query and b"GTM-FETCH01" in request.url.query
        return httpx.Response(200, text="container body here")

    transport = httpx.MockTransport(_handler)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("deckard.services.marketing_stack.gtm.httpx.AsyncClient", _PatchedClient)
    body = await fetch_gtm_container("GTM-FETCH01")
    assert body == "container body here"


@pytest.mark.asyncio
async def test_fetch_gtm_container_returns_none_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    transport = httpx.MockTransport(_handler)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("deckard.services.marketing_stack.gtm.httpx.AsyncClient", _PatchedClient)
    body = await fetch_gtm_container("GTM-FAIL01")
    assert body is None


@pytest.mark.asyncio
async def test_fetch_gtm_container_returns_none_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oh no")

    transport = httpx.MockTransport(_handler)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("deckard.services.marketing_stack.gtm.httpx.AsyncClient", _PatchedClient)
    body = await fetch_gtm_container("GTM-500X")
    assert body is None


@pytest.mark.asyncio
async def test_enrich_no_op_when_no_gtm() -> None:
    stack = {"vendors": {}, "server_side_hints": []}
    out = await enrich_with_gtm_container(stack)
    assert out == stack


@pytest.mark.asyncio
async def test_enrich_merges_tags_and_updates_load_context(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _load("gtm_container_body.js")

    async def _fake_fetch(container_id: str, *, timeout_s: float | None = None) -> str | None:
        return body

    monkeypatch.setattr("deckard.services.marketing_stack.gtm.fetch_gtm_container", _fake_fetch)

    stack = {
        "vendors": {
            "gtm": {
                "detected": True,
                "ids": ["GTM-PRIMARY"],
                "evidence": [],
                "extras": {
                    "id_count": 1,
                    "load_context": "direct",
                    "first_party_mode": None,
                    "container_fetch_status": "pending",
                    "container_tags": [],
                },
            },
            "ga4": {
                "detected": True,
                "ids": ["G-DIRECT01"],
                "evidence": [],
                "extras": {"id_count": 1, "load_context": "direct", "first_party_mode": None},
            },
        },
        "server_side_hints": [],
    }

    enriched = await enrich_with_gtm_container(stack)
    gtm = enriched["vendors"]["gtm"]
    assert gtm["extras"]["container_fetch_status"] == "ok"
    types = {tag["type"] for tag in gtm["extras"]["container_tags"]}
    assert "ga4" in types and "meta_pixel" in types

    # ga4 was direct on page and inside container -> "both"
    assert enriched["vendors"]["ga4"]["extras"]["load_context"] == "both"

    # meta_pixel was only inside container -> synthesised with load_context "gtm"
    assert enriched["vendors"]["meta_pixel"]["extras"]["load_context"] == "gtm"
    assert "5555666677778888" in enriched["vendors"]["meta_pixel"]["ids"]


@pytest.mark.asyncio
async def test_enrich_marks_failed_on_fetch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch(container_id: str, *, timeout_s: float | None = None) -> str | None:
        return None

    monkeypatch.setattr("deckard.services.marketing_stack.gtm.fetch_gtm_container", _fake_fetch)

    stack = {
        "vendors": {
            "gtm": {
                "detected": True,
                "ids": ["GTM-NOPE"],
                "evidence": [],
                "extras": {
                    "id_count": 1,
                    "load_context": "direct",
                    "first_party_mode": None,
                    "container_fetch_status": "pending",
                    "container_tags": [],
                },
            },
        },
        "server_side_hints": [],
    }

    enriched = await enrich_with_gtm_container(stack)
    assert enriched["vendors"]["gtm"]["extras"]["container_fetch_status"] == "failed"
    assert enriched["vendors"]["gtm"]["extras"]["container_tags"] == []
