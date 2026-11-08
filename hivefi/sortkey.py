"""Sort names the way people file them: 'The Beatles' under B. The words to ignore are a preference."""
import re

_articles: tuple = ("the",)
_LEAD = re.compile(r"^[\s\"'(\[¡¿.]+")


def set_articles(words) -> None:
    global _articles
    _articles = tuple(w.strip().lower() for w in words if w and w.strip())


def articles() -> tuple:
    return _articles


def sort_name(s) -> str:
    """Lower-case sort key without a leading article ('The Wall' -> 'wall', but 'Thelonious' stays) or quote marks."""
    s = _LEAD.sub("", str(s or "").casefold()).strip()
    for a in _articles:
        if s.startswith(a + " ") and len(s) > len(a) + 1:
            return s[len(a) + 1:].lstrip()
    return s
