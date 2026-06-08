"""Token-budget packing for the extractor input.

Happy path: the concatenated page corpus already fits the configured budget;
return everything in original order. Over budget: ask the ranker to order
pages by likely info density, greedily pack from the top, truncate the
boundary page to fit the remainder, drop the rest.
"""

from dataclasses import dataclass

from deckard.constants import TOKEN_ENCODING
from deckard.database.models import ScrapingResult
from deckard.services.extraction.ranker import rank_urls_with_llm

PAGE_HEADER_TEMPLATE = "--- PAGE: {url} ---\n"


@dataclass
class BudgetReport:
    fit: bool
    total_tokens_before: int
    total_tokens_after: int
    dropped_urls: list[str]
    truncated_url: str | None
    ranker_used: bool
    # Non-None when the ranker API call returned a response (even if we rejected the
    # parsed content). Used to persist the ranker's token usage on `llm_calls`.
    ranker_usage: dict | None = None


def page_header(url: str) -> str:
    return PAGE_HEADER_TEMPLATE.format(url=url)


def count_tokens(text: str) -> int:
    return len(TOKEN_ENCODING.encode(text))


def _page_block_tokens(url: str, body_tokens: int) -> int:
    return count_tokens(page_header(url)) + body_tokens


async def fit_to_budget(
    results: list[ScrapingResult],
    budget: int,
) -> tuple[list[tuple[str, str]], BudgetReport]:
    pages = [r for r in results if r.markdown]
    page_tokens = {r.url: _page_block_tokens(r.url, r.tokens or 0) for r in pages}
    pages_with_content: list[tuple[str, str]] = [(r.url, r.markdown) for r in pages]
    total_before = sum(page_tokens.values())

    if total_before <= budget:
        return pages_with_content, BudgetReport(
            fit=True,
            total_tokens_before=total_before,
            total_tokens_after=total_before,
            dropped_urls=[],
            truncated_url=None,
            ranker_used=False,
        )

    by_url = dict(pages_with_content)
    ranked, ranker_used, ranker_usage = await rank_urls_with_llm(list(by_url.keys()))

    fitted: list[tuple[str, str]] = []
    dropped: list[str] = []
    truncated_url: str | None = None
    consumed = 0

    for url in ranked:
        markdown = by_url[url]
        page_tok = page_tokens[url]
        remaining = budget - consumed
        # Once a boundary page has been truncated, the budget is conceptually exhausted —
        # every remaining URL goes to `dropped` even if a tiny round-trip slack would let it fit.
        if remaining <= 0 or truncated_url is not None:
            dropped.append(url)
            continue
        if page_tok <= remaining:
            fitted.append((url, markdown))
            consumed += page_tok
            continue
        # Boundary page: truncate the body so the full page block fits remaining budget.
        header_tokens = count_tokens(page_header(url))
        body_budget = max(0, remaining - header_tokens)
        truncated_md = TOKEN_ENCODING.decode(TOKEN_ENCODING.encode(markdown)[:body_budget])
        fitted.append((url, truncated_md))
        consumed += header_tokens + count_tokens(truncated_md)
        truncated_url = url

    return fitted, BudgetReport(
        fit=False,
        total_tokens_before=total_before,
        total_tokens_after=consumed,
        dropped_urls=dropped,
        truncated_url=truncated_url,
        ranker_used=ranker_used,
        ranker_usage=ranker_usage,
    )
