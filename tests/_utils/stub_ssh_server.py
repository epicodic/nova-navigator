"""In-process stub SSH / SFTP server for integration tests.

Listens on 127.0.0.1:0 (OS-assigned port).
Accepts all authentication.
SFTP operations are backed by the real local filesystem (no sandboxing).
exec_command requests are executed via zsh in a subprocess.

Usage::

    server = StubSSHServer(root_dir=Path("/tmp/test_remote"))
    server.start()
    # ... connect SSHFilesystem to server.host:server.port ...
    server.stop()
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import threading
from pathlib import Path

import paramiko

# ---------------------------------------------------------------------------
# SFTP handle
# ---------------------------------------------------------------------------


class StubSFTPHandle(paramiko.SFTPHandle):
    """SFTP file handle backed by a real OS file descriptor.

    The parent class (paramiko.SFTPHandle) already implements read() and
    write() using self.readfile / self.writefile, and close() shuts them down.
    We only need to override stat() and chattr().
    """

    def stat(self) -> paramiko.SFTPAttributes | int:
        """Return stat attributes for the open file descriptor."""
        fobj = self.readfile or self.writefile
        if fobj is None:
            return paramiko.SFTP_FAILURE
        try:
            return paramiko.SFTPAttributes.from_stat(os.fstat(fobj.fileno()))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def chattr(self, attr: paramiko.SFTPAttributes) -> int:  # type: ignore[override]
        """No-op: attribute changes on an open handle are not needed for tests."""
        return paramiko.SFTP_OK


# ---------------------------------------------------------------------------
# SFTP server interface
# ---------------------------------------------------------------------------


class StubSFTPServerInterface(paramiko.SFTPServerInterface):
    """SFTP server interface backed by the real local filesystem.

    Paths received from clients are treated as absolute OS paths;
    no sandboxing is applied.  For test use only.
    """

    def __init__(self, server: paramiko.ServerInterface, root_dir: Path) -> None:
        super().__init__(server)
        self._root = root_dir

    # -- directory listing ---------------------------------------------------

    def list_folder(self, path: str) -> list[paramiko.SFTPAttributes] | int:
        """List directory contents."""
        try:
            entries: list[paramiko.SFTPAttributes] = []
            for name in os.listdir(path):
                fp = os.path.join(path, name)
                try:
                    s = os.lstat(fp)
                except OSError:
                    continue
                attr = paramiko.SFTPAttributes.from_stat(s)
                attr.filename = name
                entries.append(attr)
            return entries
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def stat(self, path: str) -> paramiko.SFTPAttributes | int:
        """Return stat attributes (following symlinks)."""
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def lstat(self, path: str) -> paramiko.SFTPAttributes | int:
        """Return stat attributes (not following symlinks)."""
        try:
            return paramiko.SFTPAttributes.from_stat(os.lstat(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    # -- file I/O ------------------------------------------------------------

    def open(self, path: str, flags: int, attr: paramiko.SFTPAttributes) -> StubSFTPHandle | int:
        """Open or create a file, returning an SFTPHandle."""
        try:
            fd = os.open(path, flags, 0o666)
            if flags & os.O_WRONLY and not (flags & os.O_RDWR):
                binary_mode = "wb"
            elif flags & os.O_RDWR:
                binary_mode = "r+b"
            else:
                binary_mode = "rb"
            fobj = open(fd, binary_mode)  # noqa: SIM115
            handle = StubSFTPHandle(flags)
            if flags & os.O_WRONLY and not (flags & os.O_RDWR):
                handle.writefile = fobj
            elif flags & os.O_RDWR:
                handle.readfile = fobj
                handle.writefile = fobj
            else:
                handle.readfile = fobj
            return handle
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    # -- file / directory operations -----------------------------------------

    def remove(self, path: str) -> int:
        """Delete a file."""
        try:
            os.remove(path)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def rename(self, oldpath: str, newpath: str) -> int:
        """Rename / move a file."""
        try:
            os.rename(oldpath, newpath)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def mkdir(self, path: str, attr: paramiko.SFTPAttributes) -> int:
        """Create a directory."""
        try:
            os.mkdir(path)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def rmdir(self, path: str) -> int:
        """Remove an empty directory."""
        try:
            os.rmdir(path)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def canonicalize(self, path: str) -> str:
        """Return normalised absolute path; '.' resolves to root_dir."""
        if path == ".":
            return str(self._root)
        return os.path.normpath(path)

    def chattr(self, path: str, attr: paramiko.SFTPAttributes) -> int:  # type: ignore[override]
        """Apply utime / chmod from *attr* to *path*."""
        try:
            if attr.st_atime is not None and attr.st_mtime is not None:
                os.utime(path, (attr.st_atime, attr.st_mtime))
            if attr.st_mode is not None:
                os.chmod(path, attr.st_mode & 0o777)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def readlink(self, path: str) -> str | int:
        """Return the target of the symbolic link at *path*."""
        try:
            return os.readlink(path)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def symlink(self, target_path: str, path: str) -> int:
        """Create a symbolic link at *path* pointing to *target_path*."""
        try:
            os.symlink(target_path, path)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)


# ---------------------------------------------------------------------------
# SSH server interface
# ---------------------------------------------------------------------------


class StubSSHServerInterface(paramiko.ServerInterface):
    """SSH ServerInterface: accepts all auth, handles exec and sftp channels."""

    def check_auth_password(self, username: str, password: str) -> int:
        """Accept any password."""
        return paramiko.AUTH_SUCCESSFUL

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        """Accept any public key."""
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username: str) -> str:
        """Advertise both password and publickey auth methods."""
        return "password,publickey"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        """Allow only session channels."""
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel: paramiko.Channel, command: bytes) -> bool:
        """Execute *command* via zsh and pipe stdout/stderr back through the channel."""

        def _run() -> None:
            result = subprocess.run(["zsh", "-c", command.decode()], capture_output=True, check=False)
            channel.sendall(result.stdout)
            if result.stderr:
                channel.sendall_stderr(result.stderr)
            channel.send_exit_status(result.returncode)
            channel.close()

        threading.Thread(target=_run, daemon=True, name="stub-ssh-exec").start()
        return True

    def check_channel_shell_request(self, channel: paramiko.Channel) -> bool:
        """Reject interactive shell requests (tests use exec only)."""
        return False

    def check_channel_subsystem_request(self, channel: paramiko.Channel, name: str) -> bool:
        """Allow only the sftp subsystem."""
        return name == "sftp"


# ---------------------------------------------------------------------------
# Server orchestrator
# ---------------------------------------------------------------------------


class StubSSHServer:
    """Minimal in-process SSH server for integration tests.

    Binds to 127.0.0.1 on an OS-assigned port.  Each accepted TCP connection
    gets its own paramiko Transport running in a daemon thread.

    Args:
        root_dir: Path that StubSFTPServerInterface uses as the default cwd
            for the sftp subsystem's canonicalize('.') call.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.host = "127.0.0.1"
        self._host_key: paramiko.Ed25519Key = paramiko.Ed25519Key.generate()
        self._transports: list[paramiko.Transport] = []
        self._lock: threading.Lock = threading.Lock()
        self._sock: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        self._sock.bind(("127.0.0.1", 0))
        self.port: int = self._sock.getsockname()[1]
        self._sock.listen(16)
        self._thread: threading.Thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="stub-ssh-accept"
        )

    def start(self) -> None:
        """Start the accept loop in a daemon thread."""
        self._thread.start()

    def stop(self) -> None:
        """Close the listening socket and all open transports."""
        self._sock.close()
        with self._lock:
            for transport in list(self._transports):
                transport.close()
            self._transports.clear()

    # -- internal ------------------------------------------------------------

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                break
            self._spawn_transport(conn)

    def _spawn_transport(self, conn: socket.socket) -> None:
        transport = paramiko.Transport(conn)
        transport.add_server_key(self._host_key)
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, StubSFTPServerInterface, self.root_dir)
        server_iface = StubSSHServerInterface()
        with self._lock:
            self._transports.append(transport)
        with contextlib.suppress(Exception):
            transport.start_server(server=server_iface)
