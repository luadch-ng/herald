"""Import a setup from the old Lua announcer.

Reads the announcer's cfg/*.lua files and produces a Herald hub (connection +
bot settings + rules + preserved PID/CID). Parsing is a small self-contained
Lua-table reader - no Lua runtime, no extra dependency - tolerant of the
comments and layout the announcer writes.
"""

import os
import re

from .config import default_hub, default_rule


# -- minimal Lua-table-literal reader ---------------------------------------

def _tokenize(s):
    toks = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t\r\n":
            i += 1
        elif c == "-" and s[i + 1:i + 2] == "-":
            if s[i + 2:i + 4] == "[[":                 # block comment
                end = s.find("]]", i + 4)
                i = end + 2 if end != -1 else n
            else:                                      # line comment
                nl = s.find("\n", i)
                i = nl if nl != -1 else n
        elif c in "\"'":
            j, buf = i + 1, []
            while j < n and s[j] != c:
                if s[j] == "\\" and j + 1 < n:
                    buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(s[j + 1], s[j + 1]))
                    j += 2
                else:
                    buf.append(s[j])
                    j += 1
            toks.append(("str", "".join(buf)))
            i = j + 1
        elif c in "{}[]=,;":
            toks.append((c, c))
            i += 1
        elif c.isdigit() or (c == "-" and s[i + 1:i + 2].isdigit()):
            j = i + 1
            while j < n and (s[j].isdigit() or s[j] == "."):
                j += 1
            toks.append(("num", s[i:j]))
            i = j
        elif c.isalpha() or c == "_":
            j = i + 1
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            toks.append(("name", s[i:j]))
            i = j
        else:
            i += 1
    return toks


class _Parser:
    def __init__(self, toks, start=0):
        self.t = toks
        self.i = start

    def _peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def _next(self):
        tok = self._peek()
        self.i += 1
        return tok

    def value(self):
        kind, val = self._peek()
        if kind == "{":
            return self.table()
        self._next()
        if kind == "str":
            return val
        if kind == "num":
            return float(val) if "." in val else int(val)
        if kind == "name":
            return {"true": True, "false": False, "nil": None}.get(val, val)
        raise ValueError(f"unexpected token {kind!r}")

    def table(self):
        self._next()  # consume '{'
        out, auto = {}, 1
        while True:
            kind, _ = self._peek()
            if kind == "}" or kind is None:
                self._next()
                break
            if kind == "[":
                self._next()
                key = self.value()
                if self._next()[0] != "]" or self._next()[0] != "=":
                    raise ValueError("malformed [key]=value")
                out[key] = self.value()
            elif kind == "name" and self.t[self.i + 1:self.i + 2] == [("=", "=")]:
                key = self._next()[1]
                self._next()  # '='
                out[key] = self.value()
            else:
                out[auto] = self.value()
                auto += 1
            if self._peek()[0] in (",", ";"):
                self._next()
        return out


_MAX_MIGRATE_BYTES = 4 * 1024 * 1024   # an announcer cfg is tiny; bound a hostile/huge file


def _load_table(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read(_MAX_MIGRATE_BYTES + 1)
    if len(text) > _MAX_MIGRATE_BYTES:
        raise ValueError(f"{os.path.basename(path)} too large to import")
    toks = _tokenize(text)
    try:
        for idx, (kind, _) in enumerate(toks):
            if kind == "{":
                return _Parser(toks, idx).table()
    except RecursionError:
        raise ValueError(f"{os.path.basename(path)} nested too deeply")
    raise ValueError(f"no table found in {os.path.basename(path)}")


def import_categories(dat_path):
    """Parse a hub-side ptx_freshstuff_categories.dat (Lua
    `return { ["Movies_1080p"] = "Movies_1080p", ... }`) into a sorted list of
    category names. Not part of the announcer migration - it lives on the hub,
    not in the announcer config - so it is imported separately."""
    tbl = _load_table(dat_path)
    if not isinstance(tbl, dict):
        raise ValueError("file is not a category table")
    names = sorted({str(k).strip() for k in tbl.keys() if str(k).strip()})
    return names


# -- announcer -> Herald hub -------------------------------------------------

# rule fields the old announcer and Herald share (copied verbatim if present).
_RULE_KEYS = (
    "rulename", "active", "path", "command", "category", "alibicheck",
    "alibinick", "checkdirs", "checkdirsnfo", "checkdirssfv", "checkfiles",
    "checkspaces", "checkage", "maxage", "daydirscheme", "zeroday",
    "skip_hidden", "max_per_extension", "max_per_extension_recursive",
    "max_per_extension_max_depth",
)


def _keys_as_list(tbl):
    """An announcer blacklist/whitelist is a {name = true} set -> list of names."""
    return list(tbl.keys()) if isinstance(tbl, dict) else []


def _convert_rule(raw):
    rule = default_rule(str(raw.get("rulename", "")))
    for key in _RULE_KEYS:
        if key in raw:
            rule[key] = raw[key]
    rule["blacklist"] = _keys_as_list(raw.get("blacklist"))
    rule["whitelist"] = _keys_as_list(raw.get("whitelist"))
    return rule


def _cfg_dir(base):
    """Accept either the announcer root (has cfg/) or the cfg/ dir itself."""
    if os.path.isfile(os.path.join(base, "cfg", "hub.lua")):
        return os.path.join(base, "cfg")
    if os.path.isfile(os.path.join(base, "hub.lua")):
        return base
    raise ValueError("no announcer config found here (looked for cfg/hub.lua)")


def import_announcer(base_dir):
    """Return (hub_dict, summary_str) for the announcer at base_dir.
    Raises ValueError with a clear message on any problem."""
    cfg = _cfg_dir(base_dir)

    try:
        hub_tbl = _load_table(os.path.join(cfg, "hub.lua"))
    except (OSError, ValueError) as e:
        raise ValueError(f"could not read hub.lua: {e}")

    cfg_tbl = {}
    if os.path.isfile(os.path.join(cfg, "cfg.lua")):
        try:
            cfg_tbl = _load_table(os.path.join(cfg, "cfg.lua"))
        except (OSError, ValueError):
            cfg_tbl = {}

    keyp = str(hub_tbl.get("keyp", "") or "")
    hub = default_hub(str(hub_tbl.get("name", "") or "Imported hub"))
    hub.update(
        host=str(hub_tbl.get("addr", "") or ""),
        port=int(hub_tbl.get("port", 5001) or 5001),
        nick=str(hub_tbl.get("nick", "") or ""),
        password=str(hub_tbl.get("pass", "") or ""),
        keyprint="" if keyp in ("", "unknown") else keyp,
        description=str(cfg_tbl.get("botdesc", "") or ""),
        botslots=int(cfg_tbl.get("botslots", 0) or 0),
        botshare=int(cfg_tbl.get("botshare", 0) or 0),
        botupload=int(cfg_tbl.get("botupload", 0) or 0),
        sleeptime=int(cfg_tbl.get("sleeptime", 10) or 10),
        announceinterval=int(cfg_tbl.get("announceinterval", 300) or 300),
        sockettimeout=int(cfg_tbl.get("sockettimeout", 60) or 60),
    )

    # preserve the bot identity so the hub recognises the same CID
    id_path = os.path.join(cfg, "id.lua")
    if os.path.isfile(id_path):
        idt = open(id_path, encoding="utf-8", errors="replace").read()
        pid = re.search(r"pid\s*=\s*['\"]([A-Z2-7]+)", idt)
        cid = re.search(r"cid\s*=\s*['\"]([A-Z2-7]+)", idt)
        if pid and cid:
            hub["pid"], hub["cid"] = pid.group(1), cid.group(1)

    # rules
    rules = []
    rules_path = os.path.join(cfg, "rules.lua")
    if os.path.isfile(rules_path):
        try:
            raw = _load_table(rules_path)
            for key in sorted(k for k in raw if isinstance(k, int)):
                if isinstance(raw[key], dict):
                    rules.append(_convert_rule(raw[key]))
        except (OSError, ValueError) as e:
            raise ValueError(f"could not read rules.lua: {e}")
    hub["rules"] = rules

    ident = " (identity preserved)" if hub.get("cid") else ""
    summary = (f"Imported hub '{hub['name']}' with {len(rules)} rule(s){ident}.\n"
               f"Review it in the Config and Rules tabs, then Connect.")
    return hub, summary
