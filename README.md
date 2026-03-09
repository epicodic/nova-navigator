# Nova Navigator

A modern TUI file manager for Linux, inspired by Midnight Commander.
Built with Python and [Textual](https://github.com/Textualize/textual).

## Features

- **Dual-pane file browser** — two independent panels side-by-side for easy file management
- **Virtual File System (VFS)** — unified abstraction over local, SSH, and archive filesystems
- **SSH support** — browse remote hosts as if they were local directories (via paramiko)
- **Archive navigation** — open tar and zip archives as virtual directories
- **Embedded terminal emulator** — a live shell inside the same window
- **File operations** — copy (F5), move (F6), delete (F8) with progress tracking and conflict resolution
- **Async task scheduler** — long-running operations run in the background with cancellation support
- **Process monitor** — view and cancel running jobs
- **Bookmarks** — quick access to frequently used directories
- **File type associations** — icon and MIME type configuration via TOML
- **NerdFont icons** — rich icon rendering for files and directories
- **Menu bar** — full keyboard-driven menu with shortcuts

## Requirements

| | |
|---|---|
| OS | Ubuntu 24.04 (macOS and Windows are not supported) |
| Python | 3.12 or later |
| Package manager | [`uv`](https://github.com/astral-sh/uv) |

## Installation

```sh
git clone <repo-url>
cd nova-navigator
uv sync
```

## Usage

```sh
uv run nn
```

### Keyboard shortcuts

| Key | Action |
|---|---|
| `F5` | Copy |
| `F6` | Move |
| `F8` | Delete |
| `F4` | Open in editor |
| `Ctrl+O` | Toggle maximize terminal |
| `Ctrl+L` | Enlarge terminal |
| `Ctrl+Q` | Quit |

## Configuration

User configuration is stored in `~/.config/nova-navigator/` and overrides the defaults shipped in `config/default/`.

| File | Purpose |
|---|---|
| `bookmarks.toml` | Bookmarked directories with icons |
| `filetypes.toml` | MIME type to icon mappings |
| `icons.csv` | Icon name to NerdFont glyph mappings |

## Development

### Running tests

```sh
uv run pytest                                   # all tests
uv run pytest tests/test_archives.py           # single file
uv run pytest tests/test_archives.py::test_foo # single test
```

### Quality checks

```sh
uv run qa          # lint, type check, and tests
uv run qa --fix    # same with auto-fixes applied
```

Individual tools:

```sh
uv run ruff check .   # lint
uv run ruff format .  # format
uv run ty check .     # type check
```

## Debug Analytics

Debug analytics are disabled by default.
When enabled, a per-run directory is created at `~/.cache/nova-navigator/debug-analytics/<pid>/` containing:

- **`app.log`** — full `DEBUG`-level application log
- **`crash.txt`** — GDB-style crash report with stack frames, source context, and local variables

The directory is deleted automatically on a clean exit.
Only crash runs leave files behind.

### Environment variables

| Variable | Description |
|---|---|
| `NN_DEBUG_ANALYTICS` | Set to any non-zero value to enable debug analytics. |
| `NN_LIVE_PDB` | Set to any non-zero value to drop into a live `pdb` post-mortem session on crash. Requires `NN_DEBUG_ANALYTICS=1`. |

```sh
NN_DEBUG_ANALYTICS=1 uv run nn                    # enable logging and crash reports
NN_DEBUG_ANALYTICS=1 NN_LIVE_PDB=1 uv run nn     # also drop into pdb on crash
```

## License

MIT License — Copyright © 2025 epicodic
