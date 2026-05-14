# Archive Filesystem `read()` Implementation

## Goal

Implement `ArchiveFilesystem.read()` so that files inside tar and zip archives can be read as streams.
The implementation must be POSIX-compliant: reading a directory raises `IsADirectoryError`, reading a missing path raises `FileNotFoundError`.

## Approach

Follow the existing delegation pattern used by `listdir()` and `stats()`.
Add `read()` to the `Archive` ABC and implement it in each concrete backend.
`ArchiveFilesystem.read()` asserts the VPath and delegates directly to `self._archive.read(path.path)`.
Errors from the archive layer propagate naturally without catching or re-raising.

## Changes

### `src/nova_navigator/archive/archive.py`

Add abstract method:

```python
@abstractmethod
def read(self, path: PurePath) -> StreamReaderLike:
    """Return a stream reader for the file at *path* inside the archive.

    Raises:
        FileNotFoundError: if *path* does not exist in the archive.
        IsADirectoryError: if *path* is a directory.
    """
    raise NotImplementedError
```

`StreamReaderLike` must be imported from `..vfs.filesystem`.
`archive/archive.py` already imports `Stat` from `..vfs.types`; adding `..vfs.filesystem` introduces no circular dependency because `vfs/filesystem.py` does not import from `archive/`.

### `src/nova_navigator/archive/tar_archive.py`

```python
@override
def read(self, path: PurePath) -> StreamReaderLike:
    normalized_path = path.as_posix().lstrip("/")
    member = self._find_member(path)
    if member is None:
        raise FileNotFoundError(f"Path '{path}' not found in archive '{self._archive_path}'")
    if member.isdir():
        raise IsADirectoryError(f"Path '{path}' is a directory in archive '{self._archive_path}'")
    f = self._tar_file.extractfile(normalized_path)
    assert f is not None
    return f
```

`tarfile.ExFileObject` already satisfies `StreamReaderLike` (`.read(size: int) -> bytes`, `.close()`).

### `src/nova_navigator/archive/zip_archive.py`

```python
@override
def read(self, path: PurePath) -> StreamReaderLike:
    member = self._find_member(path)
    if member is None:
        raise FileNotFoundError(f"Path '{path}' not found in archive '{self._archive_path}'")
    if member.is_dir():
        raise IsADirectoryError(f"Path '{path}' is a directory in archive '{self._archive_path}'")
    return self._zip_file.open(member)
```

`zipfile.ZipExtFile` already satisfies `StreamReaderLike`.

### `src/nova_navigator/vfs/filesystems/archive.py`

Replace the `NotImplementedError` stub:

```python
@override
def read(self, path: VPath) -> StreamReaderLike:
    self._assert_vpath(path)
    return self._archive.read(path.path)
```

## Tests

### Archive layer — `tests/archive/test_archives.py`

For both `TarArchive` and `ZipArchive` fixtures:

- **Happy path**: `read()` on a known file returns the correct bytes.
- **`IsADirectoryError`**: `read()` on a directory path raises `IsADirectoryError`.
- **`FileNotFoundError`**: `read()` on a non-existent path raises `FileNotFoundError`.

### VFS layer — `tests/archive/` (new or existing file)

Integration tests via `ArchiveFilesystem`:

- `ArchiveFilesystem.read()` on a file returns the correct content.
- `ArchiveFilesystem.read()` on a directory raises `IsADirectoryError`.
- `ArchiveFilesystem.read()` on a missing path raises `FileNotFoundError`.

## Error Behaviour Summary

| Condition | Exception |
|-----------|-----------|
| Path does not exist in archive | `FileNotFoundError` |
| Path is a directory | `IsADirectoryError` |
| Path is a file | returns `StreamReaderLike` |
