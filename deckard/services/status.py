"""Shared status-transition helper for ``ScrapingRequest`` and ``LLMProcessingJob``.

Lives in its own module — not in the pipeline — so the extraction stage can
import it without creating a circular dependency. The helper has no knowledge
of the pipeline itself; it just centralises the timestamp / attempt-count /
error-field bookkeeping every status change has to do.

It deliberately does NOT commit — transaction boundaries belong to the
orchestrator. Callers commit explicitly when they want a checkpoint visible
to other workers (or the user via a status poll).
"""

from datetime import UTC, datetime

from deckard.database.models import LLMProcessingJob, ScrapingRequest

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def transition_status(
    entity: ScrapingRequest | LLMProcessingJob,
    to: str,
    *,
    exc: BaseException | None = None,
) -> None:
    entity.status = to
    if entity.started_at is None:
        # First non-pending transition for this entity: stamp the start markers.
        # Both entity types carry attempt_count (guaranteed by the union type),
        # so the increment is unconditional — this is attempt #1 for the entity.
        entity.started_at = datetime.now(UTC)
        entity.attempt_count = (entity.attempt_count or 0) + 1
    if to in TERMINAL_STATUSES:
        entity.finished_at = datetime.now(UTC)
    if exc is not None:
        entity.error_code = type(exc).__name__
        entity.error_message = str(exc)[:1000]
