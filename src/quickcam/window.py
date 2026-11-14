"""The application window: the live picture on the left, the controls on the right."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, obsbot, v4l2
from .config import RESOLUTIONS, Settings
from .devices import Camera, CameraIndex, StreamMode, pick_mode, sizes_offered
from .ptz import DIRECTIONS, Ptz
from .recorder import Recipe, Recorder, choose_encoder, microphone_for, newest_recording
from .stream import Stream
from .widgets import STYLE, FlowLayout, KeyCap, OptionRow, Preview, choice_button, label, set_role

MOVE_KEYS = {
    Qt.Key.Key_I: "up", Qt.Key.Key_J: "left", Qt.Key.Key_K: "down", Qt.Key.Key_L: "right",
    Qt.Key.Key_Up: "up", Qt.Key.Key_Left: "left", Qt.Key.Key_Down: "down", Qt.Key.Key_Right: "right",
}  # fmt: skip
ZOOM_KEYS = {Qt.Key.Key_H: 1, Qt.Key.Key_Plus: 1, Qt.Key.Key_Equal: 1, Qt.Key.Key_N: -1, Qt.Key.Key_Minus: -1, Qt.Key.Key_Underscore: -1}
PENDING_S = 25.0  # how long a slow change may flash before the app gives up on it
RETRY_S = 5.0  # wait this long before trying a camera again after it failed to start
SIDE_WIDTH = 552


def size_text(size: int) -> str:
    if size >= 1 << 30:
        return f"{size / (1 << 30):.1f} GB"
    if size >= 1 << 20:
        return f"{size / (1 << 20):.0f} MB"
    return f"{round(size / 1024)} KB"


class _Later:
    """A one-shot timer the motion code can cancel safely, even after it fired."""

    def __init__(self, parent, seconds: float, fn):
        self._fn = fn
        self._timer: QTimer | None = QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)
        self._timer.start(max(0, round(seconds * 1000)))

    def _fire(self) -> None:
        self.cancel()
        self._fn()

    def cancel(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.stop()
            timer.deleteLater()


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None, index: CameraIndex | None = None):
        super().__init__()
        self.settings = settings or Settings()
        self.index = index or CameraIndex()
        self.stream = Stream(self)
        self.recorder = Recorder(self)
        self.helper = obsbot.ObsbotHelper(obsbot.find_helper(self.settings.obsbot_helper), self)

        self.cameras: list[Camera] = []
        self.camera: Camera | None = None  # the camera that is open right now
        self.device: v4l2.Device | None = None
        self.ptz: Ptz | None = None
        self.stream_error = ""
        self._retry_at = 0.0
        self._latest: bytes | None = None
        self._decode_queued = False
        self._pending: dict[str, tuple[object, float]] = {}
        self._blink = True
        self._keys_down: set[tuple[str, object]] = set()
        self._zoom_token = 0
        self._panel_before_fullscreen = True
        self._camera_row_key: list = []
        self._quitting = False

        self.setWindowTitle(APP_NAME)
        self.setStyleSheet(STYLE)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(960, 600)
        self._build()

        self.stream.frame.connect(self._on_frame)
        self.stream.started.connect(self.refresh)
        self.stream.stopped.connect(self._on_stream_stopped)
        self.recorder.stopped.connect(self._on_recorder_stopped)
        self.helper.changed.connect(self.refresh)
        self.helper.failed.connect(self._on_helper_failed)
        self.helper.zoomRead.connect(self._on_zoom_read)

        self._tick = QTimer(self, interval=40)
        self._tick.timeout.connect(self._on_tick)
        self._poll = QTimer(self, interval=2000)
        self._poll.timeout.connect(self.poll)
        self._poll.start()
        self._blinker = QTimer(self, interval=450)
        self._blinker.timeout.connect(self._on_blink)
        self._keep_awake = QTimer(self)
        self._keep_awake.timeout.connect(lambda: self.ptz.keep_awake() if self.ptz and self.stream.live else None)
        self._settle = QTimer(self, singleShot=True, interval=1200)
        self._settle.timeout.connect(self.refresh)

        state = self.settings.window_state()
        width, height = state.get("width"), state.get("height")
        self.resize(width if isinstance(width, int) else 1400, height if isinstance(height, int) else 780)
        self.side.setVisible(state.get("panel", True) is not False)
        self.apply_camera()

    # ---- building the window ---------------------------------------------------
    def _build(self) -> None:
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.preview = Preview()
        row.addWidget(self.preview, 1)

        self.side = QScrollArea()
        self.side.setWidgetResizable(True)
        self.side.setFixedWidth(SIDE_WIDTH)
        self.side.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.side.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(self.side)
        panel = QWidget()
        self.side.setWidget(panel)
        col = QVBoxLayout(panel)
        col.setContentsMargins(18, 16, 18, 14)
        col.setSpacing(10)

        self.status_label = label(role="status")
        self.state_label = label(role="dim")
        col.addWidget(self.status_label)
        col.addWidget(self.state_label)

        camera_row = QHBoxLayout()
        camera_row.setSpacing(8)
        camera_row.addWidget(label("Camera", wrap=False), 0, Qt.AlignmentFlag.AlignTop)
        holder = QWidget()
        self.camera_flow = FlowLayout(holder)
        self.camera_buttons: dict[str, QPushButton] = {}
        camera_row.addWidget(holder, 1)
        col.addLayout(camera_row)
        self.lock_label = label("Stop recording to switch cameras.", "dim")
        col.addWidget(self.lock_label)

        self.tabs = QTabWidget()
        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabs.addTab(self._build_controls(), "Controls")
        self.tabs.addTab(self._build_camera_tab(), "Camera")
        self.tabs.addTab(self._build_recording(), "Recording")
        self.tabs.currentChanged.connect(self._on_tab)
        col.addWidget(self.tabs)
        col.addStretch(1)

        self.message_label = label(role="dim")
        col.addWidget(self.message_label)
        footer = QHBoxLayout()
        self.folder_label = label(f"Snapshots and recordings go to {self._short(self.settings.output_dir)}", "dim")
        footer.addWidget(self.folder_label, 1)
        open_folder = QPushButton("Open folder")
        open_folder.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        open_folder.clicked.connect(self._open_folder)
        footer.addWidget(open_folder)
        col.addLayout(footer)

    def _build_controls(self) -> QWidget:
        tab = QWidget()
        col = QVBoxLayout(tab)
        col.setContentsMargins(0, 12, 0, 0)
        col.setSpacing(10)
        col.addWidget(label("These keys work while this window is in front. Every key is also a button.", "dim"))

        self.keymap = QWidget()
        self.caps: dict[str, KeyCap] = {}

        def cap(key: str, symbol: str, text: str) -> KeyCap:
            self.caps[key] = KeyCap(key, symbol, text, self.keymap)
            return self.caps[key]

        for digit, mode in obsbot.TRACKING_KEYS.items():
            cap(digit, "", obsbot.TRACKING[mode][0]).clicked.connect(lambda _c=False, mode=mode: self.set_tracking(mode))
        cap("U", "◉", "Snapshot").clicked.connect(lambda: self.snap())
        cap("O", "●", "Record").clicked.connect(lambda: self.toggle_record())
        cap("P", "◎", "Autofocus").clicked.connect(lambda: self.toggle_focus())
        cap("M", "◧", "Hide panel").clicked.connect(lambda: self.toggle_panel())
        cap("Space", "⛶", "Full screen").clicked.connect(lambda: self.toggle_fullscreen())
        for key, symbol, text, direction in (
            ("I", "↑", "Up", "up"),
            ("J", "←", "Left", "left"),
            ("K", "↓", "Down", "down"),
            ("L", "→", "Right", "right"),
        ):
            button = cap(key, symbol, text)
            button.pressed.connect(lambda direction=direction: self.move_start(direction))
            button.released.connect(lambda direction=direction: self.move_stop(direction))
        for key, symbol, text, direction in (("H", "+", "Zoom in", 1), ("N", "−", "Zoom out", -1)):
            button = cap(key, symbol, text)
            button.pressed.connect(lambda direction=direction: self.zoom_start(direction))
            button.released.connect(lambda direction=direction: self.zoom_stop(direction))
        col.addWidget(self.keymap, 0, Qt.AlignmentFlag.AlignHCenter)
        self._tracking_row_shown: bool | None = None

        self.keys_note = label(role="dim")
        col.addWidget(self.keys_note)
        buttons = QHBoxLayout()
        self.center_button = QPushButton("Center  (C)")
        self.center_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.center_button.clicked.connect(lambda: self.center())
        buttons.addWidget(self.center_button)
        buttons.addStretch(1)
        col.addLayout(buttons)
        col.addStretch(1)
        return tab

    def _layout_keys(self, tracking_row: bool) -> None:
        """Place the keycaps as they sit on a keyboard. The number row shows only when tracking is possible."""
        if tracking_row == self._tracking_row_shown:
            return
        self._tracking_row_shown = tracking_row
        unit, height, gap = 62, 56, 6
        pitch = unit + gap
        # The number row starts at 4, three keys left of the letter rows; without it the letters start at the edge.
        x0 = round(2.75 * pitch) if tracking_row else 0
        top = height + gap if tracking_row else 0
        numbers = {"4": -2.75, "5": -1.75, "6": -0.75, "7": 0.25, "8": 1.25, "9": 2.25, "0": 3.25}
        letters = {
            "U": (0.75, 0),
            "I": (1.75, 0),
            "O": (2.75, 0),
            "P": (3.75, 0),
            "H": (0, 1),
            "J": (1, 1),
            "K": (2, 1),
            "L": (3, 1),
            "N": (0.5, 2),
            "M": (1.5, 2),
        }
        for key, offset in numbers.items():
            self.caps[key].setVisible(tracking_row)
            self.caps[key].setGeometry(x0 + round(offset * pitch), 0, unit, height)
        for key, (offset, line) in letters.items():
            self.caps[key].setGeometry(x0 + round(offset * pitch), top + line * (height + gap), unit, height)
        right = x0 + round(3.75 * pitch) + unit
        space_x = x0 + round(2.5 * pitch)
        self.caps["Space"].setGeometry(space_x, top + 2 * (height + gap), right - space_x, height)
        self.keymap.setFixedSize(right, top + 3 * height + 2 * gap)

    def _build_camera_tab(self) -> QWidget:
        tab = QWidget()
        col = QVBoxLayout(tab)
        col.setContentsMargins(0, 12, 0, 0)
        col.setSpacing(10)
        self.cam_note = label(role="dim")
        col.addWidget(self.cam_note)
        self.cam_settings = QWidget()
        inner = QVBoxLayout(self.cam_settings)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(10)
        self.framing_row = OptionRow(
            "Tracking style",
            [("standard", "Standard"), ("headroom", "Headroom"), ("motion", "Motion (fast)")],
            "How tracking frames and follows you. Motion keeps up with fast movement; Standard is calmer.",
        )
        self.framing_row.chosen.connect(lambda value: self.set_camera_setting("framing", value))
        inner.addWidget(self.framing_row)
        inner.addWidget(label("Hand gestures", "title"))
        self.toggles: dict[str, QPushButton] = {}
        gestures = (
            ("gestureTarget", "Tracking on / off", "Open palm raised beside your head. The light flashes twice."),
            ("gestureZoom", "Zoom 2x / cancel", "One hand in an “L” (index finger up, thumb out) beside your head."),
            (
                "gestureDynamicZoom",
                "Dynamic zoom",
                "Both hands in an “L” beside your head; hold until the light flashes, then change the distance between your hands.",
            ),
        )
        for key, name, how in gestures:
            inner.addLayout(self._toggle_row(key, name, how))
        inner.addWidget(label("Picture", "title"))
        inner.addLayout(self._toggle_row("mirror", "Mirror left-right", ""))
        col.addWidget(self.cam_settings)
        col.addStretch(1)
        return tab

    def _toggle_row(self, key: str, name: str, how: str) -> QHBoxLayout:
        row = QHBoxLayout()
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label(name))
        if how:
            text.addWidget(label(how, "dim"))
        row.addLayout(text, 1)
        button = choice_button("–")
        button.setMinimumWidth(56)
        button.clicked.connect(lambda _checked=False, key=key: self._toggle_setting(key))
        self.toggles[key] = button
        row.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _build_recording(self) -> QWidget:
        tab = QWidget()
        col = QVBoxLayout(tab)
        col.setContentsMargins(0, 12, 0, 0)
        col.setSpacing(12)
        self.rec_rows: dict[str, OptionRow] = {
            "res": OptionRow("Resolution (picture, recordings and snapshots)", [(k, k if k != "4k" else "4K") for k in RESOLUTIONS]),
            "fps": OptionRow("Frame rate", [(30, "30 fps"), (60, "60 fps")]),
            "format": OptionRow(
                "File type",
                [("mp4", "MP4"), ("mkv", "MKV")],
                "MP4 opens almost anywhere. Both are written so a recording cut short still plays.",
            ),
            "quality": OptionRow(
                "Quality",
                [("high", "High"), ("standard", "Standard"), ("small", "Small")],
                "High keeps the most detail and makes the biggest files; Small makes the smallest.",
            ),
            "audio": OptionRow(
                "Sound", [("on", "On"), ("off", "Off")], "From the camera's own microphone if it has one, otherwise the default microphone."
            ),
            "split": OptionRow("Start a new file every", [(10, "10 min"), (30, "30 min"), (60, "60 min")]),
        }
        for key, option_row in self.rec_rows.items():
            option_row.chosen.connect(lambda value, key=key: self.set_record_option(key, value))
            col.addWidget(option_row)
        self.rec_note = label(role="dim")
        col.addWidget(self.rec_note)
        col.addStretch(1)
        return tab

    @staticmethod
    def _short(path: Path) -> str:
        home = str(Path.home())
        text = str(path)
        return "~" + text[len(home) :] if text.startswith(home + "/") else text

    # ---- messages --------------------------------------------------------------
    def say(self, text: str, notify: bool = False) -> None:
        """Show a line at the bottom of the panel; also as a desktop notification when asked and not in front."""
        self.message_label.setText(text)
        if notify and text and not self.isActiveWindow() and shutil.which("notify-send"):
            QProcess.startDetached("notify-send", ["-a", APP_NAME, "-u", "low", "-t", "3000", f"{APP_NAME}: {text}"])

    def later(self, seconds: float, fn) -> _Later:
        return _Later(self, seconds, fn)

    # ---- which camera ----------------------------------------------------------
    def camera_name(self, camera_id: str) -> str:
        for camera in self.cameras:
            if camera.id == camera_id:
                return camera.name
        return self.settings.camera_name(camera_id)

    def wanted_mode(self, camera: Camera) -> StreamMode:
        width, height = RESOLUTIONS[self.settings.get("res")]
        return pick_mode(camera.modes, width, height, self.settings.get("fps"))

    def apply_camera(self) -> None:
        """Make what is running match the chosen camera and settings."""
        self.cameras = self.index.refresh()
        for camera in self.cameras:
            self.settings.remember_name(camera.id, camera.name)
        chosen = self.settings.camera
        camera = next((c for c in self.cameras if c.id == chosen), None)
        if camera is None:
            self._close_camera()
            self.stream_error = ""
        else:
            mode = self.wanted_mode(camera)
            same_camera = self.camera is not None and self.camera.id == camera.id and self.stream.running
            if same_camera and (self.stream.mode == mode or self.recorder.busy):
                pass  # already right, or recording: a new size waits for the recording to end
            else:
                self._open_camera(camera, mode)
        self.refresh()

    def _open_camera(self, camera: Camera, mode: StreamMode) -> None:
        self._close_camera()
        self.stream_error = ""
        try:
            self.device = v4l2.Device(camera.path)
        except OSError as error:
            self.stream_error = error.strerror or str(error)
            self._retry_at = time.monotonic() + RETRY_S
            return
        self.camera = camera
        if camera.ptz:
            self.ptz = Ptz(
                self.device,
                camera.profile,
                later=self.later,
                step_degrees=self.settings.get("step_degrees"),
                hold_speed=self.settings.get("hold_speed"),
                zoom_speed=self.settings.get("zoom_speed"),
            )
            self.ptz.on_zoom_end = self._on_zoom_end
            self.ptz.heal_autofocus(self.settings.manual_focus(camera.id))
            if camera.profile.keep_awake_s:
                self._keep_awake.start(round(camera.profile.keep_awake_s * 1000))
        self.stream.start(camera.path, mode)
        if self.tracking_possible:
            self.helper.read()

    def _close_camera(self) -> None:
        if self.recorder.recording:
            self.recorder.stop()
        self._keep_awake.stop()
        self._tick.stop()
        self._keys_down.clear()
        if self.ptz is not None:
            self.ptz.stop()
            self.ptz = None
        self.stream.stop()
        if self.device is not None:
            self.device.close()
            self.device = None
        self.camera = None
        self._latest = None
        self.helper.forget()
        for key in ("tracking", "framing", "mirror", *obsbot.GESTURES):
            self._pending.pop(key, None)

    def select_camera(self, camera_id: str) -> bool:
        if self.recorder.busy:
            self.say("Stop recording before switching cameras.")
            return False
        if camera_id != "off" and all(c.id != camera_id for c in self.cameras):
            self.say("That camera isn't connected.")
            return False
        if camera_id == self.settings.camera and "camera" not in self._pending:
            if camera_id == "off" or self.stream.running:
                return True
        self.settings.camera = camera_id
        self._set_pending("camera", camera_id)
        self._retry_at = 0.0
        self.apply_camera()
        self.say("Camera off." if camera_id == "off" else f"Switching to {self.camera_name(camera_id)}.")
        return True

    def poll(self) -> None:
        """Every two seconds: notice cameras plugged in or pulled out, and keep the status true."""
        self.cameras = self.index.refresh()
        chosen = self.settings.camera
        present = any(c.id == chosen for c in self.cameras)
        if present and not self.stream.running and time.monotonic() >= self._retry_at:
            self.apply_camera()
            return
        if not present and self.camera is not None:
            self._close_camera()
        if self.ptz is not None and self.camera is not None and not self.ptz.active:
            self.ptz.heal_autofocus(self.settings.manual_focus(self.camera.id))
        self.refresh()

    def _on_stream_stopped(self, message: str) -> None:
        was = self.camera
        self._close_camera()
        self.stream_error = message if was is not None else ""
        self._retry_at = time.monotonic() + RETRY_S
        self._pending.pop("camera", None)
        self.refresh()

    # ---- the picture -----------------------------------------------------------
    def _on_frame(self, jpeg: bytes) -> None:
        self._latest = jpeg
        if self.recorder.recording:
            self.recorder.write(jpeg)
        if not self._decode_queued:  # draw at most one frame per pass through the event loop: the newest
            self._decode_queued = True
            QTimer.singleShot(0, self._draw_latest)

    def _draw_latest(self) -> None:
        self._decode_queued = False
        if self._latest is not None and not self.isMinimized():
            self.preview.show_jpeg(self._latest)

    def snap(self) -> str | None:
        """Save the newest frame exactly as the camera sent it. Returns the file's path."""
        if not self.stream.live or self._latest is None:
            self.say("No picture to save: " + self._status_text()[0], notify=True)
            return None
        folder = self.settings.output_dir / "snaps"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{stamp}.jpg"
            count = 1
            while path.exists():
                count += 1
                path = folder / f"{stamp}-{count}.jpg"
            path.write_bytes(self._latest)
        except OSError as error:
            self.say(f"Snapshot failed: {error.strerror or error}", notify=True)
            return None
        self.say(f"Snapshot saved: {path.name}", notify=True)
        return str(path)

    # ---- recording -------------------------------------------------------------
    def toggle_record(self) -> bool:
        if self.recorder.recording:
            self.recorder.stop()
            self.say("Finishing the recording…")
            self.refresh()
            return True
        if self.recorder.busy:
            return False  # still closing the last file
        if not self.stream.live or self.camera is None or self.stream.mode is None:
            self.say("Can't record: " + self._status_text()[0], notify=True)
            return False
        mode = self.stream.mode
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            encoder = choose_encoder(self.settings.get("encoder"), mode.width, mode.height)
            microphone = microphone_for(self.camera.id, self.camera.name) if self.settings.get("audio") == "on" else ""
        finally:
            QApplication.restoreOverrideCursor()
        if encoder is None:
            self.say("Can't record: ffmpeg has no working H.264 encoder here.", notify=True)
            return False
        self.recorder.start(
            Recipe(
                folder=self.settings.output_dir / "rec",
                mode=mode,
                container=self.settings.get("format"),
                quality=self.settings.get("quality"),
                split_minutes=self.settings.get("split"),
                microphone=microphone,
                encoder=encoder,
            )
        )
        sound = "with sound" if microphone else "no sound"
        self.say(f"Recording started ({mode.label}, {sound}, encoded on the {encoder.label}).", notify=True)
        self.refresh()
        return True

    def _on_recorder_stopped(self, error: str) -> None:
        if error:
            self.say(f"Recording stopped: {error}", notify=True)
        else:
            newest = newest_recording(self.settings.output_dir / "rec")
            self.say(f"Recording saved: {newest[0]} ({size_text(newest[1])})" if newest else "Recording stopped.", notify=True)
        if self._quitting:
            return
        self.apply_camera()  # a size or frame rate chosen while recording applies now

    def set_record_option(self, key: str, value) -> None:
        if not self.settings.set(key, value):
            return
        if key in ("res", "fps"):
            if self.recorder.busy:
                self.say("Applies when this recording ends.")
            else:
                self._set_pending(key, value)
                self.apply_camera()
        self.refresh()

    # ---- moving ----------------------------------------------------------------
    def _sync_tick(self) -> None:
        if self.ptz is not None and self.ptz.needs_ticks:
            if not self._tick.isActive():
                self._tick.start()
        else:
            self._tick.stop()

    def _on_tick(self) -> None:
        if self.ptz is not None:
            self.ptz.tick()
        self._sync_tick()
        if not self._tick.isActive():
            self._settle.start()

    def move_start(self, direction: str) -> bool:
        if self.ptz is None or not self.ptz.can_move or direction not in DIRECTIONS:
            return False
        self.ptz.press(direction)
        self._sync_tick()
        return True

    def move_stop(self, direction: str) -> bool:
        if self.ptz is None:
            return False
        self.ptz.release(direction)
        self._sync_tick()
        self._settle.start()
        return True

    def zoom_start(self, direction: int) -> bool:
        if self.ptz is None or not self.ptz.can_zoom:
            return False
        self.ptz.zoom_press(direction)
        self._sync_tick()
        return True

    def zoom_stop(self, direction: int) -> bool:
        if self.ptz is None:
            return False
        self.ptz.zoom_release(direction)
        self._sync_tick()
        self._settle.start()
        return True

    def center(self) -> bool:
        if self.ptz is None or not self.ptz.center():
            return False
        self.say("Centering.")
        self.later(3.5, self.refresh)
        return True

    def toggle_focus(self) -> bool:
        if self.ptz is None or self.camera is None or not self.ptz.can_focus:
            return False
        turn_on = self.ptz.autofocus() is not True
        self.ptz.set_autofocus(turn_on)
        self.settings.set_manual_focus(self.camera.id, not turn_on)
        self.say("Autofocus on." if turn_on else "Autofocus off (manual focus).", notify=True)
        self.refresh()
        return True

    def _on_zoom_end(self) -> None:
        # While zooming at a steady speed some cameras stop updating zoom_absolute.
        # Where the SDK helper is present, ask it for the real zoom and write that back.
        if self.tracking_possible:
            self._zoom_token += 1
            token = self._zoom_token
            self.later(0.4, lambda: self.helper.read_zoom(token))

    def _on_zoom_read(self, zoom: int, token: int) -> None:
        if token == self._zoom_token and self.ptz is not None and self.ptz.zoom_dir == 0 and self.device is not None:
            self.device.set("zoom_absolute", zoom)
            self.refresh()

    # ---- OBSBOT extras ---------------------------------------------------------
    @property
    def tracking_possible(self) -> bool:
        return self.camera is not None and self.camera.profile.sdk == "obsbot" and self.helper.available

    def set_tracking(self, mode: str) -> bool:
        """Start a tracking mode; the mode that is already on turns tracking off."""
        if not self.tracking_possible or (mode != "off" and mode not in obsbot.TRACKING):
            return False
        current = self._pending["tracking"][0] if "tracking" in self._pending else self.helper.state.get("tracking", "off")
        target = "off" if current == mode else mode
        self._set_pending("tracking", target)
        self.helper.set_tracking(target)
        self.say("Tracking off…" if target == "off" else f"Tracking: {obsbot.TRACKING[target][0]}…")
        return True

    def set_camera_setting(self, key: str, value) -> None:
        if not self.tracking_possible:
            return
        self._set_pending(key, value)
        if key == "framing":
            self.helper.set_framing(value)
        elif key == "mirror":
            self.helper.set_mirror(bool(value))
        else:
            self.helper.set_gesture(key, bool(value))

    def _toggle_setting(self, key: str) -> None:
        current = self._pending[key][0] if key in self._pending else self.helper.state.get(key)
        if isinstance(current, bool):
            self.set_camera_setting(key, not current)
        self.refresh()

    def _on_helper_failed(self, reason: str) -> None:
        for key in ("tracking", "framing", "mirror", *obsbot.GESTURES):
            self._pending.pop(key, None)
        self.say(f"The camera refused that: {reason}", notify=True)
        self.refresh()

    def _on_tab(self, index: int) -> None:
        if index == 1 and self.tracking_possible:
            self.helper.read()
        self.refresh()

    # ---- slow changes flash until the camera confirms them -----------------------
    def _set_pending(self, key: str, value) -> None:
        self._pending[key] = (value, time.monotonic())
        self._blink = True
        self._blinker.start()

    def _shows(self, key: str, value, actual) -> bool:
        """Whether a choice looks selected: flashing while it is being applied, solid once it is true."""
        if key in self._pending:
            return self._pending[key][0] == value and self._blink
        return actual == value

    def _confirmed(self, key: str, value) -> bool:
        if key == "camera":
            if value == "off":
                return self.settings.camera == "off" and not self.stream.running
            return self.stream.live and self.camera is not None and self.camera.id == value
        if key in ("res", "fps"):
            if self.settings.get(key) != value:
                return True  # replaced by a newer choice
            if self.camera is None or self.recorder.busy:
                return True  # nothing running to switch, or it waits for the recording
            return self.stream.live and self.stream.mode == self.wanted_mode(self.camera)
        return self.helper.state.get(key) == value

    def _check_pending(self) -> None:
        now = time.monotonic()
        for key, (value, since) in list(self._pending.items()):
            if self._confirmed(key, value):
                del self._pending[key]
            elif now - since > PENDING_S:
                del self._pending[key]
                self.say("The camera didn't confirm that change; showing what it reports.", notify=True)
        if not self._pending:
            self._blinker.stop()
            self._blink = True

    def _on_blink(self) -> None:
        self._blink = not self._blink
        if self.helper.available and not self.helper.busy and any(k not in ("camera", "res", "fps") for k in self._pending):
            self.helper.read()
        self.refresh()

    # ---- showing the state -----------------------------------------------------
    def _status_text(self) -> tuple[str, bool]:
        """The headline and whether it is a problem."""
        chosen = self.settings.camera
        if chosen == "":
            return "Choose a camera to start", False
        if chosen == "off":
            return "Camera off", False
        name = self.camera_name(chosen)
        if all(c.id != chosen for c in self.cameras):
            return f"{name} is not connected", True
        if self.stream_error:
            return f"{name}: {self.stream_error}", True
        if not self.stream.live:
            return f"Starting {name}…", False
        position = self.ptz.position() if self.ptz else None
        if position is None:
            return (name if self.ptz else f"{name} · no pan, tilt or zoom"), False
        zoom = self.ptz.zoom()
        return f"Pan {position[0]}° · tilt {position[1]}°" + (f" · zoom {zoom}" if zoom is not None else ""), False

    def refresh(self) -> None:
        self._check_pending()
        status, problem = self._status_text()
        live = self.stream.live
        recording = self.recorder.recording
        self.status_label.setText(status)
        set_role(self.status_label, "error" if problem else "status")
        if self.recorder.busy:
            newest = newest_recording(self.settings.output_dir / "rec")
            state = (
                f"Recording · {newest[0]} ({size_text(newest[1])})"
                if newest and recording
                else "Recording…"
                if recording
                else "Finishing the recording…"
            )
        elif live and self.stream.mode:
            state = f"{self.camera.name} · {self.stream.mode.label}" if self.ptz and self.camera else self.stream.mode.label
        else:
            state = ""
        self.state_label.setText(state)
        self.state_label.setVisible(bool(state))
        self.setWindowTitle(f"{'● REC · ' if recording else ''}{APP_NAME}")
        self.preview.set_recording(recording)
        if not live:
            hint = "\n\nPick one in the Camera row." if self.settings.camera in ("", "off") and self.side.isVisible() else ""
            self.preview.clear(status + hint)

        self._refresh_camera_row()
        self.lock_label.setVisible(self.recorder.busy)

        # Controls
        can_move = live and self.ptz is not None and self.ptz.can_move
        can_zoom = live and self.ptz is not None and self.ptz.can_zoom
        can_focus = live and self.ptz is not None and self.ptz.can_focus
        tracking = live and self.tracking_possible
        self._layout_keys(self.camera is not None and self.camera.profile.sdk == "obsbot")
        for key in "IJKL":
            self.caps[key].setEnabled(can_move)
        for key in "HN":
            self.caps[key].setEnabled(can_zoom)
        self.caps["U"].setEnabled(live)
        self.caps["O"].setEnabled(live or self.recorder.recording)
        self.caps["O"].set_face(
            symbol="■" if recording else "●", caption="Stop" if recording else "Record", lit=recording, urgent=recording
        )
        autofocus = self.ptz.autofocus() if can_focus else None
        self.caps["P"].setEnabled(can_focus)
        self.caps["P"].set_face(
            caption="Focus off" if autofocus is False else "Autofocus", lit=autofocus is True, urgent=autofocus is False
        )
        self.caps["M"].set_face(caption="Hide panel")
        self.caps["Space"].set_face(lit=self.isFullScreen())
        active_mode = self.helper.state.get("tracking", "off")
        for digit, mode in obsbot.TRACKING_KEYS.items():
            self.caps[digit].setEnabled(tracking)
            self.caps[digit].set_face(lit=tracking and self._shows("tracking", mode, active_mode))
        self.center_button.setEnabled(can_move and self.ptz.has_absolute)
        self.center_button.setVisible(self.ptz is not None or not live)
        if live and self.ptz is None:
            note = "This camera can't move, zoom or track. Snapshot, record and preview still work."
        elif self.camera is not None and self.camera.profile.sdk == "obsbot" and not self.helper.available:
            note = f"Tap a move key to jump {self.settings.get('step_degrees')}° · hold it to glide. Tracking keys 4–0 need the optional obsbot-ai helper (see the README)."
        elif tracking:
            note = f"Tap a move key to jump {self.settings.get('step_degrees')}° · hold it to glide. 4–0 start tracking; press the lit one again to stop (takes about 5 s)."
        else:
            note = f"Tap a move key to jump {self.settings.get('step_degrees')}° · hold it to glide."
        self.keys_note.setText(note)

        # Camera tab
        state = self.helper.state
        self.cam_settings.setVisible(tracking)
        if tracking:
            self.cam_note.setText(
                "Settings stored on the camera. Changes take about 5 seconds to reach it."
                if state
                else "Reading the camera's settings (about 5 seconds)…"
            )
            for value, button in self.framing_row.buttons.items():
                button.setChecked(self._shows("framing", value, state.get("framing")))
                button.setEnabled("framing" in state)
            for key, button in self.toggles.items():
                value = self._pending[key][0] if key in self._pending else state.get(key)
                button.setText("On" if value is True else "Off" if value is False else "–")
                button.setChecked(self._blink if key in self._pending else value is True)
                button.setEnabled(isinstance(value, bool))
        elif self.camera is not None and self.camera.profile.sdk == "obsbot":
            self.cam_note.setText(
                "Tracking style, gestures and mirroring live on the camera and need the optional obsbot-ai helper. See the README for how to build it."
            )
        elif self.camera is not None:
            self.cam_note.setText(f"{self.camera.name} has no settings of its own here.")
        else:
            self.cam_note.setText("No camera is running.")

        # Recording tab
        offered = sizes_offered(self.camera.modes) if self.camera else set()
        resolutions = [(k, k if k != "4k" else "4K") for k, size in RESOLUTIONS.items() if not offered or size in offered]
        self.rec_rows["res"].set_options(resolutions or [(k, k) for k in RESOLUTIONS])
        mode = self.stream.mode if self.stream.running else None
        running_res = next((k for k, size in RESOLUTIONS.items() if mode and size == (mode.width, mode.height)), None)
        saved_res = self.settings.get("res")
        shown_res = saved_res if saved_res in self.rec_rows["res"].buttons or running_res is None else running_res
        for value, button in self.rec_rows["res"].buttons.items():
            button.setChecked(self._shows("res", value, shown_res))
        rates = self._rates_for_wanted_size()
        for value, button in self.rec_rows["fps"].buttons.items():
            button.setChecked(
                self._shows(
                    "fps", value, mode.fps if mode and "fps" not in self._pending and not self.recorder.busy else self.settings.get("fps")
                )
            )
            button.setEnabled(not rates or value in rates)
        self.rec_rows["fps"].set_note("" if not rates or 60 in rates else "This camera does this size at up to 30 fps.")
        for key in ("format", "quality", "audio", "split"):
            self.rec_rows[key].show_state(self.settings.get(key))
        self.rec_note.setText(
            (f"Camera running at {mode.label}. " if mode and live else "")
            + "Size and frame rate switch right away unless recording; a change made while recording applies when it ends."
        )

    def _rates_for_wanted_size(self) -> set[int]:
        if self.camera is None:
            return set()
        wanted = self.wanted_mode(self.camera)
        rates: set[int] = set()
        for mode in self.camera.modes:
            if (mode.width, mode.height) == (wanted.width, wanted.height):
                rates.update(mode.rates)
        return rates

    def _refresh_camera_row(self) -> None:
        chosen = self.settings.camera
        entries = [(c.id, c.name, True) for c in self.cameras]
        if chosen not in ("", "off") and all(c.id != chosen for c in self.cameras):
            entries.insert(0, (chosen, f"{self.settings.camera_name(chosen)} (not connected)", False))
        entries.append(("off", "Off", True))
        if entries != self._camera_row_key:
            self._camera_row_key = entries
            self.camera_flow.clear()
            self.camera_buttons = {}
            for camera_id, name, _present in entries:
                button = choice_button(name)
                button.clicked.connect(lambda _checked=False, camera_id=camera_id: self.select_camera(camera_id) or self.refresh())
                self.camera_flow.addWidget(button)
                self.camera_buttons[camera_id] = button
            self.camera_flow.invalidate()
        for camera_id, _name, present in entries:
            button = self.camera_buttons[camera_id]
            button.setChecked(self._shows("camera", camera_id, chosen))
            button.setEnabled(present and not self.recorder.busy)

    # ---- window ----------------------------------------------------------------
    def toggle_panel(self) -> None:
        self.side.setVisible(not self.side.isVisible())
        self.refresh()

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.side.setVisible(self._panel_before_fullscreen)
        else:
            self._panel_before_fullscreen = self.side.isVisible()
            self.side.setVisible(False)
            self.showFullScreen()
        self.refresh()

    def _open_folder(self) -> None:
        folder = self.settings.output_dir
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        modifiers = event.modifiers() & ~(Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier)
        if modifiers == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_Q:
            self.close()
            return
        if modifiers or event.isAutoRepeat():
            return
        if key in MOVE_KEYS:
            if self.move_start(MOVE_KEYS[key]):
                self._keys_down.add(("move", MOVE_KEYS[key]))
        elif key in ZOOM_KEYS:
            if self.zoom_start(ZOOM_KEYS[key]):
                self._keys_down.add(("zoom", ZOOM_KEYS[key]))
        elif key == Qt.Key.Key_U:
            self.snap()
        elif key == Qt.Key.Key_O:
            self.toggle_record()
        elif key == Qt.Key.Key_P:
            self.toggle_focus()
        elif key == Qt.Key.Key_C:
            self.center()
        elif key == Qt.Key.Key_M:
            self.toggle_panel()
        elif key in (Qt.Key.Key_Space, Qt.Key.Key_F, Qt.Key.Key_F11):
            self.toggle_fullscreen()
        elif key == Qt.Key.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()
        elif event.text() in obsbot.TRACKING_KEYS:
            self.set_tracking(obsbot.TRACKING_KEYS[event.text()])
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.isAutoRepeat():
            return
        key = event.key()
        if key in MOVE_KEYS and ("move", MOVE_KEYS[key]) in self._keys_down:
            self._keys_down.discard(("move", MOVE_KEYS[key]))
            self.move_stop(MOVE_KEYS[key])
        elif key in ZOOM_KEYS and ("zoom", ZOOM_KEYS[key]) in self._keys_down:
            self._keys_down.discard(("zoom", ZOOM_KEYS[key]))
            self.zoom_stop(ZOOM_KEYS[key])
        else:
            super().keyReleaseEvent(event)

    def changeEvent(self, event) -> None:
        # A key held when the window loses focus never sends its release here.
        if event.type() == event.Type.ActivationChange and not self.isActiveWindow():
            for kind, value in list(self._keys_down):
                self.move_stop(value) if kind == "move" else self.zoom_stop(value)
            self._keys_down.clear()
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        self._quitting = True
        self._poll.stop()
        if not self.isFullScreen():
            self.settings.set_window_state({"width": self.width(), "height": self.height(), "panel": self.side.isVisible()})
        if self.recorder.busy:  # let ffmpeg close the file properly
            loop = QEventLoop(self)
            self.recorder.stopped.connect(loop.quit)
            self.recorder.stop()
            QTimer.singleShot(9000, loop.quit)
            loop.exec()
        self._close_camera()
        super().closeEvent(event)

    # ---- commands from outside (quickcam ctl ...) --------------------------------
    def status(self) -> dict:
        position = self.ptz.position() if self.ptz else None
        mode = self.stream.mode if self.stream.running else None
        return {
            "ok": True,
            "camera": self.settings.camera,
            "name": self.camera_name(self.settings.camera) if self.settings.camera not in ("", "off") else "",
            "status": self._status_text()[0],
            "live": self.stream.live,
            "recording": self.recorder.recording,
            "mode": mode.label if mode else "",
            "pan": position[0] if position else None,
            "tilt": position[1] if position else None,
            "zoom": self.ptz.zoom() if self.ptz else None,
            "autofocus": self.ptz.autofocus() if self.ptz else None,
            "tracking": self.helper.state.get("tracking") if self.tracking_possible else None,
            "cameras": [{"id": c.id, "name": c.name, "ptz": c.ptz} for c in self.cameras],
        }

    def command(self, words: list[str]) -> dict:
        name, args = words[0], words[1:]
        arg = args[0] if args else ""
        zoom = {"in": 1, "out": -1}.get(arg, 0)

        def done(ok: bool, error: str = "not possible right now") -> dict:
            return {"ok": True} if ok else {"ok": False, "error": error}

        if name == "status":
            return self.status()
        if name == "show":
            self.showNormal() if self.isMinimized() else self.show()
            self.raise_()
            self.activateWindow()
            return done(True)
        if name == "snap":
            path = self.snap()
            return {"ok": True, "file": path} if path else done(False, "no picture to save")
        if name == "record":
            return {"ok": self.toggle_record(), "recording": self.recorder.recording}
        if name == "move-start":
            return done(self.move_start(arg))
        if name == "move-stop":
            return done(self.move_stop(arg))
        if name == "move":  # one tap
            return done(self.move_start(arg) and self.move_stop(arg))
        if name == "zoom-start" and zoom:
            return done(self.zoom_start(zoom))
        if name == "zoom-stop" and zoom:
            return done(self.zoom_stop(zoom))
        if name == "zoom" and zoom:  # one tap
            return done(self.zoom_start(zoom) and self.zoom_stop(zoom))
        if name == "center":
            return done(self.center())
        if name == "focus":
            return done(self.toggle_focus())
        if name == "track":
            return done(self.set_tracking(arg), "tracking is not available")
        if name == "camera" and arg:
            ok = self.select_camera(arg)
            self.refresh()
            return done(ok, "that camera isn't connected, or a recording is running")
        if name == "fullscreen":
            self.toggle_fullscreen()
            return done(True)
        if name == "panel":
            self.toggle_panel()
            return done(True)
        if name == "quit":
            QTimer.singleShot(0, self.close)
            return done(True)
        return done(False, "unknown command")
