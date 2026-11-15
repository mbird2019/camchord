"""`camchord doctor`: check this computer, say what is missing and how to fix it.

It prints one line per check ("ok", "warn" or "FAIL") and returns 1 only when the window
cannot work at all. Nothing here opens a camera for streaming or changes anything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from . import APP_NAME, __version__

QT_PLUGIN_HINT = (
    "Qt cannot start its window plugin. On X11 it usually needs the xcb-cursor library: "
    "Debian/Ubuntu `sudo apt install libxcb-cursor0`, Fedora `sudo dnf install xcb-util-cursor`, "
    "Arch `sudo pacman -S xcb-util-cursor`."
)


def _line(state: str, text: str) -> None:
    print(f"{state:>5}  {text}")


def _qt_check() -> bool:
    try:
        import PySide6
    except ImportError:
        _line("FAIL", "PySide6 is not installed in this Python (start CamChord with ./camchord, which installs it)")
        return False
    _line("ok", f"PySide6 {PySide6.__version__}")
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        _line("warn", "no graphical session in this shell (DISPLAY and WAYLAND_DISPLAY are empty); window not tested")
        return True
    code = "from PySide6.QtWidgets import QApplication; QApplication([])"
    try:
        run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        _line("FAIL", "could not test that Qt opens a window")
        return False
    if run.returncode != 0:
        _line("FAIL", QT_PLUGIN_HINT if "platform plugin" in run.stderr else f"Qt failed to start: {run.stderr.strip()[-200:]}")
        return False
    _line("ok", "Qt can open a window")
    return True


def _ffmpeg_check() -> bool:
    if not shutil.which("ffmpeg"):
        _line("FAIL", "ffmpeg not found (Debian/Ubuntu `sudo apt install ffmpeg`, Fedora `sudo dnf install ffmpeg-free`, Arch `sudo pacman -S ffmpeg`)")
        return False
    from .recorder import choose_encoder

    _line("ok", "ffmpeg found")
    encoder = choose_encoder("auto", 1920, 1080)
    if encoder is None:
        _line("warn", "ffmpeg has no video encoder that works here: preview and snapshots work, recording does not")
    else:
        _line("ok", f"recording would encode on the {encoder.label}")
    return True


def _camera_check() -> None:
    from .devices import CameraIndex, video_nodes

    nodes = video_nodes()
    if not nodes:
        _line("warn", "no camera found (no /dev/video* devices): plug one in")
        return
    blocked = [n for n in nodes if not os.access(n, os.R_OK | os.W_OK)]
    cameras = CameraIndex().refresh()
    for camera in cameras:
        extra = "pan, tilt and zoom" if camera.ptz else "picture only"
        _line("ok", f"camera: {camera.name} ({extra})")
    if blocked and not cameras:
        _line("FAIL", f"cannot open {blocked[0]}: {_permission_hint()}")
    elif blocked:
        _line("warn", f"{len(blocked)} video device(s) are not readable (fine if they are not cameras): {_permission_hint()}")
    elif not cameras:
        _line("warn", "video devices exist but none is a capture camera")
    if any(c.profile.sdk == "obsbot" for c in cameras):
        from .obsbot import find_helper

        if find_helper():
            _line("ok", "obsbot-ai helper found (tracking modes available)")
        else:
            _line("warn", "OBSBOT tracking needs the optional obsbot-ai helper (contrib/obsbot-ai); everything else works without it")


def _permission_hint() -> str:
    import grp

    try:
        in_group = grp.getgrnam("video").gr_gid in os.getgroups()
    except KeyError:
        return "no 'video' group on this system; check the device permissions"
    if in_group:
        return "you are in the 'video' group already; is another program holding it, or is this a remote login?"
    return "add yourself to the video group (`sudo usermod -aG video $USER`, then log out and in), or log in at the computer's own screen"


def run() -> int:
    print(f"{APP_NAME} {__version__} on Python {sys.version.split()[0]}")
    ok = True
    if sys.version_info < (3, 10):
        _line("FAIL", "Python 3.10 or newer is needed")
        ok = False
    ok = _qt_check() and ok
    ok = _ffmpeg_check() and ok
    _camera_check()
    if not shutil.which("pactl"):
        _line("warn", "pactl not found: recordings will have no sound (install pulseaudio-utils or pipewire-pulse)")
    print("ready" if ok else "not ready: fix the FAIL lines above")
    return 0 if ok else 1
