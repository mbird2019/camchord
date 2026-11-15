# obsbot-ai (optional helper)

OBSBOT cameras keep their tracking modes, gesture switches and a few picture
settings behind OBSBOT's own SDK; they are not reachable as ordinary camera
controls. This small program calls the SDK and prints one line of JSON, and
CamChord uses it when it finds it. Without it CamChord still moves, zooms,
previews, snapshots and records; only the tracking keys (4 to 0) and the
Camera tab stay unavailable.

The SDK is OBSBOT's and is not included here. You need:

- its headers (`dev/devs.hpp`, `dev/dev.hpp`, `util/comm.hpp`), from OBSBOT's SDK download;
- its library `libdev.so`. On Arch it comes with the AUR package
  `obsbot-camera-control`, which installs it in `/usr/lib`.

Build and install:

```sh
cd contrib/obsbot-ai
mkdir include && cp -r /path/to/sdk/include/* include/
make
make install          # copies obsbot-ai to ~/.local/bin
```

CamChord looks for `obsbot-ai` on your `PATH`. To point it somewhere else, set
`"obsbot_helper": "/full/path/to/obsbot-ai"` in `~/.config/camchord/settings.json`.

Try it by hand with the camera plugged in:

```sh
obsbot-ai status
obsbot-ai mode human upper     # none | group | hand | human [normal|upper|closeup|headless|lower]
obsbot-ai framing motion       # standard | headroom | motion
obsbot-ai gesture zoom off     # target | zoom | dynamic   on | off
obsbot-ai mirror on
```

Each call takes a few seconds, because the SDK has to find the camera first.
Tested with the OBSBOT Tiny 2 Lite only.
