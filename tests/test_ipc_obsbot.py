from quickcam import ipc
from quickcam.obsbot import parse_status


def test_request_validation():
    assert ipc.parse_request(b'["move-start","left"]') == ["move-start", "left"]
    assert ipc.parse_request(b'"snap"') is None
    assert ipc.parse_request(b"[]") is None
    assert ipc.parse_request(b'["a","b","c","d","e"]') is None
    assert ipc.parse_request(b'["snap", 1]') is None
    assert ipc.parse_request(b"\xff\xfe") is None


def test_send_reports_no_app_running(tmp_path):
    assert ipc.send(["status"], path=tmp_path / "nobody.sock") is None


def test_helper_status_maps_to_tracking_modes():
    line = '{"ok":true,"mode":"human","sub":"upper","framing":"motion","gestureTarget":true,"gestureZoom":false,"gestureDynamicZoom":false,"mirror":false,"zoom":37}'
    state = parse_status(line)
    assert state["tracking"] == "upper" and state["framing"] == "motion" and state["zoom"] == 37
    assert state["gestureTarget"] is True and state["mirror"] is False
    assert parse_status('{"ok":true,"mode":"none","sub":"normal"}')["tracking"] == "off"
    assert parse_status('{"ok":true,"mode":"human","sub":"normal"}')["tracking"] == "full"
    assert parse_status('{"ok":true,"mode":"group","sub":"normal"}')["tracking"] == "group"


def test_helper_failure_is_none():
    assert parse_status('{"ok":false,"error":"camera not found"}') is None
    assert parse_status("not json") is None
