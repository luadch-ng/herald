"""Herald update check: is a newer release published on GitHub?

Pure Python (urllib only - no Qt, no third-party deps) so the GUI and the
headless CLI share ONE implementation. It never raises to the caller: any
failure (offline, rate-limited, malformed response) returns None, so an update
check can never break startup. Notify-only - this module does not download or
install anything, it only reports what the latest published release is.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

MAX_BODY = 1 << 20   # 1 MiB read ceiling; a release JSON is a few KB

REPO = "luadch-ng/herald"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"


def parse_version(text):
    """'v1.9.2' / '1.9' -> (1, 9, 2) / (1, 9). Tolerant of a leading 'v' and a
    non-numeric tail ('1.9-rc1' -> (1, 9)); returns () when nothing numeric is
    found, which callers treat as 'unknown / ignore'."""
    if not text:
        return ()
    parts = []
    for chunk in str(text).strip().lstrip("vV").split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        if not num:
            break
        parts.append(int(num))
    return tuple(parts)


def _norm(parts):
    """Drop trailing zeros so 1.8.0 and 1.8 compare EQUAL (they are the same
    release). Without this, tuple compare makes (1,8,0) > (1,8) -> a bogus
    'update available' when the tag adds a .0 the running version omits."""
    parts = list(parts)
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(latest, current):
    """True if version string `latest` is strictly newer than `current`."""
    lv = parse_version(latest)
    return bool(lv) and _norm(lv) > _norm(parse_version(current))


def check(current=None, timeout=6):
    """Query GitHub for the latest release. On success returns:
        {current, latest, url, notes, update_available}
    On ANY failure returns None (offline, HTTP/rate-limit error, bad JSON).
    Never raises."""
    current = current or __version__
    req = urllib.request.Request(
        API_URL,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"Herald/{current}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BODY)      # cap the read (OOM / slow-drip guard)
        data = json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # GitHub was REACHED but returned an error status. 404 means the repo has
        # no published release yet (a fresh project, or all releases removed) -
        # that is "reachable, nothing newer to offer", NOT a connection failure.
        # Report it as up-to-date so the UI does not falsely cry "cannot reach
        # GitHub". Other statuses (403 rate-limit, 5xx) are genuinely
        # inconclusive, so treat those as unreachable. (HTTPError subclasses
        # URLError, so this except MUST come first.)
        if e.code == 404:
            return {"current": current, "latest": None, "url": RELEASES_URL,
                    "notes": "", "update_available": False}
        return None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name") or ""
    if not parse_version(tag):
        return None
    return {
        "current": current,
        "latest": tag,
        "url": _safe_url(data.get("html_url")),
        "notes": (data.get("body") or "").strip(),
        "update_available": is_newer(tag, current),
    }


def _safe_url(url):
    """Constrain the release URL we later hand to the browser: only https on a
    github.com host. Anything else (an attacker-influenced html_url under a TLS
    break, an odd scheme) falls back to the fixed releases page."""
    try:
        pu = urllib.parse.urlparse(url or "")
    except ValueError:
        return RELEASES_URL
    host = (pu.hostname or "").lower()
    if pu.scheme == "https" and (host == "github.com" or host.endswith(".github.com")):
        return url
    return RELEASES_URL


# -- startup throttle (callers own the settings dict + its persistence) -------

def should_check(settings, interval_hours=24):
    """True if update checking is enabled AND the throttle window has elapsed."""
    if not settings.get("check_updates", True):
        return False
    last = settings.get("update_last_check", 0) or 0
    return (time.time() - last) >= interval_hours * 3600


def mark_checked(settings, latest=""):
    """Record the check time (and last-seen tag) so the caller can persist it.
    Storing the tag lets the UI show a known-available update between checks
    without hitting the API on every start."""
    settings["update_last_check"] = int(time.time())
    if latest:
        settings["update_latest_seen"] = latest
