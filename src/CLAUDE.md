# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Nova Navigator is a modern TUI file manager (like Midnight Commander) built with Python and the [Textual](https://github.com/Textualize/textual) framework. It supports a virtual file system (VFS) abstraction for local, SSH, and archive filesystems.

## Commands

### Setup
```sh
source activate.sh        # activate venv and set PYTHONPYCACHEPREFIX, PYTHONSAFEPATH
```
The venv is at `.venv/` (managed by `uv`). The project uses `uv` for dependency management.

### Run
```sh
uv run nn                 # run the app via the installed script entry point
```

### Tests
```sh
uv run pytest                                  # run all tests
uv run pytest tests/test_archives.py           # run a specific test file
uv run pytest tests/test_archives.py::test_foo # run a specific test
```

### Linting / Type checking
```sh
uv run ruff check .       # lint
uv run ruff format .      # format
uv run ty check .         # type check
```

### Quality Assurance checks (lint, type check, tests) can be run together:
```sh
uv run qa                 # run all QA checks (lint, type check, tests)
uv run qa --fix           # run all QA checks and apply auto-fixes where possible
```

## Architecture

The codebase lives under `src/` and contains two packages:

- **`nova_navigator/`** - the main application
- **`nova_widgets/`** - reusable Textual widgets (menu bar, icon rendering), developed alongside the app

### Key layers

**Entry point:** `nova_navigator/main.py` - defines `NovaNavigator(App)` and `MainScreen(Screen)`. `MainScreen` composes two `DirectoryBrowser` panels side-by-side with a terminal emulator and footer. Keyboard bindings are defined there.

**VFS:** `nova_navigator/vfs/` - the virtual filesystem abstraction:
- `filesystem.py` - `Filesystem` ABC; subclasses implement `cwd()`, `root()`, `home()`, `iterdir()`, `stat()`, `parent()`, `read()`, `write()`, `remove()`, `rmdir()`
- `vpath.py` - `VPath`: a path + filesystem reference pair; stat is lazily cached; supports `/` operator for path joining
- `types.py` - `Stat` dataclass (size, modified, is_hidden, is_directory, is_executable, is_symlink, is_broken_symlink)
- `parse_uri.py` - URI parsing supporting nested schemes (e.g. `ssh://host/archive.tar.gz/tar://file.txt`)
- `filesystems/local.py` - `LocalFilesystem` singleton
- `filesystems/ssh.py` - `SSHFilesystem` via paramiko
- `filesystems/azure.py` - Azure blob stub (incomplete)

**Task system:** `nova_navigator/task.py` - defines a coroutine-like generator protocol for long-running operations that may need user decisions:
- `Task = Generator[DecisionRequest | Task, DecisionResponse, None]`
- `TaskScheduler` runs tasks in a thread and bridges decision requests to the GUI via async callbacks
- Tasks `yield DecisionRequest(...)` to pause and ask the user; the scheduler resumes them with a `DecisionResponse`

**Operations:** `nova_navigator/operations/` - file operations (copy, move, delete) implemented as `Operation` subclasses. `Operation.process()` runs in a thread via `asyncio.to_thread`. `filemanager/tasks.py` contains generator-based task implementations (`copy_file`, `erase`) built on top of `vfs`.

**UI widgets:** `nova_navigator/widgets/`
- `directory_browser.py` - main dual-pane file browser widget
- `terminal.py` - embedded terminal emulator (using `pyte`)
- `side_bar.py`, `footer.py`, `overlay_widget.py`

**Dialogs:** `nova_navigator/dialogs/` - bookmarks, processes, file dialogs.

**Archive support:** `nova_navigator/archive/` - tar/zip archive filesystem support (`ArchiveFilesystem` used in `vfs/archive.py`).

**Config:** `nova_navigator/config.py`, `nova_navigator/toml_config.py` - TOML-based config; defaults in `config/default/`.

### Threading model

The Textual event loop runs on the main thread. Long-running operations (file copy, delete) run in worker threads via `asyncio.to_thread`. The `TaskScheduler` in `task.py` bridges between worker threads and the Textual event loop for user decisions.

### `nova_widgets` package

A separate package in `src/nova_widgets/` providing:
- `menu/` - `MenuBar`, `Menu`, `Action` widgets with keyboard shortcuts and checkable items
- `icon.py` - icon rendering with NerdFont support
- Used in `main.py` via `from nova_widgets.menu import ...`
