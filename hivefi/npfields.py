"""What the Now Playing panel can show: every field, how it is worded, and which ones are links."""
import time
from html import escape

from .stats import fmt_size

DEFAULT_FIELDS = ["title", "artist", "album"]


def _fmt_time(sec):
    sec = int(sec or 0)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _date(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""


def _khz(hz):
    return f"{hz / 1000:g} kHz" if hz else ""


def _channels(n):
    return {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}.get(n, f"{n} channels") if n else ""


def _fmt(t):
    s = (t.get("suffix") or "").upper()
    return "" if s in ("", "STREAM") else s


def _tech(t, ctx):
    parts = [_fmt(t), _khz(t.get("sample_rate")), f"{t['bit_depth']}-bit" if t.get("bit_depth") else "",
             _channels(t.get("channels")), f"{t['bitrate']} kbps" if t.get("bitrate") else ""]
    return " · ".join(p for p in parts if p)


def _output(t, ctx):
    o = (ctx or {}).get("out")
    if not o:
        return ""
    parts = [_khz(o.get("rate")), o.get("format", "").replace("LE", "").replace("BE", ""), _channels(o.get("channels"))]
    return " · ".join(p for p in parts if p)


def _track(t, ctx):
    n, d = t.get("track_no"), t.get("disc_no") or 1
    if not n:
        return ""
    return f"Disc {d} · Track {n}" if d > 1 else f"Track {n}"


def _source(t, ctx):
    return {"remote": "Navidrome", "radio": "Internet radio", "podcast": "Podcast"}.get(t.get("source"), "Local file")


# key: (label, getter(track, ctx) -> text, link kind or None)
FIELDS = {
    "title": ("Title", lambda t, c: t.get("title") or "", "track"),
    "artist": ("Artist", lambda t, c: t.get("artist") or "", "artist"),
    "album": ("Album", lambda t, c: t.get("album") or "", "album"),
    "album_artist": ("Album artist", lambda t, c: t.get("_aa") or t.get("album_artist") or "", "album_artist"),
    "year": ("Year", lambda t, c: str(t["year"]) if t.get("year") else "", "year"),
    "genre": ("Genre", lambda t, c: t.get("genre") or "", "genre"),
    "composer": ("Composer", lambda t, c: t.get("composer") or "", "composer"),
    "track": ("Track", _track, None),
    "tech": ("Format summary", _tech, None),
    "format": ("File type", lambda t, c: _fmt(t), None),
    "sample_rate": ("Sampling rate", lambda t, c: _khz(t.get("sample_rate")), None),
    "bit_depth": ("Bit depth", lambda t, c: f"{t['bit_depth']}-bit" if t.get("bit_depth") else "", None),
    "channels": ("Channels", lambda t, c: _channels(t.get("channels")), None),
    "bitrate": ("Bitrate", lambda t, c: f"{t['bitrate']} kbps" if t.get("bitrate") else "", None),
    "output": ("Output (after DSP)", _output, None),
    "duration": ("Length", lambda t, c: _fmt_time(t["duration"]) if t.get("duration") else "", None),
    "size": ("File size", lambda t, c: fmt_size(t["size"]) if t.get("size") else "", None),
    "rating": ("Rating", lambda t, c: "★" * t["rating"] + "☆" * (5 - t["rating"]) if t.get("rating") else "", None),
    "plays": ("Plays", lambda t, c: str(t["play_count"]) if t.get("play_count") else "", None),
    "skips": ("Skips", lambda t, c: str(t["skip_count"]) if t.get("skip_count") else "", None),
    "bpm": ("BPM", lambda t, c: str(t["bpm"]) if t.get("bpm") else "", None),
    "replaygain": ("ReplayGain", lambda t, c: f"{t['rg_gain']:+.2f} dB" if t.get("rg_gain") is not None else "", None),
    "added": ("Date added", lambda t, c: _date(t.get("date_added")), None),
    "last_played": ("Last played", lambda t, c: _date(t.get("last_played")), None),
    "source": ("Source", _source, None),
    "path": ("Location", lambda t, c: t.get("path") or t.get("rpath") or t.get("url") or "", None),
    "comment": ("Comment", lambda t, c: t.get("comment") or "", None),
}


def lines(fields, t: dict, ctx=None, labels=False) -> list[tuple[str, str, str | None]]:
    """[(key, text, link kind)] for the fields that have something to say for this track (empty ones are skipped)."""
    out = []
    for key in fields:
        f = FIELDS.get(key)
        if not f:
            continue
        text = f[1](t, ctx or {})
        if text:
            out.append((key, f"{f[0]}: {text}" if labels else text, f[2]))
    return out


def html(fields, t: dict, ctx=None, labels=False, link_color=None, lead: str | None = None) -> str:
    """The panel's text: the first line large and bold, the others plain; links where a field leads somewhere.
    `lead` is a headline put in front (an internet radio's current song)."""
    rows = [f"<div style='font-size:large;font-weight:bold'>{escape(lead)}</div>"] if lead else []
    for i, (key, text, link) in enumerate(lines(fields, t, ctx, labels)):
        body = escape(text)
        if link:
            style = f' style="color:{link_color}; text-decoration:none;"' if link_color else ""
            if labels and ": " in text:
                head, _, val = text.partition(": ")
                body = f"{escape(head)}: <a href=\"{link}\"{style}>{escape(val)}</a>"
            else:
                body = f'<a href="{link}"{style}>{body}</a>'
        rows.append(f"<div style='font-size:large;font-weight:bold'>{body}</div>" if i == 0 and not lead
                    else f"<div>{body}</div>")
    return "".join(rows)
