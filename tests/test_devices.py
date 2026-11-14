import os

from quickcam.devices import friendly_name, pick_mode, sizes_offered, stable_ids
from quickcam.profiles import GENERIC, OBSBOT_TINY_2_LITE, profile_for
from quickcam.v4l2 import Mode, control_name

WEBCAM = (
    Mode("MJPG", 1280, 720, (30,)),
    Mode("MJPG", 640, 480, (30,)),
    Mode("YUYV", 640, 480, (30,)),
    Mode("YUYV", 320, 240, (30,)),
)
PTZ = (Mode("MJPG", 3840, 2160, (30,)), Mode("MJPG", 1920, 1080, (30, 60)), Mode("MJPG", 1280, 720, (30, 60)))


def test_control_names_match_v4l2_ctl():
    assert control_name("Pan, Absolute") == "pan_absolute"
    assert control_name("Focus, Automatic Continuous") == "focus_automatic_continuous"
    assert control_name("Zoom, Continuous") == "zoom_continuous"
    assert control_name("White Balance, Automatic") == "white_balance_automatic"


def test_friendly_name():
    assert friendly_name("Integrated_Webcam_HD: Integrate") == "Integrated Webcam HD"
    assert friendly_name("OBSBOT Tiny 2 Lite: OBSBOT Tiny ") == "OBSBOT Tiny 2 Lite"


def test_exact_mode_is_used():
    mode = pick_mode(PTZ, 1920, 1080, 60)
    assert (mode.input_format, mode.width, mode.height, mode.fps) == ("mjpeg", 1920, 1080, 60)


def test_too_big_a_size_falls_back_to_the_cameras_biggest():
    mode = pick_mode(WEBCAM, 1920, 1080, 30)
    assert (mode.width, mode.height, mode.fps) == (1280, 720, 30)


def test_rate_the_size_cannot_do_falls_to_the_nearest_below():
    assert pick_mode(PTZ, 3840, 2160, 60).fps == 30
    assert pick_mode(WEBCAM, 1280, 720, 60).fps == 30


def test_camera_without_mjpeg_uses_yuyv():
    mode = pick_mode(WEBCAM[2:], 1280, 720, 30)
    assert (mode.input_format, mode.width, mode.height) == ("yuyv422", 640, 480)


def test_sizes_offered_are_those_of_the_preferred_format():
    assert sizes_offered(WEBCAM) == {(1280, 720), (640, 480)}


def test_profiles():
    assert profile_for("OBSBOT Tiny 2 Lite: OBSBOT Tiny ") is OBSBOT_TINY_2_LITE
    assert profile_for("", "usb-Remo_Tech_Co.__Ltd._OBSBOT_Tiny_2_Lite-video-index0") is OBSBOT_TINY_2_LITE
    assert profile_for("Integrated_Webcam_HD", "usb-x-video-index0") is GENERIC


def test_stable_ids_prefer_by_id(tmp_path):
    dev = tmp_path
    (dev / "v4l" / "by-id").mkdir(parents=True)
    (dev / "v4l" / "by-path").mkdir(parents=True)
    for n in (0, 2):
        (dev / f"video{n}").write_text("")
    os.symlink(dev / "video2", dev / "v4l" / "by-id" / "usb-Cam-video-index0")
    os.symlink(dev / "video2", dev / "v4l" / "by-path" / "pci-usb-0:1:1.0-video-index0")
    os.symlink(dev / "video0", dev / "v4l" / "by-path" / "platform-cam-video-index0")
    ids = stable_ids(str(dev))
    assert ids[str(dev / "video2")][0] == "usb-Cam-video-index0"
    assert ids[str(dev / "video0")][0] == "platform-cam-video-index0"
