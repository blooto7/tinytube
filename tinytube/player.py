"""Launch and control mpv (preferred) or ffplay for playback."""
import json
import os
import shutil
import socket
import subprocess
import time

SOCK = "/tmp/tinytube-mpv.sock"


def find_player(gamedir):
    """Return (path, kind) where kind is 'mpv' or 'ffplay', or (None, None)."""
    for name in ("mpv", "ffplay"):
        cands = [
            os.path.join(gamedir, "bin", name),
            shutil.which(name),
            "/usr/bin/" + name,
            "/usr/local/bin/" + name,
        ]
        for c in cands:
            if c and os.path.isfile(c) and os.access(c, os.X_OK):
                return c, name
    return None, None


def find_ffmpeg(gamedir):
    """ffmpeg is optional; with it, downloads can merge higher qualities."""
    for c in (os.path.join(gamedir, "bin", "ffmpeg"),
              shutil.which("ffmpeg"), "/usr/bin/ffmpeg"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


class Player:
    def __init__(self, binary, kind):
        self.binary = binary
        self.kind = kind
        self.proc = None

    def launch(self, video_url, audio_url=None, title="", extra_args=None,
               start=0, no_video=False):
        try:
            os.remove(SOCK)
        except OSError:
            pass
        if self.kind == "mpv":
            args = [
                self.binary,
                "--really-quiet",
                "--no-terminal",
                "--no-input-default-bindings",
                "--input-ipc-server=" + SOCK,
                "--hwdec=no",
                "--cache=yes",
                "--demuxer-max-bytes=32MiB",
                "--network-timeout=30",
                "--force-media-title=" + (title or "TinyTube"),
            ]
            if no_video:
                args += ["--no-video", "--vo=null"]
            else:
                args.append("--fs")
            if start > 0:
                args.append("--start=%d" % int(start))
            if audio_url:
                args.append("--audio-file=" + audio_url)
            args += list(extra_args or [])
            args.append(video_url)
        else:  # ffplay: progressive streams only, no IPC
            args = [self.binary, "-autoexit", "-loglevel", "quiet"]
            args += ["-nodisp"] if no_video else ["-fs"]
            if start > 0:
                args += ["-ss", str(int(start))]
            args += ["-i", video_url]
        # The app runs SDL headless (dummy); the player must use the real
        # system SDL video driver or it plays invisibly.
        env = dict(os.environ)
        env.pop("SDL_VIDEODRIVER", None)
        self.proc = subprocess.Popen(args, env=env)
        return self.proc

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def _send(self, command):
        if self.kind != "mpv":
            return None
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect(SOCK)
            s.sendall((json.dumps({"command": command}) + "\n").encode())
            s.close()
        except OSError:
            pass

    def get(self, prop):
        """Read an mpv property over IPC; None on any failure."""
        if self.kind != "mpv":
            return None
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect(SOCK)
            s.sendall((json.dumps({"command": ["get_property", prop],
                                   "request_id": 7}) + "\n").encode())
            buf = b""
            for _ in range(20):
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                for line in buf.split(b"\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("request_id") == 7:
                        s.close()
                        return msg.get("data")
            s.close()
        except OSError:
            pass
        return None

    def pause_toggle(self):
        # osd-bar prefix: IPC commands do not show the OSD by default.
        self._send(["osd-bar", "cycle", "pause"])

    def seek(self, seconds):
        self._send(["osd-bar", "seek", seconds, "relative"])

    def seek_to(self, seconds):
        self._send(["osd-bar", "seek", seconds, "absolute"])

    def show_progress(self):
        self._send(["show-progress"])

    def show_text(self, msg):
        self._send(["show-text", msg, 2000])

    def stop(self):
        if not self.running():
            return
        if self.kind == "mpv":
            self._send(["quit"])
            for _ in range(20):
                if not self.running():
                    return
                time.sleep(0.1)
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
