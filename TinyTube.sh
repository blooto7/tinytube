#!/bin/bash
# TinyTube - lightweight YouTube frontend (PortMaster / muOS, aarch64)

XDG_DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}

if [ -d "/opt/system/Tools/PortMaster/" ]; then
  controlfolder="/opt/system/Tools/PortMaster"
elif [ -d "/opt/tools/PortMaster/" ]; then
  controlfolder="/opt/tools/PortMaster"
elif [ -d "$XDG_DATA_HOME/PortMaster/" ]; then
  controlfolder="$XDG_DATA_HOME/PortMaster"
else
  controlfolder="/roms/ports/PortMaster"
fi

source $controlfolder/control.txt
[ -f "${controlfolder}/mod_${CFW_NAME}.txt" ] && source "${controlfolder}/mod_${CFW_NAME}.txt"
get_controls

GAMEDIR="/$directory/ports/tinytube"
CONFDIR="$GAMEDIR/conf"
mkdir -p "$CONFDIR"
cd "$GAMEDIR"

> "$GAMEDIR/log.txt" && exec > >(tee "$GAMEDIR/log.txt") 2>&1

# --- Python 3.11 runtime -----------------------------------------------
runtime="python_3.11"
if [ ! -f "$controlfolder/libs/${runtime}.squashfs" ]; then
  if [ ! -f "$controlfolder/harbourmaster" ]; then
    pm_message "This port requires the latest PortMaster. See https://portmaster.games"
    sleep 5
    exit 1
  fi
  $ESUDO $controlfolder/harbourmaster --quiet --no-check runtime_check "${runtime}.squashfs"
fi

pythondir="$HOME/python_3.11"
$ESUDO mkdir -p "$pythondir"
if [[ "$PM_CAN_MOUNT" != "N" ]]; then
  $ESUDO umount "$pythondir" 2>/dev/null || true
fi
$ESUDO mount "$controlfolder/libs/${runtime}.squashfs" "$pythondir"

export PATH="$pythondir/bin:$PATH"
[ -f "$pythondir/bin/activate" ] && source "$pythondir/bin/activate"
PYBIN="$(command -v python3)"

# --- Environment --------------------------------------------------------
# No X11/Wayland/KMSDRM on muOS H700: run SDL headless (deterministic) and
# let main.py mirror frames to /dev/fb0 itself. Unbuffered so log.txt is
# in real order.
export SDL_VIDEODRIVER=dummy
export PYTHONUNBUFFERED=1
export SDL_GAMECONTROLLERCONFIG="$sdl_controllerconfig"
export LD_LIBRARY_PATH="$GAMEDIR/libs.${DEVICE_ARCH}:$LD_LIBRARY_PATH"
export TINYTUBE_GAMEDIR="$GAMEDIR"
export TINYTUBE_CONFDIR="$CONFDIR"
export PYTHONPYCACHEPREFIX="$CONFDIR/pycache"
export PYTHONPATH="$GAMEDIR/pydeps:$GAMEDIR:$PYTHONPATH"
export TMPDIR="$GAMEDIR/.tmp"
export PIP_CACHE_DIR="$GAMEDIR/.pipcache"
mkdir -p "$TMPDIR"

# pygame: prefer the runtime's build, else the wheel bundled in pyfallback/,
# else (last resort) a one-off pip install.
if ! "$PYBIN" -c "import sys; sys.path.append('$GAMEDIR/pyfallback'); import pygame; print('pygame', pygame.version.ver)"; then
  pm_message "Installing pygame (needs wifi)..."
  [ -f /etc/ssl/certs/ca-certificates.crt ] && export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
  "$PYBIN" -m pip install --target "$GAMEDIR/pydeps" --no-warn-script-location \
      --only-binary=:all: --timeout 30 pygame-ce \
    || pm_message "pygame install failed - check wifi and log.txt"
fi

# gptokeyb provides the global Select+Start exit hotkey.
$GPTOKEYB "python3" &
pm_platform_helper "$PYBIN"

"$PYBIN" "$GAMEDIR/main.py"

# Cleanup: never leave a stray player running.
pkill -f "tinytube-mpv" 2>/dev/null
pkill mpv 2>/dev/null
pkill ffplay 2>/dev/null

if [[ "$PM_CAN_MOUNT" != "N" ]]; then
  $ESUDO umount "$pythondir" 2>/dev/null
fi
pm_finish
