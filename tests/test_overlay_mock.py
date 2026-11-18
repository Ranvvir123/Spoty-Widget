"""Exercises spoty/overlay.py against a recording stand-in for GTK/cairo (real GTK is not installable in CI here).

This validates the overlay's logic and drawing code paths (layout, hit-testing, drag, seek, lyrics, likes, timers),
NOT real GTK behaviour.
"""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="spoty-mock-")
os.environ["XDG_CONFIG_HOME"] = TMP + "/cfg"
os.environ["XDG_CACHE_HOME"] = TMP + "/cache"

import gi  # noqa: E402
import gi.repository  # noqa: E402,F401

gi.require_version = lambda *a, **k: None
TEXTS = []


class Layout:
    def __init__(self): self.t = ""
    def set_text(self, s, n): self.t = s
    def get_pixel_size(self): return (max(1, len(self.t)) * 7, 14)
    def __getattr__(self, n): return lambda *a, **k: None


class CR(mock.MagicMock):
    """Cairo context stand-in that records every draw call name."""


pc = types.SimpleNamespace(create_layout=lambda cr: Layout(), show_layout=lambda cr, l: TEXTS.append(l.t))
fake = {
    "Gtk": mock.MagicMock(), "Gdk": mock.MagicMock(), "GdkPixbuf": mock.MagicMock(), "PangoCairo": pc,
    "Pango": types.SimpleNamespace(SCALE=1024, FontDescription=mock.MagicMock, Weight=mock.MagicMock(),
                                   EllipsizeMode=mock.MagicMock()),
}


class FakeApp:
    def __init__(self, **kw): pass
    def do_startup(self): pass
    def hold(self): pass
    def quit(self): self.quit_called = True


fake["Gtk"].Application = FakeApp
fake["Gtk"].Application.do_startup = lambda self: None
fake["Gtk"].Application.do_shutdown = lambda self: None
for name, mod in fake.items():
    sys.modules["gi.repository." + name] = mod
    setattr(sys.modules["gi.repository"], name, mod)
sys.modules["cairo"] = mock.MagicMock()

from spoty import config, overlay  # noqa: E402
from spoty.lrc import Lyrics  # noqa: E402
from spoty.mpris import PlayerState  # noqa: E402


def state(**kw):
    d = dict(bus_name="org.mpris.MediaPlayer2.spotify", status="Playing", title="Blinding Lights", artist="The Weeknd",
             album="After Hours", art_url="", length_s=112.0, position_s=19.0, trackid="/com/spotify/track/abc",
             track_uri="spotify:track:abc", can_seek=True)
    d.update(kw)
    return PlayerState(**d)


def ev(x, y, button=1, scale=1.0, etype=None):
    return types.SimpleNamespace(x=x * scale, y=y * scale, x_root=x + 100, y_root=y + 100, button=button,
                                 type=etype or overlay.Gdk.EventType.BUTTON_PRESS, direction=overlay.Gdk.ScrollDirection.UP)


class OverlayTests(unittest.TestCase):
    def setUp(self):
        TEXTS.clear()
        config.save({"mode": "compact"})
        self.app = FakeApp()
        self.ov = overlay.Overlay(self.app)
        self.ov.mpris = mock.MagicMock()
        self.ov.win.get_visible.return_value = True
        self.ov.win.get_position.return_value = (500, 300)
        self.ov.st = state()
        self.ov.st_time = overlay.time.monotonic()
        self.ov.idle = False
        self.ov.lyrics_client.fetch = lambda *a: None

    def draw(self):
        TEXTS.clear()
        self.ov.on_draw(None, CR())
        return list(TEXTS)

    def click(self, name, **kw):
        bx, by, bw, bh, _ = next(h for h in self.ov.hits if h[4] == name)
        x, y = bx + bw / 2, by + bh / 2
        self.ov.on_motion(None, ev(x, y))
        self.assertEqual(self.ov.hover, name)
        self.ov.on_press(None, ev(x, y, **kw))
        return self.ov.on_release(None, ev(x, y, **kw))

    def test_compact_draws_all_texts_and_buttons(self):
        t = self.draw()
        for want in ("Blinding Lights", "The Weeknd", "0:19", "-1:33", "Lyrics"):
            self.assertIn(want, t)
        self.assertEqual({h[4] for h in self.ov.hits}, {"hide", "prev", "play", "next", "like", "mode", "seekbar"})

    def test_buttons_call_player(self):
        self.draw()
        self.click("next"); self.ov.mpris.command.assert_called_with("Next")
        self.draw(); self.click("prev"); self.ov.mpris.command.assert_called_with("Previous")
        self.draw(); self.click("play"); self.ov.mpris.command.assert_called_with("PlayPause")
        self.assertEqual(self.ov.st.status, "Paused")                       # optimistic flip
        self.draw(); self.click("mode"); self.assertEqual(self.ov.cfg["mode"], "lyrics")
        self.assertEqual(config.load()["mode"], "lyrics")                    # persisted
        self.draw(); self.click("mode"); self.assertEqual(self.ov.cfg["mode"], "compact")

    def test_seek_bar_click_and_drag_exact_time(self):
        self.draw()
        x0, w = self.ov.bar_geom
        by = next(h for h in self.ov.hits if h[4] == "seekbar")
        y = by[1] + by[3] / 2
        # click at 25% of the bar -> 28 s of 112
        self.ov.on_press(None, ev(x0 + w * 0.25, y))
        self.assertTrue(self.ov.seek_drag)
        self.ov.on_motion(None, ev(x0 + w * 0.75, y))                         # scrub to 75% -> 84 s
        self.assertAlmostEqual(self.ov.display_position(), 84.0, places=1)
        self.assertIn("1:24", self.draw())                                    # live preview label while dragging
        self.ov.on_release(None, ev(x0 + w * 0.75, y))
        target, st = self.ov.mpris.set_position.call_args[0]
        self.assertAlmostEqual(target, 84.0, places=1)
        self.assertFalse(self.ov.seek_drag)
        self.assertAlmostEqual(self.ov.st.position_s, 84.0, places=1)         # bar stays put after the drop
        # dragging past either end clamps
        self.ov.on_press(None, ev(x0 + 5, y)); self.ov.on_motion(None, ev(x0 - 80, y)); self.ov.on_release(None, ev(x0 - 80, y))
        self.assertEqual(self.ov.mpris.set_position.call_args[0][0], 0.0)
        self.ov.on_press(None, ev(x0 + 5, y)); self.ov.on_motion(None, ev(x0 + w + 80, y)); self.ov.on_release(None, ev(x0 + w + 80, y))
        self.assertAlmostEqual(self.ov.mpris.set_position.call_args[0][0], 111.5, places=1)   # never seeks past the end

    def test_seek_blocked_for_ads_and_unseekable(self):
        self.ov.st = state(can_seek=False)
        self.draw()
        x0, w = self.ov.bar_geom
        self.ov.on_press(None, ev(x0 + 10, 90))
        self.assertFalse(self.ov.seek_drag)

    def test_scroll_wheel_seeks_5s(self):
        self.draw()
        x0, w = self.ov.bar_geom
        by = next(h for h in self.ov.hits if h[4] == "seekbar")
        before = self.ov.display_position()
        self.assertTrue(self.ov.on_scroll(None, ev(x0 + 40, by[1] + 4)))
        self.assertAlmostEqual(self.ov.mpris.set_position.call_args[0][0], before + 5, delta=0.5)

    def test_window_drag_saves_position(self):
        self.draw()
        self.ov.on_press(None, ev(300, 40))                                   # empty card area
        self.assertIsNotNone(self.ov.drag)
        self.ov.on_motion(None, ev(340, 70))
        self.ov.win.move.assert_called_with(540, 330)
        self.ov.on_release(None, ev(340, 70))
        self.assertEqual((config.load()["x"], config.load()["y"]), (500, 300))

    def test_scale_changes_hit_testing(self):
        self.ov.cfg["scale"] = 1.5
        self.draw()
        bx, by, bw, bh, _ = next(h for h in self.ov.hits if h[4] == "next")
        e = ev(bx + bw / 2, by + bh / 2, scale=1.5)
        self.ov.on_press(None, e); self.ov.on_release(None, e)
        self.ov.mpris.command.assert_called_with("Next")

    def test_lyrics_view_scrolls_and_highlights_current_line(self):
        self.ov.cfg["mode"] = "lyrics"
        ly = Lyrics(synced=[(0, "one"), (10, "two"), (20, "three"), (30, "four"), (40, "")])
        self.ov.st = state(position_s=21.0)
        self.ov.lyrics, self.ov.lyrics_state = ly, "ok"
        self.ov.lyric_pos = self.ov.lyric_target()
        self.assertEqual(self.ov.lyric_pos, 2.0)
        t = self.draw()
        for want in ("Blinding Lights", "three", "two", "four", "Card", "0:21"):
            self.assertIn(want, t)
        self.assertNotIn("one", t)                                           # two lines back is scrolled out
        # time passes -> target moves, easing converges
        self.ov.st = state(position_s=31.0, status="Paused")
        self.assertEqual(self.ov.lyric_target(), 3.0)
        self.assertTrue(self.ov.animating())
        for _ in range(60):
            self.ov.last_tick = overlay.time.monotonic() - 0.033
            self.ov.on_tick()
        self.assertEqual(self.ov.lyric_pos, 3.0)
        self.assertIn("♪", self.draw())                                 # the empty gap line shows a note

    def test_lyrics_states_messages(self):
        self.ov.cfg["mode"] = "lyrics"
        for state_name, msg in (("loading", "Loading lyrics…"), ("none", "No lyrics found for this song"),
                                ("instrumental", "♪ Instrumental"), ("error", "Couldn't reach the lyrics service")):
            self.ov.lyrics_state = state_name
            self.assertIn(msg, self.draw())

    def test_track_change_requests_lyrics_only_in_lyrics_mode(self):
        calls = []
        self.ov.lyrics_client.fetch = lambda *a: calls.append(a)
        self.ov.cfg["mode"] = "compact"
        self.ov.on_track_change(state())
        self.assertEqual(self.ov.lyrics_state, "idle")                        # privacy: nothing fetched in card view
        self.ov.set_mode("lyrics")
        self.assertEqual(self.ov.lyrics_state, "loading")
        self.ov.lyrics_state = "idle"
        self.ov.cfg["lyrics"] = False
        self.ov.request_lyrics()
        self.assertEqual(self.ov.lyrics_state, "off")

    def test_like_requires_login_and_toggles(self):
        self.draw()
        with mock.patch.object(overlay.auth, "has_token", return_value=False):
            self.ov.toggle_like()
        self.assertIn("spoty login", self.ov.toast[0])
        self.assertTrue(any("spoty login" in s for s in self.draw()))         # toast replaces the artist line
        self.assertNotIn("The Weeknd", self.draw())
        saved = []
        self.ov.bg = lambda fn, cb: cb(fn(), None)                            # run synchronously
        with mock.patch.object(overlay.auth, "has_token", return_value=True), \
             mock.patch.object(overlay.webapi, "save", lambda u, c: saved.append(("save", u))), \
             mock.patch.object(overlay.webapi, "remove", lambda u, c: saved.append(("remove", u))):
            self.ov.toggle_like(); self.assertTrue(self.ov.liked)
            self.ov.toggle_like(); self.assertFalse(self.ov.liked)
        self.assertEqual(saved, [("save", "spotify:track:abc"), ("remove", "spotify:track:abc")])

    def test_like_failure_reverts_and_explains(self):
        self.ov.bg = lambda fn, cb: cb(*(lambda: (None, RuntimeError("Spotify refused (403)")))())
        with mock.patch.object(overlay.auth, "has_token", return_value=True):
            self.ov.toggle_like()
        self.assertFalse(self.ov.liked)
        self.assertIn("403", self.ov.toast[0])

    def test_idle_and_visibility_rules(self):
        self.ov.st, self.ov.idle = None, True
        self.assertFalse(self.ov.should_show())                               # auto-hide when Spotify closed
        self.ov.cfg["auto_hide_idle"] = False
        self.assertTrue(self.ov.should_show())
        self.assertIn("Nothing playing", self.draw())
        self.ov.hide_user()
        self.assertFalse(self.ov.should_show())
        self.assertEqual(self.ov.poll_id, 0)                                  # user-hidden => NO timers at all
        self.assertEqual(self.ov.tick_id, 0)
        self.ov.win.hide.assert_called()

    def test_timers_stop_when_paused_and_when_hidden(self):
        self.ov.user_hidden = False
        self.ov.ensure_timers()
        self.assertTrue(self.ov.poll_id and self.ov.tick_id)                  # visible + playing: poll + animate
        self.ov.st = state(status="Paused")
        self.ov.cur_top, self.ov.cur_bot = self.ov.tgt_top, self.ov.tgt_bot
        self.ov.ensure_timers()
        self.assertEqual(self.ov.tick_id, 0)                                  # paused: no animation timer
        self.assertTrue(self.ov.poll_id)
        self.ov.hide_user()
        self.assertEqual((self.ov.poll_id, self.ov.tick_id), (0, 0))

    def test_actions_wired_on_app(self):
        a = overlay.OverlayApp()
        a.ov = self.ov
        a.add_action = lambda act: acts.append(act)
        acts = []
        with mock.patch.object(overlay.Gio, "SimpleAction") as SA:
            SA.new.side_effect = lambda name, t: mock.MagicMock(name=name)
            a.do_startup()
        self.assertEqual(SA.new.call_count, 8)
        self.assertEqual([c.args[0] for c in SA.new.call_args_list],
                         ["toggle", "show", "hide", "mode-toggle", "mode-compact", "mode-lyrics", "like", "quit"])

    def test_pixbuf_art_cache_sizes(self):
        self.ov.art_orig = mock.MagicMock()
        self.ov.cfg["scale"] = 1.5
        self.ov.art_pixbuf(88)
        self.ov.art_orig.scale_simple.assert_called_with(132, 132, overlay.GdkPixbuf.InterpType.HYPER)
        self.ov.art_pixbuf(88)
        self.assertEqual(self.ov.art_orig.scale_simple.call_count, 1)         # cached


if __name__ == "__main__":
    unittest.main(verbosity=2)
