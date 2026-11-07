"""Scan local folders for audio files and store tags via mutagen."""
import os
import re
from pathlib import Path

import mutagen

AUDIO_EXT = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".m4b", ".aac", ".wav",
             ".wma", ".ape", ".wv", ".aiff", ".aif", ".mpc"}


def _first(tags, *names):
    for n in names:
        v = tags.get(n) if tags else None
        if v:
            return str(v[0]) if isinstance(v, (list, tuple)) else str(v)
    return ""


def _int(s):
    m = re.match(r"\s*(\d+)", str(s or ""))
    return int(m.group(1)) if m else 0


def _gain(raw) -> float | None:
    """ReplayGain track gain in dB from mp3 (TXXX), vorbis/flac, or mp4 tags."""
    try:
        tags = raw.tags
        if tags is None:
            return None
        cands = []
        if hasattr(tags, "getall"):
            cands = [str(f.text[0]) for f in tags.getall("TXXX")
                     if f.desc.lower() == "replaygain_track_gain" and f.text]
        for k in ("replaygain_track_gain", "REPLAYGAIN_TRACK_GAIN", "----:com.apple.iTunes:replaygain_track_gain"):
            try:
                v = tags[k]
            except (KeyError, TypeError):
                continue
            v = v[0] if isinstance(v, list) else v
            cands.append(bytes(v).decode() if isinstance(v, (bytes, bytearray)) or hasattr(v, "decode") else str(v))
        for c in cands:
            m = re.search(r"-?\d+(\.\d+)?", c)
            if m:
                return float(m.group(0))
    except Exception:
        pass
    return None


def read_tags(path: str) -> dict | None:
    try:
        easy = mutagen.File(path, easy=True)
    except Exception:
        return None
    if easy is None:
        return None
    t = easy.tags or {}
    info = easy.info
    p = Path(path)
    rating = 0
    rg = None
    try:  # POPM (mp3) or FMPS_RATING / RATING (vorbis)
        raw = mutagen.File(path)
        rg = _gain(raw) if raw is not None else None
        if raw is not None and raw.tags:
            if hasattr(raw.tags, "getall") and raw.tags.getall("POPM"):
                v = raw.tags.getall("POPM")[0].rating
                rating = 0 if v == 0 else min(5, max(1, round(v / 51)))
            else:
                fm = (raw.tags.get("FMPS_RATING") or raw.tags.get("fmps_rating")
                      or raw.tags.get("----:com.apple.iTunes:FMPS_RATING"))
                if fm:
                    v = fm[0]
                    rating = round(float(bytes(v).decode() if isinstance(v, bytes) else v) * 5)
    except Exception:
        pass
    return dict(
        title=_first(t, "title") or p.stem,
        artist=_first(t, "artist") or "Unknown Artist",
        album_artist=_first(t, "albumartist", "performer") or _first(t, "artist") or "Unknown Artist",
        album=_first(t, "album") or "Unknown Album",
        genre=_first(t, "genre"),
        year=_int(_first(t, "date", "originaldate", "year")),
        track_no=_int(_first(t, "tracknumber")),
        disc_no=_int(_first(t, "discnumber")) or 1,
        duration=float(getattr(info, "length", 0) or 0),
        bitrate=int((getattr(info, "bitrate", 0) or 0) / 1000),
        suffix=p.suffix.lstrip(".").lower(),
        size=os.path.getsize(path),
        rating=rating, rg_gain=rg,
    )


def _under(path, folders):
    return any(path.startswith(f.rstrip("/") + "/") for f in folders)


def scan(db, folders, progress=None, cancelled=lambda: False, skip=()) -> tuple[int, int]:
    """Returns (added_or_updated, removed). Unchanged files (same mtime) are skipped.

    Folders in `skip` (switched off) or that are not currently reachable (e.g. an unmounted drive) are not
    scanned and their tracks are left untouched rather than being deleted from the library."""
    skip = set(skip)
    active = [f for f in folders if f not in skip and os.path.isdir(f)]
    kept = [f for f in folders if f not in active]
    existing = {r["key"]: r["mtime"] for r in db.conn.execute(
        "SELECT key, mtime FROM tracks WHERE source='local'")}
    db.mark_unseen("local")
    db.conn.executemany("UPDATE tracks SET seen=1 WHERE key=?",
                        [(k,) for k in existing if _under(k, kept)])
    # keep unseen flag off for files that still exist but unchanged
    n = 0
    files = []
    for folder in active:
        for root, _, names in os.walk(folder):
            for name in names:
                if os.path.splitext(name)[1].lower() in AUDIO_EXT:
                    files.append(os.path.join(root, name))
    for i, path in enumerate(files):
        if cancelled():
            break
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if existing.get(path) == mtime:
            db.conn.execute("UPDATE tracks SET seen=1 WHERE key=?", (path,))
        else:
            tags = read_tags(path)
            if tags is None:
                continue
            db.upsert_track(path, "local", path=path, mtime=mtime, cover_id=None, **tags)
            n += 1
        if i % 200 == 0:
            db.commit()
            if progress:
                progress(i + 1, len(files))
    removed = 0 if cancelled() else db.delete_unseen("local")
    db.commit()
    return n, removed
