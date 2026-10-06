"""Konstanten für iCloud Next."""

DOMAIN = "icloud_next"

CONF_APPLE_ID = "apple_id"
CONF_WITH_FAMILY = "with_family"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 15  # Minuten zwischen zwei aktiven Ortungen
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 120
ACCOUNT_INTERVAL = 60  # Minuten für Speicher und Kontogeräte

# Apple liefert die frische Position einer aktiven Ortung zeitversetzt.
# Gemessen am 06.10.2026: nach 30 s waren alle Geräte aktuell, sofort nur ein Teil.
LOCATE_WAIT = 30  # Sekunden

GRACE_SECONDS = 1800  # bei Störung alte Werte max. 30 Min weiterreichen

# Eine Position gilt für die Personen-Zusammenfassung als frisch, wenn Apple sie
# nicht als veraltet markiert und sie jünger als zwei Abfrageintervalle ist.
FRESH_FACTOR = 2

# Reihenfolge, in der Geräte für die Position einer Person herangezogen werden.
PERSON_DEVICE_PRIORITY = ("iPhone", "Watch", "iPad")

METHOD_PUSH = "push"
METHOD_SMS = "sms"
