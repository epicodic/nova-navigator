# Task Scheduler Framework

Nova Navigator provides an async task scheduler that runs long-running operations in worker threads while maintaining the ability to request user decisions through the GUI.
This document describes the framework, its API, and how to implement new tasks.

## Overview

The task scheduler enables:

- **Long-running operations** to run asynchronously without blocking the GUI
- **Progress tracking** with two-level granularity (overall items and per-item steps)
- **User decisions** requested mid-operation (e.g., "File exists, overwrite?")
- **Cancellation** handling when the user terminates a task
- **Concurrency** within tasks using asyncio

The framework uses an isolated event loop in a worker thread.
This prevents long-running I/O operations from stalling the Textual GUI event loop.

## Architecture

### Event Loop Isolation

The GUI runs on the main thread with its own asyncio event loop (managed by Textual).
When a task is executed:

1. A new worker thread is spawned
2. A fresh asyncio event loop is created in the worker thread
3. The task runs in this isolated loop
4. Decision requests are bridged back to the GUI loop via `asyncio.run_coroutine_threadsafe()`

This design allows tasks to use async/await syntax freely while the GUI remains responsive.

### Decision Serialization

An `asyncio.Lock` inside `AsyncTaskScheduler` ensures that at most one GUI dialog is in flight at a time.
When multiple concurrent subtasks call `request_decision()` simultaneously, they queue up on the lock.
`ALL`/`NONE`/`SKIP_ALL` responses are cached by title and applied automatically to subsequent identical requests, bypassing the lock and dialog entirely.

## Core Components

### `Decision`

An enum flag representing user choices in response to a dialog.
Common decisions:

- `Decision.YES` / `Decision.NO` — binary choice
- `Decision.OK` / `Decision.CANCEL` — confirmation
- `Decision.RETRY` / `Decision.SKIP` — error recovery
- `Decision.ALL` / `Decision.NONE` — "apply to all" variants of YES/NO
- `Decision.SKIP_ALL` — "apply to all" variant of SKIP

Decisions have properties:

- `is_negative` — `True` if the decision carries the negative bit (NO, CANCEL, SKIP, NONE, SKIP_ALL)
- `is_positive` — `True` if `not is_negative` (YES, OK, RETRY, ALL)
- `is_to_all` — `True` if the modifier bit is set (ALL, NONE, SKIP_ALL)

Use `is_decision()` to compare against a specific value:

```python
if decision.is_decision(Decision.YES) or decision.is_decision(Decision.ALL):
    # user said yes
```

### `DecisionRequest`

Represents a request for user input, passed to the GUI callback.
Contains:

- `title: str` — dialog title; also the deduplication key for caching "apply to all" decisions
- `expected_decisions: list[Decision]` — the choices available to the user
- `message: str` — the dialog message

### `Progress`

Tracks task progress with two levels of granularity:

- `completed` — number of high-level items processed (e.g., files)
- `total` — total number of items to process
- `step_completed` — bytes/items processed in the current item
- `step_total` — total bytes/items in the current item

Example: copying 5 files totaling 100 MB would have `total=5` at the overall level and `step_total` set to the size of each file as it is copied.

### `TaskStatus`

Thread-safe holder for progress state and cancellation.
Shared between the worker thread and the GUI thread.

**Key methods:**

- `check_cancelled() -> None` — raises `TaskCancelled` if the user cancelled
- `update_progress(inc_completed=0, inc_total=0) -> None` — increment counters
- `set_progress(completed, total) -> None` — set absolute values
- `set_completed() -> None` — mark overall task complete (sets completed = total)
- `update_step_progress(inc_completed=0, inc_total=0) -> None` — increment step counters
- `set_step_progress(completed, total) -> None` — set step counters
- `set_step_completed() -> None` — mark step complete
- `is_complete() -> bool` — `True` when both overall and step completed >= total

Progress updates trigger a callback to refresh the GUI.

### `TaskContext`

The primary interface for task code.
Passed as the first argument to every async task function.

**Properties:**

- `status: TaskStatus` — access to progress/cancellation state

**Methods:**

- `async request_decision(title, expected_decisions, message) -> Decision` — pause and ask the user
- `async subtask(coro: Coroutine[Any, Any, R]) -> asyncio.Task[R]` — spawn a concurrent task and yield control so it can begin

### `GuiRequestCallback`

Type alias for the callback that the GUI must supply to show decision dialogs:

```python
GuiRequestCallback = Callable[[DecisionRequest, asyncio.Future[Decision]], Awaitable[None]]
```

The callback receives the request and a `Future` that must be resolved with the user's choice.

### `AsyncTaskScheduler`

The main scheduler that runs tasks in worker threads.

**Static method:**

```python
@staticmethod
async def execute(
    gui_request_callback: GuiRequestCallback,
    task_fn: Callable[[TaskContext], Awaitable[None]],
    status: TaskStatus,
) -> AsyncTaskScheduler
```

Runs `task_fn` in a worker thread and awaits completion.
The `gui_request_callback` is invoked whenever a `DecisionRequest` needs to be shown to the user.

## Implementing a Task

A task is an async function that accepts a `TaskContext` as its only argument.

```python
async def my_task(ctx: TaskContext) -> None:
    """Example task implementation."""
    # Set up progress
    ctx.status.update_progress(inc_total=10)

    for i in range(10):
        # Check if user cancelled
        ctx.status.check_cancelled()

        # Do work...
        await some_async_operation()

        # Update progress
        ctx.status.update_progress(inc_completed=1)
```

### Common Patterns

#### Request a User Decision

```python
decision = await ctx.request_decision(
    title="Overwrite file",
    expected_decisions=[Decision.YES, Decision.NO, Decision.ALL, Decision.NONE],
    message="File 'data.txt' already exists. Overwrite?",
)

if decision.is_positive:
    # User clicked YES or ALL
    overwrite_file()
elif decision.is_negative:
    # User clicked NO or NONE
    skip_file()
```

#### Track Per-Item Progress

For operations on multiple items, use overall progress for items and step progress for sub-operations:

```python
async def copy_files(ctx: TaskContext, files: list[str]) -> None:
    ctx.status.update_progress(inc_total=len(files))

    for file_path in files:
        ctx.status.check_cancelled()

        # Get file size and set step total
        size = os.path.getsize(file_path)
        ctx.status.set_step_progress(0, size)

        # Copy in chunks
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                # ... write chunk ...
                ctx.status.update_step_progress(inc_completed=len(chunk))

        ctx.status.update_progress(inc_completed=1)
```

#### Run Concurrent Subtasks

**Spawning subtasks via `ctx.subtask()` is the preferred pattern for operations on multiple items.**

Use subtasks to allow work to proceed concurrently; this enables other subtasks to make progress while one waits for user feedback:

```python
async def process_many(ctx: TaskContext, items: list[str]) -> None:
    tasks = []
    for item in items:
        t = await ctx.subtask(process_one(ctx, item))
        tasks.append(t)

    # Wait for all to complete
    await asyncio.gather(*tasks)
```

**Why this matters:** If `process_one()` calls `ctx.request_decision()` for item A, that subtask blocks on the lock waiting for the user to respond.
Meanwhile, subtasks for items B and C continue running in the worker thread, so you get progress without stalling the entire operation.

Without subtasks, sequential processing would stall completely whenever user feedback is needed.

**Notes:**

- Each subtask shares the same `TaskContext` and updates shared progress counters
- Only one GUI dialog is shown at a time (the lock in `AsyncTaskScheduler` serializes requests)
- See `filemanager/tasks.py:_copy_dir_recursive()` (line 98) and `copy_files()` (line 120) for production examples

#### Handle Cancellation

```python
async def long_operation(ctx: TaskContext) -> None:
    for i in range(1000):
        # Raises TaskCancelled if user hit cancel
        ctx.status.check_cancelled()

        await do_work()
```

## Reference

- **Framework:** `nova_navigator/task.py`
- **Task implementations:** `nova_navigator/filemanager/tasks.py`
- **Decision enum:** `nova_navigator/decision.py`
- **Architecture guide:** See `AGENTS.md` for the broader threading model and async architecture
