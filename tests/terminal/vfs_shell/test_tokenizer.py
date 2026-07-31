"""Tests for shell tokenizer."""

from __future__ import annotations

from nova_navigator.terminal.vfs_shell.tokenizer import Token, tokenize


def test_simple_words() -> None:
    result = tokenize("ls -la /home")
    assert result == [Token("ls", False), Token("-la", False), Token("/home", False)]


def test_single_quoted_no_glob() -> None:
    result = tokenize("rm '*.log'")
    assert result == [Token("rm", False), Token("*.log", True)]


def test_double_quoted_no_glob() -> None:
    result = tokenize('echo "hello world"')
    assert result == [Token("echo", False), Token("hello world", True)]


def test_backslash_escape() -> None:
    result = tokenize(r"cat file\ name.txt")
    assert result == [Token("cat", False), Token("file name.txt", True)]


def test_unquoted_glob_not_marked_quoted() -> None:
    result = tokenize("ls *.py")
    assert result == [Token("ls", False), Token("*.py", False)]


def test_empty_string() -> None:
    result = tokenize("")
    assert result == []


def test_mixed_quoting() -> None:
    result = tokenize("cp 'source file' dest")
    assert result == [Token("cp", False), Token("source file", True), Token("dest", False)]


def test_equals_in_option() -> None:
    result = tokenize("ls --sort=size")
    assert result == [Token("ls", False), Token("--sort=size", False)]
