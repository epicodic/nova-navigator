import asyncio
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto
from typing import ClassVar, Self


class Operation(ABC):
    class State(Enum):
        INITIALIZED = auto()
        RUNNING = auto()
        COMPLETED = auto()
        FAILED = auto()
        ABORTED = auto()

    @dataclass
    class Progress:
        state: "Operation.State"
        completed: int = 0
        total: int = 0
        text: str = ""

    _next_id: ClassVar[int] = 0

    _id: int
    _task: asyncio.Task[None] | None
    _progress: Progress
    _abort_requested_event: threading.Event

    def __init__(self) -> None:
        self._task = None
        self._id = Operation.generate_next_id()
        self._progress = Operation.Progress(Operation.State.INITIALIZED)
        self._abort_requested_event = threading.Event()

    @staticmethod
    def generate_next_id() -> int:
        next_id = Operation._next_id
        Operation._next_id += 1
        return next_id

    @property
    def id(self) -> int:
        """Unique identifier for the operation."""
        return self._id

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @property
    def progress(self) -> Progress:
        return self._progress

    @property
    def abort_requested_event(self) -> threading.Event:
        return self._abort_requested_event

    def set_state(self, state: "Operation.State") -> None:
        self._progress.state = state

    def set_progress(
        self, completed: int, text: str = "", total: int | None = None, state: "Operation.State" = State.RUNNING
    ) -> None:
        self._progress.completed = completed
        if total is not None:
            self._progress.total = total
        self._progress.text = text
        self._progress.state = state

    def get_undo_operation(self) -> "Operation | None":
        return None

    async def start(self) -> Self:
        """Starts a new task to execute the operation's _runner method."""
        if self._task is not None:
            raise RuntimeError("Operation is already running.")

        self._task = asyncio.create_task(asyncio.to_thread(self.process))
        return self

    def abort(self) -> None:
        """Abort the operation."""
        self._abort_requested_event.set()

    @property
    @abstractmethod
    def title(self) -> str:
        """Returns the title of the operation."""

    @abstractmethod
    def process(self) -> None:
        """Must be implemented by subclasses to define the operation's behavior."""
