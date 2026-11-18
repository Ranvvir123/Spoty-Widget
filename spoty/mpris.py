"""Talk to Spotify (or any MPRIS player) over the session D-Bus using only Gio - no playerctl needed."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from gi.repository import Gio, GLib

PREFIX = "org.mpris.MediaPlayer2."
OBJ = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
TIMEOUT_MS = 1500


@dataclass
class PlayerState:
    bus_name: str
    status: str            # "Playing" | "Paused" | "Stopped"
    title: str
    artist: str
    album: str
    art_url: str
    length_s: float
    position_s: float
    trackid: str
    track_uri: Optional[str]   # spotify:track:<id> when it is a Spotify track
    can_seek: bool

    @property
    def playing(self) -> bool:
        return self.status == "Playing"

    @property
    def key(self) -> str:
        return f"{self.trackid}|{self.title}|{self.artist}"

    @property
    def is_ad(self) -> bool:
        return "/ad/" in self.trackid or self.trackid.endswith("/ad")


def spotify_uri(trackid: str, url: str = "") -> Optional[str]:
    m = re.match(r"^/com/spotify/track/([A-Za-z0-9]+)$", trackid or "")
    if m:
        return "spotify:track:" + m.group(1)
    m = re.search(r"open\.spotify\.com/track/([A-Za-z0-9]+)", url or "")
    if m:
        return "spotify:track:" + m.group(1)
    return None


def parse_time(text: str) -> float:
    """'83' -> 83, '1:23' -> 83, '1:02:03' -> 3723."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"bad time: {text!r}")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def fmt_time(sec: float) -> str:
    t = int(max(0, sec))
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Mpris:
    def __init__(self, player: str = "spotify", bus: Optional[Gio.DBusConnection] = None):
        self.player = player        # "spotify" or "any"
        self.bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)

    # -- discovery ---------------------------------------------------------------------
    def bus_names(self) -> list:
        r = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                               "ListNames", None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE,
                               TIMEOUT_MS, None)
        return [n for n in r.unpack()[0] if n.startswith(PREFIX)]

    def pick(self) -> Optional[str]:
        names = self.bus_names()
        for n in names:
            if "spotify" in n.lower():
                return n
        if self.player != "any" or not names:
            return None
        for n in names:
            st = self.state(n)
            if st and st.playing:
                return n
        return names[0]

    # -- reading -----------------------------------------------------------------------
    def _get_all(self, name: str) -> dict:
        r = self.bus.call_sync(name, OBJ, "org.freedesktop.DBus.Properties", "GetAll",
                               GLib.Variant("(s)", (PLAYER_IFACE,)), GLib.VariantType("(a{sv})"),
                               Gio.DBusCallFlags.NONE, TIMEOUT_MS, None)
        return r.unpack()[0]

    def state(self, name: Optional[str] = None) -> Optional[PlayerState]:
        name = name or self.pick()
        if not name:
            return None
        try:
            p = self._get_all(name)
        except GLib.Error:
            return None
        md = p.get("Metadata") or {}
        artist = md.get("xesam:artist") or ""
        if isinstance(artist, (list, tuple)):
            artist = ", ".join(str(a) for a in artist)
        trackid = str(md.get("mpris:trackid") or "")
        try:
            length = float(md.get("mpris:length") or 0) / 1e6
        except (TypeError, ValueError):
            length = 0.0
        return PlayerState(
            bus_name=name,
            status=str(p.get("PlaybackStatus") or "Stopped"),
            title=str(md.get("xesam:title") or ""),
            artist=str(artist),
            album=str(md.get("xesam:album") or ""),
            art_url=str(md.get("mpris:artUrl") or ""),
            length_s=length,
            position_s=float(p.get("Position") or 0) / 1e6,
            trackid=trackid,
            track_uri=spotify_uri(trackid, str(md.get("xesam:url") or "")),
            can_seek=bool(p.get("CanSeek", True)),
        )

    # -- control -----------------------------------------------------------------------
    def _call(self, name: str, method: str, params: Optional[GLib.Variant] = None) -> None:
        self.bus.call_sync(name, OBJ, PLAYER_IFACE, method, params, None, Gio.DBusCallFlags.NONE,
                           TIMEOUT_MS, None)

    def command(self, method: str) -> bool:
        """PlayPause | Play | Pause | Next | Previous | Stop. Returns False when no player is running."""
        name = self.pick()
        if not name:
            return False
        self._call(name, method)
        return True

    def set_position(self, seconds: float, st: Optional[PlayerState] = None) -> bool:
        """Jump to an exact time (SetPosition, falling back to a relative Seek)."""
        st = st or self.state()
        if not st or st.is_ad or not st.trackid:
            return False
        target = max(0.0, seconds)
        if st.length_s > 0:
            target = min(target, max(0.0, st.length_s - 0.5))
        try:
            self._call(st.bus_name, "SetPosition", GLib.Variant("(ox)", (st.trackid, int(target * 1e6))))
        except GLib.Error:
            self._call(st.bus_name, "Seek", GLib.Variant("(x)", (int((target - st.position_s) * 1e6),)))
        return True

    def seek_by(self, delta: float) -> Optional[float]:
        """Relative seek based on the live position. Returns the new position or None."""
        st = self.state()
        if not st:
            return None
        target = st.position_s + delta
        return target if self.set_position(target, st) else None
