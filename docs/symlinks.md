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
The first row that matches decides.

| Condition | Action |
|---|---|
| `follow_links` is on | follow the target for every destination: copy the file content, or the directory tree; a broken or looping link is skipped |
| `follow_links` is off, destination supports links | create a link with the same target text; the target is never read or entered |
| `follow_links` is off, destination has no links | follow the target, as in the first row |

`stable_symlinks` only changes the second row.
When it is on and `link.filesystem == dst.filesystem`, the text comes from `stable_link_target` instead.
With `follow_links` on, `stable_symlinks` has no effect.

Filters apply to a link by its name, like a file.
A link that a filter excludes counts as completed and is not reported.
An existing destination follows the overwrite policy of file copies (`skip`, `ask` or `overwrite`).
On the link-creating path, overwriting removes the old entry itself and never follows it.
A real folder at the destination skips the link with the reason `"folder exists"`, without a question.
A followed file is written like any file: a link at the destination is replaced, never written through.
`readlink` runs before the overwrite check there, so an unreadable link never removes an existing destination.

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

## Copy and move onto existing files, links and folders

`_claim_file_destination` in `filemanager/tasks.py` is the one decision point for a file that is about to be written.
Copy, nested copy, followed links and the cross-device move all reach it through `copy_file`, once per destination entry.
It runs after the source is opened and before the destination is opened for writing.
The two same-device move branches, which rename instead of writing, apply the same folder check.
The check is link-first: `Stat.is_symlink` is tested before `Stat.is_directory`.

| Destination | Action |
|---|---|
| nothing there | write the file |
| real folder | skip with `"folder exists"`; no question under any overwrite policy; the folder and its contents stay unchanged |
| file or link of any kind | ask the overwrite policy once, then write or skip |

A move keeps the source of a skipped item.
The old recursive erase of a destination folder by a same-device move is gone, so a file never replaces a folder.
A link source that meets a real folder is skipped the same way.

A destination link that may be overwritten is removed itself and replaced by the file.
This holds for a link to a file, to a folder, a broken link and a looping link.
The target of the link is never written and never touched.
A link to a folder is a link, not a folder, so it is replaced and not skipped.

The replacement is remove-then-write on every filesystem, without a temporary file.
Between the removal and the end of the write the destination has no entry.
When the write fails or is cancelled, the removed link is put back from its `readlink` text, on a best-effort basis.
The restored link is a new inode with a new mtime.
A hard kill between the removal and the end of the write leaves the destination absent.

A regular destination file is still truncated and written in place.
A filesystem without links, such as an archive, never has a link removed.

The destination listing is refreshed once per job, at the entry of `copy_files` and `_move_path`.
This matters for SSH, where the cached `Stat` could be stale.
Type changes by another process between the stat and the write are not detected.

An answer of `skip` or "No" is a decision of the user.
Such a policy skip is not reported, unlike `"folder exists"`.
A move of a file onto a link on the same device renames over it, so the link is replaced.

## Loop detection

Loops only matter when a directory link is followed and copied as a tree.
The holder is computed whenever links are followed, that is when `follow_links` is on or the destination has no links (`_needs_holder`).
Without `follow_links`, a link-capable destination never walks through links, so no holder is computed.

Each descent carries a chain of the resolved directory positions that were entered through links.
A directory link is a loop if its resolved target is an ancestor of, or equal to, the directory that holds it or any chain position.
The check uses `real_path`.
This rule detects loops along the current descent, not across the whole copy.
A link whose target is already being copied higher up in the same descent is skipped as a loop.
A target that appears only in a sibling branch is copied normally.

`_MAX_LINK_DEPTH = 16` is a backstop for the nesting of link-entered directories.
When `real_path` returns `None`, the link is walked through a followed view and only the depth guard protects the copy.

## Skip reasons

An item that is left out is reported with `TaskContext.report_skipped(item.uri, reason)`.
There are four exact reasons.

- `"broken link"`: the target does not exist, or no longer exists when the link is processed (for example moved earlier in the same move), and links are followed or the destination has no links.
- `"link loop"`: the directory link loops, or the depth guard was hit.
- `"unreadable link"`: `readlink` failed on the source.
- `"folder exists"`: a file or link met a real folder of the same name at the destination; `item` is the source path.

The app shows one toast per finished job and the Jobs dialog lists each path with its reason.
A skipped item counts as completed work.
See `docs/scheduler.md` for the reporting API.

## Cross-device move

A move across devices copies each link with `_copy_link` and removes the source link only if it was copied.
Both link options apply to the copied part.
A skipped link stays in the source.
A skipped file (`"folder exists"`) stays in the source too.
Its parent directory then cannot be removed and stays too.
A directory link is removed from the source once its tree copy has finished, except when the destination folder itself was skipped.
The target of a directory link is only read, never touched.
With `follow_links` on, or when the destination has no links, a link whose target was already moved earlier in the same move is skipped as `"broken link"`.
The target was moved because it was selected, not through the link.
A link-capable destination without `follow_links` recreates such a link instead, so nothing is skipped.
Moves on the same device rename the link itself.

## Link options

`FileCopyOptions` has two link options, both off by default.

- `follow_links` copies what a link points to, a file as content and a directory as a tree, instead of recreating the link.
- `stable_symlinks` rewrites the target text of a recreated relative link whose target lies outside the copied item.
  It has no effect when `follow_links` is on.

A followed file keeps the permission bits of its target, not those of the link, which are always `0o777` on Linux.
When `real_path` returns `None`, the link's own bits are used instead.
A followed directory tree in that case also gets the link's bits.

The stable rule is lexical: no link is resolved while it runs.
It is implemented by `stable_link_target` in `filemanager/link_target.py`.

- A target is inside when it is at or below the copied item.
  When the selected item is itself a link, there is no copied folder, so every relative target counts as outside.
- An absolute or empty target text is kept unchanged.
- A relative target outside the copied item becomes the path from the directory of the new link to the same target path.
- A trailing slash in the text is kept.
- `..` above `/` is clamped to `/`.
- If the source link, the new link or the copied item is not absolute, a relative target keeps its text, and `_stable_text` logs a warning.

Because `..` is applied to the text, `linkdir/../x` is read as `x`, even when `linkdir` is a link to another directory.
A rewritten target can then reach a different file than the original link did.

"Same filesystem" means `==` between the two filesystem objects.

- The rule applies only when `link.filesystem == dst.filesystem`; otherwise the text is copied unchanged.
- All `LocalFilesystem` instances are equal.
- Two `SSHFilesystem` instances are equal when they share one SSH client, so one connection.
- Separately opened connections are never equal, so the text is copied unchanged between them.
- Two `RemoteFilesystem` instances are equal when their inner filesystems are equal, whatever their remote names.

A move on the same device does not apply the stable rule.
Every link is renamed as it is, whether it is selected or inside a folder that is moved entry by entry.
A relative link whose target lies outside the moved item can therefore point elsewhere, or dangle, after the move.
This is the same limitation as a folder rename.

When `real_path` returns `None` for a followed directory link, the tree is walked through a followed view and the loop test is skipped.
Only the depth guard `_MAX_LINK_DEPTH = 16` then limits the descent, so a loop directory is entered up to 16 times before the link is skipped as `"link loop"`.
A pure cycle `l -> l` is reported as `"broken link"`.

A followed directory target that vanished while `real_path` returns `None` is not caught as a broken link.
`_copy_linked_directory` catches only an `OSError` from `real_path`, so the followed view is walked.
The destination folder created by `_ensure_directory` stays, and the error from listing the vanished folder is raised, which fails the job.
This is an accepted limitation.

With both options off and a destination without links, a link whose target vanished between the listing and its processing is now skipped as `"broken link"`.
Before this change such a link failed the job.
This is a changed edge case.

A followed file goes through the same destination step as every file copy.
See "Copy and move onto existing files, links and folders".

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
On a remote without GNU `find` or `stat`, or one that refuses exec requests, the listing uses SFTP and one extra `stat` per link.
A link whose `stat` fails is listed as a broken link, as it is with `stat -L`.

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

## Copy and Move dialog options

The Copy and the Move dialog show the checkboxes "Follow links" and "Stable symlinks".
They set `FileCopyOptions.follow_links` and `FileCopyOptions.stable_symlinks` of the started job.
Both are off when no state was saved.
Confirming the dialog with OK saves both states in the `[copy_move]` section of `settings.toml`; Cancel saves nothing.
Copy and Move share the one saved pair, and the Settings dialog shows it in the tab "Copy Move".
The dialog itself reads and writes no files.
`copy_or_move_files_job` in `filemanager/jobs.py` passes the saved states in and saves the chosen ones.
