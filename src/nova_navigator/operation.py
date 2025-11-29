import asyncio
from typing import Self


class Operation:
    _task: asyncio.Task | None

    def __init__(self) -> None:
        self._task = None

    @property
    def task(self) -> asyncio.Task | None:
        return self._task

    def get_undo_operation(self) -> "Operation | None":
        return None

    async def start(self) -> Self:
        """Starts a new task to execute the operation's _runner method."""
        if self._task is not None:
            raise RuntimeError("Operation is already running.")

        self._task = asyncio.create_task(asyncio.to_thread(self._runner))
        return self

    def _runner(self) -> None:
        """Must be implemented by subclasses to define the operation's behavior."""
        raise NotImplementedError
