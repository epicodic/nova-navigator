# Handoff: Reliable Terminal Input — status, failures, and proposal

Audience: a fresh agent ("Claude Fable") asked for an alternative idea.
Author: previous session (GitHub Copilot / Claude).
Date: 2026-09-01.

This document is self-contained. Read it top to bottom. All code paths are
relative to the `focus` git worktree unless stated otherwise.

---

## 0. Environment / repo state (read this first)

- Worktree: `~/workspace/epicodic/nova-navigator/focus`
- Branch: `feature/improve-terminal-experience`, committed at `2ba1635` ("sorting of columns").
- `git diff main...HEAD` is **empty** — the committed code on this branch has no
  terminal changes beyond `main`. **The committed state is the "old approach".**
- ALL of the feature work under test is **uncommitted** (working tree), ~2,474
  insertions / 816 deletions across 14 files. The big ones:

  | file | Δ |
  |------|---|
  | `src/nova_navigator/terminal/terminal.py` | +708 |
  | `src/nova_navigator/terminal/vfs_shell/virtual_pty_backend.py` | +199 |
  | `src/nova_navigator/terminal/shell_driver.py` | +123 |
  | `src/nova_navigator/terminal/pty_backend.py` | +27 |
  | `src/nova_navigator/nova_navigator.py` | +23 |
  | (+ ~1,400 lines of test churn) | |

- Because everything is uncommitted, `git stash` fully and reversibly restores the
  old, working terminal. Nothing is lost by stashing.
- The app launches the terminal as `Terminal("/usr/bin/zsh", ...)` in
  `src/nova_navigator/nova_navigator.py` (~line 290), i.e. it runs the user's full
  oh-my-zsh config, not `zsh -f`.
- Commands: `uv run pytest tests/terminal`, `uv run ruff check <file>`,
  `uv run ty check <file>`. Ubuntu 24.04, Python 3.12, uv-managed venv.

---

## 1. The problem we are trying to solve

When a **pane** (file browser) has focus and the user presses **Enter**:
- if the embedded shell's line buffer is **non-empty**, execute that shell command;
- if the buffer is **empty**, delegate Enter to the pane (open dir / default action).

Additionally, pane-driven directory changes must be mirrored into the terminal
(`cd`) **without** showing the `cd` or its output, and **without** destroying any
partially-typed command in the shell.

Design spec: `docs/agents/specs/2026-08-18-reliable-terminal-input-design.md`
Plan: `../test/docs/agents/plans/2026-08-18-reliable-terminal-input.md`

---

## 2. Requirements (from the design spec)

- Determine whether the actual shell editor buffer is empty when Enter is pressed.
- Detect text inserted by typing, bracketed paste, history, completion, or plugins.
- Support **local zsh and Bash**.
- Support **remote zsh and Bash over SSH**.
- Support the **virtual shell** through its directly accessible line editor.
- Preserve the complete buffer and cursor across pane-triggered directory changes.
- Hide internal directory-change commands and their echo.
- Keep one terminal per underlying system/filesystem connection.
- Serialize programmatic directory changes within each terminal.
- Prevent delayed terminal events from updating the wrong pane.
- **Degrade conservatively when shell integration is unavailable or replaced.**

The spec explicitly warns (this turns out to be the crux):

> "Several oh-my-zsh plugins do directly redefine `zle-line-init`. Therefore,
> correctness must not depend on `zle-line-init` or another shared special ZLE
> hook remaining installed."

---

## 3. The OLD approach (committed `HEAD`) — and why it worked

The spec **rejected** the old approach as "unreliable" because rendered cursor
position is not equal to shell buffer state. But in practice the old approach
worked across zsh, Bash, SSH, and 1-line vs multiline mode.

Empty-prompt detection (`git show HEAD:src/nova_navigator/terminal/terminal.py`,
`has_input()` ~lines 366–388):

```python
def has_input(self) -> bool:
    # Primary: pyte cursor vs snapshotted prompt-end cursor.
    if self._prompt_ready_received:
        if self._screen.cursor.y != self._prompt_cursor_y:
            return self._screen.cursor.y > self._prompt_cursor_y
        return self._screen.cursor.x > self._prompt_cursor_x
    # Fallback: any key forwarded since the last precmd (works for any shell).
    return self._keys_forwarded_since_precmd
```

- `_handle_prompt_ready()` (~520) snapshots the prompt-end cursor
  (`_prompt_cursor_x/y`) each time OSC 133;B fires.
- No round-trip to the shell. No shell-side widgets. No latency sensitivity.
- Fallback (`_keys_forwarded_since_precmd`) covers shells with no prompt-ready.

Navigation with input preservation (`request_cd`, ~389):
- `_draining = True`, then Ctrl+U (kill line) before the `cd`, Ctrl+Y (yank) after
  the next precmd, all under draining so the echo is hidden.
- In-flight changes counted by `_nav_pending`.

**Key point:** the old empty-prompt mechanism is local, synchronous, and
shell-agnostic. That is exactly why it survived SSH/Bash/1-line mode.

---

## 4. What the FOCUS worktree tried (the rewrite under test)

The rewrite replaces the cursor heuristic with an **authoritative shell-editor
probe** over a private OSC 777 protocol.

- New protocol model: `src/nova_navigator/terminal/shell_editor_protocol.py`
  (`EditorOperation` = ready/probe/stash/restore, `EditorResponse`, nonce).
- Shell drivers inject integration code after startup:
  `src/nova_navigator/terminal/shell_driver.py`
  - zsh: ZLE widgets reading `$BUFFER`/`$CURSOR`, bound to private keys
    `^[[99~` (probe), `^[[98~` (stash), `^[[97~` (restore) in emacs/viins/vicmd.
  - Bash: `bind -x` callbacks reading `$READLINE_LINE`/`$READLINE_POINT`.
  - Emits `OSC 777;nn;<nonce>;ready;...` when installation completes.
- `src/nova_navigator/terminal/pty_backend.py` parses OSC 777 → `editor_response`
  messages (also OSC 7 → pre_cmd, OSC 133;B → prompt_ready).
- `src/nova_navigator/terminal/terminal.py` owns:
  - `submit_enter()` (~434): async. If `not _at_prompt or not _editor_available`
    → send Enter (execute). Else send a **PROBE** and await the reply; if
    `buffer_length == 0` → return False (delegate); else execute.
  - `_request_editor()` (~878) with `_EDITOR_RESPONSE_TIMEOUT = 1.0` (line 70).
  - `_maybe_schedule_startup_probe()` (~852) / `_run_startup_probe()` (~867):
    `_editor_available` becomes True only after (a) `ready` received AND
    (b) `first_prompt_ready` received AND (c) a successful empty probe.
  - A navigation **coordinator** (`_enqueue_navigation_request` ~641, plus
    `_nav_waiting_for_cwd`, `_nav_capture_prompt_output`, futures) that serializes
    programmatic `cd`s and captures/reorders prompt output — replacing the old
    kill/yank + counter scheme.
- Virtual shell parity: `vfs_shell/line_editor.py`, `vfs_shell/virtual_pty_backend.py`.

Plan Task 7 (verify under real oh-my-zsh) was never completed. Unit/integration
tests run `zsh -f` (no framework) and do not assert `prompt_ready`.

---

## 5. What we tried to fix in THIS session

Diagnostics used a standalone spike (still present):
`tools/spike_terminal_startup.py` — drives a real `LocalPtyBackend` + `ZshDriver`,
writes the real init+editor code, pumps the recv queue for N seconds, and (as
enhanced) simulates the draining decision to print what the user would see.
Run: `uv run python tools/spike_terminal_startup.py 4 /usr/bin/zsh`.

Findings and fixes attempted:

1. **Root cause A — `prompt_ready` never fires under oh-my-zsh.**
   The zsh init used `add-zle-hook-widget ... zle-line-init` but never
   `autoload -Uz add-zle-hook-widget` → `command not found` → no OSC 133;B →
   `_editor_available` stayed False forever → `submit_enter` always executed.
   - **Fix applied** in `shell_driver.py` (ZshDriver.init_code, ~line 143):
     added `autoload -Uz add-zle-hook-widget`. Spike confirms `prompt_ready`
     now fires 2–3× within 3.5 s. This is the only fix I consider sound.

2. **Root cause B — startup "text storm".**
   ~1.6 KB of editor code is written at t=0. Under a heavy interactive config it
   lands in the ZLE typeahead buffer as a **bracketed paste** and is echoed and
   syntax-highlighted char-by-char. Spike numbers: startup code 1,831 B →
   ~5,805 B of drained output (≈3× amplification from per-keystroke full-line
   redraws).
   - I reworked the draining lifecycle in `terminal.py`
     (`_end_startup_draining`, `_force_end_startup_draining` watchdog
     `_STARTUP_DRAIN_TIMEOUT`, ending draining on `prompt_ready`). Lint + ty
     clean. **But this does not actually hide the storm — see §6.**

---

## 6. What failed in the end

### 6a. The storm fix is structurally impossible with "drop" draining
Real oh-my-zsh timeline (from the enhanced spike):

| time | event |
|------|-------|
| 801 ms | `prompt_ready` #1 (first `zle-line-init`) |
| 801–1092 ms | **the storm** (editor code pasted + highlighted char-by-char) |
| 1092 ms | editor `READY` response |
| 1122 ms | `prompt_ready` #2 — the clean powerline prompt renders here |

- The storm arrives **after** `prompt_ready` #1, so ending draining there still
  shows it.
- The current draining **drops** stdout (recv loop: `if self._draining: ... else
  _feed_stdout`). The clean prompt renders **inside** the drained window, so any
  drop-based scheme that hides the storm **also loses the prompt** → blank
  terminal until a keypress. `\r\x1b[K` cannot regenerate a dropped prompt.
- Correct-but-unbuilt option: during startup, still feed pyte but **suppress the
  refresh**, then refresh once at the settled prompt. Or don't paste at all.

### 6b. User testing of the current (rewrite + my fixes) state — four regressions
Reported by the user running the real app:

1. Sometimes works now (no longer fully broken).
2. **1-line terminal mode: the command is NEVER executed.** Works only in the
   larger multiline mode.
3. **cwd race is back:** switching pane focus rapidly back and forth eventually
   leaves the terminal stuck in the *other* (unfocused) pane's directory.
4. **SSH → Bash: Enter always executes, even on an empty prompt.**

### 6c. Root cause of the regressions
All four trace to replacing the local cursor heuristic with a synchronous,
shell-installed probe:

| symptom | cause |
|---|---|
| #2 1-line mode never executes | probe/`_editor_available` gating misbehaves under the small-mode render/rendering path |
| #4 SSH Bash always executes | Bash `bind -x` integration not active / `READY` + probe reply not delivered within `_EDITOR_RESPONSE_TIMEOUT`=1 s over SSH → `_editor_available` False → `submit_enter` executes |
| startup storm | injecting ~1.6 KB of widgets into the live line editor |
| #3 cwd race | the new navigation coordinator replaced the old kill/yank + counter scheme |

Note the spec tension: the rewrite's SSH-Bash behavior is arguably "conservative
degradation" (when integration is unavailable, execute). But the **old** cursor
heuristic delivered correct empty-prompt delegation in exactly those environments
without any integration. So the "unreliable" heuristic outperformed the
"authoritative" probe in practice.

---

## 6d. Why OSC 133;B via `zle-line-init`, and the real mistake

The `prompt_ready` (OSC 133;B) mechanism is **not** from this plan. It was
introduced 2026-05-22: `docs/agents/plans/2026-05-22-prompt-ready-osc133b.md`
(spec: `docs/agents/specs/2026-05-22-prompt-ready-osc133b-design.md`). Its stated
goal:

> "Replace the timer-based cursor snapshot with a deterministic OSC 133;B
> 'prompt-ready' marker … Each shell emits `\033]133;B\007` **when readline is
> ready for input (after the prompt is fully drawn)**."

So the marker exists to capture the exact instant the line editor becomes ready,
so the **cursor snapshot for `has_input()`** is accurate. Current zsh emission
(`shell_driver.py`, ZshDriver.init_code):

```
_nn_zle_init() { printf '\033]133;B\007' >/dev/tty };
add-zle-hook-widget -Uz zle-line-init _nn_zle_init
```

Why `zle-line-init` rather than embedding the marker in `$PROMPT`:
1. **Themes own `$PROMPT`.** oh-my-zsh/powerlevel rebuild `$PROMPT`/`$PS1` every
   `precmd`; an appended marker would be wiped unless re-injected from `precmd`
   each cycle, with ordering/transient-prompt caveats. The ZLE hook avoids
   touching the theme-owned prompt.
2. **Timing.** `zle-line-init` fires once at edit-start; a prompt-expansion marker
   can fire at odd times or repeatedly under instant/transient prompts (p10k).
3. It looked robust enough (`add-zle-hook-widget` is the blessed helper), and the
   fragility was never hit because tests run `zsh -f` (no plugins).

**The real mistake is not the emission site — it is how this rewrite USES the
signal.** In the 2026-05-22 design, `prompt_ready` was a **soft timing hint** that
fed the cursor heuristic, and `has_input()` still had a keystroke fallback if it
never fired — so a stolen `zle-line-init` degraded gracefully. This rewrite
**promoted the soft hint into a hard correctness gate**: `_editor_available`
becomes true only after `prompt_ready` fires, and if it never fires, Enter always
executes. That is what turned a tolerable fragility into a fatal one — and it is
exactly what this plan's own spec warned against ("correctness must not depend on
`zle-line-init`").

Implication for the fix: the prompt-embedded emission is a viable option (emit via
`precmd` appending to `$PROMPT`, guarding transient/instant prompts), but the
deeper correction is to **demote `prompt_ready` back to a hint** — drive the
prompt lifecycle from `precmd` (an append-safe array hook), decide emptiness from
the key-bound probe and/or the cursor heuristic, and fall back to the heuristic
(never to "always execute") when the signal is absent. Note the probe/stash/
restore widgets ride on private key bindings (`^[[99~` etc.), not on
`zle-line-init`, so authoritative inspection itself does not need the hook.

---

## 7. My proposal

The old committed design already satisfies most requirements robustly. The
authoritative probe is fragile precisely where deployment is hardest (SSH, Bash,
constrained render modes), which is the opposite of what you want.

Recommended:

1. `git stash` the uncommitted rewrite to restore the working baseline and
   confirm #2/#3/#4 disappear. Fully reversible.
2. Treat the **cursor/prompt-end heuristic (`has_input()`) as the primary,
   shell-agnostic mechanism** — never regress below it.
3. If (and only if) an authoritative buffer read is desired for a specific edge
   case (e.g. "typed then deleted everything" in the fallback path, or wide-char
   geometry ambiguity), add the OSC 777 **probe as an optional augmentation**
   used **only where it is proven available and fast** (local zsh with confirmed
   `ready` + successful empty probe). If the probe is unavailable or times out,
   **fall back to the cursor heuristic — not to "always execute".** That single
   change to `submit_enter`'s fallback would fix #4 by itself.
4. Keep the old `request_cd` kill/yank + draining for navigation, or fix the new
   coordinator's ordering race (#3) — but do not ship the coordinator as-is.
5. For the storm (only relevant if the probe path is kept): switch startup
   draining from "drop stdout" to "feed pyte, suppress refresh until settled",
   or install the integration by **sourcing a temp rcfile / ZDOTDIR** instead of
   pasting into the interactive line editor (removes the amplification at the
   source and aligns with the spec's "don't depend on `zle-line-init`").

The essential design question for the next agent:

> Is authoritative buffer inspection worth its fragility, given a local heuristic
> already works across all target environments? If yes, it must be **additive and
> optional over** the heuristic, never a replacement whose failure mode is
> "execute anyway".

---

## 8. Concrete references

Rewrite (`focus`, working tree), `src/nova_navigator/terminal/terminal.py`:
- `submit_enter` L434 · `_handle_pre_cmd` L522 · `_end_startup_draining` L575 ·
  `_handle_prompt_ready` L596 · `_enqueue_navigation_request` L641 ·
  `_maybe_schedule_startup_probe` L852 · `_run_startup_probe` L867 ·
  `_request_editor` L878 · `_EDITOR_RESPONSE_TIMEOUT` L70.
- `src/nova_navigator/terminal/shell_driver.py`: ZshDriver.init_code ~L143
  (autoload fix), `editor_integration_code`, `supports_prompt_ready` L86.
- `src/nova_navigator/terminal/pty_backend.py`: OSC 7 / 133 / 777 parsing.

Old approach (committed), `git show HEAD:src/nova_navigator/terminal/terminal.py`:
- `on_key` ~L350 · `has_input` ~L366 · `_handle_prompt_ready` (snapshot) ~L520 ·
  `request_cd` (kill/yank + draining) ~L389.

Tooling:
- `tools/spike_terminal_startup.py` — real-PTY startup diagnostic.

Known pre-existing failing test (fails with or without my edits):
`tests/integration/test_terminal_panel.py::test_pane_enter_not_consumed_by_terminal_navigates_panel`.

---

## 9. Specs worth reading (in this order)

Primary — read these first:
- `docs/agents/specs/2026-08-18-reliable-terminal-input-design.md` — the design
  the rewrite implements: problem statement, full requirements, the OSC 777
  protocol, and the *verified shell capabilities* section (zsh `BUFFER`/`CURSOR`,
  Bash `READLINE_LINE`/`READLINE_POINT`, and the `zle-line-init` warning).
- `docs/agents/specs/2026-08-31-terminal-editor-control-proposal.md` — a companion
  **alternative-structure proposal** (same problem, different module boundaries:
  editor capability as an explicit object, a dedicated navigation coordinator,
  `PtyBackend` kept byte/transport-only). Most relevant if you intend to redesign
  rather than patch.
- `docs/agents/specs/2026-05-22-prompt-ready-osc133b-design.md` — origin and
  rationale of the `OSC 133;B` prompt-ready marker and the cursor snapshot it was
  meant to feed (see §6d).

Supporting context:
- `docs/terminal.md` — current terminal sub-package architecture guide.
- `docs/agents/specs/2026-05-01-terminal-shell-driver-design.md` — `ShellDriver`
  abstraction (zsh/Bash/fallback), init code, quoting, precmd hooks.
- `docs/agents/specs/2026-07-31-vfs-shell-emulator-design.md` — the virtual shell
  and its line editor, which must mirror the same protocol.
- `docs/agents/specs/2026-04-27-silent-terminal-send-design.md` — silent send /
  draining semantics (directly relevant to hiding `cd` and the startup storm).
- `docs/agents/specs/2026-05-21-ssh-dir-sync-design.md` and
  `docs/agents/specs/2026-05-21-ssh-pty-backend-design.md` — SSH directory sync
  and PTY backend (latency context for regression #4).
- `docs/agents/specs/2026-05-21-terminal-pool-design.md` — the "one terminal per
  connection" requirement.

Plans (task-by-task, checkbox-tracked):
- `docs/agents/plans/2026-08-18-reliable-terminal-input.md` — the plan under
  implementation (note Task 7, real oh-my-zsh verification, was never done).
- `docs/agents/plans/2026-05-22-prompt-ready-osc133b.md` — origin plan for
  prompt-ready.
