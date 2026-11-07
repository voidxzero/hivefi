"""Find album covers online (MusicBrainz release search + Cover Art Archive: free, no account) and embed them."""
import time

import requests

from . import art, tagedit

MB = "https://musicbrainz.org"
CAA = "https://coverartarchive.org"
UA = {"User-Agent": "Hivefi/0.1 ( https://github.com/voidxzero/hivefi )"}
_last_mb = 0.0


def _is_image(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"


def find_cover(artist: str, album: str, mb=MB, caa=CAA, timeout=15, pause=1.1):
    """(image bytes, MusicBrainz release id) of the best match with a front cover, or None. MusicBrainz asks for at
    most one request per second, so searches are spaced `pause` seconds apart."""
    global _last_mb
    if not album or album == "Unknown Album":
        return None
    wait = _last_mb + pause - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    q = f'release:"{album}"' + (f' AND artist:"{artist}"' if artist and artist not in ("Various Artists", "Unknown Artist") else "")
    try:
        r = requests.get(f"{mb.rstrip('/')}/ws/2/release/", params={"query": q.replace("\\", " "), "fmt": "json", "limit": 6},
                         headers=UA, timeout=timeout)
        _last_mb = time.monotonic()
        releases = r.json().get("releases", []) if r.status_code == 200 else []
        for rel in releases:
            if rel.get("score", 100) < 60:
                break
            c = requests.get(f"{caa.rstrip('/')}/release/{rel['id']}/front-500", headers=UA, timeout=timeout,
                             allow_redirects=True)
            if c.status_code == 200 and _is_image(c.content):
                return c.content, rel["id"]
    except (requests.RequestException, ValueError, KeyError):
        _last_mb = time.monotonic()
    return None


def has_cover(t: dict) -> bool:
    return bool(art._embedded(t["path"]) or art._folder_image(t["path"]))


def find_covers(tracks, only_missing=True, progress=None, mb=MB, caa=CAA, pause=1.1):
    """Look up and embed covers for the local tracks' albums. Returns (updated albums, not found [names], errors)."""
    albums: dict[str, list] = {}
    for t in tracks:
        if t.get("source") == "local" and t.get("path"):
            albums.setdefault(t.get("_ak") or f'{t.get("album_artist")}\x1f{t.get("album")}', []).append(t)
    done, missing, errors = 0, [], []
    for i, ts in enumerate(albums.values()):
        first = ts[0]
        name = f'{first.get("_aa") or first.get("artist")} – {first.get("album")}'
        if progress:
            progress(f"Cover art {i + 1}/{len(albums)}: {name}")
        if only_missing and any(has_cover(t) for t in ts):
            continue
        hit = find_cover(first.get("_aa") or first.get("artist") or "", first.get("album") or "", mb, caa, pause=pause)
        if hit is None:
            missing.append(name); continue
        for t in ts:
            try:
                tagedit.write_cover(t["path"], hit[0])
                art.invalidate(t)
            except Exception as e:  # noqa: BLE001
                errors.append(f'{t["path"].rsplit("/", 1)[-1]}: {e}')
        done += 1
    return done, missing, errors
