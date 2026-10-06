"""Akku, Ladezustand, Ortungszeit, Betriebssystem und iCloud-Speicher."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import (
    GeraetEntity,
    ICloudEntity,
    konto_kennung,
    kontogeraet_kennung,
    zuordnung,
)

# Apple liefert die Bereichsnamen nur englisch
BEREICHE = {
    "photos": "Fotos und Videos",
    "backup": "Backups",
    "docs": "Dokumente",
    "mail": "Mail",
    "messages": "Nachrichten",
}

LADEZUSTAENDE = {
    "Charging": "laedt",
    "NotCharging": "entlaedt",
    "Charged": "voll",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ortung = entry.runtime_data["ortung"]
    konto = entry.runtime_data["konto"]
    entities: list[SensorEntity] = []

    for geraet_id in ortung.data["geraete"]:
        entities += [
            AkkuSensor(ortung, entry, geraet_id),
            LadezustandSensor(ortung, entry, geraet_id),
            OrtungszeitSensor(ortung, entry, geraet_id),
        ]

    treffer, rest = zuordnung(ortung.data, konto.data)
    for geraet_id, kg in treffer.items():
        entities.append(BetriebssystemSensor(konto, entry, geraet_id, kg))
    for kg in rest:
        entities.append(BetriebssystemSensor(konto, entry, kontogeraet_kennung(kg), kg))

    hub = konto_kennung(entry)
    speicher = konto.data["speicher"]
    entities += [
        SpeicherSensor(konto, entry, hub, "speicher_belegt", lambda s: s.get("belegt")),
        SpeicherSensor(konto, entry, hub, "speicher_gesamt", lambda s: s.get("gesamt")),
        SpeicherSensor(konto, entry, hub, "speicher_frei", _frei),
        SpeicherProzentSensor(konto, entry, hub),
    ]
    for schluessel, bereich in speicher["bereiche"].items():
        entities.append(
            SpeicherSensor(
                konto, entry, hub, "speicher_bereich",
                lambda s, k=schluessel: (s["bereiche"].get(k) or {}).get("bytes"),
                suffix=schluessel,
                platzhalter=BEREICHE.get(schluessel) or bereich.get("name") or schluessel,
            )
        )
    if speicher.get("familie_gesamt") is not None:
        entities.append(
            SpeicherSensor(konto, entry, hub, "speicher_familie", lambda s: s.get("familie_gesamt"))
        )
    for dsid, mitglied in speicher["familie"].items():
        entities.append(
            SpeicherSensor(
                konto, entry, hub, "speicher_mitglied",
                lambda s, k=dsid: (s["familie"].get(k) or {}).get("bytes"),
                suffix=dsid, platzhalter=mitglied.get("name") or dsid,
            )
        )
    async_add_entities(entities)


def _frei(s: dict[str, Any]) -> int | None:
    if s.get("gesamt") is None or s.get("belegt") is None:
        return None
    return s["gesamt"] - s["belegt"]


# ------------------------------------------------------------------ Geräte


class AkkuSensor(GeraetEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, entry, geraet_id) -> None:
        super().__init__(coordinator, entry, geraet_id, "akku")

    @property
    def native_value(self) -> int | None:
        g = self._g or {}
        # Offline-Zubehör meldet 0 % mit Status "Unknown" — das ist kein Messwert.
        if g.get("ladezustand") not in LADEZUSTAENDE:
            return None
        return g.get("akku")


class LadezustandSensor(GeraetEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [*LADEZUSTAENDE.values(), "unbekannt"]

    def __init__(self, coordinator, entry, geraet_id) -> None:
        super().__init__(coordinator, entry, geraet_id, "ladezustand")

    @property
    def native_value(self) -> str:
        return LADEZUSTAENDE.get((self._g or {}).get("ladezustand"), "unbekannt")


class OrtungszeitSensor(GeraetEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, geraet_id) -> None:
        super().__init__(coordinator, entry, geraet_id, "ortungszeit")

    @property
    def native_value(self) -> datetime | None:
        return (self._g or {}).get("ortungszeit")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        g = self._g or {}
        return {"veraltet": g.get("veraltet"), "positionsquelle": g.get("positionsquelle")}


class BetriebssystemSensor(ICloudEntity, SensorEntity):
    """OS-Version laut Kontogeräteliste; Seriennummer als Attribut."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, kennung: str, kg: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, kennung, "betriebssystem")
        self._schluessel = (kg["name"], kg["modell_kennung"])

    @property
    def _kg(self) -> dict[str, Any] | None:
        for kg in (self.coordinator.data or {}).get("kontogeraete", []):
            if (kg["name"], kg["modell_kennung"]) == self._schluessel:
                return kg
        return None

    @property
    def available(self) -> bool:
        return super().available and self._kg is not None

    @property
    def native_value(self) -> str | None:
        return (self._kg or {}).get("betriebssystem") or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"seriennummer": (self._kg or {}).get("seriennummer")}


# ----------------------------------------------------------------- Speicher


class SpeicherSensor(ICloudEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_suggested_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_suggested_display_precision = 1

    def __init__(
        self, coordinator, entry, hub: str, schluessel: str, wert,
        suffix: str = "", platzhalter: str = "",
    ) -> None:
        super().__init__(coordinator, entry, hub, schluessel)
        if suffix:
            self._attr_unique_id = f"{self._attr_unique_id}-{suffix}"
            self._attr_translation_placeholders = {"name": platzhalter}
        self._wert = wert

    @property
    def native_value(self) -> int | None:
        return self._wert((self.coordinator.data or {}).get("speicher") or {})


class SpeicherProzentSensor(ICloudEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, entry, hub: str) -> None:
        super().__init__(coordinator, entry, hub, "speicher_prozent")

    @property
    def native_value(self) -> float | None:
        s = (self.coordinator.data or {}).get("speicher") or {}
        if not s.get("gesamt") or s.get("belegt") is None:
            return None
        return round(s["belegt"] / s["gesamt"] * 100, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = (self.coordinator.data or {}).get("speicher") or {}
        return {"plan": s.get("plan"), "bezahlt": s.get("bezahlt")}
