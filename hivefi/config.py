"""Persistent settings (JSON in XDG config dir)."""
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

APP = "hivefi"
LEGACY_APP = "musicbee-linux"       # earlier name of this app: its folders are moved over once


def _dir(env, default, app=APP) -> Path:
    base = Path(os.environ.get(env, Path.home() / default))
    p, old = base / app, base / LEGACY_APP
    if not p.exists() and old.is_dir():
        try:
            old.rename(p)
        except OSError:
            pass
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    return _dir("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _dir("XDG_DATA_HOME", ".local/share")


def cache_dir() -> Path:
    return _dir("XDG_CACHE_HOME", ".cache")


@dataclass
class Config:
    library_dirs: list = field(default_factory=list)
    server_url: str = ""
    username: str = ""
    password: str = ""
    sync_on_start: bool = True
    sync_interval_min: int = 15          # 0 = manual only
    scrobble: bool = True
    max_bitrate: int = 0                 # 0 = original / no transcoding
    volume: int = 80
    # --- audio engine / audiophile settings (see ui/audio_dialog.py)
    engine: str = "auto"                 # auto (GStreamer if available) | qt
    output_device: str = ""              # "" = system default
    bit_perfect: bool = False            # bypass all DSP and software volume
    output_rate: int = 0                 # 0 = native
    output_depth: str = "auto"           # auto | 16 | 24 | 32 | float
    resample_quality: int = 6            # 0..10
    dither: str = "none"                 # none | rpdf | tpdf | tpdf-hf
    noise_shaping: str = "none"          # none | error-feedback | simple | medium | high
    buffer_ms: int = 0                   # 0 = device default
    seek_accurate: bool = True
    gapless: bool = True
    crossfade_ms: int = 0
    crossfade_curve: str = "equal-power"  # equal-power | linear
    crossfade_manual: bool = True        # also crossfade when you skip tracks
    crossfade_same_album: bool = False   # False: consecutive album tracks stay gapless
    fade_pause_ms: int = 0
    rg_mode: str = "track"               # off | track | album
    rg_preamp: float = 0.0
    rg_fallback: float = 0.0
    rg_headroom: float = 0.0
    volume_curve: str = "linear"         # linear | cubic | db
    limiter: bool = False
    limiter_threshold_db: float = -1.0
    balance: float = 0.0
    mono: bool = False
    swap_channels: bool = False
    crossfeed: str = "off"               # off | default | cmoy | jmeier
    eq_enabled: bool = False
    eq_bands: int = 10                   # 10 | 15 | 31
    eq_shelf_ends: bool = False
    eq_preset: str = "Flat"
    eq_gains: list = field(default_factory=lambda: [0.0] * 10)   # for the current eq_bands layout
    eq_preamp: float = 0.0
    eq_user_presets: dict = field(default_factory=dict)          # name -> {"bands": n, "gains": [...]}
    libraries_off: list = field(default_factory=list)   # "navidrome" and/or local folder paths that are switched off
    auto_dj: bool = False
    shuffle: bool = False
    repeat: str = "off"                  # off | all | one
    geometry: str = ""
    state: str = ""                      # dock layout (QMainWindow.saveState)
    header_state: str = ""               # track table columns (order/width/visibility)
    skin: str = "Dark"
    view_mode: str = "browser"           # browser | albums
    show_tray: bool = True
    close_to_tray: bool = False
    write_ratings: bool = False          # also store star ratings in local files' tags
    server_folders: list = field(default_factory=list)   # Subsonic music-folder ids to sync ([] = all libraries)
    server_folder_names: dict = field(default_factory=dict)   # id -> name of the server's libraries (last seen)
    seek_style: str = "slider"           # slider | bar | waveform | wave_line
    waveform_remote: bool = False        # also analyse streamed (server) tracks for the waveform: downloads the track
    vis_style: str = "bars"              # bars | mirror | line | radial | spectrogram | vu
    vis_enabled: bool = True             # put spectrum/level analysers in the audio chain (about 1% CPU)
    vis_active: bool = False             # runtime only: a visualizer panel is visible
    vis_bars: int = 48
    vis_colors: str = "skin"             # colour scheme name (see ui/visualizer.SCHEMES)
    vis_custom: list = field(default_factory=lambda: ["#7a1fd8", "#ff4fb8", "#ffe14a"])   # custom low/mid/peak
    vis_peaks: bool = True
    layouts: dict = field(default_factory=dict)       # name -> saved window layout (base64)
    browser_state: str = ""              # Genre | Artist | Album splitter
    resume_mode: str = "paused"          # off | paused | play: what to do with last session's queue on start
    tracks_detached: bool = False        # the track list was popped out into its own panel
    check_updates: bool = True           # look for a newer version on GitHub at startup
    github_token: str = ""               # optional; needed to update from a private repo (else `gh auth token`)

    NAVIDROME = "navidrome"

    def lib_enabled(self, key: str) -> bool:
        return key not in self.libraries_off

    def set_lib_enabled(self, key: str, on: bool):
        off = [k for k in self.libraries_off if k != key]
        self.libraries_off = off if on else off + [key]

    @staticmethod
    def server_lib_key(folder_id) -> str:
        """libraries_off key of one library (music folder) on the server."""
        return f"{Config.NAVIDROME}:{folder_id}"

    def enabled_tracks(self, tracks) -> list:
        """Tracks of the libraries that are switched on (a plain copy when nothing is switched off)."""
        if not self.libraries_off:
            return list(tracks)
        return [t for t in tracks if self.track_enabled(t)]

    @property
    def server_enabled(self) -> bool:
        return self.server_configured and self.lib_enabled(self.NAVIDROME)

    @property
    def disabled_dirs(self) -> list:
        return [d for d in self.library_dirs if not self.lib_enabled(d)]

    def track_enabled(self, t: dict) -> bool:
        """Is this track part of a library that is currently switched on?"""
        if t["source"] == "remote":
            if not self.lib_enabled(self.NAVIDROME):
                return False
            fid = t.get("folder_id")
            return fid is None or self.lib_enabled(self.server_lib_key(fid))
        if not self.libraries_off:
            return True
        p = t.get("path") or ""
        covering = [d for d in self.library_dirs if p.startswith(d.rstrip("/") + "/")]
        return not covering or any(self.lib_enabled(d) for d in covering)

    @property
    def server_configured(self) -> bool:
        return bool(self.server_url and self.username and self.password)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_dir() / "config.json"
        c = cls()
        try:
            data = json.loads(path.read_text())
            for k, v in data.items():
                if hasattr(c, k):
                    setattr(c, k, v)
            if "replaygain" in data and "rg_mode" not in data:      # legacy boolean
                c.rg_mode = "track" if data["replaygain"] else "off"
            c.eq_user_presets = {n: (v if isinstance(v, dict) else {"bands": 10, "gains": list(v)})
                                 for n, v in c.eq_user_presets.items()}
            c.vis_active = False                                     # runtime flag, never persisted as on
            if c.skin == "MusicBee Classic":                         # skin renamed with the app
                c.skin = "Classic"
            if len(c.eq_gains) != c.eq_bands:
                c.eq_gains = [0.0] * c.eq_bands
        except (OSError, ValueError):
            pass
        c._path = path
        return c

    def save(self):
        path = getattr(self, "_path", None) or config_dir() / "config.json"
        d = asdict(self)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, path)
