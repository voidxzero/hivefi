"""Podcasts: subscribe to RSS feeds, list and play episodes (streamed or downloaded)."""
import email.utils
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
UA = {"User-Agent": "Hivefi (podcast client)"}
LABEL = "Podcast"


def _text(el, tag, default=""):
    child = el.find(tag)
    return (child.text or "").strip() if child is not None and child.text else default


def parse_duration(s: str) -> float:
    """'01:02:03', '12:34' or '754' -> seconds."""
    s = (s or "").strip()
    if not s:
        return 0.0
    try:
        parts = [float(p) for p in s.split(":")]
    except ValueError:
        return 0.0
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def parse_date(s: str) -> float:
    try:
        return email.utils.parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError, IndexError):
        return 0.0


def parse_feed(data: bytes) -> tuple[dict, list[dict]]:
    """(feed info, episodes newest first). Raises ValueError for something that is not a podcast feed."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(f"not a valid feed: {e}") from e
    ch = root.find("channel")
    if ch is None:
        raise ValueError("this is not an RSS podcast feed")
    image = ch.find(f"{ITUNES}image")
    info = {"title": _text(ch, "title") or "Untitled podcast", "link": _text(ch, "link"),
            "image": (image.get("href") if image is not None else "") or _text(ch.find("image") or ET.Element("x"), "url")}
    eps = []
    for item in ch.findall("item"):
        enc = item.find("enclosure")
        if enc is None or not enc.get("url"):
            continue                                        # a post without audio
        url = enc.get("url").strip()
        guid = _text(item, "guid") or url
        eps.append({"guid": guid, "title": _text(item, "title") or guid, "url": url,
                    "published": parse_date(_text(item, "pubDate")), "size": int(enc.get("length") or 0)
                    if str(enc.get("length") or "").isdigit() else 0,
                    "duration": parse_duration(_text(item, f"{ITUNES}duration")),
                    "description": re.sub(r"<[^>]+>", "", _text(item, "description"))[:2000]})
    eps.sort(key=lambda e: -e["published"])
    return info, eps


def fetch(url: str, timeout=20) -> bytes:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.content


def subscribe(db, url: str, getter=fetch) -> int:
    """Add a podcast (or return the existing one) and load its episodes."""
    url = url.strip()
    existing = next((p for p in db.podcasts() if p["feed_url"] == url), None)
    pid = existing["id"] if existing else None
    info, eps = parse_feed(getter(url))
    if pid is None:
        pid = db.add_podcast(info["title"], url, info["link"], info["image"])
    db.add_episodes(pid, eps)
    return pid


def refresh(db, pid: int, getter=fetch) -> list[dict]:
    """Fetch the feed again; returns the episodes that are new."""
    p = db.podcast(pid)
    info, eps = parse_feed(getter(p["feed_url"]))
    db.touch_podcast(pid, info["title"], info["link"], info["image"])
    return db.add_episodes(pid, eps)


def episode_track(ep: dict, podcast: dict) -> dict:
    """An episode as a track dict (negative ids >= 1,000,000 away from songs and radio stations)."""
    f = ep.get("file") if ep.get("file") and os.path.exists(ep["file"]) else ""
    return dict(id=-(1_000_000 + int(ep["id"])), key=f"podcast:{ep['id']}", source="podcast", remote_id=None,
                path=f or None, rpath=None, url=ep["url"], title=ep["title"], artist=podcast["title"],
                album_artist=podcast["title"], album=LABEL, genre=LABEL, year=0, track_no=0, disc_no=1,
                duration=ep.get("duration") or 0, bitrate=0, suffix=Path(f).suffix.lstrip(".") if f else "stream",
                size=ep.get("size") or 0, rating=0, starred=0, play_count=1 if ep.get("played") else 0, last_played=0,
                date_added=ep.get("published") or 0, mtime=0, cover_id=None, rg_gain=None, folder_id=None, album_id=None,
                aa_tag="", _aa=podcast["title"], _ak=f"podcast:{podcast['id']}", episode_id=ep["id"],
                downloaded=bool(f), description=ep.get("description", ""))


def episode_id(track: dict):
    return track.get("episode_id") if track.get("source") == "podcast" else None


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", s).strip(" .")[:100] or "episode"


def download(db, ep_id: int, folder: str, progress=None, timeout=60) -> str:
    """Download an episode into folder/<podcast>/ and remember where it is. Returns the file path."""
    ep = db.episode(ep_id)
    pod = db.podcast(ep["podcast_id"])
    r = requests.get(ep["url"], headers=UA, stream=True, timeout=timeout)
    r.raise_for_status()
    ct = r.headers.get("content-type", "").lower()
    if "html" in ct or "xml" in ct:
        raise RuntimeError("the link did not return an audio file")
    ext = os.path.splitext(ep["url"].split("?")[0])[1].lower() or ".mp3"
    out = Path(folder) / safe_name(pod["title"]) / (safe_name(ep["title"]) + ext)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    done, total = 0, int(r.headers.get("content-length") or 0)
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk); done += len(chunk)
            if progress and total:
                progress(f"Downloading {safe_name(ep['title'])}: {100 * done // total}%")
    os.replace(tmp, out)
    db.set_episode_file(ep_id, str(out))
    return str(out)
