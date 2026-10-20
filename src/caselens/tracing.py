"""Context-local attribution prevents shared clients from mixing batch run records."""

from contextvars import ContextVar

from caselens.schemas import CallRecord

CURRENT_RUN: ContextVar[str | None] = ContextVar("caselens_run_id", default=None)
CURRENT_CALLS: ContextVar[list[CallRecord] | None] = ContextVar("caselens_calls", default=None)


def record_call(record: CallRecord, standalone: list[CallRecord]):
    calls = CURRENT_CALLS.get()
    (standalone if calls is None else calls).append(record)
