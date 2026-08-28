"""ULID run identifiers.

IDs are 26-character Crockford Base32 ULIDs: time-sortable and unique.
The plan example ``01JXYZ`` is a shortened illustration of this form.
"""

from __future__ import annotations

import os
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_LOCK = threading.Lock()
_LAST_MS = -1
_LAST_ENTROPY = 0


def new_ulid() -> str:
    """Return a monotonic ULID string."""
    global _LAST_MS, _LAST_ENTROPY
    now_ms = int(time.time() * 1000)
    with _LOCK:
        if now_ms == _LAST_MS:
            _LAST_ENTROPY += 1
            if _LAST_ENTROPY >= 2**80:
                now_ms += 1
                _LAST_ENTROPY = int.from_bytes(os.urandom(10), "big")
        else:
            _LAST_ENTROPY = int.from_bytes(os.urandom(10), "big")
        _LAST_MS = now_ms
        entropy = _LAST_ENTROPY
    return encode_ulid(now_ms, entropy.to_bytes(10, "big"))


def encode_ulid(timestamp_ms: int, entropy: bytes) -> str:
    """Encode a 48-bit timestamp and 10-byte entropy as a ULID."""
    if not 0 <= timestamp_ms < 2**48:
        raise ValueError("timestamp out of ULID range")
    if len(entropy) != 10:
        raise ValueError("entropy must be 10 bytes")
    value = (timestamp_ms << 80) | int.from_bytes(entropy, "big")
    chars = ["0"] * 26
    for index in range(25, -1, -1):
        chars[index] = _ALPHABET[value & 31]
        value >>= 5
    return "".join(chars)


def is_run_id(value: str) -> bool:
    """Return True if value is a full 26-character ULID."""
    if len(value) != 26:
        return False
    allowed = set(_ALPHABET)
    return all(char in allowed for char in value.upper())
