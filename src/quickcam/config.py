"""Settings and file locations.

Settings are one JSON file. Unknown or out-of-range values fall back to the
default, so a hand-edited file can never put the app in a state the UI cannot show.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from . import APP_ID

RESOLUTIONS = {"4k": (3840, 2160), "1440p": (2560, 1440), "1080p": (1920, 1080), "720p": (1280, 720), "480p": (640, 480)}
QUALITY_QP = {"high": 18, "standard": 24, "small": 30}

# key -> (default, allowed values or (min, max))
_CHOICES: dict[str, tuple[object, tuple]] = {
    "res": ("1080p", tuple(RESOLUTIONS)),
    "fps": (30, (30, 60)),
    "format": ("mkv", ("mkv", "mp4")),
    "quality": ("standard", tuple(QUALITY_QP)),
    "audio": ("on", ("on", "off")),
    "split": (30, (10, 30, 60)),
    "encoder": ("auto", ("auto", "vaapi", "software")),
}
_RANGES: dict[str, tuple[int, tuple[int, int]]] = {
    "step_degrees": (20, (1, 45)),
    "hold_speed": (15, (5, 120)),
    "zoom_speed": (5, (1, 20)),
}
CAMERA_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,200}$")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / APP_ID


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    return Path(base) if base else config_dir()


def videos_dir() -> Path:
    try:
        out = subprocess.run(["xdg-user-dir", "VIDEOS"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    home = os.path.expanduser("~")
    return Path(out) if out and out != home else Path(home) / "Videos"


class Settings:
    """The saved choices. ``camera`` is a stable camera id, "off", or "" (nothing chosen yet)."""

    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "settings.json"
        self._data: dict = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        self._data = data if isinstance(data, dict) else {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.new")
            tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n")
            os.replace(tmp, self.path)
        except OSError:
            pass  # a read-only home must not stop the camera from working

    # ---- validated access ---------------------------------------------------
    def get(self, key: str):
        value = self._data.get(key)
        if key in _CHOICES:
            default, allowed = _CHOICES[key]
            return value if value in allowed else default
        if key in _RANGES:
            default, (lo, hi) = _RANGES[key]
            return max(lo, min(hi, value)) if isinstance(value, int) and not isinstance(value, bool) else default
        raise KeyError(key)

    def set(self, key: str, value) -> bool:
        if key in _CHOICES:
            if value not in _CHOICES[key][1]:
                return False
        elif key in _RANGES:
            lo, hi = _RANGES[key][1]
            if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
                return False
        else:
            return False
        self._data[key] = value
        self.save()
        return True

    @staticmethod
    def choices(key: str) -> tuple:
        return _CHOICES[key][1]

    @property
    def camera(self) -> str:
        value = self._data.get("camera", "")
        return value if isinstance(value, str) and (value == "off" or CAMERA_ID.match(value)) else ""

    @camera.setter
    def camera(self, value: str) -> None:
        if value != "off" and not CAMERA_ID.match(value):
            raise ValueError("not a camera id")
        self._data["camera"] = value
        self.save()

    @property
    def output_dir(self) -> Path:
        value = self._data.get("output_dir")
        if isinstance(value, str) and value.startswith(("/", "~")):
            return Path(os.path.expanduser(value))
        return videos_dir() / APP_ID

    def manual_focus(self, camera_id: str) -> bool:
        """True if the user switched autofocus off for this camera (so the app leaves it off)."""
        value = self._data.get("manual_focus")
        return isinstance(value, list) and camera_id in value

    def set_manual_focus(self, camera_id: str, manual: bool) -> None:
        current = self._data.get("manual_focus")
        cameras = {c for c in current if isinstance(c, str)} if isinstance(current, list) else set()
        if manual:
            cameras.add(camera_id)
        else:
            cameras.discard(camera_id)
        self._data["manual_focus"] = sorted(cameras)
        self.save()

    def camera_name(self, camera_id: str) -> str:
        names = self._data.get("camera_names")
        name = names.get(camera_id) if isinstance(names, dict) else None
        return name[:60] if isinstance(name, str) and name else camera_id

    def remember_name(self, camera_id: str, name: str) -> None:
        names = self._data.get("camera_names")
        names = dict(names) if isinstance(names, dict) else {}
        if names.get(camera_id) != name:
            names[camera_id] = name
            self._data["camera_names"] = names
            self.save()

    def window_state(self) -> dict:
        value = self._data.get("window")
        return value if isinstance(value, dict) else {}

    def set_window_state(self, state: dict) -> None:
        self._data["window"] = state
        self.save()

    @property
    def obsbot_helper(self) -> str:
        value = self._data.get("obsbot_helper")
        return value if isinstance(value, str) else ""
