"""Moving the camera: tap to step, hold to glide, zoom, center, autofocus.

This module knows nothing about Qt. It is given a ``controls`` object (a
``v4l2.Device`` or a stand-in with the same ``has`` / ``get`` / ``set`` /
``controls``), a clock, and a ``later(seconds, fn)`` scheduler, so the whole
behaviour can be tested without a camera.

Directions are always the viewer's: "right" turns the picture's view to the
right. Positions are in view degrees (right and up are positive); the camera's
own sign convention is applied only when a control is read or written.

Holding a key uses the camera's turn-speed controls when it has them, which
glide at a steady speed. Where the camera reports its last target instead of
its real position (see ``profiles``), the app works out where the glide ended
from speed x time and writes that back, so the next step starts from the truth.
A camera with only absolute controls is stepped a little on every tick instead.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .profiles import Profile

ARCSEC = 3600
DIRECTIONS = {"left": (-1, 0), "right": (1, 0), "up": (0, 1), "down": (0, -1)}
AUTOFOCUS_CONTROLS = ("focus_automatic_continuous", "focus_auto")


class Ptz:
    TAP_S = 0.25  # a press shorter than this is a tap: one full step
    ZOOM_TAP_S = 0.3  # a zoom tap zooms smoothly for this long
    HOLD_LIMIT_S = 15.0  # a lost key release must not leave the gimbal turning
    ZOOM_LIMIT_S = 10.0
    STEP_INTERVAL_S = 0.1  # absolute-only cameras: how often a hold moves the target
    ZOOM_STEP_INTERVAL_S = 0.15
    CENTER_SPEED = 45.0  # degrees per second on the way home
    QUIET_S = 20.0  # keep-awake waits this long after the last real move
    FULL_SPEED = 120.0  # the hold-speed setting that maps to a generic camera's top speed

    def __init__(
        self,
        controls,
        profile: Profile,
        *,
        later: Callable[[float, Callable[[], None]], object],
        clock: Callable[[], float] = time.monotonic,
        step_degrees: int = 20,
        hold_speed: int = 15,
        zoom_speed: int = 5,
    ):
        self.c = controls
        self.profile = profile
        self._later = later
        self._clock = clock
        self.step_degrees = step_degrees
        self.hold_speed = hold_speed
        self.zoom_speed = zoom_speed
        self.on_zoom_end: Callable[[], None] | None = None

        names = controls.controls()
        self.has_speed = "pan_speed" in names and "tilt_speed" in names
        self.has_absolute = "pan_absolute" in names and "tilt_absolute" in names
        self.has_zoom_speed = "zoom_continuous" in names
        self.has_zoom_absolute = "zoom_absolute" in names
        self.autofocus_control = next((n for n in AUTOFOCUS_CONTROLS if n in names), "")
        self._calibrated = self.has_speed and profile.pan_rate is not None and profile.tilt_rate is not None

        self._held: set[str] = set()
        self.active = False  # a hold is in progress
        self.centering = False
        self._glide = (0, 0)
        self._tap_dir = (0, 0)
        self._start = (0.0, 0.0)
        self._est = (0.0, 0.0)
        self._pressed_at = 0.0
        self._hold_began = 0.0
        self._last_tick = 0.0
        self._last_step = 0.0
        self._last_motion = -1e9
        self._timers: list = []
        self.zoom_dir = 0
        self._zoom_pressed_at = 0.0
        self._zoom_timers: list = []
        self._last_zoom_step = 0.0

    # ---- what this camera can do ---------------------------------------------
    @property
    def can_move(self) -> bool:
        return self.has_speed or self.has_absolute

    @property
    def can_zoom(self) -> bool:
        return self.has_zoom_speed or self.has_zoom_absolute

    @property
    def can_focus(self) -> bool:
        return bool(self.autofocus_control)

    @property
    def needs_ticks(self) -> bool:
        """True while ``tick`` should be called about every 40 ms."""
        return self.active or (self.zoom_dir != 0 and not self.has_zoom_speed)

    # ---- limits and units ----------------------------------------------------
    def _limits(self, name: str, profile_limits: tuple[int, int] | None, sign: int) -> tuple[float, float]:
        ctrl = self.c.controls().get(name)
        lo, hi = (ctrl.minimum / ARCSEC, ctrl.maximum / ARCSEC) if ctrl else (-180.0, 180.0)
        if profile_limits:
            lo, hi = max(lo, profile_limits[0]), min(hi, profile_limits[1])
        return (lo, hi) if sign > 0 else (-hi, -lo)

    def _clamp(self, pan: float, tilt: float) -> tuple[float, float]:
        plo, phi = self._limits("pan_absolute", self.profile.pan_limits, self.profile.pan_sign)
        tlo, thi = self._limits("tilt_absolute", self.profile.tilt_limits, 1)
        return max(plo, min(phi, pan)), max(tlo, min(thi, tilt))

    def _speed_units(self, name: str, rate: float | None, degrees_per_second: float) -> int:
        ctrl = self.c.controls().get(name)
        top = ctrl.maximum if ctrl else 1
        if rate:
            units = round(degrees_per_second / rate)
        else:
            units = round(top * min(1.0, degrees_per_second / self.FULL_SPEED))
        return max(1, min(top, units))

    def _rates(self) -> tuple[float, float]:
        """Degrees per second the view turns while gliding on each axis."""
        if self._calibrated:
            return (
                self._speed_units("pan_speed", self.profile.pan_rate, self.hold_speed) * self.profile.pan_rate,
                self._speed_units("tilt_speed", self.profile.tilt_rate, self.hold_speed) * self.profile.tilt_rate,
            )
        return float(self.hold_speed), float(self.hold_speed)

    # ---- position ------------------------------------------------------------
    def position(self) -> tuple[int, int] | None:
        """(pan, tilt) in view degrees as the camera reports them, or None."""
        if not self.has_absolute:
            return None
        pan, tilt = self.c.get("pan_absolute"), self.c.get("tilt_absolute")
        if pan is None or tilt is None:
            return None
        return round(pan * self.profile.pan_sign / ARCSEC), round(tilt / ARCSEC)

    def zoom(self) -> int | None:
        return self.c.get("zoom_absolute") if self.has_zoom_absolute else None

    def _write_position(self, pan: float, tilt: float) -> None:
        pan, tilt = self._clamp(pan, tilt)
        self.c.set("pan_absolute", round(pan) * self.profile.pan_sign * ARCSEC)
        self.c.set("tilt_absolute", round(tilt) * ARCSEC)

    # ---- hold to move --------------------------------------------------------
    def _vector(self) -> tuple[int, int]:
        x = sum(DIRECTIONS[d][0] for d in self._held)
        y = sum(DIRECTIONS[d][1] for d in self._held)
        return x, y

    def _integrate(self, now: float) -> None:
        dt = now - self._last_tick
        self._last_tick = now
        pan_rate, tilt_rate = self._rates()
        self._est = self._clamp(self._est[0] + self._glide[0] * pan_rate * dt, self._est[1] + self._glide[1] * tilt_rate * dt)

    def _set_glide(self, vector: tuple[int, int]) -> None:
        self._glide = vector
        if self.has_speed:
            self.c.set("pan_speed", vector[0] * self._speed_units("pan_speed", self.profile.pan_rate, self.hold_speed))
            self.c.set("tilt_speed", vector[1] * self._speed_units("tilt_speed", self.profile.tilt_rate, self.hold_speed))

    def press(self, direction: str) -> None:
        if not self.can_move or direction not in DIRECTIONS or direction in self._held or self.centering:
            return
        now = self._clock()
        self._held.add(direction)
        self._pressed_at = now
        self._tap_dir = self._vector()
        self._last_motion = now
        if not self.active:
            self.active = True
            self._hold_began = self._last_tick = self._last_step = now
            start = self.position() or (0, 0)
            self._start = self._est = (float(start[0]), float(start[1]))
        else:
            self._integrate(now)
        self._set_glide(self._vector())

    def release(self, direction: str) -> None:
        if direction == "all":
            self._held.clear()
        else:
            self._held.discard(direction)
        if not self.active:
            return
        now = self._clock()
        self._integrate(now)
        if self._held:
            self._set_glide(self._vector())
            return
        tap = now - self._pressed_at < self.TAP_S
        self._set_glide((0, 0))
        self.active = False
        self._last_motion = now
        if not self.has_absolute:
            return
        if tap:
            self._write_position(
                self._start[0] + self._tap_dir[0] * self.step_degrees,
                self._start[1] + self._tap_dir[1] * self.step_degrees,
            )
        elif not self.has_speed or (self._calibrated and self.profile.reads_target):
            self._write_position(*self._est)

    def tick(self) -> None:
        now = self._clock()
        if self.active:
            if now - self._hold_began > self.HOLD_LIMIT_S:
                self.release("all")
            else:
                self._integrate(now)
                self._last_motion = now
                if not self.has_speed and now - self._last_step >= self.STEP_INTERVAL_S:
                    self._last_step = now
                    self._write_position(*self._est)
        if self.zoom_dir and not self.has_zoom_speed and now - self._last_zoom_step >= self.ZOOM_STEP_INTERVAL_S:
            self._zoom_step()

    # ---- zoom ----------------------------------------------------------------
    def _zoom_step(self) -> None:
        ctrl = self.c.controls().get("zoom_absolute")
        current = self.c.get("zoom_absolute")
        if ctrl is None or current is None:
            return
        self._last_zoom_step = self._clock()
        step = max(ctrl.step, 1, (ctrl.maximum - ctrl.minimum) // 20)
        self.c.set("zoom_absolute", current + self.zoom_dir * step)

    def _cancel(self, timers: list) -> None:
        for timer in timers:
            cancel = getattr(timer, "cancel", None) or getattr(timer, "stop", None)
            if cancel:
                cancel()
        timers.clear()

    def zoom_press(self, direction: int) -> None:
        if not self.can_zoom or direction not in (1, -1):
            return
        self._cancel(self._zoom_timers)
        if self.zoom_dir == direction:
            return
        self.zoom_dir = direction
        self._zoom_pressed_at = self._clock()
        if self.has_zoom_speed:
            self.c.set("zoom_continuous", direction * self.zoom_speed)
        else:
            self._zoom_step()
        self._zoom_timers.append(self._later(self.ZOOM_LIMIT_S, self.end_zoom))

    def zoom_release(self, direction: int) -> None:
        if self.zoom_dir != direction:
            return
        left = self.ZOOM_TAP_S - (self._clock() - self._zoom_pressed_at)
        if left > 0 and self.has_zoom_speed:
            self._cancel(self._zoom_timers)
            self._zoom_timers.append(self._later(left, self.end_zoom))
        else:
            self.end_zoom()

    def end_zoom(self) -> None:
        self._cancel(self._zoom_timers)
        if self.zoom_dir == 0:
            return
        self.zoom_dir = 0
        if self.has_zoom_speed:
            self.c.set("zoom_continuous", 0)
            if self.on_zoom_end:
                self.on_zoom_end()

    # ---- center --------------------------------------------------------------
    def center(self) -> bool:
        """Go to pan 0, tilt 0 (zoom is kept). False if this camera cannot."""
        if not self.has_absolute or self.centering:
            return False
        self.release("all")
        position = self.position()
        if not (self._calibrated and position):
            self._write_position(0, 0)
            return True
        # Glide home on the speed controls, each axis stopping when it should arrive,
        # then write 0,0 so the target is exact.
        pan, tilt = position
        pan_units = self._speed_units("pan_speed", self.profile.pan_rate, self.CENTER_SPEED)
        tilt_units = self._speed_units("tilt_speed", self.profile.tilt_rate, self.CENTER_SPEED)
        pan_time = abs(pan) / (pan_units * self.profile.pan_rate)
        tilt_time = abs(tilt) / (tilt_units * self.profile.tilt_rate)
        self.centering = True
        self._last_motion = self._clock()
        self.c.set("pan_speed", -_sign(pan) * pan_units)
        self.c.set("tilt_speed", -_sign(tilt) * tilt_units)

        def arrived() -> None:
            self.c.set("pan_speed", 0)
            self.c.set("tilt_speed", 0)
            self._write_position(0, 0)
            self.centering = False
            self._last_motion = self._clock()
            self._timers.clear()

        self._timers = [
            self._later(pan_time, lambda: self.c.set("pan_speed", 0)),
            self._later(tilt_time, lambda: self.c.set("tilt_speed", 0)),
            self._later(max(pan_time, tilt_time) + 0.05, arrived),
        ]
        return True

    # ---- focus ---------------------------------------------------------------
    def autofocus(self) -> bool | None:
        value = self.c.get(self.autofocus_control) if self.autofocus_control else None
        return None if value is None else value == 1

    def set_autofocus(self, on: bool) -> bool:
        return bool(self.autofocus_control) and self.c.set(self.autofocus_control, 1 if on else 0)

    def heal_autofocus(self, manual: bool) -> None:
        """Turn autofocus back on if this camera is known to lose it and the user did not ask for manual."""
        if self.profile.heal_autofocus and not manual and self.autofocus() is False:
            self.set_autofocus(True)

    # ---- housekeeping --------------------------------------------------------
    def keep_awake(self) -> bool:
        """Nudge tilt one degree and back, unless something moved recently. True if it nudged."""
        if not self.profile.keep_awake_s or not self.has_absolute or self.active or self.centering:
            return False
        if self._clock() - self._last_motion < self.QUIET_S:
            return False
        tilt = self.c.get("tilt_absolute")
        if tilt is None:
            return False
        self.c.set("tilt_absolute", tilt + ARCSEC)
        self._later(1.5, lambda: self.c.set("tilt_absolute", tilt))
        return True

    def stop(self) -> None:
        """Stop everything now (the camera is being switched or the app is closing)."""
        self._held.clear()
        if self.active:
            self.active = False
            self._set_glide((0, 0))
        if self.centering:
            self._cancel(self._timers)
            self.centering = False
            self.c.set("pan_speed", 0)
            self.c.set("tilt_speed", 0)
        self.end_zoom()


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)
