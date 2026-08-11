"""Release tracker: decides WHEN a name-filtered candidate is complete.

announce.scan_rules finds candidates that pass the name/blacklist/etc filters.
The tracker sits on top and holds a per-folder state machine so a candidate is
only emitted once it is actually *done* being written:

    seen -> writing (snapshot changing) -> complete -> (emitted, then in `already`)

Completion per candidate (see complete.py):
  - loose file        : size-stable for `settle_seconds`
  - folder, has SFV   : every SFV file present, then per `sfv_level`:
        "present"     -> announce as soon as all present
        "size_stable" -> also require the tree quiet for `settle_seconds`
        "crc"         -> also require every SFV CRC to verify
  - folder, no SFV    : tree snapshot unchanged for `settle_seconds`

The "quiet for N seconds" test is anchored on the wall-clock time WE last saw
the snapshot change, not on poll cadence - so waking early (via watchdog) never
declares a release done prematurely.
"""

import time

from . import announce, complete

SFV_PRESENT = "present"
SFV_SIZE_STABLE = "size_stable"
SFV_CRC = "crc"

DEFAULT_SETTLE = 5


class _State:
    __slots__ = ("snapshot", "changed_at", "announced", "writing_logged")

    def __init__(self, now):
        self.snapshot = None
        self.changed_at = now
        self.announced = False
        self.writing_logged = False


class ReleaseTracker:
    def __init__(self, rules, already, log=lambda *_: None):
        self.rules = rules
        self.already = already
        self.log = log
        self._state = {}   # release name -> _State

    def poll(self, now=None):
        """Return the list of candidates that just became complete. The caller
        sends them and adds their names to `already`."""
        if now is None:
            now = time.monotonic()   # settle timing must be immune to wall-clock steps
        candidates = announce.scan_rules(self.rules, self.already)
        present = set()
        ready = []
        for cand in candidates:
            present.add(cand.name)
            st = self._state.get(cand.name)
            if st is None:
                st = _State(now)
                self._state[cand.name] = st
            snap = (complete.snapshot(cand.path) if cand.is_dir
                    else complete.file_snapshot(cand.path))
            if snap != st.snapshot:
                st.snapshot = snap
                st.changed_at = now
                if not st.writing_logged:
                    self.log(f"Detected, waiting for completion: {cand.name}")
                    st.writing_logged = True
            if st.announced:
                continue
            if self._is_complete(cand, st, now):
                st.announced = True
                ready.append(cand)
        # Free per-release state once it is no longer needed: either the release
        # has been announced AND recorded in `already` (so scan_rules filters it
        # out of every future poll - keeping its _State would leak one entry per
        # release for the life of the connection), or it vanished before
        # completing. The just-announced-this-poll case is retained until the
        # caller adds it to `already`, so the `if st.announced` guard above still
        # prevents a re-announce in that window.
        for name in list(self._state):
            if name in self.already or (name not in present and not self._state[name].announced):
                del self._state[name]
        return ready

    def _is_complete(self, cand, st, now):
        settle = int(cand.rule.get("settle_seconds", DEFAULT_SETTLE))
        quiet = (now - st.changed_at) >= settle

        if not cand.is_dir:
            return quiet   # loose file: size-stable

        if not st.snapshot or st.snapshot[0] == 0:
            return False   # empty folder: nothing (real) inside yet

        has_sfv, all_present, verify_crc = complete.sfv_status(cand.path)
        if has_sfv:
            if not all_present:
                return False
            level = cand.rule.get("sfv_level", SFV_SIZE_STABLE)
            if level == SFV_PRESENT:
                return True
            if level == SFV_CRC:
                return quiet and verify_crc()
            return quiet   # size_stable (default)

        # no SFV -> pure settle
        return quiet
