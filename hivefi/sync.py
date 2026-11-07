"""Two-way sync with a Navidrome/Subsonic server.

Pull : songs (incl. rating/starred/play count), playlists
Push : queued ops (scrobbles, stars, ratings, playlist deletes) and dirty synced playlists
Local files are never uploaded (the Subsonic API has no upload endpoint).
"""
import time

from .subsonic import Subsonic, SubsonicError


def song_to_fields(s):
    return dict(
        title=s.get("title", ""), artist=s.get("artist", "") or "Unknown Artist",
        album_artist=s.get("albumArtist") or (s.get("artists") or [{}])[0].get("name") or s.get("artist", "") or "Unknown Artist",
        album=s.get("album", "") or "Unknown Album", genre=s.get("genre", ""),
        year=int(s.get("year") or 0), track_no=int(s.get("track") or 0),
        disc_no=int(s.get("discNumber") or 1), duration=float(s.get("duration") or 0),
        bitrate=int(s.get("bitRate") or 0), suffix=s.get("suffix", ""), size=int(s.get("size") or 0),
        rating=int(s.get("userRating") or 0), starred=1 if s.get("starred") else 0,
        play_count=int(s.get("playCount") or 0), rg_gain=(s.get("replayGain") or {}).get("trackGain"), cover_id=s.get("coverArt") or s.get("albumId"),
    )


def flush_ops(db, api: Subsonic) -> int:
    """Send queued offline operations. Stops on network failure, drops ops the server rejects."""
    done = 0
    for oid, op, p in db.pending_ops():
        try:
            if op == "scrobble":
                api.scrobble(p["id"], p.get("time_ms"), True)
            elif op == "star":
                api.star(p["id"]) if p["on"] else api.unstar(p["id"])
            elif op == "rating":
                api.set_rating(p["id"], p["rating"])
            elif op == "delete_playlist":
                api.delete_playlist(p["remote_id"])
        except SubsonicError as e:
            if e.code == 0:      # transport error -> try again later
                break
        db.drop_op(oid)
        done += 1
    return done


def pull_library(db, api: Subsonic, progress=None) -> tuple[int, int]:
    db.mark_unseen("remote")
    n = 0
    try:
        for s in api.iter_songs():
            db.upsert_track("sub:" + s["id"], "remote", remote_id=s["id"], **song_to_fields(s))
            n += 1
            if n % 500 == 0:
                db.commit()
                if progress:
                    progress(f"Fetched {n} songs…")
    except SubsonicError:
        db.conn.execute("UPDATE tracks SET seen=1 WHERE source='remote'")  # don't wipe on failure
        db.commit()
        raise
    removed = db.delete_unseen("remote")
    db.commit()
    return n, removed


def sync_playlists(db, api: Subsonic):
    # 1. push dirty synced playlists
    for pl in db.playlists():
        if not (pl["sync"] and pl["dirty"]):
            continue
        ids = [t["remote_id"] for t in db.playlist_tracks(pl["id"]) if t["remote_id"]]
        if pl["remote_id"]:
            api.replace_playlist(pl["remote_id"], pl["name"], ids)
        else:
            created = api.create_playlist(pl["name"], ids)
            db.conn.execute("UPDATE playlists SET remote_id=? WHERE id=?", (created.get("id"), pl["id"]))
        db.conn.execute("UPDATE playlists SET dirty=0 WHERE id=?", (pl["id"],))
        db.commit()
    # 2. pull server playlists
    remote = {p["id"]: p for p in api.playlists()}
    local = {p["remote_id"]: p for p in db.playlists() if p["remote_id"]}
    for rid, rp in remote.items():
        lp = local.get(rid)
        if lp and lp["dirty"]:
            continue
        full = api.playlist(rid)
        ids = []
        for s in full.get("entry", []) or []:
            t = db.track_by_remote(s["id"])
            if t:
                ids.append(t["id"])
        if lp:
            pid = lp["id"]
            db.conn.execute("UPDATE playlists SET name=? WHERE id=?", (rp["name"], pid))
        else:
            pid = db.create_playlist(rp["name"], remote_id=rid, sync=1)
        db.set_playlist_tracks(pid, ids, mark_dirty=False)
    # 3. playlists deleted on the server
    for rid, lp in local.items():
        if rid not in remote and not lp["dirty"]:
            db.conn.execute("DELETE FROM playlists WHERE id=?", (lp["id"],))
    db.commit()


def full_sync(db, api: Subsonic, progress=None) -> dict:
    say = progress or (lambda m: None)
    say("Connecting…")
    api.ping()
    say("Sending pending changes…")
    sent = flush_ops(db, api)
    say("Fetching library…")
    added, removed = pull_library(db, api, say)
    say("Syncing playlists…")
    sync_playlists(db, api)
    db.set_meta("last_sync", time.time())
    return {"songs": added, "removed": removed, "sent_ops": sent}
