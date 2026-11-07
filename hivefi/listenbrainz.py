"""ListenBrainz scrobbling (also works with compatible servers): listens are queued in the library database and sent
in the background, so plays made offline are submitted later."""
import time

import requests

DEFAULT_URL = "https://api.listenbrainz.org"
OP = "listenbrainz"


def _headers(token):
    return {"Authorization": f"Token {token}", "User-Agent": "Hivefi"}


def metadata(t: dict) -> dict:
    meta = {"artist_name": t.get("artist") or "", "track_name": t.get("title") or "",
            "additional_info": {"media_player": "Hivefi", "submission_client": "Hivefi",
                                "duration_ms": int((t.get("duration") or 0) * 1000)}}
    if t.get("album"):
        meta["release_name"] = t["album"]
    return meta


def listen_payload(entry: dict) -> dict:
    return {"listened_at": int(entry["listened_at"]), "track_metadata": entry["meta"]}


def validate(token: str, base=DEFAULT_URL, timeout=10) -> str:
    """User name of the token's owner; raises ValueError / requests errors otherwise."""
    r = requests.get(f"{base.rstrip('/')}/1/validate-token", headers=_headers(token), timeout=timeout)
    body = r.json()
    if not body.get("valid"):
        raise ValueError(body.get("message") or "the token is not valid")
    return body.get("user_name", "")


def now_playing(token: str, t: dict, base=DEFAULT_URL, timeout=10):
    requests.post(f"{base.rstrip('/')}/1/submit-listens", headers=_headers(token), timeout=timeout,
                  json={"listen_type": "playing_now", "payload": [{"track_metadata": metadata(t)}]})


def queue_listen(db, t: dict, when=None):
    """Remember a listen (a track counted as played) until it is sent."""
    if t.get("source") not in ("local", "remote") or not t.get("title") or not t.get("artist"):
        return False
    db.queue_op(OP, {"listened_at": int(when or time.time()), "meta": metadata(t)})
    return True


def flush(db, token: str, base=DEFAULT_URL, timeout=15) -> int:
    """Send queued listens (up to 100 per request). Stops on a network error or rate limit and keeps the rest;
    drops listens the server rejects as malformed. Returns how many were delivered."""
    sent = 0
    while True:
        batch = [(oid, p) for oid, op, p in db.pending_ops() if op == OP][:100]
        if not batch:
            return sent
        try:
            r = requests.post(f"{base.rstrip('/')}/1/submit-listens", headers=_headers(token), timeout=timeout,
                              json={"listen_type": "import" if len(batch) > 1 else "single",
                                    "payload": [listen_payload(p) for _oid, p in batch]})
        except requests.RequestException:
            return sent
        if r.status_code in (429, 500, 502, 503, 504, 401):      # try again later (401: bad token: keep the listens)
            return sent
        for oid, _p in batch:                                   # 200, or a 4xx that retrying cannot fix
            db.drop_op(oid)
        sent += len(batch) if r.status_code == 200 else 0
