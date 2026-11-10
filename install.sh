#!/bin/sh
# Installs Hivefi for the current user. Works on Arch, Fedora, Debian/Ubuntu and others.
# System packages provide Qt/GStreamer/PyGObject; the app itself goes in a venv (PEP 668 safe).
set -e
cd "$(dirname "$0")"

. /etc/os-release 2>/dev/null || true
case " $ID $ID_LIKE " in
  *" arch "*)
    PKGS="python python-pyqt6 python-numpy python-mutagen python-requests python-gobject gst-python gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugins-ugly gst-libav qt6-multimedia-ffmpeg"
    CMD="sudo pacman -S --needed $PKGS" ;;
  *" fedora "*)
    CMD="sudo dnf install python3-pyqt6 python3-numpy python3-mutagen python3-requests python3-gobject gstreamer1-plugins-base gstreamer1-plugins-good gstreamer1-plugins-bad-free gstreamer1-plugin-libav" ;;
  *" debian "*|*" ubuntu "*)
    CMD="sudo apt install python3-pyqt6 python3-pyqt6.qtmultimedia python3-numpy python3-mutagen python3-requests python3-gi gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav" ;;
  *) CMD="" ;;
esac

if [ "$1" = "--deps" ]; then
  [ -n "$CMD" ] && { echo "$CMD"; $CMD; } || echo "Unknown distro: install PyQt6, mutagen, requests, PyGObject and GStreamer plugins manually."
  exit 0
fi
[ -n "$CMD" ] && echo "Tip: install system dependencies first with:  ./install.sh --deps   ($CMD)"

# --system-site-packages lets the venv see distro-provided PyQt6 and PyGObject (gi cannot be pip-installed easily)
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e . 

mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
ln -sf "$PWD/.venv/bin/hivefi" "$HOME/.local/bin/hivefi"
sed "s|^Exec=.*|Exec=$PWD/.venv/bin/hivefi|" data/hivefi.desktop > "$HOME/.local/share/applications/hivefi.desktop"
cp hivefi/hivefi.svg "$HOME/.local/share/icons/hicolor/scalable/apps/hivefi.svg"
echo "Installed. Launch 'Hivefi' from your app menu or run: hivefi (~/.local/bin must be on PATH)"
