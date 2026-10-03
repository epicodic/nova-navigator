"""Tests for FileProvider protocol and implementations."""

from pathlib import Path, PurePath

from nova_widgets.file_provider import (
    InMemoryFileProvider,
    LocalFileProvider,
    default_file_provider,
)

# --- FileStat protocol tests (using LocalFileProvider) ---


class TestFileStatProtocol:
    """Tests for FileStat protocol: name, is_dir, is_file, is_hidden."""

    def test_file_stat_has_name_property(self, tmp_path: Path) -> None:
        """FileStat entry has name property."""
        (tmp_path / "testfile.txt").touch()
        provider = LocalFileProvider()
        entries = list(provider.iterdir(PurePath(tmp_path)))
        assert len(entries) >= 1
        stat = entries[0]
        assert hasattr(stat, "name")
        assert isinstance(stat.name, str)

    def test_file_stat_has_is_dir_property(self, tmp_path: Path) -> None:
        """FileStat entry has is_dir property."""
        (tmp_path / "testfile.txt").touch()
        provider = LocalFileProvider()
        entries = list(provider.iterdir(PurePath(tmp_path)))
        stat = entries[0]
        assert hasattr(stat, "is_dir")
        assert isinstance(stat.is_dir, bool)

    def test_file_stat_has_is_file_property(self, tmp_path: Path) -> None:
        """FileStat entry has is_file property."""
        (tmp_path / "testfile.txt").touch()
        provider = LocalFileProvider()
        entries = list(provider.iterdir(PurePath(tmp_path)))
        stat = entries[0]
        assert hasattr(stat, "is_file")
        assert isinstance(stat.is_file, bool)

    def test_file_stat_has_is_hidden_property(self, tmp_path: Path) -> None:
        """FileStat entry has is_hidden property."""
        (tmp_path / "testfile.txt").touch()
        provider = LocalFileProvider()
        entries = list(provider.iterdir(PurePath(tmp_path)))
        stat = entries[0]
        assert hasattr(stat, "is_hidden")
        assert isinstance(stat.is_hidden, bool)


# --- LocalFileProvider tests ---


class TestLocalFileProviderHome:
    """Tests for LocalFileProvider.home()."""

    def test_home_returns_pure_path(self) -> None:
        """home() returns a PurePath."""
        provider = LocalFileProvider()
        home = provider.home()
        assert isinstance(home, PurePath)

    def test_home_returns_real_path_object(self) -> None:
        """Amendment C1: home() returns real pathlib.Path, not just PurePath."""
        provider = LocalFileProvider()
        home = provider.home()
        assert isinstance(home, Path)

    def test_home_is_absolute(self) -> None:
        """home() returns an absolute path."""
        provider = LocalFileProvider()
        home = provider.home()
        assert home.is_absolute()


class TestLocalFileProviderIterdir:
    """Tests for LocalFileProvider.iterdir()."""

    def test_iterdir_lists_files_and_dirs(self, tmp_path: Path) -> None:
        """iterdir() lists files and directories."""
        (tmp_path / "file.txt").touch()
        (tmp_path / "subdir").mkdir()
        provider = LocalFileProvider()
        entries = list(provider.iterdir(PurePath(tmp_path)))
        names = {entry.name for entry in entries}
        assert "file.txt" in names
        assert "subdir" in names

    def test_iterdir_file_stat_name(self, tmp_path: Path) -> None:
        """iterdir() entries have correct name property."""
        (tmp_path / "file.txt").touch()
        provider = LocalFileProvider()
        entries = list(provider.iterdir(PurePath(tmp_path)))
        file_entry = next(e for e in entries if e.name == "file.txt")
        assert file_entry.name == "file.txt"

    def test_iterdir_file_stat_is_file(self, tmp_path: Path) -> None:
        """iterdir() entries correctly identify files."""
        (tmp_path / "file.txt").touch()
        provider = LocalFileProvider()
        entries = list(provider.iterdir(PurePath(tmp_path)))
        file_entry = next(e for e in entries if e.name == "file.txt")
        assert file_entry.is_file is True
        assert file_entry.is_dir is False

    def test_iterdir_dir_stat_is_dir(self, tmp_path: Path) -> None:
        """iterdir() entries correctly identify directories."""
        (tmp_path / "subdir").mkdir()
        provider = LocalFileProvider()
        entries = list(provider.iterdir(PurePath(tmp_path)))
        dir_entry = next(e for e in entries if e.name == "subdir")
        assert dir_entry.is_dir is True
        assert dir_entry.is_file is False

    def test_iterdir_hidden_file_marked_as_hidden(self, tmp_path: Path) -> None:
        """iterdir() correctly identifies hidden files (starting with .)."""
        (tmp_path / ".hidden").touch()
        provider = LocalFileProvider()
        entries = list(provider.iterdir(PurePath(tmp_path)))
        hidden = next(e for e in entries if e.name == ".hidden")
        assert hidden.is_hidden is True

    def test_iterdir_normal_file_not_hidden(self, tmp_path: Path) -> None:
        """iterdir() correctly identifies non-hidden files."""
        (tmp_path / "normal.txt").touch()
        provider = LocalFileProvider()
        entries = list(provider.iterdir(PurePath(tmp_path)))
        normal = next(e for e in entries if e.name == "normal.txt")
        assert normal.is_hidden is False

    def test_iterdir_returns_iterator(self, tmp_path: Path) -> None:
        """iterdir() returns an Iterator."""
        provider = LocalFileProvider()
        result = provider.iterdir(PurePath(tmp_path))
        assert hasattr(result, "__iter__")
        assert hasattr(result, "__next__")


class TestLocalFileProviderIsDir:
    """Tests for LocalFileProvider.is_dir()."""

    def test_is_dir_true_for_directory(self, tmp_path: Path) -> None:
        """is_dir() returns True for directories."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        provider = LocalFileProvider()
        assert provider.is_dir(PurePath(subdir)) is True

    def test_is_dir_false_for_file(self, tmp_path: Path) -> None:
        """is_dir() returns False for regular files."""
        file = tmp_path / "file.txt"
        file.touch()
        provider = LocalFileProvider()
        assert provider.is_dir(PurePath(file)) is False

    def test_is_dir_false_for_nonexistent(self, tmp_path: Path) -> None:
        """is_dir() returns False for non-existent paths."""
        provider = LocalFileProvider()
        assert provider.is_dir(PurePath(tmp_path / "nonexistent")) is False


class TestLocalFileProviderIsFile:
    """Tests for LocalFileProvider.is_file()."""

    def test_is_file_true_for_file(self, tmp_path: Path) -> None:
        """is_file() returns True for regular files."""
        file = tmp_path / "file.txt"
        file.touch()
        provider = LocalFileProvider()
        assert provider.is_file(PurePath(file)) is True

    def test_is_file_false_for_directory(self, tmp_path: Path) -> None:
        """is_file() returns False for directories."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        provider = LocalFileProvider()
        assert provider.is_file(PurePath(subdir)) is False

    def test_is_file_false_for_nonexistent(self, tmp_path: Path) -> None:
        """is_file() returns False for non-existent paths."""
        provider = LocalFileProvider()
        assert provider.is_file(PurePath(tmp_path / "nonexistent")) is False


class TestLocalFileProviderParent:
    """Tests for LocalFileProvider.parent()."""

    def test_parent_returns_pure_path(self, tmp_path: Path) -> None:
        """parent() returns a PurePath."""
        provider = LocalFileProvider()
        result = provider.parent(PurePath(tmp_path / "child"))
        assert isinstance(result, PurePath)

    def test_parent_returns_real_path_object(self, tmp_path: Path) -> None:
        """Amendment C1: parent() returns real pathlib.Path."""
        provider = LocalFileProvider()
        result = provider.parent(PurePath(tmp_path / "child"))
        assert isinstance(result, Path)

    def test_parent_of_nested_path(self, tmp_path: Path) -> None:
        """parent() returns the parent directory."""
        nested = tmp_path / "a" / "b" / "c"
        provider = LocalFileProvider()
        result = provider.parent(PurePath(nested))
        assert result == PurePath(tmp_path / "a" / "b")

    def test_parent_of_root_is_root(self) -> None:
        """parent() of root returns root."""
        provider = LocalFileProvider()
        root = PurePath("/")
        result = provider.parent(root)
        assert result == root


class TestLocalFileProviderJoinpath:
    """Tests for LocalFileProvider.joinpath()."""

    def test_joinpath_returns_pure_path(self, tmp_path: Path) -> None:
        """joinpath() returns a PurePath."""
        provider = LocalFileProvider()
        result = provider.joinpath(PurePath(tmp_path), "child")
        assert isinstance(result, PurePath)

    def test_joinpath_returns_real_path_object(self, tmp_path: Path) -> None:
        """Amendment C1: joinpath() returns real pathlib.Path."""
        provider = LocalFileProvider()
        result = provider.joinpath(PurePath(tmp_path), "child")
        assert isinstance(result, Path)

    def test_joinpath_creates_correct_path(self, tmp_path: Path) -> None:
        """joinpath() correctly joins path components."""
        provider = LocalFileProvider()
        result = provider.joinpath(PurePath(tmp_path), "child")
        assert result == PurePath(tmp_path / "child")

    def test_joinpath_with_multiple_segments(self, tmp_path: Path) -> None:
        """joinpath() joins a single name segment."""
        provider = LocalFileProvider()
        result = provider.joinpath(PurePath(tmp_path) / "a" / "b", "c")
        assert result == PurePath(tmp_path / "a" / "b" / "c")


class TestLocalFileProviderResolve:
    """Tests for LocalFileProvider.resolve()."""

    def test_resolve_returns_pure_path_or_none(self, tmp_path: Path) -> None:
        """resolve() returns a PurePath or None."""
        provider = LocalFileProvider()
        result = provider.resolve(PurePath(tmp_path))
        assert result is None or isinstance(result, PurePath)

    def test_resolve_returns_real_path_object_or_none(self, tmp_path: Path) -> None:
        """Amendment C1: resolve() returns real pathlib.Path or None."""
        provider = LocalFileProvider()
        result = provider.resolve(PurePath(tmp_path))
        assert result is None or isinstance(result, Path)

    def test_resolve_existing_directory(self, tmp_path: Path) -> None:
        """resolve() returns the path for existing directories."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        provider = LocalFileProvider()
        result = provider.resolve(PurePath(subdir))
        assert result is not None
        assert result == PurePath(subdir)

    def test_resolve_existing_file(self, tmp_path: Path) -> None:
        """resolve() returns the path for existing files."""
        file = tmp_path / "file.txt"
        file.touch()
        provider = LocalFileProvider()
        result = provider.resolve(PurePath(file))
        assert result is not None
        assert result == PurePath(file)

    def test_resolve_nonexistent_file_in_existing_dir(self, tmp_path: Path) -> None:
        """Amendment A3: resolve() accepts non-existent files in existing dirs."""
        provider = LocalFileProvider()
        nonexistent = PurePath(tmp_path / "nonexistent.txt")
        result = provider.resolve(nonexistent)
        assert result is not None
        assert result == nonexistent

    def test_resolve_nonexistent_dir_returns_none(self, tmp_path: Path) -> None:
        """resolve() returns None when parent dir doesn't exist."""
        provider = LocalFileProvider()
        nonexistent = PurePath(tmp_path / "nonexistent" / "file.txt")
        result = provider.resolve(nonexistent)
        assert result is None


# --- InMemoryFileProvider tests ---


class TestInMemoryFileProviderBasics:
    """Tests for InMemoryFileProvider basic operations."""

    def test_can_create_in_memory_provider(self) -> None:
        """Can instantiate InMemoryFileProvider."""
        provider = InMemoryFileProvider()
        assert provider is not None

    def test_home_returns_pure_path(self) -> None:
        """home() returns a PurePath."""
        provider = InMemoryFileProvider()
        result = provider.home()
        assert isinstance(result, PurePath)

    def test_add_dir_creates_directory(self) -> None:
        """add_dir() creates a directory entry."""
        provider = InMemoryFileProvider()
        provider.add_dir("/home/user")
        assert provider.is_dir(PurePath("/home/user"))

    def test_add_file_creates_file(self) -> None:
        """add_file() creates a file entry."""
        provider = InMemoryFileProvider()
        provider.add_dir("/home/user")
        provider.add_file("/home/user/test.txt")
        assert provider.is_file(PurePath("/home/user/test.txt"))

    def test_iterdir_lists_added_entries(self) -> None:
        """iterdir() lists added files and directories."""
        provider = InMemoryFileProvider()
        provider.add_dir("/home")
        provider.add_file("/home/test.txt")
        provider.add_dir("/home/subdir")
        entries = list(provider.iterdir(PurePath("/home")))
        names = {entry.name for entry in entries}
        assert "test.txt" in names
        assert "subdir" in names

    def test_is_dir_identifies_directories(self) -> None:
        """is_dir() correctly identifies directories."""
        provider = InMemoryFileProvider()
        provider.add_dir("/home")
        provider.add_dir("/home/subdir")
        assert provider.is_dir(PurePath("/home/subdir"))

    def test_is_file_identifies_files(self) -> None:
        """is_file() correctly identifies files."""
        provider = InMemoryFileProvider()
        provider.add_dir("/home")
        provider.add_file("/home/test.txt")
        assert provider.is_file(PurePath("/home/test.txt"))

    def test_parent_navigates_up(self) -> None:
        """parent() returns the parent directory."""
        provider = InMemoryFileProvider()
        result = provider.parent(PurePath("/home/user/documents"))
        assert result == PurePath("/home/user")

    def test_joinpath_combines_paths(self) -> None:
        """joinpath() combines path components."""
        provider = InMemoryFileProvider()
        result = provider.joinpath(PurePath("/home"), "user")
        assert result == PurePath("/home/user")

    def test_hidden_file_marked_as_hidden(self) -> None:
        """add_file() entries starting with . are hidden."""
        provider = InMemoryFileProvider()
        provider.add_dir("/home")
        provider.add_file("/home/.hidden")
        entries = list(provider.iterdir(PurePath("/home")))
        hidden_entry = next(e for e in entries if e.name == ".hidden")
        assert hidden_entry.is_hidden is True

    def test_resolve_existing_path(self) -> None:
        """resolve() returns existing paths."""
        provider = InMemoryFileProvider()
        provider.add_dir("/home")
        result = provider.resolve(PurePath("/home"))
        assert result == PurePath("/home")

    def test_resolve_nonexistent_file_in_existing_dir(self) -> None:
        """Amendment A3: resolve() accepts non-existent files in existing dirs."""
        provider = InMemoryFileProvider()
        provider.add_dir("/home")
        result = provider.resolve(PurePath("/home/nonexistent.txt"))
        assert result == PurePath("/home/nonexistent.txt")

    def test_resolve_nonexistent_dir_returns_none(self) -> None:
        """resolve() returns None when parent dir doesn't exist."""
        provider = InMemoryFileProvider()
        result = provider.resolve(PurePath("/nonexistent/file.txt"))
        assert result is None


# --- default_file_provider tests ---


class TestDefaultFileProvider:
    """Tests for default_file_provider() function."""

    def test_default_file_provider_returns_provider(self) -> None:
        """default_file_provider() returns a FileProvider."""
        provider = default_file_provider()
        assert provider is not None

    def test_default_file_provider_is_singleton(self) -> None:
        """default_file_provider() returns the same instance."""
        provider1 = default_file_provider()
        provider2 = default_file_provider()
        assert provider1 is provider2

    def test_default_file_provider_is_local_provider(self) -> None:
        """default_file_provider() returns a LocalFileProvider."""
        provider = default_file_provider()
        assert isinstance(provider, LocalFileProvider)
