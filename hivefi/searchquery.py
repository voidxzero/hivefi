"""Search box syntax: plain words plus `field:value` filters.

    beatles abbey            every word must appear in title / artist / album / genre
    "dark side"              a phrase
    artist:pink album:wall   fields: title artist album albumartist genre year rating plays format source folder
    year:1990-1999  year:>=2000  rating:>=4  plays:0   numbers: = > < >= <= and ranges
    -live  -genre:rock       a minus sign excludes
"""
import re

TEXT_FIELDS = {"title": "title", "artist": "artist", "album": "album", "albumartist": "album_artist",
               "genre": "genre", "format": "suffix", "source": "source"}
NUM_FIELDS = {"year": "year", "rating": "rating", "plays": "play_count", "bitrate": "bitrate", "track": "track_no",
              "disc": "disc_no"}
_TOKEN = re.compile(r'(-?)(?:(\w+):)?(?:"([^"]*)"|(\S+))')


class Query:
    def __init__(self, text: str):
        self.words: list[str] = []          # plain words, all of which must match (lower case)
        self.not_words: list[str] = []
        self.rules: list[tuple] = []        # (negated, kind, field, value)
        for neg, field, quoted, bare in _TOKEN.findall(text.lower()):
            value = quoted if quoted else bare
            if value == "-" and not field:                            # a lone minus sign typed so far
                continue
            if field and (field in TEXT_FIELDS or field in NUM_FIELDS or field in ("folder", "path")) and value != "":
                self.rules.append((bool(neg), field, value))
            else:                                                     # "foo:" typed so far, or an unknown field: plain text
                word = f"{field}:{value}" if field else value
                if word:
                    (self.not_words if neg else self.words).append(word)

    @property
    def is_plain(self) -> bool:
        """Only positive plain words: results of a longer text are always a subset (incremental narrowing)."""
        return not self.rules and not self.not_words

    def __bool__(self):
        return bool(self.words or self.not_words or self.rules)

    def match(self, t: dict, hay: str) -> bool:
        for w in self.words:
            if w not in hay:
                return False
        for w in self.not_words:
            if w in hay:
                return False
        for neg, field, value in self.rules:
            if _rule(t, field, value) == neg:
                return False
        return True


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _rule(t, field, value) -> bool:
    if field in TEXT_FIELDS:
        return value in str(t.get(TEXT_FIELDS[field]) or "").lower()
    if field in ("folder", "path"):
        return value in str(t.get("path") or t.get("rpath") or "").lower()
    have = _num(t.get(NUM_FIELDS[field]))
    m = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", value)
    if m:
        return float(m.group(1)) <= have <= float(m.group(2))
    m = re.fullmatch(r"(>=|<=|>|<|=)?\s*(-?\d+(?:\.\d+)?)", value)
    if not m:
        return False
    op, want = m.group(1) or "=", float(m.group(2))
    return {">=": have >= want, "<=": have <= want, ">": have > want, "<": have < want, "=": have == want}[op]
