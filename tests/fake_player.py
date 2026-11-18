"""A fake Spotify on the session bus (org.mpris.MediaPlayer2.spotify) for tests. Logs every call to $FAKE_LOG."""
import json
import os
import time

from gi.repository import Gio, GLib

XML = """<node>
 <interface name="org.freedesktop.DBus.Properties">
  <method name="GetAll"><arg type="s" direction="in"/><arg type="a{sv}" direction="out"/></method>
 </interface>
 <interface name="org.mpris.MediaPlayer2.Player">
  <method name="PlayPause"/><method name="Play"/><method name="Pause"/><method name="Next"/><method name="Previous"/>
  <method name="Seek"><arg type="x" direction="in"/></method>
  <method name="SetPosition"><arg type="o" direction="in"/><arg type="x" direction="in"/></method>
 </interface></node>"""

LOG = os.environ["FAKE_LOG"]
AD = os.environ.get("FAKE_AD") == "1"
S = {"playing": True, "pos": 19.0, "t": time.monotonic()}
LENGTH = 112.0


def pos():
    return S["pos"] + (time.monotonic() - S["t"] if S["playing"] else 0)


def log(*a):
    with open(LOG, "a") as f:
        f.write(json.dumps(a) + "\n")


def on_call(conn, sender, path, iface, method, params, inv):
    if method == "GetAll":
        trackid = "/com/spotify/ad/1" if AD else "/com/spotify/track/6rqhFgbbKwnb9MLmUQDhG6"
        md = {"mpris:trackid": GLib.Variant("o", trackid), "mpris:length": GLib.Variant("x", int(LENGTH * 1e6)),
              "xesam:title": GLib.Variant("s", "Blinding Lights"), "xesam:artist": GLib.Variant("as", ["The Weeknd"]),
              "xesam:album": GLib.Variant("s", "After Hours"), "mpris:artUrl": GLib.Variant("s", "")}
        props = {"PlaybackStatus": GLib.Variant("s", "Playing" if S["playing"] else "Paused"),
                 "Metadata": GLib.Variant("a{sv}", md), "Position": GLib.Variant("x", int(pos() * 1e6)),
                 "CanSeek": GLib.Variant("b", True)}
        inv.return_value(GLib.Variant("(a{sv})", (props,)))
        return
    log(method, params.unpack() if params is not None else [])
    if method == "PlayPause":
        S.update(pos=pos(), t=time.monotonic(), playing=not S["playing"])
    elif method == "Play":
        S.update(pos=pos(), t=time.monotonic(), playing=True)
    elif method == "Pause":
        S.update(pos=pos(), t=time.monotonic(), playing=False)
    elif method == "SetPosition":
        S.update(pos=params.unpack()[1] / 1e6, t=time.monotonic())
    elif method == "Seek":
        S.update(pos=pos() + params.unpack()[0] / 1e6, t=time.monotonic())
    inv.return_value(None)


def on_bus(conn, name):
    for info in Gio.DBusNodeInfo.new_for_xml(XML).interfaces:
        conn.register_object("/org/mpris/MediaPlayer2", info, on_call, None, None)


Gio.bus_own_name(Gio.BusType.SESSION, "org.mpris.MediaPlayer2.spotify", Gio.BusNameOwnerFlags.NONE, on_bus,
                 lambda c, n: print("ready", flush=True), None)
GLib.MainLoop().run()
