# Changelog

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
