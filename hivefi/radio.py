"""Internet radio: stations are stored in the library and played like tracks (live streams without a length)."""
from urllib.parse import urlparse

SCHEMES = ("http", "https", "icy", "mms", "rtsp")
LABEL = "Internet Radio"


def valid_url(url: str) -> bool:
    u = urlparse((url or "").strip())
    return u.scheme in SCHEMES and bool(u.netloc)


def radio_track(row) -> dict:
    """A station as a track dict the player and the track list understand (negative id: never clashes with a song)."""
    sid = int(row["id"])
    return dict(id=-sid, key=f"radio:{sid}", source="radio", remote_id=None, path=None, rpath=None, url=row["url"],
                title=row["name"], artist=LABEL, album_artist=LABEL, album=row.get("genre") or "Radio",
                genre=row.get("genre") or "", year=0, track_no=0, disc_no=1, duration=0, bitrate=0, suffix="stream",
                size=0, rating=0, starred=0, play_count=0, last_played=0, date_added=0, mtime=0, cover_id=None,
                rg_gain=None, folder_id=None, album_id=None, aa_tag="", _aa=LABEL, _ak=f"radio:{sid}",
                homepage=row.get("homepage") or "")
