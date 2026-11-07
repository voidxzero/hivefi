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
        aa_tag=_first(t, "albumartist"),                  # the explicit tag only ("" when the file has none)
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


def _parse_all(paths, cancelled, parallel_min):
    """Yield read_tags(path) for each path, in order. Large batches are parsed in worker processes: mutagen is
    pure Python, so threads would only fight the UI for the GIL (the window froze during the first scan)."""
    if len(paths) >= parallel_min:
        ex = None
        try:
            from concurrent.futures import ProcessPoolExecutor
            ex = ProcessPoolExecutor(max_workers=max(1, min(6, (os.cpu_count() or 2) - 1)))
            for res in ex.map(read_tags, paths, chunksize=32):
                if cancelled():
                    return
                yield res
            return
        except Exception:  # noqa: BLE001 - no usable process pool: fall back to this thread
            pass
        finally:
            if ex is not None:
                ex.shutdown(wait=False, cancel_futures=True)
    for p in paths:
        if cancelled():
            return
        yield read_tags(p)


def scan(db, folders, progress=None, cancelled=lambda: False, skip=(), parallel_min=64) -> tuple[int, int]:
    """Returns (added_or_updated, removed). Unchanged files (same mtime) are skipped.

    Folders in `skip` (switched off) or that are not currently reachable (e.g. an unmounted drive) are not
    scanned and their tracks are left untouched rather than being deleted from the library."""
    skip = set(skip)
    active = [f for f in folders if f not in skip and os.path.isdir(f)]
    kept = [f for f in folders if f not in active]
    existing = {r["key"]: r["mtime"] for r in db.conn.execute(
        "SELECT key, mtime FROM tracks WHERE source='local'")}
    db.begin_pass()
    db.touch([k for k in existing if _under(k, kept)])
    files = []
    for folder in active:
        for root, _, names in os.walk(folder):
            for name in names:
                if os.path.splitext(name)[1].lower() in AUDIO_EXT:
                    files.append(os.path.join(root, name))
    unchanged, todo = [], []
    for path in files:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if existing.get(path) == mtime:
            unchanged.append(path)
        else:
            todo.append((path, mtime))
    db.touch(unchanged)
    n = 0
    try:
        for i, (tags, (path, mtime)) in enumerate(zip(_parse_all([p for p, _ in todo], cancelled, parallel_min), todo)):
            if tags is not None:
                n += db.upsert_track(path, "local", path=path, mtime=mtime, cover_id=None, **tags)
            if i % 200 == 0:
                db.conn.commit()
                if progress:
                    progress(i + 1, len(todo))
    except BaseException:
        db.abort_pass(); raise
    if cancelled():
        db.abort_pass(); db.commit()
        return n, 0
    removed = db.end_pass("local")
    db.commit()
    db.all_tracks()                          # rebuild the track cache here, in the worker, not on the UI thread
    return n, removed
