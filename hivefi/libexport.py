"""Export tracks (the current view, a selection) as CSV, JSON or an M3U8 playlist."""
import csv
import json
import time

COLUMNS = [("title", "Title"), ("artist", "Artist"), ("album", "Album"), ("album_artist", "Album artist"),
           ("genre", "Genre"), ("year", "Year"), ("track_no", "Track"), ("disc_no", "Disc"), ("duration", "Length (s)"),
           ("bitrate", "Bitrate (kbps)"), ("suffix", "Format"), ("rating", "Rating"), ("play_count", "Plays"),
           ("last_played", "Last played"), ("date_added", "Date added"), ("source", "Source"), ("path", "Path")]
DATE_FIELDS = ("last_played", "date_added")


def _cell(t, key):
    v = t.get(key)
    if key in DATE_FIELDS:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(v)) if v else ""
    if key == "path" and not v:
        return t.get("rpath") or ""
    if key == "duration":
        return round(v or 0, 1)
    return "" if v is None else v


def export_csv(tracks, path, keys=None):
    keys = keys or [k for k, _ in COLUMNS]
    labels = dict(COLUMNS)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:             # BOM: Excel opens the accents correctly
        w = csv.writer(f)
        w.writerow([labels[k] for k in keys])
        for t in tracks:
            w.writerow([_cell(t, k) for k in keys])
    return len(tracks)


def export_json(tracks, path, keys=None):
    keys = keys or [k for k, _ in COLUMNS]
    with open(path, "w", encoding="utf-8") as f:
        json.dump([{k: _cell(t, k) for k in keys} for t in tracks], f, ensure_ascii=False, indent=1)
    return len(tracks)


def export_m3u(tracks, path):
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for t in tracks:
            if t.get("source") != "local" or not t.get("path"):
                continue
            f.write(f"#EXTINF:{int(t.get('duration') or 0)},{t.get('artist', '')} - {t.get('title', '')}\n{t['path']}\n")
            n += 1
    return n


def export_any(tracks, path, keys=None):
    """Pick the format from the file extension (.csv / .json / .m3u8 / .m3u)."""
    ext = str(path).lower().rsplit(".", 1)[-1]
    if ext == "json":
        return export_json(tracks, path, keys)
    if ext in ("m3u", "m3u8"):
        return export_m3u(tracks, path)
    return export_csv(tracks, path, keys)
