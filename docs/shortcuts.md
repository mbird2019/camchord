# Shortcuts that work everywhere

Inside the CamChord window the keys work on their own (I J K L, H N, U, O and
so on). To use them while another window is in front, bind keys in your window
manager to `camchord ctl`, which passes a command to the running window.

`camchord ctl` starts in a few hundredths of a second and does nothing but send
one line, so it is fine behind a key that is held down.

## Hold to move

Moving and zooming come in pairs: one command when the key goes down, one when
it comes up. A press shorter than a quarter of a second is a tap (one step,
20° by default); a longer one glides until the key is released.

```
camchord ctl move-start left      camchord ctl move-stop left
camchord ctl zoom-start in        camchord ctl zoom-stop in
```

If your window manager cannot bind key releases, use the one-step commands
`camchord ctl move left` and `camchord ctl zoom in` instead.

## Hyprland

```ini
# ~/.config/hypr/hyprland.conf
$cam = SUPER CTRL ALT
bind  = $cam, J, exec, camchord ctl move-start left
bindr = $cam, J, exec, camchord ctl move-stop left
bind  = $cam, L, exec, camchord ctl move-start right
bindr = $cam, L, exec, camchord ctl move-stop right
bind  = $cam, I, exec, camchord ctl move-start up
bindr = $cam, I, exec, camchord ctl move-stop up
bind  = $cam, K, exec, camchord ctl move-start down
bindr = $cam, K, exec, camchord ctl move-stop down
bind  = $cam, H, exec, camchord ctl zoom-start in
bindr = $cam, H, exec, camchord ctl zoom-stop in
bind  = $cam, N, exec, camchord ctl zoom-start out
bindr = $cam, N, exec, camchord ctl zoom-stop out
bind  = $cam, U, exec, camchord ctl snap
bind  = $cam, O, exec, camchord ctl record
bind  = $cam, P, exec, camchord ctl focus
bind  = $cam, C, exec, camchord ctl center
bind  = $cam, 7, exec, camchord ctl track upper
```

If a modifier is released before the letter, the release binding may not fire.
CamChord stops a glide by itself after 15 seconds and a zoom after 10, and
`camchord ctl move-stop all` stops at once.

## Sway

```
bindsym           Mod4+Ctrl+Mod1+j exec camchord ctl move-start left
bindsym --release Mod4+Ctrl+Mod1+j exec camchord ctl move-stop left
bindsym           Mod4+Ctrl+Mod1+u exec camchord ctl snap
```

## GNOME, KDE and others

Custom shortcuts there run a command on key press only. Bind the one-step
commands: `camchord ctl move left`, `camchord ctl zoom in`, `camchord ctl snap`,
`camchord ctl record`.

## All commands

Run `camchord --help`. `camchord ctl status` prints the state as JSON, which is
handy for a status bar. When CamChord is not in front, snapshots, recording and
autofocus changes made this way are confirmed with a desktop notification
(through `notify-send`, if it is installed).
