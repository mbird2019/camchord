"""Stand-ins for a camera's controls, a clock and a scheduler, so motion can be tested without hardware."""

from quickcam.v4l2 import Control

ARC = 3600


class FakeControls:
    def __init__(self, **ranges):
        self._ctrls = {name: Control(i, name, lo, hi, 1, 0) for i, (name, (lo, hi)) in enumerate(ranges.items())}
        self.values = {name: 0 for name in ranges}
        self.writes = []

    def controls(self):
        return self._ctrls

    def has(self, name):
        return name in self._ctrls

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        if name not in self._ctrls:
            return False
        ctrl = self._ctrls[name]
        value = max(ctrl.minimum, min(ctrl.maximum, int(value)))
        self.values[name] = value
        self.writes.append((name, value))
        return True


def obsbot_controls():
    return FakeControls(
        pan_absolute=(-468000, 468000),
        tilt_absolute=(-324000, 324000),
        pan_speed=(-160, 160),
        tilt_speed=(-120, 120),
        zoom_absolute=(0, 100),
        zoom_continuous=(-100, 100),
        focus_automatic_continuous=(0, 1),
    )


def absolute_only_controls():
    return FakeControls(pan_absolute=(-36000, 36000), tilt_absolute=(-36000, 36000), zoom_absolute=(100, 500))


class FakeTime:
    def __init__(self):
        self.now = 100.0
        self.jobs = []

    def clock(self):
        return self.now

    def later(self, seconds, fn):
        job = _Job(self.now + seconds, fn)
        self.jobs.append(job)
        return job

    def advance(self, seconds):
        end = self.now + seconds
        while True:
            due = sorted((j for j in self.jobs if not j.cancelled and j.when <= end), key=lambda j: j.when)
            if not due:
                break
            job = due[0]
            self.jobs.remove(job)
            self.now = job.when
            job.fn()
        self.now = end


class _Job:
    def __init__(self, when, fn):
        self.when, self.fn, self.cancelled = when, fn, False

    def cancel(self):
        self.cancelled = True
