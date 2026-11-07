"""Write basic tags to local files with mutagen (EasyID3/Vorbis/MP4 abstraction)."""
import mutagen

FIELD_TO_TAG = {"title": "title", "artist": "artist", "album": "album", "album_artist": "albumartist",
                "genre": "genre", "year": "date", "track_no": "tracknumber", "disc_no": "discnumber"}


def write_tags(path: str, fields: dict):
    """fields: db-field -> value. Empty/None values are skipped. Raises on failure."""
    f = mutagen.File(path, easy=True)
    if f is None:
        raise ValueError("unsupported file type")
    if f.tags is None:
        f.add_tags()
    for k, v in fields.items():
        if v in (None, "", 0):
            continue
        f.tags[FIELD_TO_TAG[k]] = [str(v)]
    f.save()


def write_rating(path: str, rating: int):
    """Store a 0-5 star rating in the file: ID3 POPM, Vorbis/FLAC/Opus FMPS_RATING, MP4 FMPS_RATING freeform."""
    from mutagen.id3 import ID3, POPM
    from mutagen.mp4 import MP4, MP4FreeForm
    rating = max(0, min(5, int(rating)))
    f = mutagen.File(path)
    if f is None:
        raise ValueError("unsupported file type")
    if isinstance(f, MP4):
        f.tags["----:com.apple.iTunes:FMPS_RATING"] = [MP4FreeForm(f"{rating / 5:.2f}".encode())]
        f.save(); return
    tags = f.tags
    if tags is None:
        f.add_tags(); tags = f.tags
    if isinstance(tags, ID3):
        tags.delall("POPM")
        if rating:
            tags.add(POPM(email="hivefi", rating=min(255, rating * 51), count=0))
        f.save(); return
    if rating:
        tags["FMPS_RATING"] = [f"{rating / 5:.2f}"]
    elif "FMPS_RATING" in tags:
        del tags["FMPS_RATING"]
    f.save()
