"""Liked Songs via the official Spotify Web API (needs `spoty login`).

Uses the library endpoints introduced in Feb 2026 (PUT/DELETE /me/library, GET /me/library/contains) and
falls back to the legacy /me/tracks endpoints if Spotify answers 404/405/410 for the new ones.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from . import auth

API_BASE = os.environ.get("SPOTY_API_BASE", "https://api.spotify.com/v1")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


HINTS = {
    403: "Spotify refused (403). Dev-mode apps need the app OWNER to have Premium, and your account must be "
         "added under 'User Management' in the developer dashboard.",
    429: "Rate limited by Spotify; try again in a moment.",
}


def _request(method: str, path: str, params: Optional[dict] = None, client_id: str = ""):
    url = API_BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in (0, 1):
        token = auth.get_access_token(client_id, force_refresh=bool(attempt))
        req = urllib.request.Request(url, method=method, headers={"Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read().decode() or "null"
                return r.status, json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 0:
                continue                       # expired token: refresh once and retry
            if e.code == 429:
                time.sleep(min(float(e.headers.get("Retry-After", "1") or 1), 5))
            return e.code, None
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(0, f"Network error: {e}") from e
    return 401, None


def _id(uri: str) -> str:
    return uri.rsplit(":", 1)[-1]


def _check(status: int, what: str):
    if status >= 400:
        raise ApiError(status, HINTS.get(status, f"Spotify error {status} while {what}"))


def is_saved(uri: str, client_id: str = "") -> bool:
    status, data = _request("GET", "/me/library/contains", {"uris": uri}, client_id)
    if status in (404, 405, 410):
        status, data = _request("GET", "/me/tracks/contains", {"ids": _id(uri)}, client_id)
    _check(status, "checking Liked Songs")
    return bool(data and data[0])


def _write(method: str, uri: str, client_id: str, what: str):
    status, _ = _request(method, "/me/library", {"uris": uri}, client_id)
    if status in (404, 405, 410):
        status, _ = _request(method, "/me/tracks", {"ids": _id(uri)}, client_id)
    _check(status, what)


def save(uri: str, client_id: str = "") -> None:
    _write("PUT", uri, client_id, "saving to Liked Songs")


def remove(uri: str, client_id: str = "") -> None:
    _write("DELETE", uri, client_id, "removing from Liked Songs")
