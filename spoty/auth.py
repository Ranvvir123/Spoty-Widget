"""Spotify login via OAuth 2.0 Authorization Code + PKCE (official flow, no client secret, no password).

You log in once in your own browser; only a token is stored (~/.config/spoty/token.json, mode 600).
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Optional

from .config import config_dir

AUTH_URL = os.environ.get("SPOTY_AUTH_URL", "https://accounts.spotify.com/authorize")
TOKEN_URL = os.environ.get("SPOTY_TOKEN_URL", "https://accounts.spotify.com/api/token")
REDIRECT_PORT = int(os.environ.get("SPOTY_REDIRECT_PORT", "8898"))
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"      # register EXACTLY this in the dashboard
SCOPES = "user-library-read user-library-modify"


class AuthError(Exception):
    pass


class NotLoggedIn(AuthError):
    pass


def token_path():
    return config_dir() / "token.json"


# -- PKCE helpers ------------------------------------------------------------------------
def make_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()[:96]


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def build_authorize_url(client_id: str, challenge: str, state: str) -> str:
    q = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT_URI,
         "code_challenge_method": "S256", "code_challenge": challenge, "scope": SCOPES, "state": state}
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


# -- token storage -----------------------------------------------------------------------
def load_token() -> Optional[dict]:
    try:
        return json.loads(token_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_token(tok: dict) -> None:
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(tok, f)
    os.replace(tmp, p)
    os.chmod(p, 0o600)


def logout() -> bool:
    try:
        token_path().unlink()
        return True
    except OSError:
        return False


def has_token() -> bool:
    t = load_token()
    return bool(t and t.get("refresh_token"))


# -- token endpoint ----------------------------------------------------------------------
def _post_token(form: dict) -> dict:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            msg = body.get("error_description") or body.get("error") or str(e)
        except Exception:
            msg = str(e)
        raise AuthError(f"Spotify token endpoint: {msg}") from e
    except (urllib.error.URLError, OSError) as e:
        raise AuthError(f"Network error: {e}") from e


def _store(resp: dict, client_id: str, old_refresh: str = "") -> dict:
    tok = {"access_token": resp["access_token"],
           "refresh_token": resp.get("refresh_token") or old_refresh,
           "expires_at": time.time() + int(resp.get("expires_in", 3600)),
           "scope": resp.get("scope", ""), "client_id": client_id}
    save_token(tok)
    return tok


def get_access_token(client_id: str = "", force_refresh: bool = False) -> str:
    tok = load_token()
    if not tok or not tok.get("refresh_token"):
        raise NotLoggedIn("Not signed in. Run: spoty login")
    if not force_refresh and tok.get("access_token") and tok.get("expires_at", 0) - time.time() > 60:
        return tok["access_token"]
    cid = client_id or tok.get("client_id") or ""
    if not cid:
        raise NotLoggedIn("No client_id configured. Run: spoty login --client-id <id>")
    try:
        resp = _post_token({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid})
    except AuthError as e:
        if "invalid_grant" in str(e) or "revoked" in str(e).lower():
            logout()
            raise NotLoggedIn("Spotify session expired or was revoked. Run: spoty login") from e
        raise
    return _store(resp, cid, tok["refresh_token"])["access_token"]


# -- interactive login -------------------------------------------------------------------
class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/callback":
            self.send_response(404); self.end_headers(); return
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        self.server.result = q
        ok = "code" in q
        body = ("<html><body style='font-family:sans-serif;text-align:center;margin-top:15%'>"
                + ("<h2>Signed in to Spoty.</h2><p>You can close this tab.</p>" if ok
                   else "<h2>Sign-in failed.</h2><p>%s</p>" % (q.get("error", "unknown error")))
                + "</body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def login(client_id: str, open_browser: bool = True, timeout: float = 240.0, announce=print) -> dict:
    if not client_id:
        raise AuthError("No client_id. Create a Spotify app and run: spoty login --client-id <id>")
    verifier = make_verifier()
    state = secrets.token_urlsafe(16)
    url = build_authorize_url(client_id, challenge_for(verifier), state)
    try:
        server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), _Handler)
    except OSError as e:
        raise AuthError(f"Cannot listen on 127.0.0.1:{REDIRECT_PORT} ({e}). Is another login running?") from e
    server.result = None
    server.timeout = 0.5
    announce("Opening your browser to sign in to Spotify...\nIf nothing opens, visit:\n  " + url)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    deadline = time.time() + timeout
    try:
        while server.result is None and time.time() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    res = server.result
    if not res:
        raise AuthError("Timed out waiting for the browser sign-in.")
    if res.get("state") != state:
        raise AuthError("Sign-in state mismatch (possible tampering); aborted.")
    if "code" not in res:
        raise AuthError("Spotify denied the sign-in: " + res.get("error", "unknown"))
    resp = _post_token({"grant_type": "authorization_code", "code": res["code"], "redirect_uri": REDIRECT_URI,
                        "client_id": client_id, "code_verifier": verifier})
    return _store(resp, client_id)
