"""Unit tests for the token-budget packing in budget_fitter.

The over-budget path normally calls ``rank_urls_with_llm`` (an OpenAI call); we
monkeypatch the name as imported into ``budget_fitter`` so no network call ever
happens. Token counts are real (tiktoken o200k_base) — tiktoken is never mocked.
"""

from __future__ import annotations

from deckard.database.models import ScrapingResult
from deckard.services.extraction.budget_fitter import (
    BudgetReport,
    _page_block_tokens,
    count_tokens,
    fit_to_budget,
    page_header,
)


def _result(url: str, markdown: str | None) -> ScrapingResult:
    """Build a ScrapingResult in memory with a real token count (no DB session)."""
    r = ScrapingResult()
    r.url = url
    r.markdown = markdown
    r.tokens = count_tokens(markdown) if markdown else 0
    return r


def _fake_ranker(ranked: list[str], used: bool = True, usage: dict | None = None):
    """Return an async stand-in for ``rank_urls_with_llm`` that records its calls."""

    async def fake(urls: list[str]):
        fake.calls.append(list(urls))
        return ranked, used, usage

    fake.calls = []
    return fake


def _patch_ranker(monkeypatch, fake) -> None:
    monkeypatch.setattr("deckard.services.extraction.budget_fitter.rank_urls_with_llm", fake)


# --- pure helpers ------------------------------------------------------


class TestPureHelpers:
    def test_page_header_format(self) -> None:
        assert page_header("https://x.com/a") == "--- PAGE: https://x.com/a ---\n"

    def test_count_tokens_nonzero_for_text(self) -> None:
        assert count_tokens("hello world this is some text") > 0
        assert count_tokens("") == 0

    def test_page_block_tokens_is_header_plus_body(self) -> None:
        url = "https://x.com/a"
        body_tokens = 42
        expected = count_tokens(page_header(url)) + body_tokens
        assert _page_block_tokens(url, body_tokens) == expected


# --- happy path: corpus fits -------------------------------------------


class TestFitsWithinBudget:
    async def test_all_pages_returned_in_original_order_ranker_not_called(self, monkeypatch) -> None:
        fake = _fake_ranker(ranked=[], used=True)
        _patch_ranker(monkeypatch, fake)

        results = [
            _result("https://x.com/c", "page c body content"),
            _result("https://x.com/a", "page a body content"),
            _result("https://x.com/b", "page b body content"),
        ]
        total = sum(_page_block_tokens(r.url, r.tokens) for r in results)

        fitted, report = await fit_to_budget(results, budget=total + 100)

        assert fake.calls == []  # ranker never invoked
        assert fitted == [
            ("https://x.com/c", "page c body content"),
            ("https://x.com/a", "page a body content"),
            ("https://x.com/b", "page b body content"),
        ]
        assert isinstance(report, BudgetReport)
        assert report.fit is True
        assert report.ranker_used is False
        assert report.dropped_urls == []
        assert report.truncated_url is None
        assert report.ranker_usage is None
        assert report.total_tokens_before == report.total_tokens_after == total

    async def test_exactly_at_budget_still_fits(self, monkeypatch) -> None:
        fake = _fake_ranker(ranked=[])
        _patch_ranker(monkeypatch, fake)

        results = [_result("https://x.com/a", "some content here for tokens")]
        total = _page_block_tokens(results[0].url, results[0].tokens)

        fitted, report = await fit_to_budget(results, budget=total)

        assert report.fit is True
        assert fake.calls == []
        assert len(fitted) == 1

    async def test_empty_and_none_markdown_filtered_out(self, monkeypatch) -> None:
        fake = _fake_ranker(ranked=[])
        _patch_ranker(monkeypatch, fake)

        results = [
            _result("https://x.com/a", "real content"),
            _result("https://x.com/empty", ""),
            _result("https://x.com/none", None),
            _result("https://x.com/b", "more content"),
        ]

        fitted, report = await fit_to_budget(results, budget=10_000)

        urls = [u for u, _ in fitted]
        assert urls == ["https://x.com/a", "https://x.com/b"]
        assert "https://x.com/empty" not in urls
        assert "https://x.com/none" not in urls
        assert report.fit is True


# --- over budget: greedy pack from ranked order ------------------------


class TestOverBudget:
    def _corpus(self) -> list[ScrapingResult]:
        # Three pages of roughly equal, substantial size.
        return [
            _result("https://x.com/a", "alpha " * 200),
            _result("https://x.com/b", "bravo " * 200),
            _result("https://x.com/c", "charlie " * 200),
        ]

    async def test_high_priority_fitted_boundary_truncated_rest_dropped(self, monkeypatch) -> None:
        results = self._corpus()
        block_a = _page_block_tokens("https://x.com/a", results[0].tokens)

        # Budget fits page a fully plus a slice of page b → b is the boundary,
        # c is dropped. Ranked order puts b before a before c.
        budget = block_a + 50
        ranked = ["https://x.com/b", "https://x.com/a", "https://x.com/c"]
        fake = _fake_ranker(ranked=ranked, used=True)
        _patch_ranker(monkeypatch, fake)

        fitted, report = await fit_to_budget(results, budget=budget)

        assert fake.calls == [["https://x.com/a", "https://x.com/b", "https://x.com/c"]]
        assert report.fit is False
        # ranked ordering respected: b fitted first, then a (truncated boundary).
        fitted_urls = [u for u, _ in fitted]
        assert fitted_urls == ["https://x.com/b", "https://x.com/a"]
        assert report.truncated_url == "https://x.com/a"
        assert report.dropped_urls == ["https://x.com/c"]
        assert report.ranker_used is True
        # consumed reflects the fitted blocks: full block for b, plus the truncated
        # boundary block for a. It must stay within the budget (round-trip slack only
        # ever undershoots, never overshoots, the body_budget).
        block_b_full = _page_block_tokens("https://x.com/b", results[1].tokens)
        assert report.total_tokens_after >= block_b_full
        assert report.total_tokens_after <= budget

    async def test_total_after_within_header_slack(self, monkeypatch) -> None:
        results = self._corpus()
        block_a = _page_block_tokens("https://x.com/a", results[0].tokens)
        budget = block_a + 40
        ranked = ["https://x.com/a", "https://x.com/b", "https://x.com/c"]
        _patch_ranker(monkeypatch, _fake_ranker(ranked=ranked))

        _, report = await fit_to_budget(results, budget=budget)

        # Documented caveat: round-trip token decode of the truncated body can be
        # slightly under the body_budget, so consumed never exceeds budget here and
        # certainly does not wildly exceed it.
        assert report.total_tokens_after <= budget
        assert report.fit is False

    async def test_once_truncated_small_later_page_still_dropped(self, monkeypatch) -> None:
        # A big page first (becomes boundary/truncated), then a tiny page that would
        # otherwise fit in round-trip slack — it must still be dropped.
        big = _result("https://x.com/big", "lorem " * 500)
        tiny = _result("https://x.com/tiny", "hi")
        results = [big, tiny]
        ranked = ["https://x.com/big", "https://x.com/tiny"]
        _patch_ranker(monkeypatch, _fake_ranker(ranked=ranked))

        # Budget smaller than the big block → big is truncated immediately.
        budget = count_tokens(page_header("https://x.com/big")) + 100

        fitted, report = await fit_to_budget(results, budget=budget)

        assert report.truncated_url == "https://x.com/big"
        assert [u for u, _ in fitted] == ["https://x.com/big"]
        assert report.dropped_urls == ["https://x.com/tiny"]
        assert report.fit is False

    async def test_ranker_usage_threaded_into_report(self, monkeypatch) -> None:
        results = self._corpus()
        usage = {
            "response_id": "resp_123",
            "model": "gpt-x",
            "input_tokens": 11,
            "output_tokens": 3,
        }
        ranked = ["https://x.com/a", "https://x.com/b", "https://x.com/c"]
        _patch_ranker(monkeypatch, _fake_ranker(ranked=ranked, used=True, usage=usage))

        _, report = await fit_to_budget(results, budget=50)

        assert report.ranker_usage == usage

    async def test_ranker_failed_still_reports_usage_and_used_false(self, monkeypatch) -> None:
        # Mirrors the ranker's degrade-to-fallback contract: used=False but usage
        # may still be present (API billed before parse rejection).
        results = self._corpus()
        usage = {"response_id": "r", "model": "m", "input_tokens": 1, "output_tokens": 0}
        ranked = ["https://x.com/a", "https://x.com/b", "https://x.com/c"]
        _patch_ranker(monkeypatch, _fake_ranker(ranked=ranked, used=False, usage=usage))

        _, report = await fit_to_budget(results, budget=50)

        assert report.ranker_used is False
        assert report.ranker_usage == usage


# --- boundary truncation math ------------------------------------------


class TestTruncationMath:
    async def test_truncated_markdown_is_prefix_and_block_fits_remaining(self, monkeypatch) -> None:
        from deckard.constants import TOKEN_ENCODING

        body = "the quick brown fox jumps over the lazy dog " * 100
        page = _result("https://x.com/p", body)
        results = [page]
        ranked = ["https://x.com/p"]
        _patch_ranker(monkeypatch, _fake_ranker(ranked=ranked))

        header_tokens = count_tokens(page_header("https://x.com/p"))
        budget = header_tokens + 60  # forces truncation of the single page

        fitted, report = await fit_to_budget(results, budget=budget)

        assert report.truncated_url == "https://x.com/p"
        ((url, truncated_md),) = fitted
        assert url == "https://x.com/p"

        # The truncated markdown round-trips to a PREFIX of the original markdown.
        assert body.startswith(truncated_md)
        assert len(truncated_md) < len(body)

        # Body budget was budget - header; the truncated body fits in the remainder,
        # so the full page block is <= budget.
        block = header_tokens + count_tokens(truncated_md)
        assert block <= budget
        assert report.total_tokens_after == block

        # Sanity: the truncated body is exactly the decode of the first body_budget
        # tokens of the original.
        body_budget = budget - header_tokens
        expected = TOKEN_ENCODING.decode(TOKEN_ENCODING.encode(body)[:body_budget])
        assert truncated_md == expected
