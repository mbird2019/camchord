from pathlib import Path

from quickcam.devices import StreamMode
from quickcam.recorder import Encoder, Recipe, pick_microphone, record_command
from quickcam.stream import capture_command

MODE = StreamMode("mjpeg", 1920, 1080, 30)


def test_capture_passes_camera_jpeg_through_untouched():
    cmd = capture_command("/dev/v4l/by-id/cam", MODE)
    assert cmd[cmd.index("-input_format") + 1] == "mjpeg"
    assert cmd[cmd.index("-video_size") + 1] == "1920x1080"
    assert cmd[cmd.index("-c:v") + 1] == "copy" and "mjpeg2jpeg" in cmd
    assert cmd[-1] == "pipe:1" and "-nostdin" in cmd


def test_capture_encodes_jpeg_for_cameras_without_it():
    cmd = capture_command("/dev/video0", StreamMode("yuyv422", 640, 480, 30))
    assert cmd[cmd.index("-c:v") + 1] == "mjpeg"


def test_vaapi_mkv_with_sound():
    recipe = Recipe(Path("/out"), MODE, "mkv", "high", 10, "mic.source", Encoder("vaapi", "/dev/dri/renderD128"))
    cmd = record_command(recipe)
    assert cmd[cmd.index("-vaapi_device") + 1] == "/dev/dri/renderD128"
    assert "-nostdin" in cmd  # ffmpeg must not read picture bytes as key presses
    assert cmd[cmd.index("-qp") + 1] == "18"
    assert cmd[cmd.index("-segment_time") + 1] == "600"
    assert ["-f", "pulse", "-i", "mic.source"] == cmd[cmd.index("pulse") - 1 : cmd.index("pulse") + 3]
    assert "-segment_format" not in cmd
    assert cmd[-1] == "/out/%Y%m%d-%H%M%S.mkv"


def test_software_mp4_without_sound_is_fragmented():
    cmd = record_command(Recipe(Path("/out"), MODE, "mp4", "small", 60, "", Encoder("software")))
    assert "libx264" in cmd and "-vaapi_device" not in cmd and "pulse" not in cmd and "1:a" not in cmd
    assert cmd[cmd.index("-crf") + 1] == "30"
    assert "frag_keyframe" in cmd[cmd.index("-segment_format_options") + 1]
    assert cmd[-1].endswith(".mp4")


def test_microphone_prefers_the_cameras_own():
    sources = [
        "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor",
        "alsa_input.pci-0000_00_1f.3.analog-stereo",
        "alsa_input.usb-Remo_Tech_Co.__Ltd._OBSBOT_Tiny_2_Lite-02.analog-stereo",
    ]
    cam = "usb-Remo_Tech_Co.__Ltd._OBSBOT_Tiny_2_Lite-video-index0"
    assert "OBSBOT" in pick_microphone(cam, "OBSBOT Tiny 2 Lite", sources, sources[1])
    assert pick_microphone("usb-Webcam-video-index0", "Integrated Webcam HD", sources, sources[1]) == sources[1]
    assert pick_microphone("usb-Webcam-video-index0", "Integrated Webcam HD", sources, sources[0]) == ""  # never a monitor
