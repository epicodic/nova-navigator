"""Unit tests for shell driver classes and quoting utilities."""

from __future__ import annotations

from pathlib import PurePath

from nova_navigator.terminal.shell_driver import (
    BashDriver,
    FallbackDriver,
    ZshDriver,
    _ansi_c_quote,
    _posix_octal_escape,
    detect_driver,
)

# ---------------------------------------------------------------------------
# _ansi_c_quote
# ---------------------------------------------------------------------------


def test_ansi_c_quote_simple_path_preserves_safe_chars() -> None:
    result = _ansi_c_quote("/home/user/projects")
    assert result == "$'/home/user/projects'"


def test_ansi_c_quote_escapes_single_quote() -> None:
    result = _ansi_c_quote("O'Brien")
    # Single quote (0x27 = octal 047) must be escaped
    assert "\\047" in result
    assert result.startswith("$'")
    assert result.endswith("'")


def test_ansi_c_quote_escapes_space() -> None:
    result = _ansi_c_quote("my dir")
    # Space (0x20 = octal 040) must be escaped
    assert "\\040" in result


def test_ansi_c_quote_escapes_backslash() -> None:
    result = _ansi_c_quote("back\\slash")
    # Backslash (0x5C = octal 134) must be escaped
    assert "\\134" in result


def test_ansi_c_quote_empty_string() -> None:
    result = _ansi_c_quote("")
    assert result == "$''"


def test_ansi_c_quote_preserves_safe_characters() -> None:
    safe = "abcABC012/._-"
    result = _ansi_c_quote(safe)
    # All safe chars should appear literally
    assert result == f"$'{safe}'"


def test_ansi_c_quote_escapes_newline() -> None:
    result = _ansi_c_quote("/home/user/line\nbreak")
    # Newline (0x0A = octal 012) must be escaped
    assert "\\012" in result


def test_ansi_c_quote_long_path_gets_line_continuation() -> None:
    # 300 chars of safe content — should trigger at least one continuation
    long_path = "/home/" + "a" * 294
    result = _ansi_c_quote(long_path)
    assert "\\\n" in result


# ---------------------------------------------------------------------------
# _posix_octal_escape
# ---------------------------------------------------------------------------


def test_posix_octal_escape_slash() -> None:
    # / = decimal 47 = octal 057 → \0057
    result = _posix_octal_escape("/")
    assert result == "\\0057"


def test_posix_octal_escape_simple_path() -> None:
    result = _posix_octal_escape("/tmp")  # noqa: S108
    # Each char is escaped individually
    assert "\\0057" in result  # /
    assert "\\0164" in result  # t
    assert "\\0155" in result  # m
    assert "\\0160" in result  # p


def test_posix_octal_escape_empty_string() -> None:
    result = _posix_octal_escape("")
    assert result == ""


# ---------------------------------------------------------------------------
# ZshDriver
# ---------------------------------------------------------------------------


def test_zsh_driver_init_code_embeds_fd() -> None:
    driver = ZshDriver()
    code = driver.init_code(7)
    assert ">&7" in code


def test_zsh_driver_init_code_contains_kill_stop() -> None:
    driver = ZshDriver()
    code = driver.init_code(7)
    assert "kill -STOP $$" in code


def test_zsh_driver_init_code_uses_precmd_functions() -> None:
    driver = ZshDriver()
    code = driver.init_code(5)
    assert "precmd_functions" in code


def test_zsh_driver_init_code_ends_with_newline() -> None:
    driver = ZshDriver()
    code = driver.init_code(3)
    assert code.endswith("\n")


def test_zsh_driver_init_code_prints_pid_and_pwd() -> None:
    driver = ZshDriver()
    code = driver.init_code(3)
    assert "$$" in code
    assert "pwd" in code


def test_zsh_driver_quote_simple_path() -> None:
    driver = ZshDriver()
    result = driver.quote("/home/user")
    assert result == "$'/home/user'"


def test_zsh_driver_quote_special_chars() -> None:
    driver = ZshDriver()
    result = driver.quote("O'Brien")
    assert "\\047" in result


def test_zsh_driver_cd_command() -> None:
    driver = ZshDriver()
    cmd = driver.cd_command("/tmp")  # noqa: S108
    assert cmd.startswith("cd ")
    assert "$'" in cmd


def test_zsh_driver_supports_stop_resume() -> None:
    driver = ZshDriver()
    assert driver.supports_stop_resume is True


def test_zsh_driver_parse_precmd_payload_normal() -> None:
    driver = ZshDriver()
    pid, cwd = driver.parse_precmd_payload("12345:/home/user\n")
    assert pid == 12345
    assert cwd == PurePath("/home/user")


def test_zsh_driver_parse_precmd_payload_strips_whitespace() -> None:
    driver = ZshDriver()
    pid, cwd = driver.parse_precmd_payload("  99:/var/log  \n")
    assert pid == 99
    assert cwd == PurePath("/var/log")


def test_zsh_driver_parse_precmd_payload_malformed_returns_fallback() -> None:
    driver = ZshDriver()
    pid, cwd = driver.parse_precmd_payload("garbage data\n")
    assert pid is None
    assert cwd == PurePath("/")


# ---------------------------------------------------------------------------
# BashDriver
# ---------------------------------------------------------------------------


def test_bash_driver_init_code_embeds_fd() -> None:
    driver = BashDriver()
    code = driver.init_code(5)
    assert ">&5" in code


def test_bash_driver_init_code_contains_kill_stop() -> None:
    driver = BashDriver()
    code = driver.init_code(5)
    assert "kill -STOP $$" in code


def test_bash_driver_init_code_uses_prompt_command() -> None:
    driver = BashDriver()
    code = driver.init_code(5)
    assert "PROMPT_COMMAND" in code


def test_bash_driver_init_code_ends_with_newline() -> None:
    driver = BashDriver()
    code = driver.init_code(5)
    assert code.endswith("\n")


def test_bash_driver_supports_stop_resume() -> None:
    driver = BashDriver()
    assert driver.supports_stop_resume is True


def test_bash_driver_quote_uses_ansi_c() -> None:
    driver = BashDriver()
    result = driver.quote("/tmp/test")  # noqa: S108
    assert result.startswith("$'")


def test_bash_driver_cd_command() -> None:
    driver = BashDriver()
    cmd = driver.cd_command("/var/log")
    assert cmd.startswith("cd ")


def test_bash_driver_parse_precmd_payload() -> None:
    driver = BashDriver()
    pid, cwd = driver.parse_precmd_payload("9999:/opt/app\n")
    assert pid == 9999
    assert cwd == PurePath("/opt/app")


# ---------------------------------------------------------------------------
# FallbackDriver
# ---------------------------------------------------------------------------


def test_fallback_driver_supports_stop_resume_is_false() -> None:
    driver = FallbackDriver()
    assert driver.supports_stop_resume is False


def test_fallback_driver_init_code_with_fd() -> None:
    driver = FallbackDriver()
    code = driver.init_code(4)
    assert ">&4" in code
    assert "kill" not in code


def test_fallback_driver_init_code_without_fd() -> None:
    driver = FallbackDriver()
    code = driver.init_code(None)
    assert code == ""


def test_fallback_driver_init_code_ends_with_newline() -> None:
    driver = FallbackDriver()
    code = driver.init_code(4)
    assert code.endswith("\n")


def test_fallback_driver_cd_command_is_self_contained() -> None:
    driver = FallbackDriver()
    cmd = driver.cd_command("/tmp/test")  # noqa: S108
    # Must be a complete statement, not just 'cd <quoted>'
    assert "printf" in cmd
    assert "cd" in cmd


def test_fallback_driver_cd_command_does_not_start_with_cd() -> None:
    driver = FallbackDriver()
    cmd = driver.cd_command("/tmp/test")  # noqa: S108
    # FallbackDriver returns a multi-statement command, not 'cd ...'
    assert not cmd.startswith("cd ")


def test_fallback_driver_parse_precmd_payload() -> None:
    driver = FallbackDriver()
    pid, cwd = driver.parse_precmd_payload("/home/user\n")
    assert pid is None
    assert cwd == PurePath("/home/user")


def test_fallback_driver_parse_precmd_payload_strips_whitespace() -> None:
    driver = FallbackDriver()
    pid, cwd = driver.parse_precmd_payload("  /var/log  \n")
    assert pid is None
    assert cwd == PurePath("/var/log")


# ---------------------------------------------------------------------------
# detect_driver
# ---------------------------------------------------------------------------


def test_detect_driver_zsh() -> None:
    driver = detect_driver("/usr/bin/zsh")
    assert isinstance(driver, ZshDriver)


def test_detect_driver_bash() -> None:
    driver = detect_driver("/bin/bash")
    assert isinstance(driver, BashDriver)


def test_detect_driver_sh() -> None:
    driver = detect_driver("/bin/sh")
    assert isinstance(driver, FallbackDriver)


def test_detect_driver_unknown_shell() -> None:
    driver = detect_driver("/usr/bin/fish")
    assert isinstance(driver, FallbackDriver)


def test_detect_driver_command_with_arguments() -> None:
    driver = detect_driver("/usr/bin/zsh --no-rcs")
    assert isinstance(driver, ZshDriver)
