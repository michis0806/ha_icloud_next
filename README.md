# iCloud Next

Home-Assistant-Integration für Apple iCloud: **Find My mit aktiver Ortung**,
Position je Person, **iCloud-Speicher** und Seriennummer/OS-Version der Geräte.

Entstanden, weil die offizielle `icloud`-Integration seit Home Assistant 2026.2
(pyicloud ≥ 2.3) bei den regelmäßigen Abfragen nur noch Apples Positions-Cache
liest — Akkustände sind aktuell, Positionen oft stundenlang alt
([core#181730](https://github.com/home-assistant/core/issues/181730)).

## Was anders ist

- **Aktive Ortung bei jeder Abfrage.** Apple liefert die neue Position zeitversetzt;
  die Integration stößt die Ortung an und liest das Ergebnis 30 Sekunden später.
- **Ortungszeit, „veraltet“ und Positionsquelle** (WLAN/GPS) je Gerät.
- **Position je Person.** Apple selbst liefert nur Geräte. Pro Person wird das erste
  Gerät mit frischer Position in der Reihenfolge iPhone → Apple Watch → iPad
  genommen, damit eine zu Hause ladende Uhr nicht das mitgeführte iPhone überstimmt.
- **2FA mit Wahl des Wegs.** Bei Einrichtung und Neuanmeldung wird der Code erst
  angefordert, nachdem du Push oder SMS gewählt hast. Läuft Apples Anmeldung ab,
  startet die Integration die Neuanmeldung (Reparaturen) und verschickt nie
  unaufgefordert einen Code.
- **Eigene Sitzung** unter `.storage/icloud_next/` — läuft parallel zur offiziellen
  Integration, ohne deren Sitzung anzufassen.
- **Familie optional.** Bei der Einrichtung (und später in den Optionen) wählbar,
  ob die Familienfreigabe mit abgefragt wird.

## Entities

Je Find-My-Gerät: Position, Akku, Ladezustand, Online, Stromsparmodus,
Letzte Ortung. Geräte des eigenen Kontos zusätzlich mit Betriebssystem und
Seriennummer (Apple gibt beides für Familiengeräte nicht heraus). Apple TVs und
andere Kontogeräte ohne Find My erscheinen mit ihrer OS-Version.

Je Person: Position (mit Attribut `geraet`) und „Position veraltet“.

Konto: Speicher belegt / gesamt / frei / Prozent, je Bereich (Fotos, Backups,
Dokumente, Mail, Nachrichten), mit Familie zusätzlich gesamt und je Mitglied,
dazu „Speicher fast voll“ und „Speicher überschritten“.

Neue Geräte erscheinen nach einem Neuladen der Integration.

## Installation

HACS → Benutzerdefinierte Repositories → `https://github.com/michis0806/ha_icloud_next`
(Kategorie Integration), installieren, Home Assistant neu starten, dann
Einstellungen → Geräte & Dienste → Integration hinzufügen → „iCloud Next“.

## Optionen

- **Ortung alle … Minuten** (Standard 15, 5–120). Jede Abfrage weckt die Ortung
  auf allen Geräten — kürzere Abstände kosten Akku.
- **Familie mitabfragen**

Speicher und Kontogeräte werden stündlich abgefragt.

## Hinweise

- Konten mit Sicherheitsschlüssel (FIDO2) werden nicht unterstützt.
- Die 2FA-Steuerung nutzt interne Methoden von pyicloud (getestet mit 2.6.5 und 2.7.0).
- Abgerufen werden nur lesende Daten; Seriennummer ja, IMEI, UDID und
  Zahlungsmittel aus derselben Apple-Antwort werden verworfen.
