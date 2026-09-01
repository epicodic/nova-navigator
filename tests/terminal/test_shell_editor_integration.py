"""Real PTY integration tests for shell editor protocol bindings."""

from __future__ import annotations

import errno
import os
import pty
import select
import shutil
import signal
import time
from contextlib import suppress
from dataclasses import dataclass

import pytest

from nova_navigator.terminal.shell_driver import BashDriver, ShellDriver, ZshDriver
from nova_navigator.terminal.shell_editor_protocol import EditorOperation, EditorResponse, parse_editor_response

_PASTE_TEXT = "pasted text"
_HOME_SEQUENCE = b"\x1b[H"
_END_SEQUENCE = b"\x1b[F"
_ARROW_LEFT_SEQUENCE = b"\x1b[D"


@dataclass
class _ShellSpec:
    name: str
    argv: list[str]
    driver: ShellDriver


class _InteractiveShellSession:
    def __init__(self, spec: _ShellSpec, nonce: str) -> None:
        self._spec = spec
        self._nonce = nonce
        pid, master_fd = pty.fork()
        if pid == 0:
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            os.execvpe(spec.argv[0], spec.argv, env)
        self._pid = pid
        self._master_fd = master_fd
        self._buffer = bytearray()

    def close(self) -> None:
        try:
            if not self._exited():
                for command in (b"\x03", b"exit\n"):
                    try:
                        self.write(command)
                    except OSError as error:
                        if error.errno not in {errno.EBADF, errno.EIO}:
                            raise
                        break
                deadline = time.monotonic() + 1.0
                while not self._exited() and time.monotonic() < deadline:
                    time.sleep(0.01)

            if not self._exited():
                with suppress(ProcessLookupError):
                    os.kill(self._pid, signal.SIGTERM)
                deadline = time.monotonic() + 2.0
                while not self._exited() and time.monotonic() < deadline:
                    time.sleep(0.01)

            if not self._exited():
                with suppress(ProcessLookupError):
                    os.kill(self._pid, signal.SIGKILL)
                deadline = time.monotonic() + 2.0
                while not self._exited() and time.monotonic() < deadline:
                    time.sleep(0.01)
        finally:
            try:
                os.close(self._master_fd)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise

    def write(self, data: bytes) -> None:
        os.write(self._master_fd, data)

    def install(self) -> EditorResponse:
        if self._spec.name == "zsh":
            self.write(b"PROMPT='% '")
            self.write(b"\n")
            self.write(b"bindkey -M emacs '^[[H' beginning-of-line\n")
            self.write(b"bindkey -M viins '^[[H' beginning-of-line\n")
        if self._spec.name == "bash":
            self.write(b"PS1='$ '\n")
            self.write(b"bind '\"\\e[H\": beginning-of-line'\n")
        self.write(self._spec.driver.editor_integration_code(self._nonce).encode())
        return self.wait_for(EditorOperation.READY)

    def request(self, operation: EditorOperation, timeout: float = 1.5) -> EditorResponse | None:
        self.write(self._spec.driver.editor_request(operation))
        return self.wait_for(operation, timeout=timeout)

    def wait_for(self, operation: EditorOperation, timeout: float = 1.5) -> EditorResponse | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._read_once(deadline)
            response = self._pop_response(operation)
            if response is not None:
                return response
        return None

    def drain(self) -> None:
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            if not self._read_once(deadline):
                break

    def _read_once(self, deadline: float) -> bool:
        timeout = max(0.0, min(0.05, deadline - time.monotonic()))
        ready, _, _ = select.select([self._master_fd], [], [], timeout)
        if not ready:
            return False
        chunk = os.read(self._master_fd, 4096)
        if not chunk:
            return False
        self._buffer.extend(chunk)
        return True

    def _exited(self) -> bool:
        try:
            pid, _ = os.waitpid(self._pid, os.WNOHANG)
        except ChildProcessError:
            return True
        return pid == self._pid

    def _pop_response(self, operation: EditorOperation) -> EditorResponse | None:
        while True:
            start = self._buffer.find(b"\x1b]777;")
            if start < 0:
                return None

            bel = self._buffer.find(b"\x07", start)
            st = self._buffer.find(b"\x1b\\", start)
            end = -1
            terminator_len = 0
            if bel >= 0 and (st < 0 or bel < st):
                end = bel
                terminator_len = 1
            elif st >= 0:
                end = st
                terminator_len = 2
            else:
                return None

            payload_start = start + len(b"\x1b]777;")
            payload = bytes(self._buffer[payload_start:end]).decode("utf-8", errors="ignore")
            del self._buffer[: end + terminator_len]

            parsed = parse_editor_response(payload)
            if parsed is None:
                continue
            if parsed.nonce != self._nonce:
                continue
            if parsed.operation != operation:
                continue
            return parsed


_SHELL_SPECS = [
    _ShellSpec(name="zsh", argv=["zsh", "-f", "-i"], driver=ZshDriver()),
    _ShellSpec(name="bash", argv=["bash", "--noprofile", "--norc", "-i"], driver=BashDriver()),
]


@dataclass(frozen=True)
class _ProtocolCycleResult:
    response_empty: EditorResponse
    response_pasted: EditorResponse
    response_mid_cursor: EditorResponse
    stash_ack: EditorResponse
    response_after_stash: EditorResponse
    restore_ack: EditorResponse
    response_after_restore: EditorResponse


@pytest.mark.parametrize("spec", _SHELL_SPECS, ids=[spec.name for spec in _SHELL_SPECS])
def test_editor_protocol_real_shell_pty(spec: _ShellSpec) -> None:
    if shutil.which(spec.argv[0]) is None:
        pytest.skip(f"{spec.argv[0]} is not installed")

    nonce = "abc123"
    session = _InteractiveShellSession(spec, nonce=nonce)
    try:
        ready = session.install()
        assert ready is not None
        assert ready.sequence == 0
        assert ready.buffer_length == 0
        assert ready.cursor == 0
        cycle = _run_protocol_cycle(session)
        _assert_sequence_increments(cycle)
        _assert_probe_binding_loss(spec, session)
    finally:
        session.close()


def _run_protocol_cycle(session: _InteractiveShellSession) -> _ProtocolCycleResult:
    response_empty = _require_response(session.request(EditorOperation.PROBE))
    assert response_empty.buffer_length == 0
    assert response_empty.cursor == 0

    session.write(b"\x1b[200~" + _PASTE_TEXT.encode() + b"\x1b[201~")
    session.write(_HOME_SEQUENCE)

    response_pasted = _require_response(session.request(EditorOperation.PROBE))
    assert response_pasted.buffer_length == len(_PASTE_TEXT)
    assert response_pasted.cursor == 0

    session.write(_END_SEQUENCE)
    session.write(b"\x15")
    session.write(b"0123456789")
    session.write(_ARROW_LEFT_SEQUENCE * 5)

    response_mid_cursor = _require_response(session.request(EditorOperation.PROBE))
    assert response_mid_cursor.buffer_length == 10
    assert response_mid_cursor.cursor == 5

    stash_ack = _require_response(session.request(EditorOperation.STASH))

    response_after_stash = _require_response(session.request(EditorOperation.PROBE))
    assert response_after_stash.buffer_length == 0
    assert response_after_stash.cursor == 0

    restore_ack = _require_response(session.request(EditorOperation.RESTORE))

    response_after_restore = _require_response(session.request(EditorOperation.PROBE))
    assert response_after_restore.buffer_length == 10
    assert response_after_restore.cursor == 5

    return _ProtocolCycleResult(
        response_empty=response_empty,
        response_pasted=response_pasted,
        response_mid_cursor=response_mid_cursor,
        stash_ack=stash_ack,
        response_after_stash=response_after_stash,
        restore_ack=restore_ack,
        response_after_restore=response_after_restore,
    )


def _assert_sequence_increments(cycle: _ProtocolCycleResult) -> None:
    assert cycle.response_pasted.sequence == cycle.response_empty.sequence + 1
    assert cycle.response_mid_cursor.sequence == cycle.response_pasted.sequence + 1
    assert cycle.stash_ack.sequence == cycle.response_mid_cursor.sequence + 1
    assert cycle.response_after_stash.sequence == cycle.stash_ack.sequence + 1
    assert cycle.restore_ack.sequence == cycle.response_after_stash.sequence + 1
    assert cycle.response_after_restore.sequence == cycle.restore_ack.sequence + 1


def _assert_probe_binding_loss(spec: _ShellSpec, session: _InteractiveShellSession) -> None:
    session.drain()
    _unbind_probe(spec, session)

    missing_probe = session.request(EditorOperation.PROBE, timeout=0.4)
    assert missing_probe is None


def _require_response(response: EditorResponse | None) -> EditorResponse:
    assert response is not None
    return response


def _unbind_probe(spec: _ShellSpec, session: _InteractiveShellSession) -> None:
    if spec.name == "zsh":
        session.write(b"bindkey $'\\e[99~' undefined-key\n")
        session.write(b"bindkey -M emacs $'\\e[99~' undefined-key\n")
        session.write(b"bindkey -M viins $'\\e[99~' undefined-key\n")
        session.write(b"bindkey -M vicmd $'\\e[99~' undefined-key\n")
    elif spec.name == "bash":
        session.write(b"bind '\"\\e[99~\": self-insert'\n")
        session.write(b"bind -m emacs-standard '\"\\e[99~\": self-insert'\n")
        session.write(b"bind -m emacs-meta '\"\\e[99~\": self-insert'\n")
        session.write(b"bind -m emacs-ctlx '\"\\e[99~\": self-insert'\n")
        session.write(b"bind -m vi-insert '\"\\e[99~\": self-insert'\n")
        session.write(b"bind -m vi-command '\"\\e[99~\": self-insert'\n")
    session.drain()
