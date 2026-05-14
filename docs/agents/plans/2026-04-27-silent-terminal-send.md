# Silent Terminal Send Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `send_silent()` method to `Terminal` that writes data to the PTY and suppresses its echo from the display by draining PTY output until the next shell prompt, then resetting the pyte screen.

**Architecture:** A `_draining` boolean flag is added to `Terminal`. While draining, pyte processes incoming PTY output but `_schedule_rebuild()` is skipped. When the existing `pre_cmd` pipe fires (shell prompt reappeared), the pyte screen is reset, `_draining` is cleared, and the display rebuilds cleanly. `send_silent()` is the public API that sets `_draining` and enqueues the data as normal stdin. `_spawn_pty` uses `send_silent` for the init code.

**Tech Stack:** Python 3.12, pytest

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

---

## Files

- Modify: `src/nova_navigator/widgets/terminal.py`
  - Add `_draining: bool` instance variable (init to `False`)
  - Add `send_silent(data: str) -> None` public method
  - Update `recv` loop `pre_cmd` branch: reset screen + clear `_draining`
  - Update `recv` loop: skip `_schedule_rebuild()` while `_draining`
  - Update `_spawn_pty` to call `send_silent` instead of direct enqueue
- Modify: `tests/widgets/test_terminal.py`
  - Add tests for `send_silent` behaviour

---

### Task 1: Add `_draining` flag, `send_silent`, update `recv` and `_spawn_pty`

**Files:**
- Modify: `src/nova_navigator/widgets/terminal.py`

- [ ] **Step 1: Add `_draining = False` to `__init__`**

  Locate the block that sets `self._started = False` in `__init__` (around line 293 in the current file) and add `_draining` alongside the other boolean flags:

  ```python
  self._started = False
  self._draining = False
  ```

- [ ] **Step 2: Add `send_silent` method after `send`**

  Locate `async def send(self, data: str) -> None:` and add immediately after it:

  ```python
  async def send_silent(self, data: str) -> None:
      """Send *data* to the shell and suppress its echo from the display.

      Sets the draining flag so the recv loop skips display rebuilds until
      the shell's precmd hook fires, at which point the screen is reset.
      """
      if not self._started:
          return
      assert self.send_queue is not None
      self._draining = True
      self.send_queue.put_nowait(["stdin", data])
  ```

- [ ] **Step 3: Update the `recv` loop**

  Locate the `recv` method. Make two changes:

  **3a.** In the `pre_cmd` branch, reset the screen and clear `_draining` before posting the message:

  Current:
  ```python
  elif cmd == "pre_cmd":
      cwd = PurePath(str(message[1]).strip())
      self.post_message(Terminal.PreCmd(self, cwd))
  ```
  Replace with:
  ```python
  elif cmd == "pre_cmd":
      if self._draining:
          self._screen.reset()
          self._draining = False
      cwd = PurePath(str(message[1]).strip())
      self.post_message(Terminal.PreCmd(self, cwd))
  ```

  **3b.** In the block after the drain loop, skip `_schedule_rebuild()` while draining:

  Current:
  ```python
  if stdout_fed:
      self._schedule_rebuild()
  ```
  Replace with:
  ```python
  if stdout_fed and not self._draining:
      self._schedule_rebuild()
  ```

- [ ] **Step 4: Update `_spawn_pty` to use `send_silent`**

  Locate `_spawn_pty`. Change:
  ```python
  self.send_queue.put_nowait(["stdin", shell_init_code(self.fd_pre_cmd_child)])
  ```
  To:
  ```python
  # send_silent cannot be awaited here (sync context); enqueue directly and set flag
  self._draining = True
  self.send_queue.put_nowait(["stdin", shell_init_code(self.fd_pre_cmd_child)])
  ```

  > Note: `_spawn_pty` is a sync method called from `start()` and `respawn()`. We cannot `await send_silent()` here, so we replicate its two lines inline. This is intentional — `send_silent()` is the public async API; `_spawn_pty` is the internal bootstrapping path.

- [ ] **Step 5: Run QA**

  ```
  uv run qa
  ```
  Expected: zero failures.

- [ ] **Step 6: Coding-guideline follow-up checklist**
  - [ ] Conventions file read: `docs/coding_conventions.md`
  - [ ] All new methods have full type annotations
  - [ ] `_draining` uses `_` prefix (private)
  - [ ] `send_silent` is `snake_case`
  - [ ] No `Optional[X]` used
  - [ ] `uv run qa` passes

---

### Task 2: Write tests for `send_silent`

**Files:**
- Modify: `tests/widgets/test_terminal.py`
- Test: `tests/widgets/test_terminal.py`

- [ ] **Step 1: Understand the existing test helpers**

  Read `tests/widgets/test_terminal.py` focusing on `_start_recv_only`, `_stop_recv_only`, and the `TerminalTestApp` fixture.
  The `_start_recv_only` helper bypasses the real PTY and injects fake `recv_queue` messages directly.
  For `send_silent` we need a real PTY (zsh must fire `precmd`), so use the full `Terminal` with `keep_alive=False`.

- [ ] **Step 2: Write test — `send_silent` is callable**

  ```python
  def test_send_silent_is_callable() -> None:
      terminal = Terminal("/usr/bin/zsh", id="t_silent_callable", keep_alive=False)
      assert callable(terminal.send_silent)
  ```

- [ ] **Step 3: Write test — `send_silent` does not appear on screen**

  ```python
  @pytest.mark.asyncio
  async def test_send_silent_does_not_appear_on_screen() -> None:
      """Data sent via send_silent must not be rendered on the terminal display."""
      terminal = Terminal("/usr/bin/zsh", id="t_silent_screen", keep_alive=False)
      terminal.start()
      app = TerminalTestApp(terminal)
      async with app.run_test(size=Size(80, 24)) as pilot:
          await pilot.pause(delay=1.0)  # let zsh start and fire precmd (clears _draining)
          await terminal.send_silent("# SILENT_MARKER_XYZ\n")
          await pilot.pause(delay=1.0)  # wait for precmd to fire and reset screen
          display_text = "".join(line.plain for line in terminal._display.lines)
          assert "SILENT_MARKER_XYZ" not in display_text
      terminal.stop()
  ```

- [ ] **Step 4: Write test — normal `send` still works after `send_silent`**

  ```python
  @pytest.mark.asyncio
  async def test_send_after_send_silent_appears_on_screen() -> None:
      """Normal send() must still render output after send_silent completes."""
      terminal = Terminal("/usr/bin/zsh", id="t_after_silent", keep_alive=False)
      terminal.start()
      app = TerminalTestApp(terminal)
      async with app.run_test(size=Size(80, 24)) as pilot:
          await pilot.pause(delay=1.0)  # let zsh start
          await terminal.send_silent("# warmup\n")
          await pilot.pause(delay=1.0)  # wait for precmd reset
          await terminal.send("echo VISIBLE_MARKER_XYZ\n")
          await pilot.pause(delay=0.5)
          display_text = "".join(line.plain for line in terminal._display.lines)
          assert "VISIBLE_MARKER_XYZ" in display_text
      terminal.stop()
  ```

- [ ] **Step 5: Run the new tests**

  ```
  uv run pytest tests/widgets/test_terminal.py::test_send_silent_is_callable tests/widgets/test_terminal.py::test_send_silent_does_not_appear_on_screen tests/widgets/test_terminal.py::test_send_after_send_silent_appears_on_screen -v
  ```
  Expected: all PASS.

- [ ] **Step 6: Run all terminal tests**

  ```
  uv run pytest tests/widgets/test_terminal.py -v
  ```
  Expected: all PASS.

- [ ] **Step 7: Run full QA**

  ```
  uv run qa
  ```
  Expected: zero failures.

- [ ] **Step 8: Coding-guideline follow-up checklist**
  - [ ] Conventions file read: `docs/coding_conventions.md`
  - [ ] Test functions fully type-annotated (`-> None`)
  - [ ] Test names are descriptive `snake_case`
  - [ ] `uv run qa` passes
