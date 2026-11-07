"""Albums = album title + album artist. Tracks are grouped into albums once, here, and everything that shows
albums (the Albums grid and the Genre / Artist / Album columns) uses the result.

Tag data is messy, so the rules are:
* a Subsonic/Navidrome track belongs to the album the server says it does (`album_id`);
* a local track with an *album artist tag* belongs to (album title, album artist), wherever the file lives;
* a local track without one belongs to (album title, folder): the tracks of an album sit together, so a
  compilation whose tracks all have different artists stays one album, and two different artists' "Greatest
  Hits" in different folders stay two albums. Folders called "CD 1", "Disc 2"... count as their parent folder;
* tracks of one album that are partly tagged join the album artist the tagged ones agree on.
The album artist shown for an untagged album is its artist, or "Various Artists" when the artists differ."""
import os
import re
from collections import Counter

VARIOUS = "Various Artists"
_DISC_DIR = re.compile(r"^(cd|disc|disk)\s*[-_ ]?\d+\b.*$", re.I)
_DISC_SUFFIX = re.compile(r"\s*[\(\[\-]\s*(disc|cd|disk)\s*\d+\s*[\)\]]?\s*$", re.I)
_SPACE = re.compile(r"\s+")


def norm(s) -> str:
    return _SPACE.sub(" ", str(s or "").casefold()).strip()


def norm_album(s) -> str:
    """Album title without a trailing '(Disc 1)' / '[CD2]' so the discs of one album meet."""
    return _DISC_SUFFIX.sub("", norm(s)).strip()


def album_dir(path) -> str:
    d, name = os.path.split(path or "")
    return os.path.dirname(d) if _DISC_DIR.match(os.path.basename(d) or "") else d


def annotate(tracks):
    """Give every track `_aa` (album artist to show) and `_ak` (album key). Safe to call repeatedly."""
    buckets: dict[tuple, list] = {}
    for t in tracks:
        if t.get("source") == "remote" and t.get("album_id"):
            buckets.setdefault(("r", str(t["album_id"])), []).append(t)
        else:
            buckets.setdefault(("l", norm_album(t.get("album")), album_dir(t.get("path"))), []).append(t)
    for key, ts in buckets.items():
        if key[0] == "r":
            _finish(("r", key[1]), ts)
            continue
        tags = Counter(norm(t.get("aa_tag")) for t in ts if norm(t.get("aa_tag")))
        if not tags:                                       # no album-artist tags: the folder is the album
            _finish(key, ts)
            continue
        for t in ts:                                       # tagged: (title, album artist); untagged join the largest
            t["_tag"] = norm(t.get("aa_tag")) or tags.most_common(1)[0][0]
        for tag in {t["_tag"] for t in ts}:
            _finish(("a", key[1], tag), [t for t in ts if t["_tag"] == tag], tag=tag)
        for t in ts:
            del t["_tag"]


def _finish(key, ts, tag=None):
    k = "\x1f".join(key)
    if tag is not None:
        names = Counter(t["aa_tag"] for t in ts if norm(t.get("aa_tag")) == tag)
        artist = names.most_common(1)[0][0] if names else ts[0].get("album_artist") or ts[0].get("artist")
    else:
        artists = {norm(t.get("artist")) for t in ts}
        if len(artists) <= 1:
            artist = ts[0].get("artist") or ts[0].get("album_artist") or "Unknown Artist"
        else:
            common = Counter(t.get("aa_tag") or "" for t in ts).most_common(1)[0][0] if key[0] == "r" else ""
            artist = common or VARIOUS
    for t in ts:
        t["_aa"], t["_ak"] = artist, k


def title_of(ts) -> str:
    """The album title to show for a group of tracks (the most common spelling)."""
    return Counter(t.get("album") or "Unknown Album" for t in ts).most_common(1)[0][0]
