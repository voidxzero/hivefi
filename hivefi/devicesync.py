"""Sync music to a folder or portable device (a phone, a player, a USB stick): copy, optionally convert, optionally
delete what is no longer wanted, and write playlists."""
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import convert, organizer
from .duplicates import LOSSLESS

AUDIO_EXT = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".wma", ".aiff", ".aif", ".ape", ".wv"}
CONVERT_MODES = ("never", "lossless", "all")        # which files are converted to the chosen format


@dataclass
class Options:
    dest: str
    pattern: str = organizer.DEFAULT_PATTERN
    fmt: str | None = None                          # a label from convert.FORMATS, None = copy files unchanged
    convert: str = "lossless"                       # never | lossless | all (only used when fmt is set)
    delete_extra: bool = False                      # remove audio files below dest that are not part of the sync
    playlist_dir: str = "Playlists"


@dataclass
class Item:
    track: dict
    rel: str                                        # path below dest
    action: str                                     # copy | convert | download | skip
    size: int = 0


@dataclass
class Plan:
    items: list = field(default_factory=list)
    wanted: set = field(default_factory=set)         # every path below dest this sync is about
    extra: list = field(default_factory=list)       # files an earlier sync wrote that are no longer wanted
    missing_files: int = 0                          # tracks whose source file is gone

    def count(self, action):
        return sum(1 for i in self.items if i.action == action)

    @property
    def bytes_to_write(self):
        return sum(i.size for i in self.items if i.action != "skip")


def target(t: dict, o: Options) -> tuple[str, str]:
    """(path below dest, action wanted) for one track, ignoring what already exists."""
    suffix = (t.get("suffix") or Path(t.get("path") or "").suffix.lstrip(".") or "mp3").lower()
    rel = organizer.render(t, o.pattern)
    if o.fmt and o.convert != "never" and (o.convert == "all" or suffix in LOSSLESS):
        ext = convert.FORMATS[o.fmt][0]
        if ext != suffix:
            return f"{rel}.{ext}", "convert"
    return f"{rel}.{suffix}", "copy"


MANIFEST = ".hivefi-sync.json"


def read_manifest(dest) -> set:
    """The files (relative paths) that Hivefi put on this device in earlier syncs."""
    import json
    try:
        return set(json.loads((Path(dest) / MANIFEST).read_text()).get("files", []))
    except (OSError, ValueError):
        return set()


def write_manifest(dest, files):
    import json
    p = Path(dest) / MANIFEST
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"files": sorted(files)}, indent=0))


def plan(tracks, o: Options) -> Plan:
    dest = Path(o.dest)
    pl, wanted, taken = Plan(), set(), set()
    for t in tracks:
        local = t.get("source") == "local" and t.get("path")
        if not local and not (t.get("source") == "remote" and t.get("remote_id")):
            continue
        if local and not os.path.exists(t["path"]):
            pl.missing_files += 1
            continue
        rel, action = target(t, o)
        base, n = rel, 2
        while rel.lower() in taken:                                  # two tracks with the same name: (2), (3)...
            stem, ext = os.path.splitext(base); rel = f"{stem} ({n}){ext}"; n += 1
        taken.add(rel.lower()); wanted.add(rel)
        out = dest / rel
        size = int(t.get("size") or 0) or (os.path.getsize(t["path"]) if local else 0)
        if not local:
            action = "download"
        up_to_date = False
        if out.exists():
            if action == "copy":
                up_to_date = out.stat().st_size == (os.path.getsize(t["path"]) if local else out.stat().st_size)
            elif action == "convert":
                up_to_date = out.stat().st_mtime >= os.path.getmtime(t["path"])
            else:
                up_to_date = out.stat().st_size > 0
        pl.items.append(Item(t, rel, "skip" if up_to_date else action, size))
    pl.wanted = wanted
    if o.delete_extra and dest.is_dir():
        # only what an earlier sync put there is ever removed: a phone's own recordings, voice notes and ringtones are
        # not ours, even though they are audio files
        pl.extra = sorted(r for r in read_manifest(dest) - wanted if (dest / r).is_file() and Path(r).suffix.lower() in AUDIO_EXT)
    return pl


def _download(api, t, out: Path, timeout=60):
    import requests
    r = requests.get(api.stream_url(t["remote_id"], 0), stream=True, timeout=timeout)
    r.raise_for_status()
    ct = r.headers.get("content-type", "").lower()
    if any(x in ct for x in ("json", "xml", "text/")):          # Subsonic reports errors with a normal 200 reply
        raise RuntimeError("the server did not send audio (check the login and that the track still exists)")
    tmp = out.with_suffix(out.suffix + ".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)
    os.replace(tmp, out)


def write_playlist(path: Path, rels, root: Path):
    """An M3U8 whose entries are relative to the playlist's folder (works on any device)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#EXTM3U"] + [os.path.relpath(root / r, path.parent).replace(os.sep, "/") for r in rels]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def execute(pl: Plan, o: Options, api=None, progress=None, cancelled=lambda: False, playlists=None) -> dict:
    """Carry out a plan. playlists: {name: [tracks]} written as M3U8 files. Returns counts and errors."""
    dest = Path(o.dest)
    stats = dict(copied=0, converted=0, downloaded=0, skipped=0, deleted=0, playlists=0, errors=[], cancelled=False)
    failed, removed = set(), set()
    todo = [i for i in pl.items if i.action != "skip"]
    stats["skipped"] = len(pl.items) - len(todo)
    for n, it in enumerate(todo):
        if cancelled():
            stats["cancelled"] = True
            return stats
        out, src = dest / it.rel, it.track.get("path")
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            if it.action == "copy":
                shutil.copy2(src, out); stats["copied"] += 1
                for ext in organizer.SIDECARS:                       # lyrics etc. travel with the song
                    side = Path(src).with_suffix(ext)
                    if side.is_file():
                        shutil.copy2(side, out.with_suffix(ext))
            elif it.action == "convert":
                r = subprocess.run(convert.command(src, out, o.fmt), capture_output=True, text=True)
                if r.returncode != 0:
                    out.unlink(missing_ok=True)
                    raise RuntimeError((r.stderr.strip().splitlines() or ["ffmpeg failed"])[-1])
                stats["converted"] += 1
            elif it.action == "download":
                _download(api, it.track, out); stats["downloaded"] += 1
        except Exception as e:  # noqa: BLE001 - reported at the end
            failed.add(it.rel)
            stats["errors"].append(f"{os.path.basename(it.rel)}: {e}")
        if progress:
            progress(f"Syncing {n + 1}/{len(todo)}")
    for rel in pl.extra:
        try:
            (dest / rel).unlink(); stats["deleted"] += 1; removed.add(rel)
            for ext in organizer.SIDECARS:
                (dest / rel).with_suffix(ext).unlink(missing_ok=True)
            d = (dest / rel).parent
            while d != dest and dest in d.parents:               # remove only the folders this deletion emptied
                try:
                    d.rmdir()
                except OSError:
                    break
                d = d.parent
        except OSError as e:
            stats["errors"].append(f"{rel}: {e}")
    if todo or pl.items or removed:                               # remember what Hivefi put here, for safe clean-ups
        write_manifest(dest, (read_manifest(dest) - removed) | (pl.wanted - failed))
    by_track = {id(i.track): i.rel for i in pl.items}
    for name, tracks in (playlists or {}).items():
        rels = [by_track[id(t)] for t in tracks if id(t) in by_track]
        if rels:
            safe = "".join(ch if ch.isalnum() or ch in " -_()" else "_" for ch in name).strip() or "playlist"
            write_playlist(dest / o.playlist_dir / f"{safe}.m3u8", rels, dest)
            stats["playlists"] += 1
    return stats
