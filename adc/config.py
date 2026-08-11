"""Herald config model + persistence.

Multihub by design: a hub carries its own connection settings, bot/announce
settings AND its own list of rules (in the single-hub Lua announcer these
lived in separate global cfg files). Everything is one JSON file so a hub is
fully self-contained and portable.
"""

import json
import os
import re
import sys


def _data_root():
    """Where the operator-owned config lives (hubs.json, settings.json,
    announced_*.txt). Under a PyInstaller build this is a `data/` folder NEXT
    TO the executable - portable and persistent - not the temp bundle dir that
    `__file__` would point at. From source it is the repo's data/ dir."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "data")
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


DATA_DIR = _data_root()
HUBS_FILE = os.path.join(DATA_DIR, "hubs.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
CATEGORIES_FILE = os.path.join(DATA_DIR, "categories.json")


def default_rule(rulename=""):
    """A fresh announce rule (mirrors the Lua rules.lua defaults)."""
    return {
        "rulename": rulename,
        "active": False,
        "path": "",
        "command": "+addrel",
        "category": "",
        "alibicheck": False,
        "alibinick": "DUMP",
        "checkdirs": True,
        "checkdirsnfo": False,
        "checkdirssfv": False,
        "checkfiles": False,
        "checkspaces": True,
        "checkage": False,
        "maxage": 0,
        "daydirscheme": False,
        "zeroday": False,
        # grace window (zeroday mode only): for this many minutes after
        # midnight, also scan YESTERDAY's daydir, so releases that land just
        # before midnight (and finish uploading / settle after it) still get
        # picked up instead of being orphaned in the previous day's folder.
        # 0 = off. Requested by Sopor.
        "daydir_prev_minutes": 0,
        "skip_hidden": True,
        # completion detection (see tracker.py / complete.py)
        "settle_seconds": 5,          # quiet window before a non-SFV / size-stable release is "done"
        "sfv_level": "size_stable",   # present | size_stable | crc
        "blacklist": ["(incomplete)", "(no-sfv)", "(nuked)"],
        "whitelist": [],
        "max_per_extension": {"nfo": 1, "sfv": 1},
        "max_per_extension_recursive": True,
        "max_per_extension_max_depth": 8,
    }


def default_hub(name="New hub"):
    """A fresh hub: connection + bot/announce settings + empty rule list."""
    return {
        "name": name,
        # headless runner (herald_cli.py) starts every enabled hub; the GUI
        # ignores this and connects to whichever hub is selected.
        "enabled": True,
        # GUI auto-connect: the Herald GUI connects every hub with this set on
        # startup. Independent of "enabled" (which is the headless-runner gate).
        "autoconnect": False,
        "host": "",
        "port": 5001,
        "nick": "",
        "password": "",
        "keyprint": "",
        "description": "",
        # bot / announce settings (cfg.lua equivalents). botslots defaults to
        # 1: an announcer serves no files, but a hub's usr_slots gate rejects a
        # login declaring 0 slots (ISTA 120), so a 0 would lock Herald out of
        # any default-gated luadch hub. The login floors it to >=1 regardless.
        "botslots": 1,
        "botshare": 0,
        "botupload": 0,
        "sleeptime": 10,
        "announceinterval": 300,
        "sockettimeout": 60,
        # client identity strings. The ADC VE (client version) is not a per-hub
        # setting - it comes from adc.__version__ (single source), so it always
        # matches the running Herald build. Only the AP (client name) lives here.
        "app": "Herald",
        "rules": [],
    }


def _slug(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "hub"


def load_hubs():
    """Load the hub list from disk, or seed a sensible default set."""
    if os.path.isfile(HUBS_FILE):
        try:
            with open(HUBS_FILE, "r", encoding="utf-8") as fh:
                hubs = json.load(fh)
            if isinstance(hubs, list):
                # Skip any non-dict element (a hand-edited/garbage entry) rather
                # than crashing on _migrate's hub.get(...).
                valid = [_migrate(h) for h in hubs if isinstance(h, dict)]
                if valid:
                    return valid
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    # First run: one demo hub pointing at the local test hub.
    demo = default_hub("Local test hub")
    demo.update(host="127.0.0.1", nick="dummy", password="test",
                description="Herald prototype")
    return [demo]


def save_hubs(hubs):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = HUBS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(hubs, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, HUBS_FILE)


# -- global settings (target runtime OS, ...) --------------------------------
# A Herald config can be authored on one machine (GUI) and run headless on
# another (herald_cli.py). Watch paths must be valid on the machine that RUNS
# the engine, so the target runtime OS decides whether the GUI hard-validates
# a path against the local filesystem or accepts it as a foreign-OS string.

def default_settings():
    return {"target_os": "auto",         # auto | windows | linux
            "theme": "dark",             # dark | light
            "autoscroll_log": True,      # Status tab follows new lines to bottom
            "autoscroll_releases": True, # Release tab jumps to the newest (top)
            "start_minimized": False,    # GUI starts minimized (to tray if any)
            "release_tooltips": True,    # hover a release row -> full path tooltip
            "check_updates": True,       # check GitHub for a newer release
            "update_last_check": 0,      # unix ts of the last successful check
            "update_latest_seen": ""}    # last release tag observed (cache)


def load_settings():
    base = default_settings()
    if os.path.isfile(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                base.update(loaded)
        except (OSError, ValueError):
            pass
    return base


def save_settings(settings):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS_FILE)


# -- freshstuff categories (imported from the hub's categories.dat) ----------
# The +addrel category names live on the hub (ptx_freshstuff_categories.dat),
# not in the announcer config. Importing them lets a rule PICK a category
# instead of typing it (and risking a typo the hub then rejects). Shared by
# every rule / hub.

def load_categories():
    if os.path.isfile(CATEGORIES_FILE):
        try:
            with open(CATEGORIES_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return [str(c) for c in data]
        except (OSError, ValueError):
            pass
    return []


def save_categories(cats):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CATEGORIES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(sorted(set(cats)), fh, indent=2, ensure_ascii=False)
    os.replace(tmp, CATEGORIES_FILE)


def host_os():
    """Path family of the machine we are running on: 'windows' or 'posix'."""
    return "windows" if os.name == "nt" else "posix"


def target_os(settings=None):
    """Resolve the configured target runtime to a path family ('windows'/'posix')."""
    t = (settings or load_settings()).get("target_os", "auto")
    if t == "auto":
        return host_os()
    return "windows" if t == "windows" else "posix"


def target_is_local(settings=None):
    """True when watch paths should be validated against THIS machine's fs
    (target runtime == host). False when authoring for a different OS."""
    return target_os(settings) == host_os()


# -- stable bot identity -----------------------------------------------------

def hub_path_status(hub):
    """For each ACTIVE rule in a hub, report whether its watch path exists on
    THIS machine. Returns a list of (rulename, path, exists). Used for the
    startup / --check validation surfaced to the operator; the engine itself
    already skips a missing path at runtime (announce.scan_rules)."""
    out = []
    for rule in hub.get("rules", []):
        if not rule.get("active"):
            continue
        path = str(rule.get("path", "") or "")
        out.append((rule.get("rulename") or "(unnamed)", path,
                    bool(path) and os.path.isdir(path)))
    return out


def ensure_identity(hub):
    """Give a hub a persistent PID/CID if it lacks one. Returns True if it
    changed (caller persists). A stable identity means the hub recognises the
    same bot across restarts; an imported old-announcer config already carries
    its original pid/cid."""
    if hub.get("pid") and hub.get("cid"):
        return False
    from .protocol import new_identity
    hub["pid"], hub["cid"] = new_identity()
    return True


def _migrate(hub):
    """Back-fill any missing keys so older/partial configs still load."""
    base = default_hub(hub.get("name", "Hub"))
    base.update(hub)
    base["rules"] = [_migrate_rule(r) for r in hub.get("rules", [])]
    return base


def _migrate_rule(rule):
    base = default_rule(rule.get("rulename", "Rule"))
    base.update(rule)
    return base


# -- already-announced tracking (per hub, so restarts don't re-announce) -----

def _announced_key(hub):
    """Stable dedup key for a hub. Prefer the CID: it is unique (no two hubs
    collide the way two display names can slug-collide) and rename-proof (a
    renamed hub keeps its identity, so it does not re-announce everything). Fall
    back to the name slug for a bare-string caller or a hub without an identity."""
    if isinstance(hub, dict):
        return _slug(hub.get("cid") or hub.get("name", "hub"))
    return _slug(str(hub))


def _announced_file(hub):
    return os.path.join(DATA_DIR, f"announced_{_announced_key(hub)}.txt")


def _migrate_announced(hub):
    """One-time adoption of a pre-CID-keying name-slug file: if this hub has a
    CID but no CID-keyed file yet, rename its old name-slug file over so dedup
    state survives the switch (no one-off re-announce storm)."""
    if not isinstance(hub, dict) or not hub.get("cid"):
        return
    new_path = _announced_file(hub)
    if os.path.isfile(new_path):
        return
    old_path = os.path.join(DATA_DIR, f"announced_{_slug(hub.get('name', 'hub'))}.txt")
    if old_path != new_path and os.path.isfile(old_path):
        try:
            os.replace(old_path, new_path)
        except OSError:
            pass


def load_announced(hub):
    _migrate_announced(hub)
    path = _announced_file(hub)
    seen = set()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        seen.add(line)
        except OSError:
            pass
    return seen


def append_announced(hub, name):
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(_announced_file(hub), "a", encoding="utf-8") as fh:
            fh.write(name + "\n")
    except OSError:
        pass
