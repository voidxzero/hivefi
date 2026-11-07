# Hivefi

A music manager and player for Linux (PyQt6) with two-way **Navidrome / Subsonic** sync.

Install (Arch, Fedora, Debian/Ubuntu, others):
```
./install.sh --deps   # installs system packages (Qt6, GStreamer, PyGObject) via pacman/dnf/apt
./install.sh          # venv (sees system packages) + desktop entry + ~/.local/bin/hivefi
./run.sh              # or run straight from the source tree
```
Arch packages: `python-pyqt6 python-mutagen python-requests python-gobject gst-python gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugins-ugly gst-libav qt6-multimedia-ffmpeg`.
A `packaging/arch/PKGBUILD` is included (`cd packaging/arch && makepkg -si`). Tests: `pytest`.

## Updates
On startup the app checks `github.com/voidxzero/hivefi` (branch `main`); if there is a newer commit it offers to download it and restarts itself. Also under Help → Check for Updates, and it can be turned off in Settings → General. A git checkout updates with `git pull --ff-only`; a plain copy gets the tarball extracted over it. The repo is private, so it authenticates with the `gh` CLI login, `$GITHUB_TOKEN`, or a token set in Settings → General.

## Also
- **Synced lyrics:** `.lrc` files, embedded LRC text and OpenSubsonic timed lyrics scroll and highlight in the Lyrics panel; double-click a line to seek.
- **Find Duplicates** (File menu): groups same artist + title (ignoring "(Remastered)", "feat." etc.) with a similar length, keeps the best copy (lossless > bitrate > local) unchecked, and removes the others from the library only; files are never deleted.
- **Ratings in file tags** (optional, Settings → General): ID3 POPM, FMPS_RATING for FLAC/Ogg/Opus/MP4; read back by the scanner.

## Tools menu
Work on the selected local tracks (or the whole local library when nothing is selected); also in the track right-click menu.
- **Organise Files:** move or copy files into a folder structure from a tag pattern (default `%albumartist%/%album%/%disc%%track% - %title%`), with a live preview. Moves update the library in place (ratings, playlists and play counts are kept), carry `.lrc`/`.txt`/`.cue` sidecars along, avoid name clashes and remove emptied folders.
- **Convert:** transcode with ffmpeg to FLAC, MP3 (V0 / 320 / 192), AAC, Opus, Vorbis or WAV into a separate folder; tags and cover art are carried over, originals are never touched.
- **Scan ReplayGain:** EBU R128 loudness measurement (reference -18 LUFS) that writes ReplayGain 2.0 track and album tags (ID3, Vorbis/FLAC/Opus, MP4) and updates the library.
- **Find Duplicates / Find Missing Files:** library clean-up (files are never deleted).
- **Save Queue as Playlist** (File menu and the Now Playing source), and **Make Editable Copy** of any built-in auto playlist (right-click it).

## Panels, seek bar, visualizer, session
- **Every control is a movable panel.** Controls, Seek Bar, Volume, Search and View Mode are toolbars you can drag (by their grip) to any edge of the window or float; Sources, Now Playing, Up Next, Lyrics, Track Info, Equalizer and Visualizer are docks (drag, float, tab, split). View → Panels toggles each one, **Lock Layout** freezes everything and hides the title bars, **View → Layouts** has built-in layouts (Default, Minimal, Now Playing focus, Everything) plus any layouts you save under a name.
- **Seek bar styles** (View → Seek Bar Style): Slider, Progress bar, **Waveform**, Waveform outline. Click anywhere to jump there; dragging scrubs. Waveforms are analysed with ffmpeg in the background and cached; streamed tracks are only analysed if you tick "Analyse streamed tracks too" (it downloads them).
- **Visualizer** (View → Panels → Visualizer; right-click for options): Spectrum bars, Mirrored bars, Spectrum line, Radial, Spectrogram, VU meters, driven by GStreamer `spectrum`/`level` analysers (log frequency axis, peak caps, attack/release smoothing). Unavailable in bit-perfect mode and with the Qt engine.
- **Starts where you left off:** window geometry, panel/toolbar positions and sizes, splitters, column layout, the selected source, search text, Genre/Artist/Album selection, scroll position, and the queue with the playing track and position (reopened paused by default; Preferences → General → On start can resume playback or skip it). Saved on exit and every 30 seconds.

## Features
- Classic library layout: source tree, Genre → Artist → Album column browser, sortable track table, now-playing art panel
- Local library scan (mp3/flac/ogg/opus/m4a/wav/…) via mutagen; incremental rescans; embedded/folder cover art
- Queue ("Now Playing"), shuffle, repeat off/all/one, 5-star ratings, stars, play counts, playlists
- Playback via Qt Multimedia (FFmpeg backend); media keys when focused
- Search box filters across title/artist/album/genre
- **MPRIS2**: media keys, KDE/GNOME media widgets and `playerctl` control the player and see track metadata
- **Tag editing** for local files (single or multi-select; blank = unchanged), Show in File Manager
- **ReplayGain** (local tags + server-provided), never boosts above 100% to avoid clipping; toggle in Playback menu
- **Auto-DJ**: when the queue runs out, keeps playing random tracks from the current view
- **Lyrics** (Ctrl+L): `.lrc`/`.txt` beside the file, embedded tags, or the server (OpenSubsonic / legacy API)
- **Playlists**: drag tracks from the table onto a playlist; import/export M3U/M3U8

## Panels, views & skins
- **Dockable panels** (View → Panels; drag, float, tab, close; layout is remembered, *Reset Layout* restores defaults):
  Sources, Now Playing (resizable cover art), **Up Next** (queue; double-click to jump, Delete to remove),
  **Lyrics**, **Track Info** (all metadata of the selection)
- **Two browsing modes**: *Columns* (Genre → Artist → Album) and *Albums* (cover-art grid, lazy-loaded, double-click plays)
- **Configurable columns**: right-click the table header to show/hide (Album Artist, Disc, Bitrate, Format, Added, Last Played…); drag to reorder; click stars in the Rating column to rate
- **Auto Playlists**: built-ins (Recently Added/Played, Most Played, Top Rated, Never Played, Starred) and your own rule-based ones (File → New Auto Playlist…: any/all rules, limit, order)
- **Mini player** (Ctrl+M), **tray icon** (optionally keep playing when the window is closed), **sleep timer**, full screen (F11)
- **Skins** (View → Skin): 31 built in. The originals (Dark, Light, Classic, Midnight, AMOLED, Nord, Solarized Dark), all ten **Spindle** skins (Modern Night, Luna Midnight, Olive Noir, Amp Classic, Aqua Deck, Ice Blue, Chrome Deck, Emerald Core, Classic 98 with its 3D bevels and Tahoma-style font, Vapor Dusk) and fourteen more (Dracula, Gruvbox Dark, Tokyo Night, Catppuccin Mocha, Rose Pine, One Dark, Monokai, Sunset, Cyberpunk, Forest, Slate Light, Solarized Light, Paper, High Contrast). Skins with gradient/border colours also restyle buttons, toolbars, panel titles, headers, scroll bars and tabs. **Import Skin…** takes a Hivefi skin or a skin file made for Spindle as it is.

### Writing a skin
Drop a `.json` file in `~/.config/hivefi/skins/` (View → Skin → Open Skins Folder, then Reload Skins).
All keys are optional: `name`, `base` (a built-in to inherit from), colours `window text base alt button highlight hl_text accent` (plus, for the full gradient look, `panel_top panel_bot border hilite dim btn_top btn_bot title_top title_bot lcd_bg lcd_fg lcd_dim accent_text good warn bad`; Spindle-format files with a `colors` block work too),
and `qss` (inline Qt stylesheet) or `qss_file` (path relative to the skin). See `data/skins/ocean.json`.

## Audio engine & audiophile settings
Playback runs on **GStreamer** when available (Qt Multimedia is the automatic fallback: it plays everything but has no
DSP, gapless or crossfade; a single track GStreamer can't decode is played by Qt and the next one returns to GStreamer).
Everything below is in **Playback → Audio Settings…** (Ctrl+Shift+A) and takes effect live.

**Output**
- Output device picker (PipeWire / Pulse / ALSA `hw:` devices), refreshable
- **Bit-perfect mode**: bypasses the equalizer, ReplayGain, balance, crossfade and software volume
- Output sample rate (44.1 → 192 kHz) and bit depth (16 / 24 / 32-int / 32-float), resampler quality 0–10
- Dither (RPDF / TPDF / TPDF-HF) and noise shaping (error-feedback … high), output buffer size
- Sample-accurate or fast seeking; live "signal path" readout of what leaves the DSP chain

**Transitions**
- **Gapless playback** (queued on the same pipeline, sample-accurate), **crossfade** 0–12 s with equal-power or linear curve,
  optional crossfade on manual skips, consecutive tracks of one album stay gapless unless you tick "same album",
  fade on pause/resume. Crossfade pre-rolls the next track on a second deck; if the output can't be opened twice
  (exclusive hardware devices) it announces that and stays gapless.

**Volume & ReplayGain**
- ReplayGain **off / track / album** read from the file tags (`rgvolume`), pre-amp, gain for untagged tracks, extra
  headroom (it never lets gain clip a track's own peak); volume curve linear / cubic / logarithmic; optional peak limiter

**Sound**
- Balance, mono downmix, left/right swap, **headphone crossfeed** (bs2b: default / Cmoy / Jan Meier)

**Equalizer** (Playback → Equalizer, Ctrl+E)
- **10-, 15- or 31-band** (octave, 2/3-octave, 1/3-octave ISO centres), ±12 dB in 0.5 dB steps, preamp with
  *Auto preamp* (offsets the largest boost), optional **shelving end bands**
- 13 built-in presets that re-interpolate for every layout; save/delete your own; switching layouts keeps your curve's shape
- Verified by measurement in the test-suite (a +12 dB band moves a tone of that frequency by >9 dB on all three
  layouts; far-away bands leave it alone)

## Turning libraries on and off
Each music folder and the Navidrome server is a library you can switch off without deleting anything:
**View → Libraries** (also right-click *Navidrome* / *This Computer* in the sources tree, or the checkboxes in Preferences).
"Local music only" hides Navidrome in one click. While a library is off its tracks vanish everywhere (All Music, search,
auto playlists, Auto-DJ, queue); Navidrome additionally stops syncing, streaming and scrobbling, and its synced playlists
hide. Switching back on re-syncs/rescans. Folders that are switched off or unmounted are never scanned, so their tracks
are not dropped from your library.

## Navidrome / Subsonic sync (File → Preferences → Navidrome)
- **Pull**: whole server library (shown as the "Navidrome" source, streamed on demand, optional bitrate cap), ratings, stars, play counts, playlists
- **Push**: scrobbles (+ "now playing"), stars, ratings, playlist create/edit/delete
- Playlists marked ☁ are synced; server playlists arrive automatically
- Changes made offline are queued in the DB and sent on the next sync
- Sync on start and on an interval, or manually (Ctrl+Shift+S)
- Local files are not uploaded (the Subsonic API has no upload); local playlists can sync their server tracks

Config: `~/.config/hivefi/config.json` (mode 0600, holds the server password), library DB: `~/.local/share/hivefi/`.

## Not done yet
DSD, room-correction convolution, CD ripping, internet radio/podcasts, a spectrum visualiser and MusicBrainz auto-tagging are not done yet.
