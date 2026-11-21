"""
Actions that add and remove the passcodes, cards and fingerprints a lock holds.

Every action names one lock by its device and runs over a single BLE
session, opened only for the call: the lock is left alone otherwise,
like everywhere else in this integration. The firmware takes these
commands from an admin key only, so a lock whose key carries no admin
password is refused before any radio traffic.

Credentials are addressed the way this integration already shows them:
a passcode by its digits, a card or a fingerprint by the number its name
entity and the official app display. A card or fingerprint is looked up
on the lock in the same session before it is deleted, because the lock
wants the raw bytes it listed rather than that number.

Enrolling waits for someone at the lock - a card held to the reader, the
same finger several times on the sensor - and reports each stage as a
`ttlock_ble_enrollment` event, so a dashboard can say what to do next.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.core import ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .const import DOMAIN, LOGGER
from .credential_store import async_get_credential_store
from .credential_type import TtlockBleCredentialType
from .data import TtlockBleEnrolledFingerprint, TtlockBleKnownCredential
from .key_privileges import can_administer
from .sdk import (
    EnrollmentStatus,
    FingerprintEntry,
    KeyboardPwdType,
    TTLockError,
)

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import VolDictType

    from .connection import TtlockBleConnection
    from .data import TtlockBleConfigEntry
    from .sdk import CardEntry, PasscodeEntry, TTLockClient

SERVICE_ADD_PASSCODE = "add_passcode"
SERVICE_DELETE_PASSCODE = "delete_passcode"
SERVICE_CHANGE_PASSCODE = "change_passcode"
SERVICE_ADD_CARD = "add_card"
SERVICE_DELETE_CARD = "delete_card"
SERVICE_ADD_FINGERPRINT = "add_fingerprint"
SERVICE_DELETE_FINGERPRINT = "delete_fingerprint"

ATTR_DEVICE_ID = "device_id"
ATTR_PASSCODE = "passcode"
ATTR_NEW_PASSCODE = "new_passcode"
ATTR_NAME = "name"
ATTR_START = "start"
ATTR_END = "end"
ATTR_CARD = "card"
ATTR_FINGERPRINT = "fingerprint"

EVENT_ENROLLMENT = f"{DOMAIN}_enrollment"

# A lock leaves add mode silently after a few seconds with nothing presented
# (about 8 s on a P3 padlock), and the session may land a while after the
# person at the lock started. Re-opening it this many times keeps the lock
# waiting for roughly a minute; each window fires another `waiting` event.
ENROLL_IDLE_RETRIES = 7

# The lock's own wire format for passcode windows: YYMMDDHHmm, lock-local.
_PASSCODE_TIME_FORMAT = "%y%m%d%H%M"
_OPEN_ENDED_PASSCODE = "9912311400"

_PASSCODE_DIGITS = probatio.All(cv.string, probatio.Match(r"^\d{4,9}$"))
_CREDENTIAL_NUMBER = probatio.All(probatio.Coerce(str), probatio.Match(r"^\d+$"))
_LOCK: VolDictType = {probatio.Required(ATTR_DEVICE_ID): cv.string}
_VALIDITY: VolDictType = {
    probatio.Optional(ATTR_START): cv.datetime,
    probatio.Optional(ATTR_END): cv.datetime,
}
_NAME: VolDictType = {probatio.Optional(ATTR_NAME, default=""): cv.string}

_ENROLLMENT_STAGES = {
    EnrollmentStatus.ENTER_ADD_MODE: "waiting",
    EnrollmentStatus.FR_PROGRESS: "scan_accepted",
}


@dataclass(frozen=True)
class _Target:
    """The lock an action was aimed at, resolved from its device."""

    device_id: str
    entry: TtlockBleConfigEntry
    connection: TtlockBleConnection

    @property
    def mac(self) -> str:
        return self.connection.key.lockMac

    @property
    def name(self) -> str:
        key = self.connection.key
        return key.lockAlias or key.lockName or key.lockMac


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the credential-management actions once for the integration."""
    handlers = {
        SERVICE_ADD_PASSCODE: (
            _async_add_passcode,
            probatio.Schema(
                {
                    **_LOCK,
                    probatio.Required(ATTR_PASSCODE): _PASSCODE_DIGITS,
                    **_NAME,
                    **_VALIDITY,
                }
            ),
            SupportsResponse.NONE,
        ),
        SERVICE_DELETE_PASSCODE: (
            _async_delete_passcode,
            probatio.Schema(
                {**_LOCK, probatio.Required(ATTR_PASSCODE): _PASSCODE_DIGITS}
            ),
            SupportsResponse.NONE,
        ),
        SERVICE_CHANGE_PASSCODE: (
            _async_change_passcode,
            probatio.Schema(
                {
                    **_LOCK,
                    probatio.Required(ATTR_PASSCODE): _PASSCODE_DIGITS,
                    probatio.Required(ATTR_NEW_PASSCODE): _PASSCODE_DIGITS,
                }
            ),
            SupportsResponse.NONE,
        ),
        SERVICE_ADD_CARD: (
            _async_add_card,
            probatio.Schema({**_LOCK, **_NAME, **_VALIDITY}),
            SupportsResponse.OPTIONAL,
        ),
        SERVICE_DELETE_CARD: (
            _async_delete_card,
            probatio.Schema(
                {**_LOCK, probatio.Required(ATTR_CARD): _CREDENTIAL_NUMBER}
            ),
            SupportsResponse.NONE,
        ),
        SERVICE_ADD_FINGERPRINT: (
            _async_add_fingerprint,
            probatio.Schema({**_LOCK, **_NAME, **_VALIDITY}),
            SupportsResponse.OPTIONAL,
        ),
        SERVICE_DELETE_FINGERPRINT: (
            _async_delete_fingerprint,
            probatio.Schema(
                {**_LOCK, probatio.Required(ATTR_FINGERPRINT): _CREDENTIAL_NUMBER}
            ),
            SupportsResponse.NONE,
        ),
    }
    for service, (handler, schema, supports_response) in handlers.items():
        hass.services.async_register(
            DOMAIN,
            service,
            handler,
            schema=schema,
            supports_response=supports_response,
        )


def _resolve(call: ServiceCall) -> _Target:
    """Find the loaded lock behind the call's device, refusing a non-admin key."""
    device_id: str = call.data[ATTR_DEVICE_ID]
    device = dr.async_get(call.hass).async_get(device_id)
    macs = (
        set()
        if device is None
        else {
            identifier for domain, identifier in device.identifiers if domain == DOMAIN
        }
    )
    for entry in call.hass.config_entries.async_loaded_entries(DOMAIN):
        for mac, connection in entry.runtime_data.connections.items():
            if dr.format_mac(mac) not in macs:
                continue
            if not can_administer(connection.key):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="not_admin",
                    translation_placeholders={
                        "lock": (device and (device.name_by_user or device.name)) or mac
                    },
                )
            return _Target(device_id=device_id, entry=entry, connection=connection)
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="unknown_lock",
        translation_placeholders={"device_id": device_id},
    )


def _lock_time(value: datetime | None) -> datetime | None:
    """Turn an action's datetime into the naive local wall clock the lock keeps."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = dt_util.as_local(value)
    return value.replace(tzinfo=None, second=0, microsecond=0)


def _validity(call: ServiceCall) -> tuple[datetime | None, datetime | None]:
    start = _lock_time(call.data.get(ATTR_START))
    end = _lock_time(call.data.get(ATTR_END))
    if start is not None and end is not None and end <= start:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="end_before_start"
        )
    return start, end


async def _async_run[T](target: _Target, action: str, operation: Any) -> T:
    """Run one call on the lock's connection; a lock failure becomes an HA error."""
    try:
        result: T = await target.connection.async_manage(action, operation)
    except TTLockError as error:
        LOGGER.warning("%s on %s failed: %s", action, target.mac, error)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="credential_action_failed",
            translation_placeholders={"lock": target.name, "error": str(error)},
        ) from error
    return result


def _not_found(target: _Target, credential: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="credential_not_found",
        translation_placeholders={"lock": target.name, "credential": credential},
    )


async def _async_remember(
    call: ServiceCall, target: _Target, credential: TtlockBleKnownCredential, name: str
) -> None:
    """
    Record a credential the action just created, under the name it was given.

    Announcing it through the coordinator is what makes the credential
    tracker create its name entity now, not after the next list read.
    """
    store = await async_get_credential_store(call.hass)
    store.async_add(target.mac, credential)
    if name.strip():
        store.async_set_name(
            target.mac, credential.credential_type, credential.key, name
        )
    target.entry.runtime_data.coordinator.async_update_listeners()


async def _async_forget(
    call: ServiceCall, target: _Target, credential: TtlockBleKnownCredential
) -> None:
    """Drop a credential the action just removed, and with it its name entity."""
    store = await async_get_credential_store(call.hass)
    store.async_remove(target.mac, credential)
    target.entry.runtime_data.coordinator.async_update_listeners()


def _passcode_window(
    start: datetime | None, end: datetime | None
) -> tuple[KeyboardPwdType, dict[str, str]]:
    """Pick the passcode type and the SDK's date strings for an optional window."""
    if start is None and end is None:
        return KeyboardPwdType.PERMANENT, {}
    begins = start or dt_util.now().replace(tzinfo=None, second=0, microsecond=0)
    return KeyboardPwdType.PERIOD, {
        "start_date": begins.strftime(_PASSCODE_TIME_FORMAT),
        "end_date": end.strftime(_PASSCODE_TIME_FORMAT)
        if end
        else _OPEN_ENDED_PASSCODE,
    }


async def _async_add_passcode(call: ServiceCall) -> None:
    target = _resolve(call)
    code: str = call.data[ATTR_PASSCODE]
    pwd_type, window = _passcode_window(*_validity(call))

    async def _add(client: TTLockClient) -> None:
        await client.add_passcode(code, pwd_type=pwd_type, **window)

    await _async_run(target, SERVICE_ADD_PASSCODE, _add)
    await _async_remember(
        call,
        target,
        TtlockBleKnownCredential.from_passcode(target.mac, code),
        call.data[ATTR_NAME],
    )


def _listed(entries: list[PasscodeEntry], code: str) -> PasscodeEntry | None:
    return next((entry for entry in entries if entry.passcode == code), None)


async def _async_delete_passcode(call: ServiceCall) -> None:
    target = _resolve(call)
    code: str = call.data[ATTR_PASSCODE]

    async def _delete(client: TTLockClient) -> None:
        # The delete names the code's type; a code the lock does not list
        # (made offline by the app and never typed) is tried as permanent.
        listed = _listed(await client.get_passcodes(), code)
        pwd_type = listed.pwd_type if listed else KeyboardPwdType.PERMANENT
        await client.delete_passcode(code, pwd_type=pwd_type)

    await _async_run(target, SERVICE_DELETE_PASSCODE, _delete)
    await _async_forget(
        call, target, TtlockBleKnownCredential.from_passcode(target.mac, code)
    )


async def _async_change_passcode(call: ServiceCall) -> None:
    """
    Replace a passcode, adding the new code before removing the old one.

    The lock has a one-step MODIFY, but it is not confirmed on hardware,
    while add and delete are. Adding first means a failure part-way
    leaves both codes working rather than neither; the error says which.
    The old code's type and window carry over to the new one.
    """
    target = _resolve(call)
    old_code: str = call.data[ATTR_PASSCODE]
    new_code: str = call.data[ATTR_NEW_PASSCODE]

    async def _change(client: TTLockClient) -> None:
        listed = _listed(await client.get_passcodes(), old_code)
        if listed is not None and listed.pwd_type == KeyboardPwdType.CIRCLE:
            msg = "a recurring passcode cannot be changed; delete it and add a new one"
            raise TTLockError(msg)
        pwd_type: KeyboardPwdType = KeyboardPwdType.PERMANENT
        window: dict[str, str] = {}
        if listed is not None and listed.pwd_type == KeyboardPwdType.PERIOD:
            pwd_type, window = _passcode_window(listed.start_date, listed.end_date)
        await client.add_passcode(new_code, pwd_type=pwd_type, **window)
        try:
            await client.delete_passcode(
                old_code,
                pwd_type=listed.pwd_type if listed else KeyboardPwdType.PERMANENT,
            )
        except TTLockError as error:
            msg = (
                f"the new code was added, but the old one could not be removed: {error}"
            )
            raise TTLockError(msg) from error

    await _async_run(target, SERVICE_CHANGE_PASSCODE, _change)
    store = await async_get_credential_store(call.hass)
    store.async_rekey(
        target.mac,
        TtlockBleKnownCredential.from_passcode(target.mac, old_code),
        TtlockBleKnownCredential.from_passcode(target.mac, new_code),
    )
    target.entry.runtime_data.coordinator.async_update_listeners()


def _progress_reporter(
    call: ServiceCall, target: _Target, credential_type: TtlockBleCredentialType
) -> Any:
    """Return the SDK progress callback that fires `ttlock_ble_enrollment` events."""
    scans = 0

    @callback
    def _report(status: EnrollmentStatus) -> None:
        nonlocal scans
        if status == EnrollmentStatus.FR_PROGRESS:
            scans += 1
        _fire_enrollment(
            call, target, credential_type, _ENROLLMENT_STAGES[status], scans=scans
        )

    return _report


@callback
def _fire_enrollment(
    call: ServiceCall,
    target: _Target,
    credential_type: TtlockBleCredentialType,
    stage: str,
    **extra: Any,
) -> None:
    call.hass.bus.async_fire(
        EVENT_ENROLLMENT,
        {
            ATTR_DEVICE_ID: target.device_id,
            "credential_type": str(credential_type),
            "stage": stage,
            **extra,
        },
    )


async def _async_enroll(
    call: ServiceCall,
    target: _Target,
    credential_type: TtlockBleCredentialType,
    action: str,
    operation: Any,
) -> bytes:
    """Run an enrollment, reporting `enrolled` or `failed` as its last event."""
    try:
        number: bytes = await _async_run(target, action, operation)
    except HomeAssistantError:
        _fire_enrollment(call, target, credential_type, "failed")
        raise
    return number


async def _async_add_card(call: ServiceCall) -> ServiceResponse:
    target = _resolve(call)
    start, end = _validity(call)
    report = _progress_reporter(call, target, TtlockBleCredentialType.CARD)

    async def _add(client: TTLockClient) -> bytes:
        return await client.add_card(
            start_date=start,
            end_date=end,
            on_progress=report,
            idle_retries=ENROLL_IDLE_RETRIES,
        )

    card_id = await _async_enroll(
        call, target, TtlockBleCredentialType.CARD, SERVICE_ADD_CARD, _add
    )
    number = int.from_bytes(card_id, "big")
    credential = TtlockBleKnownCredential(
        credential_type=TtlockBleCredentialType.CARD, key=str(number), label=str(number)
    )
    await _async_remember(call, target, credential, call.data[ATTR_NAME])
    _fire_enrollment(
        call, target, TtlockBleCredentialType.CARD, "enrolled", number=number
    )
    return {ATTR_CARD: number}


async def _async_delete_card(call: ServiceCall) -> None:
    target = _resolve(call)
    number = int(call.data[ATTR_CARD])

    async def _delete(client: TTLockClient) -> bool:
        cards: list[CardEntry] = await client.get_cards()
        match = next(
            (c for c in cards if int.from_bytes(c.card_id, "big") == number), None
        )
        if match is None:
            return False
        await client.delete_card(match.card_id)
        return True

    found: bool = await _async_run(target, SERVICE_DELETE_CARD, _delete)
    if not found:
        raise _not_found(target, str(number))
    await _async_forget(
        call,
        target,
        TtlockBleKnownCredential(
            credential_type=TtlockBleCredentialType.CARD,
            key=str(number),
            label=str(number),
        ),
    )


async def _async_add_fingerprint(call: ServiceCall) -> ServiceResponse:
    target = _resolve(call)
    start, end = _validity(call)
    report = _progress_reporter(call, target, TtlockBleCredentialType.FINGERPRINT)

    async def _add(client: TTLockClient) -> bytes:
        return await client.add_fingerprint(
            start_date=start,
            end_date=end,
            on_progress=report,
            idle_retries=ENROLL_IDLE_RETRIES,
        )

    raw = await _async_enroll(
        call, target, TtlockBleCredentialType.FINGERPRINT, SERVICE_ADD_FINGERPRINT, _add
    )
    entry = FingerprintEntry(
        fingerprint_id=raw[:4],
        slot=int.from_bytes(raw[4:], "big"),
        start_date=None,
        end_date=None,
    )
    credential = TtlockBleKnownCredential.from_fingerprint(entry)
    number = TtlockBleEnrolledFingerprint.from_entry(entry).number
    await _async_remember(call, target, credential, call.data[ATTR_NAME])
    _fire_enrollment(
        call, target, TtlockBleCredentialType.FINGERPRINT, "enrolled", number=number
    )
    return {ATTR_FINGERPRINT: number}


async def _async_delete_fingerprint(call: ServiceCall) -> None:
    target = _resolve(call)
    number = int(call.data[ATTR_FINGERPRINT])

    async def _delete(client: TTLockClient) -> FingerprintEntry | None:
        for entry in await client.get_fingerprints():
            if TtlockBleEnrolledFingerprint.from_entry(entry).number == number:
                await client.delete_fingerprint(entry.number)
                return entry
        return None

    deleted: FingerprintEntry | None = await _async_run(
        target, SERVICE_DELETE_FINGERPRINT, _delete
    )
    if deleted is None:
        raise _not_found(target, str(number))
    await _async_forget(
        call, target, TtlockBleKnownCredential.from_fingerprint(deleted)
    )
