# Asyncio Migration Proof of Concept - Results

**Date:** April 7, 2026  
**Status:** ✅ Successful - All tests passing

## Summary

We successfully created a proof-of-concept demonstrating that the current generator-based task system can be replaced with native Python asyncio coroutines. The key to the solution is passing a **TaskContext** object as the first parameter to all tasks.

## Architecture Changes

### Current (Generator-based)
```python
Task = Generator[DecisionRequest | Task, Decision, None]

def copy_file(status: TaskStatus, src: VPath, dst: VPath, options: FileCopyOptions) -> Task:
    decision = yield DecisionRequest("Overwrite", [...], "...")
    if decision.is_negative:
        return
    # ... copy logic
```

### Proposed (Asyncio-based)
```python
async def copy_file(ctx: TaskContext, src: VPath, dst: VPath, options: FileCopyOptions) -> None:
    decision = await ctx.request_decision("Overwrite", [...], "...")
    if decision.is_negative:
        return
    # ... copy logic
```

## TaskContext API

```python
@dataclass
class TaskContext:
    """Context passed to all async tasks."""
    
    async def request_decision(
        self, 
        title: str, 
        expected_decisions: list[Decision], 
        message: str
    ) -> Decision:
        """Request a decision from the user via the GUI."""
        ...
    
    async def spawn(self, coro: Awaitable[None]) -> None:
        """Spawn a subtask that runs concurrently."""
        ...
    
    @property
    def status(self) -> TaskStatus:
        """Access to progress tracking and cancellation."""
        ...
```

## Key Behaviors Preserved

### 1. Sequential Launch with Non-blocking Suspension ✅

**Generator version:**
```python
def copy_files(...) -> Task:
    for src_file in files:
        yield copy_file(src_file, dst)  # Launches copy_file
        # If copy_file needs user decision → it suspends, loop continues
        # Next file starts copying while first waits for decision
```

**Asyncio version:**
```python
async def copy_files(ctx: TaskContext, ...) -> None:
    spawned_tasks = []
    for src_file in files:
        task = asyncio.create_task(copy_file(ctx, src_file, dst))
        spawned_tasks.append(task)
        await asyncio.sleep(0)  # Yield control so task can start
        # Loop continues, next file starts copying
    
    await asyncio.gather(*spawned_tasks)  # Wait for all to complete
```

### 2. Decision Caching (ALL/NONE) ✅

The scheduler automatically caches `Decision.ALL` and `Decision.NONE` responses and applies them to subsequent identical requests without prompting the user again.

### 3. Depth-First Execution Order ✅

When a task spawns subtasks, they run immediately (with `sleep(0)` yielding control), preserving the sequential launch order.

## Test Results

All 4 POC tests passing:

1. ✅ `test_generator_subtask_blocking_allows_parent_to_continue` - Current implementation baseline
2. ✅ `test_asyncio_subtask_blocking_allows_parent_to_continue` - Asyncio version matches behavior
3. ✅ `test_decision_caching_with_all` - Decision caching works correctly  
4. ✅ `test_execution_order_comparison` - Both implementations produce identical execution order

## Implementation Benefits

| Aspect | Generator-based | Asyncio-based | Improvement |
|--------|----------------|---------------|-------------|
| **Lines of code** | ~280 in TaskScheduler | ~60 in AsyncTaskScheduler | **78% reduction** |
| **Complexity** | Manual `.send()` gymnastics | Native `async`/`await` | **Much simpler** |
| **Threading** | Worker threads + event loop | Single event loop per job | **Same (no change needed)** |
| **Context propagation** | Via generator state | Via TaskContext parameter | **Explicit & clear** |
| **Debugging** | Complex generator stack traces | Standard async stack traces | **Better** |
| **Type safety** | Moderate | Excellent | **Better** |
| **Future extensibility** | Limited | Can add more context methods | **Better** |

## Technical Details

### Context Propagation Solution

Initially, we tried using `contextvars.ContextVar` to implicitly pass the decision requester to tasks, but discovered that context variables don't automatically propagate to subtasks created with `asyncio.create_task()` or `TaskGroup.create_task()`.

**Solution:** Pass `TaskContext` explicitly as the first parameter to all tasks. This:
- Makes the API explicit and clear
- Avoids context propagation issues
- Provides a natural place for future extensions (e.g., logging, metrics)
- Matches common patterns in async frameworks (like FastAPI's `Request` object)

### Blocking I/O Handling

Since each TaskScheduler runs in its own worker thread with a dedicated event loop:
- **Blocking filesystem I/O is acceptable** - won't starve the GUI
- No need to rewrite filesystem layer to be async immediately
- Can incrementally add async I/O later for performance if needed

### Worker Thread Bridge

Decision requests from worker loops are bridged to the main GUI loop:

```python
async def requester(title, expected, msg):
    # Worker loop creates future
    future: asyncio.Future[Decision] = asyncio.Future()
    
    # Call GUI callback (which runs in main loop)
    await self._gui_request_callback(request, future)
    
    # Wait for result
    decision = await future  # GUI sets this from main loop
    return decision
```

## Next Steps

### Phase 1: Core Migration
1. Create `src/nova_navigator/task_async.py` with:
   - `TaskContext` dataclass
   - `AsyncTaskScheduler` class
2. Update `TaskStatus` to use `asyncio.Event` instead of `threading.Event`
3. Create async versions of file operations in `filemanager/tasks_async.py`

### Phase 2: Integration
1. Update `Job` class to work with both generator and async tasks (feature flag)
2. Add integration tests
3. Update UI bindings

### Phase 3: Cutover
1. Switch default to async implementation
2. Run in production for stability period
3. Remove generator-based code
4. Update documentation

## Recommendation

**Proceed with full implementation.** The POC demonstrates that:
- ✅ Asyncio can fully replicate current behavior
- ✅ Code complexity is significantly reduced
- ✅ No need for complex ContextVar workarounds
- ✅ Clean, explicit API with TaskContext
- ✅ Foundation for future improvements (cancellation, timeout, etc.)

The migration is **low-risk** and delivers **significant maintainability improvements**.
