"""ADC login hashes, mirroring adclib.cpp's hash_pid / hash_pas.

CID  = base32(tiger(base32decode(PID)))          -- adclib.hash
HPAS = base32(tiger(password_bytes .. salt))     -- adclib.hashpas
"""

from .base32 import from_base32, to_base32
from .tiger import tiger

_CID_SIZE = 24  # ADC CID / PID raw length (192 bits)


def cid_from_pid(pid_b32: str) -> str:
    """Derive the CID from a base32 PID (adclib.hash)."""
    raw = from_base32(pid_b32, _CID_SIZE)
    return to_base32(tiger(raw))


def hash_pas(password: str, salt_b32: str) -> str:
    """Compute the HPAS response to an IGPA salt (adclib.hashpas).

    saltBytes = len(salt) * 5 // 8 (matches the C length derivation).
    """
    salt_bytes = len(salt_b32) * 5 // 8
    salt = from_base32(salt_b32, salt_bytes)
    return to_base32(tiger(password.encode("utf-8") + salt))
