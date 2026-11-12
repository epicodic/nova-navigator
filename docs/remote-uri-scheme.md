# `remote://` URI Scheme

Nova Navigator supports a `remote://` URI scheme for referencing preconfigured remote connections by name.
This is distinct from the raw `ssh://user@host/path` form — it resolves against the saved entries in `remotes.toml`.

---

## Syntax

```
remote://<connection-name>[/path]
```

| Part | Description |
|------|-------------|
| `connection-name` | The `name` field of a `RemoteConnection` in `remotes.toml`. Case-sensitive. |
| `/path` | Optional path on the remote host. Defaults to the connection's home directory if omitted. |

### Examples

```
remote://prod-server
remote://prod-server/etc/nginx/nginx.conf
remote://dev-box/home/user/projects/myapp
```

Nesting works identically to other schemes:

```
remote://prod-server/var/backups/db.tar.gz/tar://dump.sql
```

---

## Resolution

When `parse_uri` encounters `remote://name/path`, the resolver:

1. Looks up `name` in `RemoteConfig` (the saved connections).
2. Retrieves the full `RemoteConnection` — protocol type, credentials, proxy config, etc.
3. Constructs the appropriate `Filesystem` subclass from those settings:
   - `ssh` → `SSHFilesystem` (with identity file, proxy, etc. already applied)
   - `azure` → `AzureFilesystem` (future)
   - additional protocols as they are added
4. Returns a `VPath` rooted at `/path` on that filesystem.

If `name` is not found, a `ValueError` is raised with a clear message.

---

## SSH host keys

`SSHFilesystem` verifies the server host key against OpenSSH `known_hosts` files before it authenticates.
The reader, the appender and the `HashKnownHosts` lookup live in `src/nova_navigator/remotes/known_hosts.py`.
That module imports neither Textual nor `vfs`, so `vfs/filesystems/ssh.py` can use it without a cycle.

### Files read

Each connect reads `~/.ssh/known_hosts` and `/etc/ssh/ssh_known_hosts` line by line.
The user file comes first, so its key wins for a given key type.
A missing file is skipped silently, any other read error is logged and the file is skipped.
The paths are bundled in `KnownHostsLocations`, which tests inject so they never touch the real files.
Paramiko's `load_system_host_keys`, `load_host_keys`, `save_host_keys` and `AutoAddPolicy` are not used anywhere; a test scans `src/` for them.

### Honoured and ignored lines

Plain, `host,ip`, `[host]:port` and hashed (`|1|salt|hmac`) entries are honoured.
`@revoked` lines are honoured by key: a revoked key is refused for every host.
`@cert-authority` lines, unknown markers, unsupported key types, malformed lines and wildcard or negated patterns are ignored.
Ignored lines are logged with file and line number, never with key material, and are never rewritten.
A host that is covered only by ignored lines counts as unknown and the user is asked.

### Flow and messages

All prompts are `MessageBox` dialogs built by pure functions in `remotes/ssh.py`.

| Situation | Dialog | Result |
|---|---|---|
| Key matches `known_hosts` | none | connects |
| Host unknown | "Unknown Host": host, key type, SHA256 fingerprint, OK / Cancel | OK reconnects with the confirmed key, Cancel returns `None` |
| Key differs from the stored key | "Host Key Changed": presented and stored key, the word "differs", an `ssh-keygen -R` hint, OK only | refused, nothing changed |
| Key is listed as `@revoked` | "Revoked Host Key", OK only | refused, no reconnect |
| Server presents another key than the confirmed one | "Host Key Mismatch": both fingerprints, OK only | refused, nothing stored |

There is no way to accept a changed, revoked or mismatched key from the dialogs.
The user fixes a legitimate change outside the app, for example with `ssh-keygen -R`.

### Trust and storage

After the user confirms, the reconnect trusts exactly the confirmed key and uses `RejectPolicy` for everything else.
The key is appended once the host key was verified.
That is after `connect()` returns, and also when it raises `AuthenticationException`, because paramiko checks the host key before it authenticates.
The credentials dialog then reconnects with the same confirmed key, so the host key is not asked about again.
If the append fails, the connection still succeeds and a "Host Key Not Saved" warning names the file and the error.

Never two conflicting keys for one host.
If `known_hosts` already holds a different key of the same type for the host, for example because another window stored it between prompt and reconnect, nothing is written.
The conflict surfaces as a changed-key error, not as an offer to accept.
A key that is already stored is not appended again.

### Append rules

- Exactly one line is appended; existing bytes are never rewritten.
- If the last existing line has no trailing newline, one separator newline is written first.
- The name is `host` for port 22 and `[host]:port` otherwise.
- The name is hashed (`|1|...`) if and only if the effective `HashKnownHosts` is `yes`.
- A missing `~/.ssh` is created with mode `0700` and a missing file with `0600`; existing modes are not changed.
- The write is one `O_APPEND` write under `flock`.

### Limits

- `Include` directives in `ssh_config` are not followed, so a `HashKnownHosts` set only there is missed and a plain line is written.
- `Match exec` lines are neutralised before the config is parsed, because paramiko would run the command.
- Wildcard, case-folded and `CheckHostIP` matching are not supported; such hosts are asked about again.
- Paramiko keeps one key per type and host, so a second stored key of the same type is reported as changed.
- `flock` only serialises Nova Navigator windows; OpenSSH takes no lock.

### Terminal sessions

The SSH terminal receives the already connected client from `connect_ssh`, so it performs no host-key handling of its own.

---

## Comparison with raw scheme URIs

| | `ssh://user@host/path` | `remote://name/path` |
|---|---|---|
| Credentials | Inline in URI | Loaded from saved config |
| Proxy | Not expressible | Applied from `RemoteConnection.proxy` |
| Identity file | Not expressible | Applied from `SshSettings.identity_file` |
| Protocol | Always SSH | Determined by the saved connection type (SSH, Azure, …) |
| Portability | Embeds secrets in strings/bookmarks | Name-only reference; secrets stay in config |
| Discoverability | Requires knowing host/user | Tab-complete from saved connections |

---

## Implementation notes

- `parse_uri.py` treats `remote` as a scheme like any other; the netloc is the connection name and the path is the remote path.
  No changes to the parser are required — `URIComponent(scheme="remote", netloc="prod-server", path="/etc/hosts")` is produced naturally.
- A new `RemoteFilesystem` (or a factory in `vfs/filesystems/`) resolves the netloc against `RemoteConfig` and instantiates the correct `Filesystem` subclass based on `RemoteConnection`'s protocol type.
  This is the single place that maps saved connection types to filesystem implementations.
- `RemoteFilesystem` forwards `supports_symlinks`, `symlink` and `real_path` explicitly.
  The `__getattr__` fallback does not re-bind `VPath` arguments.
- The netloc for `remote://` is always a plain name (no `@`, no `:`).
  Validation should reject names containing those characters early.
- Bookmark URIs using `remote://` remain stable even if the underlying host, credentials, or protocol change — only `remotes.toml` needs updating.
