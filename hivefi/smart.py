"""Auto (smart) playlists: rule evaluation over library track dicts."""
import time

FIELDS = {  # key: (label, kind)
    "title": ("Title", "text"), "artist": ("Artist", "text"), "album": ("Album", "text"),
    "album_artist": ("Album Artist", "text"), "genre": ("Genre", "text"), "suffix": ("Format", "text"),
    "source": ("Source (local/remote)", "text"),
    "year": ("Year", "num"), "rating": ("Rating (0-5)", "num"), "play_count": ("Play Count", "num"),
    "duration": ("Length (seconds)", "num"), "bitrate": ("Bitrate", "num"),
    "track_no": ("Track #", "num"), "disc_no": ("Disc #", "num"),
    "date_added": ("Date Added", "date"), "last_played": ("Last Played", "date"),
    "starred": ("Starred", "bool"),
}
OPS = {
    "text": ["contains", "does not contain", "is", "is not", "starts with"],
    "num": ["=", "!=", ">", "<", ">=", "<="],
    "date": ["in the last (days)", "not in the last (days)", "is never"],
    "bool": ["is true", "is false"],
}
ORDER_FIELDS = ["title", "artist", "album", "year", "rating", "play_count", "date_added", "last_played", "duration"]

PRESETS = {
    "Recently Added": {"match": "all", "rules": [{"field": "date_added", "op": "in the last (days)", "value": "30"}],
                       "limit": 200, "order": "date_added", "desc": True},
    "Recently Played": {"match": "all", "rules": [{"field": "last_played", "op": "in the last (days)", "value": "30"}],
                        "limit": 200, "order": "last_played", "desc": True},
    "Most Played": {"match": "all", "rules": [{"field": "play_count", "op": ">", "value": "0"}],
                    "limit": 100, "order": "play_count", "desc": True},
    "Top Rated": {"match": "all", "rules": [{"field": "rating", "op": ">=", "value": "4"}],
                  "limit": 0, "order": "rating", "desc": True},
    "Never Played": {"match": "all", "rules": [{"field": "play_count", "op": "=", "value": "0"}],
                     "limit": 0, "order": "artist", "desc": False},
    "Starred": {"match": "all", "rules": [{"field": "starred", "op": "is true", "value": ""}],
                "limit": 0, "order": "artist", "desc": False},
}


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def match_rule(t: dict, r: dict, now: float) -> bool:
    field, op, val = r["field"], r["op"], r.get("value", "")
    kind = FIELDS[field][1]
    v = t.get(field)
    if kind == "text":
        a, b = str(v or "").lower(), str(val).lower()
        return {"contains": b in a, "does not contain": b not in a, "is": a == b, "is not": a != b,
                "starts with": a.startswith(b)}[op]
    if kind == "num":
        a, b = _num(v), _num(val)
        return {"=": a == b, "!=": a != b, ">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]
    if kind == "date":
        v = _num(v)
        if op == "is never":
            return v == 0
        recent = v > 0 and v >= now - _num(val) * 86400
        return recent if op.startswith("in") else not recent
    return bool(v) == (op == "is true")


def evaluate(tracks, spec: dict, now: float | None = None) -> list:
    now = now or time.time()
    rules = spec.get("rules", [])
    fn = all if spec.get("match", "all") == "all" else any
    out = [t for t in tracks if (not rules) or fn(match_rule(t, r, now) for r in rules)]
    order = spec.get("order") or "title"
    if order in FIELDS:
        out.sort(key=lambda t: (str(t.get(order) or "").lower() if FIELDS[order][1] == "text"
                                else _num(t.get(order))), reverse=bool(spec.get("desc")))
    limit = int(spec.get("limit") or 0)
    return out[:limit] if limit > 0 else out
