"""Finding cameras and naming them in a way that survives a replug.

A camera is known by its /dev/v4l/by-id name (or by-path, for cameras that have
no by-id entry), never by /dev/videoN: those numbers change with plug order, and
a remembered number can silently point at a different camera after a replug.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import v4l2
from .profiles import GENERIC, Profile, profile_for

DEV = "/dev"
PTZ_CONTROLS = ("pan_absolute", "pan_speed", "tilt_absolute", "tilt_speed", "zoom_absolute", "zoom_continuous")
_INPUT_FORMATS = {"MJPG": "mjpeg", "YUYV": "yuyv422"}


@dataclass(frozen=True)
class Camera:
    id: str
    name: str
    path: str
    present: bool = True
    ptz: bool = False
    profile: Profile = GENERIC
    modes: tuple[v4l2.Mode, ...] = ()


@dataclass(frozen=True)
class StreamMode:
    input_format: str  # ffmpeg's name: "mjpeg", "yuyv422", or "" to let ffmpeg choose
    width: int
    height: int
    fps: int

    @property
    def label(self) -> str:
        return f"{self.width}x{self.height} @ {self.fps} fps"


def friendly_name(card: str) -> str:
    """ "Integrated_Webcam_HD: Integrate" -> "Integrated Webcam HD"."""
    name = " ".join(card.split(":")[0].replace("_", " ").split())
    return "".join(ch for ch in name if ch.isprintable())[:60]


def _node_number(node: str) -> int:
    match = re.search(r"(\d+)$", node)
    return int(match.group(1)) if match else 0


def stable_ids(dev: str = DEV) -> dict[str, tuple[str, str]]:
    """Map each video node ("/dev/video2") to (stable id, stable path). by-id wins over by-path."""
    found: dict[str, tuple[str, str]] = {}
    for sub in ("by-id", "by-path"):
        folder = os.path.join(dev, "v4l", sub)
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            link = os.path.join(folder, name)
            found.setdefault(os.path.realpath(link), (name, link))
    return found


def video_nodes(dev: str = DEV) -> list[str]:
    try:
        names = [n for n in os.listdir(dev) if re.fullmatch(r"video\d+", n)]
    except OSError:
        return []
    return [os.path.join(dev, n) for n in sorted(names, key=_node_number)]


def probe(node: str, stable: tuple[str, str] | None = None) -> Camera | None:
    """Describe one video node, or None if it is not a camera that can stream."""
    try:
        with v4l2.Device(node) as device:
            info = device.info()
            if not info.capture:
                return None
            modes = tuple(device.modes())
            if not modes:
                return None
            controls = device.controls()
    except OSError:
        return None
    camera_id, path = stable or (os.path.basename(node), node)
    return Camera(
        id=camera_id,
        name=friendly_name(info.card) or camera_id,
        path=path,
        ptz=any(name in controls for name in PTZ_CONTROLS),
        profile=profile_for(info.card, camera_id),
        modes=modes,
    )


class CameraIndex:
    """The connected cameras. Each node is probed once and remembered until it goes away."""

    def __init__(self, dev: str = DEV):
        self.dev = dev
        self._seen: dict[tuple[str, str], Camera | None] = {}

    def refresh(self) -> list[Camera]:
        stable = stable_ids(self.dev)
        seen: dict[tuple[str, str], Camera | None] = {}
        cameras: list[Camera] = []
        for node in video_nodes(self.dev):
            key = (node, stable.get(node, ("", ""))[0])
            camera = self._seen[key] if key in self._seen else probe(node, stable.get(node))
            seen[key] = camera
            if camera is not None and all(camera.id != other.id for other in cameras):
                cameras.append(camera)
        self._seen = seen
        return cameras


def pick_mode(modes: tuple[v4l2.Mode, ...] | list[v4l2.Mode], width: int, height: int, fps: int) -> StreamMode:
    """The mode closest to what was asked for.

    Motion-JPEG is preferred (it is what cameras offer at full size and rate). The size is the
    one asked for if the camera has it, otherwise its biggest that is not larger, otherwise its
    smallest. The rate is the one asked for if that size has it, otherwise the nearest below.
    """
    for code in ("MJPG", "YUYV"):
        usable = [m for m in modes if m.fourcc == code]
        if usable:
            input_format = _INPUT_FORMATS[code]
            break
    else:
        usable = list(modes)
        input_format = ""
    if not usable:
        return StreamMode("", width, height, fps)
    wanted_area = width * height
    exact = [m for m in usable if (m.width, m.height) == (width, height)]
    smaller = [m for m in usable if m.width * m.height <= wanted_area]
    if exact:
        mode = exact[0]
    elif smaller:
        mode = max(smaller, key=lambda m: m.width * m.height)
    else:
        mode = min(usable, key=lambda m: m.width * m.height)
    if not mode.rates or fps in mode.rates:
        rate = fps
    else:
        below = [r for r in mode.rates if r <= fps]
        rate = max(below) if below else min(mode.rates)
    return StreamMode(input_format, mode.width, mode.height, rate)


def sizes_offered(modes: tuple[v4l2.Mode, ...] | list[v4l2.Mode]) -> set[tuple[int, int]]:
    """The sizes of the format ``pick_mode`` would use."""
    for code in ("MJPG", "YUYV"):
        sizes = {(m.width, m.height) for m in modes if m.fourcc == code}
        if sizes:
            return sizes
    return {(m.width, m.height) for m in modes}
