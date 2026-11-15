"""The live picture: one ffmpeg process reads the camera and hands over JPEG frames.

The camera's own Motion-JPEG is passed through untouched (no decoding in
ffmpeg), so this costs almost nothing. The same frames feed the preview, the
snapshots (a frame saved as it is) and the recorder, which is why one camera
can do all three at once without virtual video devices.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .devices import StreamMode
from .mjpeg import Splitter


def capture_command(path: str, mode: StreamMode) -> list[str]:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "v4l2"]
    if mode.input_format:
        cmd += ["-input_format", mode.input_format]
    cmd += ["-video_size", f"{mode.width}x{mode.height}", "-framerate", str(mode.fps), "-i", path]
    if mode.input_format == "mjpeg":
        # mjpeg2jpeg adds the standard tables many cameras leave out, so every frame is a complete JPEG file.
        cmd += ["-c:v", "copy", "-bsf:v", "mjpeg2jpeg"]
    else:
        cmd += ["-c:v", "mjpeg", "-q:v", "3", "-pix_fmt", "yuvj422p"]
    return cmd + ["-f", "mjpeg", "-flush_packets", "1", "pipe:1"]


class Stream(QObject):
    frame = Signal(bytes)  # one complete JPEG
    started = Signal()  # the first frame arrived
    stopped = Signal(str)  # the process ended; the text is ffmpeg's last words, or "" after stop()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._splitter = Splitter()
        self._wanted = False
        self._got_frame = False
        self.mode: StreamMode | None = None
        self.path = ""

    @property
    def running(self) -> bool:
        return self._proc is not None

    @property
    def live(self) -> bool:
        return self._proc is not None and self._got_frame

    def start(self, path: str, mode: StreamMode) -> None:
        self.stop()
        self.path, self.mode = path, mode
        self._splitter = Splitter()
        self._got_frame = False
        self._wanted = True
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.readyReadStandardOutput.connect(self._read)
        proc.finished.connect(lambda *_: self._finished(proc))
        proc.errorOccurred.connect(lambda error: self._failed(proc, error))
        self._proc = proc
        cmd = capture_command(path, mode)
        proc.start(cmd[0], cmd[1:])

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        self._wanted = False
        if proc is None:
            return
        proc.readyReadStandardOutput.disconnect(self._read)
        if proc.state() != QProcess.ProcessState.NotRunning:
            # ffmpeg can sit blocked on a full pipe and never see a polite request, so do not wait long.
            proc.terminate()
            if not proc.waitForFinished(700):
                proc.kill()
                proc.waitForFinished(1000)
        proc.deleteLater()

    def _read(self) -> None:
        proc = self._proc
        if proc is None:
            return
        for jpeg in self._splitter.feed(bytes(proc.readAllStandardOutput())):
            if not self._got_frame:
                self._got_frame = True
                self.started.emit()
            self.frame.emit(jpeg)

    def _failed(self, proc: QProcess, error) -> None:
        if proc is self._proc and error == QProcess.ProcessError.FailedToStart:
            self._proc = None
            self._wanted = False
            proc.deleteLater()
            self.stopped.emit("ffmpeg could not be started. Is it installed?")

    def _finished(self, proc: QProcess) -> None:
        if proc is not self._proc:
            return
        message = bytes(proc.readAllStandardError()).decode("utf-8", "replace").strip()
        self._proc = None
        proc.deleteLater()
        if self._wanted:
            self._wanted = False
            last = message.splitlines()[-1] if message else "the camera stopped sending pictures"
            # Tell the owner after this event, so it can safely start a new stream from the handler.
            QTimer.singleShot(0, lambda: self.stopped.emit(last[:200]))
