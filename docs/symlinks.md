# Symbolic links

This page describes how the VFS models symbolic links and how copy, move, delete and the VFS shell treat them.
The code lives in `vfs/filesystem.py`, `vfs/filesystems/`, `archive/`, `filemanager/tasks.py` and `terminal/vfs_shell/commands/`.

## Filesystem API

`Filesystem` has four link methods.

- `readlink(path)` returns the target text exactly as stored.
  Undecodable bytes are surrogate-escaped, so the text round-trips through `symlink`.
  It raises `OSError` if the path is not a link.
- `supports_symlinks()` tells whether `symlink` can create links on this filesystem.
- `symlink(path, target)` creates a link that stores the target text unchanged.
  The target need not exist.
  Unsupported filesystems raise `OSError(ENOTSUP)`.
- `real_path(path)` returns the canonical path with every link resolved, or `None` if the filesystem cannot tell.
  It raises `OSError` (or `FileNotFoundError`) for a broken or looping link.

`capabilities.symlinks` is a hint for the UI only.
Production code decides with `supports_symlinks()`.

| Filesystem | `supports_symlinks` | `real_path` |
|---|---|---|
| `LocalFilesystem` | yes | `os.path.realpath(strict=True)` |
| `SSHFilesystem` | yes | SFTP `REALPATH`; `None` for a non-UTF-8 path |
| `ArchiveFilesystem` | no | resolved inside the archive (tar); `None` otherwise |
| `AzureFilesystem` | no | `None` |
| `RemoteFilesystem` | forwarded | forwarded explicitly |
| `MockFilesystem` (tests) | `symlinks=` flag | `None` unless a test overrides it |

`RemoteFilesystem` forwards the three methods to the inner filesystem and converts the `VPath` arguments and results.

## Link-first rule

`Stat.is_symlink` is checked before `Stat.is_directory`.
A link to a directory is therefore a link, not a directory.
`VPath.walk` never descends into a link.
Delete and the removal of a move source never follow a link.
They call `Filesystem.remove` on the link itself.

## Copy decision

`_copy_link` in `filemanager/tasks.py` is the single place that decides what to do with a link.
Copy and both cross-device move paths call it.

| Destination | Link kind | Action |
|---|---|---|
| supports links | any kind | create a link with the same target text; the target is never read or entered |
| no links | broken | create nothing and report `"broken link"` |
| no links | file target | copy the target content as a file |
| no links | directory target | copy the target tree under the link name, unless it loops |

Filters apply to a link by its name, like a file.
A link that a filter excludes counts as completed and is not reported.
An existing destination follows the overwrite policy of file copies (`skip`, `ask` or `overwrite`).
Overwriting removes the old entry itself and never follows it.
`readlink` runs before the overwrite check, so an unreadable link never removes an existing destination.

## Copy and move into existing folders

`_ensure_directory` in `filemanager/tasks.py` is the one place that makes a destination folder usable.
Copy and the cross-device move call it for the root and then for every subfolder, before that folder's contents are handled.
A same-device move into a missing destination is a plain rename and never calls it.
A same-device move onto an existing real folder also calls it for the root and every subfolder, so the root destination is stat'ed in `_move_path` and again in `_ensure_directory`.
Each call stats the destination first, after refreshing the parent listing.

It returns one of three outcomes.

- `created`: nothing is at the destination, so the folder is created.
  Only folders created this way get the source mode and mtime through `copy_stat`.
- `merged`: the destination is a real folder, so it is reused without a question.
  Its own mode and mtime are kept.
  If `mkdir` finds the path present after all, the folder counts as merged too.
- `skipped`: the overwrite policy kept the existing entry.

The check is link-first, as for every entry.
A link to a folder is not a real folder.
A file or a link of any kind at the destination goes through the overwrite policy.

- `skip`: the entry is left alone and nothing from that folder is copied.
- `ask`: the user is asked once for that entry.
  The `ALL` and `NONE` answers are shared with file prompts, because the scheduler caches them under the `Overwrite` title.
- `overwrite`: the entry itself is removed, never followed, and then the folder is created.

A skipped folder counts as completed work and is not reported as skipped.
Its contents are not copied and add nothing to the progress totals.

A move leaves a skipped source folder in place, together with its contents.
A source folder is removed only after its contents were moved, so a folder that still holds a skipped entry stays.

A top-level destination that is a link to a folder counts as an existing directory.
The source goes inside it under its own name, and the link is not replaced.

## Loop detection

Loops only matter when a directory link is copied as a tree.
A link-capable destination never walks through links.

Each descent carries a chain of the resolved directory positions that were entered through links.
A directory link is a loop if its resolved target is an ancestor of, or equal to, the directory that holds it or any chain position.
The check uses `real_path`.
This rule detects loops along the current descent, not across the whole copy.
A link whose target is already being copied higher up in the same descent is skipped as a loop.
A target that appears only in a sibling branch is copied normally.

`_MAX_LINK_DEPTH = 16` is a backstop for the nesting of link-entered directories.
When `real_path` returns `None`, the link is walked through a followed view and only the depth guard protects the copy.

## Skip reasons

A link that is left out is reported with `TaskContext.report_skipped(link.uri, reason)`.
There are three exact reasons.

- `"broken link"`: the target does not exist and the destination has no links.
- `"link loop"`: the directory link loops, or the depth guard was hit.
- `"unreadable link"`: `readlink` failed on the source.

The app shows one toast per finished job and the Jobs dialog lists each path with its reason.
A skipped link counts as completed work.
See `docs/scheduler.md` for the reporting API.

## Cross-device move

A move across devices copies a link with `_copy_link` and then removes the source link, but only if it was copied.
A skipped link stays in the source.
Its parent directory then cannot be removed and stays too.
A directory link is removed from the source once its tree copy has finished, except when the destination folder itself was skipped.
The target of a directory link is only read, never touched.
Moves on the same device rename the link itself.

## Archives

A tar symlink member reports `is_symlink`, and the other stat fields come from the resolved target.
`readlink` returns the stored text and `real_path` resolves inside the archive.
Broken, absolute, escaping and looping links resolve as broken.
Hard links read as regular files.
ZIP never reports links; an entry with a link mode reads as a file holding the target text.

Archives have no link support as a destination.
Writes keep existing members unchanged but cannot add links.
`ArchiveFilesystem.mkdir` is an implicit no-op, because directories only appear with the first member below them.
It raises `PermissionError` for a read-only archive, `FileExistsError` for an existing path and `NotADirectoryError` if an ancestor is a file.
Otherwise it does nothing.
Empty directories are therefore not stored in an archive.
The modification time of a link is not preserved.

## SSH

Link targets that SFTP cannot decode as UTF-8 are read through a shell fallback.
The fallback runs `readlink -n` and prints the bytes with `od`, so the host needs both tools.
The path in the script is built with `_quote_path`, so it stays pure ASCII.
A non-UTF-8 path makes `real_path` return `None`.

`SSHFilesystem.mkdir` raises `FileExistsError` for an existing path.
SFTP reports an existing path as a generic failure, so `mkdir` runs `lstat` after the failed call.
A dangling link counts as existing.
SSH is never on the same device, so `is_same_device` is always false and every move to or from SSH is copy-based.
This is verified against the stub server only.

The modification time of a link is not preserved, because `utime` and `chmod` follow links.
Each `stat_or_none` costs one directory listing, so large trees cost O(n) listings.

The SSH behaviour is tested against the stub server only.
No real OpenSSH server is part of the tests.
The order of the `SYMLINK` arguments (target first) and the `REALPATH` resolution follow OpenSSH and are assumed.

## VFS shell

`rm` removes a link as an entry and never lists or removes its target, with or without `-r`.
`cp -r` recreates links where the filesystem supports them.
Elsewhere it copies a file target and skips directory and broken links with a message and a non-zero exit code.
The rest of the tree is still copied.
`cp` without `-r` keeps copying a link to a file as content and omits a link to a directory.
`mv`, `ls`, `glob` and `cd` are unchanged.
