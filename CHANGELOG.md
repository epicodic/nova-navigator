# Changelog

## 0.1.0

### Added

- Two independent file panels side by side.
- Browse SSH servers as if they were local folders.
  SSH is tested with OpenSSH servers on Debian and on Alpine (BusyBox) in containers: password and key-file login, identity files from `~/.ssh/config`, ProxyJump, host key prompts, listing, copy, move, delete, symbolic links and editing.
- Open tar and zip archives as folders.
- A live shell in the same window.
  The embedded terminal runs the shell named by `$SHELL`; bash and zsh have full integration.
  With bash and plain shells, a long line wraps when the terminal is one row high, and only the row with the cursor shows.
  zsh behaves as it does in any one-row terminal.
  Horizontal scrolling of the command line (readline's `horizontal-scroll-mode`) is turned off in the embedded bash once the setup line has run.
  It is not turned off for a bash that is treated as a plain shell: `sh` linked to bash, or an SSH shell that could not be detected.
- A built-in text editor, `ned`, which opens from the file manager with F4 and can also run on its own.
- Copy (F5), move (F6) and delete (F8), with progress and conflict resolution.
- Long operations run in the background and can be cancelled.
- View and cancel running jobs in the Jobs dialog.
- Bookmarks for frequently used folders.
- Icons and file types set in a configuration file.
- Icons from a Nerd Font when one is installed; Unicode icons otherwise.
- A menu bar; arrow keys, Enter and Esc work once a menu is open.
- F1 opens help.
- F3 views a file read-only.
- The right panel's folder and sort order are restored when the application starts again.
- Azure Blob Storage, installed with the optional `azure` extra.
  Sign in with `az login` is the expected way; this has not been verified.

### Known issues

#### Possible data loss

- The same storage reached in another way, for example under another host name, user or port, is not recognised, so copying or moving a file or folder onto itself that way can destroy it.
- Servers with the same address, port and user name behind different jump hosts are treated as one machine.
  So copying or moving a file or folder between them to the same path, or a folder to a path inside it, is skipped.
- Two hard-linked names on SSH are not recognised as one file, so copying or moving one onto the other may destroy data.
  This was not tested.
- If a directory is replaced by a symbolic link while it is being deleted, moved, or removed with `rm -r` in the built-in shell, the operation can follow the link.
- In the built-in shell, inside folder `d`, `cp -r d .` can overwrite a file in `d` with the file of the same name from `d/d`.
  The command still exits with 0.
- Killing the application while it replaces a link can leave the destination missing.
  Changes made by other programs during a copy are not detected.
- An interrupted save can damage a configuration file: `remotes.toml` and `keybindings.toml` always, `settings.toml` and `bookmarks.toml` when the directory is read-only.

#### SSH

- Login through ssh-agent and passphrase-protected keys are not tested.
- A command on an SSH server that never reports an exit status blocks until you cancel it.
- SSH servers need SFTP. On a server with uutils tools, a directory with non-UTF-8 names (or U+FFFD) cannot be listed if the SFTP listing fails.
- `ssh://user:password@host` is not supported: `user:password` is sent as the user name and shown in the login dialog.
  Add to Bookmarks can store such an address, password included, in `bookmarks.toml`.
  The password also appears in the folder names of local copies.
- Wildcard, negated and `@cert-authority` entries in `known_hosts` are not matched, so you are asked to confirm such a host again.
  A second key of the same type for a host is reported as a changed key.
- A new host key is stored even if you then cancel the login.
  If storing it fails after a failed login, there is no warning.
- Of `~/.ssh/config`, `ProxyCommand` and `Include` are ignored, and `Match` blocks with other than the basic keywords never apply.
- A hanging name lookup can delay a connection beyond the configured SSH timeout.
- Reading a non-UTF-8 link target needs `readlink` and `od` on the server.
  A server that omits file attributes gets default values.
- On SSH files and TAR members, a change that keeps the size and happens within the same second is not detected.
  A ZIP or TAR file on SSH inherits this for the whole archive.
- Nothing stops another writer from racing the write-back.
  Write-back needs the `posix-rename@openssh.com` SFTP extension.
  Write-back into a nested archive on a remote host is untested.
- Large directories on SSH are slow to list, copy, move and delete, especially with many links or non-GNU servers.
- A remote command that writes very much to its error output can stall.
- Unusual server tools can make a directory show as empty or fail with an input/output error.
- Every move to or from an SSH server, also within one server, copies and then deletes.
- Entering an `ssh://` address without a path in "Go to path", on a panel of that server, opens `/`, not the login directory.
- When following links and a path cannot be resolved (for example non-UTF-8), a link loop is followed up to 16 levels.
- Some connection error texts are formatted oddly.
- Host names in `known_hosts` are stored and matched as typed, so another spelling (such as different letter case) is asked about again.

#### Azure

- Azure Blob Storage is tested against the Azurite emulator.
  It has not yet been verified with a real storage account.
- Renaming a large Azure blob whose server-side copy takes more than 300 seconds fails; the source is kept.
- The first access to Azure can take about 90 seconds (seen with default settings on a development host with proxy environment variables).
- `azure://` addresses work with host-style endpoints only.
  A `.keep` file marks directories and may show as a hidden file.
  A change made just while a blob is saved can be overwritten.
  Deleting a missing path reports nothing.

#### Archives

- A zip or TAR member that you edit and save back keeps its old date.
- A zip directory without its own entry may show no date.
- Archives do not keep empty directories.
- `.jar`, `.war`, `.ear`, `.apk`, `.whl` and single compressed files are read-only.
- Writing into a large archive rebuilds it for every file.
  Opening a large `.gz`, `.bz2` or `.xz` blocks while it is decompressed.
- Hard links in a TAR are copied as separate files.
- A panel inside an archive can show old sizes and members until it is refreshed.

#### Viewer and editor

- While a large remote file downloads for F3 or F4, the application accepts no keys and the download cannot be interrupted midway.
- With a menu open in the viewer, F10, Ctrl+W or Ctrl+Q closes the viewer at once.
  Esc closes the menu first.
- A failed download for F3 or F4 shows no message, only a failed job.
- Choosing "Abort" in the error dialog for an unreadable file ends the application.
- "Quit and keep files" while a file is still downloading for viewing can make that download fail.
- If writing a local copy back to its source hangs, quitting waits for it and further quit keys are ignored.
  The unsynced local file stays on disk.
- Cancelling F4 just after the download may leave a local copy behind.
  This was not reproduced.
- Saving in the editor creates a new file, except for a plain Save that only overwrites bytes (same length, nothing moved) in a file not changed on disk, which is patched in place.
  When the editor creates a new file, hard links, owner, group, ACLs and extended attributes are not kept; only permission bits are restored.
  See the [Known Limits section of the editor documentation](https://github.com/epicodic/nova-navigator/blob/main/docs/editor.md#known-limits-1).
- The viewer has no default key for Wrap Mode.
- F1 help does not list the editor's cursor movement, selection and other fixed text keys.

#### Settings and layout

- A `settings.toml` with a syntax error stops the start.
  If the file is read-only, OK in the settings dialog fails.
- Closing the terminal window, SIGTERM (a termination signal) or a crash loses this session's right-panel folder and sort order.
- A right-panel folder whose name is not valid UTF-8 is not remembered.
- Three file-panel keys, Insert, Ctrl+A and Ctrl+F, are not listed in the Key Bindings dialog. They can only be changed by hand in `keybindings.toml`, using the quoted action name, for example `"browser.insert_select"`; any save in the Key Bindings dialog removes such entries.
  Tab and Shift+Tab are fixed keys.
- Ctrl+F1 opens Settings, so the terminal's "Ctrl+F1 leaves focus" appears not to work.
  This was seen in the code and not tried.
- With several instances open, the last one to quit decides the stored right-panel folder and sort orders.
  Restoring a local folder can mount it or wait on a stale network mount.
- The hidden-files setting does not apply to the file dialog, which has its own switch.
- Hide Identical keeps its setting while compare is off, and the other panel can update late.
  No files are affected.
- In terminals that use the kitty keyboard protocol, the numpad + and - keys do not work after Ctrl+S; use the main-keyboard keys.
- A leftover `proxy` entry under `[network]` in `settings.toml` is ignored.

#### Security and crash reports

- Crash reports do not hide passwords typed or pasted into the embedded terminal, a user-menu prompt or another free-text field.
- The crash report is deleted when `nn` ends after most crashes inside the application.
- Crash reports can still contain a password that was being typed at the time of the crash, that is part of a URI and contains `/`, that appears in unusual nested or split forms, or that is in a configuration file being read at that moment.
  Check a crash report before sharing it.
- Crash reports may hide more than necessary.
- Passwords written into a URI in `remotes.toml` or `bookmarks.toml` stay on disk.

#### Terminal

- Typing text and then deleting it can leave Nova Navigator treating the line as still occupied.
  One Enter on a file panel is then sent to the terminal instead of navigating.
  That Enter clears the state, and the next Enter navigates.
  It happens with bash after a line of more than two rows in the one-row terminal (which then shows an empty row until Ctrl+L or Enter), with zsh after a line wider than a one-row terminal, with `sh` or dash after a line that had wrapped, and after typing before a slow prompt has appeared.
- bash can loop forever, using a whole CPU, when its prompt has colour or other invisible codes, the typed line is wider than the terminal and the width changes, with a one-row terminal or with `horizontal-scroll-mode` on.
  The embedded bash is protected from this.
  A bash started inside zsh or inside the embedded shell, or reached by a typed `ssh`, can still do it where one of these applies.
- Nothing ends a looping shell if Nova Navigator itself crashes or is killed, and a looping shell on an SSH server cannot be killed by closing its terminal.

#### Other

- Text typed while a command is still running is not detected, even when it was already entered, so a later panel change can write its `cd` into that text and the combined line may run.
  In shells without bracketed paste (sh, dash), further lines of a multi-line paste at a prompt are not detected either.
- If you type or paste into the terminal, or keys are passed to it from a file panel (such as typed characters or inserted file names), while its shell is still starting, the shell stops following the panels until you press Enter in it once more after it is ready, even if you cleared the line.
  Once it is ready and until that Enter, Enter in a file panel goes to the terminal, and runs any early text still on the line.
  Ctrl+C pressed before the shell is ready can discard its setup; the shell then does not follow the panels until it is restarted (for the local shell, type `exit`).
- On a slow shell, Enter pressed before the typed command appears may act on the file panel instead of the terminal.
  This was seen in the code, but not reproduced in the application.
- A move across devices that follows links (or to a destination without links) can skip, as broken, a link whose target was already moved in the same move.
- Where `P/d` is a symbolic link, copying or moving `P/d/d` into `P` removes the link and then fails; over SSH it can report success and leave an empty folder.
  File contents are kept.
- Relative links copied to another host keep their text even with the stable-links option.
- The built-in shell's `cp` and `mv` have no link options.
- With stable links, `..` after a linked directory is read textually.
- A followed directory target that disappears can fail the job when its real path cannot be determined.
- The overwrite question for a folder says "File".
- A move within one device renames, so relative links can end up pointing elsewhere.
  Link modification times are not kept.
  A cross-device move leaves skipped links and their folder behind.
- Some skipped entries are not listed in the Jobs dialog.
- A file you answered No for is not listed as skipped.
  Rows hidden by Hide Identical lose their selection.
- There is no "Preserve attributes" switch; permission bits and modification time are copied where the destination supports them; owner, group, ACLs and extended attributes are not.
- There is no "Dive into subdir if exists" switch.
- There are no rename masks for copy and move.
- The copy and move dialog has no "Background" button; jobs already run in the background.
- The developer script `create_bundle_shiv.sh` does not include the Azure SDK in the bundle it builds.
- fish, csh and other shells without a POSIX hook run as a plain terminal: no directory sync, no user menu commands in the terminal; the screen appears after a few seconds.
- With `sh` or dash the shell has no line editing; a panel change waits while text is typed.
- In vi editing mode, outside insert mode, directory sync and terminal commands can report the terminal as busy.
- While the terminal is focused, F1 and F3 go to the shell, not to help or the viewer.
- The menu bar cannot be opened from the keyboard.
