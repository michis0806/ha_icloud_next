# iCloud Next als Speicherziel für Home-Assistant-Backups

Machbarkeitsprüfung am 8. November 2026, lokaler Arbeitszweig `backup-feasibility`.
Diese Prüfung ergänzt die bestehende Custom Integration `icloud_next`. Der lokale
Stand 0.4.0 implementiert den nativen Backup-Agenten; die Freigabe als öffentliches
Release steht noch aus.

## Probe

`scripts/probe_icloud_backup.py` verwendet eine temporäre Kopie der gespeicherten
iCloud-Next-Sitzung. Sie liest kein Passwort für eine Anmeldung aus und unterbindet
Passwort-Fallback, 2FA-Versand und automatische PCS-Gerätefreigaben.

Ohne `--write-test` werden nur die Sitzung validiert und die Erreichbarkeit des
Drive-Verzeichnisses geprüft. Verzeichnisnamen und Kontodaten werden nicht ausgegeben.
Mit `--write-test` legt die Probe einen eindeutig benannten eigenen Testordner an:

1. Zufallsdatei lokal erzeugen und SHA-256 berechnen.
2. Datei mit einem Streaming-Multipart-Encoder hochladen.
3. Kleine Metadatendatei mit Name, Größe und SHA-256 hochladen.
4. Client schließen und aus der gespeicherten Sitzungskopie neu aufbauen.
5. Dateien neu auflisten, herunterladen und Größe, SHA-256 und Metadaten vergleichen.
6. Nur die selbst erzeugten Dateien und den eigenen leeren Testordner entfernen.

Es werden keine echten Home-Assistant-Backups oder privaten Dateien hochgeladen.
Das ist eine Prüfung des Übertragungsweges, kein Wiederherstellungstest eines Systems.
Der neue Client simuliert das Wiederverwenden einer gespeicherten Sitzung; ein
Home-Assistant-Neustart oder das Ablaufverhalten der Anmeldung wird damit nicht getestet.

## Lokal ausführen

```sh
python3 -m venv /tmp/icloud-next-backup-venv
/tmp/icloud-next-backup-venv/bin/python -m pip install -r scripts/requirements-backup-probe.txt pytest
/tmp/icloud-next-backup-venv/bin/python -m pytest -q tests/test_backup_probe.py
/tmp/icloud-next-backup-venv/bin/python scripts/probe_icloud_backup.py
```

Der Schreibtest muss ausdrücklich eingeschaltet werden:

```sh
/tmp/icloud-next-backup-venv/bin/python scripts/probe_icloud_backup.py --write-test --size-mib 1 64
```

Standard-Konfigurationsverzeichnis ist `/homeassistant`. Mit `--config-dir` kann ein
anderes Verzeichnis gewählt werden. Bei mehreren aktiven Konten ist `--entry-id`
anzugeben. Die Probe prüft vor dem Schreibtest den freien iCloud-Speicher.

## Technischer Befund

Der übliche pyicloud-Upload verwendet `requests` mit `files=...`; requests liest den
Dateiinhalt dabei vollständig ein. Für große Backup-Archive ist das ungeeignet.
Die Probe ersetzt nur im eigenen kurzlebigen Client diesen Upload durch einen
`requests-toolbelt.MultipartEncoder`. Die Datei wird in kleinen Blöcken übertragen.
Vier Offline-Tests prüfen begrenzte Lesegrößen, den tatsächlich übertragenen Inhalt,
Fortschrittsmeldungen, das Zurücksetzen des Adapters nach Fehlern sowie das Unterbinden
interaktiver Anmeldeversuche.

Der Testadapter ist kein Produktionsentwurf: Eine echte Integration darf den
`session.post`-Aufruf des gemeinsam verwendeten Ortungsclients nicht vorübergehend
austauschen. Dafür braucht der Backup-Transport eine eigene, klar abgegrenzte
Sitzung beziehungsweise einen Bibliotheks-Upload mit expliziter Streaming-Unterstützung.
Die gespeicherte Anmeldung und die Wiederanmeldelogik bleiben bei `Sitzung`.

## Umsetzung in der Custom Integration

- Eine zusätzliche Plattformdatei `custom_components/icloud_next/backup.py` stellt
  pro geladenem Konto einen stabil identifizierten `BackupAgent` bereit.
- Der Agent wird über die Backup-Plattform und deren Listener registriert. Er muss
  nicht in die bestehende Liste der Entity-Plattformen aufgenommen werden: Sonst
  würde auch die dortige Vollständigkeitsprüfung für das Entity-Aufräumen beeinflusst.
- Home Assistant übernimmt Auswahl des Speicherziels, Backup-Planung und Aufbewahrung.
  Die Wahl des iCloud-Ziels aktiviert keine eigenständige zweite Backup-Automation.
- Ein eigenes Drive-Verzeichnis enthält jeweils eine Archivdatei und die zugehörigen
  `AgentBackup`-Metadaten. Nur vollständige und konsistente Paare werden als Backups
  aufgelistet. Fehler und Abbrüche dürfen keine scheinbar vollständigen Backups erzeugen.
- `async_upload_backup`, `async_download_backup`, `async_list_backups`,
  `async_get_backup` und `async_delete_backup` sind implementiert.
- Home Assistant liefert den Backup-Inhalt als asynchronen Datenstrom. Eine erste
  robuste Implementierung kann ihn in eine temporäre Datei schreiben und diese
  speicherschonend hochladen. Dafür wird zusätzlich lokaler Platz in Backup-Größe
  benötigt. Temporäre Archive gehören außerhalb der gesicherten Bereiche (zum
  Beispiel in das temporäre Verzeichnis), damit sie keine weiteren Backups
  vergrößern. Eine spätere direkte Übertragung braucht eine begrenzte Stream-Brücke.
- Blockierende Apple-Anfragen laufen im Executor. Fortschritt muss sicher in den
  Event-Loop zurückgemeldet werden. Die Ortung darf nicht durch einen langen Upload
  oder eine Wiederanmeldung des Backup-Clients blockiert werden.
- Bestehende Session-/Passwort-/Kontosperrfehler werden in Backup-Fehler übersetzt.
  Der Agent fordert selbst keine Codes an und umgeht keine Kontosperre.
- Vor produktiver Freigabe sind Abbruch, Verbindungsfehler, volle Quota, erneute
  Anmeldung, echter Integrationsneustart und Wiederherstellung eines verschlüsselten
  Home-Assistant-Testbackups zu prüfen. Konten mit erweitertem Datenschutz benötigen
  eine eigene Prüfung; der aktuelle Kontotest ist kein allgemeiner Kompatibilitätsnachweis.

## Ergebnisse

| Testdatei | Uploaddauer | Download und Metadaten nach neuem Client |
| --- | --- | --- |
| 1 MiB | 5,60 Sekunden | SHA-256 und Metadaten identisch |
| 64 MiB | 24,88 Sekunden | SHA-256 und Metadaten identisch |
| 512 MiB | 121,36 Sekunden | SHA-256 und Metadaten identisch |

Der 512-MiB-Prozess hatte vor und nach der Übertragung denselben Spitzenverbrauch
von 281528 KiB (ca. 274,9 MiB) RAM. Die Messung betrifft den gesamten Probeprozess.
Beide Testordner, lokale Testdateien und temporäre Sitzungskopien wurden entfernt.
Vier Offline-Tests und Ruff-Prüfungen bestehen.

Die Live-Ergebnisse und die vollständigen bereinigten Ereignisprotokolle liegen unter
`/share/icloud-next-backup-feasibility/`. Es werden nur Größe, Dauer, Prüfsumme,
Prozess-Speicherverbrauch und die Namen der selbst erzeugten Testordner protokolliert.

## Referenzen

- https://contributors.home-assistant.io/core/integrations/backup/
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/onedrive/backup.py
- https://github.com/timlaing/pyicloud/blob/2026.10.1/pyicloud/services/drive.py

## Implementierungsprüfung

29 Tests unter echtem Home Assistant 2026.11.0 bestehen bei gesperrtem Netzwerk.
Sie prüfen unter anderem Abbruch, Aufräumen, Metadatenvalidierung, Sitzungskopien,
Speichermangel und das Entladen während einer Sitzungskopie. Ruff und Hassfest
bestehen (nur der bereits vorhandene SVG-Branding-Hinweis).

Der produktive Transport hat zusätzlich einen Live-Rundlauf mit 8 MiB Zufallsdaten
bestanden: Upload, neuer Client aus Sitzungskopie, Auflisten, Download mit SHA-256
und Löschen einschließlich Testordner. Ein kompletter Restore eines verschlüsselten
HA-Backups, volle Apple-Quota und erweiterter Datenschutz bleiben separat zu prüfen.
