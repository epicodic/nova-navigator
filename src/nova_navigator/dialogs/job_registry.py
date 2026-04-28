import contextlib
from collections import deque

from nova_navigator.scheduler import Job

MAX_FINISHED_JOBS = 20  # TODO: replace with config value
_TERMINAL_STATES = frozenset({Job.State.COMPLETED, Job.State.CANCELED, Job.State.FAILED})


class JobRegistry:
    """Tracks running and recently-finished jobs.

    Call :meth:`add_job` when a job is started.
    Call :meth:`update` periodically (e.g. on a timer tick) to transition
    jobs that have left the RUNNING state into the finished history.
    Call :meth:`remove_job` when the user dismisses a finished job.
    """

    _running: list[Job]
    _finished: deque[Job]

    def __init__(self) -> None:
        self._running = []
        self._finished: deque[Job] = deque(maxlen=MAX_FINISHED_JOBS)

    def add_job(self, job: Job) -> None:
        """Register *job* as a running job."""
        self._running.append(job)

    def update(self) -> None:
        """Move any terminal jobs from the running list to the finished history."""
        still_running: list[Job] = []
        for job in self._running:
            if job.state in _TERMINAL_STATES:
                self._finished.appendleft(job)
            else:
                still_running.append(job)
        self._running = still_running

    def remove_job(self, job: Job) -> None:
        """Remove *job* from the finished history (user dismissed it)."""
        with contextlib.suppress(ValueError):
            self._finished.remove(job)

    @property
    def running_jobs(self) -> list[Job]:
        """Snapshot of currently running jobs."""
        return list(self._running)

    @property
    def finished_jobs(self) -> list[Job]:
        """Snapshot of finished jobs, newest first."""
        return list(self._finished)
