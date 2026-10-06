# iCloud Next (`icloud_next`)

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=michis0806&repository=ha_icloud_next&category=integration)
[![Open your Home Assistant instance and start setting up this integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=icloud_next)

Home Assistant custom integration for Apple iCloud: **Find My with active
locating**, a location **per person**, **iCloud storage** usage and serial number /
OS version of your devices.

[Deutsche Beschreibung weiter unten.](#deutsch)

## Why

Since Home Assistant 2026.2 (pyicloud ≥ 2.3) the official
[iCloud integration](https://www.home-assistant.io/integrations/icloud/) only reads
Apple's location cache on its regular polls — battery levels are current, but
locations can be hours old
([core#181730](https://github.com/home-assistant/core/issues/181730)). In addition,
an expired Apple session leaves it stuck in `setup_error` instead of asking you to
sign in again.

## What it does differently

- **Active locating on every poll.** Apple delivers the new location with a delay;
  the integration triggers a locate request and reads the result 30 seconds later.
- **Location timestamp, "outdated" flag and position source** (Wi-Fi / GPS) per device.
- **Location per person.** Apple itself only reports devices. For every person the
  first device with a fresh location is used, in the order iPhone → Apple Watch →
  iPad, so a watch charging at home does not override the iPhone being carried.
- **Two-factor authentication with a choice of delivery.** During setup and
  re-authentication the code is only requested after you choose **push to your
  Apple devices** or **SMS**. When Apple ends the session, the integration starts a
  re-authentication (Settings → Repairs) and never sends a code on its own.
- **Its own session** under `.storage/icloud_next/` — runs side by side with the
  official integration without touching its session.
- **Family optional.** Choose during setup (and later in the options) whether
  Family Sharing members are included.

## Entities

| For | Entities |
|---|---|
| Every Find My device | Location, Battery, Charging state, Online, Low power mode, Last located |
| Devices of your own account | additionally Operating system; serial number and OS version in the device info |
| Account devices without Find My (e.g. Apple TV) | Operating system |
| Every device that can display messages (iPhone, iPad, Mac, Watch) | Message (`notify` entity) |
| Every device that can play the Find My sound | Play sound (button; disabled by default for AirPods) |
| Every person | Location (attribute `geraet` = source device), Location outdated |
| Account | Storage used / total / free / percent, per category (photos, backups, documents, mail, messages); with family also total and per member; Storage almost full, Storage exceeded |

Notes:

- Apple only reports serial number and OS version for devices of the signed-in
  account, not for family members' devices, and masks the serial number (last five
  characters only).
- Location entities of AirPods and other accessories are disabled by default.
- New devices appear after reloading the integration.

## Installation

### HACS (custom repository)

Click the HACS badge above, or manually:

1. HACS → Integrations → ⋮ → *Custom repositories*
2. Add `https://github.com/michis0806/ha_icloud_next` (category: Integration)
3. Install **iCloud Next** and restart Home Assistant.

### Manual

Copy `custom_components/icloud_next/` into your `config/custom_components/` folder
and restart Home Assistant.

## Setup

Settings → Devices & services → *Add integration* → **iCloud Next**
(or click the setup badge above).

1. Enter Apple ID and password and choose whether to include the family.
2. If Apple asks for two-factor verification, choose push or SMS.
3. Enter the six-digit code.

## Messages

Every device that supports it gets a `notify` entity. It uses Find My's
*display message* feature — not a regular push notification: Apple shows title and
text on the device.

```yaml
action: notify.send_message
target:
  entity_id: notify.iphone_von_ramona_mitteilung
data:
  title: Home Assistant
  message: Please call back
```

With a sound:

```yaml
action: icloud_next.display_message
target:
  entity_id: notify.iphone_von_ramona_mitteilung
data:
  message: Please call back
  sound: true
```

## Options

- **Locate interval (minutes)** (default 15, 5–120). Every poll wakes up location
  services on all devices — shorter intervals cost battery.
- **Include family**

Storage and account devices are polled hourly. If Apple is temporarily unreachable,
the last values are kept for up to 30 minutes before entities become `unavailable`.

## Limitations

- Accounts that require a hardware security key (FIDO2) are not supported.
- Two-factor control uses internal methods of
  [pyicloud](https://github.com/timlaing/pyicloud) (tested with 2.6.5 and 2.7.0).
- Apart from messages and the Find My sound the integration is read-only: no lost mode, no erase.
- Only the serial number is kept from Apple's device list; IMEI, UDID and payment
  methods in the same response are discarded.

## Icon

The integration ships its own brand icon (`brand/` folder inside the integration);
Home Assistant 2026.3 or newer picks it up automatically.

## Disclaimer

This project is not affiliated with Apple. iCloud and Find My are trademarks of
Apple Inc. Use at your own risk.

---

## Deutsch

Home-Assistant-Integration für Apple iCloud: **„Wo ist?“ mit aktiver Ortung**,
Position **je Person**, **iCloud-Speicher** sowie Seriennummer und OS-Version der Geräte.

- Die offizielle iCloud-Integration liest seit Home Assistant 2026.2 nur noch Apples
  Positions-Cache; Positionen sind dadurch oft stundenlang alt. iCloud Next stößt bei
  jeder Abfrage eine **aktive Ortung** an und liest das Ergebnis 30 Sekunden später.
- Je Gerät mit **Ortungszeit, „veraltet“ und Positionsquelle** (WLAN/GPS).
- **Position je Person:** erstes frisches Gerät in der Reihenfolge iPhone → Apple Watch
  → iPad, damit eine zu Hause ladende Uhr nicht das mitgeführte iPhone überstimmt.
- **2FA mit Wahl Push oder SMS** bei Einrichtung und Neuanmeldung; der Code wird erst
  nach der Wahl angefordert. Läuft die Anmeldung bei Apple ab, erscheint eine
  Reparaturmeldung — es werden nie unaufgefordert Codes verschickt.
- **Mitteilungen:** je Gerät eine `notify`-Entity („Mitteilung“) für `notify.send_message`,
  mit Ton über die Aktion `icloud_next.display_message` („Wo ist?“ → Mitteilung anzeigen).
- **Ton abspielen:** Knopf je Gerät für den Suchton aus „Wo ist?“.
- **Familie wahlweise** mit abfragen (bei der Einrichtung und in den Optionen).
- **iCloud-Speicher:** belegt, gesamt, frei, Prozent, je Bereich und je
  Familienmitglied, dazu Warnungen „fast voll“ und „überschritten“.
- Seriennummer und OS-Version gibt Apple nur für Geräte des eigenen Kontos heraus;
  die Seriennummer ist von Apple maskiert (nur die letzten fünf Zeichen).
- Installation über HACS (Custom Repository) oder manuell nach
  `config/custom_components/`, danach Neustart; Einrichtung über
  *Integration hinzufügen* → **iCloud Next**.
- Optionen: Ortungsintervall (Standard 15 Minuten) und Familie. Speicher und
  Kontogeräte werden stündlich abgefragt; bei Störungen bleiben die letzten Werte
  bis zu 30 Minuten erhalten.
