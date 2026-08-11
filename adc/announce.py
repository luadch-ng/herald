"""Release-scanning engine, ported from the Lua announcer's core/announce.lua.

Given a rule (watched path + filters), scan for new releases and return the
ones that pass every filter. The caller (connection) turns each hit into a
BMSG. All the original filters are here: blacklist, whitelist, hidden-dot,
whitespace, max-age, NFO/SFV presence, per-extension count caps, and the
daydir (MMDD) scheme.

A "rule" is a dict with these keys (see config.default_rule):
  rulename, active, path, command, category, alibicheck, alibinick,
  checkdirs, checkdirsnfo, checkdirssfv, checkfiles, checkspaces,
  checkage, maxage, daydirscheme, zeroday, skip_hidden,
  blacklist (list[str]), whitelist (list[str]),
  max_per_extension (dict[str,int]|None), max_per_extension_recursive (bool),
  max_per_extension_max_depth (int)
"""

import os
import time


class Release:
    """One release found by the scanner: its name, matching rule, full path
    on disk, and whether it is a directory (vs a loose file)."""

    __slots__ = ("name", "rule", "path", "is_dir")

    def __init__(self, name, rule, path="", is_dir=True):
        self.name = name
        self.rule = rule
        self.path = path
        self.is_dir = is_dir


def _match(name, patterns, white=False):
    """True if `name` (case-insensitive) contains any pattern. For a
    whitelist, an empty pattern list means 'allow everything'."""
    low = name.lower()
    patterns = patterns or []
    for p in patterns:
        if p.lower() in low:
            return True
    if white and not patterns:
        return True
    return False


def _age_in_days(mtime):
    return (time.time() - mtime) / 86400.0


def _is_hidden(entry):
    """Hidden = a dot-prefixed name (Unix) OR the Windows hidden attribute.
    The old announcer honoured the Windows attribute, and a folder marked
    hidden on Windows carries no leading dot, so a name-only check misses it."""
    if entry.name.startswith("."):
        return True
    try:
        return bool(entry.stat().st_file_attributes & 0x2)  # FILE_ATTRIBUTE_HIDDEN
    except (OSError, AttributeError):
        return False   # non-Windows stat has no st_file_attributes


def _directory_has_nfo(path):
    try:
        for entry in os.scandir(path):
            if entry.is_file() and entry.name.lower().endswith(".nfo"):
                return True
    except OSError:
        pass
    return False


def _directory_has_valid_sfv(path):
    """Port of directory_has_valid_sfv: find an .sfv, and verify every file
    it lists exists in the directory."""
    try:
        entries = list(os.scandir(path))
    except OSError:
        return False
    for entry in entries:
        if entry.is_file() and entry.name.lower().endswith(".sfv"):
            try:
                with open(entry.path, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        line = line.rstrip("\r\n")
                        if line and not line.startswith(";"):
                            parts = line.rsplit(" ", 1)
                            if len(parts) == 2:
                                fname = parts[0].strip()
                                if not os.path.isfile(os.path.join(path, fname)):
                                    return False
            except OSError:
                return False
            return True
    return False


def _count_files_by_ext(path, recursive, max_depth):
    counts = {}

    def walk(p, depth):
        if depth > max_depth:
            return
        try:
            entries = list(os.scandir(p))
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_file():
                    _, dot, ext = entry.name.rpartition(".")
                    if dot:
                        ext = ext.lower()
                        counts[ext] = counts.get(ext, 0) + 1
                elif entry.is_dir() and recursive:
                    walk(entry.path, depth + 1)
            except OSError:
                continue

    walk(path, 0)
    return counts


def _find_extension_excess(path, limits, recursive, max_depth):
    if not isinstance(limits, dict) or not limits:
        return None
    counts = _count_files_by_ext(path, recursive, max_depth)
    for ext, cap in limits.items():
        if counts.get(ext, 0) > cap:
            return ext, counts.get(ext, 0), cap
    return None


def _scan(path, rule, already_sent, found, blocked, log):
    """Scan one directory level for releases passing `rule`'s filters."""
    count = 0
    try:
        entries = list(os.scandir(path))
    except OSError as e:
        log(f"Error: {e}")
        return
    for entry in entries:
        name = entry.name
        if name in (".", "..") or name in blocked or name in already_sent:
            continue
        try:
            is_dir = entry.is_dir()
            is_file = entry.is_file()
            mtime = entry.stat().st_mtime
        except OSError as e:
            log(f"Error: {e}")
            continue

        excess = None
        if rule.get("max_per_extension") and is_dir:
            excess = _find_extension_excess(
                entry.path, rule["max_per_extension"],
                rule.get("max_per_extension_recursive", True),
                rule.get("max_per_extension_max_depth", 8))

        if _match(name, rule.get("blacklist")):
            count += 1
            log(f"Release: '{name}' blocked. | Reason: Exclude filter")
        elif not _match(name, rule.get("whitelist"), white=True):
            count += 1
            log(f"Release: '{name}' blocked. | Reason: Include filter")
        elif rule.get("skip_hidden", True) and _is_hidden(entry):
            count += 1
            log(f"Release: '{name}' blocked. | Reason: Hidden")
        elif rule.get("checkspaces") and " " in name:
            count += 1
            log(f"Release: '{name}' blocked. | Reason: Whitespaces")
        elif rule.get("checkage") and rule.get("maxage", 0) > 0 and _age_in_days(mtime) >= rule["maxage"]:
            count += 1
            log(f"Release: '{name}' blocked. | Reason: Max Age")
        elif rule.get("checkdirs") and rule.get("checkdirsnfo") and not _directory_has_nfo(entry.path):
            count += 1
            log(f"Release: '{name}' blocked. | Reason: NFO Check: No NFO file found")
        elif rule.get("checkdirs") and rule.get("checkdirssfv") and not _directory_has_valid_sfv(entry.path):
            count += 1
            log(f"Release: '{name}' blocked. | Reason: SFV Check")
        elif excess:
            count += 1
            log(f"Release: '{name}' blocked. | Reason: too many .{excess[0]} files ({excess[1]} > {excess[2]})")
        else:
            if is_dir and rule.get("checkdirs"):
                found[name] = (rule, entry.path, True)
            elif is_file and rule.get("checkfiles"):
                found[name] = (rule, entry.path, False)
    log(f"Releases blocked: {count}")


def scan_rules(rules, already_sent, log=lambda *_: None, warned_missing=None):
    """Run every active rule and return a list[Release] of new releases.

    `already_sent` is a set of release names already announced (so restarts
    don't re-announce). `log` is an optional line callback for progress.
    `warned_missing`, if given, is a mutable set the caller keeps across polls
    so a missing watch path is warned about ONCE (not every poll) - important
    for the headless runner writing to a log file. When None the warning fires
    every call (the legacy behaviour).
    """
    found = {}
    blocked = {}
    log("Search directories for updates...")
    for rule in rules:
        if not rule.get("active"):
            continue
        path = str(rule.get("path", ""))
        if not os.path.isdir(path):
            if warned_missing is None or path not in warned_missing:
                log(f"Warning: directory '{path}' is not a directory or does not exist, skipping...")
                if warned_missing is not None:
                    warned_missing.add(path)
            continue
        if warned_missing is not None:
            warned_missing.discard(path)   # reappeared -> warn again if it vanishes later
        log(f"Searching in '{path}'...")
        if rule.get("daydirscheme"):
            if rule.get("zeroday"):
                now = time.localtime()
                today = os.path.join(path, time.strftime("%m%d", now))
                if os.path.isdir(today):
                    _scan(today, rule, already_sent, found, blocked, log)
                else:
                    log(f"Warning: directory '{today}' seems not to exist, skipping...")
                # grace window: for the first N minutes after midnight also scan
                # yesterday's daydir, so a release that landed just before
                # midnight (still uploading / not yet settled) is not orphaned
                # in the previous day's folder. `already_sent` stops re-announce.
                prev_min = int(rule.get("daydir_prev_minutes", 0) or 0)
                if prev_min > 0:
                    since_midnight = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
                    if since_midnight < prev_min * 60:
                        # localtime(now - 1 day) handles month/year rollover
                        yday = time.strftime("%m%d", time.localtime(time.time() - 86400))
                        yday_dir = os.path.join(path, yday)
                        if os.path.isdir(yday_dir):
                            log(f"Grace window: also scanning previous daydir '{yday}'...")
                            _scan(yday_dir, rule, already_sent, found, blocked, log)
            else:
                try:
                    subdirs = [e.name for e in os.scandir(path) if e.is_dir()]
                except OSError:
                    subdirs = []
                for d in subdirs:
                    if len(d) == 4 and d.isdigit():
                        month, day = int(d[:2]), int(d[2:])
                        if 1 <= month <= 12 and 1 <= day <= 31:
                            _scan(os.path.join(path, d), rule, already_sent, found, blocked, log)
                            continue
                    log(f"Warning: directory '{d}' fits not in 4 digit day dir scheme, skipping...")
        else:
            _scan(path, rule, already_sent, found, blocked, log)

    log(f"...finished. Found {len(found)} new releases.")
    return [Release(name, rule, path, is_dir)
            for name, (rule, path, is_dir) in found.items()]


def build_command(release):
    """Assemble the BMSG body for a release (net.lua announce block):
    '<command> [<alibinick>] <category> <release>'."""
    rule = release.rule
    parts = [rule.get("command", "+addrel")]
    if rule.get("alibicheck") and rule.get("alibinick"):
        parts.append(rule["alibinick"])
    parts.append(rule.get("category", ""))
    parts.append(release.name)
    return " ".join(p for p in parts if p)
