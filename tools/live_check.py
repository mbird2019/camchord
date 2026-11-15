#!/usr/bin/env python3
"""Check the app end to end against a real camera, without showing a window.

    QT_QPA_PLATFORM=offscreen python tools/live_check.py [CAMERA_ID] [--shot FILE.png]

It uses a throwaway settings file and output folder, switches the camera on for
about fifteen seconds, takes a snapshot, records a few seconds, and prints what
it found. Exit code 0 means every step worked.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from camchord.config import Settings  # noqa: E402
from camchord.window import MainWindow  # noqa: E402


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(test, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        wait(100)
    return test()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("camera", nargs="?", default="", help="camera id (default: the first one found)")
    parser.add_argument("--shot", default="", metavar="FILE.png", help="also save pictures of the window")
    options = parser.parse_args()
    shot = options.shot
    app = QApplication(sys.argv[:1])
    app.setApplicationName("camchord-live-check")
    tmp = Path(tempfile.mkdtemp(prefix="camchord-check-"))
    settings = Settings(tmp / "settings.json")
    settings._data["output_dir"] = str(tmp / "out")
    win = MainWindow(settings)
    win.resize(1400, 780)
    win.show()
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
        if not ok:
            failures.append(name)

    cameras = win.status()["cameras"]
    print("cameras:", [c["id"] for c in cameras])
    check("first run starts no camera", not win.stream.running)
    if not cameras:
        print("no camera connected; nothing more to check")
        return 1
    camera_id = options.camera or cameras[0]["id"]
    check("choose camera", win.command(["camera", camera_id])["ok"])
    check("picture arrives", wait_until(lambda: win.stream.live, 8), win.status()["status"])
    wait(1000)
    check(
        "preview decodes", not win.preview._image.isNull(), f"source {win.preview.source_size.width()}x{win.preview.source_size.height()}"
    )
    snap = win.command(["snap"])
    check("snapshot", bool(snap.get("ok")) and Path(snap["file"]).stat().st_size > 1000, snap.get("file", ""))
    if shot:
        win.grab().save(shot)
    started = win.command(["record"])
    check("recording starts", bool(started.get("recording")), win.message_label.text())
    wait(5000)
    check("still recording after 5 s", win.recorder.recording, win.message_label.text())
    if shot:
        win.grab().save(shot.replace(".png", "-rec.png"))
    win.command(["record"])
    check("recording finishes", wait_until(lambda: not win.recorder.busy, 10), win.message_label.text())
    files = sorted((tmp / "out" / "rec").glob("*"))
    check("one recording file", len(files) == 1, str(files[0]) if files else "")
    if files:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name:format=duration", "-of", "csv=p=0", str(files[0])],
            capture_output=True, text=True,
        ).stdout.split()  # fmt: skip
        print("ffprobe:", probe)
        check("recording has h264 video", "h264" in probe)
        duration = next((float(v) for v in probe if v.replace(".", "", 1).isdigit()), 0.0)
        check("recording is about 5 s long", 3.5 <= duration <= 7.0, f"{duration:.1f} s")
        check("recording closed cleanly", "Recording saved" in win.message_label.text(), win.message_label.text())
    check("off stops the camera", win.command(["camera", "off"])["ok"] and not win.stream.running)
    wait(300)
    win.close()
    print("files kept in", tmp)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
