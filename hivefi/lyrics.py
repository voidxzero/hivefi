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


def find_lyrics(track: dict, api=None) -> str:
    """Raw lyrics: LRC text when time-synced lyrics exist, otherwise plain text."""
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
