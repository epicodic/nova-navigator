"""Lock-tracking test doubles for the lock audit of `LazyDocument` (DEC-26 note 1)."""

from __future__ import annotations

import threading
from typing import Any, Self

from nova_editor.core import PieceTable
from nova_editor.document._lazy_document import LazyDocument

_GUARDED_PROPERTIES = frozenset({"length", "tree", "is_identity", "has_open_tail"})


class TrackedLock:
    """An `RLock` that knows which thread owns it."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._owner: int | None = None
        self._depth = 0

    def __enter__(self) -> Self:
        self._lock.acquire()
        self._owner = threading.get_ident()
        self._depth += 1
        return self

    def __exit__(self, *exc: object) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
        self._lock.release()

    def held(self) -> bool:
        """Whether the calling thread owns the lock."""
        return self._owner == threading.get_ident()


class GuardedTable:
    """Proxy of a `PieceTable` that records (and raises) every access made without the document lock."""

    def __init__(self, table: PieceTable, lock: TrackedLock) -> None:
        self._table = table
        self._lock = lock
        self.violations: list[str] = []

    def _check(self, what: str) -> None:
        if not self._lock.held():
            message = f"table.{what} without the document lock"
            self.violations.append(message)
            raise AssertionError(message)

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._table, name)
        if callable(value):

            def guarded(*args: object, **kwargs: object) -> object:
                self._check(f"{name}()")
                return value(*args, **kwargs)

            return guarded
        if name in _GUARDED_PROPERTIES:
            self._check(name)
        return value


def install_guards(doc: LazyDocument) -> tuple[TrackedLock, GuardedTable]:
    """Replace the lock and the table of `doc` by tracking doubles and return them."""
    lock = TrackedLock()
    guarded = GuardedTable(doc._table, lock)
    vars(doc).update(_lock=lock, _table=guarded)  # the proxies duck-type the two members
    return lock, guarded
