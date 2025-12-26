import time

from .operation import Operation


class DummyOperation(Operation):
    """A dummy operation for testing purposes."""

    def __init__(self) -> None:
        super().__init__()
        self._completed = 0
        self._total = 100

    @property
    def title(self) -> str:
        return "Dummy Operation"

    def process(self) -> None:
        self._completed = 0
        for _ in range(self._total):
            self._completed += 1
            if self.abort_requested_event.is_set():
                self.set_state(Operation.State.ABORTED)
                return
            self.set_progress(completed=self._completed, total=self._total, text="Processing...")
            time.sleep(1)  # Simulate work being done
        self.set_state(Operation.State.COMPLETED)
