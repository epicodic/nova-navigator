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
- The netloc for `remote://` is always a plain name (no `@`, no `:`).
  Validation should reject names containing those characters early.
- Bookmark URIs using `remote://` remain stable even if the underlying host, credentials, or protocol change — only `remotes.toml` needs updating.
