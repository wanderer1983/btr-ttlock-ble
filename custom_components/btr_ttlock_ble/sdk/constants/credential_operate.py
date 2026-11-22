"""CredentialOperate: sub-op byte of COMM_IC_MANAGE (0x05) and COMM_FR_MANAGE (0x06)."""

from __future__ import annotations

from enum import IntEnum


class CredentialOperate(IntEnum):
    """First payload byte of the card and fingerprint management commands.

    Mirrors `ICOperate` in ttlock-sdk-js. Both opcodes share the ADD /
    DELETE / CLEAR / MODIFY values; only the list query differs
    (`IC_SEARCH` for cards, `FR_SEARCH` for fingerprints).
    """

    IC_SEARCH = 0x01
    ADD = 0x02
    DELETE = 0x03
    CLEAR = 0x04
    MODIFY = 0x05
    FR_SEARCH = 0x06
