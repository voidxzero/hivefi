"""Library statistics: what is in the library and how it is listened to."""
import time
from collections import Counter
from html import escape


def fmt_duration(sec: float) -> str:
    sec = int(sec or 0)
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    return (f"{d}d " if d else "") + (f"{h}h " if h or d else "") + f"{m}m"


def fmt_size(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def compute(tracks, plays, now=None) -> dict:
    """tracks: library track dicts; plays: [(track_id, played_at)] from the play log."""
    now = now or time.time()
    by_id = {t["id"]: t for t in tracks}
    artist_of = lambda t: t.get("_aa") or t.get("album_artist") or t.get("artist") or "Unknown"
    s = dict(tracks=len(tracks), albums=len({t.get("_ak") or (artist_of(t), t.get("album")) for t in tracks}),
             artists=len({artist_of(t) for t in tracks}), genres=len({t["genre"] for t in tracks if t.get("genre")}),
             duration=sum(t.get("duration") or 0 for t in tracks), size=sum(t.get("size") or 0 for t in tracks))
    s["formats"] = Counter((t.get("suffix") or "?").upper() for t in tracks).most_common(8)
    s["decades"] = sorted(Counter(f"{t['year'] // 10 * 10}s" for t in tracks if t.get("year")).items())
    s["top_artists"] = Counter(artist_of(t) for t in tracks).most_common(10)
    pc = Counter()
    for t in tracks:
        pc[artist_of(t)] += t.get("play_count") or 0
    s["top_played_artists"] = [(a, n) for a, n in pc.most_common(10) if n]
    s["top_tracks"] = [(f'{t["artist"]} – {t["title"]}', t["play_count"]) for t in
                       sorted(tracks, key=lambda t: -(t.get("play_count") or 0))[:10] if t.get("play_count")]
    s["never_played"] = sum(1 for t in tracks if not t.get("play_count"))
    rated = [t["rating"] for t in tracks if t.get("rating")]
    s["rated"], s["avg_rating"] = len(rated), (sum(rated) / len(rated) if rated else 0)
    s["total_plays"] = sum(t.get("play_count") or 0 for t in tracks)
    for days in (7, 30):
        recent = [(i, ts) for i, ts in plays if ts >= now - days * 86400 and i in by_id]
        s[f"plays_{days}"] = len(recent)
        s[f"listened_{days}"] = sum(by_id[i].get("duration") or 0 for i, _ in recent)
    s["by_hour"] = Counter(time.localtime(ts).tm_hour for i, ts in plays if i in by_id)
    return s


def render_html(s: dict) -> str:
    def table(rows, head):
        if not rows:
            return "<p><i>nothing yet</i></p>"
        return "<table cellpadding='3'>" + f"<tr><th align='left'>{head[0]}</th><th align='right'>{head[1]}</th></tr>" + \
            "".join(f"<tr><td>{escape(str(a))}</td><td align='right'>{n}</td></tr>" for a, n in rows) + "</table>"
    hour = max(s["by_hour"], key=s["by_hour"].get) if s["by_hour"] else None
    return f"""
<h2>Library</h2>
<p><b>{s['tracks']:,}</b> tracks · <b>{s['albums']:,}</b> albums · <b>{s['artists']:,}</b> artists ·
<b>{s['genres']:,}</b> genres<br>Total time <b>{fmt_duration(s['duration'])}</b> · size <b>{fmt_size(s['size'])}</b></p>
<h3>Listening</h3>
<p><b>{s['total_plays']:,}</b> plays in total · <b>{s['never_played']:,}</b> tracks never played<br>
Last 7 days: <b>{s['plays_7']}</b> plays ({fmt_duration(s['listened_7'])}) · last 30 days: <b>{s['plays_30']}</b> plays
({fmt_duration(s['listened_30'])})
{f"<br>Most plays start around <b>{hour:02d}:00</b>" if hour is not None else ""}<br>
{s['rated']:,} rated tracks{f", average {s['avg_rating']:.1f} stars" if s['rated'] else ""}</p>
<h3>Most played tracks</h3>{table(s['top_tracks'], ('Track', 'Plays'))}
<h3>Most played artists</h3>{table(s['top_played_artists'], ('Artist', 'Plays'))}
<h3>Biggest artists</h3>{table(s['top_artists'], ('Artist', 'Tracks'))}
<h3>Formats</h3>{table(s['formats'], ('Format', 'Tracks'))}
<h3>Decades</h3>{table(s['decades'], ('Decade', 'Tracks'))}
"""
