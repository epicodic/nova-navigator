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
   - `ssh` → `SSHFilesystem` (host, user, port and identity file from the saved values and `ssh_config`, jump hosts from `ProxyJump`)
   - `azure` → `AzureFilesystem` (future)
   - additional protocols as they are added
4. Returns a `VPath` rooted at `/path` on that filesystem.

If `name` is not found, a `ValueError` is raised with a clear message.

---

## SSH host keys

`SSHFilesystem` verifies the server host key against OpenSSH `known_hosts` files before it authenticates.
The reader and the appender live in `src/nova_navigator/remotes/known_hosts.py`.
The `HashKnownHosts` value comes from the shared `ssh_config` loader in `src/nova_navigator/remotes/ssh_config.py`.
Neither module imports Textual or `vfs`, so `vfs/filesystems/ssh.py` can use them without a cycle.

### Files read

Each connect reads `~/.ssh/known_hosts` and `/etc/ssh/ssh_known_hosts` line by line.
The user file comes first, so its key wins for a given key type.
A missing file is skipped silently, any other read error is logged and the file is skipped.
The paths are bundled in `KnownHostsLocations`, which tests inject so they never touch the real files.
Paramiko's `load_system_host_keys`, `load_host_keys`, `save_host_keys` and `AutoAddPolicy` are not used anywhere; a test scans `src/` for them.

### Honoured and ignored lines

Plain, `host,ip`, `[host]:port` and hashed (`|1|salt|hmac`) entries are honoured.
`@revoked` lines are honoured by key: a revoked key is refused for every host.
Lines that cannot be parsed are ignored: malformed lines, unsupported key types, `@cert-authority` lines and unknown markers.
Ignored lines are logged with file and line number, never with key material, and are never rewritten.
Lines with wildcard or negated host patterns are not detected and not logged.
They parse as ordinary entries whose host token never equals another host name.
A host that is covered only by ignored or pattern lines counts as unknown and the user is asked.

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
- Every `Match` line other than `Match host/originalhost/user/localuser/all/canonical/final` forms (including `Match exec`, however quoted) is treated as not matching and never executed: it is replaced before the config is parsed, because paramiko would run an `exec` command.
- Wildcard, case-folded and `CheckHostIP` matching are not supported; such hosts are asked about again.
- Host-key types are not restricted to the stored types.
  If the server negotiates a different key type than the one stored, the host counts as unknown and the user is asked.
  A mismatch is detected only when a key of the same type is presented.
- Paramiko keeps one key per type and host, so a second stored key of the same type is reported as changed.
- `flock` only serialises Nova Navigator windows; OpenSSH takes no lock.

### Terminal sessions

The SSH terminal receives the already connected client from `connect_ssh`, so it performs no host-key handling of its own.

---

## SSH config and jump hosts

Before it connects, `connect_ssh` resolves the requested host through the user's OpenSSH configuration.
The loader is `src/nova_navigator/remotes/ssh_config.py` and the resolver is `src/nova_navigator/remotes/ssh_route.py`.
Both import neither Textual nor `vfs`.

### Files and precedence

`~/.ssh/config` is read first, then `/etc/ssh/ssh_config`, once per connection.
For every keyword the first value found wins, as in OpenSSH.
`IdentityFile` is the exception: the lists of both files are joined in order without duplicates.
A missing file is skipped silently.
A file that cannot be read or parsed is logged by file name and exception type and skipped, and the connection proceeds with the other file or without config.

### Supported keywords

`Host` blocks and `Match` lines made only of `host`, `originalhost`, `user`, `localuser`, `all`, `canonical` and `final` are honoured.
`HostName`, `User`, `Port`, `IdentityFile` and `ProxyJump` are applied.
`HashKnownHosts` is read from the same loaded config for the host-key storage.

### Which value wins

Values given explicitly always win for the target: the user, port and identity file of a saved remote, and the user and port of an `ssh://user@host:port` URI.
Explicit values never apply to jump hosts.
Without an explicit value the config value is used, then the default (port 22, the local user name).
The name the user typed (the alias) stays the name of the filesystem, so panel titles, bookmarks and copy sources show `ssh://alias/...`.
Only the connection uses the resolved `HostName`.

### Jump hosts

`ProxyJump` takes a comma-separated list, `[user@]host[:port]` or `ssh://user@host:port` per entry.
`ProxyJump a,b` connects to `a`, then to `b` through `a`, then to the target through `b`.
Each jump host is resolved with the same rules, and a user or port written inside the `ProxyJump` value overrides its config entry.
A jump host that has its own `ProxyJump` is reached through its own jumps first.
`ProxyJump none` connects directly.
A loop or a route of more than eight hops is refused with an error naming the host.
The first hop is a TCP connection; every further hop is a `direct-tcpip` channel of the previous hop.
The jump host's server must allow TCP forwarding.
Listing, reading, writing and the remote terminal use the last hop's connection, so they work through the jumps.

### Host keys and passwords per hop

Every hop passes the host-key checks described above, including the confirmation dialog for unknown keys and the refusal of changed or revoked keys.
A key confirmed for a jump host is stored once, under the name the hop connected to.
A password question names the hop it belongs to and is asked once per hop.
After each question the chain is connected again from the first hop; earlier hops reconnect without new questions.

### Connect timeout

Every hop must be established within `network.ssh_timeout` seconds (default 30), counted from the start of that hop's attempt.
The setting is read for every connection, so a changed value applies to the next connection without a restart.
Values below one second are treated as one second.
A timer closes the socket or channel of a hop that exceeds its time, so a host that accepts TCP but never answers fails on time.
Opening the SFTP session of the target is part of the last hop's time.
Time spent in dialogs never counts, because questions are asked between attempts.
Name resolution cannot be interrupted and is bounded only by the operating system.
One attempt takes at most the number of hops times the timeout.

### Errors

An error for a jump host reads `Jump host 'alias' (host:port), hop 1 of 2: <cause>`.
An error for a target behind jumps reads `Target host 'alias' (host:port) via <last jump>: <cause>`.
A timeout reads `Timed out after 30 s connecting to jump host 'alias' (host:port).`
A failed login reads `Authentication failed for jump host 'alias' (host:port).`
The role word is `target host` for the last hop of a route with jumps and plain `host` for a direct connection.
When the alias equals the host name, only `'host:port'` is shown.
A direct connection keeps the plain paramiko message, except for the timeout and the failed login.
Messages never contain user names, passwords or key paths.

### Closing

`SSHFilesystem.close()` closes the SFTP session and the target connection, then the jump connections nearest-first.
A failed attempt closes everything it opened in the same order.
Nothing calls `close()` for a connected filesystem yet; the connections end with the process.

### Limits

- `ProxyCommand` is never executed and never becomes a hop.
  If it is set without `ProxyJump`, the app connects directly and writes one info line to the log.
  With both set, `ProxyJump` is used.
- `Include` is not followed.
- `Match exec` and every other `Match` keyword outside the list above are never executed (see the host-key limits).
- Tokens other than the ones paramiko expands (`%h`, `%p`, `%r` and the like) are not supported.
- A jump host whose SSH server refuses `direct-tcpip` cannot be used.

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
