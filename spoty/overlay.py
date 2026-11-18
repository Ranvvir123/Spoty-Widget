"""The always-on-top GTK 3 overlay. Everything is drawn with cairo/Pango in a single DrawingArea.

Resource use: when hidden by the user there are NO timers at all (zero CPU); `spoty quit` ends the process.
"""
from __future__ import annotations

import hashlib
import math
import signal
import threading
import time
import urllib.request

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

import cairo  # noqa: E402

from . import APP_ID, auth, config, palette, webapi  # noqa: E402
from .lrc import LyricsClient  # noqa: E402
from .mpris import Mpris, fmt_time  # noqa: E402

SIZES = {"compact": (400, 112), "lyrics": (400, 244)}
WHITE = (255, 255, 255)
MUTED = (178, 178, 188)
GREEN = (30, 215, 96)
RED = (240, 80, 100)


# ------------------------------------------------------------------------------------------- drawing helpers
def rgba(cr, rgb, a=1.0):
    cr.set_source_rgba(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, a)


def rrect(cr, x, y, w, h, r):
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def text(cr, s, x, y, size, color=WHITE, alpha=1.0, bold=False, maxw=None, align="left"):
    """Draw text with its top-left at (x, y). align='right' treats x as the right edge, 'center' as the middle."""
    layout = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription()
    fd.set_family("Sans")
    fd.set_absolute_size(size * Pango.SCALE)
    fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    layout.set_font_description(fd)
    layout.set_text(s or "", -1)
    if maxw is not None:
        layout.set_width(int(maxw * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
    w, _h = layout.get_pixel_size()
    if align == "right":
        x -= w
    elif align == "center":
        x -= w / 2.0
    cr.move_to(x, y)
    rgba(cr, color, alpha)
    PangoCairo.show_layout(cr, layout)
    return w


def icon_play(cr, cx, cy, s, color, a):
    cr.move_to(cx - s * 0.7, cy - s)
    cr.line_to(cx + s * 0.95, cy)
    cr.line_to(cx - s * 0.7, cy + s)
    cr.close_path()
    rgba(cr, color, a)
    cr.fill()


def icon_pause(cr, cx, cy, s, color, a):
    rgba(cr, color, a)
    for dx in (-0.85, 0.3):
        cr.rectangle(cx + s * dx, cy - s * 0.95, s * 0.55, s * 1.9)
    cr.fill()


def icon_next(cr, cx, cy, s, color, a):
    cr.move_to(cx - s * 0.85, cy - s * 0.85)
    cr.line_to(cx + s * 0.45, cy)
    cr.line_to(cx - s * 0.85, cy + s * 0.85)
    cr.close_path()
    cr.rectangle(cx + s * 0.6, cy - s * 0.85, s * 0.3, s * 1.7)
    rgba(cr, color, a)
    cr.fill()


def icon_prev(cr, cx, cy, s, color, a):
    cr.move_to(cx + s * 0.85, cy - s * 0.85)
    cr.line_to(cx - s * 0.45, cy)
    cr.line_to(cx + s * 0.85, cy + s * 0.85)
    cr.close_path()
    cr.rectangle(cx - s * 0.9, cy - s * 0.85, s * 0.3, s * 1.7)
    rgba(cr, color, a)
    cr.fill()


def icon_heart(cr, cx, cy, s, color, a, filled):
    cr.move_to(cx, cy + s * 0.9)
    cr.curve_to(cx - s * 1.6, cy - s * 0.1, cx - s * 0.7, cy - s * 1.2, cx, cy - s * 0.35)
    cr.curve_to(cx + s * 0.7, cy - s * 1.2, cx + s * 1.6, cy - s * 0.1, cx, cy + s * 0.9)
    cr.close_path()
    rgba(cr, color, a)
    if filled:
        cr.fill()
    else:
        cr.set_line_width(1.8)
        cr.stroke()


def icon_close(cr, cx, cy, s, color, a):
    rgba(cr, color, a)
    cr.set_line_width(1.8)
    cr.move_to(cx - s, cy - s)
    cr.line_to(cx + s, cy + s)
    cr.move_to(cx + s, cy - s)
    cr.line_to(cx - s, cy + s)
    cr.stroke()


def lerp3(cur, tgt, k):
    return tuple(c + (t - c) * k for c, t in zip(cur, tgt))


# ------------------------------------------------------------------------------------------- the overlay
class Overlay:
    def __init__(self, app):
        self.app = app
        self.cfg = config.load()
        self.mpris = Mpris(self.cfg["player"])
        self.lyrics_client = LyricsClient(self.cfg["lrclib_url"])

        # playback state
        self.st = None
        self.st_time = time.monotonic()
        self.idle = True
        self.user_hidden = False
        # per-track data
        self.art_orig = None
        self.art_cache = {}
        self.liked = None
        self.lyrics = None
        self.lyrics_state = "idle"        # idle|loading|ok|none|instrumental|off|error
        self.lyric_pos = 0.0
        # colours (smoothly animated)
        self.cur_top, self.cur_bot = (42.0, 42.0, 46.0), (27.0, 27.0, 31.0)
        self.tgt_top, self.tgt_bot = palette.DEFAULT
        # interaction
        self.hits = []
        self.hover = None
        self.pressed = None
        self.drag = None
        self.seek_drag = False
        self.seek_preview = 0.0
        self.bar_geom = (0, 1)
        self.toast = None
        # timers
        self.poll_id = 0
        self.poll_ms = 0
        self.tick_id = 0
        self.last_tick = time.monotonic()

        self._build_window()

    # ------------------------------------------------------------------ window
    def _build_window(self):
        w = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        w.set_title("Spoty")
        w.set_decorated(False)
        w.set_resizable(False)
        w.set_skip_taskbar_hint(True)
        w.set_skip_pager_hint(True)
        w.set_accept_focus(False)
        w.set_app_paintable(True)
        screen = w.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            w.set_visual(visual)
        css = Gtk.CssProvider()
        css.load_from_data(b".spoty { background-color: rgba(0,0,0,0); box-shadow: none; border: none; }")
        Gtk.StyleContext.add_provider_for_screen(screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        w.get_style_context().add_class("spoty")

        area = Gtk.DrawingArea()
        area.get_style_context().add_class("spoty")
        area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
                        | Gdk.EventMask.SCROLL_MASK)
        area.connect("draw", self.on_draw)
        area.connect("button-press-event", self.on_press)
        area.connect("button-release-event", self.on_release)
        area.connect("motion-notify-event", self.on_motion)
        area.connect("leave-notify-event", self.on_leave)
        area.connect("scroll-event", self.on_scroll)
        w.add(area)
        self.win, self.area = w, area
        self.apply_size()

    def apply_size(self):
        W, H = SIZES[self.cfg["mode"]]
        s = self.cfg["scale"]
        self.area.set_size_request(int(W * s), int(H * s))
        self.win.queue_resize()

    def _monitors(self):
        d = Gdk.Display.get_default()
        return [d.get_monitor(i) for i in range(d.get_n_monitors())]

    def initial_position(self):
        W, _ = SIZES[self.cfg["mode"]]
        s = self.cfg["scale"]
        x, y = self.cfg["x"], self.cfg["y"]
        if x is not None and y is not None:
            for m in self._monitors():
                g = m.get_geometry()
                if g.x <= x + 20 <= g.x + g.width and g.y <= y + 20 <= g.y + g.height:
                    return x, y
        d = Gdk.Display.get_default()
        mon = d.get_primary_monitor() or d.get_monitor(0)
        g = mon.get_workarea()
        return int(g.x + g.width - W * s - 24), int(g.y + 48)

    def present_window(self):
        self.win.show_all()
        if self.cfg["always_on_top"]:
            self.win.set_keep_above(True)
        self.win.stick()
        x, y = self.initial_position()
        self.win.move(x, y)

    def save_position(self):
        x, y = self.win.get_position()
        self.cfg["x"], self.cfg["y"] = int(x), int(y)
        config.save(self.cfg)

    # ------------------------------------------------------------------ visibility + timers
    def should_show(self):
        return (not self.user_hidden) and not (self.idle and self.cfg["auto_hide_idle"])

    def update_visibility(self):
        show = self.should_show()
        if show and not self.win.get_visible():
            self.present_window()
        elif not show and self.win.get_visible():
            self.win.hide()

    def animating(self):
        d = max(abs(a - b) for a, b in zip(self.cur_top + self.cur_bot, self.tgt_top + self.tgt_bot))
        return d > 0.6 or abs(self.lyric_pos - self.lyric_target()) > 0.003

    def ensure_timers(self):
        want_poll = 0 if self.user_hidden else (500 if self.win.get_visible() else 1500)
        if want_poll != self.poll_ms:
            if self.poll_id:
                GLib.source_remove(self.poll_id)
                self.poll_id = 0
            if want_poll:
                self.poll_id = GLib.timeout_add(want_poll, self.on_poll)
            self.poll_ms = want_poll
        playing = bool(self.st and self.st.playing)
        want_tick = self.win.get_visible() and (playing or self.animating())
        if want_tick and not self.tick_id:
            self.last_tick = time.monotonic()
            self.tick_id = GLib.timeout_add(max(15, int(1000 / self.cfg["fps"])), self.on_tick)
        elif not want_tick and self.tick_id:
            GLib.source_remove(self.tick_id)
            self.tick_id = 0

    def on_poll(self):
        self.poll_once()
        return True

    def on_tick(self):
        now = time.monotonic()
        dt = min(0.2, now - self.last_tick)
        self.last_tick = now
        k = min(1.0, dt * 6.0)
        self.cur_top = lerp3(self.cur_top, self.tgt_top, k)
        self.cur_bot = lerp3(self.cur_bot, self.tgt_bot, k)
        self.lyric_pos += (self.lyric_target() - self.lyric_pos) * min(1.0, dt * 9.0)
        if abs(self.lyric_pos - self.lyric_target()) < 0.003:
            self.lyric_pos = self.lyric_target()
        self.area.queue_draw()
        playing = bool(self.st and self.st.playing)
        if not (playing or self.animating()) or not self.win.get_visible():
            self.tick_id = 0
            return False
        return True

    def poll_once(self):
        try:
            st = self.mpris.state()
        except Exception:
            st = None
        if st is not None and not st.title and st.status == "Stopped":
            st = None
        prev = self.st
        self.st, self.st_time, self.idle = st, time.monotonic(), st is None
        if st is not None and (prev is None or prev.key != st.key):
            self.on_track_change(st)
        self.update_visibility()
        self.ensure_timers()
        self.area.queue_draw()

    def poll_soon(self, ms=300):
        GLib.timeout_add(ms, lambda: (self.poll_once(), False)[1])

    # ------------------------------------------------------------------ per-track data
    def bg(self, fn, cb):
        def run():
            try:
                res = (fn(), None)
            except Exception as e:           # network, API, decode errors: reported to the callback
                res = (None, e)
            GLib.idle_add(lambda: (cb(*res), False)[1])
        threading.Thread(target=run, daemon=True).start()

    def cur_key(self):
        return self.st.key if self.st else None

    def on_track_change(self, st):
        self.art_orig, self.art_cache, self.liked = None, {}, None
        self.lyrics, self.lyrics_state, self.lyric_pos = None, "idle", 0.0
        self.toast = None
        key = st.key
        if st.art_url:
            self.bg(lambda: self.fetch_art(st.art_url), lambda data, err: self.set_art(key, data))
        if st.track_uri and auth.has_token():
            self.bg(lambda: webapi.is_saved(st.track_uri, self.cfg["client_id"]),
                    lambda res, err: self.set_liked(key, res, err))
        if self.cfg["mode"] == "lyrics":
            self.request_lyrics()

    def fetch_art(self, url):
        f = config.cache_dir() / "art" / (hashlib.sha1(url.encode()).hexdigest() + ".img")
        if not f.exists():
            req = urllib.request.Request(url, headers={"User-Agent": "spoty-overlay"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        return f.read_bytes()

    def set_art(self, key, data):
        if not data or key != self.cur_key():
            return
        try:
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pb = loader.get_pixbuf()
        except GLib.Error:
            return
        if pb is None:
            return
        self.art_orig, self.art_cache = pb, {}
        small = pb.scale_simple(24, 24, GdkPixbuf.InterpType.BILINEAR)
        px, n, rs = small.get_pixels(), small.get_n_channels(), small.get_rowstride()
        pixels = [(px[y * rs + x * n], px[y * rs + x * n + 1], px[y * rs + x * n + 2])
                  for y in range(24) for x in range(24)]
        c1, c2 = palette.dominant_colors(pixels)
        self.tgt_top = tuple(float(v) for v in palette.tone(c1))
        self.tgt_bot = tuple(float(v) * 0.85 for v in palette.tone(c2))
        self.ensure_timers()
        self.area.queue_draw()

    def set_liked(self, key, res, err):
        if key == self.cur_key() and err is None:
            self.liked = bool(res)
            self.area.queue_draw()

    def request_lyrics(self):
        st = self.st
        if not st:
            return
        if not self.cfg["lyrics"]:
            self.lyrics_state = "off"
        elif st.is_ad or not st.title:
            self.lyrics_state = "none"
        elif self.lyrics_state == "idle" or self.lyrics_state == "off" or self.lyrics_state == "error":
            self.lyrics_state = "loading"
            key = st.key
            self.bg(lambda: self.lyrics_client.fetch(st.title, st.artist, st.album, st.length_s),
                    lambda res, err: self.set_lyrics(key, res, err))
        self.area.queue_draw()

    def set_lyrics(self, key, res, err):
        if key != self.cur_key():
            return
        if err is not None:
            self.lyrics_state = "error"
        elif res is None or res.empty:
            self.lyrics_state = "none"
        else:
            self.lyrics = res
            self.lyrics_state = "instrumental" if res.instrumental and not res.has_synced else "ok"
        self.lyric_pos = self.lyric_target()
        self.ensure_timers()
        self.area.queue_draw()

    # ------------------------------------------------------------------ position + lyrics maths
    def display_position(self):
        if self.seek_drag:
            return self.seek_preview
        st = self.st
        if not st:
            return 0.0
        p = st.position_s + (time.monotonic() - self.st_time if st.playing else 0.0)
        if st.length_s > 0:
            p = min(p, st.length_s)
        return max(0.0, p)

    def lyric_lines(self):
        ly = self.lyrics
        if self.lyrics_state != "ok" or ly is None:
            return []
        return [t for _, t in ly.synced] if ly.has_synced else ly.plain_lines()

    def lyric_target(self):
        if self.cfg["mode"] != "lyrics" or self.lyrics_state != "ok" or self.lyrics is None or not self.st:
            return 0.0
        pos = self.display_position()
        if self.lyrics.has_synced:
            return float(self.lyrics.index_at(pos))
        n = len(self.lyrics.plain_lines())
        return float(int(pos / self.st.length_s * n)) if self.st.length_s > 0 and n else 0.0

    # ------------------------------------------------------------------ drawing
    def on_draw(self, widget, cr):
        s = self.cfg["scale"]
        W, H = SIZES[self.cfg["mode"]]
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.scale(s, s)
        self.hits = []
        self.draw_background(cr, W, H)
        if self.st is None:
            self.draw_idle(cr, W, H)
        elif self.cfg["mode"] == "lyrics":
            self.draw_lyrics_view(cr, W, H)
        else:
            self.draw_compact(cr, W, H)
        return False

    def draw_background(self, cr, W, H):
        a = self.cfg["opacity"]
        pat = cairo.LinearGradient(0, 0, 0, H)
        pat.add_color_stop_rgba(0, self.cur_top[0] / 255, self.cur_top[1] / 255, self.cur_top[2] / 255, a)
        pat.add_color_stop_rgba(1, self.cur_bot[0] / 255, self.cur_bot[1] / 255, self.cur_bot[2] / 255, a)
        rrect(cr, 0.5, 0.5, W - 1, H - 1, 18)
        cr.set_source(pat)
        cr.fill_preserve()
        rgba(cr, WHITE, 0.12)
        cr.set_line_width(1)
        cr.stroke()

    def draw_idle(self, cr, W, H):
        text(cr, "Nothing playing", W / 2, H / 2 - 18, 16, WHITE, 0.9, bold=True, align="center")
        text(cr, "Open Spotify and press play", W / 2, H / 2 + 6, 12, MUTED, align="center")
        self.close_button(cr, W - 20, 20)

    def draw_art(self, cr, x, y, size):
        s = self.cfg["scale"]
        cr.save()
        rrect(cr, x, y, size, size, size * 0.17)
        pb = self.art_pixbuf(size)
        if pb is not None:
            cr.clip()
            cr.translate(x, y)
            cr.scale(1.0 / s, 1.0 / s)
            Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
            cr.paint()
        else:
            rgba(cr, (58, 58, 64), 1.0)
            cr.fill()
            text(cr, "♪", x + size / 2, y + size / 2 - size * 0.2, size * 0.4, WHITE, 0.5, align="center")
        cr.restore()

    def art_pixbuf(self, size):
        if self.art_orig is None:
            return None
        px = max(8, int(round(size * self.cfg["scale"])))
        pb = self.art_cache.get(px)
        if pb is None:
            pb = self.art_orig.scale_simple(px, px, GdkPixbuf.InterpType.HYPER)
            self.art_cache[px] = pb
        return pb

    def add_hit(self, x, y, w, h, name):
        self.hits.append((x, y, w, h, name))

    def close_button(self, cr, cx, cy):
        hot = self.hover == "hide"
        icon_close(cr, cx, cy, 4.5, WHITE, 0.95 if hot else 0.5)
        self.add_hit(cx - 12, cy - 12, 24, 24, "hide")

    def round_button(self, cr, name, kind, cx, cy, r, enabled=True):
        hot = self.hover == name
        a = (0.95 if hot else 0.8) if enabled else 0.35
        if kind == "play":
            rgba(cr, WHITE, 0.98 if hot else 0.9)
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.fill()
            if self.st and self.st.playing:
                icon_pause(cr, cx, cy, r * 0.42, (30, 30, 34), 1.0)
            else:
                icon_play(cr, cx + r * 0.05, cy, r * 0.42, (30, 30, 34), 1.0)
        else:
            if hot and enabled:
                rgba(cr, WHITE, 0.16)
                cr.arc(cx, cy, r, 0, 2 * math.pi)
                cr.fill()
            if kind == "prev":
                icon_prev(cr, cx, cy, r * 0.5, WHITE, a)
            elif kind == "next":
                icon_next(cr, cx, cy, r * 0.5, WHITE, a)
            elif kind == "like":
                if self.liked:
                    icon_heart(cr, cx, cy, r * 0.5, GREEN, 1.0, True)
                else:
                    icon_heart(cr, cx, cy, r * 0.5, WHITE, a if self.liked is not None or auth.has_token() else 0.45, False)
        self.add_hit(cx - r - 3, cy - r - 3, 2 * r + 6, 2 * r + 6, name)

    def pill_button(self, cr, name, label, x, y, w, h):
        hot = self.hover == name
        rrect(cr, x, y, w, h, h / 2)
        rgba(cr, WHITE, 0.24 if hot else 0.12)
        cr.fill()
        text(cr, label, x + w / 2, y + 5, 12, WHITE, 0.95, align="center")
        self.add_hit(x, y, w, h, name)

    def draw_title_block(self, cr, x, y, maxw, tsize, asize):
        st = self.st
        text(cr, st.title or "Unknown", x, y, tsize, WHITE, 1.0, bold=True, maxw=maxw)
        if self.toast and time.monotonic() < self.toast[2]:
            text(cr, self.toast[0], x, y + tsize + 5, asize, self.toast[1], 1.0, maxw=maxw)
        else:
            text(cr, st.artist, x, y + tsize + 5, asize, MUTED, 1.0, maxw=maxw)

    def draw_seekbar(self, cr, x, y, w, pos, length, label_y):
        h = 5.0
        frac = max(0.0, min(1.0, pos / length)) if length > 0 else 0.0
        rrect(cr, x, y, w, h, h / 2)
        rgba(cr, WHITE, 0.22)
        cr.fill()
        if frac > 0:
            rrect(cr, x, y, max(h, w * frac), h, h / 2)
            rgba(cr, WHITE, 0.95)
            cr.fill()
        if length > 0 and (self.seek_drag or self.hover == "seekbar"):
            cr.arc(x + w * frac, y + h / 2, 6.5, 0, 2 * math.pi)
            rgba(cr, WHITE, 1.0)
            cr.fill()
        self.add_hit(x - 6, y - 9, w + 12, h + 18, "seekbar")
        self.bar_geom = (x, w)
        text(cr, fmt_time(pos), x, label_y, 11, WHITE if self.seek_drag else MUTED, bold=self.seek_drag)
        if length > 0:
            # int - int so elapsed + remaining always equals the displayed total (0:19 + 1:33 = 1:52)
            text(cr, "-" + fmt_time(max(0, int(length) - int(pos))), x + w, label_y, 11, MUTED, align="right")

    def draw_controls(self, cr, cx0, cy, spacing):
        st = self.st
        self.round_button(cr, "prev", "prev", cx0, cy, 14)
        self.round_button(cr, "play", "play", cx0 + spacing, cy, 17)
        self.round_button(cr, "next", "next", cx0 + 2 * spacing, cy, 14)
        self.round_button(cr, "like", "like", cx0 + 3 * spacing + 10, cy, 14, enabled=bool(st and st.track_uri))

    def draw_compact(self, cr, W, H):
        st, pos = self.st, self.display_position()
        self.draw_art(cr, 12, 12, 88)
        tx = 112
        self.draw_title_block(cr, tx, 13, W - tx - 34, 16, 13)
        self.close_button(cr, W - 20, 20)
        self.draw_controls(cr, tx + 22, 66, 38)
        self.pill_button(cr, "mode", "Lyrics", W - 14 - 58, 54, 58, 24)
        self.draw_seekbar(cr, tx, 87, W - 14 - tx, pos, st.length_s, 94)

    def draw_lyrics_view(self, cr, W, H):
        st, pos = self.st, self.display_position()
        self.draw_art(cr, 14, 14, 56)
        tx = 84
        self.draw_title_block(cr, tx, 14, W - tx - 38, 15, 12)
        self.close_button(cr, W - 20, 20)
        self.draw_controls(cr, 100, 84, 38)
        self.pill_button(cr, "mode", "Card", W - 14 - 58, 72, 58, 24)
        self.draw_lyrics_area(cr, 14, 110, W - 28, 92, pos)
        self.draw_seekbar(cr, 14, 213, W - 28, pos, st.length_s, 223)

    def draw_lyrics_area(self, cr, x, y, w, h, pos):
        state = self.lyrics_state
        if state != "ok":
            msg = {"loading": "Loading lyrics…", "none": "No lyrics found for this song",
                   "instrumental": "♪ Instrumental", "off": "Lyrics are off (right-click → Lyrics)",
                   "error": "Couldn't reach the lyrics service", "idle": ""}[state]
            text(cr, msg, x + w / 2, y + h / 2 - 8, 14, MUTED, 0.9, align="center", maxw=w)
            return
        lines = self.lyric_lines()
        cr.save()
        cr.rectangle(x, y, w, h)
        cr.clip()
        base = self.lyric_pos
        for i in range(int(math.floor(base)) - 1, int(math.floor(base)) + 4):
            if i < 0 or i >= len(lines):
                continue
            d = abs(i - base)
            a = max(0.25, 1.0 - 0.55 * d)
            size = 14 + 3 * max(0.0, 1.0 - d)
            ly = y + 4 + (i - base + 1) * 23
            if ly < y - 20 or ly > y + h:
                continue
            text(cr, lines[i] or "♪", x + w / 2, ly, size, WHITE, a, bold=d < 0.5, maxw=w, align="center")
        cr.restore()

    # ------------------------------------------------------------------ input
    def logical(self, ev):
        s = self.cfg["scale"]
        return ev.x / s, ev.y / s

    def hit_at(self, x, y):
        for bx, by, bw, bh, name in reversed(self.hits):
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return name
        return None

    def on_press(self, widget, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if ev.button == 3:
            self.show_menu(ev)
            return True
        if ev.button != 1:
            return False
        x, y = self.logical(ev)
        name = self.hit_at(x, y)
        if name == "seekbar":
            if self.st and self.st.length_s > 0 and self.st.can_seek and not self.st.is_ad:
                self.seek_drag = True
                self.update_seek(x)
        elif name:
            self.pressed = name
            self.area.queue_draw()
        else:
            wx, wy = self.win.get_position()
            self.drag = (ev.x_root, ev.y_root, wx, wy)
        return True

    def on_motion(self, widget, ev):
        if self.drag:
            rx, ry, wx, wy = self.drag
            self.win.move(int(wx + ev.x_root - rx), int(wy + ev.y_root - ry))
            return True
        x, y = self.logical(ev)
        if self.seek_drag:
            self.update_seek(x)
            return True
        name = self.hit_at(x, y)
        if name != self.hover:
            self.hover = name
            self.area.queue_draw()
        return False

    def on_release(self, widget, ev):
        if ev.button != 1:
            return False
        if self.drag:
            self.drag = None
            self.save_position()
            return True
        if self.seek_drag:
            self.seek_drag = False
            self.commit_seek(self.seek_preview)
            return True
        x, y = self.logical(ev)
        pressed, self.pressed = self.pressed, None
        if pressed and self.hit_at(x, y) == pressed:
            self.activate(pressed)
        self.area.queue_draw()
        return True

    def on_leave(self, widget, ev):
        if self.hover is not None:
            self.hover = None
            self.area.queue_draw()
        return False

    def on_scroll(self, widget, ev):
        x, y = self.logical(ev)
        if self.hit_at(x, y) == "seekbar" and self.st and self.st.length_s > 0:
            step = 5 if ev.direction == Gdk.ScrollDirection.UP else -5
            self.commit_seek(self.display_position() + step)
            return True
        return False

    def update_seek(self, x):
        bx, bw = self.bar_geom
        self.seek_preview = max(0.0, min(1.0, (x - bx) / bw)) * self.st.length_s
        self.area.queue_draw()

    def commit_seek(self, target):
        st = self.st
        if not st:
            return
        target = max(0.0, min(target, max(0.0, st.length_s - 0.5)))
        try:
            ok = self.mpris.set_position(target, st)
        except GLib.Error:
            ok = False
        if ok:
            st.position_s, self.st_time = target, time.monotonic()     # optimistic: bar stays where you dropped it
            self.poll_soon(400)
        else:
            self.show_toast("This player doesn't allow seeking right now", RED)
        self.area.queue_draw()

    # ------------------------------------------------------------------ actions
    def command(self, method):
        try:
            self.mpris.command(method)
        except GLib.Error:
            pass
        self.poll_soon(250 if method == "PlayPause" else 400)

    def activate(self, name):
        if name == "prev":
            self.command("Previous")
        elif name == "next":
            self.command("Next")
        elif name == "play":
            if self.st:                                   # optimistic icon flip
                self.st.position_s = self.display_position()
                self.st.status, self.st_time = ("Paused" if self.st.playing else "Playing"), time.monotonic()
            self.command("PlayPause")
        elif name == "like":
            self.toggle_like()
        elif name == "mode":
            self.toggle_mode()
        elif name == "hide":
            self.hide_user()
        self.area.queue_draw()

    def show_toast(self, msg, color=GREEN, secs=2.6):
        self.toast = (msg, color, time.monotonic() + secs)
        GLib.timeout_add(int(secs * 1000) + 50, lambda: (self.area.queue_draw(), False)[1])
        self.area.queue_draw()

    def toggle_like(self):
        st = self.st
        if not st or not st.track_uri:
            self.show_toast("Can't like this item", RED)
            return
        if not auth.has_token():
            self.show_toast("Not signed in - run: spoty login", RED, 4.0)
            return
        key, uri, cid = st.key, st.track_uri, self.cfg["client_id"]
        want = not bool(self.liked)
        self.liked = want

        def work():
            (webapi.save if want else webapi.remove)(uri, cid)
            return want

        def done(res, err):
            if key != self.cur_key():
                return
            if err is not None:
                self.liked = not want
                self.show_toast(str(err)[:70], RED, 4.5)
            else:
                self.show_toast("Saved to Liked Songs" if want else "Removed from Liked Songs", GREEN)

        self.bg(work, done)

    def set_mode(self, mode):
        self.cfg["mode"] = mode
        config.save(self.cfg)
        self.apply_size()
        if mode == "lyrics":
            self.request_lyrics()
        self.lyric_pos = self.lyric_target()
        self.ensure_timers()
        self.area.queue_draw()

    def toggle_mode(self):
        self.set_mode("lyrics" if self.cfg["mode"] == "compact" else "compact")

    def set_option(self, key, value):
        self.cfg[key] = value
        config.save(self.cfg)
        self.apply_size()
        if key == "lyrics" and value:
            self.lyrics_state = "idle" if self.lyrics_state == "off" else self.lyrics_state
            self.request_lyrics()
        elif key == "lyrics":
            self.lyrics_state = "off"
        self.area.queue_draw()

    def show_overlay(self):
        self.user_hidden = False
        self.poll_once()
        self.update_visibility()
        self.ensure_timers()

    def hide_user(self):
        """Hidden by the user: window unmapped and ALL timers stopped (zero CPU until shown again)."""
        self.user_hidden = True
        self.win.hide()
        self.ensure_timers()

    def toggle_visible(self):
        if self.user_hidden or not self.win.get_visible():
            self.show_overlay()
        else:
            self.hide_user()

    def show_menu(self, ev):
        m = Gtk.Menu()

        def item(label, cb, menu=m):
            it = Gtk.MenuItem.new_with_label(label)
            it.connect("activate", lambda *_: cb())
            menu.append(it)
            return it

        item("Switch to Card view" if self.cfg["mode"] == "lyrics" else "Switch to Lyrics view", self.toggle_mode)
        item("Lyrics: " + ("ON (click to turn off)" if self.cfg["lyrics"] else "OFF (click to turn on)"),
             lambda: self.set_option("lyrics", not self.cfg["lyrics"]))
        for title, key, values in (("Size", "scale", (0.8, 1.0, 1.25, 1.5, 2.0)),
                                   ("Opacity", "opacity", (0.5, 0.7, 0.88, 1.0))):
            sub = Gtk.Menu()
            for v in values:
                item(f"{v:g}" + ("x" if key == "scale" else ""), lambda v=v, k=key: self.set_option(k, v), sub)
            parent = Gtk.MenuItem.new_with_label(title)
            parent.set_submenu(sub)
            m.append(parent)
        m.append(Gtk.SeparatorMenuItem())
        item("Hide overlay", self.hide_user)
        item("Quit overlay (frees all resources)", self.app.quit)
        m.show_all()
        m.popup_at_pointer(ev)


# ------------------------------------------------------------------------------------------- application
class OverlayApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.ov = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        for name, fn in (("toggle", lambda: self.ov.toggle_visible()), ("show", lambda: self.ov.show_overlay()),
                         ("hide", lambda: self.ov.hide_user()), ("mode-toggle", lambda: self.ov.toggle_mode()),
                         ("mode-compact", lambda: self.ov.set_mode("compact")),
                         ("mode-lyrics", lambda: self.ov.set_mode("lyrics")),
                         ("like", lambda: self.ov.toggle_like()), ("quit", lambda: self.quit())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda a, p, fn=fn: fn())
            self.add_action(act)
        self.hold()                      # keep running even while the window is hidden

    def do_activate(self):
        if self.ov is None:
            self.ov = Overlay(self)
        self.ov.show_overlay()

    def do_shutdown(self):
        if self.ov is not None:
            config.save(self.ov.cfg)
        Gtk.Application.do_shutdown(self)


def run_overlay() -> int:
    app = OverlayApp()
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (app.quit(), False)[1])
    return app.run([])
