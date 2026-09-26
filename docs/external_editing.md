# External Editing of Non-local Files

When you open a file from SSH, Azure Blob Storage, or an archive with an external application, Nova Navigator creates a private local mirror first.
The external application receives that local path, so it can edit files without needing access to the remote connection.

Open **Editing Sessions** from the application actions to see mirrors that are still active.
Choose **Finish editing** when you have saved and closed a detached application such as the default `xdg-open` launcher.
Nova Navigator compares the mirror with its original contents and writes a changed editable mirror back to its source.
Choose **Discard** to remove a mirror without changing the source.

Commands configured with `wait_for_exit = true` finish automatically after the command exits successfully.
The default `xdg-open` command is detached, so its editing session remains open until you choose **Finish editing**.

```toml
[[filetypes]]
section_name = "text"
mimetype = "text/.*"
open = "nano %f"
wait_for_exit = true
```

Nova Navigator detects a source change made after the mirror was prepared and keeps your local mirror instead of silently overwriting it.
You can retry later, choose **Overwrite source** after reviewing the conflict, or keep the local copy and discard the session.
Failed write-back also retains the mirror for another attempt.

Mirrors and recovery records live below `/tmp/nova-navigator` with private permissions.
Unfinished sessions are available after a Nova Navigator restart while those files still exist.
Many Linux systems clear `/tmp` on reboot, so recovery after reboot is best effort.

Opening an archive member mirrors only that member for the external application.
For remote archives, Nova Navigator downloads the archive into a temporary stage and rebuilds a new ZIP or TAR archive when the member is written back.
ZIP, TAR, gzip-compressed TAR, bzip2-compressed TAR, and xz-compressed TAR are supported for archive write-back.

JAR, WAR, EAR, APK, and WHL archives are read-only because rebuilding them can invalidate package integrity or signatures.
Nested archives, the F4 built-in editor, and executable-file opening do not use this workflow.

SSH cannot make the final source comparison and replacement one atomic operation, so a concurrent writer can still race the final replacement.
Some archives are rejected for write-back when their metadata cannot be preserved safely, including encrypted or multi-volume ZIP files, unsupported ZIP extras, sparse TAR entries, and TAR archives with changing global PAX metadata.
An unsupported archive feature or an SSH server without safe POSIX replacement leaves the local mirror available for recovery.
