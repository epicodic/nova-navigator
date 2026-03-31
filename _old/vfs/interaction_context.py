import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, NewType


@dataclass
class Progress:
    completed: int = 0
    total: int = 0


class DecisionChoice(Enum):
    YES = auto()
    YES_TO_ALL = auto()
    NO = auto()
    NO_TO_ALL = auto()


@dataclass
class Message:
    message: str
    args: list[Any] | None = None


DecisionId = NewType("DecisionId", int)


@dataclass
class DecisionRequest:
    id: DecisionId
    message: Message
    data: Any


@dataclass
class DecisionResponse:
    id: DecisionId
    choice: DecisionChoice
    data: Any


class OperationCancelled(Exception):
    """Raised when an operation is cancelled by the user."""


class OperationError(Exception):
    """Raised when an operation encounters an error."""

    message: Message


class InteractionContext:
    """Context for user interactions during VFS operations.

    Provides:
     * methods to prompt the user for decisions or confirmations,
     * progress reporting mechanisms,
     * cancellation handling.
    """

    DecisionCallback = Callable[["InteractionContext", DecisionId, Message], DecisionChoice]
    ProgressUpdateCallback = Callable[["InteractionContext"], None]

    _progress_callback: ProgressUpdateCallback
    _decision_callback: DecisionCallback
    _cancel_event: threading.Event | None
    _progress: Progress

    _decision_mutex: threading.Lock
    _pending_decisions: dict[DecisionId, DecisionRequest]
    _answered_decisions: list[DecisionResponse]
    _decisions_for_all: dict[str, DecisionChoice]
    _next_decision_id: int

    def __init__(
        self,
        cancel_event: threading.Event,
        progress_callback: ProgressUpdateCallback,
        decision_callback: DecisionCallback,
    ) -> None:
        self._cancel_event = cancel_event
        self._progress_callback = progress_callback
        self._decision_callback = decision_callback
        self._progress = Progress()
        self._decision_mutex = threading.Lock()
        self._pending_decisions = {}
        self._answered_decisions = []
        self._decisions_for_all = {}
        self._next_decision_id = 0

    @property
    def progress(self) -> Progress:
        return self._progress

    @property
    def cancel_event(self) -> threading.Event | None:
        return self._cancel_event

    @property
    def progress_callback(self) -> ProgressUpdateCallback:
        return self._progress_callback

    @property
    def decision_callback(self) -> DecisionCallback:
        return self._decision_callback

    def check_cancelled(self) -> None:
        if self._cancel_event and self._cancel_event.is_set():
            raise OperationCancelled

    def update_progress(self, inc_completed: int = 0, inc_total: int = 0) -> None:
        self._progress.total += inc_total
        self._progress.completed += inc_completed
        self._progress_callback(self)

    def set_progress(self, completed: int, total: int) -> None:
        self._progress.completed = completed
        self._progress.total = total
        self._progress_callback(self)

    def set_completed(self) -> None:
        self.set_progress(self._progress.total, self._progress.total)

    def _generate_decision_id(self) -> DecisionId:
        with self._decision_mutex:
            decision_id = DecisionId(self._next_decision_id)
            self._next_decision_id += 1
            return decision_id

    def request_decision(self, message: Message, data: Any) -> None:
        decision_id = self._generate_decision_id()
        # check for existing "to all" decisions
        if message.message in self._decisions_for_all:
            choice = self._decisions_for_all[message.message]
            self._add_decision_response(decision_id, choice, data)
            return

        with self._decision_mutex:
            self._pending_decisions[decision_id] = DecisionRequest(id=decision_id, message=message, data=data)

        self._decision_callback(self, decision_id, message)

    def send_decision_response(self, decision_id: DecisionId, choice: DecisionChoice) -> None:
        with self._decision_mutex:
            request = self._pending_decisions.pop(decision_id)
        self._add_decision_response(decision_id, choice, request.data)
        self._add_answer_for_all(request.message, choice)

    def _add_decision_response(self, decision_id: DecisionId, choice: DecisionChoice, data: Any) -> None:
        with self._decision_mutex:
            self._answered_decisions.append(DecisionResponse(id=decision_id, choice=choice, data=data))

    def _add_answer_for_all(self, message: Message, choice: DecisionChoice) -> None:
        if choice not in (DecisionChoice.YES_TO_ALL, DecisionChoice.NO_TO_ALL):
            return

        if choice == DecisionChoice.YES_TO_ALL:
            choice = DecisionChoice.YES
        if choice == DecisionChoice.NO_TO_ALL:
            choice = DecisionChoice.NO

        self._decisions_for_all[message.message] = choice

        # Remove existing pending decisions with the same message
        with self._decision_mutex:
            to_remove = [
                decision_id
                for decision_id, decision_request in self._pending_decisions.items()
                if decision_request.message.message == message.message
            ]

        for decision_id in to_remove:
            self.send_decision_response(decision_id, choice)
