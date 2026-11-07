"""Self-update from GitHub: compare the local commit with the remote branch head, download, restart.

Works for a git checkout (git pull --ff-only) and for a plain copy (tarball extracted over the source tree,
version remembered in `.version`). Private repos need a token: config `github_token`, $GITHUB_TOKEN / $GH_TOKEN,
or the `gh` CLI's login.
"""
import base64
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import requests

REPO = "voidxzero/hivefi"
BRANCH = "main"
ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"


class UpdateError(Exception):
    pass


def find_token(cfg=None) -> str:
    tok = (getattr(cfg, "github_token", "") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "")
    if tok:
        return tok.strip()
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _headers(token: str, accept="application/vnd.github+json") -> dict:
    h = {"Accept": accept, "User-Agent": "hivefi-updater"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _git(args, root=ROOT, token="", timeout=60):
    cmd = ["git", "-C", str(root)]
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cmd += ["-c", f"http.https://github.com/.extraheader=Authorization: Basic {basic}"]
    r = subprocess.run(cmd + args, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    return r


def is_git(root=ROOT) -> bool:
    return (Path(root) / ".git").exists()


def local_version(root=ROOT) -> str | None:
    root = Path(root)
    if is_git(root):
        r = _git(["rev-parse", "HEAD"], root)
        return r.stdout.strip() or None if r.returncode == 0 else None
    f = root / ".version"
    return f.read_text().strip() if f.exists() else None


def remote_version(token="", repo=REPO, branch=BRANCH, api=API) -> str:
    try:
        r = requests.get(f"{api}/repos/{repo}/commits/{branch}", timeout=10,
                         headers=_headers(token, "application/vnd.github.sha"))
    except requests.RequestException as e:
        raise UpdateError(f"network error: {e}") from e
    if r.status_code in (401, 403, 404):
        raise UpdateError("cannot read the repository (private repo needs a GitHub token: run `gh auth login` "
                          "or set it in Settings → General)")
    if r.status_code != 200:
        raise UpdateError(f"GitHub returned HTTP {r.status_code}")
    return r.text.strip()


def is_newer(remote: str, root=ROOT) -> bool:
    """True when `remote` is not already part of the local history."""
    local = local_version(root)
    if local == remote:
        return False
    if is_git(root) and _git(["cat-file", "-e", remote + "^{commit}"], root).returncode == 0:
        # known commit: only newer if we are not already ahead of it
        return _git(["merge-base", "--is-ancestor", remote, "HEAD"], root).returncode != 0
    return True


def check(cfg=None, root=ROOT, **kw) -> str | None:
    """Return the new remote commit sha if an update is available, else None."""
    remote = remote_version(find_token(cfg), **kw)
    return remote if is_newer(remote, root) else None


def _copy_tree(src: Path, dst: Path):
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if "__pycache__" in rel.parts or p.name.endswith(".pyc"):
            continue
        t = dst / rel
        if p.is_dir():
            t.mkdir(parents=True, exist_ok=True)
        else:
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, t)


def apply(cfg=None, root=ROOT, remote=None, repo=REPO, branch=BRANCH, api=API) -> str:
    """Download and install the update in place; returns the new version."""
    root = Path(root)
    token = find_token(cfg)
    if is_git(root):
        r = _git(["pull", "--ff-only", "origin", branch], root, token, timeout=180)
        if r.returncode != 0:
            raise UpdateError("git pull failed (local changes?): " + (r.stderr or r.stdout).strip()[-300:])
        return local_version(root) or ""
    try:
        r = requests.get(f"{api}/repos/{repo}/tarball/{remote or branch}", headers=_headers(token), timeout=60)
    except requests.RequestException as e:
        raise UpdateError(f"download failed: {e}") from e
    if r.status_code != 200:
        raise UpdateError(f"download failed: HTTP {r.status_code}")
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(r.content)) as tf:
            tf.extractall(tmp, filter="data")
        tops = [d for d in Path(tmp).iterdir() if d.is_dir()]
        if len(tops) != 1:
            raise UpdateError("unexpected archive layout")
        _copy_tree(tops[0], root)
    sha = remote or remote_version(token, repo, branch, api)
    (root / ".version").write_text(sha + "\n")
    return sha
