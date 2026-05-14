# Archive Filesystem Write Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `write()`, `mkdir()`, `remove()`, `rmdir()`, and `rename()` on `ArchiveFilesystem`, with real ZIP support via native Python 3.12 APIs and `io.UnsupportedOperation` for TAR.

**Architecture:** Add 5 abstract methods to `Archive` ABC. `TarArchive` raises `io.UnsupportedOperation` for all five. `ZipArchive` implements them natively using `zipfile` APIs. `ArchiveFilesystem` adds a `writable` flag, opens archives in `"a"` mode when writable, raises `PermissionError` for unwritable archives, and delegates to the archive layer.

**Tech Stack:** Python 3.12, pytest

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

---

## File Map

| Action | File |
|--------|------|
| Modify | `src/nova_navigator/archive/archive.py` |
| Modify | `src/nova_navigator/archive/tar_archive.py` |
| Modify | `src/nova_navigator/archive/zip_archive.py` |
| Modify | `src/nova_navigator/vfs/filesystems/archive.py` |
| Modify | `tests/archive/test_archives.py` |

---

## Task 1: Add 5 abstract write methods to `Archive` ABC

**Files:**
- Modify: `src/nova_navigator/archive/archive.py`

- [ ] **Step 1: Add `StreamWriterLike` import and the 5 abstract methods**

  Replace the import line and append to the class. Final file contents:

  ```python
  import io
  from abc import abstractmethod
  from pathlib import PurePath
  from typing import Literal

  from ..vfs.filesystem import StreamReaderLike, StreamWriterLike
  from ..vfs.types import Stat


  class Archive:
      """A class providing an abstraction for archive files."""

      Mode = Literal["r", "w", "a"]

      _archive_path: PurePath
      _mode: Mode

      def __init__(self, archive_path: PurePath, mode: Mode) -> None:
          self._archive_path = archive_path
          self._mode = mode

      @abstractmethod
      def listdir(self, path: PurePath) -> list[PurePath]:
          """List the contents of a directory inside the archive."""
          raise NotImplementedError

      @abstractmethod
      def stats(self, path: PurePath) -> Stat:
          """Get the stats of a file/directory inside the archive."""
          raise NotImplementedError

      @abstractmethod
      def read(self, path: PurePath) -> StreamReaderLike:
          """Return a stream reader for the file at *path* inside the archive.

          Raises:
              FileNotFoundError: if *path* does not exist in the archive.
              IsADirectoryError: if *path* is a directory.
          """
          raise NotImplementedError

      @abstractmethod
      def write(self, path: PurePath) -> StreamWriterLike:
          """Return a stream writer that creates a new file at *path*.

          Raises:
              FileExistsError: if *path* already exists.
              IsADirectoryError: if *path* is an existing directory.
              io.UnsupportedOperation: if the archive format does not support writes.
          """
          raise NotImplementedError

      @abstractmethod
      def mkdir(self, path: PurePath) -> None:
          """Create a directory entry at *path*.

          Raises:
              FileExistsError: if *path* already exists.
              io.UnsupportedOperation: if the archive format does not support writes.
          """
          raise NotImplementedError

      @abstractmethod
      def remove(self, path: PurePath) -> None:
          """Remove the file at *path*.

          Raises:
              FileNotFoundError: if *path* does not exist.
              IsADirectoryError: if *path* is a directory.
              io.UnsupportedOperation: if the archive format does not support writes.
          """
          raise NotImplementedError

      @abstractmethod
      def rmdir(self, path: PurePath) -> None:
          """Remove the empty directory at *path*.

          Raises:
              FileNotFoundError: if *path* does not exist.
              NotADirectoryError: if *path* is not a directory.
              OSError: if the directory is not empty.
              io.UnsupportedOperation: if the archive format does not support writes.
          """
          raise NotImplementedError

      @abstractmethod
      def rename(self, src_path: PurePath, dst_path: PurePath) -> None:
          """Rename the file at *src_path* to *dst_path*.

          Raises:
              FileNotFoundError: if *src_path* does not exist.
              IsADirectoryError: if *src_path* is a directory.
              FileExistsError: if *dst_path* already exists.
              io.UnsupportedOperation: if the archive format does not support writes.
          """
          raise NotImplementedError
  ```

- [ ] **Step 2: Run QA — confirm no new errors**

  ```
  uv run qa
  ```

  Expected: same failures as baseline (one pre-existing popup widget flake). The `TarArchive` and `ZipArchive` subclasses not yet implementing the new abstract methods is acceptable at this stage — ty may warn but tests pass.

- [ ] **Step 3: Coding-guideline follow-up checklist**
  - [ ] `docs/coding_conventions.md` read
  - [ ] Full type annotations on all new methods
  - [ ] No `Optional`, no `typing.List`

---

## Task 2: Implement TAR stubs (all 5 raise `io.UnsupportedOperation`)

**Files:**
- Modify: `src/nova_navigator/archive/tar_archive.py`
- Test: `tests/archive/test_archives.py`

- [ ] **Step 1: Write failing tests**

  Append to `tests/archive/test_archives.py`, after the existing tar-specific `read()` tests:

  ```python
  # ---------------------------------------------------------------------------
  # write/mkdir/remove/rmdir/rename — TarArchive raises UnsupportedOperation
  # ---------------------------------------------------------------------------


  def test_tar_write_raises_unsupported(tar_archive: TarArchive) -> None:
      with pytest.raises(io.UnsupportedOperation):
          tar_archive.write(PurePath("new_file.txt"))


  def test_tar_mkdir_raises_unsupported(tar_archive: TarArchive) -> None:
      with pytest.raises(io.UnsupportedOperation):
          tar_archive.mkdir(PurePath("new_dir"))


  def test_tar_remove_raises_unsupported(tar_archive: TarArchive) -> None:
      with pytest.raises(io.UnsupportedOperation):
          tar_archive.remove(PurePath("dir1/file11.txt"))


  def test_tar_rmdir_raises_unsupported(tar_archive: TarArchive) -> None:
      with pytest.raises(io.UnsupportedOperation):
          tar_archive.rmdir(PurePath("dir_empty"))


  def test_tar_rename_raises_unsupported(tar_archive: TarArchive) -> None:
      with pytest.raises(io.UnsupportedOperation):
          tar_archive.rename(PurePath("dir1/file11.txt"), PurePath("dir1/renamed.txt"))
  ```

  Also add `import io` to the imports at the top of `test_archives.py` (it's already imported — check first):

  The file already imports `import io` at line 5 — no change needed.

- [ ] **Step 2: Run tests to confirm they fail**

  ```
  uv run pytest tests/archive/test_archives.py -k "tar_write or tar_mkdir or tar_remove or tar_rmdir or tar_rename" -v
  ```

  Expected: FAIL (`NotImplementedError` from base class).

- [ ] **Step 3: Implement in `tar_archive.py`**

  Add `import io` to imports and append the 5 methods after `read()`:

  ```python
  import io
  import tarfile
  from pathlib import PurePath
  from typing import override

  from .archive import Archive, Stat, StreamReaderLike, StreamWriterLike
  ```

  Then append after the `read()` method:

  ```python
  @override
  def write(self, path: PurePath) -> StreamWriterLike:
      raise io.UnsupportedOperation("write operations are not supported for tar archives")

  @override
  def mkdir(self, path: PurePath) -> None:
      raise io.UnsupportedOperation("write operations are not supported for tar archives")

  @override
  def remove(self, path: PurePath) -> None:
      raise io.UnsupportedOperation("write operations are not supported for tar archives")

  @override
  def rmdir(self, path: PurePath) -> None:
      raise io.UnsupportedOperation("write operations are not supported for tar archives")

  @override
  def rename(self, src_path: PurePath, dst_path: PurePath) -> None:
      raise io.UnsupportedOperation("write operations are not supported for tar archives")
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```
  uv run pytest tests/archive/test_archives.py -k "tar_write or tar_mkdir or tar_remove or tar_rmdir or tar_rename" -v
  ```

  Expected: 5 PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**
  - [ ] Full type annotations on all 5 methods
  - [ ] `StreamWriterLike` imported (needed for `write()` return type annotation)

---

## Task 3: Implement `ZipArchive.write()`

**Files:**
- Modify: `src/nova_navigator/archive/zip_archive.py`
- Test: `tests/archive/test_archives.py`

- [ ] **Step 1: Write failing tests**

  Append to `tests/archive/test_archives.py` after the zip-specific `read()` tests:

  ```python
  # ---------------------------------------------------------------------------
  # ZipArchive.write()
  # ---------------------------------------------------------------------------


  @pytest.fixture
  def writable_zip_archive(tmp_path: Path) -> ZipArchive:
      path = tmp_path / "test.zip"
      _build_zip(path)
      return ZipArchive(archive_path=path, mode="a")


  def test_zip_write_new_file_content_is_readable(writable_zip_archive: ZipArchive) -> None:
      stream = writable_zip_archive.write(PurePath("new_file.txt"))
      try:
          stream.write(b"hello new file")
      finally:
          stream.close()
      result = writable_zip_archive.read(PurePath("new_file.txt"))
      try:
          assert result.read(1024) == b"hello new file"
      finally:
          result.close()


  def test_zip_write_existing_file_raises_file_exists(writable_zip_archive: ZipArchive) -> None:
      with pytest.raises(FileExistsError):
          writable_zip_archive.write(PurePath("dir1/file11.txt"))


  def test_zip_write_existing_directory_raises_is_a_directory(writable_zip_archive: ZipArchive) -> None:
      with pytest.raises(IsADirectoryError):
          writable_zip_archive.write(PurePath("dir1"))
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```
  uv run pytest tests/archive/test_archives.py -k "zip_write" -v
  ```

  Expected: FAIL.

- [ ] **Step 3: Implement `ZipArchive.write()`**

  Update imports in `zip_archive.py`:

  ```python
  import zipfile
  from pathlib import PurePath
  from typing import Any, Callable, override

  from .archive import Archive, Stat, StreamReaderLike, StreamWriterLike
  ```

  Add the `_WriteWrapper` inner class and `write()` method after `read()`:

  ```python
  @override
  def write(self, path: PurePath) -> StreamWriterLike:
      member = self._find_member(path)
      if member is not None:
          if member.is_dir():
              raise IsADirectoryError(f"Path '{path}' is a directory in archive '{self._archive_path}'")
          raise FileExistsError(f"Path '{path}' already exists in archive '{self._archive_path}'")
      normalized_path = path.as_posix().lstrip("/")

      class _WriteWrapper:
          def __init__(self, f: Any, refresh: Callable[[], None]) -> None:
              self._f = f
              self._refresh = refresh

          def write(self, data: bytes) -> int:
              return self._f.write(data)

          def close(self) -> None:
              self._f.close()
              self._refresh()

      return _WriteWrapper(self._zip_file.open(normalized_path, "w"), lambda: self._refresh_members())

  def _refresh_members(self) -> None:
      self._members = self._zip_file.infolist()
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```
  uv run pytest tests/archive/test_archives.py -k "zip_write" -v
  ```

  Expected: 3 PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**
  - [ ] `Any`, `Callable` imported from correct locations (`typing`)
  - [ ] `_refresh_members` is private (`_` prefix) — ✓
  - [ ] `_WriteWrapper` is a private inner class — ✓

---

## Task 4: Implement `ZipArchive.mkdir()`

**Files:**
- Modify: `src/nova_navigator/archive/zip_archive.py`
- Test: `tests/archive/test_archives.py`

- [ ] **Step 1: Write failing tests**

  Append after the `write()` tests:

  ```python
  # ---------------------------------------------------------------------------
  # ZipArchive.mkdir()
  # ---------------------------------------------------------------------------


  def test_zip_mkdir_creates_listable_directory(writable_zip_archive: ZipArchive) -> None:
      writable_zip_archive.mkdir(PurePath("new_dir"))
      entries = writable_zip_archive.listdir(PurePath("/"))
      assert PurePath("new_dir") in entries


  def test_zip_mkdir_new_dir_stat_is_directory(writable_zip_archive: ZipArchive) -> None:
      writable_zip_archive.mkdir(PurePath("new_dir"))
      assert writable_zip_archive.stats(PurePath("new_dir")).is_directory


  def test_zip_mkdir_existing_path_raises_file_exists(writable_zip_archive: ZipArchive) -> None:
      with pytest.raises(FileExistsError):
          writable_zip_archive.mkdir(PurePath("dir1"))


  def test_zip_mkdir_existing_file_raises_file_exists(writable_zip_archive: ZipArchive) -> None:
      with pytest.raises(FileExistsError):
          writable_zip_archive.mkdir(PurePath("dir1/file11.txt"))
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```
  uv run pytest tests/archive/test_archives.py -k "zip_mkdir" -v
  ```

  Expected: FAIL.

- [ ] **Step 3: Implement `ZipArchive.mkdir()`**

  Add after `_refresh_members()`:

  ```python
  @override
  def mkdir(self, path: PurePath) -> None:
      member = self._find_member(path)
      if member is not None:
          raise FileExistsError(f"Path '{path}' already exists in archive '{self._archive_path}'")
      normalized_path = path.as_posix().lstrip("/")
      info = zipfile.ZipInfo(normalized_path + "/")
      info.external_attr = 0o755 << 16
      self._zip_file.writestr(info, "")
      self._refresh_members()
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```
  uv run pytest tests/archive/test_archives.py -k "zip_mkdir" -v
  ```

  Expected: 4 PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**
  - [ ] Full type annotations
  - [ ] `_refresh_members()` called after mutation

---

## Task 5: Implement `ZipArchive.remove()`

**Files:**
- Modify: `src/nova_navigator/archive/zip_archive.py`
- Test: `tests/archive/test_archives.py`

- [ ] **Step 1: Write failing tests**

  Append after the `mkdir()` tests:

  ```python
  # ---------------------------------------------------------------------------
  # ZipArchive.remove()
  # ---------------------------------------------------------------------------


  def test_zip_remove_file_is_gone_from_listdir(writable_zip_archive: ZipArchive) -> None:
      writable_zip_archive.remove(PurePath("dir1/file11.txt"))
      entries = writable_zip_archive.listdir(PurePath("dir1"))
      assert PurePath("file11.txt") not in entries


  def test_zip_remove_missing_raises_file_not_found(writable_zip_archive: ZipArchive) -> None:
      with pytest.raises(FileNotFoundError):
          writable_zip_archive.remove(PurePath("no_such.txt"))


  def test_zip_remove_directory_raises_is_a_directory(writable_zip_archive: ZipArchive) -> None:
      with pytest.raises(IsADirectoryError):
          writable_zip_archive.remove(PurePath("dir1"))
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```
  uv run pytest tests/archive/test_archives.py -k "zip_remove" -v
  ```

  Expected: FAIL.

- [ ] **Step 3: Implement `ZipArchive.remove()`**

  Add after `mkdir()`:

  ```python
  @override
  def remove(self, path: PurePath) -> None:
      member = self._find_member(path)
      if member is None:
          raise FileNotFoundError(f"Path '{path}' not found in archive '{self._archive_path}'")
      if member.is_dir():
          raise IsADirectoryError(f"Path '{path}' is a directory in archive '{self._archive_path}'")
      self._zip_file.remove(member)
      self._refresh_members()
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```
  uv run pytest tests/archive/test_archives.py -k "zip_remove" -v
  ```

  Expected: 3 PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**
  - [ ] Full type annotations
  - [ ] `_refresh_members()` called after mutation

---

## Task 6: Implement `ZipArchive.rmdir()`

**Files:**
- Modify: `src/nova_navigator/archive/zip_archive.py`
- Test: `tests/archive/test_archives.py`

- [ ] **Step 1: Write failing tests**

  Append after the `remove()` tests:

  ```python
  # ---------------------------------------------------------------------------
  # ZipArchive.rmdir()
  # ---------------------------------------------------------------------------


  def test_zip_rmdir_empty_dir_is_gone(writable_zip_archive: ZipArchive) -> None:
      writable_zip_archive.rmdir(PurePath("dir_empty"))
      entries = writable_zip_archive.listdir(PurePath("/"))
      assert PurePath("dir_empty") not in entries


  def test_zip_rmdir_missing_raises_file_not_found(writable_zip_archive: ZipArchive) -> None:
      with pytest.raises(FileNotFoundError):
          writable_zip_archive.rmdir(PurePath("no_such_dir"))


  def test_zip_rmdir_file_raises_not_a_directory(writable_zip_archive: ZipArchive) -> None:
      with pytest.raises(NotADirectoryError):
          writable_zip_archive.rmdir(PurePath("dir1/file11.txt"))


  def test_zip_rmdir_nonempty_raises_os_error(writable_zip_archive: ZipArchive) -> None:
      with pytest.raises(OSError):
          writable_zip_archive.rmdir(PurePath("dir1"))
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```
  uv run pytest tests/archive/test_archives.py -k "zip_rmdir" -v
  ```

  Expected: FAIL.

- [ ] **Step 3: Implement `ZipArchive.rmdir()`**

  Add after `remove()`:

  ```python
  @override
  def rmdir(self, path: PurePath) -> None:
      member = self._find_member(path)
      if member is None:
          raise FileNotFoundError(f"Path '{path}' not found in archive '{self._archive_path}'")
      if not member.is_dir():
          raise NotADirectoryError(f"Path '{path}' is not a directory in archive '{self._archive_path}'")
      normalized_path = path.as_posix().lstrip("/")
      prefix = normalized_path + "/"
      for m in self._members:
          if m.filename.startswith(prefix) and m.filename != member.filename:
              raise OSError(f"Directory '{path}' is not empty in archive '{self._archive_path}'")
      self._zip_file.remove(member)
      self._refresh_members()
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```
  uv run pytest tests/archive/test_archives.py -k "zip_rmdir" -v
  ```

  Expected: 4 PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**
  - [ ] Full type annotations
  - [ ] Non-empty check iterates `_members` before `remove()` is called

---

## Task 7: Implement `ZipArchive.rename()`

**Files:**
- Modify: `src/nova_navigator/archive/zip_archive.py`
- Test: `tests/archive/test_archives.py`

- [ ] **Step 1: Write failing tests**

  Append after the `rmdir()` tests:

  ```python
  # ---------------------------------------------------------------------------
  # ZipArchive.rename()
  # ---------------------------------------------------------------------------


  def test_zip_rename_file_content_accessible_under_new_name(writable_zip_archive: ZipArchive) -> None:
      writable_zip_archive.rename(PurePath("dir1/file11.txt"), PurePath("dir1/renamed.txt"))
      result = writable_zip_archive.read(PurePath("dir1/renamed.txt"))
      try:
          assert result.read(1024) == _FILE11
      finally:
          result.close()


  def test_zip_rename_old_name_no_longer_exists(writable_zip_archive: ZipArchive) -> None:
      writable_zip_archive.rename(PurePath("dir1/file11.txt"), PurePath("dir1/renamed.txt"))
      with pytest.raises(FileNotFoundError):
          writable_zip_archive.read(PurePath("dir1/file11.txt"))


  def test_zip_rename_missing_src_raises_file_not_found(writable_zip_archive: ZipArchive) -> None:
      with pytest.raises(FileNotFoundError):
          writable_zip_archive.rename(PurePath("no_such.txt"), PurePath("dst.txt"))


  def test_zip_rename_directory_raises_is_a_directory(writable_zip_archive: ZipArchive) -> None:
      with pytest.raises(IsADirectoryError):
          writable_zip_archive.rename(PurePath("dir1"), PurePath("dir2_renamed"))


  def test_zip_rename_existing_dst_raises_file_exists(writable_zip_archive: ZipArchive) -> None:
      with pytest.raises(FileExistsError):
          writable_zip_archive.rename(PurePath("dir1/file11.txt"), PurePath("dir1/file12.txt"))
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```
  uv run pytest tests/archive/test_archives.py -k "zip_rename" -v
  ```

  Expected: FAIL.

- [ ] **Step 3: Implement `ZipArchive.rename()`**

  Add after `rmdir()`:

  ```python
  @override
  def rename(self, src_path: PurePath, dst_path: PurePath) -> None:
      src_member = self._find_member(src_path)
      if src_member is None:
          raise FileNotFoundError(f"Path '{src_path}' not found in archive '{self._archive_path}'")
      if src_member.is_dir():
          raise IsADirectoryError(f"Path '{src_path}' is a directory in archive '{self._archive_path}'")
      if self._find_member(dst_path) is not None:
          raise FileExistsError(f"Path '{dst_path}' already exists in archive '{self._archive_path}'")
      dst_normalized = dst_path.as_posix().lstrip("/")
      content = self._zip_file.read(src_member)
      self._zip_file.writestr(dst_normalized, content)
      self._zip_file.remove(src_member)
      self._refresh_members()
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```
  uv run pytest tests/archive/test_archives.py -k "zip_rename" -v
  ```

  Expected: 5 PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**
  - [ ] Full type annotations
  - [ ] `_refresh_members()` called once after all mutations complete

---

## Task 8: Update `ArchiveFilesystem` — `writable` flag + 5 delegating methods

**Files:**
- Modify: `src/nova_navigator/vfs/filesystems/archive.py`

- [ ] **Step 1: Update `__init__` and the 5 stub methods**

  Replace the entire file with:

  ```python
  from __future__ import annotations

  from typing import override

  from ...archive import Archive, open_archive
  from ..filesystem import Filesystem, Stat, StreamReaderLike, StreamWriterLike
  from ..vpath import VPath
  from .local import LocalFilesystem


  class ArchiveFilesystem(Filesystem):
      """Filesystem backed by a tar or zip archive.

      Pass either a pre-opened :class:`~nova_navigator.archive.Archive` or a
      :class:`~nova_navigator.vfs.vpath.VPath` pointing to an archive file on
      the local filesystem.  *archive_parent* is the :class:`VPath` returned by
      :meth:`parent` when the caller asks for the parent of the archive root —
      i.e. the directory that contains the archive file itself.

      Set *writable* to ``True`` to open the archive in append mode and enable
      write operations.  Write operations on a non-writable filesystem raise
      :exc:`PermissionError`.
      """

      _archive_parent: VPath
      _archive: Archive
      _writable: bool

      def __init__(
          self, archive_parent: VPath, archive: Archive | VPath, writable: bool = False
      ) -> None:
          self._archive_parent = archive_parent
          self._writable = writable
          if isinstance(archive, Archive):
              self._archive = archive
          else:
              assert isinstance(archive.filesystem, LocalFilesystem)
              mode = "a" if writable else "r"
              self._archive = open_archive(archive.path, mode=mode)

      def _assert_writable(self) -> None:
          if not self._writable:
              raise PermissionError("archive is not opened for writing")

      @override
      def cwd(self) -> VPath:
          return self.root()

      @override
      def root(self) -> VPath:
          return VPath("/", self)

      @override
      def home(self) -> VPath:
          return self.root()

      @override
      def iterdir(self, path: VPath) -> list[VPath]:
          self._assert_vpath(path)
          return [VPath(path.path / entry, self) for entry in self._archive.listdir(path.path)]

      @override
      def parent(self, path: VPath) -> VPath:
          self._assert_vpath(path)
          if path.path.parent == path.path:
              return self._archive_parent
          return VPath(path.path.parent, self)

      @override
      def stat(self, path: VPath) -> Stat:
          self._assert_vpath(path)
          return self._archive.stats(path.path)

      @override
      def is_same_device(self, path1: VPath, path2: VPath) -> bool:
          self._assert_vpath(path1)
          return path1.filesystem == path2.filesystem

      @override
      def read(self, path: VPath) -> StreamReaderLike:
          self._assert_vpath(path)
          return self._archive.read(path.path)

      @override
      def write(self, path: VPath) -> StreamWriterLike:
          self._assert_vpath(path)
          self._assert_writable()
          return self._archive.write(path.path)

      @override
      def remove(self, path: VPath) -> None:
          self._assert_vpath(path)
          self._assert_writable()
          self._archive.remove(path.path)

      @override
      def rename(self, src_path: VPath, dst_path: VPath) -> None:
          self._assert_vpath(src_path)
          self._assert_vpath(dst_path)
          self._assert_writable()
          self._archive.rename(src_path.path, dst_path.path)

      @override
      def rmdir(self, path: VPath) -> None:
          self._assert_vpath(path)
          self._assert_writable()
          self._archive.rmdir(path.path)

      @override
      def mkdir(self, path: VPath) -> None:
          self._assert_vpath(path)
          self._assert_writable()
          self._archive.mkdir(path.path)

      def __eq__(self, value: object) -> bool:
          return (
              isinstance(value, ArchiveFilesystem)
              and self._archive == value._archive
              and self._archive_parent == value._archive_parent
          )

      def __hash__(self) -> int:
          return hash((self._archive, self._archive_parent))
  ```

- [ ] **Step 2: Run the full archive test suite**

  ```
  uv run pytest tests/archive/ -v
  ```

  Expected: all existing tests pass (no regressions).

- [ ] **Step 3: Coding-guideline follow-up checklist**
  - [ ] `_assert_writable` is private — ✓
  - [ ] Docstring updated (no longer says "read-only")
  - [ ] `writable=False` default preserves backward compatibility

---

## Task 9: `ArchiveFilesystem` integration tests for write operations

**Files:**
- Test: `tests/archive/test_archives.py`

- [ ] **Step 1: Add writable filesystem fixtures and integration tests**

  Append to `tests/archive/test_archives.py`:

  ```python
  # ---------------------------------------------------------------------------
  # ArchiveFilesystem write operations integration
  # ---------------------------------------------------------------------------


  @pytest.fixture
  def writable_zip_archive_fs(tmp_path: Path) -> ArchiveFilesystem:
      archive_path = tmp_path / "test.zip"
      _build_zip(archive_path)
      local_fs = LocalFilesystem.singleton()
      archive_parent = local_fs.path(tmp_path)
      return ArchiveFilesystem(archive_parent=archive_parent, archive=local_fs.path(archive_path), writable=True)


  @pytest.fixture
  def writable_tar_archive_fs(tmp_path: Path) -> ArchiveFilesystem:
      archive_path = tmp_path / "test.tar.gz"
      _build_tar(archive_path)
      local_fs = LocalFilesystem.singleton()
      archive_parent = local_fs.path(tmp_path)
      return ArchiveFilesystem(archive_parent=archive_parent, archive=local_fs.path(archive_path), writable=True)


  def test_archive_fs_write_raises_permission_error_when_not_writable(
      zip_archive_fs: ArchiveFilesystem,
  ) -> None:
      vpath = zip_archive_fs.root() / "new_file.txt"
      with pytest.raises(PermissionError):
          zip_archive_fs.write(vpath)


  def test_archive_fs_write_file_readable_via_vpath(writable_zip_archive_fs: ArchiveFilesystem) -> None:
      vpath = writable_zip_archive_fs.root() / "new_file.txt"
      stream = writable_zip_archive_fs.write(vpath)
      try:
          stream.write(b"hello")
      finally:
          stream.close()
      result = writable_zip_archive_fs.read(vpath)
      try:
          assert result.read(1024) == b"hello"
      finally:
          result.close()


  def test_archive_fs_mkdir_creates_directory(writable_zip_archive_fs: ArchiveFilesystem) -> None:
      vpath = writable_zip_archive_fs.root() / "my_new_dir"
      writable_zip_archive_fs.mkdir(vpath)
      assert writable_zip_archive_fs.stat(vpath).is_directory


  def test_archive_fs_remove_file(writable_zip_archive_fs: ArchiveFilesystem) -> None:
      vpath = writable_zip_archive_fs.root() / "dir1" / "file11.txt"
      writable_zip_archive_fs.remove(vpath)
      with pytest.raises(FileNotFoundError):
          writable_zip_archive_fs.stat(vpath)


  def test_archive_fs_rmdir_empty_directory(writable_zip_archive_fs: ArchiveFilesystem) -> None:
      vpath = writable_zip_archive_fs.root() / "dir_empty"
      writable_zip_archive_fs.rmdir(vpath)
      entries = list(writable_zip_archive_fs.iterdir(writable_zip_archive_fs.root()))
      assert vpath not in entries


  def test_archive_fs_rename_file(writable_zip_archive_fs: ArchiveFilesystem) -> None:
      src = writable_zip_archive_fs.root() / "dir1" / "file11.txt"
      dst = writable_zip_archive_fs.root() / "dir1" / "renamed.txt"
      writable_zip_archive_fs.rename(src, dst)
      result = writable_zip_archive_fs.read(dst)
      try:
          assert result.read(1024) == _FILE11
      finally:
          result.close()


  def test_archive_fs_tar_write_raises_unsupported(writable_tar_archive_fs: ArchiveFilesystem) -> None:
      vpath = writable_tar_archive_fs.root() / "new_file.txt"
      with pytest.raises(io.UnsupportedOperation):
          writable_tar_archive_fs.write(vpath)
  ```

- [ ] **Step 2: Run integration tests**

  ```
  uv run pytest tests/archive/ -k "archive_fs" -v
  ```

  Expected: all PASS.

- [ ] **Step 3: Coding-guideline follow-up checklist**
  - [ ] `try/finally` used around all stream.close() calls
  - [ ] All test functions fully type-annotated

---

## Task 10: Final verification

- [ ] **Step 1: Run full QA**

  ```
  uv run qa
  ```

  Expected: same pre-existing single failure (`test_popup_widget`), all new tests pass.

- [ ] **Step 2: Confirm old read-only stubs are gone**

  ```
  grep -n "read-only" src/nova_navigator/vfs/filesystems/archive.py
  ```

  Expected: no output (docstring updated).
