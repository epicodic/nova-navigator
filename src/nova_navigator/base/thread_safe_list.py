import threading


class ThreadSafeList[T]:
    _list: list[T]
    _lock: threading.Lock

    def __init__(self, initial: list[T] | None = None) -> None:
        self._list = initial or []
        self._lock = threading.Lock()

    def append(self, item: T) -> None:
        with self._lock:
            self._list.append(item)

    def pop(self, index: int = -1) -> T:
        with self._lock:
            return self._list.pop(index)

    def peek(self, index: int = -1) -> T:
        with self._lock:
            return self._list[index]

    def pop_front(self) -> T:
        return self.pop(0)

    def peek_front(self) -> T:
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

    def __exit__(self, _exc_type, _exc_val, _exc_tb) -> None:  # noqa: ANN001
        self._lock.release()
