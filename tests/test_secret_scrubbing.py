"""Registered secrets are absent from ``crash.txt`` and ``app.log`` in every form (REQ-26)."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from types import CodeType, FunctionType, SimpleNamespace
from typing import cast

import pytest
from rich.logging import RichHandler

from nova_navigator import debug_analytics
from nova_navigator.remotes.ssh_route import SshHop
from nova_navigator.secret_registry import REDACTED, register_secret
from nova_navigator.vfs.filesystems.ssh import _hop_login, _RouteContext

HEAD = "hun" + "t9q"
TAIL = "mar" + "k7z"
SECRET = HEAD + "'\"\\\t[b]" + TAIL
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_logger = logging.getLogger("tests.secret_scrubbing")

FORMS: dict[str, Callable[[str], str]] = {"plain": str, "repr": repr}


class _Holder:
    def __init__(self, text: str) -> None:
        self._text = text

    def __repr__(self) -> str:
        return f"Holder({self._text})"


class _Reports:
    def __init__(self, handler: RichHandler) -> None:
        self._handler = handler

    def log_dir(self) -> Path:
        log_dir = debug_analytics._log_dir
        assert log_dir is not None
        return log_dir

    def crash(self, exc: BaseException) -> str:
        debug_analytics.write_crash(exc)
        return _ANSI.sub("", (self.log_dir() / "crash.txt").read_text(encoding="utf-8"))

    def log(self) -> str:
        self._handler.console.file.flush()
        return _ANSI.sub("", (self.log_dir() / "app.log").read_text(encoding="utf-8"))


@pytest.fixture
def reports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[_Reports, None, None]:
    root = logging.getLogger()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(debug_analytics, "DEBUG_ANALYTICS", True)
    monkeypatch.setattr(debug_analytics, "LIVE_PDB", False)
    monkeypatch.setattr(debug_analytics, "_log_dir", None)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(root, "level", root.level)
    before = list(root.handlers)
    debug_analytics.install()
    added = [h for h in root.handlers if h not in before]
    handler = next(h for h in added if isinstance(h, RichHandler))
    register_secret(SECRET)
    try:
        yield _Reports(handler)
    finally:
        for h in added:
            root.removeHandler(h)
        handler.console.file.close()


@pytest.fixture(params=list(FORMS))
def value(request: pytest.FixtureRequest) -> str:
    return FORMS[request.param](SECRET)


def _assert_scrubbed(text: str) -> None:
    assert HEAD not in text
    assert TAIL not in text
    assert REDACTED in text


def _caught(action: Callable[[], None]) -> BaseException:
    with pytest.raises(RuntimeError) as info:
        action()
    return info.value


def test_crash_scrubs_local_variable(reports: _Reports, value: str) -> None:
    def fail() -> None:
        innocent = [value]
        raise RuntimeError(f"boom {len(innocent)}")

    _assert_scrubbed(reports.crash(_caught(fail)))


def test_crash_scrubs_exception_message(reports: _Reports, value: str) -> None:
    _assert_scrubbed(reports.crash(RuntimeError(f"login with {value} failed")))


def test_crash_scrubs_log_message_held_at_crash(reports: _Reports, value: str) -> None:
    def fail() -> None:
        message = "x" * 110 + f" sending {value}"
        raise RuntimeError(f"boom {len(message) > 0}")

    _assert_scrubbed(reports.crash(_caught(fail)))


def test_crash_scrubs_traceback_frames(reports: _Reports, value: str) -> None:
    def inner() -> None:
        raise RuntimeError("deep")

    def outer(argument: str) -> None:
        _ = argument  # keeps the secret in this frame's locals
        inner()

    _assert_scrubbed(reports.crash(_caught(lambda: outer(value))))


def test_crash_scrubs_source_lines(reports: _Reports) -> None:
    register_secret("src" + "only7q")

    def fail() -> None:
        raise RuntimeError("boom")  # srconly7q

    text = reports.crash(_caught(fail))
    assert "src" + "only7q" not in text
    assert REDACTED in text


def test_crash_keeps_source_line_with_markup_text(reports: _Reports) -> None:
    register_secret("mksrc" + "only8w")

    def fail() -> None:
        raise RuntimeError("boom")  # markup [/] and mksrconly8w

    text = reports.crash(_caught(fail))
    assert "markup [/] and" in text
    assert "mksrconly8w" not in text
    assert REDACTED in text


def test_crash_scrubs_local_variable_name(reports: _Reports) -> None:
    register_secret("ghost" + "9zq")
    generated = "def boom():\n    ghost" + "9zq = 1\n    raise RuntimeError('boom')\n"

    def fail() -> None:
        code = compile(generated, "<generated>", "exec")
        boom = FunctionType(next(const for const in code.co_consts if isinstance(const, CodeType)), {})
        boom()

    text = reports.crash(_caught(fail))
    assert "ghost9zq" not in text
    assert REDACTED in text


def test_crash_scrubs_object_repr(reports: _Reports, value: str) -> None:
    def fail() -> None:
        holder = _Holder(value)
        raise RuntimeError(f"boom {type(holder).__name__}")

    _assert_scrubbed(reports.crash(_caught(fail)))


def test_crash_scrubs_typed_credentials_in_hop_login(reports: _Reports) -> None:
    class _Hop:
        @property
        def user(self) -> str:
            raise RuntimeError("inside hop login")

    context = SimpleNamespace(credentials={0: (None, SECRET)})
    exc = _caught(lambda: _hop_login(cast("_RouteContext", context), cast("list[SshHop]", [_Hop()]), 0))
    text = reports.crash(exc)
    assert "typed" in text
    _assert_scrubbed(text)


def test_log_scrubs_logged_variable(reports: _Reports, value: str) -> None:
    _logger.info("values %s %r", value, [value])
    _assert_scrubbed(reports.log())


def test_log_scrubs_log_message(reports: _Reports, value: str) -> None:
    message = "x" * 100 + f" sending {value}"
    _logger.warning(message)
    text = reports.log()
    _assert_scrubbed(text)
    assert "sending" in text


def test_log_scrubs_exception_message(reports: _Reports, value: str) -> None:
    try:
        raise RuntimeError(f"login with {value} failed")
    except RuntimeError:
        _logger.exception("could not connect")
    text = reports.log()
    _assert_scrubbed(text)
    assert "could not connect" in text
    assert "RuntimeError" in text


def test_log_scrubs_traceback_and_stack(reports: _Reports, value: str) -> None:
    try:
        try:
            raise KeyError(value)
        except KeyError as exc:
            exc.add_note(f"note {value}")
            raise RuntimeError("outer") from exc
    except RuntimeError:
        _logger.exception("chained", stack_info=True)
    text = reports.log()
    _assert_scrubbed(text)
    assert "Traceback" in text


def test_log_scrubs_object_repr(reports: _Reports, value: str) -> None:
    _logger.debug("holder %r", _Holder(value))
    _assert_scrubbed(reports.log())


def test_log_scrubs_third_party_logger(reports: _Reports, value: str) -> None:
    logging.getLogger("paramiko.transport").debug("auth %s", value)
    _assert_scrubbed(reports.log())


@pytest.mark.usefixtures("reports")
def test_log_leaves_the_record_unchanged_for_other_handlers() -> None:
    seen: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    capture = _Capture()
    logging.getLogger().addHandler(capture)
    try:
        _logger.info("value %s", SECRET)
    finally:
        logging.getLogger().removeHandler(capture)
    assert seen == [f"value {SECRET}"]
