# Shortcuts that work everywhere

Inside the QuickCam window the keys work on their own (I J K L, H N, U, O and
so on). To use them while another window is in front, bind keys in your window
manager to `quickcam ctl`, which passes a command to the running window.

`quickcam ctl` starts in a few hundredths of a second and does nothing but send
one line, so it is fine behind a key that is held down.

## Hold to move

Moving and zooming come in pairs: one command when the key goes down, one when
it comes up. A press shorter than a quarter of a second is a tap (one step,
20° by default); a longer one glides until the key is released.

```
quickcam ctl move-start left      quickcam ctl move-stop left
quickcam ctl zoom-start in        quickcam ctl zoom-stop in
```

If your window manager cannot bind key releases, use the one-step commands
`quickcam ctl move left` and `quickcam ctl zoom in` instead.

## Hyprland

```ini
# ~/.config/hypr/hyprland.conf
$cam = SUPER CTRL ALT
bind  = $cam, J, exec, quickcam ctl move-start left
bindr = $cam, J, exec, quickcam ctl move-stop left
bind  = $cam, L, exec, quickcam ctl move-start right
bindr = $cam, L, exec, quickcam ctl move-stop right
bind  = $cam, I, exec, quickcam ctl move-start up
bindr = $cam, I, exec, quickcam ctl move-stop up
bind  = $cam, K, exec, quickcam ctl move-start down
bindr = $cam, K, exec, quickcam ctl move-stop down
bind  = $cam, H, exec, quickcam ctl zoom-start in
bindr = $cam, H, exec, quickcam ctl zoom-stop in
bind  = $cam, N, exec, quickcam ctl zoom-start out
bindr = $cam, N, exec, quickcam ctl zoom-stop out
bind  = $cam, U, exec, quickcam ctl snap
bind  = $cam, O, exec, quickcam ctl record
bind  = $cam, P, exec, quickcam ctl focus
bind  = $cam, C, exec, quickcam ctl center
bind  = $cam, 7, exec, quickcam ctl track upper
```

If a modifier is released before the letter, the release binding may not fire.
QuickCam stops a glide by itself after 15 seconds and a zoom after 10, and
`quickcam ctl move-stop all` stops at once.

## Sway

```
bindsym           Mod4+Ctrl+Mod1+j exec quickcam ctl move-start left
bindsym --release Mod4+Ctrl+Mod1+j exec quickcam ctl move-stop left
bindsym           Mod4+Ctrl+Mod1+u exec quickcam ctl snap
```

## GNOME, KDE and others

Custom shortcuts there run a command on key press only. Bind the one-step
commands: `quickcam ctl move left`, `quickcam ctl zoom in`, `quickcam ctl snap`,
`quickcam ctl record`.

## All commands

Run `quickcam --help`. `quickcam ctl status` prints the state as JSON, which is
handy for a status bar. When QuickCam is not in front, snapshots, recording and
autofocus changes made this way are confirmed with a desktop notification
(through `notify-send`, if it is installed).
