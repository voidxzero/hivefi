"""Batch tag tools: title case, find & replace, and filling tags from file names."""
import os
import re

SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "nor", "of", "on", "or", "the", "to",
               "vs", "vs.", "via", "with"}
ROMAN = {"ii", "iii", "iv", "vi", "vii", "viii", "ix", "xi", "xii"}
KEEP_UPPER = re.compile(r"^(?:[A-Z]{2,4}|[IVXLC]+|\d+[a-z]*)$")          # DJ, MC, II, 2nd...
FIELDS = ("title", "artist", "album", "album_artist", "genre", "composer", "comment")


def title_case(s: str) -> str:
    """Capitalise the way titles are written: small words stay lower case except first and last; acronyms and
    Roman numerals keep their capitals; apostrophes do not start new words (don't, not Don'T)."""
    s = s.strip()
    if s.isupper():                                         # SHOUTING: treat as plain text
        s = s.lower()
    words = re.split(r"(\s+)", s)
    real = [i for i, w in enumerate(words) if w and not w.isspace()]
    out = []
    for i, w in enumerate(words):
        if not w or w.isspace():
            out.append(w); continue
        first, last = i == real[0], i == real[-1]
        bare = w.strip("()[]\"'.,!?:;").lower()
        if bare in ROMAN:
            out.append(w.upper()); continue
        if KEEP_UPPER.match(w.strip("()[]\"'.,!?:;")) and w.upper() == w and len(w.strip("()[]\"'.,!?:;")) > 1:
            out.append(w)
        elif bare in SMALL_WORDS and not first and not last:
            out.append(w.lower())
        else:
            out.append("-".join(_cap(p) for p in w.split("-")))
    return "".join(out)


def _cap(word: str) -> str:
    for i, ch in enumerate(word):                           # skip leading punctuation: "(live)" -> "(Live)"
        if ch.isalnum():
            return word[:i] + ch.upper() + word[i + 1:].lower()
    return word


def find_replace(s: str, find: str, repl: str, regex=False, case=False) -> str:
    if not find:
        return s
    flags = 0 if case else re.IGNORECASE
    return re.sub(find if regex else re.escape(find), repl if regex else repl.replace("\\", "\\\\"), s, flags=flags)


def tidy(s: str) -> str:
    """Trim, collapse runs of spaces, and drop track-number-looking prefixes are NOT touched (that's a different tool)."""
    return re.sub(r"\s+", " ", s).strip()


def parse_filename(path: str, pattern: str) -> dict:
    """'%track% - %artist% - %title%' applied to the file name (without extension); {} when it doesn't fit.
    Tokens: %title% %artist% %album% %albumartist% %track% %disc% %year% %genre%. A token matches as little as
    possible, so '-' separators split where you expect."""
    name = os.path.splitext(os.path.basename(path))[0]
    keys = {"title": "title", "artist": "artist", "album": "album", "albumartist": "album_artist", "track": "track_no",
            "disc": "disc_no", "year": "year", "genre": "genre"}
    order, rx = [], ""
    for part in re.split(r"(%\w+%)", pattern):
        m = re.fullmatch(r"%(\w+)%", part)
        if m and m.group(1) in keys:
            order.append(keys[m.group(1)])
            rx += r"(\d+)" if keys[m.group(1)] in ("track_no", "disc_no", "year") else "(.+?)"
        else:
            rx += re.escape(part)
    m = re.fullmatch(rx, name)
    if not m:
        return {}
    out = {}
    for k, v in zip(order, m.groups()):
        out[k] = int(v) if k in ("track_no", "disc_no", "year") else v.strip()
    return out


def apply_operation(t: dict, op: str, field: str = "title", **p) -> dict:
    """The tag changes (db field -> new value) an operation would make to one track; {} when nothing changes.
    op: 'title_case' | 'find_replace' | 'tidy' | 'from_filename' | 'upper' | 'lower'."""
    changes = {}
    if op == "from_filename":
        for k, v in parse_filename(t.get("path") or "", p.get("pattern", "%track% - %title%")).items():
            if v != t.get(k):
                changes[k] = v
        return changes
    old = t.get(field) or ""
    if field not in FIELDS or not isinstance(old, str):
        return {}
    new = {"title_case": lambda: title_case(old), "tidy": lambda: tidy(old), "upper": lambda: old.upper(),
           "lower": lambda: old.lower(),
           "find_replace": lambda: find_replace(old, p.get("find", ""), p.get("repl", ""), p.get("regex", False),
                                                p.get("case", False))}[op]()
    if new != old:
        changes[field] = new
    return changes


def plan(tracks, op: str, field: str = "title", **p) -> list:
    """[(track, changes)] for the local tracks an operation would touch."""
    out = []
    for t in tracks:
        if t.get("source") == "local" and t.get("path"):
            ch = apply_operation(t, op, field, **p)
            if ch:
                out.append((t, ch))
    return out


def apply_changes(db, changes, progress=None):
    """Write the planned changes into the files and the library. Returns (done, errors)."""
    from . import tagedit
    done, errors = 0, []
    for i, (t, ch) in enumerate(changes):
        try:
            tagedit.write_tags(t["path"], ch)
            dbv = dict(ch)
            if "album_artist" in ch:
                dbv["aa_tag"] = ch["album_artist"]
            dbv["mtime"] = os.path.getmtime(t["path"])
            db.conn.execute(f"UPDATE tracks SET {', '.join(f'{k}=?' for k in dbv)} WHERE id=?", (*dbv.values(), t["id"]))
            done += 1
        except Exception as e:  # noqa: BLE001 - reported to the user
            errors.append(f"{os.path.basename(t['path'])}: {e}")
        if progress:
            progress(f"Tagging {i + 1}/{len(changes)}")
    db.commit()
    return done, errors
