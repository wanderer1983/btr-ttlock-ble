"""Pairing a lock in setting mode, and resetting one back to it.

The sequence follows the one kind3r/ttlock-sdk-js runs in
`TTLock.initLock` (GPL-3.0), which in turn mirrors the official app:

1. `COMM_INITIALIZATION` (0x45), empty, under the default key;
2. `COMM_GET_AES_KEY` (0x19) with the literal `SCIENER`; the reply's data
   is the 16-byte AES key every later frame is encrypted with;
3. `COMM_ADD_ADMIN` (0x56): two big-endian int32s - the admin password
   and the unlock key, both chosen by the app - then `SCIENER`;
4. time calibration and a feature search, best effort;
5. the keypad admin code: read with `COMM_GET_ADMIN_CODE` (0x65) where
   the lock supports it, else set with `COMM_SET_ADMIN_KEYBOARD_PWD`
   (0x53) as one byte per digit;
6. `COMM_GET_ALARM_ERRCORD_OR_OPERATION_FINISHED` (0x57), empty, which
   tells the lock pairing is over and takes it out of setting mode.

A reset is `COMM_RESET_LOCK` (0x52), empty, after the admin handshake.
"""

from __future__ import annotations

import secrets

from .envelope import RESPONSE_SUCCESS, parse_response_status

# Every lock in setting mode answers under this key until it hands out its own.
DEFAULT_AES_KEY = bytes.fromhex("987623e8a923a1bb3d9e7d0378124588")

_MAGIC = b"SCIENER"
_AES_KEY_LENGTH = 16
# The app draws both numbers below 10^8, which keeps them positive int32s.
_ADMIN_NUMBER_BOUND = 100_000_000
ADMIN_KEYPAD_CODE_LENGTH = 7


def generate_admin_number() -> int:
    """Return a fresh admin password or unlock key, as the app picks them."""
    return secrets.randbelow(_ADMIN_NUMBER_BOUND - 1) + 1


def generate_admin_keypad_code() -> str:
    """Return a fresh keypad admin code: seven random digits."""
    return "".join(str(secrets.randbelow(10)) for _ in range(ADMIN_KEYPAD_CODE_LENGTH))


def payload_initialization() -> bytes:
    """COMM_INITIALIZATION - empty."""
    return b""


def payload_pairing_aes_key() -> bytes:
    """COMM_GET_AES_KEY while pairing - the literal `SCIENER`, as kind3r sends it.

    `session.payload_get_aes_key` carries a vendor token instead; the
    client falls back to it for a lock that refuses this one.
    """
    return _MAGIC


def parse_aes_key_response(plaintext: bytes) -> bytes:
    """Return the 16-byte AES key a successful COMM_GET_AES_KEY reply carries."""
    _echo, status, data = parse_response_status(plaintext)
    if status != RESPONSE_SUCCESS:
        raise RuntimeError(f"lock refused to hand out its AES key: {data.hex()}")
    if len(data) < _AES_KEY_LENGTH:
        raise ValueError(f"AES key reply too short: {len(data)} bytes")
    return data[:_AES_KEY_LENGTH]


def payload_add_admin(admin_ps: int, unlock_key: int) -> bytes:
    """COMM_ADD_ADMIN - `[admin_ps BE int32][unlock_key BE int32]SCIENER`."""
    return admin_ps.to_bytes(4, "big") + unlock_key.to_bytes(4, "big") + _MAGIC


def parse_add_admin_response(plaintext: bytes) -> None:
    """Raise unless the lock accepted the new admin."""
    _echo, status, data = parse_response_status(plaintext)
    if status != RESPONSE_SUCCESS:
        raise RuntimeError(f"lock refused the new admin: {data.hex()}")


def payload_get_admin_code() -> bytes:
    """COMM_GET_ADMIN_CODE - empty."""
    return b""


def parse_admin_code_response(plaintext: bytes) -> str:
    """Return the keypad admin code a lock reports, `""` when it has none.

    Data is `[battery][length][digits...]`, the digits as ASCII.
    """
    _echo, status, data = parse_response_status(plaintext)
    if status != RESPONSE_SUCCESS:
        raise RuntimeError(f"lock refused to report its admin code: {data.hex()}")
    if len(data) < 2:
        return ""
    length = data[1]
    return data[2 : 2 + length].decode("ascii", errors="ignore")


def payload_set_admin_keypad_code(code: str) -> bytes:
    """COMM_SET_ADMIN_KEYBOARD_PWD - one byte per digit, its numeric value."""
    if not code.isdigit():
        raise ValueError("the admin keypad code must be digits only")
    return bytes(int(digit) for digit in code)


def payload_operate_finished() -> bytes:
    """COMM_GET_ALARM_ERRCORD_OR_OPERATION_FINISHED - empty; ends pairing."""
    return b""


def payload_reset_lock() -> bytes:
    """COMM_RESET_LOCK - empty."""
    return b""
