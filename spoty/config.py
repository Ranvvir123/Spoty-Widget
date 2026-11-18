"""JSON config in ~/.config/spoty/config.json (honours XDG_CONFIG_HOME / XDG_CACHE_HOME)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

DEFAULTS = {
    "client_id": "",          # your own Spotify developer app (only needed for the heart/like button)
    "player": "spotify",      # "spotify" = only Spotify, "any" = fall back to any MPRIS player (e.g. a browser)
    "mode": "compact",        # "compact" | "lyrics"
    "x": None,                # window position (None = top-right corner)
    "y": None,
    "scale": 1.0,             # 0.6 .. 2.0
    "opacity": 0.88,          # 0.3 .. 1.0
    "lyrics": True,           # fetch synced lyrics from LRCLIB when the lyrics view is open
    "seek_step": 10,          # seconds for seek back/forward
    "auto_hide_idle": True,   # hide the overlay while Spotify is closed / nothing is loaded
    "always_on_top": True,
    "fps": 30,                # animation frame rate while visible and playing
    "lrclib_url": "https://lrclib.net",
}


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "spoty"


def cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "spoty"


def config_path() -> Path:
    return config_dir() / "config.json"


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def sanitize(cfg: dict) -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in cfg.items() if k in DEFAULTS})
    out["scale"] = _clamp(out["scale"], 0.6, 2.0, 1.0)
    out["opacity"] = _clamp(out["opacity"], 0.3, 1.0, 0.88)
    out["fps"] = int(_clamp(out["fps"], 5, 60, 30))
    out["seek_step"] = int(_clamp(out["seek_step"], 1, 120, 10))
    if out["mode"] not in ("compact", "lyrics"):
        out["mode"] = "compact"
    if out["player"] not in ("spotify", "any"):
        out["player"] = "spotify"
    for k in ("x", "y"):
        if not isinstance(out[k], int):
            out[k] = None
    out["client_id"] = str(out["client_id"] or "").strip()
    out["lrclib_url"] = str(out["lrclib_url"] or DEFAULTS["lrclib_url"]).rstrip("/")
    for k in ("lyrics", "auto_hide_idle", "always_on_top"):
        out[k] = bool(out[k])
    return out


def load() -> dict:
    p = config_path()
    raw = {}
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
        except (OSError, ValueError):
            raw = {}
    return sanitize(raw)


def save(cfg: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(sanitize(cfg), f, indent=2)
        os.replace(tmp, p)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
