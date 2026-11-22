"""
Websocket commands behind the TTLock sidebar panel.

The panel reads what this integration already keeps - each lock, when
it was last heard, the credentials it is known to hold and their names -
and changes credentials through the integration's actions, so nothing
here talks to a lock except the two commands that have to: reading the
credential lists, and deleting a passcode by the digest the panel holds.
Waiting for a lock to wake is the panel's way of doing what a sleeping
padlock needs: nothing is attempted until someone has touched it.

Every command is admin-only, like the panel itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.components import websocket_api
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .credential_store import async_get_credential_store
from .credential_type import TtlockBleCredentialType
from .key_privileges import can_administer, may_hold
from .sdk import LockFeature, TTLockError
from .services import async_delete_passcode_by_key, async_resolve_lock
from .wake import ASLEEP_AFTER_SECONDS, async_last_heard, async_wait_until_heard

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .connection import TtlockBleConnection
    from .coordinator import TtlockBleDataUpdateCoordinator
    from .credential_store import TtlockBleCredentialStore
    from .data import TtlockBleConfigEntry
    from .sdk import TTLockClient

MAX_WAKE_WAIT_SECONDS = 300

_MANAGED_TYPES: dict[TtlockBleCredentialType, LockFeature] = {
    TtlockBleCredentialType.FINGERPRINT: LockFeature.FINGER_PRINT,
    TtlockBleCredentialType.CARD: LockFeature.IC,
    TtlockBleCredentialType.PASSCODE: LockFeature.PASSCODE,
}


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    """Register the panel's commands."""
    for command in (
        _ws_locks,
        _ws_wait_awake,
        _ws_rename_credential,
        _ws_refresh_credentials,
        _ws_delete_passcode,
        _ws_remove_lock,
    ):
        websocket_api.async_register_command(hass, command)


def _describe_lock(
    hass: HomeAssistant,
    connection: TtlockBleConnection,
    coordinator: TtlockBleDataUpdateCoordinator,
    store: TtlockBleCredentialStore,
    entry: TtlockBleConfigEntry,
) -> dict[str, Any] | None:
    key = connection.key
    mac = key.lockMac
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, dr.format_mac(mac)), entry.entry_id
    )
    if device is None:
        return None
    lock_entity = next(
        (
            entry.entity_id
            for entry in er.async_entries_for_device(er.async_get(hass), device.id)
            if entry.domain == "lock" and entry.platform == DOMAIN
        ),
        None,
    )
    state = (coordinator.data or {}).get(mac) or {}
    description: dict[str, Any] = dict(coordinator.async_device_description(mac) or {})
    heard = async_last_heard(hass, mac)
    credentials: dict[str, Any] = {}
    for credential_type, feature in _MANAGED_TYPES.items():
        if not may_hold(key, feature):
            continue
        known = coordinator.async_credentials(mac, credential_type)
        credentials[credential_type] = (
            None
            if known is None
            else [
                {
                    "key": credential.key,
                    "label": credential.label,
                    "name": store.name_of(mac, credential_type, credential.key) or "",
                }
                for credential in known
            ]
        )
    return {
        "device_id": device.id,
        "name": device.name_by_user or device.name or key.lockAlias or mac,
        "lock_name": key.lockName,
        "mac": mac,
        "model": description.get("model"),
        "entity_id": lock_entity,
        "admin": can_administer(key),
        "locked": state.get("locked"),
        "battery": state.get("battery_level"),
        "connected": connection.is_connected,
        "heard": None
        if heard is None
        else {
            "seconds_ago": round(heard.seconds_ago, 1),
            "rssi": heard.rssi,
            "via": heard.source_name,
            "asleep": heard.asleep,
        },
        "credentials": credentials,
        # An account entry holds every lock of the account, so one of them
        # cannot leave on its own: it goes when the account no longer has it.
        "removable": "username" not in entry.data,
        "from_account": "username" in entry.data,
        "paired_locally": bool(entry.data.get("paired_locally")),
        "admin_keypad_code": entry.data.get("admin_keypad_code") or None,
    }


async def _async_all_locks(hass: HomeAssistant) -> list[dict[str, Any]]:
    store = await async_get_credential_store(hass)
    locks: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        runtime = entry.runtime_data
        for connection in runtime.connections.values():
            described = _describe_lock(
                hass, connection, runtime.coordinator, store, entry
            )
            if described is not None:
                locks.append(described)
    return sorted(locks, key=lambda lock: str(lock["name"]).casefold())


@websocket_api.websocket_command({probatio.Required("type"): f"{DOMAIN}/locks"})
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_locks(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """List every lock with its credentials and when it was last heard."""
    connection.send_result(
        msg["id"],
        {"locks": await _async_all_locks(hass), "asleep_after": ASLEEP_AFTER_SECONDS},
    )


def _target_or_error(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> Any:
    try:
        return async_resolve_lock(hass, msg["device_id"])
    except HomeAssistantError as error:
        connection.send_error(msg["id"], "invalid_lock", str(error))
        return None


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/wait_awake",
        probatio.Required("device_id"): str,
        probatio.Optional("timeout", default=90): probatio.All(
            probatio.Coerce(float), probatio.Range(min=1, max=MAX_WAKE_WAIT_SECONDS)
        ),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_wait_awake(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return once the lock advertises, or after the timeout says it did not."""
    target = _target_or_error(hass, connection, msg)
    if target is None:
        return
    awake = await async_wait_until_heard(hass, target.mac, msg["timeout"])
    connection.send_result(msg["id"], {"awake": awake})


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/rename_credential",
        probatio.Required("device_id"): str,
        probatio.Required("credential_type"): probatio.In(
            [str(t) for t in _MANAGED_TYPES]
        ),
        probatio.Required("key"): str,
        probatio.Required("name"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_rename_credential(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Name a credential; the name entity follows, as when typed there."""
    target = _target_or_error(hass, connection, msg)
    if target is None:
        return
    store = await async_get_credential_store(hass)
    credential_type = TtlockBleCredentialType(msg["credential_type"])
    if store.label_of(target.mac, credential_type, msg["key"]) is None:
        connection.send_error(
            msg["id"], "not_found", "The lock holds no such credential"
        )
        return
    store.async_set_name(target.mac, credential_type, msg["key"], msg["name"])
    target.entry.runtime_data.coordinator.async_update_listeners()
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/refresh_credentials",
        probatio.Required("device_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_refresh_credentials(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Read the fingerprint, card and passcode lists from the lock now."""
    target = _target_or_error(hass, connection, msg)
    if target is None:
        return
    heard = async_last_heard(hass, target.mac)
    if heard is not None and heard.asleep and not target.connection.is_connected:
        connection.send_error(
            msg["id"], "lock_asleep", f"{target.name} is asleep; touch it to wake it"
        )
        return
    coordinator = target.entry.runtime_data.coordinator
    if not await coordinator.async_refresh_credentials(target.mac):
        connection.send_error(
            msg["id"],
            "read_failed",
            f"Could not read every credential list from {target.name}",
        )
        return
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/delete_passcode",
        probatio.Required("device_id"): str,
        probatio.Required("key"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_delete_passcode(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete a passcode the panel knows only by its stored digest."""
    try:
        await async_delete_passcode_by_key(hass, msg["device_id"], msg["key"])
    except HomeAssistantError as error:
        connection.send_error(msg["id"], "delete_failed", str(error))
        return
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/remove_lock",
        probatio.Required("device_id"): str,
        probatio.Required("reset"): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def _ws_remove_lock(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """
    Remove a lock's entry, factory-resetting the lock first when asked.

    A lock from a TTLock account can only be reset here: its entry holds
    the whole account, so it leaves once the lock is deleted in the app.

    The reset runs before the entry goes: the entry holds the only keys
    that can reset the lock, so a reset that fails leaves both in place.
    """
    target = _target_or_error(hass, connection, msg)
    if target is None:
        return
    from_account = "username" in target.entry.data
    if from_account and not msg["reset"]:
        connection.send_error(
            msg["id"],
            "account_lock",
            f"{target.name} comes from a TTLock account; remove it in the TTLock app",
        )
        return
    if msg["reset"]:

        async def _reset(client: TTLockClient) -> None:
            await client.factory_reset()

        try:
            await target.connection.async_manage("factory_reset", _reset)
        except TTLockError as error:
            connection.send_error(
                msg["id"], "reset_failed", f"{target.name} was not reset: {error}"
            )
            return
    if from_account:
        # The account still lists the lock, and its entry holds the other
        # locks too: the entry stays until the lock is deleted in the app
        # and the account is synced again.
        connection.send_result(msg["id"], {"removed": False})
        return
    await hass.config_entries.async_remove(target.entry.entry_id)
    connection.send_result(msg["id"], {"removed": True})
