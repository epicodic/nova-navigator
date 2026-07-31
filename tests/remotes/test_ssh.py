"""Tests for SSH remote shell detection helpers."""

from __future__ import annotations

from nova_navigator.remotes.ssh import _parse_shell_detection_output


def test_parse_shell_detection_output_prefers_shell_env() -> None:
    output = "/bin/bash\n/bin/bash\n"
    assert _parse_shell_detection_output(output) == "/bin/bash"


def test_parse_shell_detection_output_falls_back_when_shell_env_empty() -> None:
    # First line ($SHELL) is blank; second line (getent passwd) has the value.
    output = "\n/usr/bin/zsh\n"
    assert _parse_shell_detection_output(output) == "/usr/bin/zsh"


def test_parse_shell_detection_output_defaults_to_sh_when_all_blank() -> None:
    assert _parse_shell_detection_output("\n\n") == "/bin/sh"


def test_parse_shell_detection_output_defaults_to_sh_when_empty() -> None:
    assert _parse_shell_detection_output("") == "/bin/sh"


def test_parse_shell_detection_output_strips_whitespace() -> None:
    assert _parse_shell_detection_output("  /bin/bash  \n") == "/bin/bash"
