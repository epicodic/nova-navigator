from __future__ import annotations

from pathlib import Path

import pytest

from nova_navigator.remotes.known_hosts import hash_known_hosts_enabled


def _cfg(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Host *\n  HashKnownHosts yes\n", True),
        ("Host *\n  HashKnownHosts YES\n", True),
        ('Host *\n  HashKnownHosts "yes"\n', True),
        ("Host *\n  HashKnownHosts=yes\n", True),
        ("Host *\n  HashKnownHosts no\n", False),
        ("Host other\n  HashKnownHosts yes\n", False),
        ("Host h.example\n  HashKnownHosts no\nHost *\n  HashKnownHosts yes\n", False),
        ("Host *\n  HashKnownHosts yes\nHost h.example\n  HashKnownHosts no\n", True),
        ("User bob\n", False),
    ],
)
def test_effective_value_for_host(tmp_path: Path, text: str, expected: bool) -> None:
    assert hash_known_hosts_enabled("h.example", [_cfg(tmp_path, "c", text)]) is expected


def test_user_config_beats_system_config(tmp_path: Path) -> None:
    user = _cfg(tmp_path, "u", "Host *\n  HashKnownHosts no\n")
    system = _cfg(tmp_path, "s", "Host *\n  HashKnownHosts yes\n")
    assert hash_known_hosts_enabled("h.example", [user, system]) is False
    assert hash_known_hosts_enabled("h.example", [_cfg(tmp_path, "empty", "# nothing\n"), system]) is True


def test_missing_and_malformed_files_are_skipped(tmp_path: Path) -> None:
    good = _cfg(tmp_path, "g", "Host *\n  HashKnownHosts yes\n")
    bad = _cfg(tmp_path, "b", "this is not\x00 a valid = config line [\nHost\n")
    assert hash_known_hosts_enabled("h.example", [tmp_path / "missing", bad, good]) is True
    assert hash_known_hosts_enabled("h.example", [tmp_path / "missing"]) is False


def test_include_is_not_followed(tmp_path: Path) -> None:
    extra = _cfg(tmp_path, "extra.conf", "Host *\n  HashKnownHosts yes\n")
    main = _cfg(tmp_path, "main", f"Include {extra}\n")
    assert hash_known_hosts_enabled("h.example", [main]) is False  # documented limitation


@pytest.mark.parametrize(
    "match_line",
    [
        'Match exec "touch {marker}"',
        'Match host h.example exec "touch {marker}"',
        "  match   EXEC 'touch {marker}'",
        '\x0cMatch exec "touch {marker}"',
        '\x0bMatch exec "touch {marker}"',
        '\u00a0Match exec "touch {marker}"',
        '\u2003Match exec "touch {marker}"',
        '\rMatch exec "touch {marker}"',
        'Match=exec "touch {marker}"',
        'Match exec "touch {marker}"\r',
        'Match "exec" "touch {marker}"',
    ],
)
def test_match_exec_never_runs_and_block_does_not_apply(tmp_path: Path, match_line: str) -> None:
    marker = tmp_path / "ran"
    config = _cfg(tmp_path, "c", match_line.format(marker=marker) + "\n  HashKnownHosts yes\n")
    assert hash_known_hosts_enabled("h.example", [config]) is False
    assert not marker.exists()


def test_match_host_still_works(tmp_path: Path) -> None:
    config = _cfg(tmp_path, "c", "Match host h.example\n  HashKnownHosts yes\n")
    assert hash_known_hosts_enabled("h.example", [config]) is True
