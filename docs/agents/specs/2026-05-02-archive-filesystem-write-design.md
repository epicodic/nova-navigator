# Archive Filesystem Write Operations Design

## Goal

Implement `write()`, `mkdir()`, `remove()`, `rmdir()`, and `rename()` on `ArchiveFilesystem`.
ZIP archives gain full read/write support.
TAR archives remain read-only: all five write methods raise `io.UnsupportedOperation`.

## Constraints

- `write()` only creates new files; overwriting an existing path raises `FileExistsError`.
- `rename()` only supports files, not directories.
- `zip -d` CLI is used for entry deletion (Linux/macOS). Windows port is a future concern.

---

## Layer 1: `Archive` ABC (`src/nova_navigator/archive/archive.py`)

Drop the `mode` parameter from `Archive.__init__` and the `_mode` field.
Each backend hardcodes its own open mode.

Add five abstract methods with the following signatures and docstrings:

```python
@abstractmethod
def write(self, path: PurePath) -> StreamWriterLike:
    """Return a stream writer that creates a new file at *path*.

    Raises:
        FileExistsError: if *path* already exists.
        IsADirectoryError: if *path* is an existing directory.
        io.UnsupportedOperation: if the archive format does not support writes.
    """

@abstractmethod
def mkdir(self, path: PurePath) -> None:
    """Create a directory entry at *path*.

    Raises:
        FileExistsError: if *path* already exists.
        io.UnsupportedOperation: if the archive format does not support writes.
    """

@abstractmethod
def remove(self, path: PurePath) -> None:
    """Remove the file at *path*.

    Raises:
        FileNotFoundError: if *path* does not exist.
        IsADirectoryError: if *path* is a directory.
        io.UnsupportedOperation: if the archive format does not support writes.
    """

@abstractmethod
def rmdir(self, path: PurePath) -> None:
    """Remove the empty directory at *path*.

    Raises:
        FileNotFoundError: if *path* does not exist.
        NotADirectoryError: if *path* is not a directory.
        OSError: if *path* is not empty.
        io.UnsupportedOperation: if the archive format does not support writes.
    """

@abstractmethod
def rename(self, src_path: PurePath, dst_path: PurePath) -> None:
    """Rename the file at *src_path* to *dst_path*.

    Raises:
        FileNotFoundError: if *src_path* does not exist.
        IsADirectoryError: if *src_path* is a directory.
        FileExistsError: if *dst_path* already exists.
        io.UnsupportedOperation: if the archive format does not support writes.
    """
```

---

## Layer 2: `archives.py` (`src/nova_navigator/archive/archives.py`)

Remove the `mode` parameter from `open_archive`.
Each entry in the registry maps an extension to a backend class and retains the `read_only` flag for consumer use.

```python
class SupportedArchive(NamedTuple):
    archive_class: type[Archive]
    read_only: bool

_SUPPORTED_ARCHIVES: dict[str, SupportedArchive] = {
    ".tar":    SupportedArchive(TarArchive,  read_only=False),
    ".tar.gz": SupportedArchive(TarArchive,  read_only=False),
    ...
    ".zip":    SupportedArchive(ZipArchive,  read_only=False),
    ".jar":    SupportedArchive(ZipArchive,  read_only=True),
    ...
}

def open_archive(archive_path: str | PurePath) -> Archive:
    ...
```

---

## Layer 3: `TarArchive` (`src/nova_navigator/archive/tar_archive.py`)

Always opens with `mode="r:*"`.
Drop the `mode` parameter from `__init__`.
All five write methods raise:

```python
raise io.UnsupportedOperation("write operations are not supported for tar archives")
```

---

## Layer 4: `ZipArchive` (`src/nova_navigator/archive/zip_archive.py`)

Always opens with `mode="a"`.
Drop the `mode` parameter from `__init__`.

### `_delete_entry(entry_name: str) -> None` (private helper)

Closes `self._zip_file`, calls `subprocess.run(["zip", "-d", str(self._archive_path), entry_name], check=True)`, reopens as `zipfile.ZipFile(self._archive_path, mode="a")`, calls `_refresh_members()`.

### `write(path)`

Check `_find_member(path)` — raise `FileExistsError` if found (check `is_dir()` for `IsADirectoryError`).
Call `self._zip_file.open(normalized_path, "w")` and wrap in `_WriteWrapper` (as already implemented).

### `mkdir(path)`

Check `_find_member(path)` — raise `FileExistsError` if found.
Build `zipfile.ZipInfo(normalized_path + "/")`, set `external_attr = 0o755 << 16`, call `self._zip_file.writestr(info, "")`.
Call `_refresh_members()`.

### `remove(path)`

Check `_find_member(path)` — raise `FileNotFoundError` if `None`, `IsADirectoryError` if `member.is_dir()`.
Call `_delete_entry(normalized_path)`.

### `rmdir(path)`

Check `_find_member(path)` — raise `FileNotFoundError` if `None`, `NotADirectoryError` if not a dir.
Check no other member name starts with `normalized_path + "/"` — raise `OSError` if not empty.
Call `_delete_entry(normalized_path + "/")`.

### `rename(src, dst)`

Check `_find_member(src)` — raise `FileNotFoundError` if `None`, `IsADirectoryError` if a dir.
Check `_find_member(dst)` — raise `FileExistsError` if found.
Read content: `content = self._zip_file.read(src_normalized)`.
Write under new name: `self._zip_file.writestr(dst_normalized, content)`.
Call `_delete_entry(src_normalized)`.

---

## Layer 5: `ArchiveFilesystem` (`src/nova_navigator/vfs/filesystems/archive.py`)

No `writable` flag.
Update the constructor to call `open_archive(archive.path)` (no `mode` argument) when opening from a `VPath`.
The five write methods simply call `self._assert_vpath()` then delegate to `self._archive`.
`io.UnsupportedOperation` from `TarArchive` propagates naturally — no catching or re-wrapping.

---

## Error Summary

| Condition | Exception |
|-----------|-----------|
| Any write op on TAR archive | `io.UnsupportedOperation` |
| `write()` / `mkdir()` — path already exists as a file | `FileExistsError` |
| `write()` — path is an existing directory | `IsADirectoryError` |
| `remove()` / `rmdir()` / `rename()` — src not found | `FileNotFoundError` |
| `remove()` / `rename()` — path is a directory | `IsADirectoryError` |
| `rmdir()` — path is not a directory | `NotADirectoryError` |
| `rmdir()` — directory is not empty | `OSError` |
| `rename()` — dst already exists | `FileExistsError` |

---

## Tests (`tests/archive/test_archives.py`)

### `TarArchive`

Parametrise over all 5 write methods: each raises `io.UnsupportedOperation`.

### `ZipArchive`

One fixture opens a fresh zip in a `tmp_path`.
For each method:
- Happy path: operation succeeds, `listdir` / `read` confirms the new state.
- All error cases from the table above.

### `ArchiveFilesystem` integration

Back a `ArchiveFilesystem` with a zip file.
For each of the 5 write methods: call through `ArchiveFilesystem` and confirm the result via `iterdir` / `read`.
For TAR: confirm `io.UnsupportedOperation` propagates through `ArchiveFilesystem`.
