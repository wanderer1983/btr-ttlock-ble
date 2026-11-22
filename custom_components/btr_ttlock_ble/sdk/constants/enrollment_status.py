"""EnrollmentStatus: progress byte the lock reports while a card or fingerprint is enrolled."""

from __future__ import annotations

from enum import IntEnum


class EnrollmentStatus(IntEnum):
    """Third data byte of an ADD reply on COMM_IC_MANAGE / COMM_FR_MANAGE.

    Mirrors the `STATUS_*` members of `ICOperate` in ttlock-sdk-js. The
    lock answers the ADD request with `ENTER_ADD_MODE`, then pushes
    `FR_PROGRESS` once per accepted fingerprint touch (cards skip that
    stage) and finally `ADD_SUCCESS` carrying the new credential's
    number. `FR_RECEIVE_TEMPLATE` is the lock asking for a template to be
    written to it, which this library does not support.
    """

    ADD_SUCCESS = 0x01
    ENTER_ADD_MODE = 0x02
    FR_PROGRESS = 0x03
    FR_RECEIVE_TEMPLATE = 0x04
