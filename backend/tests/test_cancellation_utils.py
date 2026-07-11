import asyncio

import pytest

from deerflow.utils.cancellation import await_task_through_repeated_cancellation


@pytest.mark.anyio
async def test_await_task_through_repeated_cancellation_waits_for_result():
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation():
        started.set()
        await release.wait()
        return "done"

    inner = asyncio.create_task(operation())
    waiter = asyncio.create_task(await_task_through_repeated_cancellation(inner))
    await started.wait()

    waiter.cancel()
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.sleep(0)

    assert not waiter.done()
    release.set()
    assert await waiter == "done"
