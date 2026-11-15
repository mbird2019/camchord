"""The window against stand-in cameras: no real device is opened and no ffmpeg is started."""

import pytest

pytest.importorskip("PySide6")

from fakes import obsbot_controls  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from camchord import window as window_module  # noqa: E402
from camchord.config import Settings  # noqa: E402
from camchord.devices import Camera  # noqa: E402
from camchord.profiles import OBSBOT_TINY_2_LITE  # noqa: E402
from camchord.v4l2 import Mode  # noqa: E402

WEBCAM = Camera("usb-Webcam-video-index0", "Integrated Webcam HD", "/dev/null", modes=(Mode("MJPG", 1280, 720, (30,)),))
PTZCAM = Camera(
    "usb-Remo_Tech_Co.__Ltd._OBSBOT_Tiny_2_Lite-video-index0",
    "OBSBOT Tiny 2 Lite",
    "/dev/null",
    ptz=True,
    profile=OBSBOT_TINY_2_LITE,
    modes=(Mode("MJPG", 3840, 2160, (30,)), Mode("MJPG", 1920, 1080, (30, 60))),
)


class FakeIndex:
    def __init__(self, cameras):
        self.cameras = list(cameras)

    def refresh(self):
        return list(self.cameras)


class FakeDevice(type(obsbot_controls())):
    def close(self):
        pass


class FakeStream:
    """Replaces the ffmpeg-backed stream: it is 'live' as soon as it is started."""

    def __init__(self, window):
        self.window = window
        self.mode = None
        self.path = ""
        self.running = False
        self.starts = []

    @property
    def live(self):
        return self.running

    def start(self, path, mode):
        self.path, self.mode, self.running = path, mode, True
        self.starts.append((path, mode))

    def stop(self):
        self.running = False


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_window(app, tmp_path, monkeypatch):
    windows = []

    def factory(cameras, chosen=""):
        settings = Settings(tmp_path / "settings.json")
        settings._data["output_dir"] = str(tmp_path / "out")
        if chosen:
            settings.camera = chosen
        controls = obsbot_controls()
        device = FakeDevice.__new__(FakeDevice)
        device.__dict__.update(controls.__dict__)
        monkeypatch.setattr(window_module.v4l2, "Device", lambda path: device)
        monkeypatch.setattr(window_module.MainWindow, "apply_camera", lambda self: None)
        win = window_module.MainWindow(settings, FakeIndex(cameras))
        monkeypatch.undo()
        monkeypatch.setattr(window_module.v4l2, "Device", lambda path: device)
        win.stream = FakeStream(win)
        win.apply_camera()
        windows.append(win)
        return win, device

    yield factory
    for win in windows:
        win._quitting = True
        win._poll.stop()
        win._close_camera()
        win.deleteLater()


def test_first_run_starts_no_camera(make_window):
    win, _ = make_window([WEBCAM])
    assert not win.stream.running
    assert win.status()["status"] == "Choose a camera to start"
    assert set(win.camera_buttons) == {WEBCAM.id, "off"}


def test_choosing_a_camera_starts_it_at_its_best_fitting_size(make_window):
    win, _ = make_window([WEBCAM])
    assert win.command(["camera", WEBCAM.id]) == {"ok": True}
    assert win.stream.running and win.stream.mode.label == "1280x720 @ 30 fps"
    assert win.settings.camera == WEBCAM.id
    assert win.ptz is None
    assert not win.caps["J"].isEnabled() and win.caps["U"].isEnabled()
    assert win.command(["move", "left"])["ok"] is False


def test_off_stops_the_camera_and_nothing_restarts_it(make_window):
    win, _ = make_window([WEBCAM], chosen=WEBCAM.id)
    assert win.stream.running
    win.command(["camera", "off"])
    assert not win.stream.running and win.settings.camera == "off"
    win.poll()
    assert not win.stream.running
    assert win.command(["snap"])["ok"] is False
    assert win.command(["record"])["ok"] is False


def test_unplugged_chosen_camera_stays_chosen_and_no_other_takes_over(make_window):
    win, _ = make_window([WEBCAM], chosen=PTZCAM.id)
    assert not win.stream.running
    assert "not connected" in win.status()["status"]
    win.poll()
    assert not win.stream.running and win.settings.camera == PTZCAM.id
    assert not win.camera_buttons[PTZCAM.id].isEnabled()
    win.index.cameras.append(PTZCAM)  # plugged back in
    win.poll()
    assert win.stream.running and win.camera.id == PTZCAM.id


def test_unknown_camera_id_is_refused(make_window):
    win, _ = make_window([WEBCAM])
    assert win.command(["camera", "usb-Nope-video-index0"])["ok"] is False
    assert win.settings.camera == ""


def test_ptz_camera_moves_through_commands(make_window):
    win, device = make_window([WEBCAM, PTZCAM], chosen=PTZCAM.id)
    assert win.ptz is not None and win.caps["J"].isEnabled()
    assert win.stream.mode.label == "1920x1080 @ 30 fps"
    assert win.command(["move", "right"]) == {"ok": True}
    assert device.values["pan_absolute"] == -20 * 3600
    assert win.status()["pan"] == 20
    win.command(["move-start", "up"])
    assert device.values["tilt_speed"] > 0
    win.command(["move-stop", "all"])
    assert device.values["tilt_speed"] == 0
    assert win.command(["focus"]) == {"ok": True}
    assert win.settings.manual_focus(PTZCAM.id) is (device.values["focus_automatic_continuous"] == 0)


def test_snapshot_saves_the_frame_bytes(make_window, tmp_path):
    win, _ = make_window([WEBCAM], chosen=WEBCAM.id)
    win._on_frame(b"\xff\xd8fake\xff\xd9")
    answer = win.command(["snap"])
    assert answer["ok"] and open(answer["file"], "rb").read() == b"\xff\xd8fake\xff\xd9"
    assert win.command(["snap"])["file"] != answer["file"]  # a second one in the same second gets its own name


def test_resolution_change_restarts_the_stream(make_window):
    win, _ = make_window([PTZCAM], chosen=PTZCAM.id)
    win.set_record_option("res", "4k")
    assert win.stream.mode.label == "3840x2160 @ 30 fps"
    assert len(win.stream.starts) == 2
    assert "res" not in win._pending  # confirmed at once: the stand-in stream is live immediately


def test_tracking_needs_the_helper(make_window):
    win, _ = make_window([PTZCAM], chosen=PTZCAM.id)
    win.helper.path = ""
    assert win.command(["track", "upper"])["ok"] is False


def test_unknown_command(make_window):
    win, _ = make_window([WEBCAM])
    assert win.command(["explode"])["ok"] is False
