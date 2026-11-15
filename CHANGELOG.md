# Changelog

## 0.1.0 — unreleased

First version as a standalone app. It replaces a desktop-bar widget and a set
of shell scripts that did the same job through v4l2loopback devices.

- Window with live picture, camera picker with an Off choice, keyboard map,
  recording settings.
- Snapshots and H.264 recording (VA-API or libx264) from a single camera
  stream; no virtual video devices.
- Pan, tilt, zoom, center and autofocus through V4L2 ioctls; tap to step, hold
  to glide.
- Camera profile for the OBSBOT Tiny 2 Lite (speed calibration, target
  readback, keep-awake, autofocus repair).
- `camchord ctl` remote control over a Unix socket; one instance at a time.
- Optional OBSBOT SDK helper for tracking modes and on-camera settings.
