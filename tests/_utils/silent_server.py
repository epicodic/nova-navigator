"""TCP server that accepts connections and never writes (simulates a hung SSH peer)."""

from __future__ import annotations

import contextlib
import socket
import threading


class SilentServer:
    """Listen on 127.0.0.1 (OS-assigned port), keep accepted sockets open and send nothing."""

    def __init__(self) -> None:
        self._sock: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        self._sock.bind(("127.0.0.1", 0))
        self.port: int = self._sock.getsockname()[1]
        self._sock.listen(16)
        self._accepted: list[socket.socket] = []
        self._lock: threading.Lock = threading.Lock()
        self._thread: threading.Thread = threading.Thread(target=self._accept_loop, daemon=True, name="silent-accept")

    def start(self) -> None:
        """Start the accept loop in a daemon thread."""
        self._thread.start()

    def stop(self) -> None:
        """Close the listener and every accepted socket."""
        with contextlib.suppress(OSError):
            self._sock.shutdown(socket.SHUT_RDWR)
        self._sock.close()
        with self._lock:
            for conn in self._accepted:
                with contextlib.suppress(OSError):
                    conn.close()
            self._accepted.clear()
        self._thread.join(timeout=5)

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                break
            with self._lock:
                self._accepted.append(conn)
