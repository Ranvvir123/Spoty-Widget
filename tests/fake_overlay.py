"""Stand-in for the GTK overlay: exports the same GActions so the CLI->overlay IPC can be tested without GTK."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gi.repository import Gio

from spoty import APP_ID

LOG = os.environ["FAKE_LOG"]
app = Gio.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)


def mk(name):
    a = Gio.SimpleAction.new(name, None)
    a.connect("activate", lambda *_: (open(LOG, "a").write(name + "\n"), app.quit() if name == "quit" else None))
    app.add_action(a)


app.connect("startup", lambda _a: [mk(n) for n in ("toggle", "show", "hide", "mode-toggle", "mode-compact", "mode-lyrics", "like", "quit")])
app.connect("activate", lambda _a: None)
app.hold()
sys.exit(app.run([]))
