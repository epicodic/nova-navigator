from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

from nova_navigator.nerd_font_detect import _fc_list_has_nerd_font_glyph, _probe_nerd_font, detect_nerd_font


def _cpr_reader(responses: list[tuple[int, int] | None]) -> Callable[[], tuple[int, int] | None]:
    it = iter(responses)
    return lambda: next(it, None)


def test_detect_nerd_font_returns_false_when_stdout_not_tty() -> None:
    with patch("sys.stdout.isatty", return_value=False), patch("sys.stdin.isatty", return_value=True):
        assert detect_nerd_font() is False


def test_detect_nerd_font_returns_false_when_stdin_not_tty() -> None:
    with patch("sys.stdout.isatty", return_value=True), patch("sys.stdin.isatty", return_value=False):
        assert detect_nerd_font() is False


def test_probe_returns_true_when_glyph_is_double_width() -> None:
    # Initial position: col 1; after NerdFont glyph: col 3 (cursor advanced 2).
    result = _probe_nerd_font(
        write=lambda _: None,
        flush=lambda: None,
        read_cpr=_cpr_reader([(5, 1), (5, 3)]),
    )
    assert result is True


def test_probe_returns_false_when_glyph_is_single_width() -> None:
    # After glyph: col 2 (advanced 1 -- font doesn't know the glyph).
    result = _probe_nerd_font(
        write=lambda _: None,
        flush=lambda: None,
        read_cpr=_cpr_reader([(5, 1), (5, 2)]),
    )
    assert result is False


def test_probe_returns_false_when_initial_cpr_times_out() -> None:
    result = _probe_nerd_font(
        write=lambda _: None,
        flush=lambda: None,
        read_cpr=_cpr_reader([None, None]),
    )
    assert result is False


def test_probe_returns_false_when_second_cpr_times_out() -> None:
    result = _probe_nerd_font(
        write=lambda _: None,
        flush=lambda: None,
        read_cpr=_cpr_reader([(5, 1), None]),
    )
    assert result is False


def test_detect_nerd_font_falls_back_to_fontconfig_when_probe_is_single_wide() -> None:
    # Simulate a TTY with a single-wide probe result (VTE-style terminal),
    # and a NerdFont installed according to fontconfig.
    with (
        patch("sys.stdout.isatty", return_value=True),
        patch("sys.stdin.isatty", return_value=True),
        patch("termios.tcgetattr", return_value=[]),
        patch("termios.tcsetattr"),
        patch("tty.setraw"),
        patch("sys.stdin.fileno", return_value=0),
        patch("nova_navigator.nerd_font_detect._probe_nerd_font", return_value=False),
        patch("nova_navigator.nerd_font_detect._fc_list_has_nerd_font_glyph", return_value=True),
    ):
        assert detect_nerd_font() is True


def test_detect_nerd_font_returns_false_when_probe_single_wide_and_no_fc_match() -> None:
    with (
        patch("sys.stdout.isatty", return_value=True),
        patch("sys.stdin.isatty", return_value=True),
        patch("termios.tcgetattr", return_value=[]),
        patch("termios.tcsetattr"),
        patch("tty.setraw"),
        patch("sys.stdin.fileno", return_value=0),
        patch("nova_navigator.nerd_font_detect._probe_nerd_font", return_value=False),
        patch("nova_navigator.nerd_font_detect._fc_list_has_nerd_font_glyph", return_value=False),
    ):
        assert detect_nerd_font() is False


def test_fc_list_has_nerd_font_glyph_returns_false_when_fc_list_missing() -> None:
    with patch("shutil.which", return_value=None):
        assert _fc_list_has_nerd_font_glyph() is False
