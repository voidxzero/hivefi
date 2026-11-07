"""Lyrics lookup: .lrc/.txt next to file, embedded tags, or the Subsonic server."""
import re
from pathlib import Path

import mutagen


def _strip_lrc(text: str) -> str:
    return "\n".join(re.sub(r"^(\s*\[\d+:\d+(?:[.:]\d+)?\])+", "", ln).strip()
                     for ln in text.splitlines() if not re.match(r"^\s*\[[a-z]+:", ln, re.I))


TS = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")


def is_synced(text: str) -> bool:
    return bool(text) and len(TS.findall(text)) >= 2


def parse_lrc(text: str) -> list[tuple[int, str]]:
    """LRC -> sorted [(start_ms, line)]; handles several stamps per line and the [offset:] tag."""
    offset, out = 0, []
    for ln in text.splitlines():
        m = re.match(r"^\s*\[offset:\s*(-?\d+)\]", ln, re.I)
        if m:
            offset = int(m.group(1)); continue
        stamps = TS.findall(ln)
        if not stamps:
            continue
        body = TS.sub("", ln).strip()
        for mm, ss in stamps:
            out.append((max(0, int((int(mm) * 60 + float(ss.replace(":", "."))) * 1000) - offset), body))
    out.sort(key=lambda x: x[0])
    return out


LRCLIB = "https://lrclib.net"


def fetch_online(track: dict, base=LRCLIB, timeout=10):
    """Lyrics from LRCLIB (free, no account): time-synced (LRC) when it has them, otherwise plain; '' if the song is
    unknown to it; None when the service could not be reached (so the caller tries again later)."""
    import requests
    artist, title = track.get("artist") or "", track.get("title") or ""
    if not artist or not title:
        return ""
    params = {"artist_name": artist, "track_name": title}
    if track.get("album"):
        params["album_name"] = track["album"]
    if track.get("duration"):
        params["duration"] = int(round(track["duration"]))
    headers = {"User-Agent": "Hivefi (music player)"}
    try:
        r = requests.get(f"{base.rstrip('/')}/api/get", params=params, headers=headers, timeout=timeout)
        if r.status_code == 404:                      # exact match failed (different album/length): search by name
            r = requests.get(f"{base.rstrip('/')}/api/search", params={"track_name": title, "artist_name": artist},
                             headers=headers, timeout=timeout)
            hits = r.json() if r.status_code == 200 else []
            data = next((h for h in hits if h.get("syncedLyrics") or h.get("plainLyrics")), {})
        else:
            data = r.json() if r.status_code == 200 else {}
    except (requests.RequestException, ValueError):
        return None
    return data.get("syncedLyrics") or data.get("plainLyrics") or ""


def online_lyrics(track: dict, base=LRCLIB, save_sidecar=False) -> str:
    """fetch_online() with a cache (also for 'nothing found', so a track is only looked up once) and, if asked,
    a .lrc/.txt file written beside the audio file."""
    import hashlib

    from .config import cache_dir
    key = hashlib.sha1(f"{track.get('artist')}|{track.get('title')}|{track.get('album')}".encode()).hexdigest()
    f = cache_dir() / f"lyrics-{key}"
    if f.exists():
        text = f.read_text(errors="replace")
    else:
        text = fetch_online(track, base)
        if text is None:                              # offline / service down: don't remember it as "no lyrics"
            return ""
        f.write_text(text)
    if text and save_sidecar and track.get("source") == "local" and track.get("path"):
        side = Path(track["path"]).with_suffix(".lrc" if is_synced(text) else ".txt")
        if not side.exists() and not side.with_suffix(".lrc").exists():
            try:
                side.write_text(text, encoding="utf-8")
            except OSError:
                pass
    return text


def find_lyrics(track: dict, api=None, online=False, base=LRCLIB, save_sidecar=False) -> str:
    """Raw lyrics: LRC text when time-synced lyrics exist, otherwise plain text. With online=True a track that has
    none locally or on the server is looked up on LRCLIB."""
    text = _find_local_or_server(track, api)
    if not text and online:
        return online_lyrics(track, base, save_sidecar)
    return text


def _find_local_or_server(track: dict, api=None) -> str:
    if track.get("source") == "local" and track.get("path"):
        p = Path(track["path"])
        for ext in (".lrc", ".txt"):
            f = p.with_suffix(ext)
            if f.is_file():
                try:
                    return f.read_text(errors="replace")
                except OSError:
                    pass
        try:
            f = mutagen.File(str(p))
            tags = f.tags if f else None
            if tags is not None:
                if hasattr(tags, "getall") and tags.getall("USLT"):
                    return str(tags.getall("USLT")[0].text)
                for k in ("lyrics", "LYRICS", "unsyncedlyrics", "UNSYNCEDLYRICS", "syncedlyrics", "SYNCEDLYRICS",
                          "\xa9lyr"):
                    if k in tags and tags[k]:
                        v = tags[k]
                        return str(v[0] if isinstance(v, list) else v)
        except Exception:
            pass
        return ""
    if api and track.get("remote_id"):
        return api.lyrics(track["remote_id"], track.get("artist", ""), track.get("title", ""), synced=True)
    return ""


def get_lyrics(track: dict, api=None) -> str:
    """Plain text (timestamps removed)."""
    t = find_lyrics(track, api)
    return _strip_lrc(t) if is_synced(t) or TS.search(t or "") else t
