from fakes import ARC, FakeTime, absolute_only_controls, obsbot_controls

from camchord.profiles import GENERIC, OBSBOT_TINY_2_LITE
from camchord.ptz import Ptz


def make(controls, profile, **kw):
    t = FakeTime()
    return Ptz(controls, profile, later=t.later, clock=t.clock, **kw), t


def hold(ptz, t, direction, seconds, tick=0.04):
    ptz.press(direction)
    elapsed = 0.0
    while elapsed + tick <= seconds + 1e-9:
        t.advance(tick)
        ptz.tick()
        elapsed += tick
    ptz.release(direction)


# ---- OBSBOT profile: speed controls, target readback, inverted pan ---------------
def test_tap_right_steps_one_full_step_with_the_cameras_pan_sign():
    c = obsbot_controls()
    ptz, t = make(c, OBSBOT_TINY_2_LITE)
    ptz.press("right")
    t.advance(0.1)
    ptz.release("right")
    assert c.values["pan_speed"] == 0 and c.values["tilt_speed"] == 0
    assert c.values["pan_absolute"] == -20 * ARC  # on this camera a larger pan turns the view left
    assert ptz.position() == (20, 0)
    assert not ptz.active


def test_hold_glides_on_the_speed_control_and_writes_back_where_it_ended():
    c = obsbot_controls()
    ptz, t = make(c, OBSBOT_TINY_2_LITE, hold_speed=15)
    ptz.press("left")
    assert c.values["pan_speed"] == -7  # 15 deg/s at 2.1 deg/s per unit, view left = negative
    assert ("pan_absolute", 0) not in c.writes  # no absolute write while gliding
    for _ in range(50):
        t.advance(0.04)
        ptz.tick()
    ptz.release("left")
    assert c.values["pan_speed"] == 0
    # 2 s at 7 units x 2.1 deg/s = 29.4 deg to the left
    assert ptz.position() == (-29, 0)
    assert c.values["pan_absolute"] == 29 * ARC


def test_hold_up_uses_tilt_rate():
    c = obsbot_controls()
    ptz, t = make(c, OBSBOT_TINY_2_LITE, hold_speed=15)
    hold(ptz, t, "up", 1.0)
    assert c.values["tilt_speed"] == 0
    assert ptz.position() == (0, 15)  # 14 units x 1.05 = 14.7 deg/s for 1 s


def test_diagonal_then_release_one_key_keeps_gliding_on_the_other():
    c = obsbot_controls()
    ptz, t = make(c, OBSBOT_TINY_2_LITE)
    ptz.press("right")
    ptz.press("up")
    assert c.values["pan_speed"] > 0 and c.values["tilt_speed"] > 0
    t.advance(0.5)
    ptz.release("up")
    assert ptz.active and c.values["tilt_speed"] == 0 and c.values["pan_speed"] > 0
    t.advance(0.5)
    ptz.release("right")
    assert not ptz.active and c.values["pan_speed"] == 0
    pan, tilt = ptz.position()
    assert pan == 15 and tilt == 7


def test_position_is_clamped_to_the_profile_limits():
    c = obsbot_controls()
    c.values["tilt_absolute"] = 25 * ARC
    ptz, t = make(c, OBSBOT_TINY_2_LITE)
    ptz.press("up")
    t.advance(0.1)
    ptz.release("up")
    assert ptz.position() == (0, 30)  # 25 + 20 would pass the +30 limit


def test_a_lost_release_stops_the_glide():
    c = obsbot_controls()
    ptz, t = make(c, OBSBOT_TINY_2_LITE)
    ptz.press("right")
    for _ in range(int(16 / 0.04)):
        t.advance(0.04)
        ptz.tick()
    assert not ptz.active and c.values["pan_speed"] == 0


def test_center_glides_home_then_writes_zero():
    c = obsbot_controls()
    c.values["pan_absolute"] = 45 * ARC  # view 45 deg left
    c.values["tilt_absolute"] = -20 * ARC
    ptz, t = make(c, OBSBOT_TINY_2_LITE)
    assert ptz.center()
    assert c.values["pan_speed"] > 0 and c.values["tilt_speed"] > 0  # turning right and up, toward 0
    assert ptz.centering
    ptz.press("left")  # ignored while centering
    assert not ptz.active
    t.advance(3.0)
    assert not ptz.centering
    assert c.values["pan_speed"] == 0 and c.values["tilt_speed"] == 0
    assert c.values["pan_absolute"] == 0 and c.values["tilt_absolute"] == 0


def test_zoom_tap_runs_for_the_tap_time_then_stops():
    c = obsbot_controls()
    ptz, t = make(c, OBSBOT_TINY_2_LITE, zoom_speed=5)
    ended = []
    ptz.on_zoom_end = lambda: ended.append(True)
    ptz.zoom_press(1)
    assert c.values["zoom_continuous"] == 5
    t.advance(0.05)
    ptz.zoom_release(1)
    assert c.values["zoom_continuous"] == 5  # still zooming until the tap time is up
    t.advance(0.3)
    assert c.values["zoom_continuous"] == 0 and ptz.zoom_dir == 0 and ended == [True]


def test_zoom_hold_stops_on_release_and_after_the_safety_limit():
    c = obsbot_controls()
    ptz, t = make(c, OBSBOT_TINY_2_LITE)
    ptz.zoom_press(-1)
    t.advance(1.0)
    ptz.zoom_release(-1)
    assert c.values["zoom_continuous"] == 0
    ptz.zoom_press(1)
    t.advance(11.0)
    assert c.values["zoom_continuous"] == 0 and ptz.zoom_dir == 0


def test_keep_awake_nudges_only_when_idle():
    c = obsbot_controls()
    ptz, t = make(c, OBSBOT_TINY_2_LITE)
    c.values["tilt_absolute"] = -5 * ARC
    assert ptz.keep_awake()
    assert c.values["tilt_absolute"] == -4 * ARC
    t.advance(1.6)
    assert c.values["tilt_absolute"] == -5 * ARC
    ptz.press("left")
    assert not ptz.keep_awake()  # mid-hold
    t.advance(0.1)
    ptz.release("left")
    assert not ptz.keep_awake()  # just moved
    t.advance(21)
    assert ptz.keep_awake()


def test_autofocus_heal_respects_a_manual_choice():
    c = obsbot_controls()
    ptz, _ = make(c, OBSBOT_TINY_2_LITE)
    c.values["focus_automatic_continuous"] = 0
    ptz.heal_autofocus(manual=True)
    assert ptz.autofocus() is False
    ptz.heal_autofocus(manual=False)
    assert ptz.autofocus() is True


def test_stop_halts_everything():
    c = obsbot_controls()
    ptz, t = make(c, OBSBOT_TINY_2_LITE)
    ptz.press("right")
    ptz.zoom_press(1)
    ptz.stop()
    assert c.values["pan_speed"] == 0 and c.values["zoom_continuous"] == 0 and not ptz.active


# ---- generic camera with absolute controls only ---------------------------------
def test_generic_tap_uses_the_uvc_sign():
    c = absolute_only_controls()
    ptz, t = make(c, GENERIC, step_degrees=5)
    ptz.press("right")
    t.advance(0.1)
    ptz.release("right")
    assert c.values["pan_absolute"] == 5 * ARC and ptz.position() == (5, 0)


def test_generic_hold_steps_the_target_while_held():
    c = absolute_only_controls()
    ptz, t = make(c, GENERIC, hold_speed=10)
    assert ptz.can_move and not ptz.has_speed
    hold(ptz, t, "down", 0.5)
    assert c.values["tilt_absolute"] == -5 * ARC
    assert len([w for w in c.writes if w[0] == "tilt_absolute"]) >= 4  # moved during the hold, not only at the end


def test_generic_limits_come_from_the_control_range():
    c = absolute_only_controls()
    ptz, t = make(c, GENERIC, step_degrees=45)
    ptz.press("left")
    t.advance(0.1)
    ptz.release("left")
    assert ptz.position() == (-10, 0)


def test_zoom_without_a_speed_control_steps_while_held():
    c = absolute_only_controls()
    c.values["zoom_absolute"] = 100
    ptz, t = make(c, GENERIC)
    ptz.zoom_press(1)
    assert c.values["zoom_absolute"] == 120
    assert ptz.needs_ticks
    for _ in range(10):
        t.advance(0.04)
        ptz.tick()
    ptz.zoom_release(1)
    assert c.values["zoom_absolute"] == 160 and not ptz.needs_ticks


def test_camera_without_ptz_controls_does_nothing():
    from fakes import FakeControls

    c = FakeControls(brightness=(0, 255))
    ptz, t = make(c, GENERIC)
    assert not ptz.can_move and not ptz.can_zoom and not ptz.can_focus
    ptz.press("left")
    ptz.zoom_press(1)
    assert not ptz.center()
    assert c.writes == [] and not ptz.active
