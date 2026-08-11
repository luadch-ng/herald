"""ADC wire helpers: field escaping and a random PID generator.

Escaping mirrors adclib.cpp escape/unescape: space -> \\s, newline -> \\n,
backslash -> \\\\. Applied to every free-text INF/MSG field.
"""

import os

from .base32 import to_base32
from .hashes import cid_from_pid


def escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(" ", "\\s").replace("\n", "\\n")


def unescape(s: str) -> str:
    out = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            out.append({"s": " ", "n": "\n", "\\": "\\"}.get(nxt, ""))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def new_identity():
    """Generate a fresh (pid, cid) pair from 24 CSPRNG bytes.

    The PID is private; the hub only ever sees the CID (and verifies
    CID == tiger(PID) on the PD/ID fields). Announcer reuses one stored
    pair across runs; a fresh pair is fine for the prototype.
    """
    pid = to_base32(os.urandom(24))
    return pid, cid_from_pid(pid)
