"""Recording: a second ffmpeg that is fed the live JPEG frames and writes H.264 files.

Frames are stamped with the wall clock as they arrive, and so is the sound, which
keeps the two in step. Files are split by time and named by their start time.
MP4 is written fragmented and MKV is a streaming format, so a recording cut
short by a crash or an unplugged camera still plays.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .config import QUALITY_QP
from .devices import StreamMode

BASE = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
MAX_BACKLOG = 256 * 1024 * 1024


@dataclass(frozen=True)
class Encoder:
    kind: str  # "vaapi" or "software"
    device: str = ""

    @property
    def label(self) -> str:
        return f"graphics card ({os.path.basename(self.device)})" if self.kind == "vaapi" else "processor (libx264)"


@dataclass(frozen=True)
class Recipe:
    """Everything one recording needs, taken from the settings when it starts."""

    folder: Path
    mode: StreamMode
    container: str = "mkv"  # "mkv" or "mp4"
    quality: str = "standard"
    split_minutes: int = 30
    microphone: str = ""  # a PulseAudio/PipeWire source name, or "" for no sound
    encoder: Encoder = Encoder("software")


def _video_args(encoder: Encoder, qp: int) -> list[str]:
    if encoder.kind == "vaapi":
        return ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-qp", str(qp)]
    return ["-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(qp)]


def record_command(recipe: Recipe) -> list[str]:
    cmd = list(BASE)
    if recipe.encoder.kind == "vaapi":
        cmd += ["-vaapi_device", recipe.encoder.device]
    cmd += ["-thread_queue_size", "512", "-use_wallclock_as_timestamps", "1", "-f", "mjpeg", "-i", "pipe:0"]
    if recipe.microphone:
        cmd += ["-thread_queue_size", "1024", "-use_wallclock_as_timestamps", "1", "-f", "pulse", "-i", recipe.microphone]
    cmd += ["-map", "0:v", *_video_args(recipe.encoder, QUALITY_QP[recipe.quality])]
    if recipe.microphone:
        cmd += ["-map", "1:a", "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-f", "segment", "-segment_time", str(recipe.split_minutes * 60)]
    if recipe.container == "mp4":
        cmd += ["-segment_format", "mp4", "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof"]
    cmd += ["-reset_timestamps", "1", "-strftime", "1", str(recipe.folder / f"%Y%m%d-%H%M%S.{recipe.container}")]
    return cmd


# ---- choosing an encoder -------------------------------------------------------
_probed: dict[tuple[str, int, int], Encoder | None] = {}


def _works(encoder: Encoder, width: int, height: int) -> bool:
    cmd = list(BASE)
    if encoder.kind == "vaapi":
        cmd += ["-vaapi_device", encoder.device]
    cmd += ["-f", "lavfi", "-i", f"color=black:size={width}x{height}:rate=30", "-frames:v", "3"]
    cmd += [*_video_args(encoder, 24), "-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def choose_encoder(preference: str, width: int, height: int) -> Encoder | None:
    """The first encoder that really encodes a few frames at this size, tried once and remembered.

    "auto" tries each graphics card (VA-API), then the processor; "vaapi" and "software" try only that.
    """
    key = (preference, width, height)
    if key not in _probed:
        candidates: list[Encoder] = []
        if preference in ("auto", "vaapi"):
            candidates += [Encoder("vaapi", node) for node in sorted(glob.glob("/dev/dri/renderD*"))]
        if preference in ("auto", "software"):
            candidates.append(Encoder("software"))
        _probed[key] = next((e for e in candidates if _works(e, width, height)), None)
    return _probed[key]


# ---- choosing a microphone -----------------------------------------------------
def _pactl(*args: str) -> str:
    if not shutil.which("pactl"):
        return ""
    try:
        return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def pick_microphone(camera_id: str, camera_name: str, sources: list[str], default: str) -> str:
    """The camera's own microphone if it has one, otherwise the system's default microphone."""
    inputs = [s for s in sources if not s.endswith(".monitor")]
    usb = re.sub(r"-video-index\d+$", "", camera_id)
    words = "_".join(camera_name.split())
    for needle in (usb if usb.startswith("usb-") else "", words):
        if needle:
            for source in inputs:
                if needle.lower() in source.lower():
                    return source
    return default if default and not default.endswith(".monitor") else ""


def microphone_for(camera_id: str, camera_name: str) -> str:
    sources = [line.split("\t")[1] for line in _pactl("list", "short", "sources").splitlines() if "\t" in line]
    return pick_microphone(camera_id, camera_name, sources, _pactl("get-default-source").strip())


def newest_recording(folder: Path) -> tuple[str, int] | None:
    files = [p for ext in ("mkv", "mp4") for p in folder.glob(f"*.{ext}")]
    try:
        newest = max(files, key=lambda p: p.stat().st_mtime)
        return newest.name, newest.stat().st_size
    except (ValueError, OSError):
        return None


class Recorder(QObject):
    stopped = Signal(str)  # "" after a normal stop, otherwise what went wrong

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._stopping = False
        self.recipe: Recipe | None = None
        self.dropped = 0

    @property
    def recording(self) -> bool:
        return self._proc is not None and not self._stopping

    @property
    def busy(self) -> bool:
        """True from start until the last file is closed."""
        return self._proc is not None

    def start(self, recipe: Recipe) -> None:
        if self._proc is not None:
            return
        recipe.folder.mkdir(parents=True, exist_ok=True)
        self.recipe = recipe
        self.dropped = 0
        self._stopping = False
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.setStandardOutputFile(QProcess.nullDevice())
        proc.finished.connect(lambda *_: self._finished(proc))
        proc.errorOccurred.connect(lambda error: self._failed(proc, error))
        self._proc = proc
        cmd = record_command(recipe)
        proc.start(cmd[0], cmd[1:])

    def write(self, jpeg: bytes) -> None:
        proc = self._proc
        if proc is None or self._stopping:
            return
        if proc.bytesToWrite() > MAX_BACKLOG:  # the encoder cannot keep up; drop rather than fill memory
            self.dropped += 1
            return
        proc.write(jpeg)

    def stop(self) -> None:
        """Finish the file. ``stopped`` follows when ffmpeg has closed it."""
        proc = self._proc
        if proc is None or self._stopping:
            return
        self._stopping = True
        proc.closeWriteChannel()
        # The end of the pictures alone does not stop ffmpeg while the microphone keeps going,
        # so ask it to finish (it writes the file's ending on SIGINT), after the last frames drained.
        QTimer.singleShot(300, lambda: self._signal(proc, signal.SIGINT))
        QTimer.singleShot(8000, lambda: self._signal(proc, signal.SIGKILL))

    def _signal(self, proc: QProcess, signum: int) -> None:
        if proc is self._proc and proc.state() != QProcess.ProcessState.NotRunning:
            try:
                os.kill(proc.processId(), signum)
            except OSError:
                pass

    def _failed(self, proc: QProcess, error) -> None:
        if proc is self._proc and error == QProcess.ProcessError.FailedToStart:
            self._proc = None
            proc.deleteLater()
            self.stopped.emit("ffmpeg could not be started. Is it installed?")

    def _finished(self, proc: QProcess) -> None:
        if proc is not self._proc:
            return
        log = bytes(proc.readAllStandardError()).decode("utf-8", "replace").strip()
        asked = self._stopping
        self._proc = None
        self._stopping = False
        proc.deleteLater()
        if asked and proc.exitStatus() == QProcess.ExitStatus.NormalExit:
            self.stopped.emit("")
        elif asked:
            self.stopped.emit("the recorder had to be stopped by force; the last file may be incomplete")
        else:
            lines = log.splitlines()
            self.stopped.emit((lines[-1] if lines else "the recorder stopped unexpectedly")[:200])
