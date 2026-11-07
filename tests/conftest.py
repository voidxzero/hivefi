import os

import pytest

os.environ.setdefault("HIVEFI_NO_UPDATE_CHECK", "1")   # never hit GitHub from tests


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    """Tests must never read or write the real session, cache or config of whoever runs them."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
