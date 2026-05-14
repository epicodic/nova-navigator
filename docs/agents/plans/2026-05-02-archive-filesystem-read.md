# Archive Filesystem `read()` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `ArchiveFilesystem.read()` so files inside tar and zip archives can be read as byte streams, with POSIX-compliant error behaviour.

**Architecture:** Add abstract `read()` to the `Archive` ABC; implement in `TarArchive` and `ZipArchive`; delegate from `ArchiveFilesystem.read()`. Errors (`FileNotFoundError`, `IsADirectoryError`) are raised in the archive backends and propagate naturally through the VFS layer.

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

## Task 1: Add `read()` abstract method to `Archive` ABC

**Files:**
- Modify: `src/nova_navigator/archive/archive.py`

- [ ] **Step 1: Add the import and abstract method**

  Replace the contents of `src/nova_navigator/archive/archive.py` with:

  ```python
  from abc import abstractmethod
  from pathlib import PurePath
  from typing import Literal

  from ..vfs.filesystem import StreamReaderLike
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
  ```

- [ ] **Step 2: Run QA to confirm no circular-import or type errors**

  ```
  uv run qa
  ```

  Expected: PASS (no new errors; `TarArchive` and `ZipArchive` will show `abstract method not implemented` only if ty checks abstract subclasses — that is acceptable at this stage).

- [ ] **Step 3: Coding-guideline follow-up checklist**

  - [ ] `docs/coding_conventions.md` read
  - [ ] Naming matches project rules (`read`, `StreamReaderLike`)
  - [ ] Full type annotations on the new method
  - [ ] No `Optional`, no `typing.List` etc.

---

## Task 2: Implement `TarArchive.read()`

**Files:**
- Modify: `src/nova_navigator/archive/tar_archive.py`
- Test: `tests/archive/test_archives.py`

- [ ] **Step 1: Write failing tests for `TarArchive.read()` (tar-specific fixture)**

  Append to `tests/archive/test_archives.py`, after the existing `# TarArchive-specific` section:

  ```python
  # ---------------------------------------------------------------------------
  # read() — TarArchive-specific
  # ---------------------------------------------------------------------------


  def test_tar_read_returns_correct_content(tar_archive: TarArchive) -> None:
      stream = tar_archive.read(PurePath("dir1/file11.txt"))
      try:
          assert stream.read(1024) == _FILE11
      finally:
          stream.close()


  def test_tar_read_raises_is_a_directory_error(tar_archive: TarArchive) -> None:
      with pytest.raises(IsADirectoryError):
          tar_archive.read(PurePath("dir1"))


  def test_tar_read_raises_file_not_found(tar_archive: TarArchive) -> None:
      with pytest.raises(FileNotFoundError):
          tar_archive.read(PurePath("no/such/file.txt"))
  ```

- [ ] **Step 2: Run the new tests to confirm they fail**

  ```
  uv run pytest tests/archive/test_archives.py::test_tar_read_returns_correct_content tests/archive/test_archives.py::test_tar_read_raises_is_a_directory_error tests/archive/test_archives.py::test_tar_read_raises_file_not_found -v
  ```

  Expected: FAIL (`NotImplementedError` or `AttributeError`).

- [ ] **Step 3: Implement `TarArchive.read()`**

  Add the following method to `src/nova_navigator/archive/tar_archive.py`, after `stats()`:

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

  Also add the `override` import if not already present — the existing file already has `from typing import override`.

- [ ] **Step 4: Run the new tests to confirm they pass**

  ```
  uv run pytest tests/archive/test_archives.py::test_tar_read_returns_correct_content tests/archive/test_archives.py::test_tar_read_raises_is_a_directory_error tests/archive/test_archives.py::test_tar_read_raises_file_not_found -v
  ```

  Expected: PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**

  - [ ] `docs/coding_conventions.md` read
  - [ ] Full type annotations (`path: PurePath`, `-> StreamReaderLike`)
  - [ ] `StreamReaderLike` does NOT need importing in `tar_archive.py` — it's inherited via the ABC; return type annotation is satisfied structurally
  - [ ] All three tests pass

---

## Task 3: Implement `ZipArchive.read()`

**Files:**
- Modify: `src/nova_navigator/archive/zip_archive.py`
- Test: `tests/archive/test_archives.py`

- [ ] **Step 1: Write failing tests for `ZipArchive.read()` (zip-specific fixture)**

  Append to `tests/archive/test_archives.py`, after the tar-specific `read()` tests added in Task 2:

  ```python
  # ---------------------------------------------------------------------------
  # read() — ZipArchive-specific
  # ---------------------------------------------------------------------------


  def test_zip_read_returns_correct_content(zip_archive: ZipArchive) -> None:
      stream = zip_archive.read(PurePath("dir1/file11.txt"))
      try:
          assert stream.read(1024) == _FILE11
      finally:
          stream.close()


  def test_zip_read_raises_is_a_directory_error(zip_archive: ZipArchive) -> None:
      with pytest.raises(IsADirectoryError):
          zip_archive.read(PurePath("dir1"))


  def test_zip_read_raises_file_not_found(zip_archive: ZipArchive) -> None:
      with pytest.raises(FileNotFoundError):
          zip_archive.read(PurePath("no/such/file.txt"))
  ```

- [ ] **Step 2: Run the new tests to confirm they fail**

  ```
  uv run pytest tests/archive/test_archives.py::test_zip_read_returns_correct_content tests/archive/test_archives.py::test_zip_read_raises_is_a_directory_error tests/archive/test_archives.py::test_zip_read_raises_file_not_found -v
  ```

  Expected: FAIL.

- [ ] **Step 3: Implement `ZipArchive.read()`**

  Add the following method to `src/nova_navigator/archive/zip_archive.py`, after `stats()`:

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

  The existing file already has `from typing import override`.

- [ ] **Step 4: Run the new tests to confirm they pass**

  ```
  uv run pytest tests/archive/test_archives.py::test_zip_read_returns_correct_content tests/archive/test_archives.py::test_zip_read_raises_is_a_directory_error tests/archive/test_archives.py::test_zip_read_raises_file_not_found -v
  ```

  Expected: PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**

  - [ ] Full type annotations
  - [ ] No unnecessary imports
  - [ ] All three tests pass

---

## Task 4: Shared `read()` tests via the `archive` parametrised fixture

**Files:**
- Test: `tests/archive/test_archives.py`

- [ ] **Step 1: Write parametrised tests (both backends, existing `archive` fixture)**

  Append to `tests/archive/test_archives.py`, after the zip-specific `read()` tests:

  ```python
  # ---------------------------------------------------------------------------
  # read() — parametrised (tar + zip)
  # ---------------------------------------------------------------------------


  def test_read_file_content_is_correct(archive: Archive) -> None:
      stream = archive.read(PurePath("dir1/file11.txt"))
      try:
          assert stream.read(1024) == _FILE11
      finally:
          stream.close()


  def test_read_nested_file_content_is_correct(archive: Archive) -> None:
      stream = archive.read(PurePath("dir2/dir21/nested.txt"))
      try:
          assert stream.read(1024) == _NESTED
      finally:
          stream.close()


  def test_read_directory_raises_is_a_directory_error(archive: Archive) -> None:
      with pytest.raises(IsADirectoryError):
          archive.read(PurePath("dir1"))


  def test_read_missing_path_raises_file_not_found(archive: Archive) -> None:
      with pytest.raises(FileNotFoundError):
          archive.read(PurePath("does_not_exist.txt"))
  ```

- [ ] **Step 2: Run the parametrised tests**

  ```
  uv run pytest tests/archive/test_archives.py -k "test_read" -v
  ```

  Expected: all PASS (8 tests: 2 backends × 4 cases, plus the backend-specific ones).

- [ ] **Step 3: Coding-guideline follow-up checklist**

  - [ ] Tests use `try/finally` for `stream.close()` — no resource leaks
  - [ ] No duplication between parametrised and backend-specific tests (parametrised tests cover the common contract; backend tests cover backend-specific behaviour)

---

## Task 5: Implement `ArchiveFilesystem.read()`

**Files:**
- Modify: `src/nova_navigator/vfs/filesystems/archive.py`

- [ ] **Step 1: Replace the stub**

  In `src/nova_navigator/vfs/filesystems/archive.py`, replace:

  ```python
      @override
      def read(self, path: VPath) -> StreamReaderLike:
          raise NotImplementedError("ArchiveFilesystem does not support read()")
  ```

  with:

  ```python
      @override
      def read(self, path: VPath) -> StreamReaderLike:
          self._assert_vpath(path)
          return self._archive.read(path.path)
  ```

- [ ] **Step 2: Run the full archive test suite**

  ```
  uv run pytest tests/archive/ -v
  ```

  Expected: all PASS.

- [ ] **Step 3: Coding-guideline follow-up checklist**

  - [ ] No new imports needed (all types already imported)
  - [ ] `_assert_vpath` called before use — consistent with other methods
  - [ ] No error handling added here — errors propagate from archive backends naturally

---

## Task 6: Integration tests for `ArchiveFilesystem.read()`

**Files:**
- Test: `tests/archive/test_archives.py` (append an `ArchiveFilesystem` section)

> Note: `ArchiveFilesystem` lives in `nova_navigator.vfs.filesystems.archive`; `LocalFilesystem` is needed to build the `archive_parent` VPath.

- [ ] **Step 1: Write failing integration tests**

  Append to `tests/archive/test_archives.py`:

  ```python
  # ---------------------------------------------------------------------------
  # ArchiveFilesystem.read() integration
  # ---------------------------------------------------------------------------

  from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
  from nova_navigator.vfs.filesystems.local import LocalFilesystem


  @pytest.fixture
  def tar_archive_fs(tmp_path: Path) -> ArchiveFilesystem:
      archive_path = tmp_path / "test.tar.gz"
      _build_tar(archive_path)
      local_fs = LocalFilesystem.instance()
      archive_parent = local_fs.path(tmp_path)
      return ArchiveFilesystem(archive_parent=archive_parent, archive=local_fs.path(archive_path))


  @pytest.fixture
  def zip_archive_fs(tmp_path: Path) -> ArchiveFilesystem:
      archive_path = tmp_path / "test.zip"
      _build_zip(archive_path)
      local_fs = LocalFilesystem.instance()
      archive_parent = local_fs.path(tmp_path)
      return ArchiveFilesystem(archive_parent=archive_parent, archive=local_fs.path(archive_path))


  @pytest.mark.parametrize("fs_fixture", ["tar_archive_fs", "zip_archive_fs"])
  def test_archive_fs_read_returns_correct_content(
      fs_fixture: str, request: pytest.FixtureRequest
  ) -> None:
      fs: ArchiveFilesystem = request.getfixturevalue(fs_fixture)
      vpath = fs.root() / "dir1" / "file11.txt"
      stream = fs.read(vpath)
      try:
          assert stream.read(1024) == _FILE11
      finally:
          stream.close()


  @pytest.mark.parametrize("fs_fixture", ["tar_archive_fs", "zip_archive_fs"])
  def test_archive_fs_read_directory_raises(
      fs_fixture: str, request: pytest.FixtureRequest
  ) -> None:
      fs: ArchiveFilesystem = request.getfixturevalue(fs_fixture)
      vpath = fs.root() / "dir1"
      with pytest.raises(IsADirectoryError):
          fs.read(vpath)


  @pytest.mark.parametrize("fs_fixture", ["tar_archive_fs", "zip_archive_fs"])
  def test_archive_fs_read_missing_raises(
      fs_fixture: str, request: pytest.FixtureRequest
  ) -> None:
      fs: ArchiveFilesystem = request.getfixturevalue(fs_fixture)
      vpath = fs.root() / "no_such_file.txt"
      with pytest.raises(FileNotFoundError):
          fs.read(vpath)
  ```

  > Check how `LocalFilesystem.instance()` is spelled — search `local.py` for the singleton accessor if needed. If it is a different name, use that name.

- [ ] **Step 2: Run integration tests to confirm they fail (before Task 5)**

  If Task 5 is already done, skip this step.

  ```
  uv run pytest tests/archive/ -k "archive_fs_read" -v
  ```

  Expected: FAIL (stub raises `NotImplementedError`).

- [ ] **Step 3: Run integration tests after Task 5 is complete**

  ```
  uv run pytest tests/archive/ -k "archive_fs_read" -v
  ```

  Expected: PASS.

- [ ] **Step 4: Coding-guideline follow-up checklist**

  - [ ] Imports added at the top of the appended block (or moved to file-level imports)
  - [ ] All fixtures use `tmp_path` — no shared state
  - [ ] `try/finally` closes streams

---

## Task 7: Final verification

- [ ] **Step 1: Run the full test suite**

  ```
  uv run qa
  ```

  Expected: zero failures, zero lint/type errors.

- [ ] **Step 2: Confirm `ArchiveFilesystem.read()` stub message is gone**

  ```
  grep -n "does not support read" src/nova_navigator/vfs/filesystems/archive.py
  ```

  Expected: no output.
