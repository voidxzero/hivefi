"""Convert local tracks to another format with ffmpeg (tags and cover art are carried over where possible)."""
import shutil
import subprocess
from pathlib import Path

from . import organizer

FORMATS = {   # label: (extension, ffmpeg audio args, keeps cover?)
    "FLAC (lossless)": ("flac", ["-c:a", "flac", "-compression_level", "8"], True),
    "MP3 V0 (VBR ~245 kbps)": ("mp3", ["-c:a", "libmp3lame", "-q:a", "0"], True),
    "MP3 320 kbps": ("mp3", ["-c:a", "libmp3lame", "-b:a", "320k"], True),
    "MP3 192 kbps": ("mp3", ["-c:a", "libmp3lame", "-b:a", "192k"], True),
    "AAC 256 kbps (m4a)": ("m4a", ["-c:a", "aac", "-b:a", "256k"], True),
    "Opus 128 kbps": ("opus", ["-c:a", "libopus", "-b:a", "128k"], False),
    "Ogg Vorbis (q6)": ("ogg", ["-c:a", "libvorbis", "-q:a", "6"], False),
    "WAV (16-bit)": ("wav", ["-c:a", "pcm_s16le"], False),
}


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def command(src, dst, label) -> list[str]:
    ext, audio, cover = FORMATS[label]
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(src), "-map", "0:a:0", "-map_metadata", "0"]
    if cover:
        cmd += ["-map", "0:v:0?", "-c:v", "copy", "-disposition:v", "attached_pic"]
    return cmd + audio + [str(dst)]


def convert_tracks(tracks, dest_root, label, pattern=organizer.DEFAULT_PATTERN, progress=None):
    """Returns (done, errors). Existing destination files are skipped, sources are never modified."""
    ext = FORMATS[label][0]
    done, errors = 0, []
    local = [t for t in tracks if t.get("source") == "local" and t.get("path")]
    for i, t in enumerate(local):
        dst = Path(dest_root) / (organizer.render(t, pattern) + "." + ext)
        try:
            if dst.exists():
                errors.append(f"{dst.name}: already exists, skipped"); continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(command(t["path"], dst, label), capture_output=True, text=True)
            if r.returncode != 0:
                dst.unlink(missing_ok=True)
                errors.append(f"{Path(t['path']).name}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'ffmpeg failed'}")
            else:
                done += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{Path(t['path']).name}: {e}")
        if progress:
            progress(f"Converting {i + 1}/{len(local)}")
    return done, errors
