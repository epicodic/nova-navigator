# Local Copies of Non-local Files

Opening a file from SSH, Azure, or an archive with an external application works on a local copy.
Every save of that copy is written back to its source automatically.
The built-in F4 editor and executing a local executable file are unchanged; only opening a *non-local* file with an external command goes through this mechanism.
An executable on a non-local filesystem is opened through a local copy too, rather than being executed remotely.

This page has a user part and a developer part.

## Using Local Copies

### Opening a file

Opening a file from SSH, Azure, or an archive (including an archive reached over SSH) downloads it below a private per-process directory and launches the configured filetype command on that local path.
A file already on the local filesystem is never copied: it is opened directly, and nothing below appears in the Local Copies dialog for it.
Saving the local copy in the external application is detected automatically, and the change is written back to the source without any extra step.

### The Local Copies dialog

Press **Ctrl+E**, or use **Command → Local Copies**, to see every currently open local copy.
The table lists, for each copy:

| Column | Meaning |
|---|---|
| File | The file's name |
| Source | The file's full URI (`ssh://...`, `azure://...`, `archive://...`) |
| Status | `synced`, `modified`, `syncing`, `failed`, `conflict`, or `read-only`; ` (closed)` is appended once the copy has been closed (see below) |
| Error | The last sync failure, if any |

Four actions act on the selected row:

- **Sync now** — write the local copy back to its source right away; also the way to resolve a `conflict` status (see below).
- **Reopen** — launch the configured command on the copy again, applying the reopen rules below first.
- **Close copy** — sync any unsynced change, then stop watching the file; the local file itself is kept for fast reuse later in this run, and its status gains the ` (closed)` suffix.
- **Discard** — delete the local file; asks for confirmation first if it holds unsynced changes.

Sync failures and conflicts also show up as jobs in the Jobs dialog while they run.

### Conflicts

Two different situations can produce a conflict, and each is resolved differently.

**Sync conflict** — a background sync notices the source changed since the local copy was made (or downloaded) and asks:

- **Overwrite** — replace the source with the local copy.
- **Skip** (also Escape) — keep the local copy only; the copy's status becomes `conflict` and automatic syncing pauses for it. Choosing **Sync now** on a `conflict` copy overwrites the source without asking again.

**Reopen conflict** — reopening (from the dialog, or by opening the same file again) a copy whose local file *and* whose source have both changed asks:

- **Overwrite** — write the local copy to the source, then open it.
- **Discard** — drop the local edits and open the current source instead.
- **Cancel** (also Escape) — leave the copy as it was and do nothing.

### Read-only sources

JAR, WAR, EAR, APK, and WHL archives are always opened read-only, because rebuilding them can invalidate package signing or integrity data.
Any other archive whose own source filesystem is read-only is read-only for the same reason.
A read-only copy shows status `read-only`, is created with restricted permissions, and is never written back; **Sync now** is disabled for it.

### Quitting with unsynced changes

Quitting Nova Navigator while any local copy has unsynced, failed, or conflicting changes asks:

- **Sync and quit** — sync every such copy, then quit. If any copy still cannot be synced, the dialog reports it and Nova Navigator stays open.
- **Quit and keep files** — quit without syncing; the local files are left in place.
- **Cancel** — do nothing.

With nothing unsynced, Nova Navigator quits immediately and removes its local-copy directory.

### Where the files live

Local copies live under `<tmp>/nova-navigator/<pid>/`, where `<tmp>` is `tempfile.gettempdir()` (normally `/tmp`) and `<pid>` is the running Nova Navigator process's id.
Below that, the path mirrors the source's URI, for example:

```
/tmp/nova-navigator/4211/ssh/user@host:22/etc/hosts
/tmp/nova-navigator/4211/azure/<account>/<container>/dir/blob.txt
/tmp/nova-navigator/4211/archive/local/path/to/archive.zip/member/path.txt
/tmp/nova-navigator/4211/archive/ssh/user@host:22/data/archive.tar.gz/member/path.txt
```

On startup, Nova Navigator looks for directories left behind by earlier processes that are no longer running.
An empty one is removed silently; one that still holds files produces a notification naming it, since the earlier session's changes may not have been synced.

### Limits

- Detecting a source change is best effort: it compares size, modification time, and (where the filesystem provides it) an ETag or CRC, so a same-size edit within the timestamp's resolution can be missed.
- Nothing prevents another writer from racing the final replacement of the source.
- Writing back to SSH needs the server's `posix-rename@openssh.com` SFTP extension; a server without it fails the write-back and leaves both the source and the local copy untouched.
- Archives nested inside another archive are not supported.

## Developer Part

### Architecture

| Layer | Location | Responsibility |
|---|---|---|
| Safe replacement | `Filesystem.write_atomic()` (`vfs/filesystem.py` and per-filesystem overrides) | Replace a file without exposing a partial state. |
| Writable archives | `vfs/filesystems/archive.py`, `archive/zip_rebuild.py`, `archive/tar_rebuild.py`, `archive/backing.py` | Replace or add a member by rebuilding the archive and writing it back through a local copy. |
| Local copy | `vfs/local_copy.py` | Map a `VPath` to a local file, record baselines, download, write back. |
| Change detection | `vfs/change_detector.py` | Report settled content changes of one local file. |
| Copy manager | `local_copies/manager.py`, `local_copies/tasks.py` | Registry of open copies and their detectors; runs open/sync jobs through the scheduler; Textual-free. |
| UI | `dialogs/local_copies_dialog.py`, `nova_navigator.py` | Local Copies dialog, quit prompt, startup orphan notice. |

### `write_atomic()` and `version_tag()`

`Filesystem.write_atomic(path)` (`vfs/filesystem.py`) returns an `AtomicWriterLike` — `write()` any number of times, then exactly one of `close()` (publish) or `abort()` (discard, leaving the original untouched).
The default implementation spools the written bytes to a local temporary file and uploads them through `write()` on `close()`; this is only as atomic as the backend's own `write()`.
`LocalFilesystem.write_atomic()` writes a sibling temporary file and uses `os.replace()`.
`SSHFilesystem.write_atomic()` (`_SftpAtomicWriter`) uploads to a sibling temporary name and replaces the target with the `posix-rename@openssh.com` SFTP extension; a server without it raises `OSError` on `close()` and removes the temporary file.
`AzureFilesystem` keeps the default (a blob upload is already atomic).
`RemoteFilesystem` delegates both `write_atomic()` and `version_tag()` to its wrapped filesystem.

`Filesystem.version_tag(path)` returns an opaque token that changes whenever `path` is rewritten, or `None` if the backend cannot provide one.
`LocalFilesystem` returns `dev:ino:mtime_ns:size`.
`AzureFilesystem` returns the blob's ETag.
`ArchiveFilesystem` returns the member's ZIP CRC-32 (`ZipArchive.version_tag`) or `None` for TAR.
`SSHFilesystem` does not override it, so SSH sources have no tag and rely on size and modification time alone.

### `LocalCopy` (`vfs/local_copy.py`)

`LocalCopy.create(source, root, read_only=..., progress=...)` downloads `source` below `root` in bounded-memory chunks and records a `Baseline` (a `SourceFingerprint` — size, modification time, and `version_tag()` — plus the local digest).
A source already on the local filesystem, and not an archive member, is a pass-through: `path` is the source file itself and nothing is copied.
`relative_copy_path(source)` maps a source `VPath` to its path below the process root, sanitising every segment (`sanitize_segment`) against empty/`.`/`..` segments and shortening overlong ones with a hash suffix.
`is_modified()` compares the local digest with the baseline; `source_changed()` compares a fresh `SourceFingerprint` with the baseline; `reuse_action()` combines both into a `ReuseAction` (`REUSE`, `REFRESH`, or `CONFLICT`) for the reopen flow.
`write_back()` uploads through `write_atomic()` and advances the baseline; `refresh()` re-downloads and resets it; `discard()` deletes the local file, never the source.
`rebase_container()` lets a sibling copy of the same archive absorb the archive's new fingerprint after another member's sync, so editing two members of one archive does not conflict with each other.

### `ChangeDetector` (`vfs/change_detector.py`)

`ChangeDetector(path, on_change, baseline_digest=..., poll_interval=1.0, settle_time=1.0)` watches one local file for settled content changes.
It watches the *containing directory* with `watchdog` (not the file itself, since a rename-over save replaces the inode and would silence a per-file watch) and separately polls a `FileFingerprint` (inode, size, `mtime_ns`) once per second, so it also catches changes the watcher misses.
Either source only marks the file dirty; the settle loop waits for the fingerprint to hold steady for `settle_time`, then computes the SHA-256 digest and calls `on_change(digest)` only if it differs from the baseline.
`check_now()` marks the file dirty immediately, used after an external command's process exits (covers editors that write once and quit, such as terminal editors).

### `LocalCopyManager` (`local_copies/manager.py`)

Owns every open `CopyEntry` (a `LocalCopy`, its `CopyStatus`, and its `ChangeDetector`), keyed by the source's URI, or `<archive-uri>#<member-path>` for an archive member.
Runs entirely on the GUI event loop; the registry is only ever mutated there, while the blocking I/O in `local_copies/tasks.py` (`create_copy_task`, `reopen_copy_task`, `sync_copy_task`) runs in worker threads as scheduler `Job`s.
`open()` creates a copy (or, if one is already open, defers to `_reopen()`, which applies `LocalCopy.reuse_action()` and prompts on `CONFLICT`).
A settled `ChangeDetector` change starts a `sync_copy_task`; a change arriving while a sync is already running only queues one follow-up (`_schedule_sync`/`_run_sync`).
A sync conflict sets `CopyStatus.CONFLICT` and pauses automatic syncing for that entry; `sync_now()` retries with `force=True` when the entry is already in `CONFLICT`, skipping the prompt.
`mount_archive()` mounts a local archive directly (matching `LocalCopy.create()`'s own pass-through) or, for a non-local archive, downloads it as a `LocalCopy` and wraps it in an `ArchiveFilesystem`, caching the instance by source URI so sibling-member rebasing can match archive filesystems by identity.
`unsynced()`, `close()`, `discard()`, and `shutdown()` back the dialog actions and the quit prompt; `shutdown()` is idempotent, so an explicit quit-time shutdown is not undone by the app's unconditional cleanup on unmount.

### Writable archives

`ArchiveFilesystem.write(member)` (`vfs/filesystems/archive.py`) spools the new content to a private temporary file, then on `close()` rebuilds the archive with that member substituted or appended and commits it through an `ArchiveBacking` (`archive/backing.py`).
`LocalArchiveBacking` commits straight through `Filesystem.write_atomic()` on the archive's own source.
`CopiedArchiveBacking` wraps a `LocalCopy` of a non-local archive: `prepare_write()` refreshes it if the source changed underneath, and `commit()` replaces the local copy's file and calls its `write_back()`.
`archive/zip_rebuild.py` rebuilds a ZIP by copying every unchanged entry's bytes verbatim and only patching the changed entry and its central-directory offsets (stored, deflate, and bzip2 are supported for the changed entry); it rejects encrypted entries, multi-volume archives, an unsupported archive prefix, and an ambiguous duplicate member name.
`archive/tar_rebuild.py` streams a TAR (plain, gzip, bzip2, or xz) through `tarfile`, preserving every member's metadata and substituting only the target member's content; it rejects a sparse member and a rebuild that would need to change the archive's global PAX headers.
JAR, WAR, EAR, APK, and WHL report `FilesystemCapabilities.read_only` and raise `PermissionError` on write, as does any archive whose source filesystem is itself read-only (see `archive/archives.py`, `is_archive_writable`).
The archive virtual shell's `cp` command (`terminal/vfs_shell/commands/cp.py`) writes through the same generic `Filesystem.write()`, so it can copy a file into a writable archive too.

### Probe tool

`src/tools/local_copy_probe.py` exercises a `ChangeDetector` against a real editor outside the full application, to check how that editor's save strategy is seen:

```sh
uv run local_copy_probe -- vim %f 2> probe.log
```

It copies a sample file (or `--file PATH`) into a scratch directory, launches the given command with `%f` replaced by that file, and logs every raw watch/poll signal and every settled, digest-confirmed change to stderr.
After the launched process exits it keeps watching (for detached editors) until interrupted with Ctrl+C.
