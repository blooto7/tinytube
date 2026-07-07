#!/usr/bin/env python3
"""TinyTube - a lightweight, gamepad-driven YouTube frontend for
PortMaster / muOS handhelds (Allwinner H700 and friends).

Search, subscriptions (local, no login) and watch history.
Playback is handed to mpv (software decode, quality capped)."""
import os
import sys
import json
import time
import fcntl
import struct
import threading

GAMEDIR = os.environ.get("TINYTUBE_GAMEDIR",
                         os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(GAMEDIR, "pydeps"))
sys.path.insert(0, GAMEDIR)
CONFDIR = os.environ.get("TINYTUBE_CONFDIR", os.path.join(GAMEDIR, "conf"))
CACHEDIR = os.path.join(CONFDIR, "cache")
os.makedirs(CACHEDIR, exist_ok=True)

try:
    import pygame  # noqa: E402
except ImportError:
    # Runtime has no pygame: use the wheel bundled with the port.
    sys.path.append(os.path.join(GAMEDIR, "pyfallback"))
    import pygame  # noqa: E402
import player  # noqa: E402

FONT_PATH = os.path.join(GAMEDIR, "assets", "DejaVuSans.ttf")
MAX_HISTORY = 50

# ---------------------------------------------------------------- storage


def load_json(name, default):
    try:
        with open(os.path.join(CONFDIR, name)) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(name, data):
    try:
        with open(os.path.join(CONFDIR, name), "w") as f:
            json.dump(data, f, indent=1)
    except Exception as e:
        print("save failed:", name, e)


# ---------------------------------------------------------------- input

# Raw joystick button numbers for the muOS H700 pad ("muOS-Keys").
# SDL indexes the BTN_ key codes in ascending order: 304/305/306/307 =
# physical A/B/Y/X, then L1 R1 SELECT START MENU L2 R2. Dpad is hat 0.
# Override in conf/settings.json under "btn_map" if your device differs.
DEFAULT_BTN_MAP = {"a": 0, "b": 1, "x": 3, "y": 2,
                   "l1": 4, "r1": 5, "select": 6, "start": 7}

KEY_ACTIONS = {
    pygame.K_UP: "up", pygame.K_DOWN: "down",
    pygame.K_LEFT: "left", pygame.K_RIGHT: "right",
    pygame.K_RETURN: "a", pygame.K_ESCAPE: "b",
    pygame.K_x: "x", pygame.K_y: "y",
    pygame.K_PAGEUP: "l1", pygame.K_PAGEDOWN: "r1",
    pygame.K_s: "start", pygame.K_q: "select",
}

REPEATABLE = ("up", "down", "left", "right")


class Input:
    """Translates pygame events into logical actions with d-pad repeat."""

    def __init__(self):
        # The SDL controller API is avoided on purpose: muOS swaps the
        # a/b/x/y mapping with its retro/modern setting, and the hat-based
        # dpad arrives twice (mapped buttons + raw hat events). Raw
        # joystick numbers are stable on these devices.
        self.controller = None
        self.joystick = None
        if pygame.joystick.get_count() > 0:
            self.joystick = pygame.joystick.Joystick(0)
            self.joystick.init()
            print("input: raw joystick", self.joystick.get_name())
        self.btn_map = dict(DEFAULT_BTN_MAP)
        self.held = {}         # action -> press time
        self.last_repeat = {}
        self.axis_dir = [0, 0]

    def _ctrl_action(self, button):
        m = {
            pygame.CONTROLLER_BUTTON_A: "a",
            pygame.CONTROLLER_BUTTON_B: "b",
            pygame.CONTROLLER_BUTTON_X: "x",
            pygame.CONTROLLER_BUTTON_Y: "y",
            pygame.CONTROLLER_BUTTON_DPAD_UP: "up",
            pygame.CONTROLLER_BUTTON_DPAD_DOWN: "down",
            pygame.CONTROLLER_BUTTON_DPAD_LEFT: "left",
            pygame.CONTROLLER_BUTTON_DPAD_RIGHT: "right",
            pygame.CONTROLLER_BUTTON_LEFTSHOULDER: "l1",
            pygame.CONTROLLER_BUTTON_RIGHTSHOULDER: "r1",
            pygame.CONTROLLER_BUTTON_START: "start",
            pygame.CONTROLLER_BUTTON_BACK: "select",
        }
        return m.get(button)

    def _joy_action(self, button):
        for name, num in self.btn_map.items():
            if num == button:
                return name
        return None

    def events(self):
        """Return a list of logical actions since last call."""
        acts = []

        def press(a):
            if a:
                acts.append(a)
                # select/start are tracked too, for the quit combo
                if a in REPEATABLE or a in ("select", "start"):
                    self.held[a] = time.time()
                    self.last_repeat[a] = time.time()

        def release(a):
            if a in self.held:
                del self.held[a]

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                acts.append("quit")
            elif ev.type == pygame.KEYDOWN:
                press(KEY_ACTIONS.get(ev.key))
            elif ev.type == pygame.KEYUP:
                release(KEY_ACTIONS.get(ev.key))
            elif ev.type == getattr(pygame, "CONTROLLERBUTTONDOWN", -1):
                press(self._ctrl_action(ev.button))
            elif ev.type == getattr(pygame, "CONTROLLERBUTTONUP", -2):
                release(self._ctrl_action(ev.button))
            elif ev.type == pygame.JOYBUTTONDOWN and self.controller is None:
                print("joybtn", ev.button)   # mapping aid in log.txt
                press(self._joy_action(ev.button))
            elif ev.type == pygame.JOYBUTTONUP and self.controller is None:
                release(self._joy_action(ev.button))
            elif ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                for a in ("left", "right", "up", "down"):
                    release(a)
                if hx < 0:
                    press("left")
                if hx > 0:
                    press("right")
                if hy > 0:
                    press("up")
                if hy < 0:
                    press("down")
            elif ev.type == pygame.JOYAXISMOTION and ev.axis in (0, 1):
                old = self.axis_dir[ev.axis]
                new = 1 if ev.value > 0.6 else (-1 if ev.value < -0.6 else 0)
                if new != old:
                    self.axis_dir[ev.axis] = new
                    names = (("left", "right"), ("up", "down"))[ev.axis]
                    for a in names:
                        release(a)
                    if new < 0:
                        press(names[0])
                    elif new > 0:
                        press(names[1])

        # auto-repeat held directions
        now = time.time()
        for a, t0 in list(self.held.items()):
            if a not in REPEATABLE:
                continue
            if now - t0 > 0.4 and now - self.last_repeat.get(a, 0) > 0.12:
                self.last_repeat[a] = now
                acts.append(a)
        return acts


# ---------------------------------------------------------------- app

FBIOGET_VSCREENINFO = 0x4600


class FbMirror:
    """Blit the app surface straight to /dev/fb0 when SDL has no real
    video driver for this screen (muOS H700: no X11/Wayland/DRM)."""

    def __init__(self, dev="/dev/fb0"):
        self.f = open(dev, "r+b", buffering=0)
        v = bytearray(160)
        fcntl.ioctl(self.f.fileno(), FBIOGET_VSCREENINFO, v)
        xres, yres, _, _, _, yoff, bpp = struct.unpack("7I", v[:28])
        if bpp != 32:
            self.f.close()
            raise RuntimeError("fb0 is %dbpp, need 32" % bpp)
        # fb_bitfield offsets: red @32, green @44, blue @56
        red, green, blue = (struct.unpack("I", v[o:o + 4])[0]
                            for o in (32, 44, 56))
        self.size = (xres, yres)
        self.offset = yoff * xres * 4
        self.surf = pygame.Surface(self.size, 0, 32,
                                   (0xFF << red, 0xFF << green,
                                    0xFF << blue, 0))

    def flush(self, screen):
        self.surf.blit(screen, (0, 0))
        self.f.seek(self.offset)
        self.f.write(self.surf.get_buffer().raw)

    def close(self):
        try:
            self.f.close()
        except OSError:
            pass


C_BG = (16, 16, 20)
C_FG = (235, 235, 235)
C_DIM = (140, 140, 150)
C_HI = (255, 60, 60)
C_SEL = (45, 45, 60)

OSK_ROWS = [
    "abcdefghij",
    "klmnopqrst",
    "uvwxyz0123",
    "456789-._'",
]
OSK_SPECIAL = ["SPACE", "DELETE", "CLEAR", "SEARCH"]


class App:
    def __init__(self):
        pygame.init()
        # No UI sounds; keep ALSA free for mpv (and stop underrun log spam).
        pygame.mixer.quit()
        self._open_display()
        pygame.mouse.set_visible(False)
        self.inp = Input()
        self.clock = pygame.time.Clock()

        self.settings = load_json("settings.json",
                                  {"quality": 360, "btn_map": None,
                                   "player_args": []})
        # Write the template out so users have a file to edit.
        if not os.path.exists(os.path.join(CONFDIR, "settings.json")):
            save_json("settings.json", self.settings)
        for k, v in (("audio_only", False), ("autoplay", False),
                     ("sponsorblock", True)):
            self.settings.setdefault(k, v)
        if self.settings.get("btn_map"):
            self.inp.btn_map.update(self.settings["btn_map"])
        self.subs = load_json("subscriptions.json", [])   # [{id,name}]
        self.history = load_json("history.json", [])      # newest first
        self.watchlater = load_json("watchlater.json", [])
        self.searches = load_json("searches.json", [])
        self.dl_job = None
        self.dl_dir = os.path.join(GAMEDIR, "downloads")
        os.makedirs(self.dl_dir, exist_ok=True)
        self.ffmpeg = player.find_ffmpeg(GAMEDIR)
        self.searchmenu_idx = 0
        self.dl_idx = 0
        self._playing_idx = None
        self._pending_start = 0

        self.player_bin, self.player_kind = player.find_player(GAMEDIR)
        print("player:", self.player_bin, self.player_kind)

        self.state = "menu"
        self.menu_idx = 0
        self.results = []
        self.results_title = ""
        self.results_idx = 0
        self.results_from = "search"   # search|subs|history
        self.query = ""
        self.osk_r, self.osk_c = 0, 0
        self.message = ""
        self.spinner_text = ""
        self.worker = None
        self.worker_result = None
        self.thumbs = {}          # video id -> Surface|None(pending)
        self.subs_idx = 0
        self.set_idx = 0
        self.running = True

    # ------------------------------------------------------------ display

    def _open_display(self):
        flags = 0 if "--windowed" in sys.argv else pygame.FULLSCREEN
        size = (640, 480) if "--windowed" in sys.argv else (0, 0)
        last_err = None
        for _ in range(10):
            try:
                self.screen = pygame.display.set_mode(size, flags)
                break
            except pygame.error as e:
                last_err = e
                time.sleep(0.5)
        else:
            raise SystemExit("could not open display: %s" % last_err)
        # Headless driver: draw offscreen and mirror frames to /dev/fb0.
        if getattr(self, "fbmirror", None):
            self.fbmirror.close()
        self.fbmirror = None
        if pygame.display.get_driver() in ("dummy", "offscreen"):
            try:
                self.fbmirror = FbMirror()
                self.screen = pygame.display.set_mode(self.fbmirror.size, 0)
            except Exception as e:
                print("fb0 mirror unavailable:", e)
        print("display:", pygame.display.get_driver(),
              "+ fb0 mirror" if self.fbmirror else "(native)")
        pygame.display.set_caption("TinyTube")
        self.W, self.H = self.screen.get_size()
        base = max(14, self.H // 30)
        self.f_big = pygame.font.Font(FONT_PATH, int(base * 1.5))
        self.f_med = pygame.font.Font(FONT_PATH, base)
        self.f_sml = pygame.font.Font(FONT_PATH, max(11, int(base * 0.78)))

    # ------------------------------------------------------------ helpers

    def text(self, surf, s, font, color, x, y, maxw=None):
        if maxw:
            while s and font.size(s)[0] > maxw:
                s = s[:-1]
        surf.blit(font.render(s, True, color), (x, y))

    def header(self, title):
        self.screen.fill(C_BG)
        self.text(self.screen, "TinyTube", self.f_med, C_HI, 16, 10)
        self.text(self.screen, title, self.f_med, C_DIM,
                  16 + self.f_med.size("TinyTube  ")[0], 10)
        pygame.draw.line(self.screen, (60, 60, 70),
                         (0, 44), (self.W, 44))

    def footer(self, s):
        self.text(self.screen, s, self.f_sml, C_DIM, 16, self.H - 26)

    def start_worker(self, fn, spinner):
        self.spinner_text = spinner
        self.worker_result = None

        def run():
            try:
                self.worker_result = ("ok", fn())
            except Exception as e:
                print("worker error:", repr(e))
                self.worker_result = ("err", str(e)[:200])
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()
        self.state = "busy"

    def is_subscribed(self, channel_id):
        return any(s["id"] == channel_id for s in self.subs)

    def toggle_sub(self, video):
        cid = video.get("channel_id")
        if not cid:
            return
        if self.is_subscribed(cid):
            self.subs = [s for s in self.subs if s["id"] != cid]
            self.flash("Unsubscribed from " + video.get("channel", ""))
        else:
            self.subs.append({"id": cid, "name": video.get("channel", cid)})
            self.flash("Subscribed to " + video.get("channel", ""))
        save_json("subscriptions.json", self.subs)

    def add_history(self, video):
        self.history = [h for h in self.history if h["id"] != video["id"]]
        self.history.insert(0, video)
        del self.history[MAX_HISTORY:]
        save_json("history.json", self.history)

    def flash(self, msg):
        self.message = msg
        self.message_t = time.time()

    # ------------------------------------------------------------ thumbs

    def request_thumb(self, video):
        vid = video["id"]
        if vid in self.thumbs:
            return
        self.thumbs[vid] = None
        path = os.path.join(CACHEDIR, vid + ".jpg")

        def fetch():
            import ytapi
            if not os.path.exists(path):
                if not ytapi.fetch_thumb(video["thumb"], path):
                    return
            # loaded on main thread on next draw
        if os.path.exists(path):
            self._load_thumb(vid, path)
        else:
            threading.Thread(target=fetch, daemon=True).start()

    def _load_thumb(self, vid, path):
        try:
            img = pygame.image.load(path)
            tw = int(self.W * 0.19)
            th = int(tw * 9 / 16)
            self.thumbs[vid] = pygame.transform.smoothscale(img, (tw, th))
        except Exception:
            self.thumbs[vid] = False

    def poll_thumbs(self):
        for vid, v in list(self.thumbs.items()):
            if v is None:
                path = os.path.join(CACHEDIR, vid + ".jpg")
                if os.path.exists(path):
                    self._load_thumb(vid, path)

    # ------------------------------------------------------------ states

    MENU = ["Search", "New videos", "Subscriptions", "Watch later",
            "History", "Downloads", "Settings", "Quit"]

    def do_menu(self, acts):
        for a in acts:
            if a in ("quit",):
                self.running = False
            elif a == "up":
                self.menu_idx = (self.menu_idx - 1) % len(self.MENU)
            elif a == "down":
                self.menu_idx = (self.menu_idx + 1) % len(self.MENU)
            elif a == "a":
                sel = self.MENU[self.menu_idx]
                if sel == "Search":
                    self.searchmenu_idx = 0
                    self.state = "searchmenu" if self.searches else "osk"
                elif sel == "New videos":
                    self._begin_newvideos()
                elif sel == "Subscriptions":
                    self.subs_idx = 0
                    self.state = "subs"
                elif sel == "Watch later":
                    self._show_list(self.watchlater, "Watch later",
                                    "watchlater")
                elif sel == "History":
                    self._show_list(self.history, "History", "history")
                elif sel == "Downloads":
                    self.dl_idx = 0
                    self.state = "downloads"
                elif sel == "Settings":
                    self.set_idx = 0
                    self.state = "settings"
                else:
                    self.running = False
            elif a == "b":
                pass

        self.header("")
        y = self.H // 5
        for i, item in enumerate(self.MENU):
            col = C_FG if i == self.menu_idx else C_DIM
            if i == self.menu_idx:
                pygame.draw.rect(self.screen, C_SEL,
                                 (self.W // 6 - 12, y - 6,
                                  self.W * 2 // 3, self.f_big.get_height() + 12))
            self.text(self.screen, item, self.f_big, col, self.W // 6, y)
            y += int(self.f_big.get_height() * 1.35)
        if not self.player_bin:
            self.footer("WARNING: no mpv/ffplay found - see README")
        else:
            self.footer("A select   B back   Select+Start quit")

    def do_osk(self, acts):
        rows = len(OSK_ROWS) + 1
        for a in acts:
            if a == "b":
                self.state = "menu"
            elif a == "up":
                self.osk_r = (self.osk_r - 1) % rows
                self.osk_c = min(self.osk_c, self._osk_maxc())
            elif a == "down":
                self.osk_r = (self.osk_r + 1) % rows
                self.osk_c = min(self.osk_c, self._osk_maxc())
            elif a == "left":
                self.osk_c = (self.osk_c - 1) % (self._osk_maxc() + 1)
            elif a == "right":
                self.osk_c = (self.osk_c + 1) % (self._osk_maxc() + 1)
            elif a == "x":
                self.query = self.query[:-1]
            elif a == "y":
                self.query += " "
            elif a == "start":
                self._begin_search()
            elif a == "a":
                if self.osk_r < len(OSK_ROWS):
                    self.query += OSK_ROWS[self.osk_r][self.osk_c]
                else:
                    key = OSK_SPECIAL[self.osk_c]
                    if key == "SPACE":
                        self.query += " "
                    elif key == "DELETE":
                        self.query = self.query[:-1]
                    elif key == "CLEAR":
                        self.query = ""
                    elif key == "SEARCH":
                        self._begin_search()

        self.header("Search")
        pygame.draw.rect(self.screen, C_SEL, (16, 60, self.W - 32, 40))
        self.text(self.screen, self.query + "_", self.f_med, C_FG, 24, 68,
                  self.W - 48)
        cell_w = (self.W - 32) // 10
        y0 = 130
        cell_h = int(self.f_med.get_height() * 1.7)
        for r, row in enumerate(OSK_ROWS):
            for c, ch in enumerate(row):
                x = 16 + c * cell_w
                y = y0 + r * cell_h
                sel = (self.osk_r == r and self.osk_c == c)
                if sel:
                    pygame.draw.rect(self.screen, C_HI,
                                     (x, y, cell_w - 4, cell_h - 4), 2)
                self.text(self.screen, ch, self.f_med,
                          C_FG if sel else C_DIM,
                          x + cell_w // 3, y + 4)
        y = y0 + len(OSK_ROWS) * cell_h
        sp_w = (self.W - 32) // len(OSK_SPECIAL)
        for c, key in enumerate(OSK_SPECIAL):
            x = 16 + c * sp_w
            sel = (self.osk_r == len(OSK_ROWS) and self.osk_c == c)
            if sel:
                pygame.draw.rect(self.screen, C_HI,
                                 (x, y, sp_w - 6, cell_h - 4), 2)
            self.text(self.screen, key, self.f_med,
                      C_FG if sel else C_DIM, x + 10, y + 4)
        self.footer("A type   X del   Y space   Start search   B back")

    def _osk_maxc(self):
        if self.osk_r < len(OSK_ROWS):
            return len(OSK_ROWS[self.osk_r]) - 1
        return len(OSK_SPECIAL) - 1

    def _begin_search(self):
        q = self.query.strip()
        if not q:
            return
        self.searches = [q] + [x for x in self.searches if x != q]
        del self.searches[15:]
        save_json("searches.json", self.searches)
        import ytapi

        def work():
            return ytapi.search(q)
        self.after_busy = ("results", "Results: " + q, "search")
        self.start_worker(work, "Searching...")

    def do_busy(self, acts):
        for a in acts:
            if a == "b":
                self.worker_result = ("err", "cancelled")
        if self.worker_result is not None:
            status, data = self.worker_result
            self.worker = None
            if status == "err":
                self.flash("Error: " + str(data))
                self.state = "menu"
            else:
                kind = self.after_busy[0]
                if kind == "results":
                    self.results = data
                    self.results_title = self.after_busy[1]
                    self.results_from = self.after_busy[2]
                    self.results_idx = 0
                    self.state = "results"
                    if not data:
                        self.flash("No results")
                elif kind == "play":
                    self._run_player(*data)
                elif kind == "flash":
                    self.flash(data if isinstance(data, str)
                               else self.after_busy[1])
                    self.state = "settings"
            return
        self.header("")
        dots = "." * (int(time.time() * 3) % 4)
        self.text(self.screen, self.spinner_text + dots, self.f_big, C_FG,
                  self.W // 6, self.H // 2 - 20)
        self.footer("B cancel")

    def do_results(self, acts):
        n = len(self.results)
        page = max(1, (self.H - 110) // self._row_h())
        for a in acts:
            if a == "b":
                self.state = "menu"
            elif n and a == "up":
                self.results_idx = (self.results_idx - 1) % n
            elif n and a == "down":
                self.results_idx = (self.results_idx + 1) % n
            elif n and a == "l1":
                self.results_idx = max(0, self.results_idx - page)
            elif n and a == "r1":
                self.results_idx = min(n - 1, self.results_idx + page)
            elif n and a == "a":
                self._begin_play(self.results[self.results_idx])
            elif n and a == "x":
                self.toggle_sub(self.results[self.results_idx])
            elif n and a == "y":
                v = self.results[self.results_idx]
                if self.results_from == "history":
                    self.results.pop(self.results_idx)
                    self.history = [h for h in self.history
                                    if h["id"] != v["id"]]
                    save_json("history.json", self.history)
                elif self.results_from == "watchlater":
                    self.results.pop(self.results_idx)
                    self.watchlater = [w for w in self.watchlater
                                       if w["id"] != v["id"]]
                    save_json("watchlater.json", self.watchlater)
                elif any(w["id"] == v["id"] for w in self.watchlater):
                    self.flash("Already in Watch later")
                else:
                    self.watchlater.insert(0, dict(v))
                    save_json("watchlater.json", self.watchlater)
                    self.flash("Added to Watch later")
                n = len(self.results)
                self.results_idx = min(self.results_idx, max(0, n - 1))
            elif n and a == "start":
                self._begin_download(self.results[self.results_idx])
        if self.state != "results":
            return

        self.header(self.results_title)
        if not self.results:
            self.text(self.screen, "Nothing here.", self.f_med, C_DIM,
                      16, self.H // 2)
        row_h = self._row_h()
        top = self.results_idx - max(0, self.results_idx - (page - 1))
        first = self.results_idx - top
        y = 56
        for i in range(first, min(n, first + page)):
            v = self.results[i]
            self.request_thumb(v)
            sel = (i == self.results_idx)
            if sel:
                pygame.draw.rect(self.screen, C_SEL,
                                 (8, y, self.W - 16, row_h - 4))
            th = self.thumbs.get(v["id"])
            tw = int(self.W * 0.19)
            if th:
                self.screen.blit(th, (12, y + 4))
            else:
                pygame.draw.rect(self.screen, (35, 35, 42),
                                 (12, y + 4, tw, int(tw * 9 / 16)))
            tx = tw + 24
            self.text(self.screen, v["title"], self.f_med,
                      C_FG if sel else C_DIM, tx, y + 4, self.W - tx - 16)
            sub = v.get("channel", "")
            if self.is_subscribed(v.get("channel_id", "")):
                sub = "* " + sub
            if v.get("duration"):
                sub += "   [%s]" % v["duration"]
            self.text(self.screen, sub, self.f_sml, C_DIM, tx,
                      y + 8 + self.f_med.get_height(), self.W - tx - 16)
            y += row_h
        self.poll_thumbs()
        rm = self.results_from in ("history", "watchlater")
        self.footer("A play  X sub  Y " + ("remove" if rm else "later")
                    + "  Start download  B back  L1/R1 page")

    def _row_h(self):
        return int(self.W * 0.19 * 9 / 16) + 12

    def do_subs(self, acts):
        n = len(self.subs)
        for a in acts:
            if a == "b":
                self.state = "menu"
            elif n and a == "up":
                self.subs_idx = (self.subs_idx - 1) % n
            elif n and a == "down":
                self.subs_idx = (self.subs_idx + 1) % n
            elif n and a == "y":
                gone = self.subs.pop(self.subs_idx)
                save_json("subscriptions.json", self.subs)
                self.flash("Unsubscribed from " + gone["name"])
                n = len(self.subs)
                self.subs_idx = min(self.subs_idx, max(0, n - 1))
            elif n and a == "a":
                ch = self.subs[self.subs_idx]
                import ytapi

                def work(cid=ch["id"]):
                    return ytapi.channel_videos(cid)
                self.after_busy = ("results", ch["name"], "subs")
                self.start_worker(work, "Loading channel...")
        if self.state != "subs":
            return
        self.header("Subscriptions")
        if not self.subs:
            self.text(self.screen,
                      "No subscriptions yet. Press X on a search result.",
                      self.f_med, C_DIM, 16, self.H // 2)
        y = 60
        for i, s in enumerate(self.subs):
            sel = (i == self.subs_idx)
            if sel:
                pygame.draw.rect(self.screen, C_SEL,
                                 (8, y - 4, self.W - 16,
                                  self.f_med.get_height() + 8))
            self.text(self.screen, s["name"], self.f_med,
                      C_FG if sel else C_DIM, 16, y, self.W - 32)
            y += int(self.f_med.get_height() * 1.5)
        self.footer("A open channel   Y unsubscribe   B back")

    SET_ITEMS = ["Quality", "Audio only", "Autoplay next",
                 "SponsorBlock", "Update yt-dlp", "Back"]

    def do_settings(self, acts):
        for a in acts:
            if a == "b":
                self.state = "menu"
            elif a == "up":
                self.set_idx = (self.set_idx - 1) % len(self.SET_ITEMS)
            elif a == "down":
                self.set_idx = (self.set_idx + 1) % len(self.SET_ITEMS)
            elif a in ("a", "left", "right"):
                item = self.SET_ITEMS[self.set_idx]
                if item == "Quality":
                    self.settings["quality"] = \
                        480 if self.settings.get("quality", 360) == 360 else 360
                    save_json("settings.json", self.settings)
                elif item in ("Audio only", "Autoplay next", "SponsorBlock"):
                    key = {"Audio only": "audio_only",
                           "Autoplay next": "autoplay",
                           "SponsorBlock": "sponsorblock"}[item]
                    self.settings[key] = not self.settings.get(key)
                    save_json("settings.json", self.settings)
                elif item == "Update yt-dlp" and a == "a":
                    self._update_ytdlp()
                elif item == "Back" and a == "a":
                    self.state = "menu"
        if self.state != "settings":
            return
        self.header("Settings")
        y = self.H // 3
        def onoff(k):
            return "on" if self.settings.get(k) else "off"
        vals = {"Quality": "%dp" % self.settings.get("quality", 360),
                "Audio only": onoff("audio_only"),
                "Autoplay next": onoff("autoplay"),
                "SponsorBlock": onoff("sponsorblock"),
                "Update yt-dlp": "", "Back": ""}
        for i, item in enumerate(self.SET_ITEMS):
            sel = (i == self.set_idx)
            col = C_FG if sel else C_DIM
            label = item + ("   < %s >" % vals[item] if vals[item] else "")
            if sel:
                pygame.draw.rect(self.screen, C_SEL,
                                 (self.W // 8 - 8, y - 4, self.W * 3 // 4,
                                  self.f_med.get_height() + 8))
            self.text(self.screen, label, self.f_med, col, self.W // 8, y)
            y += int(self.f_med.get_height() * 1.8)
        self.text(self.screen,
                  "Player: " + (self.player_bin or "NOT FOUND"),
                  self.f_sml, C_DIM, 16, self.H - 52)
        self.footer("A select / toggle   B back")

    def _update_ytdlp(self):
        import subprocess

        def work():
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade",
                 "--target", os.path.join(GAMEDIR, "pydeps"),
                 "--no-warn-script-location", "yt-dlp"],
                capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or "pip failed")[-200:])
            return "yt-dlp updated (restart TinyTube)"
        self.after_busy = ("flash", "updated")
        self.start_worker(work, "Updating yt-dlp...")

    # ------------------------------------------------------------ playback

    def _show_list(self, lst, title, source):
        self.results = list(lst)
        self.results_title = title
        self.results_from = source
        self.results_idx = 0
        self.state = "results"

    def do_searchmenu(self, acts):
        n = len(self.searches) + 1
        for a in acts:
            if a == "b":
                self.state = "menu"
            elif a == "up":
                self.searchmenu_idx = (self.searchmenu_idx - 1) % n
            elif a == "down":
                self.searchmenu_idx = (self.searchmenu_idx + 1) % n
            elif a == "y" and self.searchmenu_idx > 0:
                del self.searches[self.searchmenu_idx - 1]
                save_json("searches.json", self.searches)
                n = len(self.searches) + 1
                self.searchmenu_idx = min(self.searchmenu_idx, n - 1)
            elif a == "a":
                if self.searchmenu_idx == 0:
                    self.state = "osk"
                else:
                    self.query = self.searches[self.searchmenu_idx - 1]
                    self._begin_search()
        if self.state != "searchmenu":
            return
        self.header("Search")
        y = 64
        for i, item in enumerate(["< New search >"] + self.searches):
            sel = (i == self.searchmenu_idx)
            if sel:
                pygame.draw.rect(self.screen, C_SEL,
                                 (8, y - 4, self.W - 16,
                                  self.f_med.get_height() + 8))
            self.text(self.screen, item, self.f_med,
                      C_FG if sel else C_DIM, 16, y, self.W - 32)
            y += int(self.f_med.get_height() * 1.5)
        self.footer("A search   Y remove   B back")

    def _begin_newvideos(self):
        if not self.subs:
            self.flash("No subscriptions yet")
            return
        import ytapi
        subs = list(self.subs)[:12]

        def work():
            from concurrent.futures import ThreadPoolExecutor

            def one(sub):
                try:
                    vids = ytapi.channel_videos(sub["id"], 5)
                except Exception as e:
                    print("new videos failed:", sub["name"], repr(e))
                    return []
                for v in vids:
                    if not v.get("channel"):
                        v["channel"] = sub["name"]
                return vids
            with ThreadPoolExecutor(4) as ex:
                lists = [x for x in ex.map(one, subs) if x]
            # interleave newest-first per channel (flat entries carry no
            # reliable upload date)
            out = []
            for i in range(max((len(x) for x in lists), default=0)):
                for lst in lists:
                    if i < len(lst):
                        out.append(lst[i])
            return out
        self.after_busy = ("results", "New videos", "subs")
        self.start_worker(work, "Checking subscriptions...")

    def _begin_download(self, video):
        if self.dl_job and self.dl_job.get("status") == "downloading":
            self.flash("A download is already running")
            return
        import ytapi
        job = {"id": video["id"], "title": video["title"],
               "pct": 0, "status": "downloading"}
        self.dl_job = job

        def work():
            try:
                ytapi.download(video["id"], self.dl_dir,
                               self.settings.get("quality", 360),
                               have_ffmpeg=bool(self.ffmpeg),
                               progress=lambda pct: job.update(pct=pct))
                job["status"] = "done"
            except Exception as e:
                print("download failed:", repr(e))
                job["status"] = "failed"
        threading.Thread(target=work, daemon=True).start()
        self.flash("Downloading in background")

    def _local_files(self):
        try:
            return [f for f in sorted(os.listdir(self.dl_dir))
                    if f.rsplit(".", 1)[-1] in ("mp4", "m4a", "webm", "mkv")
                    and not f.endswith(".part")]
        except OSError:
            return []

    def do_downloads(self, acts):
        files = self._local_files()
        n = len(files)
        for a in acts:
            if a == "b":
                self.state = "menu"
            elif n and a == "up":
                self.dl_idx = (self.dl_idx - 1) % n
            elif n and a == "down":
                self.dl_idx = (self.dl_idx + 1) % n
            elif n and a == "a":
                if not self.player_bin:
                    self.flash("No mpv/ffplay found - see README")
                    continue
                name = files[self.dl_idx]
                self._run_player({"id": "local", "title": name},
                                 os.path.join(self.dl_dir, name), None,
                                 name, local=True, back_state="downloads")
            elif n and a == "y":
                try:
                    os.remove(os.path.join(self.dl_dir, files[self.dl_idx]))
                except OSError:
                    pass
                files = self._local_files()
                n = len(files)
                self.dl_idx = min(self.dl_idx, max(0, n - 1))
        if self.state != "downloads":
            return
        self.header("Downloads")
        y = 56
        if self.dl_job:
            j = self.dl_job
            label = {"downloading": "%d%%" % j.get("pct", 0),
                     "done": "done", "failed": "FAILED"}[j["status"]]
            self.text(self.screen, "> %s  (%s)" % (j["title"], label),
                      self.f_sml, C_HI, 16, y, self.W - 32)
            y += int(self.f_sml.get_height() * 1.8)
        if not files:
            self.text(self.screen,
                      "No downloads yet. Press Start on any video.",
                      self.f_med, C_DIM, 16, self.H // 2)
        for i, f in enumerate(files):
            sel = (i == self.dl_idx)
            if sel:
                pygame.draw.rect(self.screen, C_SEL,
                                 (8, y - 4, self.W - 16,
                                  self.f_med.get_height() + 8))
            self.text(self.screen, f, self.f_med,
                      C_FG if sel else C_DIM, 16, y, self.W - 32)
            y += int(self.f_med.get_height() * 1.5)
        self.footer("A play   Y delete   B back")

    def _begin_play(self, video):
        if not self.player_bin:
            self.flash("No mpv/ffplay found - see README")
            return
        import ytapi
        q = self.settings.get("quality", 360)
        prog = self.player_kind == "ffplay"
        audio = bool(self.settings.get("audio_only"))
        sponsor = bool(self.settings.get("sponsorblock")) and not prog
        # resume point from history
        self._pending_start = 0
        for h in self.history:
            if h["id"] == video["id"]:
                pos, dur = h.get("pos", 0), h.get("dur", 0)
                if pos > 15 and (not dur or pos < dur * 0.9):
                    self._pending_start = pos
                break
        self._playing_idx = (self.results_idx
                             if (self.results and
                                 self.results_idx < len(self.results) and
                                 self.results[self.results_idx] is video)
                             else None)

        def work():
            v, a, t, d = ytapi.resolve(video["id"], q, progressive_only=prog,
                                       audio_only=audio)
            segs = ytapi.sponsor_segments(video["id"]) if sponsor else []
            return (video, v, a, t, d, segs)
        self.after_busy = ("play", None, None)
        self.start_worker(work, "Resolving stream...")

    def _run_player(self, video, vurl, aurl, title, dur=0, segments=None,
                    local=False, back_state="results"):
        start = self._pending_start
        self._pending_start = 0
        no_video = bool(self.settings.get("audio_only")) and not local
        if not local:
            self.add_history(video)
        p = player.Player(self.player_bin, self.player_kind)

        # With the fb0 mirror the pygame display is headless: it can stay
        # up during playback so the event pump keeps working. Only a real
        # SDL driver owns the screen and must be released for the player.
        self.thumbs.clear()
        handoff = self.fbmirror is None and not no_video
        if handoff:
            pygame.display.quit()
            time.sleep(0.3)
        user_stop = False
        pos = float(start)
        got_dur = int(dur or 0)
        last_poll = 0.0
        try:
            p.launch(vurl, aurl, title,
                     self.settings.get("player_args") or [],
                     start=start, no_video=no_video)
            if start:
                p.show_text("Resuming")
            while p.running():
                if not handoff:
                    for a in self.inp.events():
                        if a in ("a", "start"):
                            p.pause_toggle()
                        elif a in ("x", "y"):
                            p.show_progress()
                        elif a == "left":
                            p.seek(-10)
                        elif a == "right":
                            p.seek(10)
                        elif a == "up":
                            p.seek(60)
                        elif a == "down":
                            p.seek(-60)
                        elif a in ("b", "select"):
                            user_stop = True
                            p.stop()
                now = time.time()
                if p.kind == "mpv" and now - last_poll > 1.0:
                    last_poll = now
                    t = p.get("playback-time")
                    if isinstance(t, (int, float)):
                        pos = float(t)
                        if not got_dur:
                            d = p.get("duration")
                            if isinstance(d, (int, float)):
                                got_dur = int(d)
                        for s0, s1 in (segments or []):
                            if s0 <= pos < s1 - 1.0:
                                p.seek_to(int(s1))
                                p.show_text("Skipped sponsored segment")
                                break
                if no_video:
                    self._draw_nowplaying(title, pos, got_dur)
                time.sleep(0.05)
        finally:
            p.stop()
            if handoff:
                pygame.display.init()
                self._open_display()
                pygame.mouse.set_visible(False)
        # remember where we stopped, for resume
        if not local:
            for h in self.history:
                if h["id"] == video["id"]:
                    if user_stop and pos > 15 and \
                            (not got_dur or pos < got_dur * 0.9):
                        h["pos"], h["dur"] = int(pos), got_dur
                    else:
                        h.pop("pos", None)
                    break
            save_json("history.json", self.history)
        self.state = back_state
        # autoplay the next entry from the same list
        if (not user_stop and not local and self.settings.get("autoplay")
                and self._playing_idx is not None
                and self._playing_idx + 1 < len(self.results)):
            self.results_idx = self._playing_idx + 1
            self._begin_play(self.results[self.results_idx])

    def _draw_nowplaying(self, title, pos, dur):
        self.header("Now playing")

        def fmt(sec):
            sec = int(sec)
            if sec >= 3600:
                return "%d:%02d:%02d" % (sec // 3600, sec % 3600 // 60,
                                         sec % 60)
            return "%d:%02d" % (sec // 60, sec % 60)
        self.text(self.screen, title, self.f_med, C_FG, 16, self.H // 3,
                  self.W - 32)
        t = fmt(pos) + (" / " + fmt(dur) if dur else "")
        self.text(self.screen, t, self.f_big, C_HI, 16, self.H // 2)
        if dur:
            bar_y = self.H // 2 + self.f_big.get_height() + 16
            frac = min(1.0, pos / dur)
            pygame.draw.rect(self.screen, (60, 60, 70),
                             (16, bar_y, self.W - 32, 8))
            pygame.draw.rect(self.screen, C_HI,
                             (16, bar_y, int((self.W - 32) * frac), 8))
        self.footer("A pause   Left/Right 10s   Up/Down 60s   B stop")
        pygame.display.flip()
        if self.fbmirror:
            self.fbmirror.flush(self.screen)

    # ------------------------------------------------------------ loop

    def run(self):
        self.message_t = 0
        handlers = {"menu": self.do_menu, "osk": self.do_osk,
                    "busy": self.do_busy, "results": self.do_results,
                    "subs": self.do_subs, "settings": self.do_settings,
                    "searchmenu": self.do_searchmenu,
                    "downloads": self.do_downloads}
        while self.running:
            acts = self.inp.events()
            if ("select" in acts and "start" in self.inp.held) or \
               ("start" in acts and "select" in self.inp.held):
                break
            handlers[self.state](acts)
            if self.message and time.time() - self.message_t < 3:
                w = self.f_med.size(self.message)[0] + 24
                pygame.draw.rect(self.screen, (60, 20, 20),
                                 (self.W - w - 8, 48, w, 36))
                self.text(self.screen, self.message, self.f_med, C_FG,
                          self.W - w + 4, 54)
            pygame.display.flip()
            if self.fbmirror:
                self.fbmirror.flush(self.screen)
            self.clock.tick(30)
        pygame.quit()


if __name__ == "__main__":
    App().run()
