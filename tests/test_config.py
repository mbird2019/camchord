import json

from camchord.config import Settings


def test_defaults(tmp_path):
    s = Settings(tmp_path / "settings.json")
    assert s.camera == ""
    assert s.get("res") == "1080p" and s.get("fps") == 30 and s.get("format") == "mkv"
    assert s.get("step_degrees") == 20


def test_bad_values_fall_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"res": "8k", "fps": "fast", "step_degrees": 999, "camera": "../../etc/passwd", "split": True}))
    s = Settings(path)
    assert s.get("res") == "1080p" and s.get("fps") == 30
    assert s.get("step_degrees") == 45  # clamped into range
    assert s.get("split") == 30
    assert s.camera == ""


def test_unreadable_file_is_treated_as_empty(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert Settings(path).get("quality") == "standard"


def test_set_validates_and_persists(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(path)
    assert s.set("res", "4k") and not s.set("res", "5k") and not s.set("nonsense", 1)
    assert not s.set("hold_speed", 1000)
    s.camera = "usb-Some_Camera-video-index0"
    s.set_manual_focus("usb-Some_Camera-video-index0", True)
    again = Settings(path)
    assert again.get("res") == "4k"
    assert again.camera == "usb-Some_Camera-video-index0"
    assert again.manual_focus("usb-Some_Camera-video-index0")
    again.set_manual_focus("usb-Some_Camera-video-index0", False)
    assert not Settings(path).manual_focus("usb-Some_Camera-video-index0")


def test_off_is_a_valid_camera_choice(tmp_path):
    s = Settings(tmp_path / "settings.json")
    s.camera = "off"
    assert Settings(s.path).camera == "off"
