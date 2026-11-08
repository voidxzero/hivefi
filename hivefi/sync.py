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
        album_id=str(s["albumId"]) if s.get("albumId") else None, rpath=s.get("path") or None,
        composer=s.get("displayComposer") or "", comment=s.get("comment") or "", bpm=int(s.get("bpm") or 0),
        aa_tag=s.get("albumArtist") or ((s.get("albumArtists") or [{}])[0].get("name") or ""),
    )


def flush_ops(db, api: Subsonic) -> int:
    """Send queued offline operations. Stops on network failure, drops ops the server rejects."""
    done = 0
    for oid, op, p in db.pending_ops():
        if op not in ("scrobble", "star", "rating", "delete_playlist"):
            continue                                            # other queues (ListenBrainz) have their own sender
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


def pull_library(db, api: Subsonic, progress=None, folders=None) -> tuple[int, int, int]:
    """Fetch the server library. `folders` = Subsonic music-folder ids to pull (None/[] = everything).
    Returns (songs seen, removed, changed): `changed` counts rows actually written, so callers can skip a refresh."""
    db.begin_pass()
    n = changed = 0
    try:
        for fid, s in api.iter_songs_in(folders):
            changed += db.upsert_track("sub:" + s["id"], "remote", remote_id=s["id"], folder_id=fid,
                                       **song_to_fields(s))
            n += 1
            if n % 500 == 0:
                db.conn.commit()
                if progress:
                    progress(f"Fetched {n} songs…")
    except SubsonicError:
        db.abort_pass()                      # don't wipe the library when the server fails half-way
        db.commit()
        raise
    removed = db.end_pass("remote")          # anything not re-seen is gone (or belongs to a library no longer chosen)
    db.commit()
    db.all_tracks()                          # rebuild the track cache here, in the worker, not on the UI thread
    return n, removed, changed


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
            if [t["id"] for t in db.playlist_tracks(pid)] == ids:       # unchanged: don't rewrite it
                continue
        else:
            pid = db.create_playlist(rp["name"], remote_id=rid, sync=1)
        db.set_playlist_tracks(pid, ids, mark_dirty=False)
    # 3. playlists deleted on the server
    for rid, lp in local.items():
        if rid not in remote and not lp["dirty"]:
            db.conn.execute("DELETE FROM playlists WHERE id=?", (lp["id"],))
    db.commit()


def sync_radio(db, api: Subsonic) -> int:
    """Stations kept on the server are added to the local list (local stations are left alone)."""
    n = 0
    for s in api.radio_stations():
        db.add_radio(s["name"] or s["url"], s["url"], homepage=s["homepage"], remote_id=s["id"])
        n += 1
    return n


def full_sync(db, api: Subsonic, progress=None, folders=None) -> dict:
    say = progress or (lambda m: None)
    say("Connecting…")
    api.ping()
    say("Sending pending changes…")
    sent = flush_ops(db, api)
    names = {f["id"]: f["name"] for f in api.music_folders()}
    say("Fetching library…")
    added, removed, changed = pull_library(db, api, say, folders)
    say("Syncing playlists…")
    sync_playlists(db, api)
    sync_radio(db, api)
    db.set_meta("last_sync", time.time())
    return {"songs": added, "removed": removed, "sent_ops": sent, "changed": changed,
            "folder_names": names}
