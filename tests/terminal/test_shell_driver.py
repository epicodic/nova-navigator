"""Unit tests for shell driver classes and quoting utilities."""

from __future__ import annotations

import shlex

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


def test_ansi_c_quote_escapes_non_ascii_by_utf8_byte_not_codepoint() -> None:
    # U+00A6 BROKEN BAR encodes as two UTF-8 bytes: 0xC2 0xA6 (octal 302, 246).
    # Escaping by codepoint alone (ord() == 166 == octal 246) would emit a lone
    # \246, which is not valid UTF-8 and makes the shell's cd target diverge
    # from the real filename on disk.
    result = _ansi_c_quote("foo ¦ bar")
    assert "\\302\\246" in result
    assert "\\246" not in result.replace("\\302\\246", "")


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


def test_posix_octal_escape_non_ascii_by_utf8_byte_not_codepoint() -> None:
    # Same UTF-8-byte requirement as _ansi_c_quote: U+00A6 must become the two
    # bytes 0xC2 0xA6 (octal 302, 246), not a single \0246 for the codepoint.
    result = _posix_octal_escape("¦")
    assert result == "\\0302\\0246"


# ---------------------------------------------------------------------------
# ZshDriver
# ---------------------------------------------------------------------------


def test_zsh_driver_init_code_does_not_contain_kill_stop() -> None:
    driver = ZshDriver()
    code = driver.init_code()
    assert "kill -STOP $$" not in code


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


# ---------------------------------------------------------------------------
# BashDriver
# ---------------------------------------------------------------------------


def test_bash_driver_init_code_does_not_contain_kill_stop() -> None:
    driver = BashDriver()
    code = driver.init_code()
    assert "kill -STOP $$" not in code


def test_bash_driver_init_code_uses_prompt_command() -> None:
    driver = BashDriver()
    code = driver.init_code()
    assert "PROMPT_COMMAND" in code


def test_bash_driver_init_code_ends_with_newline() -> None:
    driver = BashDriver()
    code = driver.init_code()
    assert code.endswith("\n")


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


def test_fallback_driver_init_code_ends_with_newline() -> None:
    driver = FallbackDriver()
    code = driver.init_code()
    assert code.endswith("\n")


def test_fallback_driver_init_code_sets_erase_to_backspace() -> None:
    assert "stty erase '^H'" in FallbackDriver().init_code()


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


def test_detect_driver_handles_quoted_path_with_space() -> None:
    driver = detect_driver(shlex.quote("/opt/my shells/zsh"))
    assert isinstance(driver, ZshDriver)


# ---------------------------------------------------------------------------
# kill_line_sequence / yank_sequence
# ---------------------------------------------------------------------------


def test_bash_driver_kill_line_sequence_enters_insert_mode_before_stashing() -> None:
    """The stash function is bound in the emacs and vi insert keymaps only, so vi
    command mode is left first."""
    driver = BashDriver()
    seq = driver.kill_line_sequence()
    assert seq.startswith(b"\x1b[95~")  # insert mode
    assert seq.endswith(b"\x1b[96~")  # stash


def test_bash_driver_kill_line_sequence_sends_only_private_keys() -> None:
    """No text is typed and no readline kill is used, so the kill ring stays as it was."""
    driver = BashDriver()
    seq = driver.kill_line_sequence()
    assert seq == b"\x1b[95~\x1b[96~"


def test_bash_driver_yank_sequence_sends_only_private_keys() -> None:
    driver = BashDriver()
    seq = driver.yank_sequence()
    assert seq == b"\x1b[95~\x1b[94~"


def test_zsh_driver_kill_line_sequence_uses_private_raw_eol_not_real_ctrl_e() -> None:
    """Real Ctrl+E (end-of-line) is one of zsh-autosuggestions' default
    accept-widgets: sending it while a suggestion is showing silently accepts
    the suggestion into the real buffer.  The private stash widget sets the
    buffer itself, so no such key is sent."""
    driver = ZshDriver()
    seq = driver.kill_line_sequence()
    assert b"\x05" not in seq  # never the real Ctrl+E
    assert seq.startswith(b"\x1b[95~")


def test_zsh_driver_kill_line_sequence_sends_only_private_keys() -> None:
    driver = ZshDriver()
    seq = driver.kill_line_sequence()
    assert seq == b"\x1b[95~\x1b[96~\x1b[93~\x1b[92~\x1b[95~\x1b[96~"


def test_zsh_driver_yank_sequence_sends_only_the_private_restore_key() -> None:
    """No Ctrl+E: it would risk accepting a new autosuggestion formed from the
    just-restored text."""
    driver = ZshDriver()
    seq = driver.yank_sequence()
    assert seq == b"\x1b[94~"


def test_zsh_driver_init_code_defines_stash_widget_bound_to_private_sequence() -> None:
    """The private sequence kill_line_sequence() sends must be bound in
    init_code() to a widget that clears the whole buffer."""
    code = ZshDriver().init_code()
    assert "BUFFER=; " in code
    assert "zle -N _nn_stash" in code
    assert r"\e[96~" in code


def test_fallback_driver_does_not_override_kill_or_yank_sequence() -> None:
    """FallbackDriver has no line editor (supports_line_editing is False) and never
    reaches hidden navigation's kill/yank step, so it relies on the base class."""
    assert "kill_line_sequence" not in FallbackDriver.__dict__
    assert "yank_sequence" not in FallbackDriver.__dict__


# ---------------------------------------------------------------------------
# supports_line_editing
# ---------------------------------------------------------------------------


def test_zsh_driver_supports_line_editing_is_true() -> None:
    driver = ZshDriver()
    assert driver.supports_line_editing is True


def test_bash_driver_supports_line_editing_is_true() -> None:
    driver = BashDriver()
    assert driver.supports_line_editing is True


def test_fallback_driver_supports_line_editing_is_false() -> None:
    driver = FallbackDriver()
    assert driver.supports_line_editing is False


# ---------------------------------------------------------------------------
# supports_editor_protocol
# ---------------------------------------------------------------------------


def test_zsh_driver_supports_editor_protocol_is_false() -> None:
    driver = ZshDriver()
    assert driver.supports_editor_protocol is False


def test_bash_driver_supports_editor_protocol_is_false() -> None:
    driver = BashDriver()
    assert driver.supports_editor_protocol is False


def test_fallback_driver_supports_editor_protocol_is_false() -> None:
    driver = FallbackDriver()
    assert driver.supports_editor_protocol is False


# ---------------------------------------------------------------------------
# _hook_body panel embedding (OSC 7 format)
# ---------------------------------------------------------------------------


def test_hook_body_includes_panel_prefix() -> None:
    """_hook_body() printf must include panel= prefix for from_nn detection."""
    driver = ZshDriver()
    body = driver._hook_body()
    assert "panel=" in body


def test_hook_body_osc7_format_has_panel_prefix() -> None:
    """_hook_body() must emit panel=;file:// format so the parser can detect NN origin."""
    driver = ZshDriver()
    body = driver._hook_body()
    assert "panel=" in body
    assert "file://" in body


def test_hook_body_does_not_include_nn_panel_variable() -> None:
    """_hook_body() must NOT reference $_NN_PANEL (removed in no-SIGSTOP redesign)."""
    driver = ZshDriver()
    body = driver._hook_body()
    assert "_NN_PANEL" not in body


def test_bash_hook_body_does_not_include_nn_panel_variable() -> None:
    driver = BashDriver()
    body = driver._hook_body()
    assert "_NN_PANEL" not in body


def test_hook_body_does_not_contain_kill_stop() -> None:
    """_hook_body() must not contain kill -STOP (removed in no-SIGSTOP redesign)."""
    driver = ZshDriver()
    body = driver._hook_body()
    assert "kill -STOP" not in body


def test_zsh_driver_init_code_has_no_prompt_marker_or_zle_hook() -> None:
    code = ZshDriver().init_code()
    assert "133" not in code
    assert "zle-line-init" not in code
    assert "add-zle-hook-widget" not in code


def test_bash_driver_init_code_has_no_prompt_marker() -> None:
    code = BashDriver().init_code()
    assert "133" not in code
    assert "PS1" not in code


def test_bash_driver_init_code_ends_with_prompt_command_and_newline() -> None:
    code = BashDriver().init_code()
    assert code.rstrip("\n").endswith("_nn_precmd")
    assert code.endswith("\n")


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
    assert r"\033]7;panel=;file://%s\007" in code


def test_bash_driver_init_code_takes_no_arguments() -> None:
    driver = BashDriver()
    code = driver.init_code()
    assert isinstance(code, str)


def test_bash_driver_init_code_contains_osc7_printf() -> None:
    driver = BashDriver()
    code = driver.init_code()
    assert r"\033]7;panel=;file://%s\007" in code


def test_fallback_driver_init_code_takes_no_arguments() -> None:
    driver = FallbackDriver()
    code = driver.init_code()
    assert isinstance(code, str)


def test_fallback_driver_init_code_contains_osc7_printf() -> None:
    driver = FallbackDriver()
    code = driver.init_code()
    assert r"\033]7;panel=;file://%s\007" in code


def test_fallback_driver_init_code_redirects_to_dev_tty() -> None:
    # FallbackDriver uses PS1 command substitution, so printf must redirect
    # to /dev/tty to avoid the OSC 7 sequence appearing in the prompt text.
    driver = FallbackDriver()
    code = driver.init_code()
    assert ">/dev/tty" in code


def test_detect_driver_zsh_returns_zsh_driver() -> None:
    driver = detect_driver("/usr/bin/zsh")
    assert isinstance(driver, ZshDriver)


def test_detect_driver_bash_returns_bash_driver() -> None:
    driver = detect_driver("/bin/bash")
    assert isinstance(driver, BashDriver)


def test_detect_driver_fallback_returns_fallback_driver() -> None:
    driver = detect_driver("/bin/sh")
    assert isinstance(driver, FallbackDriver)
