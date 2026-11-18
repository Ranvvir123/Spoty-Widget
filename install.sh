#!/usr/bin/env bash
# Spoty installer (user-level, no sudo needed except for the optional GTK packages).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/spoty"
BIN="$HOME/.local/bin/spoty"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/spoty.desktop"
APT="sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-cairo"

usage() { echo "Usage: ./install.sh [--keys] [--autostart] | --uninstall"; }

if [[ "${1:-}" == "--uninstall" ]]; then
  [[ -x "$BIN" ]] && "$BIN" quit >/dev/null 2>&1 || true
  [[ -x "$BIN" ]] && "$BIN" keys --remove >/dev/null 2>&1 || true
  rm -rf "$PREFIX" "$BIN" "$DESKTOP" "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/spoty.desktop"
  echo "Spoty removed. (Settings/token kept in ~/.config/spoty - delete that folder to forget them.)"
  exit 0
fi

WANT_KEYS=0; WANT_AUTO=0
for a in "$@"; do
  case "$a" in
    --keys) WANT_KEYS=1 ;;
    --autostart) WANT_AUTO=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

PY=/usr/bin/python3
[[ -x "$PY" ]] || { echo "Need the system Python at /usr/bin/python3"; exit 1; }
"$PY" - <<'PYEOF' || { echo; echo "Missing GTK bindings. Install them with:"; echo "  $APT"; echo "then run ./install.sh again."; exit 1; }
import gi
for ns, v in (("Gtk", "3.0"), ("Gdk", "3.0"), ("GdkPixbuf", "2.0"), ("Pango", "1.0"), ("PangoCairo", "1.0")):
    gi.require_version(ns, v)
from gi.repository import Gtk, PangoCairo
import cairo
PYEOF

mkdir -p "$PREFIX" "$(dirname "$BIN")" "$(dirname "$DESKTOP")"
rm -rf "$PREFIX/spoty"
cp -r "$SRC/spoty" "$PREFIX/spoty"
find "$PREFIX" -name __pycache__ -type d -prune -exec rm -rf {} +

cat > "$BIN" <<WRAP
#!$PY
import sys
sys.path.insert(0, "$PREFIX")
from spoty.cli import main
sys.exit(main())
WRAP
chmod +x "$BIN"

cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=Spoty Overlay
Comment=Spotify overlay with lyrics, seeking and likes
Exec=$BIN start
Icon=multimedia-player
Categories=AudioVideo;Audio;Player;
Terminal=false
Actions=Quit;

[Desktop Action Quit]
Name=Quit overlay (game mode)
Exec=$BIN quit
DESK

echo "Installed to $PREFIX  (command: $BIN)"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Note: ~/.local/bin is not on your PATH yet - log out/in once, or use the full path." ;; esac
[[ $WANT_KEYS -eq 1 ]] && "$BIN" keys --install
[[ $WANT_AUTO -eq 1 ]] && "$BIN" autostart on
echo
"$BIN" doctor || true
echo
echo "Start it with:  spoty start     (or find 'Spoty Overlay' in the menu)"
echo "Stop it fully:  spoty quit      (use this before games)"
