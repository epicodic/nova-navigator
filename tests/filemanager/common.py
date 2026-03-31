import threading

from nova_navigator.task import DecisionRequest, DecisionResponse, Task, TaskStatus


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
            response = pending.pop(0) if pending else DecisionResponse.YES
            val = task.send(response)
    except StopIteration:
        pass
    return requests
