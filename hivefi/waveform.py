"""Waveform peaks for the seek bar: decoded with ffmpeg to a coarse mono signal, reduced to ~1200 peaks, cached."""
import hashlib
import os
import shutil
import subprocess
from array import array

from .config import cache_dir

POINTS = 1200
RATE = 4000                      # analysis sample rate: plenty for an overview, ~2 MB for a 4-minute song


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def reduce_peaks(samples, n=POINTS) -> list[int]:
    """Absolute peaks of `samples` (array('h')) in n buckets, normalised to 0..255 (gamma 0.8 for readability)."""
    total = len(samples)
    if total == 0:
        return []
    n = max(1, min(n, total))
    out = []
    for i in range(n):
        seg = samples[i * total // n:max(i * total // n + 1, (i + 1) * total // n)]
        out.append(max(max(seg), -min(seg)))
    top = max(out) or 1
    return [min(255, round(255 * (p / top) ** 0.8)) for p in out]


def decode_peaks(src: str, n=POINTS, timeout=180) -> list[int]:
    """Peaks for a file path or http(s) URL. Raises RuntimeError when ffmpeg cannot decode it."""
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", str(RATE),
                        "-f", "s16le", "-"], capture_output=True, timeout=timeout)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError((r.stderr or b"ffmpeg failed").decode(errors="replace").strip()[-200:])
    s = array("h")
    s.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    return reduce_peaks(s, n)


def cache_file(key: str):
    return cache_dir() / ("wave-" + hashlib.sha1(key.encode()).hexdigest())


def track_key(t: dict, api=None) -> str | None:
    if t.get("source") == "local" and t.get("path"):
        try:
            st = os.stat(t["path"])
        except OSError:
            return None
        return f"local|{t['path']}|{st.st_mtime_ns}|{st.st_size}"
    if t.get("remote_id") and api:
        return f"remote|{api.base}|{t['remote_id']}|{t.get('size') or 0}"
    return None


def get_peaks(t: dict, api=None, cfg=None):
    """Cached/computed peaks for a track dict, or None (no ffmpeg, remote analysis off, unreadable)."""
    if not available():
        return None
    key = track_key(t, api)
    if key is None:
        return None
    f = cache_file(key)
    if f.exists():
        data = f.read_bytes()
        return list(data) if data else None
    if t.get("source") == "remote":
        if cfg is not None and not cfg.waveform_remote:
            return None
        src = api.stream_url(t["remote_id"], 0)
    else:
        src = t["path"]
    try:
        peaks = decode_peaks(src)
    except (RuntimeError, subprocess.SubprocessError, OSError):
        f.write_bytes(b"")                  # remember that this one cannot be analysed
        return None
    f.write_bytes(bytes(peaks))
    return peaks
