"""Command line: start the window, or talk to the one that is running."""

from __future__ import annotations

import argparse
import json
import shutil
import sys

from . import APP_ID, APP_NAME, DESKTOP_ID, __version__

CTL_HELP = """commands for ctl:
  show                       bring the window to the front
  status                     print the app's state as JSON
  snap                       save a snapshot
  record                     start or stop recording
  move-start DIR             start turning (DIR: left right up down); pair with move-stop
  move-stop DIR|all          stop turning; a short press-release is one step
  move DIR                   one step
  zoom-start in|out          start zooming; pair with zoom-stop
  zoom-stop in|out
  zoom in|out                one short zoom
  center                     pan 0, tilt 0
  focus                      autofocus on / off
  track MODE                 OBSBOT tracking: upper lower full closeup headless hand group off
  camera ID|off              switch camera (ids: see `cameras`)
  fullscreen | panel | quit
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_ID,
        description=f"{APP_NAME}: live view, snapshots, recording and pan/tilt/zoom for a USB camera.",
        epilog=CTL_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("cameras", help="list the connected cameras as JSON and exit")
    sub.add_parser("doctor", help="check this computer (Qt, ffmpeg, camera access) and say what to fix")
    ctl = sub.add_parser("ctl", help="send a command to the running window (see below)")
    ctl.add_argument("words", nargs="+", metavar="COMMAND")
    return parser


def _cameras() -> int:
    from .devices import CameraIndex

    cameras = [
        {
            "id": c.id,
            "name": c.name,
            "path": c.path,
            "ptz": c.ptz,
            "profile": c.profile.key,
            "sizes": sorted({f"{m.width}x{m.height}" for m in c.modes}),
        }
        for c in CameraIndex().refresh()
    ]
    print(json.dumps(cameras, indent=2))
    return 0


def _ctl(words: list[str]) -> int:
    from . import ipc

    answer = ipc.send(words)
    if answer is None:
        print(f"{APP_NAME} is not running", file=sys.stderr)
        return 2
    if words[0] == "status" or not answer.get("ok"):
        print(
            json.dumps(answer, indent=2) if words[0] == "status" else answer.get("error", "failed"),
            file=sys.stdout if answer.get("ok") else sys.stderr,
        )
    return 0 if answer.get("ok") else 1


def _gui() -> int:
    from . import ipc

    if ipc.send(["show"], timeout=2.0) is not None:
        return 0  # already running: it has just come to the front

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(DESKTOP_ID)
    app.setWindowIcon(QIcon.fromTheme(DESKTOP_ID, QIcon.fromTheme("camera-video")))
    if not shutil.which("ffmpeg"):
        QMessageBox.critical(None, APP_NAME, f"{APP_NAME} needs ffmpeg, and it was not found. Install ffmpeg and start {APP_NAME} again.")
        return 1
    window = MainWindow()
    server = ipc.Server(window.command, parent=window)
    if not server.listening:
        print(f"{APP_NAME}: remote control is unavailable (could not listen on {server.path})", file=sys.stderr)
    window.show()
    code = app.exec()
    server.close()
    return code


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.action == "cameras":
        return _cameras()
    if args.action == "doctor":
        from .doctor import run

        return run()
    if args.action == "ctl":
        return _ctl(args.words)
    return _gui()


if __name__ == "__main__":
    sys.exit(main())
