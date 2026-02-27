from unittest.mock import MagicMock

from nova_navigator.dialogs.job_registry import MAX_FINISHED_JOBS, JobRegistry
from nova_navigator.scheduler import Job


def _make_job(title: str = "test", state: Job.State = Job.State.RUNNING) -> MagicMock:
    """Create a Job with a mocked internal state for testing."""
    job = MagicMock(spec=Job)
    job.title = title
    job.state = state
    return job


def test_add_job_appears_in_running() -> None:
    registry = JobRegistry()
    job = _make_job()
    registry.add_job(job)
    assert job in registry.running_jobs


def test_update_moves_completed_job_to_finished() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    job.state = Job.State.COMPLETED
    registry.update()
    assert job not in registry.running_jobs
    assert job in registry.finished_jobs


def test_update_moves_failed_job_to_finished() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    job.state = Job.State.FAILED
    registry.update()
    assert job in registry.finished_jobs


def test_update_moves_canceled_job_to_finished() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    job.state = Job.State.CANCELED
    registry.update()
    assert job in registry.finished_jobs


def test_running_job_stays_running_after_update() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    registry.update()
    assert job in registry.running_jobs
    assert job not in registry.finished_jobs


def test_remove_job_removes_from_finished() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.RUNNING)
    registry.add_job(job)
    job.state = Job.State.COMPLETED
    registry.update()
    registry.remove_job(job)
    assert job not in registry.finished_jobs


def test_remove_job_noop_if_not_present() -> None:
    registry = JobRegistry()
    job = _make_job(state=Job.State.COMPLETED)
    registry.remove_job(job)  # should not raise


def test_finished_jobs_capped_at_max() -> None:
    registry = JobRegistry()
    jobs = [_make_job(f"job-{i}", state=Job.State.RUNNING) for i in range(MAX_FINISHED_JOBS + 3)]
    for job in jobs:
        registry.add_job(job)
    for job in jobs:
        job.state = Job.State.COMPLETED
    registry.update()
    assert len(registry.finished_jobs) == MAX_FINISHED_JOBS


def test_finished_jobs_newest_first() -> None:
    registry = JobRegistry()
    j1 = _make_job("first", Job.State.RUNNING)
    j2 = _make_job("second", Job.State.RUNNING)
    registry.add_job(j1)
    registry.add_job(j2)
    j1.state = Job.State.COMPLETED
    registry.update()
    j2.state = Job.State.COMPLETED
    registry.update()
    result = registry.finished_jobs
    assert result[0] is j2
    assert result[1] is j1


def test_initialized_job_not_moved_to_finished() -> None:
    """A job in INITIALIZED state should not be moved to finished by update()."""
    registry = JobRegistry()
    job = _make_job(state=Job.State.INITIALIZED)
    registry.add_job(job)
    registry.update()
    assert job in registry.running_jobs
    assert job not in registry.finished_jobs
