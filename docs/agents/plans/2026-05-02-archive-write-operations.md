# Archive Write Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement full read/write archive operations — ZIP archives via native `zipfile` APIs plus `zip -d` CLI for deletion; TAR archives remain read-only via `io.UnsupportedOperation`.

**Architecture:** No signature changes to `Archive`, `TarArchive`, `ZipArchive`, or `archives.py`. `ZipArchive` always opens with `mode="a"` regardless of the passed `mode` arg (append mode supports both reads and writes). A private `_delete_entry()` helper handles entry removal via `zip -d`. `ArchiveFilesystem` delegates all five write methods to the archive backend directly; `io.UnsupportedOperation` from `TarArchive` propagates naturally.

**Tech Stack:** Python 3.12, pytest

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Spec:** `docs/agents/specs/2026-05-02-archive-filesystem-write-design.md`

---

## File Map

| File | Change |
|------|--------|
| `src/nova_navigator/archive/zip_archive.py` | Always open with `"a"`; add `_delete_entry()`; fix `remove()`/`rmdir()`/`rename()` |
| `src/nova_navigator/vfs/filesystems/archive.py` | Implement 5 write methods by delegating to `self._archive` |
| `tests/archive/test_archives.py` | Update `writable_zip_archive` fixture (drop `mode="a"` — now redundant); add FS write integration tests |

No changes to `archive.py` (ABC), `archives.py`, `tar_archive.py`, or any fixture that passes `mode=` to `TarArchive`/`ZipArchive`.

---

### Task 1: Fix `ZipArchive` — always open with `"a"`, add `_delete_entry`, fix `remove`/`rmdir`/`rename`

**Files:**
- Modify: `src/nova_navigator/archive/zip_archive.py`

- [ ] **Step 1: Run the currently-failing tests to establish baseline**

```
uv run pytest tests/archive/test_archives.py -k "remove or rmdir or rename" -v
```

Expected: 4 failures (`test_zip_remove_*`, `test_zip_rmdir_empty_dir_is_gone`, `test_zip_rename_*`) with `AttributeError: 'ZipFile' object has no attribute 'remove'`.

- [ ] **Step 2: Verify `zip` CLI is available**

```
which zip
```

Expected: `/usr/bin/zip` or similar. If missing: `sudo apt install zip`.

- [ ] **Step 3: Replace `ZipArchive.__init__`, add `_delete_entry`, fix `remove`/`rmdir`/`rename`**

In `src/nova_navigator/archive/zip_archive.py`:

Change `__init__` to always open with `"a"` (ignoring the passed `mode`):

```python
def __init__(self, archive_path: PurePath, mode: Archive.Mode) -> None:
    super().__init__(archive_path, mode)
    self._zip_file = zipfile.ZipFile(archive_path, mode="a")
    self._members = self._zip_file.infolist()
```

Add `import subprocess` at the top of the file.

Add `_delete_entry` after `_refresh_members`:

```python
def _delete_entry(self, entry_name: str) -> None:
    self._zip_file.close()
    subprocess.run(["zip", "-d", str(self._archive_path), entry_name], check=True)
    self._zip_file = zipfile.ZipFile(self._archive_path, mode="a")
    self._refresh_members()
```

Replace the `remove` method:

```python
@override
def remove(self, path: PurePath) -> None:
    member = self._find_member(path)
    if member is None:
        raise FileNotFoundError(f"Path '{path}' not found in archive '{self._archive_path}'")
    if member.is_dir():
        raise IsADirectoryError(f"Path '{path}' is a directory in archive '{self._archive_path}'")
    normalized_path = path.as_posix().lstrip("/")
    self._delete_entry(normalized_path)
```

Replace the `rmdir` method:

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
    self._delete_entry(normalized_path + "/")
```

Replace the `rename` method:

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
    src_normalized = src_path.as_posix().lstrip("/")
    dst_normalized = dst_path.as_posix().lstrip("/")
    content = self._zip_file.read(src_member)
    self._zip_file.writestr(dst_normalized, content)
    self._delete_entry(src_normalized)
```

- [ ] **Step 4: Run all zip tests**

```
uv run pytest tests/archive/test_archives.py -k "zip" -v
```

Expected: all pass, including the previously-failing 4.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] `import subprocess` added at top
- [ ] `__init__` still accepts `mode: Archive.Mode` but passes `"a"` to `ZipFile`
- [ ] `_delete_entry` closes zip, calls subprocess, reopens with `"a"`, refreshes members
- [ ] `rmdir` passes `normalized_path + "/"` to `_delete_entry` (trailing slash matches ZipInfo filename)
- [ ] `rename` writes dst before deleting src (no data loss on failure)

---

### Task 2: Implement `ArchiveFilesystem` write methods

**Files:**
- Modify: `src/nova_navigator/vfs/filesystems/archive.py`

- [ ] **Step 1: Replace the five stub write methods**

Current stubs in `src/nova_navigator/vfs/filesystems/archive.py` all raise `NotImplementedError`. Replace them:

```python
@override
def write(self, path: VPath) -> StreamWriterLike:
    self._assert_vpath(path)
    return self._archive.write(path.path)

@override
def remove(self, path: VPath) -> None:
    self._assert_vpath(path)
    self._archive.remove(path.path)

@override
def rename(self, src_path: VPath, dst_path: VPath) -> None:
    self._assert_vpath(src_path)
    self._assert_vpath(dst_path)
    self._archive.rename(src_path.path, dst_path.path)

@override
def rmdir(self, path: VPath) -> None:
    self._assert_vpath(path)
    self._archive.rmdir(path.path)

@override
def mkdir(self, path: VPath) -> None:
    self._assert_vpath(path)
    self._archive.mkdir(path.path)
```

- [ ] **Step 2: Verify type check**

```
uv run ty check src/nova_navigator/vfs/filesystems/archive.py
```

Expected: no errors.

- [ ] **Step 3: Coding-guideline follow-up checklist**

- [ ] All five methods delegate to `self._archive` with no extra logic
- [ ] `rename` asserts both `src_path` and `dst_path`
- [ ] No `writable` guard or `PermissionError` logic

---

### Task 3: Update tests — fix `writable_zip_archive` fixture, add FS write integration tests

**Files:**
- Modify: `tests/archive/test_archives.py`

- [ ] **Step 1: Update `writable_zip_archive` fixture**

The fixture currently passes `mode="a"`. Since `ZipArchive` now always opens with `"a"`, the fixture is equivalent to `zip_archive`. Simplify it to use `mode="r"` (or any mode — they're all equivalent now). Keep the fixture name so existing tests don't need updating:

```python
@pytest.fixture
def writable_zip_archive(tmp_path: Path) -> ZipArchive:
    path = tmp_path / "test.zip"
    _build_zip(path)
    return ZipArchive(archive_path=path, mode="r")
```

- [ ] **Step 2: Run all archive tests to confirm no regressions**

```
uv run pytest tests/archive/test_archives.py -v
```

Expected: all pass.

- [ ] **Step 3: Add `ArchiveFilesystem` write integration tests**

Append to `tests/archive/test_archives.py`:

```python
# ---------------------------------------------------------------------------
# ArchiveFilesystem write integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def zip_archive_fs_write(tmp_path: Path) -> ArchiveFilesystem:
    archive_path = tmp_path / "test.zip"
    _build_zip(archive_path)
    local_fs = LocalFilesystem.singleton()
    archive_parent = local_fs.path(tmp_path)
    return ArchiveFilesystem(archive_parent=archive_parent, archive=local_fs.path(archive_path))


def test_archive_fs_write_new_file_readable(zip_archive_fs_write: ArchiveFilesystem) -> None:
    fs = zip_archive_fs_write
    vpath = fs.root() / "new_file.txt"
    stream = fs.write(vpath)
    try:
        stream.write(b"written via fs")
    finally:
        stream.close()
    result = fs.read(vpath)
    try:
        assert result.read(1024) == b"written via fs"
    finally:
        result.close()


def test_archive_fs_mkdir_creates_listable_dir(zip_archive_fs_write: ArchiveFilesystem) -> None:
    fs = zip_archive_fs_write
    vpath = fs.root() / "new_dir"
    fs.mkdir(vpath)
    entries = fs.iterdir(fs.root())
    assert any(v.path.name == "new_dir" for v in entries)


def test_archive_fs_remove_file_disappears_from_iterdir(zip_archive_fs_write: ArchiveFilesystem) -> None:
    fs = zip_archive_fs_write
    vpath = fs.root() / "dir1" / "file11.txt"
    fs.remove(vpath)
    entries = fs.iterdir(fs.root() / "dir1")
    assert not any(v.path.name == "file11.txt" for v in entries)


def test_archive_fs_rmdir_empty_dir_disappears(zip_archive_fs_write: ArchiveFilesystem) -> None:
    fs = zip_archive_fs_write
    vpath = fs.root() / "dir_empty"
    fs.rmdir(vpath)
    entries = fs.iterdir(fs.root())
    assert not any(v.path.name == "dir_empty" for v in entries)


def test_archive_fs_rename_content_accessible_under_new_name(zip_archive_fs_write: ArchiveFilesystem) -> None:
    fs = zip_archive_fs_write
    src = fs.root() / "dir1" / "file11.txt"
    dst = fs.root() / "dir1" / "renamed.txt"
    fs.rename(src, dst)
    result = fs.read(dst)
    try:
        assert result.read(1024) == _FILE11
    finally:
        result.close()


def test_archive_fs_rename_old_name_gone(zip_archive_fs_write: ArchiveFilesystem) -> None:
    fs = zip_archive_fs_write
    src = fs.root() / "dir1" / "file11.txt"
    dst = fs.root() / "dir1" / "renamed.txt"
    fs.rename(src, dst)
    with pytest.raises(FileNotFoundError):
        fs.read(src)


def test_archive_fs_tar_write_raises_unsupported_operation(tar_archive_fs: ArchiveFilesystem) -> None:
    with pytest.raises(io.UnsupportedOperation):
        tar_archive_fs.write(tar_archive_fs.root() / "new_file.txt")


def test_archive_fs_tar_remove_raises_unsupported_operation(tar_archive_fs: ArchiveFilesystem) -> None:
    with pytest.raises(io.UnsupportedOperation):
        tar_archive_fs.remove(tar_archive_fs.root() / "dir1" / "file11.txt")
```

- [ ] **Step 4: Run the new integration tests**

```
uv run pytest tests/archive/test_archives.py -k "archive_fs" -v
```

Expected: all pass.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] All test functions annotated `-> None`
- [ ] `zip_archive_fs_write` uses `LocalFilesystem.singleton().path(...)` pattern matching existing `tar_archive_fs`/`zip_archive_fs` fixtures

---

### Task 4: Final verification

- [ ] **Step 1: Run full test suite**

```
uv run pytest -v
```

Expected: zero failures. The pre-existing flaky test `tests/widgets/test_popup_widget.py::test_escape_key_does_nothing_when_close_on_escape_false` may fail intermittently — ignore if it was failing before this change.

- [ ] **Step 2: Run full QA**

```
uv run qa
```

Expected: zero failures in lint, type check, and tests.

- [ ] **Step 3: Confirm subprocess is the only new OS dependency**

```
grep -r "subprocess" src/nova_navigator/archive/
```

Expected: one match in `zip_archive.py` only.
