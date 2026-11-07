import io
import subprocess
import tarfile

from hivefi import updater


def git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True,
                   capture_output=True)


def make_remote(tmp_path):
    work = tmp_path / "work"; work.mkdir()
    git(work, "init", "-b", "main")
    (work / "a.txt").write_text("1")
    git(work, "add", "."); git(work, "commit", "-m", "one")
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)], check=True, capture_output=True)
    local = tmp_path / "local"
    subprocess.run(["git", "clone", str(bare), str(local)], check=True, capture_output=True)
    return work, bare, local


def test_git_checkout_update(tmp_path):
    work, bare, local = make_remote(tmp_path)
    old = updater.local_version(local)
    assert not updater.is_newer(old, local)
    (work / "a.txt").write_text("2"); git(work, "commit", "-am", "two"); git(work, "push", str(bare), "main")
    new = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert updater.is_newer(new, local)
    assert updater.apply(root=local) == new
    assert (local / "a.txt").read_text() == "2"
    assert not updater.is_newer(new, local)
    assert not updater.is_newer(old, local)            # being ahead of an older remote is not an update


def test_tarball_update(tmp_path, monkeypatch):
    root = tmp_path / "app"; root.mkdir()
    (root / "old.txt").write_text("keep")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"hello"
        ti = tarfile.TarInfo("repo-abc/pkg/new.py"); ti.size = len(data); tf.addfile(ti, io.BytesIO(data))

    class R:
        status_code = 200; content = buf.getvalue(); text = "abc123"
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: R)
    monkeypatch.setattr(updater, "find_token", lambda cfg=None: "")
    assert updater.local_version(root) is None and updater.is_newer("abc123", root)
    assert updater.apply(root=root, remote="abc123") == "abc123"
    assert (root / "pkg/new.py").read_text() == "hello" and (root / "old.txt").exists()
    assert updater.local_version(root) == "abc123" and not updater.is_newer("abc123", root)


def test_remote_errors(monkeypatch):
    class R:
        status_code = 404; text = ""
    monkeypatch.setattr(updater.requests, "get", lambda *a, **k: R)
    try:
        updater.remote_version("")
        assert False
    except updater.UpdateError as e:
        assert "token" in str(e)
