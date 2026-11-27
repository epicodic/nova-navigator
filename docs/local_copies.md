# Local Copies of Non-local Files

Opening a file from SSH, Azure, or an archive with an external application works on a local copy.
Every save of that copy is written back to its source automatically.
Executing a local executable file and opening a local file are unchanged.
Opening a *non-local* file with an external command goes through this mechanism, and so does editing a non-local file in the built-in F4 editor.
An executable on a non-local filesystem is opened through a local copy too, rather than being executed remotely.

This page has a user part and a developer part.

## Using Local Copies

### Opening a file

Opening a file from SSH, Azure, or an archive (including an archive reached over SSH) downloads it below a private per-process directory and launches the configured filetype command on that local path.
A file already on the local filesystem is never copied: it is opened directly, and nothing below appears in the Local Copies dialog for it.
Saving the local copy in the external application is detected automatically, and the change is written back to the source without any extra step.

### Editing in the built-in editor

F4 on a file, and the user menu entry "Edit User Menu File", open the built-in editor as a full-screen screen over the file list.
Enter on a file is unchanged: it runs the configured filetype command, through a local copy for a non-local file.
A file on the local filesystem is edited in place; it is never copied and nothing appears in the Local Copies dialog for it.
A file from SSH, Azure, or an archive (including an archive reached over SSH) is edited through a local copy, and a notice names the source.
A binary file opens in the hex view, hex edits and the in-place save patch the copy and are written back like any save; the whole file is downloaded first (no ranged reads).
Every save in the editor is detected like a save of an external application and written back once; the editor does not ask "file changed" after that write-back.
Closing the editor (File > Close, Ctrl+W, or Quit) returns to the file list, stops watching the copy and writes a save that was not synced yet, once.
The entry then shows ` (closed)` in the Local Copies dialog; the local file stays for fast reuse.
Sync now, Reopen (which runs the external command) and Discard work as for any closed copy, and opening the same file in the editor again reuses the copy under the reopen rules below.
If the source changed since the copy was made, the sync asks Overwrite or Skip over the editor, as for external applications.
After Skip the entry is `conflict` and the editor keeps working on the copy.
Closing the editor never overwrites the source: a notice says that the copy was not written back, and Sync now resolves it (see Conflicts).
If the final write-back fails, a notice says so, the copy keeps your changes, the entry stays `failed`, and the quit prompt still protects it.
A copy that is never written back (see Read-only sources) opens view-only with a notice: typing, Cut, Paste and Ctrl+S change nothing, and Save As writes a local file.
A Save As to another path leaves the copy, and later saves there are not written back.

### The Local Copies dialog

Press **Ctrl+E**, or use **Command → Local Copies**, to see every currently open local copy.
The table lists, for each copy:

| Column | Meaning |
|---|---|
| File | The file's name |
| Source | The file's full URI (`ssh://...`, `azure://...`); for an archive member, the archive's URI followed by `#` and the member path |
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

### Reopening a changed file

Opening a file again after its source was changed elsewhere shows the new content.
No panel refresh is needed for that.
When the source is unchanged, the existing local copy is reused without downloading it again.

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

- SSH files and TAR members have no version tag, so a source change is detected by size and modification time alone.
  The check is a fresh stat on every reopen and every sync, never a directory listing.
  A same-size change within the same whole second is not detected for these sources.
  For an archive member the container's fingerprint counts too, so a ZIP or TAR on SSH inherits this same-second limit for the whole archive.
  A local container is compared by device, inode, size and mtime in nanoseconds.
  ZIP members (CRC-32), Azure blobs (ETag) and local files do not share this limit for their own content.
- Nothing prevents another writer from racing the final replacement of the source.
- Writing back to SSH needs the server's `posix-rename@openssh.com` SFTP extension; a server without it fails the write-back and leaves both the source and the local copy untouched.
- Reading a member of a nested archive and copying it out work, also when the outer archive is on SSH.
  Editing a member of a nested ZIP whose outer archive is local and releasing the copy writes the edit through the inner ZIP into the outer ZIP.
  Writing back through a nested archive whose outer archive is remote is not covered by tests.

## Developer Part

### Architecture

| Layer | Location | Responsibility |
|---|---|---|
| Safe replacement | `Filesystem.write_atomic()` (`vfs/filesystem.py` and per-filesystem overrides) | Replace a file without exposing a partial state. |
| Writable archives | `vfs/filesystems/archive.py`, `archive/zip_rebuild.py`, `archive/tar_rebuild.py`, `archive/backing.py` | Replace or add a member by rebuilding the archive and writing it back through a local copy. |
| Local copy | `vfs/local_copy.py` | Map a `VPath` to a local file, record baselines, download, write back. |
| Change detection | `vfs/change_detector.py` | Report settled content changes of one local file. |
| Copy manager | `local_copies/manager.py`, `local_copies/tasks.py` | Registry of open copies and their detectors; runs open/sync jobs through the scheduler; Textual-free. |
| Editor session | `local_copies/editor_session.py` | Decide local file or local copy for the built-in editor, pre-check a local file, end the session with `release()` and report a failed or conflicting write-back; Textual-free. |
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

### `stat_uncached()` (fresh stat)

`Filesystem.stat_uncached(path)` (`vfs/filesystem.py`) returns the current `Stat` of `path` and is never answered from a cache.
The default is `stat()`.
Only `SourceFingerprint.of` and `create_copy_task` call it, so the change check of a reopen and of a sync sees out-of-band changes that a listing cache or a reader snapshot would hide.
For an archive member `SourceFingerprint.of` fingerprints the container first, then the member.

| Filesystem | `stat()` can be stale | `stat_uncached()` |
|---|---|---|
| `LocalFilesystem` | No, every call is an `os.stat` | Default |
| `AzureFilesystem` | No, every call asks the service and there is no local cache | Default |
| `SSHFilesystem` | Yes, a per-directory stat cache is cleared only by own writes and `refresh()` | One SFTP `lstat` (plus one `stat` for a symlink), never reads or fills the listing cache; works in GNU mode and in SFTP-fallback mode |
| `RemoteFilesystem` | Inherits the wrapped filesystem | Explicit delegate to the wrapped filesystem, because the base default would call the wrapper's own `stat()` |
| `ArchiveFilesystem` | Yes, the reader is a snapshot of the archive file | Brings the mount up to date from its container first, then answers from the reader |

`ArchiveFilesystem.stat_uncached()` calls `refresh_from_source()`.
That asks the backing to `prepare_write()`, which refreshes the local copy of a non-local archive when its source changed.
It then reopens the reader only when the local archive file differs from the one the reader was opened on (device, inode, size and modification time), so an unchanged archive costs no reopen.
Reopening clears the cached stat of every member `VPath` handed out by the mount; a lock guards that member set against concurrent `stat()` calls.
For a nested archive the container is itself an archive member, so the refresh recurses: the inner copy checks its source with `stat_uncached()` on the outer mount, which refreshes the outer mount in turn.
A same-size change within the same whole second stays undetected for sources without a version tag (SSH files and TAR members), as listed under Limits.

### `LocalCopy` (`vfs/local_copy.py`)

`LocalCopy.create(source, root, read_only=..., progress=...)` downloads `source` below `root` in bounded-memory chunks and records a `Baseline` (a `SourceFingerprint` — size, modification time, `version_tag()` and, for an archive member, the container's fingerprint — plus the local digest).
A source already on the local filesystem, and not an archive member, is a pass-through: `path` is the source file itself and nothing is copied.
`is_local_source(source)` is the one predicate for it (a plain local file, never an archive member); `LocalCopy.create`, `NovaNavigatorCore.open_path` and the editor session use it.
`relative_copy_path(source)` maps a source `VPath` to its path below the process root, sanitising every segment (`sanitize_segment`) against empty/`.`/`..` segments and shortening overlong ones with a hash suffix.
`is_modified()` compares the local digest with the baseline; `source_changed()` compares a fresh `SourceFingerprint` with the baseline (fresh means taken with `stat_uncached`, container first, see below); `reuse_action()` combines both into a `ReuseAction` (`REUSE`, `REFRESH`, or `CONFLICT`) for the reopen flow.
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
`release(entry)` ends a built-in editor session: it stops the detector first, waits for a running sync, and then syncs a modified copy once unless the entry is read-only or in `CONFLICT`.
It never forces the overwrite of a conflicting copy and never queues a second upload for a save that settles while it runs, which is why the editor session does not use `close()`.
External applications never call it.

### Editor session (`local_copies/editor_session.py`)

`open_for_editing(manager, source)` returns an `EditorTarget` (source, the local path to edit, and the `CopyEntry` or `None`).
A directory raises `IsADirectoryError`; a local file is checked by `check_readable()` and edited in place without a manager call; any other file goes through `LocalCopyManager.open()`.
`finish_editing(manager, target)` calls `release()` and returns a `SessionNotice` for a failed or conflicting write-back.
`NovaNavigator.open_editor` runs `open_for_editing` in a Textual worker.
The watcher of a copy starts its sync jobs from the context of the call that started it, and a job can only show its Overwrite/Skip question from a context that belongs to a worker.

### Viewer session (`local_copies/viewer_session.py`)

`open_for_viewing(manager, source)` returns a `ViewTarget` (the local path to show, the private copy's directory, and the source key) or `None` when the download failed or was cancelled.
A directory raises `IsADirectoryError`; a local file is checked by `check_readable()` and viewed in place.
Any other file is downloaded by `LocalCopyManager.create_view_copy()` into its own unique directory below `<root>/view/`, with `read_only=True`.
A view copy is never registered, watched or synced, so viewing a file never writes it back and never touches an F4 copy of the same source.
`finish_viewing(manager, target)` removes the view copy's directory through `discard_view_copy()`; a failed download removes its directory before returning.

### Writable archives

`ArchiveFilesystem.write(member)` (`vfs/filesystems/archive.py`) spools the new content to a private temporary file, then on `close()` rebuilds the archive with that member substituted or appended and commits it through an `ArchiveBacking` (`archive/backing.py`).
Archive writes keep existing tar symlink members and ZIP link entries unchanged but cannot add links.
`ArchiveFilesystem.mkdir` is an implicit no-op, so empty directories are not stored.
`LocalArchiveBacking` commits straight through `Filesystem.write_atomic()` on the archive's own source.
`CopiedArchiveBacking` wraps a `LocalCopy` of a non-local archive: `prepare_write()` refreshes it if the source changed underneath, and `commit()` replaces the local copy's file and calls its `write_back()`.
`archive/zip_rebuild.py` rebuilds a ZIP by copying every unchanged entry's bytes verbatim and only patching the changed entry and its central-directory offsets (stored, deflate, and bzip2 are supported for the changed entry); it rejects encrypted entries, multi-volume archives, an unsupported archive prefix, and an ambiguous duplicate member name.
#### Why ZIP archives are rebuilt by hand

Python's `zipfile` writes one extra-field block for both the local header and the central directory record of an entry.
Many real archives, including those made by the Info-ZIP `zip` tool, store different extra fields in the two places, for example a fuller extended timestamp in the local header.
Rewriting such an archive with `zipfile` either loses that metadata or has to reject the archive; the previous implementation rejected it and failed on ordinary archives.

`zip_rebuild.py` therefore reads and writes the ZIP format itself and copies every unchanged entry as raw bytes.

Advantages:

- Untouched entries stay byte-identical, including metadata the code does not understand.
- Unchanged entries are never decompressed or recompressed, so large archives rebuild quickly.
- No external tool is needed, which keeps the feature portable.

Costs:

- About 570 lines of format code (end-of-central-directory, Zip64, central and local headers) to maintain.
- Unusual layouts are rejected rather than handled: encrypted or LZMA-compressed edited entries, multi-volume archives, data before the first entry, duplicate member names, and Zip64 offsets for the edited or appended entry.

Alternatives that were rejected:

- **Rewrite with `zipfile`, keeping only the central extra fields.** Much less code, but every entry is recompressed, untouched entries change on disk, and local-only metadata is lost.
- **Call the `zip` command line tool.** It also copies unchanged entries raw, but it adds an external dependency that conflicts with planned Windows and macOS support, takes the edited entry's timestamp and permissions from a temporary file, and leaves name encoding outside our control.

`tests/archive/test_zip_rebuild.py` covers differing local and central extras, data descriptors, Zip64, each supported compression method, and the rejected layouts.

`archive/tar_rebuild.py` streams a TAR (plain, gzip, bzip2, or xz) through `tarfile`, preserving every member's metadata and substituting only the target member's content; it rejects a sparse member and a rebuild that would need to change the archive's global PAX headers.
JAR, WAR, EAR, APK, and WHL report `FilesystemCapabilities.read_only` and raise `PermissionError` on write, as does any archive whose source filesystem is itself read-only (see `archive/archives.py`, `is_archive_writable`).
The archive virtual shell's `cp` command (`terminal/vfs_shell/commands/cp.py`) writes through the same generic `Filesystem.write()`, so it can copy a file into a writable archive too.

#### Compressed single files

A file ending in `.gz`, `.bz2` or `.xz` that is not one of the tar names opens as a read-only archive with one entry (`archive/compressed_archive.py`, `CompressedFileArchive`).
The entry is the decompressed content, named like the file without the suffix, or `data` if nothing remains.
The suffix match is case-sensitive, and the compressed file's own header name is ignored.
Such a file is never writable, so writing into it raises `PermissionError`, and remove and rename are unsupported.
Mounting streams the whole file once, in 1 MiB chunks, to check it and to measure the uncompressed size, so opening a very large file takes as long as decompressing it, but memory stays small.
Each read afterwards decompresses incrementally from its own file handle.
The three suffixes are registered after the tar and zip names in `_SUPPORTED_ARCHIVES`, so the first matching suffix wins.
This keeps `.tar.gz`, `.tgz`, `.tar.bz2`, `.tbz2`, `.tar.xz` and `.txz` opening as tar archives.
A `.gz` file that contains a tar stream is shown as one entry, because only the name decides.
A corrupt, truncated or empty file raises `OSError(EINVAL)` ("Corrupt or empty ... file") from the mount; system errors such as a missing file propagate unchanged.
As for tar and zip, the size is measured at mount, so a local file rewritten afterwards is not noticed until the archive is reloaded.
For a remote file, `LocalCopyManager.mount_archive` discards the downloaded copy before re-raising, so a failed mount leaves nothing behind.

### Probe tool

`src/tools/local_copy_probe.py` exercises a `ChangeDetector` against a real editor outside the full application, to check how that editor's save strategy is seen:

```sh
uv run local_copy_probe -- vim %f 2> probe.log
```

It copies a sample file (or `--file PATH`) into a scratch directory, launches the given command with `%f` replaced by that file, and logs every raw watch/poll signal and every settled, digest-confirmed change to stderr.
After the launched process exits it keeps watching (for detached editors) until interrupted with Ctrl+C.
