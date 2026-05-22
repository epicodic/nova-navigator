"""Unit tests for shell driver classes and quoting utilities."""

from __future__ import annotations

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
    result = _posix_octal_escape("/var/data")
    # Each char is escaped individually
    assert "\\0057" in result  # /
    assert "\\0166" in result  # v
    assert "\\0141" in result  # a
    assert "\\0162" in result  # r


def test_posix_octal_escape_empty_string() -> None:
    result = _posix_octal_escape("")
    assert result == ""


# ---------------------------------------------------------------------------
# ZshDriver
# ---------------------------------------------------------------------------


def test_zsh_driver_init_code_contains_kill_stop() -> None:
    driver = ZshDriver()
    code = driver.init_code()
    assert "kill -STOP $$" in code


def test_zsh_driver_init_code_uses_precmd_functions() -> None:
    driver = ZshDriver()
    code = driver.init_code()
    assert "precmd_functions" in code


def test_zsh_driver_init_code_ends_with_newline() -> None:
    driver = ZshDriver()
    code = driver.init_code()
    assert code.endswith("\n")


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
    cmd = driver.cd_command("/var/data")
    assert cmd.startswith("cd ")
    assert "$'" in cmd


def test_zsh_driver_supports_stop_resume() -> None:
    driver = ZshDriver()
    assert driver.supports_stop_resume is True


# ---------------------------------------------------------------------------
# BashDriver
# ---------------------------------------------------------------------------


def test_bash_driver_init_code_contains_kill_stop() -> None:
    driver = BashDriver()
    code = driver.init_code()
    assert "kill -STOP $$" in code


def test_bash_driver_init_code_uses_prompt_command() -> None:
    driver = BashDriver()
    code = driver.init_code()
    assert "PROMPT_COMMAND" in code


def test_bash_driver_init_code_ends_with_newline() -> None:
    driver = BashDriver()
    code = driver.init_code()
    assert code.endswith("\n")


def test_bash_driver_supports_stop_resume() -> None:
    driver = BashDriver()
    assert driver.supports_stop_resume is True


def test_bash_driver_quote_uses_ansi_c() -> None:
    driver = BashDriver()
    result = driver.quote("/var/data")
    assert result.startswith("$'")


def test_bash_driver_cd_command() -> None:
    driver = BashDriver()
    cmd = driver.cd_command("/var/log")
    assert cmd.startswith("cd ")


# ---------------------------------------------------------------------------
# FallbackDriver
# ---------------------------------------------------------------------------


def test_fallback_driver_supports_stop_resume_is_false() -> None:
    driver = FallbackDriver()
    assert driver.supports_stop_resume is False


def test_fallback_driver_init_code_ends_with_newline() -> None:
    driver = FallbackDriver()
    code = driver.init_code()
    assert code.endswith("\n")


def test_fallback_driver_cd_command_is_self_contained() -> None:
    driver = FallbackDriver()
    cmd = driver.cd_command("/var/data")
    # Must be a complete statement, not just 'cd <quoted>'
    assert "printf" in cmd
    assert "cd" in cmd


def test_fallback_driver_cd_command_does_not_start_with_cd() -> None:
    driver = FallbackDriver()
    cmd = driver.cd_command("/var/data")
    # FallbackDriver returns a multi-statement command, not 'cd ...'
    assert not cmd.startswith("cd ")


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


# ---------------------------------------------------------------------------
# OSC 7 emission — new init_code() signature (no precmd_fd argument)
# ---------------------------------------------------------------------------


def test_zsh_driver_init_code_takes_no_arguments() -> None:
    driver = ZshDriver()
    code = driver.init_code()  # must not raise TypeError
    assert isinstance(code, str)


def test_zsh_driver_init_code_contains_osc7_printf() -> None:
    driver = ZshDriver()
    code = driver.init_code()
    assert r"\033]7;file://%s\007" in code


def test_zsh_driver_init_code_stop_resume_true_contains_kill_stop() -> None:
    driver = ZshDriver(stop_resume=True)
    code = driver.init_code()
    assert "kill -STOP $$" in code


def test_zsh_driver_init_code_stop_resume_false_omits_kill_stop() -> None:
    driver = ZshDriver(stop_resume=False)
    code = driver.init_code()
    assert "kill -STOP $$" not in code


def test_zsh_driver_default_stop_resume_is_true() -> None:
    driver = ZshDriver()
    assert driver.supports_stop_resume is True


def test_bash_driver_init_code_takes_no_arguments() -> None:
    driver = BashDriver()
    code = driver.init_code()
    assert isinstance(code, str)


def test_bash_driver_init_code_contains_osc7_printf() -> None:
    driver = BashDriver()
    code = driver.init_code()
    assert r"\033]7;file://%s\007" in code


def test_bash_driver_init_code_stop_resume_false_omits_kill_stop() -> None:
    driver = BashDriver(stop_resume=False)
    code = driver.init_code()
    assert "kill -STOP $$" not in code


def test_fallback_driver_init_code_takes_no_arguments() -> None:
    driver = FallbackDriver()
    code = driver.init_code()
    assert isinstance(code, str)


def test_fallback_driver_init_code_contains_osc7_printf() -> None:
    driver = FallbackDriver()
    code = driver.init_code()
    assert r"\033]7;file://%s\007" in code


def test_fallback_driver_init_code_redirects_to_dev_tty() -> None:
    # FallbackDriver uses PS1 command substitution, so printf must redirect
    # to /dev/tty to avoid the OSC 7 sequence appearing in the prompt text.
    driver = FallbackDriver()
    code = driver.init_code()
    assert ">/dev/tty" in code


def test_detect_driver_zsh_stop_resume_false() -> None:
    driver = detect_driver("/usr/bin/zsh", stop_resume=False)
    assert isinstance(driver, ZshDriver)
    assert driver.supports_stop_resume is False


def test_detect_driver_bash_stop_resume_false() -> None:
    driver = detect_driver("/bin/bash", stop_resume=False)
    assert isinstance(driver, BashDriver)
    assert driver.supports_stop_resume is False


def test_detect_driver_fallback_stop_resume_kwarg_ignored() -> None:
    # FallbackDriver always has stop_resume=False regardless of kwarg
    driver = detect_driver("/bin/sh", stop_resume=True)
    assert isinstance(driver, FallbackDriver)
    assert driver.supports_stop_resume is False


def test_fallback_driver_constructor_always_sets_stop_resume_false() -> None:
    # Even if caller passes stop_resume=True, FallbackDriver forces False
    driver = FallbackDriver(stop_resume=True)
    assert driver.supports_stop_resume is False
