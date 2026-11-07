"""What the app was doing when it closed (queue, position, view), so the next start picks up right there."""
import json
import os

from .config import data_dir


def _path():
    return data_dir() / "session.json"


def save(state: dict):
    p = _path(); tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, p)


def load() -> dict:
    try:
        d = json.loads(_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}
