# Changelog

## 0.3.3

- Fewer unnecessary 2FA prompts: if Apple asks for a verification code while the
  session is being renewed silently, re-authentication no longer starts right away.
  The integration signs in again without a code after 15, 30 and 60 minutes and only
  asks you if that fails too. Apple often asks for the code only temporarily –
  rejected at night, the same sign-in went through without a code in the morning.
- Before each of these attempts the last good session is restored, so a failed
  attempt cannot lose the trust token.
- Options now reload the integration via `OptionsFlowWithReload` instead of an update
  listener (fixes the "has an update listener" warning, required from Home Assistant
  2026.12).

## 0.3.2

- If Apple rejects the session twice in a row (HTTP 409/421), the integration signs
  in again silently (as pyicloud does for 450). If Apple asks for a code, it starts
  re-authentication under Repairs instead of only showing "unavailable" after 30
  minutes. Single 409s are still bridged with the last values.
- AirPods & accessories: no more battery level/state sensors (Apple never reports
  them via iCloud); they are removed on the next reload.
- No more "Play sound" for AirPods & accessories: Apple only plays the sound on
  connected AirPods (never in the closed charging case) and otherwise accepts the
  request silently. The button is removed on the next reload.

## 0.3.1

- New brand icon (cloud with location signal).

## 0.3.0

- "Play sound" button per device using the Find My sound; disabled by default for
  AirPods & accessories.

## 0.2.0

- Messages: a `notify` entity ("Message") per message-capable device for
  `notify.send_message`, plus the action `icloud_next.display_message` with optional
  sound (Find My "Display message").

## 0.1.1

- On load/reload, devices Apple no longer reports are removed together with their
  entities (not while Apple is still loading family devices); entities no longer
  provided (e.g. after turning off family) as well.
- Delete button for devices Apple no longer reports.
- Options: label "Locate interval (minutes)".
- README in English with HACS and setup buttons, German section below.
- Own brand icon (`brand/icon.png`, `brand/icon@2x.png`) for HA 2026.3+.

## 0.1.0

- First release: Find My with active locating, location per person, iCloud storage
  (also per family member), serial number and OS version of account devices.
- 2FA during setup and re-authentication with a choice of push/SMS, no automatic
  sending.
- Family optional (setup and options).

# Changelog (Deutsch)

## 0.3.3

- Verlangt Apple beim stillen Erneuern der Sitzung einen Code, startet die
  Neuanmeldung nicht mehr sofort: Die Integration meldet sich nach 15, 30 und 60
  Minuten erneut ohne Code an und fragt erst, wenn auch das scheitert. Apple verlangt
  den Code oft nur vorübergehend – nachts abgelehnt, morgens ging dieselbe Anmeldung
  durch. Vor jedem Versuch wird der letzte gute Stand der Session zurückgelegt,
  damit ein gescheiterter Versuch den Vertrauens-Token nicht verliert.
- Optionen laden die Integration über `OptionsFlowWithReload` neu statt über einen
  Update-Listener (Warnung „has an update listener“, ab Home Assistant 2026.12 nötig).

## 0.3.2

- Lehnt Apple die Sitzung zweimal hintereinander ab (HTTP 409/421), meldet sich die
  Integration still neu an (wie pyicloud bei 450). Verlangt Apple dabei einen Code,
  startet die Neuanmeldung unter Reparaturen, statt nach 30 Minuten nur „nicht
  verfügbar“ zu melden. Einzelne 409 werden weiter mit den letzten Werten überbrückt.
- AirPods & Co.: keine Sensoren Akku/Ladezustand mehr (Apple liefert über iCloud nie
  Werte); sie verschwinden beim nächsten Laden.
- Kein „Ton abspielen“ mehr für AirPods & Co.: Apple spielt den Ton nur bei
  verbundenen AirPods ab (nie in der geschlossenen Ladeschale) und nimmt den Auftrag
  sonst kommentarlos an. Der Knopf verschwindet beim nächsten Laden.

## 0.3.1

- Neues Brand-Icon (Wolke mit Ortungssignal).

## 0.3.0

- Knopf „Ton abspielen“ je Gerät mit Suchton-Funktion („Wo ist?“ → Ton abspielen);
  bei AirPods & Co. standardmäßig deaktiviert.

## 0.2.0

- Mitteilungen: je nachrichtenfähigem Gerät eine `notify`-Entity („Mitteilung“) für
  `notify.send_message`, dazu die Aktion `icloud_next.display_message` mit optionalem Ton
  (Find-My-Funktion „Mitteilung anzeigen“).

## 0.1.1

- Beim Laden/Neuladen werden Geräte, die Apple nicht mehr meldet, samt Entities entfernt
  (nicht, solange Apple die Familiengeräte noch nachlädt); nicht mehr bereitgestellte
  Entities (z. B. nach Abschalten der Familie) ebenso.
- Löschen-Knopf für Geräte, die Apple nicht mehr meldet.
- Optionen: Beschriftung „Ortungsintervall (Minuten)“.
- README auf Englisch mit HACS- und Einrichtungs-Button, deutscher Abschnitt darunter.
- Eigenes Brand-Icon (`brand/icon.png`, `brand/icon@2x.png`) für HA 2026.3+.

## 0.1.0

- Erste Version: Find My mit aktiver Ortung, Position je Person, iCloud-Speicher
  (auch je Familienmitglied), Seriennummer und OS-Version der Kontogeräte.
- 2FA bei Einrichtung und Neuanmeldung mit Wahl Push/SMS, kein automatischer Versand.
- Familie optional (Einrichtung und Optionen).
