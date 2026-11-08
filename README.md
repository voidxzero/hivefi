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
- **Every control is a movable panel (in Edit Mode).** Controls, Seek Bar, Volume, Search and View Mode are toolbars you can drag (by their grip) to any edge of the window or float; Sources, Library Browser, Now Playing, Up Next, Lyrics, Track Info, Equalizer and Visualizer are docks (drag, float, tab, split). The **track list** and the **library browser** are panels like all the others, so they can be moved, tabbed, floated, resized or closed in Edit Mode. View → Panels toggles each one. The layout is **locked by default**: title bars are hidden and panels and toolbars cannot be moved, floated or closed, but you can always resize panels by dragging the gaps between them (and the splitters in the browser). Turn on **Edit Mode** (View → Panels → Edit Mode, Ctrl+Shift+E, or the "✎ Edit layout" button in the status bar) to drag, float, tab and close panels. While it is on, editable panels and toolbars get a dashed accent frame and an editing bar appears with **Panels ▾** (show/hide any panel or toolbar), **Save layout…**, **Reset**, **Revert** (undo everything since Edit Mode was turned on) and **Done**; Esc also locks the layout. Right-click a panel's title bar for Float / Move to Left, Right, Top, Bottom / Stack as a tab with… / Close. **Preferences → Panels** is a panel editor in the style of MusicBee's: a table of every panel and toolbar with Show, Location (Left / Right / Top / Bottom / Floating) and "stacked as a tab with" choices, an Apply now button, a switch for Edit Mode, and the layout list (load, save as…, delete, reset). **View → Layouts** has built-in layouts (Default, Minimal, Now Playing focus, Everything) plus any layouts you save under a name.
- **Seek bar styles** (View → Seek Bar Style): Slider, Progress bar, **Waveform**, Waveform outline. Click anywhere to jump there; dragging scrubs. Waveforms are analysed with ffmpeg in the background and cached; streamed tracks are only analysed if you tick "Analyse streamed tracks too" (it downloads them).
- **Visualizer** (View → Panels → Visualizer; right-click for options): Spectrum bars, Mirrored bars, Spectrum line, Radial, Spectrogram, VU meters, driven by GStreamer `spectrum`/`level` analysers (log frequency axis, peak caps, attack/release smoothing). Right-click → **Colors** (or View → Visualizer Style → Colors) picks the palette: Skin accent, Fire, Ocean, Ice, Neon, Sunset, Forest, Monochrome, Rainbow, or **Custom colours…** (low / middle / peak). Unavailable in bit-perfect mode and with the Qt engine.
- **Starts where you left off:** window geometry, panel/toolbar positions and sizes, splitters, column layout, the selected source, search text, Genre/Artist/Album selection, scroll position, and the queue with the playing track and position (reopened paused by default; Preferences → General → On start can resume playback or skip it). Saved on exit and every 30 seconds.

## Now Playing panel
Right-click the panel (or View → Now Playing Options…) to choose what it shows and in which order: title, artist, album, album artist, year, genre, composer, track / disc, **file type, sampling rate, bit depth, channels, bitrate** (or all of them as one *format summary*), the **output format after the DSP chain**, length, file size, rating, plays, skips, BPM, ReplayGain, dates, source, location and comment, optionally with the name of each field in front; a live preview shows the result. The sampling rate, bit depth and channels come from the files (and from Navidrome with OpenSubsonic), are also available as optional track-list columns and in searches (`samplerate:>=96000 bits:24 channels:2`). **Everything is a link:** click the cover or the album to go to that album in the library, the artist for that artist's music, the genre, the year or the composer for everything with it, and the title to find the song in the list (in the Albums grid the album is selected there). The first start after updating re-reads local tags once to learn the audio properties.

## Fullscreen visualizer
**View → Visualizer Style → Fullscreen Visualizer** (F8, or double-click the visualizer panel) fills the screen with one of 15 effects:
- **MilkDrop-style feedback effects** (need numpy): Tunnel Vision, Spiral Galaxy, Liquid Gold, Neon Rain, Kaleido Pulse, Deep Sea, Fireworks and Echo Chamber. Every frame the previous picture is zoomed, rotated, rippled, faded and colour-shifted (like MilkDrop's warp mesh) and new shapes driven by the music's bass, mids, treble and beats are drawn on top, so the trails melt into each other. These are original effects built on that idea, not MilkDrop preset files (projectM's presets cannot be loaded).
- **Classic media-player style effects:** Bars (with falling peak caps), Scope, Ambience, Battery, Particles, Spikes and Starfield.
- **Controls:** ← / → change effect, Space toggles automatic cycling, ↑ / ↓ change the cycle time, O shows or hides the title overlay, Esc or double-click leaves (the cursor hides itself). View → Visualizer Style → Fullscreen Effect picks the effect (or Auto), how often it changes, and the quality (320 / 480 / 720 px internal resolution for the feedback effects).
- Choosing another effect always starts from a clean screen (no trails, particles or rings of the previous one), and an effect that ever fails is reported in the status bar and skipped instead of closing the program.
- The analysers deliver the spectrum and levels, not raw samples, so the "Scope" trace is reconstructed from the spectrum rather than being the true waveform. Needs the GStreamer engine with the analysers on (not bit-perfect mode).

## More tools
- **Internet radio:** a **Radio** node in the sources panel. Right-click it to add stations (name, stream URL, genre, website); stations kept on your Navidrome server are added at every sync. Streams play like tracks (no length, shown as "live"), the stream's announced title ("Artist - Song") shows in Now Playing and the window title, and radio is never part of All Music, searches, Auto-DJ or scrobbling.
- **ListenBrainz scrobbling** (Preferences → General): paste your user token (Test checks it). Plays are queued in the library database and sent in the background in batches, so plays made offline are submitted later; "now playing" is sent too. Works with compatible servers via the URL field.
- **Hotkeys** (Preferences → Hotkeys): every menu command with its shortcut; click one, press the keys you want, clear or reset it; conflicts are flagged. Only your changes are stored.
- **Batch Tag Tools** (Tools menu, and the track right-click menu): Title Case (SHOUTING input is normalised, small words stay lower case), Find and replace (plain or regular expression), trim spaces, UPPER / lower case, and **fill tags from the file name** with a pattern like `%track% - %title%`; a before/after table shows every change before anything is written.
- **Cover art:** right-click local tracks → Cover Art → Set Cover from Image… / Remove Embedded Cover (ID3, FLAC, Ogg/Opus, MP4).
- **Export** (Tools → Export Library / Selection…): the selection or the current view as CSV (opens in Excel with accents intact), JSON or an M3U8 playlist.

## Device sync, podcasts, inbox
- **Sync to Device** (Tools menu): send the selection, what is shown, all music or a playlist to any folder (a phone, a player, a USB stick). Files already there and up to date are skipped; you can keep the files as they are or convert them (only lossless files, or everything) to FLAC / MP3 / AAC / Opus / Vorbis; lyrics files travel with their songs; the playlist is written as an M3U8 with relative paths; "remove music that an earlier sync put there and that is no longer part of it" asks before removing anything and only ever touches files Hivefi itself copied (it keeps a `.hivefi-sync.json` list on the device), so recordings, voice notes and other music on a phone are never deleted and empty folders are left alone; Navidrome tracks are downloaded in their original format. A preview shows what would be copied, converted, downloaded or deleted first.
- **Podcasts:** a **Podcasts** node in the sources panel. Right-click → Add Podcast… (an RSS address). Episodes (newest first) stream or play from a download; right-click an episode to download it, delete the download or mark it played; each podcast can download new episodes automatically. Feeds are checked at start-up. Downloads go to the app's data folder (`podcast_dir` in the config changes that).
- **Folder monitoring and Inbox** (Preferences → Library): *Rescan automatically when files are added* uses the system's file notifications; the **Inbox** folder takes new music dropped into it, waits until copying has finished, reads the tags and moves each file (and its lyrics file) into the chosen library folder using a naming pattern like `%albumartist%/%album%/%disc%%track% - %title%`. Tools → Process Inbox Now does it on demand.
- **More tags:** composer, comment and BPM are read from MP3, FLAC, Ogg, Opus and M4A files, shown as optional columns and in Track Info, searchable (`composer:`, `comment:`, `bpm:>=120`), usable in Auto Playlists, editable in the tag editor and the batch tools. **Skip counts:** leaving a track in its first quarter counts as a skip (column "Skips", search `skips:`, the preset *Often Skipped*). The first start after updating re-reads local tags once; your column layout is kept and the new optional columns start hidden.

## Last.fm
Preferences → General → Last.fm: enter your own API key and shared secret (free at last.fm/api/account/create), press **Connect**, allow Hivefi in the browser, and press the button again to finish. Plays are scrobbled (at 50% or 4 minutes, never for tracks under 30 s, streams or podcasts) from a queue that survives being offline, "now playing" is announced, and right-click → Last.fm → Love / Remove Love; optionally starring a song also loves it. Hivefi cannot ship a shared API key, which is why you need your own. ListenBrainz works alongside it.

## Notifications
Preferences → General → *Show a notification when the song changes* (off by default; optionally only while Hivefi is not in front). It uses `notify-send` when installed (with the cover, and each song replaces the previous bubble), otherwise the tray icon's balloon; internet radio announces each new "Artist - Title".

## Sorting
Artists, album artists and albums sort without a leading "The" ("The Beatles" is filed under B) in the track list, the Artist / Album columns of the browser and the Albums grid. Preferences → General has the list of words to ignore (default `the`; add `a, an, die, le` or clear it to turn it off). Only the sort order changes, never the names shown.

## Search, lyrics, covers, history
- **Search syntax** (the search box): plain words and `"phrases"`, plus `artist:` `album:` `title:` `genre:` `albumartist:` `format:` `source:` `folder:` filters, number filters `year:1990-1999` `year:>=2000` `rating:>=4` `plays:0` `bitrate:>=256`, and `-word` / `-genre:rock` to exclude. Everything combines: `artist:pink -live year:<1990`.
- **Online lyrics** (LRCLIB, free, no account): Preferences → General turns on automatic lookup for tracks that have none (it sends artist, title, album and length); you can also right-click the Lyrics panel → Search for these lyrics online. Synced lyrics scroll like the others, results are cached, and they can be saved as a `.lrc` / `.txt` next to the song.
- **Cover art search** (Tools → Find Cover Art Online…, or right-click → Cover Art → Find Online…): MusicBrainz + the Cover Art Archive, one request per second, covers are embedded in every track of the album; albums that already have a cover are skipped unless you ask for a replacement.
- **Play History** (under Library): what was played, newest first; **Library Statistics** (Tools): totals, formats, decades, most played tracks and artists, listening time for the last 7 and 30 days.
- **Playlist export** as M3U8, PLS or XSPF (right-click a playlist → Export Playlist…).

## Folder view
Under **This Computer** and **Navidrome** in the sources panel, a **Folders** node shows the directory structure: the file system below your music folders, and the server's own folders (the paths Navidrome reports; with several server libraries each library is a root). Folders are built when you open them, with track counts; selecting one shows every track in it and below it in path order, and right-click offers Play Folder / Queue Folder. The opened folder is remembered across restarts. (After updating, one Navidrome sync fills in the server paths.)

## How albums are grouped
Albums are **title + album artist**. A server track belongs to the album the server says it does; a local track with an *album artist* tag belongs to (title, album artist) wherever the file lives; a local track without one belongs to (title, folder), so a compilation whose tracks all have different artists stays one album (shown under "Various Artists") and two artists' "Greatest Hits" in different folders stay two albums. Folders named "CD 1"/"Disc 2" and titles ending in "(Disc 1)" are treated as one album. The Albums grid and the Genre / Artist / Album columns use the same grouping. The first start after updating re-reads your local files' tags once (to learn which ones have an album-artist tag) and the next server sync records each track's album id.

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
