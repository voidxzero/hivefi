"""Cover art lookup (embedded tags, folder images, Subsonic) with a disk cache."""
import hashlib
import os
import re
import threading
from pathlib import Path

import mutagen

from .config import cache_dir

FOLDER_NAMES = ("cover", "folder", "front", "album", "albumart")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
_DISC_DIR = re.compile(r"^(cd|disc|disk)\s*[-_ ]?\d+\b.*$", re.I)
_BACK = re.compile(r"\b(back|inlay|inside|cd|disc|tray|booklet)\b", re.I)
_none: set[str] = set()              # folders already known to have no cover (sshfs reads are slow: don't repeat them)
_none_lock = threading.Lock()


def _embedded(path) -> bytes | None:
    try:
        f = mutagen.File(path)
        if f is None:
            return None
        if hasattr(f, "pictures") and f.pictures:           # flac/ogg
            return f.pictures[0].data
        tags = f.tags
        if tags is None:
            return None
        if hasattr(tags, "getall") and tags.getall("APIC"):  # mp3
            return tags.getall("APIC")[0].data
        if "covr" in tags and tags["covr"]:                  # m4a
            return bytes(tags["covr"][0])
        if "metadata_block_picture" in tags:                 # opus/ogg
            import base64
            from mutagen.flac import Picture
            return Picture(base64.b64decode(tags["metadata_block_picture"][0])).data
    except Exception:
        pass
    return None


def _pick_image(names: list[str]) -> str | None:
    """The cover among a folder's image files: cover/folder/front.jpg, then names with front/cover/folder in them
    ("Album - Front.jpg"), then Windows Media Player's AlbumArt*Large/Small, then the only image there is."""
    imgs = [n for n in names if os.path.splitext(n)[1].lower() in IMG_EXT]
    if not imgs:
        return None
    stem = lambda n: os.path.splitext(n)[0].lower()
    for n in sorted(imgs):
        if stem(n) in FOLDER_NAMES:
            return n
    for word in ("front", "cover", "folder"):
        hits = sorted((n for n in imgs if word in stem(n) and not _BACK.search(stem(n).replace(word, " "))), key=len)
        if hits:
            return hits[0]
    for size in ("large", "small", ""):
        hits = sorted(n for n in imgs if stem(n).startswith("albumart") and size in stem(n))
        if hits:
            return hits[0]
    rest = [n for n in imgs if not _BACK.search(stem(n))]
    return rest[0] if len(rest) == 1 else None


def _folder_image(path) -> bytes | None:
    d = Path(path).parent
    dirs = [d, d.parent] if _DISC_DIR.match(d.name) else [d]      # "Album/CD 1/01.flac": the cover is in Album/
    for folder in dirs:
        try:
            name = _pick_image([f.name for f in folder.iterdir() if f.is_file()])
            if name:
                return (folder / name).read_bytes()
        except OSError:
            continue
    return None


def invalidate(track: dict):
    """Forget the cached cover of a local track's folder (after its embedded cover was changed)."""
    if track.get("source") == "local" and track.get("path"):
        cache = _local_cache(track["path"])
        cache.unlink(missing_ok=True)
        with _none_lock:
            _none.discard(str(cache)); _none.discard(str(cache) + "+")


def _local_cache(path) -> Path:
    return cache_dir() / ("local-" + hashlib.sha1(str(Path(path).parent).encode()).hexdigest())


def album_art(tracks: list, api=None, size=0, folders=3, per_folder=3) -> bytes | None:
    """The cover of an album from its tracks: local copies first (the copies may sit in different folders, and the
    embedded picture is not always on the first track), then the server's cover. Only a few files per folder are
    read, and a folder found without any cover is remembered: the music may be on a slow network mount."""
    by_dir: dict[str, list] = {}
    for t in tracks:
        if t.get("source") == "local" and t.get("path"):
            by_dir.setdefault(str(Path(t["path"]).parent), []).append(t)
    for d, ts in list(by_dir.items())[:folders]:
        data = get_art(ts[0], api, size)
        if data:
            return data
        tried = str(_local_cache(ts[0]["path"])) + "+"
        if tried in _none:
            continue
        for t in ts[1:per_folder]:
            data = _embedded(t["path"])
            if data:
                cache = _local_cache(t["path"])
                cache.write_bytes(data)
                with _none_lock:
                    _none.discard(str(cache))
                return data
        with _none_lock:
            _none.add(tried)
    if api is not None:
        for t in tracks:
            if t.get("source") == "remote" and t.get("cover_id"):
                return get_art(t, api, size)
    return None


def get_art(track: dict, api=None, size=0) -> bytes | None:
    """Return image bytes for a track dict, or None."""
    if track.get("source") == "local" and track.get("path"):
        p = track["path"]
        cache = _local_cache(p)
        if cache.exists():
            return cache.read_bytes()
        if str(cache) in _none:
            return None
        data = _embedded(p) or _folder_image(p)
        if data:
            cache.write_bytes(data)
        else:
            with _none_lock:
                _none.add(str(cache))
        return data
    cid = track.get("cover_id")
    if cid and api:
        cache = cache_dir() / ("sub-" + hashlib.sha1(f"{api.base}|{cid}|{size}".encode()).hexdigest())
        if cache.exists():
            return cache.read_bytes()
        data = api.get_cover(cid, size)
        if data:
            cache.write_bytes(data)
        return data
    return None
