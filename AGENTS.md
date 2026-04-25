# AGENTS.md — Nova Navigator

Instructions for agentic workers (OpenAI Codex, Claude Code, GitHub Copilot agents, etc.).
This file is also served as `CLAUDE.md` via symlink for Claude Code.

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

```sh
source activate.sh        # activate venv and set PYTHONPYCACHEPREFIX, PYTHONSAFEPATH
```

The venv is at `.venv/` (managed by `uv`). The project uses `uv` for dependency management.

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

## Documentation

If the change involves documentation updates in `docs/`, verify that changes are correct.

### Writing Documentation

- Write one sentence per line. This is mandatory.
- Keep pages short. One idea per paragraph.

---

## Python Virtual Environment

The venv is at `.venv/`, managed by `uv`. After adding, removing, or updating Python dependencies in `pyproject.toml`, run:

```sh
uv sync
```

---

## Architecture

The codebase lives under `src/` and contains two packages:

- **`nova_navigator/`** — the main application
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

**Task system:** `nova_navigator/scheduler/` — async scheduler for long-running operations with user decision support. See `docs/scheduler.md` for the complete scheduler framework guide.
- `AsyncTaskScheduler` runs async task functions in worker threads with isolated event loops
- Tasks accept `TaskContext` for progress tracking, cancellation, and user decisions
- `TaskContext.request_decision()` pauses execution to show user dialogs
- `TaskContext.subtask()` — spawn a subtask

**Operations:** `nova_navigator/operations/` — file operations (copy, move, delete) implemented as `Operation` subclasses. `Operation.process()` runs in a thread via `asyncio.to_thread`. `filemanager/tasks.py` contains generator-based task implementations (`copy_file`, `erase`) built on top of `vfs`.

**UI widgets:** `nova_navigator/widgets/`
- `directory_browser.py` — main dual-pane file browser widget
- `terminal.py` — embedded terminal emulator (using `pyte`)
- `side_bar.py`, `footer.py`, `overlay_widget.py`

**Dialogs:** `nova_navigator/dialogs/` — bookmarks, processes, file dialogs.

**Archive support:** `nova_navigator/archive/` — tar/zip archive filesystem support (`ArchiveFilesystem` used in `vfs/archive.py`).

**Config:** `nova_navigator/config.py`, `nova_navigator/toml_config.py` — TOML-based config; defaults in `config/default/`.

### Threading model

The Textual event loop runs on the main thread.
Long-running operations (file copy, delete) run in worker threads via `asyncio.to_thread`.
The `TaskScheduler` in `task.py` bridges between worker threads and the Textual event loop for user decisions.

### `nova_widgets` package

A separate package in `src/nova_widgets/` providing:
- `menu/` — `MenuBar`, `Menu`, `Action` widgets with keyboard shortcuts and checkable items
- `icon.py` — icon rendering with NerdFont support
- Used in `main.py` via `from nova_widgets.menu import ...`

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

---

## Key Configuration Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | uv project config, ruff/ty/pytest settings, script entry points (`nn`, `qa`) |
| `src/nova_navigator/` | Main application package |
| `src/nova_widgets/` | Reusable Textual widgets package |
| `config/default/` | Default TOML config files (bookmarks, filetypes, icons) |
| `tests/` | Test suite (pytest) |



