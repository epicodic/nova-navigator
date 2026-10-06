# AGENTS.md — Nova Navigator

Instructions for agentic workers (OpenAI Codex, Claude Code, GitHub Copilot agents, etc.).
This file is also served as `CLAUDE.md` via symlink for Claude Code.

---

## Shared Rules

**Read [`.agents/AGENTS.md`](.agents/AGENTS.md) before anything else.**
It holds the rules shared by all projects: approval rules, reading and writing documentation, `uv` usage, Serena, communication style, private development files (`.devkit/`), and the skills.
It is private tooling that is linked in locally; if it is missing, ignore this section.

@.agents/AGENTS.md

---

## Project Overview

Nova Navigator is a modern TUI file manager (like Midnight Commander) built with Python and the [Textual](https://github.com/Textualize/textual) framework.
It supports a virtual file system (VFS) abstraction for local, SSH, and archive filesystems.

---

## Platform

| Item | Value |
|------|-------|
| OS | Ubuntu 24.04 (only — macOS and Windows are not supported) |
| Python version | 3.12 |

---

## Setup

No setup step is required before running commands.

---

## Commands

### Run the app

```sh
uv run nn                 # run the app via the installed script entry point
```

### Tests

```sh
uv run pytest                                  # run all tests
uv run pytest tests/test_archives.py           # run a specific test file
uv run pytest tests/test_archives.py::test_foo # run a specific test
```

### Quality Assurance (lint, type check, tests)

```sh
uv run qa                 # run all QA checks (lint, type check, tests)
uv run qa --fix           # run all QA checks and apply auto-fixes where possible
```

Individual tools:

```sh
uv run ruff check .       # lint
uv run ruff format .      # format
uv run ty check .         # type check
```

Always run `uv run qa` after changes and confirm zero failures before claiming work is complete.

---

## Read Documentation First

Key documents in `docs/` — read every one whose topic overlaps with your task:

| File | Topic |
|------|-------|
| `docs/coding_conventions.md` | Naming, style, and code patterns for this project |
| `docs/editor.md` | Text editor package (`nova_editor`) — architecture, layering, vendoring policy, and update instructions; also read before touching `NovaNavigator.open_editor` |
| `docs/dialog.md` | Dialog base class — read before creating new dialogs |
| `docs/scheduler.md` | Async task scheduler framework — read before touching `scheduler/` or long-running operations |
| `docs/directory_browser.md` | Directory browser widget design — read before touching `widgets/directory_browser.py` |
| `docs/terminal.md` | Terminal sub-package architecture — read before touching `terminal/` |
| `docs/remote-uri-scheme.md` | `remote://` URI scheme design — read before touching `vfs/` or remote connection handling |
| `docs/keymap.md` | Keymap system architecture — read before touching `nova_widgets/keymap/`, `nova_navigator/keymap/`, or key-handling code |
| `docs/user_menu.md` | User menu (F2) config and behaviour — read before touching `usermenu/` |
| `docs/commands.md` | Command execution service — read before touching `commands/` or `Filesystem.exec_command` |
| `docs/local_copies.md` | Local copies of non-local files — read before touching `vfs/local_copy.py`, `vfs/change_detector.py`, `local_copies/`, or archive writes |

---

## Architecture

The codebase lives under `src/` and contains three packages:

- **`nova_navigator/`** — the main application
- **`nova_editor/`** — the text editor: widget, lazy document, `EditorScreen` and the standalone `nova_edit` app (see `docs/editor.md`)
- **`nova_widgets/`** — reusable Textual widgets (menu bar, icon rendering), developed alongside the app

### Key layers

**Entry point:** `nova_navigator/main.py` — defines `NovaNavigator(App)` and `MainScreen(Screen)`. `MainScreen` composes two `DirectoryBrowser` panels side-by-side with a terminal emulator and footer. Keyboard bindings are defined there.

**VFS:** `nova_navigator/vfs/` — the virtual filesystem abstraction:
- `filesystem.py` — `Filesystem` ABC; subclasses implement `cwd()`, `root()`, `home()`, `iterdir()`, `stat()`, `parent()`, `read()`, `write()`, `remove()`, `rmdir()`
- `vpath.py` — `VPath`: a path + filesystem reference pair; stat is lazily cached; supports `/` operator for path joining
- `types.py` — `Stat` dataclass (size, modified, is_hidden, is_directory, is_executable, is_symlink, is_broken_symlink)
- `parse_uri.py` — URI parsing supporting nested schemes (e.g. `ssh://host/archive.tar.gz/tar://file.txt`)
- `filesystems/local.py` — `LocalFilesystem` singleton
- `filesystems/ssh.py` — `SSHFilesystem` via paramiko
- `filesystems/azure.py` — Azure blob stub (incomplete)

**Task system:** `nova_navigator/scheduler/` — async scheduler for long-running operations with user response support. See `docs/scheduler.md` for the complete scheduler framework guide.
- `AsyncTaskScheduler` runs async task functions in worker threads with isolated event loops
- Tasks accept `TaskContext` for progress tracking, cancellation, and user responses
- `TaskContext.request_response()` pauses execution to show user dialogs
- `TaskContext.subtask()` — spawn a subtask

**Operations:** `nova_navigator/operations/` — file operations (copy, move, delete) implemented as `Operation` subclasses. `Operation.process()` runs in a thread via `asyncio.to_thread`. `filemanager/tasks.py` contains generator-based task implementations (`copy_file`, `erase`) built on top of `vfs`.

**Terminal:** `nova_navigator/terminal/` — embedded terminal emulator sub-package. See `docs/terminal.md` for the complete architecture guide.
- `pty_backend.py` — `PtyBackend` ABC and `LocalPtyBackend` (PTY process management)
- `shell_driver.py` — `ShellDriver` ABC with `ZshDriver`, `BashDriver`, `FallbackDriver`; shell hooks, quoting, precmd parsing
- `terminal.py` — `Terminal` Textual widget (pyte rendering, draining, event handling)

**Commands:** `nova_navigator/commands/` — Textual-free `CommandRunner` service that runs a final shell script in a terminal or as a background job. See `docs/commands.md`.

**Editor:** `NovaNavigator.open_editor` (`nova_navigator/nova_navigator.py`) pushes the `nova_editor` `EditorScreen`; `nova_navigator/local_copies/editor_session.py` (Textual-free) decides local file or local copy and ends the session with `LocalCopyManager.release`; `nova_navigator/embedded_editor_keys.py` lists the editor actions in the keybindings dialog.
See `docs/editor.md` ("Embedding in the navigator") and `docs/local_copies.md`.

**User menu:** `nova_navigator/usermenu/` — F2 user menu: config parsing, condition evaluation and placeholder expansion (Textual-free), plus the `UserMenuPopup` widget in `popup.py`. See `docs/user_menu.md`.

**UI widgets:** `nova_navigator/widgets/`
- `directory_browser.py` — main dual-pane file browser widget
- `side_bar.py`, `footer.py`, `overlay_widget.py`

**Dialogs:** `nova_navigator/dialogs/` — bookmarks, processes, file dialogs.

**Archive support:** `nova_navigator/archive/` — tar/zip archive filesystem support (`ArchiveFilesystem` used in `vfs/archive.py`).

**Config:** `nova_navigator/config.py`, `nova_navigator/toml_config.py` — TOML-based config; defaults in `config/default/`.

### Threading model

The Textual event loop runs on the main thread.
Long-running operations (file copy, delete) run in worker threads via `asyncio.to_thread`.
The `TaskScheduler` in `task.py` bridges between worker threads and the Textual event loop for user responses.

### `nova_widgets` package

A separate package in `src/nova_widgets/` providing:
- `menu/` — `MenuBar`, `Menu`, `Action` widgets with keyboard shortcuts and checkable items
- `keymap/` — key sequence types, registry, and hint bar widget
- `action.py` — `Action` class for keybindings and menu items, including `ActionGroup` for radio-button groups
- `icon.py` — icon rendering with NerdFont support
- `dialog.py` — the `Dialog` base class with `ButtonSpec` and `DefaultButton`
- `message_box.py` — `MessageBox` (alias `MessageDialog`), the generic confirmation and message dialog
- `response.py` — the `Response` type
- `popup_widget.py` — `PopupWidget`, the base class for floating popup panels
- `keybindings_config.py` — `KeybindingsConfig` for loading/saving user key binding overrides
- `keybindings_dialog.py` — `KeybindingsDialog` and `KeyCaptureDialog` for editing keybindings
- `file_provider.py` — `FileProvider` protocol and `LocalFileProvider`, `InMemoryFileProvider` implementations
- `file_dialog.py` — `FileDialog` for selecting files or directories with pluggable filesystem provider
- The package must not import `nova_navigator` (enforced by `tests/test_package_independence.py`)
- Used in `main.py` via `from nova_widgets.menu import ...`

### `nova_editor` package

A separate package in `src/nova_editor/` providing a standalone text editor with app, screen, document view, and popups.
The package must not import `nova_navigator` or `vfs` modules (enforced by `tests/test_package_independence.py`).
See `docs/editor.md` for the complete architecture.

---

## Coding Conventions

Full reference: `docs/coding_conventions.md`

## Task Scheduler

For implementing long-running async operations: see `docs/scheduler.md` for the scheduler framework guide, API reference, and implementation patterns.

### Python

- Formatter / linter: **ruff** (120-char line length, 4-space indent)
- All functions and methods must have **full type annotations** (`ty check`)
- Use `X | None` — not `Optional[X]`
- Use builtin collection types: `list`, `dict`, `set`, `tuple` — not `typing.List` etc.
- Naming: `snake_case` functions/variables/members, `UpperCamelCase` types/classes, `_` prefix for private names
- Constants: `UPPER_CASE`
- Docstrings: Google style, encouraged for public API, not required by linter; multiline docstrings must start immediately after the opening `"""` (no blank line)
- **Never suppress lint or type warnings with `# noqa` or `# type: ignore` comments.**
  Fix the root cause instead.
  For example, a B027 warning ("empty method in abstract base class") means the method should be decorated with `@abstractmethod`; all concrete subclasses must then provide an implementation (even a one-line `pass` for no-op cases).

---

## Key Configuration Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | uv project config, ruff/ty/pytest settings, script entry points (`nn`, `qa`) |
| `src/nova_navigator/` | Main application package |
| `src/nova_widgets/` | Reusable Textual widgets package |
| `config/default/` | Default TOML config files (bookmarks, filetypes, icons) |
| `tests/` | Test suite (pytest) |

---

## Dialogs

Every new dialog class **must** be registered in `src/tools/dialog_tester.py`.
Add a `DialogEntry` to `_ENTRIES` with a `factory` lambda that constructs the dialog with representative arguments.
If the dialog exposes extra state after dismissal (e.g. `selected_path`, `credentials`), supply a `result_fn` lambda to include that in the printed result.

```sh
uv run dialog_tester --list          # verify the entry appears
uv run dialog_tester MyNewDialog     # smoke-test it interactively
```

---

## Writing GUI Tests

Textual widgets are tested using `App.run_test()`, which returns an async context manager yielding a `Pilot`.
Tests live under `tests/nova_widgets/` and use `pytest-asyncio` with `@pytest.mark.asyncio`.

### Minimal test app

Wrap the widget under test in a minimal `App`:

```python
from textual.app import App, ComposeResult

class MyTestApp(App[None]):
    def __init__(self, widget: MyWidget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget
```

### Interacting with the widget

```python
@pytest.mark.asyncio
async def test_something() -> None:
    app = MyTestApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()              # let the app settle after mount

        await pilot.press("down")        # send a key
        await pilot.hover(widget, offset=(2, 1))  # move mouse to row 1
        await pilot.click(widget, offset=(2, 1))  # click at that position
        await pilot.pause(delay=0.1)     # wait for async reactions

        assert widget.some_state == expected
```

Key rules:
- Always `await pilot.pause()` after mounting before inspecting state.
- `pilot.click()` does **not** fire a `MouseMove` first — call `pilot.hover()` before `pilot.click()` when the widget reacts to hover (e.g. to set a highlight).
- Use `app.query(WidgetClass)` to find widgets in the DOM.
- Prefer `pilot.click(widget_instance)` over manually posting messages.

### Inspecting the DOM

```python
items = list(app.query(MenuBarItem))   # all instances of a type
first = app.query(MenuBarItem).first() # first match
active = [w for w in app.query(MenuBarItem) if w.has_class("-active")]
```

### Visualising a test

Pass `headless=False` to render the app live in the terminal while the test runs:

```python
async with app.run_test(headless=False) as pilot:
    await pilot.pause(delay=1.0)  # slow down to observe
```

Run a single test with output visible:

```sh
uv run pytest tests/nova_widgets/test_menu.py::test_mouse_click_triggers_hovered_item -s
```

