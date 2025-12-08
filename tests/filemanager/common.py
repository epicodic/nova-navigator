import threading

from nova_navigator.task import DecisionRequest, DecisionResponse, Task, TaskStatus
from tests.mock_filesystem import MockFilesystem


def make_status(print_progress: bool = False, cancel_event: threading.Event | None = None) -> TaskStatus:
    def progress_callback(status: TaskStatus) -> None:
        print(
            f"Progress: {status.progress.completed}/{status.progress.total} "
            f"(step {status.progress.step_completed}/{status.progress.step_total})"
        )

    if print_progress:
        cb = progress_callback
    else:

        def cb(_: TaskStatus) -> None:
            return None

    return TaskStatus(
        cancel_event=cancel_event or threading.Event(),
        progress_callback=cb,
    )


def read_all(fs: MockFilesystem, path: str) -> bytes:
    reader = fs.read(fs.path(path))
    data = reader.read(1024 * 1024)  # read up to 1 MiB, which should be enough for all test cases
    reader.close()
    return data


def run_task(task: Task, decisions: list[DecisionResponse] | None = None) -> list[DecisionRequest]:
    """Drive a Task to completion, supplying decisions as needed.

    Returns the list of DecisionRequests that were yielded.
    """
    pending = list(decisions or [])
    requests: list[DecisionRequest] = []
    try:
        val = next(task)
        while True:
            if not isinstance(val, DecisionRequest):
                raise AssertionError(f"Unexpected yield type: {type(val)}")
            requests.append(val)
            if not pending:
                raise AssertionError("Task yielded a DecisionRequest but no more decisions are specified")
            response = pending.pop(0)
            val = task.send(response)
    except StopIteration:
        pass
    return requests
