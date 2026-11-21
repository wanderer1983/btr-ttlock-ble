"""The credential-management actions: passcodes, cards and fingerprints."""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from custom_components.ttlock_ble.connection import TtlockBleConnection
from custom_components.ttlock_ble.const import DOMAIN
from custom_components.ttlock_ble.credential_type import TtlockBleCredentialType
from custom_components.ttlock_ble.data import TtlockBleKnownCredential
from custom_components.ttlock_ble.sdk import (
    CardEntry,
    EnrollmentStatus,
    FingerprintEntry,
    KeyboardPwdType,
    PasscodeEntry,
    TTLockError,
)
from custom_components.ttlock_ble.services import (
    ENROLL_IDLE_RETRIES,
    EVENT_ENROLLMENT,
)

_FP_RAW = bytes([0x00, 0x00, 0x00, 0x2A, 0x00, 0x01])
_FP_NUMBER = int.from_bytes(_FP_RAW, "big")
_CARD_ID = bytes([0x11, 0x22, 0x33, 0x44])
_CARD_NUMBER = int.from_bytes(_CARD_ID, "big")


@pytest.fixture
def client(mock_ttlock_connection) -> MagicMock:
    """A fake SDK client the mocked connection hands to every operation."""
    fake = MagicMock(name="TTLockClient")
    for name in (
        "add_passcode",
        "delete_passcode",
        "add_card",
        "delete_card",
        "add_fingerprint",
        "delete_fingerprint",
    ):
        setattr(fake, name, AsyncMock(return_value=None))
    fake.get_passcodes = AsyncMock(return_value=[])
    fake.get_cards = AsyncMock(return_value=[])
    fake.get_fingerprints = AsyncMock(return_value=[])

    async def _manage(_action, operation):
        return await operation(fake)

    mock_ttlock_connection.async_manage = AsyncMock(side_effect=_manage)
    return fake


@pytest.fixture
def device_id(hass, setup_integration, sample_virtual_key) -> str:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, dr.format_mac(sample_virtual_key.lockMac)), setup_integration.entry_id
    )
    assert device is not None
    return device.id


def _credentials(setup_integration, mac, credential_type):
    coordinator = setup_integration.runtime_data.coordinator
    return coordinator.async_credentials(mac, credential_type) or []


async def _call(hass, service, **data):
    return await hass.services.async_call(
        DOMAIN,
        service,
        data,
        blocking=True,
        return_response=service in {"add_card", "add_fingerprint"},
    )


def _passcode(code, pwd_type=KeyboardPwdType.PERMANENT, start=None, end=None):
    return PasscodeEntry(
        passcode=code,
        pwd_type=pwd_type,
        start_date=start,
        end_date=end,
        cyclic_schedule=None,
    )


class TestTargeting:
    async def test_unknown_device_is_refused(self, hass, setup_integration, client):
        with pytest.raises(ServiceValidationError) as error:
            await _call(hass, "add_passcode", device_id="nope", passcode="1234")
        assert error.value.translation_key == "unknown_lock"
        client.add_passcode.assert_not_awaited()

    async def test_key_without_admin_passcode_is_refused_before_any_radio(
        self, hass, device_id, client, sample_virtual_key
    ):
        sample_virtual_key.adminPs = ""
        with pytest.raises(ServiceValidationError) as error:
            await _call(hass, "add_passcode", device_id=device_id, passcode="1234")
        assert error.value.translation_key == "not_admin"
        client.add_passcode.assert_not_awaited()

    async def test_passcode_digits_are_validated(self, hass, device_id, client):
        with pytest.raises(Exception, match="match"):
            await _call(hass, "add_passcode", device_id=device_id, passcode="12a4")

    async def test_end_before_start_is_refused(self, hass, device_id, client):
        with pytest.raises(ServiceValidationError) as error:
            await _call(
                hass,
                "add_passcode",
                device_id=device_id,
                passcode="1234",
                start="2026-12-02 10:00",
                end="2026-12-01 10:00",
            )
        assert error.value.translation_key == "end_before_start"

    async def test_lock_failure_becomes_a_translated_error(
        self, hass, device_id, client, sample_virtual_key
    ):
        client.add_passcode.side_effect = TTLockError("status=0x0")
        with pytest.raises(HomeAssistantError) as error:
            await _call(hass, "add_passcode", device_id=device_id, passcode="1234")
        assert error.value.translation_key == "credential_action_failed"
        assert error.value.translation_placeholders == {
            "lock": sample_virtual_key.lockAlias,
            "error": "status=0x0",
        }


class TestPasscodes:
    async def test_add_permanent_records_and_names_it(
        self, hass, device_id, client, setup_integration, sample_virtual_key
    ):
        await _call(
            hass, "add_passcode", device_id=device_id, passcode="482916", name="Cleaner"
        )

        client.add_passcode.assert_awaited_once_with(
            "482916", pwd_type=KeyboardPwdType.PERMANENT
        )
        mac = sample_virtual_key.lockMac
        expected = TtlockBleKnownCredential.from_passcode(mac, "482916")
        assert expected in _credentials(
            setup_integration, mac, TtlockBleCredentialType.PASSCODE
        )
        coordinator = setup_integration.runtime_data.coordinator
        assert (
            coordinator._credentials.name_of(
                mac, TtlockBleCredentialType.PASSCODE, expected.key
            )
            == "Cleaner"
        )

    async def test_add_with_window_is_a_period_code_in_lock_time(
        self, hass, device_id, client
    ):
        await _call(
            hass,
            "add_passcode",
            device_id=device_id,
            passcode="482916",
            start="2026-12-01 08:30:45",
            end="2027-01-31 23:59",
        )
        client.add_passcode.assert_awaited_once_with(
            "482916",
            pwd_type=KeyboardPwdType.PERIOD,
            start_date="2612010830",
            end_date="2701312359",
        )

    async def test_delete_uses_the_type_the_lock_lists(
        self, hass, device_id, client, setup_integration, sample_virtual_key
    ):
        mac = sample_virtual_key.lockMac
        await _call(hass, "add_passcode", device_id=device_id, passcode="482916")
        client.get_passcodes.return_value = [
            _passcode("482916", KeyboardPwdType.PERIOD)
        ]

        await _call(hass, "delete_passcode", device_id=device_id, passcode="482916")

        client.delete_passcode.assert_awaited_once_with(
            "482916", pwd_type=KeyboardPwdType.PERIOD
        )
        assert TtlockBleKnownCredential.from_passcode(
            mac, "482916"
        ) not in _credentials(setup_integration, mac, TtlockBleCredentialType.PASSCODE)

    async def test_delete_of_an_unlisted_code_is_tried_as_permanent(
        self, hass, device_id, client
    ):
        await _call(hass, "delete_passcode", device_id=device_id, passcode="482916")
        client.delete_passcode.assert_awaited_once_with(
            "482916", pwd_type=KeyboardPwdType.PERMANENT
        )

    async def test_change_adds_the_new_code_first_and_keeps_window_and_name(
        self, hass, device_id, client, setup_integration, sample_virtual_key
    ):
        mac = sample_virtual_key.lockMac
        await _call(
            hass, "add_passcode", device_id=device_id, passcode="482916", name="Cleaner"
        )
        start = dt.datetime(2026, 12, 1, 8, 30)  # noqa: DTZ001
        end = dt.datetime(2027, 1, 31, 23, 59)  # noqa: DTZ001
        client.get_passcodes.return_value = [
            _passcode("482916", KeyboardPwdType.PERIOD, start, end)
        ]
        order = []
        client.add_passcode.side_effect = lambda *_a, **_k: order.append("add")
        client.delete_passcode.side_effect = lambda *_a, **_k: order.append("delete")

        await _call(
            hass,
            "change_passcode",
            device_id=device_id,
            passcode="482916",
            new_passcode="739205",
        )

        assert order == ["add", "delete"]
        client.add_passcode.assert_awaited_with(
            "739205",
            pwd_type=KeyboardPwdType.PERIOD,
            start_date="2612010830",
            end_date="2701312359",
        )
        new = TtlockBleKnownCredential.from_passcode(mac, "739205")
        coordinator = setup_integration.runtime_data.coordinator
        assert (
            coordinator._credentials.name_of(
                mac, TtlockBleCredentialType.PASSCODE, new.key
            )
            == "Cleaner"
        )

    async def test_change_refuses_a_recurring_code(self, hass, device_id, client):
        client.get_passcodes.return_value = [
            _passcode("482916", KeyboardPwdType.CIRCLE)
        ]
        with pytest.raises(HomeAssistantError, match="recurring"):
            await _call(
                hass,
                "change_passcode",
                device_id=device_id,
                passcode="482916",
                new_passcode="739205",
            )
        client.add_passcode.assert_not_awaited()

    async def test_change_says_so_when_the_old_code_survives(
        self, hass, device_id, client
    ):
        client.delete_passcode.side_effect = TTLockError("status=0x0")
        with pytest.raises(HomeAssistantError) as error:
            await _call(
                hass,
                "change_passcode",
                device_id=device_id,
                passcode="482916",
                new_passcode="739205",
            )
        assert "the new code was added" in error.value.translation_placeholders["error"]


class TestFingerprints:
    async def test_add_reports_each_stage_and_returns_the_number(
        self, hass, device_id, client, setup_integration, sample_virtual_key
    ):
        events = []
        hass.bus.async_listen(EVENT_ENROLLMENT, lambda e: events.append(e.data))

        async def _enroll(*, start_date, end_date, on_progress, idle_retries):
            assert idle_retries == ENROLL_IDLE_RETRIES
            on_progress(EnrollmentStatus.ENTER_ADD_MODE)
            on_progress(EnrollmentStatus.FR_PROGRESS)
            on_progress(EnrollmentStatus.FR_PROGRESS)
            return _FP_RAW

        client.add_fingerprint.side_effect = _enroll

        response = await _call(
            hass, "add_fingerprint", device_id=device_id, name="Alex thumb"
        )
        await hass.async_block_till_done()

        assert response == {"fingerprint": _FP_NUMBER}
        assert [(e["stage"], e.get("scans")) for e in events] == [
            ("waiting", 0),
            ("scan_accepted", 1),
            ("scan_accepted", 2),
            ("enrolled", None),
        ]
        assert all(e["device_id"] == device_id for e in events)
        assert events[-1]["number"] == _FP_NUMBER
        mac = sample_virtual_key.lockMac
        known = _credentials(
            setup_integration, mac, TtlockBleCredentialType.FINGERPRINT
        )
        assert [c.label for c in known] == [str(_FP_NUMBER)]

    async def test_add_passes_the_window_in_lock_time(self, hass, device_id, client):
        client.add_fingerprint.return_value = _FP_RAW
        await _call(
            hass,
            "add_fingerprint",
            device_id=device_id,
            end="2027-01-31T23:59:00+00:00",
        )
        kwargs = client.add_fingerprint.await_args.kwargs
        assert kwargs["start_date"] is None
        assert kwargs["end_date"].tzinfo is None

    async def test_failed_enrollment_fires_failed_and_raises(
        self, hass, device_id, client
    ):
        events = []
        hass.bus.async_listen(EVENT_ENROLLMENT, lambda e: events.append(e.data))
        client.add_fingerprint.side_effect = TTLockError("enrollment FAILED")

        with pytest.raises(HomeAssistantError):
            await _call(hass, "add_fingerprint", device_id=device_id)
        await hass.async_block_till_done()

        assert events[-1]["stage"] == "failed"

    async def test_delete_looks_the_number_up_on_the_lock(
        self, hass, device_id, client
    ):
        listed = FingerprintEntry(
            fingerprint_id=_FP_RAW[:4], slot=1, start_date=None, end_date=None
        )
        client.get_fingerprints.return_value = [listed]

        await _call(
            hass, "delete_fingerprint", device_id=device_id, fingerprint=_FP_NUMBER
        )

        client.delete_fingerprint.assert_awaited_once_with(_FP_RAW)

    async def test_delete_of_an_unknown_number_is_refused(
        self, hass, device_id, client
    ):
        with pytest.raises(ServiceValidationError) as error:
            await _call(hass, "delete_fingerprint", device_id=device_id, fingerprint=7)
        assert error.value.translation_key == "credential_not_found"
        client.delete_fingerprint.assert_not_awaited()


class TestCards:
    async def test_add_returns_the_number_and_records_it(
        self, hass, device_id, client, setup_integration, sample_virtual_key
    ):
        client.add_card.return_value = _CARD_ID
        response = await _call(hass, "add_card", device_id=device_id, name="Blue tag")
        assert response == {"card": _CARD_NUMBER}
        known = _credentials(
            setup_integration, sample_virtual_key.lockMac, TtlockBleCredentialType.CARD
        )
        assert [c.key for c in known] == [str(_CARD_NUMBER)]

    async def test_delete_sends_the_listed_id_bytes(self, hass, device_id, client):
        client.get_cards.return_value = [
            CardEntry(card_id=_CARD_ID, start_date=None, end_date=None)
        ]
        await _call(hass, "delete_card", device_id=device_id, card=str(_CARD_NUMBER))
        client.delete_card.assert_awaited_once_with(_CARD_ID)

    async def test_delete_of_an_unknown_card_is_refused(self, hass, device_id, client):
        with pytest.raises(ServiceValidationError):
            await _call(hass, "delete_card", device_id=device_id, card="5")
        client.delete_card.assert_not_awaited()


class TestConnectionManage:
    async def test_returns_what_the_operation_returns(
        self, hass, sample_virtual_key, mock_ble_resolver, mock_ttlock_client
    ):
        conn = TtlockBleConnection(hass, sample_virtual_key)

        async def _op(client):
            assert client is mock_ttlock_client
            return 42

        assert await conn.async_manage("probe", _op) == 42

    async def test_unreachable_lock_raises(
        self, hass, sample_virtual_key, mock_ble_resolver
    ):
        mock_ble_resolver.return_value = None
        conn = TtlockBleConnection(hass, sample_virtual_key)
        with pytest.raises(TTLockError, match="not reachable"):
            await conn.async_manage("probe", AsyncMock())

    @pytest.mark.parametrize(
        ("escape", "message"),
        [
            (TTLockError("rejected"), "rejected"),
            (TimeoutError(), "timed out responding to probe"),
            (ValueError("bad"), "failed to probe: bad"),
        ],
    )
    async def test_failures_disconnect_and_surface_as_ttlock_error(
        self,
        hass,
        sample_virtual_key,
        mock_ble_resolver,
        mock_ttlock_client,
        escape,
        message,
    ):
        conn = TtlockBleConnection(hass, sample_virtual_key)
        with pytest.raises(TTLockError, match=message):
            await conn.async_manage("probe", AsyncMock(side_effect=escape))
        mock_ttlock_client.disconnect.assert_awaited()
