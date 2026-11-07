"""Duplicate finder: same artist + title (normalised) with a similar length."""
import re
import unicodedata

LOSSLESS = {"flac", "wav", "alac", "ape", "wv", "aiff", "aif", "dsf", "tta"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)               # (Remastered), [Live] ...
    s = re.sub(r"\bfeat\.?.*$|\bft\.?.*$", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def quality(t: dict) -> tuple:
    """Higher is better: lossless, then bitrate, then local over remote, then size."""
    return ((t.get("suffix") or "").lower() in LOSSLESS, t.get("bitrate") or 0,
            t.get("source") == "local", t.get("size") or 0)


def find_duplicates(tracks, tolerance=3.0) -> list[list[dict]]:
    """Groups (size >= 2) of probable duplicates, best-quality copy first, groups sorted by artist/title."""
    buckets: dict[tuple, list[dict]] = {}
    for t in tracks:
        key = (norm(t.get("artist")), norm(t.get("title")))
        if key[1]:
            buckets.setdefault(key, []).append(t)
    groups = []
    for items in buckets.values():
        items = sorted(items, key=lambda t: t.get("duration") or 0)
        cluster = [items[0]]
        for t in items[1:] + [None]:
            if t is not None and abs((t.get("duration") or 0) - (cluster[-1].get("duration") or 0)) <= tolerance:
                cluster.append(t); continue
            if len(cluster) > 1:
                groups.append(sorted(cluster, key=quality, reverse=True))
            cluster = [t] if t is not None else []
    groups.sort(key=lambda g: (norm(g[0].get("artist")), norm(g[0].get("title"))))
    return groups
