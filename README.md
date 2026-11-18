# Spoty - Spotify overlay for Linux Mint Cinnamon

A small always-on-top Spotify widget with **two views** (compact card / lyrics), a **draggable seek bar**,
play / next / previous / **like** buttons, plus a `spoty` command you bind to global shortcuts
(seek ±10 s, like, switch view ...). You never have to switch to the Spotify window.

* Controls Spotify through **MPRIS** (the standard Linux media interface) - no login, works with Free and Premium.
* **Lyrics** (time-synced, scrolling) come from the free [LRCLIB](https://lrclib.net) database, fetched only while the lyrics view is open.
* **Like / unlike** uses Spotify's *official* Web API with a one-time browser sign-in (see below). Optional.
* **Game mode:** `spoty quit` ends the process completely - zero CPU, zero RAM. Hiding it (`spoty toggle`) also stops every timer.

## Install
```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-cairo   # already present on most Mint installs
./install.sh --keys --autostart      # --keys: add Cinnamon shortcuts, --autostart: start at login (both optional)
spoty start
```
Remove everything with `./install.sh --uninstall`. Check your setup any time with `spoty doctor`.

## Using it
| | |
|---|---|
| Move | drag the card anywhere (position is remembered) |
| Seek | click or drag the bar to jump to an exact time; mouse wheel over the bar = ±5 s |
| Buttons | previous · play/pause · next · ♥ like · Lyrics/Card view switch · × hide |
| Right-click | view, lyrics on/off, size, opacity, hide, quit |

### Keyboard shortcuts (`spoty keys --install`, edit later in *Menu → Keyboard → Shortcuts → Custom Shortcuts*)
| Shortcut | Action | Command |
|---|---|---|
| Ctrl+Shift+Alt+P | play / pause | `spoty play-pause` |
| Ctrl+Shift+Alt+N / B | next / previous | `spoty next` / `spoty prev` |
| Ctrl+Shift+Alt+J / L | **seek −10 s / +10 s** | `spoty seek -10` / `spoty seek +10` |
| Ctrl+Shift+Alt+H | like / unlike | `spoty like` |
| Ctrl+Shift+Alt+O | show / hide overlay | `spoty toggle` |
| Ctrl+Shift+Alt+M | lyrics ⇄ card view | `spoty mode toggle` |
| Ctrl+Shift+Alt+Q | **quit overlay (game mode)** | `spoty quit` |

(These avoid Cinnamon's own shortcuts - e.g. Ctrl+Alt+L locks the screen. Your keyboard's media keys already work with Spotify.)
Exact seeking from the terminal: `spoty seek 1:23`. Others: `spoty status`, `spoty lyrics`, `spoty mode lyrics`.

## Game mode
Run `spoty quit` (or Ctrl+Shift+Alt+Q, or right-click → Quit). Nothing keeps running. `spoty start` / Ctrl+Shift+Alt+O brings it back.
Prefer it to start with the PC? `spoty autostart on` (and `quit` it before gaming).

## The like button (optional, one-time setup)
Spotify's *official* API is the only supported way to touch Liked Songs, and **Spotify (Feb 2026) now requires the
developer-app owner to have an active Premium subscription** (and caps a dev app at 5 users). Everything else in Spoty works without this.
Spoty never asks for or stores your Spotify email/password - you sign in on Spotify's own page and only a token is kept
(`~/.config/spoty/token.json`, permissions 600).

1. Open <https://developer.spotify.com/dashboard> → **Create app** (any name; select *Web API*).
2. Add this **Redirect URI** exactly: `http://127.0.0.1:8898/callback` and save.
3. Copy the app's **Client ID**, then run: `spoty login --client-id YOUR_CLIENT_ID` and approve in the browser.
4. If Spotify answers 403, add your account under the app's **User Management**.

`spoty logout` forgets the token. Liking uses `PUT/DELETE /me/library` (falls back to the older `/me/tracks` endpoints).

## Settings (`~/.config/spoty/config.json`)
`mode` compact|lyrics · `scale` 0.6-2.0 · `opacity` 0.3-1 · `lyrics` true/false (false = never contact the lyrics service) ·
`seek_step` seconds · `auto_hide_idle` (hide while Spotify is closed) · `fps` (animation rate while playing) ·
`player` "spotify" or "any" (also show browsers/other MPRIS players).

## Privacy
Lyrics lookups send *title, artist, album, duration* to lrclib.net (only in lyrics view; results cached in `~/.cache/spoty`).
Cover art is downloaded from Spotify's image CDN. Nothing else leaves your machine.

## Troubleshooting
* *Nothing shows* - Spotify must be the desktop app (deb/Flatpak/snap all expose MPRIS) and have played something. `spoty status` should print the song.
* *Seek bar does nothing* - ads and some Spotify builds refuse seeking; Spoty shows a notice instead of failing silently.
* *Square corners / black box* - turn on Cinnamon's compositing (Settings → General → "Disable compositing for full-screen windows" is fine to leave on).
* Log of the background overlay: `~/.cache/spoty/overlay.log`.

## Tests
`./tests/run.sh` (35 tests: real D-Bus against a fake Spotify, OAuth/PKCE and Web API against a fake server, lyrics parsing/cache,
and the overlay's drawing/click/drag/seek logic against a stand-in for GTK).
