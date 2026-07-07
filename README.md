# TinyTube

A lightweight, gamepad-driven YouTube frontend for PortMaster on muOS and other CFWs, aimed at Allwinner H700 devices (RG35XX H/Plus/SP, RG40XX, RG28XX and similar).

Search YouTube with an on-screen keyboard, keep local subscriptions, watch history and a watch-later list (no Google login), and play videos through mpv with software decoding capped at 360p/480p. Extras: resume where you stopped, autoplay next, audio-only mode, offline downloads, a new-videos feed for your subscriptions, SponsorBlock skipping and search history.

## Requirements

- PortMaster installed (on muOS it is under Apps).
- Wifi. The app streams everything; nothing works offline.
- The PortMaster **Python 3.11** runtime. PortMaster downloads it automatically on first launch if you have a connection.
- **mpv** for playback. TinyTube looks for it in this order:
  1. `tinytube/bin/mpv` (a static aarch64 build you drop in yourself)
  2. `mpv` on the system PATH (muOS ships one for its media player)
  3. `ffplay` as a last resort (progressive 360p only, no pause/seek)

## Install (muOS)

1. Copy `TinyTube.sh` and the `tinytube` folder into your ports folder (`/mnt/mmc/ports`, i.e. the same place PortMaster puts ports; the SD card `ports` folder).
2. Refresh/launch it from the Ports menu.
3. First launch may take a minute while PortMaster fetches the Python runtime. pygame is bundled with the port (in `tinytube/pyfallback/`), so no on-device install is needed.

For other CFWs (ArkOS, ROCKNIX, Knulli...) copy to that firmware's usual ports folder instead.

## Controls

| Button | Browsing | Playback |
|---|---|---|
| D-pad | Navigate | Left/Right seek 10s, Up/Down seek 60s |
| A | Select / play | Pause/resume |
| B | Back | Stop |
| X | Subscribe/unsubscribe channel | Show progress |
| Y | Space (keyboard) / add to Watch later / remove item | Show progress |
| L1/R1 | Page up/down | |
| Start | Run search (keyboard) / download video (lists) | Pause/resume |
| Select+Start | Quit | |

## Notes

- **Quality**: Settings lets you toggle 360p/480p. 360p is the sweet spot on an H700; 480p H.264 usually holds up but drops frames on busy scenes.
- **yt-dlp breaks sometimes**: YouTube changes things; when search or playback stops working, use *Settings → Update yt-dlp* (needs wifi), then restart the app.
- **Custom player flags**: add `"player_args": ["--vo=sdl"]` (for example) to `tinytube/conf/settings.json` if mpv's default video output does not work on your setup.
- **Button mapping**: if your pad is not recognised as a game controller and buttons feel wrong, set `"btn_map": {"a":0,"b":1,"x":2,"y":3,"l1":4,"r1":5,"select":6,"start":7}` in `settings.json` with the right numbers.
- Subscriptions, history, watch-later and search history live in `tinytube/conf/` as plain JSON; downloads go to `tinytube/downloads/` on the SD card.
- **Downloads** use progressive 360p unless an `ffmpeg` binary is present (in `tinytube/bin/` or on PATH), which unlocks higher-quality merged downloads.
- **Resume**: stopping a video part-way stores the position; playing it again resumes automatically.
- **Autoplay next** plays the following entry in whatever list you started from.
- **SponsorBlock** (on by default, toggle in Settings) skips sponsor segments using the public sponsor.ajay.app API.


## Building from source

This repo contains source only. The installable port also needs two vendored packages inside `tinytube/`:

```
pip download yt-dlp --no-deps -d /tmp && unzip /tmp/yt_dlp-*.whl -d tinytube/pydeps
pip download pygame-ce --platform manylinux2014_aarch64 --python-version 3.11 \
    --only-binary=:all: --no-deps -d /tmp
unzip /tmp/pygame_ce-*aarch64*.whl -d tinytube/pyfallback
```

Then zip `TinyTube.sh` + `tinytube/` together, or copy them straight to your device's ports folder. A ready-made zip is attached to the Releases page when available.

## Thanks

Thanks to the [yt-dlp](https://github.com/yt-dlp/yt-dlp) project, the [mpv](https://mpv.io) project, [pygame-ce](https://pyga.me), the DejaVu fonts team, and the PortMaster crew for the Python runtime and ports infrastructure.
