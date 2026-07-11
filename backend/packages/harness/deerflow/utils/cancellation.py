import asyncio


async def await_task_through_repeated_cancellation[T](
    task: asyncio.Task[T],
) -> T:
    """Wait for *task* while suppressing cancellation of the current waiter."""
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()
