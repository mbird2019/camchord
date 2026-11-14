"""Optional extras for OBSBOT cameras: tracking modes and settings stored on the camera.

These are not reachable over V4L2. They go through ``obsbot-ai``, a small
helper built against OBSBOT's own SDK (see contrib/obsbot-ai). The app works
without it; the tracking keys and the Camera tab simply stay unavailable.

Each helper call takes a few seconds, so calls are queued and run one at a time
in the background. ``changed`` fires when the camera has answered.
"""

from __future__ import annotations

import json
import os
import shutil

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

# key -> (label, helper arguments). Keys 4 to 0 on the keyboard, left to right.
TRACKING = {
    "headless": ("Headless", ["human", "headless"]),
    "full": ("Full body", ["human", "normal"]),
    "hand": ("Hand", ["hand"]),
    "upper": ("Upper body", ["human", "upper"]),
    "lower": ("Lower body", ["human", "lower"]),
    "group": ("Group", ["group"]),
    "closeup": ("Close-up", ["human", "closeup"]),
}
TRACKING_KEYS = {"4": "headless", "5": "full", "6": "hand", "7": "upper", "8": "lower", "9": "group", "0": "closeup"}
FRAMINGS = ("standard", "headroom", "motion")
GESTURES = {"gestureTarget": "target", "gestureZoom": "zoom", "gestureDynamicZoom": "dynamic"}
_SUB_TO_KEY = {"upper": "upper", "lower": "lower", "normal": "full", "closeup": "closeup", "headless": "headless"}
TIMEOUT_MS = 20000


def find_helper(configured: str = "") -> str:
    if configured:
        path = os.path.expanduser(configured)
        return path if os.access(path, os.X_OK) else ""
    return shutil.which("obsbot-ai") or ""


def parse_status(line: str) -> dict | None:
    """The helper's JSON answer as the state the UI shows, or None if it reported a failure."""
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("ok") is not True:
        return None
    mode, sub = data.get("mode"), data.get("sub")
    if mode == "human":
        tracking = _SUB_TO_KEY.get(sub, "full")
    elif mode in ("group", "hand"):
        tracking = mode
    else:
        tracking = "off"
    state = {"tracking": tracking}
    if data.get("framing") in FRAMINGS:
        state["framing"] = data["framing"]
    for key in (*GESTURES, "mirror"):
        if isinstance(data.get(key), bool):
            state[key] = data[key]
    if isinstance(data.get("zoom"), (int, float)):
        state["zoom"] = int(data["zoom"])
    return state


class ObsbotHelper(QObject):
    changed = Signal()  # ``state`` holds a fresh answer from the camera
    failed = Signal(str)
    zoomRead = Signal(int, int)  # the camera's real zoom, and the token given to read_zoom

    def __init__(self, path: str, parent: QObject | None = None):
        super().__init__(parent)
        self.path = path
        self.state: dict = {}
        self._queue: list[tuple[list[str], int | None]] = []
        self._proc: QProcess | None = None
        self._token: int | None = None
        self._watchdog = QTimer(self, singleShot=True, interval=TIMEOUT_MS)
        self._watchdog.timeout.connect(lambda: self._proc.kill() if self._proc else None)

    @property
    def available(self) -> bool:
        return bool(self.path)

    @property
    def busy(self) -> bool:
        return self._proc is not None or bool(self._queue)

    def forget(self) -> None:
        """The camera changed: drop what was read from the old one."""
        self._queue.clear()
        self.state = {}

    def read(self) -> None:
        self._run(["status"])

    def read_zoom(self, token: int) -> None:
        self._run(["status"], token)

    def set_tracking(self, key: str) -> None:
        self._run(["mode", *(TRACKING[key][1] if key in TRACKING else ["none"])])

    def set_framing(self, framing: str) -> None:
        if framing in FRAMINGS:
            self._run(["framing", framing])

    def set_gesture(self, state_key: str, on: bool) -> None:
        if state_key in GESTURES:
            self._run(["gesture", GESTURES[state_key], "on" if on else "off"])

    def set_mirror(self, on: bool) -> None:
        self._run(["mirror", "on" if on else "off"])

    def _run(self, args: list[str], token: int | None = None) -> None:
        if not self.available:
            return
        if args == ["status"] and token is None and any(a == ["status"] and t is None for a, t in self._queue):
            return
        self._queue.append((args, token))
        self._next()

    def _next(self) -> None:
        if self._proc is not None or not self._queue:
            return
        args, self._token = self._queue.pop(0)
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.finished.connect(lambda *_: self._finished(proc))
        proc.errorOccurred.connect(lambda error: self._finished(proc) if error == QProcess.ProcessError.FailedToStart else None)
        self._proc = proc
        self._watchdog.start()
        proc.start(self.path, args)

    def _finished(self, proc: QProcess) -> None:
        if proc is not self._proc:
            return
        self._watchdog.stop()
        out = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._proc = None
        proc.deleteLater()
        token, self._token = self._token, None
        answer = next((line for line in out.splitlines() if line.startswith("{")), "")
        state = parse_status(answer)
        if state is None:
            try:
                reason = str(json.loads(answer).get("error", ""))[:120]
            except (ValueError, AttributeError):
                reason = ""
            self.failed.emit(reason or "the camera did not answer")
        else:
            self.state = state
            if token is not None and "zoom" in state:
                self.zoomRead.emit(state["zoom"], token)
            self.changed.emit()
        self._next()
