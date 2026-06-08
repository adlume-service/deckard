"""Unit tests for the URL ranker: deterministic ordering, reconciliation, and the LLM call."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from deckard.services.extraction.ranker import (
    _deterministic_url_order,
    rank_urls_with_llm,
    reconcile_ranker_output,
)


class TestDeterministicUrlOrder:
    def test_root_first_then_depth_then_alpha(self) -> None:
        urls = [
            "https://x.com/blog/post-b",
            "https://x.com/about",
            "https://x.com/",
            "https://x.com/blog/post-a",
            "https://x.com/pricing",
        ]
        assert _deterministic_url_order(urls) == [
            "https://x.com/",
            "https://x.com/about",
            "https://x.com/pricing",
            "https://x.com/blog/post-a",
            "https://x.com/blog/post-b",
        ]

    def test_trailing_slash_and_no_slash_both_root(self) -> None:
        # "" (no path) and "/" (root path) should both rank as root, ahead of any sub-path.
        urls = ["https://x.com/about", "https://x.com", "https://x.com/"]
        ordered = _deterministic_url_order(urls)
        # Both root forms come first; alphabetical tie-break between them.
        assert ordered[:2] == ["https://x.com", "https://x.com/"]
        assert ordered[2] == "https://x.com/about"

    def test_alphabetical_tie_break_at_equal_depth(self) -> None:
        urls = [
            "https://x.com/zebra",
            "https://x.com/apple",
            "https://x.com/mango",
        ]
        assert _deterministic_url_order(urls) == [
            "https://x.com/apple",
            "https://x.com/mango",
            "https://x.com/zebra",
        ]

    def test_shallow_before_deep(self) -> None:
        urls = [
            "https://x.com/a/b/c/d",
            "https://x.com/a",
            "https://x.com/a/b",
        ]
        assert _deterministic_url_order(urls) == [
            "https://x.com/a",
            "https://x.com/a/b",
            "https://x.com/a/b/c/d",
        ]


class TestReconcileRankerOutput:
    def test_clean_pass_through_preserves_model_order(self) -> None:
        inputs = ["https://x.com/", "https://x.com/a", "https://x.com/b"]
        model = ["https://x.com/b", "https://x.com/", "https://x.com/a"]
        assert reconcile_ranker_output(inputs, model) == model

    def test_dedup_keeps_first_occurrence_position(self) -> None:
        inputs = ["https://x.com/a", "https://x.com/b", "https://x.com/c"]
        # /a repeated; first occurrence position kept, rest of order preserved.
        model = [
            "https://x.com/a",
            "https://x.com/b",
            "https://x.com/a",
            "https://x.com/c",
        ]
        assert reconcile_ranker_output(inputs, model) == [
            "https://x.com/a",
            "https://x.com/b",
            "https://x.com/c",
        ]

    def test_hallucination_raises_value_error(self) -> None:
        inputs = ["https://x.com/a", "https://x.com/b"]
        model = ["https://x.com/a", "https://x.com/NOT-AN-INPUT"]
        with pytest.raises(ValueError):
            reconcile_ranker_output(inputs, model)

    def test_missing_urls_appended_in_deterministic_order(self) -> None:
        inputs = [
            "https://x.com/",
            "https://x.com/about",
            "https://x.com/pricing",
            "https://x.com/blog/deep",
        ]
        # Model only ranked two of them.
        model = ["https://x.com/pricing", "https://x.com/about"]
        result = reconcile_ranker_output(inputs, model)
        # Present-and-ordered ones come first, in model order.
        assert result[:2] == ["https://x.com/pricing", "https://x.com/about"]
        # Then the missing ones appended in deterministic order (root first, then depth).
        missing = ["https://x.com/", "https://x.com/blog/deep"]
        assert result[2:] == _deterministic_url_order(missing)
        assert result[2:] == ["https://x.com/", "https://x.com/blog/deep"]

    def test_combined_dedup_and_missing_is_permutation_no_dups(self) -> None:
        inputs = [
            "https://x.com/",
            "https://x.com/a",
            "https://x.com/b",
            "https://x.com/c",
        ]
        # Subset {/, /a} with /a duplicated; /b and /c missing.
        model = ["https://x.com/a", "https://x.com/", "https://x.com/a"]
        result = reconcile_ranker_output(inputs, model)
        # Deduped subset first, in model order.
        assert result[:2] == ["https://x.com/a", "https://x.com/"]
        # Missing appended deterministically.
        assert result[2:] == _deterministic_url_order(["https://x.com/b", "https://x.com/c"])
        # Final list is a permutation of the full input set with no dups.
        assert sorted(result) == sorted(inputs)
        assert len(result) == len(set(result)) == len(inputs)


# --- async LLM call ----------------------------------------------------


def _make_fake_client(*, ordered=None, output_parsed_none=False, raise_on_parse=None):
    """Build a fake AsyncOpenAI-shaped client whose responses.parse is async."""

    if output_parsed_none:
        output_parsed = None
    else:
        output_parsed = SimpleNamespace(ordered=ordered)

    response = SimpleNamespace(
        id="resp_123",
        model="gpt-test",
        usage=SimpleNamespace(input_tokens=42, output_tokens=7),
        output_parsed=output_parsed,
    )

    class _Responses:
        async def parse(self, **kwargs):
            if raise_on_parse is not None:
                raise raise_on_parse
            return response

    return SimpleNamespace(responses=_Responses())


def _patch_client(monkeypatch: pytest.MonkeyPatch, fake_client) -> None:
    monkeypatch.setattr(
        "deckard.services.extraction.ranker.get_openai_client",
        lambda: fake_client,
    )


class TestRankUrlsWithLlm:
    async def test_success_returns_reconciled_true_and_usage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        urls = ["https://x.com/", "https://x.com/a", "https://x.com/b"]
        ordered = ["https://x.com/b", "https://x.com/", "https://x.com/a"]
        _patch_client(monkeypatch, _make_fake_client(ordered=ordered))

        ranked, ranker_used, usage_meta = await rank_urls_with_llm(urls)

        assert ranked == ordered  # reconciled = model order (clean pass-through)
        assert ranker_used is True
        assert usage_meta == {
            "response_id": "resp_123",
            "model": "gpt-test",
            "input_tokens": 42,
            "output_tokens": 7,
        }

    async def test_hallucinated_output_falls_back_but_keeps_usage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        urls = ["https://x.com/", "https://x.com/a"]
        # Contains a URL not in the input set -> reconcile raises -> caught.
        ordered = ["https://x.com/", "https://x.com/HALLUCINATED"]
        _patch_client(monkeypatch, _make_fake_client(ordered=ordered))

        ranked, ranker_used, usage_meta = await rank_urls_with_llm(urls)

        assert ranked == _deterministic_url_order(urls)
        assert ranker_used is False
        # CRUCIAL: API call returned, so usage is still attributed.
        assert usage_meta is not None
        assert usage_meta["response_id"] == "resp_123"

    async def test_none_parsed_output_falls_back_but_keeps_usage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        urls = ["https://x.com/", "https://x.com/a", "https://x.com/b"]
        _patch_client(monkeypatch, _make_fake_client(output_parsed_none=True))

        ranked, ranker_used, usage_meta = await rank_urls_with_llm(urls)

        assert ranked == _deterministic_url_order(urls)
        assert ranker_used is False
        assert usage_meta is not None

    async def test_api_error_before_response_yields_none_usage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        urls = ["https://x.com/", "https://x.com/a"]
        _patch_client(
            monkeypatch,
            _make_fake_client(raise_on_parse=ConnectionError("boom")),
        )

        ranked, ranker_used, usage_meta = await rank_urls_with_llm(urls)

        assert ranked == _deterministic_url_order(urls)
        assert ranker_used is False
        # No response came back -> no usage to attribute.
        assert usage_meta is None
