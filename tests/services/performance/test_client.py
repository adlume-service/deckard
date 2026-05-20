"""Unit tests for the PSI client retry behavior and auth handling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from deckard.config import get_settings
from deckard.services.performance import client as client_module

PSI_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "performance" / "pagespeed_response.json"


async def _make_handler(responses: list[httpx.Response]) -> tuple[Any, list[httpx.Request]]:
    """Build a MockTransport handler that yields ``responses`` in order and
    records every request it sees.
    """
    captured: list[httpx.Request] = []
    iterator = iter(responses)

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        try:
            return next(iterator)
        except StopIteration as exc:  # pragma: no cover - defensive
            raise AssertionError("More requests than expected") from exc

    return _handler, captured


@pytest.mark.asyncio
async def test_fetch_retries_once_on_503_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    success_body = {"lighthouseResult": {"categories": {"performance": {"score": 0.9}}}}
    responses = [
        httpx.Response(503, text="unavailable"),
        httpx.Response(200, json=success_body),
    ]
    handler, captured = await _make_handler(responses)
    transport = httpx.MockTransport(handler)

    real_async_client = httpx.AsyncClient

    def _patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _patched_async_client)

    result = await client_module.fetch_pagespeed_insights(
        "https://example.com/",
        api_key="secret-key",
        strategy="mobile",
        timeout_s=5.0,
    )

    assert result == success_body
    assert len(captured) == 2
    # API key must travel via header, never the query string.
    for request in captured:
        assert "key=" not in str(request.url)
        assert request.headers.get("X-goog-api-key") == "secret-key"


@pytest.mark.asyncio
async def test_fetch_does_not_retry_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [httpx.Response(401, text="unauthorized")]
    handler, captured = await _make_handler(responses)
    transport = httpx.MockTransport(handler)

    real_async_client = httpx.AsyncClient

    def _patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _patched_async_client)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client_module.fetch_pagespeed_insights(
            "https://example.com/",
            api_key="secret-key",
            strategy="mobile",
            timeout_s=5.0,
        )
    assert exc_info.value.response.status_code == 401
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_detect_performance_uses_passed_strategy_not_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """``detect_performance`` must take strategy from its kwarg, not settings —
    the orchestrator decides per-call which strategy to audit.
    """
    payload = json.loads(PSI_FIXTURE.read_text(encoding="utf-8"))
    handler, captured = await _make_handler([httpx.Response(200, json=payload)])
    transport = httpx.MockTransport(handler)

    real_async_client = httpx.AsyncClient

    def _patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _patched_async_client)

    settings = get_settings()
    monkeypatch.setattr(settings, "page_speed_insights_api", "secret-key")
    monkeypatch.setattr(settings, "page_speed_insights_strategy", "both")

    report = await client_module.detect_performance("https://example.com/", strategy="desktop")

    assert report["strategy"] == "desktop"
    assert len(captured) == 1
    assert "strategy=desktop" in str(captured[0].url)
