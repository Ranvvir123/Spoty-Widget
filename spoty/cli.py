"""`spoty` command line: playback control, seeking, likes, lyrics, overlay management."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import __version__, auth, config, ipc, keys, webapi
from .mpris import Mpris, fmt_time, parse_time

APT_HINT = "sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-cairo"


def _mpris(cfg=None) -> Mpris:
    cfg = cfg or config.load()
    return Mpris(player=cfg["player"])


def _need_player(st):
    if not st:
        print("Spotify is not running (no MPRIS player found).", file=sys.stderr)
        sys.exit(1)
    return st


def cmd_status(a, cfg):
    st = _mpris(cfg).state()
    if a.json:
        print(json.dumps(None if not st else st.__dict__ | {"playing": st.playing}))
        return 0 if st else 1
    st = _need_player(st)
    print(f"{'▶' if st.playing else '⏸'} {st.title} — {st.artist}  [{fmt_time(st.position_s)} / {fmt_time(st.length_s)}]")
    return 0


def cmd_simple(method):
    def run(a, cfg):
        if not _mpris(cfg).command(method):
            print("Spotify is not running.", file=sys.stderr)
            return 1
        return 0
    return run


def cmd_seek(a, cfg):
    v = a.value.strip()
    m = _mpris(cfg)
    try:
        if v[:1] in "+-":
            delta = parse_time(v[1:]) * (1 if v[0] == "+" else -1)
            new = m.seek_by(delta)
        else:
            st = _need_player(m.state())
            target = parse_time(v)
            new = target if m.set_position(target, st) else None
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    if new is None:
        print("Cannot seek right now (no player, an ad, or the player does not allow it).", file=sys.stderr)
        return 1
    print(f"→ {fmt_time(max(0, new))}")
    return 0


def _like(a, cfg, mode):
    st = _need_player(_mpris(cfg).state())
    if not st.track_uri:
        print("The current item is not a Spotify track (ad or local file).", file=sys.stderr)
        return 1
    cid = cfg["client_id"]
    try:
        if mode == "check":
            print("yes" if webapi.is_saved(st.track_uri, cid) else "no")
            return 0
        saved = webapi.is_saved(st.track_uri, cid)
        want = (not saved) if mode == "toggle" else (mode == "like")
        if want and not saved:
            webapi.save(st.track_uri, cid)
        elif not want and saved:
            webapi.remove(st.track_uri, cid)
        print(("♥ Saved to Liked Songs: " if want else "♡ Removed from Liked Songs: ") + st.title)
        return 0
    except (auth.AuthError, webapi.ApiError) as e:
        print(e, file=sys.stderr)
        return 1


def cmd_login(a, cfg):
    cid = a.client_id or cfg["client_id"]
    if a.client_id and a.client_id != cfg["client_id"]:
        cfg["client_id"] = a.client_id
        config.save(cfg)
    if not cid:
        print("First create a free app at https://developer.spotify.com/dashboard , add this Redirect URI:\n"
              f"    {auth.REDIRECT_URI}\n"
              "then run:  spoty login --client-id <your Client ID>", file=sys.stderr)
        return 2
    try:
        auth.login(cid)
    except auth.AuthError as e:
        print(e, file=sys.stderr)
        return 1
    print("Signed in. The heart button / `spoty like` now work.")
    return 0


def cmd_logout(a, cfg):
    print("Signed out." if auth.logout() else "Was not signed in.")
    return 0


def cmd_lyrics(a, cfg):
    from .lrc import LyricsClient
    st = _need_player(_mpris(cfg).state())
    try:
        ly = LyricsClient(cfg["lrclib_url"]).fetch(st.title, st.artist, st.album, st.length_s)
    except Exception as e:
        print(f"Could not fetch lyrics: {e}", file=sys.stderr)
        return 1
    if not ly or ly.empty:
        print("No lyrics found.")
        return 1
    if ly.instrumental:
        print("♪ Instrumental")
    elif ly.has_synced:
        i = ly.index_at(st.position_s)
        print(ly.synced[i][1] if i >= 0 else "")
    else:
        print(ly.plain)
    return 0


def cmd_overlay(a, cfg):
    try:
        from .overlay import run_overlay
    except (ImportError, ValueError) as e:
        print(f"GTK 3 bindings are missing ({e}).\nInstall them with:\n  {APT_HINT}", file=sys.stderr)
        return 1
    return run_overlay()


def _launch() -> bool:
    """Start the overlay in the background and confirm it appeared on D-Bus."""
    import gi
    try:
        gi.require_version("Gtk", "3.0")
    except ValueError:
        print(f"GTK 3 bindings are missing. Install them with:\n  {APT_HINT}", file=sys.stderr)
        return False
    ipc.start_detached()
    for _ in range(40):
        time.sleep(0.1)
        if ipc.running():
            return True
    print(f"The overlay did not start. See the log: {config.cache_dir() / 'overlay.log'}", file=sys.stderr)
    return False


def cmd_start(a, cfg):
    if ipc.running():
        ipc.send("show")
        print("Overlay is already running.")
        return 0
    if not _launch():
        return 1
    print("Overlay started.")
    return 0


def cmd_action(action):
    def run(a, cfg):
        if ipc.send(action):
            return 0
        if action in ("toggle", "show", "mode-toggle", "mode-compact", "mode-lyrics"):
            return 0 if _launch() else 1
        print("Overlay is not running.", file=sys.stderr)
        return 1
    return run


def cmd_mode(a, cfg):
    action = {"compact": "mode-compact", "lyrics": "mode-lyrics", "toggle": "mode-toggle"}[a.which]
    if not ipc.send(action):
        cfg["mode"] = cfg["mode"] if a.which == "toggle" else a.which
        if a.which == "toggle":
            cfg["mode"] = "lyrics" if cfg["mode"] == "compact" else "compact"
        config.save(cfg)
        return 0 if _launch() else 1
    return 0


def cmd_quit(a, cfg):
    if ipc.send("quit"):
        print("Overlay stopped - it now uses no CPU or memory.")
    else:
        print("Overlay was not running.")
    return 0


def cmd_keys(a, cfg):
    if a.remove:
        print(f"Removed {keys.remove()} Spoty shortcuts.")
    elif a.install:
        try:
            for line in keys.install():
                print("  " + line)
        except Exception as e:
            print(f"Could not write Cinnamon shortcuts ({e}). Run on a Cinnamon desktop, or add them manually:",
                  file=sys.stderr)
            print("\n".join(keys.describe()), file=sys.stderr)
            return 1
        print("Installed. Change them anytime in: Menu > Keyboard > Shortcuts > Custom Shortcuts.")
    else:
        print("\n".join(keys.describe()))
        print("\nInstall with: spoty keys --install")
    return 0


def cmd_autostart(a, cfg):
    f = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "spoty.desktop"
    if a.state == "on":
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("[Desktop Entry]\nType=Application\nName=Spoty Overlay\n"
                     f"Exec={keys.spoty_binary()} overlay\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n")
        print("Overlay will start at login. (Stop it for games with: spoty quit)")
    else:
        try:
            f.unlink()
            print("Autostart disabled.")
        except OSError:
            print("Autostart was not enabled.")
    return 0


def cmd_doctor(a, cfg):
    ok = True

    def line(good, text, optional=False):
        nonlocal ok
        if not optional:
            ok &= good
        print(("  [ok]   " if good else "  [info] " if optional else "  [FAIL] ") + text)

    line(sys.version_info >= (3, 8), f"Python {sys.version.split()[0]}")
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_version("GdkPixbuf", "2.0")
        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Gtk
        import cairo
        Gtk.init_check()
        cairo.version
        line(True, "GTK 3 + cairo bindings")
    except Exception as e:
        line(False, f"GTK 3 bindings ({e})  ->  {APT_HINT}")
    try:
        names = Mpris(cfg["player"]).bus_names()
        line(True, "D-Bus session bus; MPRIS players: " + (", ".join(n.split('.')[-1] for n in names) or "none running"))
    except Exception as e:
        line(False, f"D-Bus session bus ({e})")
    line(bool(cfg["client_id"]), "client_id set (only needed for the like button)" if cfg["client_id"]
         else "client_id not set - optional, only for the like button (spoty login --client-id ID)", optional=True)
    line(auth.has_token(), "signed in to Spotify" if auth.has_token() else "not signed in - optional", optional=True)
    print("overlay running: " + ("yes" if ipc.running() else "no"))
    return 0 if ok else 1


def build_parser():
    p = argparse.ArgumentParser(prog="spoty", description="Spotify overlay + controller for Linux (Cinnamon).")
    p.add_argument("--version", action="version", version="spoty " + __version__)
    sp = p.add_subparsers(dest="cmd", required=True, metavar="command")

    def add(name, fn, help, **kw):
        s = sp.add_parser(name, help=help)
        s.set_defaults(fn=fn)
        return s

    s = add("status", cmd_status, "show what is playing"); s.add_argument("--json", action="store_true")
    add("play-pause", cmd_simple("PlayPause"), "toggle play / pause")
    add("play", cmd_simple("Play"), "resume")
    add("pause", cmd_simple("Pause"), "pause")
    add("next", cmd_simple("Next"), "next track")
    add("prev", cmd_simple("Previous"), "previous track (restarts the song if >3 s in)")
    s = add("seek", cmd_seek, "seek: +10, -10, 83 or 1:23 (exact time)"); s.add_argument("value")
    add("like", lambda a, c: _like(a, c, "toggle"), "like / unlike the current song (needs `spoty login`)")
    add("liked", lambda a, c: _like(a, c, "check"), "is the current song in Liked Songs?")
    s = add("login", cmd_login, "sign in with Spotify's official OAuth (opens your browser)")
    s.add_argument("--client-id", default="")
    add("logout", cmd_logout, "forget the stored token")
    add("lyrics", cmd_lyrics, "print the current lyric line")
    add("overlay", cmd_overlay, "run the overlay in the foreground")
    add("start", cmd_start, "start the overlay in the background (or show it)")
    add("toggle", cmd_action("toggle"), "show / hide the overlay (hidden = zero CPU)")
    add("show", cmd_action("show"), "show the overlay")
    add("hide", cmd_action("hide"), "hide the overlay")
    s = add("mode", cmd_mode, "switch view: compact | lyrics | toggle")
    s.add_argument("which", choices=["compact", "lyrics", "toggle"])
    add("quit", cmd_quit, "stop the overlay completely (GAME MODE: frees all resources)")
    s = add("keys", cmd_keys, "list / install Cinnamon keyboard shortcuts")
    g = s.add_mutually_exclusive_group(); g.add_argument("--install", action="store_true"); g.add_argument("--remove", action="store_true")
    s = add("autostart", cmd_autostart, "start the overlay at login"); s.add_argument("state", choices=["on", "off"])
    add("doctor", cmd_doctor, "check dependencies and setup")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config.load()
    try:
        return int(args.fn(args, cfg) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as e:     # D-Bus errors etc.: short message, no traceback
        print(f"spoty: {e}", file=sys.stderr)
        return 1
