import pytest

from deerflow.runtime.runs.worker import _flush_journal_before_terminal_status


class _Journal:
    def __init__(self) -> None:
        self.flushed = False

    async def flush(self) -> None:
        self.flushed = True


class _FailingJournal:
    async def flush(self) -> None:
        raise RuntimeError("flush failed")


@pytest.mark.asyncio
async def test_flush_journal_before_terminal_status_flushes_when_present():
    journal = _Journal()

    await _flush_journal_before_terminal_status(journal, "run-1")

    assert journal.flushed is True


@pytest.mark.asyncio
async def test_flush_journal_before_terminal_status_tolerates_missing_journal():
    await _flush_journal_before_terminal_status(None, "run-1")


@pytest.mark.asyncio
async def test_flush_journal_before_terminal_status_swallows_flush_errors():
    await _flush_journal_before_terminal_status(_FailingJournal(), "run-1")
