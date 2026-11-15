"""Just enough V4L2, through ioctl, to describe a camera and drive its controls.

No external tools: a control read or write is one system call (well under a
millisecond), which the hold-to-move timing in ``ptz`` relies on.

The ioctl numbers below use the generic Linux encoding (x86, ARM, RISC-V).
"""

from __future__ import annotations

import fcntl
import os
import struct
from dataclasses import dataclass


def _ioc(direction: int, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("V") << 8) | number


_READ, _WRITE = 2, 1
VIDIOC_QUERYCAP = _ioc(_READ, 0, 104)
VIDIOC_ENUM_FMT = _ioc(_READ | _WRITE, 2, 64)
VIDIOC_G_CTRL = _ioc(_READ | _WRITE, 27, 8)
VIDIOC_S_CTRL = _ioc(_READ | _WRITE, 28, 8)
VIDIOC_QUERYCTRL = _ioc(_READ | _WRITE, 36, 68)
VIDIOC_ENUM_FRAMESIZES = _ioc(_READ | _WRITE, 74, 44)
VIDIOC_ENUM_FRAMEINTERVALS = _ioc(_READ | _WRITE, 75, 52)

CAP_VIDEO_CAPTURE = 0x00000001
BUF_TYPE_VIDEO_CAPTURE = 1
CTRL_FLAG_DISABLED = 0x0001
CTRL_FLAG_NEXT_CTRL = 0x80000000
CTRL_TYPE_CLASS = 6
FRMSIZE_DISCRETE = 1
FRMIVAL_DISCRETE = 1


def control_name(raw: str) -> str:
    """The kernel's control name as v4l2-ctl spells it: "Pan, Absolute" -> "pan_absolute"."""
    out: list[str] = []
    for ch in raw:
        if ch.isalnum():
            out.append(ch.lower())
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")


def fourcc(code: int) -> str:
    return "".join(chr((code >> shift) & 0xFF) for shift in (0, 8, 16, 24))


@dataclass(frozen=True)
class Control:
    id: int
    name: str
    minimum: int
    maximum: int
    step: int
    default: int


@dataclass(frozen=True)
class Mode:
    """One size a camera can stream in one pixel format, with its frame rates."""

    fourcc: str
    width: int
    height: int
    rates: tuple[int, ...]


@dataclass(frozen=True)
class Info:
    driver: str
    card: str
    capture: bool


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace").strip()


class Device:
    """An open V4L2 node. Opening it for controls does not disturb whoever is streaming."""

    def __init__(self, path: str):
        self.path = path
        self._fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        self._controls: dict[str, Control] | None = None

    def close(self) -> None:
        if self._fd >= 0:
            try:
                os.close(self._fd)
            finally:
                self._fd = -1

    def __enter__(self) -> Device:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _ioctl(self, request: int, buf: bytearray) -> bytearray:
        fcntl.ioctl(self._fd, request, buf)
        return buf

    def info(self) -> Info:
        buf = self._ioctl(VIDIOC_QUERYCAP, bytearray(104))
        driver, card, _bus, _version, caps, device_caps = struct.unpack_from("<16s32s32sIII", buf)
        effective = device_caps or caps
        return Info(_text(driver), _text(card), bool(effective & CAP_VIDEO_CAPTURE))

    # ---- controls -----------------------------------------------------------
    def controls(self) -> dict[str, Control]:
        if self._controls is None:
            found: dict[str, Control] = {}
            last = 0
            for _ in range(512):
                buf = bytearray(68)
                struct.pack_into("<I", buf, 0, last | CTRL_FLAG_NEXT_CTRL)
                try:
                    self._ioctl(VIDIOC_QUERYCTRL, buf)
                except OSError:
                    break
                cid, ctype, raw, lo, hi, step, default, flags = struct.unpack_from("<II32siiiiI", buf)
                last = cid
                if ctype == CTRL_TYPE_CLASS or flags & CTRL_FLAG_DISABLED:
                    continue
                name = control_name(_text(raw))
                if name:
                    found.setdefault(name, Control(cid, name, lo, hi, step, default))
            self._controls = found
        return self._controls

    def has(self, name: str) -> bool:
        return name in self.controls()

    def get(self, name: str) -> int | None:
        """The control's value, or None if the camera lacks it or cannot be read right now."""
        ctrl = self.controls().get(name)
        if ctrl is None:
            return None
        buf = bytearray(struct.pack("<Ii", ctrl.id, 0))
        try:
            self._ioctl(VIDIOC_G_CTRL, buf)
        except OSError:
            return None
        return struct.unpack_from("<Ii", buf)[1]

    def set(self, name: str, value: int) -> bool:
        """Write a control, clamped to its range. False if the camera lacks it or refused."""
        ctrl = self.controls().get(name)
        if ctrl is None:
            return False
        value = max(ctrl.minimum, min(ctrl.maximum, int(value)))
        try:
            self._ioctl(VIDIOC_S_CTRL, bytearray(struct.pack("<Ii", ctrl.id, value)))
        except OSError:
            return False
        return True

    # ---- formats ------------------------------------------------------------
    def modes(self) -> list[Mode]:
        modes: list[Mode] = []
        for fmt_index in range(64):
            buf = bytearray(64)
            struct.pack_into("<II", buf, 0, fmt_index, BUF_TYPE_VIDEO_CAPTURE)
            try:
                self._ioctl(VIDIOC_ENUM_FMT, buf)
            except OSError:
                break
            pixelformat = struct.unpack_from("<I", buf, 44)[0]
            for width, height in self._sizes(pixelformat):
                modes.append(Mode(fourcc(pixelformat), width, height, self._rates(pixelformat, width, height)))
        return modes

    def _sizes(self, pixelformat: int) -> list[tuple[int, int]]:
        sizes: list[tuple[int, int]] = []
        for index in range(256):
            buf = bytearray(44)
            struct.pack_into("<II", buf, 0, index, pixelformat)
            try:
                self._ioctl(VIDIOC_ENUM_FRAMESIZES, buf)
            except OSError:
                break
            kind = struct.unpack_from("<I", buf, 8)[0]
            if kind == FRMSIZE_DISCRETE:
                sizes.append(struct.unpack_from("<II", buf, 12))
            else:  # stepwise or continuous: min_w, max_w, step_w, min_h, max_h, step_h
                _min_w, max_w, _step_w, _min_h, max_h, _step_h = struct.unpack_from("<6I", buf, 12)
                sizes.append((max_w, max_h))
                break
        return sizes

    def _rates(self, pixelformat: int, width: int, height: int) -> tuple[int, ...]:
        rates: set[int] = set()
        for index in range(64):
            buf = bytearray(52)
            struct.pack_into("<IIII", buf, 0, index, pixelformat, width, height)
            try:
                self._ioctl(VIDIOC_ENUM_FRAMEINTERVALS, buf)
            except OSError:
                break
            kind, numerator, denominator = struct.unpack_from("<III", buf, 16)
            if numerator:
                rates.add(round(denominator / numerator))
            if kind != FRMIVAL_DISCRETE:
                break
        return tuple(sorted(rates))
