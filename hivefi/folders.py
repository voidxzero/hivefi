"""Folder view of the library: the directory structure of local files and of the server's music folders.

Built from the tracks' own paths (the Subsonic `path` of a server track, the file path of a local one), so no extra
requests are needed: the tree always matches what is in the library."""
import os


def split_dirs(path: str) -> tuple:
    parts = [p for p in str(path or "").replace("\\", "/").split("/") if p]
    return tuple(parts[:-1])                              # the last part is the file name


class FolderIndex:
    """Folders with their (recursive) track counts. A folder is identified by (root, prefix): the library it belongs
    to ("" when there is only one) and its path below that library, e.g. ("2", "Artist/Album")."""

    def __init__(self, entries):
        """entries: iterable of (track, root, dirs tuple)."""
        self._direct: dict[tuple, list] = {}              # (root, prefix) -> tracks directly in that folder
        self._kids: dict[tuple, dict] = {}                # (root, prefix) -> {child name: tracks below it}
        self._count: dict[str, int] = {}                  # root -> tracks
        for t, root, dirs in entries:
            self._count[root] = self._count.get(root, 0) + 1
            prefix = ""
            for d in dirs:
                kids = self._kids.setdefault((root, prefix), {})
                kids[d] = kids.get(d, 0) + 1
                prefix = f"{prefix}/{d}" if prefix else d
            self._direct.setdefault((root, prefix), []).append(t)

    def roots(self) -> list:
        """[(root, track count)] sorted by root."""
        return sorted(self._count.items())

    def children(self, root, prefix="") -> list:
        """[(name, child prefix, track count)] of a folder, sorted by name."""
        kids = self._kids.get((root, prefix), {})
        return [(n, f"{prefix}/{n}" if prefix else n, c) for n, c in sorted(kids.items(), key=lambda kv: kv[0].lower())]

    def tracks(self, root, prefix="") -> list:
        """Every track in the folder and below it, in path order. root '*' = all libraries."""
        out = []
        for (r, p), ts in self._direct.items():
            if (root == "*" or r == root) and (not prefix or p == prefix or p.startswith(prefix + "/")):
                out.extend(ts)
        out.sort(key=lambda t: ((t.get("rpath") or t.get("path") or "").lower(), t.get("disc_no") or 0,
                                t.get("track_no") or 0))
        return out


def remote_entries(tracks):
    for t in tracks:
        yield t, str(t.get("folder_id") or ""), split_dirs(t.get("rpath"))


def local_entries(tracks, library_dirs):
    roots = sorted((d.rstrip("/") for d in library_dirs), key=len, reverse=True)
    for t in tracks:
        p = t.get("path") or ""
        root = next((r for r in roots if p.startswith(r + "/")), os.path.dirname(p))
        yield t, root, split_dirs(p[len(root) + 1:] if p.startswith(root + "/") else p)
