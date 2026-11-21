"""Card and fingerprint management - the write sub-ops of CMD 0x05 and CMD 0x06.

The list queries live in `card` and `fingerprint` and are confirmed on
hardware. Everything here - enroll, delete, clear and change validity -
is ported from ttlock-sdk-js (`ManageICCommand` / `ManageFRCommand`) and
the esphome-ttlock Python rewrite of it, and is NOT yet confirmed
against a physical lock. The byte layouts below are exactly the ones
those two implementations send.

Identifiers are passed as raw bytes, the same bytes the list queries
return: a card is its 4- or 8-byte `CardEntry.card_id`, a fingerprint is
the 6-byte `FingerprintEntry.number` (its `fingerprint_id` followed by
its `slot`), which is what the vendor SDK calls `fpNumber`.

An ADD request answers with `[battery][op_echo=ADD][status]...`, where
`status` is an `EnrollmentStatus`; the lock then pushes further frames
on the same opcode as the credential is presented, ending with
`ADD_SUCCESS` and the new credential's number.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..constants import CredentialOperate
from .envelope import RESPONSE_SUCCESS, parse_response_status
from .fingerprint import END_DATE_SENTINEL, START_DATE_SENTINEL

if TYPE_CHECKING:
    import datetime as dt

FINGERPRINT_NUMBER_LENGTH = 6
_CARD_ID_LENGTHS = frozenset({4, 8})
_UNUSED_CARD_TAIL = b"\xff\xff\xff\xff"
_ENROLLMENT_HEADER_LENGTH = 3


def encode_date5(when: dt.datetime | None, *, sentinel: bytes) -> bytes:
    """Pack a naive lock-local `datetime` into 5 decimal bytes `(yy,mm,dd,hh,mm)`.

    `None` packs `sentinel` instead - `START_DATE_SENTINEL` or
    `END_DATE_SENTINEL`, the values the list queries decode back to
    `None` ("never set" / "permanent").
    """
    if when is None:
        return sentinel
    if not 2000 <= when.year <= 2099:
        raise ValueError(f"credential validity year must be 2000-2099, got {when.year}")
    return bytes([when.year - 2000, when.month, when.day, when.hour, when.minute])


def _validity(start: dt.datetime | None, end: dt.datetime | None) -> bytes:
    return encode_date5(start, sentinel=START_DATE_SENTINEL) + encode_date5(
        end, sentinel=END_DATE_SENTINEL
    )


def _check_card_id(card_id: bytes) -> None:
    if len(card_id) not in _CARD_ID_LENGTHS:
        raise ValueError(f"card id must be 4 or 8 bytes, got {len(card_id)}")


def _check_fingerprint_number(number: bytes) -> None:
    if len(number) != FINGERPRINT_NUMBER_LENGTH:
        raise ValueError(
            f"fingerprint number must be {FINGERPRINT_NUMBER_LENGTH} bytes, got {len(number)}"
        )


def payload_credential_add() -> bytes:
    """ADD with no number: puts the lock into enroll mode for one card or fingerprint."""
    return bytes([CredentialOperate.ADD])


def payload_credential_clear() -> bytes:
    """CLEAR: removes every card (CMD 0x05) or every fingerprint (CMD 0x06)."""
    return bytes([CredentialOperate.CLEAR])


def payload_card_delete(card_id: bytes) -> bytes:
    """`[DELETE][card_id]` - 5 or 9 bytes, the id as the list query returned it."""
    _check_card_id(card_id)
    return bytes([CredentialOperate.DELETE]) + card_id


def payload_card_modify(
    card_id: bytes, start: dt.datetime | None, end: dt.datetime | None
) -> bytes:
    """`[MODIFY][card_id][start:5][end:5]` - changes when a card is valid."""
    _check_card_id(card_id)
    return bytes([CredentialOperate.MODIFY]) + card_id + _validity(start, end)


def payload_fingerprint_delete(number: bytes) -> bytes:
    """`[DELETE][number:6]`."""
    _check_fingerprint_number(number)
    return bytes([CredentialOperate.DELETE]) + number


def payload_fingerprint_modify(
    number: bytes, start: dt.datetime | None, end: dt.datetime | None
) -> bytes:
    """`[MODIFY][number:6][start:5][end:5]` - 17 bytes, changes when a fingerprint is valid."""
    _check_fingerprint_number(number)
    return bytes([CredentialOperate.MODIFY]) + number + _validity(start, end)


def parse_enrollment_response(plaintext: bytes) -> tuple[int, bytes]:
    """Decode one ADD reply or push into `(status, tail)`.

    `status` is an `EnrollmentStatus` value (left as a plain int when the
    lock sends one this library does not know); `tail` is everything
    after it - the new credential's number on `ADD_SUCCESS`, empty
    otherwise. Raises `RuntimeError` on a FAILED envelope, which is how
    the lock reports a timeout, a duplicate credential or a full store.
    """
    _echo, status, data = parse_response_status(plaintext)
    if status != RESPONSE_SUCCESS:
        code = data[0] if data else None
        raise RuntimeError(f"enrollment FAILED: status={status:#x} code={code}")
    if len(data) < _ENROLLMENT_HEADER_LENGTH:
        raise ValueError(f"enrollment reply too short: data_length={len(data)}")
    if data[1] != CredentialOperate.ADD:
        raise ValueError(f"enrollment reply echoes op {data[1]:#x}, expected ADD")
    return data[2], data[_ENROLLMENT_HEADER_LENGTH:]


def card_id_from_enrollment(tail: bytes) -> bytes:
    """Extract the new card's id from an `ADD_SUCCESS` tail.

    Four bytes normally; eight when the lock reads long-UID cards. An
    8-byte tail whose last four bytes are all 0xFF is a 4-byte id with
    padding - ttlock-sdk-js applies the same rule.
    """
    if len(tail) >= 8 and tail[4:8] != _UNUSED_CARD_TAIL:
        return tail[:8]
    if len(tail) < 4:
        raise ValueError(f"card enrollment reply carries no card id: tail_length={len(tail)}")
    return tail[:4]


def fingerprint_number_from_enrollment(tail: bytes) -> bytes:
    """Extract the new fingerprint's 6-byte number from an `ADD_SUCCESS` tail."""
    if len(tail) < FINGERPRINT_NUMBER_LENGTH:
        raise ValueError(f"fingerprint enrollment reply carries no number: tail_length={len(tail)}")
    return tail[:FINGERPRINT_NUMBER_LENGTH]
