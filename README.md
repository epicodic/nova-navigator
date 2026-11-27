# Nova Navigator

A modern terminal file manager for Linux, inspired by Midnight Commander.
It shows two file panels side by side, with an embedded shell.

## Features

- **Two file panels** side by side, each with its own folder.
- **Remote and archive folders**: browse SSH servers, Azure Blob Storage containers, and tar and zip archives as if they were folders.
- **Built-in editor**: press `F4` to edit a file in `ned`, a text editor that comes with the package.
- **Read-only viewer**: press `F3` to look at a file without any risk of changing it.
- **Live shell**: an embedded zsh terminal in the same window.
- **File operations**: copy (`F5`), move (`F6`), rename (`Shift+F6`), delete (`F8`), with progress, cancellation and conflict handling.
- **Background jobs**: long operations run in the background, and the Jobs dialog lists and cancels them.
- **Bookmarks**, a **user menu** (`F2`) and **file type** icons and openers, all set in configuration files.
- **Keyboard driven**: rebindable key bindings and a key help screen (`F1`); the menu bar is opened with the mouse.

## Requirements

- Ubuntu 24.04 or 26.04. macOS and Windows are not supported.
- Python 3.12, 3.13 or 3.14.
- zsh at `/usr/bin/zsh`, which the embedded terminal uses.
  Install it with `sudo apt install zsh`.
- `xdg-open`, the default program for opening a file when you press Enter.
- A Nerd Font is optional.
  When one is installed, the icons are shown; otherwise Unicode icons are used.
- `uv` or `pipx` to install the package.

## Installation

Install with `uv`:

```sh
uv tool install nova-navigator
```

Or install with `pipx`:

```sh
pipx install nova-navigator
```

For Azure Blob Storage support, add the `azure` extra:

```sh
uv tool install 'nova-navigator[azure]'
pipx install 'nova-navigator[azure]'
```

Upgrade with `uv tool upgrade nova-navigator` or `pipx upgrade nova-navigator`.
Uninstall with `uv tool uninstall nova-navigator` or `pipx uninstall nova-navigator`.

## Usage

Start the file manager with:

```sh
nn
```

`nn` takes no options.
`ned` is the editor, and it can also be started on its own:

```sh
ned [file] [--config-dir DIR] [--hex]
```

`ned` opens the file, or an empty document when no file is given.
`--hex` opens the file in the hex view, whatever its content.
`--config-dir` sets the directory of `keybindings.toml`, which defaults to `~/.config/nova-edit`.

## Key bindings

These are the default bindings.
Most of them can be changed in the Key Bindings dialog, which is in the first menu.
`Tab` and `Shift+Tab` are fixed and cannot be changed.
`Insert`, `Ctrl+A` and `Ctrl+F` are not listed in the Key Bindings dialog, but can be changed by setting `browser.insert_select`, `browser.select_all` and `browser.filter` in `keybindings.toml`.

| Key | Action |
|---|---|
| `F1` | Help: show the key bindings |
| `F2` | Open the user menu |
| `F3` | View the file under the cursor read-only |
| `F4` | Edit the file under the cursor in `ned` |
| `F5` | Copy the selected files to the other panel |
| `F6` | Move the selected files to the other panel |
| `Shift+F6` | Rename the file or folder under the cursor |
| `F7` | Create a new folder |
| `F8` | Delete the selected files |
| `Ctrl+Q` | Quit |
| `Ctrl+O` | Toggle the terminal between full screen and the panels |
| `Ctrl+L` | Enlarge the terminal |
| `Ctrl+B` | Open bookmarks |
| `Ctrl+E` | Manage local copies of remote files |
| `Ctrl+H` | Show or hide hidden files |
| `Ctrl+G` | Go to a typed path |
| `Ctrl+Shift+G` | Connect to a remote server |
| `Ctrl+R` | Refresh the file list |
| `Ctrl+F1` | Open settings |
| `Alt+Left` | Go back |
| `Alt+Right` | Go forward |
| `Alt+Up` | Go to the parent folder |
| `Alt+Down` | Follow the symbolic link |
| `Ctrl+S` then `i` | Invert the selection |
| `Ctrl+S` then `a` | Select all |
| `Ctrl+S` then `n` | Select none |
| `Ctrl+S` then `+` | Select by pattern |
| `Ctrl+S` then `-` | Unselect by pattern |
| `Ctrl+T` then `n` | Sort by name |
| `Ctrl+T` then `s` | Sort by size |
| `Ctrl+T` then `t` | Sort by time |
| `Ctrl+T` then `e` | Sort by extension |
| `Insert` | Toggle the selection of the item under the cursor |
| `Ctrl+A` | Select all visible items |
| `Ctrl+F` | Filter the files in the folder |
| `Tab` | Switch between the panels |
| `Shift+Tab` | Send Tab to the shell (command completion) |

When the terminal has focus, `F1` and `F3` go to the shell.
Help is then reached by clicking the first menu.

`F1` shows the key bindings; some fixed keys, such as `Tab`, `Shift+Tab` and `Ctrl+Down`, are not listed.

## SSH, archives and Azure Blob Storage

SSH is tested with OpenSSH servers on Debian and on Alpine (BusyBox) in containers: password and key-file login, identity files from `~/.ssh/config`, ProxyJump, host key prompts, listing, copy, move, delete, symbolic links and editing.
Login through ssh-agent and passphrase-protected keys are not tested.

Connect with `Ctrl+Shift+G` (Connect to); Connect to and Manage Remotes are both in the Go menu.
Remote locations use the forms `ssh://`, `azure://` and `remote://name/path`.

Azure Blob Storage is tested against the Azurite emulator.
It has not yet been verified with a real storage account.
Sign in with `az login` is the expected way; this has not been verified.

Tar and zip archives open as folders.
Archives ending in `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.tbz2`, `.tar.xz`, `.txz` or `.zip` can be changed.
Archives ending in `.jar`, `.war`, `.ear`, `.apk`, `.whl`, `.gz`, `.bz2` or `.xz` are read-only.

## Configuration and files

User configuration is in `~/.config/nova-navigator/`.
The directory does not follow the `XDG_CONFIG_HOME` variable.

| File | Purpose |
|---|---|
| `settings.toml` | Application settings; written from the built-in defaults on first load |
| `bookmarks.toml` | Bookmarked folders; written from the built-in defaults on first load |
| `filetypes.toml` | File type icons and openers; written from the built-in defaults on first load |
| `remotes.toml` | Saved remote connections; written from the built-in defaults on first load |
| `usermenu.toml` | The user menu (`F2`); copied from the shipped default when first needed |
| `keybindings.toml` | Key binding overrides; created when you save a change in the Key Bindings dialog |
| `icons.csv` | Optional icon names and glyphs; when absent, the shipped copy is used |

The shipped defaults are in the installed package, in `nova_navigator/_default/` (`icons.csv` and `usermenu.toml`).

Passwords you enter in the login dialog are not saved.
A password written into a URI in `remotes.toml` or `bookmarks.toml` stays on disk in that file.
Add to Bookmarks stores the address of the item under the cursor, so on a connection opened with a password in its URI, that password is written to `bookmarks.toml`.

The session state, such as the right panel's folder and sort order, is kept in `~/.local/state/nova-navigator/state.toml`.

Local copies of remote files, and the viewer's copies, are kept in `nova-navigator/<pid>` inside the temporary directory, usually `/tmp`.

## Troubleshooting

Debug analytics are off by default.
When enabled, each run writes to `~/.cache/nova-navigator/debug-analytics/<pid>/`:

- `app.log`: the full debug log.
- `crash.txt`: a crash report with stack frames, source context and local variables.

The directory is deleted when `nn` ends, also after most crashes inside the application.
Only an exit that skips this, such as a killed process or an error outside the application's own error handling, leaves it behind.
While an error dialog is open, `crash.txt` is already written: choose Continue to keep `nn` running, and copy `crash.txt` before you choose Abort, because Abort ends `nn` and deletes the directory.
Crash reports can contain passwords, so check `crash.txt` before you share it; the known issues in [CHANGELOG.md](https://github.com/epicodic/nova-navigator/blob/main/CHANGELOG.md) list the cases.

| Variable | Effect |
|---|---|
| `NN_DEBUG_ANALYTICS` | Enables debug analytics when set to any value other than `0`. |
| `NN_LIVE_PDB` | On a crash, opens a live `pdb` session when set to any value other than `0`. It has an effect only with `NN_DEBUG_ANALYTICS` set. |

```sh
NN_DEBUG_ANALYTICS=1 nn
NN_DEBUG_ANALYTICS=1 NN_LIVE_PDB=1 nn
```

## Known issues

The known issues of this release are listed in [CHANGELOG.md](https://github.com/epicodic/nova-navigator/blob/main/CHANGELOG.md).

## Changes

Release notes are in [CHANGELOG.md](https://github.com/epicodic/nova-navigator/blob/main/CHANGELOG.md).

## Development

Setup, tests and quality checks for a source checkout are described in [docs/development.md](https://github.com/epicodic/nova-navigator/blob/main/docs/development.md).

## License

Nova Navigator is released under the MIT License; see [LICENSE](https://github.com/epicodic/nova-navigator/blob/main/LICENSE).
