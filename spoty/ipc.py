"""Control a running overlay from the CLI through its exported GAction interface (org.gtk.Actions)."""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

from gi.repository import Gio, GLib

from . import APP_ID, OBJECT_PATH
from .config import cache_dir


def _bus():
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def running(app_id: str = APP_ID) -> bool:
    r = _bus().call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
                         GLib.Variant("(s)", (app_id,)), GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 1000, None)
    return r.unpack()[0]


def send(action: str, app_id: str = APP_ID, path: str = OBJECT_PATH) -> bool:
    """Activate an overlay action. Returns False if the overlay is not running."""
    if not running(app_id):
        return False
    _bus().call_sync(app_id, path, "org.gtk.Actions", "Activate",
                     GLib.Variant("(sava{sv})", (action, [], {})), None, Gio.DBusCallFlags.NONE, 2000, None)
    return True


def start_detached(extra_env: Optional[dict] = None) -> None:
    """Launch the overlay as an independent background process."""
    log = cache_dir()
    log.mkdir(parents=True, exist_ok=True)
    out = open(log / "overlay.log", "ab")
    env = dict(os.environ, **(extra_env or {}))
    subprocess.Popen([sys.executable, "-m", "spoty", "overlay"], stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                     start_new_session=True, env=env, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
