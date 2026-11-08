"""Inbox: a folder where new music is dropped; it is read, named from its tags and moved into the library."""
import os
import shutil
import time
from pathlib import Path

from . import albums, organizer, scanner


def audio_files(root):
    for base, _dirs, names in os.walk(root):
        for n in sorted(names):
            if os.path.splitext(n)[1].lower() in scanner.AUDIO_EXT:
                yield os.path.join(base, n)


def process(inbox, dest_root, pattern=organizer.DEFAULT_PATTERN, min_age=5.0, now=None):
    """Move the audio files of `inbox` below `dest_root` using `pattern`. Files younger than `min_age` seconds may still
    be being copied and are left for next time; unreadable files stay where they are.
    Returns (moved paths [(from, to)], left alone [paths], errors)."""
    inbox, dest_root = Path(inbox), Path(dest_root)
    now = now or time.time()
    moved, left, errors = [], [], []
    if not inbox.is_dir() or not dest_root.is_dir() or inbox == dest_root or inbox in dest_root.parents:
        return moved, left, errors                         # nothing to do, or moving a folder into itself
    batch = []
    for path in audio_files(inbox):
        try:
            if now - os.path.getmtime(path) < min_age:
                left.append(path); continue
            tags = scanner.read_tags(path)
            if tags is None:
                left.append(path); continue
            batch.append(dict(tags, source="local", path=path))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{os.path.basename(path)}: {e}")
            left.append(path)
    albums.annotate(batch)               # albums are worked out for the whole batch, so a compilation stays together
    for track in batch:
        path = track["path"]
        try:
            dst = dest_root / (organizer.render(track, pattern) + Path(path).suffix.lower())
            base, n = dst, 2
            while dst.exists():
                dst = base.with_name(f"{base.stem} ({n}){base.suffix}"); n += 1
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(path, dst)
            for ext in organizer.SIDECARS:
                side = Path(path).with_suffix(ext)
                if side.is_file():
                    shutil.move(str(side), dst.with_suffix(ext))
            moved.append((path, str(dst)))
        except Exception as e:  # noqa: BLE001 - reported, the file stays in the inbox
            errors.append(f"{os.path.basename(path)}: {e}")
            left.append(path)
    for base, dirs, names in os.walk(inbox, topdown=False):      # remove folders that were emptied
        if Path(base) != inbox and not os.listdir(base):
            try:
                os.rmdir(base)
            except OSError:
                pass
    return moved, left, errors
