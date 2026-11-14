"""What is known about particular cameras beyond what V4L2 reports.

A camera that matches no profile gets GENERIC, which assumes the UVC standard:
pan and tilt in arc-seconds, larger pan turning the view to the right.

Add a profile only from measurements on the real camera, and say in the
comment when and how they were taken.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    key: str
    match: tuple[str, ...] = ()
    # Degrees per second for one unit of pan_speed / tilt_speed. With these the app can work
    # out where a glide ended. None: unknown, so a glide is simply stopped.
    pan_rate: float | None = None
    tilt_rate: float | None = None
    # +1: a larger pan_absolute turns the view right (UVC). -1: it turns the view left.
    pan_sign: int = 1
    # True: pan_absolute / tilt_absolute read back the last target, not where the gimbal is.
    # After a glide the app then writes the worked-out position back, so later steps start true.
    reads_target: bool = False
    # Limits in degrees, tighter than the control ranges, or None to use the control ranges.
    pan_limits: tuple[int, int] | None = None
    tilt_limits: tuple[int, int] | None = None
    # Seconds between small tilt nudges that stop the camera parking itself. 0: never.
    keep_awake_s: float = 0.0
    # Turn continuous autofocus back on whenever it is found off, unless the user chose manual.
    heal_autofocus: bool = False
    # "obsbot": tracking modes and on-camera settings through the optional SDK helper.
    sdk: str = ""


GENERIC = Profile(key="generic")

# OBSBOT Tiny 2 Lite, measured 2026-11-14 over USB on Linux 7.2 (uvcvideo):
# - one pan_speed unit turns 2.1 deg/s, one tilt_speed unit 1.05 deg/s; 0 stops;
# - pan/tilt_absolute read back the last target, and a changed target makes the gimbal
#   creep and then lunge, so holds use the speed controls;
# - larger pan_absolute turns the view left;
# - about two minutes after its last move the camera parks nose-down (grey picture) and
#   only a real gimbal move wakes it;
# - continuous autofocus was found switched off several times with no known cause.
OBSBOT_TINY_2_LITE = Profile(
    key="obsbot-tiny-2-lite",
    match=("obsbot tiny 2 lite", "obsbot_tiny_2_lite"),
    pan_rate=2.1,
    tilt_rate=1.05,
    pan_sign=-1,
    reads_target=True,
    pan_limits=(-130, 130),
    tilt_limits=(-70, 30),
    keep_awake_s=25.0,
    heal_autofocus=True,
    sdk="obsbot",
)

PROFILES = (OBSBOT_TINY_2_LITE,)


def profile_for(*names: str) -> Profile:
    """The profile whose match text appears in any of ``names`` (card name, camera id)."""
    haystack = " ".join(names).lower()
    for profile in PROFILES:
        if any(text in haystack for text in profile.match):
            return profile
    return GENERIC
