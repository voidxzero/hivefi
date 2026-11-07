"""ReplayGain scanner: measure loudness (EBU R128 via ffmpeg) and write ReplayGain 2.0 tags (-18 LUFS reference)."""
import math
import re
import shutil
import subprocess

import mutagen

REFERENCE_LUFS = -18.0


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def measure(path: str) -> tuple[float, float]:
    """(integrated loudness in LUFS, true sample peak as a linear amplitude)."""
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", path, "-vn", "-af", "ebur128=peak=sample",
                        "-f", "null", "-"], capture_output=True, text=True)
    summary = r.stderr.rsplit("Summary:", 1)[-1]
    m = re.search(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", summary)
    p = re.search(r"Sample peak:\s+Peak:\s+(-?[\d.]+|-inf)\s+dBFS", summary)
    if not m or m.group(1) == "-inf":
        raise ValueError("could not measure loudness (silent or unreadable file)")
    peak = 0.0 if not p or p.group(1) == "-inf" else 10 ** (float(p.group(1)) / 20)
    return float(m.group(1)), peak


def album_loudness(items) -> float:
    """Duration-weighted energy mean of track loudnesses: [(lufs, duration_s)]."""
    tot = sum(d for _, d in items) or 1.0
    return 10 * math.log10(sum(d * 10 ** (l / 10) for l, d in items) / tot)


def write_rg(path: str, track_gain: float, track_peak: float, album_gain: float | None = None,
             album_peak: float | None = None):
    from mutagen.id3 import ID3, TXXX
    from mutagen.mp4 import MP4, MP4FreeForm
    vals = {"REPLAYGAIN_TRACK_GAIN": f"{track_gain:+.2f} dB", "REPLAYGAIN_TRACK_PEAK": f"{track_peak:.6f}",
            "REPLAYGAIN_REFERENCE_LOUDNESS": f"{REFERENCE_LUFS:.2f} LUFS"}
    if album_gain is not None:
        vals["REPLAYGAIN_ALBUM_GAIN"] = f"{album_gain:+.2f} dB"
        vals["REPLAYGAIN_ALBUM_PEAK"] = f"{(album_peak or 0):.6f}"
    f = mutagen.File(path)
    if f is None:
        raise ValueError("unsupported file type")
    if isinstance(f, MP4):
        for k, v in vals.items():
            f.tags[f"----:com.apple.iTunes:{k.lower()}"] = [MP4FreeForm(v.encode())]
        f.save(); return
    if f.tags is None:
        f.add_tags()
    if isinstance(f.tags, ID3):
        for k, v in vals.items():
            f.tags.delall(f"TXXX:{k}")
            f.tags.add(TXXX(encoding=3, desc=k, text=[v]))
    else:                                                # Vorbis comments (FLAC / Ogg / Opus)
        for k, v in vals.items():
            f.tags[k] = [v]
    f.save()


def scan_tracks(db, tracks, progress=None, by_album=True):
    """Measure, write tags and update the library. Returns (done, errors)."""
    import os
    local = [t for t in tracks if t.get("source") == "local" and t.get("path")]
    results, errors = {}, []
    for i, t in enumerate(local):
        try:
            results[t["id"]] = measure(t["path"])
        except Exception as e:  # noqa: BLE001
            errors.append(f"{os.path.basename(t['path'])}: {e}")
        if progress:
            progress(f"Measuring loudness {i + 1}/{len(local)}")
    groups: dict = {}
    for t in local:
        if t["id"] in results:
            groups.setdefault((t.get("_ak") or (t.get("album_artist") or t.get("artist"), t.get("album")))
                              if by_album else t["id"],
                              []).append(t)
    done = 0
    for g in groups.values():
        a_gain = a_peak = None
        if by_album:
            a_gain = REFERENCE_LUFS - album_loudness([(results[t["id"]][0], t.get("duration") or 1) for t in g])
            a_peak = max(results[t["id"]][1] for t in g)
        for t in g:
            lufs, peak = results[t["id"]]
            gain = REFERENCE_LUFS - lufs
            try:
                write_rg(t["path"], gain, peak, a_gain, a_peak)
                db.conn.execute("UPDATE tracks SET rg_gain=?, mtime=? WHERE id=?",
                                (round(gain, 2), os.path.getmtime(t["path"]), t["id"]))
                done += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{os.path.basename(t['path'])}: {e}")
    db.commit()
    return done, errors
