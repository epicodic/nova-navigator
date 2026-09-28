"""Scheduler tasks for local copies.

These run in worker threads (see ``docs/scheduler.md``) and touch only the ``LocalCopy``
passed in and their ``TaskResult`` out-parameter; the manager mutates its registry only
after ``await job.start(...)`` returns on the GUI loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from nova_navigator.response import Response
from nova_navigator.scheduler import TaskContext
from nova_navigator.vfs.local_copy import LocalCopy, ReuseAction
from nova_navigator.vfs.vpath import VPath


@dataclass
class TaskResult:
    """Mutable out-parameter a job's caller inspects after ``Job.start()`` returns."""

    copy: LocalCopy | None = None
    conflict: bool = False
    opened: bool = False
    error: str | None = None
    details: list[str] = field(default_factory=list)


def _progress(ctx: TaskContext, total: int) -> Callable[[int], None]:
    """Set the step total to *total* and return a callback that advances it."""
    ctx.status.set_step_progress(0, total)
    return lambda amount: ctx.status.update_step_progress(amount)


async def create_copy_task(ctx: TaskContext, source: VPath, root: Path, read_only: bool, result: TaskResult) -> None:
    """Download *source* into a fresh :class:`LocalCopy` below *root*."""
    ctx.set_current_item(source.uri)
    size = source.filesystem.stat(source).size
    result.copy = LocalCopy.create(source, root, read_only=read_only, progress=_progress(ctx, size))
    result.opened = True
    ctx.status.set_completed()


async def reopen_copy_task(ctx: TaskContext, copy: LocalCopy, result: TaskResult) -> None:
    """Apply the reuse-on-reopen table to an already-open *copy*.

    On a REUSE or REFRESH decision *copy* is left ready to open. On a CONFLICT
    decision the user is asked to overwrite the source, discard local edits, or
    cancel the reopen; ``result.opened`` stays False on Cancel.
    """
    ctx.set_current_item(copy.source.uri)
    action = copy.reuse_action()
    if action is ReuseAction.CONFLICT:
        answer = await ctx.request_response(
            "Source and local copy both changed",
            [Response.OVERWRITE, Response.DISCARD, Response.CANCEL],
            f"{copy.source.uri}\n\nOverwrite: write the local copy to the source and open it.\nDiscard: drop local edits and open the current source.",
        )
        if answer == Response.OVERWRITE:
            copy.write_back()
        elif answer == Response.DISCARD:
            copy.refresh()
        else:
            ctx.status.set_completed()
            return
    elif action is ReuseAction.REFRESH:
        copy.refresh()
    result.copy = copy
    result.opened = True
    ctx.status.set_completed()


async def sync_copy_task(ctx: TaskContext, copy: LocalCopy, force: bool, result: TaskResult) -> None:
    """Write *copy* back to its source, asking about a changed source unless *force*.

    ``force`` skips the conflict check entirely (used for a manual sync of a copy
    already in the CONFLICT status: the user has already been asked once and a manual
    sync means "overwrite"). Otherwise, a changed source asks Overwrite/Skip; anything
    but Overwrite sets ``result.conflict`` and leaves the source untouched.
    """
    ctx.set_current_item(copy.source.uri)
    if not force and copy.source_changed():
        answer = await ctx.request_response(
            "Source changed since the local copy was made",
            [Response.OVERWRITE, Response.SKIP],
            f"{copy.source.uri}\n\nOverwrite: replace the source with the local copy.\nSkip: keep the local copy only and pause automatic sync.",
        )
        if answer != Response.OVERWRITE:
            result.conflict = True
            ctx.status.set_completed()
            return
    copy.write_back(progress=_progress(ctx, copy.path.stat().st_size))
    ctx.status.set_completed()
