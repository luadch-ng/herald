"""Tiger/192 hash, ADC / DC++ variant.

Ported bit-for-bit from luadch-ng-announcer/adclib/tiger.cpp:
- 3 passes, little-endian word order,
- original-Tiger finalisation padding (0x01 first pad byte, NOT Tiger2's 0x80),
- result is the raw little-endian bytes of the three 64-bit state words.

Verified against the real adclib output via the PID -> CID derivation in
cfg/id.lua (see tests/test_adc.py). Perf is irrelevant here: a login does a
handful of hashes.
"""

import struct

from ._table import TABLE

_MASK = (1 << 64) - 1

# Initial state (adclib TigerHash constructor res[0..2]).
_INIT = (0x0123456789ABCDEF, 0xFEDCBA9876543210, 0xF096A5B4C3B2E187)


def _round(a, b, c, x, mul):
    # Mirrors the C `round(a,b,c,x,mul)` macro. Byte indices into c:
    #   a -= t1[c.0] ^ t2[c.2] ^ t3[c.4] ^ t4[c.6]
    #   b += t4[c.1] ^ t3[c.3] ^ t2[c.5] ^ t1[c.7]
    #   b *= mul
    c ^= x
    a = (a - (TABLE[c & 0xFF]
              ^ TABLE[256 + ((c >> 16) & 0xFF)]
              ^ TABLE[512 + ((c >> 32) & 0xFF)]
              ^ TABLE[768 + ((c >> 48) & 0xFF)])) & _MASK
    b = (b + (TABLE[768 + ((c >> 8) & 0xFF)]
              ^ TABLE[512 + ((c >> 24) & 0xFF)]
              ^ TABLE[256 + ((c >> 40) & 0xFF)]
              ^ TABLE[(c >> 56) & 0xFF])) & _MASK
    b = (b * mul) & _MASK
    return a, b, c


def _pass(a, b, c, x, mul):
    # The C `pass` macro: 8 rounds rotating the (a,b,c) roles.
    a, b, c = _round(a, b, c, x[0], mul)
    b, c, a = _round(b, c, a, x[1], mul)
    c, a, b = _round(c, a, b, x[2], mul)
    a, b, c = _round(a, b, c, x[3], mul)
    b, c, a = _round(b, c, a, x[4], mul)
    c, a, b = _round(c, a, b, x[5], mul)
    a, b, c = _round(a, b, c, x[6], mul)
    b, c, a = _round(b, c, a, x[7], mul)
    return a, b, c


def _key_schedule(x):
    x = list(x)
    x[0] = (x[0] - (x[7] ^ 0xA5A5A5A5A5A5A5A5)) & _MASK
    x[1] ^= x[0]
    x[2] = (x[2] + x[1]) & _MASK
    x[3] = (x[3] - (x[2] ^ ((~x[1] & _MASK) << 19 & _MASK))) & _MASK
    x[4] ^= x[3]
    x[5] = (x[5] + x[4]) & _MASK
    x[6] = (x[6] - (x[5] ^ ((~x[4] & _MASK) >> 23))) & _MASK
    x[7] ^= x[6]
    x[0] = (x[0] + x[7]) & _MASK
    x[1] = (x[1] - (x[0] ^ ((~x[7] & _MASK) << 19 & _MASK))) & _MASK
    x[2] ^= x[1]
    x[3] = (x[3] + x[2]) & _MASK
    x[4] = (x[4] - (x[3] ^ ((~x[2] & _MASK) >> 23))) & _MASK
    x[5] ^= x[4]
    x[6] = (x[6] + x[5]) & _MASK
    x[7] = (x[7] - (x[6] ^ 0x0123456789ABCDEF)) & _MASK
    return x


def _compress(state, block):
    # block: 8 little-endian 64-bit words (one 64-byte input block).
    a, b, c = state
    aa, bb, cc = a, b, c
    x = list(block)
    for pass_no in range(3):
        if pass_no != 0:
            x = _key_schedule(x)
        mul = 5 if pass_no == 0 else (7 if pass_no == 1 else 9)
        a, b, c = _pass(a, b, c, x, mul)
        a, b, c = c, a, b  # C: tmpa=a; a=c; c=b; b=tmpa
    a ^= aa
    b = (b - bb) & _MASK
    c = (c + cc) & _MASK
    return a, b, c


def tiger(data: bytes) -> bytes:
    """Return the 24-byte Tiger digest of `data` (ADC byte order)."""
    msg_bits = (len(data) * 8) & _MASK
    buf = bytearray(data)
    buf.append(0x01)
    while len(buf) % 64 != 56:
        buf.append(0x00)
    buf += struct.pack("<Q", msg_bits)

    state = _INIT
    for off in range(0, len(buf), 64):
        words = struct.unpack_from("<8Q", buf, off)
        state = _compress(state, words)
    return struct.pack("<3Q", *state)
