# iCloud Next (`icloud_next`)

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=michis0806&repository=ha_icloud_next&category=integration)
[![Open your Home Assistant instance and start setting up this integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=icloud_next)

Home Assistant custom integration for Apple iCloud: **Find My with active
locating**, a location **per person**, **iCloud storage** usage, device information
and **Home Assistant backups in iCloud Drive**.

[Deutsche Beschreibung weiter unten.](#deutsch)

## Features

iCloud Next combines active Find My requests, account storage sensors and an
iCloud Drive destination for Home Assistant's built-in backup system. It uses its
own Apple session and can run alongside the official iCloud integration.

- **Active locating on every poll.** Apple delivers the new location with a delay;
  the integration triggers a locate request and reads the result 30 seconds later.
- **Location timestamp, "outdated" flag and position source** (Wi-Fi / GPS) per device.
- **Location per person.** Apple itself only reports devices. For every person the
  first device with a fresh location is used, in the order iPhone → Apple Watch →
  iPad, so a watch charging at home does not override the iPhone being carried.
- **Two-factor authentication with a choice of delivery.** During setup and
  re-authentication the code is only requested after you choose **push to your
  Apple devices** or **SMS**. After unsuccessful automatic session-renewal
  attempts, Home Assistant asks you to sign in again under Settings → Repairs.
  The integration never sends a verification code on its own.
- **Its own session** under `.storage/icloud_next/` — runs side by side with the
  official integration without touching its session.
- **Family optional.** Choose during setup (and later in the options) whether
  Family Sharing members are included.
- **Backups in iCloud Drive.** Upload, list, download and delete Home Assistant
  backups through the native backup system, with a configurable destination
  path. Home Assistant controls scheduling, encryption and retention.

## Entities

| For | Entities |
|---|---|
| Every Find My device | Location, Online, Last located; Battery, Charging state and Low power mode where Apple reports them (not for AirPods) |
| Devices of your own account | additionally Operating system; serial number and OS version in the device info |
| Account devices without Find My (e.g. Apple TV) | Operating system |
| Every device that can display messages (iPhone, iPad, Mac, Watch) | Message (`notify` entity) |
| iPhone, iPad, Mac, Apple Watch | Play sound (button with the Find My sound) |
| Every person | Location (attribute `geraet` = source device), Location outdated |
| Account | Storage used / total / free / percent, per category (photos, backups, documents, mail, messages); with family also total and per member; Storage almost full, Storage exceeded |

Notes:

- Apple only reports serial number and OS version for devices of the signed-in
  account, not for family members' devices. Serial numbers may be masked in
  Apple's response.
- Location entities of AirPods and other accessories are disabled by default. Apple
  provides limited data for them through this API, so the integration omits
  battery sensors, messages and Play sound buttons for accessories. Use Apple's
  Find My app for their sound and other accessory-specific functions.
- New devices appear after reloading the integration.

## Requirements

- Validated with **Home Assistant 2026.11.0**. Older Home Assistant versions have
  not been validated for the backup feature.
- An Apple account with Find My enabled for the devices you want to locate.
- Access to **iCloud Drive** and enough free iCloud storage when using backups.
  Accounts using Advanced Data Protection have not yet been validated for backups.

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

## Backups in iCloud Drive

After installing the integration and restarting Home Assistant:

1. Open **Settings → Devices & services → iCloud Next → Configure**. Set
   **Backup path in iCloud Drive** if you want a different destination.
2. Open **Settings → System → Backups** and select **iCloud Next** as a storage
   location for automatic or manual backups.

The integration uses the existing account session. It does not automatically
select the new destination or change your existing backup schedule. Home
Assistant controls encryption and retention, including deletion of expired
backups from the selected destination. Failed or incomplete uploads are not
listed as completed backups.

### Destination path

Paths are relative to iCloud Drive, for example `Backups/Home Assistant`. Missing
folders are created on the first upload. The destination must be empty or already
belong to this account's backup agent; parent folders may contain other files.
The default is the original account-specific folder,
`Home Assistant Backups - <account hash>`.

Changing the path **does not move existing backups**. Select the previous path in
the integration options to access them again. The backup agent ID and its
selection for automatic backups remain the same.

### Transfers and verification

Uploads and downloads require temporary local space for one backup archive.
Downloads are checked against their stored size and SHA-256 checksum before
being handed to Home Assistant. Signing in again is handled through iCloud Next;
the backup client never sends a two-factor code on its own.

Live transport tests covered 1, 64 and 512 MiB files, plus a nested destination
path, using fresh clients from the saved session. These checks verified
listing, download checksums and deletion. A complete restore of an encrypted
Home Assistant system backup has not yet been tested. Advanced Data Protection
and storage-quota exhaustion also need separate live validation.

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
- **Include family.** Include Family Sharing devices, locations and storage usage.
- **Backup path in iCloud Drive.** Destination folder relative to iCloud Drive,
  such as `Backups/Home Assistant`. See [Destination path](#destination-path).

Saving changed options reloads the integration.

Storage and account devices are polled hourly. If Apple is temporarily unreachable,
the last values are kept for up to 30 minutes before entities become `unavailable`.
If Apple rejects the session twice in a row (HTTP 409/421), the integration signs in
again silently. If Apple asks for a verification code during that, it retries a fresh
sign-in without a code after 15, 30 and 60 minutes (Apple often asks only
temporarily) and starts a re-authentication under Repairs only after that.
If Apple rejects the password, it tries exactly once more after 15 minutes (every
failed password sign-in can lead to an account lock) and then asks you.
If Apple locks the account, the integration stops signing in immediately, shows a
notification with a link to Apple's unlock page and waits for the re-authentication.

## Limitations

- Accounts that require a hardware security key (FIDO2) are not supported.
- Two-factor control uses internal methods of
  [pyicloud](https://github.com/timlaing/pyicloud) (current minimum: 2026.10.1).
- Find My actions are limited to messages and playing a sound. Lost mode and
  device erasure are not exposed. Backup uploads and retention-driven deletion
  write to the configured iCloud Drive destination.
- Account device entities use the name, model, OS version and available serial
  number. IMEI, UDID and payment information are not exposed as entity data.

## Icon

The integration includes its own brand icon in the `brand/` folder.

## Disclaimer

This project is not affiliated with Apple. iCloud and Find My are trademarks of
Apple Inc. Use at your own risk.

---

## Deutsch

Home-Assistant-Integration für Apple iCloud: **„Wo ist?“ mit aktiver Ortung**,
Position **je Person**, **iCloud-Speichersensoren**, Geräteinformationen und
**Home-Assistant-Backups in iCloud Drive**. Die Integration verwendet eine eigene
Apple-Sitzung und kann neben der offiziellen iCloud-Integration laufen.

### Funktionen

- **Aktive Ortung:** stößt bei jeder Abfrage eine Ortungsanfrage an und liest das
  Ergebnis 30 Sekunden später. Je Gerät gibt es Ortungszeit, „veraltet“ und
  Positionsquelle (WLAN/GPS).
- **Position je Person:** erstes Gerät mit frischer Position in der Reihenfolge
  iPhone → Apple Watch → iPad. Eine zu Hause ladende Uhr überschreibt dadurch
  nicht die aktuelle Position des mitgeführten iPhones.
- **2FA mit Wahl zwischen Push und SMS** bei Einrichtung und Neuanmeldung. Der
  Code wird erst nach der Wahl angefordert. Kann die Sitzung nicht still erneuert
  werden, erscheint nach den Wiederholungsversuchen eine Reparaturmeldung.
- **Mitteilungen und Suchton:** `notify`-Entity und Ton-Knopf für geeignete Geräte.
  Die Aktion `icloud_next.display_message` unterstützt Mitteilungen mit Ton.
  Beispiele stehen unter [Messages](#messages).
- **Familie wahlweise:** Geräte, Positionen und Speicherverbrauch der
  Familienfreigabe lassen sich bei der Einrichtung und in den Optionen einbeziehen.
- **iCloud-Speicher:** belegt, gesamt, frei, Prozent, je Bereich und je
  Familienmitglied, dazu Warnungen „fast voll“ und „überschritten“.
- **Geräteinformationen:** Seriennummer und OS-Version der Geräte des eigenen
  Kontos, soweit Apple sie liefert. Seriennummern können maskiert sein.
- **Backups in iCloud Drive:** über Home Assistants eingebaute Backup-Verwaltung,
  mit wählbarem Zielpfad. Planung, Verschlüsselung und Aufbewahrung steuert
  Home Assistant.

### Voraussetzungen und Einrichtung

Geprüft mit **Home Assistant 2026.11.0**. Ältere Versionen sind für die
Backup-Funktion nicht geprüft. Für die Ortung muss „Wo ist?“ auf den gewünschten
Geräten aktiv sein. Backups benötigen Zugriff auf iCloud Drive und ausreichend
freien iCloud-Speicher.

1. Über den HACS-Button oben als Custom Repository installieren oder
   `custom_components/icloud_next/` nach `config/custom_components/` kopieren.
2. Home Assistant neu starten.
3. **Einstellungen → Geräte & Dienste → Integration hinzufügen → iCloud Next**.
4. Apple-ID, Passwort und die gewünschte Familienoption eingeben. Falls Apple
   eine Bestätigung verlangt, Push oder SMS wählen und den sechsstelligen Code
   eingeben.

### Backups in iCloud Drive

1. Unter **Einstellungen → Geräte & Dienste → iCloud Next → Konfigurieren** bei
   Bedarf den **Backup-Pfad in iCloud Drive** ändern.
2. Unter **Einstellungen → System → Backups** iCloud Next als Speicherort für
   automatische oder manuelle Backups auswählen.

Das neue Ziel wird nicht automatisch ausgewählt. Vorhandene Backup-Zeitpläne
bleiben erhalten. Home Assistant steuert die Verschlüsselung und Aufbewahrung,
einschließlich des Löschens abgelaufener Backups im ausgewählten Ziel.

Der Pfad ist relativ zu iCloud Drive, z. B. `Backups/Home Assistant`. Fehlende
Ordner werden beim ersten Upload angelegt. Der Zielordner muss leer sein oder
bereits diesem Backup-Ziel gehören; übergeordnete Ordner dürfen andere Dateien
enthalten. Standard ist der bisherige kontospezifische Ordner
`Home Assistant Backups - <Kontohash>`.

Ein Pfadwechsel **verschiebt keine vorhandenen Backups**. Mit dem bisherigen Pfad
sind sie wieder erreichbar. Die ID des Backup-Ziels und dessen Auswahl für
automatische Backups bleiben gleich.

Upload und Download benötigen temporär lokalen Platz für ein Backup-Archiv.
Unvollständige Uploads werden nicht als fertige Backups angezeigt. Beim Download
werden Größe und SHA-256 geprüft, bevor Home Assistant die Datei erhält. Die
Backup-Übertragung verwendet einen getrennten Client; Anmeldung und erneute
Anmeldung bleiben bei iCloud Next. Der Backup-Client fordert selbst keine
2FA-Codes an.

Live geprüft wurden Übertragungen mit 1, 64 und 512 MiB sowie ein verschachtelter
Zielpfad. Neue Clients aus der gespeicherten Sitzung konnten die Dateien
auflisten, herunterladen, die Prüfsummen vergleichen und die Testdaten löschen.
Eine vollständige Wiederherstellung eines verschlüsselten HA-Systembackups,
Konten mit erweitertem Datenschutz und eine volle iCloud-Quota sind noch nicht
live geprüft.

### Optionen und Wiederanmeldung

- **Ortungsintervall (Minuten):** Standard 15, einstellbar von 5 bis 120. Jede
  Abfrage löst aktive Ortung aus; kürzere Intervalle können mehr Akku verbrauchen.
- **Familie mitabfragen:** Geräte, Positionen und Speicher der Familienfreigabe.
- **Backup-Pfad in iCloud Drive:** Zielordner für die Backup-Dateien.

Geänderte Optionen laden die Integration neu. Speicher und Kontogeräte werden
stündlich abgefragt. Bei vorübergehenden Störungen bleiben die letzten Werte bis
zu 30 Minuten verfügbar. Lehnt Apple die Sitzung zweimal hintereinander ab
(HTTP 409/421), versucht die Integration eine stille Erneuerung. Verlangt Apple
dabei einen Bestätigungscode, folgen Versuche ohne Code nach 15, 30 und 60 Minuten;
erst danach startet die Neuanmeldung unter Reparaturen. Ein abgelehntes Passwort
wird nur einmal nach 15 Minuten erneut versucht. Bei einer Kontosperre enden die
Anmeldeversuche sofort; eine Meldung verweist auf Apples Entsperrseite.

### Einschränkungen

- Konten mit verpflichtendem Hardware-Sicherheitsschlüssel werden nicht unterstützt.
- Für AirPods und anderes Zubehör sind Positions-Tracker standardmäßig deaktiviert.
  Akku-, Mitteilungs- und Ton-Entities werden für Zubehör nicht angelegt. Für
  zubehörspezifische Funktionen die „Wo ist?“-App verwenden.
- Bei „Wo ist?“ sind nur Mitteilungen und Suchton als Aktionen verfügbar; kein
  Verloren-Modus und kein Löschen von Geräten. Die Backup-Funktion schreibt und
  löscht Dateien im konfigurierten iCloud-Drive-Ziel.
- IMEI, UDID und Zahlungsinformationen werden nicht als Entity-Daten bereitgestellt.

Dieses Projekt steht nicht mit Apple in Verbindung. Nutzung auf eigene Verantwortung.
