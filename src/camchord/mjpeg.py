"""Cutting a byte stream of back-to-back JPEG pictures into single pictures."""

from __future__ import annotations

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"
_SOS = 0xDA
# Markers that stand alone, with no length field after them.
_BARE = {0x01, *range(0xD0, 0xD8)}
MAX_FRAME = 64 * 1024 * 1024


class Splitter:
    """Feed it bytes as they arrive; it hands back each complete JPEG.

    The header is walked segment by segment, so a 0xFFD9 that happens to sit
    inside a table or an APPn block is not mistaken for the end. In the picture
    data itself 0xFF is always followed by 0x00 or a restart marker, so the
    first 0xFFD9 there is the real end.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._scan_from = 0  # where to resume looking for the end marker
        self._in_scan = False

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        frames: list[bytes] = []
        while True:
            frame = self._next()
            if frame is None:
                break
            frames.append(frame)
        if len(self._buf) > MAX_FRAME:  # not a JPEG stream; do not grow without bound
            self._reset(len(self._buf))
        return frames

    def _reset(self, drop: int) -> None:
        del self._buf[:drop]
        self._scan_from = 0
        self._in_scan = False

    def _next(self) -> bytes | None:
        buf = self._buf
        if not self._in_scan:
            start = buf.find(SOI)
            if start < 0:
                # Keep a trailing 0xFF: it may be the first half of a start marker.
                self._reset(len(buf) - 1 if buf.endswith(b"\xff") else len(buf))
                return None
            if start:
                self._reset(start)
            pos = 2
            while True:
                if pos + 4 > len(buf):
                    return None  # need more header
                if buf[pos] != 0xFF:
                    self._reset(2)  # not a JPEG header after all; look for the next start
                    return self._next()
                marker = buf[pos + 1]
                if marker == 0xFF:  # fill byte
                    pos += 1
                    continue
                if marker in _BARE:
                    pos += 2
                    continue
                length = (buf[pos + 2] << 8) | buf[pos + 3]
                if length < 2:
                    self._reset(2)
                    return self._next()
                if pos + 2 + length > len(buf):
                    return None
                pos += 2 + length
                if marker == _SOS:
                    break
            self._in_scan = True
            self._scan_from = pos
        end = buf.find(EOI, self._scan_from)
        if end < 0:
            self._scan_from = max(self._scan_from, len(buf) - 1)
            return None
        frame = bytes(buf[: end + 2])
        self._reset(end + 2)
        return frame
