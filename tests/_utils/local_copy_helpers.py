"""Shared test helpers for local-copy tests."""

from __future__ import annotations

import asyncio
from pathlib import PurePath

from nova_navigator.scheduler import Job, ResponseRequest
from nova_navigator.vfs.filesystem import AtomicWriterLike
from nova_navigator.vfs.vpath import VPath
from nova_widgets import Response

from .mock_filesystem import MockFilesystem


class SchemeFs(MockFilesystem):
    """MockFilesystem whose URIs look like an SSH host, for path-mapping tests."""

    def uri_for_path(self, path: PurePath) -> str:
        return f"ssh://user@host:2222{path}"


def overwrite(fs: MockFilesystem, path: str, data: bytes) -> None:
    """Write *data* to *path* through *fs*, advancing the mock's recorded mtime."""
    writer = fs.write(VPath(path, fs))
    writer.write(data)
    writer.close()


class RefusingFs(SchemeFs):
    """SchemeFs whose write-back always fails without touching the source, like an unreachable server."""

    def write_atomic(self, path: VPath) -> AtomicWriterLike:
        raise OSError("disk full")


class ScriptedRunner:
    """A job starter that runs every job to completion and answers its prompts from a scripted list of responses."""

    def __init__(self, answers: list[Response] | None = None) -> None:
        self.answers = answers or []
        self.jobs: list[Job] = []
        self.prompts: list[str] = []

    async def __call__(self, job: Job) -> None:
        self.jobs.append(job)

        async def answer(request: ResponseRequest, future: asyncio.Future[Response]) -> None:
            self.prompts.append(request.title)
            future.set_result(self.answers.pop(0) if self.answers else Response.CANCEL)  # an unscripted prompt is recorded in `prompts` and declined

        await job.start(answer)

    @property
    def sync_jobs(self) -> list[Job]:
        """The sync jobs started so far."""
        return [job for job in self.jobs if job.title.startswith("Sync:")]
