"""Driving the running app from outside, e.g. from a window manager's key bindings.

The app listens on a Unix socket in the user's runtime folder. A request is one
line holding a JSON list of words; the answer is one line of JSON. The client
half uses only the standard library, so ``camchord ctl ...`` starts fast enough
to sit behind a key that is held down.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from pathlib import Path

from . import APP_ID
from .config import runtime_dir

MAX_REQUEST = 4096


def socket_path() -> Path:
    return runtime_dir() / f"{APP_ID}.sock"


def send(words: list[str], timeout: float = 5.0, path: Path | None = None) -> dict | None:
    """Send one command to the running app. None means no app is running."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(path or socket_path()))
            sock.sendall(json.dumps(words).encode() + b"\n")
            data = b""
            while not data.endswith(b"\n") and len(data) < 65536:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
    except (FileNotFoundError, ConnectionRefusedError):
        return None
    except OSError as error:
        return {"ok": False, "error": str(error)}
    try:
        answer = json.loads(data)
    except ValueError:
        return {"ok": False, "error": "no answer"}
    return answer if isinstance(answer, dict) else {"ok": False, "error": "bad answer"}


def parse_request(raw: bytes) -> list[str] | None:
    """The words of a request line, or None if it is not a short list of short strings."""
    try:
        words = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(words, list) or not 1 <= len(words) <= 4:
        return None
    if not all(isinstance(w, str) and len(w) <= 256 for w in words):
        return None
    return words


class Server:
    """Created by the app. ``handler`` gets the words and returns the answer dict."""

    def __init__(self, handler: Callable[[list[str]], dict], parent=None, path: Path | None = None):
        from PySide6.QtNetwork import QLocalServer

        self._handler = handler
        self.path = str(path or socket_path())
        self._server = QLocalServer(parent)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept)
        QLocalServer.removeServer(self.path)  # a stale socket from a crashed run
        self.listening = self._server.listen(self.path)

    def close(self) -> None:
        self._server.close()

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.readyRead.connect(lambda conn=conn: self._read(conn))
            conn.disconnected.connect(conn.deleteLater)

    def _read(self, conn) -> None:
        if not conn.canReadLine():
            if conn.bytesAvailable() > MAX_REQUEST:
                conn.abort()
            return
        words = parse_request(bytes(conn.readLine(MAX_REQUEST)).strip())
        try:
            answer = self._handler(words) if words else {"ok": False, "error": "bad request"}
        except Exception as error:  # a broken command must not take the app down
            answer = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        conn.write(json.dumps(answer, separators=(",", ":")).encode() + b"\n")
        conn.flush()
        conn.disconnectFromServer()
