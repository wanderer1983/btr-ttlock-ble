"""What pairing a lock produced: the keys it now answers to."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .virtual_key import USER_TYPE_ADMIN, VirtualKey

if TYPE_CHECKING:
    from .device_features import DeviceFeatures
    from .lock_version import LockVersion


@dataclass(frozen=True, slots=True)
class PairingResult:
    """The secrets a freshly paired lock holds, and what it said about itself.

    `aes_key` is the key the lock chose; `admin_ps` and `unlock_key` are
    the two numbers this client chose and registered as its admin. They
    are the whole of what any later session needs, so losing them means
    resetting the lock by hand. `admin_keypad_code` is the code that
    opens a keypad lock as its admin, `""` when the lock has none.
    `features` is `None` when the lock did not answer the feature search.
    """

    aes_key: bytes
    admin_ps: str
    unlock_key: str
    admin_keypad_code: str
    features: DeviceFeatures | None

    def to_virtual_key(self, *, lock_mac: str, name: str, lock_version: LockVersion) -> VirtualKey:
        """Return the admin key a client needs to drive the paired lock."""
        return VirtualKey(
            keyId=0,
            lockId=0,
            lockMac=lock_mac,
            lockAlias=name,
            lockName=name,
            lockVersion=lock_version,
            aesKeyStr=self.aes_key.hex(),
            unlockKey=self.unlock_key,
            lockFlagPos=0,
            timezoneRawOffSet=0,
            userType=USER_TYPE_ADMIN,
            adminPs=self.admin_ps,
            featureValue=f"{self.features.mask:X}" if self.features else "",
        )
