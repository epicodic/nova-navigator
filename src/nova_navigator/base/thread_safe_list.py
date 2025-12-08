import threading


class ThreadSafeList[T]:
    """A list wrapper that serialises all mutations and reads with a :class:`threading.Lock`.

    Can also be used as a context manager (``with my_list as raw:``), which
    holds the lock for the duration of the ``with`` block and exposes the
    underlying list directly for bulk operations.
    """

    _list: list[T]
    _lock: threading.Lock

    def __init__(self, initial: list[T] | None = None) -> None:
        self._list = initial or []
        self._lock = threading.Lock()

    def append(self, item: T) -> None:
        """Append *item* to the end of the list under the lock."""
        with self._lock:
            self._list.append(item)

    def pop(self, index: int = -1) -> T:
        """Remove and return the item at *index* (default: last) under the lock."""
        with self._lock:
            return self._list.pop(index)

    def peek(self, index: int = -1) -> T:
        """Return the item at *index* without removing it, under the lock."""
        with self._lock:
            return self._list[index]

    def pop_front(self) -> T:
        """Remove and return the first item."""
        return self.pop(0)

    def peek_front(self) -> T:
        """Return the first item without removing it."""
        return self.peek(0)

    def __len__(self) -> int:
        with self._lock:
            return len(self._list)

    def __getitem__(self, index: int) -> T:
        with self._lock:
            return self._list[index]

    def __enter__(self) -> list[T]:
        self._lock.acquire()
        return self._list

    def __exit__(self, _exc_type: type[BaseException] | None, _exc_val: BaseException | None, _exc_tb: object) -> None:
        self._lock.release()
