"""Albums = album title + album artist. Tracks are grouped into albums once, here, and everything that shows
albums (the Albums grid and the Genre / Artist / Album columns) uses the result.

Tag data is messy, so the rules are:
* a Subsonic/Navidrome track belongs to the album the server says it does (`album_id`);
* a local track with an *album artist tag* belongs to (album title, album artist), wherever the file lives;
* a local track without one belongs to (album title, folder): the tracks of an album sit together, so a
  compilation whose tracks all have different artists stays one album, and two different artists' "Greatest
  Hits" in different folders stay two albums. Folders called "CD 1", "Disc 2"... count as their parent folder;
* tracks of one album that are partly tagged join the album artist the tagged ones agree on;
* a server track without an album id (synced before ids were stored) belongs to (album title, album artist);
* finally, copies of one album are one album: groups with the same title (ignoring "CD 2", "Disc 1"...) and the
  same album artist are merged, whether they are an m4a and an mp3 copy in two folders or the server's copy of a
  local album. Their tracks stay, every copy is listed. "Unknown Album" groups are never merged.
The album artist shown for an untagged album is its artist, or "Various Artists" when the artists differ."""
import os
import re
from collections import Counter

VARIOUS = "Various Artists"
_DISC_DIR = re.compile(r"^(cd|disc|disk)\s*[-_ ]?\d+\b.*$", re.I)
# "Epic (Disc 1)", "Epic [CD2]", "Epic - CD 3", "Epic Disc 2", "Alive 2007 CD1", "Epic Disc 1 of 2"
_DISC_SUFFIX = re.compile(r"\s*(?:[\(\[\-:,]\s*|\s)(disc|cd|disk)\s*\.?\s*\d+(?:\s*(?:of|/)\s*\d+)?\s*[\)\]]?\s*$", re.I)
_DISC_IN_PARENS = re.compile(r"\s+(disc|cd|disk)\s*\.?\s*\d+\s*\)\s*$", re.I)     # "Hits (Golden Edition CD2)"
_UNKNOWN = {"", "unknown album", "[unknown album]"}
_GUESTS = re.compile(r"\s+(?:&|feat\.?|ft\.?|featuring|vs\.?|with|x)\s+.*$|\s*[,/;]\s+.*$", re.I)
_SPACE = re.compile(r"\s+")


def norm(s) -> str:
    return _SPACE.sub(" ", str(s or "").casefold()).strip()


def norm_album(s) -> str:
    """Album title without a trailing '(Disc 1)' / '[CD2]' / 'CD 2' so the discs of one album meet."""
    n = norm(s)
    if _DISC_IN_PARENS.search(n):
        return _DISC_IN_PARENS.sub(")", n).strip()
    return _DISC_SUFFIX.sub("", n).strip() or n            # a title that is only "Disc 1" stays as it is


def main_artist(s) -> str:
    """The lead artist: "Severina & Boris Novković", "Severina feat. X" and "Severina" are all "severina".
    Servers report each track's own artist as the album artist when files have no album-artist tag."""
    n = norm(s)
    return _GUESTS.sub("", n).strip() or n


def album_dir(path) -> str:
    d, name = os.path.split(path or "")
    return os.path.dirname(d) if _DISC_DIR.match(os.path.basename(d) or "") else d


def annotate(tracks):
    """Give every track `_aa` (album artist to show) and `_ak` (album key). Safe to call repeatedly."""
    buckets: dict[tuple, list] = {}
    no_id = []                                             # server tracks synced before album ids were stored
    for t in tracks:
        if t.get("source") == "remote" and t.get("album_id"):
            buckets.setdefault(("r", str(t["album_id"])), []).append(t)
        elif t.get("source") == "remote":
            no_id.append(t)
        else:
            buckets.setdefault(("l", norm_album(t.get("album")), album_dir(t.get("path"))), []).append(t)
    for key, ts in buckets.items():
        if key[0] == "r":
            _finish(key, ts)
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
    _place_without_id(no_id, buckets)
    _merge_copies(tracks)


def _twin_key(t) -> tuple:
    return (norm(t.get("title")), main_artist(t.get("artist")), norm_album(t.get("album")), t.get("track_no") or 0)


def _place_without_id(no_id, buckets):
    """A server track without an album id joins the album of its local twin (the server usually serves the same
    files), so compilations stay whole like their folders; one without a twin goes by title + lead album artist.
    Title alone would merge different artists' "Greatest Hits"; the server's per-track album artist alone would split
    every untagged compilation into one album per artist."""
    if not no_id:
        return
    twins = {}
    for key, ts in buckets.items():
        if key[0] != "r":
            for t in ts:
                twins.setdefault(_twin_key(t), t)
    rest: dict[tuple, list] = {}
    for t in no_id:
        twin = twins.get(_twin_key(t))
        if twin is not None:
            t["_ak"], t["_aa"] = twin["_ak"], twin["_aa"]
        else:
            rest.setdefault(("s", norm_album(t.get("album")),
                             main_artist(t.get("album_artist") or t.get("artist"))), []).append(t)
    for key, ts in rest.items():
        _finish(key, ts)


def _merge_copies(tracks):
    """One album per (title, album artist): copies in other folders or on the server join the first one found
    (a local one when there is one), so the grid shows one tile with every copy in it."""
    groups: dict[str, list] = {}
    for t in tracks:
        groups.setdefault(t["_ak"], []).append(t)
    first: dict[tuple, str] = {}
    for k in sorted(groups, key=lambda k: (k[0] in "rs", k)):          # local groups first, then server ones
        ts = groups[k]
        title = norm_album(title_of(ts))
        if title in _UNKNOWN:
            continue
        canon = first.setdefault((title, main_artist(ts[0]["_aa"])), k)
        if canon != k:
            aa = groups[canon][0]["_aa"]
            for t in ts:
                t["_ak"], t["_aa"] = canon, aa


def _finish(key, ts, tag=None):
    k = "\x1f".join(key)
    if tag is not None:
        names = Counter(t["aa_tag"] for t in ts if norm(t.get("aa_tag")) == tag)
        artist = names.most_common(1)[0][0] if names else ts[0].get("album_artist") or ts[0].get("artist")
    elif key[0] == "s":                                    # grouped by the server's (lead) album artist: show it
        names = Counter(t.get("album_artist") or t.get("artist") or "Unknown Artist" for t in ts)
        artist = min(names, key=lambda n: (len(main_artist(n)) != len(norm(n)), -names[n]))   # plain "Severina" first
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
