"""Unit tests for the shared ``transition_status`` helper.

Pure in-memory tests: SQLAlchemy models are instantiated without a session,
attributes are set by hand, and the helper is exercised directly. No DB,
no commit, no async.
"""

from __future__ import annotations

from datetime import UTC, datetime

from deckard.database.models import LLMProcessingJob, ScrapingRequest
from deckard.services.status import TERMINAL_STATUSES, transition_status


def _make_request(*, attempt_count: int | None = 0) -> ScrapingRequest:
    request = ScrapingRequest()
    request.status = "pending"
    request.started_at = None
    request.finished_at = None
    request.attempt_count = attempt_count
    request.error_code = None
    request.error_message = None
    return request


def _make_job() -> LLMProcessingJob:
    job = LLMProcessingJob()
    job.status = "pending"
    job.started_at = None
    job.finished_at = None
    job.error_code = None
    job.error_message = None
    return job


class TestScrapingRequestFirstTransition:
    def test_status_and_start_markers_stamped(self) -> None:
        request = _make_request(attempt_count=0)

        transition_status(request, "scraping")

        assert request.status == "scraping"
        assert isinstance(request.started_at, datetime)
        assert request.started_at.tzinfo == UTC
        assert request.attempt_count == 1

    def test_started_at_none_counts_as_first(self) -> None:
        request = _make_request(attempt_count=None)

        transition_status(request, "scraping")

        # (attempt_count or 0) + 1 -> None is treated as 0.
        assert request.attempt_count == 1

    def test_existing_attempt_count_is_incremented(self) -> None:
        request = _make_request(attempt_count=2)

        transition_status(request, "scraping")

        assert request.attempt_count == 3

    def test_non_terminal_status_leaves_finished_at_unset(self) -> None:
        request = _make_request()

        transition_status(request, "scraping")

        assert request.finished_at is None


class TestScrapingRequestSecondTransition:
    def test_start_markers_not_restamped(self) -> None:
        request = _make_request(attempt_count=0)

        transition_status(request, "scraping")
        first_started_at = request.started_at
        assert request.attempt_count == 1

        transition_status(request, "processing")

        assert request.status == "processing"
        # started_at is the first-transition stamp, untouched.
        assert request.started_at is first_started_at
        # attempt_count is NOT bumped a second time.
        assert request.attempt_count == 1


class TestTerminalStatuses:
    def test_each_terminal_status_stamps_finished_at(self) -> None:
        assert TERMINAL_STATUSES == frozenset({"completed", "failed", "cancelled"})
        for status in TERMINAL_STATUSES:
            request = _make_request()

            transition_status(request, status)

            assert request.status == status
            assert isinstance(request.finished_at, datetime)
            assert request.finished_at.tzinfo == UTC
            assert request.finished_at >= request.started_at


class TestErrorRecording:
    def test_exc_sets_code_and_message(self) -> None:
        request = _make_request()
        exc = ValueError("boom")

        transition_status(request, "failed", exc=exc)

        assert request.error_code == "ValueError"
        assert request.error_message == "boom"

    def test_message_truncated_to_1000_chars(self) -> None:
        request = _make_request()
        exc = RuntimeError("x" * 2000)

        transition_status(request, "failed", exc=exc)

        assert request.error_code == "RuntimeError"
        assert len(request.error_message) == 1000
        assert request.error_message == "x" * 1000

    def test_no_exc_leaves_error_fields_untouched(self) -> None:
        request = _make_request()

        transition_status(request, "scraping")

        assert request.error_code is None
        assert request.error_message is None


class TestLLMProcessingJob:
    def test_first_transition_stamps_start_markers(self) -> None:
        job = _make_job()
        job.attempt_count = 0

        transition_status(job, "processing")

        assert job.status == "processing"
        assert isinstance(job.started_at, datetime)
        assert job.started_at.tzinfo == UTC
        assert job.finished_at is None
        # Jobs track attempts just like requests do — this is processing attempt #1.
        assert job.attempt_count == 1

    def test_attempt_count_incremented_like_requests(self) -> None:
        job = _make_job()
        job.attempt_count = 2

        transition_status(job, "processing")

        assert job.attempt_count == 3

    def test_terminal_status_with_exc(self) -> None:
        job = _make_job()
        exc = KeyError("missing")

        transition_status(job, "failed", exc=exc)

        assert job.status == "failed"
        assert isinstance(job.finished_at, datetime)
        assert job.finished_at.tzinfo == UTC
        assert job.error_code == "KeyError"
        assert job.error_message == str(exc)
