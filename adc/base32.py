"""DC++/ADC base32, ported verbatim from adclib/base32.cpp.

NOT RFC 4648: the bit-packing order differs, and there is no `=` padding.
Alphabet is the standard ABCDEFGHIJKLMNOPQRSTUVWXYZ234567. Used for PID, CID,
SID, keyprint and the GPA salt.
"""

_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

# Reverse lookup: char -> 5-bit value, or -1 for anything not in the alphabet.
_TABLE = [-1] * 256
for _i, _ch in enumerate(_ALPHA):
    _TABLE[ord(_ch)] = _i


def to_base32(src: bytes) -> str:
    """Encode `src` bytes to a DC++ base32 string (no padding)."""
    size = len(src)
    key = 0
    i = 0
    out = []
    while i < size:
        if key > 3:
            ch = src[i] & (0xFF >> key)
            key = (key + 5) % 8
            ch = (ch << key) & 0xFF
            if i + 1 < size:
                ch |= src[i + 1] >> (8 - key)
            i += 1
        else:
            ch = (src[i] >> (8 - (key + 5))) & 0x1F
            key = (key + 5) % 8
            if key == 0:
                i += 1
        out.append(_ALPHA[ch & 0x1F])
    return "".join(out)


def from_base32(src: str, size: int) -> bytes:
    """Decode `src` into exactly `size` bytes (extra chars ignored, like the C)."""
    buf = bytearray(size)
    key = 0
    dst = 0
    for ch in src:
        temp = _TABLE[ord(ch) & 0xFF] if ord(ch) < 256 else -1
        if temp == -1:
            continue
        if key <= 3:
            key = (key + 5) % 8
            if key == 0:
                buf[dst] = (buf[dst] | temp) & 0xFF
                dst += 1
                if dst == size:
                    break
            else:
                buf[dst] = (buf[dst] | (temp << (8 - key))) & 0xFF
        else:
            key = (key + 5) % 8
            buf[dst] = (buf[dst] | (temp >> key)) & 0xFF
            dst += 1
            if dst == size:
                break
            buf[dst] = (buf[dst] | (temp << (8 - key))) & 0xFF
    return bytes(buf)
