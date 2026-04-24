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

## Core Components

### `Decision`

An enum flag representing user choices in response to a dialog.
Common decisions:

- `Decision.YES` / `Decision.NO` — binary choice
- `Decision.OK` / `Decision.CANCEL` — confirmation
- `Decision.RETRY` / `Decision.SKIP` — error recovery
- `Decision.ALL` / `Decision.NONE` — "apply to all" variants
- `Decision.SKIP_ALL` — skip all subsequent items

Decisions have properties:
- `is_positive` — whether the decision is affirmative (YES, OK, RETRY)
- `is_negative` — whether the decision is negative (NO, CANCEL, SKIP)
- `is_to_all` — whether this is an "apply to all" decision (ALL, NONE, SKIP_ALL)

Use the `is_decision()` method to check specific choices:
```python
if decision.is_decision(Decision.YES) or decision.is_decision(Decision.ALL):
    # user said yes
```

### `DecisionRequest`

Represents a request for user input, yielded by tasks to pause execution.
Contains:

- `title: str` — dialog title; also the deduplication key for caching "apply to all" decisions
- `expected_decisions: list[Decision]` — the choices available to the user
- `message: str` — the dialog message (may reference context variables)

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
- `set_completed() -> None` — mark overall task complete
- `update_step_progress(inc_completed=0, inc_total=0) -> None` — increment step counters
- `set_step_progress(completed, total) -> None` — set step counters
- `set_step_completed() -> None` — mark step complete
- `is_complete() -> bool` — check if fully done

Progress updates trigger a callback to refresh the GUI.

### `TaskContext`

The primary interface for task code.
Passed as the first argument to every async task function.

**Properties:**

- `status: TaskStatus` — access to progress/cancellation state

**Methods:**

- `async request_decision(title, expected_decisions, message) -> Decision` — pause and ask the user
- `async subtask(coro: Coroutine[Any, Any, R]) -> asyncio.Task[R]` — spawn a concurrent task

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
        with open(file_path) as f:
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

Use subtasks to allow work to proceed concurrently; this enables other subtasks to complete while one waits for user feedback:

```python
async def process_many(ctx: TaskContext, items: list[str]) -> None:
    tasks = []
    for item in items:
        t = await ctx.subtask(process_one(ctx, item))
        tasks.append(t)
    
    # Wait for all to complete
    await asyncio.gather(*tasks)
```

**Why this matters:** If `process_one()` calls `ctx.request_decision()` for item A, the GUI thread blocks to show the dialog. Meanwhile, subtasks for items B and C continue running in the worker thread, so you get progress without waiting for the user to respond.

Without subtasks, sequential processing would stall the entire operation whenever user feedback is needed.

**Notes:**
- Each subtask receives the same `TaskContext` and updates shared progress
- See `filemanager/tasks.py:_copy_dir_recursive()` (line 98) and `copy_files()` (line 120) for production examples of this pattern

#### Handle Cancellation

```python
async def long_operation(ctx: TaskContext) -> None:
    for i in range(1000):
        # Raises TaskCancelled if user hit cancel
        ctx.status.check_cancelled()
        
        await do_work()
```

### Real-World Example

The `filemanager/tasks.py` module contains production tasks for copying, moving, and erasing files.
See `copy_files()` and `erase_files()` for full examples with:

- Multi-level progress tracking
- "Apply to all" decision caching
- Recursive directory handling
- Cross-device operations
- Error handling

## Reference

- **Framework:** `nova_navigator/task.py`
- **Task implementations:** `nova_navigator/filemanager/tasks.py`
- **Decision enum:** `nova_navigator/decision.py`
- **Architecture guide:** See `AGENTS.md` for the broader threading model and async architecture

