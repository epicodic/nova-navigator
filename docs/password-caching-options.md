# SSH Password Caching Options

Analysis of in-memory and persistent password caching for SSH connections.

---

## In-Memory Password Cache

### Option A — Plain dict in process memory

A `dict[tuple[str, int, str], str]` keyed by `(host, port, user)`, scoped to the app process lifetime.
Evict on auth failure.

- Pros: zero dependencies, simple.
- Cons: passwords in plaintext in heap; visible to same-UID processes via `/proc/<pid>/mem` and in core dumps.

### Option B — SSH agent (key auth only)

Paramiko already supports `allow_agent=True`, which delegates passphrase caching to the running `ssh-agent`.
Passphrases are never in the app's memory.

- Pros: most secure approach; battle-hardened; no implementation needed.
- Cons: only covers **key passphrases**, not password-based auth; requires `ssh-agent` running.

### Option C — `mlock`-protected buffer

Store passwords in memory locked with `mlock()` so they cannot be swapped to disk.
Zero on eviction.
Requires a C extension (e.g. `secmem`, or `cryptography`'s `SecretBuffer`).

- Pros: prevents swap leaks, zero-on-free.
- Cons: significant complexity; Python's GC and string interning still make zeroing unreliable; overkill for a file manager.

### Recommendation

Use Option A for password auth (risk profile matches desktop tools like Filezilla).
Option B (key auth via `ssh-agent`) already works via paramiko — just enable `allow_agent=True`.

---

## Persistent Password Storage

### Option 1 — `keyring` / libsecret (GNOME Keyring / KWallet)

The `keyring` Python library wraps the D-Bus Secret Service API.
On Ubuntu desktop it uses GNOME Keyring, which is AES-256-GCM-encrypted and unlocked by the login session.

- Pros: OS-managed, encrypted at rest, access-controlled, auditable, standard approach used by git, VSCode, etc.
- Cons: requires GNOME Keyring daemon (always present on Ubuntu desktop; unavailable in headless/TTY-only sessions); adds `keyring` / `secretstorage` dependency.

### Option 2 — `secretstorage` directly

Same backend as Option 1 but using the `secretstorage` library which directly calls the D-Bus Secret Service.
More control (explicit collection management).

- Pros: same security as Option 1, more explicit, fine-grained control.
- Cons: Linux-only (acceptable here), slightly lower-level API.

### Option 3 — Encrypted file with master password

Derive a key from a user-chosen master password (Argon2 KDF → AES-256-GCM).
Store encrypted credentials in `~/.config/nova-navigator/credentials.enc`.

- Pros: works headless, no daemon needed, self-contained.
- Cons: requires the user to type a master password on first use per session; you're doing your own key management — OWASP A02 risk if done incorrectly; master password must be stored nowhere (defeats persistence) or itself cached in memory.

### Option 4 — `pass` (Unix password store)

Delegate to `pass(1)`, which uses GPG-encrypted files.
Call via subprocess.

- Pros: users who already use `pass` get seamless integration.
- Cons: niche; requires gpg and `pass` set up; subprocess overhead; not user-friendly for non-`pass` users.

### Option 5 — Plaintext in `remotes.toml`

Add a `password` field to `SshSettings`.

**Do not do this.**
Plaintext secrets in config files is OWASP A02 (Cryptographic Failures).
Config files are often accidentally committed to git, shared, or have broad file permissions.

### Recommendation

Use Option 1 (`keyring` + `secretstorage`) with a graceful fallback: if the keyring service is unavailable (no D-Bus session, headless server), fall back to in-memory-only and inform the user.
This is exactly how `git-credential-libsecret` and VSCode's secret storage work.

---

## Summary

| | In-memory | Persistent |
|---|---|---|
| **Best option** | Plain dict, evict on failure | `keyring` / libsecret |
| **Key auth** | Already works via `ssh-agent` | n/a |
| **Security risk** | Same-UID memory reads, core dumps | None if using OS keyring |
| **Fallback** | n/a | In-memory only when keyring unavailable |
| **Dependencies** | None | `keyring`, `secretstorage` |

---

## Design Notes

`SshSettings` currently has no `password` field.
The persistent store should be looked up at connection time using `(host, port, user)` as the key, bypassing the config file entirely.
Credentials stay out of `remotes.toml`.
