"""Gemeinsame Basis und Geräteverwaltung für iCloud Next.

Gerätestruktur unterhalb des Kontos ("iCloud <Inhaber>"):
- je Find-My-Gerät ein Gerät (Seriennummer und OS-Version, soweit es zum eigenen
  Konto gehört — Apple gibt beides für Familiengeräte nicht heraus),
- je Person ein Gerät mit der zusammengefassten Position,
- je Kontogerät ohne Find My (z. B. Apple TV) ein Gerät mit OS-Version.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import CONF_APPLE_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)


def zuordnung(
    ortung: dict[str, Any], konto: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Kontogeräte den Find-My-Geräten zuordnen.

    Apple liefert keine gemeinsame Kennung. Zugeordnet wird über die
    Modellkennung (z. B. iPhone16,2) unter den eigenen Geräten; nur bei mehreren
    Treffern entscheidet zusätzlich der Name. Rückgabe: (Find-My-ID -> Kontogerät,
    Kontogeräte ohne Find-My-Gegenstück).
    """
    eigene = [g for g in ortung["geraete"].values() if not g["person"]]
    treffer: dict[str, dict[str, Any]] = {}
    rest: list[dict[str, Any]] = []
    for kg in konto["kontogeraete"]:
        kandidaten = [
            g for g in eigene
            if g["modell_kennung"] == kg["modell_kennung"] and g["id"] not in treffer
        ]
        if len(kandidaten) > 1:
            kandidaten = [g for g in kandidaten if g["name"] == kg["name"]]
        if len(kandidaten) == 1:
            treffer[kandidaten[0]["id"]] = kg
        else:
            rest.append(kg)
    return treffer, rest


def konto_kennung(entry: ConfigEntry) -> str:
    return entry.data[CONF_APPLE_ID]


def kontogeraet_kennung(kg: dict[str, Any]) -> str:
    return f"konto-{kg.get('seriennummer') or kg['name']}"


def person_kennung(person: str) -> str:
    return f"person-{person or 'inhaber'}"


def geraete_registrieren(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Geräte anlegen bzw. Seriennummer/OS-Version nachziehen.

    Hier statt per DeviceInfo(via_device=...) in den Entities, weil via_device
    deprecated ist und via_device_id die id des bereits angelegten Kontos braucht.
    """
    ortung = entry.runtime_data["ortung"].data
    konto = entry.runtime_data["konto"].data
    registry = dr.async_get(hass)
    hub = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, konto_kennung(entry))},
        name=f"iCloud {ortung['inhaber'] or konto_kennung(entry)}",
        manufacturer="Apple",
        model="iCloud",
        model_id=konto["speicher"].get("plan"),
        entry_type=dr.DeviceEntryType.SERVICE,
        configuration_url="https://www.icloud.com/find",
    )
    treffer, rest = zuordnung(ortung, konto)
    for geraet_id, g in ortung["geraete"].items():
        kg = treffer.get(geraet_id, {})
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, geraet_id)},
            name=g["name"],
            manufacturer="Apple",
            model=g["modell"],
            model_id=g["modell_kennung"],
            serial_number=kg.get("seriennummer"),
            sw_version=kg.get("betriebssystem"),
            via_device_id=hub.id,
        )
    for person, p in (entry.runtime_data["ortung"].data.get("zusammengefasst") or {}).items():
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, person_kennung(person))},
            name=p["name"] or "Kontoinhaber",
            manufacturer="Apple",
            model="Person (Find My)",
            via_device_id=hub.id,
        )
    for kg in rest:
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, kontogeraet_kennung(kg))},
            name=kg["name"],
            manufacturer="Apple",
            model=kg["modell"],
            model_id=kg["modell_kennung"],
            serial_number=kg.get("seriennummer"),
            sw_version=kg.get("betriebssystem"),
            via_device_id=hub.id,
        )


def erwartete_geraete(entry: ConfigEntry) -> set[str]:
    """Kennungen aller Geräte, die Apple aktuell meldet (inkl. Konto und Personen)."""
    ortung = entry.runtime_data["ortung"].data
    _, rest = zuordnung(ortung, entry.runtime_data["konto"].data)
    return (
        {konto_kennung(entry)}
        | set(ortung["geraete"])
        | {person_kennung(p) for p in ortung.get("zusammengefasst") or {}}
        | {kontogeraet_kennung(kg) for kg in rest}
    )


def _kennung(geraet: dr.DeviceEntry) -> str | None:
    return next((i[1] for i in geraet.identifiers if i[0] == DOMAIN), None)


def aufraeumen(hass: HomeAssistant, entry: ConfigEntry, unique_ids: set[str]) -> None:
    """Beim (Neu-)Laden entfernen, was Apple nicht mehr meldet.

    Geräte nur, wenn die Antwort vollständig war: Apple lädt Familiengeräte
    asynchron nach, ein Mitglied auf LOADING fehlt sonst scheinbar. Entities
    (z. B. Speicher eines Mitglieds nach Abschalten der Familie) werden entfernt,
    wenn sie bei diesem Laden nicht mehr angelegt wurden.
    """
    ortung = entry.runtime_data["ortung"].data
    if ortung["geraete"] and ortung.get("familie_vollstaendig", True):
        erwartet = erwartete_geraete(entry)
        registry = dr.async_get(hass)
        for geraet in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if _kennung(geraet) not in erwartet:
                _LOGGER.info("Entferne %s – von Apple nicht mehr gemeldet", geraet.name)
                # Geräte gehören immer nur zu einem Config-Entry
                registry.async_remove_device(geraet.id)
    entities = er.async_get(hass)
    for eintrag in er.async_entries_for_config_entry(entities, entry.entry_id):
        if eintrag.unique_id not in unique_ids:
            _LOGGER.info("Entferne %s – wird nicht mehr bereitgestellt", eintrag.entity_id)
            entities.async_remove(eintrag.entity_id)


def darf_geloescht_werden(entry: ConfigEntry, geraet: dr.DeviceEntry) -> bool:
    """Löschen-Knopf nur für Geräte, die Apple nicht mehr meldet."""
    return _kennung(geraet) not in erwartete_geraete(entry)


class ICloudEntity(CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]]):
    """Basis: Gerätezuordnung über die Kennung, unique_id aus Konto + Kennung + Schlüssel."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        entry: ConfigEntry,
        kennung: str,
        schluessel: str,
    ) -> None:
        super().__init__(coordinator)
        self._kennung = kennung
        self._attr_translation_key = schluessel
        self._attr_unique_id = f"{konto_kennung(entry)}-{kennung}-{schluessel}"
        # Das Gerät selbst legt geraete_registrieren() an, hier genügt die Zuordnung.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, kennung)})


class GeraetEntity(ICloudEntity):
    """Entity eines Find-My-Geräts; Daten aus dem Ortungs-Coordinator."""

    @property
    def _g(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get("geraete", {}).get(self._kennung)

    @property
    def available(self) -> bool:
        return super().available and self._g is not None
