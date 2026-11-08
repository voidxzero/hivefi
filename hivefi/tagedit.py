"""Write basic tags to local files with mutagen (EasyID3/Vorbis/MP4 abstraction)."""
import mutagen
from mutagen.easymp4 import EasyMP4Tags

# The 'easy' MP4 layer lacks composer and BPM: teach it
EasyMP4Tags.RegisterTextKey("composer", "\xa9wrt")
EasyMP4Tags.RegisterKey("bpm", lambda tags, key: [str(v) for v in tags["tmpo"]],
                        lambda tags, key, value: tags.__setitem__("tmpo", [int(v) for v in value]),
                        lambda tags, key: tags.__delitem__("tmpo"))

FIELD_TO_TAG = {"title": "title", "artist": "artist", "album": "album", "album_artist": "albumartist",
                "genre": "genre", "year": "date", "track_no": "tracknumber", "disc_no": "discnumber",
                "composer": "composer", "bpm": "bpm"}


def write_tags(path: str, fields: dict):
    """fields: db-field -> value. Empty/None values are skipped. Raises on failure."""
    f = mutagen.File(path, easy=True)
    if f is None:
        raise ValueError("unsupported file type")
    if f.tags is None:
        f.add_tags()
    for k, v in fields.items():
        if v in (None, "", 0) or k == "comment":
            continue
        f.tags[FIELD_TO_TAG[k]] = [str(v)]
    f.save()
    if fields.get("comment"):
        _write_comment(path, str(fields["comment"]))


def _write_comment(path: str, text: str):
    """The comment has no common name in the 'easy' tag layer: ID3 COMM, Vorbis COMMENT, MP4 ©cmt."""
    from mutagen.id3 import COMM, ID3
    from mutagen.mp4 import MP4
    f = mutagen.File(path)
    if f is None:
        raise ValueError("unsupported file type")
    if isinstance(f, MP4):
        f.tags["\xa9cmt"] = [text]
    elif f.tags is not None and isinstance(f.tags, ID3):
        f.tags.delall("COMM"); f.tags.add(COMM(encoding=3, lang="eng", desc="", text=[text]))
    else:
        if f.tags is None:
            f.add_tags()
        f.tags["comment"] = [text]
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


def _picture_bytes_ok(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    raise ValueError("the cover must be a JPEG or PNG image")


def write_cover(path: str, data: bytes):
    """Embed `data` (JPEG/PNG) as the front cover: ID3 APIC, FLAC/Ogg/Opus pictures, MP4 covr."""
    import base64
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC, ID3
    from mutagen.mp4 import MP4, MP4Cover
    mime = _picture_bytes_ok(data)
    f = mutagen.File(path)
    if f is None:
        raise ValueError("unsupported file type")
    if isinstance(f, MP4):
        f.tags["covr"] = [MP4Cover(data, MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG)]
        f.save(); return
    if f.tags is None:
        f.add_tags()
    if isinstance(f.tags, ID3):
        f.tags.delall("APIC")
        f.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        f.save(); return
    pic = Picture(); pic.type, pic.mime, pic.desc, pic.data = 3, mime, "Cover", data
    if isinstance(f, FLAC):
        f.clear_pictures(); f.add_picture(pic); f.save(); return
    f.tags["metadata_block_picture"] = [base64.b64encode(pic.write()).decode()]      # Ogg Vorbis / Opus
    f.save()


def remove_cover(path: str):
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
    f = mutagen.File(path)
    if f is None or f.tags is None and not isinstance(f, FLAC):
        return
    if isinstance(f, MP4):
        f.tags.pop("covr", None)
    elif isinstance(f.tags, ID3):
        f.tags.delall("APIC")
    elif isinstance(f, FLAC):
        f.clear_pictures()
    elif "metadata_block_picture" in f.tags:
        del f.tags["metadata_block_picture"]
    f.save()
