# Contributing

Bug reports, camera reports and patches are welcome.

## Set up

`./camchord` builds the environment for you; for development add the test tools:

```sh
python -m venv .venv            # add --system-site-packages to reuse a distro PySide6
.venv/bin/pip install -e ".[dev]"
```

## Before you send a change

```sh
.venv/bin/ruff check .
.venv/bin/ruff format .
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q
```

The tests need no camera and start no ffmpeg. With a camera attached, also run
the end-to-end check, which switches the camera on for about fifteen seconds,
takes a snapshot, records five seconds and inspects the file:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/live_check.py
```

## Where things are

| File | Job |
| --- | --- |
| `v4l2.py` | Camera controls and formats through ioctl |
| `devices.py` | Finding cameras, stable ids, choosing a stream size |
| `profiles.py` | What is known about particular camera models |
| `ptz.py` | Tap, hold, glide, zoom, center, keep-awake. No Qt; fully tested with a simulated camera |
| `stream.py`, `mjpeg.py` | The capture process and cutting its output into frames |
| `recorder.py` | The recording process, encoder and microphone choice |
| `obsbot.py` | The optional OBSBOT SDK helper |
| `ipc.py` | `camchord ctl` |
| `window.py`, `widgets.py` | The window |

## Reporting a camera

Please include the output of:

```sh
camchord cameras
v4l2-ctl -d /dev/videoN --list-ctrls
v4l2-ctl -d /dev/videoN --list-formats-ext
```

and say what works: picture, snapshot, recording, tap, hold, zoom, center.

## Adding a camera profile

A profile is needed only when a camera does not behave as the UVC standard
says. Measure, do not guess:

1. **Speed.** Set `pan_speed` to a known value with `v4l2-ctl`, time how long
   the camera takes to turn a known angle (a full 90° against marks on the
   desk works well), and divide. Repeat for `tilt_speed`. That gives
   `pan_rate` and `tilt_rate` in degrees per second per unit.
2. **Readback.** While the camera glides, read `pan_absolute`. If the value
   does not change until you write a new target, set `reads_target=True`.
3. **Direction.** Write a larger `pan_absolute`. If the view turns left, set
   `pan_sign=-1`.
4. **Idle behaviour.** Leave the camera alone for five minutes. If it parks or
   sleeps, note after how long; `keep_awake_s` should be well under that.

Put the numbers in `profiles.py` with a comment saying when and how they were
measured, and add the model's name as it appears in `camchord cameras` to
`match`.

## Style

- Text the user sees is plain and specific: say what happened and what to do.
- No camera may start unless the user chose it. No silent fallback to another camera.
- New behaviour in `ptz.py` comes with a test in `tests/test_ptz.py`.
