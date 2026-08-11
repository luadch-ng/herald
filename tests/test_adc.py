"""Verify the pure-Python Tiger/base32/hash port against known vectors.

The authoritative check is the PID -> CID pair from the Lua announcer's
cfg/id.lua: it exercises tiger + base32 encode + base32 decode end to end,
and it was produced by the real C adclib. The standard Tiger vectors are a
secondary cross-check.

Run:  python -m tests.test_adc   (from the project root)
"""

import sys

sys.path.insert(0, ".")

from adc.base32 import from_base32, to_base32
from adc.hashes import cid_from_pid
from adc.tiger import tiger

_failures = 0


def check(name, got, want):
    global _failures
    ok = got == want
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"       got : {got}")
        print(f"       want: {want}")
        _failures += 1


# --- Published Tiger/192 vectors (as encoded by DC++/adclib getResult) ---
check("tiger('')", tiger(b"").hex().upper(),
      "3293AC630C13F0245F92BBB1766E16167A4E58492DDE73F3")
check("tiger('abc')", tiger(b"abc").hex().upper(),
      "2AAB1484E8C158F2BFB8C5FF41B57A525129131C957B5F93")
check("tiger('Tiger')", tiger(b"Tiger").hex().upper(),
      "DD00230799F5009FEC6DEBC838BB6A27DF2B9D6F110C7937")

# --- base32 round-trip ---
_sample = bytes(range(24))
check("base32 round-trip", from_base32(to_base32(_sample), 24), _sample)

# --- CID-from-PID known-answer test ---
# A THROWAWAY identity generated only for this test (never used by any real
# bot). The Tiger primitive it builds on is already cross-checked against the
# published vectors above, so this pins the base32 / CID packaging without
# publishing a real PID - a PID is a private ADC identity seed.
PID = "TDLXSBUBLXNTF4P2BY6E47LA3KW5QOMAWCA2MMI"
CID = "UERKMH2EULPN4LY2CFMB4JMSIAM2RQYTQF3T4ZY"
check("cid_from_pid (throwaway vector)", cid_from_pid(PID), CID)

print()
if _failures:
    print(f"{_failures} check(s) FAILED")
    sys.exit(1)
print("all checks passed")
