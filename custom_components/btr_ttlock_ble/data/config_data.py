"""Shape of the credentials and key cache persisted on the config entry."""

from __future__ import annotations

from typing import TYPE_CHECKING, NotRequired, TypedDict

if TYPE_CHECKING:
    from .stored_key import TtlockBleStoredKey


class TtlockBleConfigData(TypedDict):
    """
    Shape of the credentials and key cache persisted on the config entry.

    `username`/`password` are absent on entries created from a manually
    entered key: there is no cloud account behind those, so there is
    nothing to re-authenticate against either. `cloud_app` is absent on
    those and on cloud entries created before it existed, which all
    belong to the default app.

    `paired_locally` marks an entry whose lock this Home Assistant
    paired itself: its keys exist nowhere else, and deleting it can
    factory-reset the lock. `admin_keypad_code` is the keypad admin code
    set while pairing, `""` for a lock without a keypad.
    """

    username: NotRequired[str]
    password: NotRequired[str]
    cloud_app: NotRequired[str]
    keys: list[TtlockBleStoredKey]
    paired_locally: NotRequired[bool]
    admin_keypad_code: NotRequired[str]
