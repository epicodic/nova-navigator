# Command Execution Service

`nova_navigator.commands` runs a final, already-expanded shell script, either in a panel's terminal or as a background job.
The package has no Textual imports; it reaches the terminal and the job list through small protocols implemented by the UI layer.
It is used by the user menu (see `docs/user_menu.md`) and is meant for other features to reuse.

## Core Types

`Command` (`nova_navigator/commands/runner.py`) is a frozen dataclass with three fields:

- `script`: the shell script to run, already expanded and shell-quoted by the caller.
- `cwd`: a `VPath`; its filesystem decides where the command runs.
- `label`: names the job and any notifications or message boxes.

`CommandMode` is a `StrEnum` with `TERMINAL` and `BACKGROUND`.

`CommandResult` is a frozen dataclass with `exit_code: int | None` and `output: str`.
`exit_code` is `None` in terminal mode, since the shell does not report it back.
`output` is the tail of merged stdout/stderr in background mode, `""` in terminal mode.

## `CommandRunner`

`CommandRunner.can_run(cwd, mode)` returns `cwd.filesystem.capabilities.commands`; the same check gates both modes.

`await CommandRunner.run(command, mode)` runs the script and returns once it has finished.
It raises:

- `CommandNotSupportedError`: `can_run()` is false, or terminal mode has no terminal available for the filesystem.
- `TerminalBusyError`: terminal mode and the target terminal cannot accept the command right now (propagated from `Terminal.run_command()`).
- `CommandCancelledError`: background mode and the user cancelled the job.
- `CommandFailedError`: background mode and the job produced no result (e.g. the connection failed).

All of these subclass `CommandError` (`nova_navigator/commands/errors.py`).

The runner never refreshes panels, shows dialogs, or expands placeholders; callers do that.

## Terminal Mode

`CommandRunner` depends on two protocols instead of importing Textual widgets directly:

- `TerminalProvider.terminal_for(fs)`: returns a `CommandTerminal` for the filesystem, or `None`.
- `CommandTerminal`: has a `cwd` property and `async def run_command(self, line: str) -> None`.

`Terminal` (`nova_navigator/terminal/terminal.py`) implements `CommandTerminal`.
`MainScreen` implements `TerminalProvider` and owns the `TerminalPool`.

`terminal_line(script, cwd, terminal_cwd)` builds the line to type:

- A multi-line script is wrapped as `sh -c '<quoted script>'`; a single-line script is sent unchanged.
- If the terminal's current directory differs from `cwd`, `cd <cwd> && ` is prepended.

`Terminal.run_command(line)` types `line` into the shell as if the user had typed it, and waits for it to finish.
See `docs/terminal.md` → "Running Commands (`run_command`)" for the full behaviour: busy checks, stashing and restoring typed input, and completion on the next precmd.

## Background Mode

`Filesystem.exec_command(command, cwd, should_cancel=None)` (`nova_navigator/vfs/filesystem.py`) is the contract background mode relies on:

- Runs `command` with `sh -c` in `cwd` and waits for it to finish.
- Standard input is closed, so a command that prompts for input fails instead of hanging.
- Stdout and stderr are merged; only the last `OUTPUT_TAIL_LIMIT` (64 KiB) bytes are kept, decoded as UTF-8.
- `should_cancel` is polled while the command runs; when it returns `True` the command is terminated and the result has `exit_code == -1`.
- Only available when `capabilities.commands` is `True`; the base implementation raises `NotImplementedError`.

`FilesystemCapabilities.commands` is the flag `CommandRunner.can_run()` reads.
`Filesystem.scheme` (`"local"`, `"ssh"`, `"archive"`, `"azure"`) identifies the filesystem type; it is what the user menu's `on` field matches against, and archive/Azure filesystems never set `commands = True`.

`LocalFilesystem.exec_command()` uses `subprocess.Popen(["sh", "-c", command], cwd=...)` in a new process group, so cancellation can kill the whole group.
`SSHFilesystem.exec_command()` builds the script `cd <cwd> || exit 1\n<command>`, shell-quotes the whole script, and runs it over paramiko's `exec_command` as `sh -c '<script>' 2>&1`; it closes stdin right after issuing the command.
SSH cancellation closes the channel; since no PTY is requested, the remote shell gets no `SIGHUP`, so the remote process can keep running detached from the closed channel.

`CommandRunner._run_in_background()` wraps `exec_command()` (via `asyncio.to_thread`) in a scheduler `Job` labelled with `Command.label`.
The job is added to the app's job sink (`JobSink.add_job()`), so it appears in the jobs dialog and can be cancelled; cancelling it sets the `should_cancel` flag `exec_command()` polls.
See `docs/scheduler.md` for `Job` semantics: states, progress, cancellation, and how the jobs dialog drives them.

## Usage Example

```python
from nova_navigator.commands import Command, CommandError, CommandMode

@work
async def action_run_something(self) -> None:
    command = Command(script="echo hello", cwd=self.active_panel().path, label="Say hello")
    try:
        result = await self.command_runner.run(command, CommandMode.BACKGROUND)
    except CommandError as exc:
        await MessageBox(str(exc), title="Say hello", variant="error").run()
        return
    if result.exit_code != 0:
        await MessageBox(f"Exit code {result.exit_code}\n\n{result.output}", title="Say hello", variant="error").run()
```

`script` must already be fully expanded and shell-quoted before it reaches `Command`; the runner does no templating of its own.
`self.command_runner` is `MainScreen.command_runner`, a `CommandRunner` created lazily because it needs the running app (see `nova_navigator.py`, region "user menu").
