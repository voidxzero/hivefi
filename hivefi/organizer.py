"""Organise files: rename/move local tracks into a folder structure built from a tag pattern."""
import os
import re
import shutil
from pathlib import Path

DEFAULT_PATTERN = "%albumartist%/%album%/%disc%%track% - %title%"
TOKENS = ("albumartist", "artist", "album", "title", "track", "disc", "year", "genre")
SIDECARS = (".lrc", ".txt", ".cue")
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def clean(s: str, limit=120) -> str:
    s = _BAD.sub("_", str(s or "")).strip().strip(".")
    return re.sub(r"\s+", " ", s)[:limit].rstrip(". ")


def render(t: dict, pattern=DEFAULT_PATTERN) -> str:
    """Relative path (without extension) for a track dict."""
    multi = (t.get("disc_no") or 0) > 1
    vals = {
        "albumartist": clean(t.get("_aa") or t.get("album_artist") or t.get("artist")) or "Unknown Artist",
        "artist": clean(t.get("artist")) or "Unknown Artist",
        "album": clean(t.get("album")) or "Unknown Album",
        "title": clean(t.get("title")) or "Untitled",
        "track": f"{t['track_no']:02d}" if t.get("track_no") else "",
        "disc": f"{t['disc_no']}-" if multi else "",
        "year": str(t["year"]) if t.get("year") else "",
        "genre": clean(t.get("genre")),
    }
    parts = []
    for seg in pattern.split("/"):
        seg = re.sub(r"%(\w+)%", lambda m: vals.get(m.group(1), m.group(0)), seg)
        seg = re.sub(r"^[\s\-._]+|[\s\-._]+$", "", seg)          # nothing dangling from empty tokens
        seg = re.sub(r"\(\s*\)|\[\s*\]", "", seg).strip()
        if seg:
            parts.append(clean(seg))
    return "/".join(p for p in parts if p) or "Untitled"


def plan(tracks, dest_root, pattern=DEFAULT_PATTERN, ext_from_source=True):
    """[(track, new_path)] for local tracks that would change location. Collisions get ' (2)', ' (3)'…"""
    dest_root = Path(dest_root)
    taken: set[str] = set()
    out = []
    for t in tracks:
        if t.get("source") != "local" or not t.get("path"):
            continue
        src = Path(t["path"])
        rel = render(t, pattern)
        new = dest_root / (rel + src.suffix.lower())
        if str(new) == str(src):
            taken.add(str(new)); continue
        base, n = new, 2
        while str(new) in taken or (new.exists() and not _same(new, src)):
            new = base.with_name(f"{base.stem} ({n}){base.suffix}"); n += 1
        taken.add(str(new))
        if str(new) != str(src):               # a ' (2)' file that already sits at its own free name
            out.append((t, str(new)))
    return out


def _same(a: Path, b: Path) -> bool:
    try:
        return a.samefile(b)
    except OSError:
        return False


def _prune(dir_: Path, stop: Path):
    """Remove now-empty source folders (never above `stop`)."""
    try:
        while dir_ != stop and stop in dir_.parents and not any(dir_.iterdir()):
            dir_.rmdir(); dir_ = dir_.parent
    except OSError:
        pass


def execute(db, moves, mode="move", progress=None, prune_to=None):
    """Perform `plan()`. mode 'move' updates the library; 'copy' leaves it alone. Returns (done, errors)."""
    done, errors = 0, []
    for i, (t, new) in enumerate(moves):
        src, dst = Path(t["path"]), Path(new)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if mode == "copy":
                shutil.copy2(src, dst)
            else:
                shutil.move(str(src), str(dst))
            for ext in SIDECARS:
                side = src.with_suffix(ext)
                if side.is_file():
                    (shutil.copy2 if mode == "copy" else shutil.move)(str(side), str(dst.with_suffix(ext)))
            if mode == "move":
                db.conn.execute("UPDATE tracks SET path=?, key=?, mtime=? WHERE id=?",
                                (new, new, os.path.getmtime(new), t["id"]))
                if prune_to:
                    _prune(src.parent, Path(prune_to))
            done += 1
        except Exception as e:  # noqa: BLE001 - reported to the user
            errors.append(f"{src.name}: {e}")
        if progress:
            progress(f"Organising {i + 1}/{len(moves)}")
    db.commit()
    return done, errors
