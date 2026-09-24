# User Menu (F2)

The user menu is an MC-style F2 popup of user-defined commands.
Each entry can run a shell script in the terminal or as a background job.

## Overview

F2 opens the menu (`app.user_menu` action).
Entries come from `~/.config/nova-navigator/usermenu.toml`, owned and (re)loaded by `UserMenuStore` (`nova_navigator/usermenu/store.py`).
The file is created from the built-in default the first time it is needed.
"Command → Edit User Menu File" (`app.edit_user_menu`) opens it in the internal editor, creating it first if missing.
Changes to the file take effect the next time F2 is pressed; there is no need to restart.
The file is only re-read when its modification time has changed since the last load.

If the menu file cannot be parsed, or cannot be read at all (permissions, invalid encoding, a deleted config directory), a notification names the problem and the built-in default menu is used instead.

`UserMenuPopup` (`nova_navigator/usermenu/popup.py`) is the widget F2 shows.
It is a `nova_widgets` `Menu` with an MC-style hotkey column, shown modally with `Menu.exec()`, the same way the right-click context menu is shown.
Pressing an entry's hotkey selects it immediately; Up/Down/Enter navigate and run, and Escape closes it, exactly as in other menus built on `Menu`.

F2 was the shortcut for Rename before the user menu existed; Rename now defaults to Shift+F6, and F2 opens the user menu.
Both shortcuts come from `~/.config/nova-navigator/keybindings.toml` like any other action, so they can be rebound.
An existing `keybindings.toml` that still binds `browser.rename` to `f2` should be updated, since F2 no longer reaches Rename by default.

## Example Entry

This is the built-in `compress` entry, copied from `src/nova_navigator/_default/usermenu.toml`:

```toml
[compress]
key = "c"
label = "Compress to archive…"
group = "Archives"
on = ["local", "ssh"]
when = "targets"
mode = "background"
run = "tar czf {archive} -- {targets.name}"

  [[compress.input]]
  name = "archive"
  prompt = "Archive name"
  default = "{dir.name}.tar.gz"
```

`key` and `label` give it the hotkey `c` and its menu text.
`group = "Archives"` draws a separator between it and entries from a different group.
`on` allows it on local and SSH panels; `when = "targets"` shows it only when at least one item is targeted.
`mode = "background"` runs it as a cancellable job instead of in the terminal.
The `[[compress.input]]` table asks for an archive name before running, pre-filled from `{dir.name}.tar.gz`.
`run` uses `{archive}` (the input value) and `{targets.name}`, an attribute mapped over the `targets` list; `--` stops a target name that starts with `-` from being read as a `tar` option.

## Fields

Each entry is one named TOML table.
The table name is the entry id, used in messages and logs.

| Field | Required | Default | Meaning |
|---|---|---|---|
| table name | yes | — | Entry id, used in messages and logs |
| `label` | yes | — | Text shown in the menu; must not be blank |
| `key` | no | none | Single visible character hotkey; must not be blank or whitespace |
| `group` | no | none | A separator is drawn between consecutive visible entries whose `group` differs |
| `on` | no | `["local"]` | Filesystem schemes the entry may run on; must not be an empty list |
| `when` | no | always visible | Python expression deciding visibility |
| `default` | no | `false` | Python expression or boolean; the first visible entry where it is true is highlighted |
| `mode` | no | `"terminal"` | `"terminal"` or `"background"` |
| `run` | yes | — | POSIX `sh` script, single or multi-line; must not be blank |
| `input` | no | none | Array of `{name, prompt, default}` tables asked in one dialog before running |

`input.name` must be a valid Python identifier, must not be a Python keyword, must not shadow a context name (see below), and must be unique within the entry.
`input.prompt` must not be blank.
`input.default` is optional and may contain placeholders.
Validation errors for an input field are reported as "input #N", where N is its 1-based position in the entry's `input` array.

A TOML syntax error, an unknown field, or a validation error anywhere in the file discards the whole file: a notification names the file and the problem, and the built-in default menu is used until the file is fixed.

## Condition Context

`when`, `default` and placeholders share the same names.
All objects are read-only wrappers, never raw `VPath` objects.

### Top-Level Names

| Name | Type | Meaning |
|---|---|---|
| `file` | `FileInfo \| None` | Item under the cursor; `None` on `..` or in an empty directory |
| `selection` | `list[FileInfo]` | Explicitly marked items, possibly empty |
| `targets` | `list[FileInfo]` | `selection` if non-empty, else `[file]`, else empty |
| `dir` | `DirInfo` | Active panel directory |
| `other` | `PanelInfo` | Other panel, with `.file`, `.selection`, `.targets`, `.dir` |
| `which(cmd)` | `bool` | Whether `cmd` exists where the entry would run |
| `env` | mapping | Local environment variables (`os.environ`, always the local ones) |

An `input` field's name is also visible, once its dialog has been answered, wherever placeholders are expanded.

### `FileInfo` Members

- `name`, `stem`, `ext` (last suffix without the dot, e.g. `"gz"` for `a.tar.gz`).
- `path` (absolute path string), `uri`.
- `is_file`, `is_dir`, `is_link`, `is_broken_link`, `is_executable`, `is_hidden`.
- `size`, `mtime` (`datetime`), `mimetype` (guessed from the name with `mimetypes`; `""` if unknown).
- `matches(*globs, ignore_case=False)`: `fnmatch` on `name`.
- `re(pattern)`: `re.search` on `name`, returns `bool`.

### `DirInfo` Members

- `path`, `name`, `uri`.
- `scheme` (e.g. `"local"`, `"ssh"`), `is_local`, `host` (`""` for local).
- `has(name)`: whether `name` is in the panel's already-loaded listing; does no I/O.
- `find_up(name)`: nearest directory at or above `dir` containing `name`, as `DirInfo | None`.

Directories returned by `find_up()` have no listing, so `has()` is always `False` on them.

### Evaluation Rules

- All expressions are compiled once when the config file is loaded.
- An expression with a syntax error disables its entry; a notification names the entry.
- Allowed builtins: `len`, `any`, `all`, `min`, `max`, `sum`, `sorted`, `str`, `int`, `bool`.
- These builtins are hygiene, not a sandbox: the config file is trusted user input.
- Visibility is decided in this order: `on` contains the active panel's scheme, the command runner can run in that mode there, then `when`.
- Conditions are evaluated in a worker thread, because `which()` and `find_up()` may need SSH round-trips.
- `which()` and `find_up()` results are cached for the duration of one menu open.
- An exception in `when` hides the entry; the error is logged with `logger.exception` at ERROR level, not shown to the user.
- An exception in `default` counts as false.

### Examples

```toml
when = "file and file.mimetype.startswith('image/')"
when = "len(targets) > 1 and all(f.is_file for f in targets)"
when = "dir.find_up('.git') and which('git')"
when = "file and file.is_dir and other.dir.is_local"
when = "file and file.size > 100_000_000"
```

## Placeholders

Placeholders appear in `run` and in `input.default`.

- `{name}` or `{name.attr.attr}`, where `name` is a context name or an input name.
- `run` values are quoted with `shlex.quote`; `input.default` is expanded unquoted (there is no shell involved before the dialog is shown).
- `{name:raw}` inserts the value unquoted, wherever quoting would otherwise apply.
- A list value (e.g. `{targets}`) expands to space-separated, individually quoted items.
- An attribute on a list value maps over its items: `{targets.name}` expands to each target's `name`, space-separated and quoted.
- A `FileInfo` or `DirInfo` value expands to its absolute path.
- A `{` preceded by `$` is never substituted, so `${VAR}` stays intact for the shell to expand.
- `{...}` whose first name is not a context or input name is left unchanged.
- An attribute lookup that fails, or resolves to `None` or a callable, is an error: the command is not run and a message box names the entry and the placeholder.
- Placeholders support only attribute paths, not expressions.

Do not wrap a placeholder in quotes (`'{file}'` or `"{file}"`).
`{name}` already expands to a safely quoted shell token; wrapping it in your own quotes splits arguments or breaks the script.
Use `{name:raw}` only when you are building your own quoting.

Quoting does not stop a value that starts with `-` from being read as an option by the program you call.
Put `--` before file arguments, or prefer `{file}` (the absolute path) over `{file.name}` so the value cannot be mistaken for a flag.

## Execution

`on` lists the filesystem schemes an entry may run on, matched against the active panel's `Filesystem.scheme` (`"local"`, `"ssh"`, `"archive"`, `"azure"`).
Archive panels and the (incomplete) Azure filesystem never support running commands, so entries never run there regardless of `on`.

### Terminal Mode (Default)

Running the entry uses the panel's terminal at its current size without changing the terminal layout or focus.
If the terminal is minimized, command output is retained in scrollback and can be viewed afterwards with Ctrl+O (toggle maximized terminal).
See `docs/commands.md` → "Terminal Mode" for how the script is sent to the shell and why there is no exit code in this mode.

### Background Mode

The command runs as a scheduler job, labelled with the entry's `label`, and appears in the jobs dialog where it can be cancelled.
On a non-zero exit, a message box shows the exit code and the output tail.
On a zero exit, a notification confirms success.
See `docs/commands.md` → "Background Mode" for stdin, output and exit-code mechanics.

Both panels are reloaded after the entry finishes, whichever mode was used.

## Error Handling

| Situation | Behaviour |
|---|---|
| TOML syntax error, unknown field, or an invalid field value anywhere in the file | Notification names the file and the problem; the built-in default menu is used |
| User menu file cannot be read (permissions, non-UTF-8 encoding, a deleted config directory) | Notification with a "cannot read user menu" message; the built-in default menu is used; the file's modification time is not cached, so the next successful read reloads it |
| `when` or `default` expression has a syntax error | The entry is disabled (dropped when the file is loaded); a notification names the entry |
| Exception raised while evaluating `when` | The entry is hidden; the error is logged with `logger.exception` (ERROR level), not shown to the user |
| Exception raised while evaluating `default` | Treated as false; the entry can still be shown, just not highlighted |
| Placeholder attribute error, or an I/O error while expanding a placeholder | The command is not run; a message box titled "User menu" names the entry and the error |
| Terminal busy (`TerminalBusyError`) | A message box titled with the entry's label shows the error; nothing is sent to the terminal |
| Background command exits non-zero | A message box titled with the entry's label shows the exit code and the output tail; both panels are reloaded |
| Background command exits zero | A notification confirms success ("`<label>`: done") |
| Background command cancelled by the user | Nothing is reported; both panels are still reloaded |
| Input dialog cancelled | Nothing happens; the command is never built or run |

## Built-in Default Entries

| Id | Key | Label | Group | Mode | Condition |
|---|---|---|---|---|---|
| `extract` | `x` | Extract archive here | Archives | terminal | `file` is a tar/zip archive |
| `compress` | `c` | Compress to archive… | Archives | background | `targets` is non-empty; asks for an archive name |
| `sha256` | `s` | SHA-256 of targets | Files | terminal | `targets` is non-empty and all targets are files |
| `make` | `m` | Run make | Development | terminal | `dir.has('Makefile')` |
| `git_log` | `g` | Git log of current file | Development | terminal | `file` is set, under a `.git` directory, and `git` is on the path |

All default entries use `on = ["local", "ssh"]`.
See `src/nova_navigator/_default/usermenu.toml` for the full, commented source.
