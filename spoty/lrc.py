"""Synced lyrics: LRC parsing, current-line lookup, and a cached LRCLIB client."""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import __version__
from .config import cache_dir

_TS = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
_OFFSET = re.compile(r"^\[offset:\s*([+-]?\d+)\s*\]", re.I | re.M)
NEGATIVE_TTL = 24 * 3600


@dataclass
class Lyrics:
    synced: List[Tuple[float, str]] = field(default_factory=list)
    plain: str = ""
    instrumental: bool = False

    @property
    def has_synced(self) -> bool:
        return bool(self.synced)

    @property
    def empty(self) -> bool:
        return not self.synced and not self.plain.strip() and not self.instrumental

    def index_at(self, t: float) -> int:
        """Index of the line being sung at time t, or -1 before the first line."""
        times = [x[0] for x in self.synced]
        return bisect.bisect_right(times, t) - 1

    def plain_lines(self) -> List[str]:
        return [ln for ln in self.plain.splitlines()]


def parse_lrc(text: str) -> List[Tuple[float, str]]:
    offset = 0.0
    m = _OFFSET.search(text or "")
    if m:
        offset = int(m.group(1)) / 1000.0     # LRC: positive offset = lyrics show earlier
    out: List[Tuple[float, str]] = []
    for raw in (text or "").splitlines():
        stamps = list(_TS.finditer(raw))
        if not stamps:
            continue
        lyric = raw[stamps[-1].end():].strip()
        for s in stamps:
            minutes, secs = int(s.group(1)), float(s.group(2).replace(":", "."))
            out.append((max(0.0, minutes * 60 + secs - offset), lyric))
    out.sort(key=lambda x: x[0])
    return out


def _from_json(d: dict) -> Lyrics:
    return Lyrics(synced=parse_lrc(d.get("syncedLyrics") or ""), plain=d.get("plainLyrics") or "",
                  instrumental=bool(d.get("instrumental")))


class LyricsClient:
    def __init__(self, base_url: str = "https://lrclib.net", timeout: float = 8.0):
        self.base = (os.environ.get("SPOTY_LRCLIB_URL") or base_url).rstrip("/")
        self.timeout = timeout
        self.dir = cache_dir() / "lyrics"

    def _cache_file(self, title, artist, album, duration):
        key = hashlib.sha1(f"{title}\0{artist}\0{album}\0{int(duration)}".encode()).hexdigest()
        return self.dir / f"{key}.json"

    def _get(self, path: str, params: dict):
        url = f"{self.base}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": f"spoty-overlay/{__version__} (personal use)"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def fetch(self, title: str, artist: str, album: str, duration: float) -> Optional[Lyrics]:
        """Returns Lyrics (possibly instrumental), or None if nothing was found. Raises on network errors."""
        cf = self._cache_file(title, artist, album, duration)
        try:
            cached = json.loads(cf.read_text(encoding="utf-8"))
            if cached.get("notfound"):
                if time.time() - cached.get("at", 0) < NEGATIVE_TTL:
                    return None
            else:
                return _from_json(cached)
        except (OSError, ValueError):
            pass

        data = self._get("/api/get", {"track_name": title, "artist_name": artist,
                                      "album_name": album, "duration": int(round(duration))})
        if data is None:
            results = self._get("/api/search", {"track_name": title, "artist_name": artist}) or []
            results = [r for r in results if r.get("syncedLyrics") or r.get("plainLyrics") or r.get("instrumental")]
            if duration:
                results = [r for r in results if abs(float(r.get("duration") or 0) - duration) <= 4]
            results.sort(key=lambda r: not r.get("syncedLyrics"))
            data = results[0] if results else None

        try:
            cf.parent.mkdir(parents=True, exist_ok=True)
            cf.write_text(json.dumps(data if data else {"notfound": True, "at": time.time()}), encoding="utf-8")
        except OSError:
            pass
        return _from_json(data) if data else None
