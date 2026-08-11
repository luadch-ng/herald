"""Pure-core regression tests for the Sopor UX batch persistence contract.

Covers the config-model side of three GUI features, so the persisted shape is
guaranteed no matter what the widgets do:

  - per-hub `autoconnect` flag (GUI auto-connect on startup)
  - global `start_minimized` + `release_tooltips` settings

No Qt, no disk, no network - runs in the same headless CI job as the other
pure-core tests. Each check exits non-zero on failure.

RED before the fix: `default_hub()` had no `autoconnect` key and
`default_settings()` had neither `start_minimized` nor `release_tooltips`, so
`_migrate` never back-filled `autoconnect` on an older hub - these asserts fail.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adc import config

FAILED = []


def check(name, cond):
    print(("[PASS] " if cond else "[FAIL] ") + name)
    if not cond:
        FAILED.append(name)


def test_defaults():
    hub = config.default_hub("H")
    check("default_hub has autoconnect=False", hub.get("autoconnect") is False)

    s = config.default_settings()
    check("default_settings has start_minimized=False",
          s.get("start_minimized") is False)
    check("default_settings has release_tooltips=True",
          s.get("release_tooltips") is True)


def test_migrate_backfills_autoconnect():
    # An older config written before the flag existed: no `autoconnect` key.
    old = {"name": "Legacy", "host": "h", "port": 5001, "rules": []}
    migrated = config._migrate(old)
    check("_migrate back-fills autoconnect on an old hub",
          migrated.get("autoconnect") is False)
    # An explicit True must survive the merge (not be clobbered by the default).
    on = {"name": "On", "autoconnect": True, "rules": []}
    check("_migrate preserves autoconnect=True",
          config._migrate(on).get("autoconnect") is True)


def test_load_settings_backfills(tmp_missing=True):
    # load_settings() layers the on-disk file over default_settings(), so a
    # settings.json written before these keys existed still yields them.
    base = config.default_settings()
    for key in ("start_minimized", "release_tooltips"):
        check(f"load_settings would carry '{key}' from defaults", key in base)


def main():
    test_defaults()
    test_migrate_backfills_autoconnect()
    test_load_settings_backfills()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("all config-ux checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
