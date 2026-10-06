# Changelog

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
