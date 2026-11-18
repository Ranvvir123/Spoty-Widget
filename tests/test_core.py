import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="spoty-test-")
os.environ["XDG_CONFIG_HOME"] = TMP + "/cfg"
os.environ["XDG_CACHE_HOME"] = TMP + "/cache"

from spoty import auth, config, keys, lrc, palette, webapi  # noqa: E402
from spoty.mpris import Mpris, fmt_time, parse_time, spotify_uri  # noqa: E402


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class Pure(unittest.TestCase):
    def test_lrc_parse_and_lookup(self):
        text = "[ar:x]\n[offset:+500]\n[00:10.00]First\n[00:05.50][01:00.00]Chorus\n[00:20.00]\n"
        L = lrc.parse_lrc(text)
        self.assertEqual([t for t, _ in L], [5.0, 9.5, 19.5, 59.5])   # offset +500ms shifts earlier, sorted
        ly = lrc.Lyrics(synced=L)
        self.assertEqual(ly.index_at(0), -1)
        self.assertEqual(ly.index_at(5.0), 0)
        self.assertEqual(ly.index_at(10.0), 1)
        self.assertEqual(L[ly.index_at(60)][1], "Chorus")
        self.assertEqual(L[2][1], "")                                 # instrumental gap line

    def test_pkce_rfc7636_vector(self):
        self.assertEqual(auth.challenge_for("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
                         "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
        v = auth.make_verifier()
        self.assertTrue(43 <= len(v) <= 128)

    def test_time_helpers(self):
        self.assertEqual(parse_time("1:23"), 83)
        self.assertEqual(parse_time("83"), 83)
        self.assertEqual(parse_time("1:02:03"), 3723)
        self.assertEqual(fmt_time(19), "0:19")
        self.assertEqual(fmt_time(93.9), "1:33")
        self.assertEqual(fmt_time(3723), "1:02:03")
        with self.assertRaises(ValueError):
            parse_time("a:b")

    def test_spotify_uri(self):
        self.assertEqual(spotify_uri("/com/spotify/track/abc123"), "spotify:track:abc123")
        self.assertEqual(spotify_uri("", "https://open.spotify.com/track/XyZ9?si=1"), "spotify:track:XyZ9")
        self.assertIsNone(spotify_uri("/com/spotify/ad/1"))

    def test_palette(self):
        px = [(200, 30, 60)] * 700 + [(30, 80, 200)] * 300
        c1, c2 = palette.dominant_colors(px)
        self.assertTrue(c1[0] > 150 and c1[2] < 90, c1)         # red first
        self.assertTrue(c2[2] > 150 and c2[0] < 90, c2)         # blue second
        t = palette.tone((255, 255, 255))
        self.assertLess(0.2126 * t[0] + 0.7152 * t[1] + 0.0722 * t[2], 0.31 * 255)
        self.assertEqual(palette.dominant_colors([]), palette.DEFAULT)

    def test_config_sanitize(self):
        c = config.sanitize({"scale": 99, "opacity": "x", "mode": "weird", "x": "no", "fps": 1000, "bogus": 1})
        self.assertEqual((c["scale"], c["opacity"], c["mode"], c["x"], c["fps"]), (2.0, 0.88, "compact", None, 60))
        self.assertNotIn("bogus", c)
        config.save({"scale": 1.5})
        self.assertEqual(config.load()["scale"], 1.5)

    def test_keys_install_remove(self):
        calls, state = [], {"list": "['__dummy__', 'custom0']"}

        def run(cmd):
            calls.append(cmd)
            if cmd[1] == "get":
                return state["list"] + "\n"
            if cmd[1] == "set" and cmd[3] == "custom-list":
                state["list"] = cmd[4]
            return ""

        lines = keys.install(run, binary="/x/spoty")
        self.assertEqual(len(lines), len(keys.DEFAULTS))
        ids = eval(state["list"])
        self.assertEqual(ids[:2], ["__dummy__", "custom0"])
        self.assertIn("spoty-back10", ids)
        self.assertTrue(any(c[-1] == "/x/spoty seek -10" for c in calls))
        self.assertTrue(any(c[-1] == "['<Primary><Shift><Alt>l']" for c in calls))
        self.assertEqual(keys.remove(run), len(keys.DEFAULTS))
        self.assertEqual(eval(state["list"]), ["__dummy__", "custom0"])


class Handler(http.server.BaseHTTPRequestHandler):
    """Fake accounts.spotify.com + api.spotify.com + lrclib in one."""
    saved = set()
    legacy_only = False
    forbid = False
    expired_token = "old"
    tokens_issued = 0
    challenge = ""
    seen = []
    lrclib_calls = []

    def _send(self, code, obj=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
        Handler.seen.append(("POST", self.path, form))
        if form["grant_type"] == "authorization_code":
            ok = form["code"] == "thecode" and auth.challenge_for(form["code_verifier"]) == Handler.challenge
            if not ok:
                return self._send(400, {"error": "invalid_grant", "error_description": "bad verifier"})
            Handler.tokens_issued += 1
            return self._send(200, {"access_token": "tok1", "refresh_token": "ref1", "expires_in": 3600, "scope": "x"})
        if form["grant_type"] == "refresh_token":
            Handler.tokens_issued += 1
            return self._send(200, {"access_token": "tok%d" % (Handler.tokens_issued + 1), "expires_in": 3600})
        self._send(400, {"error": "unsupported"})

    def _api(self, method):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        Handler.seen.append((method, u.path, q, self.headers.get("Authorization")))
        if self.headers.get("Authorization") == "Bearer " + Handler.expired_token:
            return self._send(401, {"error": {"status": 401}})
        if Handler.forbid:
            return self._send(403, {"error": {"status": 403}})
        legacy = u.path.startswith("/v1/me/tracks")
        if Handler.legacy_only and not legacy:
            return self._send(404)
        ident = (q.get("uris") or "spotify:track:" + q.get("ids", "")) if not legacy else "spotify:track:" + q["ids"]
        if u.path.endswith("/contains"):
            return self._send(200, [ident in Handler.saved])
        (Handler.saved.add if method == "PUT" else Handler.saved.discard)(ident)
        self._send(200)

    def do_PUT(self): self._api("PUT")
    def do_DELETE(self): self._api("DELETE")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/api/"):
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            Handler.lrclib_calls.append((u.path, q, self.headers.get("User-Agent")))
            if u.path == "/api/get":
                return self._send(404)
            if q["track_name"] == "Nothing":
                return self._send(200, [])
            return self._send(200, [{"duration": 300, "syncedLyrics": "[00:01.00]wrong length"},
                                    {"duration": 112, "plainLyrics": "plain", "syncedLyrics": "[00:02.00]Hello\n[00:04.00]World"}])
        self._api("GET")

    def log_message(self, *a):
        pass


class Network(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.port}"
        auth.TOKEN_URL = base + "/api/token"
        auth.AUTH_URL = base + "/authorize"
        webapi.API_BASE = base + "/v1"
        cls.base = base

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Handler.saved, Handler.legacy_only, Handler.forbid, Handler.seen = set(), False, False, []
        Handler.tokens_issued = 0
        auth.logout()

    def test_full_login_pkce_then_like_roundtrip(self):
        auth.REDIRECT_PORT = 18898
        auth.REDIRECT_URI = "http://127.0.0.1:18898/callback"
        urls = []
        result = {}

        def do_login():
            try:
                result["tok"] = auth.login("cid123", open_browser=False, timeout=20, announce=urls.append)
            except Exception as e:
                result["err"] = e

        t = threading.Thread(target=do_login)
        t.start()
        self.assertTrue(wait_for(lambda: urls))
        link = urls[0].split("visit:\n  ")[1]
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(link).query).items()}
        self.assertEqual(q["code_challenge_method"], "S256")
        self.assertEqual(q["redirect_uri"], "http://127.0.0.1:18898/callback")
        self.assertEqual(q["client_id"], "cid123")
        self.assertNotIn("client_secret", q)
        Handler.challenge = q["code_challenge"]
        # simulate the browser coming back (wrong state first -> must be rejected? no: one callback only), use right state
        urllib.request.urlopen(f"http://127.0.0.1:18898/callback?code=thecode&state={q['state']}", timeout=5).read()
        t.join(10)
        self.assertNotIn("err", result, result.get("err"))
        self.assertEqual(oct(os.stat(auth.token_path()).st_mode & 0o777), "0o600")
        self.assertTrue(auth.has_token())

        uri = "spotify:track:6rqhFgbbKwnb9MLmUQDhG6"
        self.assertFalse(webapi.is_saved(uri))
        webapi.save(uri)
        self.assertTrue(webapi.is_saved(uri))
        self.assertTrue(any(s[0] == "PUT" and s[1] == "/v1/me/library" and s[2] == {"uris": uri} for s in Handler.seen))
        webapi.remove(uri)
        self.assertFalse(webapi.is_saved(uri))

    def test_state_mismatch_rejected(self):
        auth.REDIRECT_PORT = 18899
        auth.REDIRECT_URI = "http://127.0.0.1:18899/callback"
        urls, result = [], {}

        def do_login():
            try:
                auth.login("cid", open_browser=False, timeout=20, announce=urls.append)
            except Exception as e:
                result["err"] = e

        t = threading.Thread(target=do_login)
        t.start()
        self.assertTrue(wait_for(lambda: urls))
        urllib.request.urlopen("http://127.0.0.1:18899/callback?code=thecode&state=EVIL", timeout=5).read()
        t.join(10)
        self.assertIn("state mismatch", str(result.get("err")))
        self.assertFalse(auth.has_token())

    def _login_with(self, access):
        auth.save_token({"access_token": access, "refresh_token": "ref1", "expires_at": time.time() + 3600, "client_id": "cid"})

    def test_expired_token_refreshes_once_and_retries(self):
        Handler.expired_token = "stale"
        self._login_with("stale")
        self.assertFalse(webapi.is_saved("spotify:track:abc"))
        self.assertEqual(sum(1 for s in Handler.seen if s[0] == "POST"), 1)       # exactly one refresh
        self.assertNotEqual(auth.load_token()["access_token"], "stale")
        Handler.expired_token = "old"

    def test_legacy_fallback_when_library_endpoint_missing(self):
        Handler.legacy_only = True
        self._login_with("good")
        webapi.save("spotify:track:abc")
        self.assertTrue(webapi.is_saved("spotify:track:abc"))
        self.assertTrue(any(s[1] == "/v1/me/tracks" for s in Handler.seen))

    def test_403_gives_actionable_message(self):
        Handler.forbid = True
        self._login_with("good")
        with self.assertRaises(webapi.ApiError) as cm:
            webapi.save("spotify:track:abc")
        self.assertIn("Premium", str(cm.exception))

    def test_not_logged_in(self):
        with self.assertRaises(auth.NotLoggedIn):
            webapi.is_saved("spotify:track:abc")

    def test_lyrics_search_fallback_cache_and_negative_cache(self):
        os.environ["SPOTY_LRCLIB_URL"] = self.base
        Handler.lrclib_calls = []
        c = lrc.LyricsClient()
        ly = c.fetch("Blinding Lights", "The Weeknd", "After Hours", 112)
        self.assertTrue(ly.has_synced)
        self.assertEqual(ly.synced[0][1], "Hello")                    # the 300 s result was filtered out
        self.assertTrue(Handler.lrclib_calls[0][2].startswith("spoty-overlay/"))
        n = len(Handler.lrclib_calls)
        c.fetch("Blinding Lights", "The Weeknd", "After Hours", 112)
        self.assertEqual(len(Handler.lrclib_calls), n)               # served from cache
        self.assertIsNone(c.fetch("Nothing", "Nobody", "", 100))
        n = len(Handler.lrclib_calls)
        self.assertIsNone(c.fetch("Nothing", "Nobody", "", 100))
        self.assertEqual(len(Handler.lrclib_calls), n)               # "not found" cached too
        os.environ.pop("SPOTY_LRCLIB_URL")


class DBus(unittest.TestCase):
    def _spawn(self, script, env_extra=None):
        env = dict(os.environ, FAKE_LOG=self.log, **(env_extra or {}))
        p = subprocess.Popen([sys.executable, str(ROOT / "tests" / script)], stdout=subprocess.PIPE, env=env, text=True)
        self.addCleanup(p.kill)
        return p

    def setUp(self):
        self.log = tempfile.mktemp(prefix="fakelog-")
        Path(self.log).write_text("")

    def calls(self):
        return [json.loads(l) for l in Path(self.log).read_text().splitlines() if l]

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "spoty", *args], cwd=ROOT, capture_output=True, text=True, timeout=20)

    def test_mpris_read_control_and_seek(self):
        p = self._spawn("fake_player.py")
        self.assertEqual(p.stdout.readline().strip(), "ready")
        m = Mpris()
        st = m.state()
        self.assertEqual((st.title, st.artist, st.album, st.status), ("Blinding Lights", "The Weeknd", "After Hours", "Playing"))
        self.assertEqual(st.track_uri, "spotify:track:6rqhFgbbKwnb9MLmUQDhG6")
        self.assertAlmostEqual(st.length_s, 112, places=1)
        self.assertTrue(18.5 < st.position_s < 21)

        self.assertTrue(m.command("PlayPause"))
        self.assertEqual(m.state().status, "Paused")
        new = m.seek_by(+10)
        self.assertTrue(28.5 < new < 31)
        self.assertTrue(28.5 < m.state().position_s < 31)
        m.seek_by(-10)
        self.assertTrue(18 < m.state().position_s < 21)
        m.set_position(83)
        self.assertAlmostEqual(m.state().position_s, 83, delta=0.2)
        m.set_position(10_000)                                  # clamps just before the end
        self.assertAlmostEqual(m.state().position_s, 111.5, delta=0.2)
        m.seek_by(-1000)                                        # clamps at 0
        self.assertAlmostEqual(m.state().position_s, 0, delta=0.2)
        names = [c[0] for c in self.calls()]
        self.assertIn("SetPosition", names)
        self.assertEqual([c for c in self.calls() if c[0] == "SetPosition"][0][1][0], "/com/spotify/track/6rqhFgbbKwnb9MLmUQDhG6")

    def test_cli_commands(self):
        p = self._spawn("fake_player.py")
        p.stdout.readline()
        r = self.cli("status")
        self.assertIn("Blinding Lights — The Weeknd", r.stdout, r.stderr)
        self.assertEqual(self.cli("next").returncode, 0)
        self.assertEqual(self.cli("prev").returncode, 0)
        self.assertEqual(self.cli("pause").returncode, 0)
        r = self.cli("seek", "1:23")
        self.assertIn("→ 1:23", r.stdout, r.stderr)
        r = self.cli("seek", "+10")
        self.assertIn("→ 1:33", r.stdout, r.stderr)
        r = self.cli("seek", "-10")
        self.assertIn("→ 1:23", r.stdout)
        self.assertEqual(self.cli("seek", "zzz").returncode, 2)
        names = [c[0] for c in self.calls()]
        self.assertEqual(names[:3], ["Next", "Previous", "Pause"])
        j = json.loads(self.cli("status", "--json").stdout)
        self.assertEqual(j["title"], "Blinding Lights")

    def test_no_player_is_graceful(self):
        r = self.cli("status")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not running", r.stderr)
        self.assertEqual(self.cli("next").returncode, 1)
        self.assertEqual(self.cli("seek", "+10").returncode, 1)

    def test_ads_cannot_be_seeked(self):
        p = self._spawn("fake_player.py", {"FAKE_AD": "1"})
        p.stdout.readline()
        r = self.cli("seek", "+10")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("SetPosition", [c[0] for c in self.calls()])
        self.assertIn("not a Spotify track", self.cli("like").stderr)

    def test_overlay_ipc_actions_and_quit(self):
        from spoty import ipc
        self.assertFalse(ipc.running())
        self.assertEqual(self.cli("quit").returncode, 0)        # nothing running: harmless
        p = self._spawn("fake_overlay.py")
        self.assertTrue(wait_for(ipc.running))
        for cmd in (["toggle"], ["show"], ["hide"], ["mode", "lyrics"], ["mode", "compact"], ["mode", "toggle"], ["quit"]):
            self.assertEqual(self.cli(*cmd).returncode, 0, cmd)
        self.assertTrue(wait_for(lambda: p.poll() is not None))   # quit really terminates the process
        got = Path(self.log).read_text().split()
        self.assertEqual(got, ["toggle", "show", "hide", "mode-lyrics", "mode-compact", "mode-toggle", "quit"])
        self.assertFalse(ipc.running())


if __name__ == "__main__":
    unittest.main(verbosity=2)
