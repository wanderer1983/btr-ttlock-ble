# BTR TTLock BLE for Home Assistant

[![CI](https://github.com/wanderer1983/btr-ttlock-ble/actions/workflows/ci.yml/badge.svg)](https://github.com/wanderer1983/btr-ttlock-ble/actions/workflows/ci.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=wanderer1983&repository=btr-ttlock-ble&category=integration)

Local control of TTLock smart locks and padlocks over Bluetooth, for [Home Assistant](https://www.home-assistant.io/), through any Bluetooth adapter or standard [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html). No TTLock gateway and no cloud at run time.

On top of locking, unlocking, battery and live events, it manages what the lock holds: **pair a new lock straight from Home Assistant**, **add and remove fingerprints, cards and passcodes**, and **factory-reset** a lock, all from a sidebar panel or from actions.

This is a fork of [roquerodrigo/ha-ttlock-ble](https://github.com/roquerodrigo/ha-ttlock-ble). It uses its own domain (`btr_ttlock_ble`), so it can be installed alongside the original.

## What it adds

- **Pairing from Home Assistant** – a lock in pairing mode (new, or after a factory reset) shows up under *Discovered*. Pick **Pair** and Home Assistant becomes its admin and keeps its keys. Nobody ever types or copies a key, and no TTLock account is involved.
- **Credential management** – enrol and delete fingerprints and cards, add, change and delete passcodes, with optional validity windows. Enrolment reports each step (place your finger, scan accepted, enrolled) as it happens.
- **Sidebar panel** – one page with every lock: whether it is in range and through which proxy, its state and battery, and its fingerprints, cards and passcodes with add, name, change and delete. Anything that needs a session waits for the lock to wake before it starts.
- **Factory reset and removal** – remove a lock and reset it in the same step, so it is ready for this or any other app.
- **Asleep locks fail fast** – a command for a lock no proxy has heard for 20 seconds says "asleep, touch it to wake it" at once, instead of running into Bluetooth timeouts.
- **Features read from the lock** – a lock whose key does not say what it supports is asked once, so a padlock without keypad or card reader does not offer passcodes and cards.

Everything from the original is still there: local Bluetooth lock and unlock, battery and state read from the lock's own broadcasts, live operation-log events, who-unlocked tracking, clock alignment, TTLock account sign-in, manual keys, diagnostics.

## Requirements

- Home Assistant 2026.10 or newer, with the Bluetooth integration set up.
- A Bluetooth radio within range of each lock: a local adapter, or an ESP32 running an [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html) in active mode. Several locks far apart are fine, with one proxy near each. See [Bluetooth proxies](#bluetooth-proxies) for the settings that matter.
- A TTLock-family lock (TTLock, DLock-XP, Schlage Breeze and other apps built on the TTLock platform) using the V3 protocol, which covers current models.

## Installation

### With HACS (recommended)

1. Click the **Open in HACS** button above. It opens HACS in your Home Assistant with this repository filled in. Or, in HACS, open the ⋮ menu → **Custom repositories**, add `https://github.com/wanderer1983/btr-ttlock-ble` with type **Integration**.
2. Download **BTR TTLock BLE** in HACS.
3. Restart Home Assistant.

### Manual installation

1. Download `btr_ttlock_ble.zip` from the [latest release](https://github.com/wanderer1983/btr-ttlock-ble/releases/latest), or the repository as a zip.
2. Copy the `btr_ttlock_ble` folder into your Home Assistant configuration folder, under `custom_components`, so you end up with `config/custom_components/btr_ttlock_ble/manifest.json`. Create `custom_components` if it does not exist. The Samba, File editor or Studio Code Server add-ons all work for this, as does `scp` to a host with SSH access.
3. Restart Home Assistant.

To update by hand, replace the folder with the new release's and restart. The keys of locks paired by Home Assistant live in Home Assistant's own storage, not in this folder, so they survive an update.

## Adding a lock

Go to **Settings → Devices & services**. A lock in range usually waits there already under **Discovered**; otherwise use **Add integration → BTR TTLock BLE**. There are three ways to give Home Assistant a lock's keys:

| Option | When to use it |
|---|---|
| **Pair a lock that is in pairing mode** | A new lock, or one you have reset. Home Assistant pairs it and keeps the keys. The lock is not on any TTLock account afterwards, so the TTLock app cannot open it. |
| **Sign in to a TTLock account** | Locks already paired in the TTLock app (or Schlage Breeze). Every lock on the account is added at once, and the app keeps working too. A new device login is confirmed with a code TTLock emails you. |
| **Enter a lock key manually** | A lock paired by some other local tool, whose AES key, unlock key and admin passcode you already have. |

### Pairing a lock

1. Put the lock in pairing mode. A new lock is in it out of the box. A used one gets there with a factory reset: from the panel (**Remove** → *Factory-reset the lock*), from the TTLock app (delete the lock), or with the lock's reset button.
2. Wake the lock (touch it, or press a key). Its broadcast now says it is in pairing mode, and a **Discovered** card appears within seconds. The panel's **Add a lock** button opens the same screen.
3. Choose **Pair a lock that is in pairing mode** and submit. Pairing takes 10 to 20 seconds and is retried automatically if the first Bluetooth session drops.
4. Name the lock on Home Assistant's success screen; its entities follow that name.

Pair with a good signal: a padlock's metal body blocks its antenna in some directions, so turn it to face the proxy, or hold it close to the proxy for the minute pairing takes. The keys are saved the moment the lock accepts them, so a pairing interrupted halfway, or a dialog closed too early, can be finished by pairing again.

A lock from a TTLock account cannot be paired here while that account still lists it: delete it in the app first (which resets it), then reconfigure the account entry here.

## The panel

**BTR TTLock** in the sidebar (admins only) shows every lock:

- whether it is in range, with the signal and the proxy that heard it last, its state and battery;
- **Unlock**, **Read from lock** (reads its fingerprint, card and passcode lists now) and, where the lock has one, its **keypad admin code**;
- its fingerprints, cards and passcodes, each with **Name** and **Delete** (and **Change** for passcodes), and **Add** for each type the lock supports;
- **Remove** for a lock added by pairing or by a manual key: either factory-reset it first, or only forget it. For a lock from a TTLock account this is **Factory reset**.

When a step needs the lock and it has not been heard in the last few seconds, the panel asks you to touch it and waits for it to wake. Enrolment then guides you through it: hold the card to the reader, or place the same finger several times until it says *Enrolled*.

Passcodes are stored only as a digest, and shown as their first digit and last two (`1•••56`). That is why changing a passcode asks for the current code.

## Actions

All of them take a `device_id` and need a key with admin rights (every key from pairing, and the owner's key from an account):

| Action | What it does |
|---|---|
| `btr_ttlock_ble.add_passcode` | Add a keypad code, with an optional name and validity window. |
| `btr_ttlock_ble.change_passcode` | Replace a code with a new one, keeping its type, window and name. |
| `btr_ttlock_ble.delete_passcode` | Delete a code by its digits. |
| `btr_ttlock_ble.add_card` | Wait for a card at the reader and enrol it. Returns the card number. |
| `btr_ttlock_ble.delete_card` | Delete a card by its number. |
| `btr_ttlock_ble.add_fingerprint` | Wait for the same finger several times and enrol it. Returns the fingerprint number. |
| `btr_ttlock_ble.delete_fingerprint` | Delete a fingerprint by its number. |

Enrolment fires `btr_ttlock_ble_enrollment` events with a `stage` of `waiting`, `scan_accepted` (with a `scans` count), `enrolled` (with the `number`) or `failed`, so a dashboard or automation can tell the person at the lock what to do.

## Bluetooth proxies

Most locks sleep between uses and only send short broadcasts. Two proxy settings make the difference between commands that land at once and commands that time out:

```yaml
esphome:
  # ...
  platformio_options:
    build_flags:
      - -DBTM_BLE_SCAN_FAST_INT=56
      - -DBTM_BLE_SCAN_FAST_WIN=56

esp32_ble_tracker:
  scan_parameters:
    interval: 1100ms
    window: 1100ms
    active: true

bluetooth_proxy:
  active: true
```

- **Scan window**: the stock `esp32-generic` proxy package listens 30 ms out of every 320 ms, which misses most of a sleepy padlock's broadcasts. Listening the whole time (`interval` equal to `window`) catches them.
- **Connection timing**: while opening a connection the ESP32 listens 30 ms out of every 60 ms, a cycle a lock's broadcast interval can keep missing. The two build flags (in units of 0.625 ms, so 35 ms) close that gap.
- **Signal**: about -80 dBm or better connects reliably; -90 dBm and below mostly fails. The panel and the *Signal strength* sensor show it. A padlock's orientation alone can be worth 20 dB.
- **Slots**: an ESP32 proxy has three connection slots by default. Other Bluetooth devices connected through the same proxy (battery monitors, for instance) use them too.
- Keep the proxy's `logger` at `WARN`: on a Wi-Fi proxy the log stream shares the radio with Bluetooth.

## Running it next to the original

The original [`ttlock_ble`](https://github.com/roquerodrigo/ha-ttlock-ble) integration and this one use different domains, so both can be installed. A lock should be set up in only one of them, since each Bluetooth session needs the lock to itself. To move a lock over, delete it from the original and add it here; a locally paired lock's keys do not move between the two, so pair those again.

## Entities

Each configured lock produces one HA device, named with the model, hardware and firmware the lock reports about itself, carrying up to twelve entities, plus one per known fingerprint, card and passcode:

| Entity | Domain | Purpose |
|---|---|---|
| `lock.<alias>` | `lock` | Locked/unlocked state, with optimistic updates, `locking`/`unlocking` transitional states and a post-command settle window. |
| `sensor.<alias>_battery` | `sensor` | Battery percentage (diagnostic). |
| `binary_sensor.<alias>_bluetooth_connection` | `binary_sensor` | Whether a BLE session is open right now (connectivity, diagnostic). A healthy idle lock holds none, so `off` says nothing about whether the lock is reachable. |
| `binary_sensor.<alias>_door` | `binary_sensor` | Whether the door is open, from the door-sensor records in the operation log. Created the first time the lock reports its door, so a lock without a paired sensor never gets one; it follows the door a few seconds behind, once the lock's new records have been read. |
| `event.<alias>_log` | `event` | Fires for every new operation-log record read from the lock. |
| `sensor.<alias>_last_seen` | `sensor` | When the lock was last heard from, read from HA's own advertisement history (diagnostic). |
| `sensor.<alias>_signal_strength` | `sensor` | RSSI of the lock's last advertisement, in dBm, read from HA's own advertisement history (diagnostic). The `source` attribute names the adapter or proxy that heard it. While a session is held the lock does not advertise, so the reading is the one from before it opened. |
| `sensor.<alias>_signal_quality` | `sensor` | The same reading as a percentage, mapped linearly from −100 dBm (0%) to −50 dBm (100%) and clamped (diagnostic). |
| `sensor.<alias>_connection_time` | `sensor` | How long the last BLE session took to come up, in seconds (diagnostic). `unknown` until a session has been opened. |
| `sensor.<alias>_connection_success_rate` | `sensor` | Share of the last 20 BLE session attempts that came up, in percent, with `attempts` and `failures` as attributes (diagnostic). Only attempts actually made count: a lock no scanner has heard is not dialled. Starts over on every restart. |
| `sensor.<alias>_clock_drift` | `sensor` | How far the lock's own clock was off local time when last compared, in seconds, positive when it runs ahead (diagnostic). `unknown` until a session opened for something else has carried a comparison. |
| `sensor.<alias>_last_unlocked_by` | `sensor` | Who last unlocked the lock, from its operation log (see [Who unlocked](#who-unlocked)). |
| `switch.<alias>_sound` | `switch` | The lock's keypad/lock beep, read back from the lock on sessions opened for something else and re-read at most hourly. `unknown` until a session has carried a read. Only created for an admin key that carries an admin passcode, which a manually entered key usually does not. |

The event entity classifies each record as `unlock`, `lock`, `unlock_failed`, `password_change` or `other`, and attaches `record_type` and `battery` always, plus `timestamp`, `uid`, `credential_type`, `credential`, `credential_name`, `key_id` and `accessory_battery` when the record carries them.

### Who unlocked

Three event attributes describe the credential behind a record:

| Attribute | Meaning |
|---|---|
| `credential_type` | What was used: `fingerprint`, `card`, `passcode`, `app`, `gateway`, `key_fob`, `wireless_keypad`, `mechanical_key`, `face`, `palm_vein` or `qr_code`. |
| `credential` | Its identifier: the fingerprint number, the card number or the accessory MAC. For a passcode, the label of a known passcode — its first digit and last two, as in `1•••56` — and never the code itself; a code the lock refused shows nothing. |
| `credential_name` | Whose it is, when that is known: the name given to a fingerprint, card or passcode (see [Credentials](#credentials)), or `Home Assistant` for a lock or unlock sent over BLE by this integration — `Alice (via Home Assistant)` when a user asked for it, plain `Home Assistant` when an automation did. |

`sensor.<alias>_last_unlocked_by` keeps the last unlock the log reported. Its state is the `credential_name` when there is one, otherwise the `credential` — the number of a fingerprint nobody named yet, or the label of a passcode — and `unknown` when the record carries neither. It carries `credential_type`, `credential` and `unlocked_at` as attributes and survives a restart. A door sensor reporting the door open does not replace it, and a record older than the unlock already shown is ignored.

### Credentials

Every fingerprint, card and passcode the lock is known to hold gets a `text` entity on the lock's device. Its value is the name of the person the credential belongs to, empty until set, and becomes the `credential_name` of that credential's log events.

| Entity | Domain | Purpose |
|---|---|---|
| Fingerprint \<number\> | `text` | One per enrolled fingerprint, named by the number the official app shows for it. Admin keys only. |
| Card \<number\> | `text` | One per enrolled card, named by its number. Admin keys only. |
| Passcode \<label\> | `text` | One per known passcode, named by its first digit and last two (`1•••56`). Two passcodes showing the same digits are told apart with a suffix (`1•••56 #2`). |
| Enrolled fingerprints | `sensor` | How many fingerprints the lock lists (diagnostic). `unknown` until a session has carried a read. Admin keys only. |
| Enrolled cards | `sensor` | How many cards the lock lists (diagnostic). `unknown` until a session has carried a read. Admin keys only. |
| Refresh credentials | `button` | Reads the lists now instead of on the next session. No advertisement or push event tells a credential change apart from any other record, so the lists are otherwise read at most every 30 minutes, on a session opened for something else. Fails with an error when the lock cannot be reached. Admin keys only. |

The lock lists its fingerprints and cards completely, so one it no longer lists loses its entity and its name. Passcodes work differently: the lock cannot list a passcode the official app generated offline until it has been used at the keypad. They are therefore also learned from the operation log, which names the code on every use, change, deletion and expiry: a passcode is added the first time it is used, keeps its name when it is changed, and is removed when the log records its deletion or expiry, or that every passcode was cleared. A single-use passcode stays until one of those records arrives. The code itself is never stored in clear, nor shown in an entity, a state or an attribute.

Names and the known credentials are kept across restarts. The lists say nothing about cyclic (day-of-week or time-range) restrictions, so a credential shown here may still be limited to certain days or hours.

## Options

Settings → Devices & Services → BTR TTLock BLE → **Configure** exposes one setting:

- `permanent_connection` (default off) — hold a BLE session open and read the actual lock state immediately after connecting, then wait two seconds between completed reads. These requests can prevent the firmware from closing an idle session, keeping push notifications available; they also reconcile the bolt position when a notification is missed. While the session is held the lock stops advertising, so the operation log is read after every push instead. A dropped connection is still reopened immediately. The reads add BLE traffic and processing on the lock; battery impact has not been measured. With the option off there are no periodic state reads or background reconnects.

There is no polling interval or reconnect pacing to configure. Earlier releases exposed `scan_interval` and `reconnect_interval`; both are gone. Periodic reads run only with `permanent_connection` enabled; otherwise state arrives from advertisements and sessions open on demand.

To edit credentials without removing and re-adding, use the integration's three-dot menu → **Reconfigure**.

## How it works

TTLock firmware can close idle BLE sessions within seconds; the timeout depends on the lock. The integration:

1. Reads the bolt position and battery level straight out of the lock's BLE advertisements. This costs no connection at all and is the only channel that reports an operation performed while Home Assistant is not connected — an auto-lock, the official app, a keypad code.
2. Opens a session only when there is a reason to: a `lock`/`unlock` command, an advertisement announcing unsynced operation records, or the first bolt position while none is known at all. With `permanent_connection` on, `connection.py` also maintains a session with periodic state reads and reopens it on every drop bleak signals.
3. After a user-initiated `lock`/`unlock`, the SDK keeps the link alive for 25 s so push events (the lock's reports of keypad operations, auto-locks, etc.) reach Home Assistant in real time.
4. A push listener is registered before connecting, so notifications arriving during connection setup are not discarded. Each push event carries the decoded `lock_state` and `battery` when the firmware emits a heartbeat-style payload, letting the entities update without a follow-up query.
5. While a session is open anyway, the lock is asked what it is — model, hardware and firmware — and the answer is remembered, so a restart shows it without connecting.

## Development

The integration lives in `custom_components/btr_ttlock_ble`. Its Bluetooth protocol code is the [`ttlock-ble`](https://github.com/roquerodrigo/ttlock-ble) SDK, extended with credential management and pairing, and vendored under `custom_components/btr_ttlock_ble/sdk` (see `sdk/VENDORED_FROM`), so Home Assistant installs nothing from PyPI.

```bash
uv sync --all-groups
uv run ruff format --check . && uv run ruff check . && uv run mypy custom_components/btr_ttlock_ble
uv run pytest
```

The tests need Linux (Home Assistant's test harness does).

## Credits

- [Rodrigo Roque](https://github.com/roquerodrigo) wrote the original [ha-ttlock-ble](https://github.com/roquerodrigo/ha-ttlock-ble) integration and [ttlock-ble](https://github.com/roquerodrigo/ttlock-ble) SDK this builds on.
- The pairing and factory-reset sequences follow [kind3r/ttlock-sdk-js](https://github.com/kind3r/ttlock-sdk-js), which also documented much of the credential protocol.

## License

GNU General Public License v3.0 or later, see [LICENSE](LICENSE). It includes code from ha-ttlock-ble and ttlock-ble, released under the MIT License ([LICENSE-MIT](LICENSE-MIT)), and follows kind3r/ttlock-sdk-js (GPL-3.0). See [NOTICE](NOTICE).

This project is not affiliated with TTLock, Sciener or any lock manufacturer.
