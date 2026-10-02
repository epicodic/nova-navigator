"""State and thread body of one widget search (ACT6 design 6): the UI thread owns the run, the `nova-search` thread reads `job` and posts the outcome."""

from __future__ import annotations

import dataclasses
import logging
import threading
from collections.abc import Callable

from nova_editor.core import SourceChanged
from nova_editor.core.search import SearchCancelled as SearchCancelledError
from nova_editor.core.search import SearchError, SearchJob, SearchProgress, SearchResult, SearchStale


@dataclasses.dataclass
class SearchOutcome:
    """What the search thread hands to the UI thread: a result (or none), an error, or the cancellation."""

    result: SearchResult | None = None
    error: Exception | None = None
    cancelled: bool = False


@dataclasses.dataclass
class SearchRun:
    """The state of one running search; the UI thread owns it, the `nova-search` thread reads `job`."""

    job: SearchJob
    needle: str
    backward: bool
    reason: str = "cancelled"
    """Why the run was cancelled (`replaced`, `text changed`, `reloaded`, `cancelled`)."""
    abandoned: bool = False
    """True once the widget was closed: the thread then discards its outcome."""
    progress: SearchProgress | None = None
    """The latest progress report of the job."""
    progress_outstanding: bool = False
    """True while a call to the UI thread that announces `progress` is on its way (coalescing)."""
    last_message: float = 0.0
    """Time (widget clock) of the latest `SearchProgress` message."""
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    """Guards `progress`, `progress_outstanding` and the reads of `abandoned` by the thread."""


def run_search_thread(run: SearchRun, post: Callable[[SearchOutcome], None]) -> None:
    """Thread body: run the job and post exactly one outcome from `finally`, so that no defect leaves the widget in the searching state."""
    outcome = SearchOutcome(error=RuntimeError("the search thread ended unexpectedly"))
    try:
        outcome = SearchOutcome(result=run.job.run())
    except SearchCancelledError:
        outcome = SearchOutcome(cancelled=True)
    except Exception as failure:
        if not isinstance(failure, SearchError | SearchStale | SourceChanged):
            logging.getLogger(__name__).exception("the search failed")
        outcome = SearchOutcome(error=failure)
    finally:
        post(outcome)
