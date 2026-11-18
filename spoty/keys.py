"""Install global keyboard shortcuts into Cinnamon (org.cinnamon.desktop.keybindings custom keybindings)."""
from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Tuple

LIST_SCHEMA = "org.cinnamon.desktop.keybindings"
ITEM_SCHEMA = "org.cinnamon.desktop.keybindings.custom-keybinding"
ITEM_PATH = "/org/cinnamon/desktop/keybindings/custom-keybindings/{id}/"
PREFIX = "spoty-"

# <Primary> = Ctrl. Ctrl+Shift+Alt+<letter> avoids Cinnamon's built-in shortcuts (Ctrl+Alt+L locks the screen,
# Ctrl+Alt+Arrows switch workspaces). Change any of them later in Settings > Keyboard > Shortcuts > Custom.
DEFAULTS: List[Tuple[str, str, str, str]] = [
    ("play",      "Spoty: Play/Pause",        "play-pause",    "<Primary><Shift><Alt>p"),
    ("next",      "Spoty: Next track",        "next",          "<Primary><Shift><Alt>n"),
    ("prev",      "Spoty: Previous track",    "prev",          "<Primary><Shift><Alt>b"),
    ("back10",    "Spoty: Seek back 10 s",    "seek -10",      "<Primary><Shift><Alt>j"),
    ("fwd10",     "Spoty: Seek forward 10 s", "seek +10",      "<Primary><Shift><Alt>l"),
    ("like",      "Spoty: Like / unlike",     "like",          "<Primary><Shift><Alt>h"),
    ("overlay",   "Spoty: Show/hide overlay", "toggle",        "<Primary><Shift><Alt>o"),
    ("mode",      "Spoty: Lyrics/compact view", "mode toggle", "<Primary><Shift><Alt>m"),
    ("quit",      "Spoty: Quit overlay (game mode)", "quit",   "<Primary><Shift><Alt>q"),
]

Runner = Callable[[List[str]], str]


def default_runner(cmd: List[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def spoty_binary() -> str:
    """Absolute path (shortcuts run with the desktop session's PATH, which may lack ~/.local/bin)."""
    local = Path.home() / ".local" / "bin" / "spoty"
    if local.exists():
        return str(local)
    return shutil.which("spoty") or "spoty"


def _get_list(run: Runner) -> List[str]:
    out = run(["gsettings", "get", LIST_SCHEMA, "custom-list"]).strip()
    if out.startswith("@as"):
        out = out.split(" ", 1)[1]
    try:
        return [str(x) for x in ast.literal_eval(out)]
    except (ValueError, SyntaxError):
        return []


def _set_list(run: Runner, items: List[str]) -> None:
    run(["gsettings", "set", LIST_SCHEMA, "custom-list", repr(items)])


def install(run: Runner = default_runner, binary: str = "") -> List[str]:
    binary = binary or spoty_binary()
    ids = [i for i in _get_list(run) if not i.startswith(PREFIX)]
    done = []
    for key, name, args, binding in DEFAULTS:
        kid = PREFIX + key
        schema = f"{ITEM_SCHEMA}:{ITEM_PATH.format(id=kid)}"
        run(["gsettings", "set", schema, "name", name])
        run(["gsettings", "set", schema, "command", f"{binary} {args}"])
        run(["gsettings", "set", schema, "binding", repr([binding])])
        ids.append(kid)
        done.append(f"{binding:28s} {name}")
    _set_list(run, ids)
    return done


def remove(run: Runner = default_runner) -> int:
    ids = _get_list(run)
    keep = [i for i in ids if not i.startswith(PREFIX)]
    _set_list(run, keep)
    return len(ids) - len(keep)


def describe(binary: str = "") -> List[str]:
    binary = binary or spoty_binary()
    return [f"{b:28s} {n:34s} -> {binary} {a}" for _, n, a, b in DEFAULTS]
