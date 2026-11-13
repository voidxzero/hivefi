#!/bin/sh
# Installs Hivefi for the current user. Works on Arch, Fedora, Debian/Ubuntu and others.
# System packages provide every dependency; the app runs straight from this folder (no venv or pip needed).
set -e
cd "$(dirname "$0")"

. /etc/os-release 2>/dev/null || true
case " $ID $ID_LIKE " in
  *" arch "*)
    PKGS="python python-pyqt6 python-numpy python-mutagen python-requests python-gobject gst-python gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugins-ugly gst-libav qt6-multimedia-ffmpeg ffmpeg"
    CMD="sudo pacman -S --needed $PKGS" ;;
  *" fedora "*)
    CMD="sudo dnf install python3-pyqt6 python3-numpy python3-mutagen python3-requests python3-gobject gstreamer1-plugins-base gstreamer1-plugins-good gstreamer1-plugins-bad-free gstreamer1-plugin-libav ffmpeg-free" ;;
  *" debian "*|*" ubuntu "*)
    CMD="sudo apt install python3-pyqt6 python3-pyqt6.qtmultimedia python3-numpy python3-mutagen python3-requests python3-gi gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav ffmpeg" ;;
  *) CMD="" ;;
esac

if [ "$1" = "--deps" ]; then
  [ -n "$CMD" ] && { echo "$CMD"; $CMD; } || echo "Unknown distro: install PyQt6, mutagen, requests, numpy, PyGObject, GStreamer plugins and ffmpeg manually."
  exit 0
fi
[ -n "$CMD" ] && echo "Tip: install system dependencies first with:  ./install.sh --deps   ($CMD)"

if ! python3 -c "import PyQt6.QtWidgets, PyQt6.QtMultimedia, mutagen, requests, numpy" 2>/dev/null; then
  echo "Missing Python packages (PyQt6 with QtMultimedia, mutagen, requests, numpy). Install them first with: ./install.sh --deps"
  exit 1
fi

mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
# a launcher that runs the source tree directly, so updates to this folder take effect at once
rm -f "$HOME/.local/bin/hivefi"   # older installs left a symlink into .venv here
printf '#!/bin/sh\nPYTHONPATH="%s${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m hivefi "$@"\n' "$PWD" > "$HOME/.local/bin/hivefi"
chmod +x "$HOME/.local/bin/hivefi"
sed "s|^Exec=.*|Exec=$HOME/.local/bin/hivefi|" data/hivefi.desktop > "$HOME/.local/share/applications/hivefi.desktop"
cp hivefi/hivefi.svg "$HOME/.local/share/icons/hicolor/scalable/apps/hivefi.svg"
# a running desktop only notices a new icon when the theme folder itself changes
touch "$HOME/.local/share/icons/hicolor"
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t -f --ignore-theme-index "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
command -v update-desktop-database >/dev/null && update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true
echo "Installed. Launch 'Hivefi' from your app menu or run: hivefi (~/.local/bin must be on PATH)"
