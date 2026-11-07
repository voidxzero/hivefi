"""Cover art lookup (embedded tags, folder images, Subsonic) with a disk cache."""
import hashlib
import os
from pathlib import Path

import mutagen

from .config import cache_dir

FOLDER_NAMES = ("cover", "folder", "front", "album", "albumart")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")


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


def _folder_image(path) -> bytes | None:
    d = Path(path).parent
    try:
        for f in sorted(d.iterdir()):
            if f.suffix.lower() in IMG_EXT and f.stem.lower() in FOLDER_NAMES:
                return f.read_bytes()
    except OSError:
        pass
    return None


def get_art(track: dict, api=None, size=0) -> bytes | None:
    """Return image bytes for a track dict, or None."""
    if track.get("source") == "local" and track.get("path"):
        p = track["path"]
        cache = cache_dir() / ("local-" + hashlib.sha1(str(Path(p).parent).encode()).hexdigest())
        if cache.exists():
            return cache.read_bytes()
        data = _embedded(p) or _folder_image(p)
        if data:
            cache.write_bytes(data)
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
