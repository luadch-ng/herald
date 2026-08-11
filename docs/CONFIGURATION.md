# Configuring Herald

Herald is configured in the **GUI** (`Herald.exe`, or `python main.py`). The
headless runner (`herald_cli.py`, Docker) does not have its own config - it reads
what the GUI saved. Everything lives in a portable **`data/`** folder next to the
app:

- `data/hubs.json` - one entry per hub: connection + bot/announce settings + the
  hub's own list of rules.
- `data/settings.json` - global app settings (theme, target OS, update check, ...).

> `data/` holds hub passwords, keyprints and the bot identity. **Never share or
> commit it.**

You normally never edit these files by hand - use the GUI. The tabs below map to
the fields in those files.

## Hub (Config tab)

One connection per hub. Add / clone / remove hubs with the buttons under the
sidebar.

| Field | Meaning |
|---|---|
| Display name | Sidebar label. Must be unique (the announced-history file is keyed by hub). |
| Address / Port | Hub host and ADC(S) port. TLS default is `5001`. |
| Nick / Password | The announcer bot's login. |
| Keyprint (optional) | Pin the server's TLS fingerprint (`SHA256/...`). Empty = trust on first use (the log warns UNVERIFIED). Pin it for any hub you care about. |
| Enabled | The **headless** runner (`herald_cli.py`) starts every *enabled* hub. |
| Auto-connect | The **GUI** connects every *auto-connect* hub on startup. Independent of Enabled. |
| Bot slots / share / upload | Values announced to the hub. An announcer serves no files; slots is floored to `>= 1` (a hub's slot gate rejects a 0-slot login). |
| Sleep after connect (s) | Fallback poll interval for re-scanning the watched folders when live file-events are unavailable (e.g. some network / Windows-drive mounts). |
| Keepalive interval (s) | How often Herald pings the hub to stay online. Does **not** control announce timing - releases announce as soon as they are detected + complete. |
| Socket timeout (s) | Read timeout on the hub connection. |

## Rules (Rules tab)

Each hub has an ordered list of rules; a rule watches **one folder** and announces
finished releases found in it. Add / clone / delete / reorder with the buttons and
the right-click menu. Fill the editor and hit **Save rule** (on a fresh hub that
creates the first rule from what you typed).

| Field | Meaning |
|---|---|
| Rule name / Enabled | Label; only *enabled* rules announce. |
| Folder path | The directory to watch. Must be valid on the machine that RUNS the engine (see below). |
| Hub command | The command used to announce, e.g. `+addrel`. |
| Category | The hub category the command expects, e.g. `Movies_1080p`. Import the hub's categories via `File -> Import freshstuff categories`. |
| Quiet seconds | How long a release must be unchanged before it counts as finished. |
| SFV accuracy | `present` (fastest) / `wait until quiet` (recommended) / `CRC verify` (exact, reads every byte - slow). |
| Scan previous daydir | Zeroday/daydir mode: for this many minutes after midnight, also scan yesterday's day folder so late-night releases are not orphaned. `0` = off. |
| Filters | Announce directories / files, Require NFO / valid SFV, Skip hidden files, Disallow whitespace, Daydir scheme (MMDD), Only current daydir, Max age (days). |
| Exclude (one per line) | Skip a release whose name contains **any** of these terms (e.g. `(nuked)`). |
| Include (one per line) | Only announce names containing **one** of these terms. Empty = allow all. |

## Global settings (Settings menu)

| Setting | Meaning |
|---|---|
| Target runtime OS | `Auto` / `Windows` / `Linux` - the OS where the announcer actually RUNS (see below). |
| Theme | Dark / Light. |
| Start minimized | Launch minimized to the taskbar. |
| Release-Log tooltips | Show a release's full folder path on hover. |
| Check for updates on startup | Ask GitHub once a day whether a newer release exists. Notify-only - Herald never installs anything. |

The two **Auto-scroll** toggles sit above each log on the Status / Release Log tabs
(follow new lines / newest release, or hold your position).

## Cross-OS, headless and Docker

Watch paths must be valid on the machine that **runs** the engine. If you author
the config in the GUI on Windows but run it headless on Linux or in Docker:

1. Set **Settings -> Target runtime OS = Linux** (stops the GUI validating your
   paths against the wrong filesystem).
2. Enter each rule's **Folder path** as it is on that machine, e.g.
   `/releases/Movies` - matching your Docker `-v /host/path:/releases:ro` mount.
3. Run `python herald_cli.py --check` (or `docker run ... --check`) on the target;
   it reports any watch path missing there.

See the [README](../README.md) for GUI / CLI / Docker run instructions.
