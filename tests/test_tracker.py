"""Verify the release-completion tracker: SFV gate, settle timer, temp/0-byte
filtering, and the per-folder state machine. Time is injected so the settle
windows are deterministic.

Run:  python tests/test_tracker.py   (from the project root)
"""

import os
import sys
import tempfile

sys.path.insert(0, ".")

from adc import complete, config
from adc.tracker import ReleaseTracker

_fail = 0


def check(name, cond):
    global _fail
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        _fail += 1


def mkfile(path, size=10):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


def mkrule(root, **over):
    r = config.default_rule("test")
    r.update(active=True, path=root, category="Movies_1080p", command="+addrel",
             checkdirs=True, checkfiles=False, blacklist=["(nuked)"],
             max_per_extension=None, settle_seconds=5, sfv_level="size_stable")
    r.update(over)
    return r


def names(ready):
    return sorted(r.name for r in ready)


# --- complete.py units ------------------------------------------------------
root = tempfile.mkdtemp()
rel = os.path.join(root, "Cool.Movie.2024.1080p-GRP")
mkfile(os.path.join(rel, "cool.r00"), 100)
mkfile(os.path.join(rel, "cool.rar"), 100)
with open(os.path.join(rel, "cool.sfv"), "w") as fh:
    fh.write("; generated\ncool.rar 12345678\ncool.r00 abcdef12\n")
mkfile(os.path.join(rel, "Sample", "s.sfv"), 5)  # sample SFV must be ignored

has_sfv, present, _ = complete.sfv_status(rel)
check("sfv_status finds top SFV, all present", has_sfv and present)
check("parse_sfv reads 2 entries", len(complete.parse_sfv(os.path.join(rel, "cool.sfv"))) == 2)
check("Sample/ SFV excluded from discovery",
      all("Sample" not in p for p in complete.find_sfvs(rel)))

# temp + 0-byte filtering
tmp = tempfile.mkdtemp()
mkfile(os.path.join(tmp, "a.rar"), 50)
mkfile(os.path.join(tmp, "a.rar.tmp"), 999)   # temp -> ignored
open(os.path.join(tmp, "empty.nfo"), "w").close()  # 0-byte -> not counted
cnt, total, _ = complete.snapshot(tmp)
check("snapshot ignores temp + 0-byte (1 file, 50 bytes)", cnt == 1 and total == 50)


# --- tracker: SFV size_stable needs the quiet window ------------------------
r1 = tempfile.mkdtemp()
d = os.path.join(r1, "Rel.A.1080p-GRP")
with open(os.path.join(os.makedirs(d) or d, "a.sfv"), "w") as fh:
    fh.write("a.rar 11111111\n")            # SFV present, rar MISSING
t = ReleaseTracker([mkrule(r1)], set())
check("SFV present but file missing -> not ready", names(t.poll(now=100)) == [])
mkfile(os.path.join(d, "a.rar"), 100)        # rar appears at t=101
check("all present but not yet quiet -> not ready", names(t.poll(now=101)) == [])
check("quiet window elapsed -> ready", names(t.poll(now=110)) == ["Rel.A.1080p-GRP"])
check("already announced -> not re-emitted", names(t.poll(now=120)) == [])

# --- tracker: sfv_level=present announces immediately -----------------------
r2 = tempfile.mkdtemp()
d2 = os.path.join(r2, "Rel.B-GRP")
os.makedirs(d2)
mkfile(os.path.join(d2, "b.rar"), 100)
with open(os.path.join(d2, "b.sfv"), "w") as fh:
    fh.write("b.rar 22222222\n")
t2 = ReleaseTracker([mkrule(r2, sfv_level="present")], set())
check("sfv_level=present -> ready on first poll", names(t2.poll(now=0)) == ["Rel.B-GRP"])

# --- tracker: no SFV -> settle timer ----------------------------------------
r3 = tempfile.mkdtemp()
d3 = os.path.join(r3, "Rel.C.WEB-TEAM")
os.makedirs(d3)
mkfile(os.path.join(d3, "movie.mkv"), 100)
t3 = ReleaseTracker([mkrule(r3)], set())
check("no SFV, not yet settled -> not ready", names(t3.poll(now=0)) == [])
check("no SFV, settled -> ready", names(t3.poll(now=6)) == ["Rel.C.WEB-TEAM"])

# --- tracker: folder with only a temp file never announces ------------------
r4 = tempfile.mkdtemp()
d4 = os.path.join(r4, "Rel.D-GRP")
os.makedirs(d4)
mkfile(os.path.join(d4, "d.rar.tmp"), 100)   # only a temp file
t4 = ReleaseTracker([mkrule(r4)], set())
check("only-temp folder stays empty -> never ready", names(t4.poll(now=99)) == [])

# separator rules are GUI-only dividers: scan_rules must never scan them, even
# if one is (mis)marked active over a real folder that holds a release.
from adc import announce
r5 = tempfile.mkdtemp()
os.makedirs(os.path.join(r5, "Rel.E-GRP"))
_sep = dict(config.default_rule(), active=True, path=r5, separator=True)
check("separator rule skipped by scan_rules despite active + real dir",
      announce.scan_rules([_sep], set()) == [])
check("default_rule separator defaults False",
      config.default_rule().get("separator") is False)

# "Require valid SFV" must reject a 0-byte placeholder, not just any existing file
r6 = tempfile.mkdtemp()
with open(os.path.join(r6, "x.sfv"), "w") as _fh:
    _fh.write("a.rar 00000000\n")
mkfile(os.path.join(r6, "a.rar"), 0)     # listed file exists but is empty
check("valid-sfv gate rejects a 0-byte listed file",
      announce._directory_has_valid_sfv(r6) is False)
mkfile(os.path.join(r6, "a.rar"), 100)   # now has content
check("valid-sfv gate accepts a non-empty listed file",
      announce._directory_has_valid_sfv(r6) is True)

# A tab-delimited SFV listing a MISSING file must NOT pass the gate. The old
# space-only split silently skipped any line it could not split on a literal
# space, so the gate passed with nothing verified -> early announce (#3).
r7 = tempfile.mkdtemp()
with open(os.path.join(r7, "y.sfv"), "w") as _fh:
    _fh.write("missing.rar\tdeadbeef\n")   # tab between name and CRC
check("valid-sfv gate rejects a tab-delimited SFV whose file is missing",
      announce._directory_has_valid_sfv(r7) is False)
mkfile(os.path.join(r7, "missing.rar"), 100)
check("valid-sfv gate accepts once the tab-delimited listed file is present",
      announce._directory_has_valid_sfv(r7) is True)

# A NUL byte can never match a real filename, so a NUL-bearing SFV entry is
# useless; the parser must drop it so it neither sits permanently "missing"
# (blocking the whole SFV from completing) nor reaches an os.stat/open call
# (an embedded NUL raises ValueError, which the callers' `except OSError`
# would not catch). (#3 hardening)
r8 = tempfile.mkdtemp()
with open(os.path.join(r8, "z.sfv"), "w") as _fh:
    _fh.write("bad\x00name.rar deadbeef\n")
check("parse_sfv drops a NUL-byte name",
      complete.parse_sfv(os.path.join(r8, "z.sfv")) == [])
# the sole entry dropped -> no usable SFV remains (not a half-parsed one that
# would block the release forever)
check("NUL-only SFV yields no usable SFV",
      complete.sfv_status(r8)[0] is False)

# diagnose() (dev debug aid for #3): lists present + missing SFV files and the
# real tree contents, and never raises.
r9 = tempfile.mkdtemp()
with open(os.path.join(r9, "d.sfv"), "w") as _fh:
    _fh.write("there.rar aaaaaaaa\ngone.rar bbbbbbbb\n")
mkfile(os.path.join(r9, "there.rar"), 123)
_diag = "\n".join(complete.diagnose(r9))
check("diagnose lists a present SFV file with its size", "present there.rar 123 B" in _diag)
check("diagnose flags a missing SFV file", "MISSING gone.rar" in _diag)
check("diagnose lists the real tree files", "tree: 2 non-temp non-empty files" in _diag)

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all tracker checks passed")
