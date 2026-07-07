"""Thin wrapper around yt-dlp: search, channel listings, stream resolution,
downloads and SponsorBlock segments."""
import json
import os
import sys
import urllib.parse
import urllib.request

GAMEDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(GAMEDIR, "pydeps"))

from yt_dlp import YoutubeDL  # noqa: E402

UA = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "socket_timeout": 20,
}


def _entry(e):
    """Normalise a flat playlist entry into a small dict."""
    vid = e.get("id") or ""
    dur = e.get("duration")
    if dur:
        dur = int(dur)
        h, m, s = dur // 3600, (dur % 3600) // 60, dur % 60
        dur_s = ("%d:%02d:%02d" % (h, m, s)) if h else ("%d:%02d" % (m, s))
    else:
        dur_s = ""
    return {
        "id": vid,
        "title": e.get("title") or "(untitled)",
        "channel": e.get("channel") or e.get("uploader") or "",
        "channel_id": e.get("channel_id") or "",
        "duration": dur_s,
        "thumb": "https://i.ytimg.com/vi/%s/mqdefault.jpg" % vid,
    }


def search(query, limit=20):
    opts = dict(BASE_OPTS, extract_flat="in_playlist")
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info("ytsearch%d:%s" % (limit, query), download=False)
    return [_entry(e) for e in info.get("entries", []) if e.get("id")]


def channel_videos(channel_id, limit=20):
    opts = dict(BASE_OPTS, extract_flat="in_playlist", playlistend=limit)
    url = "https://www.youtube.com/channel/%s/videos" % channel_id
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return [_entry(e) for e in info.get("entries", []) if e.get("id")]


def resolve(video_id, max_height=480, progressive_only=False,
            audio_only=False):
    """Return (video_url, audio_url_or_None, title, duration_s)."""
    if audio_only:
        fmt = "ba[ext=m4a]/ba/b"
    elif progressive_only:
        fmt = "18/b[height<=360]"
    else:
        fmt = ("bv*[height<=%d][vcodec^=avc1]+ba[ext=m4a]/"
               "b[height<=%d]/18/b") % (max_height, max_height)
    opts = dict(BASE_OPTS, format=fmt, noplaylist=True)
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info("https://www.youtube.com/watch?v=" + video_id,
                                download=False)
    title = info.get("title") or video_id
    dur = int(info.get("duration") or 0)
    reqs = info.get("requested_formats")
    if reqs:
        vurl = reqs[0]["url"]
        aurl = reqs[1]["url"] if len(reqs) > 1 else None
        return vurl, aurl, title, dur
    return info["url"], None, title, dur


def download(video_id, dest_dir, max_height=360, have_ffmpeg=False,
             progress=None):
    """Download a video for offline playback. Without ffmpeg only
    progressive (single-file) formats are used, so no merging is needed."""
    os.makedirs(dest_dir, exist_ok=True)
    if have_ffmpeg:
        fmt = ("bv*[height<=%d][vcodec^=avc1]+ba[ext=m4a]/"
               "b[height<=%d]/18") % (max_height, max_height)
    else:
        fmt = "b[height<=%d][acodec!=none][vcodec!=none]/18" % max_height
    hooks = []
    if progress:
        def hook(d):
            if d.get("status") == "downloading":
                t = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                got = d.get("downloaded_bytes") or 0
                progress(int(got * 100 / t) if t else 0)
            elif d.get("status") == "finished":
                progress(100)
        hooks = [hook]
    opts = dict(BASE_OPTS, format=fmt, noplaylist=True,
                outtmpl=os.path.join(dest_dir, "%(title).80s [%(id)s].%(ext)s"),
                progress_hooks=hooks)
    with YoutubeDL(opts) as ydl:
        ydl.download(["https://www.youtube.com/watch?v=" + video_id])


def sponsor_segments(video_id):
    """Return sorted [(start, end), ...] sponsor segments, or []."""
    try:
        q = urllib.parse.urlencode(
            [("videoID", video_id)] +
            [("category", c) for c in ("sponsor", "selfpromo", "interaction")])
        req = urllib.request.Request(
            "https://sponsor.ajay.app/api/skipSegments?" + q,
            headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        return sorted((float(s["segment"][0]), float(s["segment"][1]))
                      for s in data)
    except Exception:
        return []


def fetch_thumb(url, dest):
    """Download a thumbnail to dest. Returns True on success."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r, open(dest, "wb") as f:
            f.write(r.read())
        return True
    except Exception:
        try:
            os.remove(dest)
        except OSError:
            pass
        return False
