# CamChord

A small desktop app for Linux that puts a USB camera under your fingers: a live
picture, snapshots, recording, and — where the camera has them — pan, tilt and
zoom from the keyboard.

It is an ordinary window, not a tray or panel applet, and it works on any
desktop. It was written for an OBSBOT Tiny 2 Lite on a workbench and also runs
a plain laptop webcam.

> **Status: early (0.1).** Developed and tested on one machine with two
> cameras.

## What it does

- **Live picture** from the camera you choose, at the size and frame rate you choose.
- **Snapshots**: the camera's own JPEG frame, saved untouched at full size.
- **Recording** to H.264 MP4 or MKV, with sound, encoded on the graphics card
  (VA-API) when one works and on the processor otherwise. Files are split by
  time and written so that a recording cut short still plays.
- **Pan, tilt and zoom** for cameras that offer the standard UVC controls.
  Tap a key to step, hold it to glide, `C` to center.
- **A camera you choose stays chosen.** Cameras are remembered by their stable
  name, not by `/dev/videoN`, which changes with plug order. If your camera is
  unplugged CamChord waits for it; it never quietly switches to another one.
- **Off means off.** Choose *Off* and no camera runs until you pick one again.
- **A keyboard map in the window** that shows every shortcut on the key it
  lives on. Each key is also a button.
- **Remote control** (`camchord ctl …`) so your window manager's shortcuts can
  move the camera, snap and record while another window is in front.
- **OBSBOT extras** (optional): tracking modes, gesture switches and mirroring,
  through a small helper built against OBSBOT's SDK.

Preview, snapshots and recording all come from one camera stream inside the
app. No virtual video devices (v4l2loopback) and no root setup are needed.

## Quick start

Plug in the camera, then:

```sh
git clone https://github.com/mbird2019/camchord
cd camchord
./camchord
```

The first run builds a private Python environment in `./.venv` (it downloads
Qt, about 200 MB, once) and then opens the window. Later runs start at once.
Nothing is installed system-wide and no root is needed. To get a command and a
menu entry as well, run `./camchord --install` (`--uninstall` removes them).

If it does not start or the camera is missing, run `./camchord doctor`: it
checks Qt, ffmpeg, encoders and camera access, and prints the fix for each.

What you need on the computer:

- Linux with a V4L2 camera (any UVC webcam)
- Python 3.10 or newer (Debian and Ubuntu also need `python3-venv`)
- `ffmpeg` (Debian/Ubuntu `ffmpeg`, Fedora `ffmpeg-free`, Arch `ffmpeg`)
- On X11, the `xcb-cursor` library (Debian/Ubuntu `libxcb-cursor0`, Fedora
  `xcb-util-cursor`); Wayland needs nothing extra
- Permission to open the camera: logging in at the computer's own screen is
  enough on most systems; otherwise add yourself to the `video` group
- Optional: `pactl` (PipeWire or PulseAudio) for sound in recordings,
  `notify-send` for notifications, a VA-API driver for hardware encoding

If `ffmpeg` has no H.264 encoder (some distribution builds), recording falls
back to OpenH264 and then to plain MPEG-4; preview and snapshots never need one.

### Without the OBSBOT helper

Everything works on any UVC camera without extra software: preview,
snapshots, recording, and pan, tilt, zoom, focus and the keep-awake nudge on
cameras that have those controls (the OBSBOT Tiny 2 Lite does). Only the OBSBOT
tracking modes and gesture switches (keys `4` to `0`) need the optional
`obsbot-ai` helper, which is built against OBSBOT's own SDK and cannot be
included here (see `contrib/obsbot-ai`).

### Other ways to install

With `pipx`, no checkout needed:

```sh
pipx install git+https://github.com/mbird2019/camchord
camchord
```

By hand, from a checkout (this reuses your distribution's PySide6 if you give
`--system-site-packages`; Arch `pyside6`, Fedora `python3-pyside6`):

```sh
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/camchord
```

For a menu entry by hand, copy `data/camchord.desktop` to
`~/.local/share/applications/` and `data/camchord.svg` to
`~/.local/share/icons/hicolor/scalable/apps/`, and set `Exec=` to the full path.

## Use

Start `camchord`. The first time, no camera runs: pick one in the **Camera**
row. The choice is remembered.

| Key | Does |
| --- | --- |
| `I` `J` `K` `L` or arrows | Up, left, down, right. Tap = one step (20°), hold = glide |
| `H` / `N` (or `+` / `-`) | Zoom in / out. Hold to keep zooming |
| `C` | Center (pan 0, tilt 0; zoom is kept) |
| `P` | Autofocus on / off |
| `U` | Snapshot |
| `O` | Start / stop recording |
| `Space`, `F` or `F11` | Full screen picture (`Esc` leaves) |
| `M` | Hide / show the controls panel |
| `4` … `0` | OBSBOT tracking modes (with the helper); the lit one again turns tracking off |
| `Ctrl+Q` | Quit |

Keys a camera cannot use are dimmed. A webcam with no motor gets the picture,
snapshots and recording; the move and zoom keys stay dim.

Snapshots go to `~/Videos/camchord/snaps`, recordings to
`~/Videos/camchord/rec`, both named by date and time.

The **Recording** tab sets resolution, frame rate, file type, quality, sound
and how often a new file starts. Resolution and frame rate apply to the live
picture too, and switch right away unless a recording is running.

### From the command line

```sh
camchord cameras          # list connected cameras as JSON
camchord ctl status       # state of the running window as JSON
camchord ctl snap
camchord ctl record
camchord ctl move-start left ; camchord ctl move-stop left
camchord ctl camera off
camchord --help           # every command
```

Starting `camchord` a second time brings the running window to the front.
[docs/shortcuts.md](docs/shortcuts.md) has ready-made key bindings for
Hyprland and Sway and notes for other desktops.

### Settings file

`~/.config/camchord/settings.json` holds the choices the window makes, plus a
few that have no button yet:

| Key | Default | Meaning |
| --- | --- | --- |
| `step_degrees` | 20 | How far a tap moves (1–45) |
| `hold_speed` | 15 | Glide speed in degrees per second (5–120) |
| `zoom_speed` | 5 | Zoom speed while held (1–20) |
| `encoder` | `"auto"` | `"auto"`, `"vaapi"` or `"software"` |
| `output_dir` | `~/Videos/camchord` | Where snapshots and recordings go |
| `obsbot_helper` | — | Full path to `obsbot-ai`, if it is not on `PATH` |

Values outside their range are ignored. Edit the file while CamChord is closed.

## Cameras

Any camera Linux sees as a video capture device should give a picture.
Cameras that send Motion-JPEG (almost all USB cameras, at their larger sizes)
are passed through without re-encoding; others are converted by ffmpeg.

Pan, tilt and zoom use the standard controls (`pan_absolute`, `tilt_absolute`,
`pan_speed`, `tilt_speed`, `zoom_absolute`, `zoom_continuous`). Check what
yours has with `v4l2-ctl -d /dev/videoN --list-ctrls`.

| Camera | Picture, snapshot, record | Pan / tilt / zoom | Notes |
| --- | --- | --- | --- |
| OBSBOT Tiny 2 Lite | not yet re-tested in this app | not yet re-tested in this app | The motion logic and its numbers come from a bar-widget predecessor that ran this camera daily; this app's port is covered by tests against a simulated camera only. |
| Dell integrated webcam (UVC, 720p) | works | none on the camera | |
| Other UVC PTZ cameras | expected to work | expected to work, unmeasured | Glide speed is a guess until the camera is measured; see below. |

Some cameras behave in ways V4L2 does not describe. What is known about a
model lives in [`src/camchord/profiles.py`](src/camchord/profiles.py): how many
degrees per second one speed unit turns, whether the camera reports its target
or its real position, which way positive pan goes, and whether it parks itself
when idle. A new profile is a few lines and the most useful contribution you
can make; [CONTRIBUTING.md](CONTRIBUTING.md) explains how to measure one.

### OBSBOT extras

Tracking modes (upper body, close-up, hand, group, …), gesture switches,
tracking style and mirroring are stored on the camera and only reachable
through OBSBOT's SDK. CamChord talks to it through a small optional helper;
see [contrib/obsbot-ai](contrib/obsbot-ai/README.md). The SDK is OBSBOT's and
is not distributed with this project.

## How it works

```
camera ──ffmpeg (copy)──► JPEG frames ──► CamChord ──┬─► preview (decoded at window size)
                                                     ├─► snapshot (frame saved as is)
                                                     └─► ffmpeg ──► H.264 + AAC ──► files
camera controls ◄── ioctl ── CamChord (pan, tilt, zoom, focus)
```

- One `ffmpeg` process reads the camera and forwards its Motion-JPEG frames
  without decoding them. The app splits the stream into frames.
- The preview decodes only the newest frame, at the size it is drawn.
- While recording, the same frames are piped to a second `ffmpeg`, which
  stamps them with the wall clock (so sound stays in step), encodes and
  writes time-split files.
- Camera controls are read and written directly with V4L2 ioctls, which takes
  well under a millisecond. Holding a key sets the camera's turn speed; on
  release the app works out where the glide ended (speed × time) for cameras
  that only report their last target.

The motion code (`ptz.py`) has no Qt in it and is tested against a simulated
camera, so behaviour can be changed and checked without hardware.

## Known limits

- Linux only. The ioctl numbers assume the common encoding (x86, ARM, RISC-V).
- Another program that is already streaming from the camera blocks CamChord
  ("Device or resource busy"), and the other way round. CamChord does not
  provide a virtual camera for video calls.
- Changing resolution or frame rate restarts the stream (about a second).
- Glide positions are estimates between key press and release; a camera that
  is tracking a person on its own moves without telling the app.
- No picture controls (brightness, exposure, white balance) yet, and no
  settings dialog for step and speed.

## Related projects

- [obsbot-camera-control](https://github.com/aaronsb/obsbot-camera-control):
  a native Linux control app for OBSBOT cameras with presets and a virtual
  camera. Choose it if you want OBSBOT-specific features in depth. CamChord is
  smaller, keyboard-first, records by itself and is meant to work with any UVC
  camera.
- [cameractrls](https://github.com/soyersoyer/cameractrls): picture controls
  and presets for many webcams.
- `v4l2-ctl` (v4l-utils): the command-line way to do anything V4L2 can.

## Name

CamChord: a camera, played by hand like a chord. The command, package and
settings folder are all `camchord`. It is not affiliated with any camera maker;
camera brand names (OBSBOT and others) appear only to say what was tested.

## License

[MIT](LICENSE).
