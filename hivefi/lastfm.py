"""Last.fm: scrobbling, "now playing" and love/unlove. Needs the user's own API key and secret (free at
last.fm/api/account/create); the account is authorised once in the browser. Listens are queued in the library database
and sent in the background, so plays made offline are submitted later."""
import hashlib
import time

import requests

API = "https://ws.audioscrobbler.com/2.0/"
OP = "lastfm"
MIN_SECONDS = 30                                         # Last.fm ignores tracks shorter than this
UA = {"User-Agent": "Hivefi"}
RETRY_LATER = {8, 11, 16, 29}                            # operation failed / offline / temporarily unavailable / rate limit
KEEP_CODES = RETRY_LATER | {9}                           # 9: invalid session key: keep the listens until it is renewed


class LastFmError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def signature(params: dict, secret: str) -> str:
    """md5 of the sorted name+value pairs plus the secret ('format' and 'callback' are not signed)."""
    raw = "".join(k + str(v) for k, v in sorted(params.items()) if k not in ("format", "callback"))
    return hashlib.md5((raw + secret).encode("utf-8")).hexdigest()


def call(method, params, key, secret, session="", base=API, post=True, timeout=15) -> dict:
    p = {**params, "method": method, "api_key": key}
    if session:
        p["sk"] = session
    p["api_sig"] = signature(p, secret)
    p["format"] = "json"
    try:
        r = (requests.post(base, data=p, headers=UA, timeout=timeout) if post
             else requests.get(base, params=p, headers=UA, timeout=timeout))
        body = r.json()
    except requests.RequestException as e:
        raise LastFmError(11, f"cannot reach Last.fm: {e}") from e
    except ValueError as e:
        raise LastFmError(8, "Last.fm sent something that is not JSON") from e
    if "error" in body:
        raise LastFmError(int(body["error"]), body.get("message", "error"))
    return body


def get_token(key, secret, base=API) -> str:
    return call("auth.getToken", {}, key, secret, base=base, post=False)["token"]


def auth_url(key, token) -> str:
    return f"https://www.last.fm/api/auth/?api_key={key}&token={token}"


def get_session(token, key, secret, base=API) -> tuple[str, str]:
    """(session key, user name) once the user has allowed the app in the browser."""
    s = call("auth.getSession", {"token": token}, key, secret, base=base, post=False)["session"]
    return s["key"], s.get("name", "")


def _track_params(t: dict) -> dict:
    p = {"artist": t.get("artist") or "", "track": t.get("title") or ""}
    if t.get("album"):
        p["album"] = t["album"]
    if t.get("duration"):
        p["duration"] = int(t["duration"])
    return p


def now_playing(t, key, secret, session, base=API):
    call("track.updateNowPlaying", _track_params(t), key, secret, session, base)


def love(t, key, secret, session, on=True, base=API):
    call("track.love" if on else "track.unlove", {"artist": t.get("artist") or "", "track": t.get("title") or ""},
         key, secret, session, base)


def queue_listen(db, t: dict, when=None) -> bool:
    if t.get("source") not in ("local", "remote") or not t.get("title") or not t.get("artist"):
        return False
    if (t.get("duration") or 0) and t["duration"] < MIN_SECONDS:
        return False
    db.queue_op(OP, {**_track_params(t), "timestamp": int(when or time.time())})
    return True


def flush(db, key, secret, session, base=API, timeout=20) -> int:
    """Send queued scrobbles (50 per request). Stops (keeping the rest) when Last.fm is unreachable, busy or the session
    is no longer valid; drops what it rejects for good. Returns how many were delivered."""
    sent = 0
    while True:
        batch = [(oid, p) for oid, op, p in db.pending_ops() if op == OP][:50]
        if not batch:
            return sent
        params = {}
        for i, (_oid, p) in enumerate(batch):
            for k, v in p.items():
                params[f"{k}[{i}]"] = v
        try:
            call("track.scrobble", params, key, secret, session, base, timeout=timeout)
        except LastFmError as e:
            if e.code in KEEP_CODES:
                return sent
            for oid, _p in batch:                        # rejected for good (bad parameters ...): do not retry forever
                db.drop_op(oid)
            continue
        for oid, _p in batch:
            db.drop_op(oid)
        sent += len(batch)
